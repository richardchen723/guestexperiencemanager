#!/usr/bin/env python3
"""
Query functions for reviews with tag joins and filtering.
"""

import logging
from datetime import date, datetime, timedelta
from dateutil.relativedelta import relativedelta
from typing import List, Dict, Optional, Sequence
from sqlalchemy import and_, or_, func
from sqlalchemy.orm import joinedload, selectinload

from dashboard.portfolio_mapping import (
    TAG_PORTFOLIO_NAMES,
    portfolio_name_for_listing,
    portfolio_name_for_tags,
)
from database.models import (
    Conversation,
    Review,
    Listing,
    ListingTag,
    Tag,
    ReviewFilter,
    Reservation,
    get_session,
)
from database.schema import get_database_path
from dashboard.tickets.models import (
    REVIEW_ACTION_CHASE,
    REVIEW_ACTION_HOST,
    REVIEW_RESOLUTION_STAGE_DEFINITIONS,
    REVIEW_RESOLUTION_STAGES,
    REVIEW_RESOLUTION_TICKET_TYPE,
    TICKET_PRIORITIES,
    ReviewAutomationAction,
    ReviewPortfolioRule,
    ReviewQueueState,
    Ticket,
    TicketComment,
    TicketListing,
    TicketTag,
    add_ticket_comment,
    get_ticket_comments,
    get_session as get_workflow_session,
    normalize_review_resolution_stage,
)
from dashboard.auth.models import get_user_by_id

logger = logging.getLogger(__name__)

REVIEW_WINDOW_DAYS = 14
REVIEW_RESOLUTION_LOOKBACK_MONTHS = 6
PUBLISHED_REVIEW_DEFAULT_DAYS = 90
HOSTAWAY_REVIEW_RATING_MAX = 10.0
PORTFOLIO_REVIEW_RATING_MAX = 5.0
DEFAULT_BAD_REVIEW_THRESHOLD = 5.0
DEFAULT_PORTFOLIO_BAD_REVIEW_THRESHOLDS = {
    'Enchanted Havens': 5.0,
    'Urban Stays': 4.0,
}
HOSTAWAY_DASHBOARD_BASE_URL = 'https://dashboard.hostaway.com'
REVIEW_RISK_TIERS = (
    {'key': 'bad_high', 'label': 'High chance of a bad review', 'short_label': 'High risk'},
    {'key': 'bad_elevated', 'label': 'Elevated chance of a bad review', 'short_label': 'Elevated risk'},
    {'key': 'mixed', 'label': 'Review outcome is unclear', 'short_label': 'Unclear'},
    {'key': 'good_likely', 'label': 'Likely to leave a good review', 'short_label': 'Likely good'},
    {'key': 'good_high', 'label': 'High chance of a good review', 'short_label': 'High confidence'},
)

NEGATIVE_REVIEW_SIGNALS = {
    'refund': 24,
    'dirty': 20,
    'filthy': 24,
    'unsafe': 24,
    'broken': 16,
    'not working': 16,
    'disappointed': 18,
    'terrible': 24,
    'unacceptable': 24,
    'complaint': 18,
    'problem': 10,
    'issue': 8,
    'smell': 14,
    'noise': 12,
    'noisy': 12,
}
POSITIVE_REVIEW_SIGNALS = {
    'thank you': 9,
    'thanks': 7,
    'great': 10,
    'wonderful': 14,
    'amazing': 14,
    'perfect': 14,
    'loved': 14,
    'enjoyed': 10,
    'beautiful': 9,
    'clean': 8,
}


def review_window_start(today: Optional[date] = None) -> date:
    """Return the oldest checkout date whose age is still less than 14 days."""
    reference_date = today or date.today()
    return reference_date - timedelta(days=REVIEW_WINDOW_DAYS - 1)


def is_in_review_window(departure_date: Optional[date], today: Optional[date] = None) -> bool:
    """Return whether a checkout happened from today through 13 days ago."""
    if not departure_date:
        return False
    reference_date = today or date.today()
    age = (reference_date - departure_date).days
    return 0 <= age < REVIEW_WINDOW_DAYS


def rate_guest_review_risk(message_previews: Sequence[str]) -> Dict:
    """Estimate review sentiment from guest messages using transparent, deterministic signals."""
    messages = [str(message or '').strip().lower() for message in message_previews if str(message or '').strip()]
    corpus = ' '.join(messages)
    negative_hits = [term for term in NEGATIVE_REVIEW_SIGNALS if term in corpus]
    positive_hits = [term for term in POSITIVE_REVIEW_SIGNALS if term in corpus]

    good_likelihood = 58
    good_likelihood -= min(52, sum(NEGATIVE_REVIEW_SIGNALS[term] for term in negative_hits))
    good_likelihood += min(35, sum(POSITIVE_REVIEW_SIGNALS[term] for term in positive_hits))
    if len(negative_hits) >= 3:
        good_likelihood -= 8
    good_likelihood = max(5, min(95, good_likelihood))

    if good_likelihood <= 30:
        tier_index = 0
    elif good_likelihood <= 45:
        tier_index = 1
    elif good_likelihood <= 65:
        tier_index = 2
    elif good_likelihood <= 82:
        tier_index = 3
    else:
        tier_index = 4

    confidence = 'high' if len(messages) >= 6 else ('medium' if len(messages) >= 2 else 'low')
    reasons = []
    if negative_hits:
        reasons.append(f"Concern signals: {', '.join(negative_hits[:3])}")
    if positive_hits:
        reasons.append(f"Positive signals: {', '.join(positive_hits[:3])}")
    if not reasons:
        reasons.append('No strong sentiment signals in recent guest messages')

    tier = REVIEW_RISK_TIERS[tier_index]
    return {
        'key': tier['key'],
        'label': tier['label'],
        'short_label': tier['short_label'],
        'order': tier_index,
        'good_review_likelihood': good_likelihood,
        'confidence': confidence,
        'reasons': reasons,
    }


def is_review_chase_risk_eligible(risk: Dict) -> bool:
    """Allow outreach unless the guest is high or elevated bad-review risk."""
    return (risk or {}).get('key') not in {'bad_high', 'bad_elevated'}


def should_offer_review_chase(risk: Dict, guest_reviewed: bool) -> bool:
    """Offer outreach only while an eligible guest review is still outstanding."""
    return is_review_chase_risk_eligible(risk) and not guest_reviewed


def _review_for_origin(reservation: Reservation, origin: str) -> Optional[Review]:
    normalized_origin = origin.strip().lower()
    submitted_statuses = {'submitted', 'published'}
    matching = [
        review for review in (reservation.reviews or [])
        if (review.origin or '').strip().lower() == normalized_origin
        and (
            not (review.status or '').strip()
            or (review.status or '').strip().lower() in submitted_statuses
        )
    ]
    if not matching:
        return None
    return max(
        matching,
        key=lambda review: (
            review.updated_on or review.inserted_on or datetime.min,
            review.review_date or date.min,
            review.review_id or 0,
        ),
    )


