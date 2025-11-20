#!/usr/bin/env python3
"""
Sync reservations from Hostaway API to database.
Also extracts guest data from reservations.
"""

import sys
import os
import json
import logging
from datetime import datetime, date, timedelta
from typing import List, Dict, Optional, Tuple, Any

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sync.api_client import HostawayAPIClient
from sync.progress_tracker import get_progress_tracker
from database.models import Reservation, Guest, Listing, SyncLog, Base, get_session, init_models
from database.schema import get_database_path
from config import DATABASE_PATH, VERBOSE

# Configure logging
logger = logging.getLogger(__name__)


def parse_timestamp(ts_str: Optional[str]) -> Optional[datetime]:
    """Parse timestamp string to datetime object"""
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
        except:
            continue
    
    return None


def parse_date(date_str: str) -> Optional[date]:
    """Parse date string to date object"""
    if not date_str:
        return None
    
    try:
        # Handle YYYY-MM-DD format
        return datetime.strptime(date_str.split('T')[0], '%Y-%m-%d').date()
    except:
        return None


def get_or_create_guest(session, reservation_data: Dict, guest_lookup: Dict) -> Tuple[Optional[int], bool]:
    """Get or create guest from reservation data using lookup maps.
    Returns: (guest_id, was_created)
    
    Args:
        session: Database session.
        reservation_data: Reservation data dictionary.
        guest_lookup: Dictionary with 'by_external_id' and 'by_email' lookup maps.
    """
    guest_external_id = reservation_data.get('guestExternalAccountId')
    guest_email = reservation_data.get('guestEmail')
    
    # Try to find existing guest by external ID or email using lookup maps
    guest = None
    if guest_external_id and str(guest_external_id) in guest_lookup['by_external_id']:
        guest = guest_lookup['by_external_id'][str(guest_external_id)]
    
    if not guest and guest_email and guest_email.lower() in guest_lookup['by_email']:
        guest = guest_lookup['by_email'][guest_email.lower()]
    
    was_created = False
    
    if guest:
        # Update guest information
        guest.first_name = reservation_data.get('guestFirstName') or guest.first_name
        guest.last_name = reservation_data.get('guestLastName') or guest.last_name
        guest.full_name = reservation_data.get('guestName') or guest.full_name
        guest.email = guest_email or guest.email
        guest.phone = reservation_data.get('phone') or guest.phone
        guest.country = reservation_data.get('guestCountry') or guest.country
        guest.city = reservation_data.get('guestCity') or guest.city
        guest.address = reservation_data.get('guestAddress') or guest.address
        guest.zipcode = reservation_data.get('guestZipCode') or guest.zipcode
        guest.guest_picture = reservation_data.get('guestPicture') or guest.guest_picture
        guest.guest_recommendations = reservation_data.get('guestRecommendations') or guest.guest_recommendations
        guest.guest_trips = reservation_data.get('guestTrips') or guest.guest_trips
        guest.guest_work = reservation_data.get('guestWork') or guest.guest_work
        guest.is_guest_identity_verified = reservation_data.get('isGuestIdentityVerified', 0) or guest.is_guest_identity_verified
        guest.is_guest_verified_by_email = reservation_data.get('isGuestVerifiedByEmail', 0) or guest.is_guest_verified_by_email
        guest.is_guest_verified_by_phone = reservation_data.get('isGuestVerifiedByPhone', 0) or guest.is_guest_verified_by_phone
        guest.is_guest_verified_by_reviews = reservation_data.get('isGuestVerifiedByReviews', 0) or guest.is_guest_verified_by_reviews
        guest.updated_at = datetime.utcnow()
        guest.last_synced_at = datetime.utcnow()
    else:
        # Create new guest
        guest = Guest(
            guest_external_account_id=str(guest_external_id) if guest_external_id else None,
            first_name=reservation_data.get('guestFirstName'),
            last_name=reservation_data.get('guestLastName'),
            full_name=reservation_data.get('guestName'),
            email=guest_email,
            phone=reservation_data.get('phone'),
            country=reservation_data.get('guestCountry'),
            city=reservation_data.get('guestCity'),
            address=reservation_data.get('guestAddress'),
            zipcode=reservation_data.get('guestZipCode'),
            guest_picture=reservation_data.get('guestPicture'),
            guest_recommendations=reservation_data.get('guestRecommendations'),
            guest_trips=reservation_data.get('guestTrips'),
            guest_work=reservation_data.get('guestWork'),
            is_guest_identity_verified=reservation_data.get('isGuestIdentityVerified', 0),
            is_guest_verified_by_email=reservation_data.get('isGuestVerifiedByEmail', 0),
            is_guest_verified_by_phone=reservation_data.get('isGuestVerifiedByPhone', 0),
            is_guest_verified_by_reviews=reservation_data.get('isGuestVerifiedByReviews', 0),
            last_synced_at=datetime.utcnow()
        )
        session.add(guest)
        session.flush()  # Get the guest_id
        was_created = True
        
        # Update lookup maps
        if guest.guest_external_account_id:
            guest_lookup['by_external_id'][guest.guest_external_account_id] = guest
        if guest.email:
            guest_lookup['by_email'][guest.email.lower()] = guest
    
    return guest.guest_id, was_created


