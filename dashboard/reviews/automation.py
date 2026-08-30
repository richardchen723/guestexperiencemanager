"""Guarded, human-in-the-loop review outreach automation."""

from __future__ import annotations

import hashlib
import os
import re
from datetime import date, datetime
from typing import Callable, Dict, Optional
from urllib.parse import quote

from sqlalchemy import func
from sqlalchemy.orm import joinedload, selectinload

from dashboard.portfolio_mapping import portfolio_name_for_listing
from dashboard.reviews.timezone import reference_time_utc
from dashboard.tickets.models import (
    REVIEW_ACTION_CHASE,
    REVIEW_ACTION_HOST,
    ReviewAutomationAction,
    ReviewAutomationTemplate,
    ReviewQueueState,
    get_session as get_workflow_session,
)
from database.models import Conversation, Listing, ListingTag, Reservation, get_session
from database.schema import get_database_path
from sync.api_client import HostawayAPIClient


DEFAULT_CHASE_MESSAGE_TEMPLATE = """Hi {guest_first_name},

Thank you for being such a great guest at {property_name}. You were a pleasure to host, and we'll be leaving you the great review you deserve.

We'd really appreciate a great review from you, too. If you have any suggestions for how we could improve, please send us a private message here—we'd be thrilled to learn and make the experience even better."""

DEFAULT_HOST_REVIEW_TEMPLATE = """{guest_first_name} was a great guest—respectful, communicative, and left {property_name} in good shape. We would gladly welcome {guest_first_name} back anytime and recommend them to other hosts."""

REVIEW_AUTOMATION_ACTIONS = {REVIEW_ACTION_CHASE, REVIEW_ACTION_HOST}
REVIEW_TEMPLATE_PLACEHOLDERS = (
    'guest_name',
    'guest_first_name',
    'property_name',
    'portfolio_name',
)
_PLACEHOLDER_PATTERN = re.compile(r'\{([a-zA-Z_][a-zA-Z0-9_]*)\}')
_MAX_CONTENT_LENGTH = 5000
_AIRBNB_CONFIRMATION_CODE = re.compile(r'^[A-Za-z0-9]{6,20}$')

AIRBNB_COMPLETED_RESERVATIONS_URL = 'https://www.airbnb.com/hosting/reservations/completed'
VRBO_OWNER_REVIEWS_URL = 'https://www.vrbo.com/owner/reviews'


class HostReviewPublishingUnavailable(RuntimeError):
    """Raised when live review publication has no supported Hostaway API route."""


class ReviewAutomationDisabled(RuntimeError):
    """Raised when outbound review automation has been disabled."""


def review_automation_mode() -> str:
    """Return a fail-safe mode; live delivery must be enabled explicitly."""
    mode = os.getenv('REVIEW_AUTOMATION_MODE', 'dry_run').strip().lower()
    return mode if mode in {'dry_run', 'live', 'disabled'} else 'dry_run'


def _normalize_content(value: str, label: str) -> str:
    content = str(value or '').strip()
    if len(content) < 20:
        raise ValueError(f'{label} must be at least 20 characters')
    if len(content) > _MAX_CONTENT_LENGTH:
        raise ValueError(f'{label} must be {_MAX_CONTENT_LENGTH:,} characters or fewer')
    return content


def validate_review_template(template: str, label: str = 'Template') -> str:
    """Validate editable template text and reject misspelled placeholders."""
    normalized = _normalize_content(template, label)
    unknown = sorted(set(_PLACEHOLDER_PATTERN.findall(normalized)) - set(REVIEW_TEMPLATE_PLACEHOLDERS))
    if unknown:
        allowed = ', '.join(f'{{{name}}}' for name in REVIEW_TEMPLATE_PLACEHOLDERS)
        raise ValueError(f"Unknown placeholder {{{unknown[0]}}}. Available placeholders: {allowed}")
    return normalized


def render_review_template(template: str, values: Dict[str, str]) -> str:
    """Render only the supported property/guest placeholders."""
    validated = validate_review_template(template)
    missing = [name for name in _PLACEHOLDER_PATTERN.findall(validated) if name not in values]
    if missing:
        raise ValueError(f'Missing value for placeholder {{{missing[0]}}}')
    return _PLACEHOLDER_PATTERN.sub(lambda match: str(values[match.group(1)]), validated)


