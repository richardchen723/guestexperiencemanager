#!/usr/bin/env python3
"""Independent, idempotent analysis of completed stays and guest reviews."""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Iterable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import func, text

import dashboard.config as config
from brain.models import (
    ComprehensiveStayAnalysis,
    GuestExperienceAnalysisRun,
    GuestReviewIssueAnalysis,
    PropertyGuestIssue,
    as_json_safe,
    get_session as get_brain_session,
    stable_hash,
)
from brain.scoring import is_confirmed_reservation_status
from database.models import (
    Listing,
    MessageMetadata,
    Reservation,
    Review,
    get_session as get_main_session,
)

logger = logging.getLogger(__name__)

COMPREHENSIVE_STAY_PROMPT_VERSION = "guest-experience-stay-v1"
GUEST_REVIEW_ISSUE_PROMPT_VERSION = "guest-experience-review-v1"
ANALYSIS_LOOKBACK_MONTHS = 3
ANALYSIS_DELAY = timedelta(hours=24)
DEFAULT_CHECKOUT_HOUR = 11
GUEST_EXPERIENCE_LOCK_ID = 780_411_944
STAY_QUALITIES = {"smooth", "recovered", "unresolved", "muted"}
SEVERITIES = {"minor", "material", "critical"}
RESOLUTION_STATES = {"resolved", "unresolved", "unclear"}
REVIEW_SOURCE_PARTS = {"public_review", "private_feedback", "overall_rating", "sub_rating"}

US_STATE_TIMEZONES = {
    "AK": "America/Anchorage", "AL": "America/Chicago", "AR": "America/Chicago",
    "AZ": "America/Phoenix", "CA": "America/Los_Angeles", "CO": "America/Denver",
    "CT": "America/New_York", "DC": "America/New_York", "DE": "America/New_York",
    "FL": "America/New_York", "GA": "America/New_York", "HI": "Pacific/Honolulu",
    "IA": "America/Chicago", "ID": "America/Denver", "IL": "America/Chicago",
    "IN": "America/Indiana/Indianapolis", "KS": "America/Chicago", "KY": "America/New_York",
    "LA": "America/Chicago", "MA": "America/New_York", "MD": "America/New_York",
    "ME": "America/New_York", "MI": "America/Detroit", "MN": "America/Chicago",
    "MO": "America/Chicago", "MS": "America/Chicago", "MT": "America/Denver",
    "NC": "America/New_York", "ND": "America/Chicago", "NE": "America/Chicago",
    "NH": "America/New_York", "NJ": "America/New_York", "NM": "America/Denver",
    "NV": "America/Los_Angeles", "NY": "America/New_York", "OH": "America/New_York",
    "OK": "America/Chicago", "OR": "America/Los_Angeles", "PA": "America/New_York",
    "RI": "America/New_York", "SC": "America/New_York", "SD": "America/Chicago",
    "TN": "America/Chicago", "TX": "America/Chicago", "UT": "America/Denver",
    "VA": "America/New_York", "VT": "America/New_York", "WA": "America/Los_Angeles",
    "WI": "America/Chicago", "WV": "America/New_York", "WY": "America/Denver",
}
US_CITY_TIMEZONE_OVERRIDES = {
    ("sevierville", "TN"): "America/New_York",
    ("el paso", "TX"): "America/Denver",
}


def normalize_utc(value: datetime) -> datetime:
    """Return a naive UTC timestamp, matching application storage."""
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def calendar_months_before(value: datetime, months: int) -> datetime:
    """Subtract whole calendar months while retaining a valid day and clock."""
    from calendar import monthrange

    absolute_month = value.year * 12 + (value.month - 1) - max(months, 0)
    year, zero_based_month = divmod(absolute_month, 12)
    month = zero_based_month + 1
    day = min(value.day, monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)


def analysis_window(reference_time: datetime) -> tuple[datetime, datetime]:
    """Return the exact three-calendar-month checkout window for one run."""
    end_at = normalize_utc(reference_time) - ANALYSIS_DELAY
    return calendar_months_before(normalize_utc(reference_time), ANALYSIS_LOOKBACK_MONTHS), end_at


