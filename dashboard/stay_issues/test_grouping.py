from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from jinja2 import ChoiceLoader, DictLoader, Environment, FileSystemLoader, select_autoescape
from pathlib import Path

from brain.models import (
    ComprehensiveStayAnalysis, GuestExperienceAnalysisRun, GuestReviewIssueAnalysis,
    PropertyGuestIssue, PropertyGuestIssueNote,
)
from database.models import Listing, ListingPhoto, ListingTag, Tag
from dashboard.stay_issues.grouping import group_issue_reports, report_count
from dashboard.stay_issues.service import GuestIssueDashboardService
from dashboard.stay_issues.test_workflow import _session
from dashboard.stay_issues.workflow import (
    GuestIssueWorkflowError, add_issue_note, change_issue_priority, change_issue_status, get_issue_context,
    link_issue_to_ticket, resolve_issue, sync_issue_from_ticket_status, unlink_issue_from_ticket,
)


def report(issue_id, **overrides):
    values = dict(
        issue_id=issue_id, listing_id=101, reservation_id=200 + issue_id,
        source_kind="stay", source_issue_key=f"stay-{issue_id}",
        stay_analysis_id=None, review_id=None,
        source_date=date(2026, 9, 1), issue_category="maintenance",
        summary="Loose tiles on the primary bathroom floor",
        details="The guest reported loose bathroom floor tiles.",
        severity="material", resolution_state="unresolved", source_references=[],
        workflow_status="open", operational_status="need_attention",
        linked_ticket_id=None, resolved_at=None,
    )
    values.update(overrides)
    return PropertyGuestIssue(**values)


def screenshot_reports():
    return [
        report(1, source_date=date(2026, 9, 7), source_references=[
            {"source_type": "message", "source_id": 501, "direction": "guest"},
        ]),
        report(2, summary="Primary bathroom floor tiles were loose.", source_kind="review",
               reservation_id=201, review_id=601, source_date=date(2026, 9, 6),
               source_references=[{"source_type": "review", "source_id": 601,
                                   "source_part": "private_feedback"}]),
        report(3, summary="Loose bathroom floor tile", issue_category="maintenance_safety",
               source_date=date(2026, 8, 31), severity="critical", operational_status="stuck",
               details="The guest reported a loose floor tile in the bathroom adjoining the king bedroom.",
               source_references=[{"source_type": "message", "source_id": 503}]),
    ]


def test_screenshot_reports_group_across_wording_categories_and_sources():
    reports = screenshot_reports()
    groups = group_issue_reports(reports)
    assert len(groups) == 1
    assert {row.issue_id for row in groups[0]} == {1, 2, 3}
    assert report_count(groups[0]) == 2  # A review and messages from one stay count once.
    assert group_issue_reports(list(reversed(reports))) == groups


def test_identical_complaints_group_despite_unrelated_ai_categories():
    reports = [
        report(1, issue_category="plumbing", summary="Showerhead was missing at arrival."),
        report(2, issue_category="shower_fixture", summary="Showerhead was missing at arrival.",
               source_kind="review", reservation_id=201),
    ]
    groups = group_issue_reports(reports)
    assert len(groups) == 1
    assert report_count(groups[0]) == 1  # Same stay described in messages and a review.


def test_saved_ai_identity_groups_paraphrases_across_categories_and_sources():
    reports = [
        report(1, summary="Water pooled around our ankles while washing.",
               issue_category="plumbing", complaint_key="bathroom-shower-drain-blocked"),
        report(2, summary="Shower drainage was obstructed.", source_kind="review",
               issue_category="bathroom_maintenance", complaint_key="bathroom-shower-drain-blocked"),
    ]
    groups = group_issue_reports(reports)
    assert len(groups) == 1
    assert report_count(groups[0]) == 2
    assert group_issue_reports(list(reversed(reports))) == groups


