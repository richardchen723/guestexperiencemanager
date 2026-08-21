#!/usr/bin/env python3
"""Public webhooks for STR Signal Brain."""

from __future__ import annotations

import logging
import hmac
from datetime import datetime, timezone
from typing import Any

from flask import Blueprint, jsonify, request
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

import dashboard.config as config
from brain.models import Portfolio, WhatsAppMessage, WhatsAppThread, get_session, stable_hash
from dashboard.auth.models import User, get_session as get_user_session

logger = logging.getLogger(__name__)

webhooks_bp = Blueprint("brain_webhooks", __name__, url_prefix="/webhooks")


@webhooks_bp.route("/twilio/whatsapp", methods=["POST"])
def twilio_whatsapp_webhook():
    """Ingest inbound WhatsApp messages from Twilio."""
    if not validate_twilio_request(request):
        return jsonify({"error": "Invalid Twilio signature"}), 403

    payload = normalize_twilio_payload(request.form.to_dict(flat=True))
    session = get_session()
    try:
        thread = _get_or_create_thread(session, payload)
        existing = session.query(WhatsAppMessage).filter(
            WhatsAppMessage.provider == "twilio",
            WhatsAppMessage.provider_message_id == payload["provider_message_id"],
        ).first()
        if existing:
            return jsonify({"status": "duplicate", "message_id": existing.whatsapp_message_id}), 200

        message = WhatsAppMessage(
            whatsapp_thread_id=thread.whatsapp_thread_id,
            provider="twilio",
            provider_message_id=payload["provider_message_id"],
            from_number=payload.get("from_number"),
            to_number=payload.get("to_number"),
            sender_name=payload.get("sender_name"),
            direction="inbound",
            body=payload.get("body"),
            media_urls=payload.get("media_urls", []),
            raw_payload=payload.get("raw_payload", {}),
            received_at=payload.get("received_at") or datetime.utcnow(),
        )
        session.add(message)
        thread.updated_at = datetime.utcnow()
        session.commit()
        return jsonify({"status": "stored", "message_id": message.whatsapp_message_id}), 201
    except IntegrityError as exc:
        session.rollback()
        existing = _lookup_whatsapp_message(session, "twilio", payload["provider_message_id"])
        if existing:
            return jsonify({"status": "duplicate", "message_id": existing.whatsapp_message_id}), 200
        logger.error("Error storing Twilio WhatsApp webhook: %s", exc, exc_info=True)
        return jsonify({"error": "Failed to store WhatsApp message"}), 500
    except Exception as exc:
        session.rollback()
        logger.error("Error storing Twilio WhatsApp webhook: %s", exc, exc_info=True)
        return jsonify({"error": "Failed to store WhatsApp message"}), 500
    finally:
        session.close()


@webhooks_bp.route("/twilio/whatsapp/status", methods=["POST"])
def twilio_whatsapp_status_callback():
    """Store Twilio delivery status updates for WhatsApp messages."""
    if not validate_twilio_request(request):
        return jsonify({"error": "Invalid Twilio signature"}), 403
    form = request.form.to_dict(flat=True)
    provider_message_id = form.get("MessageSid") or form.get("SmsSid")
    status = form.get("MessageStatus") or form.get("SmsStatus")
    session = get_session()
    try:
        if provider_message_id:
            message = session.query(WhatsAppMessage).filter(
                WhatsAppMessage.provider == "twilio",
                WhatsAppMessage.provider_message_id == provider_message_id,
            ).first()
            if message:
                message.provider_status = status
                message.error_code = form.get("ErrorCode")
                message.error_message = form.get("ErrorMessage")
                message.raw_payload = {
                    **(message.raw_payload or {}),
                    "status_callback": form,
                }
        _update_delivery_log_from_twilio(session, provider_message_id, status, form)
        session.commit()
        return jsonify({"status": "stored"}), 200
    except Exception as exc:
        session.rollback()
        logger.error("Error storing Twilio WhatsApp status callback: %s", exc, exc_info=True)
        return jsonify({"error": "Failed to store Twilio status"}), 500
    finally:
        session.close()