def listing_timezone(listing: Any | None) -> tuple[str, str]:
    explicit = str(getattr(listing, "timezone_name", None) or "").strip()
    if explicit:
        try:
            ZoneInfo(explicit)
            return explicit, "hostaway_timezone"
        except (ZoneInfoNotFoundError, ValueError):
            logger.warning("Unknown Hostaway timezone %r; using property location", explicit)
    state = str(getattr(listing, "state", None) or "").strip().upper()
    city = str(getattr(listing, "city", None) or "").strip().lower()
    if (city, state) in US_CITY_TIMEZONE_OVERRIDES:
        return US_CITY_TIMEZONE_OVERRIDES[(city, state)], "city_state_fallback"
    if state in US_STATE_TIMEZONES:
        return US_STATE_TIMEZONES[state], "state_fallback"
    return "UTC", "utc_fallback"


def scheduled_checkout_at_utc(reservation: Any, listing: Any | None) -> datetime:
    """Resolve the scheduled property-local checkout to naive UTC."""
    checkout_clock = _checkout_clock(getattr(listing, "check_out_time", None))
    timezone_name, _ = listing_timezone(listing)
    try:
        property_timezone = ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError):
        property_timezone = timezone.utc
    local_checkout = datetime.combine(reservation.departure_date, checkout_clock).replace(
        tzinfo=property_timezone
    )
    return normalize_utc(local_checkout)


def is_analysis_eligible(
    reservation: Any,
    listing: Any | None,
    *,
    reference_time: datetime,
) -> bool:
    """Require a checkout between three calendar months and 24 hours ago."""
    window_start, window_end = analysis_window(reference_time)
    checkout_at = scheduled_checkout_at_utc(reservation, listing)
    return window_start <= checkout_at <= window_end


def build_stay_input(
    reservation: Any,
    listing: Any | None,
    messages: Iterable[Any],
    *,
    analyzed_at: datetime,
) -> dict[str, Any]:
    """Build a complete, hashable operational stay and message payload."""
    ordered_messages = sorted(
        [
            row for row in (messages or [])
            if getattr(row, "created_at", None) and row.created_at <= analyzed_at
        ],
        key=lambda row: (row.created_at, row.message_id or 0),
    )
    message_payload = []
    for message in ordered_messages:
        body = str(getattr(message, "content_preview", "") or "").strip()
        message_payload.append({
            "message_id": int(message.message_id),
            "timestamp": message.created_at.isoformat(),
            "direction": "guest" if bool(message.is_incoming) else "support",
            "message_type": str(message.message_type or "text"),
            "has_attachment": bool(message.has_attachment),
            "text": body,
        })

    timezone_name, timezone_source = listing_timezone(listing)
    checkout_at = scheduled_checkout_at_utc(reservation, listing)
    stay_data = {
        "reservation_id": int(reservation.reservation_id),
        "listing_id": int(reservation.listing_id),
        "arrival_date": reservation.arrival_date.isoformat(),
        "departure_date": reservation.departure_date.isoformat(),
        "scheduled_checkout_at_utc": checkout_at.isoformat(),
        "channel": reservation.channel_name,
        "source": reservation.source,
        "status": reservation.status,
        "nights": reservation.nights,
        "number_of_guests": reservation.number_of_guests,
        "adults": reservation.adults,
        "children": reservation.children,
        "infants": reservation.infants,
        "pets": reservation.pets,
        "total_price": reservation.total_price,
        "currency": reservation.currency,
        "host_note": reservation.host_note,
        "guest_note": reservation.guest_note,
        "internal_comment": reservation.comment,
        "custom_field_values": _parse_json_value(reservation.custom_field_values),
    }
    listing_data = {
        "listing_name": getattr(listing, "internal_listing_name", None) or getattr(listing, "name", None),
        "property_type_id": getattr(listing, "property_type_id", None),
        "accommodates": getattr(listing, "accommodates", None),
        "bedrooms": getattr(listing, "bedrooms", None),
        "bathrooms": getattr(listing, "bathrooms", None),
        "city": getattr(listing, "city", None),
        "state": getattr(listing, "state", None),
        "country": getattr(listing, "country", None),
        "timezone": timezone_name,
    }
    payload = {
        "reservation_id": int(reservation.reservation_id),
        "analyzed_at": analyzed_at.isoformat(),
        "stay": stay_data,
        "property": listing_data,
        "messages": message_payload,
    }
    return {
        "payload": payload,
        "input_hash": stable_hash({
            "prompt_version": COMPREHENSIVE_STAY_PROMPT_VERSION,
            **payload,
        }),
        "checkout_at": checkout_at,
        "eligible_at": checkout_at + ANALYSIS_DELAY,
        "message_count": len(message_payload),
        "guest_message_count": sum(row["direction"] == "guest" for row in message_payload),
        "message_ids": [row["message_id"] for row in message_payload],
        "timezone_name": timezone_name,
        "timezone_source": timezone_source,
    }


