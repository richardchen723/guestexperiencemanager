"""Owner consent tests with fake OAuth responses; no real credentials/network."""
import importlib.util
import json
import os
from pathlib import Path
import re
import sys
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

from flask import Flask, g
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, os.environ.get("INQUIRY_WORKER_PROJECT", str(ROOT)))
spec = importlib.util.spec_from_file_location("inquiry_gmail_authorization", ROOT / "dashboard/inquiries/gmail_authorization.py")
a = importlib.util.module_from_spec(spec)
spec.loader.exec_module(a)


@pytest.fixture
def app(tmp_path, monkeypatch):
    folder = tmp_path / "private-auth"
    folder.mkdir(mode=0o700)
    app = Flask(__name__)
    app.secret_key = "offline-only"
    app.config.update(TESTING=True, INQUIRY_GMAIL_AUTHORIZATION_DIR=str(folder))
    app.add_url_rule("/auth/google/authorized", endpoint="google.authorized", view_func=lambda: "existing-login")
    monkeypatch.setattr(a.config, "GOOGLE_CLIENT_ID", "offline-client")
    monkeypatch.setattr(a.config, "GOOGLE_CLIENT_SECRET", "offline-secret")
    app.qa_user = SimpleNamespace(role="owner", is_approved=True, deleted_at=None, user_id=1)
    monkeypatch.setattr(a, "get_current_user", lambda: app.qa_user)
    def forbidden(*args, **kwargs): raise AssertionError("No real OAuth traffic in offline tests")
    monkeypatch.setattr(a.requests, "post", forbidden)
    monkeypatch.setattr(a.requests, "get", forbidden)
    a.register_gmail_authorization(app)
    return app


def start(client):
    page = client.get("/inquiries/gmail/connect")
    assert page.status_code == 200
    csrf = re.search('name="csrf" value="([^"]+)"', page.text)[1]
    result = client.post("/inquiries/gmail/connect", data={"csrf": csrf})
    assert result.status_code == 302
    return parse_qs(urlsplit(result.location).query)


def fake_oauth(monkeypatch, *, mailbox=a.MAILBOX, scopes=a.SCOPE, refresh="offline-refresh"):
    def post(url, **kw):
        assert url == "https://oauth2.googleapis.com/token" and not kw["allow_redirects"]
        return SimpleNamespace(status_code=200, json=lambda: {"scope": scopes, "refresh_token": refresh, "access_token": "offline-access"})
    def get(url, **kw):
        assert url == "https://gmail.googleapis.com/gmail/v1/users/me/profile" and not kw["allow_redirects"]
        return SimpleNamespace(status_code=200, json=lambda: {"emailAddress": mailbox})
    monkeypatch.setattr(a.requests, "post", post)
    monkeypatch.setattr(a.requests, "get", get)


def test_consent_requests_exact_mailbox_readonly_offline_scope(app):
    query = start(app.test_client())
    assert query["scope"] == [a.SCOPE] and query["login_hint"] == [a.MAILBOX]
    assert query["access_type"] == ["offline"] and query["include_granted_scopes"] == ["false"]
    assert query["redirect_uri"] == ["https://yourcottoncandy.com/auth/google/authorized"]


def test_verified_callback_stages_private_refresh_only_and_cannot_replay(app, monkeypatch):
    client = app.test_client()
    state = start(client)["state"][0]
    fake_oauth(monkeypatch)
    result = client.get("/auth/google/authorized", query_string={"state": state, "code": "offline-code"})
    assert result.status_code == 302 and result.location == "/inquiries/gmail/connected"
    p = Path(app.config["INQUIRY_GMAIL_AUTHORIZATION_DIR"]) / "larry-gmail.json"
    credential = json.loads(p.read_text())
    assert credential["mailbox"] == a.MAILBOX and credential["scopes"] == [a.SCOPE]
    assert "access_token" not in credential and p.stat().st_mode & 0o777 == 0o600
    assert client.get("/auth/google/authorized", query_string={"state": state, "code": "offline-code"}).status_code == 403


