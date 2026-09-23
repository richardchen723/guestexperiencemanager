from copy import deepcopy
from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from brain.guest_experience import build_stay_input, normalize_review_result, normalize_stay_result
from brain.guest_experience_codex import CodexGuestExperienceBatchService
from brain.guest_experience_replication import GuestExperienceReplicationService
from brain.guest_issue_identity import (
    apply_complaint_assignments, backfill_complaint_identities,
    complaint_catalog, normalize_complaint_key,
)
from brain.models import GuestExperienceAnalysisRun, PropertyGuestIssue
from brain.tests.test_guest_experience_replication import _session, _seed_result_run
from brain.tests.test_guest_experience import listing, message, reservation
from dashboard.stay_issues.grouping import group_issue_reports


def assignment(row, key="bathroom-hot-water-unavailable"):
    return {name: row[name] for name in ("source_kind", "source_issue_key", "input_hash")} | {
        "complaint_key": key,
    }


def test_normalizers_preserve_ai_identity_with_valid_evidence():
    issue = {"summary": "No hot water", "complaint_key": "bathroom-hot-water-unavailable"}
    stay = normalize_stay_result({"issues": [issue | {"source_references": [
        {"source_id": 11, "role": "complaint"},
    ]}]}, valid_messages={11: "guest"})
    review = normalize_review_result({"issues": [issue | {"source_references": [
        {"source_id": 77, "source_part": "private_feedback"},
    ]}]}, review_id=77)
    assert stay["issues"][0]["complaint_key"] == review["issues"][0]["complaint_key"] == issue["complaint_key"]


def test_stay_and_review_ingestion_persist_the_same_complaint_identity():
    session = _session()
    service = CodexGuestExperienceBatchService(brain_session=session, main_session=MagicMock())
    stay = reservation()
    prepared = build_stay_input(stay, listing(), [
        message(11, datetime(2026, 8, 19), True, "There was no showerhead."),
    ], analyzed_at=datetime(2026, 8, 22))
    key = "bathroom-showerhead-missing"
    result = normalize_stay_result({"issues": [{
        "summary": "Showerhead absent", "issue_category": "plumbing", "complaint_key": key,
        "source_references": [{"source_id": 11, "role": "complaint"}],
    }]}, valid_messages={11: "guest"})
    service._store_stay(None, stay, prepared, result, model="codex-subscription")
    review = SimpleNamespace(review_id=77, listing_id=stay.listing_id, reservation_id=stay.reservation_id,
                             review_date=date(2026, 8, 22), overall_rating=8)
    result = normalize_review_result({"issues": [{
        "summary": "Bathroom shower attachment missing", "issue_category": "shower_fixture", "complaint_key": key,
        "source_references": [{"source_id": 77, "source_part": "public_review"}],
    }]}, review_id=77)
    service._store_review(None, review, {
        "has_public_review": True, "has_private_feedback": False,
        "input_hash": "review-hash", "sub_rating_count": 0,
    }, result, {stay.reservation_id: stay})
    session.commit()
    rows = session.query(PropertyGuestIssue).all()
    assert len(rows) == 1
    assert {ref["source_type"] for ref in rows[0].source_references} == {"message", "review"}
    assert {row.complaint_key for row in rows} == {key}
    assert len(group_issue_reports(rows)) == 1


@pytest.mark.parametrize("value", ["", " ", "Plumbing", "a" * 161, 123, ["key"]])
def test_invalid_identity_is_rejected_not_silently_normalized(value):
    with pytest.raises(ValueError, match="complaint_key"):
        normalize_complaint_key(value)


def test_catalog_is_property_scoped_and_assignments_validate_before_mutating():
    session = _session()
    _seed_result_run(session)
    catalog = complaint_catalog(session, [10])
    assert len(catalog) == 2
    assert complaint_catalog(session, [11]) == []
    assignments = [assignment(row) for row in catalog]
    invalid = deepcopy(assignments)
    invalid[-1]["input_hash"] = "stale"
    with pytest.raises(ValueError, match="evidence changed"):
        apply_complaint_assignments(session, invalid)
    assert all(row.complaint_key is None for row in session.query(PropertyGuestIssue))
    expected = {(row["source_kind"], row["source_issue_key"]): row["input_hash"] for row in catalog}
    with pytest.raises(ValueError, match="every unclassified"):
        apply_complaint_assignments(session, assignments[:1], expected=expected)
    with pytest.raises(ValueError, match="not exported"):
        apply_complaint_assignments(session, assignments, expected={})
    apply_complaint_assignments(session, assignments, expected=expected)
    assert len(apply_complaint_assignments(session, assignments, expected=expected)) == 0
    with pytest.raises(ValueError, match="stale batch"):
        apply_complaint_assignments(session, [assignment(catalog[0], "different")])


