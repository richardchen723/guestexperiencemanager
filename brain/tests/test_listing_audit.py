import os
import unittest
from datetime import date
from types import SimpleNamespace

os.environ.setdefault("DATABASE_URL", "postgresql://user:pass@localhost/db")
os.environ.setdefault("OPENAI_API_KEY", "test")
os.environ.setdefault("SECRET_KEY", "test-secret")

from brain.listing_audit import (
    booking_health_payload,
    build_channel_asset,
    build_listing_audit_result,
    pricing_market_payload,
    resolve_pricelabs_audit_context,
)


def listing_detail(**overrides):
    base = {
        "id": 41,
        "internalListingName": "Skyline Retreat",
        "city": "Atlanta",
        "state": "GA",
        "airbnbExportStatus": "exported",
        "airbnbListingUrl": "https://www.airbnb.com/rooms/41",
        "airbnbName": "Skyline retreat with sweeping downtown views",
        "airbnbSummary": "A polished downtown stay with skyline views, a full kitchen, fast Wi-Fi, and walkable access to the city. " * 3,
        "vrboExportStatus": "exported",
        "vrboListingUrl": "https://www.vrbo.com/41",
        "homeawayPropertyHeadline": "Skyline retreat for city weekends",
        "homeawayPropertyDescription": "A guest-ready city retreat. " * 15,
        "bookingcomExportStatus": "exported",
        "bookingcomPropertyName": "Skyline Retreat",
        "bookingcomPropertyDescription": "A central stay with practical details for a smooth arrival. " * 10,
        "listingImages": [{"url": f"https://images.example/{i}.jpg"} for i in range(24)],
    }
    base.update(overrides)
    return base


class ListingAuditTests(unittest.TestCase):
    def test_airbnb_asset_scores_complete_guest_content(self):
        asset = build_channel_asset(
            listing_detail(),
            "airbnb",
            {"status": "ok", "title": "Skyline retreat with downtown views", "meta_description": "A polished downtown stay. " * 14},
        )

        self.assertTrue(asset["configured"])
        self.assertEqual(asset["status"], "healthy")
        self.assertGreaterEqual(asset["score"], 82)
        self.assertEqual(asset["actions"], [])

    def test_direct_asset_surfaces_missing_url(self):
        asset = build_channel_asset(listing_detail(), "direct")

        self.assertFalse(asset["configured"])
        self.assertEqual(asset["status"], "not_configured")
        self.assertIn("direct-booking URL", asset["actions"][0])

    def test_pricelabs_payload_compares_listing_with_market(self):
        snapshot = SimpleNamespace(
            status="ok",
            raw_payload={
                "prices": {
                    "currency": "USD",
                    "data": [
                        {"price": 210, "min_stay": 2, "unbookable": 0},
                        {"price": 230, "min_stay": 3, "unbookable": 0},
                    ],
                },
                "metrics": {
                    "data": {
                        "listing_level": {"occupancy": {"30": 52}},
                        "market_level": {"occupancy": {"30": 68}},
                    }
                },
            },
        )

        pricing, market = pricing_market_payload(snapshot)

        self.assertEqual(pricing["average_price"], 220)
        self.assertEqual(market["difference_points"], -16)
        self.assertEqual(market["status"], "behind")
        self.assertTrue(any("behind market" in action for action in pricing["actions"]))

    def test_pricelabs_rate_limit_uses_last_success_without_claiming_disconnection(self):
        latest = SimpleNamespace(
            status="unavailable",
            snapshot_date=date(2026, 8, 21),
            raw_payload={"source_statuses": {"prices": "unavailable", "metrics": "unavailable"}},
            error_message="429 Client Error: Too Many Requests",
        )
        prior = SimpleNamespace(
            status="ok",
            snapshot_date=date(2026, 8, 17),
            error_message=None,
            raw_payload={
                "prices": {
                    "currency": "USD",
                    "data": [{"price": 250, "min_stay": 2, "unbookable": 0}],
                },
                "metrics": {
                    "data": {
                        "listing_level": {"occupancy": {"30": 61}},
                        "market_level": {"occupancy": {"30": 64}},
                    }
                },
            },
        )

        context = resolve_pricelabs_audit_context([latest, prior])
        pricing, market = pricing_market_payload(context)

        self.assertTrue(context.using_fallback)
        self.assertEqual(pricing["status"], "stale")
        self.assertEqual(pricing["connection_status"], "connected")
        self.assertEqual(pricing["refresh_issue"], "rate_limited")
        self.assertEqual(pricing["data_snapshot_date"], "2026-08-17")
        self.assertEqual(pricing["average_price"], 250)
        self.assertEqual(market["difference_points"], -3)
        self.assertIn("Connected; using the last successful", pricing["summary"])
        self.assertFalse(any("Repair" in action for action in pricing["actions"]))

    def test_pricelabs_toggle_off_is_not_hidden_by_an_older_success(self):
        latest = SimpleNamespace(
            status="LISTING_TOGGLE_OFF",
            snapshot_date=date(2026, 8, 21),
            raw_payload={},
            error_message="Listing toggle is off",
        )
        prior = SimpleNamespace(
            status="ok",
            snapshot_date=date(2026, 8, 17),
            raw_payload={"prices": {"data": [{"price": 200}]}},
            error_message=None,
        )

        context = resolve_pricelabs_audit_context([latest, prior])
        pricing, _ = pricing_market_payload(context)

        self.assertFalse(context.using_fallback)
        self.assertEqual(pricing["status"], "LISTING_TOGGLE_OFF")
        self.assertEqual(pricing["connection_status"], "connected")
        self.assertTrue(any("sync is turned off" in action for action in pricing["actions"]))

    def test_combined_result_contains_all_four_channels_and_actions(self):
        analysis = SimpleNamespace(
            severity="high",
            snapshot_date=date(2026, 8, 21),
            horizons=[{"horizon_days": 30, "occupancy_rate": 0.3, "booked_nights": 9, "available_nights": 21}],
            booking_pattern="Forward demand is soft.",
            opinion="Forward demand is soft.",
            action_items=["Review the next 30 days of open weekday gaps."],
        )
        result = build_listing_audit_result(
            listing_detail(),
            booking_analysis=analysis,
            pricelabs_snapshot=None,
            public_pages={},
            portfolio_id=8,
            portfolio_name="Urban Stays",
        )

        self.assertEqual([asset["channel"] for asset in result["online_assets"]], ["airbnb", "vrbo", "bookingcom", "direct"])
        self.assertEqual(result["booking_health"]["horizons"][0]["occupancy_percent"], 30)
        self.assertGreater(len(result["action_items"]), 1)
        self.assertNotIn("doorSecurityCode", result["raw_payload"])


if __name__ == "__main__":
    unittest.main()