def normalize_stay_result(
    result: dict[str, Any],
    *,
    valid_messages: dict[int, str],
) -> dict[str, Any]:
    """Validate evidence and derive one of the four requested stay qualities."""
    guest_message_ids = {
        message_id for message_id, direction in valid_messages.items() if direction == "guest"
    }
    issues = []
    for raw_issue in result.get("issues") or []:
        references = []
        for raw_reference in raw_issue.get("source_references") or []:
            try:
                source_id = int(raw_reference.get("source_id"))
            except (TypeError, ValueError):
                continue
            if source_id not in valid_messages:
                continue
            role = str(raw_reference.get("role") or "context").strip().lower()
            if role not in {"complaint", "resolution", "context"}:
                role = "context"
            reference = {"source_type": "message", "source_id": source_id, "role": role}
            if reference not in references:
                references.append(reference)
        complaint_ids = {
            ref["source_id"] for ref in references
            if ref["role"] == "complaint" and ref["source_id"] in guest_message_ids
        }
        if not complaint_ids:
            continue
        severity = str(raw_issue.get("severity") or "material").strip().lower()
        resolution_state = str(raw_issue.get("resolution_state") or "unclear").strip().lower()
        issues.append({
            "issue_category": _clean_label(raw_issue.get("issue_category"), "other"),
            "summary": _clean_text(raw_issue.get("summary"), "Guest-reported issue", 240),
            "details": _clean_text(raw_issue.get("details"), "No additional detail supplied.", 2400),
            "suggested_improvement": _clean_text(raw_issue.get("suggested_improvement"), "", 1400),
            "severity": severity if severity in SEVERITIES else "material",
            "resolution_state": (
                resolution_state if resolution_state in RESOLUTION_STATES else "unclear"
            ),
            "source_references": references,
        })

    if not guest_message_ids:
        stay_quality = "muted"
    elif not issues:
        stay_quality = "smooth"
    elif any(issue["resolution_state"] != "resolved" for issue in issues):
        stay_quality = "unresolved"
    else:
        stay_quality = "recovered"

    summary = _clean_text(
        result.get("summary"),
        _default_stay_summary(stay_quality),
        900,
    )
    detailed_summary = _clean_text(
        result.get("detailed_summary"),
        summary,
        4000,
    )
    return {
        "stay_quality": stay_quality,
        "summary": summary,
        "detailed_summary": detailed_summary,
        "issues": issues,
    }


def build_review_input(review: Any) -> dict[str, Any]:
    """Build a complete public/private review payload for issue extraction."""
    sub_ratings = [
        {"category": row.rating_category, "rating": row.rating_value}
        for row in (review.sub_ratings or [])
    ]
    payload = {
        "review_id": int(review.review_id),
        "reservation_id": review.reservation_id,
        "listing_id": int(review.listing_id),
        "review_date": review.review_date.isoformat() if review.review_date else None,
        "channel": review.channel_name,
        "overall_rating": review.overall_rating,
        "publicReview": review.review_text,
        "privateFeedback": review.private_feedback,
        "sub_ratings": sub_ratings,
        "language": review.language,
        "status": review.status,
    }
    return {
        "payload": payload,
        "input_hash": stable_hash({
            "prompt_version": GUEST_REVIEW_ISSUE_PROMPT_VERSION,
            **payload,
        }),
        "has_public_review": bool(str(review.review_text or "").strip()),
        "has_private_feedback": bool(str(review.private_feedback or "").strip()),
        "sub_rating_count": len(sub_ratings),
    }


