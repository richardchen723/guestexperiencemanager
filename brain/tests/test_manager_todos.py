import unittest

from brain.services import MANAGER_TODO_LIMIT, select_manager_todos


class ManagerTodoTests(unittest.TestCase):
    def test_selector_filters_closed_items_and_caps_short_list(self):
        candidates = [
            {
                "source_type": "signal",
                "signal_id": index,
                "status": "new",
                "severity": "high",
                "title": f"Task {index}",
                "summary": "Needs follow-up.",
                "suggested_action": "Confirm next step.",
                "rank_score": 100 - index,
            }
            for index in range(12)
        ]
        candidates.extend(
            [
                {
                    "source_type": "open_loop",
                    "open_loop_id": 100,
                    "status": "resolved",
                    "severity": "critical",
                    "title": "Already done",
                    "summary": "Closed.",
                    "suggested_action": "No action.",
                    "rank_score": 999,
                },
                {
                    "source_type": "signal",
                    "signal_id": 101,
                    "status": "new",
                    "confirmed_done": True,
                    "severity": "critical",
                    "title": "Confirmed done",
                    "summary": "Already confirmed.",
                    "suggested_action": "No action.",
                    "rank_score": 999,
                },
            ]
        )

        todos = select_manager_todos(candidates)

        self.assertEqual(len(todos), MANAGER_TODO_LIMIT)
        self.assertNotIn("Already done", {item["title"] for item in todos})
        self.assertNotIn("Confirmed done", {item["title"] for item in todos})

    def test_selector_prefers_open_loop_when_signal_is_duplicate(self):
        todos = select_manager_todos(
            [
                {
                    "source_type": "signal",
                    "signal_id": 44,
                    "status": "new",
                    "severity": "critical",
                    "title": "Cleaner confirmation missing",
                    "summary": "Signal summary.",
                    "suggested_action": "Ask cleaner to confirm.",
                    "rank_score": 90,
                },
                {
                    "source_type": "open_loop",
                    "signal_id": 44,
                    "open_loop_id": 7,
                    "status": "open",
                    "severity": "high",
                    "title": "Cleaner confirmation missing",
                    "summary": "Discussed in WhatsApp.",
                    "suggested_action": "Ask cleaner to confirm.",
                    "rank_score": 88,
                },
            ]
        )

        self.assertEqual(len(todos), 1)
        self.assertEqual(todos[0]["source_type"], "open_loop")
        self.assertEqual(todos[0]["reason"], "Talked about in team messages and not confirmed done.")

    def test_selector_includes_high_roi_booking_action(self):
        todos = select_manager_todos(
            [
                {
                    "source_type": "booking_health",
                    "booking_health_analysis_id": 9,
                    "status": "open",
                    "severity": "high",
                    "title": "Booking ROI action for PT300-2M",
                    "summary": "Weak pickup across 7/30/60 days.",
                    "suggested_action": "Loosen minimum stay and lower same-week price.",
                    "priority_score": 82,
                    "category": "revenue_booking_health",
                }
            ]
        )

        self.assertEqual(todos[0]["source_type"], "booking_health")
        self.assertEqual(todos[0]["reason"], "High-ROI booking/revenue action.")


if __name__ == "__main__":
    unittest.main()
