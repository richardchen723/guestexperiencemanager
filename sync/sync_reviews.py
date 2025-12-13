#!/usr/bin/env python3
"""
Sync reviews from Hostaway API to database.
Fetches guest reviews with sub-ratings and links them to listings, reservations, and guests.
Optimized for performance with in-memory lookups and batch operations.
"""

import sys
import os
import json
import logging
from datetime import datetime, date, timedelta
from typing import List, Dict, Optional, Set, Any

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sync.api_client import HostawayAPIClient
from sync.progress_tracker import get_progress_tracker
from database.models import (
    Review, ReviewSubRating, Listing, Reservation, Guest, SyncLog,
    get_session, init_models
)
from database.schema import get_database_path
from config import VERBOSE

# Configure logging
logger = logging.getLogger(__name__)

# Constants
BATCH_SIZE = 50  # Increased batch size for better performance
PAGINATION_LIMIT = 100


def parse_timestamp(ts_str: Optional[str]) -> Optional[datetime]:
    """
    Parse timestamp string to datetime object.
    
    Args:
        ts_str: Timestamp string in various formats.
        
    Returns:
        Datetime object or None if parsing fails.
    """
    if not ts_str:
        return None
    
    formats = [
        '%Y-%m-%d %H:%M:%S',
        '%Y-%m-%dT%H:%M:%S',
        '%Y-%m-%dT%H:%M:%SZ',
        '%Y-%m-%dT%H:%M:%S.%fZ'
    ]
    
    for fmt in formats:
        try:
            return datetime.strptime(ts_str.replace('Z', ''), fmt)
        except (ValueError, AttributeError):
            continue
    
    return None


def parse_date(date_str: Optional[str]) -> Optional[date]:
    """
    Parse date string to date object.
    Handles multiple formats including YYYY-MM-DD and YYYY-MM-DD HH:MM:SS.
    
    Args:
        date_str: Date string in various formats.
        
    Returns:
        Date object or None if parsing fails.
    """
    if not date_str:
        return None
    
    try:
        # Handle YYYY-MM-DD HH:MM:SS format (from submittedAt)
        if ' ' in date_str:
            return datetime.strptime(date_str.split()[0], '%Y-%m-%d').date()
        # Handle YYYY-MM-DD format
        elif 'T' in date_str:
            return datetime.strptime(date_str.split('T')[0], '%Y-%m-%d').date()
        else:
            return datetime.strptime(date_str, '%Y-%m-%d').date()
    except (ValueError, AttributeError, TypeError):
        return None


def sync_review_sub_ratings(session, review_id: int, sub_ratings_data: List[Dict]) -> None:
    """
    Sync sub-ratings for a review.
    
    Args:
        session: Database session.
        review_id: The review ID.
        sub_ratings_data: List of sub-rating dictionaries from API.
    """
    if not sub_ratings_data:
        return
    
    # Delete existing sub-ratings for this review
    try:
        session.query(ReviewSubRating).filter(
            ReviewSubRating.review_id == review_id
        ).delete()
    except Exception as e:
        logger.warning(f"Error deleting existing sub-ratings for review {review_id}: {e}")
        return
    
    # Add new sub-ratings
    for sub_rating_data in sub_ratings_data:
        try:
            # Handle both dict format (with category/value keys) and simple format
            if isinstance(sub_rating_data, dict):
                category = sub_rating_data.get('category') or sub_rating_data.get('name') or sub_rating_data.get('type')
                value = sub_rating_data.get('value') or sub_rating_data.get('rating') or sub_rating_data.get('score')
            else:
                continue
            
            if not category or value is None:
                continue
            
            sub_rating = ReviewSubRating(
                review_id=review_id,
                rating_category=str(category),
                rating_value=float(value),
                last_synced_at=datetime.utcnow()
            )
            session.add(sub_rating)
        except (ValueError, TypeError, KeyError) as e:
            logger.warning(f"Error adding sub-rating for review {review_id}: {e}")
            continue


