#!/usr/bin/env python3
"""
Sync manager to orchestrate all data synchronization operations.
Supports full sync and incremental sync with proper error handling.
"""

import sys
import os
import logging
from datetime import datetime, timedelta
from typing import Dict, Optional, Any

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sync.sync_listings import sync_listings
from sync.sync_reservations import sync_reservations
from sync.sync_guests import deduplicate_guests
from sync.sync_messages import sync_messages_from_api
from sync.sync_reviews import sync_reviews
from sync.progress_tracker import get_progress_tracker
from database.models import SyncLog, get_session, init_models
from database.schema import get_database_path
from config import (
    SYNC_FULL_ON_START, 
    SYNC_INCREMENTAL_DAILY, 
    SYNC_INTERVAL_HOURS, 
    VERBOSE
)

# Configure logging
logger = logging.getLogger(__name__)


def get_last_sync_time(sync_type: str) -> Optional[datetime]:
    """
    Get the last successful sync time for a given sync type.
    
    Args:
        sync_type: Type of sync (e.g., 'listings', 'reservations').
        
    Returns:
        Last sync datetime or None if never synced.
    """
    db_path = get_database_path()
    session = get_session(db_path)
    
    try:
        last_sync = session.query(SyncLog).filter(
            SyncLog.sync_type == sync_type,
            SyncLog.status == 'success'
        ).order_by(SyncLog.completed_at.desc()).first()
        
        if last_sync and last_sync.completed_at:
            return last_sync.completed_at
        return None
    except Exception as e:
        logger.warning(f"Error getting last sync time for {sync_type}: {e}")
        return None
    finally:
        session.close()


def should_sync(sync_type: str, force_full: bool = False) -> bool:
    """
    Determine if a sync should run based on last sync time.
    
    Args:
        sync_type: Type of sync to check.
        force_full: If True, always return True.
        
    Returns:
        True if sync should run, False otherwise.
    """
    if force_full:
        return True
    
    if not SYNC_INCREMENTAL_DAILY:
        return False
    
    last_sync = get_last_sync_time(sync_type)
    if not last_sync:
        return True  # Never synced, do it now
    
    # Require at least 12 hours between syncs
    time_since_sync = datetime.utcnow() - last_sync
    return time_since_sync >= timedelta(hours=12)


