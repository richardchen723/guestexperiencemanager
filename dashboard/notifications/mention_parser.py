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
    mention_pattern = r'@(\w+(?:\s+\w+){0,2})'
    mentions = re.findall(mention_pattern, comment_text)
    # Clean up mentions - remove extra spaces
    mentions = [m.strip() for m in mentions if m.strip()]
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
        # Try to match against user names (case-insensitive, partial match)
        for user in users:
            if user.user_id in seen_user_ids:
                continue
            
            matched = False
            
            # Match by name (case-insensitive, partial match)
            if user.name:
                user_name_lower = user.name.lower()
                mention_lower = mention_text.lower().strip()
                # Split name into words for better matching
                name_words = user_name_lower.split()
                mention_words = mention_lower.split()
                
                # Check if mention matches full name exactly
                if user_name_lower == mention_lower:
                    matched_users.append((user.user_id, mention_text))
                    seen_user_ids.add(user.user_id)
                    matched = True
                    break
                elif len(mention_words) == 1:
                    # Single word mention - check if it matches first name or is contained in full name
                    if (len(name_words) > 0 and name_words[0].startswith(mention_words[0])) or mention_words[0] in user_name_lower:
                        matched_users.append((user.user_id, mention_text))
                        seen_user_ids.add(user.user_id)
                        matched = True
                        break
                elif len(mention_words) >= 2 and len(name_words) >= 2:
                    # Multi-word mention - check if first name and last name match
                    if (name_words[0].startswith(mention_words[0]) and 
                        name_words[-1].startswith(mention_words[-1])):
                        matched_users.append((user.user_id, mention_text))
                        seen_user_ids.add(user.user_id)
                        matched = True
                        break
            
            if matched:
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

