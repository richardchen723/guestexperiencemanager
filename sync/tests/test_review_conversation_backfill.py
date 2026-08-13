import unittest

from sync.sync_messages import merge_conversation_payloads


class ReviewConversationBackfillTests(unittest.TestCase):
    def test_merge_keeps_each_conversation_once(self):
        recent = [{'id': 10, 'reservationId': 1}]
        review_window = [
            {'id': 10, 'reservationId': 1},
            {'id': 11, 'reservationId': 2},
        ]

        merged = merge_conversation_payloads(recent, review_window)

        self.assertEqual([conversation['id'] for conversation in merged], [10, 11])

    def test_merge_ignores_payloads_without_an_id(self):
        self.assertEqual(merge_conversation_payloads([{}, {'id': None}]), [])


if __name__ == '__main__':
    unittest.main()
