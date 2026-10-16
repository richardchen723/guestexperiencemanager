"""Authenticated procurement UI and idempotent integration API."""

import hashlib
import csv
import io
import json
import logging
import secrets
from datetime import datetime
from functools import wraps

from flask import Blueprint, Response, g, jsonify, render_template, request, send_file, session as browser_session
from sqlalchemy import or_, select, text
from sqlalchemy.exc import IntegrityError
from werkzeug.utils import secure_filename

from dashboard.auth.decorators import check_feature_access
from dashboard.auth.models import get_all_users
from dashboard.auth.features import user_can_access_feature
from dashboard.auth.session import get_current_user
from database.models import Listing, get_session as get_main_session
import dashboard.config as config

from .models import Attachment, AuditEvent, CatalogItem, Command, Location, PurchaseOrder, RequestItem, Staff, Ticket, get_session, init_procurement_database
from .service import CURRENCIES, PRIORITIES, STATUS_LABELS, ProcurementService, WorkflowError, get, required, ticket_properties, ticket_status, request_query, order_query

logger = logging.getLogger(__name__)
procurement_bp = Blueprint("procurement", __name__, url_prefix="/procurement")


@procurement_bp.before_request
def require_access():
    denied = check_feature_access("procurement")
    if denied is not None:
        return denied
    if request.content_length and request.content_length > (11 * 1024 * 1024 if request.path.endswith('/receipts') else 1024 * 1024):
        return jsonify(error="This submission is too large."), 413
    if request.method not in ("GET", "HEAD", "OPTIONS") and not getattr(g, "api_key", None):
        expected = browser_session.get("procurement_csrf")
        supplied = request.headers.get("X-CSRF-Token", "")
        if not expected or not secrets.compare_digest(expected.encode(), supplied.encode()):
            return jsonify(error="Your session changed. Reload the page before saving."), 403


def reference_data():
    """Use real Cotton Candy properties and approved accounts; no duplicate directory."""
    main = get_main_session(config.MAIN_DATABASE_PATH)
    try:
        properties = {
            p.listing_id: {"id": p.listing_id, "name": p.internal_listing_name or p.name or str(p.listing_id),
                           "city": p.city or "", "timezone": p.timezone_name or "UTC",
                           "tags": sorted(t.tag.name for t in p.tags)}
            for p in main.query(Listing).all()
        }
    finally:
        main.close()
    users = {u.user_id: {"id": u.user_id, "name": u.name or u.email, "role": u.role,
                         "procurement_access": user_can_access_feature(u, "procurement")} for u in get_all_users()
             if u.is_approved and not u.deleted_at and u.email != config.API_SERVICE_EMAIL}
    return properties, users


def service_for(db, *, user=None, references=None):
    user = user or get_current_user()
    properties, users = references or reference_data()
    account_users = users
    api = bool(getattr(g, "api_key", None))
    administrator = user.is_admin() and not api
    staff = db.scalar(select(Staff).where(Staff.user_id == user.user_id)) if not administrator and not api else None
    # Request ownership records responsibility; Team access controls permissions.
    if staff and not staff.active:
        raise WorkflowError("Your housekeeper profile is inactive. Contact your manager.", 403)
    return ProcurementService(db, actor=f"{user.name or user.email} (API)" if api else user.name or user.email,
                              user_id=user.user_id if staff is None and user.user_id in users else None,
                              can_approve=administrator, properties=properties, users=users, staff_scope=staff, api=api, account_users=account_users)


