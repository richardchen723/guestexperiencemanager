from datetime import date, datetime, timedelta
from types import SimpleNamespace

from dashboard.kpi.service import (
    build_portfolio_scope,
    calculate_advocacy_metric,
    calculate_forward_metric,
    calculate_outcome_metric,
    calculate_response_metric,
    calculate_revenue_metric,
    resolve_reporting_period,
)


def ns(**values):
    return SimpleNamespace(**values)


def test_response_metric_groups_guest_sequence_and_excludes_automation():
    now = datetime(2026, 8, 20, 12, 0)
    messages = [
        ns(conversation_id=1, message_id=1, created_at=now - timedelta(hours=3), is_incoming=1, sender_name="Guest"),
        ns(conversation_id=1, message_id=2, created_at=now - timedelta(hours=3) + timedelta(minutes=1), is_incoming=1, sender_name="Guest"),
        ns(conversation_id=1, message_id=3, created_at=now - timedelta(hours=3) + timedelta(minutes=2), is_incoming=0, sender_name="Host (Automated)"),
        ns(conversation_id=1, message_id=4, created_at=now - timedelta(hours=3) + timedelta(minutes=10), is_incoming=0, sender_name="Host"),
        ns(conversation_id=2, message_id=5, created_at=now - timedelta(hours=2), is_incoming=1, sender_name="Guest"),
        ns(conversation_id=2, message_id=6, created_at=now - timedelta(hours=2) + timedelta(minutes=30), is_incoming=0, sender_name="Host"),
        ns(conversation_id=3, message_id=7, created_at=now - timedelta(hours=5), is_incoming=1, sender_name="Guest"),
    ]

    result = calculate_response_metric(
        messages,
        current_start=now - timedelta(days=28),
        current_end=now + timedelta(seconds=1),
        now=now,
    )

    assert result["median_minutes"] == 20.0
    assert result["p90_minutes"] == 30.0
    assert result["within_15_minutes_pct"] == 50.0
    assert result["answered_sequences"] == 2
    assert result["open_over_4_hours"] == 1
    assert result["automated_messages_excluded"] == 1


def test_advocacy_uses_published_guest_reviews_and_eligible_channels_only():
    today = date(2026, 8, 20)
    reviews = [
        ns(review_id=1, reservation_id=1, overall_rating=10.0, review_date=today - timedelta(days=5), origin="Guest", status="published"),
        ns(review_id=2, reservation_id=2, overall_rating=9.0, review_date=today - timedelta(days=4), origin="Guest", status="published"),
        ns(review_id=3, reservation_id=3, overall_rating=7.0, review_date=today - timedelta(days=3), origin="Guest", status="published"),
        ns(review_id=4, reservation_id=4, overall_rating=10.0, review_date=today - timedelta(days=2), origin="Host", status="published"),
        ns(review_id=5, reservation_id=5, overall_rating=10.0, review_date=today - timedelta(days=1), origin="Guest", status="pending"),
    ]
    cohort = [
        ns(reservation_id=10, channel_name="AirbnbOfficial", source=None, status="new"),
        ns(reservation_id=11, channel_name="bookingcom", source=None, status="modified"),
        ns(reservation_id=12, channel_name="customIcal", source=None, status="new"),
    ]
    cohort_reviews = [
        ns(reservation_id=10, overall_rating=10.0, review_date=today, origin="Guest", status="published"),
        ns(reservation_id=12, overall_rating=10.0, review_date=today, origin="Guest", status="published"),
    ]

    result = calculate_advocacy_metric(
        reviews,
        cohort,
        cohort_reviews,
        range_start=today - timedelta(days=30),
        range_end_exclusive=today + timedelta(days=1),
    )

    assert result["perfect_score_pct"] == 33.3
    assert result["average_rating"] == 8.67
    assert result["low_score_pct"] == 33.3
    assert result["review_coverage_pct"] == 50.0
    assert result["eligible_stay_count"] == 2


def test_revenue_allocates_total_price_to_only_nights_inside_window():
    reservations = [
        ns(
            reservation_id=1,
            arrival_date=date(2026, 8, 1),
            departure_date=date(2026, 8, 5),
            total_price=400.0,
            currency="USD",
            status="new",
        )
    ]
    recent_bookings = [
        ns(status="new"),
        ns(status="cancelled"),
    ]

    result = calculate_revenue_metric(
        reservations,
        recent_bookings,
        current_start=date(2026, 8, 3),
        current_end=date(2026, 8, 7),
    )

    assert result["gross_stay_value"] == 200.0
    assert result["adr"] == 100.0
    assert result["reservation_nights"] == 2
    assert result["cancellation_rate_pct"] == 50.0


