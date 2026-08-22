#!/usr/bin/env python3
"""Synchronize submitted guest and host reviews from Hostaway."""

import argparse
import json
import logging
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import func

from config import VERBOSE
from database.models import (
    Guest,
    Listing,
    Reservation,
    Review,
    ReviewSubRating,
    SyncLog,
    get_session,
    init_models,
)
from sync.api_client import HostawayAPIClient
from sync.progress_tracker import get_progress_tracker

logger = logging.getLogger(__name__)

BATCH_SIZE = 50
PAGINATION_LIMIT = 100
INCREMENTAL_OVERLAP_DAYS = 20
REVIEW_HOST_BACKFILL_SYNC_TYPE = 'review_host_backfill_v1'
HOST_REVIEW_POSTED_STATUSES = frozenset({'submitted', 'published'})
GUEST_REVIEW_SUBMITTED_STATUSES = frozenset({'submitted', 'published'})
API_REVIEW_SYNC_STATUSES = ('pending', 'submitted', 'published')


def parse_timestamp(value: Optional[str]) -> Optional[datetime]:
    """Parse timestamps returned by Hostaway into naive UTC datetimes."""
    if not value:
        return None
    cleaned = str(value).strip().replace('Z', '+00:00')
    try:
        parsed = datetime.fromisoformat(cleaned)
        return parsed.replace(tzinfo=None) if parsed.tzinfo else parsed
    except (TypeError, ValueError):
        pass
    for timestamp_format in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d'):
        try:
            return datetime.strptime(str(value).strip(), timestamp_format)
        except (TypeError, ValueError):
            continue
    return None


def parse_date(value: Optional[str]) -> Optional[date]:
    """Parse a Hostaway date or timestamp into a date."""
    parsed = parse_timestamp(value)
    return parsed.date() if parsed else None


def normalize_review_origin(review_data: Dict) -> Optional[str]:
    """Map Hostaway's directional review type to the local origin label."""
    review_type = str(review_data.get('type') or '').strip().lower()
    if review_type == 'guest-to-host':
        return 'Guest'
    if review_type == 'host-to-guest':
        return 'Host'
    if review_type in {'guest', 'host', 'admin'}:
        return review_type.capitalize()

    fallback = (
        review_data.get('origin')
        or review_data.get('reviewerType')
        or review_data.get('reviewer_type')
        or review_data.get('source')
    )
    if not fallback:
        return None
    normalized = str(fallback).strip()
    return normalized.capitalize() if normalized.lower() in {'guest', 'host', 'admin'} else normalized


def normalized_review_status(review_data: Dict) -> str:
    """Return the canonical lowercase review status from supported aliases."""
    return str(
        review_data.get('status')
        or review_data.get('reviewStatus')
        or review_data.get('publicationStatus')
        or ''
    ).strip().lower()


def should_sync_review_payload(review_data: Dict) -> bool:
    """Return whether a review row proves that side submitted a review.

    Hostaway creates both directional rows before either party reviews. For the
    operational queue, only ``submitted`` and ``published`` are treated as
    conclusive proof that either side completed its review. ``pending`` is kept
    out of this policy because it can represent an intermediate channel state.
    """
    status = normalized_review_status(review_data)
    origin = normalize_review_origin(review_data)
    if origin == 'Host':
        return status in HOST_REVIEW_POSTED_STATUSES
    if origin == 'Guest':
        return status in GUEST_REVIEW_SUBMITTED_STATUSES
    return False


def fetch_review_pages(
    client: HostawayAPIClient,
    *,
    listing_id: Optional[int] = None,
    review_type: Optional[str] = None,
    departure_date_start: Optional[date] = None,
    sort_by: Optional[str] = None,
    sort_order: Optional[str] = None,
    newer_than_review_id: Optional[int] = None,
) -> List[Dict]:
    """Fetch a complete set of review pages and fail on transport errors."""
    reviews: List[Dict] = []
    offset = 0
    while True:
        page = client.get_reviews_page(
            listing_id=listing_id,
            limit=PAGINATION_LIMIT,
            offset=offset,
            type=review_type,
            sortBy=sort_by,
            order=sort_order,
            statuses=list(API_REVIEW_SYNC_STATUSES),
            departure_date_start=(
                departure_date_start.isoformat() if departure_date_start else None
            ),
        )
        if page is None:
            raise RuntimeError(f'Hostaway review pagination failed at offset {offset}')
        if not page:
            break

        reached_existing_review = False
        for review in page:
            review_id = review.get('id')
            if newer_than_review_id is not None and review_id is not None:
                try:
                    if int(review_id) <= int(newer_than_review_id):
                        reached_existing_review = True
                        continue
                except (TypeError, ValueError):
                    pass
            reviews.append(review)

        # This early stop is only used with id-desc sorting. Once an existing ID
        # is reached, all subsequent review IDs are older than the watermark.
        if reached_existing_review or len(page) < PAGINATION_LIMIT:
            break
        offset += PAGINATION_LIMIT

    return reviews


