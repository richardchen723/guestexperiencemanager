"""Owner-started Gmail read-only consent, separate from sign-in and Drive.

Reuses the existing registered Google callback. Tokens stay server-side in a
private staging directory until an administrator binds systemd credentials.
"""
import json
import os
from pathlib import Path
import secrets
import stat
import tempfile
import time
from urllib.parse import urlencode

from flask import Blueprint, current_app, g, jsonify, redirect, render_template_string, request, session, url_for
import requests
from werkzeug.routing import BuildError

import dashboard.config as config
from dashboard.auth.session import get_current_user

MAILBOX = "larry@enchantedhavens.com"
SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
ORIGIN = "https://yourcottoncandy.com"
STATE_KEY = "inquiry_gmail_oauth_state"
PREFIX = "inquiry-gmail-"
bp = Blueprint("inquiry_gmail", __name__, url_prefix="/inquiries/gmail")


def owner():
    user = get_current_user()
    return bool(user and user.role == "owner" and user.is_approved and not user.deleted_at and not getattr(g, "api_key", None))


def staging():
    value = current_app.config.get("INQUIRY_GMAIL_AUTHORIZATION_DIR") or os.getenv("INQUIRY_GMAIL_AUTHORIZATION_DIR")
    if not value:
        raise ValueError("authorization_not_configured")
    p = Path(value)
    info = p.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_mode & 0o077 or info.st_uid != os.getuid():
        raise ValueError("authorization_directory_unavailable")
    return p


def api_reason(response):
    """Only allow known categories, never Google response text or credentials."""
    allowed = {"SERVICE_DISABLED", "ACCESS_TOKEN_SCOPE_INSUFFICIENT", "accessNotConfigured",
               "insufficientPermissions", "forbidden", "rateLimitExceeded", "invalid_grant", "invalid_client"}
    try:
        error = response.json().get("error", {})
        if isinstance(error, str):
            return error if error in allowed else "unclassified"
        for entry in error.get("errors", []) + error.get("details", []):
            if entry.get("reason") in allowed:
                return entry["reason"]
    except (ValueError, TypeError, AttributeError):
        pass
    return "unclassified"


def record_outcome(directory, code, diagnostics=None):
    data = {"code": code, "at": time.time(), "mailbox": MAILBOX,
            **(diagnostics or {})}
    current_app.logger.info("Inquiry Gmail authorization outcome=%s", code)
    temporary = None
    try:
        fd, temporary = tempfile.mkstemp(prefix="outcome-", dir=directory)
        with os.fdopen(fd, "w") as file:
            json.dump(data, file);file.flush();os.fsync(file.fileno())
        os.replace(temporary, directory / "last-outcome.json")
    except OSError:
        current_app.logger.warning("Inquiry Gmail outcome record unavailable")
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


