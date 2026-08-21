#!/usr/bin/env python3
"""Pure helpers for Brain ranking, status normalization, and booking-health math."""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timedelta
from typing import Any, Iterable

from brain.models import SIGNAL_AUDIENCES, SIGNAL_CATEGORIES, SIGNAL_SEVERITIES, SIGNAL_STATUSES

SEVERITY_WEIGHT = {
    "low": 15,
    "medium": 35,
    "high": 65,
    "critical": 90,
}

CATEGORY_WEIGHT = {
    "guest_experience": 18,
    "review_risk": 22,
    "operational_open_loop": 14,
    "checkin_checkout_risk": 20,
    "repeated_issue": 12,
    "revenue_booking_health": 16,
    "owner_decision": 18,
}

NON_BOOKED_RESERVATION_STATUSES = {
    "awaitingpayment",
    "cancelled",
    "canceled",
    "declined",
    "expired",
    "inquiry",
    "inquirynotpossible",
    "inquirypreapproved",
    "pending",
}


def is_confirmed_reservation_status(status: Any) -> bool:
    """Return whether a Hostaway reservation status represents occupied inventory."""
    normalized = str(status or "").strip().lower().replace("_", "").replace(" ", "")
    if not normalized or normalized.startswith("inquiry"):
        return False
    return normalized not in NON_BOOKED_RESERVATION_STATUSES


def normalize_signal_status(status: str | None) -> str:
    """Normalize a signal status to the lightweight MVP status set."""
    value = (status or "new").strip().lower().replace(" ", "_")
    if value not in SIGNAL_STATUSES:
        raise ValueError(f"Unsupported signal status: {status}")
    return value


def normalize_signal_category(category: str | None) -> str:
    """Normalize a signal category."""
    value = (category or "operational_open_loop").strip().lower().replace(" ", "_").replace("/", "_")
    aliases = {
        "guest": "guest_experience",
        "review": "review_risk",
        "open_loop": "operational_open_loop",
        "check_in_checkout_risk": "checkin_checkout_risk",
        "check_in_issue": "checkin_checkout_risk",
        "checkin_issue": "checkin_checkout_risk",
        "check_in": "checkin_checkout_risk",
        "guest_check_in": "checkin_checkout_risk",
        "guest_checkin": "checkin_checkout_risk",
        "guest_verification": "checkin_checkout_risk",
        "id_verification": "checkin_checkout_risk",
        "identity_verification": "checkin_checkout_risk",
        "arrival_issue": "checkin_checkout_risk",
        "arrival_friction": "checkin_checkout_risk",
        "checkout": "checkin_checkout_risk",
        "guest_experience_risk": "guest_experience",
        "amenity_issue": "review_risk",
        "compliance_risk": "review_risk",
        "platform_compliance": "review_risk",
        "revenue": "revenue_booking_health",
        "revenue_risk": "revenue_booking_health",
        "booking_health": "revenue_booking_health",
        "booking_risk": "revenue_booking_health",
        "booking_pace": "revenue_booking_health",
        "booking_momentum": "revenue_booking_health",
        "demand_risk": "revenue_booking_health",
        "conversion_risk": "revenue_booking_health",
        "pricing": "revenue_booking_health",
        "occupancy": "revenue_booking_health",
        "revenue_management": "revenue_booking_health",
        "owner": "owner_decision",
    }
    value = aliases.get(value, value)
    if value not in SIGNAL_CATEGORIES:
        return "operational_open_loop"
    return value


def normalize_signal_severity(severity: str | None) -> str:
    """Normalize a signal severity."""
    value = (severity or "medium").strip().lower()
    if value not in SIGNAL_SEVERITIES:
        return "medium"
    return value


def normalize_signal_audience(audience: str | None, *, category: str | None = None) -> str:
    """Collapse legacy role language into Brain's shared team view."""
    if category is not None:
        return "revenue" if normalize_signal_category(category) == "revenue_booking_health" else "operator"
    value = (audience or "").strip().lower().replace(" ", "_")
    if value in {"revenue", "pricing", "booking", "booking_health"}:
        return "revenue"
    if value not in SIGNAL_AUDIENCES:
        return "operator"
    return value


