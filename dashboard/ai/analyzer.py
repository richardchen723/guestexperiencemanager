#!/usr/bin/env python3
"""
OpenAI API integration for analyzing listing data.
"""

import json
import sys
import os
from datetime import datetime
from typing import List, Dict, Optional

# Add parent directories to path
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)

try:
    from openai import OpenAI
except ImportError:
    raise ImportError("openai library not installed. Run: pip3 install openai>=1.12.0")

import dashboard.config as config
from dashboard.data.extractor import get_recent_reviews, get_recent_messages, filter_unprocessed_reviews, filter_unprocessed_messages
from dashboard.data.formatter import format_data_for_ai
from dashboard.ai.cache import (
    get_processed_reviews, get_processed_messages,
    mark_reviews_processed, mark_messages_processed,
    get_cached_insights, update_listing_insights, clear_listing_cache
)


def analyze_new_data(reviews_data: List[Dict], messages_data: List[Dict], listing_id: int) -> Dict:
    """
    Call OpenAI API to analyze new reviews and messages.
    
    Args:
        reviews_data: List of new review dictionaries
        messages_data: List of new message dictionaries
        listing_id: The listing ID these reviews/messages belong to (for validation)
        
    Returns:
        Dictionary with quality_rating, issues, action_items
    """
    if not reviews_data and not messages_data:
        return {
            'quality_rating': None,
            'issues': [],
            'action_items': []
        }
    
    # Validate that all reviews and messages belong to the correct listing
    wrong_reviews = [r for r in reviews_data if r.get('listing_id') != listing_id]
    wrong_messages = [m for m in messages_data if m.get('listing_id') != listing_id]
    
    if wrong_reviews or wrong_messages:
        import logging
        logger = logging.getLogger(__name__)
        if wrong_reviews:
            logger.error(
                f"Found {len(wrong_reviews)} reviews that don't belong to listing {listing_id}. "
                f"Review IDs: {[r['review_id'] for r in wrong_reviews[:5]]}"
            )
        if wrong_messages:
            logger.error(
                f"Found {len(wrong_messages)} messages that don't belong to listing {listing_id}. "
                f"Message IDs: {[m['message_id'] for m in wrong_messages[:5]]}"
            )
        # Filter out wrong items
        reviews_data = [r for r in reviews_data if r.get('listing_id') == listing_id]
        messages_data = [m for m in messages_data if m.get('listing_id') == listing_id]
    
    # Initialize OpenAI client
    # Use only api_key parameter to avoid any conflicts with proxies or other kwargs
    client_kwargs = {'api_key': config.OPENAI_API_KEY}
    client = OpenAI(**client_kwargs)
    
    formatted_data = format_data_for_ai(reviews_data, messages_data)
    
    prompt = f"""Analyze the following NEW guest reviews and messages for this listing (these are additions since last analysis).

{formatted_data}

Provide analysis of these new items. Return your response as a JSON object with the following structure:
{{
    "quality_rating": "Good" or "Fair" or "Poor" (based on these new items only),
    "issues": [
        {{
            "title": "Brief issue title (e.g., 'Cleaning Concerns')",
            "details": "Detailed explanation of the issue, including specific complaints or concerns mentioned by guests. Reference specific examples from reviews/messages when possible."
        }},
        ...
    ],
    "action_items": ["action1", "action2", ...] (specific, actionable recommendations based on new data)
}}

Requirements for issues:
- Identify 3-5 or more key issues (include all significant problems, not just top 3)
- Each issue must have a clear, concise title
- Each issue must have detailed explanation with specific examples from guest feedback
- Reference specific complaints, concerns, or problems mentioned in reviews/messages
- Be thorough but focused on actionable problems

Focus on identifying problems, concerns, or areas for improvement mentioned in the reviews and messages."""

    try:
        response = client.chat.completions.create(
            model=config.OPENAI_MODEL,
            messages=[
                {"role": "system", "content": "You are an expert hospitality analyst. Analyze guest feedback to identify issues and provide actionable recommendations."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.3,
            response_format={"type": "json_object"}
        )
        
        content = response.choices[0].message.content
        result = json.loads(content)
        
        # Ensure required fields exist and normalize issues format
        issues = result.get('issues', [])
        # Normalize issues to ensure they have title and details
        normalized_issues = []
        for issue in issues:
            if isinstance(issue, dict) and 'title' in issue:
                normalized_issues.append({
                    'title': issue.get('title', 'Issue'),
                    'details': issue.get('details', issue.get('title', 'No details provided'))
                })
            elif isinstance(issue, str):
                normalized_issues.append({
                    'title': issue,
                    'details': issue
                })
            else:
                normalized_issues.append({
                    'title': str(issue),
                    'details': str(issue)
                })
        
        return {
            'quality_rating': result.get('quality_rating', 'Fair'),
            'issues': normalized_issues,
            'action_items': result.get('action_items', [])
        }
        
    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"Error calling OpenAI API: {e}")
        return {
            'quality_rating': 'Fair',
            'issues': [{'title': 'Analysis Error', 'details': 'Error analyzing data. Please try again later.'}],
            'action_items': ['Please try again later']
        }


def merge_insights(old_insights: Optional[Dict], new_insights: Dict) -> Dict:
    """
    Merge new insights with existing cached insights.
    
    Handles both old format (issues as strings) and new format (issues as objects with title/details).
    
    Args:
        old_insights: Previously cached insights (can be None)
        new_insights: New insights from AI analysis
        
    Returns:
        Merged insights dictionary
    """
    if not old_insights:
        return new_insights
    
    # Normalize issues to new format (objects with title/details)
    def normalize_issue(issue):
        if isinstance(issue, dict) and 'title' in issue:
            return issue
        elif isinstance(issue, str):
            return {'title': issue, 'details': issue}
        else:
            return {'title': str(issue), 'details': str(issue)}
    
    old_issues = [normalize_issue(i) for i in old_insights.get('issues', [])]
    new_issues = [normalize_issue(i) for i in new_insights.get('issues', [])]
    
    # Merge issues by title (deduplicate, prefer new details if title matches)
    issues_by_title = {}
    for issue in old_issues:
        issues_by_title[issue['title']] = issue
    
    # Update with new issues (new details override old if title matches)
    for issue in new_issues:
        issues_by_title[issue['title']] = issue
    
    merged_issues = list(issues_by_title.values())
    
    # Merge action items (deduplicate, keep all unique items)
    old_actions = set(old_insights.get('action_items', []))
    new_actions = set(new_insights.get('action_items', []))
    merged_actions = list(old_actions.union(new_actions))
    
    # For quality rating, if new data suggests worse rating, use that
    # Otherwise keep the existing rating (conservative approach)
    quality_map = {'Poor': 1, 'Fair': 2, 'Good': 3}
    old_rating = old_insights.get('quality_rating', 'Fair')
    new_rating = new_insights.get('quality_rating', 'Fair')
    
    if quality_map.get(new_rating, 2) < quality_map.get(old_rating, 2):
        # New rating is worse, use it
        merged_rating = new_rating
    else:
        # Keep existing rating or use new if it's better
        merged_rating = new_rating if new_rating != 'Fair' else old_rating
    
    return {
        'quality_rating': merged_rating,
        'issues': merged_issues,
        'action_items': merged_actions
    }


def get_insights(listing_id: int, force_refresh: bool = False) -> Dict:
    """
    Get insights for a listing, using cached data when possible.
    
    Detects when data has changed (new items added OR old items removed) and
    re-analyzes when necessary. This ensures cached results are always based on
    the current dataset, not stale data.
    
    Args:
        listing_id: The listing ID
        force_refresh: If True, clear cache and re-analyze all current data
        
    Returns:
        Dictionary with insights including quality_rating, issues, action_items,
        total_reviews_analyzed, total_messages_analyzed, last_updated
    """
    # Get current data from database (always fresh)
    all_reviews = get_recent_reviews(listing_id, months=config.REVIEW_MONTHS)
    all_messages = get_recent_messages(listing_id, months=config.MESSAGE_MONTHS)
    
    current_review_ids = {r['review_id'] for r in all_reviews}
    current_message_ids = {m['message_id'] for m in all_messages}
    
    # Get processed IDs from cache
    processed_reviews = get_processed_reviews(listing_id)
    processed_messages = get_processed_messages(listing_id)
    
    # Check if data has changed (new items OR removed items)
    reviews_changed = (
        current_review_ids != processed_reviews or
        len(current_review_ids) != len(processed_reviews)
    )
    messages_changed = (
        current_message_ids != processed_messages or
        len(current_message_ids) != len(processed_messages)
    )
    
    data_changed = reviews_changed or messages_changed
    
    # Get cached insights
    cached_insights = get_cached_insights(listing_id)
    
    # If force refresh, clear cache and re-analyze all current data
    if force_refresh:
        clear_listing_cache(listing_id)
        processed_reviews = set()
        processed_messages = set()
        cached_insights = None
        data_changed = True
    
    # If data hasn't changed and we have cached insights, return cached
    if not data_changed and cached_insights:
        # Update counts to reflect current data
        cached_insights['total_reviews_analyzed'] = len(all_reviews)
        cached_insights['total_messages_analyzed'] = len(all_messages)
        return cached_insights
    
    # Data has changed - need to re-analyze
    # If we have cached insights but data changed, we need to analyze all current data
    # (not just new items, because removed items mean the context changed)
    if data_changed and cached_insights:
        # Clear cache to start fresh with current dataset
        clear_listing_cache(listing_id)
        processed_reviews = set()
        processed_messages = set()
        cached_insights = None
    
    # Filter to unprocessed data (which will be all data if cache was cleared)
    new_reviews = filter_unprocessed_reviews(all_reviews, processed_reviews)
    new_messages = filter_unprocessed_messages(all_messages, processed_messages)
    
    # Analyze new data if any exists
    new_insights = None
    if new_reviews or new_messages:
        new_insights = analyze_new_data(new_reviews, new_messages, listing_id)
        
        # Mark all current items as processed (not just new ones)
        # This ensures we track the current dataset state
        if new_reviews:
            mark_reviews_processed(listing_id, list(current_review_ids))
        if new_messages:
            mark_messages_processed(listing_id, list(current_message_ids))
    
    # If we have new insights, use them (they're based on current dataset)
    # Otherwise use cached insights or create empty structure
    if new_insights:
        final_insights = new_insights
    elif cached_insights:
        final_insights = cached_insights
    else:
        final_insights = {
            'quality_rating': None,
            'issues': [],
            'action_items': []
        }
    
    # Update metadata with current counts
    final_insights['last_analyzed'] = datetime.utcnow().isoformat()
    final_insights['total_reviews_analyzed'] = len(all_reviews)
    final_insights['total_messages_analyzed'] = len(all_messages)
    
    # Update cache with final insights
    update_listing_insights(
        listing_id, 
        final_insights, 
        len(all_reviews), 
        len(all_messages)
    )
    
    return final_insights

