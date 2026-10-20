"""Native CRM UI and authenticated Larry intake API."""

import json
import logging
import os
import secrets
from functools import wraps

from flask import Blueprint, g, jsonify, render_template, request, session
from sqlalchemy import select, text
from werkzeug.exceptions import BadRequest, RequestEntityTooLarge

from dashboard.auth.decorators import check_feature_access
from dashboard.auth.features import user_can_access_feature
from dashboard.auth.models import get_all_users
from dashboard.auth.session import get_current_user
from database.models import Listing, Tag, get_session as main_session
import dashboard.config as config

from .models import SourceMessage, get_session
from .service import DEFAULT_SOURCES, GUEST_FIELDS, STATUSES, InquiryError, InquiryService


logger = logging.getLogger(__name__)
inquiries_bp = Blueprint("inquiries", __name__, url_prefix="/inquiries")


@inquiries_bp.before_request
def require_access():
    denied = check_feature_access("inquiries")
    if denied is not None:
        return denied
    # Legacy full-access API keys remain supported, while scoped keys are denied
    # by check_feature_access. Browser access uses the explicit CRM feature grant.
    if request.method not in ("GET", "HEAD", "OPTIONS") and not getattr(g, "api_key", None):
        expected = session.get("inquiries_csrf")
        supplied = request.headers.get("X-CSRF-Token", "")
        if not expected or not secrets.compare_digest(expected.encode(), supplied.encode()):
            return jsonify(error="Reload this page before saving; your session changed."), 403
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        if request.content_length and request.content_length > 1024 * 1024:
            return jsonify(error="This submission is too large."), 413
        # Flask 3.0 has no per-request max_content_length setter. Bound stream
        # reads as well as Content-Length so chunked requests cannot bypass it.
        raw = request.stream.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            return jsonify(error="This submission is too large."), 413
        try:
            g.inquiries_payload = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            return jsonify(error="A valid JSON object is required."), 400


@inquiries_bp.after_request
def private_response(response):
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


def reference_data():
    with main_session(config.MAIN_DATABASE_PATH) as db:
        properties = {p.listing_id: {"id": p.listing_id, "name": p.internal_listing_name or p.name or str(p.listing_id),
                     "aliases": list({v for v in (p.name, p.internal_listing_name) if v}), "tag_ids": [t.tag_id for t in p.tags]}
                      for p in db.query(Listing).all()}
        tags = {t.tag_id: {"id": t.tag_id, "name": t.name} for t in db.query(Tag).all()}
    users = {u.user_id: {"id": u.user_id, "name": u.name or u.email} for u in get_all_users()
             if u.is_approved and not u.deleted_at and u.email != config.API_SERVICE_EMAIL and user_can_access_feature(u, "inquiries")}
    return properties, tags, users


def configured_sources():
    raw = os.getenv("INQUIRY_EMAIL_SOURCES")
    try:
        sources = json.loads(raw) if raw is not None else DEFAULT_SOURCES
    except (ValueError, TypeError):
        raise InquiryError("Inquiry email sources need administrator configuration.", 503) from None
    if not isinstance(sources, list) or any(
        not isinstance(v, dict) or not all(isinstance(v.get(k), str) and v[k].strip() for k in ("id", "mailbox"))
        or not isinstance(v.get("subject_prefixes"), list) or not v["subject_prefixes"]
        or any(not isinstance(p, str) or not p.strip() for p in v["subject_prefixes"])
        or not isinstance(v.get("senders", []), list) or any(not isinstance(s, str) or not s.strip() for s in v.get("senders", []))
        for v in sources
    ):
        raise InquiryError("Inquiry email sources need administrator configuration.", 503)
    return sources


def admin_only():
    user = get_current_user()
    # A legacy API key authenticates as the service account, not the issuing admin.
    if not user or not user.is_admin() or getattr(g, "api_key", None):
        raise InquiryError("An administrator browser session is required.", 403)


def test_access():
    user = get_current_user()
    if not getattr(g, "api_key", None) and (not user or not user.is_admin()):
        raise InquiryError("An administrator is required for internal test records.", 403)


