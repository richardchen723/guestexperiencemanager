#!/usr/bin/env python3
"""
Sync listings from Hostaway API to database.
Fetches listing data and photo metadata.
"""

import sys
import os
import json
import logging
from datetime import datetime
from typing import List, Dict, Optional, Any

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sync.api_client import HostawayAPIClient
from sync.progress_tracker import get_progress_tracker
from database.models import Listing, ListingPhoto, ListingTag, SyncLog, Tag, get_session, init_models
from database.schema import get_database_path
from config import STORE_PHOTO_METADATA, VERBOSE

# Configure logging
logger = logging.getLogger(__name__)

# Constants
BATCH_SIZE = 10
PAGINATION_LIMIT = 100
LISTING_SYNC_MANAGED_TAG_NAMES = {
    "enchanted havens",
    "luminary resorts",
    "luminary resort",
    "pt300",
    "urban stays",
    "urbans stays",
    "middlefork",
    "middlefork ridge",
    "crockett's run",
    "crockett’s run",
    "crocketts run",
    "san gabriel units",
    "crestwood",
    "alpine cabins",
    "smoky cabins",
}
DETAIL_PROFILE_FIELDS = {
    "accommodates",
    "bedrooms",
    "bathrooms",
    "beds",
    "base_price",
}


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
    
    # Try different timestamp formats
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


def sync_listing_photos(session, listing_id: int, photos_data: List[Dict]) -> None:
    """
    Sync photos for a listing using upsert pattern (idempotent).
    
    This function is idempotent - safe to run multiple times.
    It updates existing photos by URL and creates new ones, only deleting photos
    that are no longer in the API response.
    
    Args:
        session: Database session.
        listing_id: The listing ID.
        photos_data: List of photo data dictionaries from API.
    """
    if not STORE_PHOTO_METADATA or not photos_data:
        return
    
    # Pre-load existing photos for this listing (keyed by photo_url for deduplication)
    existing_photos = session.query(ListingPhoto).filter(
        ListingPhoto.listing_id == listing_id
    ).all()
    existing_photos_by_url = {photo.photo_url: photo for photo in existing_photos if photo.photo_url}
    
    # Track which photos we've processed from API
    processed_photo_urls = set()
    
    # Upsert photos: update existing or create new
    for idx, photo_data in enumerate(photos_data):
        try:
            photo_url = photo_data.get('url', '')
            if not photo_url:
                continue
            
            processed_photo_urls.add(photo_url)
            
            # Check if photo already exists by URL
            existing_photo = existing_photos_by_url.get(photo_url)
            
            if existing_photo:
                # Update existing photo
                existing_photo.thumbnail_url = photo_data.get('thumbnailUrl', '') or existing_photo.thumbnail_url
                existing_photo.photo_type = photo_data.get('type', '') or existing_photo.photo_type
                existing_photo.display_order = photo_data.get('displayOrder', idx)
                existing_photo.caption = photo_data.get('caption', '') or existing_photo.caption
                existing_photo.width = photo_data.get('width') or existing_photo.width
                existing_photo.height = photo_data.get('height') or existing_photo.height
                existing_photo.last_synced_at = datetime.utcnow()
            else:
                # Create new photo
                photo = ListingPhoto(
                    listing_id=listing_id,
                    photo_url=photo_url,
                    thumbnail_url=photo_data.get('thumbnailUrl', ''),
                    photo_type=photo_data.get('type', ''),
                    display_order=photo_data.get('displayOrder', idx),
                    caption=photo_data.get('caption', ''),
                    width=photo_data.get('width'),
                    height=photo_data.get('height'),
                    last_synced_at=datetime.utcnow()
                )
                session.add(photo)
                # Add to lookup map for future reference in this batch
                existing_photos_by_url[photo_url] = photo
        except Exception as e:
            logger.warning(f"Error upserting photo {idx} for listing {listing_id}: {e}")
            continue
    
    # Delete photos that are no longer in API response (idempotent cleanup)
    photos_to_delete = [
        photo for photo_url, photo in existing_photos_by_url.items()
        if photo_url not in processed_photo_urls
    ]
    
    for photo in photos_to_delete:
        try:
            session.delete(photo)
        except Exception as e:
            logger.warning(f"Error deleting photo {photo.photo_id} for listing {listing_id}: {e}")
            continue


