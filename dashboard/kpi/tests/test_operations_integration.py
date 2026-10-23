"""Adapter and route integration against isolated DB fixtures; no production I/O."""
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from flask import Blueprint, Flask, g
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles

from dashboard.kpi.operations import build_operations
from dashboard.kpi.operations_data import load_operational_evidence
from dashboard.kpi.routes import register_kpi_routes


@compiles(JSONB, "sqlite")
def sqlite_fixture_json(_, __, **___):
    """Only the isolated SQLite fixture substitutes JSON storage for JSONB."""
    return "JSON"


@pytest.fixture
def db():
    from database.models import Listing, ListingTag, MessageMetadata, Reservation, Review, SyncLog, Tag
    from brain.models import GuestExperienceAnalysisRun, PropertyGuestIssue
    engine = create_engine("sqlite:///:memory:")
    @event.listens_for(engine, "connect")
    def attach(conn, _):
        conn.execute("ATTACH DATABASE ':memory:' AS brain")
        conn.execute("ATTACH DATABASE ':memory:' AS public")
    for model in (Listing, Tag, ListingTag, Reservation, Review, MessageMetadata, SyncLog, GuestExperienceAnalysisRun, PropertyGuestIssue):
        model.__table__.create(engine)
    factory = sessionmaker(bind=engine)
    with factory() as s:
        s.add(Listing(listing_id=1, name="Fixture property", timezone_name="America/New_York", status="active"))
        s.add(Reservation(reservation_id=1, listing_id=1, status="new", arrival_date=datetime(2026, 8, 1).date(), departure_date=datetime(2026, 8, 3).date(), guest_name="PRIVATE NAME", guest_email="private@example.invalid", channel_name="airbnbOfficial", total_price=500))
        s.add(MessageMetadata(message_id=1, listing_id=1, conversation_id=1, created_at=datetime(2026, 8, 1), is_incoming=1, sender_name="PRIVATE NAME", content_preview="PRIVATE MESSAGE"))
        s.add(Review(review_id=1, listing_id=1, reservation_id=1, origin="Guest", status="published", review_date=datetime(2026, 8, 5).date(), overall_rating=10, reviewer_name="PRIVATE NAME", review_text="PRIVATE REVIEW"))
        s.add(PropertyGuestIssue(issue_id=1, listing_id=1, source_kind="stay", source_issue_key="fixture-1", source_date=datetime(2024, 1, 1).date(), issue_category="cleanliness", summary="PRIVATE SUMMARY", details="PRIVATE DETAILS", source_references=[], operational_status="need_attention", resolution_state="resolved"))
        s.commit()
    yield factory, engine
    engine.dispose()


def test_adapter_is_select_only_preserves_carryins_and_omits_guest_pii(db):
    factory, engine = db
    statements = []
    @event.listens_for(engine, "before_cursor_execute")
    def capture(_, __, statement, ___, ____, _____):
        statements.append(statement)
    data = load_operational_evidence(now=datetime(2026, 10, 23, 12, tzinfo=timezone.utc), main_factory=factory, brain_factory=factory)
    assert statements and all(s.lstrip().upper().startswith("SELECT") for s in statements)
    assert data["issues"][0]["date"].year == 2024
    assert data["issues_readable"] is True
    assert "PRIVATE" not in str(data) and "private@example" not in str(data)
    assert not data["sources"]["issues"].get("checked_at")
    p = build_operations(data, as_of=datetime(2026, 10, 23, 12, tzinfo=timezone.utc))
    issues = next(x for x in p["metrics"] if x["key"] == "issues")
    assert issues["value"] == 1 and issues["denominator"] is None
    assert all(x["goal_status"] == "unknown" for x in p["metrics"])


def test_adapter_failure_is_source_error_not_silent_zero():
    def fail():
        raise RuntimeError("fixture unavailable")
    data = load_operational_evidence(main_factory=fail, brain_factory=fail)
    assert data["sources"]["catalog"]["status"] == "error"
    assert data["sources"]["issues"]["status"] == "error"
    p = build_operations(data)
    assert all(x["value"] is None for x in p["metrics"])