@webhooks_bp.route("/whatsapp/bridge", methods=["POST"])
def whatsapp_web_bridge_webhook():
    """Ingest selected WhatsApp Web group messages from the bridge process."""
    if not validate_whatsapp_bridge_request(request):
        return jsonify({"error": "Invalid bridge token"}), 403

    try:
        payload = normalize_whatsapp_bridge_payload(request.get_json(silent=True) or {})
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    session = get_session()
    try:
        thread = _get_or_create_thread(session, payload)
        existing = session.query(WhatsAppMessage).filter(
            WhatsAppMessage.provider == payload["provider"],
            WhatsAppMessage.provider_message_id == payload["provider_message_id"],
        ).first()
        if existing:
            return jsonify({"status": "duplicate", "message_id": existing.whatsapp_message_id}), 200

        message = WhatsAppMessage(
            whatsapp_thread_id=thread.whatsapp_thread_id,
            provider=payload["provider"],
            provider_message_id=payload["provider_message_id"],
            from_number=payload.get("from_number"),
            to_number=payload.get("to_number"),
            sender_name=payload.get("sender_name"),
            direction=payload.get("direction") or "inbound",
            body=payload.get("body"),
            media_urls=payload.get("media_urls", []),
            raw_payload=payload.get("raw_payload", {}),
            provider_status=payload.get("provider_status"),
            received_at=payload.get("received_at") or datetime.utcnow(),
        )
        session.add(message)
        thread.updated_at = datetime.utcnow()
        session.commit()
        return jsonify({"status": "stored", "message_id": message.whatsapp_message_id}), 201
    except IntegrityError as exc:
        session.rollback()
        existing = _lookup_whatsapp_message(session, payload["provider"], payload["provider_message_id"])
        if existing:
            return jsonify({"status": "duplicate", "message_id": existing.whatsapp_message_id}), 200
        logger.error("Error storing WhatsApp Web bridge payload: %s", exc, exc_info=True)
        return jsonify({"error": "Failed to store WhatsApp bridge message"}), 500
    except Exception as exc:
        session.rollback()
        logger.error("Error storing WhatsApp Web bridge payload: %s", exc, exc_info=True)
        return jsonify({"error": "Failed to store WhatsApp bridge message"}), 500
    finally:
        session.close()


def validate_twilio_request(flask_request) -> bool:
    """Validate the Twilio request signature when configured."""
    should_validate = getattr(config, "BRAIN_TWILIO_VALIDATE_SIGNATURE", True)
    if not should_validate:
        return True
    if not config.TWILIO_AUTH_TOKEN:
        logger.warning("Twilio auth token missing; rejecting webhook because signature validation is enabled")
        return False
    signature = flask_request.headers.get("X-Twilio-Signature")
    if not signature:
        return False
    try:
        from twilio.request_validator import RequestValidator
    except ImportError:
        logger.warning("twilio package missing; cannot validate webhook")
        return False

    url = _external_webhook_url(flask_request)
    validator = RequestValidator(config.TWILIO_AUTH_TOKEN)
    return validator.validate(url, flask_request.form, signature)


def _lookup_whatsapp_message(session, provider: str, provider_message_id: str) -> WhatsAppMessage | None:
    return session.query(WhatsAppMessage).filter(
        WhatsAppMessage.provider == provider,
        WhatsAppMessage.provider_message_id == provider_message_id,
    ).first()


def validate_whatsapp_bridge_request(flask_request) -> bool:
    """Validate the shared secret used by the WhatsApp Web bridge."""
    token = getattr(config, "BRAIN_WHATSAPP_BRIDGE_TOKEN", None)
    if not token:
        logger.warning("BRAIN_WHATSAPP_BRIDGE_TOKEN missing; rejecting WhatsApp Web bridge payload")
        return False

    auth_header = flask_request.headers.get("Authorization", "")
    candidate = ""
    if auth_header.lower().startswith("bearer "):
        candidate = auth_header.split(" ", 1)[1].strip()
    if not candidate:
        candidate = flask_request.headers.get("X-Brain-Bridge-Token", "").strip()
    return bool(candidate) and hmac.compare_digest(candidate, token)


