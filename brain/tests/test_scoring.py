from datetime import date
from types import SimpleNamespace
import unittest

from brain.scoring import (
    compute_booking_health_proxy,
    is_confirmed_reservation_status,
    make_dedupe_key,
    normalize_signal_audience,
    normalize_signal_category,
    normalize_signal_status,
    rank_signal_payload,
)
from brain.services import (
    BrainService,
    _booking_analysis_sort_key,
    _build_deterministic_booking_analysis,
    _severity_from_horizons,
    booking_health_label,
    booking_health_rank_score,
    booking_health_signal_fields,
    booking_health_status,
    portfolio_operating_status,
)


class ReservationFixture:
    def __init__(self, listing_id, arrival_date, departure_date, status="confirmed"):
        self.listing_id = listing_id
        self.arrival_date = arrival_date
        self.departure_date = departure_date
        self.status = status


class CalendarDayFixture:
    def __init__(self, listing_id, calendar_date, *, is_available, status):
        self.listing_id = listing_id
        self.calendar_date = calendar_date
        self.is_available = is_available
        self.status = status


class ScoringTests(unittest.TestCase):
    def test_status_normalization_rejects_unknown_status(self):
        self.assertEqual(normalize_signal_status("Watching"), "watching")
        with self.assertRaises(ValueError):
            normalize_signal_status("done")

    def test_category_aliases_to_canonical_values(self):
        self.assertEqual(normalize_signal_category("review"), "review_risk")
        self.assertEqual(normalize_signal_category("check_in_issue"), "checkin_checkout_risk")
        self.assertEqual(normalize_signal_category("guest_check_in"), "checkin_checkout_risk")
        self.assertEqual(normalize_signal_category("guest_verification"), "checkin_checkout_risk")
        self.assertEqual(normalize_signal_category("guest_experience_risk"), "guest_experience")
        self.assertEqual(normalize_signal_category("amenity_issue"), "review_risk")
        self.assertEqual(normalize_signal_category("booking health"), "revenue_booking_health")
        self.assertEqual(normalize_signal_category("booking_pace"), "revenue_booking_health")
        self.assertEqual(normalize_signal_category("revenue_risk"), "revenue_booking_health")
        self.assertEqual(normalize_signal_category("demand risk"), "revenue_booking_health")
        self.assertEqual(normalize_signal_category("pricing"), "revenue_booking_health")
        self.assertEqual(normalize_signal_category("something new"), "operational_open_loop")

    def test_legacy_role_inputs_normalize_to_shared_team_audience(self):
        self.assertEqual(normalize_signal_audience("owner"), "operator")
        self.assertEqual(normalize_signal_audience("manager"), "operator")
        self.assertEqual(normalize_signal_audience("operator"), "operator")
        self.assertEqual(normalize_signal_audience("manager", category="revenue_booking_health"), "revenue")
        self.assertEqual(normalize_signal_audience("revenue", category="review_risk"), "operator")
        self.assertEqual(normalize_signal_audience("revenue", category="operational_open_loop"), "operator")

    def test_dedupe_key_is_stable_for_title_spacing(self):
        first = make_dedupe_key(
            category="review_risk",
            portfolio_id=1,
            listing_id=2,
            reservation_id=3,
            title=" Hot tub not working ",
        )
        second = make_dedupe_key(
            category="review_risk",
            portfolio_id=1,
            listing_id=2,
            reservation_id=3,
            title="hot   tub not working",
        )
        self.assertEqual(first, second)

    def test_rank_signal_payload_prioritizes_critical_review_risk(self):
        high = rank_signal_payload(
            {
                "category": "review_risk",
                "severity": "critical",
                "confidence": 0.9,
                "urgency": 1,
                "silence_risk": 1,
            }
        )
        low = rank_signal_payload(
            {
                "category": "repeated_issue",
                "severity": "low",
                "confidence": 0.4,
            }
        )
        self.assertGreater(high, low)
        self.assertLessEqual(high, 100)

    def test_booking_health_proxy_counts_future_occupied_nights(self):
        reservations = [
            ReservationFixture(10, date(2026, 7, 1), date(2026, 7, 4)),
            ReservationFixture(10, date(2026, 7, 5), date(2026, 7, 6)),
            ReservationFixture(11, date(2026, 7, 1), date(2026, 7, 6)),
            ReservationFixture(10, date(2026, 7, 6), date(2026, 7, 7), status="cancelled"),
        ]
        result = compute_booking_health_proxy(
            reservations,
            listing_id=10,
            start_date=date(2026, 7, 1),
            horizon_days=7,
            expected_occupancy_rate=0.8,
        )
        self.assertEqual(result["booked_nights"], 4)
        self.assertEqual(result["available_nights"], 3)
        self.assertIn(result["diagnosis"], {"watch_booking_pace", "weak_booking_pace"})

    def test_inquiries_are_not_confirmed_reservations(self):
        self.assertTrue(is_confirmed_reservation_status("new"))
        self.assertTrue(is_confirmed_reservation_status("modified"))
        self.assertTrue(is_confirmed_reservation_status("ownerStay"))
        self.assertFalse(is_confirmed_reservation_status("inquiry"))
        self.assertFalse(is_confirmed_reservation_status("inquiryPreApproved"))
        self.assertFalse(is_confirmed_reservation_status("cancelled"))

    def test_booking_health_uses_sellable_calendar_nights_and_separates_blocks(self):
        calendar_days = [
            CalendarDayFixture(10, date(2026, 7, 1), is_available=False, status="reserved"),
            CalendarDayFixture(10, date(2026, 7, 2), is_available=True, status="available"),
            CalendarDayFixture(10, date(2026, 7, 3), is_available=False, status="blocked"),
            CalendarDayFixture(10, date(2026, 7, 4), is_available=False, status="blocked"),
            CalendarDayFixture(10, date(2026, 7, 5), is_available=True, status="available"),
            CalendarDayFixture(10, date(2026, 7, 6), is_available=False, status="reserved"),
            CalendarDayFixture(10, date(2026, 7, 7), is_available=False, status="blocked"),
        ]

        result = compute_booking_health_proxy(
            [],
            listing_id=10,
            start_date=date(2026, 7, 1),
            horizon_days=7,
            calendar_days=calendar_days,
        )

        self.assertEqual(result["booked_nights"], 2)
        self.assertEqual(result["available_nights"], 2)
        self.assertEqual(result["blocked_nights"], 3)
        self.assertEqual(result["sellable_nights"], 4)
        self.assertEqual(result["occupancy_rate"], 0.5)
        self.assertEqual(result["occupancy_source"], "hostaway_calendar")

    def test_booking_health_does_not_call_fully_blocked_inventory_weak_demand(self):
        calendar_days = [
            CalendarDayFixture(10, date(2026, 7, day), is_available=False, status="blocked")
            for day in range(1, 8)
        ]

        result = compute_booking_health_proxy(
            [],
            listing_id=10,
            start_date=date(2026, 7, 1),
            horizon_days=7,
            calendar_days=calendar_days,
        )

        self.assertEqual(result["occupancy_rate"], 0.0)
        self.assertFalse(result["occupancy_rate_measurable"])
        self.assertEqual(result["diagnosis"], "inventory_blocked")
        self.assertEqual(result["blocked_nights"], 7)

    def test_booking_health_analysis_groups_horizons_into_high_priority_card(self):
        horizons = [
            {"horizon_days": 7, "occupancy_rate": 0.0, "occupancy_pct": 0, "diagnosis": "weak_booking_pace", "confidence": 0.72},
            {"horizon_days": 30, "occupancy_rate": 0.2, "occupancy_pct": 20, "diagnosis": "weak_booking_pace", "confidence": 0.72},
            {"horizon_days": 60, "occupancy_rate": 0.3, "occupancy_pct": 30, "diagnosis": "weak_booking_pace", "confidence": 0.72},
        ]
        self.assertEqual(_severity_from_horizons(horizons), "critical")
        analysis = _build_deterministic_booking_analysis(
            listing_name="PT300-2M",
            horizons=horizons,
            pickup_context={"booked_last_7_days": 0, "booked_last_14_days": 0},
            pricelabs_context={"status": "not_configured", "calendar_proxy_summary": "avg price $100"},
            airbnb_context={"status": "missing_url"},
        )
        self.assertEqual(analysis["severity"], "critical")
        self.assertIn("0% / 20% / 30%", analysis["booking_pattern"])
        self.assertTrue(any("PriceLabs" in action for action in analysis["action_items"]))
        self.assertTrue(any("Airbnb URL" in action for action in analysis["action_items"]))

    def test_booking_health_card_includes_portfolio_name(self):
        service = BrainService.__new__(BrainService)
        service._portfolio_name_map = lambda: {8: "Urban Stays"}
        service._sanitize_listing_text = lambda value, *_args: value

        card = service._booking_analysis_dict(
            SimpleNamespace(
                booking_health_analysis_id=1,
                portfolio_id=8,
                listing_id=None,
                listing_name="PT300-10H",
                snapshot_date=date(2026, 6, 29),
                severity="high",
                confidence=0.9,
                horizons=[],
                booking_pattern="Soft demand.",
                pricelabs_opinion="PriceLabs ok.",
                airbnb_page_opinion="Airbnb live.",
                opinion="Needs action.",
                action_items=["Cut rates."],
                source_statuses={"hostaway": "ok"},
                raw_payload={},
            )
        )

        self.assertEqual(card["portfolio_name"], "Urban Stays")
        self.assertEqual(card["listing_name"], "PT300-10H")
        self.assertIn("priority_score", card)
        self.assertGreater(card["priority_score"], 0)

    def test_portfolio_status_reflects_active_signal_and_booking_risk(self):
        self.assertEqual(
            portfolio_operating_status(
                active_signals=[SimpleNamespace(severity="critical")],
                open_loop_count=0,
                booking_health=None,
            ),
            "critical",
        )
        self.assertEqual(
            portfolio_operating_status(
                active_signals=[],
                open_loop_count=0,
                booking_health=SimpleNamespace(severity="high", diagnosis="weak_booking_pace"),
            ),
            "high",
        )
        self.assertEqual(
            portfolio_operating_status(
                active_signals=[],
                open_loop_count=1,
                booking_health=None,
            ),
            "watch",
        )
        self.assertEqual(portfolio_operating_status(active_signals=[], open_loop_count=0, booking_health=None), "healthy")

    def test_booking_health_helpers_make_operator_labels(self):
        self.assertEqual(booking_health_status(SimpleNamespace(diagnosis="weak_booking_pace")), "high")
        self.assertEqual(booking_health_status(SimpleNamespace(diagnosis="watch_booking_pace")), "watch")
        self.assertEqual(booking_health_label(SimpleNamespace(diagnosis="last_minute_gap_risk")), "last minute gap risk")

    def test_booking_health_signal_uses_property_analysis(self):
        signal = booking_health_signal_fields(
            SimpleNamespace(
                listing_name="PT300-2M",
                severity="high",
                confidence=0.72,
                horizons=[
                    {"horizon_days": 7, "occupancy_pct": 0, "diagnosis": "weak_booking_pace"},
                    {"horizon_days": 30, "occupancy_pct": 20, "diagnosis": "weak_booking_pace"},
                    {"horizon_days": 60, "occupancy_pct": 30, "diagnosis": "weak_booking_pace"},
                ],
                booking_pattern="Soft demand across all windows: 0% / 20% / 30% occupancy for 7/30/60 days.",
                pricelabs_opinion="PriceLabs is unavailable, so pricing/rule confidence is lower.",
                airbnb_page_opinion="Airbnb page analysis is missing because no Airbnb URL is stored.",
                opinion="PT300-2M needs immediate booking-health review.",
                action_items=[
                    "Lower same-week price and loosen minimum stays.",
                    "Refresh the Airbnb title and first five photos.",
                ],
            )
        )

        self.assertEqual(signal["title"], "Booking health needs action for PT300-2M")
        self.assertEqual(signal["severity"], "high")
        self.assertIn("0% / 20% / 30%", signal["summary"])
        self.assertIn("Lower same-week price", signal["suggested_action"])
        self.assertIn("Airbnb title", signal["suggested_action"])

    def test_booking_health_rank_score_uses_gap_and_pickup_without_saturating(self):
        severe = booking_health_rank_score(
            SimpleNamespace(
                severity="high",
                confidence=0.86,
                horizons=[
                    {"horizon_days": 7, "occupancy_rate": 0.0, "expected_occupancy_rate": 0.45, "diagnosis": "weak_booking_pace"},
                    {"horizon_days": 30, "occupancy_rate": 0.12, "expected_occupancy_rate": 0.55, "diagnosis": "weak_booking_pace"},
                    {"horizon_days": 60, "occupancy_rate": 0.20, "expected_occupancy_rate": 0.50, "diagnosis": "weak_booking_pace"},
                ],
                raw_payload={"analysis_context": {"booking_pattern_inputs": {"booked_last_7_days": 0, "booked_last_14_days": 0}}},
            )
        )
        moderate = booking_health_rank_score(
            SimpleNamespace(
                severity="high",
                confidence=0.86,
                horizons=[
                    {"horizon_days": 7, "occupancy_rate": 0.25, "expected_occupancy_rate": 0.45, "diagnosis": "weak_booking_pace"},
                    {"horizon_days": 30, "occupancy_rate": 0.42, "expected_occupancy_rate": 0.55, "diagnosis": "weak_booking_pace"},
                    {"horizon_days": 60, "occupancy_rate": 0.46, "expected_occupancy_rate": 0.50, "diagnosis": "watch_booking_pace"},
                ],
                raw_payload={"analysis_context": {"booking_pattern_inputs": {"booked_last_7_days": 1, "booked_last_14_days": 3}}},
            )
        )

        self.assertGreater(severe, moderate)
        self.assertLess(severe, 100)

    def test_booking_health_signal_skips_healthy_analysis(self):
        signal = booking_health_signal_fields(
            SimpleNamespace(
                listing_name="PT300-2M",
                severity="healthy",
                confidence=0.5,
                horizons=[
                    {"horizon_days": 7, "occupancy_pct": 80, "diagnosis": "healthy"},
                    {"horizon_days": 30, "occupancy_pct": 75, "diagnosis": "healthy"},
                    {"horizon_days": 60, "occupancy_pct": 70, "diagnosis": "healthy"},
                ],
                action_items=[],
            )
        )

        self.assertIsNone(signal)

    def test_booking_health_signal_skips_watch_analysis(self):
        signal = booking_health_signal_fields(
            SimpleNamespace(
                listing_name="The Oak Middlefork",
                severity="watch",
                confidence=0.77,
                horizons=[
                    {"horizon_days": 7, "occupancy_pct": 86, "diagnosis": "healthy"},
                    {"horizon_days": 30, "occupancy_pct": 47, "diagnosis": "healthy"},
                    {"horizon_days": 60, "occupancy_pct": 30, "diagnosis": "watch_booking_pace"},
                ],
                action_items=["Target 30-60 day midweek gaps with modest discounts."],
            )
        )

        self.assertIsNone(signal)

    def test_booking_health_sort_puts_high_before_watch(self):
        rows = [
            SimpleNamespace(severity="watch", confidence=0.99, listing_name="Watch Listing"),
            SimpleNamespace(severity="high", confidence=0.50, listing_name="High Listing"),
            SimpleNamespace(severity="healthy", confidence=1.00, listing_name="Healthy Listing"),
        ]

        ordered = sorted(rows, key=_booking_analysis_sort_key)

        self.assertEqual([row.listing_name for row in ordered], ["High Listing", "Watch Listing", "Healthy Listing"])

    def test_booking_health_sort_uses_priority_within_same_severity(self):
        rows = [
            SimpleNamespace(
                severity="high",
                confidence=0.9,
                listing_name="Moderate High",
                horizons=[
                    {"horizon_days": 7, "occupancy_rate": 0.25, "expected_occupancy_rate": 0.45, "diagnosis": "weak_booking_pace"},
                    {"horizon_days": 30, "occupancy_rate": 0.42, "expected_occupancy_rate": 0.55, "diagnosis": "weak_booking_pace"},
                    {"horizon_days": 60, "occupancy_rate": 0.46, "expected_occupancy_rate": 0.50, "diagnosis": "watch_booking_pace"},
                ],
                raw_payload={"analysis_context": {"booking_pattern_inputs": {"booked_last_7_days": 1, "booked_last_14_days": 3}}},
            ),
            SimpleNamespace(
                severity="high",
                confidence=0.7,
                listing_name="Severe High",
                horizons=[
                    {"horizon_days": 7, "occupancy_rate": 0.0, "expected_occupancy_rate": 0.45, "diagnosis": "weak_booking_pace"},
                    {"horizon_days": 30, "occupancy_rate": 0.0, "expected_occupancy_rate": 0.55, "diagnosis": "weak_booking_pace"},
                    {"horizon_days": 60, "occupancy_rate": 0.0, "expected_occupancy_rate": 0.50, "diagnosis": "weak_booking_pace"},
                ],
                raw_payload={"analysis_context": {"booking_pattern_inputs": {"booked_last_7_days": 0, "booked_last_14_days": 0}}},
            ),
        ]

        ordered = sorted(rows, key=_booking_analysis_sort_key)

        self.assertEqual([row.listing_name for row in ordered], ["Severe High", "Moderate High"])


if __name__ == "__main__":
    unittest.main()
