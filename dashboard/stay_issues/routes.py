#!/usr/bin/env python3
"""Routes for guest issues and exact raw evidence."""

from __future__ import annotations

import logging

from flask import Blueprint, abort, jsonify, render_template, request

import dashboard.config as config
from dashboard.api.conventions import ApiParameterError, api_error
from dashboard.api.security import scoped_api_key_required
from dashboard.auth.api_keys import GUEST_ISSUES_READ_SCOPE
from dashboard.auth.decorators import approved_required, check_feature_access
from dashboard.auth.session import get_current_user
from dashboard.stay_issues.api_service import GuestIssueApiService
from dashboard.stay_issues.service import (
    GuestIssueDashboardService,
    get_message_source,
    get_review_source,
)
from dashboard.stay_issues.workflow import (
    ISSUE_PRIORITIES,
    ISSUE_STATUS_LABELS,
    GuestIssueWorkflowError,
    add_issue_note,
    change_issue_priority,
    change_issue_status,
    issue_priority,
    issue_operational_status,
    resolve_issue,
)
from database.models import get_session

logger = logging.getLogger(__name__)

guest_issues_bp = Blueprint(
    "guest_issues",
    __name__,
    url_prefix="/workspace/guest-issues",
)

guest_issues_api_bp = Blueprint(
    "guest_issues_api_v1",
    __name__,
    url_prefix="/api/v1/guest-issues",
)


@guest_issues_bp.before_request
def require_guest_issue_access():
    # Guest issues are part of the existing Properties workspace permission.
    return check_feature_access("properties")


@guest_issues_api_bp.route("", methods=["GET"])
@scoped_api_key_required(GUEST_ISSUES_READ_SCOPE)
def list_guest_issues_api():
    """List safe Guest Issue records for authorized external agents."""
    service = GuestIssueApiService()
    try:
        return jsonify(service.list_issues(request.args))
    except ApiParameterError as exc:
        return api_error(
            "invalid_parameter",
            str(exc),
            400,
            details={"parameter": exc.parameter} if exc.parameter else None,
        )
    except Exception:
        logger.exception("Could not list Guest Issues through API v1")
        return api_error(
            "internal_error",
            "Guest Issues could not be loaded.",
            500,
        )
    finally:
        service.close()


@guest_issues_api_bp.route("/<int:issue_id>", methods=["GET"])
@scoped_api_key_required(GUEST_ISSUES_READ_SCOPE)
def get_guest_issue_api(issue_id: int):
    """Return one safe Guest Issue record by its immutable ID."""
    service = GuestIssueApiService()
    try:
        payload = service.get_issue(issue_id)
        if payload is None:
            return api_error("not_found", "Guest Issue not found.", 404)
        return jsonify(payload)
    except Exception:
        logger.exception("Could not load Guest Issue %s through API v1", issue_id)
        return api_error(
            "internal_error",
            "The Guest Issue could not be loaded.",
            500,
        )
    finally:
        service.close()


@guest_issues_bp.route("/")
@approved_required
def guest_issues_page():
    service = GuestIssueDashboardService()
    try:
        view = request.args.get("view", "active")
        return render_template(
            "stay_issues/index.html",
            dashboard=service.get_dashboard(
                view=view,
                window_key=request.args.get("window", "1m"),
                start_date=request.args.get("start"),
                end_date=request.args.get("end"),
            ),
        )
    finally:
        service.close()


@guest_issues_bp.route("/api/issues/<int:issue_id>/resolve", methods=["POST"])
@approved_required
def resolve_guest_issue(issue_id: int):
    current_user = get_current_user()
    if not current_user:
        return jsonify({"error": "Authentication required"}), 401
    payload = request.get_json(silent=True) or {}
    try:
        issue = resolve_issue(
            issue_id,
            comment=payload.get("comment", ""),
            user_id=current_user.user_id,
        )
        return jsonify({
            "issue_id": issue.issue_id,
            "workflow_status": issue.workflow_status,
            "resolution_comment": issue.resolution_comment,
            "resolved_at": issue.resolved_at.isoformat() if issue.resolved_at else None,
        })
    except GuestIssueWorkflowError as exc:
        return jsonify({"error": str(exc)}), exc.status_code
    except Exception:
        logger.exception("Could not resolve guest issue %s", issue_id)
        return jsonify({"error": "The issue could not be resolved. Please try again."}), 500