def _has_manual_host_review_confirmation(state: Optional[ReviewQueueState]) -> bool:
    """Return whether a human operator explicitly confirmed the host review."""
    return bool(state and state.host_reviewed and state.host_reviewed_by)


def _clear_stale_synced_host_review_state(
    state: Optional[ReviewQueueState],
    host_review: Optional[Review],
) -> None:
    """Undo workflow state previously inferred only from a now-ineligible review.

    Manual confirmations are preserved. This lets the new status policy reopen
    cards that were automatically closed solely because a Hostaway row was
    ``pending``.
    """
    if (
        not state
        or host_review
        or state.host_reviewed_by
        or not state.host_review_id
    ):
        return
    state.host_reviewed = False
    state.host_review_id = None
    state.host_reviewed_at = None
    state.closed_at = None


def normalize_review_rating(
    rating: Optional[float],
    source_max: float = HOSTAWAY_REVIEW_RATING_MAX,
    target_max: float = PORTFOLIO_REVIEW_RATING_MAX,
) -> Optional[float]:
    """Convert a rating proportionally to the portfolio's five-star scale."""
    if rating is None:
        return None
    if source_max <= 0 or target_max <= 0:
        raise ValueError('Rating scale maximums must be positive')
    normalized = float(rating) * float(target_max) / float(source_max)
    return max(0.0, min(float(target_max), normalized))


def _rating_on_five_point_scale(review: Optional[Review]) -> Optional[float]:
    if not review:
        return None
    return normalize_review_rating(review.overall_rating)


def review_channel_name(
    review: Optional[Review],
    reservation: Optional[Reservation],
) -> str:
    """Return the most specific channel recorded for a guest review."""
    candidates = (
        review.channel_name if review else None,
        reservation.channel_name if reservation else None,
        reservation.source if reservation else None,
    )
    for candidate in candidates:
        normalized = ' '.join(str(candidate or '').split())
        if normalized:
            return normalized
    return 'Direct'


def published_review_date_range(
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    today: Optional[date] = None,
) -> tuple[date, date, bool]:
    """Resolve a custom publication range or the inclusive default 90-day range."""
    reference_date = today or date.today()
    default_start = reference_date - timedelta(days=PUBLISHED_REVIEW_DEFAULT_DAYS - 1)
    has_custom_range = start_date is not None or end_date is not None
    if has_custom_range and (start_date is None or end_date is None):
        raise ValueError('Choose both a From date and a To date')
    if has_custom_range and end_date < start_date:
        raise ValueError('To date cannot be earlier than From date')

    if has_custom_range:
        is_custom_range = start_date != default_start or end_date != reference_date
        return start_date, end_date, is_custom_range
    return default_start, reference_date, False


def published_review_rating_bucket(rating: Optional[float]) -> Optional[int]:
    """Map a normalized score to its nearest whole-star filter bucket."""
    if rating is None:
        return None
    return max(1, min(5, int(float(rating) + 0.5)))


def review_resolution_window_start(today: Optional[date] = None) -> date:
    """Return the first review date in the rolling six-month resolution pool."""
    return (today or date.today()) - relativedelta(months=REVIEW_RESOLUTION_LOOKBACK_MONTHS)


def review_resolution_date_range(
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    today: Optional[date] = None,
) -> tuple[date, date, bool]:
    """Resolve a complete custom range or the default rolling six-month range."""
    has_custom_range = start_date is not None or end_date is not None
    if has_custom_range and (start_date is None or end_date is None):
        raise ValueError('Choose both a From date and a To date')
    if has_custom_range and end_date < start_date:
        raise ValueError('To date cannot be earlier than From date')

    reference_date = today or date.today()
    if has_custom_range:
        return start_date, end_date, True
    return review_resolution_window_start(reference_date), reference_date, False


def default_bad_review_threshold(portfolio_name: str) -> float:
    """Return the initial rating threshold for a portfolio."""
    return DEFAULT_PORTFOLIO_BAD_REVIEW_THRESHOLDS.get(
        portfolio_name,
        DEFAULT_BAD_REVIEW_THRESHOLD,
    )


def is_bad_review_rating(rating: Optional[float], threshold: float) -> bool:
    """A bad review is strictly lower than the portfolio's configured threshold."""
    return rating is not None and rating < threshold


def _portfolio_name_for_listing(listing: Optional[Listing], listing_id: Optional[int] = None) -> str:
    tag_names = [row.tag.name for row in (listing.tags if listing else []) if row.tag]
    resolved_listing_id = listing_id or (listing.listing_id if listing else None)
    if resolved_listing_id is not None:
        return portfolio_name_for_listing(resolved_listing_id, tag_names) or 'Unmapped'
    return portfolio_name_for_tags(tag_names) or 'Unmapped'


def _portfolio_rule(workflow_session, portfolio_name: str) -> Optional[ReviewPortfolioRule]:
    return workflow_session.query(ReviewPortfolioRule).filter(
        ReviewPortfolioRule.portfolio_name == portfolio_name,
    ).first()


def _effective_bad_review_threshold(workflow_session, portfolio_name: str) -> float:
    rule = _portfolio_rule(workflow_session, portfolio_name)
    return (
        float(rule.bad_review_threshold)
        if rule and rule.bad_review_threshold is not None
        else default_bad_review_threshold(portfolio_name)
    )


def _guest_message_previews(reservation: Reservation) -> List[str]:
    previews = []
    for conversation in reservation.conversations or []:
        for message in conversation.messages or []:
            sender_type = (message.sender_type or '').lower()
            if message.is_incoming or sender_type == 'guest':
                if message.content_preview:
                    previews.append(message.content_preview)
    return previews


def _latest_reservation_conversation(reservation: Reservation) -> Optional[Conversation]:
    """Return the conversation most likely to contain the active guest thread."""
    conversations = list(reservation.conversations or [])
    if not conversations:
        return None
    return max(
        conversations,
        key=lambda conversation: (
            conversation.last_message_at
            or conversation.updated_on
            or conversation.inserted_on
            or datetime.min,
            conversation.conversation_id or 0,
        ),
    )


def hostaway_url_for_reservation(reservation: Reservation) -> Dict[str, str]:
    """Build a Hostaway inbox deep link with a reservation fallback."""
    conversation = _latest_reservation_conversation(reservation)
    if conversation and conversation.conversation_id:
        return {
            'url': f'{HOSTAWAY_DASHBOARD_BASE_URL}/messages/inbox/{conversation.conversation_id}',
            'destination': 'conversation',
        }
    return {
        'url': f'{HOSTAWAY_DASHBOARD_BASE_URL}/reservations/{reservation.reservation_id}',
        'destination': 'reservation',
    }


def _reservation_options():
    return (
        joinedload(Reservation.listing).joinedload(Listing.tags).joinedload(ListingTag.tag),
        joinedload(Reservation.guest),
        selectinload(Reservation.reviews),
        selectinload(Reservation.conversations).selectinload(Conversation.messages),
    )


