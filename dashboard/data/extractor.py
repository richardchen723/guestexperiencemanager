#!/usr/bin/env python3
"""
Extract recent reviews and messages from database for AI analysis.
"""

import sys
import os
from datetime import datetime, timedelta
from typing import List, Dict, Set, Optional
from pathlib import Path

# Add parent directories to path
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)

from database.models import get_session, Review, MessageMetadata
import logging
import dashboard.config as config

logger = logging.getLogger(__name__)


def get_recent_reviews(listing_id: int, months: int = 6) -> List[Dict]:
    """
    Get recent reviews for a listing from the database.
    
    Uses review_date if available, otherwise falls back to inserted_on or last_synced_at.
    
    Args:
        listing_id: The listing ID
        months: Number of months to look back (default: 6)
        
    Returns:
        List of review dictionaries with review_id, review_text, overall_rating, review_date
    """
    cutoff_datetime = datetime.utcnow() - timedelta(days=months * 30)
    cutoff_date = cutoff_datetime.date()
    
    session = get_session(config.MAIN_DATABASE_PATH)
    try:
        # Filter by review_date if available, otherwise use inserted_on (when review was created in Hostaway)
        # Do NOT use last_synced_at as it gets updated on every sync and would include all reviews
        
        # First try: reviews with actual review_date in the time window
        reviews = session.query(Review).filter(
            Review.listing_id == listing_id,
            Review.review_date.isnot(None),
            Review.review_date >= cutoff_date
        ).order_by(Review.review_date.desc()).all()
        
        # If no reviews with review_date, use inserted_on as proxy for when review was created
        # inserted_on represents when the review was added to Hostaway, which is the best proxy we have
        if not reviews:
            reviews = session.query(Review).filter(
                Review.listing_id == listing_id,
                Review.inserted_on >= cutoff_datetime  # inserted_on is when review was created in Hostaway
            ).order_by(Review.inserted_on.desc()).all()
        
        # If still no reviews, fall back to most recent reviews (limit to reasonable number)
        # This handles cases where inserted_on might be NULL
        if not reviews:
            reviews = session.query(Review).filter(
                Review.listing_id == listing_id
            ).order_by(
                Review.inserted_on.desc().nullslast(),
                Review.last_synced_at.desc().nullslast()
            ).limit(50).all()  # Limit to most recent 50 if no date filter works
        
        # Safety limit: cap at 50 most recent reviews to avoid sending too much data to AI
        # This ensures we only analyze the most recent reviews even if date filtering isn't perfect
        MAX_REVIEWS = 50
        if len(reviews) > MAX_REVIEWS:
            logger.info(
                f"Found {len(reviews)} reviews for listing {listing_id} in last {months} months. "
                f"Limiting to most recent {MAX_REVIEWS} reviews for AI analysis."
            )
            # Re-query with limit to get the most recent ones
            if reviews and reviews[0].inserted_on:  # If we have inserted_on, use it for ordering
                reviews = session.query(Review).filter(
                    Review.listing_id == listing_id,
                    Review.inserted_on.isnot(None)
                ).order_by(Review.inserted_on.desc()).limit(MAX_REVIEWS).all()
            else:  # Fallback: just take the first MAX_REVIEWS from the list
                reviews = reviews[:MAX_REVIEWS]
        
        # Don't filter out reviews without text - AI can analyze based on ratings and other metadata
        # But prioritize reviews with text if available
        
        result = []
        for review in reviews:
            # Defensive check: ensure review belongs to the requested listing
            if review.listing_id != listing_id:
                logger.warning(
                    f"Review {review.review_id} has listing_id {review.listing_id}, "
                    f"but was requested for listing {listing_id}. Skipping."
                )
                continue
            
            result.append({
                'review_id': review.review_id,
                'listing_id': review.listing_id,  # Include for verification
                'review_text': review.review_text,
                'overall_rating': review.overall_rating,
                'review_date': review.review_date,
                'reviewer_name': review.reviewer_name,
                'channel_name': review.channel_name,
                'sub_ratings': [
                    {'category': sr.rating_category, 'value': sr.rating_value}
                    for sr in review.sub_ratings
                ]
            })
        
        return result
    finally:
        session.close()