def _deduplicate_review_payloads(review_groups: List[List[Dict]]) -> List[Dict]:
    """Merge fetched groups by Hostaway review ID, preferring the newest payload."""
    merged: Dict[int, Dict] = {}
    for group in review_groups:
        for review in group:
            try:
                review_id = int(review.get('id'))
            except (TypeError, ValueError):
                continue
            previous = merged.get(review_id)
            if previous is None:
                merged[review_id] = review
                continue
            previous_updated = parse_timestamp(previous.get('updatedOn')) or datetime.min
            current_updated = parse_timestamp(review.get('updatedOn')) or datetime.min
            if current_updated >= previous_updated:
                merged[review_id] = review
    return list(merged.values())


def _host_review_backfill_completed(session) -> bool:
    return session.query(SyncLog.sync_id).filter(
        SyncLog.sync_type == REVIEW_HOST_BACKFILL_SYNC_TYPE,
        SyncLog.status == 'success',
    ).first() is not None


def _fetch_reviews_for_sync(
    session,
    client: HostawayAPIClient,
    full_sync: bool,
    listing_id: Optional[int],
    cutoff_date: Optional[date],
) -> Tuple[List[Dict], bool, int]:
    """Fetch regular incremental rows plus the one-time host-review backfill."""
    groups: List[List[Dict]] = []
    host_backfill_requested = listing_id is None and (
        full_sync or not _host_review_backfill_completed(session)
    )
    host_backfill_count = 0

    if full_sync:
        groups.append(fetch_review_pages(client, listing_id=listing_id))
    else:
        # Re-fetch the open review window so pending/submitted records can mature
        # to published without scanning years of historical reviews.
        groups.append(fetch_review_pages(
            client,
            listing_id=listing_id,
            departure_date_start=cutoff_date,
        ))

        # Hostaway direct-booking reviews can be submitted long after checkout.
        # Scan only IDs newer than our high-water mark to catch those efficiently.
        highest_review_id = session.query(func.max(Review.review_id)).scalar()
        if highest_review_id:
            groups.append(fetch_review_pages(
                client,
                listing_id=listing_id,
                sort_by='id',
                sort_order='desc',
                newer_than_review_id=int(highest_review_id),
            ))
        else:
            groups.append(fetch_review_pages(client, listing_id=listing_id))

        if host_backfill_requested:
            host_backfill = fetch_review_pages(
                client,
                listing_id=listing_id,
                review_type='host-to-guest',
            )
            host_backfill_count = len(host_backfill)
            groups.append(host_backfill)

    return _deduplicate_review_payloads(groups), host_backfill_requested, host_backfill_count


def build_lookup_maps(session, reviews_data: Optional[List[Dict]] = None) -> Dict:
    """Build lookup maps restricted to the incoming review batch."""
    listing_ids = set()
    reservation_ids = set()
    for review in reviews_data or []:
        try:
            listing_ids.add(int(review.get('listingMapId') or review.get('listingId')))
        except (TypeError, ValueError):
            pass
        try:
            reservation_ids.add(int(review.get('reservationId') or review.get('reservation_id')))
        except (TypeError, ValueError):
            pass

    listing_query = session.query(Listing)
    reservation_query = session.query(Reservation)
    if reviews_data is not None:
        listing_query = listing_query.filter(Listing.listing_id.in_(listing_ids or {-1}))
        reservation_query = reservation_query.filter(
            Reservation.reservation_id.in_(reservation_ids or {-1})
        )
    listings = listing_query.all()
    reservations = reservation_query.all()

    guest_ids = {reservation.guest_id for reservation in reservations if reservation.guest_id}
    guest_query = session.query(Guest)
    if reviews_data is not None:
        guest_query = guest_query.filter(Guest.guest_id.in_(guest_ids or {-1}))
    guests = guest_query.all()

    return {
        'listings': {listing.listing_id: listing for listing in listings},
        'reservations_by_id': {
            reservation.reservation_id: reservation for reservation in reservations
        },
        'guests_by_id': {guest.guest_id: guest for guest in guests},
    }