def _ensure_queue_state(
    workflow_session,
    reservation: Reservation,
    existing_state: Optional[ReviewQueueState],
) -> ReviewQueueState:
    if existing_state:
        return existing_state
    state = ReviewQueueState(
        reservation_id=reservation.reservation_id,
        listing_id=reservation.listing_id,
    )
    workflow_session.add(state)
    return state


def _create_resolution_ticket_for_review(
    workflow_session,
    guest_review: Review,
    current_user_id: int,
    listing: Optional[Listing] = None,
    reservation: Optional[Reservation] = None,
    state: Optional[ReviewQueueState] = None,
) -> Ticket:
    existing = workflow_session.query(Ticket).filter(
        Ticket.ticket_type == REVIEW_RESOLUTION_TICKET_TYPE,
        Ticket.source_review_id == guest_review.review_id,
    ).first()
    if existing:
        if state:
            state.resolution_ticket_id = existing.ticket_id
        return existing

    listing = listing or (reservation.listing if reservation else None)
    listing_id = guest_review.listing_id or (listing.listing_id if listing else None)
    listing_name = (
        listing.internal_listing_name or listing.name
        if listing else 'Unknown property'
    )
    guest_name = (
        reservation.guest_name if reservation else None
    ) or guest_review.reviewer_name or 'Guest'
    rating = _rating_on_five_point_scale(guest_review) or 0
    priority = 'Critical' if rating <= 2 else ('High' if rating < 4 else 'Medium')
    review_text = (guest_review.review_text or 'No written review was supplied.').strip()

    ticket = Ticket(
        listing_id=listing_id,
        issue_title='Guest review resolution',
        title=f'{rating:.1f}-star review from {guest_name} · {listing_name}',
        description=review_text,
        status='Open',
        priority=priority,
        category='review management',
        created_by=current_user_id,
        ticket_type=REVIEW_RESOLUTION_TICKET_TYPE,
        source_review_id=guest_review.review_id,
        source_reservation_id=(reservation.reservation_id if reservation else guest_review.reservation_id),
        workflow_stage=REVIEW_RESOLUTION_STAGES[0],
    )
    workflow_session.add(ticket)
    workflow_session.flush()
    if listing_id:
        workflow_session.add(TicketListing(ticket_id=ticket.ticket_id, listing_id=listing_id))
    for listing_tag in (listing.tags if listing else []):
        workflow_session.add(TicketTag(
            ticket_id=ticket.ticket_id,
            tag_id=listing_tag.tag_id,
            is_inherited=True,
        ))
    if state:
        state.resolution_ticket_id = ticket.ticket_id
    return ticket


def _create_resolution_ticket(
    workflow_session,
    reservation: Reservation,
    guest_review: Review,
    state: ReviewQueueState,
    current_user_id: int,
) -> Ticket:
    return _create_resolution_ticket_for_review(
        workflow_session,
        guest_review,
        current_user_id,
        listing=reservation.listing,
        reservation=reservation,
        state=state,
    )


def _apply_review_lifecycle(
    workflow_session,
    reservation: Reservation,
    guest_review: Optional[Review],
    host_review: Optional[Review],
    state: Optional[ReviewQueueState],
    current_user_id: int,
) -> tuple[Optional[str], Optional[ReviewQueueState]]:
    _clear_stale_synced_host_review_state(state, host_review)
    if state and state.closed_at:
        return 'closed', state

    host_reviewed = bool(host_review or _has_manual_host_review_confirmation(state))
    rating = _rating_on_five_point_scale(guest_review)
    if not (guest_review and host_reviewed and rating is not None):
        if state and state.resolution_ticket_id:
            return 'resolution', state
        return None, state

    state = _ensure_queue_state(workflow_session, reservation, state)
    state.guest_review_id = guest_review.review_id
    if host_review:
        state.host_reviewed = True
        state.host_review_id = host_review.review_id
        state.host_reviewed_at = state.host_reviewed_at or datetime.utcnow()

    portfolio_name = _portfolio_name_for_listing(reservation.listing, reservation.listing_id)
    threshold = _effective_bad_review_threshold(workflow_session, portfolio_name)
    if not is_bad_review_rating(rating, threshold):
        state.closed_at = datetime.utcnow()
        return 'closed', state

    if state.resolution_ticket_id:
        return 'resolution', state

    _create_resolution_ticket(
        workflow_session,
        reservation,
        guest_review,
        state,
        current_user_id,
    )
    return 'resolution', state


def _serialize_queue_card(
    reservation: Reservation,
    guest_review: Optional[Review],
    host_review: Optional[Review],
    state: Optional[ReviewQueueState],
    reference_date: date,
    sent_action_types: Optional[set[str]] = None,
) -> Dict:
    tags = [
        {
            'tag_id': listing_tag.tag.tag_id,
            'name': listing_tag.tag.name,
            'color': listing_tag.tag.color,
        }
        for listing_tag in (reservation.listing.tags if reservation.listing else [])
        if listing_tag.tag
    ]
    tag_names = [tag['name'] for tag in tags]
    portfolio = portfolio_name_for_listing(reservation.listing_id, tag_names) or 'Unmapped'
    age_days = (reference_date - reservation.departure_date).days
    risk = rate_guest_review_risk(_guest_message_previews(reservation))
    host_reviewed = bool(host_review or _has_manual_host_review_confirmation(state))
    rating = _rating_on_five_point_scale(guest_review)
    sent_action_types = sent_action_types or set()
    chase_review_sent = REVIEW_ACTION_CHASE in sent_action_types
    host_review_sent = REVIEW_ACTION_HOST in sent_action_types
    has_conversation = bool(reservation.conversations)
    chase_review_eligible = is_review_chase_risk_eligible(risk)
    show_chase_review_action = should_offer_review_chase(risk, bool(guest_review))
    hostaway_link = hostaway_url_for_reservation(reservation)

    return {
        'reservation_id': reservation.reservation_id,
        'listing_id': reservation.listing_id,
        'listing_name': (
            reservation.listing.internal_listing_name or reservation.listing.name
            if reservation.listing else 'Unknown property'
        ),
        'portfolio': portfolio,
        'guest_name': reservation.guest_name or (reservation.guest.full_name if reservation.guest else None) or 'Guest',
        'channel_name': reservation.channel_name or reservation.source or 'Direct',
        'arrival_date': reservation.arrival_date.isoformat() if reservation.arrival_date else None,
        'departure_date': reservation.departure_date.isoformat(),
        'days_since_checkout': age_days,
        'days_remaining': REVIEW_WINDOW_DAYS - age_days,
        'guest_reviewed': bool(guest_review),
        'guest_review_status': guest_review.status if guest_review else None,
        'guest_review_id': guest_review.review_id if guest_review else None,
        'guest_review_rating': rating,
        'guest_review_rating_raw': guest_review.overall_rating if guest_review else None,
        'guest_review_rating_source_max': HOSTAWAY_REVIEW_RATING_MAX,
        'guest_review_text': guest_review.review_text if guest_review else None,
        'host_reviewed': host_reviewed,
        'host_review_source': 'synced' if host_review else ('manual' if host_reviewed else None),
        'host_reviewed_at': state.host_reviewed_at.isoformat() if state and state.host_reviewed_at else None,
        'chase_review_sent': chase_review_sent,
        'chase_review_status': (
            'chased' if chase_review_sent else ('not_needed' if guest_review else 'not_chased')
        ),
        'host_review_sent': host_review_sent,
        'chase_review_eligible': chase_review_eligible,
        'show_chase_review_action': show_chase_review_action,
        'has_message_conversation': has_conversation,
        'hostaway_url': hostaway_link['url'],
        'hostaway_destination': hostaway_link['destination'],
        'can_chase_review': bool(
            show_chase_review_action
            and not chase_review_sent
            and has_conversation
        ),
        'can_post_host_review': not host_reviewed,
        'risk': risk,
        'tags': tags,
    }


