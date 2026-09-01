import os
import unittest
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("DATABASE_URL", "postgresql://user:pass@localhost/db")
os.environ.setdefault("OPENAI_API_KEY", "test")
os.environ.setdefault("SECRET_KEY", "test-secret")

from dashboard.listing_audit.service import (
    confirmed_channel_link_problem,
    dashboard_payload,
    merge_deep_inspections,
    scope_items_by_portfolio,
    snapshot_dict,
)


class ListingAuditDashboardPayloadTests(unittest.TestCase):
    def test_top_channel_problem_filter_preserves_connection_gaps_and_confirmed_errors_only(self):
        self.assertTrue(confirmed_channel_link_problem({"configured": False, "url": None, "page": {"status": "missing_url"}}))
        self.assertFalse(confirmed_channel_link_problem({"configured": True, "url": None, "page": {"status": "missing_url"}}))
        self.assertFalse(confirmed_channel_link_problem({
            "configured": True,
            "url": "https://channel.example/listing/41",
            "page": {"status": "ok"},
            "status": "high",
        }))
        self.assertTrue(confirmed_channel_link_problem({
            "configured": True,
            "url": "https://channel.example/listing/41",
            "page": {"status": "unavailable", "failure_kind": "rendered_error"},
        }))
        self.assertFalse(confirmed_channel_link_problem({
            "configured": True,
            "url": "https://channel.example/listing/41",
            "page": {
                "status": "unavailable",
                "failure_kind": "http_error",
                "http_status": 429,
                "summary": "Bot or Not?",
            },
        }))

    def test_payload_summarizes_channel_coverage_and_actions(self):
        run = SimpleNamespace(
            listing_audit_run_id=12,
            cadence="daily",
            status="completed",
            snapshot_date=date.today(),
            listing_count=1,
            critical_count=0,
            high_count=1,
            watch_count=0,
            healthy_count=0,
            source_statuses={},
            error_message=None,
            started_at=datetime.utcnow(),
            completed_at=datetime.utcnow(),
        )
        item = {
            "listing_id": 41,
            "listing_name": "Skyline Retreat",
            "portfolio_name": "Urban Stays",
            "health_score": 58.0,
            "severity": "high",
            "online_assets": [
                {"channel": "airbnb", "configured": True, "status": "healthy"},
                {"channel": "vrbo", "configured": True, "status": "watch"},
                {"channel": "bookingcom", "configured": False, "status": "not_exported"},
                {
                    "channel": "googlevr",
                    "label": "Google Vacation Rentals",
                    "configured": True,
                    "status": "healthy",
                    "deep_inspection": {
                        "status": "watch",
                        "summary": "5 of 6 fields verified; 1 finding.",
                        "issues": [{"priority": "medium", "message": "Expand the description."}],
                    },
                },
            ],
            "action_items": [
                {"priority": "high", "category": "Booking", "text": "Close the near-term occupancy gap."},
                {"priority": "high", "category": "Airbnb", "text": "Strengthen the Airbnb title and lead photo."},
            ],
        }

        result = dashboard_payload(run, [item], recent_runs=[run])

        self.assertTrue(result["has_data"])
        self.assertEqual(result["summary"]["listing_count"], 1)
        self.assertEqual(result["summary"]["channel_coverage"]["airbnb"]["healthy"], 1)
        booking_coverage = result["summary"]["channel_coverage"]["bookingcom"]
        self.assertEqual(booking_coverage["missing_count"], 1)
        self.assertEqual(booking_coverage["problem_count"], 1)
        self.assertEqual(booking_coverage["missing_units"][0]["listing_name"], "Skyline Retreat")
        self.assertEqual(booking_coverage["missing_units"][0]["connection_status"], "not exported")
        google_coverage = result["summary"]["channel_coverage"]["googlevr"]
        self.assertEqual(google_coverage["label"], "Google Vacation Rentals")
        self.assertEqual(google_coverage["configured"], 1)
        self.assertEqual(google_coverage["healthy"], 1)
        self.assertEqual(google_coverage["deep_reviewed"], 1)
        self.assertEqual(google_coverage["deep_issues"], 1)
        self.assertEqual(google_coverage["problem_count"], 1)
        self.assertEqual(result["summary"]["deep_reviewed_count"], 1)
        self.assertTrue(result["has_deep_review"])
        self.assertEqual(result["top_actions"][0]["listing_name"], "Skyline Retreat")
        self.assertNotIn("occupancy", result["top_actions"][0]["text"])
        self.assertEqual(result["items"][0]["listing_checks"]["content"]["issue_count"], 2)
        self.assertEqual(result["profile_label"], "All properties")
        self.assertFalse(result["is_stale"])

    def test_payload_counts_listing_checks_and_ignores_booking_health_recommendations(self):
        run = SimpleNamespace(
            listing_audit_run_id=22,
            cadence="weekly",
            status="completed",
            snapshot_date=date.today(),
            listing_count=1,
            critical_count=0,
            high_count=1,
            watch_count=0,
            healthy_count=0,
            source_statuses={},
            error_message=None,
            started_at=datetime.utcnow(),
            completed_at=datetime.utcnow(),
        )
        item = {
            "listing_id": 41,
            "listing_name": "Skyline Retreat",
            "portfolio_name": "Urban Stays",
            "health_score": 12.0,
            "severity": "critical",
            "online_assets": [{
                "channel": "airbnb",
                "label": "Airbnb",
                "configured": True,
                "status": "high",
                "score": 90.0,
                "url": "https://airbnb.example/41",
                "page": {"status": "ok", "http_status": 200},
                "deep_inspection": {
                    "status": "high",
                    "fields": {
                        "amenities": {"label": "Amenities", "status": "partial"},
                        "house_rules": {"label": "House Rules", "status": "mismatch"},
                        "title": {"label": "Title", "status": "match"},
                    },
                    "issues": [
                        {"field": "amenities", "priority": "medium", "message": "Review Airbnb amenities."},
                        {"field": "house_rules", "priority": "high", "message": "Reconcile Airbnb house rules."},
                    ],
                },
            }],
            "action_items": [
                {"priority": "critical", "category": "Booking", "text": "Reduce rates because occupancy is soft."},
                {"priority": "high", "category": "Booking", "text": "Open 1-night stays and remove check-in restrictions."},
                {"priority": "high", "category": "Airbnb", "text": "Replace the lead photo with a clearer exterior image."},
            ],
        }

        result = dashboard_payload(run, [item], recent_runs=[run])
        presented = result["items"][0]

        self.assertEqual(presented["health_score"], 90.0)
        self.assertEqual(presented["severity"], "high")
        self.assertEqual(result["summary"]["amenities_issue_count"], 1)
        self.assertEqual(result["summary"]["policies_issue_count"], 1)
        self.assertEqual(result["summary"]["content_issue_count"], 1)
        self.assertEqual(result["summary"]["link_issue_count"], 0)
        self.assertEqual(result["summary"]["confirmed_link_issue_count"], 0)
        top_action_text = [action["text"] for action in result["top_actions"]]
        self.assertIn("Reconcile Airbnb house rules.", top_action_text)
        self.assertIn("Replace the lead photo with a clearer exterior image.", top_action_text)
        self.assertFalse(any("occupancy" in text for text in top_action_text))

    def test_snapshot_read_model_does_not_expose_booking_or_pricing_health(self):
        snapshot = SimpleNamespace(
            listing_audit_snapshot_id=3,
            listing_id=41,
            listing_name="Skyline Retreat",
            portfolio_id=5,
            health_score=46.0,
            severity="high",
            booking_health={"horizons": [{"occupancy_percent": 45}]},
            pricing_health={"average_price": 150},
            market_comparison={"difference_points": -12},
            online_assets=[],
            action_items=[],
            source_statuses={},
            raw_payload={"portfolio_name": "Urban Stays"},
        )

        result = snapshot_dict(snapshot)

        self.assertNotIn("booking_health", result)
        self.assertNotIn("pricing_health", result)
        self.assertNotIn("market_comparison", result)
        self.assertEqual(
            [asset["channel"] for asset in result["online_assets"]],
            ["airbnb", "vrbo", "bookingcom", "googlevr", "direct"],
        )
        google = next(asset for asset in result["online_assets"] if asset["channel"] == "googlevr")
        self.assertEqual(google["label"], "Google Vacation Rentals")
        self.assertTrue(google["compatibility_placeholder"])

    def test_listing_health_template_contains_no_occupancy_or_pricing_panels(self):
        template = (Path(__file__).parents[2] / "templates" / "listing_audit" / "index.html").read_text()
        stylesheet = (Path(__file__).parents[2] / "static" / "css" / "listing-audit.css").read_text()

        for removed in (
            "Forward calendar",
            "PriceLabs",
            "glance-occupancy",
            "item.booking_health",
            "item.pricing_health",
            "item.market_comparison",
        ):
            self.assertNotIn(removed, template)
        self.assertIn("Confirmed link failures", template)
        self.assertIn("Amenity findings", template)
        self.assertIn("Guest policy findings", template)
        self.assertIn("Content findings", template)
        self.assertIn("overflow-wrap: anywhere", stylesheet)
        self.assertIn(".priority-action-card > p", stylesheet)

    def test_payload_excludes_content_only_findings_from_link_problems(self):
        run = SimpleNamespace(
            listing_audit_run_id=15,
            cadence="weekly",
            status="completed",
            snapshot_date=date.today(),
            listing_count=1,
            critical_count=0,
            high_count=1,
            watch_count=0,
            healthy_count=0,
            source_statuses={},
            error_message=None,
            started_at=datetime.utcnow(),
            completed_at=datetime.utcnow(),
        )
        item = {
            "listing_id": 576478,
            "listing_name": "Reflection's Edge",
            "portfolio_name": "Enchanted Havens",
            "health_score": 60.0,
            "severity": "high",
            "online_assets": [{
                "channel": "googlevr",
                "label": "Google Vacation Rentals",
                "configured": True,
                "status": "high",
                "url": "https://www.google.com/travel/hotels/entity/example/overview",
                "page": {"status": "ok"},
                "deep_inspection": {
                    "status": "high",
                    "issues": [{
                        "priority": "high",
                        "code": "deep_content_unverified",
                        "message": "The live link returned without verifiable listing content.",
                    }],
                },
            }],
            "action_items": [],
        }

        coverage = dashboard_payload(run, [item], recent_runs=[run])["summary"]["channel_coverage"]["googlevr"]

        self.assertEqual(coverage["configured"], 1)
        self.assertEqual(coverage["needs_attention"], 1)
        self.assertEqual(coverage["problem_count"], 0)
        self.assertEqual(coverage["problem_units"], [])

    def test_payload_exposes_confirmed_rendered_page_errors_with_links(self):
        run = SimpleNamespace(
            listing_audit_run_id=16,
            cadence="weekly",
            status="completed",
            snapshot_date=date.today(),
            listing_count=1,
            critical_count=0,
            high_count=1,
            watch_count=0,
            healthy_count=0,
            source_statuses={},
            error_message=None,
            started_at=datetime.utcnow(),
            completed_at=datetime.utcnow(),
        )
        url = "https://www.google.com/travel/hotels/entity/example/overview"
        item = {
            "listing_id": 576478,
            "listing_name": "Reflection's Edge",
            "portfolio_name": "Enchanted Havens",
            "health_score": 60.0,
            "severity": "high",
            "online_assets": [{
                "channel": "googlevr",
                "label": "Google Vacation Rentals",
                "configured": True,
                "status": "high",
                "url": url,
                "page": {
                    "status": "unavailable",
                    "failure_kind": "rendered_error",
                    "summary": "Oops, something went wrong. Having trouble loading details.",
                },
                "deep_inspection": {"status": "high", "issues": []},
            }],
            "action_items": [],
        }

        coverage = dashboard_payload(run, [item], recent_runs=[run])["summary"]["channel_coverage"]["googlevr"]

        self.assertEqual(coverage["problem_count"], 1)
        self.assertEqual(coverage["problem_units"][0]["listing_name"], "Reflection's Edge")
        self.assertEqual(coverage["problem_units"][0]["page_status"], "error")
        self.assertEqual(coverage["problem_units"][0]["url"], url)
        self.assertIn("something went wrong", coverage["problem_units"][0]["review_reason"])

    def test_portfolio_scope_is_case_insensitive_and_preserves_all_options(self):
        items = [
            {"listing_name": "Skyline Retreat", "portfolio_name": "Urban Stays"},
            {"listing_name": "Lake House", "portfolio_name": "Enchanted Havens"},
            {"listing_name": "Downtown Loft", "portfolio_name": "Urban Stays"},
        ]

        scoped, options, selected = scope_items_by_portfolio(items, "urban stays")

        self.assertEqual(selected, "Urban Stays")
        self.assertEqual(options, ["Enchanted Havens", "Urban Stays"])
        self.assertEqual([item["listing_name"] for item in scoped], ["Skyline Retreat", "Downtown Loft"])

        unscoped, _, invalid_selection = scope_items_by_portfolio(items, "Unknown")
        self.assertIsNone(invalid_selection)
        self.assertEqual(len(unscoped), 3)

    def test_weekly_deep_findings_merge_into_fresher_daily_assets_and_actions(self):
        current = [{
            "listing_id": 41,
            "online_assets": [{"channel": "airbnb", "label": "Airbnb", "configured": True, "status": "healthy"}],
            "action_items": [],
        }]
        weekly = [{
            "listing_id": 41,
            "online_assets": [{
                "channel": "airbnb",
                "label": "Airbnb",
                "deep_inspection": {
                    "status": "high",
                    "issues": [{"priority": "high", "message": "Reconcile the Airbnb location with Hostaway."}],
                },
            }],
            "action_items": [],
        }]

        merge_deep_inspections(current, weekly, source_run_id=19)

        inspection = current[0]["online_assets"][0]["deep_inspection"]
        self.assertEqual(inspection["source_run_id"], 19)
        self.assertEqual(current[0]["online_assets"][0]["status"], "high")
        self.assertEqual(current[0]["online_assets"][0]["status_source"], "weekly_deep_review")
        self.assertEqual(current[0]["action_items"][0]["source"], "weekly_deep_review")
        self.assertIn("location", current[0]["action_items"][0]["text"])


if __name__ == "__main__":
    unittest.main()
