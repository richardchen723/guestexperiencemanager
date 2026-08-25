from datetime import date, datetime
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from brain.models import PropertyGuestIssue
from dashboard.stay_issues.service import _issue_matches_view, resolve_dashboard_window
from dashboard.stay_issues.workflow import (
    GuestIssueWorkflowError,
    link_issue_to_ticket,
    resolve_issue,
    sync_issue_from_ticket_status,
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

    PropertyGuestIssue.__table__.create(engine)
    return sessionmaker(bind=engine)()


def _issue(session):
    issue = PropertyGuestIssue(
        source_kind="stay",
        source_issue_key="stay-1-issue-1",
        listing_id=101,
        reservation_id=201,
        source_date=date(2026, 8, 1),
        issue_category="cleanliness",
        summary="Missing linens",
        details="The guest reported that linens were missing.",
        severity="material",
        resolution_state="unresolved",
        source_references=[],
    )
    session.add(issue)
    session.commit()
    return issue


def test_quick_resolution_requires_and_records_operator_note():
    session = _session()
    issue = _issue(session)
    resolved_at = datetime(2026, 8, 22, 9, 0)

    resolved = resolve_issue(
        issue.issue_id,
        comment="  Cleaner returned with fresh linens and we confirmed with the guest.  ",
        user_id=7,
        now=resolved_at,
        session=session,
    )

    assert resolved.workflow_status == "resolved"
    assert resolved.resolution_method == "quick"
    assert resolved.resolution_comment == "Cleaner returned with fresh linens and we confirmed with the guest."
    assert resolved.resolved_by_user_id == 7
    assert resolved.resolved_at == resolved_at


def test_linked_ticket_closure_resolves_and_reopening_reopens_issue():
    session = _session()
    issue = _issue(session)

    linked = link_issue_to_ticket(
        issue.issue_id,
        ticket_id=44,
        ticket_status="Open",
        user_id=7,
        session=session,
    )
    assert linked.workflow_status == "ticketed"
    assert linked.linked_ticket_id == 44

    closed = sync_issue_from_ticket_status(
        44,
        ticket_status="Closed",
        user_id=8,
        now=datetime(2026, 8, 23, 11, 0),
        session=session,
    )
    assert closed.workflow_status == "resolved"
    assert closed.resolution_method == "ticket"
    assert "ticket #44" in closed.resolution_comment

    reopened = sync_issue_from_ticket_status(
        44,
        ticket_status="In Progress",
        user_id=8,
        session=session,
    )
    assert reopened.workflow_status == "ticketed"
    assert reopened.resolved_at is None
    assert reopened.resolution_comment is None


def test_ticketed_issue_cannot_be_quick_resolved():
    session = _session()
    issue = _issue(session)
    link_issue_to_ticket(
        issue.issue_id,
        ticket_id=45,
        ticket_status="Assigned",
        user_id=7,
        session=session,
    )

    try:
        resolve_issue(
            issue.issue_id,
            comment="Done",
            user_id=7,
            session=session,
        )
    except GuestIssueWorkflowError as exc:
        assert exc.status_code == 409
    else:
        raise AssertionError("Ticketed issue should not support quick resolution")


def test_archive_view_is_separate_from_recently_resolved():
    recent = {"workflow_status": "resolved", "is_archived": False}
    archived = {"workflow_status": "resolved", "is_archived": True}
    active = {"workflow_status": "open", "is_archived": False}

    assert _issue_matches_view(active, "active")
    assert _issue_matches_view(recent, "resolved")
    assert _issue_matches_view(archived, "archived")
    assert not _issue_matches_view(archived, "resolved")


def test_dashboard_window_presets_end_at_the_analysis_cutoff():
    now = datetime(2026, 8, 22, 12, 0)

    one_month = resolve_dashboard_window(now, window_key="1m")
    one_week = resolve_dashboard_window(now, window_key="1w")
    invalid_window = resolve_dashboard_window(now, window_key="3m")

    assert one_month["start"] == datetime(2026, 7, 22, 12, 0)
    assert one_month["end"] == datetime(2026, 8, 21, 12, 0)
    assert one_week["start"] == datetime(2026, 8, 14, 12, 0)
    assert one_week["end"] == one_month["end"]
    assert invalid_window["key"] == "1m"
    assert invalid_window["start"] == one_month["start"]


def test_custom_dashboard_window_is_inclusive_and_bounded_by_retained_data():
    now = datetime(2026, 8, 22, 12, 0)

    selected = resolve_dashboard_window(
        now,
        window_key="custom",
        start_date="2026-08-01",
        end_date="2026-08-10",
    )
    bounded = resolve_dashboard_window(
        now,
        window_key="custom",
        start_date="2025-01-01",
        end_date="2027-01-01",
    )

    assert selected["start"] == datetime(2026, 8, 1, 0, 0)
    assert selected["end"] == datetime(2026, 8, 10, 23, 59, 59, 999999)
    assert selected["notice"] is None
    assert bounded["start"] == datetime(2026, 7, 22, 12, 0)
    assert bounded["end"] == datetime(2026, 8, 21, 12, 0)
    assert bounded["notice"] == "Custom dates are limited to the analyzed one-month range."


def test_quick_resolution_updates_the_active_queue_without_redirecting():
    project_root = Path(__file__).resolve().parents[2]
    script = (project_root / "dashboard/static/js/guest-issues.js").read_text()
    template = (project_root / "dashboard/templates/stay_issues/index.html").read_text()

    assert "window.location.href" not in script
    assert "resolvedIssue?.remove()" in script
    assert "window.scrollTo(0, scrollPosition)" in script
    assert "adjustCount('[data-active-issue-count]', -1)" in script
    assert "adjustCount('[data-resolved-issue-count]', 1)" in script
    assert "adjustCount('[data-open-issue-count]', -1)" in script
    assert "data-active-issue-count" in template
    assert "data-resolved-issue-count" in template
    assert "data-open-issue-count" in template
