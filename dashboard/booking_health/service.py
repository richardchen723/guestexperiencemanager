#!/usr/bin/env python3
"""Read-only booking-health comparisons for the dashboard workspace."""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import date, timedelta
from typing import Any, Iterable

from sqlalchemy import func
from sqlalchemy.orm import selectinload

import dashboard.config as config
from brain.models import CalendarSnapshot, PriceLabsSnapshot, get_session as get_brain_session
from brain.scoring import is_confirmed_reservation_status
from dashboard.kpi.service import build_portfolio_scope
from dashboard.portfolio_mapping import portfolio_name_for_listing
from database.models import Listing, ListingTag, Reservation, get_session as get_main_session


HORIZONS = (7, 14)
UNDERPERFORMANCE_THRESHOLD_POINTS = 10.0
PRICELABS_FALLBACK_DAYS = 14


class BookingHealthService:
    """Build the comparison-first booking-health page without external calls."""

    def __init__(self, *, main_session=None, brain_session=None, today: date | None = None):
        self.main_session = main_session or get_main_session(config.MAIN_DATABASE_PATH)
        self.brain_session = brain_session or get_brain_session()
        self._owns_main_session = main_session is None
        self._owns_brain_session = brain_session is None
        self.today = today or date.today()

    def close(self):
        if self._owns_main_session:
            self.main_session.close()
        if self._owns_brain_session:
            self.brain_session.close()

    def get_dashboard(self, *, portfolio_name: str | None = None) -> dict[str, Any]:
        listings = self._active_listings()
        scope = build_portfolio_scope(listings, portfolio_name)
        listing_ids = scope["listing_ids"]
        public_scope = {
            "selected": scope["selected"],
            "property_count": scope["property_count"],
            "total_property_count": len(listings),
            "portfolios": scope["portfolios"],
        }
        if not listing_ids:
            return empty_dashboard(public_scope, "No active listings are mapped to this portfolio.")

        snapshot_date = (
            self.brain_session.query(func.max(CalendarSnapshot.snapshot_date))
            .filter(
                CalendarSnapshot.listing_id.in_(listing_ids),
                CalendarSnapshot.snapshot_date <= self.today,
            )
            .scalar()
        )
        if not snapshot_date:
            return empty_dashboard(public_scope, "No forward calendar snapshot is available yet.")

        calendar_rows = (
            self.brain_session.query(CalendarSnapshot)
            .filter(
                CalendarSnapshot.listing_id.in_(listing_ids),
                CalendarSnapshot.snapshot_date == snapshot_date,
                CalendarSnapshot.calendar_date >= snapshot_date,
                CalendarSnapshot.calendar_date < snapshot_date + timedelta(days=max(HORIZONS)),
            )
            .all()
        )
        calendar_by_listing: dict[int, list[Any]] = defaultdict(list)
        for row in calendar_rows:
            calendar_by_listing[int(row.listing_id)].append(row)

        pricelabs_by_listing = self._latest_pricelabs_snapshots(listing_ids, snapshot_date=snapshot_date)
        prior_start = same_date_last_year(snapshot_date)
        prior_end = prior_start + timedelta(days=max(HORIZONS))
        reservations = (
            self.main_session.query(Reservation)
            .filter(
                Reservation.listing_id.in_(listing_ids),
                Reservation.arrival_date < prior_end,
                Reservation.departure_date > prior_start,
            )
            .all()
        )
        reservations_by_listing: dict[int, list[Any]] = defaultdict(list)
        for reservation in reservations:
            if is_confirmed_reservation_status(getattr(reservation, "status", None)):
                reservations_by_listing[int(reservation.listing_id)].append(reservation)

        listing_id_set = set(listing_ids)
        listing_map = {
            int(listing.listing_id): listing
            for listing in listings
            if int(listing.listing_id) in listing_id_set
        }
        all_items = []
        for listing_id in listing_ids:
            listing = listing_map.get(int(listing_id))
            if not listing:
                continue
            item = build_listing_comparison(
                listing,
                calendar_rows=calendar_by_listing.get(int(listing_id), []),
                pricelabs_snapshot=pricelabs_by_listing.get(int(listing_id)),
                prior_reservations=reservations_by_listing.get(int(listing_id), []),
                snapshot_date=snapshot_date,
                threshold_points=UNDERPERFORMANCE_THRESHOLD_POINTS,
                pricelabs_url=config.PRICELABS_APP_URL,
            )
            all_items.append(item)

        items = [
            item
            for item in all_items
            if any(item["horizons"][str(days)]["underperforming"] for days in HORIZONS)
        ]
        items.sort(key=lambda item: listing_rank(item, 14))
        summary_by_horizon = {}
        for days in HORIZONS:
            key = str(days)
            underperforming = [item for item in items if item["horizons"][key]["underperforming"]]
            comparable = [item for item in all_items if comparison_ready_for_listing(item, days)]
            summary_by_horizon[key] = {
                "underperforming_count": len(underperforming),
                "comparable_count": len(comparable),
            }

        latest_calendar_created_at = max(
            (getattr(row, "created_at", None) for row in calendar_rows if getattr(row, "created_at", None)),
            default=None,
        )
        latest_pricelabs_created_at = max(
            (
                getattr(snapshot, "created_at", None)
                for snapshot in pricelabs_by_listing.values()
                if getattr(snapshot, "created_at", None)
            ),
            default=None,
        )
        updated_at = max(
            (value for value in (latest_calendar_created_at, latest_pricelabs_created_at) if value),
            default=None,
        )
        return {
            "has_data": bool(calendar_rows),
            "reason": None if calendar_rows else "The latest calendar snapshot does not contain forward dates.",
            "scope": public_scope,
            "snapshot_date": snapshot_date.isoformat(),
            "updated_at": updated_at.isoformat() if updated_at else None,
            "default_horizon": 14,
            "threshold_points": UNDERPERFORMANCE_THRESHOLD_POINTS,
            "summary_by_horizon": summary_by_horizon,
            "items": items,
            "pricelabs_url": config.PRICELABS_APP_URL,
        }

    def _active_listings(self) -> list[Listing]:
        return (
            self.main_session.query(Listing)
            .options(selectinload(Listing.tags).selectinload(ListingTag.tag))
            .filter(func.lower(func.coalesce(Listing.status, "")) != "deleted")
            .order_by(Listing.internal_listing_name, Listing.name, Listing.listing_id)
            .all()
        )

    def _latest_pricelabs_snapshots(
        self,
        listing_ids: list[int],
        *,
        snapshot_date: date,
    ) -> dict[int, PriceLabsSnapshot]:
        ranked = (
            self.brain_session.query(
                PriceLabsSnapshot.pricelabs_snapshot_id.label("snapshot_id"),
                func.row_number().over(
                    partition_by=PriceLabsSnapshot.listing_id,
                    order_by=(
                        PriceLabsSnapshot.snapshot_date.desc(),
                        PriceLabsSnapshot.created_at.desc(),
                        PriceLabsSnapshot.pricelabs_snapshot_id.desc(),
                    ),
                ).label("snapshot_rank"),
            )
            .filter(
                PriceLabsSnapshot.listing_id.in_(listing_ids),
                PriceLabsSnapshot.snapshot_date <= snapshot_date,
                PriceLabsSnapshot.snapshot_date >= snapshot_date - timedelta(days=PRICELABS_FALLBACK_DAYS),
                PriceLabsSnapshot.status.in_(("ok", "partial")),
            )
            .subquery()
        )
        rows = (
            self.brain_session.query(PriceLabsSnapshot)
            .join(ranked, PriceLabsSnapshot.pricelabs_snapshot_id == ranked.c.snapshot_id)
            .filter(ranked.c.snapshot_rank == 1)
            .all()
        )
        result = {}
        for row in rows:
            listing_id = int(row.listing_id)
            if not usable_pricelabs_snapshot(row):
                continue
            result[listing_id] = row
        return result


