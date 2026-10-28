import json
import secrets

from flask import Blueprint, g, jsonify, redirect, render_template, request, session, url_for
from sqlalchemy.exc import SQLAlchemyError, IntegrityError
from .models import get_session, Report
from .service import MAX_BYTES, ReportError, latest, query, rate, save, serialize

visibility_bp = Blueprint("visibility", __name__, url_prefix="/visibility")


def identity():
    # Reuse existing approved accounts and full-access API keys; create no grants.
    from dashboard.auth.api_keys import _get_api_key_from_request, verify_api_key
    from dashboard.auth.models import get_user_by_id
    from dashboard.auth.session import get_current_user
    from dashboard.auth.features import user_can_access_feature
    token = _get_api_key_from_request(request) if "/api/" in request.path else None
    key = verify_api_key(token) if token else None
    if token:
        if not key:
            raise ReportError("Authentication required.", 401)
        allowed = getattr(key, "allows_scope", None)
        if callable(allowed):
            full = bool(allowed("legacy:full"))
        elif hasattr(key, "scopes_json"):
            try:
                scopes = json.loads(key.scopes_json)
                full = isinstance(scopes, list) and "*" in scopes
            except (ValueError, TypeError):
                full = False
        else:
            full = True  # Legacy keys were explicitly full-access.
        if not full:
            raise ReportError("An existing full-access key is required.", 403)
        user = get_user_by_id(key.created_by) if key.created_by else None
    else:
        user = get_current_user()
    if not user:
        raise ReportError("Authentication required.", 401)
    if not user.is_approved or user.deleted_at or not user.is_admin() or not user_can_access_feature(user, "listing_audit"):
        raise ReportError("Approved administrator and Listing Health access required.", 403)
    return f"user:{user.user_id}" + (f" / api-key:{key.api_key_id}" if key else ""), bool(key)


@visibility_bp.before_request
def access():
    try:
        g.visibility_actor, key = identity()
        if request.method == "POST" and not key:
            expected = session.get("visibility_csrf", "")
            supplied = request.headers.get("X-CSRF-Token", "")
            if not expected or not secrets.compare_digest(expected.encode(), supplied.encode()):
                raise ReportError("Reload the report page before saving.", 403)
        if request.content_length and request.content_length > MAX_BYTES + 1000:
            raise ReportError("Report exceeds the 2 MB limit.", 413)
    except ReportError as exc:
        if exc.status == 401 and "/api/" not in request.path:
            return redirect(url_for("auth.login"))
        return jsonify(error=str(exc)), exc.status


@visibility_bp.after_request
def private(response):
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["X-Robots-Tag"] = "noindex, nofollow"
    return response


def run(fn, write=False):
    db = get_session()
    try:
        result = fn(db)
        if write:
            db.commit()
        return result
    except ReportError as exc:
        db.rollback()
        return jsonify(error=str(exc)), exc.status
    except IntegrityError:
        db.rollback()
        return jsonify(error="A concurrent record was saved. Refresh before retrying."), 409
    except SQLAlchemyError:
        db.rollback()
        if "/api/" in request.path:
            return jsonify(error="Reports are temporarily unavailable. Please retry."), 503
        return render_template("visibility/error.html"), 503
    finally:
        db.close()


@visibility_bp.get("/")
def index():
    try:
        page = int(request.args.get("page", 1))
        if not 1 <= page <= 1000:
            raise ValueError()
    except ValueError:
        return jsonify(error="Choose a valid report page."), 400
    session.setdefault("visibility_csrf", secrets.token_urlsafe(32))
    def load(db):
        records, count = latest(db, page)
        return render_template("visibility/index.html", records=records, count=count, page=page, rate=rate)
    return run(load)


def detail_data(db, record_id):
    row = db.scalar(query().where(Report.id == record_id))
    if not row:
        raise ReportError("Report not found.", 404)
    history = db.scalars(query().where(Report.week_start == row.week_start).order_by(Report.revision.desc())).all()
    return serialize(row), [{"id": r.id, "revision": r.revision, "created_at": r.created_at.isoformat()} for r in history]


@visibility_bp.get("/reports/<int:record_id>")
def detail(record_id):
    def load(db):
        report, history = detail_data(db, record_id)
        return render_template("visibility/detail.html", report=report, history=history, rate=rate)
    return run(load)


@visibility_bp.get("/api/reports")
def reports_api():
    def load(db):
        records, total = latest(db)
        return jsonify(reports=records, total=total)
    return run(load)


@visibility_bp.get("/api/reports/<int:record_id>")
def report_api(record_id):
    def load(db):
        report, history = detail_data(db, record_id)
        return jsonify(report=report, history=history)
    return run(load)


@visibility_bp.post("/api/reports")
def publish():
    def write(db):
        raw = request.stream.read(MAX_BYTES + 1001)
        if len(raw) > MAX_BYTES + 1000:
            raise ReportError("Report exceeds the 2 MB limit.", 413)
        try:
            body = json.loads(raw) if request.is_json else None
        except (ValueError, UnicodeDecodeError):
            body = None
        if not isinstance(body, dict) or set(body) != {"report", "expected_revision"}:
            raise ReportError("Provide report and expected_revision as JSON.")
        return jsonify(save(db, body["report"], request.headers.get("Idempotency-Key"), body["expected_revision"], g.visibility_actor))
    return run(write, write=True)


def register_visibility_routes(app):
    app.register_blueprint(visibility_bp)