def full_sync(progress_tracker: Optional[Any] = None, sync_run_id: Optional[int] = None) -> Dict:
    """
    Perform a full sync of all data.
    
    This operation is idempotent - safe to run multiple times.
    
    Args:
        progress_tracker: Optional progress tracker (WebProgressTracker or terminal tracker)
        sync_run_id: Optional sync_run_id to group sync logs. If None, will be generated.
    
    Returns:
        Dictionary with sync results for each data type.
    """
    # Generate sync_run_id if not provided
    if sync_run_id is None:
        db_path = get_database_path()
        session = get_session(db_path)
        try:
            # Get the highest sync_run_id and increment
            max_run = session.query(SyncLog.sync_run_id).filter(
                SyncLog.sync_run_id.isnot(None)
            ).order_by(SyncLog.sync_run_id.desc()).first()
            sync_run_id = (max_run[0] + 1) if max_run and max_run[0] else 1
        except Exception:
            sync_run_id = 1
        finally:
            session.close()
    
    # Use provided progress tracker or default terminal tracker
    progress = progress_tracker if progress_tracker else get_progress_tracker()
    use_terminal = progress_tracker is None
    
    if use_terminal:
        print("\n" + "=" * 80)
        print("HOSTAWAY DATA SYNC - FULL SYNC")
        print("=" * 80)
        print("Note: This operation is idempotent - safe to run multiple times")
        print("=" * 80 + "\n")
    
    logger.info(f"Starting full sync (sync_run_id={sync_run_id})")
    results = {}
    
    sync_errors = []  # Track errors from individual sync steps
    
    try:
        # 1. Sync listings
        if use_terminal:
            print("[1/5] Syncing listings...")
        try:
            results['listings'] = sync_listings(full_sync=True, progress_tracker=progress, sync_run_id=sync_run_id)
            # Check for errors in result
            if isinstance(results['listings'], dict) and results['listings'].get('status') == 'error':
                error_msg = results['listings'].get('error', 'Unknown error')
                logger.error(f"Listings sync failed: {error_msg}")
                sync_errors.append(f"Listings: {error_msg}")
        except Exception as e:
            logger.error(f"Error syncing listings: {e}", exc_info=True)
            results['listings'] = {'status': 'error', 'error': str(e)}
            sync_errors.append(f"Listings: {str(e)}")
        
        if use_terminal:
            print()  # Blank line between phases
        
        # 2. Sync reservations (also extracts guests)
        if use_terminal:
            print("[2/5] Syncing reservations...")
        try:
            results['reservations'] = sync_reservations(full_sync=True, progress_tracker=progress, sync_run_id=sync_run_id)
            # Check for errors in result
            if isinstance(results['reservations'], dict) and results['reservations'].get('status') == 'error':
                error_msg = results['reservations'].get('error', 'Unknown error')
                logger.error(f"Reservations sync failed: {error_msg}")
                sync_errors.append(f"Reservations: {error_msg}")
        except Exception as e:
            logger.error(f"Error syncing reservations: {e}", exc_info=True)
            results['reservations'] = {'status': 'error', 'error': str(e)}
            sync_errors.append(f"Reservations: {str(e)}")
        
        if use_terminal:
            print()  # Blank line between phases
        
        # 3. Deduplicate guests
        if use_terminal:
            print("[3/5] Deduplicating guests...")
        try:
            results['guests'] = deduplicate_guests(progress_tracker=progress, sync_run_id=sync_run_id)
            # Check for errors in result
            if isinstance(results['guests'], dict) and results['guests'].get('status') == 'error':
                error_msg = results['guests'].get('error', 'Unknown error')
                logger.error(f"Guest deduplication failed: {error_msg}")
                sync_errors.append(f"Guests: {error_msg}")
        except Exception as e:
            logger.error(f"Error deduplicating guests: {e}", exc_info=True)
            results['guests'] = {'status': 'error', 'error': str(e)}
            sync_errors.append(f"Guests: {str(e)}")
        
        if use_terminal:
            print()  # Blank line between phases
        
        # 4. Sync messages from API
        if use_terminal:
            print("[4/5] Syncing messages from API...")
        try:
            results['messages'] = sync_messages_from_api(full_sync=True, progress_tracker=progress, sync_run_id=sync_run_id)
            # Check for errors in result
            if isinstance(results['messages'], dict) and results['messages'].get('status') == 'error':
                error_msg = results['messages'].get('error', 'Unknown error')
                logger.error(f"Messages sync failed: {error_msg}")
                sync_errors.append(f"Messages: {error_msg}")
        except Exception as e:
            logger.error(f"Error syncing messages: {e}", exc_info=True)
            results['messages'] = {'status': 'error', 'error': str(e)}
            sync_errors.append(f"Messages: {str(e)}")
        
        if use_terminal:
            print()  # Blank line between phases
        
        # 5. Sync reviews
        if use_terminal:
            print("[5/5] Syncing reviews...")
        try:
            results['reviews'] = sync_reviews(full_sync=True, progress_tracker=progress, sync_run_id=sync_run_id)
            # Check for errors in result
            if isinstance(results['reviews'], dict) and results['reviews'].get('status') == 'error':
                error_msg = results['reviews'].get('error', 'Unknown error')
                logger.error(f"Reviews sync failed: {error_msg}")
                sync_errors.append(f"Reviews: {error_msg}")
        except Exception as e:
            logger.error(f"Error syncing reviews: {e}", exc_info=True)
            results['reviews'] = {'status': 'error', 'error': str(e)}
            sync_errors.append(f"Reviews: {str(e)}")
        
        if use_terminal:
            print()  # Blank line between phases
        
        # Print final summary using progress tracker
        if use_terminal:
            progress.print_summary(results)
        
        # If there were any errors, add them to results
        if sync_errors:
            error_summary = '; '.join(sync_errors)
            results['error'] = error_summary
            logger.warning(f"Full sync completed with errors (sync_run_id={sync_run_id}): {error_summary}")
        else:
            logger.info(f"Full sync completed successfully (sync_run_id={sync_run_id})")
        
        results['sync_run_id'] = sync_run_id
        return results
        
    except Exception as e:
        logger.error(f"Fatal error during full sync: {e}", exc_info=True)
        results['error'] = str(e)
        results['sync_run_id'] = sync_run_id
        # Don't re-raise - return results with error so caller can see what succeeded
        return results


