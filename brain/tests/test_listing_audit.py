import os
import unittest
from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("DATABASE_URL", "postgresql://user:pass@localhost/db")
os.environ.setdefault("OPENAI_API_KEY", "test")
os.environ.setdefault("SECRET_KEY", "test-secret")

from brain.listing_audit import (
    booking_health_payload,
    build_channel_asset,
    build_listing_audit_result,
    configure_pricelabs_for_listing_audit,
    cover_image_candidates,
    merge_rendered_page_result,
    pricing_market_payload,
    resolve_cover_image,
    resolve_pricelabs_audit_context,
)
from brain.channel_page_audit import (
    channel_destination_valid,
    deep_content_is_sparse,
    extract_deep_page_content,
    rendered_page_error_message,
)


def listing_detail(**overrides):
    base = {
        "id": 41,
        "internalListingName": "Skyline Retreat",
        "name": "Skyline Retreat",
        "description": "A polished downtown stay with skyline views, a full kitchen, fast Wi-Fi, and walkable access to the city. " * 3,
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
        "googleExportStatus": "exported",
        "googleVrListingUrl": "https://www.google.com/travel/hotels/entity/example/overview",
        "listingAmenities": [{"name": name} for name in ("Wifi", "Kitchen", "Parking", "Pool", "Washer", "Dryer", "Air conditioning", "Heating", "TV", "Workspace")],
        "airbnbNotes": "Guests should review parking instructions and quiet hours before arrival.",
        "houseRules": "No smoking. No parties. Quiet hours are from 10 PM to 8 AM.",
        "listingImages": [{"url": f"https://images.example/{i}.jpg"} for i in range(24)],
    }
    base.update(overrides)
    return base