def build_lookup_maps(session) -> Dict:
    """
    Build in-memory lookup maps for fast O(1) access.
    
    Args:
        session: Database session.
        
    Returns:
        Dictionary with lookup maps for listings, reservations, and guests.
    """
    # Build listing lookup map
    listings = session.query(Listing).all()
    listing_map = {listing.listing_id: listing for listing in listings}
    
    # Build reservation lookup maps
    reservations = session.query(Reservation).all()
    reservation_by_id = {r.reservation_id: r for r in reservations}
    
    # Build reservation lookup by (listing_id, guest_id, departure_date)
    reservation_by_triple = {}
    for r in reservations:
        if r.listing_id and r.guest_id and r.departure_date:
            key = (r.listing_id, r.guest_id, r.departure_date)
            # Keep the most recent reservation if multiple match
            if key not in reservation_by_triple:
                reservation_by_triple[key] = r
            elif r.departure_date > reservation_by_triple[key].departure_date:
                reservation_by_triple[key] = r
    
    # Build guest lookup maps
    guests = session.query(Guest).all()
    guest_by_id = {g.guest_id: g for g in guests}
    guest_by_name = {g.full_name.lower(): g for g in guests if g.full_name}
    guest_by_email = {g.email.lower(): g for g in guests if g.email}
    
    # Build guest lookup by first/last name parts for partial matching
    guest_by_name_part = {}
    for guest in guests:
        if guest.full_name:
            name_lower = guest.full_name.lower()
            # Add each word in the name as a key
            for name_part in name_lower.split():
                if len(name_part) > 2:  # Only index substantial name parts
                    if name_part not in guest_by_name_part:
                        guest_by_name_part[name_part] = []
                    guest_by_name_part[name_part].append(guest)
        # Also try first_name and last_name separately
        if guest.first_name and len(guest.first_name) > 2:
            first_lower = guest.first_name.lower()
            if first_lower not in guest_by_name_part:
                guest_by_name_part[first_lower] = []
            guest_by_name_part[first_lower].append(guest)
        if guest.last_name and len(guest.last_name) > 2:
            last_lower = guest.last_name.lower()
            if last_lower not in guest_by_name_part:
                guest_by_name_part[last_lower] = []
            guest_by_name_part[last_lower].append(guest)
    
    return {
        'listings': listing_map,
        'reservations_by_id': reservation_by_id,
        'reservations_by_triple': reservation_by_triple,
        'guests_by_id': guest_by_id,
        'guests_by_name': guest_by_name,
        'guests_by_email': guest_by_email,
        'guests_by_name_part': guest_by_name_part
    }


def find_reservation_for_review(review_data: Dict, listing_id: int, lookups: Dict) -> Optional[int]:
    """
    Find reservation ID for a review using in-memory lookups.
    
    Args:
        review_data: Review data from API.
        listing_id: The listing ID.
        lookups: Lookup maps from build_lookup_maps.
        
    Returns:
        Reservation ID if found, None otherwise.
    """
    # Try to get reservation_id directly from review data
    reservation_id = review_data.get('reservationId') or review_data.get('reservation_id')
    if reservation_id and reservation_id in lookups['reservations_by_id']:
        return reservation_id
    
    # Try to match by guest and dates
    reviewer_name = review_data.get('reviewerName') or review_data.get('reviewer_name')
    review_date = parse_date(review_data.get('reviewDate') or review_data.get('review_date'))
    
    if reviewer_name and review_date:
        # Find guest by name (case-insensitive)
        guest = lookups['guests_by_name'].get(reviewer_name.lower())
        
        if guest:
            # Try to find reservation by guest, listing, and check-out date
            # Review usually happens after stay, so check dates before review_date
            for (l_id, g_id, dep_date), reservation in lookups['reservations_by_triple'].items():
                if l_id == listing_id and g_id == guest.guest_id and dep_date <= review_date:
                    return reservation.reservation_id
    
    return None


def find_guest_for_review(review_data: Dict, reservation_id: Optional[int], lookups: Dict) -> Optional[int]:
    """
    Find guest ID for a review using in-memory lookups.
    Uses multiple strategies to maximize guest matching.
    
    Args:
        review_data: Review data from API.
        reservation_id: Optional reservation ID to get guest from.
        lookups: Lookup maps from build_lookup_maps.
        
    Returns:
        Guest ID if found, None otherwise.
    """
    # First try to get guest from reservation
    if reservation_id and reservation_id in lookups['reservations_by_id']:
        reservation = lookups['reservations_by_id'][reservation_id]
        if reservation.guest_id:
            return reservation.guest_id
    
    # Try to find guest by reviewer name (exact match, case-insensitive)
    reviewer_name = review_data.get('reviewerName') or review_data.get('reviewer_name')
    if reviewer_name:
        reviewer_name_lower = reviewer_name.lower()
        guest = lookups['guests_by_name'].get(reviewer_name_lower)
        if guest:
            return guest.guest_id
        
        # Try partial name matching using name parts index
        name_parts = reviewer_name_lower.split()
        for name_part in name_parts:
            if len(name_part) > 2 and name_part in lookups['guests_by_name_part']:
                # Found guests with matching name part, return the first one
                # (in a real system, you might want to score/rank matches)
                matching_guests = lookups['guests_by_name_part'][name_part]
                if matching_guests:
                    return matching_guests[0].guest_id
    
    # Try to find guest by reviewer email if available
    reviewer_email = review_data.get('reviewerEmail') or review_data.get('reviewer_email') or review_data.get('email')
    if reviewer_email and reviewer_email.lower() in lookups['guests_by_email']:
        guest = lookups['guests_by_email'][reviewer_email.lower()]
        return guest.guest_id
    
    return None