def get_review_queue(
    tag_ids: Optional[List[int]] = None,
    current_user_id: Optional[int] = None,
    today: Optional[date] = None,
) -> Dict:
    """Return every eligible checkout in the open review window, grouped by portfolio."""
    if not current_user_id:
        raise ValueError('A current user is required to reconcile review workflows')

    reference_date = today or date.today()
    main_session = get_session(get_database_path())
    workflow_session = get_workflow_session()
    try:
        query = main_session.query(Reservation).join(Listing).filter(
            Reservation.departure_date >= review_window_start(reference_date),
            Reservation.departure_date <= reference_date,
            ~func.lower(func.coalesce(Reservation.status, '')).like('%cancel%'),
            ~func.lower(func.coalesce(Reservation.status, '')).in_(['declined', 'inquiry', 'expired']),
            func.lower(func.coalesce(Reservation.channel_name, Reservation.source, '')) != 'customical',
            func.lower(func.coalesce(Listing.status, '')) != 'deleted',
        )
        if tag_ids:
            query = query.join(ListingTag).filter(ListingTag.tag_id.in_(tag_ids))

        reservations = query.options(*_reservation_options()).distinct().all()
        reservation_ids = [reservation.reservation_id for reservation in reservations]
        states = {
            state.reservation_id: state
            for state in workflow_session.query(ReviewQueueState).filter(
                ReviewQueueState.reservation_id.in_(reservation_ids or [-1])
            ).all()
        }
        sent_actions_by_reservation = {}
        for action in workflow_session.query(ReviewAutomationAction).filter(
            ReviewAutomationAction.reservation_id.in_(reservation_ids or [-1]),
            ReviewAutomationAction.status == 'sent',
        ).all():
            sent_actions_by_reservation.setdefault(action.reservation_id, set()).add(action.action_type)

        cards = []
        for reservation in reservations:
            guest_review = _review_for_origin(reservation, 'Guest')
            host_review = _review_for_origin(reservation, 'Host')
            outcome, state = _apply_review_lifecycle(
                workflow_session,
                reservation,
                guest_review,
                host_review,
                states.get(reservation.reservation_id),
                current_user_id,
            )
            if state:
                states[reservation.reservation_id] = state
            if outcome in ('closed', 'resolution'):
                continue
            cards.append(_serialize_queue_card(
                reservation,
                guest_review,
                host_review,
                state,
                reference_date,
                sent_actions_by_reservation.get(reservation.reservation_id),
            ))

        workflow_session.commit()
        cards.sort(key=lambda card: (
            card['portfolio'].lower(),
            card['risk']['order'],
            card['days_remaining'],
            card['guest_name'].lower(),
        ))

        grouped = []
        for portfolio_name in sorted({card['portfolio'] for card in cards}, key=lambda name: (name == 'Unmapped', name.lower())):
            portfolio_cards = [card for card in cards if card['portfolio'] == portfolio_name]
            grouped.append({
                'name': portfolio_name,
                'reviews': portfolio_cards,
                'count': len(portfolio_cards),
                'high_risk_count': sum(card['risk']['order'] <= 1 for card in portfolio_cards),
            })

        return {
            'reviews': cards,
            'portfolios': grouped,
            'window': {
                'days': REVIEW_WINDOW_DAYS,
                'start_date': review_window_start(reference_date).isoformat(),
                'end_date': reference_date.isoformat(),
            },
            'summary': {
                'total': len(cards),
                'needs_host_review': sum(not card['host_reviewed'] for card in cards),
                'guest_reviewed': sum(card['guest_reviewed'] for card in cards),
                'high_risk': sum(card['risk']['order'] <= 1 for card in cards),
            },
        }
    except Exception:
        workflow_session.rollback()
        raise
    finally:
        workflow_session.close()
        main_session.close()


def mark_host_reviewed(reservation_id: int, current_user_id: int, today: Optional[date] = None) -> Dict:
    """Persist the host-reviewed marker and immediately apply the review lifecycle."""
    reference_date = today or date.today()
    main_session = get_session(get_database_path())
    workflow_session = get_workflow_session()
    try:
        reservation = main_session.query(Reservation).filter(
            Reservation.reservation_id == reservation_id
        ).options(*_reservation_options()).first()
        if not reservation:
            raise LookupError('Reservation not found')
        if not is_in_review_window(reservation.departure_date, reference_date):
            raise ValueError('This reservation is outside the 14-day review window')

        state = workflow_session.query(ReviewQueueState).filter(
            ReviewQueueState.reservation_id == reservation_id
        ).first()
        state = _ensure_queue_state(workflow_session, reservation, state)
        state.host_reviewed = True
        state.host_reviewed_by = current_user_id
        state.host_reviewed_at = state.host_reviewed_at or datetime.utcnow()

        outcome, state = _apply_review_lifecycle(
            workflow_session,
            reservation,
            _review_for_origin(reservation, 'Guest'),
            _review_for_origin(reservation, 'Host'),
            state,
            current_user_id,
        )
        workflow_session.commit()
        return {
            'reservation_id': reservation_id,
            'host_reviewed': True,
            'outcome': outcome or 'host_reviewed',
            'resolution_ticket_id': state.resolution_ticket_id if state else None,
        }
    except Exception:
        workflow_session.rollback()
        raise
    finally:
        workflow_session.close()
        main_session.close()


def _historical_guest_reviews(
    main_session,
    start_date: date,
    end_date: date,
) -> List[Review]:
    start_datetime = datetime.combine(start_date, datetime.min.time())
    end_datetime = datetime.combine(end_date + timedelta(days=1), datetime.min.time())
    return main_session.query(Review).join(
        Listing,
        Review.listing_id == Listing.listing_id,
    ).filter(
        func.lower(func.coalesce(Review.origin, '')) == 'guest',
        func.lower(func.coalesce(Review.status, '')) != 'rejected',
        func.lower(func.coalesce(Listing.status, '')) != 'deleted',
        Review.overall_rating.isnot(None),
        or_(
            and_(
                Review.review_date.isnot(None),
                Review.review_date >= start_date,
                Review.review_date <= end_date,
            ),
            and_(
                Review.review_date.is_(None),
                Review.inserted_on >= start_datetime,
                Review.inserted_on < end_datetime,
            ),
        ),
    ).options(
        joinedload(Review.listing).joinedload(Listing.tags).joinedload(ListingTag.tag),
        joinedload(Review.reservation),
    ).order_by(Review.review_date.desc(), Review.review_id.desc()).all()