def make_dedupe_key(*, category: str, portfolio_id: int | None, listing_id: int | None, reservation_id: int | None, title: str) -> str:
    """Create a stable dedupe key for preserving signal status across runs."""
    payload = {
        "category": normalize_signal_category(category),
        "portfolio_id": portfolio_id,
        "listing_id": listing_id,
        "reservation_id": reservation_id,
        "title": " ".join((title or "").lower().split())[:160],
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def rank_signal_payload(payload: dict[str, Any]) -> float:
    """Rank a signal using deterministic factors before optional model refinement."""
    severity = normalize_signal_severity(payload.get("severity"))
    category = normalize_signal_category(payload.get("category"))
    confidence = _clamp_float(payload.get("confidence", 0.5), 0.0, 1.0)
    urgency = _clamp_float(payload.get("urgency", 0.0), 0.0, 1.0)
    repetition = _clamp_float(payload.get("repetition", 0.0), 0.0, 1.0)
    revenue_impact = _clamp_float(payload.get("revenue_impact", 0.0), 0.0, 1.0)
    silence_risk = _clamp_float(payload.get("silence_risk", 0.0), 0.0, 1.0)

    score = SEVERITY_WEIGHT[severity]
    score += CATEGORY_WEIGHT[category]
    score += confidence * 18
    score += urgency * 16
    score += repetition * 10
    score += revenue_impact * 12
    score += silence_risk * 10
    return round(min(score, 100.0), 2)


def compute_booking_health_proxy(
    reservations: Iterable[Any],
    *,
    listing_id: int,
    start_date: date,
    horizon_days: int,
    expected_occupancy_rate: float | None = None,
    calendar_days: Iterable[Any] | None = None,
) -> dict[str, Any]:
    """
    Compute a Hostaway-only booking-health proxy.

    This is intentionally conservative; PriceLabs or market data can lift
    confidence later, but the MVP still surfaces obvious weakness.
    """
    end_date = start_date + timedelta(days=horizon_days)
    booked_dates: set[date] = set()
    available_dates: set[date] = set()
    blocked_dates: set[date] = set()
    covered_dates: set[date] = set()

    for day in calendar_days or []:
        if getattr(day, "listing_id", listing_id) != listing_id:
            continue
        calendar_date = getattr(day, "calendar_date", None)
        if not calendar_date or not (start_date <= calendar_date < end_date):
            continue
        covered_dates.add(calendar_date)
        status = str(getattr(day, "status", "") or "").strip().lower()
        is_available = getattr(day, "is_available", None)
        if status in {"reserved", "booked"}:
            booked_dates.add(calendar_date)
        elif is_available is True or status == "available":
            available_dates.add(calendar_date)
        elif is_available is False or status == "blocked":
            blocked_dates.add(calendar_date)

    minimum_calendar_days = max(1, int(horizon_days * 0.8))
    calendar_is_authoritative = len(covered_dates) >= minimum_calendar_days
    if not calendar_is_authoritative:
        for reservation in reservations:
            if getattr(reservation, "listing_id", None) != listing_id:
                continue
            if not is_confirmed_reservation_status(getattr(reservation, "status", None)):
                continue
            arrival = getattr(reservation, "arrival_date", None)
            departure = getattr(reservation, "departure_date", None)
            if not arrival or not departure:
                continue
            current = max(arrival, start_date)
            stop = min(departure, end_date)
            while current < stop:
                booked_dates.add(current)
                current += timedelta(days=1)
        available_dates = {
            start_date + timedelta(days=offset)
            for offset in range(max(horizon_days, 0))
        } - booked_dates
        blocked_dates = set()

    booked_nights = len(booked_dates)
    available_nights = len(available_dates)
    blocked_nights = len(blocked_dates)
    sellable_nights = booked_nights + available_nights
    occupancy_is_measurable = sellable_nights > 0
    occupancy_rate = booked_nights / sellable_nights if occupancy_is_measurable else 0.0
    expected = expected_occupancy_rate
    if expected is None:
        expected = 0.45 if horizon_days <= 7 else 0.55 if horizon_days <= 30 else 0.5
    gap = expected - occupancy_rate if occupancy_is_measurable else None

    if sellable_nights <= 0 and calendar_is_authoritative:
        diagnosis = "inventory_blocked"
        recommended_action = "Confirm the calendar blocks are intentional before evaluating demand or changing price."
        confidence = 0.95
    elif gap is not None and gap >= 0.35:
        diagnosis = "weak_booking_pace"
        recommended_action = "Review pricing, minimum-stay rules, and listing freshness."
        confidence = 0.72
    elif gap is not None and gap >= 0.2:
        diagnosis = "watch_booking_pace"
        recommended_action = "Watch pickup and review restrictions for near-term gaps."
        confidence = 0.58
    elif occupancy_rate < 0.2 and horizon_days <= 7:
        diagnosis = "last_minute_gap_risk"
        recommended_action = "Check short-gap availability, discounts, and same-week visibility."
        confidence = 0.65
    else:
        diagnosis = "healthy"
        recommended_action = "No immediate booking-health action."
        confidence = 0.5

    return {
        "listing_id": listing_id,
        "horizon_days": horizon_days,
        "booked_nights": booked_nights,
        "available_nights": available_nights,
        "blocked_nights": blocked_nights,
        "sellable_nights": sellable_nights,
        "calendar_coverage_days": len(covered_dates),
        "occupancy_denominator": "sellable_nights" if calendar_is_authoritative else "horizon_days",
        "occupancy_source": "hostaway_calendar" if calendar_is_authoritative else "confirmed_reservations",
        "occupancy_rate_measurable": occupancy_is_measurable,
        "occupancy_rate": round(occupancy_rate, 4),
        "expected_occupancy_rate": round(expected, 4),
        "diagnosis": diagnosis,
        "confidence": confidence,
        "recommended_action": recommended_action,
    }


def _clamp_float(value: Any, minimum: float, maximum: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = minimum
    return min(max(parsed, minimum), maximum)
