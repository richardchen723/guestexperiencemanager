import unittest
from unittest.mock import MagicMock, patch

from dashboard.auth.init import database_initialization_lock


class DatabaseInitializationLockTests(unittest.TestCase):
    def test_advisory_lock_connection_uses_autocommit(self):
        engine = MagicMock()
        connection = MagicMock()
        engine.connect.return_value = connection
        connection.execution_options.return_value = connection

        with patch("database.models.get_engine", return_value=engine):
            with database_initialization_lock():
                pass

        connection.execution_options.assert_called_once_with(
            isolation_level="AUTOCOMMIT"
        )
        statements = [call.args[0] for call in connection.exec_driver_sql.call_args_list]
        self.assertEqual(
            statements,
            [
                "SELECT pg_advisory_lock(779481504)",
                "SELECT pg_advisory_unlock(779481504)",
            ],
        )
        connection.close.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
