import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from flask import Flask, g
from sqlalchemy import create_engine, inspect, text

from dashboard.auth import admin_routes, api_keys, decorators
from dashboard.auth.api_keys import GUEST_ISSUES_READ_SCOPE, normalize_api_key_scopes
from dashboard.auth.decorators import owner_email_required
from dashboard.auth.models import ApiKey, _migrate_api_key_scopes


class ApiKeyQuery:
    def __init__(self, keys=None, first_result=None):
        self.keys = list(keys or [])
        self.first_result = first_result

    def order_by(self, *_args):
        return self

    def filter(self, *_args):
        return self

    def all(self):
        return self.keys

    def first(self):
        return self.first_result


class OwnerEmailRequiredTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config['SECRET_KEY'] = 'test-secret'

        @self.app.route('/admin/api/api-keys/probe')
        @owner_email_required
        def owner_probe():
            return {'success': True}

    def test_configured_owner_email_is_allowed_case_insensitively(self):
        owner = SimpleNamespace(email='Yunhang.Chen@gmail.com')
        with (
            patch.object(decorators, 'is_logged_in', return_value=True),
            patch.object(decorators, 'is_approved', return_value=True),
            patch.object(decorators, 'get_current_user', return_value=owner),
        ):
            response = self.app.test_client().get('/admin/api/api-keys/probe')

        self.assertEqual(response.status_code, 200)

    def test_other_admin_email_is_denied(self):
        admin = SimpleNamespace(email='admin@example.com')
        with (
            patch.object(decorators, 'is_logged_in', return_value=True),
            patch.object(decorators, 'is_approved', return_value=True),
            patch.object(decorators, 'get_current_user', return_value=admin),
        ):
            response = self.app.test_client().get('/admin/api/api-keys/probe')

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json()['error'], 'Owner access required')

    def test_api_key_without_owner_browser_session_is_denied(self):
        with patch.object(decorators, 'is_logged_in', return_value=False):
            response = self.app.test_client().get('/admin/api/api-keys/probe')

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.get_json()['error'], 'Authentication required')


class ApiKeyManagementRouteTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)

    def test_list_returns_metadata_without_hash(self):
        key = SimpleNamespace(
            api_key_id=4,
            name='Codex integration',
            key_prefix='hk_example12',
            key_hash='must-never-be-returned',
            created_by=1,
            created_at=datetime(2026, 8, 29, 10, 0),
            last_used_at=None,
            revoked_at=None,
            is_active=True,
        )
        session = MagicMock()
        session.query.return_value = ApiKeyQuery(keys=[key])

        with self.app.test_request_context('/admin/api/api-keys'):
            with patch.object(admin_routes, 'get_session', return_value=session):
                response = admin_routes.api_list_api_keys.__wrapped__()

        payload = response.get_json()[0]
        self.assertEqual(payload['key_prefix'], 'hk_example12')
        self.assertNotIn('key_hash', payload)
        self.assertNotIn('must-never-be-returned', response.get_data(as_text=True))
        session.close.assert_called_once_with()

    def test_create_accepts_read_only_guest_issue_access(self):
        user = SimpleNamespace(user_id=3)
        with self.app.test_request_context(
            '/admin/api/api-keys',
            method='POST',
            json={'name': 'Guest agent', 'access': 'guest_issues_read'},
        ):
            with (
                patch.object(admin_routes, 'get_current_user', return_value=user),
                patch.object(admin_routes, 'create_api_key', return_value='raw-key') as create,
            ):
                response, status = admin_routes.api_create_api_key.__wrapped__()

        self.assertEqual(status, 201)
        self.assertEqual(response.get_json()['scopes'], [GUEST_ISSUES_READ_SCOPE])
        create.assert_called_once_with(
            name='Guest agent',
            created_by=3,
            scopes=[GUEST_ISSUES_READ_SCOPE],
        )

    def test_create_requires_a_name(self):
        with self.app.test_request_context(
            '/admin/api/api-keys',
            method='POST',
            json={'name': '   '},
        ):
            response, status = admin_routes.api_create_api_key.__wrapped__()

        self.assertEqual(status, 400)
        self.assertEqual(response.get_json()['error'], 'Key name is required')

    def test_delete_removes_the_selected_key(self):
        key = SimpleNamespace(api_key_id=7, name='Old integration')
        session = MagicMock()
        session.query.return_value = ApiKeyQuery(first_result=key)

        with self.app.test_request_context('/admin/api/api-keys/7', method='DELETE'):
            with patch.object(admin_routes, 'get_session', return_value=session):
                response = admin_routes.api_delete_api_key.__wrapped__(7)

        self.assertTrue(response.get_json()['success'])
        session.delete.assert_called_once_with(key)
        session.commit.assert_called_once_with()
        session.rollback.assert_not_called()
        session.close.assert_called_once_with()


