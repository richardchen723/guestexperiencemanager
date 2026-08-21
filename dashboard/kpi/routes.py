#!/usr/bin/env python3
"""KPI page and read-only dashboard API routes."""

from __future__ import annotations

import logging

from flask import Blueprint, jsonify, render_template, request

import dashboard.config as config
from brain.stay_outcomes import StayOutcomeClassificationService
from dashboard.auth.decorators import admin_required, approved_required, check_feature_access
from dashboard.auth.session import get_current_user
from dashboard.kpi.refresh import (
    KPI_REFRESH_MODE,
    active_kpi_refresh_job,
    enqueue_kpi_refresh,
    refresh_job_payload,
)
from dashboard.kpi.service import KPIService
from dashboard.sync.job_manager import get_job_manager

logger = logging.getLogger(__name__)

kpi_bp = Blueprint("kpi", __name__, url_prefix="/kpi")


@kpi_bp.before_request
def require_kpi_access():
    return check_feature_access("kpi")


@kpi_bp.route("/")
@approved_required
def kpi_page():
    return render_template("kpi/index.html", current_user=get_current_user())


@kpi_bp.route("/api/data")
@approved_required
def api_kpi_data():
    portfolio = (request.args.get("portfolio") or "all").strip()[:120]
    period_key = (request.args.get("period") or "current_month").strip()[:40]
    custom_start = (request.args.get("from") or "").strip()[:10] or None
    custom_end = (request.args.get("to") or "").strip()[:10] or None
    service = KPIService()
    try:
        return jsonify(service.get_dashboard_data(
            portfolio_name=portfolio,
            period_key=period_key,
            custom_start=custom_start,
            custom_end=custom_end,
        ))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        logger.error("Unable to build KPI dashboard: %s", exc, exc_info=True)
        return jsonify({"error": "Unable to calculate KPI data from the current Hostaway snapshots."}), 500
    finally:
        service.close()


@kpi_bp.route("/api/refresh", methods=["POST"])
@admin_required
def api_refresh_kpi_sources():
    """Queue one deduplicated refresh for the independent KPI worker."""
    body = request.get_json(silent=True) or {}
    portfolio = str(body.get("portfolio") or "all").strip()[:120]
    service = KPIService()
    try:
        scope = service.resolve_scope(portfolio)
    finally:
        service.close()

    listing_ids = scope.get("listing_ids") or []
    if not listing_ids:
        return jsonify({"error": "No active properties are available in this portfolio."}), 409

    job, created = enqueue_kpi_refresh(
        listing_ids,
        scope_name=scope.get("selected") or "all",
    )
    if not created:
        payload = refresh_job_payload(job)
        payload["reused"] = True
        return jsonify(payload), 202

    return jsonify({
        "job_id": job["job_id"],
        "status": job["status"],
        "scope": scope.get("selected") or "all",
        "property_count": len(listing_ids),
        "reused": False,
    }), 202


@kpi_bp.route("/api/refresh/active")
@admin_required
def api_active_kpi_refresh():
    active_job = active_kpi_refresh_job()
    if not active_job:
        return jsonify({"status": "idle"})
    return jsonify(refresh_job_payload(active_job))


@kpi_bp.route("/api/refresh/<job_id>")
@admin_required
def api_kpi_refresh_status(job_id: str):
    job = get_job_manager().get_job(job_id[:36])
    if not job or job.get("sync_mode") != KPI_REFRESH_MODE:
        return jsonify({"error": "KPI refresh job not found."}), 404
    return jsonify(refresh_job_payload(job))


@kpi_bp.route("/api/stay-outcomes/refresh", methods=["POST"])
@approved_required
def api_refresh_stay_outcomes():
    current_user = get_current_user()
    if not current_user or not current_user.is_admin():
        return jsonify({"error": "Administrator access is required."}), 403
    if not config.KPI_ENABLE_STAY_OUTCOME_CLASSIFICATION:
        return jsonify({
            "error": (
                "Stay-outcome classification is disabled. Set "
                "KPI_ENABLE_STAY_OUTCOME_CLASSIFICATION=True only after approving "
                "the configured OpenAI processing of guest conversation text."
            )
        }), 409
    classifier = StayOutcomeClassificationService()
    try:
        return jsonify(classifier.refresh())
    except Exception as exc:
        logger.error("Unable to refresh stay outcomes: %s", exc, exc_info=True)
        return jsonify({"error": "Stay-outcome classification refresh failed."}), 500
    finally:
        classifier.close()


def register_kpi_routes(app):
    app.register_blueprint(kpi_bp)
