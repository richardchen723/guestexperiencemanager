from copy import deepcopy
from datetime import date, datetime, timedelta
from unittest.mock import patch, MagicMock

import pytest
from sqlalchemy.exc import IntegrityError

from brain.guest_experience import is_analysis_eligible
from brain.guest_experience_codex import CodexGuestExperienceBatchService
from brain.guest_experience_replication import GuestExperienceReplicationService
from brain.models import ComprehensiveStayAnalysis, GuestExperienceAnalysisRun, GuestReviewIssueAnalysis, PropertyGuestIssue
from brain.tests.test_guest_experience import listing, reservation
from brain.tests.test_guest_experience_replication import _session
from database.models import Listing, Guest, Reservation, Conversation, MessageMetadata, Review, ReviewSubRating, SyncLog
from sync.sync_reviews import review_posted_timestamp


NOW = datetime(2026, 9, 23, 16)  # Noon Eastern; western checkout is still in the future.
KEY = "bedroom-ac-not-cooling"


@pytest.fixture
def service():
    session = _session()
    for model in (Listing, Guest, Reservation, Conversation, MessageMetadata, Review, ReviewSubRating):
        model.__table__.create(session.get_bind())
    session.add(Listing(listing_id=5, name="Test Home", timezone_name="America/Los_Angeles", check_out_time=11))
    session.add(Reservation(reservation_id=101, listing_id=5, status="confirmed",
                            arrival_date=date(2026, 9, 20), departure_date=date(2026, 9, 23)))
    session.add(Conversation(conversation_id=1, reservation_id=101, listing_id=5))
    session.add(MessageMetadata(message_id=1, conversation_id=1, reservation_id=101,
                               created_at=NOW - timedelta(hours=2), is_incoming=1, content_preview="AC isn't cooling"))
    session.commit()
    obj = CodexGuestExperienceBatchService(brain_session=session, main_session=session)
    with patch.object(obj, "_hydrate_pending_messages"):
        yield obj
    session.close()


def add_message(service, identifier=2, guest=False):
    service.main_session.add(MessageMetadata(
        message_id=identifier, conversation_id=1, reservation_id=101,
        # A newly synced message with an OLD source timestamp must still trigger a scan.
        created_at=NOW - timedelta(hours=1), is_incoming=int(guest),
        content_preview="Another update",
    ))
    service.main_session.commit()


def result(batch, *, resolved=False):
    return {"schema_version": 1, "run_id": batch["run_id"], "stays": [{
        "reservation_id": 101, "summary": "AC complaint", "detailed_summary": "AC complaint and response",
        "issues": [{"complaint_key": KEY, "summary": "AC not cooling", "issue_category": "hvac",
                    "details": "Guest reported AC not cooling", "severity": "material",
                    "resolution_state": "resolved" if resolved else "unresolved",
                    "source_references": [{"source_id": 1, "role": "complaint"}] + (
                        [{"source_id": 2, "role": "resolution"}] if resolved else [])}],
    }], "reviews": [], "complaint_assignments": []}


def first_scan(service):
    batch = service.export_batch(reference_time=NOW)
    service.import_results(result(batch))
    return batch


def test_unchanged_skips_but_late_support_message_rescans_without_duplicate(service):
    first = first_scan(service)
    issue = service.brain_session.query(PropertyGuestIssue).one()
    original_id = issue.issue_id
    issue.workflow_status, issue.resolution_comment, issue.linked_ticket_id = "resolved", "Operator fixed it", 55
    service.brain_session.commit()
    assert service.export_batch(reference_time=NOW + timedelta(minutes=10))["stays"] == []
    add_message(service)
    batch = service.export_batch(reference_time=NOW + timedelta(hours=1))
    assert batch["stays"][0]["new_message_ids"] == [2]
    assert batch["stays"][0]["existing_issues"][0]["existing_issue_key"] == issue.source_issue_key
    service.import_results(result(batch, resolved=True))
    issue = service.brain_session.query(PropertyGuestIssue).one()
    assert (issue.issue_id, issue.workflow_status, issue.linked_ticket_id) == (original_id, "resolved", 55)
    assert issue.resolution_state == "resolved"
    stay = service.brain_session.query(ComprehensiveStayAnalysis).one()
    assert stay.stay_quality == "recovered"
    assert stay.source_metadata["scan_version"] == 2
    assert stay.source_message_ids == [1, 2]
    replication = GuestExperienceReplicationService(service.brain_session)
    assert replication.export_run(first["run_id"])["stays"][0]["source_message_ids"] == [1]
    assert replication.export_run(batch["run_id"])["stays"][0]["source_message_ids"] == [1, 2]