class ApiKeyScopeTests(unittest.TestCase):
    def test_full_key_allows_legacy_and_guest_issue_scopes(self):
        key = ApiKey(scopes_json='["*"]')
        self.assertTrue(key.allows_scope('legacy:full'))
        self.assertTrue(key.allows_scope(GUEST_ISSUES_READ_SCOPE))

    def test_guest_issue_key_cannot_use_legacy_api(self):
        key = ApiKey(scopes_json='["guest_issues:read"]')
        self.assertTrue(key.allows_scope(GUEST_ISSUES_READ_SCOPE))
        self.assertFalse(key.allows_scope('legacy:full'))

    def test_scope_normalization_rejects_unknown_access(self):
        with self.assertRaises(ValueError):
            normalize_api_key_scopes(['tickets:write'])

    def test_verified_key_is_refreshed_before_it_is_detached(self):
        key = ApiKey(
            api_key_id=2,
            key_prefix='hk_example',
            key_hash='hash',
            scopes_json='["guest_issues:read"]',
            is_active=True,
        )
        query = ApiKeyQuery(first_result=key)
        session = MagicMock()
        session.query.return_value = query
        with (
            patch.object(api_keys, '_hash_api_key', return_value='hash'),
            patch.object(api_keys, 'get_session', return_value=session),
        ):
            result = api_keys.verify_api_key('raw-key')

        self.assertIs(result, key)
        session.refresh.assert_called_once_with(key)
        session.expunge.assert_called_once_with(key)

    def test_restricted_key_is_denied_by_legacy_api_decorator(self):
        app = Flask(__name__)

        @app.route('/tickets/api/probe')
        @decorators.approved_required
        def probe():
            return {'success': True}

        def deny_legacy_scope():
            g.api_key_scope_denied = 'legacy:full'
            return None

        with (
            patch.object(
                decorators,
                'authenticate_request_api_key',
                side_effect=deny_legacy_scope,
            ),
            patch.object(decorators, 'is_logged_in', return_value=False),
        ):
            response = app.test_client().get('/tickets/api/probe')

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json()['required_scope'], 'legacy:full')

    def test_migration_preserves_existing_keys_as_full_access(self):
        engine = create_engine('sqlite:///:memory:')
        with engine.begin() as connection:
            connection.execute(text('''
                CREATE TABLE api_keys (
                    api_key_id INTEGER PRIMARY KEY,
                    name TEXT,
                    key_prefix TEXT NOT NULL,
                    key_hash TEXT NOT NULL,
                    is_active INTEGER NOT NULL
                )
            '''))
            connection.execute(text('''
                INSERT INTO api_keys (api_key_id, name, key_prefix, key_hash, is_active)
                VALUES (1, 'Existing', 'hk_existing', 'hash', 1)
            '''))

        with patch('dashboard.auth.models.os.getenv', return_value=None):
            _migrate_api_key_scopes(engine)

        self.assertIn('scopes_json', {column['name'] for column in inspect(engine).get_columns('api_keys')})
        with engine.connect() as connection:
            scopes = connection.execute(text(
                'SELECT scopes_json FROM api_keys WHERE api_key_id = 1'
            )).scalar()
        self.assertEqual(scopes, '["*"]')


if __name__ == '__main__':
    unittest.main()