def _listing_name(listing: Optional[Listing]) -> str:
    if not listing:
        return 'Unknown property'
    return listing.internal_listing_name or listing.name or f'Listing {listing.listing_id}'


def _reservation_options():
    return (
        joinedload(Reservation.listing).joinedload(Listing.tags).joinedload(ListingTag.tag),
        joinedload(Reservation.guest),
        selectinload(Reservation.reviews),
        selectinload(Reservation.conversations).selectinload(Conversation.messages),
    )


def _latest_conversation(reservation: Reservation) -> Optional[Conversation]:
    conversations = list(reservation.conversations or [])
    if not conversations:
        return None
    return max(
        conversations,
        key=lambda conversation: (
            conversation.last_message_at or conversation.updated_on or conversation.inserted_on or datetime.min,
            conversation.conversation_id or 0,
        ),
    )


def _template_values(reservation: Reservation, portfolio_name: str) -> Dict[str, str]:
    guest_name = (
        reservation.guest_name
        or (reservation.guest.full_name if reservation.guest else None)
        or 'Guest'
    )
    guest_first_name = reservation.guest_first_name or guest_name.split()[0] or 'Guest'
    return {
        'guest_name': guest_name,
        'guest_first_name': guest_first_name,
        'property_name': _listing_name(reservation.listing),
        'portfolio_name': portfolio_name,
    }


def host_review_destination(reservation: Reservation) -> Dict:
    """Return the safest operator destination for a selective host review."""
    channel = str(reservation.channel_name or reservation.source or '').strip().lower()

    if 'airbnb' in channel:
        confirmation_code = str(reservation.confirmation_code or '').strip()
        has_direct_booking_link = bool(_AIRBNB_CONFIRMATION_CODE.fullmatch(confirmation_code))
        url = (
            f'https://www.airbnb.com/hosting/reservations/details/{quote(confirmation_code)}'
            if has_direct_booking_link
            else AIRBNB_COMPLETED_RESERVATIONS_URL
        )
        return {
            'supported': True,
            'platform': 'Airbnb',
            'url': url,
            'label': 'Open Airbnb booking' if has_direct_booking_link else 'Open Airbnb completed stays',
            'direct': has_direct_booking_link,
            'note': (
                'Copy the review, open Airbnb, and choose the option to review this guest.'
                if has_direct_booking_link
                else 'Copy the review, then find this guest in Airbnb’s completed stays.'
            ),
        }

    if channel in {'homeaway', 'homeawayical', 'vrbo', 'vrboofficial'} or 'vrbo' in channel:
        return {
            'supported': True,
            'platform': 'Vrbo',
            'url': VRBO_OWNER_REVIEWS_URL,
            'label': 'Open Vrbo reviews',
            'direct': False,
            'note': (
                'Copy the draft as a reference, then select this guest in Vrbo Reviews. '
                'Vrbo may request ratings and a recommendation instead of public review text.'
            ),
        }

    if 'bookingcom' in channel or 'booking.com' in channel or channel in {'booking', 'bdc'}:
        return {
            'supported': False,
            'platform': 'Booking.com',
            'url': None,
            'label': None,
            'direct': False,
            'note': 'Booking.com does not support hosts reviewing guests.',
        }

    if channel in {'bookingengine', 'direct', 'customical', 'google', 'partner', ''}:
        return {
            'supported': False,
            'platform': 'Direct booking',
            'url': None,
            'label': None,
            'direct': False,
            'note': 'This reservation has no booking-platform host review to submit.',
        }

    return {
        'supported': False,
        'platform': reservation.channel_name or reservation.source or 'This channel',
        'url': None,
        'label': None,
        'direct': False,
        'note': 'No verified host-review destination is available for this channel.',
    }


def _saved_template(workflow_session, listing_id: int) -> Optional[ReviewAutomationTemplate]:
    return workflow_session.query(ReviewAutomationTemplate).filter(
        ReviewAutomationTemplate.listing_id == listing_id,
    ).first()