def test_ai_identity_distinguishes_similar_titles_using_evidence():
    reports = [
        report(1, summary="Bathroom fixture was broken", details="The showerhead was damaged.",
               complaint_key="bathroom-showerhead-broken"),
        report(2, summary="Bathroom fixture was broken", details="The toilet seat was cracked.",
               complaint_key="bathroom-toilet-seat-cracked"),
    ]
    assert len(group_issue_reports(reports)) == 2


@pytest.mark.parametrize("overrides", [
    {"listing_id": 102},
    {"workflow_status": "resolved", "resolved_at": datetime(2026, 9, 2)},
    {"linked_ticket_id": 12},
])
def test_ai_identity_never_crosses_property_resolution_or_ticket_boundaries(overrides):
    reports = [report(1, complaint_key="primary-bathroom-floor-tiles-loose", linked_ticket_id=11),
               report(2, complaint_key="primary-bathroom-floor-tiles-loose", **overrides)]
    assert len(group_issue_reports(reports)) == 2


def test_semantically_grouped_complaints_share_workflow_and_later_reports_stay_separate():
    session = _session()
    reports = [
        report(1, summary="Water pooled around our ankles while washing.",
               issue_category="plumbing", complaint_key="bathroom-shower-drain-blocked"),
        report(2, summary="Shower drainage was obstructed.",
               issue_category="shower_fixture", complaint_key="bathroom-shower-drain-blocked"),
    ]
    session.add_all(reports)
    session.commit()
    assert get_issue_context(2, session=session)["report_count"] == 2
    change_issue_status(2, status="in_progress", user_id=7, session=session)
    assert all(row.operational_status == "in_progress" for row in reports)
    resolve_issue(2, comment="Drain cleared", user_id=7, session=session)
    assert all(row.workflow_status == "resolved" for row in reports)
    later = report(3, summary="Drain blocked again", complaint_key="bathroom-shower-drain-blocked")
    session.add(later)
    session.commit()
    assert len(group_issue_reports([*reports, later])) == 2
    assert later.workflow_status == "open"
    session.close()


@pytest.mark.parametrize("overrides", [
    {"listing_id": 102},
    {"summary": "Loose tiles on the kitchen floor"},
    {"summary": "Loose tiles on the secondary bathroom floor"},
    {"summary": "Cracked tiles on the primary bathroom floor"},
    {"summary": "Loose tiles on the primary bathroom wall"},
    {"workflow_status": "resolved", "resolved_at": datetime(2026, 9, 2)},
])
def test_different_problems_properties_locations_and_repairs_stay_separate(overrides):
    assert len(group_issue_reports([report(1), report(2, **overrides)])) == 2


def test_vague_report_does_not_bridge_two_bathrooms_or_existing_tickets():
    reports = [report(1), report(2, summary="Loose tiles on the secondary bathroom floor"),
               report(3, summary="Loose bathroom floor tile")]
    assert len(group_issue_reports(reports)) == 3
    assert len(group_issue_reports([report(1, linked_ticket_id=11),
                                    report(2, linked_ticket_id=12), report(3)])) == 3


def test_report_count_handles_missing_reservations_and_repeated_review_parts():
    assert report_count([report(1, reservation_id=None, review_id=15),
                         report(2, reservation_id=None, review_id=15),
                         report(3, reservation_id=None), report(4, reservation_id=None)]) == 3


def test_status_notes_and_resolution_apply_to_one_group_and_keep_evidence():
    session = _session()
    reports = screenshot_reports()
    unrelated = report(4, summary="Dishwasher is broken")
    session.add_all([*reports, unrelated])
    session.commit()
    issue, status_note = change_issue_status(3, status="scheduled", user_id=7, session=session)
    assert issue.issue_id == 1
    assert status_note.body == "Status changed from Stuck to Scheduled."
    assert all(row.operational_status == "scheduled" for row in reports)
    assert unrelated.operational_status == "need_attention"
    note = add_issue_note(2, note="Contractor booked", user_id=7, session=session)
    assert note.issue_id == 1
    context = get_issue_context(3, session=session)
    assert context["report_count"] == 2
    assert len(context["references"]) == 3
    resolved_at = datetime(2026, 9, 8, 12)
    resolve_issue(3, comment="Tiles repaired and inspected", user_id=7, now=resolved_at, session=session)
    assert all(row.workflow_status == "resolved" and row.resolved_at == resolved_at for row in reports)
    assert len(group_issue_reports(reports)) == 1
    assert unrelated.workflow_status == "open"
    assert session.query(PropertyGuestIssue).count() == 4
    assert session.query(PropertyGuestIssueNote).count() == 3
    # A recurrence after the completed repair becomes new work.
    later = report(5, source_date=date(2026, 9, 9))
    session.add(later)
    session.commit()
    assert len(group_issue_reports([*reports, later])) == 2
    session.close()