def test_rescan_with_genuinely_new_complaint_and_reordered_results(service):
    first_scan(service)
    add_message(service, guest=True)
    batch = service.export_batch(reference_time=NOW + timedelta(hours=1))
    payload = result(batch)
    payload["stays"][0]["issues"].insert(0, {
        "complaint_key": "kitchen-tap-leaking", "summary": "Tap leaks", "resolution_state": "unresolved",
        "source_references": [{"source_id": 2, "role": "complaint"}],
    })
    service.import_results(payload)
    assert service.brain_session.query(PropertyGuestIssue).count() == 2
    with pytest.raises(ValueError, match="not awaiting"):
        service.import_results(payload)
    assert service.brain_session.query(PropertyGuestIssue).count() == 2


def test_relabeling_old_complaint_as_new_is_rejected_atomically(service):
    first_scan(service)
    add_message(service)
    batch = service.export_batch(reference_time=NOW + timedelta(hours=1))
    payload = result(batch)
    payload["stays"][0]["issues"][0]["complaint_key"] = "aircon-not-working"
    with pytest.raises(ValueError, match="new guest complaint"):
        service.import_results(payload)
    assert service.brain_session.query(PropertyGuestIssue).count() == 1
    assert service.brain_session.query(ComprehensiveStayAnalysis).one().source_metadata["scan_version"] == 1
    assert service.brain_session.get(GuestExperienceAnalysisRun, batch["run_id"]).status == "awaiting_analysis"


def test_retries_and_out_of_order_production_delivery_preserve_latest_and_workflow(service):
    first = first_scan(service)
    replication = GuestExperienceReplicationService(service.brain_session)
    old = replication.export_run(first["run_id"])
    destination = _session()
    remote = GuestExperienceReplicationService(destination)
    remote.import_payload(old)
    issue = destination.query(PropertyGuestIssue).one()
    issue.workflow_status, issue.linked_ticket_id, issue.resolution_comment = "resolved", 8, "Done"
    destination.commit()
    add_message(service)
    second = service.export_batch(reference_time=NOW + timedelta(hours=1))
    service.import_results(result(second, resolved=True))
    latest = replication.export_run(second["run_id"])
    assert remote.import_payload(latest)["stays_updated"] == 1
    remote.import_payload(latest)
    remote.import_payload(old)
    destination.refresh(issue)
    assert destination.query(PropertyGuestIssue).count() == 1
    assert (issue.workflow_status, issue.linked_ticket_id, issue.resolution_comment) == ("resolved", 8, "Done")
    assert issue.resolution_state == "resolved"
    assert destination.query(ComprehensiveStayAnalysis).one().source_metadata["scan_version"] == 2
    assert len(replication.pending_run_ids(reference_time=NOW + timedelta(days=90))) == 2