def get_property_review_templates() -> Dict:
    """Return editable templates for every active property."""
    main_session = get_session(get_database_path())
    workflow_session = get_workflow_session()
    action_id = None
    try:
        listings = main_session.query(Listing).filter(
            func.lower(func.coalesce(Listing.status, '')) != 'deleted',
        ).options(
            joinedload(Listing.tags).joinedload(ListingTag.tag),
        ).order_by(
            func.lower(func.coalesce(Listing.internal_listing_name, Listing.name, '')),
        ).all()
        listing_ids = [listing.listing_id for listing in listings]
        saved_templates = {
            template.listing_id: template
            for template in workflow_session.query(ReviewAutomationTemplate).filter(
                ReviewAutomationTemplate.listing_id.in_(listing_ids or {-1}),
            ).all()
        }

        properties = []
        for listing in listings:
            saved = saved_templates.get(listing.listing_id)
            tag_names = [row.tag.name for row in (listing.tags or []) if row.tag]
            properties.append({
                'listing_id': listing.listing_id,
                'listing_name': _listing_name(listing),
                'portfolio': portfolio_name_for_listing(listing.listing_id, tag_names) or 'Unmapped',
                'chase_message_template': (
                    saved.chase_message_template if saved else DEFAULT_CHASE_MESSAGE_TEMPLATE
                ),
                'host_review_template': (
                    saved.host_review_template if saved else DEFAULT_HOST_REVIEW_TEMPLATE
                ),
                'is_custom': bool(saved),
                'updated_at': saved.updated_at.isoformat() if saved and saved.updated_at else None,
            })
        return {
            'properties': properties,
            'placeholders': list(REVIEW_TEMPLATE_PLACEHOLDERS),
        }
    finally:
        workflow_session.close()
        main_session.close()


def update_property_review_templates(
    listing_id: int,
    chase_message_template: str,
    host_review_template: str,
    current_user_id: int,
) -> Dict:
    """Create or replace one property's human-managed templates."""
    chase_template = validate_review_template(chase_message_template, 'Guest message template')
    host_template = validate_review_template(host_review_template, 'Host review template')
    main_session = get_session(get_database_path())
    workflow_session = get_workflow_session()
    try:
        listing = main_session.query(Listing).filter(
            Listing.listing_id == listing_id,
            func.lower(func.coalesce(Listing.status, '')) != 'deleted',
        ).first()
        if not listing:
            raise LookupError('Active property not found')
        saved = _saved_template(workflow_session, listing_id)
        if not saved:
            saved = ReviewAutomationTemplate(listing_id=listing_id)
            workflow_session.add(saved)
        saved.chase_message_template = chase_template
        saved.host_review_template = host_template
        saved.updated_by = current_user_id
        saved.updated_at = datetime.utcnow()
        workflow_session.commit()
        return {
            'listing_id': listing_id,
            'listing_name': _listing_name(listing),
            'chase_message_template': saved.chase_message_template,
            'host_review_template': saved.host_review_template,
            'is_custom': True,
            'updated_at': saved.updated_at.isoformat(),
        }
    except Exception:
        workflow_session.rollback()
        raise
    finally:
        workflow_session.close()
        main_session.close()