def incremental_sync(progress_tracker: Optional[Any] = None, sync_run_id: Optional[int] = None, force: bool = False) -> Dict:
    """
    Perform an incremental sync (only changed data).
    
    Args:
        progress_tracker: Optional progress tracker (WebProgressTracker or terminal tracker)
        sync_run_id: Optional sync_run_id to group sync logs. If None, will be generated.
        force: If True, force sync to run even if synced recently (bypasses time check).
               When manually triggered from UI, this should be True.
    
    Returns:
        Dictionary with sync results for each data type.
    """
    # Generate sync_run_id if not provided
    if sync_run_id is None:
        db_path = get_database_path()
        session = get_session(db_path)
        try:
            # Get the highest sync_run_id and increment
            max_run = session.query(SyncLog.sync_run_id).filter(
                SyncLog.sync_run_id.isnot(None)
            ).order_by(SyncLog.sync_run_id.desc()).first()
            sync_run_id = (max_run[0] + 1) if max_run and max_run[0] else 1
        except Exception:
            sync_run_id = 1
        finally:
            session.close()
    
    # Use provided progress tracker or default terminal tracker
    progress = progress_tracker if progress_tracker else get_progress_tracker()
    use_terminal = progress_tracker is None
    
    if use_terminal:
        print("\n" + "=" * 80)
        print("HOSTAWAY DATA SYNC - INCREMENTAL SYNC")
        print("=" * 80 + "\n")
    
    logger.info(f"Starting incremental sync (sync_run_id={sync_run_id})")
    results = {}
    
    sync_errors = []  # Track errors from individual sync steps
    
    try:
        # Check what needs syncing
        # If force=True, always sync (bypasses time check for manual triggers)
        sync_listings_flag = force or should_sync('listings')
        sync_reservations_flag = force or should_sync('reservations')
        sync_messages_flag = force or should_sync('messages')
        sync_reviews_flag = force or should_sync('reviews')
        
        # 1. Sync listings
        if sync_listings_flag:
            if use_terminal:
                print("[1] Syncing listings...")
            try:
                results['listings'] = sync_listings(full_sync=False, progress_tracker=progress, sync_run_id=sync_run_id)
                # Check for errors in result
                if isinstance(results['listings'], dict) and results['listings'].get('status') == 'error':
                    error_msg = results['listings'].get('error', 'Unknown error')
                    logger.error(f"Listings sync failed: {error_msg}")
                    sync_errors.append(f"Listings: {error_msg}")
            except Exception as e:
                logger.error(f"Error syncing listings: {e}", exc_info=True)
                results['listings'] = {'status': 'error', 'error': str(e)}
                sync_errors.append(f"Listings: {str(e)}")
            if use_terminal:
                print()
        else:
            if use_terminal:
                print("[1] Skipping listings (synced recently)")
            results['listings'] = {'status': 'skipped'}
        
        # 2. Sync reservations
        if sync_reservations_flag:
            if use_terminal:
                print("[2] Syncing reservations...")
            try:
                results['reservations'] = sync_reservations(full_sync=False, progress_tracker=progress, sync_run_id=sync_run_id)
                # Check for errors in result
                if isinstance(results['reservations'], dict) and results['reservations'].get('status') == 'error':
                    error_msg = results['reservations'].get('error', 'Unknown error')
                    logger.error(f"Reservations sync failed: {error_msg}")
                    sync_errors.append(f"Reservations: {error_msg}")
            except Exception as e:
                logger.error(f"Error syncing reservations: {e}", exc_info=True)
                results['reservations'] = {'status': 'error', 'error': str(e)}
                sync_errors.append(f"Reservations: {str(e)}")
            if use_terminal:
                print()
        else:
            if use_terminal:
                print("[2] Skipping reservations (synced recently)")
            results['reservations'] = {'status': 'skipped'}
        
        # 3. Deduplicate guests (always run after reservations if reservations were synced)
        if sync_reservations_flag:
            if use_terminal:
                print("[3] Deduplicating guests...")
            try:
                results['guests'] = deduplicate_guests(progress_tracker=progress, sync_run_id=sync_run_id)
                # Check for errors in result
                if isinstance(results['guests'], dict) and results['guests'].get('status') == 'error':
                    error_msg = results['guests'].get('error', 'Unknown error')
                    logger.error(f"Guest deduplication failed: {error_msg}")
                    sync_errors.append(f"Guests: {error_msg}")
            except Exception as e:
                logger.error(f"Error deduplicating guests: {e}", exc_info=True)
                results['guests'] = {'status': 'error', 'error': str(e)}
                sync_errors.append(f"Guests: {str(e)}")
            if use_terminal:
                print()
        else:
            results['guests'] = {'status': 'skipped'}
        
        # 4. Sync messages
        if sync_messages_flag:
            if use_terminal:
                print("[4] Syncing messages from API...")
            try:
                results['messages'] = sync_messages_from_api(full_sync=False, progress_tracker=progress, sync_run_id=sync_run_id)
                # Check for errors in result
                if isinstance(results['messages'], dict) and results['messages'].get('status') == 'error':
                    error_msg = results['messages'].get('error', 'Unknown error')
                    logger.error(f"Messages sync failed: {error_msg}")
                    sync_errors.append(f"Messages: {error_msg}")
            except Exception as e:
                logger.error(f"Error syncing messages: {e}", exc_info=True)
                results['messages'] = {'status': 'error', 'error': str(e)}
                sync_errors.append(f"Messages: {str(e)}")
            if use_terminal:
                print()
        else:
            if use_terminal:
                print("[4] Skipping messages (synced recently)")
            results['messages'] = {'status': 'skipped'}
        
        # 5. Sync reviews
        if sync_reviews_flag:
            if use_terminal:
                print("[5] Syncing reviews...")
            try:
                results['reviews'] = sync_reviews(full_sync=False, progress_tracker=progress, sync_run_id=sync_run_id)
                # Check for errors in result
                if isinstance(results['reviews'], dict) and results['reviews'].get('status') == 'error':
                    error_msg = results['reviews'].get('error', 'Unknown error')
                    logger.error(f"Reviews sync failed: {error_msg}")
                    sync_errors.append(f"Reviews: {error_msg}")
            except Exception as e:
                logger.error(f"Error syncing reviews: {e}", exc_info=True)
                results['reviews'] = {'status': 'error', 'error': str(e)}
                sync_errors.append(f"Reviews: {str(e)}")
            if use_terminal:
                print()
        else:
            if use_terminal:
                print("[5] Skipping reviews (synced recently)")
            results['reviews'] = {'status': 'skipped'}
        
        # Print final summary using progress tracker
        if use_terminal:
            progress.print_summary(results)
        
        # If there were any errors, add them to results
        if sync_errors:
            error_summary = '; '.join(sync_errors)
            results['error'] = error_summary
            logger.warning(f"Incremental sync completed with errors (sync_run_id={sync_run_id}): {error_summary}")
        else:
            logger.info(f"Incremental sync completed successfully (sync_run_id={sync_run_id})")
        
        results['sync_run_id'] = sync_run_id
        return results
        
    except Exception as e:
        logger.error(f"Fatal error during incremental sync: {e}", exc_info=True)
        results['error'] = str(e)
        results['sync_run_id'] = sync_run_id
        # Make sure all expected sync types are in results, even if they failed
        expected_types = ['listings', 'reservations', 'guests', 'messages', 'reviews']
        for sync_type in expected_types:
            if sync_type not in results:
                results[sync_type] = {'status': 'error', 'error': f'Fatal error prevented {sync_type} sync: {str(e)}'}
        return results


