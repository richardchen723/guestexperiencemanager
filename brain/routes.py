#!/usr/bin/env python3
"""STR Signal Brain page and API routes."""

from __future__ import annotations

import logging

from flask import Blueprint, jsonify, render_template, request

from brain.intelligence import CodexIntelligenceService
from brain.services import BrainRunService, BrainService
from dashboard.auth.decorators import approved_required
from dashboard.auth.session import get_current_user

logger = logging.getLogger(__name__)

brain_bp = Blueprint("brain", __name__)


@brain_bp.route("/")
@approved_required
def today_page():
    return render_template("brain/app.html", current_user=get_current_user(), active_page="today")


@brain_bp.route("/portfolios")
@approved_required
def portfolios_page():
    return render_template("brain/app.html", current_user=get_current_user(), active_page="portfolios")


@brain_bp.route("/portfolios/<int:portfolio_id>")
@approved_required
def portfolio_detail_page(portfolio_id: int):
    return render_template("brain/app.html", current_user=get_current_user(), active_page="portfolio-detail", portfolio_id=portfolio_id)


@brain_bp.route("/signals")
@approved_required
def signals_page():
    return render_template("brain/app.html", current_user=get_current_user(), active_page="signals")


@brain_bp.route("/todos")
@approved_required
def todos_page():
    return render_template("brain/app.html", current_user=get_current_user(), active_page="todos")


@brain_bp.route("/booking-health")
@approved_required
def booking_health_page():
    return render_template("brain/app.html", current_user=get_current_user(), active_page="booking-health")


@brain_bp.route("/open-loops")
@approved_required
def open_loops_page():
    return render_template("brain/app.html", current_user=get_current_user(), active_page="open-loops")


@brain_bp.route("/ask")
@approved_required
def ask_page():
    return render_template("brain/app.html", current_user=get_current_user(), active_page="ask")


@brain_bp.route("/settings")
@approved_required
def settings_page():
    return render_template("brain/app.html", current_user=get_current_user(), active_page="settings")


@brain_bp.route("/api/brain/today")
@approved_required
def api_today():
    service = BrainService(get_current_user())
    try:
        return jsonify(service.get_today(portfolio_id=request.args.get("portfolio_id")))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    finally:
        service.close()


@brain_bp.route("/api/brain/portfolios")
@approved_required
def api_portfolios():
    service = BrainService(get_current_user())
    try:
        return jsonify(service.list_portfolios())
    finally:
        service.close()


@brain_bp.route("/api/brain/portfolios/<int:portfolio_id>")
@approved_required
def api_portfolio_detail(portfolio_id: int):
    service = BrainService(get_current_user())
    try:
        return jsonify(service.get_portfolio_detail(portfolio_id))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 404
    finally:
        service.close()


