import unittest
from datetime import date, datetime, timedelta
from types import SimpleNamespace

from brain.services import (
    build_guest_stay_memory_payload,
    guest_stay_memory_excerpt,
    guest_stay_memory_rank_score,
    guest_stay_signal_confidence,
    guest_stay_signal_severity,
)


class GuestStayMemoryTests(unittest.TestCase):
    def test_unresolved_guest_issue_becomes_watch_memory(self):
        now = datetime(2026, 6, 29, 12, 0, 0)
        reservation = SimpleNamespace(
            reservation_id=101,
            guest_name="Avery Guest",
            arrival_date=date(2026, 6, 28),
            departure_date=date(2026, 7, 1),
        )
        messages = [
            SimpleNamespace(
                message_id=1,
                sender_name="Avery",
                sender_type="guest",
                is_incoming=1,
                content_preview="We are locked out and the door code is not working. This is very frustrating.",
                created_at=now - timedelta(hours=2),
            )
        ]

        memory = build_guest_stay_memory_payload(
            reservation=reservation,
            messages=messages,
            reviews=[],
            listing_name="PT300-10H",
            portfolio_name="Urban Stays",
            window_start=now - timedelta(days=60),
            window_end=now,
            window_days=60,
        )

        self.assertEqual(memory["status"], "watch")
        self.assertEqual(memory["risk_level"], "critical")
        self.assertGreaterEqual(memory["risk_score"], 85)
        self.assertIn("locked out", memory["risk_summary"].lower())
        self.assertIn("access", memory["suggested_action"].lower())

    def test_resolution_after_guest_issue_reduces_attention(self):
        now = datetime(2026, 6, 29, 12, 0, 0)
        reservation = SimpleNamespace(
            reservation_id=102,
            guest_name="Blake Guest",
            arrival_date=date(2026, 6, 27),
            departure_date=date(2026, 6, 30),
        )
        messages = [
            SimpleNamespace(
                message_id=1,
                sender_name="Blake",
                sender_type="guest",
                is_incoming=1,
                content_preview="The AC is not working and the room is too hot.",
                created_at=now - timedelta(hours=5),
            ),
            SimpleNamespace(
                message_id=2,
                sender_name="Host",
                sender_type="host",
                is_incoming=0,
                content_preview="The AC is fixed and guest confirmed it works now. Thanks for your patience.",
                created_at=now - timedelta(hours=2),
            ),
        ]

        memory = build_guest_stay_memory_payload(
            reservation=reservation,
            messages=messages,
            reviews=[],
            listing_name="Emerald Haven",
            portfolio_name="Enchanted Havens",
            window_start=now - timedelta(days=60),
            window_end=now,
            window_days=60,
        )

        self.assertEqual(memory["status"], "ok")
        self.assertLess(memory["risk_score"], 40)
        self.assertIn("fixed", memory["resolution_summary"].lower())

    def test_routine_guest_questions_do_not_become_review_risks(self):
        now = datetime(2026, 6, 29, 12, 0, 0)
        reservation = SimpleNamespace(
            reservation_id=104,
            guest_name="Routine Guest",
            arrival_date=date(2026, 6, 28),
            departure_date=date(2026, 7, 1),
        )
        messages = [
            SimpleNamespace(
                message_id=1,
                sender_name="Routine",
                sender_type="guest",
                is_incoming=1,
                content_preview="Do you have starter woods and fire wood to use?",
                created_at=now - timedelta(hours=4),
            ),
            SimpleNamespace(
                message_id=2,
                sender_name="Routine",
                sender_type="guest",
                is_incoming=1,
                content_preview="Where do we put the trash?",
                created_at=now - timedelta(hours=3),
            ),
            SimpleNamespace(
                message_id=3,
                sender_name="Routine",
                sender_type="guest",
                is_incoming=1,
                content_preview="If I book the following weekend and a cancellation occurs for my desired weekend, can I switch?",
                created_at=now - timedelta(hours=2),
            ),
        ]

        memory = build_guest_stay_memory_payload(
            reservation=reservation,
            messages=messages,
            reviews=[],
            listing_name="Middlefork Ridge",
            portfolio_name="Middlefork",
            window_start=now - timedelta(days=60),
            window_end=now,
            window_days=60,
        )

        self.assertEqual(memory["status"], "ok")
        self.assertEqual(memory["risk_score"], 0.0)
        self.assertIn("No guest language", memory["risk_summary"])

    def test_routine_checkin_time_question_is_not_an_access_failure(self):
        now = datetime(2026, 8, 3, 12, 0, 0)
        reservation = SimpleNamespace(
            reservation_id=106,
            guest_name="Routine Guest",
            arrival_date=date(2026, 8, 3),
            departure_date=date(2026, 8, 6),
        )
        messages = [
            SimpleNamespace(
                message_id=1,
                sender_name="Routine",
                sender_type="guest",
                is_incoming=1,
                content_preview="What's the check-in time? Is early check-in possible?",
                created_at=now - timedelta(hours=2),
            )
        ]

        memory = build_guest_stay_memory_payload(
            reservation=reservation,
            messages=messages,
            reviews=[],
            listing_name="8956: Second Floor Apartment",
            portfolio_name="LA St Gabe",
            window_start=now - timedelta(days=60),
            window_end=now,
            window_days=60,
        )

        self.assertEqual(memory["status"], "ok")
        self.assertEqual(memory["risk_score"], 0.0)
        self.assertIn("No guest language", memory["risk_summary"])

    def test_explicit_checkin_failure_remains_critical(self):
        now = datetime(2026, 8, 3, 12, 0, 0)
        reservation = SimpleNamespace(
            reservation_id=107,
            guest_name="Blocked Guest",
            arrival_date=date(2026, 8, 3),
            departure_date=date(2026, 8, 6),
        )
        messages = [
            SimpleNamespace(
                message_id=1,
                sender_name="Blocked",
                sender_type="guest",
                is_incoming=1,
                content_preview="We cannot check in because the door code is not working.",
                created_at=now - timedelta(hours=1),
            )
        ]

        memory = build_guest_stay_memory_payload(
            reservation=reservation,
            messages=messages,
            reviews=[],
            listing_name="8956: Second Floor Apartment",
            portfolio_name="LA St Gabe",
            window_start=now - timedelta(days=60),
            window_end=now,
            window_days=60,
        )

        self.assertEqual(memory["status"], "watch")
        self.assertEqual(memory["risk_level"], "critical")
        self.assertGreaterEqual(memory["risk_score"], 85)

    def test_low_review_stays_visible_as_guest_memory(self):
        now = datetime(2026, 6, 29, 12, 0, 0)
        reservation = SimpleNamespace(
            reservation_id=103,
            guest_name="Casey Guest",
            arrival_date=date(2026, 6, 10),
            departure_date=date(2026, 6, 13),
        )
        review = SimpleNamespace(
            review_id=7,
            overall_rating=2.0,
            review_date=date(2026, 6, 14),
            inserted_on=now - timedelta(days=14),
            reviewer_name="Casey",
            review_text="The place was dirty and the hot tub was broken.",
        )

        memory = build_guest_stay_memory_payload(
            reservation=reservation,
            messages=[],
            reviews=[review],
            listing_name="Crestwood Main House",
            portfolio_name="crestwood",
            window_start=now - timedelta(days=60),
            window_end=now,
            window_days=60,
        )

        self.assertEqual(memory["status"], "watch")
        self.assertEqual(memory["risk_level"], "medium")
        self.assertIn("2.0 stars", memory["summary"])
        self.assertIn("low-rated stay", memory["suggested_action"])

    def test_host_checkin_template_does_not_inflate_low_review_to_critical(self):
        now = datetime(2026, 6, 29, 12, 0, 0)
        reservation = SimpleNamespace(
            reservation_id=105,
            guest_name="Template Guest",
            arrival_date=date(2026, 6, 10),
            departure_date=date(2026, 6, 13),
        )
        messages = [
            SimpleNamespace(
                message_id=1,
                sender_name="Host",
                sender_type="host",
                is_incoming=0,
                content_preview="We are excited to host you. Please note your check-in access instructions are below.",
                created_at=now - timedelta(days=16),
            )
        ]
        review = SimpleNamespace(
            review_id=8,
            overall_rating=3.0,
            review_date=date(2026, 6, 14),
            inserted_on=now - timedelta(days=14),
            reviewer_name="Template",
            review_text="Average stay.",
        )

        memory = build_guest_stay_memory_payload(
            reservation=reservation,
            messages=messages,
            reviews=[review],
            listing_name="PT300-19M",
            portfolio_name="Urban Stays",
            window_start=now - timedelta(days=60),
            window_end=now,
            window_days=60,
        )

        self.assertEqual(memory["status"], "watch")
        self.assertEqual(memory["risk_level"], "medium")
        self.assertEqual(memory["risk_score"], 42.0)
        self.assertIn("No guest language", memory["risk_summary"])

    def test_memory_signal_helpers_shape_review_risk_signal(self):
        memory = SimpleNamespace(
            summary="Guest had an unresolved access issue.",
            risk_summary="Guest risk context: locked out.",
            latest_guest_issue="Door code is not working.",
            resolution_summary="No clear closure.",
            suggested_action="Confirm guest has access.",
            risk_level="high",
            risk_score=78,
            message_count=8,
            review_rating=None,
        )

        self.assertEqual(guest_stay_signal_severity(memory), "high")
        self.assertGreater(guest_stay_signal_confidence(memory), 0.8)
        self.assertGreater(guest_stay_memory_rank_score(memory), 90)
        self.assertIn("Door code", guest_stay_memory_excerpt(memory))


if __name__ == "__main__":
    unittest.main()