def make_app():
    base = Path(__file__).resolve().parents[2]
    app = Flask(__name__, template_folder=str(base / "templates"), static_folder=str(base / "static"))
    app.secret_key = "fixture-only"
    auth = Blueprint("auth", __name__)
    auth.add_url_rule("/login", "login", lambda: "fixture login")
    auth.add_url_rule("/pending", "pending_approval", lambda: "fixture pending")
    app.register_blueprint(auth)
    app.add_url_rule("/api-docs", "api_docs", lambda: "fixture API docs")
    app.context_processor(lambda: {"feature_access": SimpleNamespace(kpi=True), "can_manage_api_keys": False})
    register_kpi_routes(app)
    return app


@pytest.mark.parametrize("url", ["/kpi/", "/kpi/operations/issues/", "/kpi/api/operations"])
def test_unauthenticated_routes_remain_protected(url):
    with patch("dashboard.auth.decorators.authenticate_request_api_key", return_value=False):
        response = make_app().test_client().get(url)
    assert response.status_code == (401 if "/api/" in url else 302)


def test_feature_denial_happens_before_aggregation():
    with patch("dashboard.auth.decorators.authenticate_request_api_key", return_value=False), patch("dashboard.auth.decorators.is_logged_in", return_value=True), patch("dashboard.auth.decorators.is_approved", return_value=True), patch("dashboard.auth.decorators.get_current_user", return_value=object()), patch("dashboard.auth.decorators.user_can_access_feature", return_value=False), patch("dashboard.kpi.routes.operational_dashboard") as service:
        response = make_app().test_client().get("/kpi/api/operations")
    assert response.status_code == 403 and not service.called


def test_guest_issue_only_api_key_cannot_read_operational_kpi():
    def deny_full_scope():
        g.api_key_scope_denied = "legacy:full"
        return None
    with patch("dashboard.auth.decorators.authenticate_request_api_key", side_effect=deny_full_scope), patch("dashboard.kpi.routes.operational_dashboard") as service:
        response = make_app().test_client().get("/kpi/api/operations")
    assert response.status_code == 403 and not service.called
    assert response.get_json()["required_scope"] == "legacy:full"


def test_api_key_auth_readonly_payload_cache_headers_and_failure():
    with patch("dashboard.auth.decorators.authenticate_request_api_key", return_value=True), patch("dashboard.kpi.routes.operational_dashboard", return_value=build_operations({})):
        client = make_app().test_client()
        response = client.get("/kpi/api/operations")
        assert response.status_code == 200
        assert response.headers["Cache-Control"] == "private, no-store"
        assert len(response.get_json()["metrics"]) == 6
        assert client.post("/kpi/api/operations").status_code == 405
    with patch("dashboard.auth.decorators.authenticate_request_api_key", return_value=True), patch("dashboard.kpi.routes.operational_dashboard", side_effect=RuntimeError("private diagnostic")):
        response = make_app().test_client().get("/kpi/api/operations")
        assert response.status_code == 503 and b"private diagnostic" not in response.data


def test_real_overview_detail_routes_and_unknown_goal():
    with patch("dashboard.kpi.routes.check_feature_access", return_value=None), patch("dashboard.auth.decorators.is_logged_in", return_value=True), patch("dashboard.auth.decorators.is_approved", return_value=True), patch("dashboard.kpi.routes.get_current_user", return_value=None):
        client = make_app().test_client()
        response = client.get("/kpi/")
        assert response.status_code == 200
        assert response.data.count(b'<article class="ops-card"') == 6
        for key in ("revenue", "content", "reviews", "response", "issues", "cleaning"):
            response = client.get(f"/kpi/operations/{key}/")
            assert response.status_code == 200
            assert b"Diagnostic trend" in response.data and b"Source provenance" in response.data
            assert b"<dialog" not in response.data
        assert client.get("/kpi/operations/bookkeeping/").status_code == 404


def test_single_flight_cache_keeps_source_and_asof_timestamps(monkeypatch):
    from dashboard.kpi import operations_data as module
    from concurrent.futures import ThreadPoolExecutor
    monkeypatch.setattr(module, "_cache", None)
    with patch.object(module, "load_operational_evidence", return_value={}) as loader:
        with ThreadPoolExecutor(max_workers=4) as executor:
            values = list(executor.map(lambda _: module.operational_dashboard(), range(8)))
        assert loader.call_count == 1
        assert len({x["as_of"] for x in values}) == 1
