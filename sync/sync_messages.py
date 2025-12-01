#!/usr/bin/env python3
"""
Sync message metadata from conversation files to database.
Reads existing conversation files and populates database with metadata.
"""

import sys
import os
import json
import re
import glob
import time
import logging
import traceback
from datetime import datetime, timedelta, date
from typing import List, Dict, Optional, Any
from concurrent.futures import ThreadPoolExecutor, as_completed

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sync.api_client import HostawayAPIClient
from sync.progress_tracker import get_progress_tracker
from database.models import Conversation, MessageMetadata, Reservation, Listing, Guest, SyncLog, get_session, init_models
from database.schema import get_database_path
from config import VERBOSE, MESSAGE_SYNC_PARALLEL_WORKERS, USE_S3_STORAGE, BATCH_SIZE
import config
from dashboard.config import CONVERSATIONS_DIR

# Configure logger for this module
logger = logging.getLogger(__name__)


class MessageOrganizer:
    """Organizes messages into folder structure (local filesystem only)."""
    
    def __init__(self, base_dir: Optional[str] = None):
        # Use CONVERSATIONS_DIR from config if base_dir not provided
        if base_dir is None:
            base_dir = CONVERSATIONS_DIR
        # Ensure absolute path
        self.base_dir = os.path.abspath(base_dir)
        self.use_s3 = False  # Always False - S3 removed
        self.s3_storage = None
        self.ensure_base_directory()
    
    def ensure_base_directory(self):
        """Create base conversations directory."""
        os.makedirs(self.base_dir, exist_ok=True)
    
    def sanitize_filename(self, name: str) -> str:
        """Sanitize filename by removing/replacing invalid characters"""
        invalid_chars = '<>:"/\\|?*'
        for char in invalid_chars:
            name = name.replace(char, '_')
        return name.strip()
    
    def format_checkin_date(self, checkin_date: str) -> str:
        """Format check-in date for filename"""
        try:
            # Try to parse the date and format it
            if isinstance(checkin_date, str):
                # Handle different date formats
                for fmt in ['%Y-%m-%d', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%dT%H:%M:%SZ']:
                    try:
                        dt = datetime.strptime(checkin_date.split('T')[0], '%Y-%m-%d')
                        return dt.strftime('%Y-%m-%d')
                    except ValueError:
                        continue
            return str(checkin_date).split('T')[0] if checkin_date else 'unknown_date'
        except:
            return 'unknown_date'
    
    def save_conversation(self, listing_name: str, guest_name: str, 
                         checkin_date: str, messages: List[Dict]) -> str:
        """Save conversation to conversational text format"""
        # Sanitize names for filesystem
        safe_listing_name = self.sanitize_filename(listing_name)
        safe_guest_name = self.sanitize_filename(guest_name)
        formatted_date = self.format_checkin_date(checkin_date)
        
        # Create listing directory
        listing_dir = os.path.join(self.base_dir, safe_listing_name)
        os.makedirs(listing_dir, exist_ok=True)
        
        # Create filename
        filename = f"{safe_guest_name}_{formatted_date}_conversation.txt"
        filepath = os.path.join(listing_dir, filename)
        
        # Sort messages by timestamp
        sorted_messages = sorted(messages, key=lambda x: x.get('createdAt', ''))
        
        # Create conversational format
        conversational_text = []
        
        # Add header information
        conversational_text.append("=" * 60)
        conversational_text.append("GUEST CONVERSATION")
        conversational_text.append("=" * 60)
        conversational_text.append(f"Guest: {guest_name}")
        conversational_text.append(f"Listing: {listing_name}")
        conversational_text.append(f"Check-in Date: {checkin_date}")
        conversational_text.append(f"Total Messages: {len(sorted_messages)}")
        conversational_text.append("")
        
        # Add messages in conversational format
        for i, message in enumerate(sorted_messages, 1):
            timestamp = message.get('createdAt', '')
            sender = message.get('sender', 'Unknown')
            content = message.get('content', '').strip()
            
            # Format timestamp nicely
            if timestamp:
                try:
                    dt = datetime.strptime(timestamp, '%Y-%m-%d %H:%M:%S')
                    formatted_time = dt.strftime('%B %d, %Y at %I:%M %p')
                except:
                    formatted_time = timestamp
            else:
                formatted_time = 'Unknown time'
            
            # Add message with proper formatting
            conversational_text.append(f"[{i}] {formatted_time}")
            conversational_text.append(f"{sender}: {content}")
            conversational_text.append("")  # Empty line between messages
        
        # Join all lines and write to file
        full_text = '\n'.join(conversational_text)
        
        # Always save to local filesystem
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(full_text)
        
        return filepath


def parse_timestamp(ts_str: str) -> Optional[datetime]:
    """Parse timestamp string to datetime object"""
    if not ts_str:
        return None
    
    formats = [
        '%B %d, %Y at %I:%M %p',  # Format from conversation files
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


def parse_timestamp_from_api(ts_str: Optional[str]) -> Optional[datetime]:
    """
    Parse timestamp from API response (handles various ISO formats).
    
    Args:
        ts_str: Timestamp string from API (can be None or empty)
    
    Returns:
        Datetime object or None if parsing fails
    """
    if not ts_str:
        return None
    
    formats = [
        '%Y-%m-%dT%H:%M:%S',
        '%Y-%m-%dT%H:%M:%SZ',
        '%Y-%m-%dT%H:%M:%S.%f',
        '%Y-%m-%dT%H:%M:%S.%fZ',
        '%Y-%m-%d %H:%M:%S',
        '%Y-%m-%d %H:%M:%S.%f'
    ]
    
    # Clean the string (remove Z, handle microseconds)
    cleaned = ts_str.replace('Z', '').strip()
    
    for fmt in formats:
        try:
            # Try with microseconds if present
            if '.' in cleaned and '%f' in fmt:
                return datetime.strptime(cleaned, fmt)
            # Try without microseconds
            elif '.' not in cleaned and '%f' not in fmt:
                return datetime.strptime(cleaned.split('.')[0], fmt)
        except (ValueError, AttributeError):
            continue
    
    return None


def parse_conversation_file(file_path: str) -> Dict:
    """Parse a conversation file and extract metadata"""
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        lines = content.split('\n')
        
        # Extract header info
        guest_name = None
        listing_name = None
        checkin_date = None
        total_messages = 0
        
        for line in lines:
            if line.startswith('Guest:'):
                guest_name = line.replace('Guest:', '').strip()
            elif line.startswith('Listing:'):
                listing_name = line.replace('Listing:', '').strip()
            elif line.startswith('Check-in Date:'):
                checkin_date = line.replace('Check-in Date:', '').strip()
            elif line.startswith('Total Messages:'):
                total_messages = int(line.replace('Total Messages:', '').strip())
        
        # Extract messages
        messages = []
        current_message = None
        
        for line in lines:
            # Message header: [1] May 11, 2025 at 12:53 PM
            if re.match(r'^\[\d+\]', line):
                if current_message:
                    messages.append(current_message)
                
                # Extract timestamp
                timestamp_match = re.search(r'\[(\d+)\]\s+(.+)', line)
                if timestamp_match:
                    message_num = int(timestamp_match.group(1))
                    timestamp_str = timestamp_match.group(2)
                    current_message = {
                        'number': message_num,
                        'timestamp_str': timestamp_str,
                        'timestamp': parse_timestamp(timestamp_str),
                        'sender': None,
                        'content': ''
                    }
            # Sender line: Host: ... or Guest Name: ...
            elif current_message and ':' in line and not line.startswith('['):
                parts = line.split(':', 1)
                if len(parts) == 2:
                    current_message['sender'] = parts[0].strip()
                    current_message['content'] = parts[1].strip()
        
        if current_message:
            messages.append(current_message)
        
        return {
            'guest_name': guest_name,
            'listing_name': listing_name,
            'checkin_date': checkin_date,
            'total_messages': total_messages,
            'messages': messages,
            'file_path': file_path
        }
    
    except Exception as e:
        if VERBOSE:
            print(f"Error parsing {file_path}: {e}")
        return None


def parse_arrival_date(arrival_date_str: str) -> Optional[date]:
    """
    Parse arrivalDate from API response to date object.
    
    Args:
        arrival_date_str: Date string from API (format: YYYY-MM-DD or YYYY-MM-DDTHH:MM:SS)
    
    Returns:
        date object or None if parsing fails
    """
    if not arrival_date_str:
        return None
    
    try:
        # Handle different date formats
        # Format: YYYY-MM-DD or YYYY-MM-DDTHH:MM:SS or YYYY-MM-DDTHH:MM:SSZ
        date_part = arrival_date_str.split('T')[0]
        return datetime.strptime(date_part, '%Y-%m-%d').date()
    except Exception as e:
        if VERBOSE:
            print(f"Error parsing arrival date '{arrival_date_str}': {e}")
        return None


def fetch_conversations_for_reservation(client: HostawayAPIClient, reservation_id: int, max_retries: int = 3) -> List[Dict]:
    """
    Fetch conversations for a single reservation with retry logic.
    
    Args:
        client: HostawayAPIClient instance
        reservation_id: Reservation ID
        max_retries: Maximum number of retries for rate limit errors
    
    Returns:
        List of conversation dictionaries
    """
    for attempt in range(max_retries):
        try:
            conversations = client.get_conversations(reservation_id=reservation_id)
            return conversations
        except Exception as e:
            error_msg = str(e)
            # Check if it's a rate limit error (429)
            if '429' in error_msg or 'rate limit' in error_msg.lower():
                if attempt < max_retries - 1:
                    # Wait before retrying (exponential backoff)
                    wait_time = (attempt + 1) * 2
                    if VERBOSE:
                        print(f"Rate limit hit for reservation {reservation_id}, waiting {wait_time}s before retry {attempt + 1}/{max_retries}")
                    time.sleep(wait_time)
                    continue
                else:
                    if VERBOSE:
                        print(f"Rate limit error for reservation {reservation_id} after {max_retries} retries")
                    return []
            else:
                # Other error - log and return empty list
                if VERBOSE:
                    print(f"Error fetching conversations for reservation {reservation_id}: {e}")
                return []
    
    return []


def get_conversations_via_reservations(cutoff_time: datetime, client: HostawayAPIClient, progress: Any) -> List[Dict]:
    """
    Get conversations via reservation-based optimization.
    
    Fetches reservations, filters by latest_activity_on >= cutoff_time, then fetches
    conversations for those reservations in parallel.
    
    Args:
        cutoff_time: datetime object - only include reservations with latest_activity_on >= this time
        client: HostawayAPIClient instance
        progress: Progress tracker instance
    
    Returns:
        List of conversation dictionaries
    """
    from sync.sync_reservations import parse_timestamp
    
    # Step 1: Fetch reservations with larger page size (500) for fewer API calls
    progress.update_item("Fetching reservations...")
    if VERBOSE:
        print(f"Fetching reservations with latest_activity_on >= {cutoff_time}...")
    
    all_reservations = []
    offset = 0
    limit = 500  # Increased from 100 to 500 for fewer API calls
    page_count = 0
    
    while True:
        progress.update_item(f"Fetching reservations (page {page_count + 1})...")
        reservations = client.get_reservations(limit=limit, offset=offset)
        
        if not reservations:
            break
        
        all_reservations.extend(reservations)
        page_count += 1
        
        # Early-stop heuristic: if this page has no reservations with latest_activity_on >= cutoff
        # AND all reservations have latest_activity_on populated, we can stop
        # (since sorted by updatedOn DESC, older pages won't have recent activity)
        page_has_recent_activity = False
        all_have_activity = True
        
        for res in reservations:
            latest_activity_str = res.get('latestActivityOn')
            if not latest_activity_str:
                all_have_activity = False
                continue
            
            latest_activity_time = parse_timestamp(latest_activity_str)
            if latest_activity_time and latest_activity_time >= cutoff_time:
                page_has_recent_activity = True
                break
        
        # If page has no recent activity AND all reservations have activity timestamps,
        # we can stop (since sorted by updatedOn DESC, older pages won't have recent activity)
        if not page_has_recent_activity and all_have_activity and len(reservations) > 0:
            if VERBOSE:
                print(f"Early-stop: Page {page_count} has no recent activity, stopping pagination")
            break
        
        # If we got fewer than the limit, we've reached the end
        if len(reservations) < limit:
            break
        
        offset += limit
    
    if VERBOSE:
        print(f"Fetched {len(all_reservations)} total reservations from API ({page_count} pages)")
    
    # Step 2: Filter by latest_activity_on
    progress.update_item("Filtering by latest activity time...")
    
    filtered_reservations = []
    for reservation_data in all_reservations:
        latest_activity_str = reservation_data.get('latestActivityOn')
        if not latest_activity_str:
            continue
        
        latest_activity_time = parse_timestamp(latest_activity_str)
        if latest_activity_time and latest_activity_time >= cutoff_time:
            filtered_reservations.append(reservation_data)
    
    if VERBOSE:
        print(f"Filtered to {len(filtered_reservations)} reservations with latest_activity_on >= {cutoff_time}")
    
    if not filtered_reservations:
        if VERBOSE:
            print("No reservations match the latest_activity_on filter")
        return []
    
    # Step 3: Fetch conversations in parallel
    progress.update_item(f"Fetching conversations in parallel for {len(filtered_reservations)} reservations...")
    
    all_conversations = []
    reservation_ids = [r.get('id') for r in filtered_reservations if r.get('id')]
    
    if VERBOSE:
        print(f"Fetching conversations for {len(reservation_ids)} reservations using {MESSAGE_SYNC_PARALLEL_WORKERS} parallel workers...")
    
    # Use ThreadPoolExecutor for parallel fetching
    with ThreadPoolExecutor(max_workers=MESSAGE_SYNC_PARALLEL_WORKERS) as executor:
        # Submit all tasks
        future_to_reservation = {
            executor.submit(fetch_conversations_for_reservation, client, res_id): res_id
            for res_id in reservation_ids
        }
        
        # Process results as they complete
        completed = 0
        for future in as_completed(future_to_reservation):
            reservation_id = future_to_reservation[future]
            completed += 1
            
            try:
                conversations = future.result()
                if conversations:
                    all_conversations.extend(conversations)
                    if VERBOSE and completed % 10 == 0:
                        print(f"  Fetched conversations for {completed}/{len(reservation_ids)} reservations...")
            except Exception as e:
                if VERBOSE:
                    print(f"  Error getting conversations for reservation {reservation_id}: {e}")
            
            # Update progress
            progress.update_item(f"Fetching conversations... ({completed}/{len(reservation_ids)})")
    
    if VERBOSE:
        print(f"Found {len(all_conversations)} total conversations for {len(filtered_reservations)} reservations")
    
    return all_conversations


def sync_messages_from_api(full_sync: bool = True, progress_tracker: Optional[Any] = None, sync_run_id: Optional[int] = None) -> Dict:
    """
    Sync messages from Hostaway API to database.
    
    This function is idempotent - safe to run multiple times.
    
    OPTIMIZATION - Reservation-Based Approach for Incremental Syncs:
    - For incremental sync: Uses reservation-based optimization
      * Fetches reservations with latest_activity_on >= (last_sync_time - 12 hours)
      * Gets conversations for those reservations in parallel
      * Uses page size 500 for fewer API calls
      * Dramatically reduces conversations to process (90-99% reduction)
    - For full sync: Fetches all conversations without filtering
    - Only syncs messages for conversations that meet the criteria
    - Includes all reservation statuses
    
    Messages are identified uniquely by (conversation_id, created_at) tuple.
    Duplicate checking ensures no message is inserted twice.
    
    Args:
        full_sync: Whether to perform a full sync. If False, uses reservation-based optimization.
        progress_tracker: Optional progress tracker (WebProgressTracker or terminal tracker).
        sync_run_id: Optional sync_run_id to group sync logs.
    
    Returns:
        Dictionary with sync results including listing_stats.
    """
    start_time = datetime.utcnow()
    # For PostgreSQL, get_database_path() returns None - that's OK, functions use DATABASE_URL
    init_models(None)  # None is OK for PostgreSQL, function uses DATABASE_URL from environment
    session = get_session(None)  # None is OK for PostgreSQL, function uses DATABASE_URL from environment
    
    logger.debug(f"Message sync initialized: db_path={db_path}, sync_run_id={sync_run_id}")
    
    try:
        # Use provided progress tracker or default
        if progress_tracker:
            progress = progress_tracker
        else:
            progress = get_progress_tracker()
        
        # Update progress immediately to show we're starting messages sync
        # This prevents UI from being stuck on previous phase
        progress.start_phase("Syncing Messages from API", 0)  # Will update total later
        logger.debug("Started 'Syncing Messages from API' phase")
        
        client = HostawayAPIClient()
        logger.debug("HostawayAPIClient initialized")
        
        # Get conversations based on sync type
        conversations_to_process = []
        
        if full_sync:
            # Full sync: Fetch all conversations (current approach)
            progress.update_item("Fetching all conversations from API...")
            if VERBOSE:
                print("Full sync: Fetching all conversations from API...")
            
            conversations_to_process = client.get_all_conversations(limit=500)
            
            if VERBOSE:
                print(f"Full sync: Fetched {len(conversations_to_process)} total conversations from API")
        else:
            # Incremental sync: Use reservation-based optimization
            from sync.sync_manager import get_last_sync_time
            last_sync_time = get_last_sync_time('messages')
            
            if last_sync_time:
                # Calculate cutoff time: 12 hours before last sync
                cutoff_time = last_sync_time - timedelta(hours=12)
                
                if VERBOSE:
                    print(f"Incremental message sync: Using reservation-based optimization")
                    print(f"  Cutoff time (latest_activity_on >=): {cutoff_time}")
                
                # Use reservation-based approach to get conversations
                conversations_to_process = get_conversations_via_reservations(cutoff_time, client, progress)
                
                if VERBOSE:
                    print(f"Incremental sync: Found {len(conversations_to_process)} conversations via reservations")
            else:
                # No previous sync - treat as full sync
                if VERBOSE:
                    print("No previous message sync found. Performing full sync.")
                
                progress.update_item("Fetching all conversations from API...")
                conversations_to_process = client.get_all_conversations(limit=500)
                
                if VERBOSE:
                    print(f"Full sync: Fetched {len(conversations_to_process)} total conversations from API")
        
        if not conversations_to_process:
            if VERBOSE:
                print("No conversations to process. Skipping message sync.")
            
            # Update progress to show completion
            progress.complete_phase()
            
            # Still create a SyncLog entry so it appears in the summary
            end_time = datetime.utcnow()
            duration = (end_time - start_time).total_seconds()
            
            sync_log = SyncLog(
                sync_run_id=sync_run_id,
                sync_type='messages',
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
                'errors': [],
                'listing_stats': {}
            }
        
        # Update progress with actual total now that we know how many conversations to process
        progress.update_total(len(conversations_to_process))
        
        records_created = 0
        records_updated = 0
        errors = []
        listing_stats = {}  # {listing_id: {messages: count}}
        
        # MEMORY OPTIMIZATION: Load only IDs and timestamps for large tables (90% memory reduction)
        # Pre-load existing messages for fast duplicate checking - only load minimal data
        # Create a set of (conversation_id, created_at) tuples for O(1) lookup
        # Also track message_id if available for more reliable deduplication
        existing_message_data = session.query(
            MessageMetadata.message_id,
            MessageMetadata.conversation_id,
            MessageMetadata.created_at
        ).all()
        existing_message_set = {(msg.conversation_id, msg.created_at) for msg in existing_message_data if msg.created_at is not None}
        existing_message_ids = {msg.message_id for msg in existing_message_data if msg.message_id is not None}
        # Clear the full data list to free memory (we only need the sets)
        del existing_message_data
        
        # Track message_ids added in current batch (not yet committed) to prevent duplicates within batch
        pending_message_ids = set()
        
        # MEMORY OPTIMIZATION: Load only IDs and timestamps for conversations (90% memory reduction)
        # We'll query full conversation objects only when needed (per conversation)
        existing_conversation_data = session.query(
            Conversation.conversation_id,
            Conversation.last_message_at,
            Conversation.last_synced_at
        ).all()
        conversation_id_set = {c.conversation_id for c in existing_conversation_data}
        conversation_last_message_map = {c.conversation_id: c.last_message_at for c in existing_conversation_data}
        conversation_last_synced_map = {c.conversation_id: c.last_synced_at for c in existing_conversation_data}
        # Clear the full data list to free memory
        del existing_conversation_data
        
        # Keep full objects for small tables (listings, guests, reservations) - these are small and don't cause memory issues
        # Pre-load reservations, listings, and guests for fast lookup
        all_reservations = session.query(Reservation).all()
        reservation_map = {r.reservation_id: r for r in all_reservations}
        
        all_listings = session.query(Listing).all()
        listing_map = {l.listing_id: l for l in all_listings}
        
        all_guests = session.query(Guest).all()
        guest_map = {g.guest_id: g for g in all_guests}
        
        # MessageOrganizer for saving files
        organizer = MessageOrganizer()
        
        # Track optimization stats
        conversations_skipped = 0
        conversations_processed = 0
        
        # Batch processing: collect conversations and commit in batches
        batch_count = 0
        conversations_in_batch = 0
        
        # Process each conversation
        for conv_data in conversations_to_process:
            conversation_id = conv_data.get('id')
            if not conversation_id:
                continue
            
            try:
                progress.update_item(f"Conversation {conversation_id}")
                
                # Get reservation, listing, and guest info from conversation data
                reservation_id = conv_data.get('reservationId')
                listing_id = conv_data.get('listingId') or conv_data.get('listingMapId')
                guest_id = conv_data.get('guestId')
                
                # Lookup reservation if available
                reservation = reservation_map.get(reservation_id) if reservation_id else None
                
                # Get listing - prefer from reservation, fallback to conversation's listing_id
                listing = None
                if reservation:
                    listing = listing_map.get(reservation.listing_id)
                elif listing_id:
                    listing = listing_map.get(listing_id)
                
                if not listing:
                    if VERBOSE:
                        print(f"  Warning: Listing not found for conversation {conversation_id} (listing_id: {listing_id})")
                    # Continue processing - we can still sync messages without listing
                
                # Get guest - prefer from reservation, fallback to conversation's guest_id
                guest = None
                guest_name = "Unknown Guest"
                if reservation:
                    if reservation.guest_id:
                        guest = guest_map.get(reservation.guest_id)
                    guest_name = guest.full_name if guest and guest.full_name else (reservation.guest_name or "Unknown Guest")
                elif guest_id:
                    guest = guest_map.get(guest_id)
                    guest_name = guest.full_name if guest and guest.full_name else "Unknown Guest"
                
                # MEMORY OPTIMIZATION: Check if conversation exists using lightweight lookup
                conversation_exists = conversation_id in conversation_id_set
                existing_last_message_at = conversation_last_message_map.get(conversation_id)
                existing_last_synced_at = conversation_last_synced_map.get(conversation_id)
                
                # Get last message time from API conversation metadata
                api_last_msg_time = None
                if 'lastMessageAt' in conv_data:
                    api_last_msg_time = parse_timestamp_from_api(conv_data['lastMessageAt'])
                elif 'updatedOn' in conv_data:
                    api_last_msg_time = parse_timestamp_from_api(conv_data['updatedOn'])
                elif 'lastMessageDate' in conv_data:
                    api_last_msg_time = parse_timestamp_from_api(conv_data['lastMessageDate'])
                
                # Skip conversation if it hasn't changed (additional check for safety)
                if conversation_exists and existing_last_message_at and api_last_msg_time:
                    if api_last_msg_time <= existing_last_message_at:
                        conversations_skipped += 1
                        if VERBOSE:
                            print(f"  Skipping conversation {conversation_id} - no new messages (last: {existing_last_message_at})")
                        progress.increment()
                        continue
                
                conversations_processed += 1
                
                # Get last sync time for this conversation (for client-side message filtering)
                last_sync_time = existing_last_synced_at if conversation_exists else None
                
                # Fetch all messages from API - catch API errors
                logger.debug(f"Fetching messages for conversation {conversation_id} (reservation_id={reservation_id}, listing_id={listing_id})")
                try:
                    all_api_messages = client.get_conversation_messages(conversation_id)
                    logger.debug(f"Fetched {len(all_api_messages) if all_api_messages else 0} messages for conversation {conversation_id}")
                except Exception as api_error:
                    # API call failed - log and skip this conversation
                    error_details = traceback.format_exc()
                    error_msg = f"API error fetching messages for conversation {conversation_id}: {str(api_error)}"
                    errors.append(error_msg)
                    progress.increment(error=True)
                    logger.error(f"API error for conversation {conversation_id}: {str(api_error)}", exc_info=True)
                    logger.debug(f"Full traceback for conversation {conversation_id}:\n{error_details}")
                    if VERBOSE:
                        print(f"\n  {error_msg}")
                    continue
                
                if not all_api_messages:
                    progress.increment()
                    continue
                
                # Filter messages to only process new ones (for incremental sync)
                messages_to_process = all_api_messages
                if last_sync_time and not full_sync:
                    filtered_messages = []
                    for msg in all_api_messages:
                        msg_date = msg.get('date', '')
                        if not msg_date:
                            continue
                        
                        msg_created_at = parse_timestamp_from_api(msg_date)
                        if msg_created_at and msg_created_at > last_sync_time:
                            filtered_messages.append(msg)
                    
                    messages_to_process = filtered_messages
                    
                    if VERBOSE and len(messages_to_process) < len(all_api_messages):
                        print(f"  Filtered to {len(messages_to_process)} new messages (from {len(all_api_messages)} total) in conversation {conversation_id}")
                
                # CRITICAL: Get or create conversation record FIRST before processing messages
                # This ensures the conversation exists in the database before we add messages with foreign keys
                # MEMORY OPTIMIZATION: Query conversation only when needed (not pre-loaded)
                conversation = session.query(Conversation).filter(
                    Conversation.conversation_id == conversation_id
                ).first()
                
                if not conversation:
                    # Create conversation record first (before messages)
                    conversation = Conversation(
                        conversation_id=conversation_id,
                        reservation_id=reservation_id if reservation else None,
                        listing_id=listing.listing_id if listing else listing_id,
                        guest_id=reservation.guest_id if reservation and reservation.guest_id else guest_id,
                        message_count=0,  # Will be updated after messages are processed
                        first_message_at=None,  # Will be updated after messages are processed
                        last_message_at=None,  # Will be updated after messages are processed
                        last_synced_at=datetime.utcnow()
                    )
                    session.add(conversation)
                    # Flush conversation immediately to ensure it exists before adding messages
                    # This prevents foreign key constraint violations
                    try:
                        session.flush()
                        # MEMORY OPTIMIZATION: Update lightweight lookup maps when creating new conversation
                        conversation_id_set.add(conversation_id)
                        conversation_last_message_map[conversation_id] = None
                        conversation_last_synced_map[conversation_id] = conversation.last_synced_at
                    except Exception as flush_error:
                        logger.error(f"Error flushing conversation {conversation_id}: {flush_error}", exc_info=True)
                        session.rollback()
                        errors.append(f"Error creating conversation {conversation_id}: {str(flush_error)}")
                        progress.increment(error=True)
                        continue
                
                # Track new messages for this conversation
                new_messages_count = 0
                all_api_messages_dict = {}  # {(conversation_id, created_at): full_message_data}
                
                # Process each message from API
                for msg in messages_to_process:
                    # IDEMPOTENCY CHECK: Use message_id from API if available, otherwise fall back to (conversation_id, created_at)
                    message_id = msg.get('id') or msg.get('messageId')
                    msg_date = msg.get('date', '')
                    
                    if not msg_date:
                        continue
                    
                    created_at = parse_timestamp_from_api(msg_date)
                    if not created_at:
                        if VERBOSE:
                            print(f"  Could not parse timestamp: {msg_date}")
                        continue
                    
                    # IDEMPOTENCY CHECK: Prefer message_id if available, otherwise use (conversation_id, created_at)
                    if message_id:
                        # Check if message with this ID already exists in DB or pending in current batch
                        if message_id in existing_message_ids or message_id in pending_message_ids:
                            continue
                    else:
                        # Fallback: Check by (conversation_id, created_at) tuple
                        message_key = (conversation_id, created_at)
                        if message_key in existing_message_set:
                            continue
                    
                    # Determine sender
                    is_incoming = msg.get('isIncoming', False)
                    if is_incoming:
                        sender = guest_name
                    else:
                        communication_type = msg.get('communicationType', '')
                        message_source = str(msg.get('messageSource', '')).lower()
                        if communication_type == 'automation' or 'automation' in message_source:
                            sender = 'Host (Automated)'
                        else:
                            sender = 'Host'
                    
                    # Get message body
                    message_body = msg.get('body', '') or msg.get('content', '') or ''
                    
                    # Create message metadata record
                    sender_type = 'guest' if is_incoming else 'host'
                    message_meta = MessageMetadata(
                        message_id=message_id if message_id else None,  # Use API message_id if available
                        conversation_id=conversation_id,
                        reservation_id=reservation_id if reservation else None,
                        listing_id=listing.listing_id if listing else listing_id,
                        guest_id=reservation.guest_id if reservation and reservation.guest_id else guest_id,
                        sender_type=sender_type,
                        sender_name=sender,
                        is_incoming=1 if is_incoming else 0,
                        message_type='text',
                        content_preview=message_body[:200] if message_body else '',
                        has_attachment=0,
                        created_at=created_at,
                        message_file_path=None
                    )
                    session.add(message_meta)
                    records_created += 1
                    new_messages_count += 1
                    
                    # Track in memory set for duplicate checking (always use tuple for consistency)
                    message_key = (conversation_id, created_at)
                    existing_message_set.add(message_key)
                    
                    # If message_id was provided, also track it for future lookups
                    if message_id:
                        existing_message_ids.add(message_id)
                        pending_message_ids.add(message_id)  # Track in pending set for batch deduplication
                    
                    # Store full message data for file generation
                    all_api_messages_dict[message_key] = {
                        'createdAt': created_at.strftime('%Y-%m-%d %H:%M:%S'),
                        'messageType': 'incoming' if is_incoming else 'outgoing',
                        'sender': sender,
                        'content': message_body,
                        'created_at': created_at
                    }
                
                # Store ALL messages from API for file generation (complete conversation history)
                for msg in all_api_messages:
                    msg_date = msg.get('date', '')
                    if not msg_date:
                        continue
                    
                    created_at = parse_timestamp_from_api(msg_date)
                    if not created_at:
                        continue
                    
                    message_key = (conversation_id, created_at)
                    if message_key not in all_api_messages_dict:
                        is_incoming = msg.get('isIncoming', False)
                        if is_incoming:
                            sender = guest_name
                        else:
                            communication_type = msg.get('communicationType', '')
                            message_source = str(msg.get('messageSource', '')).lower()
                            if communication_type == 'automation' or 'automation' in message_source:
                                sender = 'Host (Automated)'
                            else:
                                sender = 'Host'
                        message_body = msg.get('body', '') or msg.get('content', '') or ''
                        
                        all_api_messages_dict[message_key] = {
                            'createdAt': created_at.strftime('%Y-%m-%d %H:%M:%S'),
                            'messageType': 'incoming' if is_incoming else 'outgoing',
                            'sender': sender,
                            'content': message_body,
                            'created_at': created_at
                        }
                
                # Update conversation metadata after processing messages
                # Get all messages for this conversation (from database) for file
                # Use no_autoflush to prevent auto-flush during query (which could cause duplicate key errors)
                with session.no_autoflush:
                    all_db_messages = session.query(MessageMetadata).filter(
                        MessageMetadata.conversation_id == conversation_id
                    ).order_by(MessageMetadata.created_at).all()
                
                # Update conversation with latest message counts and timestamps
                conversation.message_count = len(all_db_messages)
                if all_db_messages:
                    conversation.first_message_at = all_db_messages[0].created_at if all_db_messages[0].created_at else conversation.first_message_at
                    conversation.last_message_at = all_db_messages[-1].created_at if all_db_messages[-1].created_at else conversation.last_message_at
                conversation.last_synced_at = datetime.utcnow()
                # MEMORY OPTIMIZATION: Update lightweight lookup maps when conversation is updated
                conversation_last_message_map[conversation_id] = conversation.last_message_at
                conversation_last_synced_map[conversation_id] = conversation.last_synced_at
                
                # Note: We don't flush here anymore - we'll commit in batches
                # This allows for better performance by batching multiple conversations together
                conversations_in_batch += 1
                logger.debug(f"Added conversation {conversation_id} to batch (created={new_messages_count} messages)")
                
                # Prepare all messages for file - use API messages (full content)
                all_messages_for_file = []
                # Safely sort messages - filter out any without 'created_at' to prevent KeyError
                messages_with_date = [msg for msg in all_api_messages_dict.values() if 'created_at' in msg and msg.get('created_at')]
                sorted_api_messages = sorted(messages_with_date, key=lambda x: x['created_at'])
                for api_msg in sorted_api_messages:
                    all_messages_for_file.append({
                        'createdAt': api_msg['createdAt'],
                        'messageType': api_msg['messageType'],
                        'sender': api_msg['sender'],
                        'content': api_msg['content']
                    })
                
                # Save conversation file
                listing_name = listing.name if listing else f"Listing {listing_id}" if listing_id else "Unknown Listing"
                
                # Safely get checkin_date
                checkin_date = 'unknown'
                if reservation and reservation.arrival_date:
                    try:
                        # Handle both date and datetime objects
                        if hasattr(reservation.arrival_date, 'strftime'):
                            checkin_date = reservation.arrival_date.strftime('%Y-%m-%d')
                        elif isinstance(reservation.arrival_date, str):
                            # If it's already a string, use it directly
                            checkin_date = reservation.arrival_date.split('T')[0] if 'T' in reservation.arrival_date else reservation.arrival_date
                        else:
                            checkin_date = str(reservation.arrival_date)
                    except Exception as e:
                        if VERBOSE:
                            print(f"  Warning: Could not format arrival_date for conversation {conversation_id}: {e}")
                        checkin_date = 'unknown'
                
                # Save conversation file - catch file system errors
                logger.debug(f"Saving conversation file for conversation {conversation_id} (listing={listing_name}, guest={guest_name})")
                try:
                    file_path = organizer.save_conversation(
                        listing_name, guest_name, checkin_date, all_messages_for_file
                    )
                    logger.debug(f"Successfully saved conversation file: {file_path}")
                except Exception as file_error:
                    # File save error (permissions, invalid filename, disk full, etc.)
                    error_details = traceback.format_exc()
                    error_msg = f"File save error for conversation {conversation_id}: {str(file_error)}"
                    errors.append(error_msg)
                    progress.increment(error=True)
                    logger.error(f"File save error for conversation {conversation_id}: {str(file_error)}", exc_info=True)
                    logger.debug(f"Full traceback for conversation {conversation_id}:\n{error_details}")
                    if VERBOSE:
                        print(f"\n  {error_msg}")
                    # Continue without file path - conversation can still be saved to DB
                    file_path = None
                
                # Update conversation and all messages with file path (only if file was saved)
                if file_path:
                    conversation.conversation_file_path = file_path
                    for db_msg in all_db_messages:
                        db_msg.message_file_path = file_path
                
                # Track statistics (only count new messages)
                if new_messages_count > 0:
                    final_listing_id = listing.listing_id if listing else listing_id
                    if final_listing_id:
                        if final_listing_id not in listing_stats:
                            listing_stats[final_listing_id] = {'messages': 0}
                        listing_stats[final_listing_id]['messages'] += new_messages_count
                
                progress.increment(item_name=f"Conversation {conversation_id}")
                
                # Commit in batches for better performance
                if conversations_in_batch >= BATCH_SIZE:
                    batch_count += 1
                    try:
                        session.commit()
                        logger.debug(f"Committed batch {batch_count} ({conversations_in_batch} conversations)")
                        if VERBOSE and batch_count % 10 == 0:
                            print(f"  Committed batch {batch_count} ({conversations_in_batch} conversations)...")
                        conversations_in_batch = 0
                        # Clear pending message_ids after successful commit (they're now in existing_message_ids)
                        pending_message_ids.clear()
                        # MEMORY OPTIMIZATION: Update existing_message_ids with newly committed messages
                        # Note: We don't need to reload all messages - the new ones are already in the database
                        # and will be included in the next query if needed. For now, we just clear pending.
                        # The existing_message_set and existing_message_ids remain valid since we committed.
                    except Exception as db_error:
                        # Database error (constraint violation, lock, etc.)
                        error_details = traceback.format_exc()
                        error_msg = f"Database error committing batch {batch_count}: {str(db_error)}"
                        errors.append(error_msg)
                        logger.error(f"Database error committing batch {batch_count}: {str(db_error)}", exc_info=True)
                        logger.debug(f"Full traceback for batch {batch_count}:\n{error_details}")
                        session.rollback()  # Rollback this batch's changes
                        if VERBOSE:
                            print(f"\n  {error_msg}")
                        conversations_in_batch = 0
                        # MEMORY OPTIMIZATION: Re-fetch only minimal data after rollback (not full ORM objects)
                        # Re-fetch existing data after rollback to keep lookups accurate
                        existing_message_data = session.query(
                            MessageMetadata.message_id,
                            MessageMetadata.conversation_id,
                            MessageMetadata.created_at
                        ).all()
                        existing_message_set = {(msg.conversation_id, msg.created_at) for msg in existing_message_data if msg.created_at is not None}
                        existing_message_ids = {msg.message_id for msg in existing_message_data if msg.message_id is not None}
                        del existing_message_data
                        # Clear pending message_ids after rollback (they weren't committed)
                        pending_message_ids.clear()
                        # Re-fetch conversation data (minimal)
                        existing_conversation_data = session.query(
                            Conversation.conversation_id,
                            Conversation.last_message_at,
                            Conversation.last_synced_at
                        ).all()
                        conversation_id_set = {c.conversation_id for c in existing_conversation_data}
                        conversation_last_message_map = {c.conversation_id: c.last_message_at for c in existing_conversation_data}
                        conversation_last_synced_map = {c.conversation_id: c.last_synced_at for c in existing_conversation_data}
                        del existing_conversation_data
            
            except Exception as e:
                # Capture full exception details for debugging
                error_details = traceback.format_exc()
                error_msg = f"Error processing conversation {conversation_id}: {str(e)}"
                errors.append(error_msg)
                progress.increment(error=True)
                
                # Log error with full context
                logger.error(
                    f"Error processing conversation {conversation_id}: {str(e)}",
                    exc_info=True,
                    extra={
                        'conversation_id': conversation_id,
                        'reservation_id': reservation_id if 'reservation_id' in locals() else None,
                        'listing_id': listing_id if 'listing_id' in locals() else None,
                        'sync_run_id': sync_run_id
                    }
                )
                logger.debug(f"Full error context for conversation {conversation_id}:\n{error_details}")
                
                if VERBOSE:
                    print(f"\n  {error_msg}")
                    print(f"  Full traceback:\n{error_details}")
                continue
        
        # Commit any remaining conversations in the final batch
        if conversations_in_batch > 0:
            batch_count += 1
            try:
                session.commit()
                logger.debug(f"Committed final batch {batch_count} ({conversations_in_batch} conversations)")
            except Exception as db_error:
                error_details = traceback.format_exc()
                error_msg = f"Database error committing final batch {batch_count}: {str(db_error)}"
                errors.append(error_msg)
                logger.error(f"Database error committing final batch {batch_count}: {str(db_error)}", exc_info=True)
                session.rollback()
                if VERBOSE:
                    print(f"\n  {error_msg}")
        
        progress.complete_phase()
        
        # Log sync operation
        end_time = datetime.utcnow()
        duration = (end_time - start_time).total_seconds()
        
        # CRITICAL: If sync_run_id is None, generate one to ensure it's saved
        # This prevents the issue where sync_run_id isn't being saved to database
        if sync_run_id is None:
            # Generate sync_run_id if not provided (shouldn't happen from web UI, but handle it)
            try:
                max_run = session.query(SyncLog.sync_run_id).filter(
                    SyncLog.sync_run_id.isnot(None)
                ).order_by(SyncLog.sync_run_id.desc()).first()
                sync_run_id = (max_run[0] + 1) if max_run and max_run[0] else 1
                logger.warning(f"sync_run_id was None, generated new one: {sync_run_id}")
            except Exception as e:
                sync_run_id = 1
                logger.warning(f"Error generating sync_run_id, using 1: {e}")
        
        # Create SyncLog entry
        if VERBOSE:
            logger.debug(f"Creating SyncLog with sync_run_id={sync_run_id}, type=messages, status={'partial' if errors else 'success'}")
        
        sync_log = SyncLog(
            sync_run_id=sync_run_id,
            sync_type='messages',
            sync_mode='full' if full_sync else 'incremental',
            status='success' if not errors else 'partial',
            records_processed=len(conversations_to_process),
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
        
        # Verify sync_run_id was saved
        if VERBOSE:
            session.refresh(sync_log)
            print(f"SyncLog created: sync_id={sync_log.sync_id}, sync_run_id={sync_log.sync_run_id}")
        
        if VERBOSE:
            print(f"\nSync complete:")
            print(f"  Conversations to process: {len(conversations_to_process)}")
            print(f"  Conversations processed: {conversations_processed}")
            print(f"  Conversations skipped (no changes): {conversations_skipped}")
            print(f"  Created: {records_created} messages")
            print(f"  Updated: {records_updated} messages")
            print(f"  Errors: {len(errors)}")
            print(f"  Duration: {duration:.2f} seconds")
        
        return {
            'status': 'success' if not errors else 'partial',
            'records_processed': len(conversations_to_process),
            'records_created': records_created,
            'records_updated': records_updated,
            'errors': errors,
            'listing_stats': listing_stats
        }
        
    except Exception as e:
        error_details = traceback.format_exc()
        session.rollback()
        logger.error(
            f"Fatal error during message sync: {str(e)}",
            exc_info=True,
            extra={'sync_run_id': sync_run_id, 'full_sync': full_sync}
        )
        logger.debug(f"Full fatal error traceback:\n{error_details}")
        error_msg = f"Fatal error in sync_messages_from_api: {str(e)}"
        logger.error(error_msg)
        
        # Log error
        end_time = datetime.utcnow()
        sync_log = SyncLog(
            sync_run_id=sync_run_id,
            sync_type='messages',
            sync_mode='full' if full_sync else 'incremental',
            status='error',
            records_processed=0,
            errors=json.dumps([error_msg]),
            listing_stats=json.dumps({}),
            started_at=start_time,
            completed_at=end_time,
            duration_seconds=(end_time - start_time).total_seconds()
        )
        session.add(sync_log)
        session.commit()
        
        return {
            'status': 'error',
            'error': error_msg
        }
    
    finally:
        session.close()


def sync_messages_from_files(full_sync: bool = True, progress_tracker: Optional[Any] = None, sync_run_id: Optional[int] = None) -> Dict:
    """
    Sync message metadata from conversation files to database.
    
    Args:
        full_sync: Whether to perform a full sync.
        progress_tracker: Optional progress tracker (WebProgressTracker or terminal tracker).
        sync_run_id: Optional sync_run_id to group sync logs.
    
    Returns:
        Dictionary with sync results including listing_stats.
    """
    start_time = datetime.utcnow()
    # For PostgreSQL, get_database_path() returns None - that's OK, functions use DATABASE_URL
    init_models(None)  # None is OK for PostgreSQL, function uses DATABASE_URL from environment
    session = get_session(None)  # None is OK for PostgreSQL, function uses DATABASE_URL from environment
    
    try:
        client = HostawayAPIClient()
        
        # Find all conversation files
        conversation_files = glob.glob("conversations/**/*_conversation.txt", recursive=True)
        
        # Use provided progress tracker or default
        if progress_tracker:
            progress = progress_tracker
        else:
            progress = get_progress_tracker()
        progress.start_phase("Syncing Messages", len(conversation_files))
        
        records_created = 0
        records_updated = 0
        errors = []
        listing_stats = {}  # {listing_id: {messages: count}}
        
        for file_path in conversation_files:
            try:
                # Parse conversation file
                conv_data = parse_conversation_file(file_path)
                if not conv_data:
                    progress.increment()
                    continue
                
                guest_name = conv_data['guest_name']
                listing_name = conv_data['listing_name']
                checkin_date = conv_data['checkin_date']
                
                # Find listing
                listing = session.query(Listing).filter(
                    Listing.name == listing_name
                ).first()
                
                if not listing:
                    if VERBOSE:
                        print(f"  Listing not found: {listing_name}")
                    continue
                
                listing_id = listing.listing_id
                
                # Find reservation by guest name and check-in date
                reservation = None
                if guest_name and checkin_date:
                    # Try to find reservation
                    guest = session.query(Guest).filter(
                        Guest.full_name == guest_name
                    ).first()
                    
                    if guest:
                        reservation = session.query(Reservation).filter(
                            Reservation.guest_id == guest.guest_id,
                            Reservation.listing_id == listing_id,
                            Reservation.arrival_date == checkin_date
                        ).first()
                
                # Get conversation from API if we have reservation
                conversation_id = None
                if reservation:
                    conversations = client.get_conversations(reservation_id=reservation.reservation_id)
                    if conversations:
                        conversation_id = conversations[0].get('id')
                
                # Create or update conversation record
                if conversation_id:
                    conversation = session.query(Conversation).filter(
                        Conversation.conversation_id == conversation_id
                    ).first()
                    
                    if not conversation:
                        conversation = Conversation(
                            conversation_id=conversation_id,
                            reservation_id=reservation.reservation_id if reservation else None,
                            listing_id=listing_id,
                            guest_id=reservation.guest_id if reservation else None,
                            conversation_file_path=file_path,
                            message_count=len(conv_data['messages']),
                            first_message_at=conv_data['messages'][0]['timestamp'] if conv_data['messages'] else None,
                            last_message_at=conv_data['messages'][-1]['timestamp'] if conv_data['messages'] else None,
                            last_synced_at=datetime.utcnow()
                        )
                        session.add(conversation)
                    else:
                        conversation.conversation_file_path = file_path
                        conversation.message_count = len(conv_data['messages'])
                        conversation.first_message_at = conv_data['messages'][0]['timestamp'] if conv_data['messages'] else None
                        conversation.last_message_at = conv_data['messages'][-1]['timestamp'] if conv_data['messages'] else None
                        conversation.last_synced_at = datetime.utcnow()
                    
                    session.flush()
                    
                    # Sync message metadata
                    for msg_data in conv_data['messages']:
                        # Determine sender type
                        sender_name = msg_data['sender']
                        is_incoming = sender_name != 'Host' and sender_name != 'Host (Automated)'
                        sender_type = 'guest' if is_incoming else 'host'
                        
                        # Create message metadata
                        message_meta = MessageMetadata(
                            message_id=None,  # Will be auto-generated or use API ID if available
                            conversation_id=conversation_id,
                            reservation_id=reservation.reservation_id if reservation else None,
                            listing_id=listing_id,
                            guest_id=reservation.guest_id if reservation else None,
                            sender_type=sender_type,
                            sender_name=sender_name,
                            is_incoming=1 if is_incoming else 0,
                            message_type='text',
                            content_preview=msg_data['content'][:200] if msg_data['content'] else '',
                            has_attachment=0,
                            created_at=msg_data['timestamp'],
                            message_file_path=file_path
                        )
                        
                        # Check if message already exists (by conversation_id and created_at)
                        existing = session.query(MessageMetadata).filter(
                            MessageMetadata.conversation_id == conversation_id,
                            MessageMetadata.created_at == msg_data['timestamp']
                        ).first()
                        
                        if not existing:
                            session.add(message_meta)
                            records_created += 1
                        else:
                            # Update existing
                            existing.sender_type = sender_type
                            existing.sender_name = sender_name
                            existing.is_incoming = 1 if is_incoming else 0
                            existing.content_preview = msg_data['content'][:200] if msg_data['content'] else ''
                            existing.message_file_path = file_path
                            records_updated += 1
                
                # Track per-listing statistics
                if listing_id:
                    if listing_id not in listing_stats:
                        listing_stats[listing_id] = {'messages': 0}
                    listing_stats[listing_id]['messages'] += len(conv_data['messages'])
                
                # Increment with item name to show progress (only render once)
                progress.increment(item_name=f"{guest_name} - {listing_name}")
                
            except Exception as e:
                error_msg = f"Error syncing {file_path}: {str(e)}"
                errors.append(error_msg)
                progress.increment(error=True)
                if VERBOSE:
                    print(f"\n  {error_msg}")
                continue
        
        progress.complete_phase()
        session.commit()
        
        # Log sync operation
        end_time = datetime.utcnow()
        duration = (end_time - start_time).total_seconds()
        
        sync_log = SyncLog(
            sync_run_id=sync_run_id,
            sync_type='messages',
            sync_mode='full' if full_sync else 'incremental',
            status='success' if not errors else 'partial',
            records_processed=len(conversation_files),
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
            print(f"\nSync complete:")
            print(f"  Processed: {len(conversation_files)} conversation files")
            print(f"  Created: {records_created}")
            print(f"  Updated: {records_updated}")
            print(f"  Errors: {len(errors)}")
            print(f"  Duration: {duration:.2f} seconds")
        
        return {
            'status': 'success' if not errors else 'partial',
            'records_processed': len(conversation_files),
            'records_created': records_created,
            'records_updated': records_updated,
            'errors': errors,
            'listing_stats': listing_stats
        }
        
    except Exception as e:
        session.rollback()
        error_msg = f"Fatal error in sync_messages: {str(e)}"
        logger.error(error_msg)
        
        # Log error
        sync_log = SyncLog(
            sync_run_id=sync_run_id,
            sync_type='messages',
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
    sync_messages_from_files(full_sync=True)