def listing_tag_names(listing_data: Dict) -> List[str]:
    """Return normalized Hostaway listing tag names from listing payload data."""
    tag_rows = listing_data.get("listingTags") or listing_data.get("tags") or []
    if not isinstance(tag_rows, list):
        return []
    names = []
    for tag_data in tag_rows:
        raw_name = tag_data.get("name") if isinstance(tag_data, dict) else str(tag_data or "")
        try:
            names.append(Tag.normalize_name(raw_name))
        except ValueError:
            continue
    return sorted(set(names))


def listing_field_value(listing_data: Dict, *keys: str):
    """Return the first non-null Hostaway field across summary/detail aliases."""
    for key in keys:
        value = listing_data.get(key)
        if value is not None:
            return value
    return None


def enrich_listing_data(client: HostawayAPIClient, listing_data: Dict) -> Dict:
    """Merge the listing-detail payload when the list response omits property profile fields."""
    listing_id = listing_data.get("id")
    profile_values = (
        listing_field_value(listing_data, "personCapacity", "accommodates"),
        listing_field_value(listing_data, "bedroomsNumber", "bedrooms"),
        listing_field_value(listing_data, "bathroomsNumber", "bathrooms"),
        listing_field_value(listing_data, "bedsNumber", "beds"),
    )
    if not listing_id or all(value is not None for value in profile_values):
        return listing_data
    detail = client.get_listing(int(listing_id))
    if not isinstance(detail, dict) or not detail:
        return listing_data
    return {**listing_data, **detail}


def sync_listing_tags(session, listing_id: int, listing_data: Dict) -> None:
    """Sync Hostaway listing tags into local tag tables."""
    if "listingTags" not in listing_data and "tags" not in listing_data:
        return

    incoming_names = set(listing_tag_names(listing_data))
    existing_rows = (
        session.query(ListingTag)
        .filter(ListingTag.listing_id == listing_id)
        .all()
    )

    for row in existing_rows:
        tag_name = row.tag.name if row.tag else None
        if tag_name in LISTING_SYNC_MANAGED_TAG_NAMES and tag_name not in incoming_names:
            session.delete(row)

    for tag_name in incoming_names:
        tag = session.query(Tag).filter(Tag.name == tag_name).first()
        if not tag:
            tag = Tag(name=tag_name)
            session.add(tag)
            session.flush()
        existing = (
            session.query(ListingTag)
            .filter(ListingTag.listing_id == listing_id, ListingTag.tag_id == tag.tag_id)
            .first()
        )
        if not existing:
            session.add(ListingTag(listing_id=listing_id, tag_id=tag.tag_id))


def mark_missing_listings_deleted(
    existing_listings: List[Listing],
    received_listing_ids: set[int],
    synced_at: Optional[datetime] = None,
) -> int:
    """Mark local listings absent from a complete Hostaway listing response as deleted.

    We retain each listing and its historical reservations/reviews, but ``deleted``
    listings are excluded from active operational views. A later sync automatically
    reactivates a listing if Hostaway returns it again.
    """
    if not received_listing_ids:
        return 0

    deactivated = 0
    timestamp = synced_at or datetime.utcnow()
    for listing in existing_listings:
        if listing.listing_id in received_listing_ids:
            continue
        if (listing.status or '').strip().lower() == 'deleted':
            continue
        listing.status = 'deleted'
        listing.last_synced_at = timestamp
        deactivated += 1
    return deactivated


