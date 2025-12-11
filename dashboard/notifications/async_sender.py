#!/usr/bin/env python3
"""
Async notification sender using background threads.
"""

import threading
import logging

logger = logging.getLogger(__name__)


def send_notification_async(service, user_id: int, notification_type: str, ticket_id: int, context: dict):
    """
    Send notification asynchronously in a background thread.
    
    This prevents blocking ticket operations if Twilio API is slow.
    
    Args:
        service: WhatsAppNotificationService instance
        user_id: ID of the user to notify
        notification_type: Type of notification
        ticket_id: ID of the related ticket
        context: Additional context for the notification
    """
    # #region agent log
    with open('/Users/richardchen/projects/hostaway-messages/.cursor/debug.log', 'a') as f: f.write(f'{{"sessionId":"debug-session","runId":"run1","hypothesisId":"I","location":"async_sender.py:12","message":"send_notification_async called","data":{{"user_id":{user_id},"notification_type":"{notification_type}","ticket_id":{ticket_id}}},"timestamp":{int(__import__("time").time()*1000)}}}\n')
    # #endregion
    def _send():
        try:
            # #region agent log
            with open('/Users/richardchen/projects/hostaway-messages/.cursor/debug.log', 'a') as f: f.write(f'{{"sessionId":"debug-session","runId":"run1","hypothesisId":"I","location":"async_sender.py:27","message":"Async thread started executing","data":{{"user_id":{user_id},"ticket_id":{ticket_id}}},"timestamp":{int(__import__("time").time()*1000)}}}\n')
            # #endregion
            service.send_notification(user_id, notification_type, ticket_id, context)
            # #region agent log
            with open('/Users/richardchen/projects/hostaway-messages/.cursor/debug.log', 'a') as f: f.write(f'{{"sessionId":"debug-session","runId":"run1","hypothesisId":"I","location":"async_sender.py:30","message":"Async thread completed","data":{{"user_id":{user_id},"ticket_id":{ticket_id}}},"timestamp":{int(__import__("time").time()*1000)}}}\n')
            # #endregion
        except Exception as e:
            # #region agent log
            with open('/Users/richardchen/projects/hostaway-messages/.cursor/debug.log', 'a') as f: f.write(f'{{"sessionId":"debug-session","runId":"run1","hypothesisId":"I","location":"async_sender.py:32","message":"Exception in async thread","data":{{"error":str(e)}},"timestamp":{int(__import__("time").time()*1000)}}}\n')
            # #endregion
            logger.error(f"Error in async notification thread: {e}", exc_info=True)
    
    thread = threading.Thread(target=_send, daemon=True)
    thread.start()
    # #region agent log
    with open('/Users/richardchen/projects/hostaway-messages/.cursor/debug.log', 'a') as f: f.write(f'{{"sessionId":"debug-session","runId":"run1","hypothesisId":"I","location":"async_sender.py:36","message":"Async thread started","data":{{"user_id":{user_id},"ticket_id":{ticket_id},"thread_name":"{thread.name}"}},"timestamp":{int(__import__("time").time()*1000)}}}\n')
    # #endregion
    logger.debug(f"Started async notification thread for user {user_id}, ticket {ticket_id}")