def _portfolio_rule_payloads(
    workflow_session,
    review_contexts: List[Dict],
) -> tuple[List[Dict], Dict[str, float]]:
    saved_rules = workflow_session.query(ReviewPortfolioRule).all()
    saved_by_name = {rule.portfolio_name: rule for rule in saved_rules}
    discovered_names = {context['portfolio'] for context in review_contexts}
    extra_names = sorted(
        (discovered_names | set(saved_by_name)) - set(TAG_PORTFOLIO_NAMES),
        key=str.lower,
    )
    portfolio_names = list(TAG_PORTFOLIO_NAMES) + extra_names
    threshold_by_name = {
        name: (
            float(saved_by_name[name].bad_review_threshold)
            if name in saved_by_name
            else default_bad_review_threshold(name)
        )
        for name in portfolio_names
    }

    rules = []
    for name in portfolio_names:
        threshold = threshold_by_name[name]
        portfolio_reviews = [context for context in review_contexts if context['portfolio'] == name]
        rules.append({
            'portfolio': name,
            'display_name': 'Urban Stays (PT300)' if name == 'Urban Stays' else name,
            'bad_review_threshold': threshold,
            'configured': name in saved_by_name,
            'review_count': len(portfolio_reviews),
            'qualifying_count': sum(
                is_bad_review_rating(context['rating'], threshold)
                for context in portfolio_reviews
            ),
        })
    return rules, threshold_by_name


def get_review_resolutions(
    current_user_id: int,
    today: Optional[date] = None,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
) -> Dict:
    """Reconcile and return the portfolio-defined bad-review pool for one date range."""
    if not current_user_id:
        raise ValueError('A current user is required to reconcile review resolutions')

    range_start, range_end, is_custom_range = review_resolution_date_range(
        start_date=start_date,
        end_date=end_date,
        today=today,
    )
    main_session = get_session(get_database_path())
    workflow_session = get_workflow_session()
    try:
        historical_reviews = _historical_guest_reviews(main_session, range_start, range_end)
        review_contexts = []
        for review in historical_reviews:
            listing = review.listing
            review_contexts.append({
                'review': review,
                'listing': listing,
                'reservation': review.reservation,
                'portfolio': _portfolio_name_for_listing(listing, review.listing_id),
                'rating': _rating_on_five_point_scale(review),
            })

        rules, threshold_by_name = _portfolio_rule_payloads(workflow_session, review_contexts)
        qualifying_contexts = [
            context for context in review_contexts
            if is_bad_review_rating(
                context['rating'],
                threshold_by_name.get(
                    context['portfolio'],
                    default_bad_review_threshold(context['portfolio']),
                ),
            )
        ]
        for context in qualifying_contexts:
            _create_resolution_ticket_for_review(
                workflow_session,
                context['review'],
                current_user_id,
                listing=context['listing'],
                reservation=context['reservation'],
            )
        workflow_session.commit()

        qualifying_review_ids = {context['review'].review_id for context in qualifying_contexts}
        tickets = workflow_session.query(Ticket).filter(
            Ticket.ticket_type == REVIEW_RESOLUTION_TICKET_TYPE,
            Ticket.source_review_id.in_(qualifying_review_ids or {-1}),
        ).order_by(Ticket.created_at.desc()).all()
        ticket_ids = [ticket.ticket_id for ticket in tickets]
        note_counts = dict(workflow_session.query(
            TicketComment.ticket_id,
            func.count(TicketComment.comment_id),
        ).filter(
            TicketComment.ticket_id.in_(ticket_ids or {-1})
        ).group_by(TicketComment.ticket_id).all())
        listing_ids = {ticket.listing_id for ticket in tickets if ticket.listing_id}
        review_ids = {ticket.source_review_id for ticket in tickets if ticket.source_review_id}
        reservation_ids = {ticket.source_reservation_id for ticket in tickets if ticket.source_reservation_id}

        listings = main_session.query(Listing).filter(Listing.listing_id.in_(listing_ids or {-1})).options(
            joinedload(Listing.tags).joinedload(ListingTag.tag)
        ).all()
        reviews = main_session.query(Review).filter(Review.review_id.in_(review_ids or {-1})).all()
        reservations = main_session.query(Reservation).filter(
            Reservation.reservation_id.in_(reservation_ids or {-1})
        ).all()
        listing_map = {listing.listing_id: listing for listing in listings}
        review_map = {review.review_id: review for review in reviews}
        reservation_map = {reservation.reservation_id: reservation for reservation in reservations}
        context_map = {
            context['review'].review_id: context
            for context in qualifying_contexts
        }

        cards = []
        for ticket in tickets:
            listing = listing_map.get(ticket.listing_id)
            review = review_map.get(ticket.source_review_id)
            reservation = reservation_map.get(ticket.source_reservation_id)
            review_context = context_map.get(ticket.source_review_id, {})
            tag_names = [row.tag.name for row in (listing.tags if listing else []) if row.tag]
            portfolio = review_context.get('portfolio') or portfolio_name_for_listing(ticket.listing_id, tag_names) or 'Unmapped'
            workflow_stage = normalize_review_resolution_stage(ticket.workflow_stage)
            cards.append({
                'ticket_id': ticket.ticket_id,
                'stage': workflow_stage,
                'status': ticket.status,
                'priority': ticket.priority,
                'title': ticket.title,
                'review_text': review.review_text if review else ticket.description,
                'rating': _rating_on_five_point_scale(review),
                'rating_raw': review.overall_rating if review else None,
                'rating_source_max': HOSTAWAY_REVIEW_RATING_MAX,
                'review_date': review.review_date.isoformat() if review and review.review_date else None,
                'bad_review_threshold': threshold_by_name.get(
                    portfolio,
                    default_bad_review_threshold(portfolio),
                ),
                'guest_name': (
                    reservation.guest_name if reservation else None
                ) or (review.reviewer_name if review else None) or 'Guest',
                'listing_id': ticket.listing_id,
                'listing_name': (
                    listing.internal_listing_name or listing.name if listing else 'Unknown property'
                ),
                'portfolio': portfolio,
                'channel_name': review_channel_name(review, reservation),
                'assigned_user_name': ticket.assigned_user.name if ticket.assigned_user else None,
                'note_count': note_counts.get(ticket.ticket_id, 0),
                'created_at': ticket.created_at.isoformat() if ticket.created_at else None,
                'updated_at': ticket.updated_at.isoformat() if ticket.updated_at else None,
            })

        return {
            'stages': REVIEW_RESOLUTION_STAGES,
            'stage_definitions': REVIEW_RESOLUTION_STAGE_DEFINITIONS,
            'lanes': [
                {
                    'stage': stage,
                    'reviews': [card for card in cards if card['stage'] == stage],
                }
                for stage in REVIEW_RESOLUTION_STAGES
            ],
            'summary': {
                'total': len(cards),
                'open': sum(card['stage'] != REVIEW_RESOLUTION_STAGES[-1] for card in cards),
                'resolved': sum(card['stage'] == REVIEW_RESOLUTION_STAGES[-1] for card in cards),
            },
            'rules': rules,
            'lookback': {
                'months': REVIEW_RESOLUTION_LOOKBACK_MONTHS,
                'start_date': range_start.isoformat(),
                'end_date': range_end.isoformat(),
                'is_custom': is_custom_range,
            },
        }
    except Exception:
        workflow_session.rollback()
        raise
    finally:
        workflow_session.close()
        main_session.close()


