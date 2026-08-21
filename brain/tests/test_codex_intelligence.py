import os
import unittest
from datetime import date, datetime
from types import SimpleNamespace

os.environ.setdefault("DATABASE_URL", "postgresql://user:pass@localhost/db")
os.environ.setdefault("OPENAI_API_KEY", "test")
os.environ.setdefault("SECRET_KEY", "test-secret")

from brain.intelligence import (
    CodexIntelligenceService,
    _bounded_float,
    _coerce_int_list,
    _is_high_guest_risk,
    _is_inventory_mode_comparable,
    _is_low_review_rating,
    _listing_inventory_class,
    _listing_inventory_mode,
    _median,
    _metric_selection_rank,
    _source_quality_gaps,
)


class CodexIntelligenceTests(unittest.TestCase):
    @staticmethod
    def _metric(metric_name, listing_id, value, *, payload=None, text=None, status="ok", metric_id=1):
        return SimpleNamespace(
            business_metric_snapshot_id=metric_id,
            metric_name=metric_name,
            metric_date=date(2026, 7, 20),
            numeric_value=value,
            text_value=text,
            status=status,
            horizon_days=30,
            confidence=0.9,
            source_keys=["test"],
            metric_payload=payload or {},
            computed_at=datetime(2026, 7, 20, 6, 0, 0),
            updated_at=datetime(2026, 7, 20, 6, 0, 0),
        )

    def test_guest_risk_threshold_accepts_fractional_and_percent_scales(self):
        self.assertTrue(_is_high_guest_risk(0.72))
        self.assertTrue(_is_high_guest_risk(74))
        self.assertFalse(_is_high_guest_risk(0.4))
        self.assertFalse(_is_high_guest_risk(54))

    def test_review_threshold_accepts_five_and_ten_point_scales(self):
        self.assertTrue(_is_low_review_rating(4.2))
        self.assertTrue(_is_low_review_rating(7.33))
        self.assertFalse(_is_low_review_rating(4.8))
        self.assertFalse(_is_low_review_rating(8.67))

    def test_payload_helpers_keep_imported_insights_predictable(self):
        self.assertEqual(_median([3, 1, 2, 10]), 2.5)
        self.assertEqual(_coerce_int_list(["1", None, "x", 2]), [1, 2])
        self.assertEqual(_bounded_float(1.5, default=0.7), 1.0)
        self.assertEqual(_bounded_float("bad", default=0.7), 0.7)

    def test_listing_inventory_mode_detects_multi_unit_bundles(self):
        self.assertEqual(_listing_inventory_mode("The Cove Club Combined", None), "aggregate_bundle")
        self.assertEqual(
            _listing_inventory_mode("Dew, Sol, Mist, Moss", "Hilltop Mirror Cabins | Book 2-4"),
            "aggregate_bundle",
        )
        self.assertEqual(_listing_inventory_mode("The Retreat", "2 Cabins with Hot Tubs"), "aggregate_bundle")
        self.assertEqual(_listing_inventory_mode("Emerald Haven", "Lakeside Home"), "individual")
        self.assertEqual(_listing_inventory_class("PT300-10i", "2 BR | Skyline Views"), "2_bedroom")
        self.assertEqual(_listing_inventory_class("PT300-2M", "Updated Studio"), "studio")
        self.assertEqual(_listing_inventory_class("Emerald Haven", "Lakeside Home"), "unspecified")
        self.assertFalse(
            _is_inventory_mode_comparable(
                {"inventory_mode": "aggregate_bundle"},
                {"inventory_mode": "individual"},
            )
        )

    def test_booking_occupancy_prefers_thirty_day_horizon_for_price_matching(self):
        common = {
            "metric_name": "booking_occupancy_rate",
            "metric_date": date(2026, 7, 7),
            "updated_at": datetime(2026, 7, 7, 12, 0, 0),
        }
        sixty_day = SimpleNamespace(**common, horizon_days=60, business_metric_snapshot_id=20)
        thirty_day = SimpleNamespace(**common, horizon_days=30, business_metric_snapshot_id=10)

        self.assertGreater(_metric_selection_rank(thirty_day), _metric_selection_rank(sixty_day))

    def test_pricing_rows_surface_pricelabs_toggle_off_gap(self):
        service = object.__new__(CodexIntelligenceService)
        listing_id = 486217
        metrics = {
            ("booking_occupancy_rate", listing_id): SimpleNamespace(
                business_metric_snapshot_id=1,
                metric_date=date(2026, 7, 7),
                numeric_value=0.0,
                text_value="weak_booking_pace",
                status="attention",
                horizon_days=30,
                metric_payload={},
            )
        }
        pricing_context = SimpleNamespace(
            business_fact_id=7,
            text_value="partial",
            fact_payload={
                "status": "partial",
                "error_message": "Listing sync is not toggled ON in PriceLabs",
                "source_statuses": {"prices": "LISTING_TOGGLE_OFF", "metrics": "ok"},
                "payload": {
                    "prices": {
                        "error": "Listing sync is not toggled ON in PriceLabs",
                        "error_status": "LISTING_TOGGLE_OFF",
                    }
                },
            },
        )

        rows = service._pricing_booking_rows(
            metrics,
            {listing_id: "Dew, Sol, Mist, Moss"},
            pricing_contexts={listing_id: pricing_context},
            listing_ids=[listing_id],
        )

        self.assertEqual(len(rows), 1)
        self.assertIn("pricelabs_daily_price_unavailable", rows[0]["flags"])
        self.assertEqual(rows[0]["source_gaps"][0]["error_status"], "LISTING_TOGGLE_OFF")
        self.assertEqual(rows[0]["source_gaps"][0]["source_fact_id"], 7)

    def test_source_quality_gaps_surface_stale_fact_evidence(self):
        gaps = _source_quality_gaps(
            {
                "whatsapp_message": {
                    "latest_occurred_at": "2026-06-28T04:19:21",
                }
            },
            now=datetime(2026, 7, 7, 21, 0, 0),
        )

        whatsapp_gap = next(gap for gap in gaps if gap["fact_type"] == "whatsapp_message")
        self.assertEqual(whatsapp_gap["status"], "stale")
        self.assertEqual(whatsapp_gap["source_key"], "whatsapp")

    def test_pricing_rows_use_same_portfolio_available_rate_peers(self):
        service = object.__new__(CodexIntelligenceService)
        metrics = {}
        for listing_id, occupancy, available_price in (
            (1, 0.2, 150.0),
            (2, 0.5, 100.0),
            (3, 0.6, 110.0),
            (4, 0.5, 20.0),
        ):
            metrics[("booking_occupancy_rate", listing_id)] = self._metric(
                "booking_occupancy_rate",
                listing_id,
                occupancy,
                payload={
                    "diagnosis": "weak_booking_pace" if listing_id == 1 else "healthy",
                    "sellable_nights": 30,
                },
                metric_id=listing_id,
            )
            metrics[("pricelabs_avg_price_30d", listing_id)] = self._metric(
                "pricelabs_avg_price_30d",
                listing_id,
                available_price,
                payload={"days": 30},
                metric_id=10 + listing_id,
            )
            metrics[("pricelabs_avg_available_price_30d", listing_id)] = self._metric(
                "pricelabs_avg_available_price_30d",
                listing_id,
                available_price,
                payload={"available_days": 10},
                metric_id=20 + listing_id,
            )
        contexts = {
            1: {"portfolio_id": 10, "portfolio_name": "Comparable A"},
            2: {"portfolio_id": 10, "portfolio_name": "Comparable A"},
            3: {"portfolio_id": 10, "portfolio_name": "Comparable A"},
            4: {"portfolio_id": 20, "portfolio_name": "Different Portfolio"},
        }

        rows = service._pricing_booking_rows(
            metrics,
            {listing_id: f"Listing {listing_id}" for listing_id in contexts},
            listing_ids=list(contexts),
            listing_contexts=contexts,
            as_of_date=date(2026, 7, 20),
        )

        target = next(row for row in rows if row["listing_id"] == 1)
        self.assertEqual(target["decision_readiness"]["status"], "ready")
        self.assertEqual(target["comparable_rate"]["peer_count"], 2)
        self.assertEqual(target["comparable_rate"]["peer_median_available_price"], 105.0)
        self.assertIn("soft_booking_pace_with_high_portfolio_available_rate", target["flags"])

    def test_pricing_rows_caution_when_event_prices_skew_available_rate_average(self):
        service = object.__new__(CodexIntelligenceService)
        metrics = {}
        for listing_id, price in ((1, 330.0), (2, 180.0), (3, 190.0)):
            metrics[("booking_occupancy_rate", listing_id)] = self._metric(
                "booking_occupancy_rate",
                listing_id,
                0.2 if listing_id == 1 else 0.5,
                payload={"diagnosis": "weak_booking_pace" if listing_id == 1 else "healthy", "sellable_nights": 30},
                metric_id=listing_id,
            )
            metrics[("pricelabs_avg_price_30d", listing_id)] = self._metric(
                "pricelabs_avg_price_30d",
                listing_id,
                price,
                metric_id=10 + listing_id,
            )
            payload = {"available_days": 20}
            if listing_id == 1:
                payload.update(
                    {
                        "median_available_price": 125.0,
                        "mean_to_median_ratio": 2.64,
                        "event_or_override_available_days": 6,
                    }
                )
            metrics[("pricelabs_avg_available_price_30d", listing_id)] = self._metric(
                "pricelabs_avg_available_price_30d",
                listing_id,
                price,
                payload=payload,
                metric_id=20 + listing_id,
            )
        contexts = {
            listing_id: {"portfolio_id": 10, "portfolio_name": "Urban", "inventory_class": "studio"}
            for listing_id in (1, 2, 3)
        }

        row = service._pricing_booking_rows(
            metrics,
            {listing_id: f"Listing {listing_id}" for listing_id in contexts},
            listing_ids=list(contexts),
            listing_contexts=contexts,
            as_of_date=date(2026, 7, 20),
        )[0]

        self.assertEqual(row["decision_readiness"]["status"], "caution")
        self.assertIn("available_rate_distribution_skewed_by_event_or_override_dates", row["flags"])
        self.assertNotIn("soft_booking_pace_with_high_portfolio_available_rate", row["flags"])
        self.assertEqual(row["pricing_basis"]["available_date_median"], 125.0)
        self.assertIn("ordinary open dates separately", row["decision_readiness"]["allowed_conclusion"])

    def test_pricing_rows_surface_unverified_reservation_value_coverage(self):
        service = object.__new__(CodexIntelligenceService)
        listing_id = 1
        metrics = {
            ("booking_occupancy_rate", listing_id): self._metric(
                "booking_occupancy_rate",
                listing_id,
                0.3,
                payload={"diagnosis": "watch_booking_pace", "sellable_nights": 30},
            ),
            ("booked_nights_next_30d", listing_id): self._metric(
                "booked_nights_next_30d",
                listing_id,
                9.0,
                metric_id=2,
            ),
            ("reservation_revenue_next_30d", listing_id): self._metric(
                "reservation_revenue_next_30d",
                listing_id,
                None,
                status="not_available",
                payload={
                    "reservation_value_coverage_ratio": 0.0,
                    "unverified_value_reservation_count": 8,
                },
                metric_id=3,
            ),
            ("pricelabs_avg_price_30d", listing_id): self._metric(
                "pricelabs_avg_price_30d",
                listing_id,
                475.0,
                metric_id=4,
            ),
            ("pricelabs_avg_available_price_30d", listing_id): self._metric(
                "pricelabs_avg_available_price_30d",
                listing_id,
                375.0,
                payload={"available_days": 20, "median_available_price": 350.0, "mean_to_median_ratio": 1.0714},
                metric_id=5,
            ),
        }

        row = service._pricing_booking_rows(
            metrics,
            {listing_id: "Imported iCal Listing"},
            listing_ids=[listing_id],
            listing_contexts={listing_id: {"portfolio_id": 10, "portfolio_name": "Portfolio"}},
            as_of_date=date(2026, 7, 20),
        )[0]

        self.assertIn("reservation_value_coverage_incomplete", row["flags"])
        self.assertEqual(row["source_gaps"][0]["status"], "incomplete_value_coverage")

    def test_pricing_rows_exclude_bundle_listings_from_individual_peers(self):
        service = object.__new__(CodexIntelligenceService)
        metrics = {}
        for listing_id, available_price in ((1, 900.0), (2, 300.0), (3, 320.0)):
            metrics[("booking_occupancy_rate", listing_id)] = self._metric(
                "booking_occupancy_rate",
                listing_id,
                0.2,
                payload={"diagnosis": "weak_booking_pace"},
                metric_id=listing_id,
            )
            metrics[("pricelabs_avg_price_30d", listing_id)] = self._metric(
                "pricelabs_avg_price_30d",
                listing_id,
                available_price,
                metric_id=10 + listing_id,
            )
            metrics[("pricelabs_avg_available_price_30d", listing_id)] = self._metric(
                "pricelabs_avg_available_price_30d",
                listing_id,
                available_price,
                payload={"available_days": 10},
                metric_id=20 + listing_id,
            )
            metrics[("booked_nights_next_30d", listing_id)] = self._metric(
                "booked_nights_next_30d",
                listing_id,
                6.0,
                metric_id=30 + listing_id,
            )
        contexts = {
            1: {"portfolio_id": 10, "portfolio_name": "Estate", "inventory_mode": "aggregate_bundle"},
            2: {"portfolio_id": 10, "portfolio_name": "Estate", "inventory_mode": "individual"},
            3: {"portfolio_id": 10, "portfolio_name": "Estate", "inventory_mode": "individual"},
        }

        rows = service._pricing_booking_rows(
            metrics,
            {listing_id: f"Listing {listing_id}" for listing_id in contexts},
            listing_ids=list(contexts),
            listing_contexts=contexts,
            as_of_date=date(2026, 7, 20),
        )

        bundle = next(row for row in rows if row["listing_id"] == 1)
        self.assertEqual(bundle["decision_readiness"]["status"], "caution")
        self.assertEqual(bundle["comparable_rate"]["peer_count"], 0)
        self.assertEqual(bundle["comparable_rate"]["excluded_inventory_profile_peer_count"], 2)
        self.assertNotIn("soft_booking_pace_with_high_portfolio_available_rate", bundle["flags"])

    def test_pricing_rows_block_bundle_calendar_occupancy_without_direct_reservations(self):
        service = object.__new__(CodexIntelligenceService)
        listing_id = 1
        metrics = {
            ("booking_occupancy_rate", listing_id): self._metric(
                "booking_occupancy_rate",
                listing_id,
                0.8,
                payload={"diagnosis": "healthy", "booked_nights": 24},
            ),
            ("booked_nights_next_30d", listing_id): self._metric(
                "booked_nights_next_30d",
                listing_id,
                0.0,
                metric_id=2,
            ),
            ("pricelabs_avg_price_30d", listing_id): self._metric(
                "pricelabs_avg_price_30d",
                listing_id,
                900.0,
                metric_id=3,
            ),
            ("pricelabs_avg_available_price_30d", listing_id): self._metric(
                "pricelabs_avg_available_price_30d",
                listing_id,
                800.0,
                payload={"available_days": 6},
                metric_id=4,
            ),
        }

        row = service._pricing_booking_rows(
            metrics,
            {listing_id: "Two Cabin Bundle"},
            listing_ids=[listing_id],
            listing_contexts={
                listing_id: {
                    "portfolio_id": 10,
                    "portfolio_name": "Portfolio",
                    "inventory_mode": "aggregate_bundle",
                    "inventory_class": "aggregate_bundle",
                }
            },
            as_of_date=date(2026, 7, 20),
        )[0]

        self.assertEqual(row["decision_readiness"]["status"], "blocked")
        self.assertIn("bundle_calendar_occupancy_without_direct_reservations", row["flags"])
        self.assertIn("component-unit holds", row["decision_readiness"]["allowed_conclusion"])

    def test_pricing_rows_block_bundle_when_reservations_explain_only_small_calendar_share(self):
        service = object.__new__(CodexIntelligenceService)
        listing_id = 1
        metrics = {
            ("booking_occupancy_rate", listing_id): self._metric(
                "booking_occupancy_rate",
                listing_id,
                0.9667,
                payload={"diagnosis": "healthy", "booked_nights": 29, "sellable_nights": 30},
            ),
            ("booked_nights_next_30d", listing_id): self._metric(
                "booked_nights_next_30d",
                listing_id,
                4.0,
                metric_id=2,
            ),
            ("pricelabs_avg_price_30d", listing_id): self._metric(
                "pricelabs_avg_price_30d",
                listing_id,
                1500.0,
                metric_id=3,
            ),
            ("pricelabs_avg_available_price_30d", listing_id): self._metric(
                "pricelabs_avg_available_price_30d",
                listing_id,
                1200.0,
                payload={"available_days": 3},
                metric_id=4,
            ),
        }

        row = service._pricing_booking_rows(
            metrics,
            {listing_id: "Combined Estate"},
            listing_ids=[listing_id],
            listing_contexts={
                listing_id: {
                    "portfolio_id": 10,
                    "portfolio_name": "Portfolio",
                    "inventory_mode": "aggregate_bundle",
                    "inventory_class": "aggregate_bundle",
                }
            },
            as_of_date=date(2026, 7, 20),
        )[0]

        self.assertEqual(row["decision_readiness"]["status"], "blocked")
        self.assertIn("bundle_calendar_occupancy_not_reconciled_to_reservations", row["flags"])
        self.assertNotIn("high_booking_pace_with_limited_open_inventory", row["flags"])
        self.assertEqual(row["reservation_calendar_reconciliation"]["reservation_to_calendar_booked_ratio"], 0.1379)
        self.assertIn("component-unit holds", row["decision_readiness"]["allowed_conclusion"])

    def test_pricing_rows_block_rate_action_when_inventory_is_blocked(self):
        service = object.__new__(CodexIntelligenceService)
        listing_id = 1
        metrics = {
            ("booking_occupancy_rate", listing_id): self._metric(
                "booking_occupancy_rate",
                listing_id,
                None,
                text="inventory_blocked",
                payload={"diagnosis": "inventory_blocked", "blocked_nights": 30, "sellable_nights": 0},
            ),
            ("pricelabs_avg_price_30d", listing_id): self._metric(
                "pricelabs_avg_price_30d",
                listing_id,
                500.0,
                payload={"days": 30},
                metric_id=2,
            ),
            ("pricelabs_avg_available_price_30d", listing_id): self._metric(
                "pricelabs_avg_available_price_30d",
                listing_id,
                None,
                text="no_available_dates_in_window",
                status="not_applicable",
                payload={"available_days": 0},
                metric_id=3,
            ),
        }

        row = service._pricing_booking_rows(
            metrics,
            {listing_id: "Blocked Listing"},
            listing_ids=[listing_id],
            listing_contexts={listing_id: {"portfolio_id": 10, "portfolio_name": "Portfolio"}},
            as_of_date=date(2026, 7, 20),
        )[0]

        self.assertEqual(row["decision_readiness"]["status"], "blocked")
        self.assertIn("inventory_blocked_demand_not_measurable", row["flags"])
        self.assertIn("no_available_dates_for_pricing_action", row["flags"])
        self.assertNotIn("soft_booking_pace_with_high_portfolio_available_rate", row["flags"])

    def test_pricing_rows_do_not_treat_tiny_sellable_denominator_as_high_pace(self):
        service = object.__new__(CodexIntelligenceService)
        listing_id = 1
        metrics = {
            ("booking_occupancy_rate", listing_id): self._metric(
                "booking_occupancy_rate",
                listing_id,
                1.0,
                payload={
                    "diagnosis": "healthy",
                    "booked_nights": 2,
                    "blocked_nights": 28,
                    "sellable_nights": 2,
                },
            ),
            ("booked_nights_next_30d", listing_id): self._metric(
                "booked_nights_next_30d",
                listing_id,
                2.0,
                metric_id=2,
            ),
            ("pricelabs_avg_price_30d", listing_id): self._metric(
                "pricelabs_avg_price_30d",
                listing_id,
                500.0,
                metric_id=3,
            ),
            ("pricelabs_avg_available_price_30d", listing_id): self._metric(
                "pricelabs_avg_available_price_30d",
                listing_id,
                None,
                payload={"available_days": 0},
                metric_id=4,
            ),
        }

        row = service._pricing_booking_rows(
            metrics,
            {listing_id: "Mostly Blocked Listing"},
            listing_ids=[listing_id],
            listing_contexts={listing_id: {"portfolio_id": 10, "portfolio_name": "Portfolio"}},
            as_of_date=date(2026, 7, 20),
        )[0]

        self.assertNotIn("high_booking_pace_with_limited_open_inventory", row["flags"])
        self.assertIn("no_available_dates_for_pricing_action", row["flags"])


if __name__ == "__main__":
    unittest.main()