def get_recent_messages(listing_id: int, months: int = 2) -> List[Dict]:
    """
    Get recent messages for a listing from the database.
    
    Args:
        listing_id: The listing ID
        months: Number of months to look back (default: 2)
        
    Returns:
        List of message dictionaries with message_id, content, created_at, file_path
    """
    cutoff_date = datetime.utcnow() - timedelta(days=months * 30)
    
    session = get_session(config.MAIN_DATABASE_PATH)
    try:
        messages = session.query(MessageMetadata).filter(
            MessageMetadata.listing_id == listing_id,
            MessageMetadata.created_at >= cutoff_date
        ).order_by(MessageMetadata.created_at.desc()).all()
        
        result = []
        for msg in messages:
            # Defensive check: ensure message belongs to the requested listing
            if msg.listing_id != listing_id:
                # Only log at debug level to reduce noise
                logger.debug(
                    f"Message {msg.message_id} has listing_id {msg.listing_id}, "
                    f"but was requested for listing {listing_id}. Skipping."
                )
                continue
            
            # Use content_preview first (faster, already in database)
            # Only read full file if content_preview is missing or too short
            content = msg.content_preview or ""
            
            # Only read full file if:
            # 1. content_preview is missing/empty, OR
            # 2. content_preview is very short (< 50 chars) suggesting it might be truncated
            should_read_file = (
                msg.message_file_path and 
                (not content or len(content) < 50)
            )
            
            if should_read_file:
                try:
                    full_content = read_message_content(msg.message_file_path)
                    if full_content:
                        content = full_content
                except Exception as e:
                    # Log at debug level to reduce noise - file read errors are not critical
                    logger.debug(f"Could not read message file {msg.message_file_path}: {e}")
                    # Continue with content_preview if available
            
            result.append({
                'message_id': msg.message_id,
                'listing_id': msg.listing_id,  # Include for verification
                'content': content,
                'created_at': msg.created_at,
                'sender_name': msg.sender_name,
                'sender_type': msg.sender_type,
                'is_incoming': bool(msg.is_incoming),
                'file_path': msg.message_file_path
            })
        
        return result
    finally:
        session.close()


def read_message_content(file_path: str) -> Optional[str]:
    """
    Read message content from conversation file.
    Supports both S3 storage and local filesystem.
    
    Args:
        file_path: Path to the conversation file (local path or S3 key)
        
    Returns:
        Message content as string, or None if file not found
    """
    if not file_path:
        return None
    
    # Check if this is an S3 key
    # S3 keys from our storage will be like "conversations/Listing Name/Guest_2025-11-15_conversation.txt"
    is_s3_key = (
        file_path.startswith('conversations/') or 
        file_path.startswith('s3://') or
        (not os.path.isabs(file_path) and '/' in file_path and not os.path.exists(file_path))
    )
    
    # Also check if S3 is configured and file doesn't exist locally
    use_s3 = config.USE_S3_STORAGE
    if use_s3 and not is_s3_key:
        # Check if local file exists
        if os.path.isabs(file_path):
            full_path = Path(file_path)
        else:
            full_path = Path(config.CONVERSATIONS_DIR) / file_path
        
        if not (full_path.exists() and full_path.is_file()):
            # File doesn't exist locally, might be in S3
            is_s3_key = True
    
    if is_s3_key and use_s3:
        # Read from S3
        try:
            from utils.s3_storage import S3Storage
            s3_storage = S3Storage()
            # Remove s3:// prefix if present
            s3_key = file_path.replace('s3://', '').split('/', 1)[-1] if 's3://' in file_path else file_path
            content = s3_storage.read_file(s3_key)
            return content
        except Exception as e:
            # Log at debug level to reduce noise - file read errors are not critical for analysis
            logger.debug(f"Error reading message file from S3 {file_path}: {e}")
            return None
    
    # Read from local filesystem
    # Handle both absolute and relative paths
    if os.path.isabs(file_path):
        full_path = Path(file_path)
    else:
        full_path = Path(config.CONVERSATIONS_DIR) / file_path
    
    try:
        if full_path.exists() and full_path.is_file():
            with open(full_path, 'r', encoding='utf-8') as f:
                return f.read()
    except Exception as e:
        # Log at debug level to reduce noise - file read errors are not critical for analysis
        logger.debug(f"Error reading message file {file_path}: {e}")
    
    return None


def filter_unprocessed_reviews(reviews: List[Dict], processed_review_ids: Set[int]) -> List[Dict]:
    """
    Filter out reviews that have already been processed.
    
    Args:
        reviews: List of review dictionaries
        processed_review_ids: Set of review IDs that have been analyzed
        
    Returns:
        List of unprocessed reviews
    """
    return [r for r in reviews if r['review_id'] not in processed_review_ids]


def filter_unprocessed_messages(messages: List[Dict], processed_message_ids: Set[int]) -> List[Dict]:
    """
    Filter out messages that have already been processed.
    
    Args:
        messages: List of message dictionaries
        processed_message_ids: Set of message IDs that have been analyzed
        
    Returns:
        List of unprocessed messages
    """
    return [m for m in messages if m['message_id'] not in processed_message_ids]