def sync_listings(full_sync: bool = True, progress_tracker: Optional[Any] = None, sync_run_id: Optional[int] = None) -> Dict:
    """
    Sync all listings from Hostaway API.
    
    Args:
        full_sync: Whether to perform a full sync (currently unused but kept for API consistency).
        progress_tracker: Optional progress tracker (WebProgressTracker or terminal tracker).
        sync_run_id: Optional sync_run_id to group sync logs.
        
    Returns:
        Dictionary with sync results including status, counts, errors, and listing_stats.
    """
    start_time = datetime.utcnow()
    # For PostgreSQL, get_database_path() returns None - that's OK, functions use DATABASE_URL
    # Initialize database
    init_models(None)  # None is OK for PostgreSQL, function uses DATABASE_URL from environment
    session = get_session(None)  # None is OK for PostgreSQL, function uses DATABASE_URL from environment
    
    try:
        client = HostawayAPIClient()
        
        if VERBOSE:
            logger.info("Fetching listings from Hostaway API...")
        
        # Get all listings (handle pagination)
        all_listings: List[Dict] = []
        offset = 0
        
        while True:
            listings = client.get_listings_page(limit=PAGINATION_LIMIT, offset=offset)
            if listings is None:
                raise RuntimeError(
                    f"Hostaway listings sync failed while fetching offset {offset}; "
                    "local listing statuses were left unchanged"
                )
            if not listings:
                break
            
            all_listings.extend(listings)
            
            # Check if we got fewer than limit (last page)
            if len(listings) < PAGINATION_LIMIT:
                break
            
            offset += PAGINATION_LIMIT
        
        if VERBOSE:
            logger.info(f"Found {len(all_listings)} listings")
        
        # Pre-load existing listings into memory for faster lookups
        existing_listings = session.query(Listing).all()
        existing_listing_map = {l.listing_id: l for l in existing_listings}
        
        # Use provided progress tracker or default
        if progress_tracker:
            progress = progress_tracker
        else:
            progress = get_progress_tracker()
        progress.start_phase("Syncing Listings", len(all_listings))
        
        records_created = 0
        records_updated = 0
        records_deactivated = 0
        errors: List[str] = []
        
        # Commit in batches to avoid database locking
        batch_count = 0
        
        for listing_data in all_listings:
            try:
                listing_id = listing_data.get('id')
                if not listing_id:
                    continue
                listing_data = enrich_listing_data(client, listing_data)
                
                listing_name = listing_data.get('name', f'Listing {listing_id}')
                progress.update_item(listing_name)
                
                # Check if listing exists using lookup map
                existing_listing = existing_listing_map.get(listing_id)
                
                # Prepare data
                listing_dict = {
                    'listing_id': listing_id,
                    'name': listing_data.get('name'),
                    'description': listing_data.get('description'),
                    'property_type_id': listing_data.get('propertyTypeId'),
                    'accommodates': listing_field_value(listing_data, 'personCapacity', 'accommodates'),
                    'bedrooms': listing_field_value(listing_data, 'bedroomsNumber', 'bedrooms'),
                    'bathrooms': listing_field_value(listing_data, 'bathroomsNumber', 'bathrooms'),
                    'beds': listing_field_value(listing_data, 'bedsNumber', 'beds'),
                    'square_meters': listing_data.get('squareMeters'),
                    'address': listing_data.get('address'),
                    'city': listing_data.get('city'),
                    'state': listing_data.get('state'),
                    'country': listing_data.get('country'),
                    'zipcode': listing_data.get('zipcode'),
                    'latitude': listing_data.get('latitude'),
                    'longitude': listing_data.get('longitude'),
                    'timezone_name': listing_data.get('timezoneName'),
                    'base_price': listing_field_value(listing_data, 'price', 'basePrice'),
                    'currency': listing_data.get('currency'),
                    'check_in_time_start': listing_data.get('checkInTimeStart'),
                    'check_in_time_end': listing_data.get('checkInTimeEnd'),
                    'check_out_time': listing_data.get('checkOutTime'),
                    'status': listing_data.get('status'),
                    'amenities': (
                        json.dumps(listing_data.get('amenities', [])) 
                        if listing_data.get('amenities') else None
                    ),
                    'account_id': listing_data.get('accountId'),
                    'custom_fields': (
                        json.dumps(listing_data.get('customFields', {})) 
                        if listing_data.get('customFields') else None
                    ),
                    'inserted_on': parse_timestamp(listing_data.get('insertedOn')),
                    'updated_on': parse_timestamp(listing_data.get('updatedOn')),
                    'last_synced_at': datetime.utcnow(),
                    'internal_listing_name': listing_data.get('internalListingName')
                }
                
                if existing_listing:
                    # Check if any fields actually changed (excluding last_synced_at)
                    has_changes = False
                    for key, value in listing_dict.items():
                        if key == 'last_synced_at':
                            # Always update this, but don't count as a change
                            setattr(existing_listing, key, value)
                            continue

                        if key in DETAIL_PROFILE_FIELDS and value is None and getattr(existing_listing, key, None) is not None:
                            continue
                        
                        current_value = getattr(existing_listing, key, None)
                        if current_value != value:
                            setattr(existing_listing, key, value)
                            has_changes = True
                    
                    if has_changes:
                        records_updated += 1
                        progress.increment(updated=True)
                    else:
                        # No changes, just increment progress
                        progress.increment()
                else:
                    # Create new listing
                    listing = Listing(**listing_dict)
                    session.add(listing)
                    # Add to lookup map for future reference in this batch
                    existing_listing_map[listing_id] = listing
                    records_created += 1
                    progress.increment(created=True)
                
                # Sync photos
                photos_data = listing_data.get('photos', [])
                if photos_data:
                    sync_listing_photos(session, listing_id, photos_data)

                sync_listing_tags(session, listing_id, listing_data)
                
                # Commit in batches to avoid database locking
                batch_count += 1
                if batch_count >= BATCH_SIZE:
                    try:
                        session.commit()
                        batch_count = 0
                    except Exception as e:
                        session.rollback()
                        error_msg = f"Error committing batch: {str(e)}"
                        errors.append(error_msg)
                        logger.warning(error_msg)
                
            except Exception as e:
                error_msg = f"Error syncing listing {listing_data.get('id')}: {str(e)}"
                errors.append(error_msg)
                progress.increment(error=True)
                session.rollback()  # Rollback on error
                logger.warning(error_msg)
                continue

        # Hostaway's listings endpoint is fully paginated above for both full and
        # incremental runs. Only reconcile removals after a non-empty, error-free
        # response so a transient API or processing failure can never retire all
        # local inventory.
        if all_listings and not errors:
            received_listing_ids = {
                int(listing_data['id'])
                for listing_data in all_listings
                if listing_data.get('id') is not None
            }
            records_deactivated = mark_missing_listings_deleted(
                existing_listings,
                received_listing_ids,
            )
            records_updated += records_deactivated
        
        # Complete progress tracking
        progress.complete_phase()
        
        # Commit remaining changes
        try:
            session.commit()
        except Exception as e:
            session.rollback()
            error_msg = f"Error in final commit: {str(e)}"
            errors.append(error_msg)
            logger.error(error_msg)
        
        # Log sync operation
        end_time = datetime.utcnow()
        duration = (end_time - start_time).total_seconds()
        
        # For listings, we don't have per-listing stats (listings are the listings themselves)
        # listing_stats will be populated by other sync types
        listing_stats = {}  # Empty for listings sync
        
        sync_log = SyncLog(
            sync_run_id=sync_run_id,
            sync_type='listings',
            sync_mode='full' if full_sync else 'incremental',
            status='success' if not errors else 'partial',
            records_processed=len(all_listings),
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
            logger.info(
                f"Sync complete: {len(all_listings)} processed, "
                f"{records_created} created, {records_updated} updated, "
                f"{records_deactivated} deactivated, "
                f"{len(errors)} errors, {duration:.2f}s"
            )
        
        return {
            'status': 'success' if not errors else 'partial',
            'records_processed': len(all_listings),
            'records_created': records_created,
            'records_updated': records_updated,
            'records_deactivated': records_deactivated,
            'errors': errors
        }
        
    except Exception as e:
        session.rollback()
        error_msg = f"Fatal error in sync_listings: {str(e)}"
        logger.error(error_msg, exc_info=True)
        
        # Log error
        try:
            sync_log = SyncLog(
                sync_run_id=sync_run_id,
                sync_type='listings',
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