class ListingAuditTests(unittest.TestCase):
    def test_cover_candidates_prefer_sorted_hostaway_gallery_then_channel_thumbnail(self):
        detail = listing_detail(
            thumbnailUrl="https://a0.muscache.com/stale.jpg",
            listingImages=[
                {"url": "https://hostaway.example/second.jpg", "sortOrder": 2},
                {"url": "https://hostaway.example/first.jpg", "sortOrder": 1},
            ],
        )

        self.assertEqual(
            cover_image_candidates(detail),
            [
                ("https://hostaway.example/first.jpg", "hostaway_gallery"),
                ("https://hostaway.example/second.jpg", "hostaway_gallery"),
                ("https://a0.muscache.com/stale.jpg", "channel_thumbnail"),
            ],
        )

    def test_cover_resolution_skips_broken_image_and_uses_next_valid_candidate(self):
        detail = listing_detail(
            thumbnailUrl="https://a0.muscache.com/stale.jpg",
            listingImages=[
                {"url": "https://hostaway.example/broken.jpg", "sortOrder": 1},
                {"url": "https://hostaway.example/cover.jpg", "sortOrder": 2},
            ],
        )

        with patch(
            "brain.listing_audit.fetch_image_status",
            side_effect=[
                {"status": "unavailable", "http_status": 404},
                {"status": "ok", "url": "https://hostaway.example/cover.jpg", "http_status": 200, "content_type": "image/jpeg"},
            ],
        ):
            result = resolve_cover_image(detail)

        self.assertEqual(result["url"], "https://hostaway.example/cover.jpg")
        self.assertEqual(result["source"], "hostaway_gallery")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["attempted"], 2)

    def test_audit_pricelabs_refresh_uses_bounded_window_without_reasons(self):
        client = SimpleNamespace(price_window_days=365, include_price_reason=True)

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("LISTING_AUDIT_PRICELABS_WINDOW_DAYS", None)
            os.environ.pop("LISTING_AUDIT_PRICELABS_INCLUDE_PRICE_REASON", None)
            configure_pricelabs_for_listing_audit(client)

        self.assertEqual(client.price_window_days, 90)
        self.assertFalse(client.include_price_reason)
        self.assertEqual(client.timeout, 60)
        self.assertEqual(client.max_retries, 2)
        self.assertEqual(client.retry_backoff_seconds, 3)

    def test_audit_pricelabs_refresh_honors_bounded_overrides(self):
        client = SimpleNamespace(price_window_days=365, include_price_reason=False)

        with patch.dict(
            os.environ,
            {
                "LISTING_AUDIT_PRICELABS_WINDOW_DAYS": "120",
                "LISTING_AUDIT_PRICELABS_INCLUDE_PRICE_REASON": "true",
                "LISTING_AUDIT_PRICELABS_TIMEOUT_SECONDS": "45",
                "LISTING_AUDIT_PRICELABS_MAX_RETRIES": "3",
                "LISTING_AUDIT_PRICELABS_RETRY_BACKOFF_SECONDS": "2.5",
            },
        ):
            configure_pricelabs_for_listing_audit(client)

        self.assertEqual(client.price_window_days, 120)
        self.assertTrue(client.include_price_reason)
        self.assertEqual(client.timeout, 45)
        self.assertEqual(client.max_retries, 3)
        self.assertEqual(client.retry_backoff_seconds, 2.5)

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

    def test_google_vacation_rentals_asset_uses_hostaway_export_and_guest_url(self):
        asset = build_channel_asset(listing_detail(), "googlevr")

        self.assertTrue(asset["configured"])
        self.assertEqual(asset["label"], "Google Vacation Rentals")
        self.assertEqual(asset["export_status"], "exported")
        self.assertEqual(
            asset["url"],
            "https://www.google.com/travel/hotels/entity/example/overview",
        )
        self.assertEqual(asset["status"], "healthy")

    def test_channel_destination_validation_rejects_cross_channel_redirects(self):
        self.assertTrue(channel_destination_valid("https://www.airbnb.com/rooms/41", "airbnb"))
        self.assertTrue(channel_destination_valid("https://www.google.com/travel/hotels/entity/41", "googlevr"))
        self.assertFalse(channel_destination_valid("https://example.com/rooms/41", "airbnb"))
        self.assertTrue(channel_destination_valid("https://stay.example.com/property/41", "direct"))

    def test_rendered_page_error_detection_requires_visible_error_copy(self):
        self.assertIn(
            "Oops, something went wrong",
            rendered_page_error_message("Oops, something went wrong. Please try again. Having trouble loading details"),
        )
        self.assertEqual(
            rendered_page_error_message("A working property page with sparse dynamic listing content."),
            "",
        )

    def test_confirmed_rendered_error_replaces_sparse_static_result(self):
        original = {"status": "ok", "summary": "Sparse JavaScript shell."}
        rendered = {
            "status": "unavailable",
            "failure_kind": "rendered_error",
            "summary": "Oops, something went wrong. Having trouble loading details.",
        }

        result = merge_rendered_page_result(original, rendered)

        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["failure_kind"], "rendered_error")
        self.assertEqual(result["browser_render"]["status"], "unavailable")

    def test_automation_block_does_not_turn_working_static_page_into_link_problem(self):
        original = {"status": "ok", "summary": "Sparse JavaScript shell."}
        rendered = {
            "status": "blocked",
            "failure_kind": "automation_blocked",
            "summary": "Verify you are human.",
        }

        result = merge_rendered_page_result(original, rendered)

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["browser_render"]["status"], "blocked")

    def test_deep_page_extraction_reads_structured_listing_content(self):
        html = """
        <html><head>
          <title>Skyline Retreat</title>
          <meta name="description" content="A polished downtown stay with skyline views and a full kitchen.">
          <script type="application/ld+json">
          {"@type":"VacationRental","name":"Skyline Retreat","address":{"addressLocality":"Atlanta","addressRegion":"GA"},
           "amenityFeature":[{"name":"Wifi"},{"name":"Kitchen"}],
           "houseRules":"No smoking. Quiet hours after 10 PM.",
           "guestNotes":"Review the parking instructions before arrival."}
          </script>
        </head><body><h1>Skyline Retreat</h1><p>Guest-ready downtown home.</p></body></html>
        """

        content = extract_deep_page_content(html)

        self.assertEqual(content["fields"]["title"], "Skyline Retreat")
        self.assertIn("Atlanta", content["fields"]["location"])
        self.assertEqual(content["fields"]["amenities"], ["Wifi", "Kitchen"])
        self.assertIn("No smoking", content["fields"]["house_rules"])
        self.assertGreater(content["structured_data_blocks"], 0)
        self.assertFalse(deep_content_is_sparse({"status": "ok", "deep_content": content}))
        self.assertTrue(deep_content_is_sparse({
            "status": "ok",
            "deep_content": {"fields": {"title": "Only a shell"}},
        }))

    def test_deep_asset_persists_field_reviews_and_improvement_findings(self):
        detail = listing_detail(airbnbName="Short title")
        page = {
            "status": "ok",
            "url": detail["airbnbListingUrl"],
            "checked_at": "2026-08-23T10:00:00Z",
            "domain_valid": True,
            "redirected": False,
            "inspection_mode": "deep",
            "title": "Different Downtown Apartment",
            "meta_description": "Brief description.",
            "deep_content": {
                "fields": {
                    "title": "Different Downtown Apartment",
                    "description": "Brief description.",
                    "location": "Miami, FL",
                    "amenities": ["Wifi"],
                    "guest_notes": "",
                    "house_rules": "",
                },
                "visible_text_length": 800,
                "structured_data_blocks": 1,
                "page_image_count": 8,
            },
            "_deep_search_text": "Different Downtown Apartment Miami FL Wifi",
        }

        asset = build_channel_asset(detail, "airbnb", page, deep=True)

        inspection = asset["deep_inspection"]
        self.assertEqual(inspection["status"], "high")
        self.assertEqual(inspection["fields"]["location"]["status"], "partial")
        self.assertTrue(any(issue["code"] == "title_too_short" for issue in inspection["issues"]))
        self.assertNotIn("_deep_search_text", asset["page"])

    def test_exported_bookingcom_without_public_url_is_not_treated_as_valid(self):
        asset = build_channel_asset(listing_detail(), "bookingcom")

        self.assertTrue(asset["configured"])
        self.assertFalse(asset["url"])
        self.assertEqual(asset["status"], "watch")
        self.assertTrue(any("public Booking.com URL" in action for action in asset["actions"]))

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

    def test_combined_result_contains_all_five_channels_and_actions(self):
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

        self.assertEqual(
            [asset["channel"] for asset in result["online_assets"]],
            ["airbnb", "vrbo", "bookingcom", "googlevr", "direct"],
        )
        self.assertEqual(result["booking_health"]["horizons"][0]["occupancy_percent"], 30)
        self.assertGreater(len(result["action_items"]), 1)
        self.assertNotIn("doorSecurityCode", result["raw_payload"])

    def test_combined_result_persists_validated_cover_instead_of_stale_channel_thumbnail(self):
        detail = listing_detail(
            thumbnailUrl="https://a0.muscache.com/stale.jpg",
            _audit_cover_image={
                "url": "https://hostaway.example/cover.jpg",
                "source": "hostaway_gallery",
                "status": "ok",
            },
        )

        result = build_listing_audit_result(
            detail,
            booking_analysis=None,
            pricelabs_snapshot=None,
            public_pages={},
            portfolio_id=None,
            portfolio_name=None,
        )

        self.assertEqual(result["raw_payload"]["thumbnail_url"], "https://hostaway.example/cover.jpg")
        self.assertEqual(result["raw_payload"]["cover_image_source"], "hostaway_gallery")
        self.assertEqual(result["raw_payload"]["cover_image_status"], "ok")
        self.assertEqual(result["raw_payload"]["channel_thumbnail_url"], "https://a0.muscache.com/stale.jpg")


if __name__ == "__main__":
    unittest.main()