def test_forward_occupancy_excludes_blocks_and_pickup_uses_shared_dates():
    snapshot = date(2026, 8, 18)
    rows = [
        ns(listing_id=1, calendar_date=snapshot, status="reserved", is_available=False),
        ns(listing_id=1, calendar_date=snapshot + timedelta(days=1), status="available", is_available=True),
        ns(listing_id=1, calendar_date=snapshot + timedelta(days=2), status="blocked", is_available=False),
    ]
    prior_rows = [
        ns(listing_id=1, calendar_date=snapshot, status="available", is_available=True),
        ns(listing_id=1, calendar_date=snapshot + timedelta(days=1), status="available", is_available=True),
    ]

    result = calculate_forward_metric(
        rows,
        prior_rows,
        snapshot_date=snapshot,
        prior_snapshot_date=snapshot - timedelta(days=7),
        expected_listing_count=1,
    )

    near = result["horizons"][0]
    assert near["occupancy_pct"] == 50.0
    assert near["sellable_nights"] == 2
    assert near["blocked_nights"] == 1
    assert near["pickup_nights"] == 1
    assert result["property_coverage_pct"] == 100.0


def test_outcome_metric_keeps_needs_review_out_of_rate_but_in_coverage():
    current_start = date(2026, 7, 23)
    reservations = [
        ns(reservation_id=index, departure_date=date(2026, 8, index + 1))
        for index in range(1, 6)
    ]
    classifications = [
        ns(reservation_id=1, outcome="smooth", confidence=.95, classified_at=datetime(2026, 8, 10)),
        ns(reservation_id=2, outcome="recovered", confidence=.9, classified_at=datetime(2026, 8, 10)),
        ns(reservation_id=3, outcome="unresolved", confidence=.88, classified_at=datetime(2026, 8, 10)),
        ns(reservation_id=4, outcome="needs_review", confidence=.4, classified_at=datetime(2026, 8, 10)),
    ]

    result = calculate_outcome_metric(
        reservations,
        classifications,
        current_start=current_start,
        current_end=date(2026, 8, 20),
    )

    assert result["guest_outcome_pct"] == 66.7
    assert result["problem_incidence_pct"] == 66.7
    assert result["recovery_rate_pct"] == 50.0
    assert result["classification_coverage_pct"] == 60.0
    assert result["counts"] == {"smooth": 1, "recovered": 1, "unresolved": 1, "needs_review": 2}
    assert result["status"] == "degraded"


def test_portfolio_scope_defaults_to_all_and_filters_exact_portfolio():
    enchanted_tag = ns(tag=ns(name="enchanted havens"))
    listings = [
        ns(listing_id=1, tags=[enchanted_tag]),
        ns(listing_id=2, tags=[]),
    ]

    all_scope = build_portfolio_scope(listings, "unknown")
    selected_scope = build_portfolio_scope(listings, "Enchanted Havens")

    assert all_scope["selected"] == "all"
    assert all_scope["listing_ids"] == [1, 2]
    assert selected_scope["listing_ids"] == [1]
    assert selected_scope["property_count"] == 1


def test_reporting_period_presets_use_calendar_boundaries():
    today = date(2026, 8, 20)

    current = resolve_reporting_period("current_month", today=today)
    previous = resolve_reporting_period("last_month", today=today)
    three_months = resolve_reporting_period("last_3_months", today=today)

    assert (current.start_date, current.end_date) == (date(2026, 8, 1), today)
    assert (previous.start_date, previous.end_date) == (date(2026, 7, 1), date(2026, 7, 31))
    assert (three_months.start_date, three_months.end_date) == (date(2026, 6, 1), today)


def test_custom_reporting_period_validates_order_future_and_maximum_length():
    today = date(2026, 8, 20)
    custom = resolve_reporting_period("custom", "2026-05-10", "2026-08-01", today=today)
    assert custom.start_date == date(2026, 5, 10)
    assert custom.end_date == date(2026, 8, 1)

    for start, end, message in (
        ("2026-08-10", "2026-08-01", "From date"),
        ("2026-08-01", "2026-08-21", "future"),
        ("2025-01-01", "2026-08-01", "366 days"),
    ):
        try:
            resolve_reporting_period("custom", start, end, today=today)
        except ValueError as exc:
            assert message.lower() in str(exc).lower()
        else:
            raise AssertionError("Expected the custom reporting period to be rejected")
