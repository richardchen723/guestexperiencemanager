from datetime import date, datetime, timedelta
from types import SimpleNamespace

from sqlalchemy.sql import operators

import dashboard.kpi.refresh as refresh_module
import dashboard.kpi.worker as worker_module
from dashboard.kpi.refresh import (
    claim_next_kpi_refresh,
    enqueue_kpi_refresh,
    normalize_calendar_rows,
    recover_stale_kpi_refreshes,
    refresh_job_payload,
    refresh_listing_ids,
)
from database.models import SyncJob


class FakeJobQuery:
    def __init__(self, jobs):
        self.jobs = list(jobs)

    def filter(self, *criteria):
        for criterion in criteria:
            column_name = criterion.left.name
            expected = criterion.right.value
            if criterion.operator is operators.eq:
                self.jobs = [job for job in self.jobs if getattr(job, column_name) == expected]
            elif criterion.operator is operators.in_op:
                self.jobs = [job for job in self.jobs if getattr(job, column_name) in expected]
            elif criterion.operator is operators.lt:
                self.jobs = [job for job in self.jobs if getattr(job, column_name) < expected]
            else:
                raise AssertionError(f"Unsupported test criterion: {criterion}")
        return self

    def order_by(self, *_args):
        return self

    def with_for_update(self, **_kwargs):
        return self

    def first(self):
        return self.jobs[0] if self.jobs else None

    def all(self):
        return list(self.jobs)


class FakeJobSession:
    def __init__(self):
        self.jobs = []
        self.commits = 0

    def get_bind(self):
        return SimpleNamespace(dialect=SimpleNamespace(name="sqlite"))

    def query(self, model):
        assert model is SyncJob
        return FakeJobQuery(self.jobs)

    def add(self, job):
        self.jobs.append(job)

    def commit(self):
        self.commits += 1

    def rollback(self):
        return None

    def close(self):
        return None


def test_normalize_calendar_rows_filters_range_duplicates_and_bad_values():
    snapshot_date = date(2026, 8, 20)
    refreshed_at = datetime(2026, 8, 20, 14, 0)

    rows = normalize_calendar_rows(
        101,
        [
            {
                "date": "2026-08-20",
                "isAvailable": "true",
                "status": "available",
                "price": "225.50",
                "minimumStay": "2",
                "maximumStay": "bad",
            },
            {"date": "2026-08-20", "status": "duplicate"},
            {"date": "2026-10-19", "status": "outside-exclusive-end"},
            {"date": "not-a-date", "status": "invalid"},
        ],
        snapshot_date=snapshot_date,
        refreshed_at=refreshed_at,
    )

    assert len(rows) == 1
    assert rows[0]["listing_id"] == 101
    assert rows[0]["calendar_date"] == snapshot_date
    assert rows[0]["is_available"] is True
    assert rows[0]["price"] == 225.5
    assert rows[0]["minimum_stay"] == 2
    assert rows[0]["maximum_stay"] is None
    assert rows[0]["refreshed_at"] == refreshed_at


def test_refresh_job_payload_exposes_warning_only_for_completed_job():
    payload = refresh_job_payload({
        "job_id": "job-1",
        "status": "completed",
        "progress": {
            "phase": "Refresh complete",
            "processed": 1,
            "total": 1,
            "percentage": 100,
            "errors": 1,
        },
        "error": "Calendar refresh failed for 1 of 48 properties.",
    })

    assert payload["status"] == "completed"
    assert payload["progress"]["percentage"] == 100.0
    assert payload["warning"] == "Calendar refresh failed for 1 of 48 properties."
    assert payload["error"] is None


