#!/usr/bin/env python3
"""Import WhatsApp exported chat text into Brain memory."""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.exc import IntegrityError

from brain.models import WhatsAppMessage, get_session, stable_hash
from brain.services import load_whatsapp_group_profiles
from brain.webhooks import _get_or_create_thread, _lookup_whatsapp_message, normalize_whatsapp_bridge_payload

IOS_LINE_RE = re.compile(
    r"^\[(?P<date>\d{1,2}/\d{1,2}/\d{2,4}),\s+"
    r"(?P<time>\d{1,2}:\d{2}(?::\d{2})?(?:\s?[AP]M)?)\]\s+(?P<rest>.*)$",
    re.IGNORECASE,
)
ANDROID_LINE_RE = re.compile(
    r"^(?P<date>\d{1,2}/\d{1,2}/\d{2,4}),\s+"
    r"(?P<time>\d{1,2}:\d{2}(?::\d{2})?(?:\s?[AP]M)?)\s+-\s+(?P<rest>.*)$",
    re.IGNORECASE,
)
DATE_FORMATS = (
    "%m/%d/%y %I:%M:%S %p",
    "%m/%d/%y %I:%M %p",
    "%m/%d/%Y %I:%M:%S %p",
    "%m/%d/%Y %I:%M %p",
    "%m/%d/%y %H:%M:%S",
    "%m/%d/%y %H:%M",
    "%m/%d/%Y %H:%M:%S",
    "%m/%d/%Y %H:%M",
)
MEDIA_MARKERS = {"<media omitted>", "image omitted", "video omitted", "audio omitted", "sticker omitted"}


@dataclass
class ExportMessage:
    """One parsed WhatsApp export message."""

    occurred_at: datetime
    sender_name: str
    body: str
    is_system: bool = False


def parse_export_file(path: str | Path, *, source_timezone: str = "America/Los_Angeles", include_system: bool = False) -> list[ExportMessage]:
    """Parse a WhatsApp exported chat text file."""
    text = Path(path).read_text(encoding="utf-8-sig")
    return parse_export_text(text, source_timezone=source_timezone, include_system=include_system)


def parse_export_text(text: str, *, source_timezone: str = "America/Los_Angeles", include_system: bool = False) -> list[ExportMessage]:
    """Parse iOS or Android WhatsApp export text into messages."""
    tz = ZoneInfo(source_timezone)
    messages: list[ExportMessage] = []
    current: ExportMessage | None = None
    for raw_line in (text or "").splitlines():
        line = raw_line.rstrip("\n")
        parsed = _parse_export_line(line, tz)
        if parsed:
            if current and (include_system or not current.is_system):
                messages.append(current)
            current = parsed
            continue
        if current:
            current.body = f"{current.body}\n{line}".strip()
    if current and (include_system or not current.is_system):
        messages.append(current)
    return messages


def _parse_export_line(line: str, tz: ZoneInfo) -> ExportMessage | None:
    match = IOS_LINE_RE.match(line) or ANDROID_LINE_RE.match(line)
    if not match:
        return None
    occurred_at = _parse_export_datetime(match.group("date"), match.group("time"), tz)
    if not occurred_at:
        return None
    rest = match.group("rest").strip()
    sender, body = _split_sender_body(rest)
    return ExportMessage(
        occurred_at=occurred_at,
        sender_name=sender,
        body=body,
        is_system=sender == "WhatsApp System",
    )


def _parse_export_datetime(date_text: str, time_text: str, tz: ZoneInfo) -> datetime | None:
    normalized_time = time_text.upper().replace("\u202f", " ").replace("\xa0", " ")
    normalized = f"{date_text} {normalized_time}"
    normalized = " ".join(normalized.split())
    for fmt in DATE_FORMATS:
        try:
            local = datetime.strptime(normalized, fmt).replace(tzinfo=tz)
            return local.astimezone(timezone.utc).replace(tzinfo=None)
        except ValueError:
            continue
    return None


def _split_sender_body(rest: str) -> tuple[str, str]:
    if ": " not in rest:
        return "WhatsApp System", rest
    sender, body = rest.split(": ", 1)
    return sender.strip() or "Unknown", body.strip()