@bp.after_request
def private(response):
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@bp.route("/connect", methods=["GET", "POST"])
def connect():
    if not owner():
        return "The Cotton Candy owner must sign in to connect this mailbox.", 403
    try:
        directory = staging()
        if not config.GOOGLE_CLIENT_ID or not config.GOOGLE_CLIENT_SECRET or url_for("google.authorized") != "/auth/google/authorized":
            raise ValueError("existing_oauth_client_unavailable")
    except (ValueError, OSError, BuildError):
        return "Gmail authorization is not configured on this server.", 503
    csrf = session.setdefault("inquiry_gmail_csrf", secrets.token_urlsafe(32))
    if request.method == "GET":
        return render_template_string("""<!doctype html><title>Connect Larry mailbox</title>
        <meta name="viewport" content="width=device-width,initial-scale=1">
        <main style="max-width:42rem;margin:3rem auto;padding:1rem;font:16px/1.6 system-ui">
        <h1>Connect Larry's inquiry mailbox</h1>
        <p>Grant this production server read-only Gmail access to <strong>{{mailbox}}</strong>.
        It will collect new inquiry emails and guest replies into Cotton Candy.
        This grant does not include sending, deleting, labelling or marking mail read.</p>
        <p>Choose the Larry account on Google's next screen. Intake activation remains a separate server step.</p>
        <form method="post"><input type="hidden" name="csrf" value="{{csrf}}">
        <button type="submit">Continue to Google consent</button></form>
        <p><a href="/inquiries/">Return to Guest Inquiry CRM</a></p></main>""", mailbox=MAILBOX, csrf=csrf)
    supplied = request.form.get("csrf", "")
    if not supplied or not secrets.compare_digest(csrf, supplied):
        return "Reload this page before connecting.", 403
    state = PREFIX + secrets.token_urlsafe(32)
    # Remove only this module's expired nonce files; never touch staged credentials.
    for old in directory.glob("state-" + PREFIX + "*"):
        info = old.lstat()
        if stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid() and time.time() - info.st_mtime > 600:
            old.unlink()
    state_path = directory / ("state-" + state)
    fd = os.open(state_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as file:
        json.dump({"created_at": time.time(), "owner_id": get_current_user().user_id}, file)
    session[STATE_KEY] = state
    # No sign-in/Drive scopes are combined into this authorization.
    return redirect("https://accounts.google.com/o/oauth2/v2/auth?" + urlencode({
        "client_id": config.GOOGLE_CLIENT_ID, "redirect_uri": ORIGIN + "/auth/google/authorized",
        "response_type": "code", "scope": SCOPE, "access_type": "offline", "prompt": "consent",
        "include_granted_scopes": "false", "login_hint": MAILBOX, "state": state,
    }))


def callback():
    state = request.args.get("state", "")
    expected = session.pop(STATE_KEY, "")
    if not owner() or not expected or not secrets.compare_digest(state, expected):
        return "Gmail authorization state expired or did not match. Start again.", 403
    try:
        directory = staging()
        state_path = directory / ("state-" + expected)
        info = state_path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_uid != os.getuid():
            raise ValueError()
        saved = json.loads(state_path.read_text())
        state_path.unlink()  # One use, including denied/failed exchanges.
        if saved["owner_id"] != get_current_user().user_id or not 0 <= time.time() - saved["created_at"] <= 600:
            raise ValueError()
    except (ValueError, KeyError, OSError):
        return "Gmail authorization state expired. Start again.", 403
    code = request.args.get("code")
    if request.args.get("error") or not code:
        record_outcome(directory, "consent_not_granted")
        return "Gmail access was not granted. No mailbox credential was changed.", 400
    failure = "token_exchange_failed"
    diagnostics = {}
    try:
        response = requests.post("https://oauth2.googleapis.com/token", data={
            "client_id": config.GOOGLE_CLIENT_ID, "client_secret": config.GOOGLE_CLIENT_SECRET,
            "code": code, "grant_type": "authorization_code", "redirect_uri": ORIGIN + "/auth/google/authorized",
        }, timeout=(10, 30), allow_redirects=False)
        diagnostics["token_http_status"] = response.status_code
        if response.status_code != 200:
            diagnostics["api_reason"] = api_reason(response)
            raise ValueError()
        token = response.json()
        failure = "scope_or_offline_token_missing"
        if set(token.get("scope", "").split()) != {SCOPE} or not token.get("refresh_token") or not token.get("access_token"):
            raise ValueError()
        failure = "gmail_profile_unavailable"
        profile = requests.get("https://gmail.googleapis.com/gmail/v1/users/me/profile",
                               headers={"Authorization": "Bearer " + token["access_token"]},
                               timeout=(10, 30), allow_redirects=False)
        diagnostics["profile_http_status"] = profile.status_code
        if profile.status_code != 200:
            diagnostics["api_reason"] = api_reason(profile)
            raise ValueError()
        failure = "wrong_mailbox_selected"
        if profile.json().get("emailAddress", "").lower() != MAILBOX:
            raise ValueError()
        credential = {"type": "authorized_user", "mailbox": MAILBOX, "scopes": [SCOPE],
                      "client_id": config.GOOGLE_CLIENT_ID, "client_secret": config.GOOGLE_CLIENT_SECRET,
                      "refresh_token": token["refresh_token"], "authorized_at": time.time()}
        failure = "credential_staging_failed"
        fd, temporary = tempfile.mkstemp(prefix="credential-", dir=directory)
        try:
            with os.fdopen(fd, "w") as file:
                json.dump(credential, file)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, directory / "larry-gmail.json")
            directory_fd = os.open(directory, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    except (ValueError, TypeError, OSError, requests.RequestException):
        record_outcome(directory, failure, diagnostics)
        return "Could not complete Larry's read-only authorization (" + failure + "). Start again or ask the administrator to review setup.", 400
    record_outcome(directory, "authorization_ready", {"scopes": [SCOPE]})
    return redirect(url_for("inquiry_gmail.connected"))


@bp.get("/status")
def status():
    if not owner():
        return "Owner sign-in required.", 403
    try:
        directory = staging()
        p = directory / "last-outcome.json"
        saved = json.loads(p.read_text()) if p.exists() else None
        if saved:
            saved = {k: saved[k] for k in ("code", "at", "mailbox", "scopes", "token_http_status", "profile_http_status", "api_reason") if k in saved}
        return jsonify(credential_staged=(directory / "larry-gmail.json").exists(), last_outcome=saved)
    except (ValueError, OSError):
        return jsonify(error="Authorization status unavailable"), 503


@bp.get("/connected")
def connected():
    if not owner():
        return "Owner sign-in required.", 403
    return "Larry's read-only Gmail authorization was verified and securely staged on this server. The intake worker can now be activated after its credential binding is checked."


def register_gmail_authorization(app):
    app.register_blueprint(bp)
    @app.before_request
    def inquiry_google_callback():
        # Existing sign-in and Drive callback handling stays in place for all other states.
        if request.endpoint == "google.authorized" and request.args.get("state", "").startswith(PREFIX):
            response = app.make_response(callback())
            return private(response)