@guest_issues_bp.route("/api/issues/<int:issue_id>/status", methods=["POST"])
@approved_required
def update_guest_issue_status(issue_id: int):
    current_user = get_current_user()
    if not current_user:
        return jsonify({"error": "Authentication required"}), 401
    payload = request.get_json(silent=True) or {}
    try:
        issue, activity = change_issue_status(
            issue_id,
            status=payload.get("status", ""),
            note=payload.get("note", ""),
            user_id=current_user.user_id,
        )
        status = issue_operational_status(issue)
        return jsonify({
            "issue_id": issue.issue_id,
            "workflow_status": issue.workflow_status,
            "operational_status": status,
            "status_label": ISSUE_STATUS_LABELS[status],
            "note": _note_payload(activity, current_user) if activity else None,
        })
    except GuestIssueWorkflowError as exc:
        return jsonify({"error": str(exc)}), exc.status_code
    except Exception:
        logger.exception("Could not update guest issue %s status", issue_id)
        return jsonify({"error": "The issue status could not be updated. Please try again."}), 500


@guest_issues_bp.route("/api/issues/<int:issue_id>/priority", methods=["POST"])
@approved_required
def update_guest_issue_priority(issue_id: int):
    current_user = get_current_user()
    if not current_user:
        return jsonify({"error": "Authentication required"}), 401
    payload = request.get_json(silent=True) or {}
    try:
        issue, activity = change_issue_priority(
            issue_id,
            priority=payload.get("priority", ""),
            user_id=current_user.user_id,
        )
        priority = issue_priority(issue)
        return jsonify({
            "issue_id": issue.issue_id,
            "priority": priority,
            "priority_key": priority.lower(),
            "priority_options": ISSUE_PRIORITIES,
            "priority_updated_at": (
                issue.priority_updated_at.isoformat() if issue.priority_updated_at else None
            ),
            "priority_updated_by_user_id": issue.priority_updated_by_user_id,
            "priority_updated_by_name": (
                current_user.name or current_user.email or f"Team member {current_user.user_id}"
            ) if issue.priority_updated_by_user_id else None,
            "note": _note_payload(activity, current_user) if activity else None,
        })
    except GuestIssueWorkflowError as exc:
        return jsonify({"error": str(exc)}), exc.status_code
    except Exception:
        logger.exception("Could not update guest issue %s priority", issue_id)
        return jsonify({"error": "The issue priority could not be updated. Please try again."}), 500


@guest_issues_bp.route("/api/issues/<int:issue_id>/notes", methods=["POST"])
@approved_required
def add_guest_issue_note(issue_id: int):
    current_user = get_current_user()
    if not current_user:
        return jsonify({"error": "Authentication required"}), 401
    payload = request.get_json(silent=True) or {}
    try:
        note = add_issue_note(
            issue_id,
            note=payload.get("note", ""),
            user_id=current_user.user_id,
        )
        return jsonify({"issue_id": issue_id, "note": _note_payload(note, current_user)})
    except GuestIssueWorkflowError as exc:
        return jsonify({"error": str(exc)}), exc.status_code
    except Exception:
        logger.exception("Could not add a note to guest issue %s", issue_id)
        return jsonify({"error": "The note could not be added. Please try again."}), 500


def _note_payload(note, user) -> dict | None:
    if not note:
        return None
    return {
        "note_id": note.note_id,
        "body": note.body,
        "note_type": note.note_type,
        "note_type_label": {
            "status_change": "Status update",
            "priority_change": "Priority update",
            "resolution": "Resolution",
        }.get(note.note_type, "Note"),
        "created_at": note.created_at.isoformat() if note.created_at else None,
        "author_user_id": note.author_user_id,
        "author_name": user.name or user.email or f"Team member {user.user_id}",
    }


@guest_issues_bp.route("/sources/messages/<message_id>")
@approved_required
def message_source(message_id: str):
    try:
        parsed_id = int(message_id)
    except (TypeError, ValueError):
        abort(404)
    session = get_session(config.MAIN_DATABASE_PATH)
    try:
        source = get_message_source(session, parsed_id)
        if not source:
            abort(404)
        return render_template("stay_issues/source.html", source=source)
    finally:
        session.close()


@guest_issues_bp.route("/sources/reviews/<int:review_id>")
@approved_required
def review_source(review_id: int):
    session = get_session(config.MAIN_DATABASE_PATH)
    try:
        source = get_review_source(session, review_id)
        if not source:
            abort(404)
        return render_template("stay_issues/source.html", source=source)
    finally:
        session.close()


def register_guest_issue_routes(app):
    app.register_blueprint(guest_issues_bp)
    app.register_blueprint(guest_issues_api_bp)
