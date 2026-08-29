#!/usr/bin/env python3
"""Operator workflow for guest issues and their linked tickets."""

from __future__ import annotations

from datetime import datetime, time
from typing import Any

from brain.models import (
    PropertyGuestIssue,
    PropertyGuestIssueNote,
    get_session as get_brain_session,
)


ACTIVE_TICKET_STATUSES = {"Open", "Assigned", "In Progress", "Blocked"}
CLOSED_TICKET_STATUSES = {"Resolved", "Closed"}
WORKFLOW_STATUSES = {"open", "ticketed", "resolved"}
ISSUE_STATUS_LABELS = {
    "need_attention": "Need attention",
    "scheduled": "Scheduled",
    "in_progress": "In progress",
    "stuck": "Stuck",
    "resolved": "Resolved",
}
ACTIVE_ISSUE_STATUSES = set(ISSUE_STATUS_LABELS) - {"resolved"}
ISSUE_PRIORITIES = ("Critical", "High", "Medium", "Low")
ISSUE_PRIORITY_ORDER = {priority: index for index, priority in enumerate(ISSUE_PRIORITIES)}
DEFAULT_ISSUE_PRIORITY = "Medium"
TICKET_ISSUE_STATUS = {
    "Open": "need_attention",
    "Assigned": "scheduled",
    "In Progress": "in_progress",
    "Blocked": "stuck",
}
MAX_RESOLUTION_COMMENT_LENGTH = 2000
MAX_ISSUE_NOTE_LENGTH = 2000


