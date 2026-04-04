#!/usr/bin/env python3
"""
Boost routes - UI page and REST API endpoints.
"""

import logging
from flask import Blueprint, jsonify, render_template, request

from dashboard.auth.decorators import admin_required
from dashboard.boost.models import init_boost_database
from dashboard.boost import service
from dashboard.boost import proxy_manager
from dashboard.boost import listing_helper

logger = logging.getLogger(__name__)

boost_bp = Blueprint("boost", __name__, url_prefix="/boost")


@boost_bp.before_app_request
def _ensure_boost_tables():
    """Lazily initialise the boost schema on first request."""
    if not getattr(_ensure_boost_tables, "_done", False):
        try:
            init_boost_database()
            _ensure_boost_tables._done = True
        except Exception as e:
            logger.error(f"Failed to init boost database: {e}")


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------

@boost_bp.route("/")
@admin_required
def boost_page():
    return render_template("boost/index.html")


# ---------------------------------------------------------------------------
# Listing API (Hostaway integration for dropdown)
# ---------------------------------------------------------------------------

@boost_bp.route("/api/listings", methods=["GET"])
@admin_required
def api_list_listings():
    listings = listing_helper.get_all_listings()
    return jsonify(listings)


@boost_bp.route("/api/listings/<int:listing_id>/details", methods=["GET"])
@admin_required
def api_listing_details(listing_id):
    details = listing_helper.get_listing_details(listing_id)
    if not details:
        return jsonify({"error": "Listing not found"}), 404
    return jsonify(details)


# ---------------------------------------------------------------------------
# Campaign API
# ---------------------------------------------------------------------------

@boost_bp.route("/api/campaigns", methods=["GET"])
@admin_required
def api_list_campaigns():
    campaigns = service.list_campaigns()
    return jsonify(campaigns)


@boost_bp.route("/api/campaigns", methods=["POST"])
@admin_required
def api_create_campaign():
    data = request.get_json()
    if not data:
        return jsonify({"error": "Request body required"}), 400
    required = ["name", "search_area", "date_window_start", "date_window_end", "target_listing_id"]
    missing = [f for f in required if not data.get(f)]
    if missing:
        return jsonify({"error": f"Missing required fields: {', '.join(missing)}"}), 400

    lid = data["target_listing_id"]
    details = listing_helper.get_listing_details(lid)
    if details:
        data.setdefault("target_listing_url", details.get("airbnb_url"))
        data.setdefault("target_listing_name", details.get("name"))
        data.setdefault("target_lat", details.get("lat"))
        data.setdefault("target_lng", details.get("lng"))

    campaign = service.create_campaign(data)
    return jsonify(campaign), 201


@boost_bp.route("/api/campaigns/<int:campaign_id>", methods=["PUT"])
@admin_required
def api_update_campaign(campaign_id):
    data = request.get_json()
    if not data:
        return jsonify({"error": "Request body required"}), 400
    result = service.update_campaign(campaign_id, data)
    if not result:
        return jsonify({"error": "Campaign not found"}), 404
    return jsonify(result)


@boost_bp.route("/api/campaigns/<int:campaign_id>", methods=["DELETE"])
@admin_required
def api_delete_campaign(campaign_id):
    ok = service.delete_campaign(campaign_id)
    if not ok:
        return jsonify({"error": "Campaign not found"}), 404
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Session API
# ---------------------------------------------------------------------------

@boost_bp.route("/api/campaigns/<int:campaign_id>/trigger", methods=["POST"])
@admin_required
def api_trigger_session(campaign_id):
    payload = request.get_json(silent=True) or {}
    raw_headless = payload.get("headless", False)
    if isinstance(raw_headless, bool):
        headless = raw_headless
    elif isinstance(raw_headless, str):
        headless = raw_headless.strip().lower() in {"1", "true", "yes", "on", "headless"}
    else:
        headless = bool(raw_headless)

    result = service.trigger_session(campaign_id, headless=headless)
    if "error" in result:
        return jsonify(result), 409
    return jsonify(result), 202


@boost_bp.route("/api/campaigns/<int:campaign_id>/stop", methods=["POST"])
@admin_required
def api_stop_session(campaign_id):
    result = service.stop_session(campaign_id)
    if "error" in result:
        return jsonify(result), 404
    return jsonify(result)


@boost_bp.route("/api/campaigns/<int:campaign_id>/sessions", methods=["GET"])
@admin_required
def api_list_sessions(campaign_id):
    limit = request.args.get("limit", 50, type=int)
    sessions = service.get_sessions(campaign_id, limit=limit)
    return jsonify(sessions)


@boost_bp.route("/api/campaigns/<int:campaign_id>/status", methods=["GET"])
@admin_required
def api_session_status(campaign_id):
    running = service.get_running_status(campaign_id)
    return jsonify({"running": running})


# ---------------------------------------------------------------------------
# Ranking / Dashboard API
# ---------------------------------------------------------------------------

@boost_bp.route("/api/campaigns/<int:campaign_id>/rankings", methods=["GET"])
@admin_required
def api_rankings(campaign_id):
    limit = request.args.get("limit", 90, type=int)
    rankings = service.get_rankings(campaign_id, limit=limit)
    return jsonify(rankings)


@boost_bp.route("/api/campaigns/<int:campaign_id>/stats", methods=["GET"])
@admin_required
def api_dashboard_stats(campaign_id):
    stats = service.get_dashboard_stats(campaign_id)
    return jsonify(stats)


# ---------------------------------------------------------------------------
# Proxy API
# ---------------------------------------------------------------------------

@boost_bp.route("/api/proxies", methods=["GET"])
@admin_required
def api_list_proxies():
    proxies = proxy_manager.get_all_proxies()
    return jsonify(proxies)


@boost_bp.route("/api/proxies/import", methods=["POST"])
@admin_required
def api_import_proxies():
    data = request.get_json()
    if not data or not data.get("text"):
        return jsonify({"error": "Proxy text is required"}), 400
    added, skipped = proxy_manager.import_proxies(data["text"])
    return jsonify({"added": added, "updated": skipped})


@boost_bp.route("/api/proxies/clear", methods=["POST"])
@admin_required
def api_clear_proxies():
    proxy_manager.clear_all_proxies()
    return jsonify({"ok": True})


@boost_bp.route("/api/proxies/<int:proxy_id>/toggle", methods=["POST"])
@admin_required
def api_toggle_proxy(proxy_id):
    data = request.get_json() or {}
    is_active = data.get("is_active", True)
    proxy_manager.toggle_proxy(proxy_id, is_active)
    return jsonify({"ok": True})


@boost_bp.route("/api/proxies/delete", methods=["POST"])
@admin_required
def api_delete_proxies():
    data = request.get_json() or {}
    ids = data.get("ids", [])
    if not ids:
        return jsonify({"error": "No proxy IDs provided"}), 400
    count = proxy_manager.delete_proxies(ids)
    return jsonify({"deleted": count})


# ---------------------------------------------------------------------------
# Registration helper
# ---------------------------------------------------------------------------

def register_boost_routes(app):
    app.register_blueprint(boost_bp)