def test_review_and_messages_share_one_issue_and_review_is_not_scanned_twice(service):
    first_scan(service)
    session = service.main_session
    session.add(Review(review_id=77, listing_id=5, reservation_id=101, origin="Guest", status="published",
                       posted_at=NOW, review_date=NOW.date(), review_text="AC was not cooling"))
    session.commit()
    batch = service.export_batch(reference_time=NOW + timedelta(hours=1))
    service.import_results({"schema_version": 1, "run_id": batch["run_id"], "stays": [], "reviews": [{
        "review_id": 77, "summary": "AC complaint", "issues": [{
            "complaint_key": KEY, "summary": "AC not cooling", "source_references": [
                {"source_id": 77, "source_part": "public_review"}],
        }],
    }], "complaint_assignments": []})
    issue = session.query(PropertyGuestIssue).one()
    assert {ref["source_type"] for ref in issue.source_references} == {"message", "review"}
    assert session.query(GuestReviewIssueAnalysis).count() == 1
    assert service.export_batch(reference_time=NOW + timedelta(hours=2))["reviews"] == []


def test_review_window_is_independent_of_checkout_and_requires_precise_timestamp(service):
    session = service.main_session
    session.get(Reservation, 101).departure_date = date(2026, 7, 1)
    for identifier, posted_at in [(71, NOW - timedelta(hours=36)), (72, NOW - timedelta(hours=36, seconds=1)),
                                  (73, None), (74, NOW + timedelta(seconds=1))]:
        session.add(Review(review_id=identifier, listing_id=5, reservation_id=101, origin="Guest",
                           status="submitted", posted_at=posted_at, review_date=NOW.date()))
    session.commit()
    batch = service.export_batch(reference_time=NOW)
    assert [row["review_id"] for row in batch["reviews"]] == [71]
    assert batch["stays"] == []


def test_overlap_and_expired_batch_retry_after_window(service):
    first = service.export_batch(reference_time=NOW)
    assert service.export_batch(reference_time=NOW + timedelta(minutes=1))["stays"] == []
    retry = service.export_batch(reference_time=NOW + timedelta(days=5))
    assert [row["reservation_id"] for row in retry["stays"]] == [101]
    service.import_results(result(retry))
    add_message(service)
    # A successful retry clears the exception; it must not monitor this stay forever.
    assert service.export_batch(reference_time=NOW + timedelta(days=6))["stays"] == []


def test_input_changes_between_export_and_import_do_not_mark_scanned(service):
    batch = service.export_batch(reference_time=NOW)
    add_message(service)
    with pytest.raises(ValueError, match="evidence changed"):
        service.import_results(result(batch))
    assert service.brain_session.query(ComprehensiveStayAnalysis).count() == 0


def test_unique_issue_identity_is_enforced_in_database(service):
    first_scan(service)
    original = service.brain_session.query(PropertyGuestIssue).one()
    service.brain_session.add(PropertyGuestIssue(
        source_kind="review", source_issue_key="another-source", listing_id=5,
        source_date=NOW.date(), issue_category="hvac", summary="Duplicate", details="Duplicate",
        severity="material", dedupe_key=original.dedupe_key, source_references=[],
    ))
    with pytest.raises(IntegrityError):
        service.brain_session.commit()
    service.brain_session.rollback()


def test_legacy_unclassified_issue_is_backfilled_and_updated_once_on_production(service):
    first = first_scan(service)
    session = service.brain_session
    issue = session.query(PropertyGuestIssue).one()
    issue.complaint_key = None
    issue.dedupe_key = None
    run = session.get(GuestExperienceAnalysisRun, first["run_id"])
    details = dict(run.details)
    details.pop("result_snapshot")
    run.details = details
    session.commit()
    replication = GuestExperienceReplicationService(session)
    destination = _session()
    remote = GuestExperienceReplicationService(destination)
    remote.import_payload(replication.export_run(run.run_id))
    add_message(service)
    batch = service.export_batch(reference_time=NOW + timedelta(hours=1))
    payload = result(batch, resolved=True)
    payload["stays"][0]["issues"][0]["existing_issue_key"] = issue.source_issue_key
    payload["complaint_assignments"] = [{
        **{name: item[name] for name in ("source_kind", "source_issue_key", "input_hash")},
        "complaint_key": KEY,
    } for item in batch["complaint_catalog"] if item["complaint_key"] is None]
    service.import_results(payload)
    delivery = replication.export_run(batch["run_id"])
    remote.import_payload(delivery)
    remote.import_payload(delivery)
    assert destination.query(PropertyGuestIssue).count() == 1
    assert destination.query(PropertyGuestIssue).one().resolution_state == "resolved"


