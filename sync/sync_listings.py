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
from database.models import Listing, ListingPhoto, SyncLog, get_session, init_models
from database.schema import get_database_path
from config import STORE_PHOTO_METADATA, VERBOSE

# Configure logging
logger = logging.getLogger(__name__)

# Constants
BATCH_SIZE = 10
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
    session = get_session(db_path)
    
    try:
        client = HostawayAPIClient()
        
        if VERBOSE:
            logger.info("Fetching listings from Hostaway API...")
        
        # Get all listings (handle pagination)
        all_listings: List[Dict] = []
        offset = 0
        
        while True:
            listings = client.get_listings(limit=PAGINATION_LIMIT, offset=offset)
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
        errors: List[str] = []
        
        # Commit in batches to avoid database locking
        batch_count = 0
        
        for listing_data in all_listings:
            try:
                listing_id = listing_data.get('id')
                if not listing_id:
                    continue
                
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
                    'accommodates': listing_data.get('accommodates'),
                    'bedrooms': listing_data.get('bedrooms'),
                    'bathrooms': listing_data.get('bathrooms'),
                    'beds': listing_data.get('beds'),
                    'square_meters': listing_data.get('squareMeters'),
                    'address': listing_data.get('address'),
                    'city': listing_data.get('city'),
                    'state': listing_data.get('state'),
                    'country': listing_data.get('country'),
                    'zipcode': listing_data.get('zipcode'),
                    'latitude': listing_data.get('latitude'),
                    'longitude': listing_data.get('longitude'),
                    'timezone_name': listing_data.get('timezoneName'),
                    'base_price': listing_data.get('basePrice'),
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
                    'last_synced_at': datetime.utcnow()
                }
                
                if existing_listing:
                    # Check if any fields actually changed (excluding last_synced_at)
                    has_changes = False
                    for key, value in listing_dict.items():
                        if key == 'last_synced_at':
                            # Always update this, but don't count as a change
                            setattr(existing_listing, key, value)
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
                f"{len(errors)} errors, {duration:.2f}s"
            )
        
        return {
            'status': 'success' if not errors else 'partial',
            'records_processed': len(all_listings),
            'records_created': records_created,
            'records_updated': records_updated,
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