def test_background_refresh_runs_incremental_sources_then_calendar(monkeypatch):
    events = []

    class FakeJobManager:
        def update_job_status(self, job_id, status, **kwargs):
            events.append(("status", job_id, status, kwargs))

        def set_sync_run_id(self, job_id, sync_run_id):
            events.append(("run_id", job_id, sync_run_id))

    class FakeProgress:
        def __init__(self, job_id):
            self.job_id = job_id

        def start_phase(self, phase, total=0):
            events.append(("phase", phase, total))

        def increment(self, **kwargs):
            events.append(("increment", kwargs))

        def complete_phase(self):
            events.append(("complete_phase",))

    class FakeSession:
        def close(self):
            events.append(("session_closed",))

    monkeypatch.setattr(refresh_module, "get_job_manager", lambda: FakeJobManager())
    monkeypatch.setattr(refresh_module, "WebProgressTracker", FakeProgress)
    monkeypatch.setattr(refresh_module, "get_main_session", lambda _path: FakeSession())
    monkeypatch.setattr(refresh_module, "_next_sync_run_id", lambda _session: 42)

    def fake_incremental_sync(**kwargs):
        events.append(("core", kwargs["sync_run_id"], kwargs["force"], kwargs["include_messages"]))
        return {"status": "success"}

    def fake_calendar(listing_ids, **kwargs):
        events.append(("calendar", listing_ids, bool(kwargs.get("progress"))))
        return {
            "requested_listings": 2,
            "refreshed_listings": 2,
            "failed_listings": 0,
            "calendar_rows": 120,
        }

    monkeypatch.setattr(refresh_module, "incremental_sync", fake_incremental_sync)
    monkeypatch.setattr(refresh_module, "refresh_calendar_snapshots", fake_calendar)

    refresh_module.run_kpi_refresh_async("job-1", [101, 102])

    assert ("run_id", "job-1", 42) in events
    assert ("core", 42, True, True) in events
    assert ("calendar", [101, 102], True) in events
    assert ("status", "job-1", "completed", {}) in events
    assert events.index(("core", 42, True, True)) < events.index(("calendar", [101, 102], True))


def test_enqueue_kpi_refresh_persists_scope_and_deduplicates_active_work():
    session = FakeJobSession()
    queued_at = datetime(2026, 8, 20, 10, 0)

    first, first_created = enqueue_kpi_refresh(
        [102, 101, 102],
        scope_name="Mountain portfolio",
        session=session,
        now=queued_at,
    )
    second, second_created = enqueue_kpi_refresh(
        [999],
        scope_name="Another portfolio",
        session=session,
        now=queued_at + timedelta(seconds=1),
    )

    assert first_created is True
    assert second_created is False
    assert second["job_id"] == first["job_id"]
    assert refresh_listing_ids(first) == [101, 102]
    assert first["progress"]["request"]["scope"] == "Mountain portfolio"
    assert len(session.jobs) == 1


def test_worker_claim_is_atomic_and_keeps_persisted_scope():
    session = FakeJobSession()
    queued, _ = enqueue_kpi_refresh([101, 102], session=session)

    claimed = claim_next_kpi_refresh(session=session, now=datetime(2026, 8, 20, 10, 5))
    duplicate_claim = claim_next_kpi_refresh(session=session, now=datetime(2026, 8, 20, 10, 6))

    assert claimed["job_id"] == queued["job_id"]
    assert claimed["status"] == "running"
    assert refresh_listing_ids(claimed) == [101, 102]
    assert duplicate_claim is None


def test_stale_worker_job_is_requeued_without_losing_request():
    session = FakeJobSession()
    queued, _ = enqueue_kpi_refresh(
        [201, 202],
        scope_name="Urban portfolio",
        session=session,
        now=datetime(2026, 8, 20, 9, 0),
    )
    claim_next_kpi_refresh(session=session, now=datetime(2026, 8, 20, 9, 1))

    recovered = recover_stale_kpi_refreshes(
        session=session,
        now=datetime(2026, 8, 20, 9, 10),
        stale_after=timedelta(minutes=3),
    )
    row = session.jobs[0]

    assert recovered == 1
    assert row.status == "pending"
    assert row.sync_run_id == 0
    assert row.get_progress()["request"]["listing_ids"] == [201, 202]
    assert row.get_progress()["phase"] == "Requeued after background worker restart"


def test_independent_worker_executes_claimed_job(monkeypatch):
    events = []
    monkeypatch.setattr(worker_module, "claim_next_kpi_refresh", lambda: {
        "job_id": "job-queued",
        "progress": {"request": {"listing_ids": [301, 302]}},
    })
    monkeypatch.setattr(
        worker_module,
        "run_kpi_refresh_async",
        lambda job_id, listing_ids: events.append((job_id, listing_ids)),
    )

    assert worker_module.process_next_kpi_refresh() is True
    assert events == [("job-queued", [301, 302])]
