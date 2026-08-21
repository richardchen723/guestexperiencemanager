import os
import unittest

os.environ.setdefault("DATABASE_URL", "postgresql://user:pass@localhost/db")
os.environ.setdefault("OPENAI_API_KEY", "test")
os.environ.setdefault("SECRET_KEY", "test-secret")

import dashboard.config as config
from brain import app as brain_app


class BrainAppConfigTests(unittest.TestCase):
    def test_insecure_oauth_flag_sets_oauthlib_escape_hatch(self):
        original_debug = config.FLASK_DEBUG
        original_allow = config.BRAIN_ALLOW_INSECURE_OAUTH
        original_env = os.environ.get("OAUTHLIB_INSECURE_TRANSPORT")
        try:
            config.FLASK_DEBUG = False
            config.BRAIN_ALLOW_INSECURE_OAUTH = True
            os.environ.pop("OAUTHLIB_INSECURE_TRANSPORT", None)

            brain_app._configure_oauth_transport()

            self.assertEqual(os.environ.get("OAUTHLIB_INSECURE_TRANSPORT"), "1")
        finally:
            config.FLASK_DEBUG = original_debug
            config.BRAIN_ALLOW_INSECURE_OAUTH = original_allow
            if original_env is None:
                os.environ.pop("OAUTHLIB_INSECURE_TRANSPORT", None)
            else:
                os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = original_env


if __name__ == "__main__":
    unittest.main()
