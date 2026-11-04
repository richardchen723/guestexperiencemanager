"""Timezone ingestion regression tests; mocked API and isolated DB only."""
import json
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from database.models import Listing, SyncLog
from sync.listing_timezone import resolve_listing_timezone
from sync.sync_listings import sync_listings


def test_native_timezone_is_persisted_and_repeat_sync_is_idempotent():
    from sqlalchemy import create_engine, event, select
    from sqlalchemy.orm import sessionmaker
    engine = create_engine("sqlite:///:memory:")
    @event.listens_for(engine, "connect")
    def attach(conn, _):
        conn.execute("ATTACH DATABASE ':memory:' AS public")
    Listing.__table__.create(engine)
    SyncLog.__table__.create(engine)
    factory = sessionmaker(bind=engine)
    with factory() as session:
        session.add(Listing(listing_id=1))
        session.commit()
    client = MagicMock()
    client.get_listings_page.return_value = [{"id": 1, "name": "Fixture", "personCapacity": 2, "bedroomsNumber": 1, "bathroomsNumber": 1, "bedsNumber": 1, "timeZoneName": "America/New_York"}]
    try:
        with patch("sync.sync_listings.init_models"), patch("sync.sync_listings.get_session", side_effect=lambda _: factory()), patch("sync.sync_listings.HostawayAPIClient", return_value=client), patch("sync.sync_listings.sync_listing_tags"):
            first = sync_listings(progress_tracker=MagicMock(), sync_run_id=1)
            second = sync_listings(progress_tracker=MagicMock(), sync_run_id=2)
        assert first["status"] == second["status"] == "success"
        assert first["records_updated"] == 1 and second["records_updated"] == 0
        with factory() as session:
            assert session.get(Listing, 1).timezone_name == "America/New_York"
            logs = session.execute(select(SyncLog).order_by(SyncLog.sync_id)).scalars().all()
            assert len(logs) == 2
            assert all(json.loads(log.listing_stats)["1"]["timezone"]["source_field"] == "timeZoneName" for log in logs)
    finally:
        engine.dispose()


@pytest.mark.parametrize("field", ["timeZoneName", "timezoneName"])
def test_native_and_legacy_fields_are_accepted(field):
    r = resolve_listing_timezone({field: " America/New_York "})
    assert r == {"value": "America/New_York", "source_field": field, "status": "valid", "issues": []}


@pytest.mark.parametrize("value", [None, "", "  ", "not/a/timezone", "-05:00", 42])
def test_missing_or_invalid_payload_never_clears_valid_stored_timezone(value):
    r = resolve_listing_timezone({"timeZoneName": value}, existing="America/Chicago")
    assert r["value"] == "America/Chicago" and r["source_field"] == "stored"
    assert r["status"] == "preserved" and r["issues"]


def test_invalid_native_alias_falls_back_to_valid_legacy_with_evidence():
    r = resolve_listing_timezone({"timeZoneName": "bad-zone", "timezoneName": "America/Chicago"})
    assert r["value"] == "America/Chicago" and r["source_field"] == "timezoneName"
    assert r["issues"] == ["invalid:timeZoneName"]


def test_conflicting_valid_aliases_require_review_not_arbitrary_selection():
    payload = {"timeZoneName": "America/New_York", "timezoneName": "America/Chicago"}
    assert resolve_listing_timezone(payload)["value"] is None
    r = resolve_listing_timezone(payload, existing="America/Los_Angeles")
    assert r["value"] == "America/Los_Angeles" and r["source_field"] == "stored"
    assert "conflicting_timezone_fields" in r["issues"]


def test_invalid_or_absent_timezone_is_unknown_no_geographic_guess():
    for payload in ({}, {"timeZoneName": "bad-zone"}, {"city": "New York", "country": "US"}):
        assert resolve_listing_timezone(payload, existing="bad-zone")["value"] is None


@pytest.mark.parametrize("payload,previous,expected,field", [
    ({"timeZoneName": "America/New_York"}, None, "America/New_York", "timeZoneName"),
    ({"timezoneName": "America/Chicago"}, None, "America/Chicago", "timezoneName"),
    ({}, "America/Los_Angeles", "America/Los_Angeles", "stored"),
])
def test_sync_writes_validated_timezone_and_persists_source_evidence(payload, previous, expected, field):
    listing = Listing(listing_id=1, timezone_name=previous)
    session = MagicMock()
    session.query.return_value.all.return_value = [listing]
    client = MagicMock()
    # Complete ordinary profile prevents any extra listing-detail request.
    client.get_listings_page.return_value = [{"id": 1, "name": "Fixture", "personCapacity": 2, "bedroomsNumber": 1, "bathroomsNumber": 1, "bedsNumber": 1, **payload}]
    with patch("sync.sync_listings.init_models"), patch("sync.sync_listings.get_session", return_value=session), patch("sync.sync_listings.HostawayAPIClient", return_value=client), patch("sync.sync_listings.sync_listing_tags"):
        r = sync_listings(full_sync=False, progress_tracker=MagicMock(), sync_run_id=7)
    assert r["status"] == "success" and listing.timezone_name == expected
    client.get_listing.assert_not_called()
    logs = [call.args[0] for call in session.add.call_args_list if isinstance(call.args[0], SyncLog)]
    proof = json.loads(logs[0].listing_stats)["1"]["timezone"]
    assert proof["value"] == expected and proof["source_field"] == field
    # The populated native/legacy zone now reaches real local-calendar logic.
    from dashboard.kpi.operations import local_window
    w = local_window(datetime(2026, 10, 23, 12, tzinfo=timezone.utc), listing.timezone_name)
    assert w["start"].isoformat() == "2026-07-23" and w["days"] == 92
