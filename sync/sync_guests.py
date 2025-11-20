#!/usr/bin/env python3
"""
Deduplicate and consolidate guest information from reservations.
This ensures guest data is consistent across all reservations.
"""

import sys
import os
import logging
from datetime import datetime
from typing import Dict, Optional, Any

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sync.progress_tracker import get_progress_tracker
from database.models import Guest, Reservation, SyncLog, get_session, init_models
from database.schema import get_database_path
from config import VERBOSE

# Configure logging
logger = logging.getLogger(__name__)


def deduplicate_guests(progress_tracker: Optional[Any] = None, sync_run_id: Optional[int] = None) -> Dict:
    """
    Deduplicate guests by email and external account ID.
    
    Optimized with early exit check and batch commits for better performance.
    
    Args:
        progress_tracker: Optional progress tracker (WebProgressTracker or terminal tracker).
        sync_run_id: Optional sync_run_id to group sync logs.
    
    Returns:
        Dictionary with sync results.
    """
    start_time = datetime.utcnow()
    db_path = get_database_path()
    
    init_models(db_path)
    session = get_session(db_path)
    
    try:
        # Use provided progress tracker or default
        if progress_tracker:
            progress = progress_tracker
        else:
            progress = get_progress_tracker()
        
        # Early exit check: Use SQL aggregation to quickly detect if duplicates exist
        # This avoids loading all records into memory if there are no duplicates
        from sqlalchemy import func
        
        # Check for duplicate emails using SQL aggregation
        email_duplicate_check = session.query(
            func.lower(Guest.email).label('email_lower'),
            func.count(Guest.guest_id).label('count')
        ).filter(
            Guest.email.isnot(None),
            Guest.email != ''
        ).group_by(func.lower(Guest.email)).having(func.count(Guest.guest_id) > 1).first()
        
        # Check for duplicate external_ids using SQL aggregation
        external_id_duplicate_check = session.query(
            Guest.guest_external_account_id,
            func.count(Guest.guest_id).label('count')
        ).filter(
            Guest.guest_external_account_id.isnot(None),
            Guest.guest_external_account_id != ''
        ).group_by(Guest.guest_external_account_id).having(func.count(Guest.guest_id) > 1).first()
        
        # If no duplicates found in either category, skip processing
        if not email_duplicate_check and not external_id_duplicate_check:
            progress.start_phase("Deduplicating Guests", 1)
            progress.update_item("Checking for duplicates...")
            progress.increment()
            progress.complete_phase()
            
            # Create SyncLog entry
            end_time = datetime.utcnow()
            duration = (end_time - start_time).total_seconds()
            
            sync_log = SyncLog(
                sync_run_id=sync_run_id,
                sync_type='guests',
                sync_mode='full',  # Will be set by caller if needed
                status='success',
                records_processed=0,
                records_created=0,
                records_updated=0,
                errors=None,
                listing_stats=None,
                started_at=start_time,
                completed_at=end_time,
                duration_seconds=duration
            )
            session.add(sync_log)
            session.commit()
            
            return {
                'status': 'success',
                'duplicates_merged': 0
            }
        
        # Count total guests to process for progress tracking
        total_email_guests = session.query(Guest).filter(
            Guest.email.isnot(None),
            Guest.email != ''
        ).count()
        
        total_external_id_guests = session.query(Guest).filter(
            Guest.guest_external_account_id.isnot(None),
            Guest.guest_external_account_id != ''
        ).count()
        
        # Estimate total work (email phase + external_id phase)
        # Use max to avoid double-counting guests that have both
        estimated_total = max(total_email_guests, total_external_id_guests) + min(total_email_guests, total_external_id_guests) // 2
        
        progress.start_phase("Deduplicating Guests", estimated_total)
        progress.update_item("Processing email duplicates...")
        
        # Get all guests with emails
        guests_with_email = session.query(Guest).filter(
            Guest.email.isnot(None),
            Guest.email != ''
        ).all()
        
        # Group by email
        email_groups = {}
        for guest in guests_with_email:
            email_lower = guest.email.lower() if guest.email else None
            if email_lower:
                if email_lower not in email_groups:
                    email_groups[email_lower] = []
                email_groups[email_lower].append(guest)
            progress.increment(allow_exceed_total=True)
        
        # Merge duplicates by email
        merged_count = 0
        batch_size = 50
        batch_count = 0
        
        for email, guest_list in email_groups.items():
            if len(guest_list) > 1:
                # Keep the first guest, merge others into it
                primary_guest = guest_list[0]
                progress.update_item(f"Merging email: {email}")
                
                for duplicate_guest in guest_list[1:]:
                    # Update reservations to point to primary guest
                    session.query(Reservation).filter(
                        Reservation.guest_id == duplicate_guest.guest_id
                    ).update({'guest_id': primary_guest.guest_id}, synchronize_session=False)
                    
                    # Merge data if primary is missing it
                    if not primary_guest.guest_external_account_id and duplicate_guest.guest_external_account_id:
                        primary_guest.guest_external_account_id = duplicate_guest.guest_external_account_id
                    if not primary_guest.first_name and duplicate_guest.first_name:
                        primary_guest.first_name = duplicate_guest.first_name
                    if not primary_guest.last_name and duplicate_guest.last_name:
                        primary_guest.last_name = duplicate_guest.last_name
                    if not primary_guest.full_name and duplicate_guest.full_name:
                        primary_guest.full_name = duplicate_guest.full_name
                    if not primary_guest.phone and duplicate_guest.phone:
                        primary_guest.phone = duplicate_guest.phone
                    if not primary_guest.country and duplicate_guest.country:
                        primary_guest.country = duplicate_guest.country
                    if not primary_guest.city and duplicate_guest.city:
                        primary_guest.city = duplicate_guest.city
                    
                    # Delete duplicate
                    session.delete(duplicate_guest)
                    merged_count += 1
                    progress.increment(allow_exceed_total=True)
                    
                    # Batch commit every N operations to reduce lock time
                    batch_count += 1
                    if batch_count >= batch_size:
                        session.flush()  # Write changes but don't commit yet
                        batch_count = 0
        
        # Process external account ID duplicates
        progress.update_item("Processing external ID duplicates...")
        
        # Get all guests with external IDs
        guests_with_external_id = session.query(Guest).filter(
            Guest.guest_external_account_id.isnot(None),
            Guest.guest_external_account_id != ''
        ).all()
        
        external_id_groups = {}
        for guest in guests_with_external_id:
            ext_id = guest.guest_external_account_id
            if ext_id:
                if ext_id not in external_id_groups:
                    external_id_groups[ext_id] = []
                external_id_groups[ext_id].append(guest)
            progress.increment(allow_exceed_total=True)
        
        for ext_id, guest_list in external_id_groups.items():
            if len(guest_list) > 1:
                primary_guest = guest_list[0]
                progress.update_item(f"Merging external ID: {ext_id}")
                
                for duplicate_guest in guest_list[1:]:
                    # Update reservations
                    session.query(Reservation).filter(
                        Reservation.guest_id == duplicate_guest.guest_id
                    ).update({'guest_id': primary_guest.guest_id}, synchronize_session=False)
                    
                    # Merge data
                    if not primary_guest.email and duplicate_guest.email:
                        primary_guest.email = duplicate_guest.email
                    if not primary_guest.first_name and duplicate_guest.first_name:
                        primary_guest.first_name = duplicate_guest.first_name
                    if not primary_guest.last_name and duplicate_guest.last_name:
                        primary_guest.last_name = duplicate_guest.last_name
                    if not primary_guest.full_name and duplicate_guest.full_name:
                        primary_guest.full_name = duplicate_guest.full_name
                    
                    session.delete(duplicate_guest)
                    merged_count += 1
                    progress.increment(allow_exceed_total=True)
                    
                    # Batch commit
                    batch_count += 1
                    if batch_count >= batch_size:
                        session.flush()
                        batch_count = 0
        
        progress.complete_phase()
        
        # Create SyncLog entry
        end_time = datetime.utcnow()
        duration = (end_time - start_time).total_seconds()
        
        sync_log = SyncLog(
            sync_run_id=sync_run_id,
            sync_type='guests',
            sync_mode='full',  # Will be set by caller if needed
            status='success',
            records_processed=0,  # We don't track individual guest processing
            records_created=0,
            records_updated=0,
            errors=None,
            listing_stats=None,
            started_at=start_time,
            completed_at=end_time,
            duration_seconds=duration
        )
        session.add(sync_log)
        session.commit()
        
        return {
            'status': 'success',
            'duplicates_merged': merged_count
        }
        
    except Exception as e:
        session.rollback()
        error_msg = f"Error in deduplicate_guests: {str(e)}"
        logger.error(error_msg)
        return {
            'status': 'error',
            'error': error_msg
        }
    
    finally:
        session.close()


if __name__ == "__main__":
    deduplicate_guests()
