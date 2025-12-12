#!/usr/bin/env python3
"""
WhatsApp notification service using Twilio.
"""

import sys
import os
import logging
import re
from typing import Optional, Dict, Any

project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)

import dashboard.config as config
from dashboard.auth.models import get_user_by_id
from dashboard.config import DEBUG_LOG_PATH

logger = logging.getLogger(__name__)


class WhatsAppNotificationService:
    """Service for sending WhatsApp notifications via Twilio."""
    
    def __init__(self):
        """Initialize Twilio client with credentials from config."""
        self.account_sid = config.TWILIO_ACCOUNT_SID
        self.auth_token = config.TWILIO_AUTH_TOKEN
        self.whatsapp_from = config.TWILIO_WHATSAPP_FROM
        self.base_url = config.APP_BASE_URL
        
        # Initialize Twilio client if credentials are available
        self.client = None
        if self.account_sid and self.auth_token:
            try:
                from twilio.rest import Client
                self.client = Client(self.account_sid, self.auth_token)
                logger.info("Twilio client initialized successfully")
                
                # Check if using sandbox (common sandbox number pattern)
                if self.whatsapp_from and '14155238886' in self.whatsapp_from:
                    logger.warning("Using Twilio WhatsApp sandbox. Recipients must join the sandbox first by sending the join code to the sandbox number.")
            except ImportError:
                logger.warning("Twilio package not installed. WhatsApp notifications will be disabled.")
            except Exception as e:
                logger.warning(f"Error initializing Twilio client: {e}. WhatsApp notifications will be disabled.")
        else:
            logger.warning("Twilio credentials not configured. WhatsApp notifications will be disabled.")
    
    def send_notification(self, user_id: int, notification_type: str, ticket_id: int, context: Dict[str, Any]) -> bool:
        """
        Send a WhatsApp notification to a user.
        
        Args:
            user_id: ID of the user to notify
            notification_type: Type of notification ('mention', 'assignment', 'status_change')
            ticket_id: ID of the related ticket
            context: Additional context for the notification (e.g., mentioner_name, old_status, new_status)
        
        Returns:
            True if notification was sent successfully, False otherwise
        """
        # #region agent log
        try:
            with open(DEBUG_LOG_PATH, 'a') as f: f.write(f'{{"sessionId":"debug-session","runId":"run1","hypothesisId":"G","location":"whatsapp_service.py:45","message":"send_notification called","data":{{"user_id":{user_id},"notification_type":"{notification_type}","ticket_id":{ticket_id},"client_exists":{self.client is not None}}},"timestamp":{int(__import__("time").time()*1000)}}}\n')
        except: pass
        # #endregion
        if not self.client:
            # #region agent log
            try:
                with open(DEBUG_LOG_PATH, 'a') as f: f.write(f'{{"sessionId":"debug-session","runId":"run1","hypothesisId":"G","location":"whatsapp_service.py:58","message":"Twilio client not available","data":{{"account_sid_exists":{self.account_sid is not None},"auth_token_exists":{self.auth_token is not None}}},"timestamp":{int(__import__("time").time()*1000)}}}\n')
            except: pass
            # #endregion
            logger.debug(f"Twilio client not available, skipping notification for user {user_id}")
            return False
        
        try:
            # Get user
            user = get_user_by_id(user_id)
            if not user:
                # #region agent log
                try:
                    with open(DEBUG_LOG_PATH, 'a') as f: f.write(f'{{"sessionId":"debug-session","runId":"run1","hypothesisId":"G","location":"whatsapp_service.py:65","message":"User not found","data":{{"user_id":{user_id}}},"timestamp":{int(__import__("time").time()*1000)}}}\n')
                except: pass
                # #endregion
                logger.warning(f"User {user_id} not found for notification")
                return False
            
            # Check if user has WhatsApp number and notifications enabled
            # #region agent log
            try:
                with open(DEBUG_LOG_PATH, 'a') as f: f.write(f'{{"sessionId":"debug-session","runId":"run1","hypothesisId":"G","location":"whatsapp_service.py:70","message":"Checking user WhatsApp settings","data":{{"user_id":{user_id},"has_whatsapp_number":{user.whatsapp_number is not None},"whatsapp_number":"{user.whatsapp_number or ""}","notifications_enabled":{user.whatsapp_notifications_enabled if hasattr(user, "whatsapp_notifications_enabled") else "N/A"}}},"timestamp":{int(__import__("time").time()*1000)}}}\n')
            except: pass
            # #endregion
            if not user.whatsapp_number:
                logger.debug(f"User {user_id} does not have WhatsApp number configured")
                return False
            
            if not user.whatsapp_notifications_enabled:
                logger.debug(f"User {user_id} has WhatsApp notifications disabled")
                return False
            
            # Validate phone number
            if not self._validate_phone_number(user.whatsapp_number):
                logger.warning(f"Invalid WhatsApp number for user {user_id}: {user.whatsapp_number}")
                return False
            
            # Get ticket information
            from dashboard.tickets.models import get_ticket
            ticket = get_ticket(ticket_id)
            if not ticket:
                logger.warning(f"Ticket {ticket_id} not found for notification")
                return False
            
            # Format message
            message = self._format_message(notification_type, ticket, context)
            
            # Send via Twilio
            # #region agent log
            try:
                with open(DEBUG_LOG_PATH, 'a') as f: f.write(f'{{"sessionId":"debug-session","runId":"run1","hypothesisId":"G","location":"whatsapp_service.py:94","message":"Calling _send_via_twilio","data":{{"user_id":{user_id},"phone_number":"{user.whatsapp_number}","message_length":{len(message)}}},"timestamp":{int(__import__("time").time()*1000)}}}\n')
            except: pass
            # #endregion
            twilio_message_sid = self._send_via_twilio(user.whatsapp_number, message)
            # #region agent log
            try:
                with open(DEBUG_LOG_PATH, 'a') as f: f.write(f'{{"sessionId":"debug-session","runId":"run1","hypothesisId":"G","location":"whatsapp_service.py:96","message":"_send_via_twilio returned","data":{{"twilio_message_sid":"{twilio_message_sid or "None"}"}},"timestamp":{int(__import__("time").time()*1000)}}}\n')
            except: pass
            # #endregion
            
            if twilio_message_sid:
                logger.info(f"Sent WhatsApp notification to user {user_id} (ticket {ticket_id}, type: {notification_type})")
                return True
            else:
                logger.warning(f"Failed to send WhatsApp notification to user {user_id}")
                return False
                
        except Exception as e:
            logger.error(f"Error sending WhatsApp notification to user {user_id}: {e}", exc_info=True)
            return False
    
    def _format_message(self, notification_type: str, ticket, context: Dict[str, Any]) -> str:
        """
        Generate message text based on notification type.
        
        Args:
            notification_type: Type of notification
            ticket: Ticket object
            context: Additional context
        
        Returns:
            Formatted message string
        """
        ticket_url = self._get_ticket_url(ticket.ticket_id)
        
        if notification_type == 'mention':
            mentioner_name = context.get('mentioner_name', 'Someone')
            comment_preview = context.get('comment_preview', '')
            if comment_preview:
                # Truncate comment preview to 100 characters
                if len(comment_preview) > 100:
                    comment_preview = comment_preview[:97] + '...'
                message = f"You were mentioned in ticket #{ticket.ticket_id}: {ticket.title} by {mentioner_name}.\n\n{comment_preview}\n\nView: {ticket_url}"
            else:
                message = f"You were mentioned in ticket #{ticket.ticket_id}: {ticket.title} by {mentioner_name}.\n\nView: {ticket_url}"
        
        elif notification_type == 'assignment':
            due_date_str = f"Due: {ticket.due_date.strftime('%Y-%m-%d')}" if ticket.due_date else "No due date"
            message = f"Ticket #{ticket.ticket_id}: {ticket.title} has been assigned to you.\nPriority: {ticket.priority}, {due_date_str}.\n\nView: {ticket_url}"
        
        elif notification_type == 'status_change':
            old_status = context.get('old_status', 'Unknown')
            new_status = ticket.status
            changer_name = context.get('changer_name', 'Someone')
            message = f"Ticket #{ticket.ticket_id}: {ticket.title} status changed from {old_status} to {new_status} by {changer_name}.\n\nView: {ticket_url}"
        
        else:
            # Fallback message
            message = f"Update on ticket #{ticket.ticket_id}: {ticket.title}\n\nView: {ticket_url}"
        
        return message
    
    def _get_ticket_url(self, ticket_id: int) -> str:
        """Generate ticket detail page URL."""
        return f"{self.base_url}/tickets/{ticket_id}/page"
    
    def _validate_phone_number(self, phone_number: str) -> bool:
        """
        Validate phone number format (E.164 format).
        
        E.164 format: +[country code][number] (e.g., +14155552671)
        """
        if not phone_number:
            return False
        
        # E.164 format: starts with +, followed by 1-15 digits
        pattern = r'^\+[1-9]\d{1,14}$'
        return bool(re.match(pattern, phone_number))
    
    def _send_via_twilio(self, phone_number: str, message: str) -> Optional[str]:
        """
        Send message via Twilio API.
        
        Args:
            phone_number: Recipient phone number in E.164 format
            message: Message text to send
        
        Returns:
            Twilio message SID if successful, None otherwise
        """
        # #region agent log
        try:
            with open(DEBUG_LOG_PATH, 'a') as f: f.write(f'{{"sessionId":"debug-session","runId":"run1","hypothesisId":"H","location":"whatsapp_service.py:183","message":"_send_via_twilio called","data":{{"phone_number":"{phone_number}","client_exists":{self.client is not None},"whatsapp_from":"{self.whatsapp_from or "None"}"}},"timestamp":{int(__import__("time").time()*1000)}}}\n')
        except: pass
        # #endregion
        if not self.client or not self.whatsapp_from:
            # #region agent log
            try:
                with open(DEBUG_LOG_PATH, 'a') as f: f.write(f'{{"sessionId":"debug-session","runId":"run1","hypothesisId":"H","location":"whatsapp_service.py:195","message":"Twilio client or sender not configured","data":{{"client_exists":{self.client is not None},"whatsapp_from_exists":{self.whatsapp_from is not None}}},"timestamp":{int(__import__("time").time()*1000)}}}\n')
            except: pass
            # #endregion
            logger.warning("Twilio client or sender number not configured")
            return None
        
        try:
            # Ensure phone number is in correct format for Twilio
            # Twilio expects whatsapp:+[number] format
            if not phone_number.startswith('whatsapp:'):
                to_number = f"whatsapp:{phone_number}"
            else:
                to_number = phone_number
            
            # #region agent log
            try:
                with open(DEBUG_LOG_PATH, 'a') as f: f.write(f'{{"sessionId":"debug-session","runId":"run1","hypothesisId":"H","location":"whatsapp_service.py:207","message":"Calling Twilio API","data":{{"to_number":"{to_number}","from_number":"{self.whatsapp_from}","message_preview":"{message[:50]}..."}},"timestamp":{int(__import__("time").time()*1000)}}}\n')
            except: pass
            # #endregion
            # Send message
            twilio_message = self.client.messages.create(
                body=message,
                from_=self.whatsapp_from,
                to=to_number
            )
            
            # #region agent log
            error_code_val = getattr(twilio_message, 'error_code', None)
            error_message_val = getattr(twilio_message, 'error_message', None)
            try:
                with open(DEBUG_LOG_PATH, 'a') as f: f.write(f'{{"sessionId":"debug-session","runId":"run1","hypothesisId":"H","location":"whatsapp_service.py:216","message":"Twilio message created","data":{{"message_sid":"{twilio_message.sid}","status":"{twilio_message.status}","error_code":"{error_code_val or "None"}","error_message":"{error_message_val or "None"}","from_number":"{self.whatsapp_from}","to_number":"{to_number}"}},"timestamp":{int(__import__("time").time()*1000)}}}\n')
            except: pass
            # #endregion
            
            # Check if this is a sandbox number (sandbox numbers typically start with specific patterns)
            is_sandbox = 'sandbox' in self.whatsapp_from.lower() or '14155238886' in self.whatsapp_from
            # #region agent log
            try:
                with open(DEBUG_LOG_PATH, 'a') as f: f.write(f'{{"sessionId":"debug-session","runId":"run1","hypothesisId":"J","location":"whatsapp_service.py:220","message":"Twilio configuration check","data":{{"is_sandbox":{is_sandbox},"whatsapp_from":"{self.whatsapp_from}","account_sid_set":{self.account_sid is not None}}},"timestamp":{int(__import__("time").time()*1000)}}}\n')
            except: pass
            # #endregion
            
            # If sandbox, log a warning about joining
            if is_sandbox:
                logger.warning(f"Using Twilio WhatsApp sandbox. Recipient {to_number} must join the sandbox by sending the join code to {self.whatsapp_from}")
                # #region agent log
                try:
                    with open(DEBUG_LOG_PATH, 'a') as f: f.write(f'{{"sessionId":"debug-session","runId":"run1","hypothesisId":"J","location":"whatsapp_service.py:225","message":"Sandbox mode detected","data":{{"to_number":"{to_number}","join_required":true}},"timestamp":{int(__import__("time").time()*1000)}}}\n')
                except: pass
                # #endregion
            
            # Try to fetch message status after a short delay to check delivery
            import time
            time.sleep(2)  # Wait 2 seconds for status update
            try:
                updated_message = self.client.messages(twilio_message.sid).fetch()
                error_code = getattr(updated_message, 'error_code', None)
                error_message = getattr(updated_message, 'error_message', None)
                # #region agent log
                try:
                    with open(DEBUG_LOG_PATH, 'a') as f: f.write(f'{{"sessionId":"debug-session","runId":"run1","hypothesisId":"H","location":"whatsapp_service.py:247","message":"Twilio message status updated","data":{{"message_sid":"{twilio_message.sid}","status":"{updated_message.status}","error_code":"{error_code or "None"}","error_message":"{error_message or "None"}"}},"timestamp":{int(__import__("time").time()*1000)}}}\n')
                except: pass
                # #endregion
                
                if updated_message.status in ['failed', 'undelivered']:
                    error_msg = error_message or 'Unknown error'
                    error_code_str = str(error_code) if error_code else 'N/A'
                    logger.warning(f"Twilio message failed: {error_msg} (Code: {error_code_str})")
                    
                    # Check for common error codes
                    if error_code == 63007:
                        logger.warning(f"Recipient {to_number} needs to join the Twilio WhatsApp sandbox. Send 'join <code>' to {self.whatsapp_from}")
                    elif error_code == 63016:
                        logger.warning(f"Recipient {to_number} is not a valid WhatsApp number or not registered with WhatsApp")
                    elif error_code:
                        logger.warning(f"Twilio error code {error_code}: {error_msg}. Check Twilio console for details.")
                elif updated_message.status == 'sent':
                    logger.info(f"Twilio message sent successfully to {to_number}")
                elif updated_message.status == 'delivered':
                    logger.info(f"Twilio message delivered to {to_number}")
            except Exception as e:
                # #region agent log
                try:
                    with open(DEBUG_LOG_PATH, 'a') as f: f.write(f'{{"sessionId":"debug-session","runId":"run1","hypothesisId":"H","location":"whatsapp_service.py:262","message":"Error fetching message status","data":{{"error":str(e)}},"timestamp":{int(__import__("time").time()*1000)}}}\n')
                except: pass
                # #endregion
                logger.debug(f"Could not fetch updated message status: {e}")
            
            logger.debug(f"Twilio message sent: SID={twilio_message.sid}, Status={twilio_message.status}")
            return twilio_message.sid
            
        except Exception as e:
            # #region agent log
            try:
                with open(DEBUG_LOG_PATH, 'a') as f: f.write(f'{{"sessionId":"debug-session","runId":"run1","hypothesisId":"H","location":"whatsapp_service.py:222","message":"Exception in _send_via_twilio","data":{{"error":str(e)}},"timestamp":{int(__import__("time").time()*1000)}}}\n')
            except: pass
            # #endregion
            logger.error(f"Error sending message via Twilio: {e}", exc_info=True)
            return None