def test_muted_stays_are_marked_then_rescanned_when_guest_speaks(service):
    service.main_session.query(MessageMetadata).delete()
    service.main_session.commit()
    batch = service.export_batch(reference_time=NOW)
    assert batch["local_results"]["muted_stays_analyzed"] == 1
    assert batch["stays"] == []
    assert service.brain_session.query(ComprehensiveStayAnalysis).one().stay_quality == "muted"
    assert service.export_batch(reference_time=NOW + timedelta(minutes=1))["local_results"]["muted_stays_analyzed"] == 0
    add_message(service, identifier=1, guest=True)
    batch = service.export_batch(reference_time=NOW + timedelta(hours=1))
    service.import_results(result(batch))
    assert service.brain_session.query(PropertyGuestIssue).count() == 1
    assert service.brain_session.query(ComprehensiveStayAnalysis).one().source_metadata["scan_version"] == 2


def test_failed_message_refresh_cannot_be_mistaken_for_silent_guest(service):
    with patch("brain.guest_experience_codex.HostawayAPIClient") as client:
        client.return_value.get_conversations_page.return_value = None
        with pytest.raises(RuntimeError, match="Conversation refresh failed"):
            CodexGuestExperienceBatchService._hydrate_pending_messages(service, [service.main_session.get(Reservation, 101)])
    assert service.brain_session.query(ComprehensiveStayAnalysis).count() == 0


@pytest.mark.parametrize("now", [datetime(2026, 3, 8, 16), datetime(2026, 11, 1, 17)])
def test_noon_eastern_includes_western_checkout_across_dst(now):
    assert is_analysis_eligible(reservation(departure_date=now.date()), listing(check_out_time=16), reference_time=now)


def test_review_posted_timestamp_normalizes_timezone_and_rejects_update_or_date_only():
    assert review_posted_timestamp({"submittedAt": "2026-09-23T12:00:00-04:00"}) == (NOW, "submittedAt")
    assert review_posted_timestamp({"reviewDate": "2026-09-23", "updatedOn": "2026-09-23T16:00:00Z"}) == (None, None)


def test_scan_refresh_ignores_old_invalid_property_but_saves_recent_review(service):
    from sync.sync_reviews import sync_reviews
    session = service.main_session
    session.connection().exec_driver_sql("ATTACH DATABASE ':memory:' AS public")
    SyncLog.__table__.create(session.get_bind())
    rows = [
        {"id": 71, "listingMapId": 5, "reservationId": 101, "type": "guest-to-host",
         "status": "published", "submittedAt": "2026-09-23 16:00:00", "publicReview": "Good stay"},
        {"id": 72, "listingMapId": 99999, "type": "guest-to-host", "status": "published",
         "submittedAt": "2025-11-20 17:30:40", "updatedOn": "2026-09-23 16:00:00"},
    ]
    with patch("sync.sync_reviews.init_models"), patch("sync.sync_reviews.get_session", return_value=session), \
         patch("sync.sync_reviews.HostawayAPIClient"), \
         patch("sync.sync_reviews._fetch_reviews_for_sync", return_value=(rows, False, 0)), \
         patch("sync.sync_reviews.datetime", wraps=datetime) as clock:
        clock.utcnow.return_value = NOW + timedelta(hours=1)
        outcome = sync_reviews(full_sync=True, guest_posted_since=NOW - timedelta(hours=36), progress_tracker=MagicMock())
    assert outcome["status"] == "success"
    assert outcome["records_processed"] == 1
    assert outcome["errors"] == []
    assert session.get(Review, 71).posted_at == NOW
    assert session.get(Review, 72) is None
