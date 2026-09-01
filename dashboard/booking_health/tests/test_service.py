import os
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("DATABASE_URL", "postgresql://user:pass@localhost/db")
os.environ.setdefault("OPENAI_API_KEY", "test")
os.environ.setdefault("SECRET_KEY", "test-secret")

from dashboard.booking_health.service import (
    build_listing_comparison,
    market_occupancy_for_horizon,
)


def ns(**values):
    return SimpleNamespace(**values)


def listing_fixture(listing_id=41):
    return ns(
        listing_id=listing_id,
        internal_listing_name="Blue Haven",
        name="Blue Haven public name",
        city="Port Angeles",
        state="WA",
        bedrooms=3,
        tags=[],
    )


def calendar_fixture(start):
    rows = []
    states = ["reserved"] * 4 + ["available"] * 8 + ["blocked"] * 2
    for offset, status in enumerate(states):
        rows.append(ns(
            listing_id=41,
            calendar_date=start + timedelta(days=offset),
            status=status,
            is_available=status == "available",
        ))
    return rows


def pricelabs_fixture(start, *, include_stly=True):
    price_rows = []
    for offset in range(14):
        row = {"date": (start + timedelta(days=offset)).isoformat()}
        if include_stly:
            row["booking_status_STLY"] = "Booked" if offset < 7 else ""
        price_rows.append(row)
    return ns(
        raw_payload={
            "prices": {"data": price_rows},
            "metrics": {
                "data": {
                    "market_level": {"occupancy": {"7": 80, "15": 62}},
                    "listing_level": {"occupancy": {"7": 50, "15": 40}},
                }
            },
        }
    )


def test_listing_comparison_excludes_blocks_and_compares_market_and_last_year():
    start = date(2026, 9, 1)
    result = build_listing_comparison(
        listing_fixture(),
        calendar_rows=calendar_fixture(start),
        pricelabs_snapshot=pricelabs_fixture(start),
        prior_reservations=[],
        snapshot_date=start,
    )

    fourteen = result["horizons"]["14"]
    assert fourteen["unit_occupancy"] == 33.3
    assert fourteen["booked_nights"] == 4
    assert fourteen["open_nights"] == 8
    assert fourteen["blocked_nights"] == 2
    assert fourteen["market_occupancy"] == 62.0
    assert fourteen["market_horizon_days"] == 15
    assert fourteen["last_year_occupancy"] == 50.0
    assert fourteen["last_year_source"] == "pricelabs_stly"
    assert fourteen["market_gap"] == -28.7
    assert fourteen["last_year_gap"] == -16.7
    assert fourteen["underperforming"] is True
    assert [day["state"] for day in result["calendar"][-2:]] == ["blocked", "blocked"]


def test_last_year_falls_back_to_confirmed_reservations():
    start = date(2026, 9, 1)
    prior = [
        ns(
            listing_id=41,
            arrival_date=date(2025, 9, 2),
            departure_date=date(2025, 9, 5),
            status="new",
        ),
        ns(
            listing_id=41,
            arrival_date=date(2025, 9, 5),
            departure_date=date(2025, 9, 7),
            status="cancelled",
        ),
    ]
    result = build_listing_comparison(
        listing_fixture(),
        calendar_rows=calendar_fixture(start),
        pricelabs_snapshot=pricelabs_fixture(start, include_stly=False),
        prior_reservations=prior,
        snapshot_date=start,
    )

    seven = result["horizons"]["7"]
    assert seven["last_year_occupancy"] == 42.9
    assert seven["last_year_source"] == "reservations"


def test_fully_blocked_inventory_is_not_labeled_underperforming():
    start = date(2026, 9, 1)
    blocked = [
        ns(
            listing_id=41,
            calendar_date=start + timedelta(days=offset),
            status="blocked",
            is_available=False,
        )
        for offset in range(14)
    ]
    result = build_listing_comparison(
        listing_fixture(),
        calendar_rows=blocked,
        pricelabs_snapshot=pricelabs_fixture(start),
        prior_reservations=[],
        snapshot_date=start,
    )

    assert result["horizons"]["14"]["unit_occupancy"] is None
    assert result["horizons"]["14"]["underperforming"] is False


def test_partial_calendar_coverage_does_not_create_a_false_underperformance_flag():
    start = date(2026, 9, 1)
    partial = [
        ns(
            listing_id=41,
            calendar_date=start,
            status="available",
            is_available=True,
        )
    ]
    result = build_listing_comparison(
        listing_fixture(),
        calendar_rows=partial,
        pricelabs_snapshot=pricelabs_fixture(start),
        prior_reservations=[],
        snapshot_date=start,
    )

    assert result["horizons"]["14"]["unit_occupancy"] is None
    assert result["horizons"]["14"]["underperforming"] is False


def test_market_horizon_uses_exact_seven_and_nearest_fifteen_day_metric():
    start = date(2026, 9, 1)
    snapshot = pricelabs_fixture(start)

    assert market_occupancy_for_horizon(snapshot, 7) == (80.0, 7)
    assert market_occupancy_for_horizon(snapshot, 14) == (62.0, 15)


def test_booking_health_frontend_keeps_pricing_and_diagnosis_out_of_the_page():
    dashboard = Path(__file__).parents[2]
    template = (dashboard / "templates" / "booking_health" / "index.html").read_text()
    script = (dashboard / "static" / "js" / "booking-health.js").read_text()

    combined = f"{template}\n{script}"
    assert "Open in PriceLabs" in combined
    assert "Average nightly rate" not in combined
    assert "Adjust rates" not in combined
    assert "Why it is behind" not in combined
    assert "suggested improvement" not in combined.lower()


def test_booking_health_header_resets_the_global_sticky_header_styles():
    dashboard = Path(__file__).parents[2]
    stylesheet = (dashboard / "static" / "css" / "booking-health.css").read_text()

    header_rule = stylesheet.split(".booking-health-header {", 1)[1].split("}", 1)[0]
    assert "position: static" in header_rule
    assert "height: auto" in header_rule
    assert "background: transparent" in header_rule
    assert "box-shadow: none" in header_rule
