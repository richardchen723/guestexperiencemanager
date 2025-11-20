#!/usr/bin/env python3
"""
AI-powered ticket suggestions from issues.
"""

import sys
import os
import json
import re
from typing import Dict, Optional
from datetime import datetime, timedelta

# Add parent directories to path
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)

try:
    from openai import OpenAI
except ImportError:
    raise ImportError("openai library not installed. Run: pip3 install openai>=1.12.0")

import dashboard.config as config
from database.models import get_session as get_main_session, Listing


def generate_ticket_suggestions(listing_id: int, issue_title: str, issue_details: str = '') -> Dict:
    """
    Generate AI suggestions for creating a ticket from an issue.
    
    Args:
        listing_id: The listing ID this ticket is for
        issue_title: The title of the issue
        issue_details: Additional details about the issue
        
    Returns:
        Dictionary with suggested title, description, priority, and due_date
    """
    # Get listing information for context
    session = get_main_session(config.MAIN_DATABASE_PATH)
    try:
        listing = session.query(Listing).filter(Listing.listing_id == listing_id).first()
        if not listing:
            raise ValueError(f"Listing {listing_id} not found")
        
        listing_name = listing.name or f"Listing {listing_id}"
        listing_address = f"{listing.address or ''}, {listing.city or ''}".strip(', ')
    finally:
        session.close()
    
    # Initialize OpenAI client
    client_kwargs = {'api_key': config.OPENAI_API_KEY}
    client = OpenAI(**client_kwargs)
    
    # Build prompt for ticket suggestions
    prompt = f"""You are a property management operations expert. Based on the following issue for a property listing, suggest a specific, atomic action item (ticket) that can be created to address this issue.

Listing: {listing_name}
Address: {listing_address}

Issue:
Title: {issue_title}
Details: {issue_details if issue_details else 'No additional details provided.'}

Create a ticket suggestion that is:
1. Specific and atomic (one clear action, not multiple actions)
2. Actionable (someone can actually do this task)
3. Directed to only this one issue
4. Clear about what needs to be done

IMPORTANT: The ticket MUST be atomic - a single, specific task. Do NOT create tickets with multiple steps or actions.
Examples of atomic tasks:
- GOOD: "Replace broken smoke detector in master bedroom"
- GOOD: "Deep clean kitchen appliances"
- GOOD: "Send follow-up email to guest about check-in instructions"
- BAD: "Fix plumbing and clean bathroom" (two separate tasks)
- BAD: "Replace lightbulbs and update welcome guide" (two separate tasks)

IMPORTANT: You must respond with ONLY a valid JSON object. Do not include any text before or after the JSON.

Return your response as a JSON object with the following exact structure:
{{
    "title": "Specific, concise ticket title (e.g., 'Replace broken smoke detector in master bedroom')",
    "description": "Detailed description of the action item, including specific steps or requirements. Be very specific about what needs to be done, where, and any relevant details.",
    "priority": "Low" or "Medium" or "High" or "Critical" (based on urgency and impact),
    "category": "cleaning" or "maintenance" or "online" or "other" (classify the task type),
    "suggested_due_date_days": <number> (suggested number of days from now for due date, or null if no urgency)
}}

Guidelines:
- The ticket should be a single, specific task that addresses the issue (ATOMIC - one action only)
- Category classification:
  * "cleaning" - tasks related to cleaning, sanitization, or tidying
  * "maintenance" - tasks related to repairs, replacements, or property upkeep
  * "online" - tasks that can be resolved by the online team (e.g., listing updates, communication improvements, documentation updates)
  * "other" - tasks that don't fit the above categories
- Priority should reflect urgency: Critical (safety/legal), High (guest experience), Medium (operational), Low (nice-to-have)
- suggested_due_date_days should be reasonable (e.g., 1-3 for critical, 7-14 for high, 30 for medium, null for low)
- Be specific about location, item, or context when relevant
- Return ONLY the JSON object, nothing else
"""

    try:
        # Try with JSON mode first (for supported models)
        try:
            response = client.chat.completions.create(
                model=config.OPENAI_MODEL,
                messages=[
                    {"role": "system", "content": "You are an expert property management operations analyst. Create specific, actionable tickets from issues. Always respond with valid JSON only."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.7,
                response_format={"type": "json_object"}
            )
        except Exception as json_mode_error:
            # Fallback: try without JSON mode if the model doesn't support it
            response = client.chat.completions.create(
                model=config.OPENAI_MODEL,
                messages=[
                    {"role": "system", "content": "You are an expert property management operations analyst. Create specific, actionable tickets from issues. Always respond with valid JSON only."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.7
            )
        
        result = response.choices[0].message.content.strip()
        
        # Try to extract JSON if it's wrapped in markdown code blocks
        # Remove markdown code blocks if present
        json_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', result, re.DOTALL)
        if json_match:
            result = json_match.group(1)
        
        # Try to find JSON object in the response
        json_match = re.search(r'\{.*\}', result, re.DOTALL)
        if json_match:
            result = json_match.group(0)
        
        suggestions = json.loads(result)
        
        # Validate and normalize the response
        if not isinstance(suggestions, dict):
            raise ValueError("Invalid response format from AI")
        
        # Ensure all required fields exist
        result_dict = {
            'title': suggestions.get('title', issue_title),
            'description': suggestions.get('description', ''),
            'priority': suggestions.get('priority', 'Medium'),
            'category': suggestions.get('category', 'other'),
            'suggested_due_date_days': suggestions.get('suggested_due_date_days')
        }
        
        # Validate priority
        valid_priorities = ['Low', 'Medium', 'High', 'Critical']
        if result_dict['priority'] not in valid_priorities:
            result_dict['priority'] = 'Medium'
        
        # Validate category
        valid_categories = ['cleaning', 'maintenance', 'online', 'other']
        if result_dict['category'] not in valid_categories:
            result_dict['category'] = 'other'
        
        # Calculate actual due date if suggested_due_date_days is provided
        if result_dict['suggested_due_date_days'] is not None:
            try:
                days = int(result_dict['suggested_due_date_days'])
                if days > 0:
                    due_date = (datetime.now() + timedelta(days=days)).date()
                    result_dict['suggested_due_date'] = due_date.isoformat()
            except (ValueError, TypeError):
                pass
        
        return result_dict
        
    except json.JSONDecodeError as e:
        # JSON parsing failed - return basic suggestion
        import traceback
        traceback.print_exc()
        return {
            'title': f"Address: {issue_title}",
            'description': issue_details or f"Action item to address: {issue_title}",
            'priority': 'Medium',
            'category': 'other',
            'suggested_due_date_days': None
        }
    except Exception as e:
        # Return a basic suggestion if AI fails
        import traceback
        traceback.print_exc()
        return {
            'title': f"Address: {issue_title}",
            'description': issue_details or f"Action item to address: {issue_title}",
            'priority': 'Medium',
            'category': 'other',
            'suggested_due_date_days': None
        }