def test_export_supplies_matching_instructions_and_existing_complaints():
    session = _session()
    _seed_result_run(session)
    service = CodexGuestExperienceBatchService(brain_session=session, main_session=MagicMock())
    review = MagicMock(review_id=77, listing_id=10)
    meta = {"eligible": 1, "already_analyzed": 0, "backlog": 0}
    with patch.object(service, "_lock"), patch.object(
        service, "_expire_and_collect_inflight", return_value=(set(), set()),
    ), patch.object(service, "_eligible_stays", return_value=([], meta)), patch.object(
        service, "_eligible_reviews", return_value=([(review, {"payload": {"review_id": 77}, "input_hash": "hash"})], meta),
    ):
        payload = service.export_batch(reference_time=datetime(2026, 8, 23))
    assert len(payload["complaint_catalog"]) == 2
    assert "not issue_category" in payload["instructions"]["complaint_matching"]
    run = session.get(GuestExperienceAnalysisRun, payload["run_id"])
    assert len(run.details["complaint_catalog_pending"]) == 2
    assert run.details["complaint_matching_version"] == 1
    with patch.object(service, "_lock"), pytest.raises(ValueError, match="requires complaint_key"):
        service.import_results({"schema_version": 1, "run_id": run.run_id,
                                "reviews": [{"review_id": 77, "issues": [{"summary": "No hot water"}]}]})


def test_backfill_and_replication_preserve_production_resolution():
    source, destination = _session(), _session()
    run = _seed_result_run(source)
    replication = GuestExperienceReplicationService(source)
    GuestExperienceReplicationService(destination).import_payload(replication.export_run(run.run_id))
    production_issue = destination.query(PropertyGuestIssue).filter_by(source_kind="stay").one()
    production_issue.workflow_status = "resolved"
    production_issue.operational_status = "resolved"
    production_issue.resolved_at = datetime(2026, 9, 10)
    production_issue.resolution_comment = "Repaired by operator"
    production_issue.linked_ticket_id = 55
    destination.commit()

    catalog = complaint_catalog(source, [10])
    payload = {"complaint_assignments": [assignment(row) for row in catalog]}
    result = backfill_complaint_identities(source, payload, catalog)
    assert result == {"issues_updated": 2, "run_ids_to_sync": [run.run_id]}
    assert source.get(GuestExperienceAnalysisRun, run.run_id).details["production_sync"]["status"] == "pending"
    first = GuestExperienceReplicationService(destination).import_payload(replication.export_run(run.run_id))
    assert first["issues_existing"] == 2
    destination.refresh(production_issue)
    assert production_issue.complaint_key == "bathroom-hot-water-unavailable"
    assert production_issue.workflow_status == "resolved"
    assert production_issue.resolution_comment == "Repaired by operator"
    assert production_issue.linked_ticket_id == 55
    assert len(group_issue_reports(destination.query(PropertyGuestIssue).all())) == 2
    # Retrying old payloads that lack identity metadata must not erase the backfill.
    old = replication.export_run(run.run_id)
    old["schema_version"] = 1
    for row in old["issues"]:
        row.pop("complaint_key")
    GuestExperienceReplicationService(destination).import_payload(old)
    destination.refresh(production_issue)
    assert production_issue.complaint_key == "bathroom-hot-water-unavailable"


def test_batch_can_replicate_identities_for_reports_from_an_earlier_run():
    source, destination = _session(), _session()
    previous = _seed_result_run(source)
    replication = GuestExperienceReplicationService(source)
    GuestExperienceReplicationService(destination).import_payload(replication.export_run(previous.run_id))
    catalog = complaint_catalog(source, [10])
    current = _seed_result_run(source)
    assignments = [assignment(row) for row in catalog]
    apply_complaint_assignments(source, assignments)
    current.details = dict(current.details or {}) | {"complaint_assignments": assignments}
    source.commit()
    payload = replication.export_run(current.run_id)
    assert payload["complaint_assignments"] == assignments
    GuestExperienceReplicationService(destination).import_payload(payload)
    assert destination.query(PropertyGuestIssue).filter_by(
        source_issue_key=catalog[0]["source_issue_key"],
    ).one().complaint_key == "bathroom-hot-water-unavailable"


def test_failed_identity_replication_rolls_back_the_entire_batch():
    source, destination = _session(), _session()
    run = _seed_result_run(source)
    payload = GuestExperienceReplicationService(source).export_run(run.run_id)
    payload["complaint_assignments"] = [{
        "source_kind": "stay", "source_issue_key": "stay:101:0",
        "input_hash": "wrong", "complaint_key": "bathroom-hot-water-unavailable",
    }]
    with pytest.raises(ValueError, match="evidence changed"):
        GuestExperienceReplicationService(destination).import_payload(payload)
    assert destination.query(PropertyGuestIssue).count() == 0
    assert destination.query(GuestExperienceAnalysisRun).count() == 0
