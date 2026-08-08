import os
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from flask import Flask
from sqlalchemy import create_engine, inspect, text

from dashboard.auth import admin_routes
from dashboard.auth.models import (
    ApiKey,
    User,
    UserFeaturePermission,
    UserGoogleDriveCredential,
    _migrate_user_soft_delete_field,
    delete_user,
)
from dashboard.tickets.models import ReviewPortfolioRule, ReviewQueueState, Ticket


class FakeQuery:
    def __init__(self, first_result=None):
        self.first_result = first_result
        self.update_calls = []
        self.delete_calls = []

    def filter(self, *_args, **_kwargs):
        return self

    def first(self):
        return self.first_result

    def update(self, values, **kwargs):
        self.update_calls.append((values, kwargs))
        return 1

    def delete(self, **kwargs):
        self.delete_calls.append(kwargs)
        return 1


class UserDeletionTests(unittest.TestCase):
    def test_delete_user_anonymizes_account_without_deleting_history_row(self):
        user = SimpleNamespace(
            user_id=3,
            email='hillarychen2025@gmail.com',
            name='Hillary Chen',
            picture_url='https://example.com/hillary.jpg',
            google_id='google-3',
            role='user',
            is_approved=False,
            approved_at=None,
            approved_by=1,
            last_login=None,
            whatsapp_number='+15555550123',
            whatsapp_notifications_enabled=True,
            deleted_at=None,
        )
        queries = {
            User: FakeQuery(first_result=user),
            Ticket: FakeQuery(),
            ReviewQueueState: FakeQuery(),
            ReviewPortfolioRule: FakeQuery(),
            UserGoogleDriveCredential: FakeQuery(),
            UserFeaturePermission: FakeQuery(),
            ApiKey: FakeQuery(),
        }
        session = MagicMock()
        session.query.side_effect = lambda model: queries[model]

        with patch('dashboard.auth.models.get_session', return_value=session):
            self.assertTrue(delete_user(3))

        self.assertEqual(user.email, 'deleted-user-3@deleted.invalid')
        self.assertEqual(user.name, 'Deleted user')
        self.assertIsNone(user.google_id)
        self.assertFalse(user.is_approved)
        self.assertFalse(user.whatsapp_notifications_enabled)
        self.assertIsNotNone(user.deleted_at)
        self.assertEqual(len(queries[Ticket].update_calls), 2)
        self.assertEqual(len(queries[ReviewQueueState].update_calls), 1)
        self.assertEqual(len(queries[ReviewPortfolioRule].update_calls), 1)
        self.assertEqual(len(queries[UserGoogleDriveCredential].delete_calls), 1)
        self.assertEqual(len(queries[UserFeaturePermission].delete_calls), 1)
        self.assertEqual(len(queries[UserFeaturePermission].update_calls), 1)
        self.assertEqual(len(queries[ApiKey].update_calls), 1)
        session.delete.assert_not_called()
        session.commit.assert_called_once_with()
        session.rollback.assert_not_called()
        session.close.assert_called_once_with()

    def test_soft_delete_migration_is_idempotent_for_existing_database(self):
        engine = create_engine('sqlite:///:memory:')
        with engine.begin() as conn:
            conn.execute(text(
                'CREATE TABLE users (user_id INTEGER PRIMARY KEY, email TEXT NOT NULL)'
            ))

        with patch.dict(os.environ, {'DATABASE_URL': ''}):
            _migrate_user_soft_delete_field(engine)
            _migrate_user_soft_delete_field(engine)

        column_names = {column['name'] for column in inspect(engine).get_columns('users')}
        index_names = {index['name'] for index in inspect(engine).get_indexes('users')}
        self.assertIn('deleted_at', column_names)
        self.assertIn('ix_users_deleted_at', index_names)


class UserDeletionRouteTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)

    def test_delete_route_returns_safe_success_message(self):
        current_user = SimpleNamespace(user_id=1, role='owner')
        target_user = SimpleNamespace(user_id=3, role='user')
        with self.app.test_request_context('/admin/api/users/3', method='DELETE'):
            with (
                patch.object(admin_routes, 'get_current_user', return_value=current_user),
                patch.object(admin_routes, 'get_user_by_id', return_value=target_user),
                patch.object(admin_routes, 'delete_user', return_value=True),
            ):
                response = admin_routes.api_delete_user.__wrapped__(3)

        self.assertTrue(response.get_json()['success'])
        self.assertIn('Historical work has been retained', response.get_json()['message'])

    def test_delete_route_does_not_expose_database_exception(self):
        current_user = SimpleNamespace(user_id=1, role='owner')
        target_user = SimpleNamespace(user_id=3, role='user')
        with self.app.test_request_context('/admin/api/users/3', method='DELETE'):
            with (
                patch.object(admin_routes, 'get_current_user', return_value=current_user),
                patch.object(admin_routes, 'get_user_by_id', return_value=target_user),
                patch.object(
                    admin_routes,
                    'delete_user',
                    side_effect=Exception('secret SQL constraint details'),
                ),
            ):
                response, status = admin_routes.api_delete_user.__wrapped__(3)

        self.assertEqual(status, 500)
        self.assertNotIn('secret SQL', response.get_json()['error'])


if __name__ == '__main__':
    unittest.main()