def normalize_twilio_payload(form: dict[str, Any]) -> dict[str, Any]:
    """Normalize Twilio form data into Brain's WhatsApp message shape."""
    media_urls = []
    try:
        media_count = int(form.get("NumMedia") or 0)
    except ValueError:
        media_count = 0
    for index in range(media_count):
        url = form.get(f"MediaUrl{index}")
        if url:
            media_urls.append(
                {
                    "url": url,
                    "content_type": form.get(f"MediaContentType{index}"),
                }
            )

    provider_message_id = form.get("MessageSid") or form.get("SmsMessageSid")
    if not provider_message_id:
        provider_message_id = stable_hash(form)
    return {
        "provider": "twilio",
        "provider_message_id": provider_message_id,
        "from_number": _strip_whatsapp_prefix(form.get("From")),
        "to_number": _strip_whatsapp_prefix(form.get("To")),
        "sender_name": form.get("ProfileName") or form.get("WaId"),
        "body": form.get("Body") or "",
        "media_urls": media_urls,
        "raw_payload": dict(form),
        "received_at": datetime.utcnow(),
    }


def normalize_whatsapp_bridge_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Normalize WhatsApp Web bridge JSON into Brain's WhatsApp message shape."""
    if not isinstance(payload, dict):
        raise ValueError("JSON object payload is required")

    canonical_group_name = _clean_bridge_value(payload.get("group_profile_name"))
    group_id = _clean_bridge_value(canonical_group_name or payload.get("group_id") or payload.get("chat_id"))
    group_name = _clean_bridge_value(canonical_group_name or payload.get("group_name") or payload.get("chat_name"))
    if not group_id:
        raise ValueError("group_id is required")
    if not group_name:
        raise ValueError("group_name is required")

    sender_id = _clean_bridge_value(payload.get("sender_id") or payload.get("author") or payload.get("from"))
    sender_name = _clean_bridge_value(payload.get("sender_name") or payload.get("notify_name") or payload.get("pushname"))
    message_type = _clean_bridge_value(payload.get("message_type") or payload.get("type")) or "chat"
    body = _clean_bridge_value(payload.get("body") or payload.get("caption")) or ""
    has_media = bool(payload.get("has_media"))
    if not body and has_media:
        body = f"[{message_type} message]"

    received_at = _parse_bridge_received_at(payload.get("timestamp") or payload.get("received_at"))
    provider_message_id = _clean_bridge_value(payload.get("provider_message_id") or payload.get("message_id"))
    if not provider_message_id:
        provider_message_id = stable_hash(
            {
                "group_id": group_id,
                "sender_id": sender_id,
                "body": body,
                "timestamp": received_at.isoformat(),
            }
        )

    media_urls = payload.get("media_urls") if isinstance(payload.get("media_urls"), list) else []
    from_me = bool(payload.get("from_me"))
    portfolio_name = _clean_bridge_value(payload.get("portfolio_name"))
    group_kind = _clean_bridge_value(payload.get("group_kind"))
    group_description = _clean_bridge_value(payload.get("group_description"))
    return {
        "provider": "whatsapp_web",
        "provider_message_id": provider_message_id,
        "provider_thread_id": group_id,
        "thread_display_name": group_name,
        "from_number": sender_id,
        "to_number": group_id,
        "sender_name": sender_name,
        "body": body[:12000],
        "media_urls": media_urls,
        "direction": "outbound" if from_me else "inbound",
        "provider_status": _clean_bridge_value(payload.get("status")),
        "portfolio_name": portfolio_name,
        "group_kind": group_kind,
        "group_description": group_description,
        "raw_payload": dict(payload),
        "received_at": received_at,
    }