def find_reservation_for_review(review_data: Dict, lookups: Dict) -> Optional[int]:
    """Resolve the review's reservation using Hostaway's authoritative ID."""
    value = review_data.get('reservationId') or review_data.get('reservation_id')
    try:
        reservation_id = int(value)
    except (TypeError, ValueError):
        return None
    return reservation_id if reservation_id in lookups['reservations_by_id'] else None


def find_guest_for_review(
    reservation_id: Optional[int],
    lookups: Dict,
) -> Optional[int]:
    """Resolve the guest through the synchronized reservation relationship."""
    reservation = lookups['reservations_by_id'].get(reservation_id)
    return reservation.guest_id if reservation and reservation.guest_id else None


def sync_review_sub_ratings(session, review_id: int, sub_ratings_data: List[Dict]) -> None:
    """Replace sub-ratings when Hostaway explicitly supplies that collection."""
    session.query(ReviewSubRating).filter(
        ReviewSubRating.review_id == review_id
    ).delete(synchronize_session=False)

    for sub_rating_data in sub_ratings_data or []:
        if not isinstance(sub_rating_data, dict):
            continue
        category = (
            sub_rating_data.get('category')
            or sub_rating_data.get('name')
            or sub_rating_data.get('type')
        )
        value = sub_rating_data.get('value')
        if value is None:
            value = sub_rating_data.get('rating')
        if value is None:
            value = sub_rating_data.get('score')
        if not category or value is None:
            continue
        session.add(ReviewSubRating(
            review_id=review_id,
            rating_category=str(category),
            rating_value=float(value),
            last_synced_at=datetime.utcnow(),
        ))


def _reviewer_name(
    review_data: Dict,
    guest_id: Optional[int],
    reservation_id: Optional[int],
    lookups: Dict,
) -> str:
    guest = lookups['guests_by_id'].get(guest_id)
    if guest:
        if guest.full_name:
            return guest.full_name
        combined = f"{guest.first_name or ''} {guest.last_name or ''}".strip()
        if combined:
            return combined
    reservation = lookups['reservations_by_id'].get(reservation_id)
    return (
        review_data.get('reviewerName')
        or review_data.get('guestName')
        or (reservation.guest_name if reservation else None)
        or 'Unknown'
    )


def _review_date(
    review_data: Dict,
    reservation_id: Optional[int],
    lookups: Dict,
) -> Optional[date]:
    value = (
        review_data.get('submittedAt')
        or review_data.get('updatedOn')
        or review_data.get('reviewDate')
        or review_data.get('review_date')
        or review_data.get('date')
    )
    parsed = parse_date(value)
    if parsed:
        return parsed
    reservation = lookups['reservations_by_id'].get(reservation_id)
    return reservation.departure_date if reservation else None


def _review_dict(review_data: Dict, lookups: Dict) -> Tuple[Optional[Dict], Optional[str]]:
    """Convert one actionable Hostaway payload to local review fields."""
    try:
        review_id = int(review_data.get('id'))
        listing_id = int(review_data.get('listingMapId') or review_data.get('listingId'))
    except (TypeError, ValueError):
        return None, 'Review is missing a valid review or listing ID'

    listing = lookups['listings'].get(listing_id)
    if not listing:
        return None, f'Listing {listing_id} is not synchronized for review {review_id}'

    reservation_id = find_reservation_for_review(review_data, lookups)
    guest_id = find_guest_for_review(reservation_id, lookups)
    reviewer_name = _reviewer_name(review_data, guest_id, reservation_id, lookups)
    reservation = lookups['reservations_by_id'].get(reservation_id)

    overall_rating = review_data.get('overallRating')
    if overall_rating is None:
        overall_rating = review_data.get('rating')
    if overall_rating is None:
        overall_rating = review_data.get('overall_rating')

    return ({
        'review_id': review_id,
        'listing_id': listing_id,
        'reservation_id': reservation_id,
        'guest_id': guest_id,
        'channel_id': review_data.get('channelId'),
        'channel_name': review_data.get('channelName') or (
            reservation.channel_name if reservation else None
        ),
        'overall_rating': overall_rating,
        'review_text': (
            review_data.get('publicReview')
            or review_data.get('reviewText')
            or review_data.get('text')
            or review_data.get('review_text')
        ),
        'private_feedback': (
            review_data.get('privateFeedback')
            or review_data.get('private_feedback')
        ),
        'reviewer_name': reviewer_name,
        'reviewer_picture': (
            review_data.get('reviewerPicture') or review_data.get('reviewer_picture')
        ),
        'review_date': _review_date(review_data, reservation_id, lookups),
        'response_text': (
            review_data.get('revieweeResponse')
            or review_data.get('responseText')
            or review_data.get('response_text')
        ),
        'response_date': parse_date(
            review_data.get('revieweeResponseDate')
            or review_data.get('responseDate')
            or review_data.get('response_date')
        ),
        'is_verified': 1 if (
            review_data.get('isVerified') or review_data.get('is_verified')
        ) else 0,
        'language': review_data.get('language'),
        'helpful_count': (
            review_data.get('helpfulCount')
            if review_data.get('helpfulCount') is not None
            else review_data.get('helpful_count') or 0
        ),
        'status': normalized_review_status(review_data),
        'origin': normalize_review_origin(review_data),
        'inserted_on': parse_timestamp(
            review_data.get('insertedOn') or review_data.get('inserted_on')
        ),
        'updated_on': parse_timestamp(
            review_data.get('updatedOn') or review_data.get('updated_on')
        ),
        'last_synced_at': datetime.utcnow(),
    }, None)