def sync_reservations(full_sync: bool = True, listing_id: int = None, progress_tracker: Optional[Any] = None, sync_run_id: Optional[int] = None) -> Dict:
    """
    Sync reservations from Hostaway API.
    
    Args:
        full_sync: Whether to perform a full sync.
        listing_id: Optional specific listing to sync.
        progress_tracker: Optional progress tracker (WebProgressTracker or terminal tracker).
        sync_run_id: Optional sync_run_id to group sync logs.
    
    Returns:
        Dictionary with sync results including listing_stats.
    """
    start_time = datetime.utcnow()
    db_path = get_database_path()
    
    # Initialize database
    init_models(db_path)
    session = get_session(db_path)
    
    try:
        client = HostawayAPIClient()
        
        # Handle listing_id parameter (for backward compatibility)
        # Note: We now fetch all reservations, but can filter by listing_id if needed
        if listing_id:
            listing = session.query(Listing).filter(Listing.listing_id == listing_id).first()
            if not listing:
                return {'status': 'error', 'error': f'Listing {listing_id} not found'}
        
        # Get last sync time for incremental sync
        cutoff_time = None
        if not full_sync:
            from sync.sync_manager import get_last_sync_time
            last_sync_time = get_last_sync_time('reservations')
            if last_sync_time:
                # Use 12 hours before last sync to catch any reservations we might have missed
                cutoff_time = last_sync_time - timedelta(hours=12)
                if VERBOSE:
                    print(f"Incremental reservation sync: Filtering reservations updated after {cutoff_time}")
            else:
                # No previous sync - treat as full sync
                if VERBOSE:
                    print("No previous reservation sync found. Performing full sync.")
                full_sync = True
        
        # Use provided progress tracker or default
        if progress_tracker:
            progress = progress_tracker
        else:
            progress = get_progress_tracker()
        
        # Fetch reservations from API with pagination and progress updates
        if VERBOSE:
            if full_sync:
                print("Fetching all reservations from API...")
            else:
                print(f"Fetching reservations updated after {cutoff_time} from API...")
                print("Note: API returns reservations sorted by updatedOn DESC (newest first)")
                print("We will stop fetching when we encounter reservations older than cutoff time")
        
        all_reservations = []
        offset = 0
        limit = 100
        
        # Start progress phase - we'll update total as we discover more reservations
        phase_name = "Fetching All Reservations" if full_sync else "Fetching Updated Reservations"
        progress.start_phase(phase_name, 0)
        
        try:
            while True:
                progress.update_item(f"Fetching reservations (offset: {offset})...")
                
                # Fetch reservations (API defaults to sorted by updatedOn DESC - newest first)
                reservations = client.get_reservations(limit=limit, offset=offset)
                
                if not reservations:
                    break
                
                # For incremental sync: check if we've reached reservations older than cutoff
                if not full_sync and cutoff_time:
                    # Check if this page contains any reservations older than cutoff
                    page_has_old_reservations = False
                    for reservation_data in reservations:
                        updated_on = parse_timestamp(reservation_data.get('updatedOn'))
                        latest_activity_on = parse_timestamp(reservation_data.get('latestActivityOn'))
                        reservation_timestamp = updated_on or latest_activity_on
                        
                        # If reservation is older than cutoff, we've reached the end
                        if reservation_timestamp and reservation_timestamp < cutoff_time:
                            page_has_old_reservations = True
                            break
                    
                    if page_has_old_reservations:
                        # This page has old reservations - only include ones newer than cutoff
                        # Since API is sorted DESC, all reservations before this point are newer
                        filtered_page = []
                        for reservation_data in reservations:
                            updated_on = parse_timestamp(reservation_data.get('updatedOn'))
                            latest_activity_on = parse_timestamp(reservation_data.get('latestActivityOn'))
                            reservation_timestamp = updated_on or latest_activity_on
                            
                            # Include if newer than cutoff, or if no timestamp (include to be safe)
                            if not reservation_timestamp or reservation_timestamp >= cutoff_time:
                                filtered_page.append(reservation_data)
                            else:
                                # We've hit the cutoff - stop here
                                break
                        
                        all_reservations.extend(filtered_page)
                        if VERBOSE:
                            print(f"Reached cutoff time. Stopping pagination. Processed {len(all_reservations)} reservations.")
                        break
                
                # Add this batch of reservations
                all_reservations.extend(reservations)
                reservations_after = len(all_reservations)
                
                # Update total: increment it as we discover more reservations
                # This gives a dynamic progress bar that updates as we discover more reservations
                progress.update_total(reservations_after)
                
                # Increment progress for this batch of reservations
                for _ in range(len(reservations)):
                    progress.increment(
                        allow_exceed_total=True,
                        item_name=f"Fetched {reservations_after} reservations..."
                    )
                
                if len(reservations) < limit:
                    # Last page - we're done
                    break
                
                offset += limit
                
        except Exception as e:
            error_msg = f"Error fetching reservations from API: {str(e)}"
            if VERBOSE:
                print(f"\n    {error_msg}")
            progress.complete_phase()
            # Create error SyncLog entry
            end_time = datetime.utcnow()
            duration = (end_time - start_time).total_seconds()
            sync_log = SyncLog(
                sync_run_id=sync_run_id,
                sync_type='reservations',
                sync_mode='full' if full_sync else 'incremental',
                status='error',
                records_processed=0,
                errors=json.dumps([error_msg]),
                listing_stats=json.dumps({}),
                started_at=start_time,
                completed_at=end_time,
                duration_seconds=duration
            )
            session.add(sync_log)
            session.commit()
            return {
                'status': 'error',
                'error': error_msg
            }
        
        progress.complete_phase()
        
        if VERBOSE:
            print(f"Fetched {len(all_reservations)} total reservations from API")
        
        # For incremental sync, filtering is already done during pagination (early stop)
        # For full sync, process all reservations
        reservations_to_process = all_reservations
        
        if VERBOSE:
            if full_sync:
                print(f"Full sync: Processing all {len(reservations_to_process)} reservations")
            else:
                print(f"Incremental sync: Fetched {len(reservations_to_process)} reservations (stopped at cutoff time)")
        
        # Handle empty results - still create SyncLog entry
        if not reservations_to_process:
            if VERBOSE:
                print("No reservations to process. Skipping reservation sync.")
            
            # Still create a SyncLog entry so it appears in the summary
            end_time = datetime.utcnow()
            duration = (end_time - start_time).total_seconds()
            
            sync_log = SyncLog(
                sync_run_id=sync_run_id,
                sync_type='reservations',
                sync_mode='full' if full_sync else 'incremental',
                status='success',
                records_processed=0,
                records_created=0,
                records_updated=0,
                errors=None,
                listing_stats=json.dumps({}),
                started_at=start_time,
                completed_at=end_time,
                duration_seconds=duration
            )
            session.add(sync_log)
            session.commit()
            
            return {
                'status': 'success',
                'records_processed': 0,
                'records_created': 0,
                'records_updated': 0,
                'guests_created': 0,
                'errors': [],
                'listing_stats': {}
            }
        
        records_created = 0
        records_updated = 0
        guests_created = 0
        errors = []
        
        # Pre-load existing reservations and guests into memory for faster lookups
        existing_reservations = session.query(Reservation).all()
        existing_reservation_map = {r.reservation_id: r for r in existing_reservations}
        
        existing_guests = session.query(Guest).all()
        guest_lookup = {
            'by_external_id': {g.guest_external_account_id: g for g in existing_guests if g.guest_external_account_id},
            'by_email': {g.email.lower(): g for g in existing_guests if g.email}
        }
        
        # Start processing phase
        progress.start_phase("Processing Reservations", len(reservations_to_process))
        
        # Track per-listing statistics
        listing_stats = {}  # {listing_id: {reservations: count, guests: count}}
        
        # Commit in batches to avoid database locking
        batch_count = 0
        
        # Process each reservation
        for reservation_data in reservations_to_process:
            try:
                reservation_id = reservation_data.get('id')
                if not reservation_id:
                    continue
                
                guest_name = reservation_data.get('guestName', f'Reservation {reservation_id}')
                progress.update_item(f"Reservation: {guest_name}")
                
                # Get or create guest using lookup maps
                guest_id, guest_was_created = get_or_create_guest(session, reservation_data, guest_lookup)
                if guest_was_created:
                    guests_created += 1
                
                # Check if reservation exists using lookup map
                existing_reservation = existing_reservation_map.get(reservation_id)
                
                # Prepare reservation data
                reservation_dict = {
                    'reservation_id': reservation_id,
                    'listing_id': reservation_data.get('listingMapId'),
                    'guest_id': guest_id,
                    'channel_id': reservation_data.get('channelId'),
                    'channel_name': reservation_data.get('channelName'),
                    'source': reservation_data.get('source'),
                    'channel_reservation_id': reservation_data.get('channelReservationId'),
                    'confirmation_code': reservation_data.get('confirmationCode'),
                    'guest_name': reservation_data.get('guestName'),
                    'guest_first_name': reservation_data.get('guestFirstName'),
                    'guest_last_name': reservation_data.get('guestLastName'),
                    'guest_email': reservation_data.get('guestEmail'),
                    'guest_phone': reservation_data.get('phone'),
                    'guest_country': reservation_data.get('guestCountry'),
                    'guest_city': reservation_data.get('guestCity'),
                    'guest_address': reservation_data.get('guestAddress'),
                    'guest_zipcode': reservation_data.get('guestZipCode'),
                    'arrival_date': parse_date(reservation_data.get('arrivalDate')),
                    'departure_date': parse_date(reservation_data.get('departureDate')),
                    'nights': reservation_data.get('nights'),
                    'is_dates_unspecified': 1 if reservation_data.get('isDatesUnspecified') else 0,
                    'number_of_guests': reservation_data.get('numberOfGuests'),
                    'adults': reservation_data.get('adults'),
                    'children': reservation_data.get('children'),
                    'infants': reservation_data.get('infants'),
                    'pets': reservation_data.get('pets'),
                    'total_price': reservation_data.get('totalPrice'),
                    'currency': reservation_data.get('currency'),
                    'tax_amount': reservation_data.get('taxAmount'),
                    'cleaning_fee': reservation_data.get('cleaningFee'),
                    'security_deposit_fee': reservation_data.get('securityDepositFee'),
                    'remaining_balance': reservation_data.get('remainingBalance'),
                    'status': reservation_data.get('status'),
                    'payment_status': reservation_data.get('paymentStatus'),
                    'is_paid': 1 if reservation_data.get('isPaid') else 0,
                    'is_starred': 1 if reservation_data.get('isStarred') else 0,
                    'is_archived': 1 if reservation_data.get('isArchived') else 0,
                    'is_pinned': 1 if reservation_data.get('isPinned') else 0,
                    'reservation_date': parse_timestamp(reservation_data.get('reservationDate')),
                    'cancellation_date': parse_timestamp(reservation_data.get('cancellationDate')),
                    'cancelled_by': reservation_data.get('cancelledBy'),
                    'host_note': reservation_data.get('hostNote'),
                    'guest_note': reservation_data.get('guestNote'),
                    'comment': reservation_data.get('comment'),
                    'custom_field_values': json.dumps(reservation_data.get('customFieldValues', [])) if reservation_data.get('customFieldValues') else None,
                    'inserted_on': parse_timestamp(reservation_data.get('insertedOn')),
                    'updated_on': parse_timestamp(reservation_data.get('updatedOn')),
                    'latest_activity_on': parse_timestamp(reservation_data.get('latestActivityOn')),
                    'last_synced_at': datetime.utcnow()
                }
                
                listing_id = reservation_dict.get('listing_id')
                
                if existing_reservation:
                    # Check if any fields actually changed (excluding last_synced_at)
                    has_changes = False
                    for key, value in reservation_dict.items():
                        if key == 'last_synced_at':
                            # Always update this, but don't count as a change
                            setattr(existing_reservation, key, value)
                            continue
                        
                        current_value = getattr(existing_reservation, key, None)
                        if current_value != value:
                            setattr(existing_reservation, key, value)
                            has_changes = True
                    
                    if has_changes:
                        records_updated += 1
                        progress.increment(updated=True)
                    else:
                        # No changes, just increment progress
                        progress.increment()
                else:
                    # Create new reservation
                    reservation = Reservation(**reservation_dict)
                    session.add(reservation)
                    # Add to lookup map for future reference in this batch
                    existing_reservation_map[reservation_id] = reservation
                    records_created += 1
                    progress.increment(created=True)
                
                # Track per-listing statistics
                reservation_listing_id = reservation_dict.get('listing_id')
                if reservation_listing_id:
                    if reservation_listing_id not in listing_stats:
                        listing_stats[reservation_listing_id] = {'reservations': 0, 'guests': set()}
                    listing_stats[reservation_listing_id]['reservations'] += 1
                    if guest_id:
                        listing_stats[reservation_listing_id]['guests'].add(guest_id)
                
                # Commit in batches to avoid database locking
                batch_count += 1
                if batch_count >= 50:  # Batch size
                    try:
                        session.commit()
                        batch_count = 0
                    except Exception as e:
                        session.rollback()
                        error_msg = f"Error committing batch: {str(e)}"
                        errors.append(error_msg)
                        if VERBOSE:
                            print(f"\n  {error_msg}")
                
            except Exception as e:
                error_msg = f"Error syncing reservation {reservation_data.get('id')}: {str(e)}"
                errors.append(error_msg)
                progress.increment(error=True)
                if VERBOSE:
                    print(f"\n  {error_msg}")
                continue
        
        # Complete progress tracking
        progress.complete_phase()
        
        # Commit all changes
        session.commit()
        
        # Convert sets to counts for JSON serialization
        listing_stats_serializable = {}
        for listing_id, stats in listing_stats.items():
            listing_stats_serializable[listing_id] = {
                'reservations': stats['reservations'],
                'guests': len(stats['guests'])
            }
        
        # Log sync operation
        end_time = datetime.utcnow()
        duration = (end_time - start_time).total_seconds()
        
        sync_log = SyncLog(
            sync_run_id=sync_run_id,
            sync_type='reservations',
            sync_mode='full' if full_sync else 'incremental',
            status='success' if not errors else 'partial',
            records_processed=len(reservations_to_process),
            records_created=records_created,
            records_updated=records_updated,
            errors=json.dumps(errors) if errors else None,
            listing_stats=json.dumps(listing_stats_serializable),
            started_at=start_time,
            completed_at=end_time,
            duration_seconds=duration
        )
        session.add(sync_log)
        session.commit()
        
        if VERBOSE:
            print(f"\nSync complete:")
            print(f"  Processed: {len(reservations_to_process)} reservations")
            print(f"  Created: {records_created}")
            print(f"  Updated: {records_updated}")
            print(f"  Guests created: {guests_created}")
            print(f"  Errors: {len(errors)}")
            print(f"  Duration: {duration:.2f} seconds")
        
        return {
            'status': 'success' if not errors else 'partial',
            'records_processed': len(reservations_to_process),
            'records_created': records_created,
            'records_updated': records_updated,
            'guests_created': guests_created,
            'errors': errors,
            'listing_stats': listing_stats_serializable
        }
        
    except Exception as e:
        session.rollback()
        error_msg = f"Fatal error in sync_reservations: {str(e)}"
        logger.error(error_msg)
        
        # Log error
        sync_log = SyncLog(
            sync_run_id=sync_run_id,
            sync_type='reservations',
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
        
        return {
            'status': 'error',
            'error': error_msg
        }
    
    finally:
        session.close()


if __name__ == "__main__":
    sync_reservations(full_sync=True)
