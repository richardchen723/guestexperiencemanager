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
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any


def _load_runtime_environment():
    """Load the app environment before database models choose PostgreSQL types."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    project_root = Path(__file__).resolve().parent.parent
    configured = os.getenv("HOSTAWAY_ENV_FILE")
    candidates = [Path(configured).expanduser()] if configured else []
    candidates.extend((project_root / ".env", project_root.parent / ".env"))
    for candidate in candidates:
        if candidate.is_file():
            load_dotenv(candidate)
            return


_load_runtime_environment()

from sqlalchemy import func, text

from brain.guest_experience import (
    COMPREHENSIVE_STAY_PROMPT_VERSION,
    GUEST_EXPERIENCE_LOCK_ID,
    GUEST_REVIEW_ISSUE_PROMPT_VERSION,
    build_review_input,
    build_stay_input,
    is_analysis_eligible,
    normalize_review_result,
    normalize_stay_result,
    normalize_utc,
    STAY_SCAN_LOOKBACK,
    REVIEW_SCAN_LOOKBACK,
    SCAN_TIMEZONE,
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
from brain.guest_experience_replication import (
    GuestExperienceReplicationError,
    GuestExperienceReplicationService,
    ProductionSshClient,
    ProductionSshConfig,
)
from brain.scoring import is_confirmed_reservation_status
from brain.guest_issue_identity import (
    COMPLAINT_MATCHING_INSTRUCTIONS,
    COMPLAINT_MATCHING_VERSION,
    apply_complaint_assignments,
    backfill_complaint_identities,
    complaint_catalog,
)
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
from brain.guest_scan_state import RESCAN_INSTRUCTIONS, existing_issues, issue_context, merge_issue, scan_version

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
        self._touched_issue_keys = set()

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
        window_start, window_end = now - STAY_SCAN_LOOKBACK, now
        self._touched_issue_keys = set()

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
        catalog = complaint_catalog(self.brain_session, sorted(
            {int(reservation.listing_id) for reservation, _, _ in stay_rows}
            | {int(review.listing_id) for review, _ in review_rows}
        )) if exported_stays or exported_reviews else []
        details = {
            "execution_provider": CODEX_ANALYSIS_PROVIDER,
            "scan_policy_version": 2,
            "schema_version": BATCH_SCHEMA_VERSION,
            "complaint_matching_version": COMPLAINT_MATCHING_VERSION,
            "complaint_catalog_pending": [
                {key: row[key] for key in ("source_kind", "source_issue_key", "input_hash")}
                for row in catalog if not row["complaint_key"]
            ],
            "exported_at": now.isoformat(),
            "stay_ids": [row["reservation_id"] for row in exported_stays],
            "review_ids": [row["review_id"] for row in exported_reviews],
            "stay_input_hashes": {
                str(reservation.reservation_id): prepared["input_hash"]
                for reservation, _, prepared in stay_rows
                if prepared["guest_message_count"] > 0
            },
            "previous_stay_hashes": {
                str(reservation.reservation_id): prepared.get("previous_input_hash")
                for reservation, _, prepared in stay_rows
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
        run.details = dict(run.details) | {"touched_issue_keys": sorted(self._touched_issue_keys)}
        if run.status == "completed":
            run.completed_at = _utcnow()
            if muted_count:
                GuestExperienceReplicationService(self.brain_session).seal_run(run.run_id)
        self.brain_session.commit()

        return {
            "schema_version": BATCH_SCHEMA_VERSION,
            "run_id": run.run_id,
            "analysis_provider": CODEX_ANALYSIS_PROVIDER,
            "window": {
                "start_at": window_start.isoformat(),
                "end_at": window_end.isoformat(),
                "checkout_delay_hours": 0,
                "stay_lookback_hours": 72,
                "include_departures_today": True,
                "review_lookback_hours": 36,
                "timezone": "America/New_York",
            },
            "instructions": {
                "stay_qualities": ["smooth", "recovered", "unresolved", "muted"],
                "complaint_evidence": "Every stay issue must cite at least one guest message as complaint evidence.",
                "review_evidence": "Every review issue must cite the review ID and exact public/private/rating source part.",
                "complaint_matching": COMPLAINT_MATCHING_INSTRUCTIONS,
                "rescanning": RESCAN_INSTRUCTIONS,
            },
            "complaint_catalog": catalog,
            "stays": exported_stays,
            "reviews": exported_reviews,
            "local_results": {"muted_stays_analyzed": muted_count},
            "backlog": {
                "stays": stay_meta["backlog"],
                "reviews": review_meta["backlog"],
            },
        }

    def import_results(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            return self._import_results(payload)
        except Exception:
            self.brain_session.rollback()
            raise

    def _import_results(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Validate and import one Codex-produced batch result."""
        if int(payload.get("schema_version") or 0) != RESULT_SCHEMA_VERSION:
            raise ValueError(f"Unsupported result schema_version: {payload.get('schema_version')!r}")
        run_id = int(payload["run_id"])
        now = _utcnow()

        self._lock()
        self._touched_issue_keys = set()
        run = self.brain_session.query(GuestExperienceAnalysisRun).filter(
            GuestExperienceAnalysisRun.run_id == run_id
        ).first()
        if not run:
            raise ValueError(f"Unknown guest-experience run_id {run_id}")
        if run.status not in {"awaiting_analysis", "partial"}:
            raise ValueError(f"Run {run_id} is not awaiting analysis (status={run.status})")

        details = dict(run.details or {})
        # Validate identities before storing any analyses. Older in-flight batches
        # retain their original contract; newly exported batches require matching.
        if details.get("complaint_matching_version"):
            for kind in ("stays", "reviews"):
                for result in payload.get(kind) or []:
                    for issue in result.get("issues") or []:
                        if not issue.get("complaint_key"):
                            raise ValueError("Every issue requires complaint_key; follow the batch's complaint_matching instructions")
            expected = {
                (row["source_kind"], row["source_issue_key"]): row["input_hash"]
                for row in details.get("complaint_catalog_pending", [])
            }
            apply_complaint_assignments(
                self.brain_session, payload.get("complaint_assignments", []), expected=expected,
            )
            details["complaint_assignments"] = payload.get("complaint_assignments", [])
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
        if set(stay_results) != expected_stays or set(review_results) != expected_reviews:
            raise ValueError("Return every exported stay and review; incomplete batches are not marked scanned")

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
                raise ValueError("An exported reservation or result is missing")
            prepared = build_stay_input(
                reservation,
                listings.get(int(reservation.listing_id)),
                messages_by_reservation.get(reservation_id, []),
                analyzed_at=exported_at,
            )
            expected_hash = (details.get("stay_input_hashes") or {}).get(str(reservation_id))
            if expected_hash != prepared["input_hash"]:
                raise ValueError("Stay evidence changed since export; export a fresh batch")
            previous = self.brain_session.query(ComprehensiveStayAnalysis).filter_by(reservation_id=reservation_id).first()
            previous_hash = (details.get("previous_stay_hashes") or {}).get(str(reservation_id))
            if (previous.input_hash if previous else None) != previous_hash:
                raise ValueError("Stay was scanned by another batch; export a fresh batch")
            directions = {
                message["message_id"]: message["direction"]
                for message in prepared["payload"]["messages"]
            }
            normalized = normalize_stay_result(raw_result, valid_messages=directions)
            if len(normalized["issues"]) != len(raw_result.get("issues") or []):
                raise ValueError("Every stay issue must cite a valid guest complaint message")
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
                raise ValueError("An exported review or result is missing")
            if self._review_exists(review_id):
                already_analyzed += 1
                continue
            prepared = build_review_input(review)
            expected_hash = (details.get("review_input_hashes") or {}).get(str(review_id))
            if expected_hash != prepared["input_hash"]:
                raise ValueError("Review evidence changed since export; export a fresh batch")
            normalized = normalize_review_result(raw_result, review_id=review_id)
            if len(normalized["issues"]) != len(raw_result.get("issues") or []):
                raise ValueError("Every review issue must cite valid review evidence")
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
        details["touched_issue_keys"] = sorted(self._touched_issue_keys | set(details.get("touched_issue_keys", [])))
        details["import"] = {
            "stays_analyzed": analyzed_stays,
            "reviews_analyzed": analyzed_reviews,
            "already_analyzed": already_analyzed,
            "errors": errors,
        }
        run.details = as_json_safe(details)
        run.completed_at = now
        self.brain_session.flush()
        GuestExperienceReplicationService(self.brain_session).seal_run(run_id)
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

    def _retry_ids(self, kind):
        identifiers = set()
        for run in self.brain_session.query(GuestExperienceAnalysisRun).filter(
            GuestExperienceAnalysisRun.status.in_(("expired", "partial"))
        ).all():
            if (run.details or {}).get("scan_policy_version") == 2:
                identifiers.update(set((run.details or {}).get(kind, [])) - set((run.details or {}).get("retried_" + kind, [])))
        return identifiers

    def _finish_retry(self, kind, identifier):
        for run in self.brain_session.query(GuestExperienceAnalysisRun).filter(
            GuestExperienceAnalysisRun.status.in_(("expired", "partial"))
        ).all():
            details = dict(run.details or {})
            if identifier in details.get(kind, []):
                details["retried_" + kind] = sorted(set(details.get("retried_" + kind, [])) | {identifier})
                run.details = details

    def _eligible_stays(self, now: datetime, *, excluded_ids: set[int], limit: int):
        retry_ids = self._retry_ids("stay_ids")
        today = now.replace(tzinfo=timezone.utc).astimezone(SCAN_TIMEZONE).date()
        candidates = self.main_session.query(Reservation).filter(
            Reservation.arrival_date.isnot(None), Reservation.departure_date.isnot(None),
            ((Reservation.departure_date >= (now - STAY_SCAN_LOOKBACK).date() - timedelta(days=1))
             & (Reservation.departure_date <= today)) | Reservation.reservation_id.in_(retry_ids or [-1]),
        ).order_by(Reservation.departure_date, Reservation.reservation_id).all()
        listings = {row.listing_id: row for row in self.main_session.query(Listing).filter(
            Listing.listing_id.in_({row.listing_id for row in candidates} or [-1])
        ).all()}
        eligible = [row for row in candidates if is_confirmed_reservation_status(row.status)
                    and str(getattr(listings.get(row.listing_id), "status", "")).lower() != "deleted"
                    and (row.reservation_id in retry_ids or is_analysis_eligible(
                        row, listings.get(row.listing_id), reference_time=now))]
        # Refresh BEFORE comparing message IDs. New support replies matter too.
        available = [row for row in eligible if row.reservation_id not in excluded_ids]
        self._hydrate_pending_messages(available)
        grouped = defaultdict(list)
        for message in self.main_session.query(MessageMetadata).filter(
            MessageMetadata.reservation_id.in_([row.reservation_id for row in available] or [-1])
        ).all():
            grouped[message.reservation_id].append(message)
        previous = {row.reservation_id: row for row in self.brain_session.query(ComprehensiveStayAnalysis).filter(
            ComprehensiveStayAnalysis.reservation_id.in_([row.reservation_id for row in available] or [-1])
        ).all()}
        pending, unchanged = [], 0
        for reservation in available:
            listing = listings.get(reservation.listing_id)
            prepared = build_stay_input(reservation, listing, grouped[reservation.reservation_id], analyzed_at=now)
            prior = previous.get(reservation.reservation_id)
            new_ids = set(prepared["message_ids"]) - set(prior.source_message_ids or []) if prior else set(prepared["message_ids"])
            if prior and not new_ids:
                unchanged += 1
                self._finish_retry("stay_ids", reservation.reservation_id)
                continue
            prepared["previous_input_hash"] = prior.input_hash if prior else None
            prepared["payload"]["new_message_ids"] = sorted(new_ids)
            prepared["payload"]["previous_scan_at"] = prior.analyzed_at.isoformat() if prior else None
            prepared["payload"]["existing_issues"] = issue_context(existing_issues(
                self.brain_session, reservation.listing_id, reservation.reservation_id))
            pending.append((reservation, listing, prepared))
        return pending[:limit], {
            "eligible": len(eligible), "already_analyzed": unchanged,
            "inflight": len(eligible) - len(available), "exported_or_local": min(limit, len(pending)),
            "backlog": max(len(pending) - limit, 0),
        }

    def _hydrate_pending_messages(self, reservations: list[Reservation]):
        """Refresh complete message bodies for the exact stays about to be exported."""
        if not reservations:
            return
        client = HostawayAPIClient()
        for reservation in reservations:
            conversation_payloads = []
            offset = 0
            while True:
                page = client.get_conversations_page(
                    reservation_id=int(reservation.reservation_id), limit=100, offset=offset,
                )
                if page is None:
                    raise RuntimeError(f"Conversation refresh failed for stay {reservation.reservation_id}")
                conversation_payloads.extend(page)
                if len(page) < 100:
                    break
                offset += 100
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
        retry_ids = self._retry_ids("review_ids")
        reviews = self.main_session.query(Review).filter(
            func.lower(func.coalesce(Review.origin, "")) == "guest",
            func.lower(func.coalesce(Review.status, "")).in_(("submitted", "published")),
        ).order_by(Review.posted_at.asc().nulls_last(), Review.review_id).all()
        eligible = [row for row in reviews if row.review_id in retry_ids or (
            row.posted_at and now - REVIEW_SCAN_LOOKBACK <= normalize_utc(row.posted_at) <= now)]
        existing_ids = {row[0] for row in self.brain_session.query(GuestReviewIssueAnalysis.review_id).filter(
            GuestReviewIssueAnalysis.review_id.in_([row.review_id for row in eligible] or [-1])
        ).all()}
        pending = [row for row in eligible if row.review_id not in existing_ids and row.review_id not in excluded_ids]
        rows = []
        for review in pending[:limit]:
            prepared = build_review_input(review)
            prepared["payload"]["existing_issues"] = issue_context(existing_issues(
                self.brain_session, review.listing_id, review.reservation_id, review.review_id))
            rows.append((review, prepared))
        return rows, {
            "eligible": len(eligible), "already_analyzed": len(existing_ids),
            "inflight": sum(row.review_id in excluded_ids for row in eligible),
            "exported": len(rows), "backlog": max(len(pending) - limit, 0),
            "unknown_posted_at": sum(row.posted_at is None for row in reviews),
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
        row = self.brain_session.query(ComprehensiveStayAnalysis).filter_by(
            reservation_id=reservation.reservation_id).first()
        previous_ids = set(row.source_message_ids or []) if row else None
        version = scan_version(row) + 1
        if row and row.run_id and row.run_id != run_id:
            GuestExperienceReplicationService(self.brain_session).seal_run(row.run_id)
        if not row:
            row = ComprehensiveStayAnalysis(reservation_id=reservation.reservation_id)
            self.brain_session.add(row)
        for name, value in {
            "run_id": run_id, "listing_id": reservation.listing_id,
            "arrival_date": reservation.arrival_date, "departure_date": reservation.departure_date,
            "checkout_at": prepared["checkout_at"], "eligible_at": prepared["eligible_at"],
            "stay_quality": result["stay_quality"], "summary": result["summary"],
            "detailed_summary": result["detailed_summary"], "issue_count": len(result["issues"]),
            "message_count": prepared["message_count"], "guest_message_count": prepared["guest_message_count"],
            "source_message_ids": prepared["message_ids"], "input_hash": prepared["input_hash"],
            "prompt_version": COMPREHENSIVE_STAY_PROMPT_VERSION, "model": model, "analyzed_at": _utcnow(),
            "source_metadata": {"analysis_provider": CODEX_ANALYSIS_PROVIDER, "scan_version": version,
                                "timezone_name": prepared["timezone_name"], "timezone_source": prepared["timezone_source"],
                                "stay_lookback_hours": 72, "analysis_delay_hours": 0},
        }.items():
            setattr(row, name, value)
        self.brain_session.flush()
        for issue in result["issues"]:
            merge_issue(self, run_id=run_id, source_kind="stay", listing_id=reservation.listing_id,
                        reservation_id=reservation.reservation_id, review_id=None,
                        analysis_id=row.stay_analysis_id, source_date=reservation.departure_date,
                        issue=issue, previous_message_ids=previous_ids)
        all_issues = self.brain_session.query(PropertyGuestIssue).filter_by(stay_analysis_id=row.stay_analysis_id).all()
        row.issue_count = len(all_issues)
        row.stay_quality = ("muted" if not row.guest_message_count else "smooth" if not all_issues else
                            "unresolved" if any(issue.resolution_state != "resolved" for issue in all_issues) else "recovered")
        self._finish_retry("stay_ids", reservation.reservation_id)
        return True

    def _store_review(self, run_id, review, prepared, result, reservations):
        if self._review_exists(int(review.review_id)):
            return False
        row = GuestReviewIssueAnalysis(
            run_id=run_id, review_id=review.review_id, listing_id=review.listing_id,
            reservation_id=review.reservation_id, review_date=review.review_date,
            summary=result["summary"], issue_count=len(result["issues"]),
            has_public_review=prepared["has_public_review"], has_private_feedback=prepared["has_private_feedback"],
            input_hash=prepared["input_hash"], prompt_version=GUEST_REVIEW_ISSUE_PROMPT_VERSION,
            model=CODEX_ANALYSIS_PROVIDER, source_metadata={
                "analysis_provider": CODEX_ANALYSIS_PROVIDER, "sub_rating_count": prepared["sub_rating_count"],
                "overall_rating": review.overall_rating, "review_lookback_hours": 36,
                "posted_at": getattr(review, "posted_at", None).isoformat() if getattr(review, "posted_at", None) else None,
            }, analyzed_at=_utcnow(),
        )
        self.brain_session.add(row)
        self.brain_session.flush()
        posted_at = getattr(review, "posted_at", None)
        source_date = posted_at.replace(tzinfo=timezone.utc).astimezone(SCAN_TIMEZONE).date() if posted_at else (review.review_date or _utcnow().date())
        for issue in result["issues"]:
            merge_issue(self, run_id=run_id, source_kind="review", listing_id=review.listing_id,
                        reservation_id=review.reservation_id, review_id=review.review_id,
                        analysis_id=row.review_analysis_id, source_date=source_date, issue=issue)
        row.issue_count = self.brain_session.query(PropertyGuestIssue).filter_by(review_analysis_id=row.review_analysis_id).count()
        self._finish_retry("review_ids", review.review_id)
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
    subparsers.add_parser("refresh-inputs", help="Refresh reservations and submitted reviews before the daily scan")
    export_parser = subparsers.add_parser("export")
    export_parser.add_argument("--max-stays", type=int, default=8)
    export_parser.add_argument("--max-reviews", type=int, default=16)
    export_parser.add_argument("--output", required=True)
    import_parser = subparsers.add_parser("import")
    import_parser.add_argument("--results", required=True)
    import_parser.add_argument("--batch")
    import_parser.add_argument("--cleanup", action="store_true")
    complaint_export = subparsers.add_parser("export-complaints")
    complaint_export.add_argument("--listing-id", type=int, action="append")
    complaint_export.add_argument("--output", required=True)
    complaint_import = subparsers.add_parser("import-complaints")
    complaint_import.add_argument("--batch", required=True)
    complaint_import.add_argument("--results", required=True)
    sync_import_parser = subparsers.add_parser("sync-import")
    sync_import_parser.add_argument("--input", default="-")
    sync_production_parser = subparsers.add_parser("sync-production")
    sync_scope = sync_production_parser.add_mutually_exclusive_group(required=True)
    sync_scope.add_argument("--run-id", type=int)
    sync_scope.add_argument("--pending", action="store_true")
    sync_production_parser.add_argument(
        "--ssh-target",
        default=os.getenv("GUEST_EXPERIENCE_PRODUCTION_SSH_TARGET"),
    )
    sync_production_parser.add_argument(
        "--identity-file",
        default=os.getenv("GUEST_EXPERIENCE_PRODUCTION_SSH_KEY"),
    )
    sync_production_parser.add_argument(
        "--ssh-port",
        type=int,
        default=int(os.getenv("GUEST_EXPERIENCE_PRODUCTION_SSH_PORT", "22")),
    )
    sync_production_parser.add_argument(
        "--remote-app-dir",
        default=os.getenv(
            "GUEST_EXPERIENCE_PRODUCTION_APP_DIR",
            "/opt/hostaway-messages/app",
        ),
    )
    sync_production_parser.add_argument(
        "--remote-python",
        default=os.getenv(
            "GUEST_EXPERIENCE_PRODUCTION_PYTHON",
            "/opt/hostaway-messages/venv/bin/python",
        ),
    )
    sync_production_parser.add_argument(
        "--remote-env-file",
        default=os.getenv(
            "GUEST_EXPERIENCE_PRODUCTION_ENV_FILE",
            "/opt/hostaway-messages/.env",
        ),
    )
    sync_production_parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=int(os.getenv("GUEST_EXPERIENCE_PRODUCTION_TIMEOUT_SECONDS", "120")),
    )
    args = parser.parse_args()

    if args.action in {"export", "import", "refresh-inputs"}:
        init_models(None)
    init_guest_experience_tables()
    if args.action == "refresh-inputs":
        from sync.sync_reservations import sync_reservations
        from sync.sync_reviews import sync_reviews
        results = {"reservations": sync_reservations(full_sync=False)}
        # Fetch all review IDs: an old pending review may be submitted today.
        results["reviews"] = sync_reviews(full_sync=True, guest_posted_since=_utcnow() - REVIEW_SCAN_LOOKBACK)
        for kind, result in results.items():
            if result.get("errors") or result.get("status") in {"failed", "error", "partial"}:
                raise RuntimeError(f"{kind} refresh failed: {result}")
        print(json.dumps(results, default=str))
        return
    if args.action in {"export-complaints", "import-complaints"}:
        session = get_brain_session()
        try:
            if args.action == "export-complaints":
                listing_ids = args.listing_id or [row[0] for row in session.query(
                    PropertyGuestIssue.listing_id
                ).filter(PropertyGuestIssue.complaint_key.is_(None)).distinct().all()]
                catalog = complaint_catalog(session, listing_ids)
                _write_private_json(args.output, {
                    "instructions": COMPLAINT_MATCHING_INSTRUCTIONS,
                    "complaint_catalog": catalog,
                    "result_shape": {"complaint_assignments": [{
                        "source_kind": "stay or review", "source_issue_key": "copy from catalog",
                        "input_hash": "copy from catalog", "complaint_key": "meaning-based-identity",
                    }]},
                })
                print(json.dumps({"reports_exported": len(catalog), "output": args.output}))
            else:
                batch = json.loads(Path(args.batch).expanduser().read_text())
                payload = json.loads(Path(args.results).expanduser().read_text())
                print(json.dumps(backfill_complaint_identities(
                    session, payload, batch["complaint_catalog"],
                )))
        finally:
            session.close()
        return
    if args.action in {"export", "import"}:
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
        return

    brain_session = get_brain_session()
    replication = GuestExperienceReplicationService(brain_session)
    try:
        if args.action == "sync-import":
            if brain_session.get_bind().dialect.name != "postgresql":
                raise GuestExperienceReplicationError("Production sync-import requires PostgreSQL")
            if args.input == "-":
                payload = json.load(sys.stdin)
            else:
                with Path(args.input).expanduser().resolve().open("r", encoding="utf-8") as handle:
                    payload = json.load(handle)
            print(json.dumps(replication.import_payload(payload), indent=2))
            return

        if not args.ssh_target:
            raise GuestExperienceReplicationError(
                "Production SSH target is required via --ssh-target or "
                "GUEST_EXPERIENCE_PRODUCTION_SSH_TARGET"
            )
        if not args.identity_file:
            raise GuestExperienceReplicationError(
                "Production SSH identity is required via --identity-file or "
                "GUEST_EXPERIENCE_PRODUCTION_SSH_KEY"
            )
        client = ProductionSshClient(ProductionSshConfig(
            target=args.ssh_target,
            identity_file=Path(args.identity_file),
            remote_app_dir=args.remote_app_dir,
            remote_python=args.remote_python,
            remote_env_file=args.remote_env_file,
            port=args.ssh_port,
            timeout_seconds=max(1, args.timeout_seconds),
        ))
        run_ids = (
            replication.pending_run_ids()
            if args.pending
            else [int(args.run_id)]
        )
        synced = []
        for run_id in run_ids:
            try:
                payload = replication.export_run(run_id)
                result = client.import_payload(payload)
                replication.mark_sync_completed(
                    run_id,
                    target=args.ssh_target,
                    result=result,
                )
                synced.append(result)
            except Exception as exc:
                replication.mark_sync_failed(
                    run_id,
                    target=args.ssh_target,
                    error=exc,
                )
                raise
        print(json.dumps({
            "status": "completed",
            "runs_synced": len(synced),
            "run_ids": run_ids,
            "results": synced,
        }, indent=2))
    finally:
        brain_session.close()


if __name__ == "__main__":
    main()
