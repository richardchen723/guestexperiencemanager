#!/usr/bin/env python3
"""
Parser for extracting @mentions from comment text.
"""

import sys
import os
import re
from typing import List, Tuple

project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)

from dashboard.auth.models import get_all_users
from dashboard.config import DEBUG_LOG_PATH


def parse_mentions(comment_text: str) -> List[Tuple[int, str]]:
    """
    Parse @mentions from comment text and match against users.
    
    Supports:
    - @username (matches against user.name, case-insensitive, partial match)
    - @email (matches against user.email, exact match after @)
    
    Args:
        comment_text: Text containing mentions
    
    Returns:
        List of (user_id, mention_text) tuples for matched users
    """
    # #region agent log
    try:
        with open(DEBUG_LOG_PATH, 'a') as f: f.write(f'{{"sessionId":"debug-session","runId":"run1","hypothesisId":"E","location":"mention_parser.py:17","message":"parse_mentions called","data":{{"comment_text":"{comment_text[:100] if comment_text else ""}"}},"timestamp":{int(__import__("time").time()*1000)}}}\n')
    except: pass
    # #endregion
    if not comment_text:
        return []
    
    # Find all @mentions in the text
    # Pattern: @ followed by word characters, optionally followed by space and more word characters (for full names)
    # This matches: @username, @FirstName LastName
    # We'll match up to 3 words (first name, middle name, last name)
    # Stop at word boundary, punctuation, or end of string to avoid capturing trailing text
    mention_pattern = r'@(\w+(?:\s+\w+){0,2})(?=\s|$|[^\w\s@]|$)'
    matches = re.finditer(mention_pattern, comment_text)
    mentions = [match.group(1).strip() for match in matches]
    # Clean up mentions - remove extra spaces and empty strings
    mentions = [m for m in mentions if m.strip()]
    # #region agent log
    try:
        with open(DEBUG_LOG_PATH, 'a') as f: f.write(f'{{"sessionId":"debug-session","runId":"run1","hypothesisId":"E","location":"mention_parser.py:37","message":"Mentions found by regex","data":{{"mentions":{mentions}}},"timestamp":{int(__import__("time").time()*1000)}}}\n')
    except: pass
    # #endregion
    
    if not mentions:
        return []
    
    # Get all users for matching
    users = get_all_users()
    # #region agent log
    try:
        with open(DEBUG_LOG_PATH, 'a') as f: f.write(f'{{"sessionId":"debug-session","runId":"run1","hypothesisId":"E","location":"mention_parser.py:43","message":"Users loaded","data":{{"user_count":{len(users)},"user_names":[u.name or u.email for u in users]}},"timestamp":{int(__import__("time").time()*1000)}}}\n')
    except: pass
    # #endregion
    
    matched_users = []
    seen_user_ids = set()  # Avoid duplicate notifications
    
    for mention_text in mentions:
        # Try progressively shorter versions of the mention (in case regex captured too much)
        # e.g., "Richard Chen testing" -> try "Richard Chen testing", then "Richard Chen", then "Richard"
        mention_words = mention_text.strip().split()
        mention_variants = []
        for i in range(len(mention_words), 0, -1):
            variant = ' '.join(mention_words[:i])
            if variant:
                mention_variants.append(variant)
        
        # Try to match against user names (case-insensitive, partial match)
        matched_user = None
        matched_variant = None
        
        for variant in mention_variants:
            for user in users:
                if user.user_id in seen_user_ids:
                    continue
                
                matched = False
                
                # Match by name (case-insensitive, partial match)
                if user.name:
                    user_name_lower = user.name.lower()
                    variant_lower = variant.lower().strip()
                    # Split name into words for better matching
                    name_words = user_name_lower.split()
                    variant_words = variant_lower.split()
                    
                    # Check if mention matches full name exactly
                    if user_name_lower == variant_lower:
                        matched_user = user
                        matched_variant = variant
                        matched = True
                        break
                    elif len(variant_words) == 1:
                        # Single word mention - check if it matches first name or is contained in full name
                        if (len(name_words) > 0 and name_words[0].startswith(variant_words[0])) or variant_words[0] in user_name_lower:
                            matched_user = user
                            matched_variant = variant
                            matched = True
                            break
                    elif len(variant_words) >= 2 and len(name_words) >= 2:
                        # Multi-word mention - check if first name and last name match
                        if (name_words[0].startswith(variant_words[0]) and 
                            name_words[-1].startswith(variant_words[-1])):
                            matched_user = user
                            matched_variant = variant
                            matched = True
                            break
                
                if matched:
                    break
            
            if matched_user:
                break
        
        # If we found a match, add it
        if matched_user:
            matched_users.append((matched_user.user_id, matched_variant))
            seen_user_ids.add(matched_user.user_id)
            continue
        
        # If no name match, try email matching with the original mention text
        for user in users:
            if user.user_id in seen_user_ids:
                continue
            
            # Match by email (exact match)
            if user.email:
                # Remove @ from mention_text if present for email matching
                email_part = mention_text[1:] if mention_text.startswith('@') else mention_text
                # Also try matching the part after @ in email
                email_local = user.email.split('@')[0].lower() if '@' in user.email else ''
                if (user.email.lower() == email_part.lower() or 
                    email_local == email_part.lower()):
                    matched_users.append((user.user_id, mention_text))
                    seen_user_ids.add(user.user_id)
                    # #region agent log
                    try:
                        with open(DEBUG_LOG_PATH, 'a') as f: f.write(f'{{"sessionId":"debug-session","runId":"run1","hypothesisId":"E","location":"mention_parser.py:83","message":"User matched by email","data":{{"user_id":{user.user_id},"user_email":"{user.email}","mention_text":"{mention_text}"}},"timestamp":{int(__import__("time").time()*1000)}}}\n')
                    except: pass
                    # #endregion
                    break
    
    # #region agent log
    try:
        with open(DEBUG_LOG_PATH, 'a') as f: f.write(f'{{"sessionId":"debug-session","runId":"run1","hypothesisId":"E","location":"mention_parser.py:85","message":"parse_mentions returning","data":{{"matched_users":{matched_users}}},"timestamp":{int(__import__("time").time()*1000)}}}\n')
    except: pass
    # #endregion
    return matched_users