def normalize_review_result(result: dict[str, Any], *, review_id: int) -> dict[str, Any]:
    issues = []
    for raw_issue in result.get("issues") or []:
        references = []
        for raw_reference in raw_issue.get("source_references") or []:
            try:
                source_id = int(raw_reference.get("source_id"))
            except (TypeError, ValueError):
                continue
            source_part = str(raw_reference.get("source_part") or "").strip().lower()
            if source_id != int(review_id) or source_part not in REVIEW_SOURCE_PARTS:
                continue
            reference = {
                "source_type": "review",
                "source_id": source_id,
                "source_part": source_part,
            }
            if reference not in references:
                references.append(reference)
        if not references:
            continue
        severity = str(raw_issue.get("severity") or "material").strip().lower()
        evidence_basis = str(raw_issue.get("evidence_basis") or "explicit_feedback").strip().lower()
        issues.append({
            "issue_category": _clean_label(raw_issue.get("issue_category"), "other"),
            "summary": _clean_text(raw_issue.get("summary"), "Review issue", 240),
            "details": _clean_text(raw_issue.get("details"), "No additional detail supplied.", 2400),
            "suggested_improvement": _clean_text(raw_issue.get("suggested_improvement"), "", 1400),
            "severity": severity if severity in SEVERITIES else "material",
            "evidence_basis": (
                evidence_basis if evidence_basis in {"explicit_feedback", "rating_signal"}
                else "explicit_feedback"
            ),
            "source_references": references,
        })
    return {
        "summary": _clean_text(
            result.get("summary"),
            "No property issue was identified in this guest review.",
            1400,
        ),
        "issues": issues,
    }