def _get_or_create_thread(session, payload: dict[str, Any]) -> WhatsAppThread:
    provider = payload.get("provider") or "twilio"
    from_number = payload.get("from_number")
    provider_thread_id = payload.get("provider_thread_id") or from_number or payload["provider_message_id"]
    thread = session.query(WhatsAppThread).filter(
        WhatsAppThread.provider == provider,
        WhatsAppThread.provider_thread_id == provider_thread_id,
    ).first()
    mapped_user_id = _lookup_user_id_by_whatsapp(from_number) if provider == "twilio" else None
    display_name = payload.get("thread_display_name") or payload.get("sender_name")
    portfolio_id = _lookup_portfolio_id(session, payload.get("portfolio_name"))
    if not thread:
        thread = WhatsAppThread(
            provider=provider,
            provider_thread_id=provider_thread_id,
            phone_number=from_number if provider == "twilio" else None,
            display_name=display_name,
            mapped_user_id=mapped_user_id,
            portfolio_id=portfolio_id,
        )
        session.add(thread)
        session.flush()
    else:
        if provider == "twilio":
            thread.phone_number = from_number or thread.phone_number
        thread.display_name = display_name or thread.display_name
        thread.mapped_user_id = mapped_user_id or thread.mapped_user_id
        thread.portfolio_id = portfolio_id or thread.portfolio_id
    return thread


def _lookup_user_id_by_whatsapp(phone_number: str | None) -> int | None:
    if not phone_number:
        return None
    user_session = get_user_session()
    try:
        user = user_session.query(User).filter(User.whatsapp_number == phone_number).first()
        return user.user_id if user else None
    finally:
        user_session.close()


def _lookup_portfolio_id(session, portfolio_name: str | None) -> int | None:
    if not portfolio_name:
        return None
    normalized = portfolio_name.strip().lower()
    portfolio = session.query(Portfolio).filter(func.lower(Portfolio.name) == normalized).first()
    return portfolio.portfolio_id if portfolio else None


def _strip_whatsapp_prefix(value: str | None) -> str | None:
    if not value:
        return None
    return value.replace("whatsapp:", "")


def _clean_bridge_value(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _parse_bridge_received_at(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).replace(tzinfo=None) if value.tzinfo else value
    if isinstance(value, (int, float)):
        timestamp = float(value)
        if timestamp > 9999999999:
            timestamp = timestamp / 1000
        return datetime.fromtimestamp(timestamp, tz=timezone.utc).replace(tzinfo=None)
    if isinstance(value, str) and value.strip():
        text = value.strip()
        try:
            if text.isdigit():
                return _parse_bridge_received_at(int(text))
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            return parsed.astimezone(timezone.utc).replace(tzinfo=None) if parsed.tzinfo else parsed
        except ValueError:
            logger.warning("Unable to parse WhatsApp bridge timestamp: %s", text)
    return datetime.utcnow()


def _update_delivery_log_from_twilio(session, provider_message_id: str | None, status: str | None, form: dict[str, Any]):
    if not provider_message_id:
        return
    from brain.models import BriefDeliveryLog

    log = session.query(BriefDeliveryLog).filter(
        BriefDeliveryLog.provider == "twilio",
        BriefDeliveryLog.provider_message_id == provider_message_id,
    ).first()
    if not log:
        return
    log.status = status or log.status
    log.error_message = form.get("ErrorMessage") or log.error_message
    log.payload = {
        **(log.payload or {}),
        "status_callback": form,
    }
    if status in {"delivered", "sent"}:
        log.delivered_at = datetime.utcnow()


def _external_webhook_url(flask_request) -> str:
    base_url = getattr(config, "BRAIN_PUBLIC_BASE_URL", None)
    if base_url:
        return f"{base_url.rstrip('/')}{flask_request.path}"
    proto = flask_request.headers.get("X-Forwarded-Proto", flask_request.scheme)
    host = flask_request.headers.get("X-Forwarded-Host", flask_request.host)
    return f"{proto}://{host}{flask_request.path}"
