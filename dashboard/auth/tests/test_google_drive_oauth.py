from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

from flask import Flask, session

import dashboard.auth.routes as auth_routes


def _test_app():
    app = Flask(__name__)
    app.secret_key = "test-secret"
    return app


def test_drive_oauth_reuses_the_registered_login_callback_by_default(monkeypatch):
    app = _test_app()
    app.add_url_rule('/auth/google/authorized', endpoint='google.authorized', view_func=lambda: 'login-callback')
    redirect_uri = "http://localhost/auth/google/authorized"
    monkeypatch.setattr(auth_routes.config, "GOOGLE_CLIENT_ID", "client-id")
    monkeypatch.setattr(auth_routes.config, "GOOGLE_CLIENT_SECRET", "client-secret")
    monkeypatch.setattr(auth_routes.config, "GOOGLE_DRIVE_OAUTH_REDIRECT_URI", None)
    monkeypatch.setattr(
        auth_routes,
        "get_current_user",
        lambda: SimpleNamespace(user_id=1, email="owner@example.com"),
    )

    with app.test_request_context("/auth/google-drive/connect?next=/bookkeeping/"):
        response = auth_routes.google_drive_connect.__wrapped__()
        query = parse_qs(urlparse(response.location).query)

    assert query["redirect_uri"] == [redirect_uri]


def test_drive_oauth_accepts_an_explicit_production_redirect(monkeypatch):
    app = _test_app()
    redirect_uri = "https://app.example.com/auth/google-drive/callback"
    monkeypatch.setattr(auth_routes.config, "GOOGLE_DRIVE_OAUTH_REDIRECT_URI", redirect_uri)

    with app.test_request_context("/"):
        assert auth_routes._google_drive_oauth_redirect_uri() == redirect_uri


def test_shared_google_callback_dispatches_drive_state_without_breaking_login(monkeypatch):
    app = _test_app()
    monkeypatch.setattr(auth_routes, "google_drive_callback_authorized", lambda: "drive-callback")
    monkeypatch.setattr(auth_routes, "handle_google_callback", lambda: "login-callback")

    with app.test_request_context("/auth/google/callback?state=drive-state"):
        session[auth_routes.GOOGLE_DRIVE_OAUTH_STATE_KEY] = "drive-state"
        assert auth_routes.google_callback() == "drive-callback"

    with app.test_request_context("/auth/google/callback?state=login-state"):
        assert auth_routes.google_callback() == "login-callback"


def test_registered_login_callback_is_intercepted_only_for_drive_state(monkeypatch):
    app = _test_app()
    app.add_url_rule('/auth/google/authorized', endpoint='google.authorized', view_func=lambda: 'login-callback')
    monkeypatch.setattr(auth_routes, "google_drive_callback_authorized", lambda: "drive-callback")

    with app.test_request_context('/auth/google/authorized?state=drive-state'):
        session[auth_routes.GOOGLE_DRIVE_OAUTH_STATE_KEY] = 'drive-state'
        assert auth_routes._intercept_google_authorized_drive_callback() == 'drive-callback'

    with app.test_request_context('/auth/google/authorized?state=login-state'):
        assert auth_routes._intercept_google_authorized_drive_callback() is None
