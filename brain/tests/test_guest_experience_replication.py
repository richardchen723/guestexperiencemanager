import json
from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from brain.guest_experience_replication import (
    GuestExperienceReplicationError,
    GuestExperienceReplicationService,
    ProductionSshClient,
    ProductionSshConfig,
)
from brain.models import (
    ComprehensiveStayAnalysis,
    GuestExperienceAnalysisRun,
    GuestReviewIssueAnalysis,
    PropertyGuestIssue,
)


@compiles(JSONB, "sqlite")
def compile_jsonb_for_sqlite(_type, _compiler, **_kwargs):
    return "JSON"


def _session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def attach_brain(dbapi_connection, _connection_record):
        dbapi_connection.execute("ATTACH DATABASE ':memory:' AS brain")

    for table in (
        GuestExperienceAnalysisRun.__table__,
        ComprehensiveStayAnalysis.__table__,
        GuestReviewIssueAnalysis.__table__,
        PropertyGuestIssue.__table__,
    ):
        table.create(engine)
    return sessionmaker(bind=engine)()


def _seed_result_run(session, *, run_start=datetime(2026, 8, 22, 12, 0), old_window=False):
    run = GuestExperienceAnalysisRun(
        status="completed",
        window_start_at=datetime(2026, 5, 22, 12, 0) if old_window else datetime(2026, 7, 22, 12, 0),
        window_end_at=datetime(2026, 8, 21, 12, 0),
        eligible_stay_count=1,
        stays_analyzed=1,
        reviews_analyzed=1,
        details={"execution_provider": "codex-subscription"},
        started_at=run_start,
        completed_at=run_start,
    )
    session.add(run)
    session.flush()
    offset = int(run.run_id) * 100
    stay = ComprehensiveStayAnalysis(
        run_id=run.run_id,
        listing_id=10,
        reservation_id=offset + 1,
        arrival_date=date(2026, 8, 18),
        departure_date=date(2026, 8, 21),
        checkout_at=datetime(2026, 8, 21, 18, 0),
        eligible_at=datetime(2026, 8, 22, 18, 0),
        stay_quality="recovered",
        summary="The hot water issue was recovered.",
        detailed_summary="The guest reported no hot water and support restored it.",
        issue_count=1,
        message_count=4,
        guest_message_count=2,
        source_message_ids=[901, 902],
        input_hash=f"stay-hash-{offset}",
        prompt_version="guest-experience-stay-v1",
        model="codex-subscription",
        source_metadata={"analysis_lookback_months": 1},
        analyzed_at=run_start,
        created_at=run_start,
    )
    review = GuestReviewIssueAnalysis(
        run_id=run.run_id,
        review_id=offset + 2,
        listing_id=10,
        reservation_id=offset + 1,
        review_date=date(2026, 8, 22),
        summary="The guest requested a firmer pillow.",
        issue_count=1,
        has_public_review=True,
        has_private_feedback=True,
        input_hash=f"review-hash-{offset}",
        prompt_version="guest-experience-review-v1",
        model="codex-subscription",
        source_metadata={"analysis_lookback_months": 1},
        analyzed_at=run_start,
        created_at=run_start,
    )
    session.add_all((stay, review))
    session.flush()
    session.add_all((
        PropertyGuestIssue(
            source_kind="stay",
            source_issue_key=f"stay:{stay.reservation_id}:0",
            stay_analysis_id=stay.stay_analysis_id,
            listing_id=10,
            reservation_id=stay.reservation_id,
            source_date=date(2026, 8, 21),
            issue_category="essential_amenity",
            summary="No hot water",
            details="The guest reported no hot water.",
            suggested_improvement="Test hot water before arrival.",
            severity="material",
            resolution_state="resolved",
            source_references=[{"source_type": "message", "source_id": 901, "role": "complaint"}],
            workflow_status="resolved",
            resolution_comment="Local operator note must not overwrite production workflow.",
            created_at=run_start,
            updated_at=run_start,
        ),
        PropertyGuestIssue(
            source_kind="review",
            source_issue_key=f"review:{review.review_id}:0",
            review_analysis_id=review.review_analysis_id,
            listing_id=10,
            reservation_id=review.reservation_id,
            review_id=review.review_id,
            source_date=date(2026, 8, 22),
            issue_category="comfort",
            summary="Pillow comfort",
            details="The guest requested a firmer pillow.",
            severity="minor",
            source_references=[{"source_type": "review", "source_id": review.review_id, "source_part": "private_feedback"}],
            created_at=run_start,
            updated_at=run_start,
        ),
    ))
    session.commit()
    return run