def test_ticket_workflow_includes_existing_and_later_reports():
    session = _session()
    reports = screenshot_reports()
    session.add_all(reports)
    session.commit()
    link_issue_to_ticket(2, ticket_id=55, ticket_status="Assigned", user_id=7, session=session)
    assert all(row.linked_ticket_id == 55 for row in reports)
    later = report(4)
    session.add(later)
    session.commit()
    with pytest.raises(GuestIssueWorkflowError, match="tied to a ticket"):
        resolve_issue(4, comment="Done", user_id=7, session=session)
    sync_issue_from_ticket_status(55, ticket_status="Closed", user_id=7, session=session)
    assert all(row.workflow_status == "resolved" and row.linked_ticket_id == 55 for row in [*reports, later])
    assert len(group_issue_reports([*reports, later])) == 1
    sync_issue_from_ticket_status(55, ticket_status="In Progress", user_id=7, session=session)
    assert all(row.operational_status == "in_progress" for row in [*reports, later])
    unlink_issue_from_ticket(55, session=session)
    assert all(row.workflow_status == "open" and row.linked_ticket_id is None for row in [*reports, later])
    session.close()


def test_group_keeps_highest_priority_and_priority_changes_update_every_report():
    service = dashboard_fixture()
    session = service.brain_session
    reports = session.query(PropertyGuestIssue).filter(PropertyGuestIssue.issue_id.in_([1, 2, 3])).all()
    reports[0].priority = "Low"
    reports[1].priority = "Critical"
    reports[2].priority = "High"
    reports[0].created_at = datetime(2026, 9, 7, 12)
    reports[2].created_at = datetime(2026, 8, 31, 12)
    session.commit()
    dashboard = service.get_dashboard()
    grouped = dashboard["portfolios"][0]["units"][0]["issues"][0]
    assert grouped["priority"] == "Critical"
    assert grouped["reported_at"] == datetime(2026, 8, 31, 12)
    assert dashboard["summary"]["priority_counts"]["critical"] == 1
    assert get_issue_context(3, session=session)["priority"] == "Critical"
    updated, note = change_issue_priority(3, priority="Medium", user_id=7, session=session)
    assert updated.issue_id == 1
    assert all(row.priority == "Medium" for row in reports)
    assert all(row.priority_updated_by_user_id == 7 for row in reports)
    assert note.body == "Priority changed from Critical to Medium."
    session.close()


def dashboard_fixture():
    session = _session()
    for model in (Listing, ListingPhoto, ListingTag, Tag,
                  ComprehensiveStayAnalysis, GuestReviewIssueAnalysis, GuestExperienceAnalysisRun):
        model.__table__.create(session.bind)
    session.add(Listing(listing_id=101, name="Garden House", internal_listing_name="Garden House", status="active"))
    session.add_all(screenshot_reports())
    session.add(report(4, summary="Dishwasher is broken", source_date=date(2026, 9, 8),
                       details="The dishwasher did not start when the guest tried to run a wash cycle."))
    session.commit()
    return GuestIssueDashboardService(main_session=session, brain_session=session, now=datetime(2026, 9, 9, 12))