@brain_bp.route("/api/brain/signals")
@approved_required
def api_signals():
    service = BrainService(get_current_user())
    try:
        return jsonify(
            service.list_signals(
                {
                    "portfolio_id": request.args.get("portfolio_id"),
                    "category": request.args.get("category"),
                    "status": request.args.get("status"),
                    "severity": request.args.get("severity"),
                    "audience": request.args.get("audience"),
                    "limit": request.args.get("limit", 100),
                }
            )
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    finally:
        service.close()


@brain_bp.route("/api/brain/signals/<int:signal_id>/status", methods=["PATCH"])
@approved_required
def api_update_signal_status(signal_id: int):
    data = request.get_json(silent=True) or {}
    service = BrainService(get_current_user())
    try:
        return jsonify(service.update_signal_status(signal_id, data.get("status")))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    finally:
        service.close()


@brain_bp.route("/api/brain/todos")
@approved_required
def api_todos():
    service = BrainService(get_current_user())
    try:
        return jsonify(
            service.list_manager_todos(
                limit=request.args.get("limit", 9),
                portfolio_id=request.args.get("portfolio_id"),
            )
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    finally:
        service.close()


@brain_bp.route("/api/brain/booking-health")
@approved_required
def api_booking_health():
    service = BrainService(get_current_user())
    try:
        return jsonify(service.get_booking_health(portfolio_id=request.args.get("portfolio_id")))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    finally:
        service.close()


@brain_bp.route("/api/brain/open-loops")
@approved_required
def api_open_loops():
    service = BrainService(get_current_user())
    try:
        return jsonify(service.list_open_loops(portfolio_id=request.args.get("portfolio_id")))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    finally:
        service.close()


@brain_bp.route("/api/brain/data-foundation")
@approved_required
def api_data_foundation():
    service = BrainService(get_current_user())
    try:
        return jsonify(service.get_data_foundation(portfolio_id=request.args.get("portfolio_id")))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    finally:
        service.close()


@brain_bp.route("/api/brain/data-foundation/audit")
@approved_required
def api_data_foundation_audit():
    service = BrainService(get_current_user())
    try:
        return jsonify(service.data_foundation_audit(portfolio_id=request.args.get("portfolio_id")))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    finally:
        service.close()


@brain_bp.route("/api/brain/data-foundation/facts")
@approved_required
def api_data_foundation_facts():
    service = BrainService(get_current_user())
    try:
        return jsonify(
            service.list_business_facts(
                {
                    "portfolio_id": request.args.get("portfolio_id"),
                    "source_key": request.args.get("source_key"),
                    "fact_type": request.args.get("fact_type"),
                    "status": request.args.get("status"),
                    "listing_id": request.args.get("listing_id"),
                    "reservation_id": request.args.get("reservation_id"),
                    "guest_id": request.args.get("guest_id"),
                    "occurred_from": request.args.get("occurred_from"),
                    "occurred_to": request.args.get("occurred_to"),
                    "limit": request.args.get("limit", 100),
                }
            )
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    finally:
        service.close()


@brain_bp.route("/api/brain/data-foundation/metrics")
@approved_required
def api_data_foundation_metrics():
    service = BrainService(get_current_user())
    try:
        return jsonify(
            service.list_business_metrics(
                {
                    "portfolio_id": request.args.get("portfolio_id"),
                    "metric_name": request.args.get("metric_name"),
                    "category": request.args.get("category"),
                    "grain": request.args.get("grain"),
                    "status": request.args.get("status"),
                    "listing_id": request.args.get("listing_id"),
                    "metric_date": request.args.get("metric_date"),
                    "metric_from": request.args.get("metric_from"),
                    "metric_to": request.args.get("metric_to"),
                    "horizon_days": request.args.get("horizon_days"),
                    "limit": request.args.get("limit", 100),
                }
            )
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    finally:
        service.close()


@brain_bp.route("/api/brain/intelligence")
@approved_required
def api_codex_intelligence():
    service = CodexIntelligenceService()
    try:
        return jsonify(
            service.list_insights(
                category=request.args.get("category"),
                status=request.args.get("status") or "active",
                limit=int(request.args.get("limit", 100)),
            )
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    finally:
        service.close()


@brain_bp.route("/api/brain/ask", methods=["POST"])
@approved_required
def api_ask():
    data = request.get_json(silent=True) or {}
    service = BrainService(get_current_user())
    try:
        return jsonify(service.ask(data.get("question", ""), portfolio_id=data.get("portfolio_id") or request.args.get("portfolio_id")))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    finally:
        service.close()


@brain_bp.route("/api/brain/runs/morning", methods=["POST"])
@approved_required
def api_run_morning():
    return _run_brain("morning")


@brain_bp.route("/api/brain/runs/afternoon", methods=["POST"])
@approved_required
def api_run_afternoon():
    return _run_brain("afternoon")


@brain_bp.route("/api/brain/runs/manual", methods=["POST"])
@approved_required
def api_run_manual():
    return _run_brain("manual")


@brain_bp.route("/api/brain/runs/aggregate", methods=["POST"])
@approved_required
def api_run_aggregate():
    from brain.aggregator import BrainDataAggregator

    pull = request.args.get("pull") == "true"
    deep_hostaway = request.args.get("deep") == "true" or request.args.get("deep_hostaway") == "true"
    source_refresh_result = None
    runner = BrainDataAggregator()
    try:
        if pull:
            source_runner = BrainRunService()
            try:
                source_refresh_result = source_runner.refresh_source_snapshots(
                    pull_hostaway=True,
                    force_hostaway=deep_hostaway,
                )
            finally:
                source_runner.close()
        result = runner.run(mode="manual", pull=False)
        if source_refresh_result is not None:
            result["pull_result"] = source_refresh_result
        return jsonify(result)
    except Exception as exc:
        logger.error("Brain aggregate run failed: %s", exc, exc_info=True)
        return jsonify({"error": str(exc)}), 500
    finally:
        runner.close()


@brain_bp.route("/api/brain/settings/portfolios", methods=["POST"])
@approved_required
def api_create_portfolio():
    data = request.get_json(silent=True) or {}
    service = BrainService(get_current_user())
    try:
        return jsonify(service.create_portfolio(data.get("name", ""), data.get("description", ""))), 201
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    finally:
        service.close()


@brain_bp.route("/api/brain/settings/data")
@approved_required
def api_settings_data():
    service = BrainService(get_current_user())
    try:
        return jsonify(service.settings_data())
    finally:
        service.close()


@brain_bp.route("/api/brain/settings/portfolio-listings", methods=["POST"])
@approved_required
def api_map_listing():
    data = request.get_json(silent=True) or {}
    service = BrainService(get_current_user())
    try:
        return jsonify(service.map_listing_to_portfolio(int(data["listing_id"]), int(data["portfolio_id"])))
    except (KeyError, ValueError) as exc:
        return jsonify({"error": str(exc)}), 400
    finally:
        service.close()


@brain_bp.route("/api/brain/settings/portfolio-users", methods=["POST"])
@approved_required
def api_assign_portfolio_user():
    data = request.get_json(silent=True) or {}
    service = BrainService(get_current_user())
    try:
        return jsonify(
            service.assign_user_to_portfolio(
                int(data["user_id"]),
                int(data["portfolio_id"]),
            )
        )
    except (KeyError, ValueError) as exc:
        return jsonify({"error": str(exc)}), 400
    finally:
        service.close()


@brain_bp.route("/api/brain/settings/portfolio-users", methods=["DELETE"])
@approved_required
def api_remove_portfolio_user():
    data = request.get_json(silent=True) or {}
    service = BrainService(get_current_user())
    try:
        return jsonify(service.remove_user_from_portfolio(int(data["user_id"]), int(data["portfolio_id"])))
    except (KeyError, ValueError) as exc:
        return jsonify({"error": str(exc)}), 400
    finally:
        service.close()


@brain_bp.route("/api/brain/settings/bootstrap", methods=["POST"])
@approved_required
def api_bootstrap():
    service = BrainService(get_current_user())
    try:
        result = service.ensure_default_portfolio()
        return jsonify({"status": "bootstrapped", "result": result})
    finally:
        service.close()


def _run_brain(run_type: str):
    runner = BrainRunService()
    try:
        return jsonify(runner.run(run_type=run_type, deliver=request.args.get("deliver") == "true"))
    except Exception as exc:
        logger.error("Brain run failed: %s", exc, exc_info=True)
        return jsonify({"error": str(exc)}), 500
    finally:
        runner.close()
