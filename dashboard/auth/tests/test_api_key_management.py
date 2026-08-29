import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from flask import Flask

from dashboard.auth import admin_routes, decorators
from dashboard.auth.decorators import owner_email_required


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


if __name__ == '__main__':
    unittest.main()
