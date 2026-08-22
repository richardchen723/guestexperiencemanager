#!/usr/bin/env python3
"""Idempotent replication of Codex guest-experience results to production."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
from copy import deepcopy
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from sqlalchemy import text

from brain.guest_experience import analysis_window
from brain.models import (
    ComprehensiveStayAnalysis,
    GuestExperienceAnalysisRun,
    GuestReviewIssueAnalysis,
    PropertyGuestIssue,
    as_json_safe,
)

REPLICATION_SCHEMA_VERSION = 1
REPLICATION_SOURCE = "codex-local-hostaway-messages"
SYNCABLE_RUN_STATUSES = {"completed", "partial"}

RUN_FIELDS = (
    "status",
    "window_start_at",
    "window_end_at",
    "eligible_stay_count",
    "stays_analyzed",
    "stays_already_analyzed",
    "reviews_analyzed",
    "reviews_already_analyzed",
    "error_count",
    "details",
    "started_at",
    "completed_at",
)
STAY_FIELDS = (
    "listing_id",
    "reservation_id",
    "arrival_date",
    "departure_date",
    "checkout_at",
    "eligible_at",
    "stay_quality",
    "summary",
    "detailed_summary",
    "issue_count",
    "message_count",
    "guest_message_count",
    "source_message_ids",
    "input_hash",
    "prompt_version",
    "model",
    "source_metadata",
    "analyzed_at",
    "created_at",
)
REVIEW_FIELDS = (
    "review_id",
    "listing_id",
    "reservation_id",
    "review_date",
    "summary",
    "issue_count",
    "has_public_review",
    "has_private_feedback",
    "input_hash",
    "prompt_version",
    "model",
    "source_metadata",
    "analyzed_at",
    "created_at",
)
ISSUE_FIELDS = (
    "source_kind",
    "source_issue_key",
    "listing_id",
    "reservation_id",
    "review_id",
    "source_date",
    "issue_category",
    "summary",
    "details",
    "suggested_improvement",
    "severity",
    "resolution_state",
    "source_references",
    "created_at",
)

DATE_FIELDS = {"arrival_date", "departure_date", "review_date", "source_date"}
DATETIME_FIELDS = {
    "window_start_at",
    "window_end_at",
    "started_at",
    "completed_at",
    "checkout_at",
    "eligible_at",
    "analyzed_at",
    "created_at",
}


class GuestExperienceReplicationError(RuntimeError):
    """Raised when a result payload cannot be safely replicated."""


class GuestExperienceReplicationService:
    """Export local result rows and atomically import them on production."""

    def __init__(self, session):
        self.session = session

    def export_run(self, run_id: int) -> dict[str, Any]:
        run = self.session.query(GuestExperienceAnalysisRun).filter(
            GuestExperienceAnalysisRun.run_id == int(run_id)
        ).first()
        if not run:
            raise GuestExperienceReplicationError(f"Unknown analysis run {run_id}")
        if run.status not in SYNCABLE_RUN_STATUSES:
            raise GuestExperienceReplicationError(
                f"Analysis run {run_id} is not complete enough to sync (status={run.status})"
            )

        stays = self.session.query(ComprehensiveStayAnalysis).filter(
            ComprehensiveStayAnalysis.run_id == run.run_id
        ).order_by(ComprehensiveStayAnalysis.reservation_id).all()
        reviews = self.session.query(GuestReviewIssueAnalysis).filter(
            GuestReviewIssueAnalysis.run_id == run.run_id
        ).order_by(GuestReviewIssueAnalysis.review_id).all()
        stay_ids = [row.stay_analysis_id for row in stays]
        review_ids = [row.review_analysis_id for row in reviews]
        issues = self.session.query(PropertyGuestIssue).filter(
            (PropertyGuestIssue.stay_analysis_id.in_(stay_ids or [-1]))
            | (PropertyGuestIssue.review_analysis_id.in_(review_ids or [-1]))
        ).order_by(PropertyGuestIssue.source_kind, PropertyGuestIssue.source_issue_key).all()
        if not stays and not reviews:
            raise GuestExperienceReplicationError(f"Analysis run {run_id} contains no result rows")

        run_payload = _serialize_row(run, RUN_FIELDS)
        details = deepcopy(run_payload.get("details") or {})
        details.pop("production_sync", None)
        run_payload["details"] = details
        return {
            "schema_version": REPLICATION_SCHEMA_VERSION,
            "source": os.getenv("GUEST_EXPERIENCE_REPLICATION_SOURCE", REPLICATION_SOURCE),
            "source_run_id": int(run.run_id),
            "exported_at": datetime.utcnow().isoformat(),
            "run": run_payload,
            "stays": [_serialize_row(row, STAY_FIELDS) for row in stays],
            "reviews": [_serialize_row(row, REVIEW_FIELDS) for row in reviews],
            "issues": [_serialize_row(row, ISSUE_FIELDS) for row in issues],
        }

    def import_payload(self, payload: dict[str, Any]) -> dict[str, int | str]:
        self._validate_payload(payload)
        source = str(payload["source"])
        source_run_id = int(payload["source_run_id"])
        now = datetime.utcnow()

        if self.session.get_bind().dialect.name == "postgresql":
            self.session.execute(
                text("SELECT pg_advisory_xact_lock(780411945)")
            )

        try:
            remote_run, run_created = self._get_or_create_run(
                source,
                source_run_id,
                payload["run"],
                payload.get("exported_at"),
                now,
            )
            stay_map: dict[int, ComprehensiveStayAnalysis] = {}
            review_map: dict[int, GuestReviewIssueAnalysis] = {}
            stays_inserted = stays_existing = 0
            reviews_inserted = reviews_existing = 0
            issues_inserted = issues_existing = 0

            for item in payload["stays"]:
                reservation_id = int(item["reservation_id"])
                existing = self.session.query(ComprehensiveStayAnalysis).filter(
                    ComprehensiveStayAnalysis.reservation_id == reservation_id
                ).first()
                if existing:
                    self._require_matching_hash(existing.input_hash, item.get("input_hash"), "stay", reservation_id)
                    stay_map[reservation_id] = existing
                    stays_existing += 1
                    continue
                row = ComprehensiveStayAnalysis(
                    run_id=remote_run.run_id,
                    **_deserialize_fields(item, STAY_FIELDS),
                )
                self.session.add(row)
                self.session.flush()
                stay_map[reservation_id] = row
                stays_inserted += 1

            for item in payload["reviews"]:
                review_id = int(item["review_id"])
                existing = self.session.query(GuestReviewIssueAnalysis).filter(
                    GuestReviewIssueAnalysis.review_id == review_id
                ).first()
                if existing:
                    self._require_matching_hash(existing.input_hash, item.get("input_hash"), "review", review_id)
                    review_map[review_id] = existing
                    reviews_existing += 1
                    continue
                row = GuestReviewIssueAnalysis(
                    run_id=remote_run.run_id,
                    **_deserialize_fields(item, REVIEW_FIELDS),
                )
                self.session.add(row)
                self.session.flush()
                review_map[review_id] = row
                reviews_inserted += 1

            for item in payload["issues"]:
                source_kind = str(item["source_kind"])
                source_issue_key = str(item["source_issue_key"])
                existing = self.session.query(PropertyGuestIssue).filter(
                    PropertyGuestIssue.source_kind == source_kind,
                    PropertyGuestIssue.source_issue_key == source_issue_key,
                ).first()
                if existing:
                    issues_existing += 1
                    continue
                values = _deserialize_fields(item, ISSUE_FIELDS)
                if source_kind == "stay":
                    reservation_id = int(item["reservation_id"])
                    analysis = stay_map.get(reservation_id)
                    if not analysis:
                        raise GuestExperienceReplicationError(
                            f"Issue {source_issue_key} has no replicated stay analysis"
                        )
                    values["stay_analysis_id"] = analysis.stay_analysis_id
                else:
                    review_id = int(item["review_id"])
                    analysis = review_map.get(review_id)
                    if not analysis:
                        raise GuestExperienceReplicationError(
                            f"Issue {source_issue_key} has no replicated review analysis"
                        )
                    values["review_analysis_id"] = analysis.review_analysis_id
                self.session.add(PropertyGuestIssue(**values))
                issues_inserted += 1

            replication_details = dict(remote_run.details or {})
            replication_details["production_replication"] = {
                "source": source,
                "source_run_id": source_run_id,
                "source_exported_at": payload.get("exported_at"),
                "last_imported_at": now.isoformat(),
            }
            remote_run.details = as_json_safe(replication_details)
            self.session.commit()
            return {
                "status": "completed",
                "source_run_id": source_run_id,
                "remote_run_id": int(remote_run.run_id),
                "run_created": int(run_created),
                "stays_inserted": stays_inserted,
                "stays_existing": stays_existing,
                "reviews_inserted": reviews_inserted,
                "reviews_existing": reviews_existing,
                "issues_inserted": issues_inserted,
                "issues_existing": issues_existing,
            }
        except Exception:
            self.session.rollback()
            raise

    def pending_run_ids(self, *, reference_time: datetime | None = None) -> list[int]:
        now = reference_time or datetime.utcnow()
        current_window_start, _ = analysis_window(now)
        runs = self.session.query(GuestExperienceAnalysisRun).filter(
            GuestExperienceAnalysisRun.status.in_(tuple(SYNCABLE_RUN_STATUSES)),
            GuestExperienceAnalysisRun.window_end_at >= current_window_start,
        ).order_by(GuestExperienceAnalysisRun.started_at, GuestExperienceAnalysisRun.run_id).all()
        pending: list[int] = []
        for run in runs:
            details = run.details or {}
            if (details.get("production_sync") or {}).get("status") == "completed":
                continue
            expected_start, _ = analysis_window(run.started_at)
            if abs((run.window_start_at - expected_start).total_seconds()) > 300:
                continue
            has_results = self.session.query(ComprehensiveStayAnalysis.stay_analysis_id).filter(
                ComprehensiveStayAnalysis.run_id == run.run_id
            ).first() or self.session.query(GuestReviewIssueAnalysis.review_analysis_id).filter(
                GuestReviewIssueAnalysis.run_id == run.run_id
            ).first()
            if has_results:
                pending.append(int(run.run_id))
        return pending

    def mark_sync_completed(self, run_id: int, *, target: str, result: dict[str, Any]):
        self._mark_sync(run_id, {
            "status": "completed",
            "target": target,
            "completed_at": datetime.utcnow().isoformat(),
            "result": as_json_safe(result),
        })

    def mark_sync_failed(self, run_id: int, *, target: str, error: Exception):
        self._mark_sync(run_id, {
            "status": "failed",
            "target": target,
            "attempted_at": datetime.utcnow().isoformat(),
            "error": str(error)[:500],
        })

    def _mark_sync(self, run_id: int, sync_state: dict[str, Any]):
        run = self.session.query(GuestExperienceAnalysisRun).filter(
            GuestExperienceAnalysisRun.run_id == int(run_id)
        ).first()
        if not run:
            raise GuestExperienceReplicationError(f"Unknown analysis run {run_id}")
        details = dict(run.details or {})
        details["production_sync"] = as_json_safe(sync_state)
        run.details = as_json_safe(details)
        self.session.commit()

    def _get_or_create_run(
        self,
        source: str,
        source_run_id: int,
        run_payload: dict[str, Any],
        exported_at: str | None,
        now: datetime,
    ) -> tuple[GuestExperienceAnalysisRun, bool]:
        for row in self.session.query(GuestExperienceAnalysisRun).filter(
            GuestExperienceAnalysisRun.details.isnot(None)
        ).all():
            replication = (row.details or {}).get("production_replication") or {}
            if replication.get("source") == source and int(replication.get("source_run_id") or 0) == source_run_id:
                return row, False
        details = deepcopy(run_payload.get("details") or {})
        details["production_replication"] = {
            "source": source,
            "source_run_id": source_run_id,
            "source_exported_at": exported_at,
            "first_imported_at": now.isoformat(),
        }
        values = _deserialize_fields(run_payload, RUN_FIELDS)
        values["details"] = as_json_safe(details)
        run = GuestExperienceAnalysisRun(**values)
        self.session.add(run)
        self.session.flush()
        return run, True

    @staticmethod
    def _require_matching_hash(existing: str, incoming: Any, kind: str, source_id: int):
        if str(existing) != str(incoming):
            raise GuestExperienceReplicationError(
                f"Production {kind} {source_id} already exists with a different input hash"
            )

    @staticmethod
    def _validate_payload(payload: dict[str, Any]):
        if int(payload.get("schema_version") or 0) != REPLICATION_SCHEMA_VERSION:
            raise GuestExperienceReplicationError(
                f"Unsupported replication schema_version: {payload.get('schema_version')!r}"
            )
        if not payload.get("source") or int(payload.get("source_run_id") or 0) < 1:
            raise GuestExperienceReplicationError("Replication source and source_run_id are required")
        if not isinstance(payload.get("run"), dict):
            raise GuestExperienceReplicationError("Replication run payload is required")
        for key in ("stays", "reviews", "issues"):
            if not isinstance(payload.get(key), list):
                raise GuestExperienceReplicationError(f"Replication {key} must be a list")
        for issue in payload["issues"]:
            if issue.get("source_kind") not in {"stay", "review"}:
                raise GuestExperienceReplicationError("Issue source_kind must be stay or review")


@dataclass(frozen=True)
class ProductionSshConfig:
    target: str
    identity_file: Path
    remote_app_dir: str = "/opt/hostaway-messages/app"
    remote_python: str = "/opt/hostaway-messages/venv/bin/python"
    remote_env_file: str = "/opt/hostaway-messages/.env"
    port: int = 22
    timeout_seconds: int = 120

    def validate(self):
        if self.target.startswith("-") or any(char.isspace() for char in self.target):
            raise GuestExperienceReplicationError("Invalid production SSH target")
        identity = self.identity_file.expanduser().resolve()
        if not identity.is_file():
            raise GuestExperienceReplicationError(f"Production SSH identity file not found: {identity}")
        if not 1 <= int(self.port) <= 65535:
            raise GuestExperienceReplicationError("Invalid production SSH port")
        for value, label in (
            (self.remote_app_dir, "remote app directory"),
            (self.remote_python, "remote Python path"),
            (self.remote_env_file, "remote environment file"),
        ):
            if not PurePosixPath(value).is_absolute():
                raise GuestExperienceReplicationError(f"Production {label} must be absolute")


class ProductionSshClient:
    """Stream result-only payloads to a production importer over SSH stdin."""

    def __init__(self, config: ProductionSshConfig):
        config.validate()
        self.config = config

    def import_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        command = self._ssh_command()
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        try:
            completed = subprocess.run(
                command,
                input=encoded,
                text=True,
                capture_output=True,
                check=False,
                timeout=self.config.timeout_seconds,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise GuestExperienceReplicationError(f"Production SSH transfer failed: {exc}") from exc
        if completed.returncode != 0:
            error = (completed.stderr or completed.stdout or "remote importer failed").strip()
            raise GuestExperienceReplicationError(
                f"Production SSH importer exited {completed.returncode}: {error[-1000:]}"
            )
        try:
            result = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise GuestExperienceReplicationError("Production SSH importer returned invalid JSON") from exc
        if result.get("status") != "completed":
            raise GuestExperienceReplicationError("Production SSH importer did not confirm completion")
        return result

    def _ssh_command(self) -> list[str]:
        config = self.config
        remote_command = " ".join((
            "cd",
            shlex.quote(config.remote_app_dir),
            "&&",
            "sudo -n -u hostaway env",
            shlex.quote(f"HOSTAWAY_ENV_FILE={config.remote_env_file}"),
            shlex.quote(config.remote_python),
            "-m brain.guest_experience_codex sync-import --input -",
        ))
        return [
            "ssh",
            "-T",
            "-i",
            str(config.identity_file.expanduser().resolve()),
            "-p",
            str(config.port),
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=15",
            "-o",
            "StrictHostKeyChecking=yes",
            "-o",
            "ServerAliveInterval=15",
            config.target,
            remote_command,
        ]


def _serialize_row(row: Any, fields: tuple[str, ...]) -> dict[str, Any]:
    return {field: as_json_safe(getattr(row, field)) for field in fields}


def _deserialize_fields(payload: dict[str, Any], fields: tuple[str, ...]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for field in fields:
        value = payload.get(field)
        if value is not None and field in DATE_FIELDS:
            value = date.fromisoformat(str(value))
        elif value is not None and field in DATETIME_FIELDS:
            value = datetime.fromisoformat(str(value))
        values[field] = value
    return values