def render_dashboard(dashboard):
    root = Path(__file__).resolve().parents[2]
    env = Environment(loader=ChoiceLoader([
        DictLoader({"base.html": '<!doctype html><meta name="viewport" content="width=device-width, initial-scale=1"><title>Guest issues preview</title><style>body{font-family:Arial,sans-serif;background:#f5f7f5;margin:20px}*{box-sizing:border-box}</style>{% block extra_head %}{% endblock %}{% block content %}{% endblock %}{% block extra_scripts %}{% endblock %}'}),
        FileSystemLoader(root / "dashboard/templates"),
    ]), autoescape=select_autoescape())
    def url_for(endpoint, **kwargs):
        if endpoint == "static":
            return "/static/" + kwargs["filename"]
        from urllib.parse import urlencode
        return "/workspace/guest-issues/?" + urlencode(kwargs)
    return env.get_template("stay_issues/index.html").render(dashboard=dashboard, url_for=url_for)


def test_dashboard_counts_sorting_windows_history_and_rendering(monkeypatch):
    service = dashboard_fixture()
    dashboard = service.get_dashboard()
    issues = dashboard["portfolios"][0]["units"][0]["issues"]
    assert [row["report_count"] for row in issues] == [2, 1]
    assert dashboard["summary"]["active_issue_count"] == 2
    assert dashboard["summary"]["status_counts"]["stuck"] == 1
    assert dashboard["summary"]["review_issue_count"] == 1
    grouped = issues[0]
    assert grouped["issue_ids"] == [1, 2, 3]
    assert grouped["source_kinds"] == ["review", "stay"]
    assert len(grouped["references"]) == 3
    assert len(grouped["reports"]) == 3
    assert grouped["severity"] == "critical"
    weekly = service.get_dashboard(window_key="1w")
    weekly_issues = weekly["portfolios"][0]["units"][0]["issues"]
    assert [row["issue_id"] for row in weekly_issues] == [4, 1]
    assert weekly_issues[1]["report_count"] == 1
    # The canonical action target stays valid when only another report is in range.
    custom = service.get_dashboard(window_key="custom", start_date="2026-08-31", end_date="2026-08-31")
    selected = custom["portfolios"][0]["units"][0]["issues"][0]
    assert selected["issue_id"] == 1
    assert selected["source_date"] == date(2026, 8, 31)
    html = render_dashboard(dashboard)
    assert html.count("data-report-count=") == 2
    assert 'data-source="review stay"' in html
    assert "Most reported" in html and "Report history" in html
    assert html.count("/sources/messages/501") == 2  # Evidence list and report history.
    user_session = MagicMock()
    user_session.query.return_value.filter.return_value.all.return_value = [SimpleNamespace(user_id=7, name="Alex", email=None)]
    monkeypatch.setattr("dashboard.stay_issues.service.get_user_session", lambda: user_session)
    resolve_issue(1, comment="Tiles fixed", user_id=7, now=service.now, session=service.brain_session)
    resolved = service.get_dashboard(view="resolved")
    assert resolved["summary"]["active_issue_count"] == 1
    assert resolved["summary"]["recently_resolved_count"] == 1
    assert resolved["portfolios"][0]["units"][0]["issues"][0]["report_count"] == 2
    service.brain_session.close()


def test_scan_history_includes_today_before_checkout_and_displays_eastern_time():
    service = dashboard_fixture()
    service.brain_session.add(ComprehensiveStayAnalysis(
        listing_id=101, reservation_id=501, arrival_date=date(2026, 9, 7), departure_date=date(2026, 9, 9),
        checkout_at=datetime(2026, 9, 9, 18), eligible_at=datetime(2026, 9, 9, 4),
        stay_quality="smooth", summary="Smooth stay", detailed_summary="Smooth stay", issue_count=0,
        message_count=1, guest_message_count=1, source_message_ids=[123], input_hash="test",
        prompt_version="test", analyzed_at=datetime(2026, 9, 9, 12), source_metadata={"scan_version": 2},
    ))
    service.brain_session.commit()
    dashboard = service.get_dashboard()
    assert len(dashboard["scanned_stays"]) == 1
    assert dashboard["scanned_stays"][0]["last_scanned"].hour == 8
    html = render_dashboard(dashboard)
    assert "Last scanned (Eastern)" in html
    assert "8:00 AM EDT" in html
    assert "Rescanned" in html