def build_listing_comparison(
    listing: Any,
    *,
    calendar_rows: Iterable[Any],
    pricelabs_snapshot: Any | None,
    prior_reservations: Iterable[Any],
    snapshot_date: date,
    threshold_points: float = UNDERPERFORMANCE_THRESHOLD_POINTS,
    pricelabs_url: str = "https://app.pricelabs.co",
) -> dict[str, Any]:
    """Build one listing row for both supported horizons."""
    calendar_map = {getattr(row, "calendar_date", None): calendar_state(row) for row in calendar_rows or []}
    calendar = []
    for offset in range(max(HORIZONS)):
        calendar_date = snapshot_date + timedelta(days=offset)
        calendar.append({
            "date": calendar_date.isoformat(),
            "day": calendar_date.day,
            "weekday": calendar_date.strftime("%a"),
            "state": calendar_map.get(calendar_date, "unknown"),
        })

    pricelabs_days = pricelabs_daily_rows(pricelabs_snapshot)
    horizons = {}
    for horizon_days in HORIZONS:
        scoped_calendar = calendar[:horizon_days]
        booked_nights = sum(day["state"] == "booked" for day in scoped_calendar)
        open_nights = sum(day["state"] == "open" for day in scoped_calendar)
        blocked_nights = sum(day["state"] == "blocked" for day in scoped_calendar)
        known_nights = booked_nights + open_nights + blocked_nights
        sellable_nights = booked_nights + open_nights
        minimum_calendar_coverage = max(1, math.ceil(horizon_days * 0.8))
        unit_occupancy = (
            percent(booked_nights, sellable_nights)
            if known_nights >= minimum_calendar_coverage
            else None
        )
        market_occupancy, market_horizon_days = market_occupancy_for_horizon(
            pricelabs_snapshot,
            horizon_days,
        )
        last_year_occupancy, last_year_source = last_year_occupancy_for_horizon(
            pricelabs_days,
            prior_reservations,
            listing_id=int(listing.listing_id),
            snapshot_date=snapshot_date,
            horizon_days=horizon_days,
        )
        market_gap = difference(unit_occupancy, market_occupancy)
        last_year_gap = difference(unit_occupancy, last_year_occupancy)
        comparable_gaps = [gap for gap in (market_gap, last_year_gap) if gap is not None]
        underperforming = bool(comparable_gaps) and min(comparable_gaps) <= -abs(threshold_points)
        horizons[str(horizon_days)] = {
            "unit_occupancy": unit_occupancy,
            "market_occupancy": market_occupancy,
            "market_horizon_days": market_horizon_days,
            "last_year_occupancy": last_year_occupancy,
            "last_year_source": last_year_source,
            "market_gap": market_gap,
            "last_year_gap": last_year_gap,
            "worst_gap": min(comparable_gaps) if comparable_gaps else None,
            "underperforming": underperforming,
            "booked_nights": booked_nights,
            "open_nights": open_nights,
            "blocked_nights": blocked_nights,
            "known_nights": known_nights,
            "sellable_nights": sellable_nights,
        }

    tag_names = [
        link.tag.name
        for link in getattr(listing, "tags", [])
        if getattr(link, "tag", None) and getattr(link.tag, "name", None)
    ]
    portfolio_name = portfolio_name_for_listing(int(listing.listing_id), tag_names) or "Unmapped"
    city = str(getattr(listing, "city", "") or "").strip()
    state = str(getattr(listing, "state", "") or "").strip()
    location = ", ".join(part for part in (city, state) if part) or "Location unavailable"
    bedrooms = getattr(listing, "bedrooms", None)
    if bedrooms is not None:
        location = f"{location} · {format_number(bedrooms)}BR"
    return {
        "listing_id": int(listing.listing_id),
        "listing_name": (
            str(getattr(listing, "internal_listing_name", "") or "").strip()
            or str(getattr(listing, "name", "") or "").strip()
            or f"Listing {listing.listing_id}"
        ),
        "location": location,
        "portfolio_name": portfolio_name,
        "calendar": calendar,
        "horizons": horizons,
        "pricelabs_url": pricelabs_url,
    }


