from datetime import datetime
from types import SimpleNamespace
import unittest

from brain.services import (
    BrainService,
    OPERATOR_ESCALATED_MIN_RANK,
    apply_signal_status_transition,
    operator_open_loop_status_for_signal_status,
    should_retire_deterministic_signal,
    should_retire_openai_signal,
)


class FakeQuery:
    def __init__(self, rows):
        self.rows = rows

    def filter(self, *_args, **_kwargs):
        return self

    def all(self):
        return self.rows


class FakeSession:
    def __init__(self, rows=None):
        self.rows = list(rows or [])
        self.added = []

    def query(self, *_args, **_kwargs):
        return FakeQuery(self.rows)

    def add(self, row):
        self.added.append(row)
        self.rows.append(row)


class SignalLifecycleTests(unittest.TestCase):
    def test_operator_resolve_closes_signal_and_open_loop(self):
        signal = SimpleNamespace(status="new", resolved_at=None, rank_score=72)

        effects = apply_signal_status_transition(signal, "resolved")

        self.assertEqual(signal.status, "resolved")
        self.assertIsNotNone(signal.resolved_at)
        self.assertTrue(effects["closed"])
        self.assertEqual(operator_open_loop_status_for_signal_status("resolved"), "resolved")

    def test_operator_reopens_resolved_signal_when_watching(self):
        signal = SimpleNamespace(status="resolved", resolved_at=object(), rank_score=72)

        effects = apply_signal_status_transition(signal, "watching")

        self.assertEqual(signal.status, "watching")
        self.assertIsNone(signal.resolved_at)
        self.assertTrue(effects["active"])
        self.assertEqual(operator_open_loop_status_for_signal_status("watching"), "open")

    def test_operator_escalation_gets_urgent_rank_and_stays_active(self):
        signal = SimpleNamespace(status="new", resolved_at=None, rank_score=41)

        effects = apply_signal_status_transition(signal, "escalated")

        self.assertEqual(signal.status, "escalated")
        self.assertEqual(signal.rank_score, OPERATOR_ESCALATED_MIN_RANK)
        self.assertTrue(effects["rank_score_changed"])
        self.assertEqual(operator_open_loop_status_for_signal_status("escalated"), "open")

    def test_status_update_closes_linked_open_loop(self):
        service = BrainService.__new__(BrainService)
        loop = SimpleNamespace(status="open", resolved_at=None, last_known_update="Waiting", last_activity_at=None)
        service.session = FakeSession([loop])
        signal = SimpleNamespace(signal_id=10, category="operational_open_loop")
        now = datetime(2026, 6, 29, 8, 0, 0)

        effects = service._process_signal_status_open_loops(signal, "resolved", now=now)

        self.assertEqual(loop.status, "resolved")
        self.assertEqual(loop.resolved_at, now)
        self.assertEqual(effects["open_loops_closed"], 1)

    def test_status_update_reopens_operational_open_loop(self):
        service = BrainService.__new__(BrainService)
        loop = SimpleNamespace(
            status="resolved",
            resolved_at=datetime(2026, 6, 28, 8, 0, 0),
            last_known_update="Resolved",
            involved_people="Team",
        )
        service.session = FakeSession([loop])
        now = datetime(2026, 6, 29, 8, 15, 0)
        signal = SimpleNamespace(
            signal_id=11,
            portfolio_id=2,
            listing_id=3,
            reservation_id=None,
            category="operational_open_loop",
            title="Cleaner confirmation needed",
            summary="No confirmation yet.",
            suggested_action="Ask cleaner to confirm.",
            last_seen_at=now,
            confidence=0.8,
        )

        effects = service._process_signal_status_open_loops(signal, "watching", now=now)

        self.assertEqual(loop.status, "open")
        self.assertIsNone(loop.resolved_at)
        self.assertEqual(loop.title, "Cleaner confirmation needed")
        self.assertEqual(effects["open_loops_reopened"], 1)

    def test_recomputed_deterministic_signal_retires_when_absent_from_current_run(self):
        signal = SimpleNamespace(
            source="deterministic_booking_health",
            run_id=41,
            status="new",
        )

        self.assertTrue(should_retire_deterministic_signal(signal, current_run_id=42))

    def test_current_run_signal_does_not_retire(self):
        signal = SimpleNamespace(
            source="deterministic_guest_message",
            run_id=42,
            status="new",
        )

        self.assertFalse(should_retire_deterministic_signal(signal, current_run_id=42))

    def test_escalated_signal_does_not_retire(self):
        signal = SimpleNamespace(
            source="deterministic_booking_health",
            run_id=41,
            status="escalated",
        )

        self.assertFalse(should_retire_deterministic_signal(signal, current_run_id=42))

    def test_whatsapp_open_loop_does_not_retire_by_absence(self):
        signal = SimpleNamespace(
            source="deterministic_whatsapp",
            run_id=41,
            status="new",
        )

        self.assertFalse(should_retire_deterministic_signal(signal, current_run_id=42))

    def test_old_openai_signal_retires_after_successful_current_pass(self):
        signal = SimpleNamespace(
            source="openai_structured",
            run_id=41,
            status="new",
        )

        self.assertTrue(should_retire_openai_signal(signal, current_run_id=42))

    def test_current_openai_signal_does_not_retire(self):
        signal = SimpleNamespace(
            source="openai_structured",
            run_id=42,
            status="new",
        )

        self.assertFalse(should_retire_openai_signal(signal, current_run_id=42))

    def test_escalated_openai_signal_does_not_retire(self):
        signal = SimpleNamespace(
            source="openai_structured",
            run_id=41,
            status="escalated",
        )

        self.assertFalse(should_retire_openai_signal(signal, current_run_id=42))


if __name__ == "__main__":
    unittest.main()
