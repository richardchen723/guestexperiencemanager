import logging
import unittest

from utils.logging_config import SENSITIVE_THIRD_PARTY_LOGGERS, suppress_sensitive_third_party_logs


class LoggingConfigTests(unittest.TestCase):
    def test_sensitive_third_party_loggers_are_warning_or_higher(self):
        for logger_name in SENSITIVE_THIRD_PARTY_LOGGERS:
            logging.getLogger(logger_name).setLevel(logging.DEBUG)

        suppress_sensitive_third_party_logs()

        for logger_name in SENSITIVE_THIRD_PARTY_LOGGERS:
            self.assertGreaterEqual(logging.getLogger(logger_name).level, logging.WARNING)


if __name__ == "__main__":
    unittest.main()
