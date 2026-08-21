import unittest
from types import SimpleNamespace
from unittest.mock import patch

from flask import Flask

from dashboard.auth.decorators import check_feature_access
from dashboard.auth.features import (
    FEATURES,
    assigned_feature_access,
    effective_feature_access,
    first_accessible_endpoint,
    normalize_feature_access,
)


def user(*, role='user', approved=True, user_id=17):
    return SimpleNamespace(user_id=user_id, role=role, is_approved=approved)


class FeatureAccessTests(unittest.TestCase):
    @patch('dashboard.auth.features.get_user_feature_overrides', return_value={17: {}})
    def test_regular_users_keep_standard_features_by_default(self, _mock_overrides):
        access = effective_feature_access(user())

        self.assertTrue(access['dashboard'])
        self.assertTrue(access['listing_audit'])
        self.assertTrue(access['properties'])
        self.assertTrue(access['tickets'])
        self.assertTrue(access['reviews'])
        self.assertTrue(access['knowledge'])
        self.assertTrue(access['sync'])
        self.assertFalse(access['bookkeeping'])
        self.assertFalse(access['boost'])
        self.assertFalse(access['activities'])

    def test_explicit_overrides_can_grant_and_revoke_features(self):
        access = assigned_feature_access(user(), overrides={
            'dashboard': False,
            'bookkeeping': True,
        })

        self.assertFalse(access['dashboard'])
        self.assertTrue(access['bookkeeping'])
        self.assertTrue(access['tickets'])

    def test_admins_and_owners_always_receive_all_features(self):
        for role in ('admin', 'owner'):
            access = effective_feature_access(user(role=role))
            self.assertEqual(set(access), set(FEATURES))
            self.assertTrue(all(access.values()))

    def test_unapproved_accounts_have_no_effective_access(self):
        access = effective_feature_access(
            user(approved=False),
            overrides={'bookkeeping': True},
        )
        self.assertFalse(any(access.values()))

    def test_normalization_creates_an_explicit_full_access_map(self):
        access = normalize_feature_access(['tickets', 'bookkeeping'])
        self.assertEqual(set(access), set(FEATURES))
        self.assertTrue(access['tickets'])
        self.assertTrue(access['bookkeeping'])
        self.assertFalse(access['dashboard'])

    def test_unknown_feature_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Unknown feature permission'):
            normalize_feature_access(['bookkeeping', 'super-admin'])

    @patch('dashboard.auth.features.get_user_feature_overrides')
    def test_first_landing_page_respects_restricted_access(self, mock_overrides):
        mock_overrides.return_value = {
            17: {feature_key: feature_key == 'bookkeeping' for feature_key in FEATURES}
        }
        self.assertEqual(first_accessible_endpoint(user()), 'bookkeeping.bookkeeping_page')

    @patch('dashboard.auth.decorators.authenticate_request_api_key', return_value=False)
    @patch('dashboard.auth.decorators.user_can_access_feature', return_value=False)
    @patch('dashboard.auth.decorators.get_current_user')
    @patch('dashboard.auth.decorators.is_approved', return_value=True)
    @patch('dashboard.auth.decorators.is_logged_in', return_value=True)
    def test_feature_denial_is_json_for_nested_api_routes(
        self,
        _mock_logged_in,
        _mock_approved,
        mock_current_user,
        _mock_can_access,
        _mock_api_key,
    ):
        mock_current_user.return_value = user()
        app = Flask(__name__)
        with app.test_request_context('/bookkeeping/api/reference-data'):
            response, status = check_feature_access('bookkeeping')

        self.assertEqual(status, 403)
        self.assertEqual(response.get_json()['feature'], 'bookkeeping')


if __name__ == '__main__':
    unittest.main()
