import os
import unittest
from unittest.mock import patch

from brain.pricelabs import PriceLabsClient


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class PriceLabsClientTests(unittest.TestCase):
    @patch("brain.pricelabs.requests.get")
    @patch("brain.pricelabs.requests.post")
    def test_snapshot_fetches_prices_with_reason_and_listing_metrics(self, post, get):
        post.return_value = FakeResponse(
            [
                {
                    "id": "558675",
                    "pms": "hostaway",
                    "currency": "USD",
                    "last_refreshed_at": "2026-07-07T10:00:00+00:00",
                    "data": [{"date": "2026-07-08", "price": 240, "min_stay": 2, "demand_desc": "High Demand"}],
                }
            ]
        )
        get.return_value = FakeResponse(
            {
                "data": {
                    "listing_level": {
                        "occupancy": {"7": 82.5},
                        "revenue": {"7": 1200},
                        "last_booked_date": "2026-07-01T00:00:00.000Z",
                    },
                    "market_level": {"occupancy": {"7": 71.0}},
                }
            }
        )

        with patch.dict(
            os.environ,
            {
                "PRICELABS_API_KEY": "test-key",
                "PRICELABS_BASE_URL": "https://api.pricelabs.co/v1",
                "PRICELABS_PMS_NAME": "hostaway",
                "PRICELABS_PRICE_WINDOW_DAYS": "60",
                "PRICELABS_INCLUDE_PRICE_REASON": "true",
                "PRICELABS_FETCH_METRICS": "true",
            },
            clear=False,
        ):
            result = PriceLabsClient().fetch_listing_snapshot(558675)

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["payload"]["source_statuses"], {"prices": "ok", "metrics": "ok"})
        price_request = post.call_args.kwargs["json"]["listings"][0]
        self.assertEqual(price_request["id"], "558675")
        self.assertEqual(price_request["pms"], "hostaway")
        self.assertTrue(price_request["reason"])
        self.assertIn("dateFrom", price_request)
        self.assertIn("dateTo", price_request)
        self.assertEqual(get.call_args.kwargs["params"], {"listing_id": "558675", "pms_name": "hostaway"})


if __name__ == "__main__":
    unittest.main()
