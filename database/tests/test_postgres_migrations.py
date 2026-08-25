import os
import unittest
from unittest.mock import MagicMock, patch

from database.models import _migrate_review_private_feedback_column


class PostgresMigrationTests(unittest.TestCase):
    @staticmethod
    def _engine_with_scalar_results(*values):
        engine = MagicMock()
        connection = engine.begin.return_value.__enter__.return_value
        results = []
        for value in values:
            result = MagicMock()
            result.scalar.return_value = value
            results.append(result)
        connection.execute.side_effect = results
        return engine, connection

    def test_existing_private_feedback_column_skips_alter_table(self):
        engine, connection = self._engine_with_scalar_results(True, True)

        with patch.dict(os.environ, {"DATABASE_URL": "postgresql://example"}):
            _migrate_review_private_feedback_column(engine)

        statements = [str(call.args[0]) for call in connection.execute.call_args_list]
        self.assertFalse(any("ALTER TABLE" in statement for statement in statements))
        self.assertFalse(any("lock_timeout" in statement for statement in statements))

    def test_missing_private_feedback_column_uses_bounded_lock_wait(self):
        engine, connection = self._engine_with_scalar_results(True, False, None, None)

        with patch.dict(os.environ, {"DATABASE_URL": "postgresql://example"}):
            _migrate_review_private_feedback_column(engine)

        statements = [str(call.args[0]) for call in connection.execute.call_args_list]
        self.assertIn("SET LOCAL lock_timeout = '5s'", statements[-2])
        self.assertIn("ALTER TABLE public.reviews", statements[-1])


if __name__ == "__main__":
    unittest.main()