def endpoint(*, mutation=False):
    def decorator(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            db = get_session()
            try:
                # Resolve auth/reference data before taking a write connection:
                # nested auth sessions must not exhaust the small shared pool
                # while concurrent writers wait on the procurement lock.
                user = get_current_user()
                references = reference_data()
                if mutation:
                    # Serialize the small procurement write workload across web workers.
                    # Protects quantity aggregates and replay keys, not just individual rows.
                    if db.bind.dialect.name == "postgresql":
                        db.execute(text("SELECT pg_advisory_xact_lock(418227390)"))
                    service = service_for(db, user=user, references=references)
                    key = required(request.headers.get("Idempotency-Key"), "Idempotency-Key header", 180)
                    if request.mimetype == "multipart/form-data":
                        files = []
                        for name, upload in request.files.items():
                            content = upload.stream.read(10 * 1024 * 1024 + 1)
                            upload.stream.seek(0)
                            files.append((name, upload.filename, hashlib.sha256(content).hexdigest()))
                        raw = json.dumps({"fields": sorted(request.form.items()), "files": files}, sort_keys=True).encode()
                    else:
                        raw = request.get_data(cache=True)
                    fingerprint = hashlib.sha256(str(user.user_id).encode() + request.method.encode() + request.path.encode() + raw).hexdigest()
                    previous = db.get(Command, key)
                    if previous:
                        if previous.fingerprint != fingerprint:
                            raise WorkflowError("This submission key was used for a different action. Refresh and try again.", 409)
                        if "/requests" not in request.path:
                            service.purchaser()
                        elif previous.result.get("id"):
                            ticket = service.ticket(previous.result["id"])
                            if service.staff_scope is not None:
                                return jsonify(service.ticket_dict(ticket, detail=True))
                        if any(part in request.path for part in ("/approve", "/adjust-costs", "/directory/")) and not service.can_approve:
                            raise WorkflowError("Administrator access is required for this action.", 403)
                        return jsonify(previous.result)
                else:
                    service = service_for(db, user=user, references=references)
                result = fn(service, *args, **kwargs)
                if mutation:
                    db.flush()
                    db.add(Command(key=key, fingerprint=fingerprint, result=result))
                    db.commit()
                if isinstance(result, Response):
                    return result
                return jsonify(result)
            except WorkflowError as exc:
                db.rollback()
                return jsonify(error=str(exc)), exc.status
            except IntegrityError:
                db.rollback()
                return jsonify(error="This record already exists or conflicts with another submission. Refresh and review it."), 409
            except Exception:
                db.rollback()
                logger.exception("Procurement request failed")
                return jsonify(error="Procurement could not save or load this record. Please try again."), 500
            finally:
                db.close()
        return wrapped
    return decorator


def payload():
    value = request.get_json(silent=True)
    if not isinstance(value, dict):
        raise WorkflowError("Send a JSON object with this request.")
    def validate(obj):
        for key, item in obj.items():
            if key in ("items", "lines", "allocations", "confirmations"):
                limit = 500 if key == "items" else 100
                if not isinstance(item, list) or len(item) > limit or not all(isinstance(row, dict) for row in item):
                    raise WorkflowError(f"{key.capitalize()} must be a list of at most {limit} objects.")
                for row in item:
                    validate(row)
            elif key.endswith("_id") and item not in (None, ""):
                if isinstance(item, bool) or not str(item).isdigit() or int(item) < 1:
                    raise WorkflowError(f"{key.replace('_', ' ').capitalize()} must be a positive integer.")
    validate(value)
    for name in ("source", "evidence"):
        if name in value and value[name] is not None and not isinstance(value[name], dict):
            raise WorkflowError(f"{name.capitalize()} must be an object.")
    return value


@procurement_bp.get("/")
def procurement_page():
    browser_session.setdefault("procurement_csrf", secrets.token_urlsafe(32))
    return render_template("procurement/index.html", procurement_csrf=browser_session["procurement_csrf"])


@procurement_bp.get("/api/bootstrap")
@endpoint()
def bootstrap(service):
    db = service.session
    staff = list(db.scalars(select(Staff).order_by(Staff.name)))
    locations = list(db.scalars(select(Location).order_by(Location.label))) if service.staff_scope is None else []
    return {
        "properties": [p for p in service.properties.values() if service.staff_scope is None or p["id"] in service.staff_scope.listing_ids],
        "users": sorted(service.users.values(), key=lambda user: user["name"].casefold()) if service.staff_scope is None else [],
        "account_users": list(service.account_users.values()) if service.can_approve else [],
        "staff": [{"id": s.id, "version": s.version, "name": s.name, "phone": s.phone if service.staff_scope is None else None,
                   "user_id": s.user_id, "listing_ids": s.listing_ids, "active": s.active} for s in staff
                  if service.staff_scope is None or s.id == service.staff_scope.id],
        "locations": [{"id": l.id, "label": l.label, "kind": l.kind, "address": l.address, "instructions": l.instructions,
                       "staff_id": l.staff_id, "listing_id": l.listing_id, "active": l.active} for l in locations],
        "catalog": [{"id": c.id, "name": c.name, "category": c.category, "unit": c.unit, "pack_description": c.pack_description, "active": c.active}
                    for c in db.scalars(select(CatalogItem).order_by(CatalogItem.name))],
        "statuses": STATUS_LABELS, "priorities": PRIORITIES, "currencies": CURRENCIES,
        "can_approve": service.can_approve, "can_purchase": service.staff_scope is None,
        "staff_id": service.staff_scope.id if service.staff_scope else None,
        "user_id": service.user_id,
    }


@procurement_bp.get("/api/requests")
@endpoint()
def list_requests(service):
    query = request_query().order_by(Ticket.created_at.desc())
    if service.staff_scope is not None:
        query = query.where(or_(Ticket.listing_id.in_(service.staff_scope.listing_ids), Ticket.items.any(RequestItem.listing_id.in_(service.staff_scope.listing_ids))))
    return {"requests": [service.ticket_dict(t) for t in service.session.scalars(query)]}


@procurement_bp.get("/api/requests/<int:ticket_id>")
@endpoint()
def request_detail(service, ticket_id):
    return service.ticket_dict(service.ticket(ticket_id), detail=True)


@procurement_bp.post("/api/requests")
@endpoint(mutation=True)
def create_request(service):
    ticket, replayed = service.create_ticket(payload())
    return {**service.ticket_dict(ticket, detail=True), "source_replayed": replayed}


@procurement_bp.patch("/api/requests/<int:ticket_id>")
@endpoint(mutation=True)
def update_request(service, ticket_id):
    return service.ticket_dict(service.update_ticket(ticket_id, payload()), detail=True)


@procurement_bp.post("/api/requests/<int:ticket_id>/<action>")
@endpoint(mutation=True)
def request_action(service, ticket_id, action):
    if action == "cancel-item":
        ticket = service.cancel_item(ticket_id, payload())
    elif action == "cancel":
        ticket = service.cancel_ticket(ticket_id, payload())
    elif action in ("confirm", "return"):
        ticket = service.confirm(ticket_id, payload(), returning=action == "return")
    elif action == "confirm-batch":
        ticket = service.confirm_batch(ticket_id, payload())
    elif action == "problem":
        ticket = service.fulfillment_issue(ticket_id, payload())
    else:
        raise WorkflowError("Unknown request action.", 404)
    return service.ticket_dict(ticket, detail=True)


@procurement_bp.get("/api/orders")
@endpoint()
def list_orders(service):
    service.purchaser()
    return {"orders": [service.order_dict(o) for o in service.session.scalars(order_query().order_by(PurchaseOrder.id.desc()))]}


@procurement_bp.get("/api/orders/<int:order_id>")
@endpoint()
def order_detail(service, order_id):
    service.purchaser()
    return service.order_dict(get(service.session, PurchaseOrder, order_id), detail=True)


@procurement_bp.post("/api/orders")
@endpoint(mutation=True)
def create_order(service):
    return service.order_dict(service.save_order(payload()), detail=True)


@procurement_bp.patch("/api/orders/<int:order_id>")
@endpoint(mutation=True)
def revise_order(service, order_id):
    return service.order_dict(service.save_order(payload(), order_id), detail=True)


@procurement_bp.post("/api/orders/<int:order_id>/<action>")
@endpoint(mutation=True)
def order_action(service, order_id, action):
    if action == "shipments":
        order = service.create_shipment(order_id, payload())
    elif action == "allocate":
        order = service.add_allocation(order_id, payload())
    elif action == "refund":
        order = service.refund(order_id, payload())
    else:
        order = service.order_action(order_id, action, payload())
    return service.order_dict(order, detail=True)


@procurement_bp.post("/api/shipments/<int:shipment_id>/deliver")
@endpoint(mutation=True)
def deliver(service, shipment_id):
    return service.order_dict(service.deliver_shipment(shipment_id, payload()), detail=True)


@procurement_bp.patch("/api/shipments/<int:shipment_id>")
@endpoint(mutation=True)
def update_shipment(service, shipment_id):
    return service.order_dict(service.update_shipment(shipment_id, payload()), detail=True)


@procurement_bp.get("/api/report")
@endpoint()
def report(service):
    service.purchaser()
    return service.report(request.args.get("month", datetime.utcnow().strftime("%Y-%m")))


@procurement_bp.get("/api/report.csv")
@endpoint()
def export_report(service):
    service.purchaser()
    report = service.report(request.args.get("month", datetime.utcnow().strftime("%Y-%m")))
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(["Month", "Currency", "Property", "Allocated cost", "Confirmed at property"])
    from decimal import Decimal
    def amount(value):
        return format(Decimal(value) / 100, ".2f")
    def safe_label(value):
        value = str(value or "")
        return "'" + value if value.lstrip().startswith(("=", "+", "-", "@")) or value.startswith(("\t", "\r", "\n")) else value
    for group in report["currencies"]:
        for prop in group["properties"]:
            writer.writerow([report["month"], group["currency"], safe_label(prop["name"]), amount(prop["allocated_cents"]), amount(prop["confirmed_cents"])])
        writer.writerow([report["month"], group["currency"], "Unallocated shared stock", amount(group["unallocated_cents"]), ""])
    return Response("\ufeff" + output.getvalue(), content_type="text/csv; charset=utf-8", headers={
        "Content-Disposition": f'attachment; filename="procurement-{report["month"]}.csv"',
        "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff",
    })


@procurement_bp.get("/api/events")
@endpoint()
def integration_events(service):
    """Cursor feed for Ethan; publishing/delivery remains in the existing bot."""
    service.purchaser()
    try:
        after = int(request.args.get("after", "0"))
        if after < 0:
            raise ValueError
    except ValueError:
        raise WorkflowError("Event cursor must be a nonnegative integer.")
    events = service.session.scalars(select(AuditEvent).where(AuditEvent.id > after, AuditEvent.ticket_id.is_not(None)).order_by(AuditEvent.id).limit(200)).all()
    result = []
    for event in events:
        ticket = service.ticket(event.ticket_id)
        staff = service.session.get(Staff, ticket.requester_staff_id) if ticket.requester_staff_id else None
        result.append({"event_id": event.id, "action": event.action, "ticket_id": ticket.id,
                       "request_code": f"PR-{ticket.id:04d}", "property": ticket.property_snapshot.get("name"),
                       "properties": ticket_properties(ticket), "listing_ids": [p["id"] for p in ticket_properties(ticket)],
                       "affected_listing_ids": event.detail.get("listing_ids", [event.detail["listing_id"]] if event.detail.get("listing_id") else [p["id"] for p in ticket_properties(ticket)]),
                       "status": ticket_status(ticket), "owner_user_id": ticket.owner_user_id,
                       "requester_phone": staff.phone if staff and staff.active else None,
                       "source_messages": [{"channel": m.source, "message_id": m.message_id} for m in ticket.messages if m.message_id]})
    return {"events": result, "next_cursor": events[-1].id if events else after}


@procurement_bp.get("/api/attention")
@endpoint()
def attention_queue(service):
    service.purchaser()
    tickets = [service.ticket_dict(t) for t in service.session.scalars(request_query().order_by(Ticket.created_at))]
    return {"requests": [t for t in tickets if t["status"] not in ("completed", "cancelled") and (t["flags"] or t["priority"] == "critical")]}


@procurement_bp.post("/api/directory/<kind>")
@endpoint(mutation=True)
def directory(service, kind):
    if not service.can_approve:
        raise WorkflowError("An administrator manages the procurement directory.", 403)
    data = payload()
    db = service.session
    if kind == "staff":
        obj = service.save_staff(data)
        return {"id": obj.id, "version": obj.version, "saved": True}
    elif kind == "locations":
        obj = get(db, Location, data["id"]) if data.get("id") else Location()
        obj.label = required(data.get("label"), "Location label", 160)
        obj.kind = data.get("kind")
        if obj.kind not in ("housekeeper", "property", "storage"):
            raise WorkflowError("Choose a valid location type.")
        obj.address = required(data.get("address"), "Full delivery address", 2000)
        obj.instructions = str(data.get("instructions") or "")[:2000]
        obj.staff_id = get(db, Staff, data["staff_id"]).id if data.get("staff_id") else None
        obj.listing_id = service.property(data["listing_id"])["id"] if data.get("listing_id") else None
        if obj.kind == "housekeeper" and not obj.staff_id:
            raise WorkflowError("Choose the receiving housekeeper.")
        if obj.kind == "property" and not obj.listing_id:
            raise WorkflowError("Choose the destination property.")
    elif kind == "catalog":
        obj = get(db, CatalogItem, data["id"]) if data.get("id") else CatalogItem()
        obj.name = required(data.get("name"), "Product name", 180)
        obj.unit = required(data.get("unit"), "Standard unit", 40)
        obj.category = required(data.get("category", "supplies"), "Category", 60)
        obj.pack_description = str(data.get("pack_description") or "")[:160]
    else:
        raise WorkflowError("Unknown directory section.", 404)
    if "active" in data and not isinstance(data["active"], bool):
        raise WorkflowError("Active must be true or false.")
    obj.active = data.get("active", True)
    db.add(obj)
    db.flush()
    service.audit("directory_updated", {"kind": kind, "id": obj.id, "values": data})
    return {"id": obj.id, "saved": True}


@procurement_bp.post("/api/orders/<int:order_id>/receipts")
@endpoint(mutation=True)
def upload_receipt(service, order_id):
    service.purchaser()
    order = get(service.session, PurchaseOrder, order_id)
    upload = request.files.get("file")
    if not upload:
        raise WorkflowError("Choose a PDF, PNG, or JPEG receipt.")
    content = upload.stream.read(10 * 1024 * 1024 + 1)
    if len(content) > 10 * 1024 * 1024:
        raise WorkflowError("Receipts must be 10 MB or smaller.", 413)
    if content.startswith(b"%PDF-"):
        mime = "application/pdf"
    elif content.startswith(b"\x89PNG\r\n\x1a\n"):
        mime = "image/png"
    elif content.startswith(b"\xff\xd8\xff"):
        mime = "image/jpeg"
    else:
        raise WorkflowError("The file must contain a PDF, PNG, or JPEG receipt.")
    attachment = Attachment(order=order, name=(secure_filename(upload.filename or "receipt") or "receipt")[:240], content=content, mime_type=mime)
    service.session.add(attachment)
    order.version += 1
    service.session.flush()
    service.audit("receipt_attached", {"attachment_id": attachment.id, "filename": attachment.name}, order=order)
    return service.order_dict(order, detail=True)


@procurement_bp.get("/api/receipts/<int:attachment_id>")
def download_receipt(attachment_id):
    db = get_session()
    try:
        service = service_for(db)
        service.purchaser()
        attachment = get(db, Attachment, attachment_id)
        response = send_file(io.BytesIO(attachment.content), mimetype=attachment.mime_type, download_name=attachment.name, as_attachment=True)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cache-Control"] = "private, no-store"
        return response
    except WorkflowError as exc:
        return jsonify(error=str(exc)), exc.status
    finally:
        db.close()


def register_procurement_routes(app):
    app.register_blueprint(procurement_bp)
