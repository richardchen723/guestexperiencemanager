import unittest
from datetime import datetime
from unittest.mock import patch

from dashboard.reviews.query import _review_for_origin
from database.models import Conversation, MessageMetadata, Reservation, Review
from sync.api_client import HostawayAPIClient
from sync.sync_guests import reassign_guest_references
from sync.sync_manager import should_sync
from sync.sync_messages import conversation_last_message_at, message_id_for_payload
from sync.sync_reviews import fetch_review_pages, should_sync_review_payload


class ApiParameterTests(unittest.TestCase):
    def make_client(self, response=None):
        client = HostawayAPIClient.__new__(HostawayAPIClient)
        client._make_request = lambda _endpoint, params=None: response
        return client

    def test_review_arrays_and_sort_use_current_hostaway_parameter_names(self):
        captured = {}
        client = self.make_client()
        client._make_request = lambda endpoint, params: (
            captured.update({'endpoint': endpoint, 'params': params}) or {'result': []}
        )

        result = client.get_reviews_page(
            listing_id=123,
            statuses=['pending', 'submitted', 'published'],
            sortBy='id',
            order='desc',
            departure_date_start='2026-08-01',
        )

        self.assertEqual(result, [])
        self.assertEqual(captured['endpoint'], 'reviews')
        self.assertEqual(captured['params']['listingMapIds[0]'], 123)
        self.assertEqual(captured['params']['statuses[0]'], 'pending')
        self.assertEqual(captured['params']['statuses[2]'], 'published')
        self.assertEqual(captured['params']['sortOrder'], 'desc')
        self.assertNotIn('listingId', captured['params'])
        self.assertNotIn('status', captured['params'])
        self.assertNotIn('order', captured['params'])

    def test_reservation_page_uses_cursor_and_supported_activity_filter(self):
        captured = {}
        client = self.make_client()
        client._make_request = lambda endpoint, params: (
            captured.update({'endpoint': endpoint, 'params': params}) or {'result': []}
        )

        client.get_reservations_page(
            limit=500,
            after_id=456,
            latest_activity_on=datetime(2026, 8, 12, 18, 30),
        )

        self.assertEqual(captured['params']['afterId'], 456)
        self.assertEqual(captured['params']['latestActivityStart'], '2026-08-12')
        self.assertNotIn('latestActivityOn', captured['params'])

    def test_strict_pages_distinguish_failure_from_empty_result(self):
        client = self.make_client(None)
        self.assertIsNone(client.get_reservations_page(limit=100))
        self.assertIsNone(client.get_conversations_page(limit=100))
        self.assertIsNone(client.get_conversation_messages_page(10, limit=100))
        self.assertIsNone(client.get_reviews_page(limit=100))

    def test_conversation_message_history_is_fully_paginated(self):
        client = self.make_client()
        pages = {
            0: [{'id': 1}, {'id': 2}],
            2: [{'id': 3}],
        }
        client.get_conversation_messages_page = (
            lambda _conversation_id, limit, offset: pages[offset]
        )

        result = client.get_all_conversation_messages(99, limit=2)

        self.assertEqual([row['id'] for row in result], [1, 2, 3])


class ReviewSyncPolicyTests(unittest.TestCase):
    def test_pending_host_review_counts_as_posted_but_pending_guest_row_does_not(self):
        self.assertTrue(should_sync_review_payload({
            'type': 'host-to-guest',
            'status': 'pending',
        }))
        self.assertFalse(should_sync_review_payload({
            'type': 'guest-to-host',
            'status': 'pending',
        }))

    def test_submitted_and_published_directional_reviews_are_synchronized(self):
        self.assertTrue(should_sync_review_payload({
            'type': 'guest-to-host',
            'status': 'submitted',
        }))
        self.assertTrue(should_sync_review_payload({
            'type': 'host-to-guest',
            'status': 'published',
        }))

    def test_new_review_id_scan_stops_after_high_watermark(self):
        class Client:
            def __init__(self):
                self.calls = 0

            def get_reviews_page(self, **_kwargs):
                self.calls += 1
                return [
                    {'id': 105, 'type': 'guest-to-host', 'status': 'published'},
                    {'id': 100, 'type': 'host-to-guest', 'status': 'published'},
                ]

        client = Client()
        result = fetch_review_pages(
            client,
            sort_by='id',
            sort_order='desc',
            newer_than_review_id=100,
        )

        self.assertEqual([review['id'] for review in result], [105])
        self.assertEqual(client.calls, 1)

    def test_queue_uses_only_proven_submissions_for_each_origin(self):
        reservation = Reservation(reservation_id=1, listing_id=1)
        reservation.reviews = [
            Review(review_id=1, listing_id=1, origin='Guest', status='pending'),
            Review(review_id=2, listing_id=1, origin='Host', status='pending'),
        ]

        self.assertIsNone(_review_for_origin(reservation, 'Guest'))
        self.assertEqual(_review_for_origin(reservation, 'Host').review_id, 2)


class MessageSyncPolicyTests(unittest.TestCase):
    def test_conversation_activity_uses_hostaway_sent_and_received_fields(self):
        self.assertEqual(
            conversation_last_message_at({
                'messageSentOn': '2026-08-12 10:00:00',
                'messageReceivedOn': '2026-08-12 11:30:00',
                'updatedOn': '2026-08-13 12:00:00',
            }),
            datetime(2026, 8, 12, 11, 30),
        )

    def test_message_ids_are_normalized_and_missing_ids_use_stable_negative_space(self):
        created_at = datetime(2026, 8, 12, 11, 30)
        self.assertEqual(
            message_id_for_payload(10, {'id': '12345'}, created_at),
            12345,
        )
        payload = {'isIncoming': True, 'body': 'Hello'}
        first = message_id_for_payload(10, payload, created_at)
        second = message_id_for_payload(10, payload, created_at)
        self.assertLess(first, 0)
        self.assertEqual(first, second)


class GuestDeduplicationTests(unittest.TestCase):
    def test_all_guest_foreign_keys_are_reassigned(self):
        updated_models = []

        class Query:
            def __init__(self, model):
                self.model = model

            def filter(self, *_args):
                return self

            def update(self, values, synchronize_session=False):
                updated_models.append((self.model, values, synchronize_session))

        class Session:
            def query(self, model):
                return Query(model)

        reassign_guest_references(Session(), duplicate_guest_id=2, primary_guest_id=1)

        self.assertEqual(
            {entry[0] for entry in updated_models},
            {Reservation, Conversation, MessageMetadata, Review},
        )
        self.assertTrue(all(entry[1] == {'guest_id': 1} for entry in updated_models))


class SyncSchedulingTests(unittest.TestCase):
    @patch('sync.sync_manager.get_last_sync_time', return_value=datetime(2026, 8, 12, 12, 0))
    @patch('sync.sync_manager.SYNC_INCREMENTAL_DAILY', True)
    @patch('sync.sync_manager.SYNC_INTERVAL_HOURS', 24)
    @patch('sync.sync_manager.datetime')
    def test_configured_interval_is_respected(
        self,
        datetime_mock,
        _last_sync,
    ):
        datetime_mock.utcnow.return_value = datetime(2026, 8, 13, 11, 0)
        self.assertFalse(should_sync('reviews'))
        datetime_mock.utcnow.return_value = datetime(2026, 8, 13, 12, 0)
        self.assertTrue(should_sync('reviews'))


if __name__ == '__main__':
    unittest.main()