def endpoint(*, mutation=False):
    def decorator(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            db = get_session()
            try:
                user = get_current_user()
                properties, tags, users = reference_data()
                sources = configured_sources()
                if mutation and db.bind.dialect.name == "postgresql":
                    # Serializes ingestion/idempotency and version checks across workers.
                    db.execute(text("SELECT pg_advisory_xact_lock(813102026)"))
                service = InquiryService(db, properties=properties, tags=tags, users=users, sources=sources,
                                         actor="Larry" if getattr(g, "api_key", None) else user.name or user.email)
                result = fn(service, *args, **kwargs)
                if mutation:
                    db.commit()
                return jsonify(result)
            except InquiryError as exc:
                db.rollback()
                return jsonify(error=str(exc)), exc.status
            except (BadRequest, RequestEntityTooLarge) as exc:
                db.rollback()
                return jsonify(error="Invalid JSON or submission too large."), exc.code
            except Exception:
                db.rollback()
                logger.exception("Guest inquiry request failed")
                return jsonify(error="The inquiry could not be saved. Your original email remains in the inbox; retry this action."), 500
            finally:
                db.close()
        return wrapped
    return decorator


def payload():
    result = getattr(g, "inquiries_payload", None)
    if not isinstance(result, dict):
        raise InquiryError("A JSON object is required.")
    return result


@inquiries_bp.route("/")
def inquiry_page():
    session.setdefault("inquiries_csrf", secrets.token_urlsafe(32))
    return render_template("inquiries/index.html", inquiries_csrf=session["inquiries_csrf"])


@inquiries_bp.get("/api/bootstrap")
@endpoint()
def bootstrap(service):
    user = get_current_user()
    return {"properties": list(service.properties.values()), "tags": list(service.tags.values()),
            "users": list(service.users.values()), "statuses": STATUSES, "settings": service.settings_dict(),
            "can_administer": bool(user.is_admin() and not getattr(g, "api_key", None)),
            "sources": [{k: v.get(k) for k in ("id", "mailbox", "brand", "subject_prefixes")} for v in service.sources]}


@inquiries_bp.get("/api/leads")
@endpoint()
def leads(service):
    if request.args.get("record_scope") == "internal-tests":
        test_access()
    return service.listing(request.args)


@inquiries_bp.get("/api/worker-context")
@endpoint()
def worker_context(service):
    return {"sources": service.sources, "properties": list(service.properties.values()), "tags": list(service.tags.values()),
            "extraction_fields": sorted(GUEST_FIELDS), "timezone": service.settings().timezone,
            "contract": "Each source message needs mailbox, source, immutable message_id, thread_id, sender, subject, original body, receipt timestamp with offset and extraction. Extract only explicit guest information; preserve staff corrections through CRM conflict handling."}


@inquiries_bp.get("/api/leads/<int:lead_id>")
@endpoint()
def lead_detail(service, lead_id):
    return service.serialize(service.lead(lead_id), detail=True)


@inquiries_bp.patch("/api/leads/<int:lead_id>")
@endpoint(mutation=True)
def update_lead(service, lead_id):
    return service.patch(lead_id, payload())


@inquiries_bp.post("/api/intake")
@endpoint(mutation=True)
def intake(service):
    data = payload()
    if data.get("internal_test") is True:
        test_access()
    return service.ingest(data)


@inquiries_bp.get("/api/failures")
@endpoint()
def failures(service):
    admin_only()
    rows = service.db.scalars(select(SourceMessage).where(SourceMessage.processing_status == "failed").order_by(SourceMessage.id.desc()).limit(100))
    return {"failures": [{"id": m.id, "subject": m.subject, "body": m.body, "mailbox": m.mailbox,
                          "received_at": m.received_at.isoformat(), "error": m.error, "attempts": m.attempts,
                          "source_url": m.source_url} for m in rows]}


@inquiries_bp.post("/api/failures/<int:message_id>/retry")
@endpoint(mutation=True)
def retry(service, message_id):
    admin_only()
    message = service.db.get(SourceMessage, message_id)
    if not message or message.processing_status != "failed":
        raise InquiryError("Unresolved message not found.", 404)
    data = {k: getattr(message, k) for k in ("source", "mailbox", "message_id", "thread_id", "internet_message_id", "in_reply_to", "sender", "subject", "body")}
    data["received_at"] = message.received_at.isoformat()
    data["extraction"] = payload().get("extraction")
    data["internal_test"] = message.is_test
    return service.ingest(data)


@inquiries_bp.patch("/api/settings")
@endpoint(mutation=True)
def settings(service):
    admin_only()
    return service.update_settings(payload())


def register_inquiry_routes(app):
    app.register_blueprint(inquiries_bp)
    from .gmail_authorization import register_gmail_authorization
    register_gmail_authorization(app)