def calendar_state(row: Any) -> str:
    status = str(getattr(row, "status", "") or "").strip().lower()
    if status in {"reserved", "booked"}:
        return "booked"
    if getattr(row, "is_available", None) is True or status == "available":
        return "open"
    if getattr(row, "is_available", None) is False or status == "blocked":
        return "blocked"
    return "unknown"


def usable_pricelabs_snapshot(snapshot: Any | None) -> bool:
    if not snapshot:
        return False
    raw = getattr(snapshot, "raw_payload", None) or {}
    return isinstance(raw, dict) and bool(raw.get("prices") or raw.get("metrics"))


def pricelabs_daily_rows(snapshot: Any | None) -> dict[date, dict[str, Any]]:
    raw = getattr(snapshot, "raw_payload", None) or {}
    rows = ((raw.get("prices") or {}).get("data") or []) if isinstance(raw, dict) else []
    result = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            row_date = date.fromisoformat(str(row.get("date") or "")[:10])
        except ValueError:
            continue
        result[row_date] = row
    return result


def market_occupancy_for_horizon(snapshot: Any | None, horizon_days: int) -> tuple[float | None, int | None]:
    raw = getattr(snapshot, "raw_payload", None) or {}
    metrics = ((raw.get("metrics") or {}).get("data") or {}) if isinstance(raw, dict) else {}
    market = ((metrics.get("market_level") or {}).get("occupancy") or {}) if isinstance(metrics, dict) else {}
    preferred = (7,) if horizon_days == 7 else (14, 15)
    for candidate in preferred:
        value = dictionary_number(market, candidate)
        if value is not None:
            return normalize_percent(value), candidate
    return None, None


