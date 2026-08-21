#!/usr/bin/env python3
"""Application services for STR Signal Brain."""

from __future__ import annotations

import html
import json
import logging
import os
import re
import statistics
import time
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import requests
from sqlalchemy import case, exists, func, or_
from sqlalchemy.orm import joinedload

import dashboard.config as config
from brain.access import accessible_portfolio_ids, filter_portfolio_query, is_brain_admin
from brain.ai import ASK_PROMPT_VERSION, BOOKING_HEALTH_PROMPT_VERSION, SIGNAL_PROMPT_VERSION, BrainAIClient
from brain.aggregator import (
    DEFAULT_SOURCE_DEFINITIONS,
    FOUNDATION_LISTING_COVERAGE_TARGETS,
    FOUNDATION_REQUIRED_FACT_TYPES_BY_SOURCE,
    FOUNDATION_REQUIRED_METRIC_NAMES,
    FOUNDATION_SOURCE_FRESHNESS_TARGETS,
    data_source_health_status,
    summarize_data_aggregator_health,
    summarize_foundation_audit,
)
from brain.delivery import BrainDeliveryService
from brain.models import (
    BookingHealthSnapshot,
    BookingHealthAnalysis,
    BriefDeliveryLog,
    BusinessFact,
    BusinessMetricSnapshot,
    CalendarSnapshot,
    DataIngestionRun,
    DataSource,
    DailyBrief,
    GuestStayMemory,
    OpenLoop,
    Portfolio,
    PortfolioListing,
    PortfolioUser,
    PromptArtifact,
    PriceLabsSnapshot,
    Signal,
    SignalEvidence,
    SignalRun,
    WhatsAppGroupMemory,
    WhatsAppThread,
    WhatsAppMessage,
    as_json_safe,
    get_session,
    stable_hash,
)
from brain.pricelabs import PriceLabsClient
from brain.scoring import (
    compute_booking_health_proxy,
    is_confirmed_reservation_status,
    make_dedupe_key,
    normalize_signal_audience,
    normalize_signal_category,
    normalize_signal_severity,
    normalize_signal_status,
    rank_signal_payload,
)
from database.models import (
    Listing,
    ListingTag,
    MessageMetadata,
    Reservation,
    Review,
    SyncLog,
    Tag,
    get_session as get_main_session,
)

logger = logging.getLogger(__name__)

ACTIVE_SIGNAL_STATUSES = ("new", "acknowledged", "watching", "escalated")
INACTIVE_SIGNAL_STATUSES = ("resolved", "ignored")
OPERATOR_ESCALATED_MIN_RANK = 95.0
MANAGER_TODO_LIMIT = 9
STALE_RECOMPUTED_SIGNAL_SOURCES = (
    "deterministic_booking_health",
    "deterministic_guest_message",
    "deterministic_guest_stay_memory",
)
OPENAI_SIGNAL_SOURCE = "openai_structured"

TAG_PORTFOLIOS = (
    {
        "name": "Enchanted Havens",
        "description": "Listings tagged as Enchanted Havens in Cotton Candy.",
        "aliases": ("enchanted havens",),
    },
    {
        "name": "Luminary Resorts",
        "description": "Listings tagged as Luminary Resorts in Cotton Candy.",
        "aliases": ("luminary resorts", "luminary resort"),
    },
    {
        "name": "Urban Stays",
        "description": "PT300 / Urban Stays listings from Cotton Candy tags.",
        "aliases": ("pt300", "urban stays", "urbans stays"),
    },
    {
        "name": "Middlefork",
        "description": "Middlefork / Crockett's Run listings from Cotton Candy tags.",
        "aliases": (
            "middlefork",
            "middlefork ridge",
            "crockett's run",
            "crockett’s run",
            "crocketts run",
        ),
    },
    {
        "name": "crestwood",
        "description": "Crestwood listings grouped from the Hostaway portfolio tag.",
        "aliases": ("crestwood",),
    },
    {
        "name": "LA St Gabe",
        "description": "Los Angeles St Gabe listings grouped from the San Gabriel Units tag.",
        "aliases": ("la st gabe", "st gabe", "st gabe units", "san gabriel units"),
    },
)

TAG_ALIAS_TO_PORTFOLIO = {
    alias: spec["name"]
    for spec in TAG_PORTFOLIOS
    for alias in spec["aliases"]
}
TAG_PORTFOLIO_NAMES = tuple(spec["name"] for spec in TAG_PORTFOLIOS)
EXPLICIT_LISTING_PORTFOLIOS = {
    558675: "crestwood",
    558676: "crestwood",
    558677: "crestwood",
    558678: "crestwood",
}
UNKNOWN_LISTING_NAME = "Unknown property"
AIRBNB_URL_PATTERN = re.compile(r"https?://(?:www\.)?airbnb\.[^\s\"'<>]+", re.IGNORECASE)
HTML_TAG_PATTERN = re.compile(r"<[^>]+>")
BOOKING_HEALTH_HORIZONS = (7, 30, 60)
AI_REVENUE_SIGNAL_RE = re.compile(
    r"\b(?:booking|bookings|pace|occupancy|revenue|price|pricing|pricelabs|minimum stay|min stay|rate|adr|calendar)\b",
    re.IGNORECASE,
)
AI_REVIEW_SIGNAL_RE = re.compile(
    r"\b(?:review|reviews|rating|ratings|stars?|complaint|complaints|quality risk|bad experience|refund)\b",
    re.IGNORECASE,
)
AI_EXPLICIT_OPERATIONAL_CATEGORIES = {
    "cleaning",
    "housekeeping",
    "maintenance",
    "repair",
    "repairs",
    "security",
    "vendor",
    "vendor_followup",
    "task",
    "turnover",
}


def normalize_tag_name(name: str | None) -> str:
    """Normalize Cotton Candy listing tags for portfolio matching."""
    return " ".join((name or "").strip().lower().split())


def portfolio_name_for_tags(tag_names: tuple[str, ...] | list[str]) -> str | None:
    """Return the canonical Brain portfolio name for listing tag names."""
    normalized = {normalize_tag_name(tag_name) for tag_name in tag_names}
    for spec in TAG_PORTFOLIOS:
        if normalized.intersection(spec["aliases"]):
            return spec["name"]
    return None


def portfolio_name_for_listing(listing_id: int, tag_names: tuple[str, ...] | list[str]) -> str | None:
    """Return the Brain portfolio name from explicit listing rules or tags."""
    return EXPLICIT_LISTING_PORTFOLIOS.get(int(listing_id)) or portfolio_name_for_tags(tag_names)


def listing_record_display_name(listing) -> str | None:
    """Return the user-facing Hostaway listing name Brain should display."""
    if not listing:
        return None
    return (getattr(listing, "internal_listing_name", None) or getattr(listing, "name", None) or "").strip() or None


def sanitize_listing_reference_text(text: str | None, listing_names_by_id: dict[int, str | None]) -> str:
    """Replace visible listing-id references with internal listing names."""
    if not text:
        return text or ""
    result = str(text)
    for listing_id, listing_name in sorted(
        ((int(item_id), name) for item_id, name in (listing_names_by_id or {}).items() if item_id and name),
        key=lambda item: len(str(item[0])),
        reverse=True,
    ):
        escaped_id = re.escape(str(listing_id))
        result = re.sub(
            rf"\b[Ll]isting\s+(?:ID\s+|#)?{escaped_id}\b",
            lambda _match, name=listing_name: name,
            result,
        )
        result = re.sub(
            rf"\b[Pp]roperty\s+(?:ID\s+|#)?{escaped_id}\b",
            lambda _match, name=listing_name: name,
            result,
        )
        result = re.sub(rf"\b{escaped_id}\b", lambda _match, name=listing_name: name, result)
    return result


TODAY_GUEST_RISK_CATEGORIES = {"guest_experience", "review_risk", "checkin_checkout_risk"}


def build_today_operating_snapshot(
    *,
    active_signals: list[dict[str, Any]] | None = None,
    top_signals: list[dict[str, Any]],
    priority_actions: list[dict[str, Any]],
    memory_highlights: list[dict[str, Any]],
    open_loops: list[dict[str, Any]],
    booking_health_warnings: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Select one direct answer for each core Today operating question."""
    top_signals = top_signals or []
    priority_actions = priority_actions or []
    memory_highlights = memory_highlights or []
    open_loops = open_loops or []
    booking_health_warnings = booking_health_warnings or []
    action_today = priority_actions[0] if priority_actions else None
    guest_signal_pool = active_signals or (priority_actions + top_signals)
    guest_risk = _best_guest_review_risk_signal(guest_signal_pool, exclude_signal=action_today) or _best_guest_review_risk_signal(guest_signal_pool)
    unresolved_loop = _best_today_unresolved_loop(open_loops)
    booking_momentum = booking_health_warnings[0] if booking_health_warnings else None
    revenue_signal = _first_signal_by_category(
        priority_actions + top_signals,
        {"revenue_booking_health"},
        exclude_listing_keys={_listing_snapshot_identity(booking_momentum)} if booking_momentum else None,
    ) or _first_signal_by_category(priority_actions + top_signals, {"revenue_booking_health"})
    return [
        _snapshot_from_memory(
            "missed",
            "What did I miss?",
            memory_highlights[0] if memory_highlights else None,
        ),
        _snapshot_from_signal(
            "action_today",
            "What needs action today?",
            action_today,
            empty_title="No urgent action surfaced",
            empty_summary="Brain did not find a high-priority active signal in the current read.",
        ),
        _snapshot_from_signal(
            "guest_review_risk",
            "Which guest issue could become a bad review?",
            guest_risk,
            empty_title="No guest review risk surfaced",
            empty_summary="No active guest/review-risk signal is above the attention threshold right now.",
        ),
        _snapshot_from_open_loop(
            "unresolved_loop",
            "Which operational loop is still unresolved?",
            unresolved_loop,
        ),
        _snapshot_from_booking(
            "booking_momentum",
            "Which listing is losing booking momentum?",
            booking_momentum,
        ),
        _snapshot_from_roi(
            "highest_roi",
            "What action has the highest ROI?",
            revenue_signal,
            booking_health_warnings[0] if booking_health_warnings else None,
        ),
    ]


def _first_signal_by_category(
    signals: list[dict[str, Any]],
    categories: set[str],
    exclude_keys: set[tuple[str, Any] | None] | None = None,
    exclude_listing_keys: set[tuple[str, Any] | None] | None = None,
) -> dict[str, Any] | None:
    for signal in signals or []:
        if exclude_keys and _signal_snapshot_identity(signal) in exclude_keys:
            continue
        if exclude_listing_keys and _listing_snapshot_identity(signal) in exclude_listing_keys:
            continue
        if signal.get("category") in categories:
            return signal
    return None


def _best_today_unresolved_loop(open_loops: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Pick the first-screen unresolved-loop answer from the freshest actionable item."""
    candidates = [item for item in open_loops or [] if item]
    if not candidates:
        return None
    return sorted(candidates, key=_today_unresolved_loop_sort_key)[0]


def _today_unresolved_loop_sort_key(item: dict[str, Any]) -> tuple[Any, ...]:
    status_order = {"escalated": 0, "open": 0, "new": 0, "acknowledged": 1, "watching": 2}
    severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    latest = _open_loop_item_latest_at(item)
    return (
        status_order.get(item.get("status"), 4),
        _signal_last_seen_sort_value(latest),
        severity_order.get(item.get("severity"), 4),
        -float(item.get("rank_score") or 0.0),
        item.get("title") or "",
    )


def apply_signal_status_transition(signal: Any, status: str, *, now: datetime | None = None) -> dict[str, Any]:
    """Apply operator status semantics to a signal and return processing effects."""
    now = now or datetime.utcnow()
    old_status = getattr(signal, "status", None)
    new_status = normalize_signal_status(status)
    old_rank = float(getattr(signal, "rank_score", 0.0) or 0.0)
    effects: dict[str, Any] = {
        "old_status": old_status,
        "new_status": new_status,
        "changed": old_status != new_status,
        "closed": new_status in INACTIVE_SIGNAL_STATUSES,
        "active": new_status in ACTIVE_SIGNAL_STATUSES,
        "rank_score_changed": False,
    }

    signal.status = new_status
    signal.updated_at = now
    if new_status in INACTIVE_SIGNAL_STATUSES:
        signal.resolved_at = now
    elif old_status in INACTIVE_SIGNAL_STATUSES:
        signal.resolved_at = None

    if new_status == "escalated" and old_rank < OPERATOR_ESCALATED_MIN_RANK:
        signal.rank_score = OPERATOR_ESCALATED_MIN_RANK
        effects["rank_score_changed"] = True
        effects["old_rank_score"] = old_rank
        effects["new_rank_score"] = OPERATOR_ESCALATED_MIN_RANK

    return effects


def operator_open_loop_status_for_signal_status(status: str) -> str | None:
    """Return the open-loop status implied by an operator signal status."""
    normalized = normalize_signal_status(status)
    if normalized == "resolved":
        return "resolved"
    if normalized == "ignored":
        return "ignored"
    if normalized in ACTIVE_SIGNAL_STATUSES:
        return "open"
    return None


def operator_signal_status_note(status: str) -> str:
    """Describe how Brain processed an operator status update."""
    normalized = normalize_signal_status(status)
    notes = {
        "acknowledged": "Acknowledged in Brain; keep this visible until a next step or closure is confirmed.",
        "watching": "Watching in Brain; keep this visible for the next operating read.",
        "resolved": "Resolved in Brain by operator status update.",
        "ignored": "Ignored in Brain by operator status update.",
        "escalated": "Escalated in Brain; prioritize this before the next brief.",
        "new": "Reopened in Brain by operator status update.",
    }
    return notes.get(normalized, f"Marked {normalized} in Brain.")


def select_manager_todos(candidates: list[dict[str, Any]], *, limit: int = MANAGER_TODO_LIMIT) -> list[dict[str, Any]]:
    """Return a short deduped list of manager todos from candidate operating items."""
    limit = min(max(int(limit or MANAGER_TODO_LIMIT), 1), MANAGER_TODO_LIMIT)
    best_by_key: dict[tuple[Any, ...], dict[str, Any]] = {}
    for candidate in candidates or []:
        if not manager_todo_is_actionable(candidate):
            continue
        item = normalize_manager_todo(candidate)
        key = manager_todo_dedupe_key(item)
        existing = best_by_key.get(key)
        if not existing or manager_todo_sort_key(item) < manager_todo_sort_key(existing):
            best_by_key[key] = item
    return sorted(best_by_key.values(), key=manager_todo_sort_key)[:limit]


def manager_todo_is_actionable(item: dict[str, Any]) -> bool:
    status = str(item.get("status") or "").lower()
    if status in {"resolved", "ignored", "done", "closed", "completed"}:
        return False
    if item.get("confirmed_done") is True:
        return False
    return bool(item.get("title") and (item.get("suggested_action") or item.get("summary")))


def normalize_manager_todo(item: dict[str, Any]) -> dict[str, Any]:
    source_type = item.get("source_type") or "signal"
    source_id = item.get("source_id") or item.get("signal_id") or item.get("open_loop_id") or item.get("booking_health_analysis_id")
    todo = {
        "todo_id": f"{source_type}:{source_id or stable_hash(item)[:12]}",
        "source_type": source_type,
        "source_id": source_id,
        "signal_id": item.get("signal_id"),
        "open_loop_id": item.get("open_loop_id"),
        "booking_health_analysis_id": item.get("booking_health_analysis_id"),
        "portfolio_id": item.get("portfolio_id"),
        "portfolio_name": item.get("portfolio_name"),
        "listing_id": item.get("listing_id"),
        "listing_name": item.get("listing_name"),
        "category": item.get("category") or source_type,
        "severity": normalize_manager_todo_severity(item.get("severity")),
        "status": item.get("status") or "open",
        "title": item.get("title"),
        "summary": item.get("summary") or item.get("opinion") or item.get("booking_pattern"),
        "suggested_action": item.get("suggested_action") or item.get("suggested_next_step") or item.get("action"),
        "reason": item.get("reason") or manager_todo_reason(item),
        "priority_score": round(float(item.get("priority_score") or item.get("rank_score") or 0.0), 2),
        "confidence": item.get("confidence"),
        "last_seen_at": item.get("last_seen_at") or item.get("last_activity_at") or item.get("latest_evidence_at") or item.get("snapshot_date"),
        "evidence_count": item.get("evidence_count") or len(item.get("evidence") or []),
    }
    return todo


def normalize_manager_todo_severity(value: Any) -> str:
    normalized = str(value or "medium").strip().lower()
    if normalized == "watch":
        return "medium"
    return normalized if normalized in {"critical", "high", "medium", "low"} else "medium"


def manager_todo_reason(item: dict[str, Any]) -> str:
    source_type = item.get("source_type")
    category = item.get("category")
    if source_type == "open_loop":
        return "Talked about in team messages and not confirmed done."
    if source_type == "booking_health" or category == "revenue_booking_health":
        return "High-ROI booking/revenue action."
    if category in {"review_risk", "guest_experience", "checkin_checkout_risk"}:
        return "Guest-facing risk that needs manager follow-up."
    return "Active Brain signal that still needs a manager decision or follow-up."


def manager_todo_dedupe_key(item: dict[str, Any]) -> tuple[Any, ...]:
    if item.get("signal_id"):
        return ("signal", item.get("signal_id"))
    if item.get("open_loop_id"):
        return ("open_loop", item.get("open_loop_id"))
    if item.get("booking_health_analysis_id"):
        return ("booking_health", item.get("booking_health_analysis_id"))
    return (
        "context",
        item.get("portfolio_id"),
        item.get("listing_id"),
        item.get("category"),
        " ".join(str(item.get("title") or "").lower().split())[:120],
    )


def manager_todo_sort_key(item: dict[str, Any]) -> tuple[Any, ...]:
    source_order = {"open_loop": 0, "signal": 1, "booking_health": 2}
    status_order = {"escalated": 0, "open": 0, "new": 0, "acknowledged": 1, "watching": 2}
    severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    return (
        source_order.get(item.get("source_type"), 4),
        status_order.get(item.get("status"), 3),
        severity_order.get(item.get("severity"), 4),
        -float(item.get("priority_score") or 0.0),
        _signal_last_seen_sort_value(_parse_source_datetime(item.get("last_seen_at"))),
        item.get("title") or "",
    )


def _open_loop_item_latest_at(item: dict[str, Any]) -> datetime | None:
    evidence_values = [_parse_source_datetime(item.get("latest_evidence_at"))]
    for row in item.get("evidence") or []:
        if isinstance(row, dict):
            evidence_values.append(_parse_source_datetime(row.get("occurred_at")))
    evidence_values = [value for value in evidence_values if value]
    if evidence_values:
        activity_at = _parse_source_datetime(item.get("last_activity_at"))
        if activity_at:
            evidence_values.append(activity_at)
        return max(evidence_values)
    values = [
        _parse_source_datetime(item.get("last_activity_at")),
        _parse_source_datetime(item.get("last_seen_at")),
    ]
    values = [value for value in values if value]
    return max(values) if values else None


def _best_guest_review_risk_signal(
    signals: list[dict[str, Any]],
    *,
    exclude_signal: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Pick the guest-risk card from fresh, actionable evidence before stale post-review memory."""
    exclude_key = _signal_snapshot_identity(exclude_signal)
    candidates = []
    for signal in signals or []:
        if _signal_snapshot_identity(signal) == exclude_key:
            continue
        if signal.get("category") not in TODAY_GUEST_RISK_CATEGORIES:
            continue
        candidates.append(signal)
    if not candidates:
        return None
    return sorted(candidates, key=_guest_review_risk_sort_key)[0]


def _guest_review_risk_sort_key(signal: dict[str, Any]) -> tuple[Any, ...]:
    latest = _signal_item_latest_evidence_at(signal) or _parse_source_datetime(signal.get("last_seen_at"))
    age_bucket = _guest_risk_age_bucket(latest)
    source = signal.get("source") or ""
    evidence_types = {
        (row or {}).get("source_type")
        for row in signal.get("evidence") or []
        if isinstance(row, dict)
    }
    source_bucket = 0
    if "hostaway_message" in evidence_types or source == "openai_structured":
        source_bucket = 0
    elif source == "deterministic_guest_message":
        source_bucket = 1
    elif "guest_stay_memory" in evidence_types or source == "deterministic_guest_stay_memory":
        source_bucket = 2
    severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    recency_sort = -latest.timestamp() if latest else 0
    return (
        age_bucket,
        source_bucket,
        severity_order.get(signal.get("severity"), 4),
        -float(signal.get("rank_score") or 0.0),
        recency_sort,
    )


def _guest_risk_age_bucket(latest: datetime | None) -> int:
    if not latest:
        return 3
    age_days = (datetime.utcnow() - latest).total_seconds() / 86400
    if age_days <= 7:
        return 0
    if age_days <= 30:
        return 1
    return 2


def _signal_item_latest_evidence_at(signal: dict[str, Any]) -> datetime | None:
    dates = [
        _parse_source_datetime((row or {}).get("occurred_at"))
        for row in signal.get("evidence") or []
        if isinstance(row, dict)
    ]
    dates = [value for value in dates if value]
    return max(dates) if dates else None


def _signal_snapshot_identity(signal: dict[str, Any] | None) -> tuple[str, Any] | None:
    if not signal:
        return None
    if signal.get("signal_id") is not None:
        return ("signal", signal.get("signal_id"))
    return (
        "signal_fallback",
        signal.get("category"),
        signal.get("title"),
        signal.get("listing_id") or signal.get("listing_name"),
        signal.get("reservation_id"),
    )


def _listing_snapshot_identity(item: dict[str, Any] | None) -> tuple[str, Any] | None:
    if not item:
        return None
    if item.get("listing_id") is not None:
        return ("listing_id", item.get("listing_id"))
    listing_name = (item.get("listing_name") or "").strip().lower()
    if listing_name:
        return ("listing_name", listing_name)
    return None


def _snapshot_base(
    *,
    key: str,
    question: str,
    title: str,
    summary: str,
    suggested_action: str,
    answer: str | None = None,
    severity: str = "ok",
    status: str = "ok",
    source_type: str | None = None,
    source_id: Any = None,
    portfolio_name: str | None = None,
    listing_name: str | None = None,
    rank_score: Any = None,
) -> dict[str, Any]:
    return {
        "key": key,
        "question": question,
        "title": title,
        "answer": answer or title or summary or "Nothing urgent surfaced.",
        "summary": summary,
        "suggested_action": suggested_action,
        "severity": severity or "ok",
        "status": status or "ok",
        "source_type": source_type,
        "source_id": source_id,
        "portfolio_name": portfolio_name,
        "listing_name": listing_name,
        "rank_score": rank_score,
    }


def _empty_snapshot(key: str, question: str, title: str, summary: str) -> dict[str, Any]:
    return _snapshot_base(
        key=key,
        question=question,
        title=title,
        answer=title,
        summary=summary,
        suggested_action="No action needed unless new evidence appears.",
        severity="ok",
        status="ok",
        source_type="none",
    )


def _snapshot_from_signal(
    key: str,
    question: str,
    signal: dict[str, Any] | None,
    *,
    empty_title: str,
    empty_summary: str,
) -> dict[str, Any]:
    if not signal:
        return _empty_snapshot(key, question, empty_title, empty_summary)
    return _snapshot_base(
        key=key,
        question=question,
        title=signal.get("title") or empty_title,
        answer=_snapshot_signal_answer(key, signal, empty_title),
        summary=signal.get("summary") or signal.get("why_it_matters") or "",
        suggested_action=signal.get("suggested_action") or "Review the signal and confirm the next owner.",
        severity=signal.get("severity") or "medium",
        status=signal.get("status") or "new",
        source_type="signal",
        source_id=signal.get("signal_id"),
        portfolio_name=signal.get("portfolio_name"),
        listing_name=signal.get("listing_name"),
        rank_score=signal.get("rank_score"),
    )


def _snapshot_from_memory(key: str, question: str, memory: dict[str, Any] | None) -> dict[str, Any]:
    if not memory:
        return _empty_snapshot(
            key,
            question,
            "No major WhatsApp miss surfaced",
            "Brain did not find a recent WhatsApp thread above the attention threshold.",
        )
    return _snapshot_base(
        key=key,
        question=question,
        title=f"{memory.get('group_name') or 'Team WhatsApp'} needs a skim",
        answer=f"{memory.get('group_name') or 'Team WhatsApp'} is the thread most likely to contain missed operational context.",
        summary=memory.get("focus") or memory.get("summary") or "",
        suggested_action=memory.get("suggested_action") or "Review the WhatsApp thread and close the latest likely open item.",
        severity="high" if float(memory.get("attention_score") or 0.0) >= 160 else "medium",
        status=memory.get("status") or "watch",
        source_type="whatsapp_group_memory",
        source_id=memory.get("whatsapp_group_memory_id"),
        portfolio_name=memory.get("portfolio_name"),
        rank_score=memory.get("attention_score"),
    )


def _snapshot_from_open_loop(key: str, question: str, loop: dict[str, Any] | None) -> dict[str, Any]:
    if not loop:
        return _empty_snapshot(
            key,
            question,
            "No unresolved loop surfaced",
            "Brain did not find an active operational loop above the attention threshold.",
        )
    return _snapshot_base(
        key=key,
        question=question,
        title=loop.get("title") or "Open operational loop",
        answer=f"{loop.get('title') or 'An operational loop'} is still unresolved.",
        summary=loop.get("summary") or loop.get("last_known_update") or "",
        suggested_action=loop.get("suggested_next_step") or loop.get("action") or "Confirm owner, ETA, and closure in the source thread.",
        severity=loop.get("severity") or "medium",
        status=loop.get("status") or "open",
        source_type=loop.get("kind") or "open_loop",
        source_id=loop.get("open_loop_id") or loop.get("signal_id"),
        portfolio_name=loop.get("portfolio_name"),
        listing_name=loop.get("listing_name"),
        rank_score=loop.get("rank_score"),
    )


def _snapshot_from_booking(key: str, question: str, booking: dict[str, Any] | None) -> dict[str, Any]:
    if not booking:
        return _empty_snapshot(
            key,
            question,
            "No weak booking momentum surfaced",
            "Booking-health analysis did not find a listing that needs immediate revenue attention.",
        )
    action_items = [item for item in (booking.get("action_items") or []) if item]
    return _snapshot_base(
        key=key,
        question=question,
        title=booking.get("listing_name") or "Booking momentum watch",
        answer=f"{booking.get('listing_name') or 'This listing'} is the booking-momentum watch item.",
        summary=booking.get("booking_pattern") or booking.get("opinion") or "",
        suggested_action=action_items[0] if action_items else "Review booking pace, restrictions, pricing, and listing freshness.",
        severity=_booking_signal_severity(booking.get("severity")),
        status=booking.get("severity") or "watch",
        source_type="booking_health",
        source_id=booking.get("booking_health_analysis_id") or booking.get("listing_id"),
        portfolio_name=booking.get("portfolio_name"),
        listing_name=booking.get("listing_name"),
        rank_score=booking.get("confidence"),
    )


def _snapshot_from_roi(
    key: str,
    question: str,
    revenue_signal: dict[str, Any] | None,
    booking: dict[str, Any] | None,
) -> dict[str, Any]:
    if revenue_signal:
        item = _snapshot_from_signal(
            key,
            question,
            revenue_signal,
            empty_title="No ROI action surfaced",
            empty_summary="No booking/revenue action currently outranks guest and operations work.",
        )
        item["source_type"] = "revenue_signal"
        return item
    if booking:
        item = _snapshot_from_booking(key, question, booking)
        item["title"] = f"Revenue action for {item.get('listing_name') or item.get('title')}"
        item["answer"] = f"{item.get('listing_name') or 'This listing'} has the highest-ROI booking action right now."
        item["source_type"] = "booking_roi"
        return item
    return _empty_snapshot(
        key,
        question,
        "No high-ROI action surfaced",
        "Brain did not find a booking or pricing action that currently outranks guest and operations work.",
    )


def _snapshot_signal_answer(key: str, signal: dict[str, Any], fallback: str) -> str:
    title = signal.get("title") or fallback
    listing = signal.get("listing_name")
    if key == "action_today":
        if listing:
            return f"{listing} is the highest-priority action right now."
        return f"{title} needs action first."
    if key == "guest_review_risk":
        if listing:
            return f"{listing} is the guest/review risk to watch right now."
        return f"{title} is the guest/review risk to watch right now."
    if key == "highest_roi":
        if listing:
            return f"{listing} has the highest-ROI revenue action right now."
        return f"{title} is the highest-ROI action right now."
    return title


class BrainService:
    """Read/write service used by Brain web pages and APIs."""

    def __init__(self, user):
        self.user = user
        self.session = get_session()
        self.main_session = get_main_session(config.MAIN_DATABASE_PATH)
        self.portfolio_ids = accessible_portfolio_ids(self.session, user)
        self._listing_name_cache: dict[int, str] = {}

    def close(self):
        self.session.close()
        self.main_session.close()

    def get_today(self, portfolio_id: int | str | None = None) -> dict[str, Any]:
        """Return the Today command-center payload."""
        self.ensure_default_portfolio()
        scoped_portfolio_ids = self._portfolio_scope_ids(portfolio_id)
        signals_query = self._query_accessible_signals().options(joinedload(Signal.evidence)).filter(Signal.status.in_(ACTIVE_SIGNAL_STATUSES))
        signals_query = self._apply_portfolio_scope(signals_query, Signal, scoped_portfolio_ids)
        signals = signals_query.order_by(
            Signal.rank_score.desc(), Signal.last_seen_at.desc()
        ).limit(80).all()
        top_signals = select_operating_signals(signals, limit=10, per_category_limit=2)
        decisions = select_operating_signals([s for s in signals if is_decision_signal(s)], limit=5, per_category_limit=2)
        priority_actions = select_operating_signals(signals, limit=10, per_category_limit=3)
        booking_warnings = self.get_booking_health(limit=8, portfolio_id=portfolio_id)["items"]
        open_loops = self._combined_open_loop_items(limit=20, portfolio_ids=scoped_portfolio_ids)
        memory_highlights = self._today_memory_highlights(limit=6, portfolio_ids=scoped_portfolio_ids)
        active_signal_items = [self._signal_dict(s, include_evidence=True) for s in signals]
        top_signal_items = [self._signal_dict(s) for s in top_signals]
        decision_items = [self._signal_dict(s) for s in decisions]
        priority_action_items = [self._signal_dict(s) for s in priority_actions]
        operating_snapshot = build_today_operating_snapshot(
            active_signals=active_signal_items,
            top_signals=top_signal_items,
            priority_actions=priority_action_items,
            memory_highlights=memory_highlights,
            open_loops=open_loops,
            booking_health_warnings=booking_warnings,
        )
        portfolio_health = self.list_portfolios()["items"]
        if scoped_portfolio_ids is not None:
            scoped_set = set(scoped_portfolio_ids)
            portfolio_health = [row for row in portfolio_health if row.get("portfolio_id") in scoped_set]
        latest_briefs_query = self.session.query(DailyBrief)
        if self.portfolio_ids is not None:
            latest_briefs_query = latest_briefs_query.filter(DailyBrief.user_id == getattr(self.user, "user_id", None))
        latest_briefs = latest_briefs_query.order_by(DailyBrief.generated_at.desc()).limit(4).all()
        readiness = self.today_readiness()

        return {
            "generated_at": datetime.utcnow().isoformat(),
            "portfolio_id": int(portfolio_id) if str(portfolio_id or "").isdigit() else None,
            "readiness": readiness,
            "operating_snapshot": operating_snapshot,
            "top_signals": top_signal_items,
            "decisions": decision_items,
            "priority_actions": priority_action_items,
            "portfolio_health": portfolio_health,
            "booking_health_warnings": booking_warnings,
            "open_loops": open_loops,
            "memory_highlights": memory_highlights,
            "latest_briefs": [self._brief_dict(brief) for brief in latest_briefs],
        }

    def today_readiness(self) -> dict[str, Any]:
        """Return a compact trust/readiness summary for the operator Today page."""
        scheduled_reads = self._scheduled_read_health()
        brief_delivery = self._brief_delivery_health()
        hostaway = self._hostaway_health()
        pricelabs = self._pricelabs_health()
        whatsapp = self._whatsapp_health()
        return summarize_today_readiness(
            scheduled_reads=scheduled_reads,
            brief_delivery=brief_delivery,
            hostaway=hostaway,
            pricelabs=pricelabs,
            whatsapp=whatsapp,
        )

    def get_data_foundation(self, portfolio_id: int | str | None = None) -> dict[str, Any]:
        """Return source and fact coverage for future Brain product surfaces."""
        scoped_portfolio_ids = self._portfolio_scope_ids(portfolio_id)
        active_query = self._query_accessible_business_facts(scoped_portfolio_ids).filter(BusinessFact.status == "active")
        source_counts = dict(
            active_query.with_entities(BusinessFact.source_key, func.count(BusinessFact.business_fact_id))
            .group_by(BusinessFact.source_key)
            .all()
        )
        fact_type_counts = [
            {"fact_type": fact_type, "count": int(count or 0)}
            for fact_type, count in active_query.with_entities(BusinessFact.fact_type, func.count(BusinessFact.business_fact_id))
            .group_by(BusinessFact.fact_type)
            .order_by(func.count(BusinessFact.business_fact_id).desc(), BusinessFact.fact_type)
            .all()
        ]
        metric_query = self._query_accessible_business_metrics(scoped_portfolio_ids).filter(BusinessMetricSnapshot.status != "withdrawn")
        metric_name_counts = [
            {"metric_name": metric_name, "count": int(count or 0)}
            for metric_name, count in metric_query.with_entities(BusinessMetricSnapshot.metric_name, func.count(BusinessMetricSnapshot.business_metric_snapshot_id))
            .group_by(BusinessMetricSnapshot.metric_name)
            .order_by(func.count(BusinessMetricSnapshot.business_metric_snapshot_id).desc(), BusinessMetricSnapshot.metric_name)
            .all()
        ]
        latest_facts = active_query.order_by(
            BusinessFact.occurred_at.desc().nullslast(),
            BusinessFact.updated_at.desc(),
        ).limit(12).all()
        latest_metrics = metric_query.order_by(
            BusinessMetricSnapshot.metric_date.desc(),
            BusinessMetricSnapshot.updated_at.desc(),
        ).limit(12).all()
        source_rows = self._data_foundation_source_rows(source_counts)
        latest_completed_at = self.session.query(func.max(DataIngestionRun.completed_at)).filter(
            DataIngestionRun.status == "completed"
        ).scalar()
        return {
            "generated_at": datetime.utcnow().isoformat(),
            "portfolio_id": int(portfolio_id) if str(portfolio_id or "").isdigit() else None,
            "health": summarize_data_aggregator_health(
                source_rows,
                fact_count=sum(int(value or 0) for value in source_counts.values()),
                latest_completed_at=latest_completed_at,
            ),
            "fact_type_counts": fact_type_counts,
            "metric_name_counts": metric_name_counts,
            "latest_facts": [self._business_fact_dict(fact) for fact in latest_facts],
            "latest_metrics": [self._business_metric_dict(metric) for metric in latest_metrics],
        }

    def data_foundation_audit(self, portfolio_id: int | str | None = None) -> dict[str, Any]:
        """Return a strict coverage audit for the data foundation."""
        scoped_portfolio_ids = self._portfolio_scope_ids(portfolio_id)
        facts_query = self._query_accessible_business_facts(scoped_portfolio_ids).filter(BusinessFact.status == "active")
        metrics_query = self._query_accessible_business_metrics(scoped_portfolio_ids).filter(BusinessMetricSnapshot.status != "withdrawn")
        fact_counts_by_type = dict(
            facts_query.with_entities(BusinessFact.fact_type, func.count(BusinessFact.business_fact_id))
            .group_by(BusinessFact.fact_type)
            .all()
        )
        fact_counts_by_source = dict(
            facts_query.with_entities(BusinessFact.source_key, func.count(BusinessFact.business_fact_id))
            .group_by(BusinessFact.source_key)
            .all()
        )
        metric_counts_by_name = dict(
            metrics_query.with_entities(BusinessMetricSnapshot.metric_name, func.count(BusinessMetricSnapshot.business_metric_snapshot_id))
            .group_by(BusinessMetricSnapshot.metric_name)
            .all()
        )
        source_rows = self._data_foundation_source_rows(fact_counts_by_source)
        source_rows_by_key = {row.get("source_key"): row for row in source_rows}
        mapped_listing_ids = self._data_foundation_listing_ids(scoped_portfolio_ids)
        listing_coverage = self._data_foundation_listing_coverage(scoped_portfolio_ids, mapped_listing_ids)
        source_freshness = self._data_foundation_source_freshness(scoped_portfolio_ids)

        gaps: list[dict[str, Any]] = []
        for definition in DEFAULT_SOURCE_DEFINITIONS:
            source = source_rows_by_key.get(definition.source_key)
            if not source:
                gaps.append({"severity": "critical", "key": f"source:{definition.source_key}", "message": f"{definition.name} is not registered."})
                continue
            if source.get("status") in {"missing", "error"}:
                gaps.append({"severity": "critical", "key": f"source:{definition.source_key}", "message": source.get("message") or f"{definition.name} is missing."})
            elif source.get("status") == "stale":
                gaps.append({"severity": "warning", "key": f"source:{definition.source_key}", "message": source.get("message") or f"{definition.name} is stale."})

        for source_key, fact_types in FOUNDATION_REQUIRED_FACT_TYPES_BY_SOURCE.items():
            for fact_type in fact_types:
                count = int(fact_counts_by_type.get(fact_type, 0) or 0)
                if count <= 0:
                    gaps.append(
                        {
                            "severity": "error",
                            "key": f"fact:{fact_type}",
                            "source_key": source_key,
                            "message": f"Required fact type {fact_type} has no active facts.",
                        }
                    )

        for metric_name in FOUNDATION_REQUIRED_METRIC_NAMES:
            count = int(metric_counts_by_name.get(metric_name, 0) or 0)
            if count <= 0:
                gaps.append(
                    {
                        "severity": "warning",
                        "key": f"metric:{metric_name}",
                        "message": f"Required metric {metric_name} has no active snapshots.",
                    }
                )

        for row in listing_coverage:
            if row["expected_listing_count"] <= 0:
                continue
            if row["coverage_pct"] < row["minimum_pct"]:
                gaps.append(
                    {
                        "severity": "error",
                        "key": f"{row['kind']}:{row['name']}:listing_coverage",
                        "message": f"{row['name']} covers {row['coverage_pct']}% of mapped listings; target is {row['minimum_pct']}%.",
                        "coverage": row,
                    }
                )

        for row in source_freshness:
            if row["status"] == "ok":
                continue
            gaps.append(
                {
                    "severity": "warning",
                    "key": f"freshness:{row['source_key']}:{row['name']}",
                    "message": row["message"],
                    "freshness": row,
                }
            )

        summary = summarize_foundation_audit(
            gaps,
            source_count=len(source_rows),
            fact_count=sum(int(value or 0) for value in fact_counts_by_type.values()),
            metric_count=sum(int(value or 0) for value in metric_counts_by_name.values()),
        )
        return {
            "generated_at": datetime.utcnow().isoformat(),
            "portfolio_id": int(portfolio_id) if str(portfolio_id or "").isdigit() else None,
            "summary": summary,
            "sources": source_rows,
            "fact_type_counts": [
                {"fact_type": key, "count": int(value or 0)}
                for key, value in sorted(fact_counts_by_type.items())
            ],
            "metric_name_counts": [
                {"metric_name": key, "count": int(value or 0)}
                for key, value in sorted(metric_counts_by_name.items())
            ],
            "listing_coverage": listing_coverage,
            "source_freshness": source_freshness,
            "mapped_listing_count": len(mapped_listing_ids),
        }

    def list_business_facts(self, filters: dict[str, Any] | None = None) -> dict[str, Any]:
        """Return normalized business facts with provenance and portfolio scoping."""
        filters = filters or {}
        scoped_portfolio_ids = self._portfolio_scope_ids(filters.get("portfolio_id"))
        query = self._query_accessible_business_facts(scoped_portfolio_ids)
        status = str(filters.get("status") or "active").strip().lower()
        if status and status != "all":
            query = query.filter(BusinessFact.status == status)
        for field_name, column in (
            ("source_key", BusinessFact.source_key),
            ("fact_type", BusinessFact.fact_type),
        ):
            values = _filter_values(filters.get(field_name))
            if values:
                query = query.filter(column.in_(values))
        for field_name, column in (
            ("listing_id", BusinessFact.listing_id),
            ("reservation_id", BusinessFact.reservation_id),
            ("guest_id", BusinessFact.guest_id),
        ):
            value = _coerce_int(filters.get(field_name))
            if value is not None:
                query = query.filter(column == value)
        occurred_from = _parse_source_datetime(filters.get("occurred_from"))
        occurred_to = _parse_source_datetime(filters.get("occurred_to"))
        if occurred_from:
            query = query.filter(BusinessFact.occurred_at >= occurred_from)
        if occurred_to:
            query = query.filter(BusinessFact.occurred_at <= occurred_to)
        limit = min(max(_coerce_int(filters.get("limit")) or 100, 1), 500)
        rows = query.order_by(
            BusinessFact.occurred_at.desc().nullslast(),
            BusinessFact.updated_at.desc(),
            BusinessFact.business_fact_id.desc(),
        ).limit(limit).all()
        return {
            "generated_at": datetime.utcnow().isoformat(),
            "limit": limit,
            "items": [self._business_fact_dict(row) for row in rows],
        }

    def list_business_metrics(self, filters: dict[str, Any] | None = None) -> dict[str, Any]:
        """Return decision-ready metric snapshots with portfolio scoping."""
        filters = filters or {}
        scoped_portfolio_ids = self._portfolio_scope_ids(filters.get("portfolio_id"))
        query = self._query_accessible_business_metrics(scoped_portfolio_ids)
        status = str(filters.get("status") or "").strip().lower()
        if status and status != "all":
            query = query.filter(BusinessMetricSnapshot.status == status)
        else:
            query = query.filter(BusinessMetricSnapshot.status != "withdrawn")
        for field_name, column in (
            ("metric_name", BusinessMetricSnapshot.metric_name),
            ("category", BusinessMetricSnapshot.category),
            ("grain", BusinessMetricSnapshot.grain),
        ):
            values = _filter_values(filters.get(field_name))
            if values:
                query = query.filter(column.in_(values))
        listing_id = _coerce_int(filters.get("listing_id"))
        if listing_id is not None:
            query = query.filter(BusinessMetricSnapshot.listing_id == listing_id)
        metric_date = _parse_source_date(filters.get("metric_date"))
        metric_from = _parse_source_date(filters.get("metric_from"))
        metric_to = _parse_source_date(filters.get("metric_to"))
        if metric_date:
            query = query.filter(BusinessMetricSnapshot.metric_date == metric_date)
        if metric_from:
            query = query.filter(BusinessMetricSnapshot.metric_date >= metric_from)
        if metric_to:
            query = query.filter(BusinessMetricSnapshot.metric_date <= metric_to)
        horizon_days = _coerce_int(filters.get("horizon_days"))
        if horizon_days is not None:
            query = query.filter(BusinessMetricSnapshot.horizon_days == horizon_days)
        limit = min(max(_coerce_int(filters.get("limit")) or 100, 1), 500)
        rows = query.order_by(
            BusinessMetricSnapshot.metric_date.desc(),
            BusinessMetricSnapshot.updated_at.desc(),
            BusinessMetricSnapshot.business_metric_snapshot_id.desc(),
        ).limit(limit).all()
        return {
            "generated_at": datetime.utcnow().isoformat(),
            "limit": limit,
            "items": [self._business_metric_dict(row) for row in rows],
        }

    def list_portfolios(self) -> dict[str, Any]:
        """List accessible portfolios with compact health summaries."""
        self.ensure_default_portfolio()
        query = self.session.query(Portfolio).filter(Portfolio.is_active == True)  # noqa: E712
        if self.portfolio_ids is not None:
            query = query.filter(Portfolio.portfolio_id.in_(self.portfolio_ids or [-1]))
        portfolios = query.order_by(Portfolio.sort_order, Portfolio.name).all()
        listing_counts = dict(
            self.session.query(PortfolioListing.portfolio_id, func.count(PortfolioListing.listing_id))
            .group_by(PortfolioListing.portfolio_id)
            .all()
        )
        open_loop_counts = dict(
            self.session.query(OpenLoop.portfolio_id, func.count(OpenLoop.open_loop_id))
            .filter(OpenLoop.status == "open")
            .group_by(OpenLoop.portfolio_id)
            .all()
        )
        items = []
        for portfolio in portfolios:
            active_signals = self.session.query(Signal).filter(
                Signal.portfolio_id == portfolio.portfolio_id,
                Signal.status.in_(ACTIVE_SIGNAL_STATUSES),
            ).order_by(Signal.rank_score.desc()).limit(25).all()
            top_signal = active_signals[0] if active_signals else None
            top_booking = self._top_portfolio_booking_health(portfolio.portfolio_id)
            active_signal_count = len(active_signals)
            critical_signal_count = len([signal for signal in active_signals if signal.severity == "critical"])
            high_signal_count = len([signal for signal in active_signals if signal.severity == "high"])
            open_loop_count = open_loop_counts.get(portfolio.portfolio_id, 0)
            portfolio_status = portfolio_operating_status(
                active_signals=active_signals,
                open_loop_count=open_loop_count,
                booking_health=top_booking,
            )
            items.append(
                {
                    "portfolio_id": portfolio.portfolio_id,
                    "name": portfolio.name,
                    "description": portfolio.description,
                    "status": portfolio_status,
                    "property_count": listing_counts.get(portfolio.portfolio_id, 0),
                    "top_operational_signal": self._sanitize_listing_text(top_signal.title, top_signal.listing_id) if top_signal else None,
                    "top_revenue_signal": booking_health_label(top_booking) if top_booking else None,
                    "open_loop_count": open_loop_count,
                    "active_signal_count": active_signal_count,
                    "critical_signal_count": critical_signal_count,
                    "high_signal_count": high_signal_count,
                    "booking_health": booking_health_status(top_booking),
                    "suggested_action": self._sanitize_listing_text(top_signal.suggested_action, top_signal.listing_id) if top_signal else "No urgent action.",
                }
            )
        return {"items": items}

    def _top_portfolio_booking_health(self, portfolio_id: int) -> BookingHealthAnalysis | BookingHealthSnapshot | None:
        latest_analysis_date = self.session.query(func.max(BookingHealthAnalysis.snapshot_date)).scalar()
        if latest_analysis_date:
            analysis = self.session.query(BookingHealthAnalysis).filter(
                BookingHealthAnalysis.portfolio_id == portfolio_id,
                BookingHealthAnalysis.snapshot_date == latest_analysis_date,
                BookingHealthAnalysis.severity.in_(("critical", "high", "watch")),
            ).order_by(
                case(
                    (BookingHealthAnalysis.severity == "critical", 0),
                    (BookingHealthAnalysis.severity == "high", 1),
                    (BookingHealthAnalysis.severity == "watch", 2),
                    else_=3,
                ),
                BookingHealthAnalysis.confidence.desc(),
            ).first()
            if analysis:
                return analysis
        return self.session.query(BookingHealthSnapshot).filter(
            BookingHealthSnapshot.portfolio_id == portfolio_id,
            BookingHealthSnapshot.diagnosis != "healthy",
        ).order_by(BookingHealthSnapshot.snapshot_date.desc(), BookingHealthSnapshot.confidence.desc()).first()

    def get_portfolio_detail(self, portfolio_id: int) -> dict[str, Any]:
        """Return a portfolio detail payload."""
        portfolio = self._get_accessible_portfolio(portfolio_id)
        listing_rows = self.session.query(PortfolioListing).filter(
            PortfolioListing.portfolio_id == portfolio_id
        ).order_by(PortfolioListing.listing_id).all()
        listing_ids = [row.listing_id for row in listing_rows]
        listings = []
        if listing_ids:
            listing_map = {
                listing.listing_id: listing
                for listing in self.main_session.query(Listing).filter(Listing.listing_id.in_(listing_ids)).all()
            }
            for row in listing_rows:
                listing = listing_map.get(row.listing_id)
                if listing:
                    listings.append(
                        {
                            "listing_id": listing.listing_id,
                            "name": row.listing_name_override or listing_record_display_name(listing) or UNKNOWN_LISTING_NAME,
                            "city": listing.city,
                            "status": listing.status,
                        }
                    )
        signals = self._query_accessible_signals().filter(
            Signal.portfolio_id == portfolio_id,
            Signal.status.in_(ACTIVE_SIGNAL_STATUSES),
        ).order_by(
            Signal.rank_score.desc(), Signal.last_seen_at.desc()
        ).limit(20).all()
        open_loops = self._combined_open_loop_items(limit=20, portfolio_ids=[portfolio_id])
        booking = self.session.query(BookingHealthSnapshot).filter(
            BookingHealthSnapshot.portfolio_id == portfolio_id
        ).order_by(BookingHealthSnapshot.snapshot_date.desc(), BookingHealthSnapshot.confidence.desc()).limit(20).all()
        return {
            "portfolio": {
                "portfolio_id": portfolio.portfolio_id,
                "name": portfolio.name,
                "description": portfolio.description,
                "status": portfolio.status,
            },
            "listings": listings,
            "top_signals": [self._signal_dict(signal) for signal in signals],
            "open_loops": open_loops,
            "booking_health": [self._booking_dict(item) for item in booking],
        }

    def list_signals(self, filters: dict[str, Any] | None = None) -> dict[str, Any]:
        """List signals with lightweight filtering."""
        filters = filters or {}
        query = self._query_accessible_signals()
        if filters.get("portfolio_id"):
            query = query.filter(Signal.portfolio_id == int(filters["portfolio_id"]))
        if filters.get("category"):
            query = query.filter(Signal.category == normalize_signal_category(filters["category"]))
        status_filter = filters.get("status")
        if not status_filter:
            status_filter = "active"
        if status_filter == "active":
            query = query.filter(Signal.status.in_(ACTIVE_SIGNAL_STATUSES))
        elif status_filter and status_filter != "all":
            query = query.filter(Signal.status == normalize_signal_status(filters["status"]))
        if filters.get("severity"):
            query = query.filter(Signal.severity == normalize_signal_severity(filters["severity"]))
        if filters.get("audience"):
            audience = normalize_signal_audience(filters["audience"])
            if audience == "revenue":
                query = query.filter(Signal.category == "revenue_booking_health")
            else:
                query = query.filter(Signal.category != "revenue_booking_health")
        signals = query.order_by(Signal.rank_score.desc(), Signal.last_seen_at.desc()).limit(int(filters.get("limit", 100))).all()
        return {"items": [self._signal_dict(signal, include_evidence=True) for signal in signals]}

    def update_signal_status(self, signal_id: int, status: str) -> dict[str, Any]:
        """Update a signal status and process linked Brain workflow state."""
        signal = self._query_accessible_signals().filter(Signal.signal_id == signal_id).first()
        if not signal:
            raise ValueError("Signal not found")
        now = datetime.utcnow()
        effects = apply_signal_status_transition(signal, status, now=now)
        open_loop_effects = self._process_signal_status_open_loops(signal, effects["new_status"], now=now)
        effects.update(open_loop_effects)
        self._audit("signal_status_update", "signal", signal.signal_id, effects)
        self.session.commit()
        result = self._signal_dict(signal, include_evidence=True)
        result["processing_effects"] = effects
        return result

    def _process_signal_status_open_loops(self, signal: Signal, status: str, *, now: datetime) -> dict[str, Any]:
        """Synchronize open-loop rows with an operator signal status change."""
        target_loop_status = operator_open_loop_status_for_signal_status(status)
        effects: dict[str, Any] = {
            "open_loop_status": target_loop_status,
            "open_loops_closed": 0,
            "open_loops_reopened": 0,
            "open_loops_created": 0,
        }
        if not target_loop_status:
            return effects

        loops = self.session.query(OpenLoop).filter(OpenLoop.signal_id == signal.signal_id).all()
        if target_loop_status == "open" and signal.category == "operational_open_loop" and not loops:
            loop = OpenLoop(signal_id=signal.signal_id)
            self.session.add(loop)
            loops = [loop]
            effects["open_loops_created"] = 1

        for loop in loops:
            previous_loop_status = loop.status
            if target_loop_status in {"resolved", "ignored"}:
                loop.status = target_loop_status
                loop.resolved_at = now
                loop.last_known_update = operator_signal_status_note(status)
                loop.last_activity_at = now
                if previous_loop_status == "open":
                    effects["open_loops_closed"] += 1
            elif signal.category == "operational_open_loop":
                loop.status = "open"
                loop.resolved_at = None
                if previous_loop_status != "open":
                    effects["open_loops_reopened"] += 1
                self._hydrate_open_loop_from_signal(loop, signal, status=status, now=now)
            loop.updated_at = now

        return effects

    def _hydrate_open_loop_from_signal(self, loop: OpenLoop, signal: Signal, *, status: str, now: datetime):
        """Ensure an active operational signal has an actionable open-loop row."""
        loop.portfolio_id = signal.portfolio_id
        loop.listing_id = signal.listing_id
        loop.reservation_id = signal.reservation_id
        loop.title = signal.title
        loop.summary = signal.summary
        if not loop.involved_people:
            loop.involved_people = "Team"
        if not loop.last_known_update or status == "escalated":
            loop.last_known_update = operator_signal_status_note(status)
        loop.suggested_next_step = signal.suggested_action
        loop.last_activity_at = signal.last_seen_at or now
        loop.confidence = signal.confidence or 0.5

    def get_booking_health(self, limit: int = 50, portfolio_id: int | str | None = None) -> dict[str, Any]:
        """Return one booking-health analysis card per property."""
        scoped_portfolio_ids = self._portfolio_scope_ids(portfolio_id)
        latest_analysis_date = self.session.query(func.max(BookingHealthAnalysis.snapshot_date)).scalar()
        if latest_analysis_date:
            query = self.session.query(BookingHealthAnalysis).filter(
                BookingHealthAnalysis.portfolio_id.isnot(None),
                BookingHealthAnalysis.snapshot_date == latest_analysis_date,
            )
            query = filter_portfolio_query(query, BookingHealthAnalysis, self.portfolio_ids)
            query = self._apply_portfolio_scope(query, BookingHealthAnalysis, scoped_portfolio_ids)
            analyses = sorted(query.all(), key=_booking_analysis_sort_key)[:limit]
            return {"items": [self._booking_analysis_dict(analysis) for analysis in analyses]}

        return {"items": self._booking_snapshot_fallback_cards(limit, portfolio_ids=scoped_portfolio_ids)}

    def list_open_loops(self, limit: int = 100, portfolio_id: int | str | None = None) -> dict[str, Any]:
        """Return accessible open loops."""
        return {"items": self._combined_open_loop_items(limit=limit, portfolio_ids=self._portfolio_scope_ids(portfolio_id))}

    def list_manager_todos(self, limit: int = MANAGER_TODO_LIMIT, portfolio_id: int | str | None = None) -> dict[str, Any]:
        """Return a short manager todo list from open loops, signals, and high-ROI actions."""
        scoped_portfolio_ids = self._portfolio_scope_ids(portfolio_id)
        candidates: list[dict[str, Any]] = []

        candidates.extend(
            self._manager_todo_from_open_loop(item)
            for item in self._combined_open_loop_items(limit=40, portfolio_ids=scoped_portfolio_ids)
        )

        signals_query = self._query_accessible_signals().options(joinedload(Signal.evidence)).filter(
            Signal.status.in_(ACTIVE_SIGNAL_STATUSES)
        )
        signals_query = self._apply_portfolio_scope(signals_query, Signal, scoped_portfolio_ids)
        signals = signals_query.order_by(Signal.rank_score.desc(), Signal.last_seen_at.desc()).limit(50).all()
        candidates.extend(self._manager_todo_from_signal(self._signal_dict(signal, include_evidence=True)) for signal in signals)

        for item in self.get_booking_health(limit=40, portfolio_id=portfolio_id)["items"]:
            if item.get("severity") == "healthy":
                continue
            candidates.append(self._manager_todo_from_booking_health(item))

        items = select_manager_todos(candidates, limit=limit)
        return {
            "generated_at": datetime.utcnow().isoformat(),
            "portfolio_id": int(portfolio_id) if str(portfolio_id or "").isdigit() else None,
            "limit": min(max(int(limit or MANAGER_TODO_LIMIT), 1), MANAGER_TODO_LIMIT),
            "count": len(items),
            "items": items,
        }

    def _manager_todo_from_open_loop(self, item: dict[str, Any]) -> dict[str, Any]:
        return {
            **item,
            "source_type": "open_loop",
            "source_id": item.get("open_loop_id") or item.get("signal_id"),
            "category": item.get("category") or "operational_open_loop",
            "status": item.get("status") or "open",
            "suggested_action": item.get("suggested_next_step") or item.get("action"),
            "priority_score": item.get("rank_score") or 0,
            "reason": "Talked about in team messages and not confirmed done.",
        }

    def _manager_todo_from_signal(self, item: dict[str, Any]) -> dict[str, Any]:
        return {
            **item,
            "source_type": "signal",
            "source_id": item.get("signal_id"),
            "priority_score": item.get("rank_score") or 0,
            "evidence_count": len(item.get("evidence") or []),
            "reason": manager_todo_reason(item),
        }

    def _manager_todo_from_booking_health(self, item: dict[str, Any]) -> dict[str, Any]:
        action_items = item.get("action_items") or []
        return {
            **item,
            "source_type": "booking_health",
            "source_id": item.get("booking_health_analysis_id") or item.get("listing_id"),
            "category": "revenue_booking_health",
            "status": "open",
            "title": f"Booking ROI action for {item.get('listing_name') or 'property'}",
            "summary": item.get("opinion") or item.get("booking_pattern"),
            "suggested_action": action_items[0] if action_items else item.get("booking_pattern") or item.get("opinion"),
            "priority_score": item.get("priority_score") or 0,
            "reason": "High-ROI booking/revenue action.",
        }

    def ask(self, question: str, portfolio_id: int | str | None = None) -> dict[str, Any]:
        """Answer a natural-language question using stored evidence."""
        question = (question or "").strip()
        if not question:
            raise ValueError("Question is required")
        original_portfolio_ids = self.portfolio_ids
        if portfolio_id:
            self.portfolio_ids = self._portfolio_scope_ids(portfolio_id)
        try:
            return self._ask_scoped(question)
        finally:
            self.portfolio_ids = original_portfolio_ids

    def _ask_scoped(self, question: str) -> dict[str, Any]:
        core_response = self._ask_core_operating_question_response(question)
        if core_response:
            return core_response
        scope_response = self._ask_explicit_unknown_scope_response(question)
        if scope_response:
            return scope_response
        evidence_rows = self._ask_evidence_rows(question)
        evidence = [self._evidence_dict(row) for row in evidence_rows]
        evidence_listing_names = self._listing_names_for_ids(row.listing_id for row in evidence_rows)
        if not evidence:
            return ask_insufficient_response(
                prompt_version=ASK_PROMPT_VERSION,
                reason="No stored evidence matched the question.",
                missing_data="Try naming a portfolio, WhatsApp group, listing, guest issue, or time window.",
        )
        try:
            ai = BrainAIClient()
            result = ai.answer_question(ask_model_question(question), evidence)
            citations = self._ask_citations_with_context(result.get("citations", []), evidence)
            answer = sanitize_listing_reference_text(result.get("answer", ""), evidence_listing_names)
            if not ask_response_is_evidence_backed(answer, citations):
                return ask_insufficient_response(
                    prompt_version=ASK_PROMPT_VERSION,
                    reason="Relevant records were found, but the answer did not cite stored evidence.",
                    missing_data="Ask again with a narrower portfolio, group, listing, or issue so Brain can cite the exact records.",
                    evidence_count=len(evidence),
                )
            return {
                "answer": answer,
                "confidence": max(0.0, min(float(result.get("confidence", 0.0) or 0.0), 1.0)),
                "citations": [
                    {
                        **citation,
                        "reason": sanitize_listing_reference_text(citation.get("reason", ""), evidence_listing_names),
                    }
                    for citation in citations
                ],
                "insufficient_evidence": False,
                "evidence_count": len(evidence),
                "prompt_version": ASK_PROMPT_VERSION,
            }
        except Exception as exc:
            logger.warning("Ask Brain OpenAI answer failed: %s", exc)
            citation = evidence[0]
            return {
                "answer": sanitize_listing_reference_text(
                    f"I found relevant evidence but could not complete model reasoning. Most relevant note: {citation['summary']}",
                    evidence_listing_names,
                ),
                "confidence": 0.35,
                "citations": self._ask_citations_with_context(
                    [{"evidence_id": citation["evidence_id"], "reason": "Most relevant stored evidence"}],
                    evidence,
                ),
                "prompt_version": ASK_PROMPT_VERSION,
            }

    def _ask_core_operating_question_response(self, question: str) -> dict[str, Any] | None:
        """Answer exact Today snapshot questions from the same surfaced card."""
        snapshot_key = ask_core_operating_question_key(question)
        if not snapshot_key:
            return None
        today = self.get_today()
        item = next((row for row in today.get("operating_snapshot") or [] if row.get("key") == snapshot_key), None)
        if not item:
            return None
        evidence_rows = self._ask_operating_snapshot_evidence_rows(item)
        evidence = [self._evidence_dict(row) for row in evidence_rows]
        if not evidence:
            return ask_insufficient_response(
                prompt_version=ASK_PROMPT_VERSION,
                reason="Today surfaced an answer, but no stored evidence was available to cite.",
                missing_data="Run Brain again or ask about a specific listing, group, or signal.",
            )
        evidence_listing_names = self._listing_names_for_ids(row.listing_id for row in evidence_rows)
        citations = self._ask_citations_with_context(
            [
                {
                    "evidence_id": row.evidence_id,
                    "reason": f"Evidence behind today's '{item.get('question') or question}' card.",
                }
                for row in evidence_rows[:5]
                if row.evidence_id
            ],
            evidence,
        )
        answer = core_operating_answer_text(item)
        return {
            "answer": sanitize_listing_reference_text(answer, evidence_listing_names),
            "confidence": 0.95 if citations else 0.4,
            "citations": citations,
            "insufficient_evidence": False,
            "evidence_count": len(evidence),
            "prompt_version": ASK_PROMPT_VERSION,
            "operating_snapshot_key": snapshot_key,
        }

    def _ask_operating_snapshot_evidence_rows(self, item: dict[str, Any], limit: int = 6) -> list[SignalEvidence]:
        source_type = item.get("source_type")
        source_id = item.get("source_id")
        if source_type in {"signal", "revenue_signal"}:
            return self._ask_signal_evidence_by_signal_id(source_id, limit=limit)
        if source_type == "open_loop":
            try:
                open_loop_id = int(source_id)
            except (TypeError, ValueError):
                open_loop_id = None
            if open_loop_id:
                query = self._query_accessible_open_loops().filter(OpenLoop.open_loop_id == open_loop_id)
                loop = query.first()
                if loop and loop.signal_id:
                    return self._ask_signal_evidence_by_signal_id(loop.signal_id, limit=limit)
        if source_type in {"booking_health", "booking_roi"}:
            rows = self._ask_booking_health_evidence_rows(item, limit=limit)
            if rows:
                return rows
        if source_type == "whatsapp_group_memory":
            return self._ask_whatsapp_group_memory_core_evidence_rows(source_id, limit=limit)
        return []

    def _ask_signal_evidence_by_signal_id(self, signal_id: Any, limit: int = 6) -> list[SignalEvidence]:
        try:
            signal_id_int = int(signal_id)
        except (TypeError, ValueError):
            return []
        signal = self._query_accessible_signals().filter(Signal.signal_id == signal_id_int).first()
        if not signal:
            return []
        return sorted(signal.evidence, key=lambda row: signal_evidence_sort_key(signal.category, row))[:limit]

    def _ask_booking_health_evidence_rows(self, item: dict[str, Any], limit: int = 6) -> list[SignalEvidence]:
        source_id = item.get("source_id")
        rows: list[SignalEvidence] = []
        if source_id is not None:
            rows = self.session.query(SignalEvidence).join(Signal, Signal.signal_id == SignalEvidence.signal_id).filter(
                Signal.category == "revenue_booking_health",
                SignalEvidence.source_type == "booking_health_analysis",
                SignalEvidence.source_id == str(source_id),
            ).all()
            if self.portfolio_ids is not None:
                rows = [row for row in rows if row.signal and row.signal.portfolio_id in (self.portfolio_ids or [])]
        if rows:
            signal_id = rows[0].signal_id
            return self._ask_signal_evidence_by_signal_id(signal_id, limit=limit)
        listing_name = item.get("listing_name")
        if not listing_name:
            return []
        signals = self._query_accessible_signals().filter(
            Signal.category == "revenue_booking_health",
            Signal.status.in_(ACTIVE_SIGNAL_STATUSES),
        ).order_by(Signal.rank_score.desc()).limit(80).all()
        for signal in signals:
            if (self._listing_display_name(signal.listing_id) or "").strip().lower() == str(listing_name).strip().lower():
                return self._ask_signal_evidence_by_signal_id(signal.signal_id, limit=limit)
        return []

    def _ask_whatsapp_group_memory_evidence_row(self, source_id: Any) -> SignalEvidence | None:
        memory = self._ask_whatsapp_group_memory_row(source_id)
        if not memory:
            return None
        evidence = self._get_or_create_evidence(
            source_type="whatsapp_group_memory",
            source_id=str(memory.whatsapp_group_memory_id),
            occurred_at=memory.last_message_at or memory.window_end_at,
            summary=f"WhatsApp group memory for {memory.group_name or 'team chat'}.",
            excerpt=_whatsapp_group_memory_excerpt(memory),
            metadata={
                "portfolio_id": memory.portfolio_id,
                "thread_id": memory.provider_thread_id,
                "thread_name": memory.group_name,
                "group_kind": memory.group_kind,
                "window_days": memory.window_days,
                "memory_date": memory.memory_date.isoformat() if memory.memory_date else None,
                "message_count": memory.message_count,
                "participant_count": memory.participant_count,
                "status": memory.status,
            },
        )
        self.session.commit()
        return evidence

    def _ask_whatsapp_group_memory_core_evidence_rows(self, source_id: Any, limit: int = 6) -> list[SignalEvidence]:
        memory = self._ask_whatsapp_group_memory_row(source_id)
        if not memory:
            return []
        rows: list[SignalEvidence] = []
        memory_evidence = self._ask_whatsapp_group_memory_evidence_row(source_id)
        if memory_evidence:
            rows.append(memory_evidence)
        refs = whatsapp_group_memory_message_refs(memory.source_metadata or {}, limit=max(limit - len(rows), 0))
        messages = self._whatsapp_messages_for_memory_refs(memory, refs)
        for message in messages:
            rows.append(self._evidence_for_whatsapp_message(message, source_type="whatsapp_message"))
            if len(rows) >= limit:
                break
        if rows:
            self.session.commit()
        return _dedupe_evidence_rows(rows)[:limit]

    def _ask_whatsapp_group_memory_row(self, source_id: Any) -> WhatsAppGroupMemory | None:
        try:
            memory_id = int(source_id)
        except (TypeError, ValueError):
            return None
        query = self.session.query(WhatsAppGroupMemory).filter(WhatsAppGroupMemory.whatsapp_group_memory_id == memory_id)
        if self.portfolio_ids is not None:
            query = query.filter(WhatsAppGroupMemory.portfolio_id.in_(self.portfolio_ids or [-1]))
        return query.first()

    def _whatsapp_messages_for_memory_refs(self, memory: WhatsAppGroupMemory, refs: list[dict[str, Any]]) -> list[WhatsAppMessage]:
        messages: list[WhatsAppMessage] = []
        seen_ids: set[int] = set()
        for ref in refs:
            message = None
            message_id = ref.get("message_id")
            if message_id:
                try:
                    message_id_int = int(message_id)
                except (TypeError, ValueError):
                    message_id_int = None
                if message_id_int and message_id_int not in seen_ids:
                    message = self.session.query(WhatsAppMessage).options(joinedload(WhatsAppMessage.thread)).filter(
                        WhatsAppMessage.whatsapp_message_id == message_id_int,
                        WhatsAppMessage.whatsapp_thread_id == memory.whatsapp_thread_id,
                    ).first()
            if not message:
                message = self._match_whatsapp_memory_ref_by_time_and_excerpt(memory, ref)
            if message and message.whatsapp_message_id not in seen_ids:
                messages.append(message)
                seen_ids.add(message.whatsapp_message_id)
        return messages

    def _match_whatsapp_memory_ref_by_time_and_excerpt(self, memory: WhatsAppGroupMemory, ref: dict[str, Any]) -> WhatsAppMessage | None:
        received_at = _parse_source_datetime(ref.get("received_at"))
        excerpt = (ref.get("excerpt") or "").strip()
        query = self.session.query(WhatsAppMessage).options(joinedload(WhatsAppMessage.thread)).filter(
            WhatsAppMessage.whatsapp_thread_id == memory.whatsapp_thread_id,
            WhatsAppMessage.body.isnot(None),
        )
        if received_at:
            query = query.filter(WhatsAppMessage.received_at >= received_at - timedelta(seconds=2))
            query = query.filter(WhatsAppMessage.received_at <= received_at + timedelta(seconds=2))
        if excerpt:
            query = query.filter(WhatsAppMessage.body.ilike(f"%{excerpt[:80]}%"))
        return query.order_by(WhatsAppMessage.received_at.desc()).first()

    def _evidence_for_whatsapp_message(self, message: WhatsAppMessage, *, source_type: str = "whatsapp_memory") -> SignalEvidence:
        thread = message.thread
        raw_payload = message.raw_payload if isinstance(message.raw_payload, dict) else {}
        return self._get_or_create_evidence(
            source_type=source_type,
            source_id=str(message.whatsapp_message_id),
            occurred_at=message.received_at,
            summary=f"WhatsApp message in {thread.display_name if thread else 'team chat'}.",
            excerpt=message.body,
            metadata={
                "from_number": message.from_number,
                "sender_name": message.sender_name,
                "thread_id": thread.provider_thread_id if thread else None,
                "thread_name": thread.display_name if thread else None,
                "portfolio_id": thread.portfolio_id if thread else None,
                "group_kind": raw_payload.get("group_kind"),
            },
        )

    def _ask_evidence_rows(self, question: str, limit: int = 28) -> list[SignalEvidence]:
        """Collect evidence for Ask Brain from signals plus raw operational memory."""
        terms = _ask_search_terms(question)
        if not self._ask_entity_terms_are_accessible(terms):
            return []
        evidence_rows = _dedupe_evidence_rows(self._ask_active_signal_evidence_rows(question, terms, limit=min(limit, 16)))
        seen = {(row.source_type, row.source_id) for row in evidence_rows}
        for row in self._ask_guest_stay_memory_evidence_rows(terms, limit=max(limit - len(evidence_rows), 8)):
            key = (row.source_type, row.source_id)
            if key in seen:
                continue
            evidence_rows.append(row)
            seen.add(key)
            if len(evidence_rows) >= limit:
                break
        for row in self._ask_whatsapp_group_memory_evidence_rows(terms, limit=max(limit - len(evidence_rows), 8)):
            key = (row.source_type, row.source_id)
            if key in seen:
                continue
            evidence_rows.append(row)
            seen.add(key)
            if len(evidence_rows) >= limit:
                break
        for row in self._ask_signal_evidence_rows(terms, limit=limit):
            key = (row.source_type, row.source_id)
            if key in seen:
                continue
            evidence_rows.append(row)
            seen.add(key)
            if len(evidence_rows) >= limit:
                break
        for row in self._ask_whatsapp_evidence_rows(terms, limit=max(limit - len(evidence_rows), 10)):
            key = (row.source_type, row.source_id)
            if key in seen:
                continue
            evidence_rows.append(row)
            seen.add(key)
            if len(evidence_rows) >= limit:
                break
        return evidence_rows[:limit]

    def _ask_entity_terms_are_accessible(self, terms: list[str]) -> bool:
        if self.portfolio_ids is None:
            return True
        entity_terms = [term for term in terms if term in ASK_ENTITY_TERMS]
        if not entity_terms:
            return True
        portfolio_ids = self.portfolio_ids or []
        if not portfolio_ids:
            return False
        portfolio_names = [
            row[0] or ""
            for row in self.session.query(Portfolio.name).filter(Portfolio.portfolio_id.in_(portfolio_ids)).all()
        ]
        thread_names = [
            row[0] or ""
            for row in self.session.query(WhatsAppThread.display_name).filter(WhatsAppThread.portfolio_id.in_(portfolio_ids)).all()
        ]
        accessible_text = " ".join(portfolio_names + thread_names).lower()
        return all(term in accessible_text for term in entity_terms)

    def _ask_explicit_unknown_scope_response(self, question: str) -> dict[str, Any] | None:
        scopes = _ask_explicit_scope_phrases(question)
        if not scopes:
            return None
        known_text = self._ask_known_scope_text()
        for scope in scopes:
            if not ask_explicit_scope_is_known(scope, known_text):
                return ask_insufficient_response(
                    prompt_version=ASK_PROMPT_VERSION,
                    reason=f"I could not match '{scope.strip()}' to any known portfolio, listing, or WhatsApp group.",
                    missing_data="Check the name or ask using the canonical portfolio/listing/group name.",
                )
        return None

    def _ask_known_scope_text(self) -> str:
        portfolio_query = self.session.query(Portfolio.name).filter(Portfolio.is_active == True)  # noqa: E712
        thread_query = self.session.query(WhatsAppThread.display_name).filter(WhatsAppThread.display_name.isnot(None))
        listing_ids: list[int] | None = None
        if self.portfolio_ids is not None:
            portfolio_query = portfolio_query.filter(Portfolio.portfolio_id.in_(self.portfolio_ids or [-1]))
            thread_query = thread_query.filter(WhatsAppThread.portfolio_id.in_(self.portfolio_ids or [-1]))
            listing_ids = [
                row[0]
                for row in self.session.query(PortfolioListing.listing_id)
                .filter(PortfolioListing.portfolio_id.in_(self.portfolio_ids or [-1]))
                .all()
            ]
        portfolio_names = [row[0] or "" for row in portfolio_query.all()]
        thread_names = [row[0] or "" for row in thread_query.all()]
        listing_query = self.main_session.query(Listing).filter(
            func.lower(func.coalesce(Listing.status, "")) != "deleted"
        ).order_by(Listing.name)
        if listing_ids is not None:
            listing_query = listing_query.filter(Listing.listing_id.in_(listing_ids or [-1]))
        listing_names = [listing_record_display_name(listing) or "" for listing in listing_query.limit(1500).all()]
        return " ".join(portfolio_names + thread_names + listing_names).lower()

    def _ask_signal_evidence_rows(self, terms: list[str], limit: int) -> list[SignalEvidence]:
        evidence_query = self.session.query(SignalEvidence).outerjoin(Signal, Signal.signal_id == SignalEvidence.signal_id)
        if self.portfolio_ids is not None:
            evidence_query = evidence_query.filter(
                or_(
                    Signal.portfolio_id.in_(self.portfolio_ids or [-1]),
                    SignalEvidence.evidence_metadata["portfolio_id"].astext.in_([str(item) for item in (self.portfolio_ids or [])]),
                )
            )
        if terms:
            predicates = []
            for term in terms:
                pattern = f"%{term}%"
                predicates.append(SignalEvidence.summary.ilike(pattern))
                predicates.append(SignalEvidence.excerpt.ilike(pattern))
            evidence_query = evidence_query.filter(or_(*predicates))
        return evidence_query.order_by(SignalEvidence.created_at.desc()).limit(min(limit, 18)).all()

    def _ask_whatsapp_group_memory_evidence_rows(self, terms: list[str], limit: int) -> list[SignalEvidence]:
        query = self.session.query(WhatsAppGroupMemory).filter(WhatsAppGroupMemory.message_count > 0)
        if self.portfolio_ids is not None:
            query = query.filter(WhatsAppGroupMemory.portfolio_id.in_(self.portfolio_ids or [-1]))
        if terms:
            predicates = []
            for term in terms:
                pattern = f"%{term}%"
                predicates.append(WhatsAppGroupMemory.group_name.ilike(pattern))
                predicates.append(WhatsAppGroupMemory.summary.ilike(pattern))
                predicates.append(WhatsAppGroupMemory.open_loop_summary.ilike(pattern))
                predicates.append(WhatsAppGroupMemory.risk_summary.ilike(pattern))
                predicates.append(WhatsAppGroupMemory.decision_summary.ilike(pattern))
                predicates.append(WhatsAppGroupMemory.cleaning_maintenance_summary.ilike(pattern))
            query = query.filter(or_(*predicates))
        memories = query.order_by(
            WhatsAppGroupMemory.memory_date.desc(),
            WhatsAppGroupMemory.last_message_at.desc().nullslast(),
        ).limit(limit).all()
        evidence_rows: list[SignalEvidence] = []
        for memory in memories:
            evidence_rows.append(
                self._get_or_create_evidence(
                    source_type="whatsapp_group_memory",
                    source_id=str(memory.whatsapp_group_memory_id),
                    occurred_at=memory.last_message_at or memory.window_end_at,
                    summary=f"WhatsApp group memory for {memory.group_name or 'team chat'}.",
                    excerpt=_whatsapp_group_memory_excerpt(memory),
                    metadata={
                        "portfolio_id": memory.portfolio_id,
                        "thread_id": memory.provider_thread_id,
                        "thread_name": memory.group_name,
                        "group_kind": memory.group_kind,
                        "window_days": memory.window_days,
                        "memory_date": memory.memory_date.isoformat() if memory.memory_date else None,
                        "message_count": memory.message_count,
                        "participant_count": memory.participant_count,
                        "status": memory.status,
                    },
                )
            )
        if evidence_rows:
            self.session.commit()
        return evidence_rows

    def _ask_guest_stay_memory_evidence_rows(self, terms: list[str], limit: int) -> list[SignalEvidence]:
        query = self.session.query(GuestStayMemory).filter(
            or_(GuestStayMemory.message_count > 0, GuestStayMemory.review_id.isnot(None))
        )
        if self.portfolio_ids is not None:
            query = query.filter(GuestStayMemory.portfolio_id.in_(self.portfolio_ids or [-1]))
        if terms:
            predicates = []
            for term in terms:
                pattern = f"%{term}%"
                predicates.append(GuestStayMemory.guest_name.ilike(pattern))
                predicates.append(GuestStayMemory.summary.ilike(pattern))
                predicates.append(GuestStayMemory.risk_summary.ilike(pattern))
                predicates.append(GuestStayMemory.latest_guest_issue.ilike(pattern))
                predicates.append(GuestStayMemory.resolution_summary.ilike(pattern))
                predicates.append(GuestStayMemory.suggested_action.ilike(pattern))
            query = query.filter(or_(*predicates))
        memories = query.order_by(
            GuestStayMemory.memory_date.desc(),
            GuestStayMemory.risk_score.desc(),
            GuestStayMemory.last_message_at.desc().nullslast(),
        ).limit(limit).all()
        evidence_rows: list[SignalEvidence] = []
        for memory in memories:
            evidence_rows.append(
                self._get_or_create_evidence(
                    source_type="guest_stay_memory",
                    source_id=str(memory.guest_stay_memory_id),
                    listing_id=memory.listing_id,
                    reservation_id=memory.reservation_id,
                    occurred_at=memory.last_message_at or (datetime.combine(memory.memory_date, datetime.min.time()) if memory.memory_date else None),
                    summary=f"Guest stay memory for {memory.guest_name or 'guest'} at {self._listing_display_name(memory.listing_id) or UNKNOWN_LISTING_NAME}.",
                    excerpt=guest_stay_memory_excerpt(memory),
                    metadata={
                        "portfolio_id": memory.portfolio_id,
                        "guest_name": memory.guest_name,
                        "risk_level": memory.risk_level,
                        "risk_score": memory.risk_score,
                        "status": memory.status,
                        "review_rating": memory.review_rating,
                        "arrival_date": memory.arrival_date.isoformat() if memory.arrival_date else None,
                        "departure_date": memory.departure_date.isoformat() if memory.departure_date else None,
                    },
                )
            )
        if evidence_rows:
            self.session.commit()
        return evidence_rows

    def _ask_whatsapp_evidence_rows(self, terms: list[str], limit: int) -> list[SignalEvidence]:
        query = self.session.query(WhatsAppMessage).options(joinedload(WhatsAppMessage.thread)).join(
            WhatsAppThread,
            WhatsAppThread.whatsapp_thread_id == WhatsAppMessage.whatsapp_thread_id,
        ).filter(
            WhatsAppMessage.body.isnot(None),
            WhatsAppThread.portfolio_id.isnot(None),
        )
        if self.portfolio_ids is not None:
            query = query.filter(WhatsAppThread.portfolio_id.in_(self.portfolio_ids or [-1]))
        if terms:
            predicates = []
            for term in terms:
                pattern = f"%{term}%"
                predicates.append(WhatsAppMessage.body.ilike(pattern))
                predicates.append(WhatsAppMessage.sender_name.ilike(pattern))
                predicates.append(WhatsAppThread.display_name.ilike(pattern))
            query = query.filter(or_(*predicates))
        messages = query.order_by(WhatsAppMessage.received_at.desc()).limit(limit).all()
        evidence_rows: list[SignalEvidence] = []
        for message in messages:
            thread = message.thread
            raw_payload = message.raw_payload if isinstance(message.raw_payload, dict) else {}
            evidence_rows.append(
                self._get_or_create_evidence(
                    source_type="whatsapp_memory",
                    source_id=str(message.whatsapp_message_id),
                    occurred_at=message.received_at,
                    summary=f"WhatsApp message in {thread.display_name if thread else 'team chat'}.",
                    excerpt=message.body,
                    metadata={
                        "from_number": message.from_number,
                        "sender_name": message.sender_name,
                        "thread_id": thread.provider_thread_id if thread else None,
                        "thread_name": thread.display_name if thread else None,
                        "portfolio_id": thread.portfolio_id if thread else None,
                        "group_kind": raw_payload.get("group_kind"),
                    },
                )
            )
        if evidence_rows:
            self.session.commit()
        return evidence_rows

    def _ask_active_signal_evidence_rows(self, question: str, terms: list[str], limit: int) -> list[SignalEvidence]:
        """Prioritize evidence behind currently surfaced signals for broad operating questions."""
        if not ask_question_should_start_from_active_signals(question, terms):
            return []
        categories = ask_signal_categories_for_question(question)
        query = self._query_accessible_signals().filter(Signal.status.in_(ACTIVE_SIGNAL_STATUSES))
        if categories:
            query = query.filter(Signal.category.in_(categories))
        signals = query.order_by(Signal.rank_score.desc(), Signal.last_seen_at.desc()).limit(80).all()
        entity_terms = ask_active_signal_filter_terms(terms)
        if entity_terms:
            portfolio_names = self._portfolio_name_map()
            signals = [
                signal
                for signal in signals
                if ask_signal_matches_entity_terms(signal, entity_terms, portfolio_names, self._listing_display_name(signal.listing_id))
            ]
        selected_signals = select_operating_signals(
            signals,
            limit=8,
            per_category_limit=3 if categories else 2,
        )
        evidence_rows: list[SignalEvidence] = []
        for signal in selected_signals:
            rows = sorted(
                signal.evidence,
                key=lambda item, category=signal.category: signal_evidence_sort_key(category, item),
            )
            for row in rows[:3]:
                evidence_rows.append(row)
                if len(evidence_rows) >= limit:
                    return evidence_rows
        return evidence_rows

    def _ask_citations_with_context(self, citations: list[dict[str, Any]], evidence: list[dict[str, Any]]) -> list[dict[str, Any]]:
        evidence_by_id = {item["evidence_id"]: item for item in evidence}
        enriched = []
        for citation in citations or []:
            try:
                evidence_id = int(citation.get("evidence_id"))
            except (TypeError, ValueError):
                continue
            row = evidence_by_id.get(evidence_id)
            if not row:
                continue
            enriched.append(
                {
                    **citation,
                    "evidence_id": evidence_id,
                    "source_type": row.get("source_type"),
                    "summary": row.get("summary"),
                    "excerpt": row.get("excerpt"),
                    "occurred_at": row.get("occurred_at"),
                    "metadata": row.get("metadata") or {},
                }
            )
        return enriched

    def create_portfolio(self, name: str, description: str = "") -> dict[str, Any]:
        """Create a portfolio; admin only."""
        if not is_brain_admin(self.user):
            raise PermissionError("Admin access required")
        name = (name or "").strip()
        if not name:
            raise ValueError("Portfolio name is required")
        portfolio = Portfolio(name=name.strip(), description=description.strip() or None)
        self.session.add(portfolio)
        self._audit("portfolio_create", "portfolio", None, {"name": name})
        self.session.commit()
        return {"portfolio_id": portfolio.portfolio_id, "name": portfolio.name}

    def map_listing_to_portfolio(self, listing_id: int, portfolio_id: int) -> dict[str, Any]:
        """Assign a listing to a portfolio; admin only."""
        if not is_brain_admin(self.user):
            raise PermissionError("Admin access required")
        self._get_accessible_portfolio(portfolio_id)
        listing = self.main_session.query(Listing).filter(Listing.listing_id == listing_id).first()
        if not listing:
            raise ValueError("Listing not found")
        row = self.session.query(PortfolioListing).filter(PortfolioListing.listing_id == listing_id).first()
        if not row:
            row = PortfolioListing(listing_id=listing_id)
            self.session.add(row)
        row.portfolio_id = portfolio_id
        row.listing_name_override = listing_record_display_name(listing)
        self._audit("portfolio_listing_map", "listing", listing_id, {"portfolio_id": portfolio_id})
        self.session.commit()
        return {"listing_id": listing_id, "portfolio_id": portfolio_id}

    def settings_data(self) -> dict[str, Any]:
        """Return admin settings data for portfolio, listing, and user mapping."""
        if not is_brain_admin(self.user):
            raise PermissionError("Admin access required")
        self.ensure_default_portfolio()
        portfolios = self.session.query(Portfolio).filter(Portfolio.is_active == True).order_by(Portfolio.sort_order, Portfolio.name).all()  # noqa: E712
        mappings = {row.listing_id: row for row in self.session.query(PortfolioListing).all()}
        listings = self.main_session.query(Listing).filter(
            func.lower(func.coalesce(Listing.status, "")) != "deleted"
        ).order_by(Listing.name).limit(1000).all()
        from dashboard.auth.models import User, get_session as get_user_session

        user_session = get_user_session()
        try:
            users = user_session.query(User).filter(User.is_approved == True).order_by(User.name, User.email).all()  # noqa: E712
            portfolio_users = self.session.query(PortfolioUser).all()
            return {
                "portfolios": [
                    {
                        "portfolio_id": item.portfolio_id,
                        "name": item.name,
                        "description": item.description,
                        "status": item.status,
                        "is_active": item.is_active,
                    }
                    for item in portfolios
                ],
                "listings": [
                    {
                        "listing_id": listing.listing_id,
                        "name": listing_record_display_name(listing) or UNKNOWN_LISTING_NAME,
                        "city": listing.city,
                        "status": listing.status,
                        "portfolio_id": mappings.get(listing.listing_id).portfolio_id if mappings.get(listing.listing_id) else None,
                    }
                    for listing in listings
                ],
                "users": [
                    {
                        "user_id": user.user_id,
                        "name": user.name or user.email,
                        "email": user.email,
                        "role": user.role,
                    }
                    for user in users
                ],
                "portfolio_users": [
                    {
                        "portfolio_id": row.portfolio_id,
                        "user_id": row.user_id,
                        "role": row.role,
                    }
                    for row in portfolio_users
                ],
                "source_health": self.source_health_data(),
            }
        finally:
            user_session.close()

    def source_health_data(self) -> dict[str, Any]:
        """Return lightweight source coverage status for admin settings."""
        if not is_brain_admin(self.user):
            raise PermissionError("Admin access required")
        return {
            "latest_run": self._latest_run_health(),
            "data_aggregator": self._data_aggregator_health(),
            "scheduled_reads": self._scheduled_read_health(),
            "brief_delivery": self._brief_delivery_health(),
            "hostaway": self._hostaway_health(),
            "pricelabs": self._pricelabs_health(),
            "whatsapp": self._whatsapp_health(),
        }

    def _latest_run_health(self) -> dict[str, Any]:
        run = self.session.query(SignalRun).order_by(SignalRun.signal_run_id.desc()).first()
        if not run:
            return {"status": "missing", "message": "No Brain runs have completed yet."}
        return {
            "status": run.status,
            "signal_run_id": run.signal_run_id,
            "run_type": run.run_type,
            "started_at": run.started_at.isoformat() if run.started_at else None,
            "completed_at": run.completed_at.isoformat() if run.completed_at else None,
            "source_counts": run.source_counts or {},
        }

    def _data_aggregator_health(self) -> dict[str, Any]:
        sources = self.session.query(DataSource).filter(DataSource.is_active == True).order_by(DataSource.category, DataSource.name).all()  # noqa: E712
        fact_counts = dict(
            self.session.query(BusinessFact.source_key, func.count(BusinessFact.business_fact_id))
            .filter(BusinessFact.status == "active")
            .group_by(BusinessFact.source_key)
            .all()
        )
        latest_runs_by_source = {}
        for row in self.session.query(DataIngestionRun).order_by(DataIngestionRun.source_key, DataIngestionRun.started_at.desc()).all():
            latest_runs_by_source.setdefault(row.source_key, row)
        source_rows = []
        for source in sources:
            row = data_source_health_status(source)
            latest_run = latest_runs_by_source.get(source.source_key)
            row["fact_count"] = int(fact_counts.get(source.source_key, 0) or 0)
            row["latest_run_id"] = latest_run.data_ingestion_run_id if latest_run else None
            row["latest_run_status"] = latest_run.status if latest_run else None
            row["latest_run_completed_at"] = latest_run.completed_at.isoformat() if latest_run and latest_run.completed_at else None
            row["latest_record_counts"] = latest_run.record_counts if latest_run else {}
            source_rows.append(row)
        latest_completed_at = self.session.query(func.max(DataIngestionRun.completed_at)).filter(
            DataIngestionRun.status == "completed"
        ).scalar()
        return summarize_data_aggregator_health(
            source_rows,
            fact_count=sum(int(value or 0) for value in fact_counts.values()),
            latest_completed_at=latest_completed_at,
        )

    def _scheduled_read_health(self) -> dict[str, Any]:
        now = datetime.utcnow()
        latest_runs = []
        for run_type in ("morning", "afternoon", "nightly"):
            run = self.session.query(SignalRun).filter(SignalRun.run_type == run_type).order_by(SignalRun.started_at.desc()).first()
            if not run:
                latest_runs.append({"run_type": run_type, "status": "missing"})
                continue
            brief_count = self.session.query(func.count(DailyBrief.daily_brief_id)).filter(DailyBrief.run_id == run.signal_run_id).scalar()
            latest_runs.append(
                {
                    "run_type": run_type,
                    "status": run.status,
                    "signal_run_id": run.signal_run_id,
                    "started_at": run.started_at.isoformat() if run.started_at else None,
                    "completed_at": run.completed_at.isoformat() if run.completed_at else None,
                    "brief_count": int(brief_count or 0),
                    "source_counts": run.source_counts or {},
                }
            )
        return summarize_scheduled_read_health(latest_runs, now=now)

    def _brief_delivery_health(self) -> dict[str, Any]:
        latest_run = self.session.query(SignalRun).filter(
            SignalRun.run_type.in_(("morning", "afternoon")),
            SignalRun.status == "completed",
        ).order_by(SignalRun.completed_at.desc().nullslast(), SignalRun.started_at.desc()).first()
        if not latest_run:
            return summarize_brief_delivery([], [])
        briefs = self.session.query(DailyBrief).filter(DailyBrief.run_id == latest_run.signal_run_id).all()
        brief_ids = [brief.daily_brief_id for brief in briefs]
        logs = []
        if brief_ids:
            logs = self.session.query(BriefDeliveryLog).filter(BriefDeliveryLog.daily_brief_id.in_(brief_ids)).all()
        return summarize_brief_delivery(briefs, logs, run=latest_run)

    def _hostaway_health(self) -> dict[str, Any]:
        """Summarize whether Hostaway-derived listing, calendar, and guest memory data are fresh."""
        now = datetime.utcnow()
        recent_runs = self.session.query(SignalRun).filter(
            SignalRun.status == "completed",
        ).order_by(SignalRun.completed_at.desc().nullslast(), SignalRun.started_at.desc()).limit(100).all()
        latest_run = next(
            (
                run for run in recent_runs
                if any((run.source_counts or {}).get(key) for key in ("hostaway_active_listings", "booking_snapshots", "booking_analyses"))
            ),
            None,
        )
        mapped_listing_count = int(self.session.query(func.count(PortfolioListing.portfolio_listing_id)).scalar() or 0)
        if not latest_run:
            return {
                "status": "missing",
                "message": "No completed Hostaway-backed Brain run has been stored yet.",
                "mapped_listing_count": mapped_listing_count,
                "active_listing_count": 0,
                "calendar_snapshot_count": 0,
                "booking_snapshot_count": 0,
                "booking_analysis_count": 0,
                "guest_stay_memory_count": 0,
            }

        source_counts = latest_run.source_counts or {}
        run_id = latest_run.signal_run_id
        active_listing_count = _safe_int(source_counts.get("hostaway_active_listings"))
        booking_snapshot_count = int(self.session.query(func.count(BookingHealthSnapshot.booking_health_snapshot_id)).filter(
            BookingHealthSnapshot.run_id == run_id,
        ).scalar() or 0)
        booking_analysis_count = int(self.session.query(func.count(BookingHealthAnalysis.booking_health_analysis_id)).filter(
            BookingHealthAnalysis.run_id == run_id,
        ).scalar() or 0)
        calendar_snapshot_count = int(self.session.query(func.count(CalendarSnapshot.calendar_snapshot_id)).filter(
            CalendarSnapshot.run_id == run_id,
        ).scalar() or 0)
        latest_guest_memory_run_id = self.session.query(func.max(GuestStayMemory.run_id)).scalar()
        guest_stay_memory_count = int(self.session.query(func.count(GuestStayMemory.guest_stay_memory_id)).filter(
            GuestStayMemory.run_id == latest_guest_memory_run_id,
        ).scalar() or 0) if latest_guest_memory_run_id else 0
        latest_calendar_at = self.session.query(func.max(CalendarSnapshot.created_at)).filter(CalendarSnapshot.run_id == run_id).scalar()
        latest_guest_memory_at = self.session.query(func.max(GuestStayMemory.updated_at)).filter(GuestStayMemory.run_id == latest_guest_memory_run_id).scalar() if latest_guest_memory_run_id else None
        completed_age_hours = _age_hours(latest_run.completed_at, now)
        guest_memory_age_hours = _age_hours(latest_guest_memory_at, now)
        expected_booking_snapshots = mapped_listing_count * 3 if mapped_listing_count else 0

        missing_parts = []
        if mapped_listing_count <= 0:
            missing_parts.append("portfolio listing mappings")
        if active_listing_count <= 0:
            missing_parts.append("active Hostaway listings")
        if expected_booking_snapshots and booking_snapshot_count < expected_booking_snapshots:
            missing_parts.append("complete booking-health snapshots")
        if mapped_listing_count and booking_analysis_count < mapped_listing_count:
            missing_parts.append("per-listing booking analyses")
        if mapped_listing_count and calendar_snapshot_count <= 0:
            missing_parts.append("calendar snapshots")
        if guest_stay_memory_count <= 0:
            missing_parts.append("guest-stay memories")

        if completed_age_hours is not None and completed_age_hours > 36:
            status = "stale"
            message = "Hostaway-backed Brain run is older than 36 hours."
        elif guest_memory_age_hours is not None and guest_memory_age_hours > 36:
            status = "stale"
            message = "Hostaway guest-stay memory is older than 36 hours."
        elif missing_parts:
            status = "degraded"
            message = "Hostaway run is missing " + ", ".join(missing_parts) + "."
        else:
            status = "ok"
            message = "Hostaway listings, calendar snapshots, booking health, and guest memory look current."

        return {
            "status": status,
            "message": message,
            "run_id": run_id,
            "run_type": latest_run.run_type,
            "completed_at": latest_run.completed_at.isoformat() if latest_run.completed_at else None,
            "completed_age_hours": round(completed_age_hours, 2) if completed_age_hours is not None else None,
            "guest_memory_run_id": latest_guest_memory_run_id,
            "active_listing_count": active_listing_count,
            "mapped_listing_count": mapped_listing_count,
            "ignored_or_unmapped_listing_count": max(active_listing_count - mapped_listing_count, 0),
            "calendar_snapshot_count": calendar_snapshot_count,
            "booking_snapshot_count": booking_snapshot_count,
            "expected_booking_snapshot_count": expected_booking_snapshots,
            "booking_analysis_count": booking_analysis_count,
            "guest_stay_memory_count": guest_stay_memory_count,
            "latest_calendar_at": latest_calendar_at.isoformat() if latest_calendar_at else None,
            "latest_guest_memory_at": latest_guest_memory_at.isoformat() if latest_guest_memory_at else None,
            "guest_memory_age_hours": round(guest_memory_age_hours, 2) if guest_memory_age_hours is not None else None,
            "source_counts": source_counts,
        }

    def _pricelabs_health(self) -> dict[str, Any]:
        latest_run_id = self.session.query(func.max(PriceLabsSnapshot.run_id)).scalar()
        mapped_listing_count = int(self.session.query(func.count(PortfolioListing.portfolio_listing_id)).scalar() or 0)
        if not latest_run_id:
            return {
                "status": "not_configured" if not config.PRICELABS_API_KEY else "missing",
                "message": "No PriceLabs snapshots have been stored yet.",
                "status_counts": {},
                "snapshot_count": 0,
                "expected_snapshot_count": mapped_listing_count,
                "mapped_listing_count": mapped_listing_count,
                "configured": bool(config.PRICELABS_API_KEY),
                "base_url": config.PRICELABS_BASE_URL,
            }
        rows = self.session.query(PriceLabsSnapshot).filter(PriceLabsSnapshot.run_id == latest_run_id).all()
        return summarize_pricelabs_health(
            rows,
            run_id=latest_run_id,
            expected_snapshot_count=mapped_listing_count,
            configured=bool(config.PRICELABS_API_KEY),
            base_url=config.PRICELABS_BASE_URL,
            now=datetime.utcnow(),
        )

    def _whatsapp_health(self) -> dict[str, Any]:
        now = datetime.utcnow()
        cutoff_24 = now - timedelta(hours=24)
        cutoff_7 = now - timedelta(days=7)
        cutoff_60 = now - timedelta(days=60)
        configured_groups = load_whatsapp_group_profiles()
        backfill_state = load_whatsapp_bridge_state()
        rows = self.session.query(
            WhatsAppThread.display_name,
            WhatsAppThread.provider_thread_id,
            Portfolio.name.label("portfolio_name"),
            func.count(WhatsAppMessage.whatsapp_message_id).label("message_count_60d"),
            func.sum(
                case(
                    (WhatsAppMessage.received_at >= cutoff_7, 1),
                    else_=0,
                )
            ).label("message_count_7d"),
            func.sum(
                case(
                    (WhatsAppMessage.received_at >= cutoff_24, 1),
                    else_=0,
                )
            ).label("message_count_24h"),
            func.sum(
                case(
                    ((WhatsAppMessage.received_at >= cutoff_24) & (WhatsAppMessage.direction == "outbound"), 1),
                    else_=0,
                )
            ).label("account_sent_count_24h"),
            func.min(WhatsAppMessage.received_at).label("first_received_at"),
            func.max(WhatsAppMessage.received_at).label("last_received_at"),
        ).join(
            WhatsAppMessage,
            WhatsAppMessage.whatsapp_thread_id == WhatsAppThread.whatsapp_thread_id,
        ).outerjoin(
            Portfolio,
            Portfolio.portfolio_id == WhatsAppThread.portfolio_id,
        ).filter(
            WhatsAppMessage.received_at >= cutoff_60,
        ).group_by(
            WhatsAppThread.display_name,
            WhatsAppThread.provider_thread_id,
            Portfolio.name,
        ).all()
        thread_rows = [
            {
                "display_name": row.display_name,
                "provider_thread_id": row.provider_thread_id,
                "portfolio_name": row.portfolio_name,
                "message_count_60d": int(row.message_count_60d or 0),
                "message_count_7d": int(row.message_count_7d or 0),
                "message_count_24h": int(row.message_count_24h or 0),
                "account_sent_count_24h": int(row.account_sent_count_24h or 0),
                "first_received_at": row.first_received_at.isoformat() if row.first_received_at else None,
                "last_received_at": row.last_received_at.isoformat() if row.last_received_at else None,
            }
            for row in rows
        ]
        groups = whatsapp_source_group_health(configured_groups, thread_rows, backfill_state, now=now)
        ingestion = summarize_whatsapp_ingestion(thread_rows, backfill_state, now=now)
        missing_count = len([group for group in groups if group["status"] == "missing"])
        not_found_count = len([group for group in groups if group["status"] == "not_found"])
        stale_count = len([group for group in groups if group["status"] == "stale"])
        incomplete_history_count = len([group for group in groups if group["status"] == "incomplete_history"])
        history_limited_count = len([group for group in groups if group["status"] == "history_limited"])
        verified_empty_count = len([group for group in groups if group.get("verified_empty")])
        status = "ok"
        if missing_count or not_found_count:
            status = "missing_groups"
        elif stale_count:
            status = "stale"
        elif ingestion["status"] in {"missing", "stale", "degraded"}:
            status = ingestion["status"]
        return {
            "status": status,
            "history_status": "partial_older_history" if incomplete_history_count or history_limited_count else "ok",
            "configured_group_count": len(configured_groups),
            "synced_group_count": len([group for group in groups if group["message_count_60d"] > 0]),
            "missing_group_count": missing_count,
            "not_found_group_count": not_found_count,
            "stale_group_count": stale_count,
            "incomplete_history_group_count": incomplete_history_count,
            "history_limited_group_count": history_limited_count,
            "verified_empty_group_count": verified_empty_count,
            "groups": groups,
            "last_backfill": _whatsapp_backfill_summary(backfill_state.get("last_backfill")),
            "ongoing_ingestion": ingestion,
        }

    def assign_user_to_portfolio(self, user_id: int, portfolio_id: int, role: str | None = None) -> dict[str, Any]:
        """Include a user in portfolio brief delivery."""
        if not is_brain_admin(self.user):
            raise PermissionError("Admin access required")
        self._get_accessible_portfolio(portfolio_id)
        from dashboard.auth.models import User, get_session as get_user_session

        user_session = get_user_session()
        try:
            user = user_session.query(User).filter(User.user_id == user_id, User.is_approved == True).first()  # noqa: E712
            if not user:
                raise ValueError("Approved user not found")
        finally:
            user_session.close()
        row = self.session.query(PortfolioUser).filter(
            PortfolioUser.user_id == user_id,
            PortfolioUser.portfolio_id == portfolio_id,
        ).first()
        if not row:
            row = PortfolioUser(user_id=user_id, portfolio_id=portfolio_id)
            self.session.add(row)
        row.role = "operator"
        self._audit("portfolio_user_assign", "portfolio_user", None, {"user_id": user_id, "portfolio_id": portfolio_id, "role": row.role})
        self.session.commit()
        return {"user_id": user_id, "portfolio_id": portfolio_id, "role": row.role}

    def remove_user_from_portfolio(self, user_id: int, portfolio_id: int) -> dict[str, Any]:
        """Remove a user from portfolio brief delivery."""
        if not is_brain_admin(self.user):
            raise PermissionError("Admin access required")
        row = self.session.query(PortfolioUser).filter(
            PortfolioUser.user_id == user_id,
            PortfolioUser.portfolio_id == portfolio_id,
        ).first()
        if row:
            self.session.delete(row)
            self._audit("portfolio_user_remove", "portfolio_user", None, {"user_id": user_id, "portfolio_id": portfolio_id})
            self.session.commit()
        return {"removed": bool(row), "user_id": user_id, "portfolio_id": portfolio_id}

    def ensure_default_portfolio(self, allowed_listing_ids: set[int] | None = None):
        """Ensure Brain portfolios mirror Cotton Candy listing tags."""
        return self.sync_portfolios_from_listing_tags(allowed_listing_ids)

    def sync_portfolios_from_listing_tags(self, allowed_listing_ids: set[int] | None = None) -> dict[str, Any]:
        """Create canonical portfolios and map listings from Cotton Candy tags.

        Ignored tags, including smoky cabins, remain unmapped so Brain only
        analyzes the portfolio groups requested for this service. A few no-tag
        listings are mapped by explicit listing ID when the portfolio lives
        outside the Cotton Candy tag model.
        """
        portfolio_by_name = self._ensure_tag_portfolios()
        self._deactivate_non_tag_portfolios()
        if allowed_listing_ids is None:
            self.session.commit()
            return {"portfolios": list(portfolio_by_name), "mapped": None, "unmapped": None}

        allowed_listing_ids = set(allowed_listing_ids)
        listings_query = self.main_session.query(Listing).filter(
            Listing.listing_id.in_(allowed_listing_ids),
            or_(Listing.status.is_(None), Listing.status != "deleted"),
        )
        listings = listings_query.all()
        listing_ids = {listing.listing_id for listing in listings}
        tag_map = self._listing_tag_map(listing_ids)
        desired_mappings: dict[int, Portfolio] = {}
        for listing in listings:
            portfolio_name = portfolio_name_for_listing(listing.listing_id, tag_map.get(listing.listing_id, ()))
            if portfolio_name:
                desired_mappings[listing.listing_id] = portfolio_by_name[portfolio_name]

        existing_rows = {
            row.listing_id: row
            for row in self.session.query(PortfolioListing)
            .filter(PortfolioListing.listing_id.in_(allowed_listing_ids))
            .all()
        }
        changed = False
        for listing in listings:
            portfolio = desired_mappings.get(listing.listing_id)
            row = existing_rows.get(listing.listing_id)
            if portfolio:
                if not row:
                    row = PortfolioListing(listing_id=listing.listing_id)
                    self.session.add(row)
                if row.portfolio_id != portfolio.portfolio_id:
                    row.portfolio_id = portfolio.portfolio_id
                    changed = True
                name = listing_record_display_name(listing)
                if row.listing_name_override != name:
                    row.listing_name_override = name
                    changed = True
            elif row:
                self.session.delete(row)
                changed = True

        self.session.commit()
        return {
            "portfolios": list(portfolio_by_name),
            "mapped": len(desired_mappings),
            "unmapped": len(listing_ids - set(desired_mappings)),
        }

    def _ensure_tag_portfolios(self) -> dict[str, Portfolio]:
        portfolio_by_name: dict[str, Portfolio] = {}
        for sort_order, spec in enumerate(TAG_PORTFOLIOS, start=1):
            portfolio = self.session.query(Portfolio).filter(func.lower(Portfolio.name) == spec["name"].lower()).first()
            if not portfolio:
                portfolio = Portfolio(name=spec["name"])
                self.session.add(portfolio)
                self.session.flush()
            portfolio.name = spec["name"]
            portfolio.description = spec["description"]
            portfolio.sort_order = sort_order * 10
            portfolio.is_active = True
            portfolio_by_name[portfolio.name] = portfolio
        return portfolio_by_name

    def _deactivate_non_tag_portfolios(self):
        self.session.query(Portfolio).filter(~Portfolio.name.in_(TAG_PORTFOLIO_NAMES)).update(
            {Portfolio.is_active: False},
            synchronize_session=False,
        )

    def _listing_tag_map(self, listing_ids: set[int]) -> dict[int, tuple[str, ...]]:
        if not listing_ids:
            return {}
        rows = (
            self.main_session.query(ListingTag.listing_id, Tag.name)
            .join(Tag, Tag.tag_id == ListingTag.tag_id)
            .filter(ListingTag.listing_id.in_(listing_ids))
            .all()
        )
        tag_map: dict[int, list[str]] = {}
        for listing_id, tag_name in rows:
            tag_map.setdefault(listing_id, []).append(normalize_tag_name(tag_name))
        return {listing_id: tuple(tags) for listing_id, tags in tag_map.items()}

    def _query_accessible_signals(self):
        query = self.session.query(Signal).filter(
            Signal.portfolio_id.isnot(None),
            exists().where(SignalEvidence.signal_id == Signal.signal_id)
        )
        return filter_portfolio_query(query, Signal, self.portfolio_ids)

    def _query_accessible_open_loops(self):
        query = self.session.query(OpenLoop).filter(OpenLoop.portfolio_id.isnot(None))
        return filter_portfolio_query(query, OpenLoop, self.portfolio_ids)

    def _query_accessible_business_facts(self, portfolio_ids: list[int] | None = None):
        query = self.session.query(BusinessFact)
        effective_portfolio_ids = self.portfolio_ids if portfolio_ids is None else portfolio_ids
        if effective_portfolio_ids is None:
            return query
        listing_ids = self.session.query(PortfolioListing.listing_id).filter(
            PortfolioListing.portfolio_id.in_(effective_portfolio_ids or [-1])
        )
        return query.filter(
            or_(
                BusinessFact.portfolio_id.in_(effective_portfolio_ids or [-1]),
                BusinessFact.listing_id.in_(listing_ids),
            )
        )

    def _query_accessible_business_metrics(self, portfolio_ids: list[int] | None = None):
        query = self.session.query(BusinessMetricSnapshot)
        effective_portfolio_ids = self.portfolio_ids if portfolio_ids is None else portfolio_ids
        if effective_portfolio_ids is None:
            return query
        listing_ids = self.session.query(PortfolioListing.listing_id).filter(
            PortfolioListing.portfolio_id.in_(effective_portfolio_ids or [-1])
        )
        return query.filter(
            or_(
                BusinessMetricSnapshot.portfolio_id.in_(effective_portfolio_ids or [-1]),
                BusinessMetricSnapshot.listing_id.in_(listing_ids),
            )
        )

    def _portfolio_scope_ids(self, portfolio_id: int | str | None) -> list[int] | None:
        if portfolio_id in (None, "", "all"):
            return self.portfolio_ids
        portfolio_id_int = int(portfolio_id)
        self._get_accessible_portfolio(portfolio_id_int)
        return [portfolio_id_int]

    def _apply_portfolio_scope(self, query, model, portfolio_ids: list[int] | None):
        if portfolio_ids is None:
            return query
        return query.filter(model.portfolio_id.in_(portfolio_ids or [-1]))

    def _portfolio_name_map(self) -> dict[int, str]:
        query = self.session.query(Portfolio)
        if self.portfolio_ids is not None:
            query = query.filter(Portfolio.portfolio_id.in_(self.portfolio_ids or [-1]))
        return {
            portfolio.portfolio_id: portfolio.name
            for portfolio in query.all()
        }

    def _data_foundation_source_rows(self, fact_counts: dict[str, int]) -> list[dict[str, Any]]:
        latest_runs_by_source = {}
        for row in self.session.query(DataIngestionRun).order_by(DataIngestionRun.source_key, DataIngestionRun.started_at.desc()).all():
            latest_runs_by_source.setdefault(row.source_key, row)
        rows = []
        for source in self.session.query(DataSource).filter(DataSource.is_active == True).order_by(DataSource.category, DataSource.name).all():  # noqa: E712
            row = data_source_health_status(source)
            latest_run = latest_runs_by_source.get(source.source_key)
            row["fact_count"] = int(fact_counts.get(source.source_key, 0) or 0)
            row["latest_run_id"] = latest_run.data_ingestion_run_id if latest_run else None
            row["latest_run_status"] = latest_run.status if latest_run else None
            row["latest_run_completed_at"] = latest_run.completed_at.isoformat() if latest_run and latest_run.completed_at else None
            row["latest_record_counts"] = latest_run.record_counts if latest_run else {}
            rows.append(row)
        return rows

    def _data_foundation_listing_ids(self, portfolio_ids: list[int] | None) -> set[int]:
        query = self.session.query(PortfolioListing.listing_id)
        effective_portfolio_ids = self.portfolio_ids if portfolio_ids is None else portfolio_ids
        if effective_portfolio_ids is not None:
            query = query.filter(PortfolioListing.portfolio_id.in_(effective_portfolio_ids or [-1]))
        return {int(listing_id) for listing_id, in query.all() if listing_id is not None}

    def _business_fact_listing_ids(self, portfolio_ids: list[int] | None, fact_type: str, listing_ids: list[int]) -> set[int]:
        rows = (
            self._query_accessible_business_facts(portfolio_ids)
            .filter(
                BusinessFact.status == "active",
                BusinessFact.fact_type == fact_type,
                BusinessFact.listing_id.in_(listing_ids or [-1]),
            )
            .with_entities(BusinessFact.listing_id)
            .distinct()
            .all()
        )
        return {int(listing_id) for listing_id, in rows if listing_id is not None}

    def _business_metric_listing_ids(self, portfolio_ids: list[int] | None, metric_name: str, listing_ids: list[int]) -> set[int]:
        rows = (
            self._query_accessible_business_metrics(portfolio_ids)
            .filter(
                BusinessMetricSnapshot.status != "withdrawn",
                BusinessMetricSnapshot.metric_name == metric_name,
                BusinessMetricSnapshot.listing_id.in_(listing_ids or [-1]),
            )
            .with_entities(BusinessMetricSnapshot.listing_id)
            .distinct()
            .all()
        )
        return {int(listing_id) for listing_id, in rows if listing_id is not None}

    def _data_foundation_listing_coverage(self, portfolio_ids: list[int] | None, mapped_listing_ids: set[int]) -> list[dict[str, Any]]:
        listing_ids = sorted(mapped_listing_ids) or [-1]
        expected_count = len(mapped_listing_ids)
        rows: list[dict[str, Any]] = []

        for target in FOUNDATION_LISTING_COVERAGE_TARGETS:
            kind = str(target.get("kind") or "")
            name = str(target.get("name") or "")
            covered_count = 0
            components: list[dict[str, Any]] = []
            if kind == "fact":
                covered_count = len(self._business_fact_listing_ids(portfolio_ids, name, listing_ids))
            elif kind == "metric":
                covered_count = len(self._business_metric_listing_ids(portfolio_ids, name, listing_ids))
            elif kind == "match":
                required_sets: list[set[int]] = []
                for fact_type in target.get("required_facts") or ():
                    covered_listing_ids = self._business_fact_listing_ids(portfolio_ids, str(fact_type), listing_ids)
                    required_sets.append(covered_listing_ids)
                    components.append(
                        {
                            "kind": "fact",
                            "name": str(fact_type),
                            "covered_listing_count": len(covered_listing_ids),
                        }
                    )
                for metric_name in target.get("required_metrics") or ():
                    covered_listing_ids = self._business_metric_listing_ids(portfolio_ids, str(metric_name), listing_ids)
                    required_sets.append(covered_listing_ids)
                    components.append(
                        {
                            "kind": "metric",
                            "name": str(metric_name),
                            "covered_listing_count": len(covered_listing_ids),
                        }
                    )
                covered_count = len(set.intersection(*required_sets)) if required_sets else 0

            row = {
                "kind": kind,
                "name": name,
                "covered_listing_count": int(covered_count or 0),
                "expected_listing_count": expected_count,
                "coverage_pct": round((float(covered_count or 0) / expected_count) * 100, 1) if expected_count else 0.0,
                "minimum_pct": float(target.get("minimum_pct") or 0),
            }
            if components:
                row["components"] = components
            rows.append(row)

        return rows

    def _data_foundation_source_freshness(self, portfolio_ids: list[int] | None) -> list[dict[str, Any]]:
        now = datetime.utcnow()
        rows: list[dict[str, Any]] = []
        for target in FOUNDATION_SOURCE_FRESHNESS_TARGETS:
            kind = target.get("kind")
            latest_at = None
            if kind == "sync_log":
                sync_type = str(target.get("sync_type") or "")
                latest_at = (
                    self.main_session.query(func.max(SyncLog.completed_at))
                    .filter(SyncLog.sync_type == sync_type, SyncLog.status == "success")
                    .scalar()
                )
            elif kind == "fact_occurred":
                fact_type = str(target.get("fact_type") or "")
                latest_at = (
                    self._query_accessible_business_facts(portfolio_ids)
                    .filter(BusinessFact.status == "active", BusinessFact.fact_type == fact_type)
                    .with_entities(func.max(BusinessFact.occurred_at))
                    .scalar()
                )

            max_age_hours = float(target.get("max_age_hours") or 0)
            latest_dt = _parse_source_datetime(latest_at) if latest_at else None
            age_hours = round((now - latest_dt).total_seconds() / 3600, 1) if latest_dt else None
            if latest_dt is None:
                status = "missing"
                message = f"{target['name']} has no freshness evidence."
            elif max_age_hours and age_hours is not None and age_hours > max_age_hours:
                status = "stale"
                message = f"{target['name']} is {round(age_hours / 24, 1)} days old; target is {round(max_age_hours / 24, 1)} days."
            else:
                status = "ok"
                message = f"{target['name']} is fresh."
            rows.append(
                {
                    "source_key": target["source_key"],
                    "name": target["name"],
                    "kind": kind,
                    "latest_at": latest_dt.isoformat() if latest_dt else None,
                    "age_hours": age_hours,
                    "max_age_hours": max_age_hours,
                    "status": status,
                    "message": message,
                }
            )
        return rows

    def _get_accessible_portfolio(self, portfolio_id: int) -> Portfolio:
        query = self.session.query(Portfolio).filter(Portfolio.portfolio_id == portfolio_id)
        if self.portfolio_ids is not None:
            query = query.filter(Portfolio.portfolio_id.in_(self.portfolio_ids or [-1]))
        portfolio = query.first()
        if not portfolio:
            raise ValueError("Portfolio not found")
        return portfolio

    def _audit(self, action: str, entity_type: str, entity_id: Any, metadata: dict[str, Any] | None = None):
        from brain.models import BrainAuditLog

        self.session.add(
            BrainAuditLog(
                user_id=getattr(self.user, "user_id", None),
                action=action,
                entity_type=entity_type,
                entity_id=str(entity_id) if entity_id is not None else None,
                audit_metadata=metadata or {},
            )
        )

    def _get_or_create_evidence(
        self,
        *,
        source_type: str,
        source_id: str | None = None,
        listing_id: int | None = None,
        reservation_id: int | None = None,
        occurred_at: datetime | None = None,
        summary: str,
        excerpt: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> SignalEvidence:
        query = self.session.query(SignalEvidence).filter(
            SignalEvidence.source_type == source_type,
            SignalEvidence.source_id == source_id,
            SignalEvidence.signal_id.is_(None),
        )
        evidence = query.first() if source_id else None
        if not evidence:
            evidence = SignalEvidence(source_type=source_type, source_id=source_id)
            self.session.add(evidence)
        evidence.listing_id = listing_id
        evidence.reservation_id = reservation_id
        evidence.occurred_at = occurred_at
        evidence.summary = summary[:1000]
        evidence.excerpt = (excerpt or "")[:3000]
        evidence.evidence_metadata = as_json_safe(metadata or {})
        self.session.flush()
        return evidence

    def _listing_display_name(self, listing_id: int | None) -> str | None:
        if not listing_id:
            return None
        listing_id = int(listing_id)
        if listing_id not in self._listing_name_cache:
            listing = self.main_session.query(Listing).filter(Listing.listing_id == listing_id).first()
            self._listing_name_cache[listing_id] = listing_record_display_name(listing) or UNKNOWN_LISTING_NAME
        return self._listing_name_cache[listing_id]

    def _listing_names_for_ids(self, listing_ids) -> dict[int, str | None]:
        names: dict[int, str | None] = {}
        for listing_id in listing_ids or []:
            if listing_id:
                names[int(listing_id)] = self._listing_display_name(int(listing_id))
        return names

    def _sanitize_listing_text(self, text: str | None, *listing_ids: int | None) -> str:
        return sanitize_listing_reference_text(text, self._listing_names_for_ids(listing_ids))

    def _business_fact_dict(self, fact: BusinessFact) -> dict[str, Any]:
        listing_name = self._listing_display_name(fact.listing_id)
        portfolio_name = self._portfolio_name_map().get(fact.portfolio_id)
        return {
            "business_fact_id": fact.business_fact_id,
            "fact_key": fact.fact_key,
            "fact_type": fact.fact_type,
            "grain": fact.grain,
            "source_key": fact.source_key,
            "source_table": fact.source_table,
            "source_id": fact.source_id,
            "portfolio_id": fact.portfolio_id,
            "portfolio_name": portfolio_name,
            "listing_id": fact.listing_id,
            "listing_name": listing_name,
            "reservation_id": fact.reservation_id,
            "guest_id": fact.guest_id,
            "occurred_at": fact.occurred_at.isoformat() if fact.occurred_at else None,
            "effective_start": fact.effective_start.isoformat() if fact.effective_start else None,
            "effective_end": fact.effective_end.isoformat() if fact.effective_end else None,
            "numeric_value": fact.numeric_value,
            "text_value": self._sanitize_listing_text(fact.text_value, fact.listing_id),
            "confidence": fact.confidence,
            "status": fact.status,
            "payload": as_json_safe(fact.fact_payload or {}),
            "ingestion_run_id": fact.ingestion_run_id,
            "created_at": fact.created_at.isoformat() if fact.created_at else None,
            "updated_at": fact.updated_at.isoformat() if fact.updated_at else None,
        }

    def _business_metric_dict(self, metric: BusinessMetricSnapshot) -> dict[str, Any]:
        listing_name = self._listing_display_name(metric.listing_id)
        portfolio_name = self._portfolio_name_map().get(metric.portfolio_id)
        return {
            "business_metric_snapshot_id": metric.business_metric_snapshot_id,
            "metric_key": metric.metric_key,
            "metric_name": metric.metric_name,
            "category": metric.category,
            "grain": metric.grain,
            "metric_date": metric.metric_date.isoformat() if metric.metric_date else None,
            "horizon_days": metric.horizon_days,
            "portfolio_id": metric.portfolio_id,
            "portfolio_name": portfolio_name,
            "listing_id": metric.listing_id,
            "listing_name": listing_name,
            "numeric_value": metric.numeric_value,
            "text_value": self._sanitize_listing_text(metric.text_value, metric.listing_id),
            "status": metric.status,
            "confidence": metric.confidence,
            "source_keys": as_json_safe(metric.source_keys or []),
            "payload": as_json_safe(metric.metric_payload or {}),
            "computed_at": metric.computed_at.isoformat() if metric.computed_at else None,
            "created_at": metric.created_at.isoformat() if metric.created_at else None,
            "updated_at": metric.updated_at.isoformat() if metric.updated_at else None,
        }

    def _signal_dict(self, signal: Signal, include_evidence: bool = False) -> dict[str, Any]:
        listing_name = self._listing_display_name(signal.listing_id)
        portfolio_name = self._portfolio_name_map().get(signal.portfolio_id)
        result = {
            "signal_id": signal.signal_id,
            "portfolio_id": signal.portfolio_id,
            "portfolio_name": portfolio_name,
            "listing_id": signal.listing_id,
            "listing_name": listing_name,
            "reservation_id": signal.reservation_id,
            "category": signal.category,
            "severity": signal.severity,
            "confidence": signal.confidence,
            "title": self._sanitize_listing_text(signal.title, signal.listing_id),
            "summary": self._sanitize_listing_text(signal.summary, signal.listing_id),
            "why_it_matters": self._sanitize_listing_text(signal.why_it_matters, signal.listing_id),
            "suggested_action": self._sanitize_listing_text(signal.suggested_action, signal.listing_id),
            "audience": normalize_signal_audience(signal.owner_or_manager, category=signal.category),
            "status": signal.status,
            "rank_score": signal.rank_score,
            "source": signal.source,
            "first_seen_at": signal.first_seen_at.isoformat() if signal.first_seen_at else None,
            "last_seen_at": signal.last_seen_at.isoformat() if signal.last_seen_at else None,
        }
        if include_evidence:
            evidence = sorted(
                signal.evidence,
                key=lambda item: signal_evidence_sort_key(signal.category, item),
            )
            result["evidence"] = [self._evidence_dict(item) for item in evidence]
        return result

    def _evidence_dict(self, evidence: SignalEvidence) -> dict[str, Any]:
        listing_name = self._listing_display_name(evidence.listing_id)
        return {
            "evidence_id": evidence.evidence_id,
            "source_type": evidence.source_type,
            "source_id": evidence.source_id,
            "listing_id": evidence.listing_id,
            "listing_name": listing_name,
            "reservation_id": evidence.reservation_id,
            "occurred_at": evidence.occurred_at.isoformat() if evidence.occurred_at else None,
            "summary": self._sanitize_listing_text(evidence.summary, evidence.listing_id),
            "excerpt": self._sanitize_listing_text(evidence.excerpt, evidence.listing_id),
            "url": evidence.url,
            "metadata": evidence.evidence_metadata or {},
        }

    def _booking_dict(self, snapshot: BookingHealthSnapshot) -> dict[str, Any]:
        listing_name = self._listing_display_name(snapshot.listing_id)
        return {
            "booking_health_snapshot_id": snapshot.booking_health_snapshot_id,
            "portfolio_id": snapshot.portfolio_id,
            "listing_id": snapshot.listing_id,
            "listing_name": listing_name,
            "horizon_days": snapshot.horizon_days,
            "snapshot_date": snapshot.snapshot_date.isoformat() if snapshot.snapshot_date else None,
            "occupancy_rate": snapshot.occupancy_rate,
            "booked_nights": snapshot.booked_nights,
            "available_nights": snapshot.available_nights,
            "expected_occupancy_rate": snapshot.expected_occupancy_rate,
            "diagnosis": snapshot.diagnosis,
            "confidence": snapshot.confidence,
            "recommended_action": self._sanitize_listing_text(snapshot.recommended_action, snapshot.listing_id),
            "raw_metrics": snapshot.raw_metrics or {},
        }

    def _booking_analysis_dict(self, analysis: BookingHealthAnalysis) -> dict[str, Any]:
        listing_name = analysis.listing_name or self._listing_display_name(analysis.listing_id) or UNKNOWN_LISTING_NAME
        source_statuses = analysis.source_statuses or {}
        portfolio_name = self._portfolio_name_map().get(analysis.portfolio_id)
        return {
            "booking_health_analysis_id": analysis.booking_health_analysis_id,
            "portfolio_id": analysis.portfolio_id,
            "portfolio_name": portfolio_name,
            "listing_id": analysis.listing_id,
            "listing_name": listing_name,
            "snapshot_date": analysis.snapshot_date.isoformat() if analysis.snapshot_date else None,
            "severity": analysis.severity,
            "confidence": analysis.confidence,
            "priority_score": booking_health_rank_score(analysis),
            "horizons": analysis.horizons or [],
            "booking_pattern": self._sanitize_listing_text(analysis.booking_pattern, analysis.listing_id),
            "pricelabs_opinion": self._sanitize_listing_text(analysis.pricelabs_opinion, analysis.listing_id),
            "airbnb_page_opinion": self._sanitize_listing_text(analysis.airbnb_page_opinion, analysis.listing_id),
            "opinion": self._sanitize_listing_text(analysis.opinion, analysis.listing_id),
            "action_items": [
                self._sanitize_listing_text(item, analysis.listing_id)
                for item in (analysis.action_items or [])
                if item
            ],
            "source_statuses": source_statuses,
            "raw_payload": analysis.raw_payload or {},
        }

    def _booking_snapshot_fallback_cards(self, limit: int, portfolio_ids: list[int] | None = None) -> list[dict[str, Any]]:
        query = self.session.query(BookingHealthSnapshot).filter(BookingHealthSnapshot.portfolio_id.isnot(None))
        query = filter_portfolio_query(query, BookingHealthSnapshot, self.portfolio_ids)
        query = self._apply_portfolio_scope(query, BookingHealthSnapshot, portfolio_ids)
        latest_snapshot_date = query.with_entities(func.max(BookingHealthSnapshot.snapshot_date)).scalar()
        if not latest_snapshot_date:
            return []
        snapshots = query.filter(BookingHealthSnapshot.snapshot_date == latest_snapshot_date).all()
        grouped: dict[int, list[BookingHealthSnapshot]] = {}
        for snapshot in snapshots:
            grouped.setdefault(snapshot.listing_id, []).append(snapshot)
        cards = []
        for listing_id, rows in grouped.items():
            listing_name = self._listing_display_name(listing_id) or UNKNOWN_LISTING_NAME
            horizons = [_horizon_payload(row) for row in sorted(rows, key=lambda row: row.horizon_days)]
            pattern = _deterministic_booking_pattern(horizons, {})
            cards.append(
                {
                    "portfolio_id": rows[0].portfolio_id,
                    "portfolio_name": self._portfolio_name_map().get(rows[0].portfolio_id),
                    "listing_id": listing_id,
                    "listing_name": listing_name,
                    "snapshot_date": latest_snapshot_date.isoformat(),
                    "severity": _severity_from_horizons(horizons),
                    "confidence": max((row.confidence or 0.0) for row in rows),
                    "horizons": horizons,
                    "booking_pattern": pattern,
                    "pricelabs_opinion": "PriceLabs context has not been analyzed yet.",
                    "airbnb_page_opinion": "Airbnb page context has not been analyzed yet.",
                    "opinion": pattern,
                    "action_items": _deterministic_booking_actions(horizons, {}, {}, {}),
                    "source_statuses": {"analysis": "fallback_from_snapshots"},
                    "raw_payload": {},
                }
            )
        cards.sort(key=lambda item: (_severity_sort_value(item["severity"]), item["listing_name"]))
        return cards[:limit]

    def _today_memory_highlights(self, limit: int = 6, portfolio_ids: list[int] | None = None) -> list[dict[str, Any]]:
        """Return compact WhatsApp memory cards for the Today operating picture."""
        query = self.session.query(WhatsAppGroupMemory).filter(WhatsAppGroupMemory.message_count > 0)
        query = filter_portfolio_query(query, WhatsAppGroupMemory, self.portfolio_ids)
        query = self._apply_portfolio_scope(query, WhatsAppGroupMemory, portfolio_ids)
        latest_memory_date = query.with_entities(func.max(WhatsAppGroupMemory.memory_date)).scalar()
        if not latest_memory_date:
            return []
        memories = query.filter(WhatsAppGroupMemory.memory_date == latest_memory_date).all()
        portfolio_names = self._portfolio_name_map()
        highlights = [
            whatsapp_group_memory_highlight(memory, portfolio_name=portfolio_names.get(memory.portfolio_id))
            for memory in memories
        ]
        highlights = [highlight for highlight in highlights if float(highlight.get("attention_score") or 0.0) > 0.0]
        highlights.sort(key=whatsapp_memory_highlight_sort_key)
        return highlights[:limit]

    def _open_loop_dict(self, loop: OpenLoop, related_signal: Signal | None = None, portfolio_names: dict[int, str] | None = None) -> dict[str, Any]:
        listing_name = self._listing_display_name(loop.listing_id)
        portfolio_names = portfolio_names or self._portfolio_name_map()
        last_seen_at = related_signal.last_seen_at if related_signal and related_signal.last_seen_at else loop.last_activity_at
        rank_score = related_signal.rank_score if related_signal else (loop.confidence or 0.0) * 80
        evidence = sorted(related_signal.evidence, key=lambda item: signal_evidence_sort_key(related_signal.category, item)) if related_signal else []
        evidence_latest = max([item.occurred_at for item in evidence if item.occurred_at], default=None)
        return {
            "kind": "open_loop",
            "open_loop_id": loop.open_loop_id,
            "signal_id": loop.signal_id,
            "portfolio_id": loop.portfolio_id,
            "portfolio_name": portfolio_names.get(loop.portfolio_id, "Unknown portfolio"),
            "listing_id": loop.listing_id,
            "listing_name": listing_name,
            "reservation_id": loop.reservation_id,
            "status": loop.status,
            "severity": related_signal.severity if related_signal else "medium",
            "rank_score": rank_score,
            "title": self._sanitize_listing_text(loop.title, loop.listing_id),
            "summary": self._sanitize_listing_text(loop.summary, loop.listing_id),
            "involved_people": loop.involved_people,
            "last_known_update": self._sanitize_listing_text(loop.last_known_update, loop.listing_id),
            "suggested_next_step": self._sanitize_listing_text(loop.suggested_next_step, loop.listing_id),
            "action": self._sanitize_listing_text(loop.suggested_next_step, loop.listing_id),
            "last_activity_at": loop.last_activity_at.isoformat() if loop.last_activity_at else None,
            "last_seen_at": last_seen_at.isoformat() if last_seen_at else None,
            "latest_evidence_at": evidence_latest.isoformat() if evidence_latest else None,
            "evidence": [self._evidence_dict(item) for item in evidence[:6]],
            "confidence": loop.confidence,
        }

    def _open_loop_signal_dict(self, signal: Signal, portfolio_names: dict[int, str] | None = None) -> dict[str, Any]:
        portfolio_names = portfolio_names or self._portfolio_name_map()
        last_seen = signal.last_seen_at.isoformat() if signal.last_seen_at else None
        evidence = sorted(signal.evidence, key=lambda item: signal_evidence_sort_key(signal.category, item))
        evidence_latest = max([item.occurred_at for item in evidence if item.occurred_at], default=None)
        latest_activity = evidence_latest or signal.last_seen_at
        return {
            "kind": "signal",
            "open_loop_id": None,
            "signal_id": signal.signal_id,
            "portfolio_id": signal.portfolio_id,
            "portfolio_name": portfolio_names.get(signal.portfolio_id, "Unknown portfolio"),
            "listing_id": signal.listing_id,
            "listing_name": self._listing_display_name(signal.listing_id),
            "reservation_id": signal.reservation_id,
            "status": signal.status,
            "severity": signal.severity,
            "rank_score": signal.rank_score,
            "title": self._sanitize_listing_text(signal.title, signal.listing_id),
            "summary": self._sanitize_listing_text(signal.summary, signal.listing_id),
            "involved_people": [],
            "last_known_update": f"Last seen {last_seen}" if last_seen else "",
            "suggested_next_step": self._sanitize_listing_text(signal.suggested_action, signal.listing_id),
            "action": self._sanitize_listing_text(signal.suggested_action, signal.listing_id),
            "last_activity_at": latest_activity.isoformat() if latest_activity else last_seen,
            "last_seen_at": last_seen,
            "latest_evidence_at": evidence_latest.isoformat() if evidence_latest else None,
            "evidence": [self._evidence_dict(item) for item in evidence[:6]],
            "confidence": signal.confidence,
        }

    def _combined_open_loop_items(self, *, limit: int = 100, portfolio_ids: list[int] | None = None) -> list[dict[str, Any]]:
        portfolio_names = self._portfolio_name_map()
        signal_query = self._query_accessible_signals().options(joinedload(Signal.evidence)).filter(
            Signal.status.in_(ACTIVE_SIGNAL_STATUSES),
            Signal.category == "operational_open_loop",
        )
        if portfolio_ids is not None:
            signal_query = signal_query.filter(Signal.portfolio_id.in_(portfolio_ids or [-1]))
        signals = signal_query.order_by(Signal.rank_score.desc(), Signal.last_seen_at.desc()).limit(max(limit, 100)).all()
        signals_by_id = {signal.signal_id: signal for signal in signals}

        loop_query = self._query_accessible_open_loops().filter(OpenLoop.status == "open")
        if portfolio_ids is not None:
            loop_query = loop_query.filter(OpenLoop.portfolio_id.in_(portfolio_ids or [-1]))
        loops = loop_query.order_by(
            OpenLoop.last_activity_at.desc().nullslast(), OpenLoop.created_at.desc()
        ).limit(max(limit, 100)).all()

        linked_signal_ids = {loop.signal_id for loop in loops if loop.signal_id}
        items = [
            self._open_loop_dict(loop, related_signal=signals_by_id.get(loop.signal_id), portfolio_names=portfolio_names)
            for loop in loops
        ]
        items.extend(
            self._open_loop_signal_dict(signal, portfolio_names=portfolio_names)
            for signal in signals
            if signal.signal_id not in linked_signal_ids
        )
        items.sort(key=open_loop_item_sort_key)
        return items[:limit]

    def _brief_dict(self, brief: DailyBrief) -> dict[str, Any]:
        signal_ids = (brief.payload or {}).get("signal_ids", [])
        signal_listing_ids = [
            row[0]
            for row in self.session.query(Signal.listing_id).filter(Signal.signal_id.in_(signal_ids or [-1])).all()
        ]
        listing_names = self._listing_names_for_ids(signal_listing_ids)
        return {
            "daily_brief_id": brief.daily_brief_id,
            "audience": brief.audience,
            "channel": brief.channel,
            "subject": brief.subject,
            "body": sanitize_listing_reference_text(brief.body, listing_names),
            "payload": as_json_safe(brief.payload or {}),
            "status": brief.status,
            "generated_at": brief.generated_at.isoformat() if brief.generated_at else None,
            "sent_at": brief.sent_at.isoformat() if brief.sent_at else None,
        }


class BrainRunService:
    """Runs morning, afternoon, and manual Brain analysis."""

    def __init__(self):
        self.session = get_session()
        self.main_session = get_main_session(config.MAIN_DATABASE_PATH)
        self.pricelabs = PriceLabsClient()
        self._hostaway_listing_client = None
        self._listing_name_cache: dict[int, str] = {}
        self._last_ai_retired_signal_count = 0

    def close(self):
        self.session.close()
        self.main_session.close()

    def _listing_display_name(self, listing_id: int | None) -> str | None:
        if not listing_id:
            return None
        listing_id = int(listing_id)
        if listing_id not in self._listing_name_cache:
            listing = self.main_session.query(Listing).filter(Listing.listing_id == listing_id).first()
            self._listing_name_cache[listing_id] = listing_record_display_name(listing) or UNKNOWN_LISTING_NAME
        return self._listing_name_cache[listing_id]

    def _listing_names_for_ids(self, listing_ids) -> dict[int, str | None]:
        names: dict[int, str | None] = {}
        for listing_id in listing_ids or []:
            if listing_id:
                names[int(listing_id)] = self._listing_display_name(int(listing_id))
        return names

    def _sanitize_listing_text(self, text: str | None, *listing_ids: int | None) -> str:
        return sanitize_listing_reference_text(text, self._listing_names_for_ids(listing_ids))

    def run(self, run_type: str = "manual", deliver: bool = False) -> dict[str, Any]:
        """Run the deterministic MVP signal pipeline."""
        run = SignalRun(run_type=run_type, status="running", prompt_version=SIGNAL_PROMPT_VERSION, model=config.OPENAI_MODEL)
        self.session.add(run)
        self.session.commit()
        try:
            source_refresh = self._refresh_foundation_sources(run.signal_run_id, include_booking_analysis=True)
            active_listing_ids = source_refresh["active_listing_ids"]
            listing_map = source_refresh["listing_map"]
            booking_snapshots = source_refresh["booking_snapshots"]
            source_counts = source_refresh["counts"]
            signal_count = self._generate_deterministic_signals(run.signal_run_id, listing_map, booking_snapshots)
            ai_signal_count = self._generate_ai_signals(run, listing_map)
            signal_count += ai_signal_count
            retired_signal_count = self._retire_stale_deterministic_signals(run.signal_run_id)
            brief_count = self._generate_briefs(run.signal_run_id, deliver=deliver) if should_generate_briefs_for_run_type(run_type) else 0
            from brain.aggregator import BrainDataAggregator

            foundation_result = BrainDataAggregator(session=self.session, main_session=self.main_session).run(
                mode=run_type,
                brain_run_id=run.signal_run_id,
            )
            run.status = "completed"
            run.source_counts = {
                **source_counts,
                "signals": signal_count,
                "ai_signals": ai_signal_count,
                "retired_signals": retired_signal_count,
                "retired_ai_signals": self._last_ai_retired_signal_count,
                "briefs": brief_count,
                "data_foundation_sources": foundation_result.get("source_count", 0),
                "data_foundation_facts": foundation_result.get("facts_written", 0),
                "data_foundation_status": foundation_result.get("status"),
            }
            run.input_hash = stable_hash(run.source_counts)
            run.completed_at = datetime.utcnow()
            self.session.commit()
            return {"signal_run_id": run.signal_run_id, "status": run.status, "signals": signal_count, "briefs": brief_count}
        except Exception as exc:
            logger.error("Brain run failed: %s", exc, exc_info=True)
            run.status = "error"
            run.error_message = str(exc)
            run.completed_at = datetime.utcnow()
            self.session.commit()
            raise

    def refresh_source_snapshots(
        self,
        *,
        run_type: str = "aggregate_pull",
        pull_hostaway: bool = False,
        force_hostaway: bool = False,
        include_booking_analysis: bool = False,
    ) -> dict[str, Any]:
        """Refresh source snapshots used by the data foundation without generating signals or briefs."""
        run = SignalRun(run_type=run_type, status="running", prompt_version="data-foundation-source-refresh", model=None)
        self.session.add(run)
        self.session.commit()
        try:
            source_refresh = self._refresh_foundation_sources(
                run.signal_run_id,
                pull_hostaway=pull_hostaway,
                force_hostaway=force_hostaway,
                include_booking_analysis=include_booking_analysis,
            )
            pull_result = source_refresh["counts"].get("hostaway_incremental_pull")
            run.status = "degraded" if isinstance(pull_result, dict) and pull_result.get("status") == "error" else "completed"
            run.source_counts = source_refresh["counts"]
            run.input_hash = stable_hash(run.source_counts)
            run.completed_at = datetime.utcnow()
            self.session.commit()
            return {
                "signal_run_id": run.signal_run_id,
                "status": run.status,
                "source_counts": run.source_counts,
            }
        except Exception as exc:
            logger.error("Brain source snapshot refresh failed: %s", exc, exc_info=True)
            self.session.rollback()
            persisted_run = self.session.get(SignalRun, run.signal_run_id)
            if persisted_run:
                persisted_run.status = "error"
                persisted_run.error_message = str(exc)
                persisted_run.completed_at = datetime.utcnow()
                self.session.commit()
            raise

    def _refresh_foundation_sources(
        self,
        run_id: int,
        *,
        pull_hostaway: bool = False,
        force_hostaway: bool = False,
        include_booking_analysis: bool = True,
    ) -> dict[str, Any]:
        pull_result = self._pull_hostaway_incremental(
            force=force_hostaway,
            include_messages=True,
            message_recent_activity_hours=None if force_hostaway else config.BRAIN_HOSTAWAY_MESSAGE_TAIL_HOURS,
            message_max_reservations=None if force_hostaway else config.BRAIN_HOSTAWAY_MESSAGE_TAIL_MAX_RESERVATIONS,
        ) if pull_hostaway else None
        active_listing_ids = self._refresh_hostaway_listings(run_id)
        bootstrap_service = BrainService(system_user())
        try:
            bootstrap_service.ensure_default_portfolio(active_listing_ids)
        finally:
            bootstrap_service.close()
        if active_listing_ids:
            self._remove_stale_portfolio_mappings(active_listing_ids)
        listing_map = self._listing_portfolio_map(active_listing_ids)
        self._refresh_calendar_snapshots(run_id, listing_map)
        self._refresh_pricelabs_snapshots(run_id, listing_map)
        booking_snapshots = self._compute_booking_health(run_id, listing_map)
        booking_analysis_count = self._compute_booking_health_analyses(run_id, listing_map, booking_snapshots) if include_booking_analysis else 0
        whatsapp_thread_profile_count = self._sync_whatsapp_thread_profiles()
        whatsapp_memory_count = self._refresh_whatsapp_group_memories(run_id)
        guest_stay_memory_count = self._refresh_guest_stay_memories(run_id, listing_map)
        stay_outcome_result = {"status": "disabled"}
        if (
            getattr(config, "KPI_ENABLE_STAY_OUTCOME_CLASSIFICATION", True)
            and getattr(config, "BRAIN_ENABLE_OPENAI_SIGNALS", True)
        ):
            try:
                from brain.stay_outcomes import StayOutcomeClassificationService

                stay_outcome_result = StayOutcomeClassificationService(
                    session=self.session,
                    main_session=self.main_session,
                ).refresh(
                    run_id=run_id,
                    max_stays=getattr(config, "KPI_STAY_OUTCOME_MAX_PER_RUN", 120),
                )
                stay_outcome_result["status"] = "completed"
            except Exception as exc:
                logger.error("Stay-outcome classification refresh failed: %s", exc, exc_info=True)
                stay_outcome_result = {"status": "error", "error": str(exc)}
        return {
            "active_listing_ids": active_listing_ids,
            "listing_map": listing_map,
            "booking_snapshots": booking_snapshots,
            "counts": {
                "listings": len(listing_map),
                "hostaway_active_listings": len(active_listing_ids or []),
                "hostaway_incremental_pull": pull_result,
                "booking_snapshots": len(booking_snapshots),
                "booking_analyses": booking_analysis_count,
                "whatsapp_thread_profiles": whatsapp_thread_profile_count,
                "whatsapp_group_memories": whatsapp_memory_count,
                "guest_stay_memories": guest_stay_memory_count,
                "stay_outcomes": stay_outcome_result,
            },
        }

    def _pull_hostaway_incremental(
        self,
        *,
        force: bool = False,
        include_messages: bool = False,
        message_recent_activity_hours: int | None = None,
        message_max_reservations: int | None = None,
    ) -> dict[str, Any]:
        try:
            from sync.sync_manager import incremental_sync

            return {
                "status": "completed",
                "forced": bool(force),
                "include_messages": bool(include_messages),
                "message_recent_activity_hours": message_recent_activity_hours,
                "message_max_reservations": message_max_reservations,
                "result": as_json_safe(
                    incremental_sync(
                        force=force,
                        include_messages=include_messages,
                        message_recent_activity_hours=message_recent_activity_hours,
                        message_max_reservations=message_max_reservations,
                    )
                ),
            }
        except Exception as exc:
            logger.warning("Hostaway incremental sync before source refresh failed: %s", exc, exc_info=True)
            return {
                "status": "error",
                "forced": bool(force),
                "include_messages": bool(include_messages),
                "message_recent_activity_hours": message_recent_activity_hours,
                "message_max_reservations": message_max_reservations,
                "error_message": str(exc),
            }

    def _refresh_hostaway_listings(self, run_id: int) -> set[int] | None:
        """Sync listing basics and return current Hostaway listing IDs when available."""
        try:
            from sync.sync_listings import sync_listings

            sync_listings(full_sync=False, sync_run_id=run_id)
        except Exception as exc:
            logger.warning("Hostaway listing sync skipped: %s", exc, exc_info=True)

        try:
            from sync.api_client import HostawayAPIClient

            client = HostawayAPIClient()
            active_ids: set[int] = set()
            limit = 100
            offset = 0
            while True:
                listings = client.get_listings_page(limit=limit, offset=offset)
                if listings is None:
                    raise RuntimeError(f"Hostaway listings request failed at offset {offset}")
                if not listings:
                    break
                for listing in listings:
                    listing_id = listing.get("id") or listing.get("listingId")
                    if listing_id:
                        active_ids.add(int(listing_id))
                if len(listings) < limit:
                    break
                offset += limit
            return active_ids or None
        except Exception as exc:
            logger.warning("Could not fetch current Hostaway listing IDs: %s", exc, exc_info=True)
            return None

    def _remove_stale_portfolio_mappings(self, active_listing_ids: set[int]):
        """Keep Brain portfolio mappings focused on listings Hostaway currently returns."""
        self.session.query(PortfolioListing).filter(
            ~PortfolioListing.listing_id.in_(active_listing_ids)
        ).delete(synchronize_session=False)
        self.session.commit()

    def _listing_portfolio_map(self, active_listing_ids: set[int] | None = None) -> dict[int, int]:
        query = self.session.query(PortfolioListing)
        if active_listing_ids:
            query = query.filter(PortfolioListing.listing_id.in_(active_listing_ids))
        return {row.listing_id: row.portfolio_id for row in query.all()}

    def _refresh_calendar_snapshots(self, run_id: int, listing_map: dict[int, int]):
        try:
            from sync.api_client import HostawayAPIClient

            client = HostawayAPIClient()
        except Exception as exc:
            logger.info("Hostaway calendar snapshot skipped: %s", exc)
            return

        start = date.today()
        end = start + timedelta(days=60)
        for listing_id in listing_map:
            try:
                days = client.get_calendar(listing_id, start.isoformat(), end.isoformat())
            except Exception as exc:
                logger.warning("Calendar fetch failed for listing %s: %s", listing_id, exc)
                continue
            for day in days or []:
                calendar_date = _parse_date(day.get("date"))
                if not calendar_date:
                    continue
                snapshot = self.session.query(CalendarSnapshot).filter(
                    CalendarSnapshot.listing_id == listing_id,
                    CalendarSnapshot.calendar_date == calendar_date,
                    CalendarSnapshot.snapshot_date == start,
                ).first()
                if not snapshot:
                    snapshot = CalendarSnapshot(listing_id=listing_id, calendar_date=calendar_date, snapshot_date=start)
                    self.session.add(snapshot)
                snapshot.run_id = run_id
                snapshot.is_available = _coerce_bool(day.get("isAvailable"))
                snapshot.status = day.get("status")
                snapshot.price = _coerce_float(day.get("price"))
                snapshot.minimum_stay = _coerce_int(day.get("minimumStay"))
                snapshot.maximum_stay = _coerce_int(day.get("maximumStay"))
                snapshot.raw_payload = as_json_safe(day)
        self.session.commit()

    def _refresh_pricelabs_snapshots(self, run_id: int, listing_map: dict[int, int]):
        request_delay = max(0.0, float(os.getenv("PRICELABS_LISTING_DELAY_SECONDS", "0.75")))
        for index, listing_id in enumerate(listing_map):
            snapshot_payload = self.pricelabs.fetch_listing_snapshot(listing_id)
            snapshot = PriceLabsSnapshot(
                run_id=run_id,
                listing_id=listing_id,
                external_listing_id=str(listing_id),
                status=snapshot_payload["status"],
                confidence=snapshot_payload["confidence"],
                raw_payload=snapshot_payload.get("payload"),
                error_message=snapshot_payload.get("error"),
            )
            self.session.add(snapshot)
            if request_delay and index < len(listing_map) - 1:
                time.sleep(request_delay)
        self.session.commit()

    def _compute_booking_health(self, run_id: int, listing_map: dict[int, int]) -> list[BookingHealthSnapshot]:
        today = date.today()
        reservations = self.main_session.query(Reservation).filter(
            Reservation.departure_date >= today,
            Reservation.arrival_date <= today + timedelta(days=60),
        ).all()
        latest_calendar_snapshot_date = self.session.query(func.max(CalendarSnapshot.snapshot_date)).filter(
            CalendarSnapshot.snapshot_date <= today,
            CalendarSnapshot.snapshot_date >= today - timedelta(days=2),
        ).scalar()
        calendar_rows = []
        if latest_calendar_snapshot_date:
            calendar_rows = self.session.query(CalendarSnapshot).filter(
                CalendarSnapshot.snapshot_date == latest_calendar_snapshot_date,
                CalendarSnapshot.calendar_date >= today,
                CalendarSnapshot.calendar_date < today + timedelta(days=60),
            ).all()
        calendar_by_listing: dict[int, list[CalendarSnapshot]] = {}
        for row in calendar_rows:
            calendar_by_listing.setdefault(int(row.listing_id), []).append(row)
        snapshots = []
        for listing_id, portfolio_id in listing_map.items():
            for horizon in (7, 30, 60):
                metrics = compute_booking_health_proxy(
                    reservations,
                    listing_id=listing_id,
                    start_date=today,
                    horizon_days=horizon,
                    calendar_days=calendar_by_listing.get(listing_id, []),
                )
                snapshot = self.session.query(BookingHealthSnapshot).filter(
                    BookingHealthSnapshot.listing_id == listing_id,
                    BookingHealthSnapshot.horizon_days == horizon,
                    BookingHealthSnapshot.snapshot_date == today,
                ).first()
                if not snapshot:
                    snapshot = BookingHealthSnapshot(listing_id=listing_id, horizon_days=horizon, snapshot_date=today)
                    self.session.add(snapshot)
                snapshot.run_id = run_id
                snapshot.portfolio_id = portfolio_id
                snapshot.occupancy_rate = metrics["occupancy_rate"]
                snapshot.booked_nights = metrics["booked_nights"]
                snapshot.available_nights = metrics["available_nights"]
                snapshot.expected_occupancy_rate = metrics["expected_occupancy_rate"]
                snapshot.diagnosis = metrics["diagnosis"]
                snapshot.confidence = metrics["confidence"]
                snapshot.recommended_action = metrics["recommended_action"]
                snapshot.raw_metrics = metrics
                snapshots.append(snapshot)
        self.session.commit()
        return snapshots

    def _compute_booking_health_analyses(
        self,
        run_id: int,
        listing_map: dict[int, int],
        booking_snapshots: list[BookingHealthSnapshot],
    ) -> int:
        """Create one daily booking-health analysis per mapped listing."""
        today = date.today()
        grouped: dict[int, list[BookingHealthSnapshot]] = {}
        for snapshot in booking_snapshots:
            grouped.setdefault(snapshot.listing_id, []).append(snapshot)

        analyses: list[BookingHealthAnalysis] = []
        ai_candidates: list[BookingHealthAnalysis] = []
        for listing_id, portfolio_id in listing_map.items():
            rows = sorted(grouped.get(listing_id, []), key=lambda item: item.horizon_days)
            if not rows:
                continue
            listing_name = self._listing_display_name(listing_id) or UNKNOWN_LISTING_NAME
            analysis = self.session.query(BookingHealthAnalysis).filter(
                BookingHealthAnalysis.listing_id == listing_id,
                BookingHealthAnalysis.snapshot_date == today,
            ).first()
            if not analysis:
                analysis = BookingHealthAnalysis(listing_id=listing_id, snapshot_date=today)
                self.session.add(analysis)
            existing_raw = analysis.raw_payload or {}
            current_ai = (existing_raw.get("ai") or {}) if isinstance(existing_raw, dict) else {}

            horizons = [_horizon_payload(row) for row in rows]
            calendar_context = self._calendar_context(listing_id, today)
            pickup_context = self._booking_pickup_context(listing_id, today)
            pricelabs_snapshot = self._latest_pricelabs_snapshot(listing_id, today)
            pricelabs_context = _pricelabs_context(pricelabs_snapshot, calendar_context)
            airbnb_context = self._daily_airbnb_page_context(listing_id, existing_raw)
            analysis_context = {
                "listing_id": listing_id,
                "listing_name": listing_name,
                "horizons": horizons,
                "booking_pattern_inputs": pickup_context,
                "calendar_proxy": calendar_context,
                "pricelabs": pricelabs_context,
                "airbnb_page": airbnb_context,
            }
            context_hash = stable_hash(analysis_context)
            has_current_ai = (
                current_ai.get("prompt_version") == BOOKING_HEALTH_PROMPT_VERSION
                and current_ai.get("context_hash") == context_hash
            )
            deterministic = _build_deterministic_booking_analysis(
                listing_name=listing_name,
                horizons=horizons,
                pickup_context=pickup_context,
                pricelabs_context=pricelabs_context,
                airbnb_context=airbnb_context,
            )

            analysis.run_id = run_id
            analysis.portfolio_id = portfolio_id
            analysis.listing_name = listing_name
            analysis.horizons = as_json_safe(horizons)
            analysis.confidence = deterministic["confidence"] if not has_current_ai else analysis.confidence
            analysis.severity = deterministic["severity"] if not has_current_ai else analysis.severity
            if not has_current_ai or not analysis.opinion:
                analysis.booking_pattern = deterministic["booking_pattern"]
                analysis.pricelabs_opinion = deterministic["pricelabs_opinion"]
                analysis.airbnb_page_opinion = deterministic["airbnb_page_opinion"]
                analysis.opinion = deterministic["opinion"]
                analysis.action_items = deterministic["action_items"]
                ai_candidates.append(analysis)

            source_statuses = {
                "hostaway": "ok",
                "pricelabs": pricelabs_context.get("status"),
                "airbnb": airbnb_context.get("status"),
                "analysis": "ai_cached" if has_current_ai else "deterministic",
            }
            analysis.source_statuses = source_statuses
            analysis.raw_payload = as_json_safe(
                {
                    "analysis_context": analysis_context,
                    "analysis_context_hash": context_hash,
                    "ai": current_ai if has_current_ai else None,
                }
            )
            analyses.append(analysis)

        self.session.commit()
        self._enhance_booking_health_analyses_with_ai(run_id, ai_candidates)
        self.session.commit()
        return len(analyses)

    def _latest_pricelabs_snapshot(self, listing_id: int, snapshot_date: date) -> PriceLabsSnapshot | None:
        return self.session.query(PriceLabsSnapshot).filter(
            PriceLabsSnapshot.listing_id == listing_id,
            PriceLabsSnapshot.snapshot_date == snapshot_date,
        ).order_by(PriceLabsSnapshot.created_at.desc()).first()

    def _sync_whatsapp_thread_profiles(self) -> int:
        """Attach configured group aliases to canonical names and portfolios."""
        profiles = load_whatsapp_group_profiles()
        if not profiles:
            return 0
        portfolio_ids_by_name = {
            (portfolio.name or "").strip().lower(): portfolio.portfolio_id
            for portfolio in self.session.query(Portfolio).filter(Portfolio.is_active == True).all()  # noqa: E712
        }
        updated = 0
        threads = self.session.query(WhatsAppThread).filter(
            WhatsAppThread.status == "active",
            WhatsAppThread.provider.in_(("whatsapp_web", "twilio")),
        ).all()
        for thread in threads:
            profile = whatsapp_profile_for_group_name(thread.display_name, profiles)
            if not profile:
                continue
            canonical_name = profile.get("name") or thread.display_name
            portfolio_id = portfolio_ids_by_name.get(str(profile.get("portfolio") or "").strip().lower())
            changed = False
            if canonical_name and thread.display_name != canonical_name:
                thread.display_name = canonical_name
                changed = True
            if portfolio_id and thread.portfolio_id != portfolio_id:
                thread.portfolio_id = portfolio_id
                changed = True
            if changed:
                thread.updated_at = datetime.utcnow()
                updated += 1
        if updated:
            self.session.commit()
        return updated

    def _refresh_whatsapp_group_memories(self, run_id: int, window_days: int = 60) -> int:
        """Refresh rolling WhatsApp group memories used by Ask Brain and signal generation."""
        window_end = datetime.utcnow()
        window_start = window_end - timedelta(days=window_days)
        memory_date = window_end.date()
        profiles = load_whatsapp_group_profiles()
        threads = self.session.query(WhatsAppThread).filter(
            WhatsAppThread.status == "active",
            WhatsAppThread.provider.in_(("whatsapp_web", "twilio")),
        ).order_by(WhatsAppThread.display_name).all()
        thread_groups: dict[str, list[WhatsAppThread]] = {}
        for thread in threads:
            thread_groups.setdefault(whatsapp_thread_memory_key(thread, profiles), []).append(thread)
        count = 0
        for group_threads in thread_groups.values():
            thread_ids = [thread.whatsapp_thread_id for thread in group_threads]
            messages = self.session.query(WhatsAppMessage).filter(
                WhatsAppMessage.whatsapp_thread_id.in_(thread_ids),
                WhatsAppMessage.received_at >= window_start,
                WhatsAppMessage.received_at <= window_end,
                WhatsAppMessage.body.isnot(None),
            ).order_by(WhatsAppMessage.received_at.asc()).limit(5000).all()
            if not messages:
                continue
            message_counts = Counter(message.whatsapp_thread_id for message in messages)
            thread = sorted(
                group_threads,
                key=lambda item: (-message_counts.get(item.whatsapp_thread_id, 0), item.whatsapp_thread_id),
            )[0]
            payload = build_whatsapp_group_memory_payload(
                thread=thread,
                messages=messages,
                window_start=window_start,
                window_end=window_end,
                window_days=window_days,
            )
            payload["source_metadata"]["merged_thread_ids"] = thread_ids
            payload["source_metadata"]["merged_provider_thread_ids"] = [
                item.provider_thread_id for item in group_threads if item.provider_thread_id
            ]
            if len(group_threads) > 1:
                stale_thread_ids = [thread_id for thread_id in thread_ids if thread_id != thread.whatsapp_thread_id]
                self.session.query(WhatsAppGroupMemory).filter(
                    WhatsAppGroupMemory.whatsapp_thread_id.in_(stale_thread_ids),
                    WhatsAppGroupMemory.window_days == window_days,
                    WhatsAppGroupMemory.memory_date == memory_date,
                ).delete(synchronize_session=False)
            memory = self.session.query(WhatsAppGroupMemory).filter(
                WhatsAppGroupMemory.whatsapp_thread_id == thread.whatsapp_thread_id,
                WhatsAppGroupMemory.window_days == window_days,
                WhatsAppGroupMemory.memory_date == memory_date,
            ).first()
            if not memory:
                memory = WhatsAppGroupMemory(
                    whatsapp_thread_id=thread.whatsapp_thread_id,
                    window_days=window_days,
                    memory_date=memory_date,
                )
                self.session.add(memory)
            memory.run_id = run_id
            memory.portfolio_id = thread.portfolio_id
            memory.provider = thread.provider
            memory.provider_thread_id = thread.provider_thread_id
            memory.group_name = thread.display_name
            memory.group_kind = payload["group_kind"]
            memory.window_start_at = window_start
            memory.window_end_at = window_end
            memory.message_count = payload["message_count"]
            memory.participant_count = payload["participant_count"]
            memory.last_message_at = payload["last_message_at"]
            memory.status = payload["status"]
            memory.summary = payload["summary"]
            memory.open_loop_summary = payload["open_loop_summary"]
            memory.risk_summary = payload["risk_summary"]
            memory.decision_summary = payload["decision_summary"]
            memory.cleaning_maintenance_summary = payload["cleaning_maintenance_summary"]
            memory.memory_hash = payload["memory_hash"]
            memory.source_metadata = as_json_safe(payload["source_metadata"])
            memory.updated_at = window_end
            count += 1
        self.session.commit()
        return count

    def _refresh_guest_stay_memories(self, run_id: int, listing_map: dict[int, int], window_days: int = 60) -> int:
        """Refresh reservation-level Hostaway guest memory used for review-risk detection."""
        if not listing_map:
            return 0
        window_end = datetime.utcnow()
        window_start = window_end - timedelta(days=window_days)
        memory_date = window_end.date()
        listing_ids = list(listing_map)

        recent_message_reservation_ids = {
            int(row[0])
            for row in self.main_session.query(MessageMetadata.reservation_id).filter(
                MessageMetadata.listing_id.in_(listing_ids),
                MessageMetadata.reservation_id.isnot(None),
                MessageMetadata.created_at >= window_start,
            ).distinct().all()
            if row[0]
        }
        recent_review_reservation_ids = {
            int(row[0])
            for row in self.main_session.query(Review.reservation_id).filter(
                Review.listing_id.in_(listing_ids),
                Review.reservation_id.isnot(None),
                Review.review_date >= window_start.date(),
            ).distinct().all()
            if row[0]
        }
        candidate_reservation_ids = recent_message_reservation_ids | recent_review_reservation_ids
        stay_window_end = window_end.date() + timedelta(days=14)
        date_overlap = or_(
            Reservation.arrival_date.between(window_start.date(), stay_window_end),
            Reservation.departure_date.between(window_start.date(), stay_window_end),
            Reservation.latest_activity_on >= window_start,
        )
        query = self.main_session.query(Reservation).filter(Reservation.listing_id.in_(listing_ids))
        if candidate_reservation_ids:
            query = query.filter(or_(Reservation.reservation_id.in_(candidate_reservation_ids), date_overlap))
        else:
            query = query.filter(date_overlap)
        reservations = query.order_by(
            Reservation.departure_date.desc().nullslast(),
            Reservation.latest_activity_on.desc().nullslast(),
        ).limit(2000).all()

        count = 0
        for reservation in reservations:
            messages = self.main_session.query(MessageMetadata).filter(
                MessageMetadata.reservation_id == reservation.reservation_id,
                MessageMetadata.created_at >= window_start,
                MessageMetadata.created_at <= window_end,
                MessageMetadata.content_preview.isnot(None),
            ).order_by(MessageMetadata.created_at.asc()).limit(600).all()
            reviews = self.main_session.query(Review).filter(
                Review.reservation_id == reservation.reservation_id,
                Review.listing_id == reservation.listing_id,
            ).order_by(Review.review_date.desc().nullslast(), Review.inserted_on.desc().nullslast()).limit(5).all()
            if not messages and not reviews:
                continue
            portfolio_id = listing_map.get(reservation.listing_id)
            if not portfolio_id:
                continue
            listing_name = self._listing_display_name(reservation.listing_id) or UNKNOWN_LISTING_NAME
            payload = build_guest_stay_memory_payload(
                reservation=reservation,
                messages=messages,
                reviews=reviews,
                listing_name=listing_name,
                portfolio_name=self._portfolio_name_for_id(portfolio_id),
                window_start=window_start,
                window_end=window_end,
                window_days=window_days,
            )
            memory = self.session.query(GuestStayMemory).filter(
                GuestStayMemory.reservation_id == reservation.reservation_id,
                GuestStayMemory.memory_date == memory_date,
            ).first()
            if not memory:
                memory = GuestStayMemory(reservation_id=reservation.reservation_id, memory_date=memory_date, listing_id=reservation.listing_id)
                self.session.add(memory)
            latest_review = reviews[0] if reviews else None
            memory.run_id = run_id
            memory.portfolio_id = portfolio_id
            memory.listing_id = reservation.listing_id
            memory.guest_id = reservation.guest_id
            memory.guest_name = payload["guest_name"]
            memory.channel_name = reservation.channel_name or reservation.source
            memory.arrival_date = reservation.arrival_date
            memory.departure_date = reservation.departure_date
            memory.reservation_status = reservation.status
            memory.window_start_at = window_start
            memory.window_end_at = window_end
            memory.message_count = payload["message_count"]
            memory.incoming_count = payload["incoming_count"]
            memory.last_message_at = payload["last_message_at"]
            memory.review_id = latest_review.review_id if latest_review else None
            memory.review_rating = latest_review.overall_rating if latest_review else None
            memory.review_date = latest_review.review_date if latest_review else None
            memory.status = payload["status"]
            memory.risk_level = payload["risk_level"]
            memory.risk_score = payload["risk_score"]
            memory.summary = payload["summary"]
            memory.risk_summary = payload["risk_summary"]
            memory.latest_guest_issue = payload["latest_guest_issue"]
            memory.resolution_summary = payload["resolution_summary"]
            memory.suggested_action = payload["suggested_action"]
            memory.memory_hash = payload["memory_hash"]
            memory.source_metadata = as_json_safe(payload["source_metadata"])
            memory.updated_at = window_end
            count += 1
        self.session.commit()
        return count

    def _portfolio_name_for_id(self, portfolio_id: int | None) -> str | None:
        if not portfolio_id:
            return None
        row = self.session.query(Portfolio.name).filter(Portfolio.portfolio_id == portfolio_id).first()
        return row[0] if row else None

    def _calendar_context(self, listing_id: int, snapshot_date: date) -> dict[str, Any]:
        rows = self.session.query(CalendarSnapshot).filter(
            CalendarSnapshot.listing_id == listing_id,
            CalendarSnapshot.snapshot_date == snapshot_date,
            CalendarSnapshot.calendar_date >= snapshot_date,
            CalendarSnapshot.calendar_date < snapshot_date + timedelta(days=60),
        ).order_by(CalendarSnapshot.calendar_date).all()
        return _calendar_context(rows)

    def _booking_pickup_context(self, listing_id: int, today: date) -> dict[str, Any]:
        future = self.main_session.query(Reservation).filter(
            Reservation.listing_id == listing_id,
            Reservation.departure_date >= today,
            Reservation.arrival_date <= today + timedelta(days=60),
        ).all()
        active = [row for row in future if is_confirmed_reservation_status(row.status)]
        recently_booked_7 = [
            row for row in active
            if row.reservation_date and row.reservation_date.date() >= today - timedelta(days=7)
        ]
        recently_booked_14 = [
            row for row in active
            if row.reservation_date and row.reservation_date.date() >= today - timedelta(days=14)
        ]
        channel_counts: dict[str, int] = {}
        revenue = 0.0
        for row in active:
            channel = row.channel_name or row.source or "unknown"
            channel_counts[channel] = channel_counts.get(channel, 0) + 1
            revenue += float(row.total_price or 0.0)
        next_arrival = min((row.arrival_date for row in active if row.arrival_date), default=None)
        return {
            "future_reservation_count": len(active),
            "booked_last_7_days": len(recently_booked_7),
            "booked_last_14_days": len(recently_booked_14),
            "next_arrival": next_arrival.isoformat() if next_arrival else None,
            "channel_mix": channel_counts,
            "future_60_day_revenue": round(revenue, 2),
        }

    def _daily_airbnb_page_context(self, listing_id: int, existing_raw: dict[str, Any]) -> dict[str, Any]:
        today_iso = date.today().isoformat()
        existing = ((existing_raw or {}).get("analysis_context") or {}).get("airbnb_page") or {}
        if existing.get("snapshot_date") == today_iso:
            return existing
        url = self._find_airbnb_url(listing_id)
        if not url:
            return {"status": "missing_url", "snapshot_date": today_iso, "url": None, "summary": "No Airbnb URL found for this listing."}
        try:
            response = requests.get(
                url,
                headers={
                    "User-Agent": "Mozilla/5.0 STR Signal Brain booking-health analyzer",
                    "Accept-Language": "en-US,en;q=0.9",
                },
                timeout=10,
            )
            response.raise_for_status()
            return _airbnb_page_context(url, response.text, response.status_code)
        except Exception as exc:
            logger.info("Airbnb page fetch unavailable for %s: %s", listing_id, exc)
            return {
                "status": "fetch_unavailable",
                "snapshot_date": today_iso,
                "url": url,
                "summary": "Airbnb page could not be fetched today.",
                "error": str(exc),
            }

    def _find_airbnb_url(self, listing_id: int) -> str | None:
        listing = self.main_session.query(Listing).filter(Listing.listing_id == listing_id).first()
        for source in (
            getattr(listing, "custom_fields", None),
            getattr(listing, "description", None),
        ):
            url = _extract_airbnb_url(source)
            if url:
                return url
        try:
            from sync.api_client import HostawayAPIClient

            if self._hostaway_listing_client is None:
                self._hostaway_listing_client = HostawayAPIClient()
            data = self._hostaway_listing_client.get_listing(listing_id)
            return _extract_airbnb_url(data) or (data or {}).get("airbnbListingUrl")
        except Exception as exc:
            logger.info("Hostaway listing detail fetch skipped for Airbnb URL %s: %s", listing_id, exc)
            return None

    def _enhance_booking_health_analyses_with_ai(self, run_id: int, analyses: list[BookingHealthAnalysis]):
        if not analyses or not getattr(config, "BRAIN_ENABLE_OPENAI_BOOKING_HEALTH", True):
            return
        properties = []
        for analysis in analyses:
            context = (analysis.raw_payload or {}).get("analysis_context") or {}
            if context:
                properties.append(context)
        if not properties:
            return
        context = {
            "today": date.today().isoformat(),
            "instruction": (
                "Return one booking health analysis per property. Consider booking pattern, 7/30/60 day occupancy, "
                "PriceLabs settings/status, Hostaway calendar proxy, and Airbnb page status. Give concrete revenue "
                "management actions. If a source is missing, say how that limits confidence."
            ),
            "properties": properties,
        }
        input_hash = stable_hash(context)
        artifact = PromptArtifact(
            run_id=run_id,
            prompt_version=BOOKING_HEALTH_PROMPT_VERSION,
            model=config.OPENAI_MODEL,
            input_hash=input_hash,
            input_payload=context,
            status="running",
        )
        self.session.add(artifact)
        self.session.commit()
        try:
            result = BrainAIClient().generate_booking_health_analyses(context)
            artifact.output_payload = as_json_safe(result)
            artifact.usage = result.get("_usage")
            artifact.status = "ok"
            result_by_listing = {
                int(item["listing_id"]): item
                for item in result.get("analyses", [])
                if str(item.get("listing_id", "")).isdigit()
            }
            for analysis in analyses:
                item = result_by_listing.get(analysis.listing_id)
                if not item:
                    continue
                listing_id = analysis.listing_id
                analysis.severity = _normalize_booking_severity(item.get("severity"), analysis.severity)
                analysis.confidence = max(0.0, min(float(item.get("confidence") or analysis.confidence or 0.45), 1.0))
                analysis.booking_pattern = self._sanitize_listing_text(item.get("booking_pattern"), listing_id)
                analysis.pricelabs_opinion = self._sanitize_listing_text(item.get("pricelabs_opinion"), listing_id)
                analysis.airbnb_page_opinion = self._sanitize_listing_text(item.get("airbnb_page_opinion"), listing_id)
                analysis.opinion = self._sanitize_listing_text(item.get("opinion"), listing_id)
                analysis.action_items = [
                    self._sanitize_listing_text(action, listing_id)
                    for action in (item.get("action_items") or [])[:6]
                    if action
                ]
                raw_payload = dict(analysis.raw_payload or {})
                raw_payload["ai"] = {
                    "prompt_version": BOOKING_HEALTH_PROMPT_VERSION,
                    "prompt_artifact_id": artifact.prompt_artifact_id,
                    "context_hash": stable_hash(raw_payload.get("analysis_context") or {}),
                    "generated_at": datetime.utcnow().isoformat(),
                }
                analysis.raw_payload = as_json_safe(raw_payload)
                source_statuses = dict(analysis.source_statuses or {})
                source_statuses["analysis"] = "ai_enhanced"
                analysis.source_statuses = source_statuses
            self.session.commit()
        except Exception as exc:
            logger.warning("Booking-health AI analysis failed: %s", exc, exc_info=True)
            artifact.status = "error"
            artifact.error_message = str(exc)
            self.session.commit()

    def _generate_deterministic_signals(
        self,
        run_id: int,
        listing_map: dict[int, int],
        booking_snapshots: list[BookingHealthSnapshot],
    ) -> int:
        count = self._generate_booking_health_signals_from_analyses(run_id, booking_snapshots)

        count += self._generate_guest_stay_memory_signals(run_id)
        count += self._generate_message_risk_signals(run_id, listing_map)
        count += self._generate_whatsapp_open_loop_signals(run_id, listing_map)
        self.session.commit()
        return count

    def _generate_booking_health_signals_from_analyses(
        self,
        run_id: int,
        booking_snapshots: list[BookingHealthSnapshot],
    ) -> int:
        """Promote the per-property booking-health analysis into one signal."""
        if not booking_snapshots:
            return 0
        grouped_snapshots: dict[int, list[BookingHealthSnapshot]] = {}
        for snapshot in booking_snapshots:
            grouped_snapshots.setdefault(snapshot.listing_id, []).append(snapshot)

        analyses = self.session.query(BookingHealthAnalysis).filter(
            BookingHealthAnalysis.run_id == run_id,
            BookingHealthAnalysis.listing_id.in_(list(grouped_snapshots)),
        ).all()
        count = 0
        for analysis in analyses:
            fields = booking_health_signal_fields(analysis)
            if not fields:
                continue
            evidence = [
                {
                    "source_type": "booking_health_analysis",
                    "source_id": str(analysis.booking_health_analysis_id),
                    "listing_id": analysis.listing_id,
                    "reservation_id": None,
                    "summary": fields["summary"],
                    "excerpt": fields["suggested_action"],
                    "metadata": {
                        "portfolio_id": analysis.portfolio_id,
                        "source_statuses": analysis.source_statuses or {},
                    },
                }
            ]
            for snapshot in sorted(grouped_snapshots.get(analysis.listing_id, []), key=lambda item: item.horizon_days):
                if snapshot.diagnosis == "healthy":
                    continue
                evidence.append(
                    {
                        "source_type": "booking_health",
                        "source_id": str(snapshot.booking_health_snapshot_id),
                        "listing_id": snapshot.listing_id,
                        "reservation_id": None,
                        "summary": (
                            f"{snapshot.horizon_days}-day booking pace: "
                            f"{round((snapshot.occupancy_rate or 0.0) * 100)}% booked, "
                            f"{snapshot.available_nights} open nights."
                        ),
                        "excerpt": snapshot.recommended_action,
                        "metadata": {
                            "portfolio_id": snapshot.portfolio_id,
                            "horizon_days": snapshot.horizon_days,
                            "diagnosis": snapshot.diagnosis,
                            "expected_occupancy_rate": snapshot.expected_occupancy_rate,
                        },
                    }
                )
            self._upsert_signal(
                run_id=run_id,
                portfolio_id=analysis.portfolio_id,
                listing_id=analysis.listing_id,
                reservation_id=None,
                category="revenue_booking_health",
                severity=fields["severity"],
                confidence=fields["confidence"],
                title=fields["title"],
                summary=fields["summary"],
                why_it_matters=fields["why_it_matters"],
                suggested_action=fields["suggested_action"],
                audience="revenue",
                source="deterministic_booking_health",
                evidence_entries=evidence,
                rank_score=booking_health_rank_score(analysis),
            )
            count += 1
        return count

    def _retire_stale_deterministic_signals(self, run_id: int) -> int:
        """Resolve recomputed deterministic signals that did not reappear this run."""
        stale_signals = self.session.query(Signal).filter(
            Signal.source.in_(STALE_RECOMPUTED_SIGNAL_SOURCES),
            Signal.status.in_(ACTIVE_SIGNAL_STATUSES),
            Signal.status != "escalated",
            or_(Signal.run_id.is_(None), Signal.run_id != run_id),
        ).all()
        retired_at = datetime.utcnow()
        count = 0
        for signal in stale_signals:
            if not should_retire_deterministic_signal(signal, run_id):
                continue
            signal.status = "resolved"
            signal.resolved_at = retired_at
            signal.updated_at = retired_at
            count += 1
        return count

    def _retire_stale_openai_signals(self, run_id: int) -> int:
        """Resolve older OpenAI signals only after a successful current OpenAI pass."""
        stale_signals = self.session.query(Signal).filter(
            Signal.source == OPENAI_SIGNAL_SOURCE,
            Signal.status.in_(ACTIVE_SIGNAL_STATUSES),
            Signal.status != "escalated",
            or_(Signal.run_id.is_(None), Signal.run_id != run_id),
        ).all()
        retired_at = datetime.utcnow()
        count = 0
        for signal in stale_signals:
            if not should_retire_openai_signal(signal, run_id):
                continue
            signal.status = "resolved"
            signal.resolved_at = retired_at
            signal.updated_at = retired_at
            count += 1
        return count

    def _generate_ai_signals(self, run: SignalRun, listing_map: dict[int, int]) -> int:
        """Ask OpenAI for evidence-backed signal candidates and persist cited ones only."""
        if not getattr(config, "BRAIN_ENABLE_OPENAI_SIGNALS", True):
            return 0
        evidence_rows = self._build_ai_evidence_pool(run.signal_run_id, listing_map)
        if not evidence_rows:
            return 0
        context = {
            "today": date.today().isoformat(),
            "instruction": "Return only signals that need human attention and cite evidence_ids from the provided evidence. When referring to a property, use listing_name and never write 'Listing <id>'.",
            "evidence": [self._evidence_for_prompt(row) for row in evidence_rows],
        }
        input_hash = stable_hash(context)
        artifact = PromptArtifact(
            run_id=run.signal_run_id,
            prompt_version=SIGNAL_PROMPT_VERSION,
            model=config.OPENAI_MODEL,
            input_hash=input_hash,
            input_payload=context,
            status="running",
        )
        self.session.add(artifact)
        self.session.commit()
        try:
            result = BrainAIClient().generate_signals(context)
            artifact.output_payload = as_json_safe(result)
            artifact.usage = result.get("_usage")
            artifact.status = "ok"
            run.raw_output = as_json_safe(result)
            run.usage = result.get("_usage")
            count = self._persist_ai_signal_candidates(run.signal_run_id, result.get("signals", []), evidence_rows)
            self._last_ai_retired_signal_count = self._retire_stale_openai_signals(run.signal_run_id)
            self.session.commit()
            return count
        except Exception as exc:
            logger.warning("OpenAI signal generation failed: %s", exc, exc_info=True)
            artifact.status = "error"
            artifact.error_message = str(exc)
            self.session.commit()
            return 0

    def _build_ai_evidence_pool(self, run_id: int, listing_map: dict[int, int]) -> list[SignalEvidence]:
        """Create an evidence pool for model-aided signals."""
        evidence_rows: list[SignalEvidence] = []
        cutoff = datetime.utcnow() - timedelta(days=7)

        messages = self.main_session.query(MessageMetadata).filter(
            MessageMetadata.created_at >= cutoff,
            MessageMetadata.content_preview.isnot(None),
        ).order_by(MessageMetadata.created_at.desc()).limit(120).all()
        for message in messages:
            if message.listing_id not in listing_map:
                continue
            evidence_rows.append(
                self._get_or_create_evidence(
                    source_type="hostaway_message",
                    source_id=str(message.message_id),
                    listing_id=message.listing_id,
                    reservation_id=message.reservation_id,
                    occurred_at=message.created_at,
                    summary="Recent Hostaway guest message.",
                    excerpt=message.content_preview,
                    metadata={"is_incoming": bool(message.is_incoming), "sender_type": message.sender_type},
                )
            )

        review_cutoff = date.today() - timedelta(days=60)
        reviews = self.main_session.query(Review).filter(
            Review.review_date >= review_cutoff,
            Review.review_text.isnot(None),
        ).order_by(Review.review_date.desc()).limit(80).all()
        for review in reviews:
            if review.listing_id not in listing_map:
                continue
            evidence_rows.append(
                self._get_or_create_evidence(
                    source_type="hostaway_review",
                    source_id=str(review.review_id),
                    listing_id=review.listing_id,
                    reservation_id=review.reservation_id,
                    occurred_at=datetime.combine(review.review_date, datetime.min.time()) if review.review_date else None,
                    summary=f"Recent review rated {review.overall_rating or 'unknown'} stars.",
                    excerpt=review.review_text,
                    metadata={"overall_rating": review.overall_rating, "channel_name": review.channel_name},
                )
            )

        latest_guest_memory_date = self.session.query(func.max(GuestStayMemory.memory_date)).scalar()
        if latest_guest_memory_date:
            guest_memories = self.session.query(GuestStayMemory).filter(
                GuestStayMemory.memory_date == latest_guest_memory_date,
                GuestStayMemory.portfolio_id.isnot(None),
                or_(GuestStayMemory.message_count > 0, GuestStayMemory.review_id.isnot(None)),
            ).order_by(GuestStayMemory.risk_score.desc(), GuestStayMemory.last_message_at.desc().nullslast()).limit(120).all()
            for memory in guest_memories:
                if memory.listing_id not in listing_map:
                    continue
                evidence_rows.append(
                    self._get_or_create_evidence(
                        source_type="guest_stay_memory",
                        source_id=str(memory.guest_stay_memory_id),
                        listing_id=memory.listing_id,
                        reservation_id=memory.reservation_id,
                        occurred_at=memory.last_message_at or datetime.combine(memory.memory_date, datetime.min.time()),
                        summary=f"Guest stay memory risk level {memory.risk_level} for {memory.guest_name or 'guest'}.",
                        excerpt=guest_stay_memory_excerpt(memory),
                        metadata={
                            "portfolio_id": memory.portfolio_id,
                            "guest_name": memory.guest_name,
                            "risk_level": memory.risk_level,
                            "risk_score": memory.risk_score,
                            "status": memory.status,
                            "review_rating": memory.review_rating,
                            "arrival_date": memory.arrival_date.isoformat() if memory.arrival_date else None,
                            "departure_date": memory.departure_date.isoformat() if memory.departure_date else None,
                        },
                    )
                )

        whatsapp = self.session.query(WhatsAppMessage).options(joinedload(WhatsAppMessage.thread)).filter(
            WhatsAppMessage.received_at >= cutoff,
            WhatsAppMessage.body.isnot(None),
        ).order_by(WhatsAppMessage.received_at.desc()).limit(240).all()
        for message in whatsapp:
            thread = message.thread
            thread_name = thread.display_name if thread else None
            portfolio_id = thread.portfolio_id if thread else None
            raw_payload = message.raw_payload or {}
            evidence_rows.append(
                self._get_or_create_evidence(
                    source_type="whatsapp_message",
                    source_id=str(message.whatsapp_message_id),
                    occurred_at=message.received_at,
                    summary=f"Recent team WhatsApp message in {thread_name or 'team chat'}.",
                    excerpt=message.body,
                    metadata={
                        "from_number": message.from_number,
                        "sender_name": message.sender_name,
                        "thread_id": thread.provider_thread_id if thread else None,
                        "thread_name": thread_name,
                        "portfolio_id": portfolio_id,
                        "group_kind": raw_payload.get("group_kind"),
                    },
                )
            )

        latest_memory_date = self.session.query(func.max(WhatsAppGroupMemory.memory_date)).scalar()
        if latest_memory_date:
            memories = self.session.query(WhatsAppGroupMemory).filter(
                WhatsAppGroupMemory.memory_date == latest_memory_date,
                WhatsAppGroupMemory.message_count > 0,
            ).order_by(WhatsAppGroupMemory.last_message_at.desc().nullslast()).limit(80).all()
            for memory in memories:
                evidence_rows.append(
                    self._get_or_create_evidence(
                        source_type="whatsapp_group_memory",
                        source_id=str(memory.whatsapp_group_memory_id),
                        occurred_at=memory.last_message_at or memory.window_end_at,
                        summary=f"WhatsApp group memory for {memory.group_name or 'team chat'}.",
                        excerpt=_whatsapp_group_memory_excerpt(memory),
                        metadata={
                            "portfolio_id": memory.portfolio_id,
                            "thread_id": memory.provider_thread_id,
                            "thread_name": memory.group_name,
                            "group_kind": memory.group_kind,
                            "window_days": memory.window_days,
                            "memory_date": memory.memory_date.isoformat() if memory.memory_date else None,
                            "message_count": memory.message_count,
                            "participant_count": memory.participant_count,
                            "status": memory.status,
                        },
                    )
                )

        booking = self.session.query(BookingHealthSnapshot).filter(
            BookingHealthSnapshot.run_id == run_id,
            BookingHealthSnapshot.diagnosis != "healthy",
        ).order_by(BookingHealthSnapshot.confidence.desc()).limit(120).all()
        for snapshot in booking:
            evidence_rows.append(
                self._get_or_create_evidence(
                    source_type="booking_health",
                    source_id=str(snapshot.booking_health_snapshot_id),
                    listing_id=snapshot.listing_id,
                    occurred_at=datetime.utcnow(),
                    summary=f"{snapshot.horizon_days}-day booking health is {snapshot.diagnosis}.",
                    excerpt=snapshot.recommended_action,
                    metadata=snapshot.raw_metrics,
                )
            )
        self.session.commit()
        return [row for row in evidence_rows if row and row.evidence_id]

    def _persist_ai_signal_candidates(
        self,
        run_id: int,
        candidates: list[dict[str, Any]],
        evidence_rows: list[SignalEvidence],
    ) -> int:
        evidence_by_id = {row.evidence_id: row for row in evidence_rows}
        deterministic_revenue_listing_ids = {
            int(row[0])
            for row in self.session.query(Signal.listing_id).filter(
                Signal.run_id == run_id,
                Signal.source == "deterministic_booking_health",
                Signal.category == "revenue_booking_health",
                Signal.listing_id.isnot(None),
            ).all()
        }
        count = 0
        for candidate in candidates or []:
            evidence_ids = [int(item) for item in candidate.get("evidence_ids", []) if str(item).isdigit()]
            cited_evidence = [evidence_by_id[item] for item in evidence_ids if item in evidence_by_id]
            if not cited_evidence:
                continue
            listing_id = candidate.get("listing_id") or cited_evidence[0].listing_id
            reservation_id = candidate.get("reservation_id") or cited_evidence[0].reservation_id
            portfolio_id = candidate.get("portfolio_id")
            if listing_id:
                mapping = self.session.query(PortfolioListing).filter(PortfolioListing.listing_id == listing_id).first()
                portfolio_id = mapping.portfolio_id if mapping else None
            if portfolio_id is None:
                for evidence in cited_evidence:
                    try:
                        metadata_portfolio_id = (evidence.evidence_metadata or {}).get("portfolio_id")
                        if metadata_portfolio_id:
                            portfolio_id = int(metadata_portfolio_id)
                            break
                    except (TypeError, ValueError):
                        continue
            if portfolio_id is None:
                portfolio_id = self.session.query(Portfolio.portfolio_id).order_by(Portfolio.portfolio_id).scalar()
            category = ai_candidate_category(candidate, cited_evidence)
            if should_skip_ai_signal_candidate(
                candidate,
                cited_evidence,
                category,
                listing_id=listing_id,
                deterministic_revenue_listing_ids=deterministic_revenue_listing_ids,
            ):
                continue
            signal = self._upsert_signal_from_existing_evidence(
                run_id=run_id,
                portfolio_id=portfolio_id,
                listing_id=listing_id,
                reservation_id=reservation_id,
                category=category,
                severity=candidate.get("severity", "medium"),
                confidence=float(candidate.get("confidence") or 0.55),
                title=candidate.get("title") or "Brain signal",
                summary=candidate.get("summary") or "",
                why_it_matters=candidate.get("why_it_matters") or "",
                suggested_action=candidate.get("suggested_action") or "",
                audience=candidate.get("audience") or candidate.get("owner_or_manager") or "operator",
                source="openai_structured",
                evidence_rows=cited_evidence,
                rank_score=ai_structured_signal_rank_score(candidate, cited_evidence, category),
            )
            count += 1 if signal else 0
        return count

    def _generate_guest_stay_memory_signals(self, run_id: int) -> int:
        """Promote reservation-level guest memories into review-risk signals."""
        latest_memory_date = self.session.query(func.max(GuestStayMemory.memory_date)).scalar()
        if not latest_memory_date:
            return 0
        memories = self.session.query(GuestStayMemory).filter(
            GuestStayMemory.memory_date == latest_memory_date,
            GuestStayMemory.status == "watch",
            GuestStayMemory.portfolio_id.isnot(None),
        ).order_by(GuestStayMemory.risk_score.desc(), GuestStayMemory.last_message_at.desc().nullslast()).limit(80).all()
        count = 0
        for memory in memories:
            listing_name = self._listing_display_name(memory.listing_id) or UNKNOWN_LISTING_NAME
            guest_label = memory.guest_name or "Guest"
            severity = guest_stay_signal_severity(memory)
            title = f"{guest_label} may become a review risk at {listing_name}"
            evidence = [
                {
                    "source_type": "guest_stay_memory",
                    "source_id": str(memory.guest_stay_memory_id),
                    "listing_id": memory.listing_id,
                    "reservation_id": memory.reservation_id,
                    "occurred_at": memory.last_message_at or datetime.combine(memory.memory_date, datetime.min.time()),
                    "summary": f"Guest stay memory risk level: {memory.risk_level}.",
                    "excerpt": guest_stay_memory_excerpt(memory),
                    "metadata": {
                        "portfolio_id": memory.portfolio_id,
                        "guest_name": memory.guest_name,
                        "arrival_date": memory.arrival_date.isoformat() if memory.arrival_date else None,
                        "departure_date": memory.departure_date.isoformat() if memory.departure_date else None,
                        "risk_level": memory.risk_level,
                        "risk_score": memory.risk_score,
                        "status": memory.status,
                        "review_rating": memory.review_rating,
                    },
                }
            ]
            self._upsert_signal(
                run_id=run_id,
                portfolio_id=memory.portfolio_id,
                listing_id=memory.listing_id,
                reservation_id=memory.reservation_id,
                category="review_risk",
                severity=severity,
                confidence=guest_stay_signal_confidence(memory),
                title=title,
                summary=memory.risk_summary or memory.summary,
                why_it_matters=(
                    "Brain sees this as a stay-level risk, not just a single keyword: "
                    "guest language, timing, and closure signals suggest the experience may still need recovery."
                ),
                suggested_action=memory.suggested_action or "Confirm the issue is resolved in Hostaway and send a clear guest-facing recovery note.",
                audience="operator",
                source="deterministic_guest_stay_memory",
                evidence_entries=evidence,
                rank_score=guest_stay_memory_rank_score(memory),
            )
            count += 1
        return count

    def _generate_message_risk_signals(self, run_id: int, listing_map: dict[int, int]) -> int:
        cutoff = datetime.utcnow() - timedelta(days=3)
        risky_terms = ("not working", "broken", "dirty", "refund", "angry", "disappointed", "locked out", "no access")
        messages = self.main_session.query(MessageMetadata).filter(MessageMetadata.created_at >= cutoff).order_by(
            MessageMetadata.created_at.desc()
        ).limit(500).all()
        count = 0
        seen_stays: set[tuple[int | None, int | None]] = set()
        latest_memory_date = self.session.query(func.max(GuestStayMemory.memory_date)).scalar()
        memory_signal_reservations = set()
        if latest_memory_date:
            memory_signal_reservations = {
                int(row[0])
                for row in self.session.query(GuestStayMemory.reservation_id).filter(
                    GuestStayMemory.memory_date == latest_memory_date,
                    GuestStayMemory.status == "watch",
                    GuestStayMemory.reservation_id.isnot(None),
                ).all()
                if row[0]
            }
        for message in messages:
            if not bool(message.is_incoming):
                continue
            if message.reservation_id and int(message.reservation_id) in memory_signal_reservations:
                continue
            content = (message.content_preview or "").lower()
            if not content or not any(term in content for term in risky_terms):
                continue
            portfolio_id = listing_map.get(message.listing_id)
            if not portfolio_id:
                continue
            stay_key = (message.listing_id, message.reservation_id)
            if stay_key in seen_stays:
                continue
            seen_stays.add(stay_key)
            listing_name = self._listing_display_name(message.listing_id) or UNKNOWN_LISTING_NAME
            title = f"Guest message may need follow-up for {listing_name}"
            self._upsert_signal(
                run_id=run_id,
                portfolio_id=portfolio_id,
                listing_id=message.listing_id,
                reservation_id=message.reservation_id,
                category="review_risk",
                severity="high",
                confidence=0.72,
                title=title,
                summary=message.content_preview,
                why_it_matters="Recent guest language suggests a preventable review risk if the issue is not closed clearly.",
                suggested_action="Confirm the guest received a useful update and that the issue has a clear responsible person.",
                audience="operator",
                source="deterministic_guest_message",
                rank_score=guest_message_risk_rank_score(message.content_preview),
                evidence_entries=[
                    {
                        "source_type": "hostaway_message",
                        "source_id": str(message.message_id),
                        "listing_id": message.listing_id,
                        "reservation_id": message.reservation_id,
                        "occurred_at": message.created_at,
                        "summary": "Recent guest message contains risk language.",
                        "excerpt": message.content_preview,
                    }
                ],
            )
            count += 1
        return count

    def _generate_whatsapp_open_loop_signals(self, run_id: int, listing_map: dict[int, int]) -> int:
        self._retire_legacy_whatsapp_open_loops()
        cutoff = datetime.utcnow() - timedelta(days=14)
        listing_aliases = self._whatsapp_listing_aliases(listing_map)
        recent_messages = self.session.query(WhatsAppMessage).options(joinedload(WhatsAppMessage.thread)).join(
            WhatsAppThread,
            WhatsAppThread.whatsapp_thread_id == WhatsAppMessage.whatsapp_thread_id,
        ).filter(
            WhatsAppMessage.received_at >= cutoff,
            WhatsAppMessage.body.isnot(None),
            WhatsAppThread.portfolio_id.isnot(None),
        ).order_by(WhatsAppMessage.received_at.desc()).limit(2500).all()

        open_candidates: dict[tuple[int, str], dict[str, Any]] = {}
        messages = sorted(recent_messages, key=lambda row: row.received_at or datetime.min)
        for message in messages:
            thread = message.thread
            if not thread or not thread.portfolio_id:
                continue
            body = message.body or ""
            resolution = whatsapp_resolution_candidate(body)
            if resolution:
                self._clear_matching_whatsapp_candidates(open_candidates, thread, resolution, message)
                self._resolve_existing_whatsapp_loop(thread, resolution, message)
                continue

            candidate = whatsapp_open_loop_candidate(body)
            if not candidate:
                continue
            key = (thread.whatsapp_thread_id, candidate["topic_key"])
            if key not in open_candidates:
                open_candidates[key] = {
                    "thread": thread,
                    "topic": candidate,
                    "messages": [],
                }
            open_candidates[key]["messages"].append(message)
            if len(open_candidates[key]["messages"]) > 4:
                open_candidates[key]["messages"] = open_candidates[key]["messages"][-4:]

        count = 0
        for candidate in sorted(
            open_candidates.values(),
            key=lambda item: item["messages"][-1].received_at or datetime.min,
            reverse=True,
        )[:40]:
            thread = candidate["thread"]
            topic = candidate["topic"]
            messages = candidate["messages"]
            latest = messages[-1]
            thread_name = thread.display_name or "team WhatsApp"
            group_kind = _message_group_kind(latest)
            title = whatsapp_signal_title(thread_name, topic["topic_label"])
            latest_excerpt = _truncate_chat_text(latest.body)
            sender = latest.sender_name or latest.from_number or "Someone"
            inferred_listing = infer_whatsapp_listing(
                thread_name=thread_name,
                message_bodies=[message.body for message in messages],
                listing_aliases=[
                    alias for alias in listing_aliases
                    if alias.get("portfolio_id") == thread.portfolio_id
                ],
            )
            listing_id = inferred_listing.get("listing_id")
            listing_name = inferred_listing.get("listing_name")
            listing_context = f" ({listing_name})" if listing_name else ""
            summary = f"{sender} in {thread_name}{listing_context}: {latest_excerpt}"
            evidence = [
                {
                    "source_type": "whatsapp_message",
                    "source_id": str(message.whatsapp_message_id),
                    "listing_id": listing_id,
                    "reservation_id": None,
                    "occurred_at": message.received_at,
                    "summary": f"{thread_name} WhatsApp message about {topic['topic_label']}{listing_context}.",
                    "excerpt": message.body,
                    "metadata": {
                        "thread_id": thread.provider_thread_id,
                        "thread_name": thread_name,
                        "portfolio_id": thread.portfolio_id,
                        "listing_inference": inferred_listing or None,
                        "group_kind": _message_group_kind(message),
                        "sender_name": message.sender_name,
                        "from_number": message.from_number,
                        "topic_key": topic["topic_key"],
                        "topic_label": topic["topic_label"],
                    },
                }
                for message in messages
            ]
            signal = self._upsert_signal(
                run_id=run_id,
                portfolio_id=thread.portfolio_id,
                listing_id=listing_id,
                reservation_id=None,
                category="operational_open_loop",
                severity=topic["severity"],
                confidence=topic["confidence"],
                title=title,
                summary=summary,
                why_it_matters=(
                    f"{group_kind.capitalize()} WhatsApp threads move fast. "
                    "If the latest request is not explicitly closed, cleaning, maintenance, or guest follow-up can slip."
                ),
                suggested_action=topic["suggested_action"],
                audience="operator",
                source="deterministic_whatsapp",
                evidence_entries=evidence,
            )
            if listing_id:
                self._retire_unscoped_whatsapp_signal(
                    portfolio_id=thread.portfolio_id,
                    title=title,
                    replacement_signal_id=signal.signal_id,
                    resolved_at=latest.received_at or datetime.utcnow(),
                )
            if signal.status in ("resolved", "ignored"):
                continue

            existing_loop = self.session.query(OpenLoop).filter(OpenLoop.signal_id == signal.signal_id).first()
            if not existing_loop:
                existing_loop = OpenLoop(signal_id=signal.signal_id)
                self.session.add(existing_loop)
            existing_loop.portfolio_id = thread.portfolio_id
            existing_loop.listing_id = listing_id
            existing_loop.status = "open"
            existing_loop.title = title
            existing_loop.summary = summary
            existing_loop.involved_people = ", ".join(dict.fromkeys(
                (message.sender_name or message.from_number or "Unknown") for message in messages
            ))
            existing_loop.last_known_update = latest_excerpt
            existing_loop.suggested_next_step = topic["suggested_action"]
            existing_loop.last_activity_at = latest.received_at
            existing_loop.confidence = topic["confidence"]
            existing_loop.resolved_at = None
            count += 1
        self._retire_reclassified_whatsapp_open_loops()
        return count

    def _whatsapp_listing_aliases(self, listing_map: dict[int, int]) -> list[dict[str, Any]]:
        if not listing_map:
            return []
        listings = self.main_session.query(Listing).filter(Listing.listing_id.in_(list(listing_map))).all()
        aliases: list[dict[str, Any]] = []
        for listing in listings:
            listing_id = int(listing.listing_id)
            listing_name = listing_record_display_name(listing) or UNKNOWN_LISTING_NAME
            raw_names = [
                listing_name,
                getattr(listing, "internal_listing_name", None),
                getattr(listing, "name", None),
            ]
            for alias in listing_aliases_for_whatsapp(raw_names):
                aliases.append(
                    {
                        "listing_id": listing_id,
                        "portfolio_id": listing_map.get(listing_id),
                        "listing_name": listing_name,
                        "alias": alias,
                    }
                )
        return aliases

    def _retire_unscoped_whatsapp_signal(
        self,
        *,
        portfolio_id: int | None,
        title: str,
        replacement_signal_id: int,
        resolved_at: datetime,
    ):
        """Resolve older portfolio-level WhatsApp loops after a property-scoped signal replaces them."""
        signals = self.session.query(Signal).filter(
            Signal.source == "deterministic_whatsapp",
            Signal.portfolio_id == portfolio_id,
            Signal.title == title,
            Signal.listing_id.is_(None),
            Signal.signal_id != replacement_signal_id,
            Signal.status.in_(ACTIVE_SIGNAL_STATUSES),
            Signal.status != "escalated",
        ).all()
        for signal in signals:
            signal.status = "resolved"
            signal.resolved_at = resolved_at
            signal.updated_at = datetime.utcnow()
            for loop in self.session.query(OpenLoop).filter(OpenLoop.signal_id == signal.signal_id, OpenLoop.status == "open").all():
                loop.status = "resolved"
                loop.resolved_at = resolved_at
                loop.last_known_update = "Retired because Brain now has a property-scoped WhatsApp loop for the same topic."

    def _retire_reclassified_whatsapp_open_loops(self):
        """Resolve older WhatsApp loops when the underlying message no longer qualifies."""
        active_signals = self.session.query(Signal).filter(
            Signal.source == "deterministic_whatsapp",
            Signal.status.in_(ACTIVE_SIGNAL_STATUSES),
            Signal.status != "escalated",
        ).all()
        now = datetime.utcnow()
        for signal in active_signals:
            evidence_rows = self.session.query(SignalEvidence).filter(
                SignalEvidence.signal_id == signal.signal_id,
                SignalEvidence.source_type == "whatsapp_message",
            ).all()
            if not evidence_rows:
                continue
            still_open = False
            for evidence in evidence_rows:
                candidate = whatsapp_open_loop_candidate(evidence.excerpt)
                if not candidate:
                    continue
                metadata = evidence.evidence_metadata or {}
                thread_name = metadata.get("thread_name")
                topic_label = metadata.get("topic_label")
                if (
                    topic_label == candidate.get("topic_label")
                    or signal.title == whatsapp_signal_title(thread_name, candidate.get("topic_label"))
                ):
                    still_open = True
                    break
            if still_open:
                continue
            signal.status = "resolved"
            signal.resolved_at = now
            signal.updated_at = now
            for loop in self.session.query(OpenLoop).filter(OpenLoop.signal_id == signal.signal_id, OpenLoop.status == "open").all():
                loop.status = "resolved"
                loop.resolved_at = now
                loop.last_known_update = "Retired because the WhatsApp evidence now classifies as routine or closed."

    def _clear_matching_whatsapp_candidates(
        self,
        candidates: dict[tuple[int, str], dict[str, Any]],
        thread: WhatsAppThread,
        resolution: dict[str, Any],
        message: WhatsAppMessage,
    ):
        keys = [
            key for key in candidates
            if key[0] == thread.whatsapp_thread_id
            and whatsapp_resolution_matches_topic(resolution, key[1])
        ]
        for key in keys:
            latest = candidates[key]["messages"][-1]
            if not latest.received_at or not message.received_at or message.received_at >= latest.received_at:
                candidates.pop(key, None)

    def _resolve_existing_whatsapp_loop(
        self,
        thread: WhatsAppThread,
        resolution: dict[str, Any],
        message: WhatsAppMessage,
    ):
        thread_name = thread.display_name or "team WhatsApp"
        if whatsapp_resolution_matches_topic(resolution, "*"):
            possible_titles = [
                title for (title,) in self.session.query(Signal.title).filter(
                    Signal.source == "deterministic_whatsapp",
                    Signal.portfolio_id == thread.portfolio_id,
                    Signal.status.in_(ACTIVE_SIGNAL_STATUSES),
                    Signal.title.ilike(f"Open loop in {thread_name}:%"),
                ).all()
            ]
        else:
            possible_titles = [
                whatsapp_signal_title(thread_name, label)
                for label in whatsapp_resolution_compatible_topic_labels(resolution)
            ]
        signals = self.session.query(Signal).filter(
            Signal.source == "deterministic_whatsapp",
            Signal.portfolio_id == thread.portfolio_id,
            Signal.title.in_(possible_titles or [""]),
            Signal.status.in_(ACTIVE_SIGNAL_STATUSES),
        ).all()
        for signal in signals:
            signal.status = "resolved"
            signal.resolved_at = message.received_at or datetime.utcnow()
            loop = self.session.query(OpenLoop).filter(
                OpenLoop.signal_id == signal.signal_id,
                OpenLoop.status == "open",
            ).first()
            if loop:
                loop.status = "resolved"
                loop.resolved_at = message.received_at or datetime.utcnow()
                loop.last_known_update = _truncate_chat_text(message.body)
                loop.last_activity_at = message.received_at

    def _retire_legacy_whatsapp_open_loops(self):
        legacy_signals = self.session.query(Signal).filter(
            Signal.source == "deterministic_whatsapp",
            Signal.title == "Team WhatsApp thread may have an open loop",
            Signal.status.in_(ACTIVE_SIGNAL_STATUSES),
        ).all()
        now = datetime.utcnow()
        for signal in legacy_signals:
            signal.status = "resolved"
            signal.resolved_at = now
            loops = self.session.query(OpenLoop).filter(
                OpenLoop.signal_id == signal.signal_id,
                OpenLoop.status == "open",
            ).all()
            for loop in loops:
                loop.status = "resolved"
                loop.resolved_at = now
                loop.last_known_update = "Retired legacy generic WhatsApp detector after group-aware detector deployment."

    def _upsert_signal(
        self,
        *,
        run_id: int,
        portfolio_id: int | None,
        listing_id: int | None,
        reservation_id: int | None,
        category: str,
        severity: str,
        confidence: float,
        title: str,
        summary: str,
        why_it_matters: str,
        suggested_action: str,
        audience: str,
        source: str,
        evidence_entries: list[dict[str, Any]],
        rank_score: float | None = None,
    ) -> Signal:
        if not evidence_entries:
            raise ValueError("Signals require at least one evidence entry")
        category = normalize_signal_category(category)
        severity = normalize_signal_severity(severity)
        title = self._sanitize_listing_text(title, listing_id)
        summary = self._sanitize_listing_text(summary, listing_id)
        why_it_matters = self._sanitize_listing_text(why_it_matters, listing_id)
        suggested_action = self._sanitize_listing_text(suggested_action, listing_id)
        dedupe_key = make_dedupe_key(
            category=category,
            portfolio_id=portfolio_id,
            listing_id=listing_id,
            reservation_id=reservation_id,
            title=title,
        )
        payload = {
            "category": category,
            "severity": severity,
            "confidence": confidence,
            "revenue_impact": 0.7 if category == "revenue_booking_health" else 0.0,
            "silence_risk": 0.7 if category in ("review_risk", "operational_open_loop") else 0.3,
        }
        signal = self.session.query(Signal).filter(Signal.dedupe_key == dedupe_key).first()
        if not signal:
            signal = Signal(dedupe_key=dedupe_key, status="new", first_seen_at=datetime.utcnow())
            self.session.add(signal)
        signal.run_id = run_id
        signal.portfolio_id = portfolio_id
        signal.listing_id = listing_id
        signal.reservation_id = reservation_id
        signal.category = category
        signal.severity = severity
        signal.confidence = confidence
        signal.title = title[:255]
        signal.summary = summary
        signal.why_it_matters = why_it_matters
        signal.suggested_action = suggested_action
        signal.owner_or_manager = normalize_signal_audience(audience, category=category)
        signal.rank_score = _bounded_rank_score(rank_score) if rank_score is not None else rank_signal_payload(payload)
        signal.source = source
        signal.last_seen_at = datetime.utcnow()
        self.session.flush()
        self.session.query(SignalEvidence).filter(SignalEvidence.signal_id == signal.signal_id).delete(synchronize_session=False)
        for entry in evidence_entries:
            self.session.add(
                SignalEvidence(
                    signal_id=signal.signal_id,
                    source_type=entry["source_type"],
                    source_id=entry.get("source_id"),
                    listing_id=entry.get("listing_id"),
                    reservation_id=entry.get("reservation_id"),
                    occurred_at=entry.get("occurred_at"),
                    summary=entry["summary"],
                    excerpt=entry.get("excerpt"),
                    url=entry.get("url"),
                    evidence_metadata=entry.get("metadata") or {},
                )
            )
        return signal

    def _get_or_create_evidence(
        self,
        *,
        source_type: str,
        source_id: str | None = None,
        listing_id: int | None = None,
        reservation_id: int | None = None,
        occurred_at: datetime | None = None,
        summary: str,
        excerpt: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> SignalEvidence:
        query = self.session.query(SignalEvidence).filter(
            SignalEvidence.source_type == source_type,
            SignalEvidence.source_id == source_id,
            SignalEvidence.signal_id.is_(None),
        )
        evidence = query.first() if source_id else None
        if not evidence:
            evidence = SignalEvidence(source_type=source_type, source_id=source_id)
            self.session.add(evidence)
        evidence.listing_id = listing_id
        evidence.reservation_id = reservation_id
        evidence.occurred_at = occurred_at
        evidence.summary = summary[:1000]
        evidence.excerpt = (excerpt or "")[:3000]
        evidence.evidence_metadata = as_json_safe(metadata or {})
        self.session.flush()
        return evidence

    def _evidence_for_prompt(self, evidence: SignalEvidence) -> dict[str, Any]:
        metadata = evidence.evidence_metadata or {}
        return {
            "evidence_id": evidence.evidence_id,
            "source_type": evidence.source_type,
            "portfolio_id": metadata.get("portfolio_id"),
            "listing_id": evidence.listing_id,
            "listing_name": self._listing_display_name(evidence.listing_id),
            "reservation_id": evidence.reservation_id,
            "occurred_at": evidence.occurred_at.isoformat() if evidence.occurred_at else None,
            "summary": self._sanitize_listing_text(evidence.summary, evidence.listing_id),
            "excerpt": self._sanitize_listing_text(evidence.excerpt, evidence.listing_id),
            "source_context": {
                "thread_name": metadata.get("thread_name"),
                "group_kind": metadata.get("group_kind"),
                "sender_name": metadata.get("sender_name"),
                "guest_name": metadata.get("guest_name"),
                "risk_level": metadata.get("risk_level"),
                "risk_score": metadata.get("risk_score"),
            },
        }

    def _upsert_signal_from_existing_evidence(
        self,
        *,
        run_id: int,
        portfolio_id: int | None,
        listing_id: int | None,
        reservation_id: int | None,
        category: str,
        severity: str,
        confidence: float,
        title: str,
        summary: str,
        why_it_matters: str,
        suggested_action: str,
        audience: str,
        source: str,
        evidence_rows: list[SignalEvidence],
        rank_score: float | None = None,
    ) -> Signal | None:
        if not evidence_rows:
            return None
        category = normalize_signal_category(category)
        severity = normalize_signal_severity(severity)
        title = self._sanitize_listing_text(title, listing_id)
        summary = self._sanitize_listing_text(summary, listing_id)
        why_it_matters = self._sanitize_listing_text(why_it_matters, listing_id)
        suggested_action = self._sanitize_listing_text(suggested_action, listing_id)
        dedupe_key = make_dedupe_key(
            category=category,
            portfolio_id=portfolio_id,
            listing_id=listing_id,
            reservation_id=reservation_id,
            title=title,
        )
        signal = self.session.query(Signal).filter(Signal.dedupe_key == dedupe_key).first()
        if not signal:
            signal = Signal(dedupe_key=dedupe_key, status="new", first_seen_at=datetime.utcnow())
            self.session.add(signal)
        signal.run_id = run_id
        signal.portfolio_id = portfolio_id
        signal.listing_id = listing_id
        signal.reservation_id = reservation_id
        signal.category = category
        signal.severity = severity
        signal.confidence = max(0.0, min(float(confidence), 1.0))
        signal.title = title[:255]
        signal.summary = summary
        signal.why_it_matters = why_it_matters
        signal.suggested_action = suggested_action
        signal.owner_or_manager = normalize_signal_audience(audience, category=category)
        signal.rank_score = _bounded_rank_score(rank_score) if rank_score is not None else rank_signal_payload(
            {
                "category": category,
                "severity": severity,
                "confidence": signal.confidence,
                "urgency": 0.6,
                "silence_risk": 0.7,
                "revenue_impact": 0.7 if category == "revenue_booking_health" else 0.0,
            }
        )
        signal.source = source
        signal.last_seen_at = datetime.utcnow()
        self.session.flush()
        self.session.query(SignalEvidence).filter(SignalEvidence.signal_id == signal.signal_id).delete(synchronize_session=False)
        for evidence in evidence_rows:
            self.session.add(
                SignalEvidence(
                    signal_id=signal.signal_id,
                    source_type=evidence.source_type,
                    source_id=evidence.source_id,
                    listing_id=evidence.listing_id,
                    reservation_id=evidence.reservation_id,
                    occurred_at=evidence.occurred_at,
                    summary=evidence.summary,
                    excerpt=evidence.excerpt,
                    url=evidence.url,
                    evidence_metadata=evidence.evidence_metadata or {},
                )
            )
        return signal

    def _generate_briefs(self, run_id: int, deliver: bool = False) -> int:
        top_signals = self.session.query(Signal).filter(Signal.status.in_(ACTIVE_SIGNAL_STATUSES)).order_by(
            Signal.rank_score.desc(), Signal.last_seen_at.desc()
        ).limit(80).all()
        if not top_signals:
            return 0
        from brain.models import PortfolioUser
        from dashboard.auth.models import get_user_by_email

        primary_operator = get_user_by_email(config.OWNER_EMAIL)
        operating_brief = self._build_operating_brief(
            subject="STR Signal Brain - Daily Brief",
            audience="operator",
            signals=top_signals,
            portfolio_ids=None,
        )
        portfolio_user_ids = sorted({row[0] for row in self.session.query(PortfolioUser.user_id).all() if row[0]})
        recipient_user_ids = sorted({*(portfolio_user_ids or []), *([primary_operator.user_id] if primary_operator else [])})
        briefs = []
        if recipient_user_ids:
            for user_id in recipient_user_ids:
                briefs.append(DailyBrief(
                    run_id=run_id,
                    audience="operator",
                    user_id=user_id,
                    channel="dashboard",
                    subject=operating_brief["subject"],
                    body=operating_brief["body"],
                    payload=operating_brief["payload"],
                ))
        else:
            briefs.append(DailyBrief(
                run_id=run_id,
                audience="operator",
                channel="dashboard",
                subject=operating_brief["subject"],
                body=operating_brief["body"],
                payload=operating_brief["payload"],
            ))
        self.session.add_all(briefs)
        self.session.commit()
        if deliver:
            self._deliver_briefs(briefs)
        return len(briefs)

    def _build_operating_brief(
        self,
        *,
        subject: str,
        audience: str,
        signals: list[Signal],
        portfolio_ids: list[int] | None,
    ) -> dict[str, Any]:
        """Build the daily brief as a compact operating picture."""
        portfolio_ids = portfolio_ids if portfolio_ids is not None else None
        portfolio_names = self._portfolio_name_map()
        filtered_signals = [
            signal for signal in signals
            if portfolio_ids is None or signal.portfolio_id in portfolio_ids
        ]
        needs_action_signals = select_operating_signals(filtered_signals, limit=5, per_category_limit=2)
        open_loop_items = self._brief_combined_open_loop_items(
            filtered_signals,
            portfolio_ids=portfolio_ids,
            portfolio_names=portfolio_names,
            limit=25,
        )
        memory_items = self._brief_memory_highlight_items(
            portfolio_ids=portfolio_ids,
            portfolio_names=portfolio_names,
            limit=5,
        )
        sections = [
            {
                "key": "needs_action",
                "title": "Needs Action Today",
                "items": [self._brief_signal_item(signal, portfolio_names) for signal in needs_action_signals],
            },
            {
                "key": "whatsapp_memory",
                "title": "What You Might Have Missed",
                "items": memory_items,
            },
            {
                "key": "guest_review_risks",
                "title": "Guest / Review Risks",
                "items": [
                    self._brief_signal_item(signal, portfolio_names)
                    for signal in filtered_signals
                    if signal.category in {"guest_experience", "review_risk", "checkin_checkout_risk"}
                ][:5],
            },
            {
                "key": "open_loops",
                "title": "Operational Open Loops",
                "items": open_loop_items[:5],
            },
            {
                "key": "booking_roi",
                "title": "Booking Health / ROI",
                "items": [
                    self._brief_signal_item(signal, portfolio_names)
                    for signal in filtered_signals
                    if signal.category == "revenue_booking_health"
                ][:5],
            },
            {
                "key": "decisions",
                "title": "Decisions / Approvals",
                "items": [
                    self._brief_signal_item(signal, portfolio_names)
                    for signal in filtered_signals
                    if is_decision_signal(signal)
                ][:5],
            },
        ]
        section_by_key = {section["key"]: section for section in sections}
        operating_snapshot = build_today_operating_snapshot(
            top_signals=[
                self._snapshot_signal_input(item)
                for item in (section_by_key.get("needs_action", {}).get("items") or [])
            ],
            priority_actions=[
                self._snapshot_signal_input(item)
                for item in (section_by_key.get("needs_action", {}).get("items") or [])
            ],
            memory_highlights=[
                self._snapshot_memory_input(item)
                for item in (section_by_key.get("whatsapp_memory", {}).get("items") or [])
            ],
            open_loops=[
                self._snapshot_open_loop_input(item)
                for item in (section_by_key.get("open_loops", {}).get("items") or [])
            ],
            booking_health_warnings=[
                self._snapshot_booking_input(item)
                for item in (section_by_key.get("booking_roi", {}).get("items") or [])
            ],
        )
        counts = {
            "active_signals": len(filtered_signals),
            "guest_review_risks": len([signal for signal in filtered_signals if signal.category in {"guest_experience", "review_risk", "checkin_checkout_risk"}]),
            "open_loops": len(open_loop_items),
            "memory_highlights": len(memory_items),
            "booking_roi": len([signal for signal in filtered_signals if signal.category == "revenue_booking_health"]),
            "decisions": len([signal for signal in filtered_signals if is_decision_signal(signal)]),
        }
        payload = {
            "audience": audience,
            "generated_for_date": date.today().isoformat(),
            "counts": counts,
            "operating_snapshot": operating_snapshot,
            "sections": sections,
            "signal_ids": sorted({item["signal_id"] for section in sections for item in section["items"] if item.get("signal_id")}),
        }
        return {
            "subject": subject,
            "body": format_operating_brief_body(
                subject=subject,
                generated_date=date.today(),
                counts=counts,
                sections=sections,
                operating_snapshot=operating_snapshot,
            ),
            "payload": as_json_safe(payload),
        }

    def _portfolio_name_map(self) -> dict[int, str]:
        return {
            portfolio.portfolio_id: portfolio.name
            for portfolio in self.session.query(Portfolio).all()
        }

    def _brief_open_loops(self, portfolio_ids: list[int] | None) -> list[OpenLoop]:
        query = self.session.query(OpenLoop).filter(OpenLoop.status == "open", OpenLoop.portfolio_id.isnot(None))
        if portfolio_ids is not None:
            query = query.filter(OpenLoop.portfolio_id.in_(portfolio_ids or [-1]))
        return query.order_by(OpenLoop.last_activity_at.desc().nullslast(), OpenLoop.created_at.desc()).limit(25).all()

    def _brief_memory_highlight_items(
        self,
        *,
        portfolio_ids: list[int] | None,
        portfolio_names: dict[int, str],
        limit: int,
    ) -> list[dict[str, Any]]:
        query = self.session.query(WhatsAppGroupMemory).filter(WhatsAppGroupMemory.message_count > 0)
        if portfolio_ids is not None:
            query = query.filter(WhatsAppGroupMemory.portfolio_id.in_(portfolio_ids or [-1]))
        latest_memory_date = query.with_entities(func.max(WhatsAppGroupMemory.memory_date)).scalar()
        if not latest_memory_date:
            return []
        memories = query.filter(WhatsAppGroupMemory.memory_date == latest_memory_date).all()
        highlights = [
            whatsapp_group_memory_highlight(memory, portfolio_name=portfolio_names.get(memory.portfolio_id))
            for memory in memories
        ]
        highlights = [highlight for highlight in highlights if float(highlight.get("attention_score") or 0.0) > 0.0]
        highlights.sort(key=whatsapp_memory_highlight_sort_key)
        return [whatsapp_memory_highlight_brief_item(highlight) for highlight in highlights[:limit]]

    def _brief_combined_open_loop_items(
        self,
        signals: list[Signal],
        *,
        portfolio_ids: list[int] | None,
        portfolio_names: dict[int, str],
        limit: int = 25,
    ) -> list[dict[str, Any]]:
        operational_signals = [
            signal
            for signal in signals
            if signal.category == "operational_open_loop"
            and signal.status in ACTIVE_SIGNAL_STATUSES
            and (portfolio_ids is None or signal.portfolio_id in portfolio_ids)
        ]
        signals_by_id = {signal.signal_id: signal for signal in operational_signals}
        loops = self._brief_open_loops(portfolio_ids)
        linked_signal_ids = {loop.signal_id for loop in loops if loop.signal_id}
        items = [
            self._brief_open_loop_item(loop, portfolio_names, related_signal=signals_by_id.get(loop.signal_id))
            for loop in loops
        ]
        items.extend(
            self._brief_open_loop_signal_item(signal, portfolio_names)
            for signal in operational_signals
            if signal.signal_id not in linked_signal_ids
        )
        items.sort(key=open_loop_item_sort_key)
        return items[:limit]

    def _brief_signal_item(self, signal: Signal, portfolio_names: dict[int, str]) -> dict[str, Any]:
        title = self._sanitize_listing_text(signal.title, signal.listing_id)
        action = self._sanitize_listing_text(signal.suggested_action, signal.listing_id)
        return {
            "kind": "signal",
            "signal_id": signal.signal_id,
            "portfolio_id": signal.portfolio_id,
            "portfolio_name": portfolio_names.get(signal.portfolio_id, "Unknown portfolio"),
            "listing_id": signal.listing_id,
            "listing_name": self._listing_display_name(signal.listing_id),
            "category": signal.category,
            "severity": signal.severity,
            "confidence": signal.confidence,
            "rank_score": signal.rank_score,
            "title": title,
            "summary": self._sanitize_listing_text(signal.summary, signal.listing_id),
            "action": action,
            "last_seen_at": signal.last_seen_at.isoformat() if signal.last_seen_at else None,
        }

    def _brief_open_loop_item(self, loop: OpenLoop, portfolio_names: dict[int, str], related_signal: Signal | None = None) -> dict[str, Any]:
        last_seen_at = related_signal.last_seen_at if related_signal and related_signal.last_seen_at else loop.last_activity_at
        return {
            "kind": "open_loop",
            "open_loop_id": loop.open_loop_id,
            "signal_id": loop.signal_id,
            "portfolio_id": loop.portfolio_id,
            "portfolio_name": portfolio_names.get(loop.portfolio_id, "Unknown portfolio"),
            "listing_id": loop.listing_id,
            "listing_name": self._listing_display_name(loop.listing_id),
            "status": loop.status,
            "severity": related_signal.severity if related_signal else "medium",
            "confidence": loop.confidence,
            "rank_score": related_signal.rank_score if related_signal else (loop.confidence or 0.0) * 80,
            "title": self._sanitize_listing_text(loop.title, loop.listing_id),
            "summary": self._sanitize_listing_text(loop.summary, loop.listing_id),
            "action": self._sanitize_listing_text(loop.suggested_next_step, loop.listing_id),
            "suggested_next_step": self._sanitize_listing_text(loop.suggested_next_step, loop.listing_id),
            "last_known_update": self._sanitize_listing_text(loop.last_known_update, loop.listing_id),
            "last_seen_at": last_seen_at.isoformat() if last_seen_at else None,
            "last_activity_at": loop.last_activity_at.isoformat() if loop.last_activity_at else None,
        }

    def _brief_open_loop_signal_item(self, signal: Signal, portfolio_names: dict[int, str]) -> dict[str, Any]:
        last_seen = signal.last_seen_at.isoformat() if signal.last_seen_at else None
        return {
            "kind": "signal",
            "open_loop_id": None,
            "signal_id": signal.signal_id,
            "portfolio_id": signal.portfolio_id,
            "portfolio_name": portfolio_names.get(signal.portfolio_id, "Unknown portfolio"),
            "listing_id": signal.listing_id,
            "listing_name": self._listing_display_name(signal.listing_id),
            "status": signal.status,
            "severity": signal.severity,
            "confidence": signal.confidence,
            "rank_score": signal.rank_score,
            "title": self._sanitize_listing_text(signal.title, signal.listing_id),
            "summary": self._sanitize_listing_text(signal.summary, signal.listing_id),
            "action": self._sanitize_listing_text(signal.suggested_action, signal.listing_id),
            "suggested_next_step": self._sanitize_listing_text(signal.suggested_action, signal.listing_id),
            "last_known_update": f"Last seen {last_seen}" if last_seen else "",
            "last_seen_at": last_seen,
            "last_activity_at": last_seen,
        }

    def _snapshot_signal_input(self, item: dict[str, Any]) -> dict[str, Any]:
        return {
            "signal_id": item.get("signal_id"),
            "category": item.get("category"),
            "title": item.get("title"),
            "summary": item.get("summary"),
            "suggested_action": item.get("action") or item.get("suggested_next_step"),
            "severity": item.get("severity"),
            "status": item.get("status") or "new",
            "rank_score": item.get("rank_score"),
            "listing_name": item.get("listing_name"),
            "portfolio_name": item.get("portfolio_name"),
        }

    def _snapshot_memory_input(self, item: dict[str, Any]) -> dict[str, Any]:
        return {
            "whatsapp_group_memory_id": item.get("whatsapp_group_memory_id"),
            "group_name": item.get("group_name") or item.get("title"),
            "portfolio_name": item.get("portfolio_name"),
            "focus": item.get("summary"),
            "summary": item.get("summary"),
            "suggested_action": item.get("action") or item.get("suggested_next_step"),
            "status": item.get("status") or "watch",
            "attention_score": item.get("rank_score"),
        }

    def _snapshot_open_loop_input(self, item: dict[str, Any]) -> dict[str, Any]:
        return {
            "kind": item.get("kind") or "open_loop",
            "open_loop_id": item.get("open_loop_id"),
            "signal_id": item.get("signal_id"),
            "title": item.get("title"),
            "summary": item.get("summary"),
            "suggested_next_step": item.get("suggested_next_step") or item.get("action"),
            "severity": item.get("severity"),
            "status": item.get("status") or "open",
            "rank_score": item.get("rank_score"),
            "portfolio_name": item.get("portfolio_name"),
            "listing_name": item.get("listing_name"),
            "last_activity_at": item.get("last_activity_at"),
            "last_seen_at": item.get("last_seen_at"),
            "latest_evidence_at": item.get("latest_evidence_at"),
            "evidence": item.get("evidence") or [],
        }

    def _snapshot_booking_input(self, item: dict[str, Any]) -> dict[str, Any]:
        return {
            "listing_id": item.get("listing_id"),
            "listing_name": item.get("listing_name"),
            "portfolio_name": item.get("portfolio_name"),
            "severity": item.get("severity"),
            "booking_pattern": item.get("summary"),
            "opinion": item.get("summary"),
            "action_items": [item.get("action")] if item.get("action") else [],
            "confidence": item.get("confidence"),
        }

    def _deliver_briefs(self, briefs: list[DailyBrief]):
        delivery = BrainDeliveryService()
        for brief in briefs:
            try:
                target_user = None
                if brief.user_id:
                    from dashboard.auth.models import get_user_by_id

                    target_user = get_user_by_id(brief.user_id)
                elif normalize_signal_audience(brief.audience) == "operator":
                    from dashboard.auth.models import get_user_by_email

                    target_user = get_user_by_email(config.OWNER_EMAIL)
                if target_user:
                    email_result = delivery.send_email(target_user.email, brief.subject, brief.body)
                    whatsapp_result = delivery.send_whatsapp_to_user(target_user.user_id, brief.body)
                    self._log_delivery_attempt(brief, "email", email_result)
                    self._log_delivery_attempt(brief, "whatsapp", whatsapp_result)
                    delivered = bool(email_result.get("sent") or whatsapp_result.get("sent"))
                    brief.status = "sent" if delivered else "generated"
                    brief.sent_at = datetime.utcnow() if delivered else None
                self.session.commit()
            except Exception as exc:
                brief.status = "error"
                brief.error_message = str(exc)
                self.session.commit()

    def _log_delivery_attempt(self, brief: DailyBrief, channel: str, result: dict[str, Any]):
        self.session.add(
            BriefDeliveryLog(
                daily_brief_id=brief.daily_brief_id,
                channel=channel,
                recipient=result.get("recipient"),
                provider=result.get("provider"),
                provider_message_id=result.get("provider_message_id"),
                status=(result.get("status") or ("sent" if result.get("sent") else "skipped")),
                error_message=result.get("error"),
                payload=as_json_safe(result),
                delivered_at=datetime.utcnow() if result.get("sent") else None,
            )
        )


def format_operating_brief_body(
    *,
    subject: str,
    generated_date: date,
    counts: dict[str, Any],
    sections: list[dict[str, Any]],
    operating_snapshot: list[dict[str, Any]] | None = None,
) -> str:
    """Format a structured operating brief for email and WhatsApp delivery."""
    lines = [
        subject,
        f"Date: {generated_date.isoformat()}",
        "",
        (
            f"Active signals: {counts.get('active_signals', 0)} | "
            f"Guest/review risks: {counts.get('guest_review_risks', 0)} | "
            f"Open loops: {counts.get('open_loops', 0)} | "
            f"WhatsApp misses: {counts.get('memory_highlights', 0)} | "
            f"Booking ROI: {counts.get('booking_roi', 0)}"
        ),
    ]
    if operating_snapshot:
        lines.extend(["", "Daily Operating Snapshot:"])
        for idx, item in enumerate(operating_snapshot[:6], 1):
            context = item.get("portfolio_name") or item.get("listing_name")
            status = item.get("status") or item.get("severity")
            context_text = f" [{context}]" if context else ""
            status_text = f" ({status})" if status else ""
            lines.append(f"{idx}. {item.get('question') or 'Question'}: {item.get('answer') or item.get('title') or 'Nothing surfaced'}{context_text}{status_text}")
            if item.get("suggested_action"):
                lines.append(f"   Next: {item['suggested_action']}")
    for section in sections:
        lines.extend(["", f"{section.get('title')}:"])
        items = section.get("items") or []
        if not items:
            lines.append("None surfaced.")
            continue
        for idx, item in enumerate(items[:5], 1):
            context = item.get("portfolio_name") or item.get("listing_name")
            severity = item.get("severity")
            context_text = f" [{context}]" if context else ""
            severity_text = f" ({severity})" if severity else ""
            lines.append(f"{idx}. {item.get('title') or 'Untitled'}{context_text}{severity_text}")
            if item.get("action"):
                lines.append(f"   Next: {item['action']}")
    return "\n".join(lines)


def signal_visible_to_portfolio_brief(signal: Any) -> bool:
    """Return True when a signal belongs in a portfolio-scoped operating brief."""
    return True


def should_generate_briefs_for_run_type(run_type: str | None) -> bool:
    """Nightly runs maintain Brain state quietly; morning/afternoon/manual can create briefs."""
    return (run_type or "").strip().lower() != "nightly"


def is_decision_signal(signal: Any) -> bool:
    """Return True for decisions/approvals, excluding booking ROI surfaced separately."""
    return getattr(signal, "category", None) == "owner_decision"


def ai_candidate_category(candidate: dict[str, Any], evidence_rows: list[Any]) -> str:
    """Normalize model category and correct obvious revenue/booking-health candidates."""
    raw_category = candidate.get("category")
    category = normalize_signal_category(raw_category)
    if category == "revenue_booking_health":
        return category
    if category == "operational_open_loop" and _ai_candidate_explicitly_operational(raw_category):
        if _ai_candidate_has_review_evidence(evidence_rows) and _ai_candidate_looks_like_review_risk(candidate):
            return "review_risk"
        return category
    if category == "operational_open_loop" and _ai_candidate_looks_like_review_risk(candidate):
        return "review_risk"
    if _ai_candidate_looks_like_revenue(candidate) and any(
        getattr(evidence, "source_type", None) == "booking_health" for evidence in evidence_rows or []
    ):
        return "revenue_booking_health"
    return category


def should_skip_ai_signal_candidate(
    candidate: dict[str, Any],
    evidence_rows: list[Any],
    category: str | None = None,
    *,
    listing_id: int | None = None,
    deterministic_revenue_listing_ids: set[int] | None = None,
) -> bool:
    """Avoid duplicate OpenAI signals for booking health that deterministic analysis already owns."""
    rows = evidence_rows or []
    normalized_category = normalize_signal_category(category or candidate.get("category"))
    if normalized_category != "revenue_booking_health":
        return False
    has_booking_health_evidence = any(getattr(evidence, "source_type", None) == "booking_health" for evidence in rows)
    if not has_booking_health_evidence:
        return False
    if listing_id and int(listing_id) in (deterministic_revenue_listing_ids or set()):
        return True
    return all(getattr(evidence, "source_type", None) == "booking_health" for evidence in rows)


def _ai_candidate_looks_like_revenue(candidate: dict[str, Any]) -> bool:
    text = " ".join(
        str(candidate.get(key) or "")
        for key in ("title", "summary", "why_it_matters", "suggested_action")
    )
    return bool(AI_REVENUE_SIGNAL_RE.search(text))


def _ai_candidate_looks_like_review_risk(candidate: dict[str, Any]) -> bool:
    text = " ".join(
        str(candidate.get(key) or "")
        for key in ("title", "summary", "why_it_matters", "suggested_action")
    )
    return bool(AI_REVIEW_SIGNAL_RE.search(text))


def _ai_candidate_explicitly_operational(category: Any) -> bool:
    value = str(category or "").strip().lower().replace(" ", "_").replace("-", "_").replace("/", "_")
    return value in AI_EXPLICIT_OPERATIONAL_CATEGORIES


def _ai_candidate_has_review_evidence(evidence_rows: list[Any]) -> bool:
    return any(getattr(evidence, "source_type", None) in {"hostaway_review", "guest_stay_memory"} for evidence in evidence_rows or [])


def ai_structured_signal_rank_score(candidate: dict[str, Any], evidence_rows: list[Any], category: str | None = None) -> float:
    """Rank AI signals without flattening every high-confidence signal to 100."""
    normalized_category = normalize_signal_category(category or candidate.get("category"))
    severity = normalize_signal_severity(candidate.get("severity"))
    confidence = max(0.0, min(float(candidate.get("confidence") or 0.55), 1.0))
    base = {
        "critical": 92.0,
        "high": 82.0,
        "medium": 64.0,
        "low": 45.0,
    }.get(severity, 58.0)
    category_points = {
        "review_risk": 5.0,
        "checkin_checkout_risk": 4.0,
        "operational_open_loop": 2.0,
        "guest_experience": 1.0,
        "revenue_booking_health": 1.0,
        "owner_decision": 2.0,
    }.get(normalized_category, 0.0)
    evidence_points = _ai_signal_evidence_rank_points(normalized_category, evidence_rows)
    text_points = _ai_signal_text_rank_points(candidate)
    confidence_points = confidence * 5.0
    return _bounded_rank_score(base + category_points + evidence_points + text_points + confidence_points, maximum=99.0)


def _ai_signal_evidence_rank_points(category: str, evidence_rows: list[Any]) -> float:
    source_types = {getattr(evidence, "source_type", None) for evidence in evidence_rows or []}
    points = 0.0
    if category == "review_risk":
        if "guest_stay_memory" in source_types:
            points += 4.0
        if "hostaway_review" in source_types:
            points += 4.0
        if "hostaway_message" in source_types:
            points += 1.0
        if "whatsapp_group_memory" in source_types:
            points += 1.0
    elif category == "checkin_checkout_risk":
        if "guest_stay_memory" in source_types:
            points += 3.0
        if "hostaway_message" in source_types:
            points += 3.0
        if "whatsapp_message" in source_types:
            points += 1.0
    elif category == "operational_open_loop":
        if "whatsapp_message" in source_types:
            points += 3.0
        if "whatsapp_group_memory" in source_types:
            points += 2.0
        if "hostaway_message" in source_types:
            points += 1.0
    elif category == "revenue_booking_health":
        if "booking_health_analysis" in source_types:
            points += 3.0
        if "booking_health" in source_types:
            points += 3.0
        if "hostaway_review" in source_types:
            points += 2.0
    elif category == "guest_experience":
        if "guest_stay_memory" in source_types:
            points += 3.0
        if "hostaway_message" in source_types:
            points += 2.0
        if "whatsapp_group_memory" in source_types:
            points += 1.0
        if "hostaway_review" in source_types:
            points += 1.0
    return min(points, 6.0)


def _ai_signal_text_rank_points(candidate: dict[str, Any]) -> float:
    text = " ".join(
        str(candidate.get(key) or "")
        for key in ("title", "summary", "why_it_matters", "suggested_action")
    ).lower()
    points = 0.0
    if re.search(r"\b(?:waiting|lobby|stranded|no access|cannot find|can't find|blocked)\b", text):
        points += 4.0
    if re.search(r"\b(?:refund|cancel|chargeback)\b", text):
        points += 3.0
    if re.search(r"\b(?:policy|compliance|incentiv|brib)\b", text):
        points += 3.0
    if re.search(r"\b(?:severe|serious|repeated|multiple|persistent)\b", text):
        points += 2.0
    if re.search(r"\b(?:possible|may need|if unresolved)\b", text):
        points -= 1.0
    return max(points, -2.0)


def guest_message_risk_rank_score(body: str | None) -> float:
    """Rank simple deterministic guest-message risks without over-promoting every keyword hit."""
    text = (body or "").lower()
    score = 84.0
    if re.search(r"\b(?:full refund|refund|chargeback|cancel)\b", text):
        score += 8.0
    if re.search(r"\b(?:locked out|no access|can't access|cannot access)\b", text):
        score += 7.0
    if re.search(r"\b(?:dirty|disappointed|angry)\b", text):
        score += 5.0
    if re.search(r"\b(?:not working|broken)\b", text):
        score += 3.0
    return _bounded_rank_score(score, maximum=96.0)


GUEST_STAY_RISK_RULES = (
    (
        "refund_or_cancellation",
        "refund/cancellation language",
        re.compile(
            r"\b(?:full refund|partial refund|refund|chargeback|compensation|"
            r"cancel my|cancel this|cancel the reservation|cancel booking|need to cancel|canceled|cancelled)\b",
            re.IGNORECASE,
        ),
        "critical",
        72.0,
        "Confirm the recovery offer, guest-facing response, and whether any refund/credit needs approval.",
    ),
    (
        "access_or_checkin",
        "check-in/access issue",
        re.compile(
            r"\b(?:"
            r"locked out|no access|can't access|cannot access|unable to access|"
            r"can't get in|cannot get in|unable to get in|not able to get in|won't let (?:me|us) in|"
            r"can't check[ -]?in|cannot check[ -]?in|unable to check[ -]?in|not able to check[ -]?in|"
            r"(?:door|entry|access|keypad) (?:code|key) (?:is |was )?"
            r"(?:not working|invalid|wrong|missing)|"
            r"(?:didn't|did not|haven't|have not|never) (?:receive|received|get|got) (?:the )?check[ -]?in instructions|"
            r"front desk.{0,60}(?:can't find|cannot find|no reservation|not in (?:the )?system)"
            r")\b",
            re.IGNORECASE,
        ),
        "critical",
        70.0,
        "Confirm the guest has access now, then send a short apology and resolution note in Hostaway.",
    ),
    (
        "safety_or_compliance",
        "safety/compliance issue",
        re.compile(r"\b(?:unsafe|dangerous|mold|bed bug|bedbug|blood|smoke alarm|carbon monoxide|police|security|fire alarm|fire hazard)\b", re.IGNORECASE),
        "critical",
        76.0,
        "Escalate immediately, document the response, and confirm whether guest relocation or refund handling is needed.",
    ),
    (
        "cleanliness",
        "cleanliness complaint",
        re.compile(r"\b(?:dirty|unclean|not clean|filthy|gross|hair|stain|stained|trash everywhere|trash left|garbage|smell|odor)\b", re.IGNORECASE),
        "high",
        58.0,
        "Confirm corrective cleaning or guest recovery and close the loop with the guest in writing.",
    ),
    (
        "broken_amenity",
        "broken amenity or maintenance issue",
        re.compile(r"\b(?:not working|doesn't work|does not work|broken|leak|clog|toilet|shower|hot tub|pool|heater|a/c|ac\b|air conditioning|wifi|internet)\b", re.IGNORECASE),
        "high",
        54.0,
        "Confirm the repair owner, ETA, and guest-facing update before the stay ends.",
    ),
    (
        "negative_sentiment",
        "negative guest sentiment",
        re.compile(r"\b(?:angry|upset|disappointed|frustrated|unacceptable|terrible|awful|bad experience|bad review|complaint)\b", re.IGNORECASE),
        "high",
        56.0,
        "Send a direct recovery note and verify whether the underlying issue is fully resolved.",
    ),
)
GUEST_STAY_RESOLUTION_RE = re.compile(
    r"\b(?:fixed|resolved|all set|handled|done|completed|confirmed|sent|refunded|credited|guest confirmed|"
    r"thank you|thanks|appreciate|works now|got in|access confirmed|cleaned|ready)\b",
    re.IGNORECASE,
)
GUEST_STAY_UNRESOLVED_RE = re.compile(
    r"\b(?:still|not working|doesn't work|does not work|broken|dirty|refund|locked out|no access|"
    r"upset|angry|complaint|bad review|unacceptable|unsafe|can't access|cannot access)\b",
    re.IGNORECASE,
)
GUEST_STAY_RECENT_ATTENTION_DAYS = 7


def build_guest_stay_memory_payload(
    *,
    reservation: Any,
    messages: list[Any],
    reviews: list[Any],
    listing_name: str,
    portfolio_name: str | None,
    window_start: datetime,
    window_end: datetime,
    window_days: int,
) -> dict[str, Any]:
    """Build a compact daily memory row for one Hostaway reservation/stay."""
    ordered_messages = sorted(
        [message for message in messages or [] if (getattr(message, "content_preview", None) or "").strip()],
        key=lambda message: getattr(message, "created_at", None) or datetime.min,
    )
    ordered_reviews = sorted(
        reviews or [],
        key=lambda review: (
            getattr(review, "review_date", None) or date.min,
            getattr(review, "inserted_on", None) or datetime.min,
        ),
        reverse=True,
    )
    incoming_count = len([message for message in ordered_messages if bool(getattr(message, "is_incoming", None))])
    risk_updates: list[dict[str, Any]] = []
    resolution_updates: list[dict[str, Any]] = []
    latest_guest_issue: dict[str, Any] | None = None
    latest_risk_at: datetime | None = None
    latest_resolution_at: datetime | None = None

    for message in ordered_messages:
        body = getattr(message, "content_preview", None) or ""
        occurred_at = getattr(message, "created_at", None)
        sender = _guest_stay_message_sender(message)
        resolution = _guest_stay_resolution_update(message, sender=sender)
        if resolution:
            resolution_updates.append(resolution)
            if occurred_at and (latest_resolution_at is None or occurred_at > latest_resolution_at):
                latest_resolution_at = occurred_at
        risk = _guest_stay_risk_update(message, sender=sender)
        if not risk:
            continue
        risk_updates.append(risk)
        if occurred_at and (latest_risk_at is None or occurred_at > latest_risk_at):
            latest_risk_at = occurred_at
        if bool(getattr(message, "is_incoming", None)):
            latest_guest_issue = risk

    incoming_risk_updates = [update for update in risk_updates if update.get("is_incoming")]
    latest_review = ordered_reviews[0] if ordered_reviews else None
    review_context = _guest_stay_review_context(latest_review)
    attention_cutoff = window_end - timedelta(days=GUEST_STAY_RECENT_ATTENTION_DAYS)
    recent_unresolved_risks = [
        update for update in risk_updates
        if update.get("is_incoming")
        and _source_datetime_is_at_or_after(update.get("occurred_at"), attention_cutoff)
        and (latest_resolution_at is None or _source_datetime_is_after(update.get("occurred_at"), latest_resolution_at))
    ]
    unresolved_risks = [
        update for update in risk_updates
        if update.get("is_incoming")
        and (latest_resolution_at is None or _source_datetime_is_after(update.get("occurred_at"), latest_resolution_at))
    ]
    current_or_recent_stay = _reservation_is_current_or_recent(reservation, window_end.date())
    risk_score = guest_stay_memory_score(
        risk_updates=risk_updates,
        recent_unresolved_risks=recent_unresolved_risks,
        unresolved_risks=unresolved_risks,
        latest_risk_at=latest_risk_at,
        latest_resolution_at=latest_resolution_at,
        review_context=review_context,
        current_or_recent_stay=current_or_recent_stay,
        window_end=window_end,
    )
    risk_level = guest_stay_risk_level(risk_score)
    has_low_review = bool(review_context and (review_context.get("rating") or 5) <= 3.5)
    status = "watch" if risk_score >= 40 and (recent_unresolved_risks or (current_or_recent_stay and unresolved_risks) or has_low_review) else "ok"
    last_message_at = getattr(ordered_messages[-1], "created_at", None) if ordered_messages else None
    summary = _guest_stay_summary_sentence(
        reservation=reservation,
        listing_name=listing_name,
        portfolio_name=portfolio_name,
        message_count=len(ordered_messages),
        incoming_count=incoming_count,
        latest_guest_issue=latest_guest_issue,
        latest_review=review_context,
        status=status,
    )
    risk_summary = _guest_stay_update_sentence(
        "Guest risk context",
        incoming_risk_updates[-8:],
        fallback="No guest language suggesting review risk stood out in the window.",
    )
    resolution_summary = _guest_stay_update_sentence(
        "Resolution context",
        resolution_updates[-6:],
        fallback="No clear closure or guest-confirmed resolution was found after the risky language.",
    )
    suggested_action = guest_stay_suggested_action(
        recent_unresolved_risks=recent_unresolved_risks,
        unresolved_risks=unresolved_risks,
        review_context=review_context,
        fallback=(latest_guest_issue or {}).get("suggested_action"),
    )
    source_metadata = {
        "portfolio_name": portfolio_name,
        "listing_name": listing_name,
        "risk_updates": risk_updates[-12:],
        "incoming_risk_updates": incoming_risk_updates[-12:],
        "recent_unresolved_risks": recent_unresolved_risks[-8:],
        "unresolved_risks": unresolved_risks[-8:],
        "resolution_updates": resolution_updates[-8:],
        "latest_risk_at": latest_risk_at.isoformat() if latest_risk_at else None,
        "latest_resolution_at": latest_resolution_at.isoformat() if latest_resolution_at else None,
        "review": review_context,
        "current_or_recent_stay": current_or_recent_stay,
        "attention_cutoff_at": attention_cutoff.isoformat(),
        "window_days": window_days,
        "window_start_at": window_start.isoformat(),
        "window_end_at": window_end.isoformat(),
    }
    memory_hash = stable_hash(
        {
            "reservation_id": getattr(reservation, "reservation_id", None),
            "message_count": len(ordered_messages),
            "last_message_at": last_message_at.isoformat() if last_message_at else None,
            "review": review_context,
            "risk_score": risk_score,
            "source_metadata": source_metadata,
        }
    )
    return {
        "guest_name": _guest_name_for_reservation(reservation),
        "status": status,
        "risk_level": risk_level,
        "risk_score": risk_score,
        "message_count": len(ordered_messages),
        "incoming_count": incoming_count,
        "last_message_at": last_message_at,
        "summary": _compact_memory_text(summary, limit=1400),
        "risk_summary": _compact_memory_text(risk_summary, limit=1200),
        "latest_guest_issue": _compact_memory_text((latest_guest_issue or {}).get("excerpt"), limit=700),
        "resolution_summary": _compact_memory_text(resolution_summary, limit=1000),
        "suggested_action": _compact_memory_text(suggested_action, limit=700),
        "memory_hash": memory_hash,
        "source_metadata": source_metadata,
    }


def guest_stay_memory_score(
    *,
    risk_updates: list[dict[str, Any]],
    recent_unresolved_risks: list[dict[str, Any]],
    unresolved_risks: list[dict[str, Any]],
    latest_risk_at: datetime | None,
    latest_resolution_at: datetime | None,
    review_context: dict[str, Any] | None,
    current_or_recent_stay: bool,
    window_end: datetime,
) -> float:
    """Score a stay by severity, recency, unresolved guest language, and review context."""
    incoming_risk_updates = [update for update in risk_updates if update.get("is_incoming")]
    score = max((float(update.get("weight") or 0.0) for update in incoming_risk_updates), default=0.0)
    if unresolved_risks:
        score += min(len(unresolved_risks) * 6.0, 18.0)
    if recent_unresolved_risks:
        score += 12.0
    if current_or_recent_stay and unresolved_risks:
        score += 8.0
    if latest_risk_at:
        age_hours = max((window_end - latest_risk_at).total_seconds() / 3600.0, 0.0)
        if age_hours <= 24:
            score += 8.0
        elif age_hours <= 72:
            score += 4.0
    if latest_resolution_at and latest_risk_at and latest_resolution_at > latest_risk_at:
        score -= 24.0
    if review_context:
        rating = review_context.get("rating")
        if rating is not None and float(rating) <= 3.0:
            score += 42.0
        elif rating is not None and float(rating) <= 3.5:
            score += 30.0
    return _bounded_rank_score(score, maximum=99.0)


def guest_stay_risk_level(score: float | int | None) -> str:
    parsed = float(score or 0.0)
    if parsed >= 85:
        return "critical"
    if parsed >= 65:
        return "high"
    if parsed >= 40:
        return "medium"
    return "low"


def guest_stay_signal_severity(memory: Any) -> str:
    risk_level = getattr(memory, "risk_level", None) or guest_stay_risk_level(getattr(memory, "risk_score", None))
    if risk_level in {"critical", "high", "medium", "low"}:
        return risk_level
    return "medium"


def guest_stay_signal_confidence(memory: Any) -> float:
    score = float(getattr(memory, "risk_score", None) or 0.0)
    message_count = int(getattr(memory, "message_count", None) or 0)
    base = 0.58 + min(score / 220.0, 0.3) + min(message_count / 100.0, 0.08)
    if getattr(memory, "review_rating", None) is not None:
        base += 0.04
    return round(min(max(base, 0.52), 0.92), 2)


def guest_stay_memory_rank_score(memory: Any) -> float:
    score = float(getattr(memory, "risk_score", None) or 0.0)
    base = {
        "critical": 92.0,
        "high": 84.0,
        "medium": 70.0,
        "low": 52.0,
    }.get(getattr(memory, "risk_level", None), 64.0)
    review_points = 5.0 if getattr(memory, "review_rating", None) is not None and float(getattr(memory, "review_rating") or 5.0) <= 3.5 else 0.0
    return _bounded_rank_score(base + min(score / 8.0, 10.0) + review_points, maximum=98.0)


def guest_stay_suggested_action(
    *,
    recent_unresolved_risks: list[dict[str, Any]],
    unresolved_risks: list[dict[str, Any]],
    review_context: dict[str, Any] | None,
    fallback: str | None,
) -> str:
    if recent_unresolved_risks:
        return recent_unresolved_risks[-1].get("suggested_action") or fallback or "Confirm resolution and send the guest a clear recovery update."
    if unresolved_risks:
        return unresolved_risks[-1].get("suggested_action") or fallback or "Check whether this guest issue was explicitly closed in Hostaway."
    if review_context and (review_context.get("rating") or 5) <= 3.5:
        return "Review the low-rated stay, document the root cause, and confirm whether the same issue is repeating at this property."
    return fallback or "No action needed unless new guest risk language appears."


def guest_stay_memory_excerpt(memory: Any) -> str:
    parts = [
        getattr(memory, "summary", None),
        getattr(memory, "risk_summary", None),
        getattr(memory, "latest_guest_issue", None),
        getattr(memory, "resolution_summary", None),
        getattr(memory, "suggested_action", None),
    ]
    return _compact_memory_text(" ".join(part for part in parts if part), limit=1800)


def _guest_stay_risk_update(message: Any, *, sender: str) -> dict[str, Any] | None:
    body = getattr(message, "content_preview", None) or ""
    if GUEST_STAY_RESOLUTION_RE.search(body) and not GUEST_STAY_UNRESOLVED_RE.search(body):
        return None
    for key, label, pattern, severity, weight, suggested_action in GUEST_STAY_RISK_RULES:
        if not pattern.search(body):
            continue
        occurred_at = getattr(message, "created_at", None)
        return {
            "message_id": getattr(message, "message_id", None),
            "topic_key": key,
            "topic_label": label,
            "severity": severity,
            "weight": weight,
            "sender": sender,
            "sender_type": getattr(message, "sender_type", None),
            "is_incoming": bool(getattr(message, "is_incoming", None)),
            "occurred_at": occurred_at.isoformat() if occurred_at else None,
            "excerpt": _truncate_chat_text(body, limit=260),
            "suggested_action": suggested_action,
        }
    return None


def _guest_stay_resolution_update(message: Any, *, sender: str) -> dict[str, Any] | None:
    body = getattr(message, "content_preview", None) or ""
    if not GUEST_STAY_RESOLUTION_RE.search(body):
        return None
    occurred_at = getattr(message, "created_at", None)
    return {
        "message_id": getattr(message, "message_id", None),
        "sender": sender,
        "sender_type": getattr(message, "sender_type", None),
        "is_incoming": bool(getattr(message, "is_incoming", None)),
        "occurred_at": occurred_at.isoformat() if occurred_at else None,
        "excerpt": _truncate_chat_text(body, limit=260),
    }


def _guest_stay_message_sender(message: Any) -> str:
    return (
        getattr(message, "sender_name", None)
        or getattr(message, "sender_type", None)
        or ("Guest" if bool(getattr(message, "is_incoming", None)) else "Host")
    )


def _guest_name_for_reservation(reservation: Any) -> str:
    return (
        getattr(reservation, "guest_name", None)
        or " ".join(
            item for item in (
                getattr(reservation, "guest_first_name", None),
                getattr(reservation, "guest_last_name", None),
            ) if item
        ).strip()
        or "Guest"
    )


def _guest_stay_review_context(review: Any | None) -> dict[str, Any] | None:
    if not review:
        return None
    review_date = getattr(review, "review_date", None)
    return {
        "review_id": getattr(review, "review_id", None),
        "rating": getattr(review, "overall_rating", None),
        "review_date": review_date.isoformat() if hasattr(review_date, "isoformat") else review_date,
        "reviewer_name": getattr(review, "reviewer_name", None),
        "excerpt": _truncate_chat_text(getattr(review, "review_text", None), limit=320),
    }


def _reservation_is_current_or_recent(reservation: Any, today: date) -> bool:
    arrival = getattr(reservation, "arrival_date", None)
    departure = getattr(reservation, "departure_date", None)
    if arrival and departure:
        return arrival - timedelta(days=1) <= today <= departure + timedelta(days=7)
    return False


def _guest_stay_summary_sentence(
    *,
    reservation: Any,
    listing_name: str,
    portfolio_name: str | None,
    message_count: int,
    incoming_count: int,
    latest_guest_issue: dict[str, Any] | None,
    latest_review: dict[str, Any] | None,
    status: str,
) -> str:
    guest_name = _guest_name_for_reservation(reservation)
    dates = _reservation_date_span(reservation)
    parts = [
        f"{guest_name} at {listing_name}",
        f"({portfolio_name})" if portfolio_name else "",
        f"{dates}." if dates else ".",
        f"{message_count} Hostaway messages in window, {incoming_count} from guest.",
    ]
    if latest_guest_issue:
        parts.append(f"Latest guest issue: {latest_guest_issue.get('excerpt')}")
    if latest_review:
        parts.append(f"Latest review: {latest_review.get('rating') or 'unknown'} stars; {latest_review.get('excerpt') or 'no text'}")
    if status == "ok" and not latest_guest_issue:
        parts.append("No unresolved guest-review risk stood out.")
    return " ".join(part for part in parts if part)


def _reservation_date_span(reservation: Any) -> str:
    arrival = getattr(reservation, "arrival_date", None)
    departure = getattr(reservation, "departure_date", None)
    if arrival and departure:
        return f"{arrival.isoformat()} to {departure.isoformat()}"
    if arrival:
        return f"arrival {arrival.isoformat()}"
    if departure:
        return f"departure {departure.isoformat()}"
    return ""


def _guest_stay_update_sentence(label: str, updates: list[dict[str, Any]], *, fallback: str) -> str:
    if not updates:
        return fallback
    parts = []
    for update in updates[-5:]:
        when = update.get("occurred_at")
        sender = update.get("sender") or "Someone"
        topic = update.get("topic_label")
        excerpt = update.get("excerpt") or ""
        prefix = f"{sender}"
        if when:
            prefix += f" at {when}"
        if topic:
            prefix += f" ({topic})"
        parts.append(f"{prefix}: {excerpt}")
    return f"{label}: " + " | ".join(parts)


def select_operating_signals(signals: list[Any], *, limit: int, per_category_limit: int = 2) -> list[Any]:
    """Choose a compact, category-balanced set for Today and daily briefs."""
    ordered = sorted(signals or [], key=operating_signal_sort_key)
    selected, category_counts = _select_operating_signal_pass(
        ordered,
        limit=limit,
        per_category_limit=per_category_limit,
        selected_ids=set(),
        category_counts={},
        enforce_attention_diversity=True,
    )
    if len(selected) >= limit:
        return selected
    selected_ids = {id(signal) for signal in selected}
    selected, _category_counts = _select_operating_signal_pass(
        ordered,
        limit=limit,
        per_category_limit=per_category_limit,
        selected_ids=selected_ids,
        category_counts=category_counts,
        enforce_attention_diversity=False,
        initial_selected=selected,
    )
    return selected


def _select_operating_signal_pass(
    ordered: list[Any],
    *,
    limit: int,
    per_category_limit: int,
    selected_ids: set[int],
    category_counts: dict[str, int],
    enforce_attention_diversity: bool,
    initial_selected: list[Any] | None = None,
) -> tuple[list[Any], dict[str, int]]:
    selected = list(initial_selected or [])
    seen_attention_keys = {
        key
        for key in (attention_signal_key(signal) for signal in selected)
        if key is not None
    }
    for signal in ordered:
        if id(signal) in selected_ids:
            continue
        category = getattr(signal, "category", None) or "unknown"
        if category_counts.get(category, 0) >= per_category_limit:
            continue
        attention_key = attention_signal_key(signal)
        if enforce_attention_diversity and attention_key is not None and attention_key in seen_attention_keys:
            continue
        selected.append(signal)
        selected_ids.add(id(signal))
        if attention_key is not None:
            seen_attention_keys.add(attention_key)
        category_counts[category] = category_counts.get(category, 0) + 1
        if len(selected) >= limit:
            return selected, category_counts
    return selected, category_counts


def attention_signal_key(signal: Any) -> tuple[str, Any] | None:
    """Return a key for user-attention duplicates across guest/review/open-loop signals."""
    category = getattr(signal, "category", None)
    if category not in {"guest_experience", "review_risk", "operational_open_loop", "checkin_checkout_risk"}:
        return None
    reservation_id = getattr(signal, "reservation_id", None)
    if reservation_id:
        return ("reservation", reservation_id)
    listing_id = getattr(signal, "listing_id", None)
    if listing_id:
        return ("listing", listing_id)
    return None


def operating_signal_sort_key(signal: Any) -> tuple[Any, ...]:
    severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    category_order = {
        "guest_experience": 0,
        "review_risk": 0,
        "checkin_checkout_risk": 0,
        "operational_open_loop": 1,
        "revenue_booking_health": 2,
        "owner_decision": 3,
    }
    severity = getattr(signal, "severity", None)
    category = getattr(signal, "category", None)
    return (
        severity_order.get(severity, 4),
        category_order.get(category, 4),
        -float(getattr(signal, "rank_score", None) or 0.0),
        _signal_last_seen_sort_value(getattr(signal, "last_seen_at", None)),
    )


def open_loop_item_sort_key(item: dict[str, Any]) -> tuple[Any, ...]:
    """Sort combined open-loop rows from persisted loops and active signals."""
    status_order = {"escalated": 0, "open": 0, "new": 0, "acknowledged": 1, "watching": 2}
    severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    last_seen = _parse_source_datetime(item.get("last_seen_at") or item.get("last_activity_at"))
    return (
        status_order.get(item.get("status"), 4),
        severity_order.get(item.get("severity"), 4),
        -float(item.get("rank_score") or 0.0),
        _signal_last_seen_sort_value(last_seen),
        item.get("title") or "",
    )


def _signal_last_seen_sort_value(value: Any) -> float:
    if not value:
        return 0.0
    try:
        return -float(value.timestamp())
    except Exception:
        return 0.0


def signal_evidence_sort_key(category: str | None, evidence: Any) -> tuple[Any, ...]:
    """Sort evidence so the reason for a signal appears before supporting context."""
    normalized_category = normalize_signal_category(category)
    source_type = getattr(evidence, "source_type", None) or ""
    if normalized_category == "revenue_booking_health":
        priority = {
            "booking_health_analysis": 0,
            "booking_health": 1,
            "pricelabs_snapshot": 2,
            "airbnb_page": 3,
            "hostaway_review": 5,
            "hostaway_message": 6,
            "whatsapp_message": 7,
        }
    elif normalized_category == "operational_open_loop":
        priority = {
            "whatsapp_message": 0,
            "whatsapp_group_memory": 1,
            "whatsapp_memory": 2,
            "hostaway_message": 3,
            "hostaway_review": 4,
            "booking_health": 6,
            "booking_health_analysis": 7,
        }
    elif normalized_category == "review_risk":
        priority = {
            "guest_stay_memory": 0,
            "hostaway_review": 1,
            "hostaway_message": 2,
            "whatsapp_message": 3,
            "whatsapp_memory": 4,
            "whatsapp_group_memory": 5,
            "booking_health_analysis": 6,
            "booking_health": 7,
        }
    elif normalized_category in {"guest_experience", "checkin_checkout_risk", "repeated_issue"}:
        priority = {
            "guest_stay_memory": 0,
            "hostaway_message": 1,
            "whatsapp_message": 2,
            "hostaway_review": 3,
            "whatsapp_memory": 4,
            "whatsapp_group_memory": 5,
            "booking_health_analysis": 6,
            "booking_health": 7,
        }
    else:
        priority = {}
    return (
        priority.get(source_type, 4),
        _signal_last_seen_sort_value(getattr(evidence, "occurred_at", None)),
        getattr(evidence, "evidence_id", None) or 0,
    )


def summarize_scheduled_read_health(reads: list[Any], *, now: datetime | None = None) -> dict[str, Any]:
    """Summarize whether scheduled morning/afternoon/nightly reads are current."""
    now = now or datetime.utcnow()
    required = {"morning", "afternoon"}
    normalized = []
    by_type = {}
    for item in reads or []:
        run_type = _object_value(item, "run_type")
        row = {
            "run_type": run_type,
            "status": _object_value(item, "status") or "missing",
            "signal_run_id": _object_value(item, "signal_run_id"),
            "started_at": _object_value(item, "started_at"),
            "completed_at": _object_value(item, "completed_at"),
            "brief_count": int(_object_value(item, "brief_count") or 0),
            "source_counts": _object_value(item, "source_counts") or {},
        }
        normalized.append(row)
        if run_type:
            by_type[run_type] = row
    missing_required = sorted(
        run_type
        for run_type in required
        if run_type not in by_type or by_type.get(run_type, {}).get("status") == "missing"
    )
    failed = [
        row
        for row in normalized
        if row.get("status") not in {"completed", "missing"} and row.get("run_type") in {"morning", "afternoon", "nightly"}
    ]
    read_completed = [
        _parse_source_datetime(row.get("completed_at"))
        for row in normalized
        if row.get("run_type") in required and row.get("status") == "completed"
    ]
    read_completed = [item for item in read_completed if item]
    latest_completed = max(read_completed, default=None)
    status = "ok"
    message = "Scheduled reads are current."
    if missing_required:
        status = "missing"
        message = f"Missing scheduled read history for {', '.join(missing_required)}."
    elif failed:
        status = "degraded"
        message = "At least one scheduled read did not complete."
    elif not latest_completed or latest_completed < now - timedelta(hours=36):
        status = "stale"
        message = "No morning or afternoon read has completed in the last 36 hours."
    return {
        "status": status,
        "message": message,
        "latest_read_completed_at": latest_completed.isoformat() if latest_completed else None,
        "reads": normalized,
    }


def summarize_brief_delivery(briefs: list[Any], delivery_logs: list[Any], run: Any | None = None) -> dict[str, Any]:
    """Summarize latest scheduled brief delivery across email and WhatsApp attempts."""
    brief_status_counts: dict[str, int] = {}
    for brief in briefs or []:
        status = _object_value(brief, "status") or "unknown"
        brief_status_counts[status] = brief_status_counts.get(status, 0) + 1
    channel_status_counts: dict[str, dict[str, int]] = {}
    attempts = []
    for log in delivery_logs or []:
        channel = _object_value(log, "channel") or "unknown"
        status = _object_value(log, "status") or "unknown"
        channel_counts = channel_status_counts.setdefault(channel, {})
        channel_counts[status] = channel_counts.get(status, 0) + 1
        attempted_at = _object_value(log, "attempted_at")
        attempts.append(
            {
                "channel": channel,
                "status": status,
                "recipient": _object_value(log, "recipient"),
                "provider": _object_value(log, "provider"),
                "attempted_at": attempted_at.isoformat() if hasattr(attempted_at, "isoformat") else attempted_at,
                "error": _object_value(log, "error_message"),
            }
        )
    status = "missing"
    message = "No scheduled brief has been generated yet."
    failure_statuses = {"error", "failed", "undelivered"}
    success_statuses = {"queued", "sent", "delivered", "accepted"}
    has_failure = bool(brief_status_counts.get("error")) or any(
        status in failure_statuses
        for statuses in channel_status_counts.values()
        for status in statuses
    )
    whatsapp_statuses = channel_status_counts.get("whatsapp", {})
    has_whatsapp_success = any(status in success_statuses for status in whatsapp_statuses)
    if briefs:
        status = "ok" if has_whatsapp_success or brief_status_counts.get("sent") else "missing"
        message = "Latest scheduled brief has a WhatsApp delivery attempt."
    if has_failure:
        status = "degraded"
        message = "Latest scheduled brief has a failed delivery or brief error."
    elif briefs and not delivery_logs:
        status = "missing"
        message = "Latest scheduled brief has no delivery attempts recorded."
    elif briefs and delivery_logs and not has_whatsapp_success and not brief_status_counts.get("sent"):
        status = "skipped"
        message = "Latest scheduled brief was generated, but no WhatsApp delivery was queued."
    generated_at_values = [
        _object_value(brief, "generated_at")
        for brief in briefs or []
        if _object_value(brief, "generated_at")
    ]
    latest_generated = max(generated_at_values, default=None)
    return {
        "status": status,
        "message": message,
        "signal_run_id": _object_value(run, "signal_run_id") if run else None,
        "run_type": _object_value(run, "run_type") if run else None,
        "brief_count": len(briefs or []),
        "brief_status_counts": brief_status_counts,
        "channel_status_counts": channel_status_counts,
        "latest_generated_at": latest_generated.isoformat() if hasattr(latest_generated, "isoformat") else latest_generated,
        "attempts": attempts[:8],
    }


def summarize_pricelabs_health(
    rows: list[Any],
    *,
    run_id: Any = None,
    expected_snapshot_count: int = 0,
    configured: bool = True,
    base_url: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Summarize whether PriceLabs snapshots are complete and fresh enough for revenue guidance."""
    now = now or datetime.utcnow()
    rows = rows or []
    expected_snapshot_count = int(expected_snapshot_count or 0)
    if not configured:
        return {
            "status": "not_configured",
            "message": "PriceLabs API credentials are not configured.",
            "run_id": run_id,
            "snapshot_count": len(rows),
            "expected_snapshot_count": expected_snapshot_count,
            "mapped_listing_count": expected_snapshot_count,
            "status_counts": {},
            "configured": False,
            "base_url": base_url,
        }
    if not rows:
        return {
            "status": "missing",
            "message": "No PriceLabs snapshots have been stored yet.",
            "run_id": run_id,
            "snapshot_count": 0,
            "expected_snapshot_count": expected_snapshot_count,
            "mapped_listing_count": expected_snapshot_count,
            "status_counts": {},
            "configured": True,
            "base_url": base_url,
        }

    status_counts: dict[str, int] = {}
    created_values: list[datetime] = []
    snapshot_dates = []
    for row in rows:
        status = _object_value(row, "status") or "unknown"
        status_counts[status] = status_counts.get(status, 0) + 1
        created = _parse_source_datetime(_object_value(row, "created_at"))
        if created:
            created_values.append(created)
        snapshot_date = _object_value(row, "snapshot_date")
        if snapshot_date:
            snapshot_dates.append(snapshot_date.isoformat() if hasattr(snapshot_date, "isoformat") else str(snapshot_date))

    ok_count = status_counts.get("ok", 0)
    latest_created = max(created_values, default=None)
    age_hours = _age_hours(latest_created, now)
    incomplete = expected_snapshot_count > 0 and len(rows) < expected_snapshot_count
    has_failures = ok_count < len(rows)
    stale = age_hours is not None and age_hours > 36

    if ok_count <= 0:
        status = "unavailable"
        message = "PriceLabs snapshots were attempted, but none returned usable pricing data."
    elif stale:
        status = "stale"
        message = "PriceLabs snapshots are older than 36 hours."
    elif incomplete:
        status = "degraded"
        message = "PriceLabs snapshots are missing for some mapped listings."
    elif has_failures:
        status = "degraded"
        message = "Some PriceLabs listing snapshots failed or returned non-OK status."
    else:
        status = "ok"
        message = "PriceLabs snapshots are fresh and complete for mapped listings."

    return {
        "status": status,
        "message": message,
        "run_id": run_id,
        "snapshot_count": len(rows),
        "expected_snapshot_count": expected_snapshot_count,
        "mapped_listing_count": expected_snapshot_count,
        "status_counts": status_counts,
        "configured": True,
        "base_url": base_url,
        "latest_snapshot_at": latest_created.isoformat() if latest_created else None,
        "snapshot_age_hours": round(age_hours, 2) if age_hours is not None else None,
        "latest_snapshot_date": max(snapshot_dates) if snapshot_dates else None,
        "sample_errors": [
            {
                "listing_id": _object_value(row, "listing_id"),
                "status": _object_value(row, "status"),
                "error": _object_value(row, "error_message"),
            }
            for row in rows
            if _object_value(row, "status") != "ok" and _object_value(row, "error_message")
        ][:5],
    }


def summarize_today_readiness(
    *,
    scheduled_reads: dict[str, Any],
    brief_delivery: dict[str, Any],
    hostaway: dict[str, Any],
    pricelabs: dict[str, Any],
    whatsapp: dict[str, Any],
) -> dict[str, Any]:
    """Condense system/data freshness into the first-screen operator trust signal."""
    components = [
        {
            "key": "scheduled_reads",
            "label": "Scheduled reads",
            "status": (scheduled_reads or {}).get("status") or "missing",
            "detail": (scheduled_reads or {}).get("latest_read_completed_at"),
        },
        {
            "key": "brief_delivery",
            "label": "Brief delivery",
            "status": (brief_delivery or {}).get("status") or "missing",
            "detail": _brief_delivery_component_detail(brief_delivery or {}),
        },
        {
            "key": "hostaway",
            "label": "Hostaway",
            "status": (hostaway or {}).get("status") or "missing",
            "detail": _hostaway_component_detail(hostaway or {}),
        },
        {
            "key": "pricelabs",
            "label": "PriceLabs",
            "status": (pricelabs or {}).get("status") or "missing",
            "detail": _pricelabs_component_detail(pricelabs or {}),
        },
        {
            "key": "whatsapp",
            "label": "WhatsApp",
            "status": (whatsapp or {}).get("status") or "missing",
            "detail": _whatsapp_component_detail(whatsapp or {}),
        },
    ]
    worst_status = _worst_readiness_status([component["status"] for component in components])
    return {
        "status": worst_status,
        "message": _today_readiness_message(worst_status, components),
        "latest_read_completed_at": (scheduled_reads or {}).get("latest_read_completed_at"),
        "components": components,
    }


def _brief_delivery_component_detail(brief_delivery: dict[str, Any]) -> str:
    counts = brief_delivery.get("channel_status_counts") or {}
    whatsapp = counts.get("whatsapp") or {}
    if whatsapp:
        return "WhatsApp " + "/".join(f"{count} {status}" for status, count in whatsapp.items())
    return brief_delivery.get("message") or ""


def _snapshot_count_detail(source: dict[str, Any]) -> str:
    count = source.get("snapshot_count")
    return f"{count} snapshots" if count is not None else ""


def _pricelabs_component_detail(pricelabs: dict[str, Any]) -> str:
    parts = []
    count = pricelabs.get("snapshot_count")
    expected = pricelabs.get("expected_snapshot_count")
    if count is not None and expected:
        parts.append(f"{count}/{expected} snapshots")
    elif count is not None:
        parts.append(f"{count} snapshots")
    latest = pricelabs.get("latest_snapshot_at")
    if latest:
        parts.append(f"latest {latest}")
    age = pricelabs.get("snapshot_age_hours")
    if age is not None:
        parts.append(f"{round(float(age), 1)}h old")
    message = pricelabs.get("message")
    if message and pricelabs.get("status") != "ok":
        parts.append(message)
    return "; ".join(parts)


def _hostaway_component_detail(hostaway: dict[str, Any]) -> str:
    parts = []
    active = hostaway.get("active_listing_count")
    mapped = hostaway.get("mapped_listing_count")
    if active is not None and mapped is not None:
        parts.append(f"{mapped}/{active} mapped listings")
    booking = hostaway.get("booking_analysis_count")
    if booking is not None:
        parts.append(f"{booking} booking analyses")
    memories = hostaway.get("guest_stay_memory_count")
    if memories is not None:
        parts.append(f"{memories} guest memories")
    completed = hostaway.get("completed_at")
    if completed:
        parts.append(f"latest {completed}")
    return "; ".join(parts) or hostaway.get("message") or ""


def _whatsapp_component_detail(whatsapp: dict[str, Any]) -> str:
    configured = whatsapp.get("configured_group_count")
    synced = whatsapp.get("synced_group_count")
    ingestion = whatsapp.get("ongoing_ingestion") or {}
    active_24h = ingestion.get("active_group_count_24h")
    message_count_24h = ingestion.get("message_count_24h")
    ingestion_status = ingestion.get("status")
    limited = whatsapp.get("history_limited_group_count") or 0
    incomplete = whatsapp.get("incomplete_history_group_count") or 0
    parts = []
    if configured is not None and synced is not None:
        parts.append(f"{synced}/{configured} groups")
    if active_24h is not None:
        activity = f"{active_24h} active in 24h"
        if message_count_24h is not None:
            activity += f", {message_count_24h} messages"
        parts.append(activity)
    if ingestion_status == "ok":
        parts.append("forward capture current")
    if limited or incomplete:
        parts.append(f"{limited + incomplete} older-history partial, informational")
    return "; ".join(parts)


def _worst_readiness_status(statuses: list[str]) -> str:
    rank = {
        "ok": 0,
        "completed": 0,
        "history_limited": 0,
        "skipped": 1,
        "not_configured": 1,
        "stale": 2,
        "incomplete_history": 2,
        "degraded": 2,
        "missing": 3,
        "missing_groups": 3,
        "not_found": 3,
        "unavailable": 3,
        "error": 3,
    }
    inverse = {0: "ok", 1: "watch", 2: "degraded", 3: "missing"}
    worst = max((rank.get(status, 2) for status in statuses or ["missing"]), default=3)
    return inverse[worst]


def _today_readiness_message(status: str, components: list[dict[str, Any]]) -> str:
    if status == "ok":
        return "Brain read is current and delivery/data sources look healthy."
    if status == "watch":
        watch_items = [component["label"] for component in components if _worst_readiness_status([component["status"]]) == "watch"]
        return f"Brain is usable; watch {', '.join(watch_items) or 'source coverage'}."
    if status == "degraded":
        degraded_items = [component["label"] for component in components if _worst_readiness_status([component["status"]]) == "degraded"]
        return f"Brain is running with degraded inputs: {', '.join(degraded_items) or 'review source health'}."
    missing_items = [component["label"] for component in components if _worst_readiness_status([component["status"]]) == "missing"]
    return f"Brain readiness needs attention: {', '.join(missing_items) or 'review source health'}."


def _object_value(item: Any, key: str) -> Any:
    if isinstance(item, dict):
        return item.get(key)
    return getattr(item, key, None)


def load_whatsapp_group_profiles(path: str | os.PathLike[str] | None = None) -> list[dict[str, Any]]:
    """Load the WhatsApp bridge allowlist without exposing bridge credentials."""
    config_path = Path(
        path
        or os.getenv("BRAIN_WHATSAPP_BRIDGE_GROUP_CONFIG")
        or Path(__file__).resolve().parent.parent / "whatsapp_bridge" / "groups.json"
    )
    try:
        payload = json.loads(config_path.read_text())
    except Exception as exc:
        logger.warning("Unable to load WhatsApp group config %s: %s", config_path, exc)
        return []
    groups = []
    for item in payload.get("groups", []) if isinstance(payload, dict) else []:
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        groups.append(
            {
                "name": name,
                "aliases": [str(alias).strip() for alias in item.get("aliases", []) if str(alias).strip()],
                "portfolio": item.get("portfolio"),
                "kind": item.get("kind"),
                "description": item.get("description"),
            }
        )
    return groups


def whatsapp_profile_for_group_name(group_name: str | None, profiles: list[dict[str, Any]] | None = None) -> dict[str, Any] | None:
    """Return the configured WhatsApp group profile matching a name or alias."""
    key = _normalize_source_name(group_name)
    if not key:
        return None
    for profile in profiles if profiles is not None else load_whatsapp_group_profiles():
        candidates = [profile.get("name"), *(profile.get("aliases") or [])]
        if key in {_normalize_source_name(candidate) for candidate in candidates if candidate}:
            return profile
    return None


def canonical_whatsapp_thread_rows(configured_groups: list[dict[str, Any]], thread_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge stored WhatsApp thread rows that are aliases of one configured group."""
    merged: dict[str, dict[str, Any]] = {}
    passthrough: list[dict[str, Any]] = []
    profiles = configured_groups or []
    for raw_row in thread_rows or []:
        row = dict(raw_row)
        profile = whatsapp_profile_for_group_name(row.get("display_name"), profiles)
        if not profile:
            passthrough.append(row)
            continue
        canonical_name = profile.get("name") or row.get("display_name")
        key = _normalize_source_name(canonical_name)
        if key not in merged:
            merged[key] = {
                "display_name": canonical_name,
                "provider_thread_id": row.get("provider_thread_id"),
                "portfolio_name": profile.get("portfolio") or row.get("portfolio_name"),
                "message_count_60d": 0,
                "message_count_7d": 0,
                "message_count_24h": 0,
                "account_sent_count_24h": 0,
                "first_received_at": None,
                "last_received_at": None,
                "matched_thread": None,
                "_actual_names": [],
            }
        target = merged[key]
        target["message_count_60d"] = int(target.get("message_count_60d") or 0) + int(row.get("message_count_60d") or 0)
        target["message_count_7d"] = int(target.get("message_count_7d") or 0) + int(row.get("message_count_7d") or 0)
        target["message_count_24h"] = int(target.get("message_count_24h") or 0) + int(row.get("message_count_24h") or 0)
        target["account_sent_count_24h"] = int(target.get("account_sent_count_24h") or 0) + int(row.get("account_sent_count_24h") or 0)
        target["first_received_at"] = _source_datetime_min_iso(target.get("first_received_at"), row.get("first_received_at"))
        target["last_received_at"] = _source_datetime_max_iso(target.get("last_received_at"), row.get("last_received_at"))
        actual_name = row.get("display_name")
        if actual_name and actual_name not in target["_actual_names"]:
            target["_actual_names"].append(actual_name)
    result = []
    for row in merged.values():
        actual_names = row.pop("_actual_names", [])
        row["matched_thread"] = ", ".join(actual_names) if actual_names else row.get("display_name")
        result.append(row)
    result.extend(passthrough)
    return result


def whatsapp_thread_memory_key(thread: Any, profiles: list[dict[str, Any]]) -> str:
    """Return the canonical grouping key used for WhatsApp rolling memory."""
    profile = whatsapp_profile_for_group_name(getattr(thread, "display_name", None), profiles)
    if profile:
        return f"profile:{_normalize_source_name(profile.get('name'))}"
    return f"thread:{getattr(thread, 'provider', None)}:{getattr(thread, 'provider_thread_id', None) or getattr(thread, 'whatsapp_thread_id', None)}"


def summarize_whatsapp_ingestion(
    thread_rows: list[dict[str, Any]],
    bridge_state: dict[str, Any] | None = None,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Summarize whether future WhatsApp ingestion appears current."""
    now = now or datetime.utcnow()
    latest_message_at = max(
        (_parse_source_datetime(row.get("last_received_at")) for row in thread_rows or []),
        default=None,
    )
    message_count_24h = sum(int(row.get("message_count_24h") or 0) for row in thread_rows or [])
    account_sent_count_24h = sum(int(row.get("account_sent_count_24h") or 0) for row in thread_rows or [])
    active_group_count_24h = len([row for row in thread_rows or [] if int(row.get("message_count_24h") or 0) > 0])
    last_backfill = (bridge_state or {}).get("last_backfill") or {}
    latest_backfill_at = _parse_source_datetime(last_backfill.get("completed_at") or last_backfill.get("started_at"))
    bridge_ready_at = _parse_source_datetime((bridge_state or {}).get("last_ready_at"))
    live_forwarded_at = _parse_source_datetime((bridge_state or {}).get("last_live_forwarded_at"))
    latest_message_age_hours = _age_hours(latest_message_at, now)
    latest_backfill_age_hours = _age_hours(latest_backfill_at, now)
    bridge_ready_age_hours = _age_hours(bridge_ready_at, now)
    live_forwarded_age_hours = _age_hours(live_forwarded_at, now)
    recent_reader_alive = any(
        age is not None and age <= 36
        for age in (latest_backfill_age_hours, bridge_ready_age_hours, live_forwarded_age_hours)
    )

    status = "ok"
    if not latest_message_at and not latest_backfill_at and not bridge_ready_at:
        status = "missing"
        message = "No WhatsApp messages, bridge heartbeat, or batch backfill records have been captured yet."
    elif not recent_reader_alive and latest_backfill_at and latest_backfill_age_hours is not None and latest_backfill_age_hours > 36:
        status = "stale"
        message = "The WhatsApp batch reader has not completed in more than 36 hours."
    elif not recent_reader_alive and not latest_backfill_at and latest_message_age_hours is not None and latest_message_age_hours > 36:
        status = "stale"
        message = "WhatsApp messages have not arrived in more than 36 hours, and no bridge heartbeat or batch backfill record is available."
    elif not recent_reader_alive and latest_message_at and latest_message_age_hours is not None and latest_message_age_hours > 36:
        status = "stale"
        message = "No new WhatsApp messages have arrived in more than 36 hours, and the bridge reader has not checked in recently."
    elif latest_message_at and latest_message_age_hours is not None and latest_message_age_hours > 36:
        message = "WhatsApp bridge/batch reader is current; no recent group messages were observed."
    else:
        message = "WhatsApp live bridge and batch ingestion look current."

    return {
        "status": status,
        "message": message,
        "message_count_24h": message_count_24h,
        "account_sent_count_24h": account_sent_count_24h,
        "active_group_count_24h": active_group_count_24h,
        "latest_message_at": latest_message_at.isoformat() if latest_message_at else None,
        "latest_message_age_hours": round(latest_message_age_hours, 2) if latest_message_age_hours is not None else None,
        "latest_backfill_at": latest_backfill_at.isoformat() if latest_backfill_at else None,
        "latest_backfill_age_hours": round(latest_backfill_age_hours, 2) if latest_backfill_age_hours is not None else None,
        "bridge_ready_at": bridge_ready_at.isoformat() if bridge_ready_at else None,
        "bridge_ready_age_hours": round(bridge_ready_age_hours, 2) if bridge_ready_age_hours is not None else None,
        "live_forwarded_at": live_forwarded_at.isoformat() if live_forwarded_at else None,
        "live_forwarded_age_hours": round(live_forwarded_age_hours, 2) if live_forwarded_age_hours is not None else None,
    }


def load_whatsapp_bridge_state(path: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    """Load the bridge state summary written by scheduled history syncs."""
    state_path = Path(
        path
        or os.getenv("BRAIN_WHATSAPP_BRIDGE_STATE_PATH")
        or Path(__file__).resolve().parent.parent / "data" / "whatsapp-bridge-state.json"
    )
    try:
        payload = json.loads(state_path.read_text())
    except FileNotFoundError:
        return {"state_path": str(state_path), "last_backfill": None}
    except Exception as exc:
        logger.warning("Unable to load WhatsApp bridge state %s: %s", state_path, exc)
        return {"state_path": str(state_path), "last_backfill": None, "error": str(exc)}
    last_backfills = payload.get("last_backfills") if isinstance(payload, dict) else None
    sorted_backfills = _sorted_whatsapp_backfills(last_backfills if isinstance(last_backfills, list) else [])
    last_backfill = sorted_backfills[0] if sorted_backfills else None
    coverage_backfill = _merged_whatsapp_coverage_backfill(sorted_backfills)
    return {
        "state_path": str(state_path),
        "last_backfill": last_backfill,
        "coverage_backfill": coverage_backfill,
        "last_ready_at": payload.get("last_ready_at"),
        "last_ready_status": payload.get("last_ready_status"),
        "last_live_forwarded_at": payload.get("last_live_forwarded_at"),
        "last_disconnected_at": payload.get("last_disconnected_at"),
        "last_disconnected_reason": payload.get("last_disconnected_reason"),
    }


def _sorted_whatsapp_backfills(backfills: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        [item for item in backfills if isinstance(item, dict)],
        key=lambda item: _parse_source_datetime(item.get("started_at")) or datetime.min,
        reverse=True,
    )


def _merged_whatsapp_coverage_backfill(backfills: list[dict[str, Any]], min_lookback_days: int = 60) -> dict[str, Any] | None:
    """Return latest per-group coverage from historical backfills, not short catch-ups."""
    groups: dict[str, Any] = {}
    newest_started_at: str | None = None
    source_count = 0
    for backfill in backfills:
        lookback_days = _safe_int(backfill.get("lookback_days"))
        if lookback_days < min_lookback_days:
            continue
        source_count += 1
        newest_started_at = newest_started_at or backfill.get("started_at")
        for name, state in (backfill.get("groups") or {}).items():
            if not name or name in groups or not isinstance(state, dict):
                continue
            groups[name] = {
                **state,
                "_lookback_days": lookback_days,
                "_backfill_reason": backfill.get("reason"),
                "_backfill_started_at": backfill.get("started_at"),
            }
    if not groups:
        return None
    return {
        "reason": "merged-60-day-coverage",
        "started_at": newest_started_at,
        "lookback_days": min_lookback_days,
        "source_backfill_count": source_count,
        "groups": groups,
    }


def whatsapp_source_group_health(
    configured_groups: list[dict[str, Any]],
    thread_rows: list[dict[str, Any]],
    bridge_state: dict[str, Any] | None = None,
    *,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Match expected WhatsApp groups to stored messages and bridge backfill state."""
    now = now or datetime.utcnow()
    stale_cutoff = now - timedelta(days=7)
    history_cutoff = now - timedelta(days=60)
    thread_rows = canonical_whatsapp_thread_rows(configured_groups, thread_rows)
    rows_by_name = {
        _normalize_source_name(row.get("display_name")): row
        for row in thread_rows
        if row.get("display_name")
    }
    last_backfill = (bridge_state or {}).get("last_backfill") or {}
    coverage_backfill = (bridge_state or {}).get("coverage_backfill") or last_backfill
    coverage_backfill_lookback_days = coverage_backfill.get("lookback_days")
    latest_state_by_name = {_normalize_source_name(name): value for name, value in (last_backfill.get("groups") or {}).items()}
    coverage_state_by_name = {_normalize_source_name(name): value for name, value in (coverage_backfill.get("groups") or {}).items()}
    health_rows = []
    for group in configured_groups:
        candidates = [group.get("name"), *(group.get("aliases") or [])]
        row = _first_matching_source_row(candidates, rows_by_name)
        state = dict(_first_matching_whatsapp_state(candidates, coverage_state_by_name) or {})
        if not state:
            state = dict(_first_matching_whatsapp_state(candidates, latest_state_by_name) or {})
        if coverage_backfill_lookback_days is not None and "_lookback_days" not in state:
            state["_lookback_days"] = coverage_backfill_lookback_days
        last_received = _parse_source_datetime(row.get("last_received_at") if row else None)
        count_60d = int((row or {}).get("message_count_60d") or 0)
        status = "ok"
        verified_empty = False
        if count_60d <= 0:
            if _whatsapp_group_verified_empty(state):
                status = "ok"
                verified_empty = True
            elif state.get("status") == "not_found":
                status = "not_found"
            else:
                status = "missing"
        else:
            if last_received and last_received < stale_cutoff:
                status = "stale"
            else:
                history_status = _whatsapp_group_history_status(state, row, history_cutoff)
                if history_status:
                    status = history_status
        annotation = _whatsapp_group_health_annotation(
            status=status,
            verified_empty=verified_empty,
            state=state,
            row=row,
            history_cutoff=history_cutoff,
            stale_cutoff=stale_cutoff,
        )
        health_rows.append(
            {
                "name": group.get("name"),
                "portfolio": group.get("portfolio"),
                "kind": group.get("kind"),
                "status": status,
                "verified_empty": verified_empty,
                "status_reason": annotation["reason"],
                "recommended_action": annotation["action"],
                "message_count_60d": count_60d,
                "message_count_7d": int((row or {}).get("message_count_7d") or 0),
                "message_count_24h": int((row or {}).get("message_count_24h") or 0),
                "account_sent_count_24h": int((row or {}).get("account_sent_count_24h") or 0),
                "last_received_at": (row or {}).get("last_received_at"),
                "first_received_at": (row or {}).get("first_received_at"),
                "matched_thread": (row or {}).get("matched_thread") or (row or {}).get("display_name"),
                "backfill": {
                    "lookback_days": state.get("lookback_days", state.get("_lookback_days")),
                    "reason": state.get("_backfill_reason"),
                    "started_at": state.get("_backfill_started_at"),
                    "sync_history": state.get("sync_history"),
                    "sync_history_attempts": state.get("sync_history_attempts"),
                    "fetch_rounds": state.get("fetch_rounds"),
                    "fetched": state.get("fetched"),
                    "within_lookback": state.get("within_lookback"),
                    "stored": state.get("stored"),
                    "duplicates": state.get("duplicates"),
                    "failed": state.get("failed"),
                    "reached_cutoff": state.get("reached_cutoff"),
                    "exhausted": state.get("exhausted"),
                    "newest_fetched_at": state.get("newest_fetched_at"),
                    "oldest_fetched_at": state.get("oldest_fetched_at"),
                },
            }
        )
    return health_rows


def _whatsapp_group_health_annotation(
    *,
    status: str,
    verified_empty: bool,
    state: dict[str, Any],
    row: dict[str, Any] | None,
    history_cutoff: datetime,
    stale_cutoff: datetime,
) -> dict[str, str]:
    """Return operator-facing source-health context for one expected group."""
    last_received = _parse_source_datetime((row or {}).get("last_received_at"))
    first_received = _parse_source_datetime((row or {}).get("first_received_at"))
    oldest_fetched = _parse_source_datetime(state.get("oldest_fetched_at"))
    if verified_empty:
        return {
            "reason": "The 60-day backfill reached the cutoff and found no messages to store for this group.",
            "action": "No action needed unless you expect this group to be active.",
        }
    if status == "missing":
        return {
            "reason": "Brain has no stored messages for this configured group.",
            "action": "Confirm the exact WhatsApp group name/alias, then run a 60-day backfill or import a WhatsApp export.",
        }
    if status == "not_found":
        return {
            "reason": "The paired WhatsApp account did not expose a matching group during the latest history sync.",
            "action": "Confirm the paired account is still a member and add an alias if WhatsApp shows the group under a different name.",
        }
    if status == "stale":
        return {
            "reason": "Brain has messages for this group, but none have arrived within the last 7 days.",
            "action": "Check whether the group is still active; if it is, rerun the scheduled backfill and verify the WhatsApp linked device.",
        }
    if status == "history_limited":
        oldest = oldest_fetched or first_received
        oldest_text = f" Oldest available message is {oldest.isoformat()}." if oldest else ""
        return {
            "reason": f"Brain has current messages, but WhatsApp Web stopped exposing older messages before the 60-day cutoff.{oldest_text}",
            "action": "No urgent action needed for daily operations. Ongoing twice-daily backfills will keep future messages current; import an export only if old context becomes necessary.",
        }
    if status == "incomplete_history":
        oldest = oldest_fetched or first_received
        cutoff_text = history_cutoff.isoformat()
        oldest_text = f" Oldest fetched/stored message is {oldest.isoformat()}." if oldest else ""
        return {
            "reason": f"The last available 60-day coverage proof does not reach the cutoff ({cutoff_text}).{oldest_text}",
            "action": "No urgent action for daily operations. Keep the paired device online; repair/import older history only if old context becomes necessary.",
        }
    if status == "ok":
        if last_received and last_received < stale_cutoff:
            return {
                "reason": "The group has 60-day coverage, but recent activity is low.",
                "action": "No immediate action needed unless the group should have newer messages.",
            }
        return {
            "reason": "Brain has current messages for this group.",
            "action": "No action needed.",
        }
    return {
        "reason": "Brain could not determine this group's source-health state.",
        "action": "Review the latest WhatsApp backfill logs and group mapping.",
    }


def _whatsapp_backfill_summary(last_backfill: dict[str, Any] | None) -> dict[str, Any] | None:
    if not last_backfill:
        return None
    return {
        "reason": last_backfill.get("reason"),
        "started_at": last_backfill.get("started_at"),
        "completed_at": last_backfill.get("completed_at"),
        "lookback_days": last_backfill.get("lookback_days"),
        "group_count": len(last_backfill.get("groups") or {}),
    }


def _whatsapp_group_history_incomplete(state: dict[str, Any], row: dict[str, Any] | None, history_cutoff: datetime) -> bool:
    """Return True when the last sync has messages but did not prove 60-day coverage."""
    return _whatsapp_group_history_status(state, row, history_cutoff) in {"incomplete_history", "history_limited"}


def _whatsapp_group_history_status(state: dict[str, Any], row: dict[str, Any] | None, history_cutoff: datetime) -> str | None:
    """Return the history coverage status for a group with stored messages."""
    if not state:
        return None
    if state.get("status") in {"not_found", "error"}:
        return "history_limited" if (row or {}).get("message_count_60d") else None
    oldest_fetched = _parse_source_datetime(state.get("oldest_fetched_at"))
    first_received = _parse_source_datetime((row or {}).get("first_received_at"))
    if oldest_fetched and oldest_fetched <= history_cutoff:
        return None
    if first_received and first_received <= history_cutoff:
        return None
    lookback_days = _safe_int(state.get("lookback_days", state.get("_lookback_days")))
    if state.get("reached_cutoff") is True and lookback_days >= 60:
        return None
    fetched = state.get("fetched")
    within_lookback = state.get("within_lookback")
    maxed_out = state.get("maxed_out")
    exhausted = state.get("exhausted")
    has_history = bool(_safe_int(fetched) or _safe_int(within_lookback))
    if exhausted is True and maxed_out is not True and has_history:
        return "history_limited"
    return "history_limited" if any(
        value
        for value in (
            fetched,
            within_lookback,
            maxed_out,
            exhausted is False,
        )
    ) else None


def _whatsapp_group_verified_empty(state: dict[str, Any]) -> bool:
    lookback_days = _safe_int(state.get("lookback_days", state.get("_lookback_days")))
    return bool(state.get("reached_cutoff") is True and lookback_days >= 60)


def _safe_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _filter_values(value: Any) -> list[str]:
    if value in (None, "", "all"):
        return []
    if isinstance(value, (list, tuple, set)):
        raw_values = value
    else:
        raw_values = str(value).split(",")
    return [str(item).strip() for item in raw_values if str(item).strip()]


def _first_matching_source_row(candidates: list[str | None], rows_by_name: dict[str, Any]) -> Any | None:
    for candidate in candidates:
        row = rows_by_name.get(_normalize_source_name(candidate))
        if row is not None:
            return row
    return None


def _first_matching_whatsapp_state(candidates: list[str | None], state_by_name: dict[str, Any]) -> Any | None:
    matches = [
        state_by_name.get(_normalize_source_name(candidate))
        for candidate in candidates
        if state_by_name.get(_normalize_source_name(candidate)) is not None
    ]
    if not matches:
        return None
    for state in matches:
        if not isinstance(state, dict) or state.get("status") not in {"not_found", "error"}:
            return state
    return matches[0]


def _normalize_source_name(value: str | None) -> str:
    return (
        " ".join((value or "").strip().lower().split())
        .replace("\u200e", "")
        .replace("\u200f", "")
        .replace("\u2018", "'")
        .replace("\u2019", "'")
        .replace("\u201b", "'")
        .replace("\u2032", "'")
    )


def _source_datetime_min_iso(left: str | datetime | None, right: str | datetime | None) -> str | None:
    values = [value for value in (_parse_source_datetime(left), _parse_source_datetime(right)) if value]
    return min(values).isoformat() if values else None


def _source_datetime_max_iso(left: str | datetime | None, right: str | datetime | None) -> str | None:
    values = [value for value in (_parse_source_datetime(left), _parse_source_datetime(right)) if value]
    return max(values).isoformat() if values else None


def _age_hours(value: datetime | None, now: datetime) -> float | None:
    if not value:
        return None
    return max((now - value).total_seconds() / 3600, 0.0)


def _parse_source_datetime(value: str | datetime | None) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None) if value.tzinfo else value
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo:
            return parsed.replace(tzinfo=None)
        return parsed
    except ValueError:
        return None


def _parse_source_date(value: Any) -> date | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def should_retire_deterministic_signal(signal: Any, current_run_id: int) -> bool:
    """Return True when a recomputed deterministic signal is absent from this run."""
    return (
        getattr(signal, "source", None) in STALE_RECOMPUTED_SIGNAL_SOURCES
        and getattr(signal, "run_id", None) != current_run_id
        and getattr(signal, "status", None) in ACTIVE_SIGNAL_STATUSES
        and getattr(signal, "status", None) != "escalated"
    )


def should_retire_openai_signal(signal: Any, current_run_id: int) -> bool:
    """Return True when an OpenAI signal belongs to an older model pass."""
    return (
        getattr(signal, "source", None) == OPENAI_SIGNAL_SOURCE
        and getattr(signal, "run_id", None) != current_run_id
        and getattr(signal, "status", None) in ACTIVE_SIGNAL_STATUSES
        and getattr(signal, "status", None) != "escalated"
    )


WHATSAPP_ACTION_RE = re.compile(
    r"\b(?:please|pls|plz|can you|could you|need|needs|make sure|confirm|follow up|follow-up|"
    r"check|fix|repair|send|schedule|call|ask|remind|update|look into|take care|handle|"
    r"verify|still|not done|not working|missing|issue|problem|asap|urgent)\b|\?",
    re.IGNORECASE,
)
WHATSAPP_RESOLUTION_RE = re.compile(
    r"\b(?:done|all set|fixed|resolved|completed|complete|confirmed|ready|sent|scheduled|"
    r"handled|took care|taken care|closed|cleaned|finished|good now|working now)\b",
    re.IGNORECASE,
)
WHATSAPP_LOW_SIGNAL_RE = re.compile(
    r"^(?:ok|okay|yes|no|thanks|thank you|got it|sounds good|perfect|great|np|no problem)[.! ]*$",
    re.IGNORECASE,
)
WHATSAPP_STRONG_RESOLUTION_RE = re.compile(
    r"\b(?:done|all set|fixed|resolved|completed|sent|scheduled|handled|took care|taken care|closed|cleaned|finished)\b",
    re.IGNORECASE,
)
WHATSAPP_COMMITTED_UPDATE_RE = re.compile(
    r"\b(?:(?:i|we)(?:'ll| will)|will)\s+(?:inform|tell|let|update|send|notify|call|reach out)\b|"
    r"\b(?:already|no problem).{0,80}\b(?:informed|told|sent|handled|updated|notified|will inform|will tell)\b",
    re.IGNORECASE,
)
WHATSAPP_HANDOFF_UPDATE_RE = re.compile(
    r"\b(?:i\s+)?(?:gave|sent|shared)\b.{0,80}\b(?:number|contact|info|details)\b.{0,80}\b(?:call|contact|reach out)\b",
    re.IGNORECASE,
)
WHATSAPP_ROUTINE_LOCATION_QUESTION_RE = re.compile(
    r"\b(?:where\s+(?:do|does|can|should|is|are)|do you know where|can you tell me where)\b"
    r".{0,100}\b(?:kept|located|put|keep|find|stored|trash cans?|trash|dumpster|firewood|wood|supplies|towels?|linens?)\b",
    re.IGNORECASE,
)
WHATSAPP_ROUTINE_SCHEDULE_RE = re.compile(
    r"\b(?:cleanings?\s+on|check[ -]?out\s+tomorrow|check[ -]?in\s+tomorrow|we have a check[ -]?out|"
    r"we have a check[ -]?in|just a reminder)\b",
    re.IGNORECASE,
)
WHATSAPP_ROUTINE_SCHEDULE_KEEP_RE = re.compile(
    r"\b(?:confirm|confirmed|ready|not ready|issue|problem|missing|blocked|guest says|guest is|late|early|asap|urgent|"
    r"can you|could you|please|pls|plz|need|needs|owner|eta)\b",
    re.IGNORECASE,
)
WHATSAPP_GENERAL_FOLLOWUP_KEEP_RE = re.compile(
    r"\b(?:confirm|confirmation|send|share|receipt|invoice|quote|estimate|order|ordered|buy|purchase|get one|get from|"
    r"store|schedule|call|follow up|follow-up|update|eta|approval|approve|decision|repair|fix|missing|not done|not working)\b",
    re.IGNORECASE,
)
WHATSAPP_TOPIC_RULES = (
    (
        "guest_review_risk",
        "guest issue or review risk",
        re.compile(r"\b(?:guest|refund|review|complain|complaint|angry|upset|bad review|unhappy|issue)\b", re.IGNORECASE),
        "high",
        "Confirm the guest-facing resolution, responsible person, and next update before this can become a review issue.",
        0.72,
    ),
    (
        "checkin_access",
        "check-in or access",
        re.compile(r"\b(?:check[ -]?in|check[ -]?out|lock|door|code|guest access|entry|keypad|key|parking)\b", re.IGNORECASE),
        "high",
        "Confirm the guest/access status and post the exact next step in the group.",
        0.7,
    ),
    (
        "cleaning_turnover",
        "cleaning or turnover",
        re.compile(r"\b(?:clean|cleaner|cleaning|turnover|linen|laundry|towel|trash|ready|checkout|check out)\b", re.IGNORECASE),
        "medium",
        "Ask the cleaner or responsible person to confirm completion with any blocker and expected ready time.",
        0.66,
    ),
    (
        "maintenance_hot_tub",
        "hot tub or pool maintenance",
        re.compile(r"\b(?:hot tub|hottub|spa|pool|jacuzzi)\b", re.IGNORECASE),
        "high",
        "Confirm whether the amenity is working, who owns the repair, and what guest message is needed.",
        0.7,
    ),
    (
        "maintenance_hvac",
        "HVAC or comfort issue",
        re.compile(r"\b(?:a/c|ac\b|air conditioning|heater|heat\b|thermostat|temperature|too hot|too cold)\b", re.IGNORECASE),
        "high",
        "Confirm troubleshooting status, vendor ETA, and guest communication for the comfort issue.",
        0.7,
    ),
    (
        "maintenance_plumbing",
        "plumbing or water issue",
        re.compile(r"\b(?:leak|toilet|plumbing|sink|shower|water|drain|clog|septic)\b", re.IGNORECASE),
        "high",
        "Confirm the water/plumbing issue lead, current status, and whether guest recovery is needed.",
        0.7,
    ),
    (
        "vendor_followup",
        "vendor or maintenance follow-up",
        re.compile(r"\b(?:handyman|contractor|maintenance|repair|repaired|fix|fixed|vendor|quote|estimate|invoice|luis|denice)\b", re.IGNORECASE),
        "medium",
        "Ask the assigned person to confirm responsibility, ETA, and completion criteria.",
        0.64,
    ),
    (
        "owner_decision",
        "decision or approval",
        re.compile(r"\b(?:approval|approve|decision|budget|replace|buy|purchase)\b", re.IGNORECASE),
        "medium",
        "Clarify what decision is needed, who decides, and the deadline.",
        0.62,
    ),
)
WHATSAPP_GENERAL_TOPIC = {
    "topic_key": "general_followup",
    "topic_label": "team follow-up",
    "severity": "medium",
    "suggested_action": "Ask the responsible person to confirm status and next step in the group.",
    "confidence": 0.58,
}
WHATSAPP_RESOLUTION_COMPATIBLE_TOPIC_KEYS = {
    "vendor_followup": {
        "vendor_followup",
        "maintenance_hot_tub",
        "maintenance_hvac",
        "maintenance_plumbing",
        "general_followup",
    },
    "maintenance_hot_tub": {"maintenance_hot_tub", "vendor_followup", "general_followup"},
    "maintenance_hvac": {"maintenance_hvac", "vendor_followup", "general_followup"},
    "maintenance_plumbing": {"maintenance_plumbing", "vendor_followup", "general_followup"},
    "cleaning_turnover": {"cleaning_turnover", "general_followup"},
    "checkin_access": {"checkin_access", "guest_review_risk", "general_followup"},
    "guest_review_risk": {"guest_review_risk", "checkin_access", "general_followup"},
    "owner_decision": {"owner_decision", "general_followup"},
}
WHATSAPP_ALIAS_STOPWORDS = {
    "airbnb",
    "and",
    "cabin",
    "clean",
    "cleaner",
    "cleaning",
    "club",
    "concierge",
    "contractor",
    "cottage",
    "guest",
    "haven",
    "havens",
    "house",
    "lodge",
    "maintenance",
    "management",
    "middlefork",
    "pt300",
    "resort",
    "resorts",
    "ridge",
    "stays",
    "team",
    "the",
    "unit",
    "urban",
}


def whatsapp_open_loop_candidate(body: str | None) -> dict[str, Any] | None:
    """Classify a WhatsApp message as a likely unresolved operational loop."""
    text = _normalize_whatsapp_text(body)
    if not text or WHATSAPP_LOW_SIGNAL_RE.match(text):
        return None
    if (WHATSAPP_COMMITTED_UPDATE_RE.search(text) or WHATSAPP_HANDOFF_UPDATE_RE.search(text)) and not _has_unresolved_language(text):
        return None
    if WHATSAPP_ROUTINE_LOCATION_QUESTION_RE.search(text):
        return None
    if _is_routine_schedule_message(text):
        return None
    if not WHATSAPP_ACTION_RE.search(text):
        return None
    if WHATSAPP_RESOLUTION_RE.search(text) and not _has_unresolved_language(text):
        return None
    topic = dict(_whatsapp_topic_for_text(text))
    if topic["topic_key"] == "general_followup" and not WHATSAPP_GENERAL_FOLLOWUP_KEEP_RE.search(text):
        return None
    if re.search(r"\b(?:urgent|asap|today|now|same day)\b", text, re.IGNORECASE):
        topic["severity"] = "high"
        topic["confidence"] = min(float(topic["confidence"]) + 0.06, 0.82)
    return topic


def whatsapp_resolution_candidate(body: str | None) -> dict[str, Any] | None:
    """Classify a WhatsApp message as a likely closure update."""
    text = _normalize_whatsapp_text(body)
    committed_update = bool(WHATSAPP_COMMITTED_UPDATE_RE.search(text) or WHATSAPP_HANDOFF_UPDATE_RE.search(text))
    if not text or not (WHATSAPP_RESOLUTION_RE.search(text) or committed_update):
        return None
    if WHATSAPP_ACTION_RE.search(text) and not committed_update and not WHATSAPP_STRONG_RESOLUTION_RE.search(text):
        return None
    return dict(_whatsapp_topic_for_text(text))


def whatsapp_resolution_matches_topic(resolution: dict[str, Any] | None, topic_key: str | None) -> bool:
    """Return whether a closure update can safely close a pending WhatsApp topic."""
    if not resolution or not topic_key:
        return False
    if topic_key == "*":
        return resolution.get("topic_key") == "general_followup"
    compatible = whatsapp_resolution_compatible_topic_keys(resolution)
    return "*" in compatible or topic_key in compatible


def whatsapp_resolution_compatible_topic_keys(resolution: dict[str, Any] | None) -> set[str]:
    if not resolution:
        return set()
    topic_key = resolution.get("topic_key")
    if topic_key == "general_followup":
        return {"*"}
    return set(WHATSAPP_RESOLUTION_COMPATIBLE_TOPIC_KEYS.get(topic_key, {topic_key}))


def whatsapp_resolution_compatible_topic_labels(resolution: dict[str, Any] | None) -> list[str]:
    keys = whatsapp_resolution_compatible_topic_keys(resolution)
    if "*" in keys:
        return [WHATSAPP_GENERAL_TOPIC["topic_label"]]
    return [
        label
        for key, label, *_rest in WHATSAPP_TOPIC_RULES
        if key in keys
    ] + ([WHATSAPP_GENERAL_TOPIC["topic_label"]] if "general_followup" in keys else [])


def whatsapp_signal_title(thread_name: str | None, topic_label: str | None) -> str:
    """Return the stable signal title used for WhatsApp open-loop dedupe."""
    return f"Open loop in {thread_name or 'team WhatsApp'}: {topic_label or 'team follow-up'}"


def listing_aliases_for_whatsapp(raw_names: list[str | None]) -> list[str]:
    """Return normalized listing aliases safe enough for WhatsApp property inference."""
    aliases: list[str] = []
    for raw_name in raw_names or []:
        normalized = _normalize_match_text(raw_name)
        if not normalized:
            continue
        aliases.append(normalized)
        no_leading_the = re.sub(r"^the\s+", "", normalized)
        aliases.append(no_leading_the)
        tokens = normalized.split()
        if len(tokens) >= 2:
            aliases.append(" ".join(tokens[:2]))
            aliases.append(" ".join(tokens[-2:]))
        if "-" in str(raw_name or ""):
            aliases.append(_normalize_match_text(str(raw_name).replace("-", " ")))
        for token in tokens:
            if _token_is_useful_listing_alias(token):
                aliases.append(token)
    return sorted({alias for alias in aliases if _alias_is_useful_for_whatsapp(alias)}, key=lambda item: (-len(item), item))


def infer_whatsapp_listing(
    *,
    thread_name: str | None,
    message_bodies: list[str | None],
    listing_aliases: list[dict[str, Any]],
) -> dict[str, Any]:
    """Infer a listing from WhatsApp group/message text when the match is unambiguous."""
    thread_text = _normalize_match_text(thread_name)
    message_text = _normalize_match_text(" ".join(body or "" for body in message_bodies or []))
    best_by_listing: dict[int, dict[str, Any]] = {}
    for alias_row in listing_aliases or []:
        alias = _normalize_match_text(alias_row.get("alias"))
        if not _alias_is_useful_for_whatsapp(alias):
            continue
        thread_match = _text_contains_alias(thread_text, alias)
        message_match = _text_contains_alias(message_text, alias)
        if not thread_match and not message_match:
            continue
        alias_score = min(len(alias) / 16.0, 1.0)
        score = alias_score
        match_source = "message"
        if message_match:
            score += 1.0
        if thread_match:
            score += 2.0
            match_source = "thread"
        if re.search(r"\d", alias):
            score += 0.5
        if len(alias.split()) >= 2:
            score += 0.35
        listing_id = int(alias_row["listing_id"])
        existing = best_by_listing.get(listing_id)
        if existing and existing["score"] >= score:
            continue
        best_by_listing[listing_id] = {
            "listing_id": listing_id,
            "listing_name": alias_row.get("listing_name"),
            "portfolio_id": alias_row.get("portfolio_id"),
            "matched_alias": alias,
            "match_source": match_source,
            "score": round(score, 3),
        }
    ranked = sorted(best_by_listing.values(), key=lambda item: (-float(item["score"]), item.get("listing_name") or ""))
    if not ranked:
        return {}
    if ranked[0]["score"] < 1.75:
        return {}
    if len(ranked) > 1 and ranked[0]["score"] - ranked[1]["score"] < 0.35:
        return {}
    return ranked[0]


def _normalize_match_text(value: str | None) -> str:
    return " ".join(re.sub(r"[^a-zA-Z0-9]+", " ", str(value or "").lower()).split())


def _text_contains_alias(text: str, alias: str) -> bool:
    if not text or not alias:
        return False
    return bool(re.search(rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])", text))


def _token_is_useful_listing_alias(token: str) -> bool:
    if len(token) >= 4:
        return token not in WHATSAPP_ALIAS_STOPWORDS
    return len(token) >= 2 and bool(re.search(r"\d", token))


def _alias_is_useful_for_whatsapp(alias: str) -> bool:
    if not alias or alias in WHATSAPP_ALIAS_STOPWORDS:
        return False
    compact = alias.replace(" ", "")
    if len(compact) < 3:
        return False
    if len(alias.split()) == 1 and alias in WHATSAPP_ALIAS_STOPWORDS:
        return False
    return True


def _is_routine_schedule_message(text: str) -> bool:
    return bool(WHATSAPP_ROUTINE_SCHEDULE_RE.search(text) and not WHATSAPP_ROUTINE_SCHEDULE_KEEP_RE.search(text))


ASK_STOPWORDS = {
    "about",
    "after",
    "again",
    "biggest",
    "brain",
    "could",
    "doing",
    "from",
    "have",
    "happened",
    "high",
    "issue",
    "issues",
    "management",
    "message",
    "messages",
    "need",
    "needs",
    "open",
    "please",
    "risk",
    "risks",
    "show",
    "status",
    "tell",
    "that",
    "their",
    "there",
    "today",
    "what",
    "when",
    "where",
    "which",
    "with",
}
ASK_ENTITY_TERMS = {
    "crestwood",
    "crockett",
    "enchanted",
    "havens",
    "luminary",
    "middlefork",
    "pt300",
    "resorts",
    "urban",
}
ASK_ACTIVE_SIGNAL_RE = re.compile(
    r"\b(?:miss|missed|action|actions|today|urgent|priority|priorities|guest|review|bad review|"
    r"unresolved|open loop|follow[- ]?up|booking|momentum|revenue|roi|price|pricing|highest roi)\b",
    re.IGNORECASE,
)
ASK_GUEST_RISK_RE = re.compile(
    r"\b(?:guest|review|bad review|complaint|refund|sentiment|experience|check[ -]?in|check[ -]?out|arrival|access)\b",
    re.IGNORECASE,
)
ASK_OPEN_LOOP_RE = re.compile(
    r"\b(?:open loop|unresolved|follow[- ]?up|still open|not closed|pending|confirmation|confirm)\b",
    re.IGNORECASE,
)
ASK_REVENUE_RE = re.compile(
    r"\b(?:booking|momentum|revenue|roi|price|pricing|pricelabs|occupancy|pace|listing losing|highest roi)\b",
    re.IGNORECASE,
)
ASK_CORE_OPERATING_QUESTION_KEYS = {
    "what did i miss": "missed",
    "what needs action today": "action_today",
    "which guest issue could become a bad review": "guest_review_risk",
    "which operational loop is still unresolved": "unresolved_loop",
    "which listing is losing booking momentum": "booking_momentum",
    "what action has the highest roi": "highest_roi",
    "what action has highest roi": "highest_roi",
    "what action will create the highest roi": "highest_roi",
    "what action will create highest roi": "highest_roi",
    "which action will create the highest roi": "highest_roi",
    "which action will create highest roi": "highest_roi",
    "what action creates the highest roi": "highest_roi",
    "what action creates highest roi": "highest_roi",
}
ASK_EXPLICIT_SCOPE_RE = re.compile(
    r"\b(?:portfolio|property|listing|group)\s+([a-zA-Z0-9][a-zA-Z0-9' -]{0,80})",
    re.IGNORECASE,
)
ASK_SCOPE_TRAILING_RE = re.compile(
    r"\b(?:with|about|for|today|yesterday|tomorrow|that|where|which|when|who|has|have|having|issue|issues|risk|risks|message|messages)\b",
    re.IGNORECASE,
)


def ask_question_should_start_from_active_signals(question: str | None, terms: list[str] | None = None) -> bool:
    """Return True for operating-picture questions that should start with active signals."""
    text = question or ""
    if ASK_ACTIVE_SIGNAL_RE.search(text):
        return True
    if re.search(r"\bwhat happened\b|\bhappened\b", text, re.IGNORECASE) and ask_active_signal_filter_terms(terms or []):
        return True
    return bool({"miss", "missed", "urgent", "priority", "unresolved", "booking", "revenue"}.intersection(terms or []))


def ask_model_question(question: str | None) -> str:
    """Clarify Brain-specific language before sending Ask questions to the model."""
    text = (question or "").strip()
    if ASK_REVENUE_RE.search(text) and re.search(r"\b(?:roi|highest roi|high roi)\b", text, re.IGNORECASE):
        return (
            f"{text}\n\n"
            "In STR Signal Brain, 'high ROI' means a high-ROI action opportunity or revenue-recovery priority, "
            "not that the listing is currently performing well. Explain the weak booking-health evidence, why acting "
            "could recover revenue, and what action the evidence supports."
        )
    return text


def ask_core_operating_question_key(question: str | None) -> str | None:
    """Return the Today snapshot key for exact core operating questions."""
    normalized = _normalize_ask_question(question)
    return ASK_CORE_OPERATING_QUESTION_KEYS.get(normalized)


def _normalize_ask_question(question: str | None) -> str:
    return " ".join(re.sub(r"[^a-zA-Z0-9]+", " ", question or "").lower().split())


def core_operating_answer_text(item: dict[str, Any]) -> str:
    """Build a concise cited answer for a Today snapshot card."""
    answer = (item.get("answer") or item.get("title") or "").strip()
    title = (item.get("title") or "").strip()
    summary = (item.get("summary") or "").strip()
    action = (item.get("suggested_action") or "").strip()
    parts = [_operating_answer_sentence(answer)]
    if title and not _operating_answer_part_redundant(title, answer, item):
        parts.append(title)
    if summary:
        parts.append(summary)
    if action:
        parts.append(f"Next: {action}")
    return _clean_operating_answer_text(" ".join(_operating_answer_sentence(part) for part in parts if part))


def _operating_answer_sentence(text: str | None) -> str:
    value = " ".join((text or "").strip().split())
    if not value:
        return ""
    if value.startswith("Next:") or re.search(r"[.!?]\s*$", value):
        return value
    return f"{value}."


def _operating_answer_part_redundant(part: str, answer: str, item: dict[str, Any]) -> bool:
    normalized_part = _normalize_ask_question(part)
    normalized_answer = _normalize_ask_question(answer)
    normalized_listing = _normalize_ask_question(item.get("listing_name"))
    if not normalized_part:
        return True
    if normalized_part == normalized_answer:
        return True
    if normalized_listing and normalized_part == normalized_listing:
        return True
    if item.get("key") == "missed" and normalized_part.endswith("needs a skim"):
        return True
    if item.get("key") == "highest_roi" and normalized_listing:
        generic_revenue_titles = {
            f"booking health needs action for {normalized_listing}",
            f"critical booking health gap for {normalized_listing}",
            f"revenue action for {normalized_listing}",
        }
        if normalized_part in generic_revenue_titles:
            return True
    return normalized_part in normalized_answer


def _clean_operating_answer_text(text: str | None) -> str:
    cleaned = clean_team_reference_text(text)
    return " ".join(cleaned.split())


def clean_team_reference_text(text: str | None) -> str:
    """Remove raw WhatsApp handles and placeholder contact labels from operator-facing text."""
    cleaned = re.sub(r"@\d{7,}", "team member", text or "")
    cleaned = re.sub(r"\bby\s+[._-]{2,}\s*:\s*team member\b", "by team member", cleaned)
    cleaned = re.sub(r"\bby\s+[._-]{2,}\s*:", "by team member:", cleaned)
    cleaned = re.sub(r"\b[._-]{2,}\s*:", "team member:", cleaned)
    return cleaned


def ask_signal_categories_for_question(question: str | None) -> set[str] | None:
    """Infer which active signal categories best answer an Ask Brain question."""
    text = question or ""
    categories: set[str] = set()
    if ASK_GUEST_RISK_RE.search(text):
        categories.update({"guest_experience", "review_risk", "checkin_checkout_risk"})
    if ASK_OPEN_LOOP_RE.search(text):
        categories.add("operational_open_loop")
    if ASK_REVENUE_RE.search(text):
        categories.add("revenue_booking_health")
    return categories or None


def ask_signal_matches_entity_terms(signal: Any, terms: list[str], portfolio_names: dict[int, str], listing_name: str | None) -> bool:
    """Return True when an active signal belongs to the requested portfolio/listing/group terms."""
    haystack = " ".join(
        str(value or "").lower()
        for value in (
            getattr(signal, "title", None),
            getattr(signal, "summary", None),
            getattr(signal, "suggested_action", None),
            portfolio_names.get(getattr(signal, "portfolio_id", None)),
            listing_name,
        )
    )
    return all(term in haystack for term in terms)


def ask_active_signal_filter_terms(terms: list[str] | None) -> list[str]:
    """Return Ask terms safe to use as active-signal entity/listing filters."""
    result = []
    for term in terms or []:
        normalized = str(term or "").strip().lower()
        if not normalized:
            continue
        if normalized in ASK_ENTITY_TERMS or _ask_term_looks_like_listing_code(normalized):
            result.append(normalized)
    return result


def _ask_term_looks_like_listing_code(term: str) -> bool:
    compact = term.replace("-", "")
    return len(compact) >= 3 and bool(re.search(r"\d", compact))


def ask_response_is_evidence_backed(answer: str | None, citations: list[dict[str, Any]] | None) -> bool:
    """Ask Brain answers must include text plus at least one valid stored-evidence citation."""
    return bool((answer or "").strip() and citations)


def ask_explicit_scope_is_known(scope_text: str | None, known_scope_text: str) -> bool:
    """Return True when at least one useful explicit-scope token is known to Brain."""
    terms = _ask_search_terms(scope_text)
    if not terms:
        return True
    known = known_scope_text.lower()
    return any(term in known for term in terms)


def ask_insufficient_response(
    *,
    prompt_version: str,
    reason: str,
    missing_data: str,
    evidence_count: int = 0,
) -> dict[str, Any]:
    """Return a consistent refusal when Ask Brain cannot answer from cited evidence."""
    return {
        "answer": f"I do not have enough cited evidence to answer that safely. {reason} {missing_data}".strip(),
        "confidence": 0.0,
        "citations": [],
        "insufficient_evidence": True,
        "evidence_count": evidence_count,
        "missing_data": missing_data,
        "prompt_version": prompt_version,
    }


def _ask_explicit_scope_phrases(question: str | None) -> list[str]:
    """Extract explicit scope phrases such as 'portfolio PT300' or 'group Luminary Cleaning'."""
    phrases: list[str] = []
    for match in ASK_EXPLICIT_SCOPE_RE.finditer(question or ""):
        scope = ASK_SCOPE_TRAILING_RE.split(match.group(1), maxsplit=1)[0].strip(" .,:;!?")
        if scope and scope.lower() not in {"portfolio", "property", "listing", "group"}:
            phrases.append(scope)
    return phrases


def _ask_search_terms(question: str | None) -> list[str]:
    """Extract useful search terms while preserving portfolio/group words."""
    text = re.sub(r"[^a-zA-Z0-9' -]+", " ", question or "").lower()
    terms = []
    for term in text.split():
        normalized = term.strip("'")
        if len(normalized) < 4 or normalized in ASK_STOPWORDS:
            continue
        if normalized not in terms:
            terms.append(normalized)
    return terms[:10]


def _dedupe_evidence_rows(rows: list[SignalEvidence]) -> list[SignalEvidence]:
    deduped: list[SignalEvidence] = []
    seen = set()
    for row in rows:
        key = (row.source_type, row.source_id or str(row.evidence_id))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)
    return deduped


def _whatsapp_topic_for_text(text: str) -> dict[str, Any]:
    for topic_key, topic_label, pattern, severity, suggested_action, confidence in WHATSAPP_TOPIC_RULES:
        if pattern.search(text):
            return {
                "topic_key": topic_key,
                "topic_label": topic_label,
                "severity": severity,
                "suggested_action": suggested_action,
                "confidence": confidence,
            }
    return dict(WHATSAPP_GENERAL_TOPIC)


def _has_unresolved_language(text: str) -> bool:
    return bool(re.search(r"\b(?:still|not done|not working|waiting|pending|need|needs|please|pls|plz|can you)\b|\?", text, re.IGNORECASE))


def _normalize_whatsapp_text(body: str | None) -> str:
    without_urls = re.sub(r"https?://\S+", " ", body or "")
    return " ".join(without_urls.strip().lower().split())


def _truncate_chat_text(body: str | None, limit: int = 520) -> str:
    text = " ".join((body or "").strip().split())
    if len(text) <= limit:
        return text
    return f"{text[: max(limit - 3, 0)].rstrip()}..."


def _message_group_kind(message: WhatsAppMessage) -> str:
    raw_payload = message.raw_payload if isinstance(message.raw_payload, dict) else {}
    return raw_payload.get("group_kind") or "team"


WHATSAPP_MEMORY_RISK_RE = re.compile(
    r"\b(?:guest|refund|bad review|review|complaint|angry|upset|locked out|no access|not working|broken|dirty|urgent|asap)\b",
    re.IGNORECASE,
)
WHATSAPP_MEMORY_DECISION_RE = re.compile(
    r"\b(?:approval|approve|decision|budget|quote|estimate|replace|buy|purchase|owner)\b",
    re.IGNORECASE,
)
WHATSAPP_MEMORY_CLEANING_MAINTENANCE_RE = re.compile(
    r"\b(?:clean|cleaner|cleaning|turnover|linen|laundry|towel|maintenance|repair|fix|vendor|contractor|"
    r"hot tub|pool|hvac|a/c|ac\b|plumbing|leak|clog|water|door|lock|code)\b",
    re.IGNORECASE,
)
WHATSAPP_MEMORY_ATTENTION_DAYS = 3


def build_whatsapp_group_memory_payload(
    *,
    thread: Any,
    messages: list[Any],
    window_start: datetime,
    window_end: datetime,
    window_days: int,
) -> dict[str, Any]:
    """Build a compact rolling memory from raw WhatsApp group messages."""
    ordered_messages = sorted(
        [message for message in messages or [] if (getattr(message, "body", None) or "").strip()],
        key=lambda message: getattr(message, "received_at", None) or datetime.min,
    )
    group_name = getattr(thread, "display_name", None) or "team WhatsApp"
    group_kind = _thread_group_kind(thread, ordered_messages)
    participants: Counter[str] = Counter()
    topic_counts: Counter[str] = Counter()
    unresolved_topics: dict[str, dict[str, Any]] = {}
    latest_updates: list[dict[str, Any]] = []
    risk_updates: list[dict[str, Any]] = []
    decision_updates: list[dict[str, Any]] = []
    cleaning_maintenance_updates: list[dict[str, Any]] = []
    resolution_count = 0
    last_resolution_at: datetime | None = None

    for message in ordered_messages:
        body = getattr(message, "body", None) or ""
        sender = _whatsapp_memory_sender(message)
        received_at = getattr(message, "received_at", None)
        participants[sender] += 1
        resolution = whatsapp_resolution_candidate(body)
        if resolution:
            resolution_count += 1
            if received_at and (last_resolution_at is None or received_at > last_resolution_at):
                last_resolution_at = received_at
            unresolved_topics.pop(resolution["topic_key"], None)
        candidate = whatsapp_open_loop_candidate(body)
        if candidate:
            topic_counts[candidate["topic_label"]] += 1
            unresolved_topics[candidate["topic_key"]] = {
                "topic_key": candidate["topic_key"],
                "topic_label": candidate["topic_label"],
                "severity": candidate["severity"],
                "message_id": getattr(message, "whatsapp_message_id", None),
                "sender": sender,
                "received_at": received_at.isoformat() if received_at else None,
                "excerpt": _truncate_chat_text(body, limit=220),
            }
        update = _whatsapp_memory_update(message, sender=sender)
        if update and not WHATSAPP_LOW_SIGNAL_RE.match(_normalize_whatsapp_text(body)):
            latest_updates.append(update)
        if WHATSAPP_MEMORY_RISK_RE.search(body):
            risk_updates.append(update)
        if WHATSAPP_MEMORY_DECISION_RE.search(body):
            decision_updates.append(update)
        if WHATSAPP_MEMORY_CLEANING_MAINTENANCE_RE.search(body):
            cleaning_maintenance_updates.append(update)

    latest_updates = [item for item in latest_updates if item][-12:]
    risk_updates = [item for item in risk_updates if item][-8:]
    decision_updates = [item for item in decision_updates if item][-8:]
    cleaning_maintenance_updates = [item for item in cleaning_maintenance_updates if item][-8:]
    unresolved_list = sorted(
        unresolved_topics.values(),
        key=lambda item: _parse_source_datetime(item.get("received_at")) or datetime.min,
        reverse=True,
    )
    last_message_at = getattr(ordered_messages[-1], "received_at", None) if ordered_messages else None
    first_message_at = getattr(ordered_messages[0], "received_at", None) if ordered_messages else None
    attention_cutoff = window_end - timedelta(days=WHATSAPP_MEMORY_ATTENTION_DAYS)
    recent_unresolved_list = [
        topic for topic in unresolved_list
        if _source_datetime_is_at_or_after(topic.get("received_at"), attention_cutoff)
    ]
    recent_risk_updates = [
        update for update in risk_updates
        if _source_datetime_is_at_or_after(update.get("received_at"), attention_cutoff)
        and (last_resolution_at is None or _source_datetime_is_after(update.get("received_at"), last_resolution_at))
    ]
    recent_decision_updates = [
        update for update in decision_updates
        if _source_datetime_is_at_or_after(update.get("received_at"), attention_cutoff)
    ]
    recent_cleaning_maintenance_updates = [
        update for update in cleaning_maintenance_updates
        if _source_datetime_is_at_or_after(update.get("received_at"), attention_cutoff)
    ]
    attention_score = whatsapp_memory_attention_score(
        recent_unresolved_topics=recent_unresolved_list,
        recent_risk_updates=recent_risk_updates,
        message_count=len(ordered_messages),
        last_message_at=last_message_at,
        window_end=window_end,
    )
    status = "watch" if attention_score > 0 else "ok"
    source_metadata = {
        "participants": dict(participants.most_common(20)),
        "topic_counts": dict(topic_counts),
        "unresolved_topics": unresolved_list[:8],
        "recent_unresolved_topics": recent_unresolved_list[:8],
        "latest_updates": latest_updates,
        "risk_updates": risk_updates,
        "recent_risk_updates": recent_risk_updates,
        "decision_updates": decision_updates,
        "recent_decision_updates": recent_decision_updates,
        "cleaning_maintenance_updates": cleaning_maintenance_updates,
        "recent_cleaning_maintenance_updates": recent_cleaning_maintenance_updates,
        "resolution_count": resolution_count,
        "last_resolution_at": last_resolution_at.isoformat() if last_resolution_at else None,
        "attention_score": attention_score,
        "attention_cutoff_at": attention_cutoff.isoformat(),
        "first_message_at": first_message_at.isoformat() if first_message_at else None,
        "window_start_at": window_start.isoformat(),
        "window_end_at": window_end.isoformat(),
    }
    summary = _compact_memory_text(
        " ".join(
            part for part in (
                _whatsapp_group_activity_sentence(group_name, ordered_messages, participants, window_days),
                _whatsapp_latest_updates_sentence(latest_updates),
                _whatsapp_unresolved_topics_sentence(unresolved_list),
            ) if part
        ),
        limit=1400,
    )
    open_loop_summary = _whatsapp_unresolved_topics_sentence(unresolved_list)
    risk_summary = _whatsapp_memory_update_sentence("Risk language", risk_updates, fallback="No high-risk guest/review language stood out in the window.")
    decision_summary = _whatsapp_memory_update_sentence("Decision/approval mentions", decision_updates, fallback="No clear decision or approval thread stood out.")
    cleaning_maintenance_summary = _whatsapp_memory_update_sentence(
        "Cleaning/maintenance context",
        cleaning_maintenance_updates,
        fallback="No specific cleaning or maintenance pattern stood out.",
    )
    memory_hash = stable_hash(
        {
            "thread": getattr(thread, "provider_thread_id", None),
            "window_days": window_days,
            "message_count": len(ordered_messages),
            "last_message_at": last_message_at.isoformat() if last_message_at else None,
            "source_metadata": source_metadata,
        }
    )
    return {
        "status": status,
        "group_kind": group_kind,
        "message_count": len(ordered_messages),
        "participant_count": len(participants),
        "last_message_at": last_message_at,
        "summary": summary,
        "open_loop_summary": _compact_memory_text(open_loop_summary, limit=1000),
        "risk_summary": _compact_memory_text(risk_summary, limit=1000),
        "decision_summary": _compact_memory_text(decision_summary, limit=1000),
        "cleaning_maintenance_summary": _compact_memory_text(cleaning_maintenance_summary, limit=1000),
        "source_metadata": source_metadata,
        "memory_hash": memory_hash,
    }


def _thread_group_kind(thread: Any, messages: list[Any]) -> str:
    for message in reversed(messages or []):
        raw_payload = getattr(message, "raw_payload", None)
        if isinstance(raw_payload, dict) and raw_payload.get("group_kind"):
            return raw_payload["group_kind"]
    return "team"


def whatsapp_memory_attention_score(
    *,
    recent_unresolved_topics: list[dict[str, Any]],
    recent_risk_updates: list[dict[str, Any]],
    message_count: int,
    last_message_at: datetime | None,
    window_end: datetime,
) -> float:
    """Score whether a WhatsApp memory deserves Today-page attention."""
    score = 0.0
    severity_points = {"critical": 60.0, "high": 50.0, "medium": 34.0, "low": 18.0}
    for topic in recent_unresolved_topics[:6]:
        score += severity_points.get(topic.get("severity"), 24.0)
    if recent_risk_updates:
        score += min(len(recent_risk_updates), 4) * 8.0
    if recent_unresolved_topics or recent_risk_updates:
        score += min(max(int(message_count or 0), 0) / 80.0, 18.0)
        if last_message_at:
            age_hours = max((window_end - last_message_at).total_seconds() / 3600.0, 0.0)
            score += max(0.0, 18.0 - min(age_hours, 72.0) / 4.0)
    return round(score, 2)


def _source_datetime_is_at_or_after(value: str | datetime | None, cutoff: datetime) -> bool:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = _parse_source_datetime(value)
    return bool(parsed and parsed >= cutoff)


def _source_datetime_is_after(value: str | datetime | None, cutoff: datetime) -> bool:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = _parse_source_datetime(value)
    return bool(parsed and parsed > cutoff)


def _whatsapp_memory_sender(message: Any) -> str:
    return (getattr(message, "sender_name", None) or getattr(message, "from_number", None) or "Unknown").strip() or "Unknown"


def _whatsapp_memory_update(message: Any, *, sender: str) -> dict[str, Any] | None:
    body = _truncate_chat_text(getattr(message, "body", None), limit=220)
    if not body:
        return None
    received_at = getattr(message, "received_at", None)
    return {
        "message_id": getattr(message, "whatsapp_message_id", None),
        "sender": sender,
        "received_at": received_at.isoformat() if received_at else None,
        "excerpt": body,
    }


def _whatsapp_group_activity_sentence(group_name: str, messages: list[Any], participants: Counter[str], window_days: int) -> str:
    top_participants = ", ".join(name for name, _count in participants.most_common(4))
    participant_text = f" from {len(participants)} participants" if participants else ""
    top_text = f"; most active: {top_participants}" if top_participants else ""
    return f"{group_name}: {len(messages)} messages in the last {window_days} days{participant_text}{top_text}."


def _whatsapp_latest_updates_sentence(updates: list[dict[str, Any]]) -> str:
    return _whatsapp_memory_update_sentence("Latest meaningful updates", updates[-4:], fallback="")


def _whatsapp_unresolved_topics_sentence(unresolved_topics: list[dict[str, Any]]) -> str:
    if not unresolved_topics:
        return "No likely unresolved WhatsApp loop remained after the latest closure signals."
    parts = []
    for topic in unresolved_topics[:5]:
        parts.append(
            f"{topic.get('topic_label')} by {topic.get('sender')}: {topic.get('excerpt')}"
        )
    return "Likely open topics: " + " | ".join(parts)


def _whatsapp_recent_topics_sentence(unresolved_topics: list[dict[str, Any]]) -> str:
    if not unresolved_topics:
        return ""
    parts = []
    for topic in unresolved_topics[:4]:
        parts.append(
            f"{topic.get('topic_label')} by {topic.get('sender')}: {topic.get('excerpt')}"
        )
    return "Recent likely open topics: " + " | ".join(parts)


def compact_whatsapp_focus_for_operator(text: str | None, *, topic_limit: int = 2, char_limit: int = 360) -> str:
    """Keep the Today/Ask WhatsApp missed-context card focused on the top actionable items."""
    cleaned = " ".join(clean_team_reference_text(text).split())
    if not cleaned:
        return ""
    match = re.match(r"^(?:Recent\s+)?Likely open topics:\s*(.+)$", cleaned, re.IGNORECASE)
    if not match:
        return _compact_memory_text(cleaned, limit=char_limit)
    topics = []
    seen = set()
    for raw_part in match.group(1).split("|"):
        part = _operating_answer_sentence(raw_part.strip())
        if not part:
            continue
        normalized = _normalize_ask_question(part)
        if normalized in seen:
            continue
        seen.add(normalized)
        topics.append(part)
        if len(topics) >= max(1, topic_limit):
            break
    if not topics:
        return _compact_memory_text(cleaned, limit=char_limit)
    label = "Top missed item" if len(topics) == 1 else "Top missed items"
    joined = "; ".join(_whatsapp_focus_list_item(topic) for topic in topics)
    return _compact_memory_text(f"{label}: {joined}.", limit=char_limit)


def _whatsapp_focus_list_item(topic: str) -> str:
    compact = _compact_memory_text(topic, limit=180)
    if compact.endswith("..."):
        return compact
    if compact.endswith("."):
        return compact[:-1]
    return compact


def _whatsapp_memory_update_sentence(label: str, updates: list[dict[str, Any]], *, fallback: str) -> str:
    if not updates:
        return fallback
    parts = [f"{item.get('sender')}: {item.get('excerpt')}" for item in updates[-4:]]
    return f"{label}: " + " | ".join(parts)


def _compact_memory_text(text: str | None, *, limit: int) -> str:
    compacted = " ".join((text or "").split())
    if len(compacted) <= limit:
        return compacted
    cutoff = max(limit - 3, 0)
    candidate = compacted[:cutoff].rstrip()
    word_boundary = candidate.rfind(" ")
    if word_boundary >= max(24, int(cutoff * 0.65)):
        candidate = candidate[:word_boundary]
    candidate = candidate.rstrip(" ,;:")
    candidate = re.sub(
        r"\s+\b(?:a|an|and|are|at|by|for|from|has|have|in|is|of|on|or|should|that|the|their|this|to|was|were|with)\b$",
        "",
        candidate,
        flags=re.IGNORECASE,
    ).rstrip(" ,;:")
    return f"{candidate}..."


def _whatsapp_group_memory_excerpt(memory: Any) -> str:
    parts = [
        getattr(memory, "summary", None),
        getattr(memory, "open_loop_summary", None),
        getattr(memory, "risk_summary", None),
        getattr(memory, "decision_summary", None),
        getattr(memory, "cleaning_maintenance_summary", None),
    ]
    return _compact_memory_text(" ".join(part for part in parts if part), limit=1800)


def whatsapp_group_memory_message_refs(metadata: dict[str, Any] | None, *, limit: int = 5) -> list[dict[str, Any]]:
    """Return message references behind the actionable parts of a WhatsApp group memory."""
    metadata = metadata or {}
    if limit <= 0:
        return []
    buckets = (
        "recent_unresolved_topics",
        "recent_risk_updates",
        "recent_cleaning_maintenance_updates",
        "recent_decision_updates",
        "latest_updates",
    )
    refs: list[dict[str, Any]] = []
    seen = set()
    for bucket in buckets:
        for item in metadata.get(bucket) or []:
            if not isinstance(item, dict):
                continue
            key = item.get("message_id") or (item.get("received_at"), item.get("excerpt"))
            if not key or key in seen:
                continue
            seen.add(key)
            refs.append(
                {
                    "message_id": item.get("message_id"),
                    "received_at": item.get("received_at"),
                    "excerpt": item.get("excerpt"),
                    "bucket": bucket,
                }
            )
            if len(refs) >= max(0, limit):
                return refs
    return refs


def whatsapp_group_memory_highlight(memory: Any, *, portfolio_name: str | None = None) -> dict[str, Any]:
    """Convert a rolling WhatsApp memory row into a compact Today card."""
    metadata = _object_value(memory, "source_metadata") or {}
    attention_score = float(metadata.get("attention_score") or 0.0) if isinstance(metadata, dict) else 0.0
    status = "watch" if attention_score > 0 else "ok"
    focus = _first_non_empty(
        _whatsapp_recent_topics_sentence((metadata or {}).get("recent_unresolved_topics") or []) if status == "watch" else None,
        _whatsapp_memory_update_sentence("Recent risk language", (metadata or {}).get("recent_risk_updates") or [], fallback=""),
        _whatsapp_memory_update_sentence("Recent cleaning/maintenance context", (metadata or {}).get("recent_cleaning_maintenance_updates") or [], fallback=""),
        _whatsapp_memory_update_sentence("Recent decision/approval mentions", (metadata or {}).get("recent_decision_updates") or [], fallback=""),
        _object_value(memory, "summary"),
    )
    group_name = _object_value(memory, "group_name") or "team WhatsApp"
    action = (
        f"Review {group_name} in WhatsApp and confirm the latest likely open item is closed."
        if status == "watch"
        else f"Skim {group_name} only if you need detail; Brain did not find a likely open loop."
    )
    memory_date = _object_value(memory, "memory_date")
    last_message_at = _object_value(memory, "last_message_at")
    return {
        "whatsapp_group_memory_id": _object_value(memory, "whatsapp_group_memory_id"),
        "portfolio_id": _object_value(memory, "portfolio_id"),
        "portfolio_name": portfolio_name,
        "group_name": group_name,
        "group_kind": _object_value(memory, "group_kind") or "team",
        "status": status,
        "message_count": int(_object_value(memory, "message_count") or 0),
        "participant_count": int(_object_value(memory, "participant_count") or 0),
        "memory_date": memory_date.isoformat() if hasattr(memory_date, "isoformat") else memory_date,
        "last_message_at": last_message_at.isoformat() if hasattr(last_message_at, "isoformat") else last_message_at,
        "attention_score": attention_score,
        "focus": compact_whatsapp_focus_for_operator(focus),
        "summary": _compact_memory_text(_object_value(memory, "summary"), limit=520),
        "suggested_action": action,
    }


def whatsapp_memory_highlight_sort_key(highlight: dict[str, Any]) -> tuple[Any, ...]:
    status_order = {"watch": 0, "degraded": 1, "ok": 2}
    last_message = _parse_source_datetime(highlight.get("last_message_at"))
    return (
        status_order.get(highlight.get("status"), 3),
        -float(highlight.get("attention_score") or 0.0),
        _signal_last_seen_sort_value(last_message),
        highlight.get("group_name") or "",
    )


def whatsapp_memory_highlight_brief_item(highlight: dict[str, Any]) -> dict[str, Any]:
    """Convert a Today WhatsApp memory highlight into a daily-brief item."""
    score = float(highlight.get("attention_score") or 0.0)
    severity = "high" if score >= 160 else "medium"
    group_name = highlight.get("group_name") or "Team WhatsApp"
    return {
        "kind": "whatsapp_memory",
        "portfolio_id": highlight.get("portfolio_id"),
        "portfolio_name": highlight.get("portfolio_name"),
        "group_name": group_name,
        "status": highlight.get("status") or "watch",
        "severity": severity,
        "rank_score": score,
        "title": f"{group_name}: recent WhatsApp thread to review",
        "summary": highlight.get("focus") or highlight.get("summary") or "",
        "action": highlight.get("suggested_action") or f"Review {group_name} in WhatsApp.",
        "suggested_next_step": highlight.get("suggested_action") or f"Review {group_name} in WhatsApp.",
        "last_seen_at": highlight.get("last_message_at"),
    }


def _first_non_empty(*values: Any) -> str:
    for value in values:
        text = " ".join(str(value or "").split())
        if text:
            return text
    return ""


def _horizon_payload(snapshot: BookingHealthSnapshot) -> dict[str, Any]:
    raw_metrics = snapshot.raw_metrics or {}
    return {
        "horizon_days": snapshot.horizon_days,
        "occupancy_rate": snapshot.occupancy_rate,
        "occupancy_pct": round(snapshot.occupancy_rate * 100) if snapshot.occupancy_rate is not None else None,
        "booked_nights": snapshot.booked_nights,
        "available_nights": snapshot.available_nights,
        "blocked_nights": raw_metrics.get("blocked_nights"),
        "sellable_nights": raw_metrics.get("sellable_nights"),
        "calendar_coverage_days": raw_metrics.get("calendar_coverage_days"),
        "occupancy_denominator": raw_metrics.get("occupancy_denominator"),
        "occupancy_source": raw_metrics.get("occupancy_source"),
        "occupancy_rate_measurable": raw_metrics.get("occupancy_rate_measurable", True),
        "expected_occupancy_rate": snapshot.expected_occupancy_rate,
        "expected_pct": round((snapshot.expected_occupancy_rate or 0.0) * 100),
        "diagnosis": snapshot.diagnosis,
        "confidence": snapshot.confidence,
        "recommended_action": snapshot.recommended_action,
    }


def booking_health_signal_fields(analysis: Any) -> dict[str, Any] | None:
    """Build one signal payload from a daily per-property booking analysis."""
    horizons = list(getattr(analysis, "horizons", None) or [])
    source_severity = _normalize_booking_severity(
        getattr(analysis, "severity", None),
        _severity_from_horizons(horizons),
    )
    if source_severity in {"healthy", "watch"}:
        return None
    has_attention_horizon = any((item.get("diagnosis") or "healthy") != "healthy" for item in horizons)
    if not has_attention_horizon:
        return None

    listing_name = getattr(analysis, "listing_name", None) or UNKNOWN_LISTING_NAME
    signal_severity = _booking_signal_severity(source_severity)
    horizon_summary = _booking_horizon_signal_summary(horizons)
    booking_pattern = (getattr(analysis, "booking_pattern", None) or horizon_summary).strip()
    opinion_parts = [
        item
        for item in (
            getattr(analysis, "opinion", None),
            getattr(analysis, "pricelabs_opinion", None),
            getattr(analysis, "airbnb_page_opinion", None),
        )
        if item
    ]
    action_items = [
        str(item).strip()
        for item in (getattr(analysis, "action_items", None) or [])
        if str(item or "").strip()
    ]
    suggested_action = " ".join(action_items[:2]) or "Review booking pace, PriceLabs rules, restrictions, and listing freshness."
    title_prefix = "Critical booking health gap" if signal_severity == "critical" else "Booking health needs action"
    if signal_severity == "medium":
        title_prefix = "Booking pace needs watch"
    return {
        "title": f"{title_prefix} for {listing_name}",
        "severity": signal_severity,
        "confidence": max(0.0, min(float(getattr(analysis, "confidence", None) or 0.45), 1.0)),
        "summary": booking_pattern or f"{listing_name}: booking pace needs review.",
        "why_it_matters": (
            " ".join(opinion_parts[:2])
            or "Forward booking weakness can turn into lost revenue if pricing, restrictions, or listing quality are not corrected early."
        ),
        "suggested_action": suggested_action,
    }


def booking_health_rank_score(analysis: Any) -> float:
    """Rank booking-health cards by gap severity, pickup weakness, and evidence confidence."""
    horizons = list(getattr(analysis, "horizons", None) or [])
    source_severity = _normalize_booking_severity(
        getattr(analysis, "severity", None),
        _severity_from_horizons(horizons),
    )
    signal_severity = _booking_signal_severity(source_severity)
    base = {
        "critical": 93.0,
        "high": 80.0,
        "medium": 64.0,
    }.get(signal_severity, 55.0)
    gap_points = _booking_gap_rank_points(horizons)
    pickup_points = _booking_pickup_rank_points(getattr(analysis, "raw_payload", None))
    confidence_points = max(0.0, min(float(getattr(analysis, "confidence", None) or 0.0), 1.0)) * 4.0
    return _bounded_rank_score(base + gap_points + pickup_points + confidence_points, maximum=99.0)


def _booking_gap_rank_points(horizons: list[dict[str, Any]]) -> float:
    weights = {7: 0.45, 30: 0.35, 60: 0.20}
    score = 0.0
    for item in horizons:
        try:
            horizon_days = int(item.get("horizon_days") or 0)
        except (TypeError, ValueError):
            horizon_days = 0
        expected = item.get("expected_occupancy_rate")
        if expected is None:
            expected = 0.45 if horizon_days <= 7 else 0.55 if horizon_days <= 30 else 0.50
        occupancy = item.get("occupancy_rate")
        if occupancy is None:
            occupancy_pct = item.get("occupancy_pct")
            if occupancy_pct is None:
                continue
            occupancy = float(occupancy_pct) / 100.0
        gap = max(float(expected or 0.0) - float(occupancy or 0.0), 0.0)
        score += weights.get(horizon_days, 0.1) * min(gap / 0.55, 1.0)
    return score * 10.0


def _booking_pickup_rank_points(raw_payload: Any) -> float:
    if not isinstance(raw_payload, dict):
        return 0.0
    pickup = ((raw_payload.get("analysis_context") or {}).get("booking_pattern_inputs") or {})
    booked_last_14 = int(pickup.get("booked_last_14_days") or 0)
    booked_last_7 = int(pickup.get("booked_last_7_days") or 0)
    if booked_last_14 == 0:
        return 3.0
    if booked_last_7 == 0:
        return 1.0
    return 0.0


def _bounded_rank_score(value: Any, *, maximum: float = 100.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = 0.0
    return round(min(max(parsed, 0.0), maximum), 2)


def _booking_signal_severity(value: str | None) -> str:
    severity = _normalize_booking_severity(value)
    if severity == "critical":
        return "critical"
    if severity == "high":
        return "high"
    return "medium"


def _booking_horizon_signal_summary(horizons: list[dict[str, Any]]) -> str:
    by_horizon = {int(item["horizon_days"]): item for item in horizons if item.get("horizon_days")}
    parts = []
    for days in BOOKING_HEALTH_HORIZONS:
        item = by_horizon.get(days)
        if not item:
            continue
        if item.get("diagnosis") == "inventory_blocked":
            parts.append(f"{days}d inventory blocked")
        elif item.get("occupancy_pct") is not None:
            parts.append(f"{days}d {int(item['occupancy_pct'])}% booked")
    if not parts:
        return ""
    return "Booking pace by window: " + " / ".join(parts) + "."


def _severity_from_horizons(horizons: list[dict[str, Any]]) -> str:
    by_horizon = {int(item["horizon_days"]): item for item in horizons if item.get("horizon_days")}
    weak = [item for item in horizons if item.get("diagnosis") == "weak_booking_pace"]
    last_minute = [item for item in horizons if item.get("diagnosis") == "last_minute_gap_risk"]
    blocked = [item for item in horizons if item.get("diagnosis") == "inventory_blocked"]
    rate_7 = by_horizon.get(7, {}).get("occupancy_rate")
    rate_30 = by_horizon.get(30, {}).get("occupancy_rate")
    if rate_7 is not None and rate_30 is not None and rate_7 < 0.2 and rate_30 < 0.35:
        return "critical"
    if len(weak) >= 2 or last_minute:
        return "high"
    if weak or any(item.get("diagnosis") == "watch_booking_pace" for item in horizons):
        return "watch"
    if blocked:
        return "watch"
    return "healthy"


def _severity_sort_value(severity: str) -> int:
    return {"critical": 0, "high": 1, "watch": 2, "healthy": 3}.get(severity, 4)


def portfolio_operating_status(*, active_signals: list[Any], open_loop_count: int = 0, booking_health: Any | None = None) -> str:
    """Return the portfolio badge shown to operators."""
    severities = {getattr(signal, "severity", None) for signal in active_signals or []}
    if "critical" in severities:
        return "critical"
    booking_status = booking_health_status(booking_health)
    if "high" in severities or booking_status in {"critical", "high"}:
        return "high"
    if open_loop_count or active_signals or booking_status == "watch":
        return "watch"
    return "healthy"


def booking_health_status(booking_health: Any | None) -> str:
    if booking_health is None:
        return "healthy"
    severity = getattr(booking_health, "severity", None)
    if severity:
        return _normalize_booking_severity(severity, "watch")
    diagnosis = getattr(booking_health, "diagnosis", None)
    if diagnosis in {"weak_booking_pace", "last_minute_gap_risk"}:
        return "high"
    if diagnosis and diagnosis != "healthy":
        return "watch"
    return "healthy"


def booking_health_label(booking_health: Any | None) -> str | None:
    if booking_health is None:
        return None
    severity = getattr(booking_health, "severity", None)
    if severity:
        return _normalize_booking_severity(severity, "watch")
    diagnosis = getattr(booking_health, "diagnosis", None)
    return str(diagnosis or "watch").replace("_", " ")


def _booking_analysis_sort_key(analysis: Any) -> tuple[int, float, str]:
    severity = _normalize_booking_severity(getattr(analysis, "severity", None), "watch")
    listing_name = getattr(analysis, "listing_name", None) or ""
    return (_severity_sort_value(severity), -booking_health_rank_score(analysis), listing_name)


def _normalize_booking_severity(value: str | None, fallback: str = "watch") -> str:
    normalized = (value or fallback or "watch").strip().lower().replace("medium", "watch")
    return normalized if normalized in {"critical", "high", "watch", "healthy"} else fallback


def _build_deterministic_booking_analysis(
    *,
    listing_name: str,
    horizons: list[dict[str, Any]],
    pickup_context: dict[str, Any],
    pricelabs_context: dict[str, Any],
    airbnb_context: dict[str, Any],
) -> dict[str, Any]:
    severity = _severity_from_horizons(horizons)
    booking_pattern = _deterministic_booking_pattern(horizons, pickup_context)
    pricelabs_opinion = _deterministic_pricelabs_opinion(pricelabs_context)
    airbnb_page_opinion = _deterministic_airbnb_opinion(airbnb_context)
    actions = _deterministic_booking_actions(horizons, pickup_context, pricelabs_context, airbnb_context)
    confidence = _deterministic_booking_confidence(horizons, pricelabs_context, airbnb_context)
    opinion = f"{listing_name}: {booking_pattern} {pricelabs_opinion} {airbnb_page_opinion}".strip()
    return {
        "severity": severity,
        "confidence": confidence,
        "booking_pattern": booking_pattern,
        "pricelabs_opinion": pricelabs_opinion,
        "airbnb_page_opinion": airbnb_page_opinion,
        "opinion": opinion,
        "action_items": actions,
    }


def _deterministic_booking_pattern(horizons: list[dict[str, Any]], pickup_context: dict[str, Any]) -> str:
    by_horizon = {int(item["horizon_days"]): item for item in horizons if item.get("horizon_days")}
    pct = {horizon: by_horizon.get(horizon, {}).get("occupancy_pct") for horizon in BOOKING_HEALTH_HORIZONS}
    recent_7 = int((pickup_context or {}).get("booked_last_7_days") or 0)
    recent_14 = int((pickup_context or {}).get("booked_last_14_days") or 0)
    blocked_horizons = [
        int(item["horizon_days"])
        for item in horizons
        if item.get("diagnosis") == "inventory_blocked" and item.get("horizon_days")
    ]
    comparable_pct = [value for value in pct.values() if value is not None]
    if blocked_horizons and not comparable_pct:
        base = "Demand cannot be evaluated because all forward inventory in the measured windows is blocked."
    elif comparable_pct and all(value < 35 for value in comparable_pct):
        base = f"Soft demand across all windows: {pct.get(7)}% / {pct.get(30)}% / {pct.get(60)}% occupancy for 7/30/60 days."
    elif pct.get(7) is not None and pct.get(30) is not None and pct[7] < 25 and pct[30] >= 40:
        base = f"Near-term gap risk: 7-day occupancy is {pct.get(7)}%, while 30/60-day pace is less concerning."
    elif (pct.get(30) is not None and pct[30] < 40) or (pct.get(60) is not None and pct[60] < 40):
        base = f"Forward pickup is thin: {pct.get(30)}% in 30 days and {pct.get(60)}% in 60 days."
    else:
        base = f"Booking pace is acceptable: {pct.get(7)}% / {pct.get(30)}% / {pct.get(60)}% occupancy for 7/30/60 days."
    if recent_14 == 0:
        return f"{base} No future-stay reservations were created in the last 14 days."
    return f"{base} Recent pickup: {recent_7} future-stay bookings in 7 days, {recent_14} in 14 days."


def _deterministic_pricelabs_opinion(context: dict[str, Any]) -> str:
    status = context.get("status")
    if status == "ok":
        price_text = context.get("price_summary") or "PriceLabs returned pricing data."
        min_stay = context.get("min_stay_summary")
        if min_stay:
            return f"PriceLabs context: {price_text} {min_stay}"
        return f"PriceLabs context: {price_text}"
    proxy = context.get("calendar_proxy_summary")
    if proxy:
        return f"PriceLabs is {status or 'unavailable'}; using Hostaway calendar proxy instead: {proxy}"
    return f"PriceLabs is {status or 'unavailable'}, so pricing/rule confidence is lower."


def _deterministic_airbnb_opinion(context: dict[str, Any]) -> str:
    status = context.get("status")
    if status == "ok":
        bits = [context.get("title"), context.get("meta_description"), context.get("visible_signals")]
        summary = " ".join(str(bit).strip() for bit in bits if bit)
        return f"Airbnb page snapshot: {summary[:420] or 'page fetched, but little public detail was extractable.'}"
    if status == "missing_url":
        return "Airbnb page analysis is missing because no Airbnb URL is stored for this listing."
    return f"Airbnb page analysis is {status or 'unavailable'} today."


def _deterministic_booking_actions(
    horizons: list[dict[str, Any]],
    pickup_context: dict[str, Any],
    pricelabs_context: dict[str, Any],
    airbnb_context: dict[str, Any],
) -> list[str]:
    by_horizon = {int(item["horizon_days"]): item for item in horizons if item.get("horizon_days")}
    actions: list[str] = []
    if any(item.get("diagnosis") == "inventory_blocked" for item in horizons):
        actions.append("Confirm that forward calendar blocks are intentional before evaluating demand or changing price.")
    rate_7 = by_horizon.get(7, {}).get("occupancy_rate")
    rate_30 = by_horizon.get(30, {}).get("occupancy_rate")
    rate_60 = by_horizon.get(60, {}).get("occupancy_rate")
    if rate_7 is not None and rate_7 < 0.35:
        actions.append("For next-7-day gaps, check same-week price competitiveness and remove overly restrictive minimum stays.")
    if rate_30 is not None and rate_30 < 0.45:
        actions.append("For the next 30 days, test a targeted discount or rule relaxation on open weekday gaps.")
    if rate_60 is not None and rate_60 < 0.45:
        actions.append("Review forward base price and seasonal assumptions before the 60-day gap becomes urgent.")
    if (pickup_context or {}).get("booked_last_14_days", 0) == 0:
        actions.append("Investigate visibility and conversion because there was no future-stay pickup in the last 14 days.")
    if pricelabs_context.get("status") != "ok":
        actions.append("Connect or repair PriceLabs data so Brain can compare rules and price movement directly.")
    elif pricelabs_context.get("min_stay_risk"):
        actions.append("Review PriceLabs minimum-stay rules against current short gaps.")
    if airbnb_context.get("status") != "ok":
        actions.append("Store a valid Airbnb URL so Brain can inspect public merchandising daily.")
    return actions[:6] or ["No urgent revenue action; keep monitoring pickup and channel visibility."]


def _deterministic_booking_confidence(
    horizons: list[dict[str, Any]],
    pricelabs_context: dict[str, Any],
    airbnb_context: dict[str, Any],
) -> float:
    base = max((float(item.get("confidence") or 0.0) for item in horizons), default=0.45)
    if pricelabs_context.get("status") == "ok":
        base += 0.08
    if airbnb_context.get("status") == "ok":
        base += 0.05
    if pricelabs_context.get("status") != "ok" and airbnb_context.get("status") != "ok":
        base -= 0.08
    return round(min(max(base, 0.25), 0.92), 2)


def _calendar_context(rows: list[CalendarSnapshot]) -> dict[str, Any]:
    if not rows:
        return {"status": "missing", "summary": "No Hostaway calendar rows are stored for the current snapshot."}
    prices = [float(row.price) for row in rows if row.price is not None and row.price > 0]
    min_stays = [int(row.minimum_stay) for row in rows if row.minimum_stay]
    unavailable = [row for row in rows if row.is_available is False or (row.status or "").lower() in {"reserved", "blocked"}]
    summary_parts = [f"{len(rows)} calendar days"]
    if prices:
        summary_parts.append(f"avg price ${statistics.mean(prices):.0f}")
        summary_parts.append(f"price range ${min(prices):.0f}-${max(prices):.0f}")
    if min_stays:
        summary_parts.append(f"min-stay range {min(min_stays)}-{max(min_stays)} nights")
    summary_parts.append(f"{len(unavailable)} unavailable/reserved days")
    return {
        "status": "ok",
        "day_count": len(rows),
        "avg_price": round(statistics.mean(prices), 2) if prices else None,
        "min_price": min(prices) if prices else None,
        "max_price": max(prices) if prices else None,
        "min_stay_min": min(min_stays) if min_stays else None,
        "min_stay_max": max(min_stays) if min_stays else None,
        "unavailable_days": len(unavailable),
        "summary": "; ".join(summary_parts),
    }


def _pricelabs_context(snapshot: PriceLabsSnapshot | None, calendar_context: dict[str, Any]) -> dict[str, Any]:
    proxy_summary = calendar_context.get("summary")
    if not snapshot:
        return {
            "status": "missing",
            "confidence": 0.0,
            "calendar_proxy_summary": proxy_summary,
            "summary": "No PriceLabs snapshot is stored for this listing today.",
        }
    payload = snapshot.raw_payload
    numeric = _collect_numeric_fields(payload)
    prices = [value for key, value in numeric if "price" in key or "rate" in key]
    min_stays = [int(value) for key, value in numeric if "min" in key and "stay" in key and value > 0]
    result = {
        "status": snapshot.status,
        "confidence": snapshot.confidence,
        "error": snapshot.error_message,
        "calendar_proxy_summary": proxy_summary,
        "price_summary": None,
        "min_stay_summary": None,
        "min_stay_risk": False,
    }
    if prices:
        result["price_summary"] = f"visible price/rate values average ${statistics.mean(prices):.0f}, range ${min(prices):.0f}-${max(prices):.0f}."
    if min_stays:
        result["min_stay_summary"] = f"Visible minimum-stay values range {min(min_stays)}-{max(min_stays)} nights."
        result["min_stay_risk"] = max(min_stays) >= 4
    if snapshot.status != "ok" and snapshot.error_message:
        result["summary"] = f"PriceLabs status {snapshot.status}: {snapshot.error_message}"
    elif snapshot.status == "ok":
        result["summary"] = "PriceLabs returned a usable snapshot."
    else:
        result["summary"] = f"PriceLabs status is {snapshot.status}."
    return result


def _collect_numeric_fields(payload: Any, prefix: str = "") -> list[tuple[str, float]]:
    values: list[tuple[str, float]] = []
    if isinstance(payload, dict):
        for key, value in payload.items():
            path = f"{prefix}.{key}".strip(".").lower()
            values.extend(_collect_numeric_fields(value, path))
    elif isinstance(payload, list):
        for item in payload[:120]:
            values.extend(_collect_numeric_fields(item, prefix))
    else:
        try:
            if isinstance(payload, bool) or payload is None:
                return values
            parsed = float(payload)
            if 0 < parsed < 100000:
                values.append((prefix, parsed))
        except (TypeError, ValueError):
            pass
    return values


def _extract_airbnb_url(value: Any) -> str | None:
    if not value:
        return None
    if isinstance(value, dict):
        for key, nested in value.items():
            if str(key).lower() in {"airbnblistingurl", "airbnb_url", "airbnburl", "airbnb"}:
                found = _extract_airbnb_url(nested)
                if found:
                    return found
        for nested in value.values():
            found = _extract_airbnb_url(nested)
            if found:
                return found
        return None
    if isinstance(value, list):
        for nested in value:
            found = _extract_airbnb_url(nested)
            if found:
                return found
        return None
    text = str(value)
    try:
        parsed = json.loads(text)
        if parsed != value:
            found = _extract_airbnb_url(parsed)
            if found:
                return found
    except (TypeError, ValueError, json.JSONDecodeError):
        pass
    match = AIRBNB_URL_PATTERN.search(text)
    return match.group(0).rstrip(").,]") if match else None


def _airbnb_page_context(url: str, html_text: str, status_code: int) -> dict[str, Any]:
    today_iso = date.today().isoformat()
    title = _first_html_match(html_text, r"<title[^>]*>(.*?)</title>")
    description = _first_html_match(html_text, r'<meta[^>]+name=["\']description["\'][^>]+content=["\'](.*?)["\']')
    if not description:
        description = _first_html_match(html_text, r'<meta[^>]+property=["\']og:description["\'][^>]+content=["\'](.*?)["\']')
    visible = _compact_text(html_text)
    if "404 page not found" in (title or "").lower() or "page not found" in visible.lower()[:500]:
        return {
            "status": "not_found",
            "snapshot_date": today_iso,
            "url": url,
            "http_status": status_code,
            "title": title,
            "meta_description": description,
            "visible_signals": "",
            "summary": "Airbnb page returned a page-not-found response.",
        }
    signals = []
    for token in ("Superhost", "Guest favorite", "Rare find", "reviews", "rating", "new"):
        if token.lower() in visible.lower():
            signals.append(token)
    return {
        "status": "ok",
        "snapshot_date": today_iso,
        "url": url,
        "http_status": status_code,
        "title": title,
        "meta_description": description,
        "visible_signals": ", ".join(dict.fromkeys(signals)),
        "summary": (description or title or visible[:280] or "Airbnb page fetched."),
    }


def _first_html_match(text: str, pattern: str) -> str | None:
    match = re.search(pattern, text or "", flags=re.IGNORECASE | re.DOTALL)
    return _clean_html_text(match.group(1)) if match else None


def _compact_text(text: str) -> str:
    return _clean_html_text(HTML_TAG_PATTERN.sub(" ", text or ""))[:2000]


def _clean_html_text(text: str | None) -> str:
    cleaned = html.unescape(text or "")
    return " ".join(cleaned.split())


def system_user():
    """Small stand-in for system/admin actions inside jobs."""

    class SystemUser:
        user_id = None
        role = "admin"
        email = "brain-system@local"

        def is_admin(self):
            return True

    return SystemUser()


def _parse_date(value) -> date | None:
    if isinstance(value, date):
        return value
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date()
    except ValueError:
        try:
            return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
        except ValueError:
            return None


def _coerce_bool(value) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() in ("true", "1", "yes", "available")
    return bool(value)


def _coerce_float(value) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _coerce_int(value) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None
