#!/usr/bin/env python3
"""Cached, evidence-backed classification of completed guest stays."""

from __future__ import annotations

import logging
from calendar import monthrange
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Iterable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import text

import dashboard.config as config
from brain.ai import STAY_OUTCOME_PROMPT_VERSION, BrainAIClient
from brain.models import StayOutcomeClassification, as_json_safe, get_session, stable_hash
from brain.scoring import is_confirmed_reservation_status
from database.models import (
    Conversation,
    Listing,
    MessageMetadata,
    Reservation,
    get_session as get_main_session,
)

logger = logging.getLogger(__name__)

OUTCOMES = {"smooth", "recovered", "unresolved", "needs_review"}
MATERIAL_SEVERITIES = {"material", "critical"}
MINIMUM_CLASSIFICATION_CONFIDENCE = 0.75
DEFAULT_CHECKOUT_HOUR = 11
CLASSIFICATION_DELAY = timedelta(hours=24)
STAY_OUTCOME_REFRESH_LOCK_ID = 780_411_928

# Hostaway's listing detail currently supplies checkout hour but leaves
# timezoneName blank for this portfolio. These location fallbacks keep the
# finalization timestamp local to the property rather than incorrectly using
# UTC. Explicit Hostaway timezoneName always takes priority.
US_STATE_TIMEZONES = {
    "AK": "America/Anchorage",
    "AL": "America/Chicago",
    "AR": "America/Chicago",
    "AZ": "America/Phoenix",
    "CA": "America/Los_Angeles",
    "CO": "America/Denver",
    "CT": "America/New_York",
    "DC": "America/New_York",
    "DE": "America/New_York",
    "FL": "America/New_York",
    "GA": "America/New_York",
    "HI": "Pacific/Honolulu",
    "IA": "America/Chicago",
    "ID": "America/Denver",
    "IL": "America/Chicago",
    "IN": "America/Indiana/Indianapolis",
    "KS": "America/Chicago",
    "KY": "America/New_York",
    "LA": "America/Chicago",
    "MA": "America/New_York",
    "MD": "America/New_York",
    "ME": "America/New_York",
    "MI": "America/Detroit",
    "MN": "America/Chicago",
    "MO": "America/Chicago",
    "MS": "America/Chicago",
    "MT": "America/Denver",
    "NC": "America/New_York",
    "ND": "America/Chicago",
    "NE": "America/Chicago",
    "NH": "America/New_York",
    "NJ": "America/New_York",
    "NM": "America/Denver",
    "NV": "America/Los_Angeles",
    "NY": "America/New_York",
    "OH": "America/New_York",
    "OK": "America/Chicago",
    "OR": "America/Los_Angeles",
    "PA": "America/New_York",
    "RI": "America/New_York",
    "SC": "America/New_York",
    "SD": "America/Chicago",
    "TN": "America/Chicago",
    "TX": "America/Chicago",
    "UT": "America/Denver",
    "VA": "America/New_York",
    "VT": "America/New_York",
    "WA": "America/Los_Angeles",
    "WI": "America/Chicago",
    "WV": "America/New_York",
    "WY": "America/Denver",
}
US_CITY_TIMEZONE_OVERRIDES = {
    ("sevierville", "TN"): "America/New_York",
    ("el paso", "TX"): "America/Denver",
}


def normalize_utc_datetime(value: datetime) -> datetime:
    """Return a naive UTC datetime, matching timestamps stored by the app."""
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def stay_outcome_activation_time(
    value: str | datetime | None = None,
    *,
    reference_time: datetime | None = None,
) -> datetime:
    """Resolve the fixed production activation timestamp in UTC."""
    configured = value if value is not None else config.KPI_STAY_OUTCOME_ACTIVATED_AT
    if isinstance(configured, datetime):
        return normalize_utc_datetime(configured)
    if configured:
        try:
            parsed = datetime.fromisoformat(str(configured).strip().replace("Z", "+00:00"))
            return normalize_utc_datetime(parsed)
        except ValueError:
            logger.warning(
                "Invalid KPI_STAY_OUTCOME_ACTIVATED_AT=%r; using this run as the activation anchor",
                configured,
            )
    return normalize_utc_datetime(reference_time or datetime.utcnow())


