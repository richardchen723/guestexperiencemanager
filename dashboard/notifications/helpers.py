#!/usr/bin/env python3
"""
Helper functions for sending WhatsApp notifications.
"""

import sys
import os
import logging

project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)

from dashboard.notifications.whatsapp_service import WhatsAppNotificationService
from dashboard.notifications.async_sender import send_notification_async
from dashboard.config import DEBUG_LOG_PATH

logger = logging.getLogger(__name__)

# Global service instance (singleton pattern)
_service_instance = None


def _get_service():
    """Get or create WhatsApp notification service instance."""
    global _service_instance
    if _service_instance is None:
        _service_instance = WhatsAppNotificationService()
    return _service_instance


def send_mention_notification(mentioned_user_id: int, ticket_id: int, comment_text: str, mentioner_name: str):
    """
    Send notification when a user is mentioned in a ticket comment.
    
    Args:
        mentioned_user_id: ID of the mentioned user
        ticket_id: ID of the ticket
        comment_text: Text of the comment (for preview)
        mentioner_name: Name of the user who mentioned them
    """
    # #region agent log
    try:
        with open(DEBUG_LOG_PATH, 'a') as f: f.write(f'{{"sessionId":"debug-session","runId":"run1","hypothesisId":"F","location":"helpers.py:30","message":"send_mention_notification called","data":{{"mentioned_user_id":{mentioned_user_id},"ticket_id":{ticket_id}}},"timestamp":{int(__import__("time").time()*1000)}}}\n')
    except: pass
    # #endregion
    try:
        service = _get_service()
        # #region agent log
        try:
            with open(DEBUG_LOG_PATH, 'a') as f: f.write(f'{{"sessionId":"debug-session","runId":"run1","hypothesisId":"F","location":"helpers.py:42","message":"Service obtained","data":{{"service_client_exists":{service.client is not None}}},"timestamp":{int(__import__("time").time()*1000)}}}\n')
        except: pass
        # #endregion
        context = {
            'mentioner_name': mentioner_name,
            'comment_preview': comment_text
        }
        send_notification_async(service, mentioned_user_id, 'mention', ticket_id, context)
        # #region agent log
        try:
            with open(DEBUG_LOG_PATH, 'a') as f: f.write(f'{{"sessionId":"debug-session","runId":"run1","hypothesisId":"F","location":"helpers.py:47","message":"Async notification sent","data":{{"mentioned_user_id":{mentioned_user_id}}},"timestamp":{int(__import__("time").time()*1000)}}}\n')
        except: pass
        # #endregion
    except Exception as e:
        # #region agent log
        try:
            with open(DEBUG_LOG_PATH, 'a') as f: f.write(f'{{"sessionId":"debug-session","runId":"run1","hypothesisId":"F","location":"helpers.py:49","message":"Exception in send_mention_notification","data":{{"error":str(e)}},"timestamp":{int(__import__("time").time()*1000)}}}\n')
        except: pass
        # #endregion
        logger.error(f"Error sending mention notification: {e}", exc_info=True)


def send_assignment_notification(user_id: int, ticket_id: int):
    """
    Send notification when a ticket is assigned to a user.
    
    Args:
        user_id: ID of the assigned user
        ticket_id: ID of the ticket
    """
    try:
        service = _get_service()
        context = {}
        send_notification_async(service, user_id, 'assignment', ticket_id, context)
    except Exception as e:
        logger.error(f"Error sending assignment notification: {e}", exc_info=True)


def send_status_change_notification(user_id: int, ticket_id: int, old_status: str, new_status: str, changer_name: str):
    """
    Send notification when a ticket's status changes.
    
    Args:
        user_id: ID of the assigned user
        ticket_id: ID of the ticket
        old_status: Previous status
        new_status: New status
        changer_name: Name of the user who changed the status
    """
    try:
        service = _get_service()
        context = {
            'old_status': old_status,
            'new_status': new_status,
            'changer_name': changer_name
        }
        send_notification_async(service, user_id, 'status_change', ticket_id, context)
    except Exception as e:
        logger.error(f"Error sending status change notification: {e}", exc_info=True)

