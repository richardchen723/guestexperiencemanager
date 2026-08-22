#!/usr/bin/env python3
"""Routes for guest issues and exact raw evidence."""

from __future__ import annotations

import logging

from flask import Blueprint, abort, jsonify, render_template, request

import dashboard.config as config
from dashboard.auth.decorators import approved_required, check_feature_access
from dashboard.auth.session import get_current_user
from dashboard.stay_issues.service import (
    GuestIssueDashboardService,
    get_message_source,
    get_review_source,
)
from dashboard.stay_issues.workflow import GuestIssueWorkflowError, resolve_issue
from database.models import get_session

logger = logging.getLogger(__name__)

guest_issues_bp = Blueprint(
    "guest_issues",
    __name__,
    url_prefix="/workspace/guest-issues",
)


@guest_issues_bp.before_request
def require_guest_issue_access():
    # Guest issues are part of the existing Properties workspace permission.
    return check_feature_access("properties")


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
                window_key=request.args.get("window", "3m"),
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
