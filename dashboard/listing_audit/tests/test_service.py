import os
import unittest
from datetime import date, datetime
from types import SimpleNamespace

os.environ.setdefault("DATABASE_URL", "postgresql://user:pass@localhost/db")
os.environ.setdefault("OPENAI_API_KEY", "test")
os.environ.setdefault("SECRET_KEY", "test-secret")

from dashboard.listing_audit.service import (
    confirmed_channel_link_problem,
    dashboard_payload,
    merge_deep_inspections,
    scope_items_by_portfolio,
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
            "action_items": [{"priority": "high", "category": "Booking", "text": "Close the near-term gap."}],
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
        self.assertEqual(result["summary"]["deep_reviewed_count"], 1)
        self.assertTrue(result["has_deep_review"])
        self.assertEqual(result["top_actions"][0]["listing_name"], "Skyline Retreat")
        self.assertEqual(result["profile_label"], "All properties")
        self.assertFalse(result["is_stale"])

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
