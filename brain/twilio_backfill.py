#!/usr/bin/env python3
"""Backfill inbound Twilio WhatsApp messages into Brain."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from typing import Any

import dashboard.config as config
from brain.models import WhatsAppMessage, get_session
from brain.webhooks import _get_or_create_thread, _strip_whatsapp_prefix


def backfill_recent_inbound(limit: int = 50) -> dict[str, Any]:
    """Fetch recent inbound WhatsApp messages from Twilio and store missing rows."""
    from twilio.rest import Client

    client = Client(config.TWILIO_ACCOUNT_SID, config.TWILIO_AUTH_TOKEN)
    session = get_session()
    created = 0
    skipped = 0
    try:
        for twilio_message in client.messages.list(limit=limit):
            if getattr(twilio_message, "direction", None) != "inbound":
                continue
            from_number = _strip_whatsapp_prefix(getattr(twilio_message, "from_", None))
            to_number = _strip_whatsapp_prefix(getattr(twilio_message, "to", None))
            if not from_number and not to_number:
                continue
            provider_message_id = twilio_message.sid
            existing = session.query(WhatsAppMessage).filter(
                WhatsAppMessage.provider == "twilio",
                WhatsAppMessage.provider_message_id == provider_message_id,
            ).first()
            if existing:
                skipped += 1
                continue
            payload = {
                "provider_message_id": provider_message_id,
                "from_number": from_number,
                "to_number": to_number,
                "sender_name": None,
                "body": getattr(twilio_message, "body", "") or "",
                "media_urls": [],
                "raw_payload": {
                    "sid": provider_message_id,
                    "direction": getattr(twilio_message, "direction", None),
                    "status": getattr(twilio_message, "status", None),
                    "date_created": _iso_or_none(getattr(twilio_message, "date_created", None)),
                    "date_sent": _iso_or_none(getattr(twilio_message, "date_sent", None)),
                    "source": "twilio_backfill",
                },
                "received_at": _datetime_or_utcnow(getattr(twilio_message, "date_sent", None) or getattr(twilio_message, "date_created", None)),
            }
            thread = _get_or_create_thread(session, payload)
            session.add(
                WhatsAppMessage(
                    whatsapp_thread_id=thread.whatsapp_thread_id,
                    provider="twilio",
                    provider_message_id=provider_message_id,
                    from_number=from_number,
                    to_number=to_number,
                    direction="inbound",
                    body=payload["body"],
                    media_urls=[],
                    raw_payload=payload["raw_payload"],
                    provider_status=getattr(twilio_message, "status", None),
                    received_at=payload["received_at"],
                )
            )
            thread.updated_at = datetime.utcnow()
            created += 1
        session.commit()
        return {"created": created, "skipped": skipped}
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _iso_or_none(value):
    return value.isoformat() if value else None


def _datetime_or_utcnow(value):
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            return value.astimezone(timezone.utc).replace(tzinfo=None)
        return value
    return datetime.utcnow()


def main():
    parser = argparse.ArgumentParser(description="Backfill inbound Twilio WhatsApp messages into Brain")
    parser.add_argument("--limit", type=int, default=50)
    args = parser.parse_args()
    print(json.dumps(backfill_recent_inbound(limit=args.limit), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