class GuestIssueWorkflowError(ValueError):
    """A user-correctable guest-issue workflow error."""

    def __init__(self, message: str, *, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def get_issue_context(issue_id: int, *, session=None) -> dict[str, Any] | None:
    """Return safe issue context for ticket creation."""
    owns_session = session is None
    session = session or get_brain_session()
    try:
        issue = session.query(PropertyGuestIssue).filter(
            PropertyGuestIssue.issue_id == issue_id
        ).first()
        if not issue:
            return None
        return {
            "issue_id": issue.issue_id,
            "listing_id": issue.listing_id,
            "reservation_id": issue.reservation_id,
            "source_kind": issue.source_kind,
            "source_date": issue.source_date,
            "reported_at": issue_reported_at(issue),
            "category": issue.issue_category,
            "summary": issue.summary,
            "details": issue.details,
            "severity": issue.severity,
            "workflow_status": issue.workflow_status or "open",
            "operational_status": issue_operational_status(issue),
            "priority": issue_priority(issue),
            "linked_ticket_id": issue.linked_ticket_id,
            "resolved_at": issue.resolved_at,
            "references": list(issue.source_references or []),
        }
    finally:
        if owns_session:
            session.close()


def get_issue_context_for_ticket(ticket_id: int, *, session=None) -> dict[str, Any] | None:
    """Return the guest issue linked to a ticket, when one exists."""
    owns_session = session is None
    session = session or get_brain_session()
    try:
        issue = session.query(PropertyGuestIssue).filter(
            PropertyGuestIssue.linked_ticket_id == ticket_id
        ).first()
        return get_issue_context(issue.issue_id, session=session) if issue else None
    finally:
        if owns_session:
            session.close()


def resolve_issue(
    issue_id: int,
    *,
    comment: str,
    user_id: int,
    now: datetime | None = None,
    session=None,
) -> PropertyGuestIssue:
    """Resolve an issue directly with a required operator note."""
    note = _validate_resolution_comment(comment)
    owns_session = session is None
    session = session or get_brain_session()
    try:
        issue = _get_issue_for_update(session, issue_id)
        if issue.linked_ticket_id:
            raise GuestIssueWorkflowError(
                "This issue is tied to a ticket. Close the ticket to resolve it.",
                status_code=409,
            )
        if (issue.workflow_status or "open") == "resolved":
            raise GuestIssueWorkflowError("This issue is already resolved.", status_code=409)

        resolved_at = now or datetime.utcnow()
        issue.workflow_status = "resolved"
        issue.operational_status = "resolved"
        issue.resolution_comment = note
        issue.resolution_method = "quick"
        issue.resolved_at = resolved_at
        issue.resolved_by_user_id = user_id
        issue.updated_at = resolved_at
        _append_issue_note(
            session,
            issue_id=issue.issue_id,
            user_id=user_id,
            body=note,
            note_type="resolution",
            created_at=resolved_at,
        )
        session.commit()
        session.refresh(issue)
        return issue
    except Exception:
        session.rollback()
        raise
    finally:
        if owns_session:
            session.close()


def issue_operational_status(issue: PropertyGuestIssue) -> str:
    """Return a valid operational state while honoring the resolved lifecycle."""
    if (issue.workflow_status or "open") == "resolved":
        return "resolved"
    status = str(issue.operational_status or "need_attention").strip().lower()
    return status if status in ACTIVE_ISSUE_STATUSES else "need_attention"


def issue_priority(issue: PropertyGuestIssue) -> str:
    """Return a valid operator priority for display and queue ordering."""
    normalized = _normalize_issue_priority(getattr(issue, "priority", None))
    return normalized or DEFAULT_ISSUE_PRIORITY


def issue_reported_at(issue: PropertyGuestIssue) -> datetime:
    """Return the immutable timestamp when an issue was first detected."""
    reported_at = getattr(issue, "created_at", None)
    if reported_at:
        return reported_at
    source_date = getattr(issue, "source_date", None)
    if source_date:
        return datetime.combine(source_date, time.min)
    return datetime.min


def change_issue_priority(
    issue_id: int,
    *,
    priority: str,
    user_id: int,
    now: datetime | None = None,
    session=None,
) -> tuple[PropertyGuestIssue, PropertyGuestIssueNote | None]:
    """Set one issue's operator priority and append an auditable activity entry."""
    normalized = _normalize_issue_priority(priority)
    if not normalized:
        raise GuestIssueWorkflowError("Choose a valid issue priority.")

    owns_session = session is None
    session = session or get_brain_session()
    try:
        issue = _get_issue_for_update(session, issue_id)
        previous = issue_priority(issue)
        if previous == normalized:
            return issue, None

        changed_at = now or datetime.utcnow()
        issue.priority = normalized
        issue.priority_updated_at = changed_at
        issue.priority_updated_by_user_id = user_id
        issue.updated_at = changed_at
        activity = _append_issue_note(
            session,
            issue_id=issue.issue_id,
            user_id=user_id,
            body=f"Priority changed from {previous} to {normalized}.",
            note_type="priority_change",
            created_at=changed_at,
        )
        session.commit()
        session.refresh(issue)
        session.refresh(activity)
        return issue, activity
    except Exception:
        session.rollback()
        raise
    finally:
        if owns_session:
            session.close()


def change_issue_status(
    issue_id: int,
    *,
    status: str,
    user_id: int,
    note: str = "",
    now: datetime | None = None,
    session=None,
) -> tuple[PropertyGuestIssue, PropertyGuestIssueNote | None]:
    """Move one unlinked active issue through its operational workflow."""
    normalized = str(status or "").strip().lower()
    if normalized not in ISSUE_STATUS_LABELS:
        raise GuestIssueWorkflowError("Choose a valid issue status.")
    if normalized == "resolved":
        resolved = resolve_issue(
            issue_id,
            comment=note,
            user_id=user_id,
            now=now,
            session=session,
        )
        return resolved, None

    owns_session = session is None
    session = session or get_brain_session()
    try:
        issue = _get_issue_for_update(session, issue_id)
        if (issue.workflow_status or "open") == "resolved":
            raise GuestIssueWorkflowError("This issue is already resolved.", status_code=409)
        if issue.linked_ticket_id:
            raise GuestIssueWorkflowError(
                f"Status follows linked ticket #{issue.linked_ticket_id}. Update the ticket instead.",
                status_code=409,
            )

        previous = issue_operational_status(issue)
        changed_at = now or datetime.utcnow()
        issue.operational_status = normalized
        issue.updated_at = changed_at
        activity = None
        supplied_note = str(note or "").strip()
        if previous != normalized or supplied_note:
            body = supplied_note or (
                f"Status changed from {ISSUE_STATUS_LABELS[previous]} "
                f"to {ISSUE_STATUS_LABELS[normalized]}."
            )
            activity = _append_issue_note(
                session,
                issue_id=issue.issue_id,
                user_id=user_id,
                body=body,
                note_type="status_change",
                created_at=changed_at,
            )
        session.commit()
        session.refresh(issue)
        if activity:
            session.refresh(activity)
        return issue, activity
    except Exception:
        session.rollback()
        raise
    finally:
        if owns_session:
            session.close()


def add_issue_note(
    issue_id: int,
    *,
    note: str,
    user_id: int,
    now: datetime | None = None,
    session=None,
) -> PropertyGuestIssueNote:
    """Append an operator note without replacing earlier activity."""
    body = _validate_issue_note(note)
    owns_session = session is None
    session = session or get_brain_session()
    try:
        issue = _get_issue_for_update(session, issue_id)
        created_at = now or datetime.utcnow()
        activity = _append_issue_note(
            session,
            issue_id=issue.issue_id,
            user_id=user_id,
            body=body,
            note_type="operator",
            created_at=created_at,
        )
        issue.updated_at = created_at
        session.commit()
        session.refresh(activity)
        return activity
    except Exception:
        session.rollback()
        raise
    finally:
        if owns_session:
            session.close()


def validate_issue_for_ticket(issue_id: int, *, listing_ids: list[int] | None = None) -> dict[str, Any]:
    """Validate that an issue can be attached to a newly created ticket."""
    issue = get_issue_context(issue_id)
    if not issue:
        raise GuestIssueWorkflowError("Guest issue not found.", status_code=404)
    if issue["workflow_status"] == "resolved":
        raise GuestIssueWorkflowError("This issue is already resolved.", status_code=409)
    if issue["linked_ticket_id"]:
        raise GuestIssueWorkflowError(
            f"This issue is already tied to ticket #{issue['linked_ticket_id']}.",
            status_code=409,
        )
    if listing_ids is not None and int(issue["listing_id"]) not in {
        int(value) for value in listing_ids
    }:
        raise GuestIssueWorkflowError(
            "The ticket must include the rental unit associated with this issue.",
            status_code=400,
        )
    return issue


def link_issue_to_ticket(
    issue_id: int,
    *,
    ticket_id: int,
    ticket_status: str,
    user_id: int,
    now: datetime | None = None,
    session=None,
) -> PropertyGuestIssue:
    """Attach one issue to one ticket and mirror the ticket's lifecycle."""
    owns_session = session is None
    session = session or get_brain_session()
    try:
        issue = _get_issue_for_update(session, issue_id)
        if issue.linked_ticket_id and issue.linked_ticket_id != ticket_id:
            raise GuestIssueWorkflowError(
                f"This issue is already tied to ticket #{issue.linked_ticket_id}.",
                status_code=409,
            )
        if (issue.workflow_status or "open") == "resolved" and not issue.linked_ticket_id:
            raise GuestIssueWorkflowError("This issue is already resolved.", status_code=409)

        issue.linked_ticket_id = ticket_id
        _apply_ticket_status(issue, ticket_status=ticket_status, user_id=user_id, now=now)
        session.commit()
        session.refresh(issue)
        return issue
    except Exception:
        session.rollback()
        raise
    finally:
        if owns_session:
            session.close()


def sync_issue_from_ticket_status(
    ticket_id: int,
    *,
    ticket_status: str,
    user_id: int,
    now: datetime | None = None,
    session=None,
) -> PropertyGuestIssue | None:
    """Resolve or reopen a linked issue when its ticket status changes."""
    owns_session = session is None
    session = session or get_brain_session()
    try:
        query = session.query(PropertyGuestIssue).filter(
            PropertyGuestIssue.linked_ticket_id == ticket_id
        )
        if session.bind and session.bind.dialect.name == "postgresql":
            query = query.with_for_update()
        issue = query.first()
        if not issue:
            return None
        _apply_ticket_status(issue, ticket_status=ticket_status, user_id=user_id, now=now)
        session.commit()
        session.refresh(issue)
        return issue
    except Exception:
        session.rollback()
        raise
    finally:
        if owns_session:
            session.close()


def unlink_issue_from_ticket(
    ticket_id: int,
    *,
    now: datetime | None = None,
    session=None,
) -> PropertyGuestIssue | None:
    """Reopen and unlink an issue if its ticket is deleted."""
    owns_session = session is None
    session = session or get_brain_session()
    try:
        query = session.query(PropertyGuestIssue).filter(
            PropertyGuestIssue.linked_ticket_id == ticket_id
        )
        if session.bind and session.bind.dialect.name == "postgresql":
            query = query.with_for_update()
        issue = query.first()
        if not issue:
            return None
        changed_at = now or datetime.utcnow()
        issue.linked_ticket_id = None
        issue.workflow_status = "open"
        issue.operational_status = "need_attention"
        issue.resolution_comment = None
        issue.resolution_method = None
        issue.resolved_at = None
        issue.resolved_by_user_id = None
        issue.updated_at = changed_at
        session.commit()
        session.refresh(issue)
        return issue
    except Exception:
        session.rollback()
        raise
    finally:
        if owns_session:
            session.close()


def _get_issue_for_update(session, issue_id: int) -> PropertyGuestIssue:
    query = session.query(PropertyGuestIssue).filter(PropertyGuestIssue.issue_id == issue_id)
    if session.bind and session.bind.dialect.name == "postgresql":
        query = query.with_for_update()
    issue = query.first()
    if not issue:
        raise GuestIssueWorkflowError("Guest issue not found.", status_code=404)
    return issue


def _normalize_issue_priority(value: str | None) -> str | None:
    normalized = str(value or "").strip().lower()
    return next(
        (priority for priority in ISSUE_PRIORITIES if priority.lower() == normalized),
        None,
    )


def _validate_resolution_comment(comment: str) -> str:
    note = str(comment or "").strip()
    if not note:
        raise GuestIssueWorkflowError("Add a short note describing what happened and how it was resolved.")
    if len(note) > MAX_RESOLUTION_COMMENT_LENGTH:
        raise GuestIssueWorkflowError(
            f"Resolution notes must be {MAX_RESOLUTION_COMMENT_LENGTH} characters or fewer."
        )
    return note


def _validate_issue_note(note: str) -> str:
    body = str(note or "").strip()
    if not body:
        raise GuestIssueWorkflowError("Add a note before posting.")
    if len(body) > MAX_ISSUE_NOTE_LENGTH:
        raise GuestIssueWorkflowError(
            f"Issue notes must be {MAX_ISSUE_NOTE_LENGTH} characters or fewer."
        )
    return body


def _append_issue_note(
    session,
    *,
    issue_id: int,
    user_id: int,
    body: str,
    note_type: str,
    created_at: datetime,
) -> PropertyGuestIssueNote:
    activity = PropertyGuestIssueNote(
        issue_id=issue_id,
        author_user_id=user_id,
        note_type=note_type,
        body=_validate_issue_note(body),
        created_at=created_at,
    )
    session.add(activity)
    return activity


def _apply_ticket_status(
    issue: PropertyGuestIssue,
    *,
    ticket_status: str,
    user_id: int,
    now: datetime | None,
) -> None:
    changed_at = now or datetime.utcnow()
    if ticket_status in CLOSED_TICKET_STATUSES:
        issue.workflow_status = "resolved"
        issue.operational_status = "resolved"
        issue.resolution_comment = (
            f"Resolved through ticket #{issue.linked_ticket_id} when the ticket was marked {ticket_status}."
        )
        issue.resolution_method = "ticket"
        issue.resolved_at = changed_at
        issue.resolved_by_user_id = user_id
    else:
        issue.workflow_status = "ticketed"
        issue.operational_status = TICKET_ISSUE_STATUS.get(ticket_status, "need_attention")
        issue.resolution_comment = None
        issue.resolution_method = None
        issue.resolved_at = None
        issue.resolved_by_user_id = None
    issue.updated_at = changed_at
