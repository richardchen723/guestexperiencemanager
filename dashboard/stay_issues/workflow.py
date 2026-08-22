#!/usr/bin/env python3
"""Operator workflow for guest issues and their linked tickets."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from brain.models import PropertyGuestIssue, get_session as get_brain_session


ACTIVE_TICKET_STATUSES = {"Open", "Assigned", "In Progress", "Blocked"}
CLOSED_TICKET_STATUSES = {"Resolved", "Closed"}
WORKFLOW_STATUSES = {"open", "ticketed", "resolved"}
MAX_RESOLUTION_COMMENT_LENGTH = 2000


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
            "category": issue.issue_category,
            "summary": issue.summary,
            "details": issue.details,
            "severity": issue.severity,
            "workflow_status": issue.workflow_status or "open",
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
        issue.resolution_comment = note
        issue.resolution_method = "quick"
        issue.resolved_at = resolved_at
        issue.resolved_by_user_id = user_id
        issue.updated_at = resolved_at
        session.commit()
        session.refresh(issue)
        return issue
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


def _validate_resolution_comment(comment: str) -> str:
    note = str(comment or "").strip()
    if not note:
        raise GuestIssueWorkflowError("Add a short note describing what happened and how it was resolved.")
    if len(note) > MAX_RESOLUTION_COMMENT_LENGTH:
        raise GuestIssueWorkflowError(
            f"Resolution notes must be {MAX_RESOLUTION_COMMENT_LENGTH} characters or fewer."
        )
    return note


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
        issue.resolution_comment = (
            f"Resolved through ticket #{issue.linked_ticket_id} when the ticket was marked {ticket_status}."
        )
        issue.resolution_method = "ticket"
        issue.resolved_at = changed_at
        issue.resolved_by_user_id = user_id
    else:
        issue.workflow_status = "ticketed"
        issue.resolution_comment = None
        issue.resolution_method = None
        issue.resolved_at = None
        issue.resolved_by_user_id = None
    issue.updated_at = changed_at