def update_review_resolution_rule(
    portfolio_name: str,
    threshold: float,
    current_user_id: int,
) -> Dict:
    """Persist a portfolio's strict bad-review rating threshold."""
    normalized_name = ' '.join(str(portfolio_name or '').split())
    if not normalized_name or len(normalized_name) > 120:
        raise ValueError('A valid portfolio name is required')
    try:
        normalized_threshold = float(threshold)
    except (TypeError, ValueError):
        raise ValueError('Threshold must be a number')
    if not 1.0 <= normalized_threshold <= 5.0:
        raise ValueError('Threshold must be between 1.0 and 5.0 stars')

    workflow_session = get_workflow_session()
    try:
        rule = _portfolio_rule(workflow_session, normalized_name)
        if not rule:
            rule = ReviewPortfolioRule(portfolio_name=normalized_name)
            workflow_session.add(rule)
        rule.bad_review_threshold = round(normalized_threshold, 1)
        rule.updated_by = current_user_id
        rule.updated_at = datetime.utcnow()
        workflow_session.commit()
        return {
            'portfolio': rule.portfolio_name,
            'bad_review_threshold': float(rule.bad_review_threshold),
        }
    except Exception:
        workflow_session.rollback()
        raise
    finally:
        workflow_session.close()


def update_review_resolution_stage(ticket_id: int, stage: str) -> Dict:
    """Move a review-resolution ticket to another service-recovery stage."""
    if stage not in REVIEW_RESOLUTION_STAGES:
        raise ValueError('Invalid review resolution stage')
    workflow_session = get_workflow_session()
    try:
        ticket = workflow_session.query(Ticket).filter(
            Ticket.ticket_id == ticket_id,
            Ticket.ticket_type == REVIEW_RESOLUTION_TICKET_TYPE,
        ).first()
        if not ticket:
            raise LookupError('Review resolution not found')
        ticket.workflow_stage = stage
        ticket.status = 'Resolved' if stage == REVIEW_RESOLUTION_STAGES[-1] else (
            'Open' if stage == REVIEW_RESOLUTION_STAGES[0] else 'In Progress'
        )
        ticket.updated_at = datetime.utcnow()
        workflow_session.commit()
        return {'ticket_id': ticket.ticket_id, 'stage': ticket.workflow_stage, 'status': ticket.status}
    except Exception:
        workflow_session.rollback()
        raise
    finally:
        workflow_session.close()


def get_review_resolution_detail(ticket_id: int) -> Dict:
    """Return editable case details and the chronological operator note history."""
    workflow_session = get_workflow_session()
    try:
        ticket = workflow_session.query(Ticket).options(
            joinedload(Ticket.assigned_user),
            joinedload(Ticket.creator),
        ).filter(
            Ticket.ticket_id == ticket_id,
            Ticket.ticket_type == REVIEW_RESOLUTION_TICKET_TYPE,
        ).first()
        if not ticket:
            raise LookupError('Review resolution not found')

        detail = {
            'ticket_id': ticket.ticket_id,
            'title': ticket.title,
            'stage': normalize_review_resolution_stage(ticket.workflow_stage),
            'status': ticket.status,
            'priority': ticket.priority,
            'assigned_user_id': ticket.assigned_user_id,
            'assigned_user_name': ticket.assigned_user.name if ticket.assigned_user else None,
            'due_date': ticket.due_date.isoformat() if ticket.due_date else None,
            'created_by_name': ticket.creator.name if ticket.creator else None,
            'created_at': ticket.created_at.isoformat() if ticket.created_at else None,
            'updated_at': ticket.updated_at.isoformat() if ticket.updated_at else None,
        }
    finally:
        workflow_session.close()

    detail['notes'] = [comment.to_dict() for comment in get_ticket_comments(ticket_id)]
    return detail


def update_review_resolution(ticket_id: int, changes: Dict) -> Dict:
    """Update the editable fields of one review-resolution case."""
    if not isinstance(changes, dict):
        raise ValueError('Invalid review resolution data')

    updates = {}
    if 'title' in changes:
        title = ' '.join(str(changes.get('title') or '').split())
        if not title:
            raise ValueError('Case title is required')
        if len(title) > 240:
            raise ValueError('Case title must be 240 characters or fewer')
        updates['title'] = title

    if 'stage' in changes:
        stage = changes.get('stage')
        if stage not in REVIEW_RESOLUTION_STAGES:
            raise ValueError('Invalid review resolution stage')
        updates['workflow_stage'] = stage
        updates['status'] = 'Resolved' if stage == REVIEW_RESOLUTION_STAGES[-1] else (
            'Open' if stage == REVIEW_RESOLUTION_STAGES[0] else 'In Progress'
        )

    if 'priority' in changes:
        priority = changes.get('priority')
        if priority not in TICKET_PRIORITIES:
            raise ValueError('Invalid review resolution priority')
        updates['priority'] = priority

    if 'assigned_user_id' in changes:
        assigned_user_id = changes.get('assigned_user_id')
        if assigned_user_id in ('', None):
            updates['assigned_user_id'] = None
        else:
            try:
                assigned_user_id = int(assigned_user_id)
            except (TypeError, ValueError):
                raise ValueError('Invalid assigned operator')
            user = get_user_by_id(assigned_user_id)
            if not user or not user.is_approved:
                raise ValueError('Assigned operator must be an approved user')
            updates['assigned_user_id'] = assigned_user_id

    if 'due_date' in changes:
        due_date_value = changes.get('due_date')
        if due_date_value in ('', None):
            updates['due_date'] = None
        else:
            try:
                updates['due_date'] = date.fromisoformat(str(due_date_value))
            except ValueError:
                raise ValueError('Due date must use YYYY-MM-DD format')

    if not updates:
        raise ValueError('No editable fields were supplied')

    workflow_session = get_workflow_session()
    try:
        ticket = workflow_session.query(Ticket).filter(
            Ticket.ticket_id == ticket_id,
            Ticket.ticket_type == REVIEW_RESOLUTION_TICKET_TYPE,
        ).first()
        if not ticket:
            raise LookupError('Review resolution not found')
        for field_name, value in updates.items():
            setattr(ticket, field_name, value)
        ticket.updated_at = datetime.utcnow()
        workflow_session.commit()
    except Exception:
        workflow_session.rollback()
        raise
    finally:
        workflow_session.close()

    return get_review_resolution_detail(ticket_id)


