import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch


class DeliveryTests(unittest.TestCase):
    def test_email_delivery_returns_structured_skip_when_smtp_is_missing(self):
        os.environ.setdefault("DATABASE_URL", "postgresql://user:pass@localhost/db")
        os.environ.setdefault("OPENAI_API_KEY", "test")
        os.environ.setdefault("SECRET_KEY", "test-secret")

        from brain.delivery import BrainDeliveryService

        with patch.dict(os.environ, {"SMTP_HOST": ""}):
            result = BrainDeliveryService().send_email("ops@example.com", "Subject", "Body")

        self.assertFalse(result["sent"])
        self.assertEqual(result["provider"], "smtp")
        self.assertEqual(result["recipient"], "ops@example.com")
        self.assertIn("SMTP", result["error"])

    def test_whatsapp_delivery_compacts_long_brief_before_twilio_send(self):
        os.environ.setdefault("DATABASE_URL", "postgresql://user:pass@localhost/db")
        os.environ.setdefault("OPENAI_API_KEY", "test")
        os.environ.setdefault("SECRET_KEY", "test-secret")

        import dashboard.config as config
        from brain.delivery import BrainDeliveryService, WHATSAPP_BODY_LIMIT

        sent_payload = {}

        class Messages:
            def create(self, **kwargs):
                sent_payload.update(kwargs)
                return SimpleNamespace(sid="SM123", status="queued")

        class Client:
            def __init__(self, *_args, **_kwargs):
                self.messages = Messages()

        long_body = "STR Signal Brain - Daily Brief\n" + "\n".join(
            f"{idx}. Long operational signal with enough detail to overflow Twilio's message body limit."
            for idx in range(80)
        )
        original = (config.TWILIO_ACCOUNT_SID, config.TWILIO_AUTH_TOKEN, config.TWILIO_WHATSAPP_FROM)
        try:
            config.TWILIO_ACCOUNT_SID = "ACtest"
            config.TWILIO_AUTH_TOKEN = "token"
            config.TWILIO_WHATSAPP_FROM = "whatsapp:+15555550100"
            rest_module = SimpleNamespace(Client=Client)
            with patch.dict("sys.modules", {"twilio": SimpleNamespace(rest=rest_module), "twilio.rest": rest_module}):
                result = BrainDeliveryService().send_whatsapp("+17709255856", long_body)
        finally:
            config.TWILIO_ACCOUNT_SID, config.TWILIO_AUTH_TOKEN, config.TWILIO_WHATSAPP_FROM = original

        self.assertTrue(result["sent"])
        self.assertTrue(result["truncated"])
        self.assertLessEqual(len(sent_payload["body"]), WHATSAPP_BODY_LIMIT)
        self.assertIn("More in Brain dashboard.", sent_payload["body"])


if __name__ == "__main__":
    unittest.main()