def sync_reviews(full_sync: bool = True, listing_id: Optional[int] = None, progress_tracker: Optional[Any] = None, sync_run_id: Optional[int] = None) -> Dict:
    """
    Sync reviews from Hostaway API.
    Optimized for performance with in-memory lookups and batch operations.
    
    Args:
        full_sync: Whether to perform a full sync (currently unused but kept for API consistency).
        listing_id: Optional listing ID to sync reviews for specific listing only.
        
    Returns:
        Dictionary with sync results including status, counts, and errors.
    """
    start_time = datetime.utcnow()
    # For PostgreSQL, get_database_path() returns None - that's OK, functions use DATABASE_URL
    # Initialize database
    init_models(None)  # None is OK for PostgreSQL, function uses DATABASE_URL from environment
    session = get_session(None)  # None is OK for PostgreSQL, function uses DATABASE_URL from environment
    
    try:
        client = HostawayAPIClient()
        
        # Calculate cutoff time for incremental sync
        cutoff_time = None
        cutoff_date = None
        cutoff_datetime = None
        if not full_sync:
            from sync.sync_manager import get_last_sync_time
            last_sync_time = get_last_sync_time('reviews')
            if last_sync_time:
                # Calculate cutoff: t1 - 20 days
                cutoff_time = last_sync_time - timedelta(days=20)
                # Use datetime for comparison with review submission dates (submittedAt/reviewDate)
                # Reviews can be submitted days/weeks after departure, so we should use submission date, not departure date
                cutoff_datetime = cutoff_time
                # Also keep cutoff_date for backward compatibility with departureDate filtering (but don't use it for early-stop)
                cutoff_date = cutoff_time.date()
                if VERBOSE:
                    logger.info(f"Incremental review sync: Filtering reviews with submission date >= {cutoff_datetime} (cutoff = {last_sync_time} - 20 days)")
            else:
                # No previous sync - treat as full sync
                if VERBOSE:
                    logger.info("No previous review sync found. Performing full sync.")
                full_sync = True
        
        # Pre-load all lookups into memory for fast access
        if VERBOSE:
            logger.info("Building lookup maps...")
        lookups = build_lookup_maps(session)
        
        # Get listings to sync reviews for
        if listing_id:
            if listing_id not in lookups['listings']:
                return {'status': 'error', 'error': f'Listing {listing_id} not found'}
            listings = [lookups['listings'][listing_id]]
        else:
            listings = list(lookups['listings'].values())
        
        if VERBOSE:
            logger.info(f"Syncing reviews for {len(listings)} listings...")
        
        # Use provided progress tracker or default
        if progress_tracker:
            progress = progress_tracker
        else:
            progress = get_progress_tracker()
        
        all_reviews: List[Dict] = []
        errors: List[str] = []
        listing_stats = {}  # {listing_id: {reviews: count}}
        reviews_skipped_cutoff = 0  # Track reviews skipped due to cutoff
        
        # Fetch reviews for each listing (or try to fetch all at once)
        # Start with unknown total (0), will update dynamically as we fetch reviews
        phase_name = "Fetching Reviews (Incremental)" if not full_sync and cutoff_date else "Fetching Reviews"
        progress.start_phase(phase_name, 0)
        
        # Try to fetch all reviews at once first (if API supports it)
        # Note: We don't use status filter since API ignores it - we filter client-side instead
        # Only fetch guest-to-host reviews (filter at API level for efficiency)
        bulk_fetch_succeeded = False
        try:
            if VERBOSE:
                logger.info(f"Starting bulk fetch of guest-to-host reviews (full_sync={full_sync})...")
            offset = 0
            while True:
                # For incremental sync, use sorting by review submission date (submittedAt) DESC for early-stop optimization
                # This is more accurate than departureDate since reviews can be submitted days/weeks after departure
                # Only sync guest-to-host reviews (filter at API level for efficiency)
                if not full_sync and cutoff_datetime:
                    # Try to sort by submittedAt if API supports it, otherwise use departureDate
                    reviews = client.get_reviews(
                        limit=PAGINATION_LIMIT, 
                        offset=offset, 
                        sortBy='departureDate',  # API may not support submittedAt sorting, so use departureDate as fallback
                        order='desc',
                        type='guest-to-host'  # Only fetch guest-to-host reviews
                    )
                else:
                    reviews = client.get_reviews(
                        limit=PAGINATION_LIMIT, 
                        offset=offset,
                        type='guest-to-host'  # Only fetch guest-to-host reviews
                    )
                if not reviews:
                    break
                
                # For incremental sync, filter by cutoff datetime using review submission date
                # This is more accurate than departureDate since reviews can be submitted after departure
                if not full_sync and cutoff_datetime:
                    filtered_reviews = []
                    should_stop = False
                    for review in reviews:
                        # Get review submission date (submittedAt is preferred, fallback to reviewDate or updatedOn)
                        submission_date_str = (
                            review.get('submittedAt') or 
                            review.get('reviewDate') or 
                            review.get('updatedOn') or
                            review.get('review_date')
                        )
                        departure_date_str = review.get('departureDate')
                        
                        # Use submission date for filtering (more accurate for incremental sync)
                        if submission_date_str:
                            submission_datetime = parse_timestamp(submission_date_str)
                            if submission_datetime:
                                if submission_datetime >= cutoff_datetime:
                                    filtered_reviews.append(review)
                                else:
                                    # Since reviews are sorted DESC, once we hit reviews below cutoff, stop fetching
                                    # However, we can't rely on early-stop since we're sorting by departureDate but filtering by submission date
                                    # So we'll continue fetching but filter them out
                                    reviews_skipped_cutoff += 1
                                    # Don't break here - continue fetching since submission dates may be later than departure dates
                            else:
                                # Can't parse submission date - check departure date as fallback
                                if departure_date_str:
                                    departure_date = parse_date(departure_date_str)
                                    if departure_date and departure_date >= cutoff_date:
                                        filtered_reviews.append(review)
                                    else:
                                        reviews_skipped_cutoff += 1
                                else:
                                    # No dates - include it anyway (will be filtered by status later)
                                    filtered_reviews.append(review)
                        elif departure_date_str:
                            # No submission date, fallback to departure date
                            departure_date = parse_date(departure_date_str)
                            if departure_date and departure_date >= cutoff_date:
                                filtered_reviews.append(review)
                            else:
                                reviews_skipped_cutoff += 1
                        else:
                            # No dates at all - include it anyway (will be filtered by status later)
                            filtered_reviews.append(review)
                    
                    reviews = filtered_reviews
                    # Don't use early-stop since we're filtering by submission date but sorting by departure date
                    # Reviews can be submitted days/weeks after departure, so we need to fetch all pages
                    # Continue fetching until we get no more results
                
                all_reviews.extend(reviews)
                
                # Stop if we got fewer reviews than requested (end of data)
                if len(reviews) < PAGINATION_LIMIT:
                    break
                
                reviews_after = len(all_reviews)
                
                # Update total: increment it as we discover more reviews
                # This gives a dynamic progress bar that updates as we discover more reviews
                progress.update_total(reviews_after)
                
                # Increment progress for this batch of reviews
                # We increment by the number of reviews in this batch
                for _ in range(len(reviews)):
                    progress.increment(
                        allow_exceed_total=True,
                        item_name=f"Fetched {reviews_after} reviews..."
                    )
                
                # Increment offset for next batch
                offset += PAGINATION_LIMIT
            # Mark all listings as processed since we fetched all reviews at once
            bulk_fetch_succeeded = True
            if VERBOSE:
                logger.info(f"Bulk fetch completed: fetched {len(all_reviews)} reviews (before deduplication)")
        except Exception:
            # Fall back to per-listing fetching if bulk fetch doesn't work
            if VERBOSE:
                logger.info("Bulk fetch not available, fetching per listing...")
            # Only do per-listing fetch if bulk fetch didn't succeed
            # Note: We don't use status filter since API ignores it - we filter client-side instead
            if not bulk_fetch_succeeded:
                all_reviews = []
                for listing in listings:
                    try:
                        listing_offset = 0
                        listing_reviews = []
                        while True:
                            # For incremental sync, use sorting by departureDate DESC for early-stop optimization
                            # Only sync guest-to-host reviews (filter at API level for efficiency)
                            if not full_sync and cutoff_date:
                                reviews = client.get_reviews(
                                    listing_id=listing.listing_id,
                                    limit=PAGINATION_LIMIT,
                                    offset=listing_offset,
                                    sortBy='departureDate',
                                    order='desc',
                                    type='guest-to-host'  # Only fetch guest-to-host reviews
                                )
                            else:
                                reviews = client.get_reviews(
                                    listing_id=listing.listing_id,
                                    limit=PAGINATION_LIMIT,
                                    offset=listing_offset,
                                    type='guest-to-host'  # Only fetch guest-to-host reviews
                                )
                            if not reviews:
                                break
                            
                            # For incremental sync, filter by cutoff date and stop if we hit reviews below cutoff
                            if not full_sync and cutoff_date:
                                filtered_reviews = []
                                should_stop = False
                                for review in reviews:
                                    departure_date_str = review.get('departureDate')
                                    if departure_date_str:
                                        departure_date = parse_date(departure_date_str)
                                        if departure_date:
                                            if departure_date >= cutoff_date:
                                                filtered_reviews.append(review)
                                            else:
                                                # Since reviews are sorted DESC by departureDate,
                                                # once we hit a review below cutoff, all subsequent reviews will also be below cutoff
                                                should_stop = True
                                                reviews_skipped_cutoff += 1
                                                break
                                        else:
                                            # Can't parse date - include it anyway (will be filtered by status later)
                                            filtered_reviews.append(review)
                                    else:
                                        # No departureDate - include it anyway (will be filtered by status later)
                                        filtered_reviews.append(review)
                                
                                reviews = filtered_reviews
                                if should_stop:
                                    # We've hit reviews below cutoff, stop fetching for this listing
                                    if VERBOSE:
                                        logger.info(f"Reached cutoff date {cutoff_date} for listing {listing.listing_id}. Stopping fetch.")
                                    break
                            
                            listing_reviews.extend(reviews)
                            if len(reviews) < PAGINATION_LIMIT:
                                # Last page - we're done for this listing
                                break
                            listing_offset += PAGINATION_LIMIT
                        
                        # Add reviews to total and update progress
                        all_reviews.extend(listing_reviews)
                        reviews_after = len(all_reviews)
                        
                        # Update total
                        progress.update_total(reviews_after)
                        
                        # Increment for each review in this listing's batch
                        for _ in range(len(listing_reviews)):
                            progress.increment(
                                allow_exceed_total=True,
                                item_name=f"{listing.name}: {reviews_after} reviews..."
                            )
                    except Exception as e:
                        error_msg = f"Error fetching reviews for listing {listing.listing_id}: {str(e)}"
                        errors.append(error_msg)
                        progress.increment(error=True, allow_exceed_total=True)
                        logger.warning(error_msg)
                        continue
        
        progress.complete_phase()
        
        if VERBOSE:
            if not full_sync and cutoff_date:
                logger.info(f"Found {len(all_reviews)} reviews before deduplication (cutoff: {cutoff_date}, skipped {reviews_skipped_cutoff} reviews below cutoff)")
            else:
                logger.info(f"Found {len(all_reviews)} reviews before deduplication")
        
        # DEDUPLICATE: Remove duplicate reviews by review_id
        # The API might return the same review multiple times (e.g., for different listings)
        seen_review_ids = set()
        deduplicated_reviews = []
        duplicates_removed = 0
        
        for review_data in all_reviews:
            review_id = review_data.get('id')
            if not review_id:
                continue
            
            # If we've seen this review_id before, skip it
            if review_id in seen_review_ids:
                duplicates_removed += 1
                if VERBOSE and duplicates_removed <= 10:  # Log first 10 duplicates
                    logger.warning(f"Duplicate review_id {review_id} found, skipping")
                continue
            
            seen_review_ids.add(review_id)
            deduplicated_reviews.append(review_data)
        
        all_reviews = deduplicated_reviews
        
        if VERBOSE:
            logger.info(f"After deduplication: {len(all_reviews)} unique reviews (removed {duplicates_removed} duplicates)")
        
        # MEMORY OPTIMIZATION: Load only IDs for existing reviews (90% memory reduction)
        # Pre-load existing reviews into memory for faster lookups - only load IDs
        existing_review_data = session.query(Review.review_id).all()
        existing_review_ids = {r.review_id for r in existing_review_data}
        del existing_review_data
        # For reviews, we only need to check if they exist, so we don't need full objects
        # We'll query full review objects only when we need to update them
        
        # Start processing phase
        progress.start_phase("Processing Reviews", len(all_reviews))
        
        records_created = 0
        records_updated = 0
        batch_count = 0
        
        # Track reviews processed in this batch to avoid duplicates within the batch
        processed_in_batch = set()
        
        # Track pending changes for this batch (to handle rollback correctly)
        batch_created = 0
        batch_updated = 0
        # Track review_ids added to existing_review_ids in this batch (for cleanup on rollback)
        batch_created_review_ids = []
        
        # Process each review
        for review_data in all_reviews:
            try:
                review_id = review_data.get('id')
                if not review_id:
                    continue
                
                # Get review status - we filter client-side since API ignores status parameter
                review_status = (
                    review_data.get('status') or 
                    review_data.get('reviewStatus') or 
                    review_data.get('publicationStatus')
                )
                
                # Normalize status to lowercase for storage
                review_status_lower = str(review_status).lower().strip() if review_status else 'published'
                
                
                # Client-side filtering: Only process reviews with 'published' or 'submitted' status
                # The API doesn't reliably filter by status, so we fetch all reviews and filter client-side
                if not review_status or review_status_lower not in ['published', 'submitted']:
                    if review_status:
                        logger.debug(f"Review {review_id} has status '{review_status}' (not 'published' or 'submitted'), skipping")
                    else:
                        logger.debug(f"Review {review_id} has no status field, skipping (only processing 'published' or 'submitted' reviews)")
                    progress.increment()
                    continue
                
                # Skip if we've already processed this review_id in this batch
                if review_id in processed_in_batch:
                    if VERBOSE:
                        logger.warning(f"Review {review_id} already processed in this batch, skipping")
                    continue
                
                processed_in_batch.add(review_id)
                
                # Get listing ID from review
                listing_map_id = review_data.get('listingMapId') or review_data.get('listingId')
                if not listing_map_id:
                    logger.debug(f"Review {review_id} skipped: no listing_map_id")
                    progress.increment()
                    continue
                
                # Find listing using lookup map
                listing = lookups['listings'].get(listing_map_id)
                if not listing:
                    logger.warning(f"Listing {listing_map_id} not found for review {review_id}")
                    logger.debug(f"Review {review_id} skipped: listing {listing_map_id} not in lookup map")
                    progress.increment()
                    continue
                
                # Find reservation and guest using lookup maps
                reservation_id = find_reservation_for_review(review_data, listing_map_id, lookups)
                guest_id = find_guest_for_review(review_data, reservation_id, lookups)
                
                # Determine reviewer name: prefer guest name from DB, then API, then reservation guest name
                reviewer_name = review_data.get('reviewerName') or review_data.get('reviewer_name')
                
                # If we found a guest, use their name from the database
                if guest_id and guest_id in lookups['guests_by_id']:
                    guest = lookups['guests_by_id'][guest_id]
                    if guest.full_name:
                        reviewer_name = guest.full_name
                    elif guest.first_name or guest.last_name:
                        reviewer_name = f"{guest.first_name or ''} {guest.last_name or ''}".strip()
                
                # If still no name, try to get it from reservation
                if not reviewer_name and reservation_id and reservation_id in lookups['reservations_by_id']:
                    reservation = lookups['reservations_by_id'][reservation_id]
                    if reservation.guest_name:
                        reviewer_name = reservation.guest_name
                
                # Fall back to API reviewer name or "Unknown"
                reviewer_name = reviewer_name or 'Unknown'
                
                # Determine review_date: Use actual review submission date from API
                # For submitted reviews, submittedAt might be None, so use updatedOn as fallback
                # Only use departure_date as last resort if no API date is available
                review_date = parse_date(
                    review_data.get('submittedAt') or  # Primary field: actual submission date
                    review_data.get('updatedOn') or    # Fallback: for submitted reviews, this is likely submission time
                    review_data.get('reviewDate') or 
                    review_data.get('review_date') or
                    review_data.get('date')
                )
                
                # Last resort: use departure_date from reservation if no API date available
                if not review_date and reservation_id and reservation_id in lookups['reservations_by_id']:
                    reservation = lookups['reservations_by_id'][reservation_id]
                    if reservation.departure_date:
                        review_date = reservation.departure_date
                
                # MEMORY OPTIMIZATION: Check if review exists using lightweight lookup, then query if needed
                review_exists = review_id in existing_review_ids
                existing_review = None
                if review_exists:
                    # Query the review only when we need to update it
                    existing_review = session.query(Review).filter(Review.review_id == review_id).first()
                
                # Get origin field from API response
                # The API uses 'type' field with values like "guest-to-host", "host-to-guest", etc.
                origin = None
                
                # First try the 'type' field (primary source)
                review_type = review_data.get('type')
                if review_type:
                    # Map API type values to origin values
                    type_lower = str(review_type).lower().strip()
                    if type_lower == 'guest-to-host':
                        origin = 'Guest'
                    elif type_lower == 'host-to-guest':
                        origin = 'Host'
                    elif type_lower in ['guest', 'host', 'admin']:
                        origin = review_type.capitalize()
                
                # Fallback to other possible fields
                if not origin:
                    origin = (
                        review_data.get('origin') or 
                        review_data.get('reviewerType') or 
                        review_data.get('reviewer_type') or
                        review_data.get('source') or
                        None
                    )
                    # Normalize origin (capitalize first letter if present)
                    if origin:
                        origin = str(origin).strip()
                        if origin.lower() in ['guest', 'host', 'admin']:
                            origin = origin.capitalize()
                
                # Get channel_name from review data, or fallback to reservation
                channel_name = review_data.get('channelName')
                if not channel_name and reservation_id and reservation_id in lookups['reservations_by_id']:
                    reservation = lookups['reservations_by_id'][reservation_id]
                    if reservation and reservation.channel_name:
                        channel_name = reservation.channel_name
                
                # Prepare review data
                review_dict = {
                    'review_id': review_id,
                    'listing_id': listing_map_id,
                    'reservation_id': reservation_id,
                    'guest_id': guest_id,
                    'channel_id': review_data.get('channelId'),
                    'channel_name': channel_name,
                    'overall_rating': review_data.get('overallRating') or review_data.get('rating') or review_data.get('overall_rating'),
                    'review_text': (
                        review_data.get('publicReview') or  # Primary field from API
                        review_data.get('reviewText') or 
                        review_data.get('text') or 
                        review_data.get('review_text')
                    ),
                    'reviewer_name': reviewer_name,
                    'reviewer_picture': review_data.get('reviewerPicture') or review_data.get('reviewer_picture'),
                    'review_date': review_date,
                    'response_text': (
                        review_data.get('revieweeResponse') or  # Primary field from API
                        review_data.get('responseText') or 
                        review_data.get('response_text')
                    ),
                    'response_date': parse_date(
                        review_data.get('revieweeResponseDate') or  # Check if this exists
                        review_data.get('responseDate') or 
                        review_data.get('response_date')
                    ),
                    'is_verified': 1 if review_data.get('isVerified') or review_data.get('is_verified') else 0,
                    'language': review_data.get('language'),
                    'helpful_count': review_data.get('helpfulCount') or review_data.get('helpful_count') or 0,
                    'status': review_status_lower,  # Store normalized status
                    'origin': origin,  # Store origin field
                    'inserted_on': parse_timestamp(review_data.get('insertedOn') or review_data.get('inserted_on')),
                    'updated_on': parse_timestamp(review_data.get('updatedOn') or review_data.get('updated_on')),
                    'last_synced_at': datetime.utcnow()
                }
                
                if existing_review:
                    # Check if any fields actually changed (excluding last_synced_at)
                    has_changes = False
                    for key, value in review_dict.items():
                        if key == 'last_synced_at':
                            # Always update this, but don't count as a change
                            setattr(existing_review, key, value)
                            continue
                        
                        current_value = getattr(existing_review, key, None)
                        if current_value != value:
                            setattr(existing_review, key, value)
                            has_changes = True
                    
                    if has_changes:
                        batch_updated += 1
                        logger.debug(f"Review {review_id} will be updated (has_changes=True)")
                        # Increment once with item name and updated flag
                        progress.increment(updated=True, item_name=f"{reviewer_name} - {listing.name}")
                    else:
                        logger.debug(f"Review {review_id} skipped: exists but no changes")
                        # No changes, just increment progress
                        progress.increment(item_name=f"{reviewer_name} - {listing.name}")
                else:
                    # Create new review
                    logger.debug(f"Review {review_id} will be created (new review)")
                    review = Review(**review_dict)
                    session.add(review)
                    # MEMORY OPTIMIZATION: Update lightweight lookup set when creating new review
                    existing_review_ids.add(review_id)
                    batch_created_review_ids.append(review_id)  # Track for cleanup on rollback
                    batch_created += 1
                    # Increment once with item name and created flag
                    progress.increment(created=True, item_name=f"{reviewer_name} - {listing.name}")
                
                # Track per-listing statistics
                if listing_map_id:
                    if listing_map_id not in listing_stats:
                        listing_stats[listing_map_id] = {'reviews': 0}
                    listing_stats[listing_map_id]['reviews'] += 1
                
                # Sync sub-ratings
                sub_ratings_data = (
                    review_data.get('subRatings') or 
                    review_data.get('sub_ratings') or 
                    review_data.get('ratings') or
                    []
                )
                if sub_ratings_data:
                    try:
                        sync_review_sub_ratings(session, review_id, sub_ratings_data)
                    except Exception as e:
                        logger.warning(f"Error syncing sub-ratings for review {review_id}: {e}")
                        # Don't fail the entire review if sub-ratings fail
                
                # Commit in batches to avoid database locking
                batch_count += 1
                logger.debug(f"After processing review {review_id}: batch_created={batch_created}, batch_updated={batch_updated}, batch_count={batch_count}")
                if batch_count >= BATCH_SIZE:
                    try:
                        session.commit()
                        # Only increment counters after successful commit
                        records_created += batch_created
                        records_updated += batch_updated
                        logger.debug(f"Batch commit successful: {batch_created} created, {batch_updated} updated in this batch")
                        batch_count = 0
                        batch_created = 0
                        batch_updated = 0
                        batch_created_review_ids = []  # Reset tracking list after successful commit
                    except Exception as e:
                        session.rollback()
                        error_msg = f"Error committing batch: {str(e)}"
                        errors.append(error_msg)
                        logger.error(f"Batch commit failed (lost {batch_created} created, {batch_updated} updated): {error_msg}", exc_info=True)
                        # MEMORY OPTIMIZATION: Remove reviews from existing_review_ids that were rolled back
                        # This is critical: if we don't remove them, subsequent processing will think they exist
                        for review_id in batch_created_review_ids:
                            existing_review_ids.discard(review_id)
                            logger.debug(f"Removed review {review_id} from existing_review_ids after rollback")
                        # Reset batch counters since commit failed (reviews were rolled back)
                        batch_count = 0
                        batch_created = 0
                        batch_updated = 0
                        batch_created_review_ids = []  # Reset tracking list
                
            except Exception as e:
                error_msg = f"Error syncing review {review_data.get('id')}: {str(e)}"
                errors.append(error_msg)
                progress.increment(error=True)
                session.rollback()  # Rollback on error
                logger.warning(error_msg)
                continue
        
        # Complete progress tracking
        progress.complete_phase()
        
        # Commit remaining changes
        try:
            session.commit()
            # Add any remaining batch changes to counters after successful final commit
            records_created += batch_created
            records_updated += batch_updated
            logger.debug(f"Final commit successful: {batch_created} created, {batch_updated} updated in final batch")
            batch_created_review_ids = []  # Reset tracking list after successful commit
        except Exception as e:
            session.rollback()
            error_msg = f"Error in final commit: {str(e)}"
            errors.append(error_msg)
            logger.error(f"Final commit failed (lost {batch_created} created, {batch_updated} updated): {error_msg}", exc_info=True)
            # MEMORY OPTIMIZATION: Remove reviews from existing_review_ids that were rolled back
            for review_id in batch_created_review_ids:
                existing_review_ids.discard(review_id)
                logger.debug(f"Removed review {review_id} from existing_review_ids after final commit rollback")
            batch_created_review_ids = []  # Reset tracking list
        
        # Log sync operation
        end_time = datetime.utcnow()
        duration = (end_time - start_time).total_seconds()
        
        sync_log = SyncLog(
            sync_run_id=sync_run_id,
            sync_type='reviews',
            sync_mode='full' if full_sync else 'incremental',
            status='success' if not errors else 'partial',
            records_processed=len(all_reviews),
            records_created=records_created,
            records_updated=records_updated,
            errors=json.dumps(errors) if errors else None,
            listing_stats=json.dumps(listing_stats),
            started_at=start_time,
            completed_at=end_time,
            duration_seconds=duration
        )
        session.add(sync_log)
        session.commit()
        
        if VERBOSE:
            sync_mode = "incremental" if not full_sync and cutoff_date else "full"
            log_msg = (
                f"Sync complete ({sync_mode}): {len(all_reviews)} processed, "
                f"{records_created} created, {records_updated} updated, "
                f"{len(errors)} errors, {duration:.2f}s"
            )
            if not full_sync and cutoff_date:
                log_msg += f", {reviews_skipped_cutoff} reviews skipped (below cutoff {cutoff_date})"
            logger.info(log_msg)
        
        return {
            'status': 'success' if not errors else 'partial',
            'records_processed': len(all_reviews),
            'records_created': records_created,
            'records_updated': records_updated,
            'errors': errors,
            'listing_stats': listing_stats
        }
        
    except Exception as e:
        session.rollback()
        error_msg = f"Fatal error in sync_reviews: {str(e)}"
        logger.error(error_msg, exc_info=True)
        
        # Log error
        try:
            sync_log = SyncLog(
                sync_run_id=sync_run_id,
                sync_type='reviews',
                sync_mode='full' if full_sync else 'incremental',
                status='error',
                records_processed=0,
                errors=json.dumps([error_msg]),
                listing_stats=json.dumps({}),
                started_at=start_time,
                completed_at=datetime.utcnow(),
                duration_seconds=(datetime.utcnow() - start_time).total_seconds()
            )
            session.add(sync_log)
            session.commit()
        except Exception:
            pass  # If we can't log, at least we tried
        
        return {
            'status': 'error',
            'error': error_msg
        }
    
    finally:
        session.close()


if __name__ == "__main__":
    sync_reviews(full_sync=True)