def one_calendar_month_before(value: datetime) -> datetime:
    """Return the same wall-clock time one calendar month earlier."""
    if value.month == 1:
        year, month = value.year - 1, 12
    else:
        year, month = value.year, value.month - 1
    day = min(value.day, monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)


def initial_stay_outcome_checkout_cutoff(
    activation_time: str | datetime | None = None,
    *,
    reference_time: datetime | None = None,
) -> datetime:
    """Oldest checkout timestamp allowed into the one-time production backfill."""
    return one_calendar_month_before(
        stay_outcome_activation_time(activation_time, reference_time=reference_time)
    )


def scheduled_checkout_at_utc(reservation: Any, listing: Any | None = None) -> datetime:
    """Build the property's scheduled Hostaway checkout timestamp in UTC."""
    checkout_clock = _checkout_clock(getattr(listing, "check_out_time", None))
    timezone_name, _ = listing_timezone_resolution(listing)
    try:
        listing_timezone = ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError):
        logger.warning("Unknown listing timezone %r; using UTC for checkout eligibility", timezone_name)
        listing_timezone = timezone.utc
    local_checkout = datetime.combine(reservation.departure_date, checkout_clock).replace(
        tzinfo=listing_timezone
    )
    return normalize_utc_datetime(local_checkout)


def listing_timezone_resolution(listing: Any | None) -> tuple[str, str]:
    """Return an IANA timezone and the auditable source used to resolve it."""
    explicit = str(getattr(listing, "timezone_name", None) or "").strip()
    if explicit:
        try:
            ZoneInfo(explicit)
            return explicit, "hostaway_timezone"
        except (ZoneInfoNotFoundError, ValueError):
            logger.warning("Unknown Hostaway listing timezone %r; trying location fallback", explicit)
    state = str(getattr(listing, "state", None) or "").strip().upper()
    city = str(getattr(listing, "city", None) or "").strip().lower()
    city_timezone = US_CITY_TIMEZONE_OVERRIDES.get((city, state))
    if city_timezone:
        return city_timezone, "city_state_fallback"
    state_timezone = US_STATE_TIMEZONES.get(state)
    if state_timezone:
        return state_timezone, "state_fallback"
    return "UTC", "utc_fallback"


def stay_outcome_eligible_at(reservation: Any, listing: Any | None = None) -> datetime:
    """Return the earliest UTC time at which this completed stay may be analyzed."""
    return scheduled_checkout_at_utc(reservation, listing) + CLASSIFICATION_DELAY


def is_stay_outcome_eligible(
    reservation: Any,
    listing: Any | None,
    *,
    reference_time: datetime,
    earliest_checkout_at: datetime,
) -> bool:
    """Apply both the 24-hour finalization rule and fixed activation cutoff."""
    checkout_at = scheduled_checkout_at_utc(reservation, listing)
    return checkout_at >= earliest_checkout_at and checkout_at + CLASSIFICATION_DELAY <= reference_time