def get_review_automation_preview(
    reservation_id: int,
    action_type: str,
    current_user_id: int,
    today: Optional[date] = None,
    reference_time: Optional[datetime] = None,
) -> Dict:
    """Build and validate a final human-editable action preview."""
    if action_type not in REVIEW_AUTOMATION_ACTIONS:
        raise ValueError('Unknown review automation action')
    if not current_user_id:
        raise ValueError('A current user is required')

    # Imported here so the queue can import the action models without a cycle.
    from dashboard.reviews.query import (
        _guest_message_previews,
        _review_for_origin,
        rate_guest_review_risk,
        require_reservation_in_review_window,
        should_offer_review_chase,
    )

    reference_at = reference_time_utc(reference_time, legacy_today=today)
    main_session = get_session(get_database_path())
    workflow_session = get_workflow_session()
    try:
        reservation = main_session.query(Reservation).filter(
            Reservation.reservation_id == reservation_id,
        ).options(*_reservation_options()).first()
        if not reservation:
            raise LookupError('Reservation not found')
        require_reservation_in_review_window(reservation, reference_at)

        guest_review = _review_for_origin(reservation, 'Guest')
        host_review = _review_for_origin(reservation, 'Host')
        state = workflow_session.query(ReviewQueueState).filter(
            ReviewQueueState.reservation_id == reservation_id,
        ).first()
        already_sent = workflow_session.query(ReviewAutomationAction).filter(
            ReviewAutomationAction.reservation_id == reservation_id,
            ReviewAutomationAction.action_type == action_type,
            ReviewAutomationAction.status == 'sent',
        ).first()
        if already_sent:
            raise ValueError('This action has already been completed for the reservation')

        risk = rate_guest_review_risk(_guest_message_previews(reservation))
        conversation = _latest_conversation(reservation)
        if action_type == REVIEW_ACTION_CHASE:
            if not should_offer_review_chase(risk, bool(guest_review)):
                if guest_review:
                    raise ValueError('The guest has already submitted a review')
                raise ValueError('Chase review is unavailable for high or elevated bad-review risk')
            if not conversation:
                raise ValueError('No Hostaway conversation is available for this reservation')
        elif host_review or (state and state.host_reviewed):
            raise ValueError('The host review is already complete')

        tag_names = [row.tag.name for row in (reservation.listing.tags or []) if row.tag]
        portfolio_name = portfolio_name_for_listing(reservation.listing_id, tag_names) or 'Unmapped'
        saved = _saved_template(workflow_session, reservation.listing_id)
        raw_template = (
            (saved.chase_message_template if saved else DEFAULT_CHASE_MESSAGE_TEMPLATE)
            if action_type == REVIEW_ACTION_CHASE
            else (saved.host_review_template if saved else DEFAULT_HOST_REVIEW_TEMPLATE)
        )
        values = _template_values(reservation, portfolio_name)
        rendered = render_review_template(raw_template, values)
        mode = review_automation_mode()
        live_host_review_supported = False
        assisted_host_review = action_type == REVIEW_ACTION_HOST
        review_destination = host_review_destination(reservation) if assisted_host_review else None
        execution_enabled = (
            mode == 'dry_run' if action_type == REVIEW_ACTION_CHASE
            else False
        ) or (mode == 'live' and action_type == REVIEW_ACTION_CHASE)
        if assisted_host_review:
            capability_note = (
                'This is a human-controlled review. Copy the final text and post it on the '
                'booking platform; the next sync will confirm when the host review is submitted.'
            )
        elif mode == 'disabled':
            capability_note = 'Review automation is disabled in this environment.'
        elif mode == 'dry_run':
            capability_note = 'Test mode is active. Nothing will be sent or posted to Hostaway.'
        else:
            capability_note = 'This message will be sent through the existing Hostaway conversation.'

        return {
            'action_type': action_type,
            'reservation_id': reservation.reservation_id,
            'listing_id': reservation.listing_id,
            'listing_name': values['property_name'],
            'portfolio': portfolio_name,
            'guest_name': values['guest_name'],
            'channel_name': reservation.channel_name or reservation.source or 'Direct',
            'conversation_id': conversation.conversation_id if conversation else None,
            'communication_type': 'channel',
            'content': rendered,
            'template_is_custom': bool(saved),
            'risk': risk,
            'mode': mode,
            'simulated': mode == 'dry_run',
            'execution_enabled': execution_enabled,
            'live_host_review_supported': live_host_review_supported,
            'assisted_host_review': assisted_host_review,
            'review_destination': review_destination,
            'capability_note': capability_note,
        }
    finally:
        workflow_session.close()
        main_session.close()


class DryRunReviewAutomationGateway:
    """No-network transport used by default for local development and tests."""

    def _result(self, action_type: str, reservation_id: int, content: str) -> Dict:
        digest = hashlib.sha256(
            f'{action_type}:{reservation_id}:{content}'.encode('utf-8'),
        ).hexdigest()[:12]
        return {'provider_reference': f'dry-run-{digest}', 'simulated': True}

    def send_guest_message(self, preview: Dict, content: str) -> Dict:
        return self._result(REVIEW_ACTION_CHASE, preview['reservation_id'], content)

    def post_host_review(self, preview: Dict, content: str) -> Dict:
        return self._result(REVIEW_ACTION_HOST, preview['reservation_id'], content)


