from datetime import date, datetime
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from brain.models import PropertyGuestIssue, PropertyGuestIssueNote
from dashboard.stay_issues.service import (
    _issue_matches_view,
    _sort_issues_by_priority,
    resolve_dashboard_window,
)
from dashboard.stay_issues.workflow import (
    GuestIssueWorkflowError,
    add_issue_note,
    change_issue_priority,
    change_issue_status,
    issue_priority,
    issue_reported_at,
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
    PropertyGuestIssueNote.__table__.create(engine)
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
    activity = session.query(PropertyGuestIssueNote).one()
    assert activity.note_type == "resolution"
    assert activity.body == "Cleaner returned with fresh linens and we confirmed with the guest."


def test_operator_can_move_issue_through_statuses_and_append_notes():
    session = _session()
    issue = _issue(session)

    scheduled, scheduled_note = change_issue_status(
        issue.issue_id,
        status="scheduled",
        user_id=7,
        now=datetime(2026, 8, 22, 10, 0),
        session=session,
    )
    assert scheduled.operational_status == "scheduled"
    assert scheduled_note.body == "Status changed from Need attention to Scheduled."

    in_progress, progress_note = change_issue_status(
        issue.issue_id,
        status="in_progress",
        note="Vendor arrived and started the repair.",
        user_id=8,
        now=datetime(2026, 8, 22, 11, 0),
        session=session,
    )
    assert in_progress.operational_status == "in_progress"
    assert progress_note.body == "Vendor arrived and started the repair."

    operator_note = add_issue_note(
        issue.issue_id,
        note="Replacement part is expected tomorrow.",
        user_id=9,
        now=datetime(2026, 8, 22, 12, 0),
        session=session,
    )
    assert operator_note.note_type == "operator"
    assert [row.body for row in session.query(PropertyGuestIssueNote).order_by(
        PropertyGuestIssueNote.created_at
    )] == [
        "Status changed from Need attention to Scheduled.",
        "Vendor arrived and started the repair.",
        "Replacement part is expected tomorrow.",
    ]


def test_operator_can_set_priority_with_actor_and_timestamp_audit():
    session = _session()
    issue = _issue(session)
    changed_at = datetime(2026, 8, 22, 10, 30)

    updated, activity = change_issue_priority(
        issue.issue_id,
        priority="critical",
        user_id=7,
        now=changed_at,
        session=session,
    )

    assert issue_priority(updated) == "Critical"
    assert updated.priority_updated_by_user_id == 7
    assert updated.priority_updated_at == changed_at
    assert activity.note_type == "priority_change"
    assert activity.author_user_id == 7
    assert activity.created_at == changed_at
    assert activity.body == "Priority changed from Medium to Critical."

    unchanged, duplicate_activity = change_issue_priority(
        issue.issue_id,
        priority="Critical",
        user_id=8,
        session=session,
    )
    assert issue_priority(unchanged) == "Critical"
    assert unchanged.priority_updated_by_user_id == 7
    assert duplicate_activity is None


def test_issue_priority_validation_and_urgent_first_sorting():
    session = _session()
    issue = _issue(session)
    try:
        change_issue_priority(
            issue.issue_id,
            priority="emergency",
            user_id=7,
            session=session,
        )
    except GuestIssueWorkflowError as exc:
        assert str(exc) == "Choose a valid issue priority."
    else:
        raise AssertionError("Invalid issue priority should be rejected")

    sorted_issues = _sort_issues_by_priority([
        {"issue_id": 1, "priority": "Low", "reported_at": datetime(2026, 8, 22, 8)},
        {"issue_id": 2, "priority": "Critical", "reported_at": datetime(2026, 8, 20, 9)},
        {"issue_id": 3, "priority": "High", "reported_at": datetime(2026, 8, 21, 10)},
        {"issue_id": 4, "priority": "Critical", "reported_at": datetime(2026, 8, 22, 11)},
    ])
    assert [row["issue_id"] for row in sorted_issues] == [4, 2, 3, 1]


def test_reported_timestamp_is_stable_when_operator_edits_issue():
    session = _session()
    issue = _issue(session)
    reported_at = datetime(2026, 8, 20, 14, 15)
    issue.created_at = reported_at
    session.commit()

    change_issue_priority(
        issue.issue_id,
        priority="High",
        user_id=7,
        now=datetime(2026, 8, 22, 10),
        session=session,
    )
    changed, _activity = change_issue_status(
        issue.issue_id,
        status="in_progress",
        user_id=7,
        now=datetime(2026, 8, 22, 11),
        session=session,
    )

    assert issue_reported_at(changed) == reported_at
    assert changed.created_at == reported_at
    assert changed.updated_at == datetime(2026, 8, 22, 11)


def test_issue_status_and_notes_validate_operator_input():
    session = _session()
    issue = _issue(session)

    for operation, expected_message in (
        (
            lambda: change_issue_status(
                issue.issue_id,
                status="waiting_forever",
                user_id=7,
                session=session,
            ),
            "Choose a valid issue status.",
        ),
        (
            lambda: add_issue_note(
                issue.issue_id,
                note="   ",
                user_id=7,
                session=session,
            ),
            "Add a note before posting.",
        ),
    ):
        try:
            operation()
        except GuestIssueWorkflowError as exc:
            assert str(exc) == expected_message
        else:
            raise AssertionError("Invalid operator input should be rejected")


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
    assert linked.operational_status == "need_attention"
    assert linked.linked_ticket_id == 44

    closed = sync_issue_from_ticket_status(
        44,
        ticket_status="Closed",
        user_id=8,
        now=datetime(2026, 8, 23, 11, 0),
        session=session,
    )
    assert closed.workflow_status == "resolved"
    assert closed.operational_status == "resolved"
    assert closed.resolution_method == "ticket"
    assert "ticket #44" in closed.resolution_comment

    reopened = sync_issue_from_ticket_status(
        44,
        ticket_status="In Progress",
        user_id=8,
        session=session,
    )
    assert reopened.workflow_status == "ticketed"
    assert reopened.operational_status == "in_progress"
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

    try:
        change_issue_status(
            issue.issue_id,
            status="stuck",
            user_id=7,
            session=session,
        )
    except GuestIssueWorkflowError as exc:
        assert exc.status_code == 409
        assert "follows linked ticket" in str(exc)
    else:
        raise AssertionError("Linked issue status should follow its ticket")


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


def test_issue_status_filter_and_note_thread_are_wired_without_page_navigation():
    project_root = Path(__file__).resolve().parents[2]
    script = (project_root / "dashboard/static/js/guest-issues.js").read_text()
    template = (project_root / "dashboard/templates/stay_issues/index.html").read_text()

    assert "guestIssueStatus" in script
    assert "matchesStatus" in script
    assert "renderStatusCounts" in script
    assert "statusCounts.all" in script
    assert "/status`" in script
    assert "/notes`" in script
    assert "appendIssueNote" in script
    assert "data-status-label" in template
    assert "data-issue-status" in template
    assert "data-issue-note-form" in template
    assert "data-note-list" in template
    assert "data-note-preview" in template
    assert '<details class="issue-note-thread">' in template


def test_issue_priority_controls_filter_sort_and_audit_are_wired():
    project_root = Path(__file__).resolve().parents[2]
    script = (project_root / "dashboard/static/js/guest-issues.js").read_text()
    template = (project_root / "dashboard/templates/stay_issues/index.html").read_text()
    routes = (project_root / "dashboard/stay_issues/routes.py").read_text()
    ticket_routes = (project_root / "dashboard/tickets/routes.py").read_text()

    for element_id in ("guestIssuePriority", "guestIssueSort"):
        assert f'id="{element_id}"' in template
    for marker in (
        "data-priority=",
        "data-priority-pill",
        "data-issue-priority",
        "data-priority-audit",
        "data-priority-error",
    ):
        assert marker in template
    assert "priorityRank" in script
    assert "sortIssueCards" in script
    assert "matchesPriority" in script
    assert "/priority`" in script
    assert '"/api/issues/<int:issue_id>/priority"' in routes
    assert "change_issue_priority" in routes
    assert "issue.get('priority')" in ticket_routes


def test_reported_timestamp_recency_presets_custom_range_and_sort_are_wired():
    project_root = Path(__file__).resolve().parents[2]
    script = (project_root / "dashboard/static/js/guest-issues.js").read_text()
    template = (project_root / "dashboard/templates/stay_issues/index.html").read_text()

    for element_id in (
        "guestIssueReportedFrom",
        "guestIssueReportedTo",
        "guestIssueReportedError",
    ):
        assert f'id="{element_id}"' in template
    for marker in (
        'data-reported-preset="today"',
        'data-reported-preset="24h"',
        'data-reported-preset="7d"',
        'data-reported-preset="30d"',
        "data-reported-custom-toggle",
        "data-clear-reported",
        "data-reset-issue-filters",
        "data-reported-at=",
        "data-reported-time",
    ):
        assert marker in template
    assert 'value="reported_desc"' in template
    assert 'value="reported_asc"' in template
    assert "reportedRange" in script
    assert "matchesReported" in script
    assert "localDateBoundary" in script
    assert "To date must be on or after From date." in script
    assert "localizeReportedTimes" in script


def test_guest_issue_card_reports_date_without_time_or_timezone():
    project_root = Path(__file__).resolve().parents[2]
    script = (project_root / "dashboard/static/js/guest-issues.js").read_text()
    service = (project_root / "dashboard/stay_issues/service.py").read_text()
    template = (project_root / "dashboard/templates/stay_issues/index.html").read_text()

    reported_formatter = script.split("const localizeReportedTimes", 1)[1].split("};", 1)[0]

    assert 'class="issue-reported-summary">Reported <time' in template
    assert "month: 'short', day: 'numeric', year: 'numeric'" in reported_formatter
    assert "hour:" not in reported_formatter
    assert "minute:" not in reported_formatter
    assert "timeZoneName:" not in reported_formatter
    assert 'reported_at.strftime("%b %-d, %Y")' in service
    assert "%p UTC" not in service
