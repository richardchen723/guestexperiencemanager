#!/usr/bin/env python3
"""
Format review and message data for AI analysis.
"""

import sys
import os
from typing import List, Dict

# Add parent directories to path
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)

import dashboard.config as config


def format_reviews_for_ai(reviews: List[Dict]) -> str:
    """
    Format review data into prompt-friendly text.
    
    Args:
        reviews: List of review dictionaries
        
    Returns:
        Formatted string for AI prompt
    """
    if not reviews:
        return "No reviews available."
    
    lines = []
    for review in reviews:
        review_id = review.get('review_id', 'N/A')
        rating = review.get('overall_rating')
        date = review.get('review_date')
        reviewer = review.get('reviewer_name', 'Anonymous')
        channel = review.get('channel_name')
        text = review.get('review_text')
        
        # Format date
        date_str = date.strftime('%Y-%m-%d') if date else 'Date unknown'
        
        # Format rating
        rating_str = f"{rating}/5" if rating is not None else "Rating not provided"
        
        # Format channel
        channel_str = f" | Channel: {channel}" if channel else ""
        
        # Format sub-ratings if available
        sub_ratings = review.get('sub_ratings', [])
        sub_rating_str = ""
        if sub_ratings:
            sub_rating_str = " | Sub-ratings: " + ", ".join(
                f"{sr.get('category', 'Unknown')}: {sr.get('value', 'N/A')}" for sr in sub_ratings
            )
        
        # Format text
        text_str = text if text and text.strip() else "No written review text available"
        
        lines.append(
            f"Review ID {review_id} | Date: {date_str} | Rating: {rating_str} | "
            f"Reviewer: {reviewer}{channel_str}{sub_rating_str}\n"
            f"Text: {text_str}\n"
        )
    
    return "\n".join(lines)


def format_messages_for_ai(messages: List[Dict]) -> str:
    """
    Format message data into prompt-friendly text.
    
    Args:
        messages: List of message dictionaries
        
    Returns:
        Formatted string for AI prompt
    """
    if not messages:
        return "No messages available."
    
    lines = []
    for msg in messages:
        message_id = msg.get('message_id', 'N/A')
        date = msg.get('created_at', 'N/A')
        sender = msg.get('sender_name', 'Unknown')
        sender_type = msg.get('sender_type', 'Unknown')
        is_incoming = msg.get('is_incoming', False)
        direction = "Guest" if is_incoming else "Host"
        content = msg.get('content', 'No content')
        
        # Truncate very long messages
        if len(content) > 1000:
            content = content[:1000] + "... [truncated]"
        
        lines.append(
            f"Message ID {message_id} | Date: {date} | {direction}: {sender} ({sender_type})\n"
            f"Content: {content}\n"
        )
    
    return "\n".join(lines)


def format_data_for_ai(reviews: List[Dict], messages: List[Dict]) -> str:
    """
    Combine reviews and messages into a single context string for AI analysis.
    
    Args:
        reviews: List of review dictionaries
        messages: List of message dictionaries
        
    Returns:
        Combined formatted string
    """
    review_text = format_reviews_for_ai(reviews)
    message_text = format_messages_for_ai(messages)
    
    return f"""GUEST REVIEWS (Last {config.REVIEW_MONTHS} months):
{review_text}

GUEST MESSAGES (Last {config.MESSAGE_MONTHS} months):
{message_text}"""