def add_review_resolution_note(ticket_id: int, current_user_id: int, note_text: str) -> Dict:
    """Append a permanent operator note to one review-resolution case."""
    note_text = str(note_text or '').strip()
    if not note_text:
        raise ValueError('Write a note before posting')
    if len(note_text) > 5000:
        raise ValueError('Notes must be 5,000 characters or fewer')

    workflow_session = get_workflow_session()
    try:
        exists = workflow_session.query(Ticket.ticket_id).filter(
            Ticket.ticket_id == ticket_id,
            Ticket.ticket_type == REVIEW_RESOLUTION_TICKET_TYPE,
        ).first()
        if not exists:
            raise LookupError('Review resolution not found')
    finally:
        workflow_session.close()

    return add_ticket_comment(ticket_id, current_user_id, note_text).to_dict()


def _published_review_card(review: Review) -> Dict:
    listing = review.listing
    reservation = review.reservation
    rating = _rating_on_five_point_scale(review)
    portfolio = _portfolio_name_for_listing(listing, review.listing_id)
    return {
        'review_id': review.review_id,
        'listing_id': review.listing_id,
        'listing_name': (
            listing.internal_listing_name or listing.name
            if listing else 'Unknown property'
        ),
        'portfolio': portfolio,
        'portfolio_display_name': 'Urban Stays (PT300)' if portfolio == 'Urban Stays' else portfolio,
        'guest_name': (
            review.reviewer_name
            or (reservation.guest_name if reservation else None)
            or 'Guest'
        ),
        'rating': rating,
        'rating_raw': review.overall_rating,
        'rating_source_max': HOSTAWAY_REVIEW_RATING_MAX,
        'rating_bucket': published_review_rating_bucket(rating),
        'review_text': review.review_text,
        'publication_date': review.review_date.isoformat() if review.review_date else None,
        'departure_date': (
            reservation.departure_date.isoformat()
            if reservation and reservation.departure_date else None
        ),
        'channel_name': review_channel_name(review, reservation),
    }


def get_published_reviews(
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    portfolio: Optional[str] = None,
    ratings: Optional[Sequence[int]] = None,
    sort: str = 'newest',
    today: Optional[date] = None,
) -> Dict:
    """Return published guest reviews with composable reporting filters."""
    range_start, range_end, is_custom_range = published_review_date_range(
        start_date=start_date,
        end_date=end_date,
        today=today,
    )
    normalized_portfolio = ' '.join(str(portfolio or '').split()) or None
    selected_ratings = sorted({int(rating) for rating in (ratings or [])})
    if any(rating < 1 or rating > 5 for rating in selected_ratings):
        raise ValueError('Ratings must be whole numbers from 1 to 5')
    if sort not in {'newest', 'oldest', 'rating_desc', 'rating_asc'}:
        raise ValueError('Invalid published review sort order')

    session = get_session(get_database_path())
    try:
        published_reviews = session.query(Review).join(
            Listing,
            Review.listing_id == Listing.listing_id,
        ).filter(
            func.lower(func.coalesce(Review.status, '')) == 'published',
            func.lower(func.coalesce(Review.origin, '')) == 'guest',
            Review.review_date >= range_start,
            Review.review_date <= range_end,
        ).options(
            joinedload(Review.listing).joinedload(Listing.tags).joinedload(ListingTag.tag),
            joinedload(Review.reservation),
        ).order_by(Review.review_date.desc(), Review.review_id.desc()).all()

        range_cards = [_published_review_card(review) for review in published_reviews]
        portfolio_counts = {}
        for card in range_cards:
            portfolio_counts[card['portfolio']] = portfolio_counts.get(card['portfolio'], 0) + 1

        rating_scope_cards = [
            card for card in range_cards
            if not normalized_portfolio or card['portfolio'] == normalized_portfolio
        ]
        rating_counts = {rating: 0 for rating in range(1, 6)}
        for card in rating_scope_cards:
            if card['rating_bucket']:
                rating_counts[card['rating_bucket']] += 1

        cards = [
            card for card in rating_scope_cards
            if not selected_ratings or card['rating_bucket'] in selected_ratings
        ]
        if sort == 'oldest':
            cards.sort(key=lambda card: (card['publication_date'] or '9999-12-31', card['review_id']))
        elif sort == 'rating_desc':
            cards.sort(
                key=lambda card: (
                    card['rating'] is not None,
                    card['rating'] or 0,
                    card['publication_date'] or '',
                    card['review_id'],
                ),
                reverse=True,
            )
        elif sort == 'rating_asc':
            cards.sort(key=lambda card: (
                card['rating'] is None,
                card['rating'] or 0,
                card['publication_date'] or '9999-12-31',
                card['review_id'],
            ))
        else:
            cards.sort(
                key=lambda card: (card['publication_date'] or '', card['review_id']),
                reverse=True,
            )

        rated_cards = [card for card in cards if card['rating'] is not None]
        average_rating = (
            round(sum(card['rating'] for card in rated_cards) / len(rated_cards), 2)
            if rated_cards else None
        )
        portfolio_order = {name: index for index, name in enumerate(TAG_PORTFOLIO_NAMES)}
        portfolio_options = sorted(
            portfolio_counts,
            key=lambda name: (portfolio_order.get(name, len(portfolio_order)), name.lower()),
        )

        return {
            'reviews': cards,
            'summary': {
                'total': len(cards),
                'average_rating': average_rating,
                'five_star_count': sum(card['rating_bucket'] == 5 for card in cards),
                'portfolio_count': len({card['portfolio'] for card in cards}),
            },
            'range': {
                'start_date': range_start.isoformat(),
                'end_date': range_end.isoformat(),
                'is_custom': is_custom_range,
                'default_days': PUBLISHED_REVIEW_DEFAULT_DAYS,
            },
            'filters': {
                'portfolio': normalized_portfolio,
                'ratings': selected_ratings,
                'sort': sort,
            },
            'filter_options': {
                'range_total': len(range_cards),
                'rating_total': len(rating_scope_cards),
                'portfolios': [
                    {
                        'portfolio': name,
                        'display_name': 'Urban Stays (PT300)' if name == 'Urban Stays' else name,
                        'count': portfolio_counts[name],
                    }
                    for name in portfolio_options
                ],
                'ratings': [
                    {'rating': rating, 'count': rating_counts[rating]}
                    for rating in range(5, 0, -1)
                ],
            },
        }
    finally:
        session.close()


