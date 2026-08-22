import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import Mock, patch

import requests

from sync.api_client import (
    TOKEN_ACTIVATION_DELAY,
    HostawayAPIClient,
)


class HostawayTokenCacheTests(unittest.TestCase):
    def setUp(self):
        self.account_patch = patch('sync.api_client.HOSTAWAY_ACCOUNT_ID', 'account-1')
        self.key_patch = patch('sync.api_client.HOSTAWAY_API_KEY', 'secret-1')
        self.url_patch = patch('sync.api_client.HOSTAWAY_BASE_URL', 'https://hostaway.test/v1')
        self.account_patch.start()
        self.key_patch.start()
        self.url_patch.start()
        with HostawayAPIClient._token_lock:
            HostawayAPIClient._token_cache.clear()

    def tearDown(self):
        with HostawayAPIClient._token_lock:
            HostawayAPIClient._token_cache.clear()
        self.url_patch.stop()
        self.key_patch.stop()
        self.account_patch.stop()

    def make_client(self, token='token-1', expires_in=3600):
        client = HostawayAPIClient()
        response = Mock()
        response.json.return_value = {
            'access_token': token,
            'expires_in': expires_in,
        }
        session = Mock()
        session.post.return_value = response
        client._build_session = Mock(return_value=session)
        client._thread_local.session = session
        return client, session

    def test_new_token_waits_until_active_then_is_reused_by_new_client(self):
        first_client, first_session = self.make_client()
        cache_was_populated_during_wait = []

        def observe_activation_wait(seconds):
            self.assertEqual(seconds, TOKEN_ACTIVATION_DELAY)
            cache_was_populated_during_wait.append(
                first_client._token_cache_key in HostawayAPIClient._token_cache,
            )

        with patch('sync.api_client.time.sleep', side_effect=observe_activation_wait) as sleep_mock:
            first_token = first_client.get_access_token()
            second_client, second_session = self.make_client(token='should-not-be-requested')
            second_token = second_client.get_access_token()

        self.assertEqual(first_token, 'token-1')
        self.assertEqual(second_token, 'token-1')
        self.assertEqual(cache_was_populated_during_wait, [False])
        sleep_mock.assert_called_once_with(TOKEN_ACTIVATION_DELAY)
        first_session.post.assert_called_once()
        second_session.post.assert_not_called()

    def test_expired_cached_token_is_replaced(self):
        client, session = self.make_client(token='fresh-token')
        HostawayAPIClient._token_cache[client._token_cache_key] = ('expired-token', 0.0)

        with patch('sync.api_client.time.sleep') as sleep_mock:
            token = client.get_access_token()

        self.assertEqual(token, 'fresh-token')
        sleep_mock.assert_called_once_with(TOKEN_ACTIVATION_DELAY)
        session.post.assert_called_once()

    def test_concurrent_clients_share_one_token_refresh(self):
        first_client, first_session = self.make_client()
        second_client, second_session = self.make_client(token='should-not-be-requested')
        token_request_started = Event()
        release_token_response = Event()
        second_request_started = Event()
        token_response = first_session.post.return_value

        def delayed_token_response(*_args, **_kwargs):
            token_request_started.set()
            if not release_token_response.wait(timeout=2):
                raise AssertionError('Timed out waiting to release token response')
            return token_response

        def fetch_second_token():
            second_request_started.set()
            return second_client.get_access_token()

        first_session.post.side_effect = delayed_token_response
        with patch('sync.api_client.time.sleep'):
            with ThreadPoolExecutor(max_workers=2) as executor:
                first_future = executor.submit(first_client.get_access_token)
                self.assertTrue(token_request_started.wait(timeout=2))
                second_future = executor.submit(fetch_second_token)
                self.assertTrue(second_request_started.wait(timeout=2))
                release_token_response.set()
                first_token = first_future.result(timeout=2)
                second_token = second_future.result(timeout=2)

        self.assertEqual(first_token, 'token-1')
        self.assertEqual(second_token, 'token-1')
        first_session.post.assert_called_once()
        second_session.post.assert_not_called()

    def test_invalidation_does_not_remove_a_newer_shared_token(self):
        client, _session = self.make_client()
        HostawayAPIClient._token_cache[client._token_cache_key] = ('new-token', 9999999999.0)

        client._invalidate_access_token('old-token')
        self.assertIn(client._token_cache_key, HostawayAPIClient._token_cache)

        client._invalidate_access_token('new-token')
        self.assertNotIn(client._token_cache_key, HostawayAPIClient._token_cache)

    def test_forbidden_post_invalidates_token_without_retrying_message(self):
        client, session = self.make_client()
        HostawayAPIClient._token_cache[client._token_cache_key] = ('rejected-token', 9999999999.0)
        response = Mock(status_code=403)
        response.raise_for_status.side_effect = requests.exceptions.HTTPError(response=response)
        session.post.return_value = response

        with self.assertRaisesRegex(RuntimeError, r'Hostaway rejected.*\(403\)'):
            client._make_post_request('conversations/99/messages', {'body': 'Hello'})

        session.post.assert_called_once()
        self.assertNotIn(client._token_cache_key, HostawayAPIClient._token_cache)


if __name__ == '__main__':
    unittest.main()
