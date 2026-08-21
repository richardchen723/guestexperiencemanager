#!/usr/bin/env python3
"""Brief delivery adapters for email and WhatsApp."""

from __future__ import annotations

import logging
import os
import smtplib
from email.message import EmailMessage

import dashboard.config as config
from dashboard.auth.models import get_user_by_id

logger = logging.getLogger(__name__)

WHATSAPP_BODY_LIMIT = 1500
WHATSAPP_COMPACT_FOOTER = "More in Brain dashboard."


class BrainDeliveryService:
    """Send daily briefs to configured email and WhatsApp channels."""

    def send_email(self, to_email: str, subject: str, body: str) -> dict:
        host = os.getenv("SMTP_HOST")
        if not host or not to_email:
            logger.info("SMTP not configured or recipient missing; skipping email brief")
            return {"sent": False, "provider": "smtp", "recipient": to_email, "error": "SMTP not configured or recipient missing"}
        port = int(os.getenv("SMTP_PORT", "587"))
        username = os.getenv("SMTP_USERNAME")
        password = os.getenv("SMTP_PASSWORD")
        use_tls = os.getenv("SMTP_USE_TLS", "true").lower() == "true"
        sender = os.getenv("BRAIN_EMAIL_FROM") or username
        if not sender:
            logger.info("BRAIN_EMAIL_FROM or SMTP_USERNAME is required for email delivery")
            return {"sent": False, "provider": "smtp", "recipient": to_email, "error": "Sender missing"}

        message = EmailMessage()
        message["From"] = sender
        message["To"] = to_email
        message["Subject"] = subject
        message.set_content(body)

        with smtplib.SMTP(host, port, timeout=30) as smtp:
            if use_tls:
                smtp.starttls()
            if username and password:
                smtp.login(username, password)
            smtp.send_message(message)
        return {"sent": True, "provider": "smtp", "recipient": to_email}

    def send_whatsapp_to_user(self, user_id: int, body: str) -> dict:
        user = get_user_by_id(user_id)
        if not user or not user.whatsapp_number or not user.whatsapp_notifications_enabled:
            return {"sent": False, "provider": "twilio", "recipient": None, "error": "User WhatsApp delivery unavailable"}
        return self.send_whatsapp(user.whatsapp_number, body)

    def send_whatsapp(self, to_number: str, body: str) -> dict:
        if not (config.TWILIO_ACCOUNT_SID and config.TWILIO_AUTH_TOKEN and config.TWILIO_WHATSAPP_FROM):
            logger.info("Twilio credentials not configured; skipping WhatsApp brief")
            return {"sent": False, "provider": "twilio", "recipient": to_number, "error": "Twilio credentials not configured"}
        from twilio.rest import Client

        client = Client(config.TWILIO_ACCOUNT_SID, config.TWILIO_AUTH_TOKEN)
        destination = to_number if to_number.startswith("whatsapp:") else f"whatsapp:{to_number}"
        message_body = compact_whatsapp_body(body)
        message = client.messages.create(body=message_body, from_=config.TWILIO_WHATSAPP_FROM, to=destination)
        return {
            "sent": True,
            "provider": "twilio",
            "recipient": to_number,
            "provider_message_id": getattr(message, "sid", None),
            "status": getattr(message, "status", None),
            "body_length": len(message_body),
            "original_body_length": len(body or ""),
            "truncated": message_body != (body or ""),
        }


def compact_whatsapp_body(body: str | None, limit: int = WHATSAPP_BODY_LIMIT) -> str:
    """Keep WhatsApp briefs under Twilio's body limit while preserving whole lines."""
    text = body or ""
    if len(text) <= limit:
        return text
    footer = f"\n\n{WHATSAPP_COMPACT_FOOTER}"
    available = max(0, limit - len(footer))
    kept: list[str] = []
    current_len = 0
    for line in text.splitlines():
        addition_len = len(line) if not kept else len(line) + 1
        if current_len + addition_len > available:
            break
        kept.append(line)
        current_len += addition_len
    if kept:
        return ("\n".join(kept).rstrip() + footer)[:limit]
    return (text[:available].rstrip() + footer)[:limit]
