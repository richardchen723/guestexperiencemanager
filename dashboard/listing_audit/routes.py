#!/usr/bin/env python3
"""Workspace listing-health page and API."""

import logging

from flask import Blueprint, jsonify, render_template, request

from dashboard.auth.decorators import approved_required, check_feature_access
from dashboard.listing_audit.service import ListingAuditDashboardService

logger = logging.getLogger(__name__)

listing_audit_bp = Blueprint("listing_audit", __name__, url_prefix="/workspace/listing-health")


@listing_audit_bp.before_request
def require_listing_audit_access():
    return check_feature_access("listing_audit")


@listing_audit_bp.route("/")
@approved_required
def listing_audit_page():
    service = ListingAuditDashboardService()
    try:
        return render_template(
            "listing_audit/index.html",
            audit=service.get_dashboard(portfolio_name=request.args.get("portfolio")),
        )
    finally:
        service.close()


@listing_audit_bp.route("/api/latest")
@approved_required
def api_latest_listing_audit():
    service = ListingAuditDashboardService()
    try:
        return jsonify(service.get_dashboard(portfolio_name=request.args.get("portfolio")))
    except Exception as exc:
        logger.error("Listing audit API failed: %s", exc, exc_info=True)
        return jsonify({"error": "The latest listing audit could not be loaded."}), 500
    finally:
        service.close()


def register_listing_audit_routes(app):
    app.register_blueprint(listing_audit_bp)