def test_replication_is_idempotent_and_preserves_production_workflow():
    source = _session()
    destination = _session()
    run = _seed_result_run(source)
    payload = GuestExperienceReplicationService(source).export_run(run.run_id)

    assert payload["schema_version"] == 1
    assert "workflow_status" not in payload["issues"][0]
    first = GuestExperienceReplicationService(destination).import_payload(payload)

    assert first["stays_inserted"] == 1
    assert first["reviews_inserted"] == 1
    assert first["issues_inserted"] == 2
    production_issue = destination.query(PropertyGuestIssue).filter(
        PropertyGuestIssue.source_kind == "stay"
    ).one()
    assert production_issue.workflow_status == "open"
    production_issue.workflow_status = "resolved"
    production_issue.resolution_comment = "Resolved by the production operator."
    destination.commit()

    second = GuestExperienceReplicationService(destination).import_payload(payload)
    destination.refresh(production_issue)

    assert second["run_created"] == 0
    assert second["stays_existing"] == 1
    assert second["reviews_existing"] == 1
    assert second["issues_existing"] == 2
    assert production_issue.workflow_status == "resolved"
    assert production_issue.resolution_comment == "Resolved by the production operator."


def test_replication_rejects_an_existing_analysis_with_a_different_hash():
    source = _session()
    destination = _session()
    run = _seed_result_run(source)
    payload = GuestExperienceReplicationService(source).export_run(run.run_id)
    GuestExperienceReplicationService(destination).import_payload(payload)
    stay = destination.query(ComprehensiveStayAnalysis).one()
    stay.input_hash = "different-production-hash"
    destination.commit()

    with pytest.raises(GuestExperienceReplicationError, match="different input hash"):
        GuestExperienceReplicationService(destination).import_payload(payload)


def test_pending_runs_include_only_current_one_month_runs():
    session = _session()
    current = _seed_result_run(session)
    _seed_result_run(session, old_window=True)

    pending = GuestExperienceReplicationService(session).pending_run_ids(
        reference_time=datetime(2026, 8, 22, 12, 0)
    )

    assert pending == [current.run_id]


def test_ssh_client_streams_payload_over_stdin_not_command_arguments(tmp_path):
    identity = tmp_path / "production.pem"
    identity.write_text("test key", encoding="utf-8")
    client = ProductionSshClient(ProductionSshConfig(
        target="ubuntu@52.6.90.97",
        identity_file=identity,
    ))
    payload = {"schema_version": 1, "private_result": "never place this in argv"}
    remote_result = {"status": "completed", "stays_inserted": 1}

    with patch("brain.guest_experience_replication.subprocess.run") as run_mock:
        run_mock.return_value = SimpleNamespace(
            returncode=0,
            stdout=json.dumps(remote_result),
            stderr="",
        )
        result = client.import_payload(payload)

    argv = run_mock.call_args.args[0]
    assert result == remote_result
    assert "never place this in argv" not in " ".join(argv)
    assert json.loads(run_mock.call_args.kwargs["input"]) == payload
    assert "StrictHostKeyChecking=yes" in argv
    assert argv[-2] == "ubuntu@52.6.90.97"
    assert "sync-import --input -" in argv[-1]