def _add_sync_log(
    session,
    *,
    sync_run_id: Optional[int],
    sync_type: str,
    sync_mode: str,
    status: str,
    started_at: datetime,
    records_processed: int = 0,
    records_created: int = 0,
    records_updated: int = 0,
    errors: Optional[List[str]] = None,
    listing_stats: Optional[Dict] = None,
) -> None:
    completed_at = datetime.utcnow()
    session.add(SyncLog(
        sync_run_id=sync_run_id,
        sync_type=sync_type,
        sync_mode=sync_mode,
        status=status,
        records_processed=records_processed,
        records_created=records_created,
        records_updated=records_updated,
        errors=json.dumps(errors) if errors else None,
        listing_stats=json.dumps(listing_stats or {}),
        started_at=started_at,
        completed_at=completed_at,
        duration_seconds=(completed_at - started_at).total_seconds(),
    ))


def sync_reviews(
    full_sync: bool = True,
    listing_id: Optional[int] = None,
    progress_tracker: Optional[Any] = None,
    sync_run_id: Optional[int] = None,
) -> Dict:
    """Synchronize submitted reviews, including automatic host-review status."""
    started_at = datetime.utcnow()
    init_models(None)
    session = get_session(None)
    progress = progress_tracker or get_progress_tracker()
    sync_mode = 'full' if full_sync else 'incremental'

    try:
        if listing_id and not session.query(Listing.listing_id).filter(
            Listing.listing_id == listing_id
        ).first():
            raise ValueError(f'Listing {listing_id} not found')

        cutoff_date = None
        if not full_sync:
            from sync.sync_manager import get_last_sync_time

            last_sync_time = get_last_sync_time('reviews')
            if last_sync_time:
                cutoff_date = (last_sync_time - timedelta(days=INCREMENTAL_OVERLAP_DAYS)).date()
            else:
                full_sync = True
                sync_mode = 'full'

        client = HostawayAPIClient()
        progress.start_phase(
            'Fetching Reviews' if full_sync else 'Fetching Recent Reviews',
            0,
        )
        all_reviews, host_backfill_requested, host_backfill_count = _fetch_reviews_for_sync(
            session,
            client,
            full_sync,
            listing_id,
            cutoff_date,
        )
        # Network pagination has already finished at this point. Updating the
        # terminal once avoids emitting thousands of carriage-return frames to
        # non-interactive service logs during a historical backfill.
        progress.update_total(1)
        progress.increment(item_name=f'Fetched {len(all_reviews)} reviews')
        progress.complete_phase()

        actionable_reviews = [review for review in all_reviews if should_sync_review_payload(review)]
        lookups = build_lookup_maps(session, actionable_reviews)
        review_ids = []
        for review in actionable_reviews:
            try:
                review_ids.append(int(review.get('id')))
            except (TypeError, ValueError):
                continue
        existing_reviews = (
            session.query(Review).filter(Review.review_id.in_(review_ids)).all()
            if review_ids else []
        )
        existing_by_id = {review.review_id: review for review in existing_reviews}

        records_created = 0
        records_updated = 0
        pending_created = 0
        pending_updated = 0
        pending_count = 0
        errors: List[str] = []
        listing_stats: Dict[int, Dict[str, int]] = {}

        progress.start_phase('Processing Reviews', len(actionable_reviews))
        for review_data in actionable_reviews:
            review_id = review_data.get('id')
            try:
                created = False
                changed = False
                # A savepoint isolates malformed records without rolling back the
                # other valid reviews waiting in this commit batch.
                with session.begin_nested():
                    values, validation_error = _review_dict(review_data, lookups)
                    if validation_error or not values:
                        raise ValueError(validation_error or 'Invalid review payload')

                    review_id = values['review_id']
                    listing = lookups['listings'][values['listing_id']]
                    existing = existing_by_id.get(review_id)
                    if existing:
                        for field, value in values.items():
                            if field == 'last_synced_at':
                                setattr(existing, field, value)
                                continue
                            if getattr(existing, field, None) != value:
                                setattr(existing, field, value)
                                changed = True
                    else:
                        existing = Review(**values)
                        session.add(existing)
                        existing_by_id[review_id] = existing
                        created = True

                    supplied_sub_ratings = next((
                        review_data[key]
                        for key in ('subRatings', 'sub_ratings', 'ratings', 'reviewCategory')
                        if key in review_data
                    ), None)
                    if supplied_sub_ratings is not None:
                        sync_review_sub_ratings(
                            session,
                            review_id,
                            supplied_sub_ratings if isinstance(supplied_sub_ratings, list) else [],
                        )

                    stats = listing_stats.setdefault(values['listing_id'], {'reviews': 0})
                    stats['reviews'] += 1
                    progress.increment(
                        created=created,
                        updated=changed,
                        item_name=f"{values['reviewer_name']} - {listing.name}",
                    )

                pending_created += int(created)
                pending_updated += int(changed)
                pending_count += 1
                if pending_count >= BATCH_SIZE:
                    try:
                        session.commit()
                        records_created += pending_created
                        records_updated += pending_updated
                    except Exception as commit_error:
                        session.rollback()
                        errors.append(f'Error committing review batch: {commit_error}')
                        logger.error('Review batch commit failed', exc_info=True)
                        existing_reviews = (
                            session.query(Review).filter(Review.review_id.in_(review_ids)).all()
                            if review_ids else []
                        )
                        existing_by_id = {review.review_id: review for review in existing_reviews}
                    pending_created = pending_updated = pending_count = 0
            except Exception as review_error:
                error_message = f'Error syncing review {review_id}: {review_error}'
                errors.append(error_message)
                progress.increment(error=True)
                logger.warning(error_message)

        if pending_count:
            try:
                session.commit()
                records_created += pending_created
                records_updated += pending_updated
            except Exception as commit_error:
                session.rollback()
                errors.append(f'Error committing final review batch: {commit_error}')
                logger.error('Final review batch commit failed', exc_info=True)
        progress.complete_phase()

        status = 'success' if not errors else 'partial'
        _add_sync_log(
            session,
            sync_run_id=sync_run_id,
            sync_type='reviews',
            sync_mode=sync_mode,
            status=status,
            started_at=started_at,
            records_processed=len(actionable_reviews),
            records_created=records_created,
            records_updated=records_updated,
            errors=errors,
            listing_stats=listing_stats,
        )
        if host_backfill_requested and not errors:
            _add_sync_log(
                session,
                sync_run_id=sync_run_id,
                sync_type=REVIEW_HOST_BACKFILL_SYNC_TYPE,
                sync_mode='backfill',
                status='success',
                started_at=started_at,
                records_processed=host_backfill_count,
            )
        session.commit()

        if VERBOSE:
            logger.info(
                'Review sync complete: fetched=%s actionable=%s created=%s updated=%s errors=%s',
                len(all_reviews),
                len(actionable_reviews),
                records_created,
                records_updated,
                len(errors),
            )
        return {
            'status': status,
            'records_fetched': len(all_reviews),
            'records_processed': len(actionable_reviews),
            'records_created': records_created,
            'records_updated': records_updated,
            'host_backfill_completed': bool(host_backfill_requested and not errors),
            'errors': errors,
            'listing_stats': listing_stats,
        }
    except Exception as error:
        session.rollback()
        error_message = f'Fatal error in sync_reviews: {error}'
        logger.error(error_message, exc_info=True)
        try:
            _add_sync_log(
                session,
                sync_run_id=sync_run_id,
                sync_type='reviews',
                sync_mode=sync_mode,
                status='error',
                started_at=started_at,
                errors=[error_message],
            )
            session.commit()
        except Exception:
            session.rollback()
        return {'status': 'error', 'error': error_message}
    finally:
        session.close()


def _main() -> int:
    parser = argparse.ArgumentParser(description='Synchronize Hostaway reviews')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--full', action='store_true', help='Fetch all submitted reviews')
    mode.add_argument('--incremental', action='store_true', help='Fetch recent and newly created reviews')
    args = parser.parse_args()
    result = sync_reviews(full_sync=not args.incremental)
    # A partial run deliberately leaves the one-time backfill marker unset so the
    # timer retries it. Return failure as well so service monitoring reflects the
    # incomplete sync instead of reporting a false success.
    return 0 if result.get('status') == 'success' else 1


if __name__ == '__main__':
    raise SystemExit(_main())