@pytest.mark.parametrize("mailbox,scope,refresh", [("other@example.invalid", a.SCOPE, "offline"),
                                                  (a.MAILBOX, a.SCOPE + " https://www.googleapis.com/auth/gmail.send", "offline"),
                                                  (a.MAILBOX, a.SCOPE, None)])
def test_wrong_account_broader_scope_or_missing_offline_token_never_stored(app, monkeypatch, mailbox, scope, refresh):
    client = app.test_client()
    state = start(client)["state"][0]
    fake_oauth(monkeypatch, mailbox=mailbox, scopes=scope, refresh=refresh)
    assert client.get("/auth/google/authorized", query_string={"state": state, "code": "offline"}).status_code == 400
    assert not (Path(app.config["INQUIRY_GMAIL_AUTHORIZATION_DIR"]) / "larry-gmail.json").exists()


def test_existing_signin_and_drive_callbacks_are_untouched(app):
    client = app.test_client()
    assert client.get("/auth/google/authorized", query_string={"state": "existing-other-state"}).text == "existing-login"


def test_non_owner_csrf_and_forged_state_are_denied(app):
    client = app.test_client()
    assert client.post("/inquiries/gmail/connect", data={}).status_code == 403
    assert client.get("/auth/google/authorized", query_string={"state": a.PREFIX + "forged", "code": "x"}).status_code == 403
    app.qa_user.role = "admin"
    assert client.get("/inquiries/gmail/connect").status_code == 403


def test_api_key_cannot_start_mailbox_consent(app):
    @app.before_request
    def api_key(): g.api_key = object()
    assert app.test_client().get("/inquiries/gmail/connect").status_code == 403


def test_denied_consent_is_consumed_and_does_not_change_existing_staged_credential(app):
    client = app.test_client()
    state = start(client)["state"][0]
    p = Path(app.config["INQUIRY_GMAIL_AUTHORIZATION_DIR"]) / "larry-gmail.json"
    p.write_text("existing-offline-value");p.chmod(0o600)
    assert client.get("/auth/google/authorized", query_string={"state": state, "error": "access_denied"}).status_code == 400
    assert p.read_text() == "existing-offline-value"


def test_expired_state_cannot_exchange_code(app, monkeypatch):
    client = app.test_client()
    state = start(client)["state"][0]
    p = Path(app.config["INQUIRY_GMAIL_AUTHORIZATION_DIR"]) / ("state-" + state)
    saved = json.loads(p.read_text());saved["created_at"] -= 601;p.write_text(json.dumps(saved))
    assert client.get("/auth/google/authorized", query_string={"state": state, "code": "offline"}).status_code == 403
    assert not p.exists()


def test_status_reports_success_without_any_credentials(app, monkeypatch):
    client = app.test_client()
    state = start(client)["state"][0]
    fake_oauth(monkeypatch)
    client.get("/auth/google/authorized", query_string={"state": state, "code": "offline"})
    result = client.get("/inquiries/gmail/status")
    assert result.json["credential_staged"] and result.json["last_outcome"]["code"] == "authorization_ready"
    assert all(v not in result.text for v in ("offline-refresh", "offline-access", "offline-secret", "offline-client"))


def test_profile_failure_records_only_fixed_safe_diagnostics(app, monkeypatch):
    client = app.test_client()
    state = start(client)["state"][0]
    fake_oauth(monkeypatch)
    monkeypatch.setattr(a.requests, "get", lambda *args, **kwargs: SimpleNamespace(status_code=403, json=lambda: {
        "error": {"message": "private error text must not persist", "details": [{"reason": "SERVICE_DISABLED"}]}}))
    response = client.get("/auth/google/authorized", query_string={"state": state, "code": "offline"})
    assert response.status_code == 400
    result = client.get("/inquiries/gmail/status")
    assert result.json["last_outcome"]["code"] == "gmail_profile_unavailable"
    assert result.json["last_outcome"]["profile_http_status"] == 403
    assert result.json["last_outcome"]["api_reason"] == "SERVICE_DISABLED"
    assert not result.json["credential_staged"]
    assert "private error text" not in result.text
