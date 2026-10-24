"""Durable cadence, atomicity, concurrency and HTTP cost tests (isolated DB)."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import threading
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import sessionmaker

from dashboard.kpi.operations import build_operations
from dashboard.kpi.snapshot_job import run_due_snapshot
from dashboard.kpi.snapshot_store import (Base, CADENCE, CONTRACT_VERSION, LEASE, OperationalSnapshot, OperationalSnapshotState, claim_due_snapshot, publish_snapshot, read_saved_snapshot)
from dashboard.kpi.tests.test_operations_integration import make_app

NOW = datetime(2026, 10, 23, 12, tzinfo=timezone.utc)


@pytest.fixture
def store(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'snapshots.sqlite'}", execution_options={"schema_translate_map": {"brain": None}}, connect_args={"check_same_thread": False, "timeout": 10})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    yield factory, engine
    engine.dispose()


def run(factory, now=NOW, loader=None, calculator=None):
    return run_due_snapshot(factory=factory, now=now, clock=lambda: now, loader=loader or (lambda **_: {}), calculator=calculator)


def test_cold_get_does_not_initialize_or_calculate(store):
    factory, engine = store
    statements = []
    @event.listens_for(engine, "before_cursor_execute")
    def track(_, __, statement, ___, ____, _____): statements.append(statement)
    with patch("dashboard.kpi.operations.build_operations") as calc, patch("dashboard.kpi.operations_data.load_operational_evidence") as load:
        p = read_saved_snapshot(factory=factory, now=NOW)
    assert p["snapshot_state"]["status"] == "not_yet_calculated"
    assert len(p["metrics"]) == 6 and p["metrics"][1]["data_status"] == "deferred"
    assert all(m["value"] is None for m in p["metrics"])
    assert not load.called and not calc.called
    assert statements and all(x.lstrip().upper().startswith("SELECT") for x in statements)


def test_missing_storage_never_tries_ddl_or_sources(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'empty.sqlite'}", execution_options={"schema_translate_map": {"brain": None}})
    try:
        with patch("dashboard.kpi.operations.build_operations") as calc:
            p = read_saved_snapshot(factory=sessionmaker(bind=engine), now=NOW)
        assert p["snapshot_state"]["status"] == "storage_unavailable" and not calc.called
    finally: engine.dispose()


def test_first_job_publishes_48h_slot_repeat_is_idempotent(store):
    factory, _ = store
    calls = []
    loader = lambda **kw: calls.append(kw["now"]) or {}
    first = run(factory, loader=loader)
    assert first["status"] == "published"
    assert run(factory, NOW + CADENCE - timedelta(seconds=1), loader=loader)["status"] == "not_due"
    assert len(calls) == 1
    second = run(factory, NOW + CADENCE, loader=loader)
    assert second["status"] == "published" and len(calls) == 2
    assert second["snapshot_id"] != first["snapshot_id"]
    p = read_saved_snapshot(factory=factory, now=NOW + CADENCE)
    assert p["snapshot_state"]["next_due_at"] == (NOW + CADENCE * 2).isoformat()
    assert p["metric_contract_version"] == CONTRACT_VERSION


def test_repeated_http_gets_and_all_pages_call_no_sources_or_aggregation(store):
    factory, _ = store
    run(factory)
    with patch("dashboard.kpi.snapshot_store.read_saved_snapshot", side_effect=lambda: read_saved_snapshot(factory=factory, now=NOW)), patch("dashboard.kpi.operations_data.load_operational_evidence") as loader, patch("dashboard.kpi.operations.build_operations") as calc, patch("dashboard.auth.decorators.authenticate_request_api_key", return_value=True), patch("dashboard.kpi.routes.check_feature_access", return_value=None), patch("dashboard.auth.decorators.is_logged_in", return_value=True), patch("dashboard.auth.decorators.is_approved", return_value=True), patch("dashboard.kpi.routes.get_current_user", return_value=None):
        client = make_app().test_client()
        for _ in range(12): assert client.get("/kpi/api/operations").status_code == 200
        for key in ("revenue", "content", "reviews", "response", "issues", "cleaning"):
            assert client.get(f"/kpi/operations/{key}/").status_code == 200
        assert client.get("/kpi/").status_code == 200
    assert not loader.called and not calc.called


def test_snapshot_age_does_not_become_24h_source_staleness(store):
    factory, _ = store
    data = {"listings": [{"id": 1, "name": "Fixture", "timezone": "America/New_York"}], "sources": {s: {"status": "complete", "checked_at": NOW.isoformat()} for s in ("catalog", "reservations", "reviews", "messages", "issues", "content")}}
    run(factory, loader=lambda **_: data)
    p = read_saved_snapshot(factory=factory, now=NOW + timedelta(hours=36))
    assert p["snapshot_state"]["schedule_status"] == "scheduled"
    assert all(not s["stale"] for m in p["metrics"] for s in m["sources"])
    assert p["snapshot_state"]["source_as_of"]["catalog"]["checked_at"] == NOW.isoformat()
    assert read_saved_snapshot(factory=factory, now=NOW + timedelta(hours=49))["snapshot_state"]["schedule_status"] == "overdue"


def test_preexisting_stale_source_is_retained_separately(store):
    factory, _ = store
    data = {"sources": {"catalog": {"status": "partial", "checked_at": (NOW - timedelta(days=3)).isoformat()}}}
    run(factory, loader=lambda **_: data)
    p = read_saved_snapshot(factory=factory, now=NOW)
    assert p["snapshot_state"]["schedule_status"] == "scheduled"
    assert p["snapshot_state"]["source_as_of"]["catalog"]["stale"] is True


@pytest.mark.parametrize("failure", ["loader", "source", "incomplete_payload"])
def test_failed_refresh_retains_last_good_and_records_failure(store, failure):
    factory, _ = store
    first = run(factory)
    def broken(**_): raise RuntimeError("PRIVATE exception details")
    if failure == "loader": result = run(factory, NOW + CADENCE, loader=broken)
    elif failure == "source": result = run(factory, NOW + CADENCE, loader=lambda **_: {"sources": {"reviews": {"status": "error"}}})
    else: result = run(factory, NOW + CADENCE, calculator=lambda *_, **__: {"metrics": []})
    assert result["status"] == "failed" and "PRIVATE" not in str(result)
    p = read_saved_snapshot(factory=factory, now=NOW + CADENCE)
    assert p["snapshot_state"]["snapshot_id"] == first["snapshot_id"]
    assert p["snapshot_state"]["refresh_status"] == "failed"
    assert p["snapshot_state"]["calculated_at"] == NOW.isoformat()


def test_first_failed_calculation_keeps_not_calculated_state(store):
    factory, _ = store
    result = run(factory, calculator=lambda *_, **__: {"metrics": []})
    p = read_saved_snapshot(factory=factory, now=NOW)
    assert result["status"] == "failed"
    assert p["snapshot_state"]["status"] == "not_yet_calculated"
    assert p["snapshot_state"]["refresh_status"] == "failed"


def test_atomic_publish_failure_does_not_replace_pointer(store):
    factory, _ = store
    first = run(factory)
    def reject(*_): raise RuntimeError("injected insert failure")
    event.listen(OperationalSnapshot, "before_insert", reject)
    try: failed = run(factory, NOW + CADENCE)
    finally: event.remove(OperationalSnapshot, "before_insert", reject)
    assert failed["status"] == "failed"
    p = read_saved_snapshot(factory=factory, now=NOW + CADENCE)
    assert p["snapshot_state"]["snapshot_id"] == first["snapshot_id"]
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(OperationalSnapshot)) == 1


def test_multiworker_claims_do_only_one_calculation(store):
    factory, _ = store
    entered, release = threading.Event(), threading.Event()
    def loader(**_):
        entered.set(); assert release.wait(5); return {}
    with ThreadPoolExecutor(max_workers=5) as pool:
        active = pool.submit(run, factory, NOW, loader)
        assert entered.wait(5)
        others = list(pool.map(lambda _: run(factory), range(8)))
        assert all(x["status"] == "busy" for x in others)
        release.set()
        assert active.result()["status"] == "published"
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(OperationalSnapshot)) == 1


def test_expired_lease_recovers_same_slot_and_fences_old_publisher(store):
    factory, _ = store
    first = claim_due_snapshot(factory=factory, now=NOW)
    later = NOW + LEASE + timedelta(seconds=1)
    second = claim_due_snapshot(factory=factory, now=later)
    assert first["scheduled_for_at"] == second["scheduled_for_at"]
    assert publish_snapshot(first, build_operations({}, as_of=NOW), factory=factory, now=later)["status"] == "superseded"
    assert publish_snapshot(second, build_operations({}, as_of=later), factory=factory, now=later)["status"] == "published"


def test_contract_upgrade_requires_new_publication(store):
    factory, _ = store
    first = run(factory)
    with factory() as session:
        session.get(OperationalSnapshot, first["snapshot_id"]).contract_version = "old"
        session.get(OperationalSnapshotState, "portfolio").contract_version = "old"
        session.commit()
    assert read_saved_snapshot(factory=factory, now=NOW)["snapshot_state"]["status"] == "contract_update_pending"
    assert run(factory, NOW + timedelta(minutes=1))["status"] == "published"


def test_saved_window_is_from_calculation_not_read_time(store):
    factory, _ = store
    data = {"listings": [{"id": 1, "name": "Fixture", "timezone": "America/New_York"}]}
    run(factory, loader=lambda **_: data)
    p = read_saved_snapshot(factory=factory, now=NOW + timedelta(days=1))
    assert p["property_windows"][0]["start"] == "2026-07-23"
    assert p["property_windows"][0]["end"] == "2026-10-23"
    assert p["property_windows"][0]["days"] == 92


def test_new_revenue_comparison_is_saved_and_older_payloads_remain_read_only(store):
    from copy import deepcopy
    from dashboard.kpi.tests.test_estimates import booking, data
    factory, _ = store
    evidence = data(); evidence.update(allow_estimates=True, reservations=[booking(), booking(2, 2025)])
    first = run(factory, loader=lambda **_: evidence)
    payload = read_saved_snapshot(factory=factory, now=NOW)
    revenue = payload['metrics'][0]
    assert revenue['trend_schema'] == 'revenue-weekly-yoy.v1'
    assert any(x['prior_booking_value_usd'] is not None for x in revenue['trend'])
    with factory() as session:
        saved = session.get(OperationalSnapshot, first['snapshot_id'])
        old = deepcopy(saved.payload)
        for key in ('trend_schema','trend_scope','trend_alignment'): old['metrics'][0].pop(key)
        old['metrics'][0]['trend'] = [{'date':'2026-07-27','value':200,'unit':'USD booking proxy'}]
        saved.payload = old
        session.commit()
    with patch('dashboard.kpi.operations.build_operations') as calc, patch('dashboard.kpi.operations_data.load_operational_evidence') as load:
        reread = read_saved_snapshot(factory=factory, now=NOW+timedelta(hours=1))
    assert reread['snapshot_state']['status'] == 'available'
    assert reread['snapshot_state']['snapshot_id'] == first['snapshot_id']
    assert 'trend_schema' not in reread['metrics'][0] and len(reread['metrics']) == 6
    assert reread['property_windows'] == payload['property_windows']  # Audit data stays stored.
    assert reread['metrics'][1:] == payload['metrics'][1:]
    assert not calc.called and not load.called
