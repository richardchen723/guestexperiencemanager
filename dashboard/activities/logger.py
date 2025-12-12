#!/usr/bin/env python3
"""
Activity logging service for tracking user activities.
"""

import sys
import os
import logging
import threading
from datetime import datetime
from typing import Optional, Dict, Any

project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)

from dashboard.tickets.models import ActivityLog, get_session

logger = logging.getLogger(__name__)


def log_activity(user_id: int, activity_type: str, entity_type: str, 
                 entity_id: Optional[int] = None, action: str = None, 
                 metadata: Optional[Dict[str, Any]] = None):
    """
    Log an activity to the database asynchronously.
    
    Args:
        user_id: ID of the user performing the activity
        activity_type: Type of activity ('ticket', 'auth', 'comment')
        entity_type: Type of entity ('ticket', 'user', 'comment')
        entity_id: ID of the entity (ticket_id, user_id, etc.)
        action: Action performed ('create', 'update', 'delete', 'login', 'logout', 'status_change', 'assign', etc.)
        metadata: Additional context as dictionary (will be stored as JSON)
    """
    def _log():
        try:
            session = get_session()
            try:
                activity = ActivityLog(
                    user_id=user_id,
                    activity_type=activity_type,
                    entity_type=entity_type,
                    entity_id=entity_id,
                    action=action or 'unknown',
                    activity_metadata=metadata,  # Fixed: use activity_metadata not metadata
                    created_at=datetime.utcnow()
                )
                session.add(activity)
                session.commit()
            except Exception as e:
                session.rollback()
                logger.error(f"Error logging activity: {e}", exc_info=True)
            finally:
                session.close()
        except Exception as e:
            logger.error(f"Error in activity logging thread: {e}", exc_info=True)
    
    # Log asynchronously to avoid blocking main operations
    thread = threading.Thread(target=_log, daemon=True)
    thread.start()


def log_ticket_activity(user_id: int, action: str, ticket_id: int, 
                       metadata: Optional[Dict[str, Any]] = None):
    """
    Log a ticket-related activity.
    
    Args:
        user_id: ID of the user performing the activity
        action: Action performed ('create', 'update', 'delete', 'status_change', 'assign')
        ticket_id: ID of the ticket
        metadata: Additional context
    """
    log_activity(
        user_id=user_id,
        activity_type='ticket',
        entity_type='ticket',
        entity_id=ticket_id,
        action=action,
        metadata=metadata
    )


def log_auth_activity(user_id: int, action: str, 
                     metadata: Optional[Dict[str, Any]] = None):
    """
    Log an authentication-related activity.
    
    Args:
        user_id: ID of the user
        action: Action performed ('login', 'logout', 'profile_update')
        metadata: Additional context (e.g., ip_address, user_agent for login)
    """
    log_activity(
        user_id=user_id,
        activity_type='auth',
        entity_type='user',
        entity_id=user_id,
        action=action,
        metadata=metadata
    )


def log_comment_activity(user_id: int, action: str, ticket_id: int, 
                        comment_id: Optional[int] = None,
                        metadata: Optional[Dict[str, Any]] = None):
    """
    Log a comment-related activity.
    
    Args:
        user_id: ID of the user performing the activity
        action: Action performed ('create', 'delete')
        ticket_id: ID of the ticket the comment belongs to
        comment_id: ID of the comment (if applicable)
        metadata: Additional context
    """
    if metadata is None:
        metadata = {}
    if comment_id:
        metadata['comment_id'] = comment_id
    
    log_activity(
        user_id=user_id,
        activity_type='comment',
        entity_type='comment',
        entity_id=comment_id,
        action=action,
        metadata=metadata
    )