def sync(force_full: bool = False) -> Dict:
    """
    Main sync function - performs full or incremental sync.
    
    Args:
        force_full: If True, force a full sync regardless of last sync time.
        
    Returns:
        Dictionary with sync results.
    """
    # Initialize database
    db_path = get_database_path()
    try:
        init_models(db_path)
    except Exception as e:
        logger.error(f"Failed to initialize database: {e}", exc_info=True)
        return {'status': 'error', 'error': f"Database initialization failed: {e}"}
    
    if force_full or (SYNC_FULL_ON_START and not get_last_sync_time('listings')):
        return full_sync()
    else:
        return incremental_sync()


if __name__ == "__main__":
    import argparse
    
    # Setup logging
    from utils.logging_config import setup_logging
    setup_logging()
    
    parser = argparse.ArgumentParser(
        description='Sync Hostaway data to local database',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 -m sync.sync_manager --full          # Force full sync
  python3 -m sync.sync_manager --incremental   # Force incremental sync
  python3 -m sync.sync_manager                 # Auto-detect (default)
        """
    )
    parser.add_argument(
        '--full', 
        action='store_true', 
        help='Force full sync of all data'
    )
    parser.add_argument(
        '--incremental', 
        action='store_true', 
        help='Force incremental sync'
    )
    
    args = parser.parse_args()
    
    try:
        if args.full:
            sync(force_full=True)
        elif args.incremental:
            sync(force_full=False)
        else:
            # Default behavior from config
            sync(force_full=False)
    except KeyboardInterrupt:
        logger.info("Sync interrupted by user")
        print("\n\nSync interrupted by user")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Unexpected error: {e}", exc_info=True)
        print(f"\n\nFatal error: {e}")
        sys.exit(1)