def build_stay_outcome_input(
    reservation: Any,
    messages: Iterable[Any],
    *,
    conversation_present: bool,
    window_end: datetime | None = None,
) -> dict[str, Any]:
    """Return the bounded, hashable conversation payload used by the classifier."""
    window_start = datetime.combine(reservation.arrival_date - timedelta(days=2), time.min)
    window_end = window_end or datetime.combine(reservation.departure_date + timedelta(days=1), time.max)
    ordered = sorted(
        [
            message
            for message in messages or []
            if getattr(message, "created_at", None)
            and window_start <= message.created_at <= window_end
            and not _message_is_automated(message)
        ],
        key=lambda row: (row.created_at, row.message_id or 0),
    )
    if len(ordered) > 160:
        ordered = ordered[:40] + ordered[-120:]

    message_payload = []
    possible_truncation_count = 0
    for message in ordered:
        body = " ".join(str(getattr(message, "content_preview", "") or "").split())
        if len(body) == 200:
            possible_truncation_count += 1
        message_payload.append(
            {
                "message_id": int(message.message_id),
                "timestamp": message.created_at.isoformat(),
                "direction": "guest" if bool(message.is_incoming) else "host",
                "text": body[:1200],
            }
        )

    payload = {
        "reservation_id": int(reservation.reservation_id),
        "listing_id": int(reservation.listing_id),
        "arrival_date": reservation.arrival_date.isoformat(),
        "departure_date": reservation.departure_date.isoformat(),
        "analysis_cutoff": window_end.isoformat(),
        "conversation_present": bool(conversation_present),
        "messages": message_payload,
    }
    return {
        "payload": payload,
        "input_hash": stable_hash({"prompt_version": STAY_OUTCOME_PROMPT_VERSION, **payload}),
        "message_count": len(message_payload),
        "incoming_count": sum(1 for item in message_payload if item["direction"] == "guest"),
        "possible_truncation_count": possible_truncation_count,
    }


def normalize_stay_outcome_result(
    result: dict[str, Any],
    *,
    valid_message_ids: set[int],
) -> dict[str, Any]:
    """Validate the model output and derive the final label from issue states."""
    issues = []
    for raw_issue in result.get("issues") or []:
        severity = str(raw_issue.get("severity") or "minor").strip().lower()
        if severity not in {"minor", "material", "critical"}:
            severity = "minor"
        resolution_state = str(raw_issue.get("resolution_state") or "unclear").strip().lower()
        if resolution_state not in {"resolved", "unresolved", "unclear"}:
            resolution_state = "unclear"
        evidence_ids = _valid_evidence_ids(raw_issue.get("evidence_message_ids"), valid_message_ids)
        issues.append(
            {
                "issue_type": str(raw_issue.get("issue_type") or "other"),
                "severity": severity,
                "description": str(raw_issue.get("description") or "").strip()[:900],
                "resolution_state": resolution_state,
                "resolution_evidence": str(raw_issue.get("resolution_evidence") or "").strip()[:900],
                "evidence_message_ids": evidence_ids,
            }
        )

    material = [issue for issue in issues if issue["severity"] in MATERIAL_SEVERITIES]
    resolved = [issue for issue in material if issue["resolution_state"] == "resolved"]
    unresolved = [issue for issue in material if issue["resolution_state"] == "unresolved"]
    unclear = [issue for issue in material if issue["resolution_state"] == "unclear"]
    if not material:
        derived_outcome = "smooth"
    elif unresolved:
        derived_outcome = "unresolved"
    elif unclear:
        derived_outcome = "needs_review"
    else:
        derived_outcome = "recovered"

    try:
        confidence = min(max(float(result.get("confidence") or 0.0), 0.0), 1.0)
    except (TypeError, ValueError):
        confidence = 0.0
    if confidence < MINIMUM_CLASSIFICATION_CONFIDENCE:
        derived_outcome = "needs_review"

    evidence_ids = set(_valid_evidence_ids(result.get("evidence_message_ids"), valid_message_ids))
    for issue in issues:
        evidence_ids.update(issue["evidence_message_ids"])

    return {
        "outcome": derived_outcome,
        "confidence": round(confidence, 4),
        "summary": str(result.get("summary") or "Outcome derived from captured Hostaway messages.").strip()[:1400],
        "issues": issues,
        "evidence_message_ids": sorted(evidence_ids),
        "issue_count": len(issues),
        "material_issue_count": len(material),
        "resolved_issue_count": len(resolved),
        "unresolved_issue_count": len(unresolved),
        "model_outcome": str(result.get("outcome") or "needs_review").strip().lower(),
    }


