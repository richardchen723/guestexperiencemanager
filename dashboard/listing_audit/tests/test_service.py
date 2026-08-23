import os
import unittest
from datetime import date, datetime
from types import SimpleNamespace

os.environ.setdefault("DATABASE_URL", "postgresql://user:pass@localhost/db")
os.environ.setdefault("OPENAI_API_KEY", "test")
os.environ.setdefault("SECRET_KEY", "test-secret")

from dashboard.listing_audit.service import dashboard_payload, scope_items_by_portfolio


class ListingAuditDashboardPayloadTests(unittest.TestCase):
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
                {"channel": "googlevr", "configured": True, "status": "healthy"},
            ],
            "action_items": [{"priority": "high", "category": "Booking", "text": "Close the near-term gap."}],
        }

        result = dashboard_payload(run, [item], recent_runs=[run])

        self.assertTrue(result["has_data"])
        self.assertEqual(result["summary"]["listing_count"], 1)
        self.assertEqual(result["summary"]["channel_coverage"]["airbnb"]["healthy"], 1)
        booking_coverage = result["summary"]["channel_coverage"]["bookingcom"]
        self.assertEqual(booking_coverage["missing_count"], 1)
        self.assertEqual(booking_coverage["missing_units"][0]["listing_name"], "Skyline Retreat")
        self.assertEqual(booking_coverage["missing_units"][0]["connection_status"], "not exported")
        google_coverage = result["summary"]["channel_coverage"]["googlevr"]
        self.assertEqual(google_coverage["label"], "Google Vacation Rentals")
        self.assertEqual(google_coverage["configured"], 1)
        self.assertEqual(google_coverage["healthy"], 1)
        self.assertEqual(result["top_actions"][0]["listing_name"], "Skyline Retreat")
        self.assertEqual(result["profile_label"], "All properties")
        self.assertFalse(result["is_stale"])

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


if __name__ == "__main__":
    unittest.main()