def get_unresponded_reviews(tag_ids: Optional[List[int]] = None) -> List[Dict]:
    """
    Query reviews where status='Submitted' AND origin='Guest'.
    Join with listings and listing_tags to include tag information.
    
    Args:
        tag_ids: Optional list of tag IDs to filter by. If provided, only reviews
                 for listings with at least one of these tags will be returned.
    
    Returns:
        List of review dictionaries with listing and tag information.
    """
    db_path = get_database_path()
    session = get_session(db_path)
    
    try:
        # Query reviews with status='submitted' (lowercase) and origin='Guest'
        # Note: status is stored in lowercase in database
        query = session.query(Review).join(Listing).filter(
            and_(
                func.lower(Review.status) == 'submitted',
                Review.origin == 'Guest'
            )
        )
        
        # Filter by tag_ids if provided
        if tag_ids:
            # Join with listing_tags and tags to filter by tag_ids
            query = query.join(ListingTag).join(Tag).filter(
                Tag.tag_id.in_(tag_ids)
            )
        
        reviews = query.options(
            joinedload(Review.listing).joinedload(Listing.tags).joinedload(ListingTag.tag),
            joinedload(Review.reservation)
        ).distinct().all()
        
        # Convert to dictionaries with tag information
        result = []
        for review in reviews:
            # Use internal_listing_name with fallback to name
            listing_name = None
            if review.listing:
                listing_name = review.listing.internal_listing_name or review.listing.name
            
            # Get departure date from reservation
            departure_date = None
            if review.reservation and review.reservation.departure_date:
                departure_date = review.reservation.departure_date.isoformat()
            
            review_dict = {
                'review_id': review.review_id,
                'listing_id': review.listing_id,
                'listing_name': listing_name,
                'overall_rating': review.overall_rating,
                'normalized_rating': normalize_review_rating(review.overall_rating),
                'rating_source_max': HOSTAWAY_REVIEW_RATING_MAX,
                'review_text': review.review_text,
                'review_date': review.review_date.isoformat() if review.review_date else None,
                'reviewer_name': review.reviewer_name,
                'departure_date': departure_date,
                'status': review.status,
                'origin': review.origin,
                'channel_name': review.channel_name,
                'tags': []
            }
            
            # Get tags from listing
            if review.listing and review.listing.tags:
                for listing_tag in review.listing.tags:
                    if listing_tag.tag:
                        review_dict['tags'].append({
                            'tag_id': listing_tag.tag.tag_id,
                            'name': listing_tag.tag.name,
                            'color': listing_tag.tag.color
                        })
            
            result.append(review_dict)
        
        return result
        
    except Exception as e:
        logger.error(f"Error querying unresponded reviews: {e}", exc_info=True)
        raise
    finally:
        session.close()


def get_reviews_by_filter(filter_obj: ReviewFilter, sort_by: str = 'review_date', sort_order: str = 'desc') -> List[Dict]:
    """
    Query reviews matching filter criteria.
    
    Args:
        filter_obj: ReviewFilter object with criteria.
        sort_by: Field to sort by ('review_date' or 'overall_rating'). Default: 'review_date'.
        sort_order: Sort order ('asc' or 'desc'). Default: 'desc'.
        
    Returns:
        List of review dictionaries with listing and tag information.
    """
    db_path = get_database_path()
    session = get_session(db_path)
    
    try:
        # Start with base query
        query = session.query(Review).join(Listing).filter(
            Review.status == 'published'
        )
        
        # Filter by tag_ids if provided
        if filter_obj.tag_ids:
            # Parse tag_ids (could be JSON string or list)
            tag_ids = filter_obj.tag_ids
            if isinstance(tag_ids, str):
                import json
                try:
                    tag_ids = json.loads(tag_ids)
                except:
                    tag_ids = []
            
            if tag_ids:
                # Join with listing_tags and tags to filter by tag_ids
                query = query.join(ListingTag).join(Tag).filter(
                    Tag.tag_id.in_(tag_ids)
                )
        
        # Filter by max_rating if provided
        # Note: max_rating is in 5-star scale (0-5), but overall_rating is in 10-point scale (0-10)
        # Convert 5-star rating to 10-point scale by multiplying by 2
        if filter_obj.max_rating is not None:
            max_rating_10_point = filter_obj.max_rating * 2.0
            query = query.filter(Review.overall_rating <= max_rating_10_point)
        
        # Filter by months_back if provided
        if filter_obj.months_back is not None:
            cutoff_date = datetime.utcnow() - relativedelta(months=filter_obj.months_back)
            # Use departure_date from reservation (when guest stayed) for date filtering
            # This makes more sense for "bad reviews in last X months" - we want reviews for stays in that period
            # Fall back to review_date if no reservation is linked
            query = query.outerjoin(Reservation, Review.reservation_id == Reservation.reservation_id).filter(
                or_(
                    and_(
                        Reservation.departure_date.isnot(None),
                        Reservation.departure_date >= cutoff_date.date()
                    ),
                    and_(
                        Reservation.departure_date.is_(None),
                        Review.review_date.isnot(None),
                        Review.review_date >= cutoff_date.date()
                    )
                    )
                )
        
        # Apply sorting
        # Use nullslast() if available (SQLAlchemy 1.1+), otherwise handle nulls in Python
        try:
            if sort_by == 'review_date':
                if sort_order == 'desc':
                    query = query.order_by(Review.review_date.desc().nullslast())
                else:
                    query = query.order_by(Review.review_date.asc().nullslast())
            elif sort_by == 'overall_rating':
                if sort_order == 'desc':
                    query = query.order_by(Review.overall_rating.desc().nullslast())
                else:
                    query = query.order_by(Review.overall_rating.asc().nullslast())
        except AttributeError:
            # Fallback for older SQLAlchemy versions - sort in Python
            if sort_by == 'review_date':
                if sort_order == 'desc':
                    query = query.order_by(Review.review_date.desc())
                else:
                    query = query.order_by(Review.review_date.asc())
            elif sort_by == 'overall_rating':
                if sort_order == 'desc':
                    query = query.order_by(Review.overall_rating.desc())
                else:
                    query = query.order_by(Review.overall_rating.asc())
        
        # Execute query with tag loading
        reviews = query.options(
            joinedload(Review.listing).joinedload(Listing.tags).joinedload(ListingTag.tag),
            joinedload(Review.reservation)
        ).distinct().all()
        
        # Convert to dictionaries with tag information
        result = []
        for review in reviews:
            # Use internal_listing_name with fallback to name
            listing_name = None
            if review.listing:
                listing_name = review.listing.internal_listing_name or review.listing.name
            
            # Get departure date from reservation
            departure_date = None
            if review.reservation and review.reservation.departure_date:
                departure_date = review.reservation.departure_date.isoformat()
            
            review_dict = {
                'review_id': review.review_id,
                'listing_id': review.listing_id,
                'listing_name': listing_name,
                'overall_rating': review.overall_rating,
                'normalized_rating': normalize_review_rating(review.overall_rating),
                'rating_source_max': HOSTAWAY_REVIEW_RATING_MAX,
                'review_text': review.review_text,
                'review_date': review.review_date.isoformat() if review.review_date else None,
                'reviewer_name': review.reviewer_name,
                'departure_date': departure_date,
                'status': review.status,
                'origin': review.origin,
                'channel_name': review.channel_name,
                'tags': []
            }
            
            # Get tags from listing
            if review.listing and review.listing.tags:
                for listing_tag in review.listing.tags:
                    if listing_tag.tag:
                        review_dict['tags'].append({
                            'tag_id': listing_tag.tag.tag_id,
                            'name': listing_tag.tag.name,
                            'color': listing_tag.tag.color
                        })
            
            result.append(review_dict)
        
        return result
        
    except Exception as e:
        logger.error(f"Error querying reviews by filter: {e}", exc_info=True)
        raise
    finally:
        session.close()
