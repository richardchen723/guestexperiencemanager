#!/usr/bin/env python3
"""Background source refresh used by the KPI dashboard."""

from __future__ import annotations

import logging
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable

from sqlalchemy import func, text
from sqlalchemy.orm.attributes import flag_modified

import dashboard.config as config
from brain.models import CalendarSnapshot, get_session as get_brain_session
from dashboard.sync.job_manager import get_job_manager
from dashboard.sync.web_progress import WebProgressTracker
from database.models import SyncJob, SyncLog, get_session as get_main_session
from sync.api_client import HostawayAPIClient
from sync.sync_manager import incremental_sync

logger = logging.getLogger(__name__)

KPI_REFRESH_MODE = "kpi_refresh"
FORWARD_CALENDAR_DAYS = 60
KPI_REFRESH_QUEUE_LOCK_ID = 780_411_927
KPI_REFRESH_STALE_AFTER = timedelta(minutes=3)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def enqueue_kpi_refresh(
    listing_ids: Iterable[int],
    *,
    scope_name: str = "all",
    session: Any | None = None,
    now: datetime | None = None,
) -> tuple[dict[str, Any], bool]:
    """Persist one deduplicated KPI refresh for the independent worker."""
    normalized_ids = sorted({int(value) for value in listing_ids if value is not None})
    if not normalized_ids:
        raise ValueError("At least one listing is required for a KPI refresh.")

    db_session = session or get_main_session(config.MAIN_DATABASE_PATH)
    owns_session = session is None
    queued_at = now or _utcnow()
    try:
        bind = db_session.get_bind()
        if bind.dialect.name == "postgresql":
            db_session.execute(
                text("SELECT pg_advisory_xact_lock(:lock_id)"),
                {"lock_id": KPI_REFRESH_QUEUE_LOCK_ID},
            )

        active = (
            db_session.query(SyncJob)
            .filter(
                SyncJob.sync_mode == KPI_REFRESH_MODE,
                SyncJob.status.in_(("pending", "running")),
            )
            .order_by(SyncJob.started_at.desc())
            .first()
        )
        if active is not None:
            result = _job_record(active)
            db_session.commit()
            return result, False

        job = SyncJob(
            job_id=str(uuid.uuid4()),
            sync_run_id=0,
            sync_mode=KPI_REFRESH_MODE,
            status="pending",
            progress={
                "phase": "Queued for background refresh",
                "processed": 0,
                "total": 0,
                "created": 0,
                "updated": 0,
                "errors": 0,
                "percentage": 0.0,
                "request": {
                    "listing_ids": normalized_ids,
                    "scope": str(scope_name or "all")[:120],
                },
            },
            error_message=None,
            started_at=queued_at,
            completed_at=None,
            updated_at=queued_at,
        )
        db_session.add(job)
        db_session.commit()
        return _job_record(job), True
    except Exception:
        db_session.rollback()
        raise
    finally:
        if owns_session:
            db_session.close()