class GuestExperienceAnalysisService:
    """Disabled legacy API-backed runner retained only for migration safety."""

    def __init__(self, *, session=None, main_session=None, ai_client=None):
        raise RuntimeError(
            "API-backed guest-experience analysis is disabled. "
            "Use brain.guest_experience_codex from a Codex scheduled task."
        )

    def close(self):
        if self._owns_session:
            self.session.close()
        if self._owns_main_session:
            self.main_session.close()

    def refresh(
        self,
        *,
        reference_time: datetime | None = None,
        max_stays: int | None = None,
        max_reviews: int | None = None,
    ) -> dict[str, Any]:
        now = normalize_utc(reference_time or datetime.utcnow())
        window_start, window_end = analysis_window(now)
        max_stays = max(1, int(max_stays or config.GUEST_EXPERIENCE_MAX_STAYS_PER_RUN))
        max_reviews = max(1, int(max_reviews or config.GUEST_EXPERIENCE_MAX_REVIEWS_PER_RUN))

        if self.session.get_bind().dialect.name == "postgresql":
            self.session.execute(
                text("SELECT pg_advisory_xact_lock(:lock_id)"),
                {"lock_id": GUEST_EXPERIENCE_LOCK_ID},
            )

        run = GuestExperienceAnalysisRun(
            status="running",
            window_start_at=window_start,
            window_end_at=window_end,
            started_at=now,
        )
        self.session.add(run)
        self.session.flush()

        try:
            stay_stats = self._analyze_stays(
                run.run_id,
                now=now,
                max_stays=max_stays,
            )
            review_stats = self._analyze_reviews(
                run.run_id,
                now=now,
                max_reviews=max_reviews,
            )
            error_count = stay_stats["errors"] + review_stats["errors"]
            run.status = "completed" if error_count == 0 else "partial"
            run.eligible_stay_count = stay_stats["eligible"]
            run.stays_analyzed = stay_stats["analyzed"]
            run.stays_already_analyzed = stay_stats["already_analyzed"]
            run.reviews_analyzed = review_stats["analyzed"]
            run.reviews_already_analyzed = review_stats["already_analyzed"]
            run.error_count = error_count
            run.details = as_json_safe({"stays": stay_stats, "reviews": review_stats})
            run.completed_at = datetime.utcnow()
            self.session.commit()
            return {
                "status": run.status,
                "run_id": run.run_id,
                "window_start_at": window_start.isoformat(),
                "window_end_at": window_end.isoformat(),
                "stays": stay_stats,
                "reviews": review_stats,
                "model": self.ai.model,
            }
        except Exception:
            self.session.rollback()
            logger.error("Guest-experience analysis run failed", exc_info=True)
            raise

    def _analyze_stays(self, run_id: int, *, now: datetime, max_stays: int) -> dict[str, int]:
        window_start, _ = analysis_window(now)
        candidates = (
            self.main_session.query(Reservation)
            .filter(
                Reservation.arrival_date.isnot(None),
                Reservation.departure_date.isnot(None),
                Reservation.departure_date >= window_start.date(),
                Reservation.departure_date <= now.date(),
            )
            .order_by(Reservation.departure_date.asc(), Reservation.reservation_id.asc())
            .all()
        )
        candidates = [row for row in candidates if is_confirmed_reservation_status(row.status)]
        listing_ids = sorted({int(row.listing_id) for row in candidates})
        listings = {
            int(row.listing_id): row
            for row in self.main_session.query(Listing)
            .filter(Listing.listing_id.in_(listing_ids or [-1]))
            .all()
        }
        eligible = [
            row for row in candidates
            if is_analysis_eligible(row, listings.get(int(row.listing_id)), reference_time=now)
        ]
        eligible_ids = [int(row.reservation_id) for row in eligible]
        existing_ids = {
            int(row[0]) for row in self.session.query(ComprehensiveStayAnalysis.reservation_id)
            .filter(ComprehensiveStayAnalysis.reservation_id.in_(eligible_ids or [-1]))
            .all()
        }
        pending = [row for row in eligible if int(row.reservation_id) not in existing_ids][:max_stays]
        pending_ids = [int(row.reservation_id) for row in pending]
        messages = (
            self.main_session.query(MessageMetadata)
            .filter(MessageMetadata.reservation_id.in_(pending_ids or [-1]))
            .order_by(MessageMetadata.reservation_id, MessageMetadata.created_at, MessageMetadata.message_id)
            .all()
        )
        messages_by_reservation: dict[int, list[Any]] = defaultdict(list)
        for message in messages:
            messages_by_reservation[int(message.reservation_id)].append(message)

        prepared_stays = []
        analyzed = errors = muted = 0
        for reservation in pending:
            prepared = build_stay_input(
                reservation,
                listings.get(int(reservation.listing_id)),
                messages_by_reservation.get(int(reservation.reservation_id), []),
                analyzed_at=now,
            )
            if prepared["guest_message_count"] == 0:
                normalized = {
                    "stay_quality": "muted",
                    "summary": "Muted stay — the guest sent no messages.",
                    "detailed_summary": (
                        "No guest communication was captured for this stay. "
                        f"The conversation contains {prepared['message_count']} support or automated messages."
                    ),
                    "issues": [],
                }
                self._store_stay(run_id, reservation, prepared, normalized)
                analyzed += 1
                muted += 1
            else:
                prepared_stays.append((reservation, prepared))

        batch_size = max(1, int(config.GUEST_EXPERIENCE_STAY_BATCH_SIZE))
        for offset in range(0, len(prepared_stays), batch_size):
            batch = prepared_stays[offset:offset + batch_size]
            try:
                response = self.ai.analyze_comprehensive_stays({
                    "prompt_version": COMPREHENSIVE_STAY_PROMPT_VERSION,
                    "stays": [prepared["payload"] for _, prepared in batch],
                })
                results = {
                    int(item["reservation_id"]): item
                    for item in response.get("stays") or []
                    if item.get("reservation_id") is not None
                }
                for reservation, prepared in batch:
                    raw_result = results.get(int(reservation.reservation_id))
                    if not raw_result:
                        errors += 1
                        continue
                    directions = {
                        item["message_id"]: item["direction"]
                        for item in prepared["payload"]["messages"]
                    }
                    normalized = normalize_stay_result(raw_result, valid_messages=directions)
                    self._store_stay(run_id, reservation, prepared, normalized)
                    analyzed += 1
            except Exception as exc:
                errors += len(batch)
                logger.error("Comprehensive stay batch failed: %s", exc, exc_info=True)

        return {
            "eligible": len(eligible),
            "analyzed": analyzed,
            "already_analyzed": len(existing_ids),
            "muted": muted,
            "backlog": max(len(eligible) - len(existing_ids) - len(pending), 0),
            "errors": errors,
        }

    def _analyze_reviews(self, run_id: int, *, now: datetime, max_reviews: int) -> dict[str, int]:
        window_start, _ = analysis_window(now)
        reviews = (
            self.main_session.query(Review)
            .filter(
                func.lower(func.coalesce(Review.origin, "")) == "guest",
                func.lower(func.coalesce(Review.status, "")).in_(("submitted", "published")),
            )
            .order_by(Review.review_date.asc().nulls_last(), Review.review_id.asc())
            .all()
        )
        reservation_ids = {int(row.reservation_id) for row in reviews if row.reservation_id}
        reservations = {
            int(row.reservation_id): row
            for row in self.main_session.query(Reservation)
            .filter(Reservation.reservation_id.in_(reservation_ids or [-1]))
            .all()
        }
        review_listing_ids = sorted({int(row.listing_id) for row in reviews})
        listings = {
            int(row.listing_id): row
            for row in self.main_session.query(Listing)
            .filter(Listing.listing_id.in_(review_listing_ids or [-1]))
            .all()
        }

        def review_in_window(review: Review) -> bool:
            reservation = reservations.get(int(review.reservation_id)) if review.reservation_id else None
            if reservation and reservation.departure_date:
                return is_analysis_eligible(
                    reservation,
                    listings.get(int(review.listing_id)),
                    reference_time=now,
                )
            return bool(
                review.review_date
                and window_start.date() <= review.review_date <= (now - ANALYSIS_DELAY).date()
            )

        eligible = [review for review in reviews if review_in_window(review)]
        eligible_ids = [int(row.review_id) for row in eligible]
        existing_ids = {
            int(row[0]) for row in self.session.query(GuestReviewIssueAnalysis.review_id)
            .filter(GuestReviewIssueAnalysis.review_id.in_(eligible_ids or [-1]))
            .all()
        }
        pending = [row for row in eligible if int(row.review_id) not in existing_ids][:max_reviews]
        analyzed = errors = 0
        batch_size = max(1, int(config.GUEST_EXPERIENCE_REVIEW_BATCH_SIZE))
        prepared_reviews = [(review, build_review_input(review)) for review in pending]
        for offset in range(0, len(prepared_reviews), batch_size):
            batch = prepared_reviews[offset:offset + batch_size]
            try:
                response = self.ai.analyze_guest_review_issues({
                    "prompt_version": GUEST_REVIEW_ISSUE_PROMPT_VERSION,
                    "reviews": [prepared["payload"] for _, prepared in batch],
                })
                results = {
                    int(item["review_id"]): item
                    for item in response.get("reviews") or []
                    if item.get("review_id") is not None
                }
                for review, prepared in batch:
                    raw_result = results.get(int(review.review_id))
                    if not raw_result:
                        errors += 1
                        continue
                    normalized = normalize_review_result(raw_result, review_id=int(review.review_id))
                    self._store_review(run_id, review, prepared, normalized, reservations)
                    analyzed += 1
            except Exception as exc:
                errors += len(batch)
                logger.error("Guest review analysis batch failed: %s", exc, exc_info=True)

        return {
            "eligible": len(eligible),
            "analyzed": analyzed,
            "already_analyzed": len(existing_ids),
            "backlog": max(len(eligible) - len(existing_ids) - len(pending), 0),
            "errors": errors,
        }

    def _store_stay(
        self,
        run_id: int,
        reservation: Reservation,
        prepared: dict[str, Any],
        result: dict[str, Any],
    ) -> bool:
        existing = self.session.query(ComprehensiveStayAnalysis.stay_analysis_id).filter(
            ComprehensiveStayAnalysis.reservation_id == reservation.reservation_id
        ).first()
        if existing:
            return False
        row = ComprehensiveStayAnalysis(
            run_id=run_id,
            listing_id=reservation.listing_id,
            reservation_id=reservation.reservation_id,
            arrival_date=reservation.arrival_date,
            departure_date=reservation.departure_date,
            checkout_at=prepared["checkout_at"],
            eligible_at=prepared["eligible_at"],
            stay_quality=result["stay_quality"],
            summary=result["summary"],
            detailed_summary=result["detailed_summary"],
            issue_count=len(result["issues"]),
            message_count=prepared["message_count"],
            guest_message_count=prepared["guest_message_count"],
            source_message_ids=as_json_safe(prepared["message_ids"]),
            input_hash=prepared["input_hash"],
            prompt_version=COMPREHENSIVE_STAY_PROMPT_VERSION,
            model=self.ai.model if prepared["guest_message_count"] else None,
            source_metadata=as_json_safe({
                "timezone_name": prepared["timezone_name"],
                "timezone_source": prepared["timezone_source"],
                "analysis_lookback_months": ANALYSIS_LOOKBACK_MONTHS,
                "analysis_delay_hours": int(ANALYSIS_DELAY.total_seconds() // 3600),
            }),
            analyzed_at=datetime.utcnow(),
        )
        self.session.add(row)
        self.session.flush()
        for index, issue in enumerate(result["issues"]):
            self.session.add(PropertyGuestIssue(
                source_kind="stay",
                source_issue_key=f"stay:{reservation.reservation_id}:{index}",
                stay_analysis_id=row.stay_analysis_id,
                listing_id=reservation.listing_id,
                reservation_id=reservation.reservation_id,
                source_date=reservation.departure_date,
                issue_category=issue["issue_category"],
                summary=issue["summary"],
                details=issue["details"],
                suggested_improvement=issue["suggested_improvement"],
                severity=issue["severity"],
                resolution_state=issue["resolution_state"],
                source_references=as_json_safe(issue["source_references"]),
            ))
        return True

    def _store_review(
        self,
        run_id: int,
        review: Review,
        prepared: dict[str, Any],
        result: dict[str, Any],
        reservations: dict[int, Reservation],
    ) -> bool:
        existing = self.session.query(GuestReviewIssueAnalysis.review_analysis_id).filter(
            GuestReviewIssueAnalysis.review_id == review.review_id
        ).first()
        if existing:
            return False
        row = GuestReviewIssueAnalysis(
            run_id=run_id,
            review_id=review.review_id,
            listing_id=review.listing_id,
            reservation_id=review.reservation_id,
            review_date=review.review_date,
            summary=result["summary"],
            issue_count=len(result["issues"]),
            has_public_review=prepared["has_public_review"],
            has_private_feedback=prepared["has_private_feedback"],
            input_hash=prepared["input_hash"],
            prompt_version=GUEST_REVIEW_ISSUE_PROMPT_VERSION,
            model=self.ai.model,
            source_metadata=as_json_safe({
                "sub_rating_count": prepared["sub_rating_count"],
                "overall_rating": review.overall_rating,
                "analysis_lookback_months": ANALYSIS_LOOKBACK_MONTHS,
            }),
            analyzed_at=datetime.utcnow(),
        )
        self.session.add(row)
        self.session.flush()
        reservation = reservations.get(int(review.reservation_id)) if review.reservation_id else None
        source_date = review.review_date or (
            reservation.departure_date if reservation and reservation.departure_date else date.today()
        )
        for index, issue in enumerate(result["issues"]):
            self.session.add(PropertyGuestIssue(
                source_kind="review",
                source_issue_key=f"review:{review.review_id}:{index}",
                review_analysis_id=row.review_analysis_id,
                listing_id=review.listing_id,
                reservation_id=review.reservation_id,
                review_id=review.review_id,
                source_date=source_date,
                issue_category=issue["issue_category"],
                summary=issue["summary"],
                details=issue["details"],
                suggested_improvement=issue["suggested_improvement"],
                severity=issue["severity"],
                resolution_state=None,
                source_references=as_json_safe(issue["source_references"]),
            ))
        return True


def _checkout_clock(value: Any) -> time:
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


def _parse_json_value(value: Any) -> Any:
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        return as_json_safe(value)
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return value


def _clean_text(value: Any, default: str, limit: int) -> str:
    cleaned = " ".join(str(value or "").split()).strip()
    return (cleaned or default)[:limit]


def _clean_label(value: Any, default: str) -> str:
    cleaned = str(value or default).strip().lower().replace(" ", "_")
    return "".join(character for character in cleaned if character.isalnum() or character == "_")[:80] or default


def _default_stay_summary(stay_quality: str) -> str:
    return {
        "muted": "Muted stay — the guest sent no messages.",
        "smooth": "Smooth stay — no guest complaint was identified.",
        "recovered": "Recovered stay — guest complaints were resolved.",
        "unresolved": "Unresolved stay — at least one guest complaint remained open or unclear.",
    }.get(stay_quality, "Stay analysis completed.")
