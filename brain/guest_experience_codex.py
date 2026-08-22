#!/usr/bin/env python3
"""Local batch bridge for subscription-backed Codex guest-experience analysis.

This module never calls a model API. It exports an eligible batch to a local
JSON file and imports evidence-linked results produced by a Codex scheduled
task running under the user's ChatGPT subscription.
"""

from __future__ import annotations

import argparse
import json
import os
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import func, text

from brain.guest_experience import (
    ANALYSIS_DELAY,
    ANALYSIS_LOOKBACK_MONTHS,
    COMPREHENSIVE_STAY_PROMPT_VERSION,
    GUEST_EXPERIENCE_LOCK_ID,
    GUEST_REVIEW_ISSUE_PROMPT_VERSION,
    analysis_window,
    build_review_input,
    build_stay_input,
    is_analysis_eligible,
    normalize_review_result,
    normalize_stay_result,
    normalize_utc,
)
from brain.models import (
    ComprehensiveStayAnalysis,
    GuestExperienceAnalysisRun,
    GuestReviewIssueAnalysis,
    PropertyGuestIssue,
    as_json_safe,
    get_session as get_brain_session,
    init_guest_experience_tables,
)
from brain.scoring import is_confirmed_reservation_status
from database.models import (
    Conversation,
    Listing,
    MessageMetadata,
    Reservation,
    Review,
    get_session as get_main_session,
    init_models,
)
from sync.api_client import HostawayAPIClient
from sync.sync_messages import message_id_for_payload, parse_timestamp_from_api

CODEX_ANALYSIS_PROVIDER = "codex-subscription"
BATCH_SCHEMA_VERSION = 1
RESULT_SCHEMA_VERSION = 1
INFLIGHT_TTL = timedelta(hours=6)