def import_export_file(
    path: str | Path,
    *,
    group_name: str,
    source_timezone: str = "America/Los_Angeles",
    include_system: bool = False,
    dry_run: bool = False,
    limit: int | None = None,
) -> dict[str, Any]:
    """Import one exported WhatsApp chat file into Brain."""
    profile = _profile_for_group(group_name)
    messages = parse_export_file(path, source_timezone=source_timezone, include_system=include_system)
    if limit:
        messages = messages[: int(limit)]
    if dry_run:
        return {
            "status": "dry_run",
            "group_name": group_name,
            "parsed": len(messages),
            "first_received_at": messages[0].occurred_at.isoformat() if messages else None,
            "last_received_at": messages[-1].occurred_at.isoformat() if messages else None,
        }

    session = get_session()
    imported = 0
    duplicates = 0
    failed = 0
    try:
        for message in messages:
            try:
                result = _store_export_message(session, message, group_name=group_name, profile=profile)
                session.commit()
                if result == "stored":
                    imported += 1
                else:
                    duplicates += 1
            except Exception:
                session.rollback()
                failed += 1
        session.commit()
    finally:
        session.close()
    return {
        "status": "completed",
        "group_name": group_name,
        "parsed": len(messages),
        "stored": imported,
        "duplicates": duplicates,
        "failed": failed,
        "first_received_at": messages[0].occurred_at.isoformat() if messages else None,
        "last_received_at": messages[-1].occurred_at.isoformat() if messages else None,
    }


def _store_export_message(session, message: ExportMessage, *, group_name: str, profile: dict[str, Any]) -> str:
    provider_message_id = "export_" + stable_hash(
        {
            "group": profile.get("name") or group_name,
            "occurred_at": message.occurred_at.isoformat(),
            "sender": message.sender_name,
            "body": message.body,
        }
    )
    has_media = message.body.strip().lower() in MEDIA_MARKERS
    payload = normalize_whatsapp_bridge_payload(
        {
            "provider_message_id": provider_message_id,
            "group_id": profile.get("name") or group_name,
            "group_name": group_name,
            "group_profile_name": profile.get("name") or group_name,
            "sender_id": message.sender_name,
            "sender_name": message.sender_name,
            "body": "[media message]" if has_media else message.body,
            "timestamp": message.occurred_at.isoformat(),
            "message_type": "media" if has_media else "chat",
            "has_media": has_media,
            "portfolio_name": profile.get("portfolio") or "",
            "group_kind": profile.get("kind") or "unknown",
            "group_description": profile.get("description") or "",
            "source": "whatsapp_export",
        }
    )
    thread = _get_or_create_thread(session, payload)
    existing = _lookup_whatsapp_message(session, payload["provider"], payload["provider_message_id"])
    if existing:
        return "duplicate"
    content_duplicate = session.query(WhatsAppMessage).filter(
        WhatsAppMessage.whatsapp_thread_id == thread.whatsapp_thread_id,
        WhatsAppMessage.received_at == payload["received_at"],
        WhatsAppMessage.sender_name == payload.get("sender_name"),
        WhatsAppMessage.body == payload.get("body"),
    ).first()
    if content_duplicate:
        return "duplicate"
    try:
        session.add(
            WhatsAppMessage(
                whatsapp_thread_id=thread.whatsapp_thread_id,
                provider=payload["provider"],
                provider_message_id=payload["provider_message_id"],
                from_number=payload.get("from_number"),
                to_number=payload.get("to_number"),
                sender_name=payload.get("sender_name"),
                direction=payload.get("direction") or "inbound",
                body=payload.get("body"),
                media_urls=payload.get("media_urls", []),
                raw_payload={**(payload.get("raw_payload") or {}), "source": "whatsapp_export"},
                provider_status="imported",
                received_at=payload["received_at"],
            )
        )
        thread.updated_at = datetime.utcnow()
        session.flush()
        return "stored"
    except IntegrityError:
        session.rollback()
        return "duplicate"


def _profile_for_group(group_name: str) -> dict[str, Any]:
    key = _normalize_group_name(group_name)
    for profile in load_whatsapp_group_profiles():
        names = [profile.get("name"), *(profile.get("aliases") or [])]
        if key in {_normalize_group_name(name) for name in names if name}:
            return profile
    return {"name": group_name, "portfolio": "", "kind": "unknown", "description": "Imported WhatsApp export"}


def _normalize_group_name(value: str | None) -> str:
    return " ".join(str(value or "").strip().lower().split())


def main() -> int:
    parser = argparse.ArgumentParser(description="Import a WhatsApp exported chat text file into STR Signal Brain.")
    parser.add_argument("--file", required=True, help="Path to WhatsApp exported .txt file")
    parser.add_argument("--group-name", required=True, help="Brain/WhatsApp group name for the export")
    parser.add_argument("--timezone", default="America/Los_Angeles", help="Timezone used by the export timestamps")
    parser.add_argument("--include-system", action="store_true", help="Import WhatsApp system messages too")
    parser.add_argument("--dry-run", action="store_true", help="Parse only; do not store")
    parser.add_argument("--limit", type=int, help="Only import the first N parsed messages")
    args = parser.parse_args()
    result = import_export_file(
        args.file,
        group_name=args.group_name,
        source_timezone=args.timezone,
        include_system=args.include_system,
        dry_run=args.dry_run,
        limit=args.limit,
    )
    import json

    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("failed", 0) == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