def claim_next_kpi_refresh(
    *,
    session: Any | None = None,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    """Atomically claim the oldest queued KPI refresh for one worker."""
    db_session = session or get_main_session(config.MAIN_DATABASE_PATH)
    owns_session = session is None
    claimed_at = now or _utcnow()
    try:
        query = (
            db_session.query(SyncJob)
            .filter(
                SyncJob.sync_mode == KPI_REFRESH_MODE,
                SyncJob.status == "pending",
            )
            .order_by(SyncJob.started_at.asc())
        )
        if db_session.get_bind().dialect.name == "postgresql":
            query = query.with_for_update(skip_locked=True)
        job = query.first()
        if job is None:
            return None

        progress = dict(job.get_progress())
        progress.update({
            "phase": "Starting background refresh",
            "processed": 0,
            "total": 0,
            "created": 0,
            "updated": 0,
            "errors": 0,
            "percentage": 0.0,
        })
        job.status = "running"
        job.error_message = None
        job.completed_at = None
        job.updated_at = claimed_at
        job.set_progress(progress)
        flag_modified(job, "progress")
        db_session.commit()
        return _job_record(job)
    except Exception:
        db_session.rollback()
        raise
    finally:
        if owns_session:
            db_session.close()


def recover_stale_kpi_refreshes(
    *,
    session: Any | None = None,
    now: datetime | None = None,
    stale_after: timedelta = KPI_REFRESH_STALE_AFTER,
) -> int:
    """Requeue refreshes whose worker stopped without completing the job."""
    db_session = session or get_main_session(config.MAIN_DATABASE_PATH)
    owns_session = session is None
    recovered_at = now or _utcnow()
    cutoff = recovered_at - stale_after
    try:
        stale_jobs = (
            db_session.query(SyncJob)
            .filter(
                SyncJob.sync_mode == KPI_REFRESH_MODE,
                SyncJob.status == "running",
                SyncJob.updated_at < cutoff,
            )
            .all()
        )
        for job in stale_jobs:
            progress = dict(job.get_progress())
            progress.update({
                "phase": "Requeued after background worker restart",
                "processed": 0,
                "total": 0,
                "created": 0,
                "updated": 0,
                "errors": 0,
                "percentage": 0.0,
                "current_item": None,
            })
            job.status = "pending"
            job.sync_run_id = 0
            job.error_message = None
            job.completed_at = None
            job.updated_at = recovered_at
            job.set_progress(progress)
            flag_modified(job, "progress")
        if stale_jobs:
            db_session.commit()
        return len(stale_jobs)
    except Exception:
        db_session.rollback()
        raise
    finally:
        if owns_session:
            db_session.close()


def refresh_listing_ids(job: dict[str, Any]) -> list[int]:
    """Read the persisted refresh scope without exposing it in the browser payload."""
    request_payload = (job.get("progress") or {}).get("request") or {}
    return sorted({
        int(value)
        for value in request_payload.get("listing_ids") or []
        if value is not None
    })


def run_kpi_refresh_async(job_id: str, listing_ids: list[int]) -> None:
    """Refresh Hostaway-backed KPI sources and persist progress for polling."""
    job_manager = get_job_manager()
    progress = WebProgressTracker(job_id)
    main_session = None

    try:
        job_manager.update_job_status(job_id, "running")
        main_session = get_main_session(config.MAIN_DATABASE_PATH)
        sync_run_id = _next_sync_run_id(main_session)
        job_manager.set_sync_run_id(job_id, sync_run_id)

        core_result = incremental_sync(
            progress_tracker=progress,
            sync_run_id=sync_run_id,
            force=True,
            include_messages=True,
        )

        calendar_result = refresh_calendar_snapshots(
            listing_ids,
            progress=progress,
        )

        warnings: list[str] = []
        core_status = str(core_result.get("status") or "error").lower()
        if core_status == "partial":
            warnings.append("One or more Hostaway source phases completed with warnings.")
        if calendar_result["failed_listings"]:
            warnings.append(
                f"Forward calendar refresh failed for {calendar_result['failed_listings']} "
                f"of {calendar_result['requested_listings']} properties."
            )

        fatal_reasons: list[str] = []
        if core_status == "error":
            fatal_reasons.append("The Hostaway listings, reservations, messages, or reviews refresh failed.")
        if listing_ids and not calendar_result["refreshed_listings"]:
            fatal_reasons.append("The forward calendar refresh failed for all selected properties.")

        progress.start_phase("Refresh complete", 1)
        progress.increment(error=bool(warnings or fatal_reasons), item_name="KPI data is ready")
        progress.complete_phase()

        if fatal_reasons:
            job_manager.update_job_status(job_id, "error", error=" ".join(fatal_reasons))
        else:
            update_kwargs = {"error": " ".join(warnings)} if warnings else {}
            job_manager.update_job_status(job_id, "completed", **update_kwargs)
    except Exception:
        logger.error("KPI source refresh failed for job %s", job_id, exc_info=True)
        job_manager.update_job_status(
            job_id,
            "error",
            error="The source refresh stopped unexpectedly. Please retry or review Data sync history.",
        )
    finally:
        if main_session is not None:
            main_session.close()


def refresh_calendar_snapshots(
    listing_ids: Iterable[int],
    *,
    progress: Any | None = None,
    client: Any | None = None,
    session: Any | None = None,
    today: date | None = None,
    refreshed_at: datetime | None = None,
) -> dict[str, int]:
    """Replace today's 60-day calendar snapshot for each successfully fetched listing."""
    normalized_ids = sorted({int(value) for value in listing_ids if value is not None})
    snapshot_date = today or date.today()
    refreshed_at = refreshed_at or datetime.utcnow()
    end_date = snapshot_date + timedelta(days=FORWARD_CALENDAR_DAYS)
    api_client = client or HostawayAPIClient()
    brain_session = session or get_brain_session()
    owns_session = session is None

    if progress:
        progress.start_phase("Refreshing forward calendar", len(normalized_ids))

    refreshed_count = 0
    failed_count = 0
    row_count = 0
    try:
        existing_rows = []
        if normalized_ids:
            existing_rows = (
                brain_session.query(CalendarSnapshot)
                .filter(
                    CalendarSnapshot.listing_id.in_(normalized_ids),
                    CalendarSnapshot.snapshot_date == snapshot_date,
                    CalendarSnapshot.calendar_date >= snapshot_date,
                    CalendarSnapshot.calendar_date < end_date,
                )
                .all()
            )
        existing_by_key = {
            (int(row.listing_id), row.calendar_date): row
            for row in existing_rows
        }

        for listing_id in normalized_ids:
            if progress:
                progress.update_item(f"Property {listing_id}")
            try:
                raw_days = api_client.get_calendar(
                    listing_id,
                    snapshot_date.isoformat(),
                    end_date.isoformat(),
                )
                if raw_days is None:
                    raise RuntimeError("Hostaway returned no calendar payload")

                values = normalize_calendar_rows(
                    listing_id,
                    raw_days,
                    snapshot_date=snapshot_date,
                    refreshed_at=refreshed_at,
                )
                returned_dates = {value["calendar_date"] for value in values}

                for key, row in list(existing_by_key.items()):
                    if key[0] == listing_id and key[1] not in returned_dates:
                        brain_session.delete(row)
                        existing_by_key.pop(key, None)

                for value in values:
                    key = (listing_id, value["calendar_date"])
                    row = existing_by_key.get(key)
                    if row is None:
                        row = CalendarSnapshot(
                            listing_id=listing_id,
                            calendar_date=value["calendar_date"],
                            snapshot_date=snapshot_date,
                        )
                        brain_session.add(row)
                        existing_by_key[key] = row
                    row.is_available = value["is_available"]
                    row.status = value["status"]
                    row.price = value["price"]
                    row.minimum_stay = value["minimum_stay"]
                    row.maximum_stay = value["maximum_stay"]
                    row.raw_payload = value["raw_payload"]
                    # CalendarSnapshot has no updated_at; this records the latest
                    # successful replacement time for the freshness indicator.
                    row.created_at = refreshed_at

                brain_session.commit()
                row_count += len(values)
                refreshed_count += 1
                if progress:
                    progress.increment(updated=True, item_name=f"Property {listing_id}")
            except Exception as exc:
                brain_session.rollback()
                failed_count += 1
                logger.warning("KPI calendar refresh failed for listing %s: %s", listing_id, exc)
                if progress:
                    progress.increment(error=True, item_name=f"Property {listing_id}")

        if progress:
            progress.complete_phase()
        return {
            "requested_listings": len(normalized_ids),
            "refreshed_listings": refreshed_count,
            "failed_listings": failed_count,
            "calendar_rows": row_count,
        }
    finally:
        if owns_session:
            brain_session.close()


def normalize_calendar_rows(
    listing_id: int,
    days: Iterable[dict[str, Any]],
    *,
    snapshot_date: date,
    refreshed_at: datetime,
) -> list[dict[str, Any]]:
    """Normalize Hostaway calendar payloads before database upsert."""
    end_date = snapshot_date + timedelta(days=FORWARD_CALENDAR_DAYS)
    rows: list[dict[str, Any]] = []
    seen_dates: set[date] = set()
    for raw_day in days or []:
        if not isinstance(raw_day, dict):
            continue
        calendar_date = _parse_date(raw_day.get("date"))
        if not calendar_date or not (snapshot_date <= calendar_date < end_date):
            continue
        if calendar_date in seen_dates:
            continue
        seen_dates.add(calendar_date)
        rows.append({
            "listing_id": int(listing_id),
            "calendar_date": calendar_date,
            "snapshot_date": snapshot_date,
            "is_available": _coerce_bool(raw_day.get("isAvailable")),
            "status": raw_day.get("status"),
            "price": _coerce_float(raw_day.get("price")),
            "minimum_stay": _coerce_int(raw_day.get("minimumStay")),
            "maximum_stay": _coerce_int(raw_day.get("maximumStay")),
            "raw_payload": dict(raw_day),
            "refreshed_at": refreshed_at,
        })
    return rows


def active_kpi_refresh_job() -> dict[str, Any] | None:
    """Return the newest pending/running KPI refresh, if one exists."""
    jobs = get_job_manager().get_all_active_jobs().values()
    matches = [job for job in jobs if job.get("sync_mode") == KPI_REFRESH_MODE]
    if not matches:
        return None
    return max(matches, key=lambda job: job.get("started_at") or datetime.min)


def refresh_job_payload(job: dict[str, Any]) -> dict[str, Any]:
    """Return the stable, browser-safe status contract for a refresh job."""
    progress = job.get("progress") or {}
    return {
        "job_id": job.get("job_id"),
        "status": job.get("status") or "unknown",
        "progress": {
            "phase": progress.get("phase") or "Preparing source refresh",
            "processed": int(progress.get("processed") or 0),
            "total": int(progress.get("total") or 0),
            "percentage": round(float(progress.get("percentage") or 0), 1),
            "errors": int(progress.get("errors") or 0),
            "current_item": progress.get("current_item"),
        },
        "warning": job.get("error") if job.get("status") == "completed" else None,
        "error": job.get("error") if job.get("status") == "error" else None,
    }


def _job_record(job: SyncJob) -> dict[str, Any]:
    return {
        "job_id": job.job_id,
        "sync_mode": job.sync_mode,
        "sync_run_id": job.sync_run_id,
        "status": job.status,
        "progress": job.get_progress(),
        "error": job.error_message,
        "started_at": job.started_at,
        "completed_at": job.completed_at,
    }


def _next_sync_run_id(session: Any) -> int:
    latest_log = session.query(func.max(SyncLog.sync_run_id)).scalar() or 0
    latest_job = session.query(func.max(SyncJob.sync_run_id)).scalar() or 0
    return max(int(latest_log), int(latest_job)) + 1


def _parse_date(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _coerce_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if value in (1, "1", "true", "True", "yes", "Yes"):
        return True
    if value in (0, "0", "false", "False", "no", "No"):
        return False
    return None


def _coerce_float(value: Any) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _coerce_int(value: Any) -> int | None:
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None