class CodexGuestExperienceBatchService:
    """Export and import bounded local batches without any API model client."""

    def __init__(self, *, brain_session=None, main_session=None):
        self.brain_session = brain_session or get_brain_session()
        self.main_session = main_session or get_main_session("")
        self._owns_brain_session = brain_session is None
        self._owns_main_session = main_session is None

    def close(self):
        if self._owns_brain_session:
            self.brain_session.close()
        if self._owns_main_session:
            self.main_session.close()

    def export_batch(
        self,
        *,
        reference_time: datetime | None = None,
        max_stays: int = 8,
        max_reviews: int = 16,
    ) -> dict[str, Any]:
        """Reserve and export one local Codex analysis batch."""
        now = normalize_utc(reference_time or _utcnow())
        max_stays = max(1, int(max_stays))
        max_reviews = max(1, int(max_reviews))
        window_start, window_end = analysis_window(now)

        self._lock()
        inflight_stays, inflight_reviews = self._expire_and_collect_inflight(now)
        run = GuestExperienceAnalysisRun(
            status="preparing",
            window_start_at=window_start,
            window_end_at=window_end,
            started_at=now,
        )
        self.brain_session.add(run)
        self.brain_session.flush()

        stay_rows, stay_meta = self._eligible_stays(
            now,
            excluded_ids=inflight_stays,
            limit=max_stays,
        )
        review_rows, review_meta = self._eligible_reviews(
            now,
            excluded_ids=inflight_reviews,
            limit=max_reviews,
        )

        exported_stays: list[dict[str, Any]] = []
        muted_count = 0
        for reservation, listing, prepared in stay_rows:
            if prepared["guest_message_count"] == 0:
                self._store_stay(
                    run.run_id,
                    reservation,
                    prepared,
                    {
                        "stay_quality": "muted",
                        "summary": "Muted stay — the guest sent no messages.",
                        "detailed_summary": (
                            "No guest communication was captured for this stay. "
                            f"The conversation contains {prepared['message_count']} support or automated messages."
                        ),
                        "issues": [],
                    },
                    model=None,
                )
                muted_count += 1
                continue
            exported_stays.append(prepared["payload"])

        exported_reviews = [prepared["payload"] for _, prepared in review_rows]
        details = {
            "execution_provider": CODEX_ANALYSIS_PROVIDER,
            "schema_version": BATCH_SCHEMA_VERSION,
            "exported_at": now.isoformat(),
            "stay_ids": [row["reservation_id"] for row in exported_stays],
            "review_ids": [row["review_id"] for row in exported_reviews],
            "stay_input_hashes": {
                str(reservation.reservation_id): prepared["input_hash"]
                for reservation, _, prepared in stay_rows
                if prepared["guest_message_count"] > 0
            },
            "review_input_hashes": {
                str(review.review_id): prepared["input_hash"]
                for review, prepared in review_rows
            },
            "muted_stays_analyzed": muted_count,
            "stays": stay_meta,
            "reviews": review_meta,
        }
        run.status = "awaiting_analysis" if exported_stays or exported_reviews else "completed"
        run.eligible_stay_count = stay_meta["eligible"]
        run.stays_analyzed = muted_count
        run.stays_already_analyzed = stay_meta["already_analyzed"]
        run.reviews_already_analyzed = review_meta["already_analyzed"]
        run.details = as_json_safe(details)
        if run.status == "completed":
            run.completed_at = _utcnow()
        self.brain_session.commit()

        return {
            "schema_version": BATCH_SCHEMA_VERSION,
            "run_id": run.run_id,
            "analysis_provider": CODEX_ANALYSIS_PROVIDER,
            "window": {
                "start_at": window_start.isoformat(),
                "end_at": window_end.isoformat(),
                "checkout_delay_hours": 24,
                "lookback_calendar_months": ANALYSIS_LOOKBACK_MONTHS,
            },
            "instructions": {
                "stay_qualities": ["smooth", "recovered", "unresolved", "muted"],
                "complaint_evidence": "Every stay issue must cite at least one guest message as complaint evidence.",
                "review_evidence": "Every review issue must cite the review ID and exact public/private/rating source part.",
            },
            "stays": exported_stays,
            "reviews": exported_reviews,
            "local_results": {"muted_stays_analyzed": muted_count},
            "backlog": {
                "stays": stay_meta["backlog"],
                "reviews": review_meta["backlog"],
            },
        }

    def import_results(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Validate and import one Codex-produced batch result."""
        if int(payload.get("schema_version") or 0) != RESULT_SCHEMA_VERSION:
            raise ValueError(f"Unsupported result schema_version: {payload.get('schema_version')!r}")
        run_id = int(payload["run_id"])
        now = _utcnow()

        self._lock()
        run = self.brain_session.query(GuestExperienceAnalysisRun).filter(
            GuestExperienceAnalysisRun.run_id == run_id
        ).first()
        if not run:
            raise ValueError(f"Unknown guest-experience run_id {run_id}")
        if run.status not in {"awaiting_analysis", "partial"}:
            raise ValueError(f"Run {run_id} is not awaiting analysis (status={run.status})")

        details = dict(run.details or {})
        expected_stays = {int(value) for value in details.get("stay_ids") or []}
        expected_reviews = {int(value) for value in details.get("review_ids") or []}
        exported_at = normalize_utc(datetime.fromisoformat(details["exported_at"]))
        stay_results = {
            int(row["reservation_id"]): row
            for row in payload.get("stays") or []
            if row.get("reservation_id") is not None
        }
        review_results = {
            int(row["review_id"]): row
            for row in payload.get("reviews") or []
            if row.get("review_id") is not None
        }
        if set(stay_results) - expected_stays:
            raise ValueError("Result contains a reservation that was not exported in this run")
        if set(review_results) - expected_reviews:
            raise ValueError("Result contains a review that was not exported in this run")

        reservations = {
            int(row.reservation_id): row
            for row in self.main_session.query(Reservation)
            .filter(Reservation.reservation_id.in_(expected_stays or [-1]))
            .all()
        }
        listing_ids = {int(row.listing_id) for row in reservations.values()}
        reviews = {
            int(row.review_id): row
            for row in self.main_session.query(Review)
            .filter(Review.review_id.in_(expected_reviews or [-1]))
            .all()
        }
        listing_ids.update(int(row.listing_id) for row in reviews.values())
        listings = {
            int(row.listing_id): row
            for row in self.main_session.query(Listing)
            .filter(Listing.listing_id.in_(listing_ids or [-1]))
            .all()
        }
        messages = (
            self.main_session.query(MessageMetadata)
            .filter(MessageMetadata.reservation_id.in_(expected_stays or [-1]))
            .order_by(MessageMetadata.reservation_id, MessageMetadata.created_at, MessageMetadata.message_id)
            .all()
        )
        messages_by_reservation: dict[int, list[Any]] = defaultdict(list)
        for message in messages:
            messages_by_reservation[int(message.reservation_id)].append(message)

        analyzed_stays = analyzed_reviews = already_analyzed = errors = 0
        for reservation_id in sorted(expected_stays):
            reservation = reservations.get(reservation_id)
            raw_result = stay_results.get(reservation_id)
            if not reservation or not raw_result:
                errors += 1
                continue
            if self._stay_exists(reservation_id):
                already_analyzed += 1
                continue
            prepared = build_stay_input(
                reservation,
                listings.get(int(reservation.listing_id)),
                messages_by_reservation.get(reservation_id, []),
                analyzed_at=exported_at,
            )
            expected_hash = (details.get("stay_input_hashes") or {}).get(str(reservation_id))
            if expected_hash != prepared["input_hash"]:
                errors += 1
                continue
            directions = {
                message["message_id"]: message["direction"]
                for message in prepared["payload"]["messages"]
            }
            normalized = normalize_stay_result(raw_result, valid_messages=directions)
            self._store_stay(
                run_id,
                reservation,
                prepared,
                normalized,
                model=CODEX_ANALYSIS_PROVIDER,
            )
            analyzed_stays += 1

        reservation_lookup = {
            int(row.reservation_id): row
            for row in self.main_session.query(Reservation)
            .filter(Reservation.reservation_id.in_({int(row.reservation_id) for row in reviews.values() if row.reservation_id} or [-1]))
            .all()
        }
        for review_id in sorted(expected_reviews):
            review = reviews.get(review_id)
            raw_result = review_results.get(review_id)
            if not review or not raw_result:
                errors += 1
                continue
            if self._review_exists(review_id):
                already_analyzed += 1
                continue
            prepared = build_review_input(review)
            expected_hash = (details.get("review_input_hashes") or {}).get(str(review_id))
            if expected_hash != prepared["input_hash"]:
                errors += 1
                continue
            normalized = normalize_review_result(raw_result, review_id=review_id)
            self._store_review(
                run_id,
                review,
                prepared,
                normalized,
                reservation_lookup,
            )
            analyzed_reviews += 1

        missing = (len(expected_stays) - len(stay_results)) + (len(expected_reviews) - len(review_results))
        errors += max(missing, 0)
        run.status = "completed" if errors == 0 else "partial"
        run.stays_analyzed = int(run.stays_analyzed or 0) + analyzed_stays
        run.reviews_analyzed = int(run.reviews_analyzed or 0) + analyzed_reviews
        run.error_count = errors
        details["imported_at"] = now.isoformat()
        details["import"] = {
            "stays_analyzed": analyzed_stays,
            "reviews_analyzed": analyzed_reviews,
            "already_analyzed": already_analyzed,
            "errors": errors,
        }
        run.details = as_json_safe(details)
        run.completed_at = now
        self.brain_session.commit()
        return {
            "status": run.status,
            "run_id": run_id,
            "stays_analyzed": analyzed_stays,
            "reviews_analyzed": analyzed_reviews,
            "already_analyzed": already_analyzed,
            "errors": errors,
        }

    def _lock(self):
        if self.brain_session.get_bind().dialect.name == "postgresql":
            self.brain_session.execute(
                text("SELECT pg_advisory_xact_lock(:lock_id)"),
                {"lock_id": GUEST_EXPERIENCE_LOCK_ID},
            )

    def _expire_and_collect_inflight(self, now: datetime) -> tuple[set[int], set[int]]:
        stay_ids: set[int] = set()
        review_ids: set[int] = set()
        rows = self.brain_session.query(GuestExperienceAnalysisRun).filter(
            GuestExperienceAnalysisRun.status == "awaiting_analysis"
        ).all()
        for row in rows:
            if row.started_at and row.started_at < now - INFLIGHT_TTL:
                row.status = "expired"
                row.completed_at = now
                continue
            details = row.details or {}
            stay_ids.update(int(value) for value in details.get("stay_ids") or [])
            review_ids.update(int(value) for value in details.get("review_ids") or [])
        self.brain_session.flush()
        return stay_ids, review_ids

    def _eligible_stays(self, now: datetime, *, excluded_ids: set[int], limit: int):
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
        listing_ids = {int(row.listing_id) for row in candidates}
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
            int(row[0]) for row in self.brain_session.query(ComprehensiveStayAnalysis.reservation_id)
            .filter(ComprehensiveStayAnalysis.reservation_id.in_(eligible_ids or [-1]))
            .all()
        }
        pending = [
            row for row in eligible
            if int(row.reservation_id) not in existing_ids and int(row.reservation_id) not in excluded_ids
        ][:limit]
        self._hydrate_pending_messages(pending)
        pending_ids = [int(row.reservation_id) for row in pending]
        messages = (
            self.main_session.query(MessageMetadata)
            .filter(MessageMetadata.reservation_id.in_(pending_ids or [-1]))
            .order_by(MessageMetadata.reservation_id, MessageMetadata.created_at, MessageMetadata.message_id)
            .all()
        )
        grouped: dict[int, list[Any]] = defaultdict(list)
        for message in messages:
            grouped[int(message.reservation_id)].append(message)
        rows = []
        for reservation in pending:
            listing = listings.get(int(reservation.listing_id))
            rows.append((
                reservation,
                listing,
                build_stay_input(
                    reservation,
                    listing,
                    grouped.get(int(reservation.reservation_id), []),
                    analyzed_at=now,
                ),
            ))
        return rows, {
            "eligible": len(eligible),
            "already_analyzed": len(existing_ids),
            "inflight": len(set(eligible_ids) & excluded_ids),
            "exported_or_local": len(pending),
            "backlog": max(len(eligible) - len(existing_ids) - len(set(eligible_ids) & excluded_ids) - len(pending), 0),
        }

    def _hydrate_pending_messages(self, reservations: list[Reservation]):
        """Refresh complete message bodies for the exact stays about to be exported."""
        if not reservations:
            return
        client = HostawayAPIClient()
        for reservation in reservations:
            conversation_payloads = client.get_conversations(
                reservation_id=int(reservation.reservation_id),
                limit=500,
            )
            for conversation_payload in conversation_payloads:
                conversation_id = conversation_payload.get("id")
                if conversation_id is None:
                    continue
                conversation_id = int(conversation_id)
                conversation = self.main_session.query(Conversation).filter(
                    Conversation.conversation_id == conversation_id
                ).first()
                if not conversation:
                    conversation = Conversation(
                        conversation_id=conversation_id,
                        reservation_id=reservation.reservation_id,
                        listing_id=reservation.listing_id,
                        guest_id=reservation.guest_id,
                        channel_id=conversation_payload.get("channelId"),
                        communication_type=conversation_payload.get("communicationType"),
                        status=conversation_payload.get("status"),
                    )
                    self.main_session.add(conversation)
                    self.main_session.flush()
                else:
                    conversation.reservation_id = reservation.reservation_id
                    conversation.listing_id = reservation.listing_id
                    conversation.guest_id = reservation.guest_id

                messages = client.get_all_conversation_messages(conversation_id, limit=500)
                existing = {
                    int(row.message_id): row
                    for row in self.main_session.query(MessageMetadata)
                    .filter(MessageMetadata.conversation_id == conversation_id)
                    .all()
                }
                for raw_message in messages:
                    created_at = parse_timestamp_from_api(raw_message.get("date") or "")
                    if not created_at:
                        continue
                    message_id = message_id_for_payload(conversation_id, raw_message, created_at)
                    is_incoming = bool(raw_message.get("isIncoming", False))
                    body = raw_message.get("body") or raw_message.get("content") or ""
                    row = existing.get(int(message_id))
                    if not row:
                        row = MessageMetadata(
                            message_id=message_id,
                            conversation_id=conversation_id,
                        )
                        self.main_session.add(row)
                        existing[int(message_id)] = row
                    row.reservation_id = reservation.reservation_id
                    row.listing_id = reservation.listing_id
                    row.guest_id = reservation.guest_id
                    row.sender_type = "guest" if is_incoming else "host"
                    row.sender_name = "Guest" if is_incoming else (
                        "Host (Automated)"
                        if str(raw_message.get("communicationType") or "").lower() == "automation"
                        or "automation" in str(raw_message.get("messageSource") or "").lower()
                        else "Host"
                    )
                    row.is_incoming = 1 if is_incoming else 0
                    row.message_type = str(raw_message.get("type") or "text")
                    row.content_preview = str(body)
                    row.has_attachment = 1 if raw_message.get("hasAttachment") else 0
                    row.created_at = created_at
                conversation.message_count = len(messages)
                if messages:
                    timestamps = [
                        parse_timestamp_from_api(message.get("date") or "")
                        for message in messages
                    ]
                    timestamps = [value for value in timestamps if value]
                    if timestamps:
                        conversation.first_message_at = min(timestamps)
                        conversation.last_message_at = max(timestamps)
                conversation.last_synced_at = _utcnow()
        self.main_session.commit()

    def _eligible_reviews(self, now: datetime, *, excluded_ids: set[int], limit: int):
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
        listing_ids = {int(row.listing_id) for row in reviews}
        listings = {
            int(row.listing_id): row
            for row in self.main_session.query(Listing)
            .filter(Listing.listing_id.in_(listing_ids or [-1]))
            .all()
        }

        def in_window(review: Review) -> bool:
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

        eligible = [row for row in reviews if in_window(row)]
        eligible_ids = [int(row.review_id) for row in eligible]
        existing_ids = {
            int(row[0]) for row in self.brain_session.query(GuestReviewIssueAnalysis.review_id)
            .filter(GuestReviewIssueAnalysis.review_id.in_(eligible_ids or [-1]))
            .all()
        }
        pending = [
            row for row in eligible
            if int(row.review_id) not in existing_ids and int(row.review_id) not in excluded_ids
        ][:limit]
        return [(review, build_review_input(review)) for review in pending], {
            "eligible": len(eligible),
            "already_analyzed": len(existing_ids),
            "inflight": len(set(eligible_ids) & excluded_ids),
            "exported": len(pending),
            "backlog": max(len(eligible) - len(existing_ids) - len(set(eligible_ids) & excluded_ids) - len(pending), 0),
        }

    def _stay_exists(self, reservation_id: int) -> bool:
        return self.brain_session.query(ComprehensiveStayAnalysis.stay_analysis_id).filter(
            ComprehensiveStayAnalysis.reservation_id == reservation_id
        ).first() is not None

    def _review_exists(self, review_id: int) -> bool:
        return self.brain_session.query(GuestReviewIssueAnalysis.review_analysis_id).filter(
            GuestReviewIssueAnalysis.review_id == review_id
        ).first() is not None

    def _store_stay(self, run_id, reservation, prepared, result, *, model):
        if self._stay_exists(int(reservation.reservation_id)):
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
            model=model,
            source_metadata=as_json_safe({
                "analysis_provider": CODEX_ANALYSIS_PROVIDER,
                "timezone_name": prepared["timezone_name"],
                "timezone_source": prepared["timezone_source"],
                "analysis_lookback_months": ANALYSIS_LOOKBACK_MONTHS,
                "analysis_delay_hours": 24,
            }),
            analyzed_at=_utcnow(),
        )
        self.brain_session.add(row)
        self.brain_session.flush()
        for index, issue in enumerate(result["issues"]):
            self.brain_session.add(PropertyGuestIssue(
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

    def _store_review(self, run_id, review, prepared, result, reservations):
        if self._review_exists(int(review.review_id)):
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
            model=CODEX_ANALYSIS_PROVIDER,
            source_metadata=as_json_safe({
                "analysis_provider": CODEX_ANALYSIS_PROVIDER,
                "sub_rating_count": prepared["sub_rating_count"],
                "overall_rating": review.overall_rating,
                "analysis_lookback_months": ANALYSIS_LOOKBACK_MONTHS,
            }),
            analyzed_at=_utcnow(),
        )
        self.brain_session.add(row)
        self.brain_session.flush()
        reservation = reservations.get(int(review.reservation_id)) if review.reservation_id else None
        source_date = review.review_date or (
            reservation.departure_date if reservation and reservation.departure_date else date.today()
        )
        for index, issue in enumerate(result["issues"]):
            self.brain_session.add(PropertyGuestIssue(
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


def _write_private_json(path: str, payload: dict[str, Any]):
    output_path = Path(path).expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(output_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, default=str)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def main():
    parser = argparse.ArgumentParser(description="Local Codex guest-experience batch bridge")
    subparsers = parser.add_subparsers(dest="action", required=True)
    export_parser = subparsers.add_parser("export")
    export_parser.add_argument("--max-stays", type=int, default=8)
    export_parser.add_argument("--max-reviews", type=int, default=16)
    export_parser.add_argument("--output", required=True)
    import_parser = subparsers.add_parser("import")
    import_parser.add_argument("--results", required=True)
    import_parser.add_argument("--batch")
    import_parser.add_argument("--cleanup", action="store_true")
    args = parser.parse_args()

    init_models(None)
    init_guest_experience_tables()
    service = CodexGuestExperienceBatchService()
    try:
        if args.action == "export":
            payload = service.export_batch(
                max_stays=args.max_stays,
                max_reviews=args.max_reviews,
            )
            _write_private_json(args.output, payload)
            print(json.dumps({
                "run_id": payload["run_id"],
                "stays_exported": len(payload["stays"]),
                "reviews_exported": len(payload["reviews"]),
                "muted_stays_analyzed": payload["local_results"]["muted_stays_analyzed"],
                "backlog": payload["backlog"],
                "output": str(Path(args.output).expanduser().resolve()),
            }, indent=2))
        else:
            results_path = Path(args.results).expanduser().resolve()
            with results_path.open("r", encoding="utf-8") as handle:
                result = service.import_results(json.load(handle))
            print(json.dumps(result, indent=2))
            if args.cleanup:
                results_path.unlink(missing_ok=True)
                if args.batch:
                    Path(args.batch).expanduser().resolve().unlink(missing_ok=True)
    finally:
        service.close()


if __name__ == "__main__":
    main()
