import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace

from brain.aggregator import (
    build_fact_key,
    build_metric_key,
    data_source_health_status,
    summarize_data_aggregator_health,
    summarize_foundation_audit,
    _booking_health_severity_from_diagnoses,
    _booking_metric_status,
    _latest_fresh_booking_health_analysis_facts,
    _latest_booking_health_horizon_facts,
    _latest_guest_stay_memory_facts,
    _latest_pricelabs_snapshot_rows,
    _overlap_nights,
    _pricelabs_fact_is_unavailable,
    _pricelabs_has_date_override,
    _pricelabs_holiday_events,
    _pricelabs_daily_price_value,
    _pricelabs_daily_booking_metric_rows,
    _reservation_value_coverage,
    _severity_metric_status,
)


class DataAggregatorTests(unittest.TestCase):
    def test_fact_key_is_stable_and_readable(self):
        self.assertEqual(
            build_fact_key("PriceLabs Daily Price", "PriceLabs", "123:2026-07-07"),
            "pricelabs_daily_price:pricelabs:123:2026-07-07",
        )

    def test_metric_key_includes_grain_entity_date_and_horizon(self):
        self.assertEqual(
            build_metric_key(
                metric_name="PriceLabs Avg Price 30d",
                grain="listing",
                metric_date="2026-07-07",
                portfolio_id=3,
                listing_id=558675,
                horizon_days=30,
            ),
            "metric:pricelabs_avg_price_30d:listing:2026-07-07:portfolio=3:listing=558675:horizon=30",
        )

    def test_source_health_marks_stale_after_threshold(self):
        now = datetime(2026, 7, 7, 12, 0, 0)
        source = SimpleNamespace(
            source_key="pricelabs",
            name="PriceLabs",
            category="revenue",
            connector_type="pricelabs_api",
            status="ok",
            last_success_at=now - timedelta(hours=49),
            last_ingested_at=now - timedelta(hours=49),
            last_error_at=None,
            last_error_message=None,
            freshness_threshold_minutes=36 * 60,
        )

        health = data_source_health_status(source, now=now)

        self.assertEqual(health["status"], "stale")
        self.assertGreater(health["age_minutes"], health["freshness_threshold_minutes"])

    def test_aggregator_health_requires_facts_and_current_sources(self):
        health = summarize_data_aggregator_health(
            [
                {"source_key": "hostaway_reservations", "status": "ok"},
                {"source_key": "pricelabs", "status": "ok"},
            ],
            fact_count=42,
            latest_completed_at=datetime(2026, 7, 7, 11, 0, 0),
        )

        self.assertEqual(health["status"], "ok")
        self.assertEqual(health["fact_count"], 42)
        self.assertEqual(health["ok_source_count"], 2)

    def test_aggregator_health_degrades_when_source_is_missing(self):
        health = summarize_data_aggregator_health(
            [
                {"source_key": "hostaway_reservations", "status": "ok"},
                {"source_key": "bookkeeping_revenue", "status": "missing"},
            ],
            fact_count=12,
        )

        self.assertEqual(health["status"], "degraded")
        self.assertEqual(health["missing_source_count"], 1)

    def test_foundation_audit_is_ok_when_required_coverage_exists(self):
        summary = summarize_foundation_audit([], source_count=10, fact_count=100, metric_count=12)

        self.assertEqual(summary["status"], "ok")
        self.assertEqual(summary["score"], 100)
        self.assertEqual(summary["gap_count"], 0)

    def test_foundation_audit_marks_critical_gaps_missing(self):
        summary = summarize_foundation_audit(
            [{"severity": "critical", "key": "source:pricelabs", "message": "PriceLabs is missing."}],
            source_count=9,
            fact_count=100,
            metric_count=12,
        )

        self.assertEqual(summary["status"], "missing")
        self.assertLess(summary["score"], 100)
        self.assertEqual(summary["severity_counts"]["critical"], 1)

    def test_foundation_audit_marks_warning_gaps_watch(self):
        summary = summarize_foundation_audit(
            [{"severity": "warning", "key": "metric:review_avg_rating_90d", "message": "Metric is missing."}],
            source_count=10,
            fact_count=100,
            metric_count=11,
        )

        self.assertEqual(summary["status"], "watch")
        self.assertEqual(summary["severity_counts"]["warning"], 1)

    def test_metric_status_helpers_normalize_decision_attention(self):
        self.assertEqual(_booking_metric_status("healthy"), "ok")
        self.assertEqual(_booking_metric_status("watch_booking_pace"), "watch")
        self.assertEqual(_booking_metric_status("weak_booking_pace"), "attention")
        self.assertEqual(_severity_metric_status("high"), "attention")
        self.assertEqual(_severity_metric_status("watch"), "watch")

    def test_booking_health_severity_uses_worst_horizon_diagnosis(self):
        self.assertEqual(_booking_health_severity_from_diagnoses(["healthy", "watch_booking_pace"]), "watch")
        self.assertEqual(_booking_health_severity_from_diagnoses(["watch_booking_pace", "weak_booking_pace"]), "high")
        self.assertEqual(_booking_health_severity_from_diagnoses(["healthy"]), "healthy")

    def test_booking_health_analysis_is_ignored_when_horizon_is_newer(self):
        stale = SimpleNamespace(
            listing_id=10,
            effective_start=datetime(2026, 7, 20),
            updated_at=datetime(2026, 7, 20),
            created_at=datetime(2026, 7, 20),
            business_fact_id=1,
        )
        horizon = SimpleNamespace(effective_start=datetime(2026, 8, 17))

        rows = _latest_fresh_booking_health_analysis_facts([stale], {10: [horizon]})

        self.assertEqual(rows, [])

    def test_booking_health_analysis_keeps_latest_current_row_per_listing(self):
        older = SimpleNamespace(
            listing_id=10,
            effective_start=datetime(2026, 8, 17),
            updated_at=datetime(2026, 8, 17, 1),
            created_at=datetime(2026, 8, 17, 1),
            business_fact_id=1,
        )
        latest = SimpleNamespace(
            listing_id=10,
            effective_start=datetime(2026, 8, 17),
            updated_at=datetime(2026, 8, 17, 2),
            created_at=datetime(2026, 8, 17, 2),
            business_fact_id=2,
        )
        horizon = SimpleNamespace(effective_start=datetime(2026, 8, 17))

        rows = _latest_fresh_booking_health_analysis_facts([older, latest], {10: [horizon]})

        self.assertEqual(rows, [latest])

    def test_overlap_nights_clips_to_metric_window(self):
        self.assertEqual(
            _overlap_nights(
                datetime(2026, 7, 1),
                datetime(2026, 7, 10),
                datetime(2026, 7, 5).date(),
                datetime(2026, 7, 8).date(),
            ),
            3,
        )

    def test_pricelabs_daily_booking_metrics_use_price_row_booking_fields(self):
        rows = _pricelabs_daily_booking_metric_rows(
            [
                {
                    "date": "2026-07-08",
                    "occupancy": 42,
                    "ADR": 250,
                    "ADR_STLY": 210,
                    "booking_status": "booked",
                    "booked_date": "2026-06-30",
                },
                {"date": "not-a-date", "ADR": 100},
            ]
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["date"].isoformat(), "2026-07-08")
        self.assertEqual(rows[0]["metric"], "daily_booking_pattern")
        self.assertEqual(rows[0]["value"], 42)
        self.assertEqual(rows[0]["adr"], 250)
        self.assertEqual(rows[0]["booking_status"], "booked")

    def test_pricelabs_daily_price_uses_recommended_price_not_negative_user_sentinel(self):
        price, source = _pricelabs_daily_price_value(
            {
                "price": 738,
                "user_price": -1,
                "uncustomized_price": 599,
                "reason": {"listing_info": {"customized_price": "738", "uncustomized_price": "1190"}},
            }
        )

        self.assertEqual(price, 738)
        self.assertEqual(source, "price")

    def test_pricelabs_daily_price_falls_back_to_positive_user_price(self):
        price, source = _pricelabs_daily_price_value({"price": None, "user_price": 1340, "uncustomized_price": 1048})

        self.assertEqual(price, 1340)
        self.assertEqual(source, "user_price")

    def test_pricelabs_unavailable_day_uses_booking_status_when_flag_is_zero(self):
        self.assertTrue(
            _pricelabs_fact_is_unavailable(
                SimpleNamespace(fact_payload={"unbookable": 0, "booking_status": "Booked (Check-In)"})
            )
        )
        self.assertTrue(
            _pricelabs_fact_is_unavailable(
                SimpleNamespace(fact_payload={"unbookable": 0, "booking_status": "Blocked"})
            )
        )
        self.assertFalse(
            _pricelabs_fact_is_unavailable(
                SimpleNamespace(fact_payload={"unbookable": 0, "booking_status": ""})
            )
        )

    def test_pricelabs_event_and_date_override_metadata_is_structured(self):
        row = SimpleNamespace(
            fact_payload={
                "reason": {
                    "listing_info": {"holiday_events": "Dragon Con, College Football"},
                    "pricing_customizations": {
                        "0": {"key": "date_specific_override", "title": "Date Override (Listing)"}
                    },
                }
            }
        )

        self.assertEqual(_pricelabs_holiday_events(row), ["College Football", "Dragon Con"])
        self.assertTrue(_pricelabs_has_date_override(row))

    def test_reservation_value_coverage_marks_zero_value_ical_rows_unverified(self):
        coverage = _reservation_value_coverage(
            [
                SimpleNamespace(numeric_value=450.0, fact_payload={"channel_name": "airbnbOfficial"}),
                SimpleNamespace(numeric_value=0.0, fact_payload={"channel_name": "customIcal"}),
                SimpleNamespace(numeric_value=None, fact_payload={"channel_name": "customIcal"}),
            ]
        )

        self.assertEqual(coverage["value_covered_reservation_count"], 1)
        self.assertEqual(coverage["unverified_value_reservation_count"], 2)
        self.assertEqual(coverage["reservation_value_coverage_ratio"], 0.3333)
        self.assertEqual(coverage["unverified_value_channel_counts"], {"customIcal": 2})

    def test_latest_pricelabs_snapshot_rows_keeps_only_newest_per_listing(self):
        old = SimpleNamespace(listing_id=146889, created_at=datetime(2026, 6, 28, 4, 0), pricelabs_snapshot_id=99, raw_payload={"data": [{"date": "2026-07-07", "price": 700}]})
        newer = SimpleNamespace(listing_id=146889, created_at=datetime(2026, 7, 7, 19, 0), pricelabs_snapshot_id=150, raw_payload={"prices": {"data": [{"date": "2026-07-07", "price": 738}]}})
        other = SimpleNamespace(listing_id=178403, created_at=datetime(2026, 6, 28, 4, 0), pricelabs_snapshot_id=101, raw_payload={"data": [{"date": "2026-07-07", "price": 734}]})

        rows = _latest_pricelabs_snapshot_rows([old, other, newer])

        self.assertEqual({row.listing_id: row.pricelabs_snapshot_id for row in rows}, {146889: 150, 178403: 101})

    def test_latest_pricelabs_snapshot_rows_prefers_usable_older_snapshot_over_new_error(self):
        usable = SimpleNamespace(listing_id=558676, created_at=datetime(2026, 7, 7, 19, 0), pricelabs_snapshot_id=235, raw_payload={"prices": {"data": [{"date": "2026-07-07", "price": 1258}]}})
        error = SimpleNamespace(listing_id=558676, created_at=datetime(2026, 7, 7, 21, 0), pricelabs_snapshot_id=400, raw_payload={"prices": {"request": {"id": "558676"}}})

        rows = _latest_pricelabs_snapshot_rows([usable, error])

        self.assertEqual(rows[0].pricelabs_snapshot_id, 235)

    def test_latest_booking_health_horizon_facts_keeps_current_horizon_snapshot(self):
        old = SimpleNamespace(
            listing_id=184081,
            fact_payload={"horizon_days": 30},
            effective_start=datetime(2026, 6, 27),
            updated_at=datetime(2026, 6, 28, 4, 0),
            business_fact_id=100,
        )
        current = SimpleNamespace(
            listing_id=184081,
            fact_payload={"horizon_days": 30},
            effective_start=datetime(2026, 7, 7),
            updated_at=datetime(2026, 7, 7, 18, 50),
            business_fact_id=200,
        )

        rows = _latest_booking_health_horizon_facts([old, current])

        self.assertEqual(rows, [current])

    def test_latest_guest_stay_memory_facts_keeps_current_reservation_snapshot(self):
        old = SimpleNamespace(
            listing_id=295678,
            reservation_id=123,
            fact_payload={"memory_date": "2026-07-20"},
            numeric_value=99,
            updated_at=datetime(2026, 7, 20, 6, 0),
            business_fact_id=100,
        )
        current = SimpleNamespace(
            listing_id=295678,
            reservation_id=123,
            fact_payload={"memory_date": "2026-08-03"},
            numeric_value=0,
            updated_at=datetime(2026, 8, 3, 22, 0),
            business_fact_id=200,
        )
        other = SimpleNamespace(
            listing_id=295678,
            reservation_id=456,
            fact_payload={"memory_date": "2026-08-03"},
            numeric_value=60,
            updated_at=datetime(2026, 8, 3, 22, 0),
            business_fact_id=201,
        )

        rows = _latest_guest_stay_memory_facts([old, other, current])

        self.assertEqual(rows, [current, other])


if __name__ == "__main__":
    unittest.main()