def last_year_occupancy_for_horizon(
    pricelabs_days: dict[date, dict[str, Any]],
    reservations: Iterable[Any],
    *,
    listing_id: int,
    snapshot_date: date,
    horizon_days: int,
) -> tuple[float | None, str | None]:
    booked = 0
    sellable = 0
    covered = 0
    for offset in range(horizon_days):
        current_date = snapshot_date + timedelta(days=offset)
        row = pricelabs_days.get(current_date)
        if not row or "booking_status_STLY" not in row:
            continue
        covered += 1
        status = str(row.get("booking_status_STLY") or "").strip().lower()
        if status.startswith("booked") or status in {"reserved", "owner stay", "ownerstay"}:
            booked += 1
            sellable += 1
        elif status != "blocked":
            sellable += 1
    minimum_coverage = max(1, math.ceil(horizon_days * 0.8))
    if covered >= minimum_coverage and sellable:
        return percent(booked, sellable), "pricelabs_stly"

    prior_start = same_date_last_year(snapshot_date)
    prior_dates = [prior_start + timedelta(days=offset) for offset in range(horizon_days)]
    booked_dates = set()
    for reservation in reservations or []:
        if int(getattr(reservation, "listing_id", -1) or -1) != listing_id:
            continue
        if not is_confirmed_reservation_status(getattr(reservation, "status", None)):
            continue
        arrival = getattr(reservation, "arrival_date", None)
        departure = getattr(reservation, "departure_date", None)
        if not arrival or not departure:
            continue
        booked_dates.update(day for day in prior_dates if arrival <= day < departure)
    return percent(len(booked_dates), len(prior_dates)), "reservations"


def same_date_last_year(value: date) -> date:
    try:
        return value.replace(year=value.year - 1)
    except ValueError:
        return value.replace(year=value.year - 1, day=28)


def dictionary_number(values: Any, key: int) -> float | None:
    if not isinstance(values, dict):
        return None
    value = values.get(str(key), values.get(key))
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def normalize_percent(value: float) -> float:
    return round(value * 100 if 0 <= value <= 1 else value, 1)


def percent(numerator: int | float, denominator: int | float) -> float | None:
    if not denominator:
        return None
    return round(float(numerator) / float(denominator) * 100, 1)


def difference(left: float | None, right: float | None) -> float | None:
    if left is None or right is None:
        return None
    return round(left - right, 1)


def comparison_ready_for_listing(item: dict[str, Any], horizon_days: int) -> bool:
    horizon = item["horizons"][str(horizon_days)]
    return horizon["unit_occupancy"] is not None and (
        horizon["market_occupancy"] is not None or horizon["last_year_occupancy"] is not None
    )


def listing_rank(item: dict[str, Any], horizon_days: int) -> tuple[float, str]:
    horizon = item["horizons"][str(horizon_days)]
    gap = horizon["worst_gap"]
    if gap is None:
        fallback = min(
            (
                item["horizons"][str(days)]["worst_gap"]
                for days in HORIZONS
                if item["horizons"][str(days)]["worst_gap"] is not None
            ),
            default=0.0,
        )
        gap = fallback
    return (gap, item["listing_name"].lower())


def format_number(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    return str(int(number)) if number.is_integer() else f"{number:g}"


def empty_dashboard(scope: dict[str, Any], reason: str) -> dict[str, Any]:
    return {
        "has_data": False,
        "reason": reason,
        "scope": scope,
        "snapshot_date": None,
        "updated_at": None,
        "default_horizon": 14,
        "threshold_points": UNDERPERFORMANCE_THRESHOLD_POINTS,
        "summary_by_horizon": {
            str(days): {"underperforming_count": 0, "comparable_count": 0}
            for days in HORIZONS
        },
        "items": [],
        "pricelabs_url": config.PRICELABS_APP_URL,
    }
