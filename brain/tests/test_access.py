from types import SimpleNamespace
import unittest

from brain.access import accessible_portfolio_ids, filter_portfolio_query, is_brain_admin


class BrainAccessTests(unittest.TestCase):
    def test_approved_user_gets_shared_operating_view(self):
        user = SimpleNamespace(user_id=7, role="user")

        self.assertIsNone(accessible_portfolio_ids(session=None, user=user))

    def test_dashboard_roles_do_not_change_brain_operating_view(self):
        for role in ("owner", "admin", "user", "manager"):
            with self.subTest(role=role):
                user = SimpleNamespace(user_id=7, role=role)

                self.assertIsNone(accessible_portfolio_ids(session=None, user=user))

    def test_dashboard_roles_do_not_limit_brain_controls(self):
        for role in ("owner", "admin", "user", "manager"):
            with self.subTest(role=role):
                user = SimpleNamespace(user_id=7, role=role)

                self.assertTrue(is_brain_admin(user))

        self.assertFalse(is_brain_admin(None))

    def test_missing_user_gets_no_portfolios(self):
        self.assertEqual(accessible_portfolio_ids(session=None, user=None), [])

    def test_empty_filter_blocks_results(self):
        class Query:
            def filter(self, value):
                return value

        self.assertFalse(filter_portfolio_query(Query(), SimpleNamespace(portfolio_id=1), []))


if __name__ == "__main__":
    unittest.main()