class StayOutcomeClassificationService:
    """Analyze each eligible completed stay once; dashboard reads remain API-free."""

    def __init__(self, *, session=None, main_session=None, ai_client=None):
        self.session = session or get_session()
        self.main_session = main_session or get_main_session(config.MAIN_DATABASE_PATH)
        self.ai = ai_client or BrainAIClient(model=config.KPI_STAY_OUTCOME_MODEL)
        self._owns_session = session is None
        self._owns_main_session = main_session is None

    def close(self):
        if self._owns_session:
            self.session.close()
        if self._owns_main_session:
            self.main_session.close()

    def refresh(
        self,
        *,
        run_id: int | None = None,
        window_days: int = 42,
        max_stays: int = 120,
        batch_size: int = 8,
        reference_date: date | None = None,
        reference_time: datetime | None = None,
        activation_time: str | datetime | None = None,
    ) -> dict[str, Any]:
        if self.session.get_bind().dialect.name == "postgresql":
            # Serialize classifier runs so two background/manual refreshes cannot
            # spend twice or race the database-level one-reservation constraint.
            self.session.execute(
                text("SELECT pg_advisory_xact_lock(:lock_id)"),
                {"lock_id": STAY_OUTCOME_REFRESH_LOCK_ID},
            )
        if reference_time is not None:
            now = normalize_utc_datetime(reference_time)
        elif reference_date is not None:
            now = datetime.combine(reference_date, time.max)
        else:
            now = datetime.utcnow()
        activation_at = stay_outcome_activation_time(activation_time, reference_time=now)
        activation_cutoff = initial_stay_outcome_checkout_cutoff(activation_at)
        requested_cutoff = now - timedelta(days=max(1, window_days))
        earliest_checkout_at = max(activation_cutoff, requested_cutoff)
        reservations = (
            self.main_session.query(Reservation)
            .filter(
                Reservation.arrival_date.isnot(None),
                Reservation.departure_date.isnot(None),
                Reservation.departure_date >= earliest_checkout_at.date(),
                Reservation.departure_date <= now.date(),
            )
            .order_by(Reservation.departure_date.desc(), Reservation.reservation_id.desc())
            .limit(max(max_stays * 10, 2000))
            .all()
        )
        reservations = [
            row for row in reservations
            if is_confirmed_reservation_status(row.status)
        ]
        listing_ids = sorted({int(row.listing_id) for row in reservations})
        listings = {
            int(row.listing_id): row
            for row in self.main_session.query(Listing)
            .filter(Listing.listing_id.in_(listing_ids or [-1]))
            .all()
        }
        reservations = [
            row
            for row in reservations
            if is_stay_outcome_eligible(
                row,
                listings.get(int(row.listing_id)),
                reference_time=now,
                earliest_checkout_at=earliest_checkout_at,
            )
        ]
        reservation_ids = [row.reservation_id for row in reservations]
        if not reservation_ids:
            return {
                "eligible": 0,
                "classified": 0,
                "cached": 0,
                "needs_review": 0,
                "errors": 0,
                "model": config.KPI_STAY_OUTCOME_MODEL,
                "activation_at": activation_at.isoformat(),
                "earliest_checkout_at": earliest_checkout_at.isoformat(),
            }

        messages = (
            self.main_session.query(MessageMetadata)
            .filter(MessageMetadata.reservation_id.in_(reservation_ids))
            .order_by(MessageMetadata.reservation_id, MessageMetadata.created_at, MessageMetadata.message_id)
            .all()
        )
        messages_by_reservation: dict[int, list[Any]] = defaultdict(list)
        for message in messages:
            messages_by_reservation[int(message.reservation_id)].append(message)
        conversation_reservation_ids = {
            int(row[0])
            for row in self.main_session.query(Conversation.reservation_id)
            .filter(Conversation.reservation_id.in_(reservation_ids))
            .distinct()
            .all()
            if row[0] is not None
        }
        analyzed_reservation_ids = {
            int(row[0])
            for row in self.session.query(StayOutcomeClassification.reservation_id)
            .filter(
                StayOutcomeClassification.reservation_id.in_(reservation_ids),
            )
            .distinct()
            .all()
        }

        cached = 0
        needs_review = 0
        backlog = 0
        pending: list[tuple[Any, dict[str, Any]]] = []
        for reservation in reservations:
            if reservation.reservation_id in analyzed_reservation_ids:
                cached += 1
                continue
            listing = listings.get(int(reservation.listing_id))
            eligible_at = stay_outcome_eligible_at(reservation, listing)
            prepared = build_stay_outcome_input(
                reservation,
                messages_by_reservation.get(reservation.reservation_id, []),
                conversation_present=reservation.reservation_id in conversation_reservation_ids,
                window_end=eligible_at,
            )
            prepared["checkout_at"] = scheduled_checkout_at_utc(reservation, listing)
            prepared["eligible_at"] = eligible_at
            prepared["timezone_name"], prepared["timezone_source"] = listing_timezone_resolution(listing)
            if not prepared["payload"]["conversation_present"]:
                self._store(
                    reservation,
                    prepared,
                    {
                        "outcome": "needs_review",
                        "confidence": 0.0,
                        "summary": "No Hostaway conversation is linked to this completed stay.",
                        "issues": [],
                        "evidence_message_ids": [],
                        "issue_count": 0,
                        "material_issue_count": 0,
                        "resolved_issue_count": 0,
                        "unresolved_issue_count": 0,
                        "model_outcome": "needs_review",
                    },
                    run_id=run_id,
                    classification_source="data_coverage",
                )
                needs_review += 1
                continue
            if prepared["message_count"] == 0:
                self._store(
                    reservation,
                    prepared,
                    {
                        "outcome": "smooth",
                        "confidence": 0.8,
                        "summary": "No guest-impacting issue was detected in the captured Hostaway conversation.",
                        "issues": [],
                        "evidence_message_ids": [],
                        "issue_count": 0,
                        "material_issue_count": 0,
                        "resolved_issue_count": 0,
                        "unresolved_issue_count": 0,
                        "model_outcome": "smooth",
                    },
                    run_id=run_id,
                    classification_source="no_issue_observed",
                )
                continue
            if len(pending) < max_stays:
                pending.append((reservation, prepared))
            else:
                backlog += 1

        classified = 0
        errors = 0
        for offset in range(0, len(pending), max(1, batch_size)):
            batch = pending[offset:offset + max(1, batch_size)]
            context = {
                "prompt_version": STAY_OUTCOME_PROMPT_VERSION,
                "important": "Reviews are deliberately excluded; classify only the captured message evidence.",
                "stays": [prepared["payload"] for _, prepared in batch],
            }
            try:
                response = self.ai.classify_stay_outcomes(context)
                results = {
                    int(item.get("reservation_id")): item
                    for item in response.get("stays") or []
                    if item.get("reservation_id") is not None
                }
                for reservation, prepared in batch:
                    raw_result = results.get(reservation.reservation_id)
                    if not raw_result:
                        errors += 1
                        continue
                    valid_ids = {item["message_id"] for item in prepared["payload"]["messages"]}
                    normalized = normalize_stay_outcome_result(raw_result, valid_message_ids=valid_ids)
                    self._store(
                        reservation,
                        prepared,
                        normalized,
                        run_id=run_id,
                        classification_source="openai",
                    )
                    classified += 1
                    if normalized["outcome"] == "needs_review":
                        needs_review += 1
            except Exception as exc:
                errors += len(batch)
                logger.error("Stay outcome classification batch failed: %s", exc, exc_info=True)

        self.session.commit()
        return {
            "eligible": len(reservations),
            "classified": classified,
            "cached": cached,
            "needs_review": needs_review,
            "errors": errors,
            "backlog": backlog,
            "prompt_version": STAY_OUTCOME_PROMPT_VERSION,
            "model": config.KPI_STAY_OUTCOME_MODEL,
            "activation_at": activation_at.isoformat(),
            "earliest_checkout_at": earliest_checkout_at.isoformat(),
        }

    def _store(
        self,
        reservation: Any,
        prepared: dict[str, Any],
        result: dict[str, Any],
        *,
        run_id: int | None,
        classification_source: str,
    ):
        row = (
            self.session.query(StayOutcomeClassification)
            .filter(StayOutcomeClassification.reservation_id == reservation.reservation_id)
            .first()
        )
        if row:
            return False
        row = StayOutcomeClassification(
            reservation_id=reservation.reservation_id,
            listing_id=reservation.listing_id,
            arrival_date=reservation.arrival_date,
            departure_date=reservation.departure_date,
            input_hash=prepared["input_hash"],
            prompt_version=STAY_OUTCOME_PROMPT_VERSION,
            summary=result["summary"],
        )
        self.session.add(row)
        row.run_id = run_id
        row.listing_id = reservation.listing_id
        row.arrival_date = reservation.arrival_date
        row.departure_date = reservation.departure_date
        row.outcome = result["outcome"] if result["outcome"] in OUTCOMES else "needs_review"
        row.confidence = result["confidence"]
        row.issue_count = result["issue_count"]
        row.material_issue_count = result["material_issue_count"]
        row.resolved_issue_count = result["resolved_issue_count"]
        row.unresolved_issue_count = result["unresolved_issue_count"]
        row.summary = result["summary"]
        row.issues = as_json_safe(result["issues"])
        row.evidence_message_ids = as_json_safe(result["evidence_message_ids"])
        row.model = getattr(self.ai, "model", config.KPI_STAY_OUTCOME_MODEL) if classification_source == "openai" else None
        row.record_status = "current"
        row.classification_source = classification_source
        row.source_metadata = as_json_safe(
            {
                "message_count": prepared["message_count"],
                "incoming_count": prepared["incoming_count"],
                "possible_truncation_count": prepared["possible_truncation_count"],
                "model_outcome": result.get("model_outcome"),
                "window_start": (reservation.arrival_date - timedelta(days=2)).isoformat(),
                "window_end": prepared["payload"]["analysis_cutoff"],
                "scheduled_checkout_at_utc": prepared["checkout_at"].isoformat(),
                "eligible_at_utc": prepared["eligible_at"].isoformat(),
                "timezone_name": prepared["timezone_name"],
                "timezone_source": prepared["timezone_source"],
                "reviews_used": False,
            }
        )
        row.classified_at = datetime.utcnow()
        return True


def _checkout_clock(value: Any) -> time:
    """Normalize Hostaway's integer checkout time, accepting hour or HHMM forms."""
    try:
        numeric = int(value)
    except (TypeError, ValueError):
        return time(DEFAULT_CHECKOUT_HOUR)
    if 0 <= numeric <= 23:
        return time(numeric)
    if 100 <= numeric <= 2359:
        hour, minute = divmod(numeric, 100)
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return time(hour, minute)
    return time(DEFAULT_CHECKOUT_HOUR)


def _message_is_automated(message: Any) -> bool:
    sender = str(getattr(message, "sender_name", "") or "").strip().lower()
    return "automated" in sender or "automation" in sender


def _valid_evidence_ids(values: Any, valid_message_ids: set[int]) -> list[int]:
    result = []
    for value in values or []:
        try:
            message_id = int(value)
        except (TypeError, ValueError):
            continue
        if message_id in valid_message_ids and message_id not in result:
            result.append(message_id)
    return result
