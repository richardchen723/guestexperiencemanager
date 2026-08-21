import os
import unittest


class WebhookTests(unittest.TestCase):
    def test_normalize_twilio_payload_extracts_media_and_numbers(self):
        os.environ.setdefault("DATABASE_URL", "postgresql://user:pass@localhost/db")
        os.environ.setdefault("OPENAI_API_KEY", "test")
        os.environ.setdefault("SECRET_KEY", "test-secret")

        from brain.webhooks import normalize_twilio_payload

        payload = normalize_twilio_payload(
            {
                "MessageSid": "SM123",
                "From": "whatsapp:+14155550123",
                "To": "whatsapp:+14155550999",
                "ProfileName": "Manager",
                "Body": "Please confirm cleaner follow up",
                "NumMedia": "1",
                "MediaUrl0": "https://example.com/image.jpg",
                "MediaContentType0": "image/jpeg",
            }
        )

        self.assertEqual(payload["provider_message_id"], "SM123")
        self.assertEqual(payload["from_number"], "+14155550123")
        self.assertEqual(payload["to_number"], "+14155550999")
        self.assertEqual(payload["sender_name"], "Manager")
        self.assertEqual(payload["media_urls"], [{"url": "https://example.com/image.jpg", "content_type": "image/jpeg"}])

    def test_external_webhook_url_uses_configured_public_base_url(self):
        os.environ.setdefault("DATABASE_URL", "postgresql://user:pass@localhost/db")
        os.environ.setdefault("OPENAI_API_KEY", "test")
        os.environ.setdefault("SECRET_KEY", "test-secret")

        import dashboard.config as config
        from brain.webhooks import _external_webhook_url

        class RequestFixture:
            path = "/webhooks/twilio/whatsapp/status"
            headers = {}
            scheme = "http"
            host = "127.0.0.1:5002"

        original_base_url = config.BRAIN_PUBLIC_BASE_URL
        try:
            config.BRAIN_PUBLIC_BASE_URL = "https://brain.yourcottoncandy.com/"
            self.assertEqual(
                _external_webhook_url(RequestFixture()),
                "https://brain.yourcottoncandy.com/webhooks/twilio/whatsapp/status",
            )
        finally:
            config.BRAIN_PUBLIC_BASE_URL = original_base_url

    def test_normalize_whatsapp_bridge_payload_maps_group_message(self):
        os.environ.setdefault("DATABASE_URL", "postgresql://user:pass@localhost/db")
        os.environ.setdefault("OPENAI_API_KEY", "test")
        os.environ.setdefault("SECRET_KEY", "test-secret")

        from brain.webhooks import normalize_whatsapp_bridge_payload

        payload = normalize_whatsapp_bridge_payload(
            {
                "provider_message_id": "false_120363@g.us_ABC",
                "group_id": "120363@g.us",
                "group_name": "PT300 management",
                "sender_id": "14155550123@c.us",
                "sender_name": "Cleaner",
                "body": "Please confirm hot tub follow up",
                "timestamp": 1782500000,
                "message_type": "chat",
                "portfolio_name": "Urban Stays",
                "group_kind": "management",
            }
        )

        self.assertEqual(payload["provider"], "whatsapp_web")
        self.assertEqual(payload["provider_message_id"], "false_120363@g.us_ABC")
        self.assertEqual(payload["provider_thread_id"], "120363@g.us")
        self.assertEqual(payload["thread_display_name"], "PT300 management")
        self.assertEqual(payload["from_number"], "14155550123@c.us")
        self.assertEqual(payload["to_number"], "120363@g.us")
        self.assertEqual(payload["sender_name"], "Cleaner")
        self.assertEqual(payload["direction"], "inbound")
        self.assertEqual(payload["portfolio_name"], "Urban Stays")
        self.assertEqual(payload["group_kind"], "management")

    def test_normalize_whatsapp_bridge_payload_prefers_canonical_group_profile(self):
        os.environ.setdefault("DATABASE_URL", "postgresql://user:pass@localhost/db")
        os.environ.setdefault("OPENAI_API_KEY", "test")
        os.environ.setdefault("SECRET_KEY", "test-secret")

        from brain.webhooks import normalize_whatsapp_bridge_payload

        payload = normalize_whatsapp_bridge_payload(
            {
                "provider_message_id": "false_PT300-management_ABC",
                "group_id": "PT300 management",
                "group_name": "PT300 management",
                "group_profile_name": "PT300 Management",
                "sender_name": "Cleaner",
                "body": "Please confirm hot tub follow up",
                "timestamp": 1782500000,
            }
        )

        self.assertEqual(payload["provider_thread_id"], "PT300 Management")
        self.assertEqual(payload["thread_display_name"], "PT300 Management")

    def test_whatsapp_bridge_token_validation_accepts_bearer(self):
        os.environ.setdefault("DATABASE_URL", "postgresql://user:pass@localhost/db")
        os.environ.setdefault("OPENAI_API_KEY", "test")
        os.environ.setdefault("SECRET_KEY", "test-secret")

        import dashboard.config as config
        from brain.webhooks import validate_whatsapp_bridge_request

        class RequestFixture:
            headers = {"Authorization": "Bearer bridge-secret"}

        original_token = config.BRAIN_WHATSAPP_BRIDGE_TOKEN
        try:
            config.BRAIN_WHATSAPP_BRIDGE_TOKEN = "bridge-secret"
            self.assertTrue(validate_whatsapp_bridge_request(RequestFixture()))
        finally:
            config.BRAIN_WHATSAPP_BRIDGE_TOKEN = original_token


if __name__ == "__main__":
    unittest.main()