class HostawayReviewAutomationGateway:
    """Live Hostaway transport for capabilities exposed by the public API."""

    def __init__(self, client_factory: Callable[[], HostawayAPIClient] = HostawayAPIClient):
        self.client_factory = client_factory

    def send_guest_message(self, preview: Dict, content: str) -> Dict:
        result = self.client_factory().send_conversation_message(
            preview['conversation_id'],
            content,
            preview.get('communication_type') or 'channel',
        )
        provider_result = result.get('result') if isinstance(result.get('result'), dict) else result
        provider_reference = provider_result.get('id') or provider_result.get('messageId')
        return {
            'provider_reference': str(provider_reference) if provider_reference is not None else None,
            'simulated': False,
        }

    def post_host_review(self, preview: Dict, content: str) -> Dict:
        raise HostReviewPublishingUnavailable(
            'Hostaway’s public API does not expose a supported host-review publishing endpoint'
        )


def _gateway_for_mode(mode: str):
    if mode == 'dry_run':
        return DryRunReviewAutomationGateway()
    if mode == 'live':
        return HostawayReviewAutomationGateway()
    raise ReviewAutomationDisabled('Review automation is disabled in this environment')


def perform_review_automation_action(
    reservation_id: int,
    action_type: str,
    content: str,
    current_user_id: int,
    gateway=None,
) -> Dict:
    """Execute one confirmed action, recording only live outbound attempts."""
    final_content = _normalize_content(
        content,
        'Guest message' if action_type == REVIEW_ACTION_CHASE else 'Host review',
    )
    preview = get_review_automation_preview(reservation_id, action_type, current_user_id)
    if not preview['execution_enabled']:
        if preview['mode'] == 'disabled':
            raise ReviewAutomationDisabled(preview['capability_note'])
        raise HostReviewPublishingUnavailable(preview['capability_note'])

    selected_gateway = gateway or _gateway_for_mode(preview['mode'])
    execute = (
        selected_gateway.send_guest_message
        if action_type == REVIEW_ACTION_CHASE
        else selected_gateway.post_host_review
    )
    if preview['mode'] == 'dry_run':
        result = execute(preview, final_content)
        return {
            **preview,
            **result,
            'status': 'simulated',
            'content': final_content,
            'message': 'Simulation completed. Nothing was sent to Hostaway.',
        }

    workflow_session = get_workflow_session()
    action = ReviewAutomationAction(
        reservation_id=reservation_id,
        listing_id=preview['listing_id'],
        conversation_id=preview.get('conversation_id'),
        action_type=action_type,
        content=final_content,
        status='pending',
        created_by=current_user_id,
    )
    try:
        prior = workflow_session.query(ReviewAutomationAction).filter(
            ReviewAutomationAction.reservation_id == reservation_id,
            ReviewAutomationAction.action_type == action_type,
            ReviewAutomationAction.status == 'sent',
        ).first()
        if prior:
            raise ValueError('This action has already been completed for the reservation')
        workflow_session.add(action)
        workflow_session.flush()
        try:
            result = execute(preview, final_content)
        except Exception as exc:
            action.status = 'failed'
            action.error_message = str(exc)[:1000]
            action.completed_at = datetime.utcnow()
            workflow_session.commit()
            raise
        action.status = 'sent'
        action.provider_reference = result.get('provider_reference')
        action.completed_at = datetime.utcnow()
        workflow_session.commit()
        action_id = action.action_id
    except Exception:
        if action.action_id is None:
            workflow_session.rollback()
        raise
    finally:
        workflow_session.close()

    lifecycle = None
    if action_type == REVIEW_ACTION_HOST:
        from dashboard.reviews.query import mark_host_reviewed
        lifecycle = mark_host_reviewed(reservation_id, current_user_id)
    return {
        **preview,
        **result,
        'status': 'sent',
        'content': final_content,
        'action_id': action_id,
        'lifecycle': lifecycle,
        'message': (
            'Guest message sent through Hostaway.'
            if action_type == REVIEW_ACTION_CHASE
            else 'Host review posted.'
        ),
    }
