import os
import unittest

os.environ.setdefault("DATABASE_URL", "postgresql://user:pass@localhost/db")
os.environ.setdefault("OPENAI_API_KEY", "test")
os.environ.setdefault("SECRET_KEY", "test-secret")

from brain.whatsapp_export_import import parse_export_text


class WhatsAppExportImportTests(unittest.TestCase):
    def test_parse_ios_export_with_multiline_message(self):
        messages = parse_export_text(
            "\n".join(
                [
                    "[6/20/26, 9:15:02 AM] Cleaner: Towels are missing",
                    "Can someone bring more?",
                    "[6/20/26, 9:18:00 AM] Manager: I can handle it",
                ]
            ),
            source_timezone="America/Los_Angeles",
        )

        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[0].sender_name, "Cleaner")
        self.assertIn("Can someone bring more?", messages[0].body)
        self.assertEqual(messages[0].occurred_at.isoformat(), "2026-06-20T16:15:02")

    def test_parse_android_export_and_skip_system_messages_by_default(self):
        messages = parse_export_text(
            "\n".join(
                [
                    "6/20/26, 9:15 AM - Messages and calls are end-to-end encrypted.",
                    "6/20/26, 9:16 AM - Saad: No problem.",
                ]
            ),
            source_timezone="America/Los_Angeles",
        )

        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0].sender_name, "Saad")
        self.assertEqual(messages[0].body, "No problem.")

    def test_parse_export_can_include_system_messages(self):
        messages = parse_export_text(
            "6/20/26, 9:15 AM - Messages and calls are end-to-end encrypted.",
            source_timezone="America/Los_Angeles",
            include_system=True,
        )

        self.assertEqual(len(messages), 1)
        self.assertTrue(messages[0].is_system)


if __name__ == "__main__":
    unittest.main()
