import unittest

from database.models import Conversation, Listing, Reservation
from sync.sync_messages import merge_conversation_payloads, repair_conversation_relationships


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

    def test_repairs_conversation_created_before_reservation_was_available(self):
        conversation = Conversation(conversation_id=10, listing_id=20)
        reservation = Reservation(reservation_id=30, listing_id=21, guest_id=40)
        listing = Listing(listing_id=21)

        changed = repair_conversation_relationships(
            conversation,
            reservation,
            listing,
            fallback_listing_id=20,
            fallback_guest_id=None,
        )

        self.assertTrue(changed)
        self.assertEqual(conversation.reservation_id, 30)
        self.assertEqual(conversation.listing_id, 21)
        self.assertEqual(conversation.guest_id, 40)

    def test_relationship_repair_is_idempotent(self):
        conversation = Conversation(
            conversation_id=10,
            reservation_id=30,
            listing_id=21,
            guest_id=40,
        )
        reservation = Reservation(reservation_id=30, listing_id=21, guest_id=40)

        changed = repair_conversation_relationships(
            conversation,
            reservation,
            listing=None,
            fallback_listing_id=21,
            fallback_guest_id=40,
        )

        self.assertFalse(changed)


if __name__ == '__main__':
    unittest.main()
