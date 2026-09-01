#!/usr/bin/env python3
"""Booking-health workspace route."""

from flask import Blueprint, render_template, request

from dashboard.auth.decorators import approved_required, check_feature_access
from dashboard.auth.session import get_current_user
from dashboard.booking_health.service import BookingHealthService


booking_health_bp = Blueprint("booking_health", __name__, url_prefix="/workspace/booking-health")


@booking_health_bp.before_request
def require_booking_health_access():
    return check_feature_access("booking_health")


@booking_health_bp.route("/")
@approved_required
def booking_health_page():
    service = BookingHealthService()
    try:
        return render_template(
            "booking_health/index.html",
            current_user=get_current_user(),
            booking_health=service.get_dashboard(portfolio_name=request.args.get("portfolio")),
        )
    finally:
        service.close()


def register_booking_health_routes(app):
    app.register_blueprint(booking_health_bp)
