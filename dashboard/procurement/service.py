"""Transactional procurement rules shared by the dashboard and Ethan API.

Amounts are integer minor units; quantities use three decimal places. A receipt
from a carrier never confirms that supplies are available at a property.
"""

from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import selectinload

from .models import (
    Allocation, AuditEvent, CatalogItem, Confirmation, Location, OrderLine,
    PurchaseOrder, Refund, RequestItem, Shipment, ShipmentLine, SourceMessage,
    Staff, Ticket,
)

ZERO = Decimal(0)
PRIORITIES = ("critical", "high", "normal", "planned")
CURRENCIES = ("USD", "CAD", "GBP", "EUR", "AUD", "MYR")
STATUS_LABELS = {
    "new": "New", "in_progress": "In Progress",
    "awaiting_confirmation": "Awaiting Property Confirmation",
    "completed": "Completed", "cancelled": "Cancelled",
}


class WorkflowError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def required(value, label, limit=240):
    if not isinstance(value, str) or not value.strip():
        raise WorkflowError(f"{label} is required.")
    value = value.strip()
    if len(value) > limit:
        raise WorkflowError(f"{label} must be {limit} characters or fewer.")
    return value


def quantity(value, *, optional=False):
    if optional and value in (None, ""):
        return None
    try:
        result = Decimal(str(value))
        if not result.is_finite() or result <= 0 or result > 99999999 or result.as_tuple().exponent < -3:
            raise InvalidOperation
        return result.quantize(Decimal(".001"))
    except (InvalidOperation, TypeError, ValueError):
        raise WorkflowError("Quantity must be positive with at most three decimal places.")


def cents(value, label="Amount"):
    try:
        result = Decimal(str(value if value is not None else 0))
        if not result.is_finite() or result < 0 or result > 99999999 or result.as_tuple().exponent < -2:
            raise InvalidOperation
        return int((result * 100).to_integral_exact())
    except (InvalidOperation, TypeError, ValueError):
        raise WorkflowError(f"{label} must be a nonnegative amount with at most two decimal places.")


def timestamp(value, label="Date", optional=True):
    if value in (None, "") and optional:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError
        return parsed.astimezone(timezone.utc).replace(tzinfo=None)
    except (ValueError, TypeError):
        raise WorkflowError(f"{label} must include a time zone.")


def calendar_date(value, label="Date"):
    try:
        return date.fromisoformat(str(value))
    except (ValueError, TypeError):
        raise WorkflowError(f"{label} must be a valid date.")


def iso(value):
    return value.isoformat() + "Z" if value else None


def get(session, model, object_id):
    try:
        object_id = int(object_id)
    except (ValueError, TypeError):
        raise WorkflowError("Choose a valid record.")
    obj = session.get(model, object_id)
    if not obj:
        raise WorkflowError("This record could not be found.", 404)
    return obj


def check_version(obj, data):
    if isinstance(data.get("version"), bool) or not isinstance(data.get("version"), int) or data.get("version") != obj.version:
        raise WorkflowError("This record changed since you opened it. Refresh and review the latest version.", 409)


def split_cents(total, weights):
    """Largest-remainder allocation: exact conservation, deterministic cent rounding."""
    weights = [Decimal(str(w)) for w in weights]
    denominator = sum(weights)
    if not weights or denominator <= 0:
        return [0] * len(weights)
    sign = -1 if total < 0 else 1
    exact = [Decimal(abs(total)) * w / denominator for w in weights]
    result = [int(x) for x in exact]
    remainder = abs(total) - sum(result)
    for index in sorted(range(len(weights)), key=lambda i: (-(exact[i] - result[i]), i))[:remainder]:
        result[index] += 1
    return [sign * n for n in result]


def line_subtotal(line):
    return int((line.quantity / (line.pack_size or Decimal(1)) * line.unit_price_cents).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def order_totals(order):
    subtotals = [line_subtotal(line) for line in order.lines]
    subtotal = sum(subtotals)
    total = subtotal + order.tax_cents + order.shipping_cents - order.discount_cents
    if total > 9007199254740991:
        raise WorkflowError("This order total exceeds the supported monetary range.")
    # Quantity is a fallback for genuinely free products with shipping charges.
    weights = subtotals if subtotal else [line.quantity for line in order.lines]
    landed = split_cents(total, weights)
    return {"subtotal_cents": subtotal, "total_cents": total, "line_totals": dict(zip([x.id for x in order.lines], landed))}


def returned_quantity(allocation):
    return -sum((c.quantity for c in allocation.confirmations if c.quantity < 0), ZERO)


def confirmed_quantity(allocation):
    return sum((c.quantity for c in allocation.confirmations), ZERO)


def active_quantity(allocation):
    return allocation.quantity - returned_quantity(allocation) - (allocation.problem_quantity or ZERO)


def delivered_quantity(line):
    carrier = sum((sl.quantity for shipment in line.order.shipments if shipment.delivered_at and not shipment.cancelled_reason
                   for sl in shipment.lines if sl.line_id == line.id), ZERO)
    physical_receipts = sum((c.quantity for a in line.allocations for c in a.confirmations if c.quantity > 0), ZERO)
    return max(carrier, physical_receipts)


def allocation_delivered(allocation):
    """Assign the delivered pool once, in stable allocation order; never count it twice."""
    remaining = delivered_quantity(allocation.line) - sum((returned_quantity(a) + (a.excluded_delivered_quantity or ZERO) for a in allocation.line.allocations), ZERO)
    # Actual housekeeper receipts take precedence over assigning an unconfirmed
    # carrier-delivered pool. This also handles missing or late carrier scans.
    remaining -= sum((confirmed_quantity(a) for a in allocation.line.allocations), ZERO)
    for other in sorted(allocation.line.allocations, key=lambda a: a.id):
        confirmed = confirmed_quantity(other)
        extra = max(ZERO, min(active_quantity(other) - confirmed, remaining))
        delivered = confirmed + extra
        if other.id == allocation.id:
            return delivered
        remaining -= extra
    return ZERO


def item_progress(item):
    allocations = [a for a in item.allocations if a.line.order.state != "cancelled"]
    reserved = sum((active_quantity(a) for a in allocations), ZERO)
    ordered = sum((active_quantity(a) for a in allocations if a.line.order.state == "ordered"), ZERO)
    delivered = sum((allocation_delivered(a) for a in allocations if a.line.order.state == "ordered"), ZERO)
    confirmed = sum((confirmed_quantity(a) for a in allocations), ZERO)
    needed = (item.quantity or ZERO) - item.cancelled_quantity
    return {"requested": item.quantity, "cancelled": item.cancelled_quantity, "reserved": reserved,
            "ordered": ordered, "delivered": delivered, "confirmed": confirmed,
            "remaining": max(ZERO, needed - confirmed), "to_order": max(ZERO, needed - reserved)}


def ticket_properties(ticket):
    """Explicit members; legacy single-property requests keep their snapshot."""
    return [{**(ticket.property_snapshot or {}), "id": ticket.listing_id}] if ticket.listing_id else (ticket.property_snapshots or [])


def item_listing_id(item):
    return item.listing_id or item.ticket.listing_id


def item_property(item):
    return next((p for p in ticket_properties(item.ticket) if p["id"] == item_listing_id(item)), {})


def line_destination(line):
    return line.destination or line.order.destination


def order_destinations(order):
    destinations = {}
    for line in order.lines:
        destination = line_destination(line)
        if destination:
            destinations[destination["id"]] = destination
    return list(destinations.values())


def shipment_allocations(shipment):
    ids = {line.line_id for line in shipment.lines}
    return [a for line in shipment.order.lines if line.id in ids for a in line.allocations]


def ticket_status(ticket, items=None):
    if ticket.cancelled_reason:
        return "cancelled"
    items = ticket.items if items is None else items
    progress = [item_progress(i) for i in items]
    complete_information = bool(items) and all(item_listing_id(i) or ticket.storage_location_id for i in items) and all(p["requested"] is not None for p in progress)
    if complete_information and all(p["remaining"] == 0 for p in progress):
        return "completed"
    if complete_information and all(p["delivered"] + p["cancelled"] >= p["requested"] for p in progress):
        return "awaiting_confirmation"
    if ticket.reviewed_at or any(p["reserved"] for p in progress):
        return "in_progress"
    return "new"


def ticket_missing_information(ticket, items=None):
    items = ticket.items if items is None else items
    if ticket_status(ticket, items) in ("completed", "cancelled"):
        return []
    missing = []
    if not items:
        missing.append({"field": "items", "message": "Add the supplies needed for this request."})
    if (not items and not ticket_properties(ticket) and not ticket.storage_location_id) or any(not (item_listing_id(i) or ticket.storage_location_id) for i in items):
        missing.append({"field": "property", "message": "Choose the property or shared storage destination."})
    if not ticket.required_by:
        missing.append({"field": "required_by", "message": "Set the needed-by date and time."})
    return missing


def ticket_flags(ticket, now=None, items=None):
    now = now or datetime.utcnow()
    items = ticket.items if items is None else items
    if ticket_status(ticket, items) in ("completed", "cancelled"):
        return []
    flags = []
    if ticket_missing_information(ticket, items):
        flags.append("Needs information")
    if not ticket.owner_user_id:
        flags.append("Unassigned")
    if ticket.required_by and ticket.required_by < now:
        flags.append("Overdue")
    if not ticket.reviewed_at and ticket.response_due_at and ticket.response_due_at < now:
        flags.append("Staff response overdue")
    orders = {a.line.order for i in items for a in i.allocations if a.line.order.state != "cancelled"}
    line_ids = {a.line_id for i in items for a in i.allocations if a.line.order.state != "cancelled"}
    shipments = [s for o in orders for s in o.shipments if any(line.line_id in line_ids for line in s.lines)]
    if any(o.state == "draft" for o in orders):
        flags.append("Awaiting approval")
    if any(s.expected_at and ticket.required_by and s.expected_at > ticket.required_by and not s.delivered_at and not s.cancelled_reason for s in shipments):
        flags.append("Delivery at risk")
    if any(s.expected_at and s.expected_at < now and not s.delivered_at and not s.cancelled_reason for s in shipments):
        flags.append("Delivery delayed")
    if any((a.problem_quantity or ZERO) > 0 for i in items if item_progress(i)["remaining"] > 0 for a in i.allocations):
        flags.append("Fulfillment issue")
    return flags


class ProcurementService:
    def __init__(self, session, *, actor, user_id=None, can_approve=False, properties=None, users=None, staff_scope=None, api=False, account_users=None):
        self.session = session
        self.actor = actor
        self.user_id = user_id
        self.can_approve = can_approve
        self.properties = properties or {}
        self.users = users or {}
        self.account_users = account_users if account_users is not None else self.users
        self.staff_scope = staff_scope
        self.api = api

    def audit(self, action, detail, *, ticket=None, order=None):
        self.session.add(AuditEvent(actor=self.actor, action=action, detail=detail,
                                    ticket_id=ticket.id if ticket else None, order_id=order.id if order else None))

    def touch(self, ticket):
        ticket.version += 1

    def property(self, listing_id):
        try:
            result = self.properties[int(listing_id)]
        except (ValueError, TypeError, KeyError):
            raise WorkflowError("Choose an existing property.")
        if self.staff_scope is not None and result["id"] not in self.staff_scope.listing_ids:
            raise WorkflowError("This property is outside your assignments.", 403)
        return result

    def ticket(self, ticket_id):
        ticket = get(self.session, Ticket, ticket_id)
        if self.staff_scope is not None and not set(p["id"] for p in ticket_properties(ticket)) & set(self.staff_scope.listing_ids):
            raise WorkflowError("This request is outside your property assignments.", 403)
        return ticket

    def item_access(self, item):
        if self.staff_scope is not None and item_listing_id(item) not in self.staff_scope.listing_ids:
            raise WorkflowError("This item is outside your property assignments.", 403)

    def request_properties(self, data):
        ids = data.get("listing_ids", [data["listing_id"]] if data.get("listing_id") else [])
        if not isinstance(ids, list) or len(ids) > 100 or any(isinstance(i, bool) or not str(i).isdigit() or int(i) < 1 for i in ids):
            raise WorkflowError("Choose up to 100 valid properties.")
        if len(set(map(int, ids))) != len(ids):
            raise WorkflowError("Select each property only once.")
        if data.get("listing_id") and list(map(int, ids)) != [int(data["listing_id"])]:
            raise WorkflowError("Use listing_ids for a multi-property request; omit listing_id.")
        return [self.property(i) for i in ids]

    def request_item_property(self, row, properties):
        ids = [p["id"] for p in properties]
        listing_id = row.get("listing_id")
        if listing_id in (None, ""):
            if len(ids) > 1:
                raise WorkflowError("Choose the property for each requested item.")
            return ids[0] if ids else None
        prop = self.property(listing_id)
        if prop["id"] not in ids:
            raise WorkflowError("Every item must belong to a selected request property.")
        return prop["id"]

    def validate_request_members(self, ticket):
        ids = {p["id"] for p in ticket_properties(ticket)}
        if ids != {item_listing_id(i) for i in ticket.items} - {None}:
            raise WorkflowError("Each selected property needs at least one item; remove unused properties or add their quantities.")

    def purchaser(self):
        if self.staff_scope is not None:
            raise WorkflowError("Purchasing access is required for this action.", 403)

    def save_staff(self, data):
        """Maintain operational people without creating or granting login access."""
        if not self.can_approve:
            raise WorkflowError("An administrator manages housekeepers.", 403)
        staff = get(self.session, Staff, data["id"]) if data.get("id") else Staff()
        if staff.id:
            check_version(staff, data)
        name = required(data.get("name"), "Housekeeper name", 160)
        if any(other.id != staff.id and other.name.casefold() == name.casefold()
               for other in self.session.scalars(select(Staff))):
            raise WorkflowError("A housekeeper with this name already exists. Edit the existing person instead.", 409)
        ids = data.get("listing_ids", [])
        if not isinstance(ids, list) or len(ids) > 1000 or any(
            isinstance(i, bool) or not str(i).isdigit() or int(i) < 1 for i in ids
        ):
            raise WorkflowError("Property assignments must be a list of valid property IDs.")
        listing_ids = list(dict.fromkeys(self.property(i)["id"] for i in ids))
        phone = data.get("phone") or ""
        if not isinstance(phone, str) or len(phone.strip()) > 80:
            raise WorkflowError("WhatsApp number must be 80 characters or fewer.")
        if "active" in data and not isinstance(data["active"], bool):
            raise WorkflowError("Active must be true or false.")
        before = {"name": staff.name, "phone": staff.phone, "listing_ids": staff.listing_ids,
                  "active": staff.active, "user_id": staff.user_id} if staff.id else None
        if "user_id" in data:
            account_id = int(data["user_id"]) if data["user_id"] else None
            if account_id is not None and account_id != staff.user_id and account_id not in self.account_users:
                raise WorkflowError("Choose an active, approved Cotton Candy account.")
            staff.user_id = account_id
        staff.name, staff.phone, staff.listing_ids = name, phone.strip(), listing_ids
        staff.active = data.get("active", staff.active if staff.id else True)
        if staff.id:
            staff.version += 1
        self.session.add(staff)
        self.session.flush()
        self.audit("directory_updated", {"kind": "staff", "id": staff.id, "before": before,
                   "values": {"name": staff.name, "phone": staff.phone, "listing_ids": staff.listing_ids,
                              "active": staff.active, "user_id": staff.user_id, "version": staff.version}})
        return staff

    def owner(self, user_id):
        if user_id in (None, ""):
            return None
        try:
            user_id = int(user_id)
            if user_id not in self.users:
                raise ValueError
        except (ValueError, TypeError):
            raise WorkflowError("Choose an active, approved staff owner.")
        return user_id

    def catalog_fields(self, data, *, purchase=False):
        catalog = get(self.session, CatalogItem, data["catalog_id"]) if data.get("catalog_id") else None
        if catalog and not catalog.active:
            raise WorkflowError("This catalog product is inactive.")
        if catalog and data.get("unit") and str(data["unit"]).strip().casefold() != catalog.unit.casefold():
            raise WorkflowError(f"This catalog product uses {catalog.unit}. Enter that unit or choose a custom supply; units never convert silently.")
        if catalog and not purchase and data.get("name") and str(data["name"]).strip().casefold() != catalog.name.casefold():
            raise WorkflowError("Use the selected catalog product name or choose a custom supply.")
        return {"catalog_id": catalog.id if catalog else None,
                "name": required(data["name"], "Purchased product name", 180) if purchase and data.get("name") else catalog.name if catalog else required(data.get("name"), "Item name", 180),
                "unit": catalog.unit if catalog else required(data.get("unit"), "Unit", 40),
                "category": catalog.category if catalog else required(data.get("category", "supplies"), "Category", 60)}

    def create_ticket(self, data):
        properties = self.request_properties(data)
        source = data.get("source") or {}
        if not isinstance(source, dict):
            raise WorkflowError("Source must be an object.")
        if source.get("message_id"):
            required(source.get("body"), "Original source message", 20000)
        if source.get("message_id"):
            existing = self.session.scalars(select(SourceMessage).where(
                SourceMessage.source == source.get("channel", "whatsapp"), SourceMessage.message_id == str(source["message_id"])
            )).all()
            for message in existing:
                if {p["id"] for p in ticket_properties(message.ticket)} == {p["id"] for p in properties} and message.ticket.storage_location_id == (int(data["storage_location_id"]) if data.get("storage_location_id") else None):
                    return self.ticket(message.ticket_id), True
        prop = properties[0] if len(properties) == 1 else None
        location = get(self.session, Location, data["storage_location_id"]) if data.get("storage_location_id") else None
        if properties and location:
            raise WorkflowError("Choose a property or shared storage, not both.")
        if location and (not location.active or location.kind != "storage"):
            raise WorkflowError("Choose an active shared storage location.")
        if self.staff_scope is not None and not properties:
            raise WorkflowError("Choose one of your assigned properties.", 403)
        staff_id = self.staff_scope.id if self.staff_scope is not None else data.get("requester_staff_id")
        staff = get(self.session, Staff, staff_id) if staff_id else None
        if staff and (not staff.active or any(p["id"] not in staff.listing_ids for p in properties)):
            raise WorkflowError("The requester must be active and assigned to every selected property. Staff can use a requester name for requests spanning housekeepers.")
        priority = data.get("priority", "normal")
        if priority not in PRIORITIES:
            raise WorkflowError("Choose a valid priority.")
        items = data.get("items")
        if not isinstance(items, list) or not 1 <= len(items) <= 500:
            raise WorkflowError("Add between 1 and 500 property/supply items.")
        response_hours = {"critical": .5, "high": 4, "normal": 24, "planned": 72}[priority]
        ticket = Ticket(title=required(data.get("title"), "Request title"), listing_id=prop["id"] if prop else None,
                        property_snapshot=prop or {}, property_snapshots=properties, storage_location_id=location.id if location else None,
                        requester_staff_id=staff.id if staff else None,
                        requester_name=staff.name if staff else required(data.get("requester_name"), "Requester", 160),
                        owner_user_id=self.owner(data.get("owner_user_id", self.user_id)), priority=priority,
                        required_by=timestamp(data.get("required_by"), "Required-by date"),
                        response_due_at=timestamp(data.get("response_due_at")) or datetime.utcnow() + timedelta(hours=response_hours),
                        created_by=self.actor)
        self.session.add(ticket)
        for item in items:
            ticket.items.append(RequestItem(**self.catalog_fields(item), listing_id=self.request_item_property(item, properties), quantity=quantity(item.get("quantity"), optional=True)))
        self.validate_request_members(ticket)
        self.session.flush()
        if source.get("body"):
            self._message(ticket, source)
        self.audit("request_created", {"title": ticket.title, "source": source.get("channel", "manual")}, ticket=ticket)
        return ticket, False

    def _message(self, ticket, source):
        channel = required(source.get("channel", "manual"), "Source channel", 80)
        message_id = str(source["message_id"]) if source.get("message_id") else None
        if message_id and len(message_id) > 240:
            raise WorkflowError("Source message ID is too long.")
        if message_id and self.session.scalar(select(SourceMessage).where(SourceMessage.source == channel, SourceMessage.message_id == message_id, SourceMessage.ticket_id == ticket.id)):
            return
        self.session.add(SourceMessage(ticket=ticket, source=channel, message_id=message_id,
                                       body=required(source.get("body"), "Message", 20000), source_url=safe_url(source.get("url"))))

    def update_ticket(self, ticket_id, data):
        ticket = self.ticket(ticket_id)
        check_version(ticket, data)
        if set(data) - {"version", "source", "title", "requester_name", "owner_user_id", "priority", "required_by", "response_due_at", "listing_id", "listing_ids", "requester_staff_id", "review", "items"}:
            raise WorkflowError("Request status is calculated from fulfillment. Use the supported request fields and actions.")
        if ticket.cancelled_reason:
            raise WorkflowError("Cancelled requests cannot be edited.")
        before = self.ticket_dict(ticket)
        if self.staff_scope is not None and set(data) - {"version", "source"}:
            raise WorkflowError("Staff owners manage request details. You can add a message or confirm receipt.", 403)
        if "source" in data:
            if not isinstance(data["source"], dict):
                raise WorkflowError("Source must be an object.")
            self._message(ticket, data["source"])
        for name in ("title", "requester_name"):
            if name in data:
                setattr(ticket, name, required(data[name], name.replace("_", " ")))
        if "owner_user_id" in data:
            ticket.owner_user_id = self.owner(data["owner_user_id"])
        if "priority" in data:
            if data["priority"] not in PRIORITIES:
                raise WorkflowError("Choose a valid priority.")
            ticket.priority = data["priority"]
        for name in ("required_by", "response_due_at"):
            if name in data:
                setattr(ticket, name, timestamp(data[name]))
        properties = ticket_properties(ticket)
        if "listing_id" in data or "listing_ids" in data:
            new_properties = self.request_properties(data)
            removed = {p["id"] for p in properties} - {p["id"] for p in new_properties}
            if any(i.allocations and item_listing_id(i) in removed for i in ticket.items):
                raise WorkflowError("The destination property cannot change after purchasing has started.")
            if len(properties) <= 1 and len(new_properties) == 1:
                for item in ticket.items:
                    if not item.allocations:
                        item.listing_id = new_properties[0]["id"]
            properties = new_properties
            prop = properties[0] if len(properties) == 1 else None
            ticket.listing_id, ticket.property_snapshot = (prop["id"], prop) if prop else (None, {})
            ticket.property_snapshots, ticket.storage_location_id = properties, None
        if "requester_staff_id" in data:
            staff = get(self.session, Staff, data["requester_staff_id"]) if data["requester_staff_id"] else None
            if staff and staff.id != ticket.requester_staff_id and (not staff.active or any(p["id"] not in staff.listing_ids for p in properties)):
                raise WorkflowError("Choose an active housekeeper assigned to every selected property.")
            ticket.requester_staff_id = staff.id if staff else None
            if staff:
                ticket.requester_name = staff.name
        if data.get("review"):
            if not ticket.owner_user_id:
                raise WorkflowError("Assign a staff owner before reviewing this request.")
            ticket.reviewed_at = ticket.reviewed_at or datetime.utcnow()
        for row in data.get("items", []):
            if row.get("id"):
                item = get(self.session, RequestItem, row["id"])
                if item.ticket_id != ticket.id:
                    raise WorkflowError("Item does not belong to this request.")
                if any(a.line.order.state != "cancelled" for a in item.allocations):
                    raise WorkflowError("Ordered or reserved items cannot be rewritten. Cancel an unpurchased order or add a new item.")
                if row.get("remove") is True:
                    if item.allocations or item.cancelled_quantity:
                        raise WorkflowError("Items with purchasing or cancellation history cannot be removed.")
                    ticket.items.remove(item)
                    self.session.delete(item)
                    continue
                fields = self.catalog_fields(row)
                for key, value in fields.items():
                    setattr(item, key, value)
                item.quantity = quantity(row.get("quantity"), optional=True)
                item.listing_id = self.request_item_property(row, properties)
                if item.cancelled_quantity and (item.quantity is None or item.quantity < item.cancelled_quantity):
                    raise WorkflowError("Requested quantity cannot be below the cancelled quantity.")
            else:
                ticket.items.append(RequestItem(**self.catalog_fields(row), listing_id=self.request_item_property(row, properties), quantity=quantity(row.get("quantity"), optional=True)))
        if not ticket.items or len(ticket.items) > 500:
            raise WorkflowError("Keep between 1 and 500 property/supply items on a request.")
        self.validate_request_members(ticket)
        self.touch(ticket)
        self.session.flush()
        self.audit("request_updated", {"before": before, "after": self.ticket_dict(ticket)}, ticket=ticket)
        return ticket

    def cancel_item(self, ticket_id, data):
        self.purchaser()
        ticket = self.ticket(ticket_id)
        check_version(ticket, data)
        item = get(self.session, RequestItem, data.get("item_id"))
        if item.ticket_id != ticket.id or item.quantity is None:
            raise WorkflowError("Choose a request item with a known quantity.")
        amount = quantity(data.get("quantity"))
        if amount > item_progress(item)["to_order"]:
            raise WorkflowError("Only the unpurchased, unreserved quantity can be cancelled.")
        item.cancelled_quantity += amount
        item.cancellation_reason = required(data.get("reason"), "Cancellation reason", 2000)
        self.touch(ticket)
        if all(i.quantity is not None and i.cancelled_quantity == i.quantity for i in ticket.items):
            ticket.cancelled_reason = "All requested items were cancelled with recorded reasons."
        self.audit("item_cancelled", {"item_id": item.id, "quantity": str(amount), "reason": item.cancellation_reason}, ticket=ticket)
        return ticket

    def cancel_ticket(self, ticket_id, data):
        self.purchaser()
        ticket = self.ticket(ticket_id)
        check_version(ticket, data)
        if ticket.cancelled_reason or any(item_progress(i)["reserved"] for i in ticket.items):
            raise WorkflowError("Cancel unpurchased orders first. Purchased requests must be resolved item by item.")
        ticket.cancelled_reason = required(data.get("reason"), "Cancellation reason", 2000)
        self.touch(ticket)
        self.audit("request_cancelled", {"reason": ticket.cancelled_reason}, ticket=ticket)
        return ticket

    def _destination(self, location_id):
        location = get(self.session, Location, location_id)
        if not location.active:
            raise WorkflowError("Choose an active delivery location.")
        if location.staff_id and not get(self.session, Staff, location.staff_id).active:
            raise WorkflowError("The receiving housekeeper is inactive. Choose another destination.")
        return {"id": location.id, "label": location.label, "kind": location.kind, "address": location.address,
                "instructions": location.instructions, "staff_id": location.staff_id, "listing_id": location.listing_id}

    def _allocate(self, line, data):
        item = get(self.session, RequestItem, data.get("item_id"))
        ticket = self.ticket(item.ticket_id)
        if ticket.cancelled_reason or not (item_listing_id(item) or ticket.storage_location_id) or item.quantity is None:
            raise WorkflowError("Resolve the request destination and quantity before allocating a purchase.")
        if item.unit.casefold() != line.unit.casefold():
            raise WorkflowError("Purchase and request units must match. Convert pack sizes to the requested unit.")
        if ((item.name.casefold() != line.name.casefold() or
             (item.catalog_id and line.catalog_id and item.catalog_id != line.catalog_id)) and not line.substitution_reason):
            raise WorkflowError("Record a substitution reason when buying a different product.")
        amount = quantity(data.get("quantity"))
        if amount > item_progress(item)["to_order"]:
            raise WorkflowError("This quantity exceeds the request's unreserved need.")
        if sum((a.quantity for a in line.allocations), ZERO) + amount > line.quantity:
            raise WorkflowError("Allocated quantity exceeds the purchased quantity.")
        allocation = Allocation(item=item, line=line, quantity=amount)
        self.session.add(allocation)
        if not self.api:
            ticket.reviewed_at = ticket.reviewed_at or datetime.utcnow()
        self.touch(ticket)
        self.session.flush()
        return allocation

    def save_order(self, data, order_id=None):
        self.purchaser()
        order = get(self.session, PurchaseOrder, order_id) if order_id else PurchaseOrder()
        if order_id:
            check_version(order, data)
            if order.state not in ("draft", "approved"):
                raise WorkflowError("Placed orders are immutable. Record returns or refunds separately.")
            required(data.get("change_reason"), "Reason for changing the order", 2000)
            affected_items = {a.item for line in order.lines for a in line.allocations}
            affected = {item.ticket for item in affected_items}
            for ticket in affected:
                self.touch(ticket)
            order.lines.clear()
            self.session.flush()
            for item in affected_items:
                self.session.expire(item, ["allocations"])
            order.version += 1
            order.purchase_revision += 1
        order.vendor = required(data.get("vendor"), "Retailer", 160)
        order.vendor_key = order.vendor.casefold()
        order.reference = None
        order.currency = data.get("currency", "USD")
        if order.currency not in CURRENCIES:
            raise WorkflowError("Choose a supported currency.")
        default_destination = self._destination(data["location_id"]) if data.get("location_id") else None
        order.destination = default_destination or {}
        order.tax_cents = cents(data.get("tax"), "Tax")
        order.shipping_cents = cents(data.get("shipping"), "Shipping")
        order.discount_cents = cents(data.get("discount"), "Discount")
        order.responsibility = data.get("responsibility", "unassigned")
        if order.responsibility not in ("owner", "company", "included", "unassigned"):
            raise WorkflowError("Choose a valid expense responsibility.")
        order.notes = str(data.get("notes") or "")[:5000]
        order.state, order.approval = "draft", None
        self.session.add(order)
        rows = data.get("lines")
        if not isinstance(rows, list) or not 1 <= len(rows) <= 100:
            raise WorkflowError("Add between 1 and 100 purchase lines.")
        self.session.flush()
        for row in rows:
            fields = self.catalog_fields(row, purchase=True)
            fields.pop("category")
            line = OrderLine(**fields, quantity=quantity(row.get("quantity")), pack_size=quantity(row.get("pack_size", 1)), unit_price_cents=cents(row.get("unit_price"), "Pack price"),
                             substitution_reason=str(row.get("substitution_reason") or "").strip()[:2000] or None,
                             destination=self._destination(row["location_id"]) if row.get("location_id") else default_destination)
            if not line.destination:
                raise WorkflowError("Choose a verified delivery destination for every purchase line.")
            if line.pack_size > 1 and line.quantity % line.pack_size:
                raise WorkflowError("Purchase whole packs for each delivery destination. Round up the purchased quantity and leave extras as shared stock.")
            order.lines.append(line)
            self.session.flush()
            for allocation in row.get("allocations", []):
                self._allocate(line, allocation)
        destinations = order_destinations(order)
        order.destination = destinations[0] if len(destinations) == 1 else {
            "id": None, "label": f"{len(destinations)} delivery destinations", "kind": "multiple", "address": ""}
        if order.discount_cents > order_totals(order)["subtotal_cents"]:
            raise WorkflowError("Discount cannot exceed the product subtotal.")
        self.audit("order_revised" if order_id else "order_created", {"snapshot": self.order_dict(order), "reason": data.get("change_reason")}, order=order)
        return order

    def order_action(self, order_id, action, data):
        self.purchaser()
        order = get(self.session, PurchaseOrder, order_id)
        check_version(order, data)
        if action == "approve":
            if not self.can_approve:
                raise WorkflowError("An administrator must approve this purchase in Cotton Candy.", 403)
            if order.state != "draft":
                raise WorkflowError("Only draft orders can be approved.")
            order.approval = {"actor": self.actor, "user_id": self.user_id, "version": order.purchase_revision,
                              "approved_at": iso(datetime.utcnow()), "snapshot": self.order_dict(order)}
            order.state = "approved"
        elif action == "place":
            if order.state != "approved" or not order.approval or order.approval["version"] != order.purchase_revision:
                raise WorkflowError("Approve the current order version before recording the purchase.")
            if any(self._destination(destination["id"]) != destination for destination in order_destinations(order)):
                raise WorkflowError("The delivery address changed after approval. Revise the order and obtain approval again.")
            order.reference = required(data.get("reference"), "Retailer order number", 180)
            order.purchased_on = calendar_date(data.get("purchased_on"), "Purchase date")
            if order.purchased_on > date.today():
                raise WorkflowError("The purchase date cannot be in the future.")
            order.placed_at, order.state = datetime.utcnow(), "ordered"
        elif action == "cancel":
            if order.state not in ("draft", "approved"):
                raise WorkflowError("Purchased orders cannot be cancelled here. Record a return or refund.")
            order.state = "cancelled"
            required(data.get("reason"), "Cancellation reason", 2000)
        elif action == "adjust-costs":
            if not self.can_approve:
                raise WorkflowError("An administrator must approve receipt cost corrections.", 403)
            if order.state != "ordered" or not order.attachments:
                raise WorkflowError("Record the purchase and attach the receipt before correcting final costs.")
            reason = required(data.get("reason"), "Cost correction reason", 2000)
            before = self.order_dict(order)
            for name in ("tax", "shipping", "discount"):
                if name in data:
                    setattr(order, name + "_cents", cents(data[name], name.capitalize()))
            seen = set()
            for row in data.get("lines", []):
                line = get(self.session, OrderLine, row.get("line_id"))
                if line.order_id != order.id or line.id in seen:
                    raise WorkflowError("Choose each purchase line at most once.")
                seen.add(line.id)
                line.unit_price_cents = cents(row.get("unit_price"), "Final unit price")
                if "pack_size" in row:
                    line.pack_size = quantity(row["pack_size"])
            totals = order_totals(order)
            if order.discount_cents > totals["subtotal_cents"]:
                raise WorkflowError("Discount cannot exceed the product subtotal.")
            if any(sum(r.amount_cents for r in l.refunds) > totals["line_totals"][l.id] for l in order.lines):
                raise WorkflowError("Corrected costs cannot fall below refunds already recorded.")
            order.reconciled_at, order.actual_charged_cents = None, None
            self.audit("receipt_costs_approved", {"reason": reason, "before": before, "after": self.order_dict(order)}, order=order)
        elif action == "reconcile":
            if order.state != "ordered" or not order.attachments:
                raise WorkflowError("Record the purchase and attach its receipt before reconciling.")
            amount = cents(data.get("actual_charged"), "Actual charged amount")
            if amount != order_totals(order)["total_cents"]:
                raise WorkflowError("The receipt total differs from this order. Resolve the discrepancy before reconciling.")
            order.actual_charged_cents, order.reconciled_at = amount, datetime.utcnow()
        else:
            raise WorkflowError("Unknown purchase action.")
        order.version += 1
        for ticket in {a.item.ticket for line in order.lines for a in line.allocations}:
            self.touch(ticket)
            self.audit(f"order_{action}", {"order_id": order.id, "reference": order.reference,
                       "listing_ids": sorted({item_listing_id(a.item) for line in order.lines for a in line.allocations if a.item.ticket_id == ticket.id and item_listing_id(a.item)})}, ticket=ticket, order=order)
        self.audit(f"purchase_{action}", {"reason": data.get("reason"), "version": order.version,
                                         **({"approval": order.approval} if action == "approve" else {})}, order=order)
        return order

    def add_allocation(self, order_id, data):
        self.purchaser()
        order = get(self.session, PurchaseOrder, order_id)
        check_version(order, data)
        if order.state != "ordered":
            raise WorkflowError("Revise draft orders to change allocations. Only placed stock can be distributed here.")
        line = get(self.session, OrderLine, data.get("line_id"))
        if line.order_id != order.id:
            raise WorkflowError("Purchase line does not belong to this order.")
        allocation = self._allocate(line, data)
        order.version += 1
        self.audit("shared_stock_allocated", {"line_id": line.id, "quantity": str(allocation.quantity)}, ticket=allocation.item.ticket, order=order)
        return order

    def create_shipment(self, order_id, data):
        self.purchaser()
        order = get(self.session, PurchaseOrder, order_id)
        check_version(order, data)
        if order.state != "ordered":
            raise WorkflowError("Record the purchase before adding a delivery.")
        rows = data.get("lines")
        if not isinstance(rows, list) or not rows:
            raise WorkflowError("Select the quantities in this shipment.")
        shipment = Shipment(order=order, carrier=str(data.get("carrier") or "")[:120], tracking=str(data.get("tracking") or "")[:240],
                            expected_at=timestamp(data.get("expected_at")))
        self.session.add(shipment)
        seen = set()
        destinations = set()
        for row in rows:
            line = get(self.session, OrderLine, row.get("line_id"))
            if line.order_id != order.id or line.id in seen:
                raise WorkflowError("Each purchase line can appear only once per shipment.")
            seen.add(line.id)
            destinations.add(line_destination(line)["id"])
            if len(destinations) > 1:
                raise WorkflowError("A shipment can have only one delivery destination. Record separate shipments for separate addresses.")
            amount = quantity(row.get("quantity"))
            shipped = sum((sl.quantity for s in order.shipments if not s.cancelled_reason for sl in s.lines if sl.line_id == line.id), ZERO)
            if shipped + amount > line.quantity:
                raise WorkflowError("Shipment quantities exceed the purchased quantity.")
            shipment.lines.append(ShipmentLine(line_id=line.id, quantity=amount))
        order.version += 1
        self.session.flush()
        self.audit("shipment_created", {"shipment_id": shipment.id, "tracking": shipment.tracking}, order=order)
        affected = shipment_allocations(shipment)
        for ticket in {a.item.ticket for a in affected}:
            self.touch(ticket)
            self.audit("shipment_created", {"shipment_id": shipment.id, "tracking": shipment.tracking,
                       "listing_ids": sorted({item_listing_id(a.item) for a in affected if a.item.ticket_id == ticket.id and item_listing_id(a.item)})}, ticket=ticket, order=order)
        return order

    def deliver_shipment(self, shipment_id, data):
        self.purchaser()
        shipment = get(self.session, Shipment, shipment_id)
        order = shipment.order
        check_version(order, data)
        if shipment.cancelled_reason:
            raise WorkflowError("Cancelled shipments cannot be marked delivered.")
        if shipment.delivered_at:
            raise WorkflowError("This shipment is already recorded as delivered.")
        shipment.delivered_at = timestamp(data.get("delivered_at")) or datetime.utcnow()
        if shipment.delivered_at > datetime.utcnow() + timedelta(minutes=5):
            raise WorkflowError("Delivery cannot be recorded in the future.")
        order.version += 1
        self.audit("shipment_delivered", {"shipment_id": shipment.id, "awaiting_housekeeper": True}, order=order)
        affected = shipment_allocations(shipment)
        for ticket in {a.item.ticket for a in affected}:
            self.touch(ticket)
            self.audit("delivery_reported", {"shipment_id": shipment.id, "awaiting_housekeeper": True,
                       "listing_ids": sorted({item_listing_id(a.item) for a in affected if a.item.ticket_id == ticket.id and item_listing_id(a.item)})}, ticket=ticket, order=order)
        return order

    def update_shipment(self, shipment_id, data):
        self.purchaser()
        shipment = get(self.session, Shipment, shipment_id)
        order = shipment.order
        check_version(order, data)
        if shipment.delivered_at or shipment.cancelled_reason:
            raise WorkflowError("Only active, undelivered shipments can be changed.")
        before = {"carrier": shipment.carrier, "tracking": shipment.tracking, "expected_at": iso(shipment.expected_at)}
        if "cancelled_reason" in data:
            shipment.cancelled_reason = required(data["cancelled_reason"], "Shipment cancellation reason", 2000)
            action = "shipment_cancelled"
        else:
            for name, limit in (("carrier", 120), ("tracking", 240)):
                if name in data:
                    setattr(shipment, name, str(data[name] or "")[:limit])
            if "expected_at" in data:
                shipment.expected_at = timestamp(data["expected_at"])
            action = "shipment_updated"
        order.version += 1
        detail = {"shipment_id": shipment.id, "before": before, "carrier": shipment.carrier,
                  "tracking": shipment.tracking, "expected_at": iso(shipment.expected_at), "reason": shipment.cancelled_reason}
        self.audit(action, detail, order=order)
        affected = shipment_allocations(shipment)
        for ticket in {a.item.ticket for a in affected}:
            self.touch(ticket)
            self.audit(action, {**detail, "listing_ids": sorted({item_listing_id(a.item) for a in affected if a.item.ticket_id == ticket.id and item_listing_id(a.item)})}, ticket=ticket, order=order)
        return order

    def confirm(self, ticket_id, data, *, returning=False):
        ticket = self.ticket(ticket_id)
        check_version(ticket, data)
        if ticket.cancelled_reason:
            raise WorkflowError("Cancelled requests cannot receive supplies.")
        allocation = get(self.session, Allocation, data.get("allocation_id"))
        if allocation.item.ticket_id != ticket.id or allocation.line.order.state != "ordered":
            raise WorkflowError("Choose a purchased allocation from this request.")
        self.item_access(allocation.item)
        staff = get(self.session, Staff, data.get("staff_id"))
        original_listing_id = item_listing_id(allocation.item)
        prop = self.property(data.get("listing_id") or original_listing_id)
        if original_listing_id and prop["id"] != original_listing_id:
            raise WorkflowError("Confirm supplies at the property's original destination.")
        if not staff.active or prop["id"] not in staff.listing_ids:
            raise WorkflowError("Confirmation must come from an active housekeeper assigned to this property.")
        if self.staff_scope is not None and staff.id != self.staff_scope.id:
            raise WorkflowError("You can only confirm receipt as yourself.", 403)
        evidence = data.get("evidence") or {}
        if not isinstance(evidence, dict):
            raise WorkflowError("Confirmation evidence must be an object.")
        if self.api or self.staff_scope is None:
            required(evidence.get("reference"), "Housekeeper confirmation reference (message ID or evidence)", 2000)
        amount = quantity(data.get("quantity"))
        if returning:
            available = sum((c.quantity for c in allocation.confirmations if c.listing_id == prop["id"]), ZERO)
            if amount > available:
                raise WorkflowError("Return quantity exceeds supplies previously confirmed at this property.")
            amount = -amount
        elif data.get("confirm_delivery") is True:
            if amount > active_quantity(allocation) - confirmed_quantity(allocation):
                raise WorkflowError("Confirmed quantities cannot exceed the outstanding purchased quantity.")
        elif amount > allocation_delivered(allocation) - confirmed_quantity(allocation):
            raise WorkflowError("Confirm only delivered quantities that have not already been confirmed.")
        confirmation = Confirmation(allocation=allocation, staff_id=staff.id, listing_id=prop["id"], property_name=prop["name"],
                                    quantity=amount, note=required(data.get("note"), "Confirmation note", 4000),
                                    evidence={"reference": evidence.get("reference"), "recorded_by": self.actor, "housekeeper": staff.name})
        self.session.add(confirmation)
        self.touch(ticket)
        allocation.line.order.version += 1
        self.session.flush()
        self.audit("property_return" if returning else "property_confirmed", {
            "allocation_id": allocation.id, "quantity": str(amount), "housekeeper": staff.name,
            "listing_id": prop["id"], "note": confirmation.note, "evidence": confirmation.evidence,
        }, ticket=ticket, order=allocation.line.order)
        return ticket

    def confirm_batch(self, ticket_id, data):
        """One atomic, idempotent submission for explicitly selected receipts."""
        ticket = self.ticket(ticket_id)
        check_version(ticket, data)
        rows = data.get("confirmations")
        if not isinstance(rows, list) or not 1 <= len(rows) <= 100:
            raise WorkflowError("Select between 1 and 100 quantities to confirm.")
        seen = set()
        for row in rows:
            if not isinstance(row, dict):
                raise WorkflowError("Each receipt must be an object.")
            allocation_id = get(self.session, Allocation, row.get("allocation_id")).id
            if allocation_id in seen:
                raise WorkflowError("Select each purchased allocation only once.")
            seen.add(allocation_id)
            self.confirm(ticket.id, {**row, "version": ticket.version,
                         "note": data.get("note"), "evidence": data.get("evidence") or {},
                         "confirm_delivery": data.get("confirm_delivery") is True})
        return ticket

    def fulfillment_issue(self, ticket_id, data):
        ticket = self.ticket(ticket_id)
        check_version(ticket, data)
        allocation = get(self.session, Allocation, data.get("allocation_id"))
        if allocation.item.ticket_id != ticket.id or allocation.line.order.state != "ordered":
            raise WorkflowError("Choose a purchased allocation from this request.")
        self.item_access(allocation.item)
        amount = quantity(data.get("quantity"))
        if amount > active_quantity(allocation) - confirmed_quantity(allocation):
            raise WorkflowError("Report only outstanding quantities. Supplies already confirmed at the property require a return.")
        kind = data.get("kind")
        if kind not in ("lost", "damaged", "incorrect", "retailer_cancelled"):
            raise WorkflowError("Choose a valid fulfillment problem.")
        reason = required(data.get("reason"), "Problem details", 4000)
        excluded = min(amount, max(ZERO, allocation_delivered(allocation) - confirmed_quantity(allocation)))
        allocation.problem_quantity = (allocation.problem_quantity or ZERO) + amount
        allocation.excluded_delivered_quantity = (allocation.excluded_delivered_quantity or ZERO) + excluded
        self.touch(ticket)
        allocation.line.order.version += 1
        self.audit("fulfillment_issue", {"allocation_id": allocation.id, "quantity": str(amount), "kind": kind,
                                         "listing_id": item_listing_id(allocation.item),
                                         "reason": reason, "replacement_need_reopened": True}, ticket=ticket, order=allocation.line.order)
        return ticket

    def refund(self, order_id, data):
        self.purchaser()
        order = get(self.session, PurchaseOrder, order_id)
        check_version(order, data)
        line = get(self.session, OrderLine, data.get("line_id"))
        if order.state != "ordered" or line.order_id != order.id:
            raise WorkflowError("Choose a line from this purchased order.")
        amount = cents(data.get("amount"), "Refund")
        maximum = order_totals(order)["line_totals"][line.id]
        if not amount or amount + sum(r.amount_cents for r in line.refunds) > maximum:
            raise WorkflowError("Refunds must be positive and cannot exceed the line's full allocated cost.")
        refunded_on = calendar_date(data.get("refunded_on"), "Refund date")
        if refunded_on > date.today() or refunded_on < order.purchased_on:
            raise WorkflowError("Refund date must fall between the purchase date and today.")
        refund = Refund(line=line, amount_cents=amount, reason=required(data.get("reason"), "Refund reason", 2000), refunded_on=refunded_on)
        self.session.add(refund)
        order.version += 1
        self.audit("refund_recorded", {"line_id": line.id, "amount_cents": amount, "reason": refund.reason}, order=order)
        return order

    def ticket_dict(self, ticket, detail=False):
        visible_items = [i for i in ticket.items if self.staff_scope is None or item_listing_id(i) in self.staff_scope.listing_ids]
        properties = [p for p in ticket_properties(ticket) if self.staff_scope is None or p["id"] in self.staff_scope.listing_ids]
        status = ticket_status(ticket, visible_items)
        if not hasattr(self, "_directory_staff"):
            self._directory_staff = list(self.session.scalars(select(Staff).where(Staff.active.is_(True))))
        property_rows = []
        for prop in properties:
            rows = [i for i in visible_items if item_listing_id(i) == prop["id"]]
            property_status = ticket_status(ticket, rows)
            property_rows.append({**prop, "status": property_status, "status_label": STATUS_LABELS[property_status],
                                  "ready": property_status == "completed", "required_by": iso(ticket.required_by),
                                  "housekeepers": [{"id": s.id, "name": s.name} for s in self._directory_staff
                                                   if prop["id"] in s.listing_ids and (self.staff_scope is None or s.id == self.staff_scope.id)]})
        items = []
        for item in visible_items:
            progress = {k: float(v) if v is not None else None for k, v in item_progress(item).items()}
            items.append({"id": item.id, "listing_id": item_listing_id(item), "property": item_property(item).get("name"),
                          "catalog_id": item.catalog_id, "name": item.name, "category": item.category,
                          "unit": item.unit, "quantity": float(item.quantity) if item.quantity is not None else None,
                          "cancellation_reason": item.cancellation_reason, **progress,
                          "allocations": [{"id": a.id, "order_id": a.line.order_id, "line_id": a.line_id,
                                           "vendor": a.line.order.vendor, "order_state": a.line.order.state,
                                           "quantity": float(active_quantity(a)), "confirmed": float(confirmed_quantity(a)),
                                           "problem_quantity": float(a.problem_quantity or ZERO),
                                           "reportable": float(max(ZERO, active_quantity(a) - confirmed_quantity(a))),
                                           "confirmable": float(max(ZERO, allocation_delivered(a) - confirmed_quantity(a))) if a.line.order.state == "ordered" else 0,
                                           "property_confirmable": float(max(ZERO, active_quantity(a) - confirmed_quantity(a))) if a.line.order.state == "ordered" else 0,
                                           "confirmations": [{"quantity": float(c.quantity), "property": c.property_name,
                                                              "housekeeper": c.evidence.get("housekeeper"), "note": c.note, "at": iso(c.created_at)} for c in a.confirmations]}
                                          for a in item.allocations if a.line.order.state != "cancelled"]})
        result = {"id": ticket.id, "code": f"PR-{ticket.id:04d}", "version": ticket.version, "title": ticket.title,
                  "listing_id": properties[0]["id"] if len(properties) == 1 else None,
                  "listing_ids": [p["id"] for p in properties], "properties": property_rows,
                  "property": ", ".join(p.get("name", str(p["id"])) for p in properties[:3]) + (f" + {len(properties)-3} more" if len(properties) > 3 else "") if properties else (f"Shared storage #{ticket.storage_location_id}" if ticket.storage_location_id else "Property needed"),
                  "storage_location_id": ticket.storage_location_id,
                  "city": properties[0].get("city") if len(properties) == 1 else None,
                  "timezone": properties[0].get("timezone", "UTC") if properties and len({p.get("timezone", "UTC") for p in properties}) == 1 else "UTC",
                  "tags": sorted({tag for p in properties for tag in p.get("tags", [])}),
                  "requester_name": "Shared request" if self.staff_scope and ticket.requester_staff_id != self.staff_scope.id else ticket.requester_name,
                  "requester_staff_id": None if self.staff_scope and ticket.requester_staff_id != self.staff_scope.id else ticket.requester_staff_id, "owner_user_id": ticket.owner_user_id,
                  "owner_name": self.users.get(ticket.owner_user_id, {}).get("name", "Unassigned"),
                  "priority": ticket.priority, "required_by": iso(ticket.required_by), "response_due_at": iso(ticket.response_due_at),
                  "reviewed_at": iso(ticket.reviewed_at), "created_at": iso(ticket.created_at),
                  "status": status, "status_label": STATUS_LABELS[status], "flags": ticket_flags(ticket, items=visible_items),
                  "missing_information": ticket_missing_information(ticket, visible_items), "items": items,
                  "ready_properties": sum(p["ready"] for p in property_rows), "property_count": len(property_rows),
                  "cancelled_reason": ticket.cancelled_reason,
                  "fulfilled_items": sum(1 for i in items if i["requested"] is not None and i["remaining"] == 0)}
        if ticket.owner_user_id not in self.users and status not in ("completed", "cancelled") and "Unassigned" not in result["flags"]:
            result["flags"].append("Unassigned")
        if detail:
            result["messages"] = [{"source": m.source, "message_id": m.message_id, "body": m.body, "url": m.source_url, "at": iso(m.created_at)} for m in ticket.messages] if self.staff_scope is None else []
            result["messages_private"] = self.staff_scope is not None
            events = self.activity(ticket_id=ticket.id)
            result["activity"] = events if self.staff_scope is None else [e for e in events if e["action"] in ("property_confirmed", "property_return") and e["detail"].get("listing_id") in self.staff_scope.listing_ids]
            ids = [p["id"] for p in properties]
            result["possible_duplicates"] = [] if self.staff_scope is not None else [{"id": t.id, "code": f"PR-{t.id:04d}", "title": t.title} for t in self.session.scalars(select(Ticket).where(or_(Ticket.listing_id.in_(ids), Ticket.items.any(RequestItem.listing_id.in_(ids))), Ticket.id != ticket.id)).all()
                                              if ticket_status(t) not in ("completed", "cancelled") and {(item_listing_id(i), i.name.casefold()) for i in t.items} & {(item_listing_id(i), i.name.casefold()) for i in ticket.items}]
        return result

    def activity(self, *, ticket_id=None, order_id=None):
        query = select(AuditEvent).order_by(AuditEvent.id.desc())
        query = query.where(AuditEvent.ticket_id == ticket_id) if ticket_id else query.where(
            AuditEvent.order_id == order_id, or_(AuditEvent.ticket_id.is_(None), AuditEvent.action.in_(
                ("property_confirmed", "property_return", "fulfillment_issue", "shared_stock_allocated"))))
        return [{"id": e.id, "action": e.action, "actor": e.actor, "detail": e.detail, "at": iso(e.created_at)} for e in self.session.scalars(query.limit(200))]

    def order_dict(self, order, detail=False):
        totals = order_totals(order)
        allocation_costs = {}
        for line in order.lines:
            shares = split_cents(totals["line_totals"][line.id], [a.quantity for a in line.allocations] + [line.quantity - sum((a.quantity for a in line.allocations), ZERO)])
            allocation_costs.update({a.id: value for a, value in zip(line.allocations, shares)})
        result = {"id": order.id, "code": f"PO-{order.id:04d}", "version": order.version, "purchase_revision": order.purchase_revision, "vendor": order.vendor,
                  "reference": order.reference, "currency": order.currency, "destination": order.destination if self.staff_scope is None else {"label": order.destination["label"]},
                  "destinations": order_destinations(order) if self.staff_scope is None else [],
                  "state": order.state or "draft", "tax_cents": order.tax_cents or 0, "shipping_cents": order.shipping_cents or 0,
                  "discount_cents": order.discount_cents or 0, "responsibility": order.responsibility, "notes": order.notes,
                  "subtotal_cents": totals["subtotal_cents"], "total_cents": totals["total_cents"],
                  "refund_cents": sum(r.amount_cents for l in order.lines for r in l.refunds),
                  "purchased_on": order.purchased_on.isoformat() if order.purchased_on else None,
                  "approval": {k: v for k, v in (order.approval or {}).items() if k != "snapshot"},
                  "reconciled_at": iso(order.reconciled_at), "actual_charged_cents": order.actual_charged_cents,
                  "lines": [{"id": line.id, "catalog_id": line.catalog_id, "name": line.name, "unit": line.unit,
                             "quantity": float(line.quantity), "pack_size": float(line.pack_size or 1), "unit_price_cents": line.unit_price_cents,
                             "destination": line_destination(line) if self.staff_scope is None else None,
                             "landed_cents": totals["line_totals"][line.id], "substitution_reason": line.substitution_reason,
                             "unallocated": float(line.quantity - sum((a.quantity for a in line.allocations), ZERO)),
                             "unshipped": float(line.quantity - sum((sl.quantity for s in order.shipments if not s.cancelled_reason for sl in s.lines if sl.line_id == line.id), ZERO)),
                             "delivered": float(delivered_quantity(line)),
                             "allocations": [{"id": a.id, "item_id": a.item_id, "ticket_id": a.item.ticket_id,
                                              "request": f"PR-{a.item.ticket_id:04d}", "listing_id": item_listing_id(a.item),
                                              "property": item_property(a.item).get("name", "Shared storage"),
                                              "allocated_cents": allocation_costs[a.id], "quantity": float(a.quantity)} for a in line.allocations],
                             "refunds": [{"amount_cents": r.amount_cents, "reason": r.reason, "date": r.refunded_on.isoformat()} for r in line.refunds]}
                            for line in order.lines],
                  "shipments": [{"id": s.id, "carrier": s.carrier, "tracking": s.tracking, "expected_at": iso(s.expected_at), "delivered_at": iso(s.delivered_at), "cancelled_reason": s.cancelled_reason,
                                 "destination": next((line_destination(line) for line in order.lines if any(sl.line_id == line.id for sl in s.lines)), order.destination),
                                 "lines": [{"line_id": sl.line_id, "quantity": float(sl.quantity)} for sl in s.lines]} for s in order.shipments],
                  "attachments": [{"id": a.id, "name": a.name} for a in order.attachments]}
        if detail:
            result["activity"] = self.activity(order_id=order.id)
            if self.staff_scope is None:
                result["approved_snapshot"] = (order.approval or {}).get("snapshot")
        return result

    def report(self, month):
        try:
            start = date.fromisoformat(month + "-01")
        except (ValueError, TypeError):
            raise WorkflowError("Choose a reporting month in YYYY-MM format.")
        end = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
        groups = defaultdict(lambda: {"purchased_cents": 0, "refund_cents": 0, "unallocated_cents": 0, "properties": defaultdict(lambda: {"allocated_cents": 0, "confirmed_cents": 0, "categories": defaultdict(int)})})
        orders = self.session.scalars(order_query().where(PurchaseOrder.state == "ordered", or_(
            and_(PurchaseOrder.purchased_on >= start, PurchaseOrder.purchased_on < end),
            PurchaseOrder.lines.any(OrderLine.refunds.any(and_(Refund.refunded_on >= start, Refund.refunded_on < end))),
        ))).all()
        for order in orders:
            included = start <= order.purchased_on < end
            if not included and not any(start <= r.refunded_on < end for l in order.lines for r in l.refunds):
                continue
            group = groups[order.currency]
            if included:
                group["purchased_cents"] += order_totals(order)["total_cents"]
            line_totals = order_totals(order)["line_totals"]
            for line in order.lines:
                refund = sum(r.amount_cents for r in line.refunds if start <= r.refunded_on < end)
                group["refund_cents"] += refund
                amount = (line_totals[line.id] if included else 0) - refund
                weights = [a.quantity for a in line.allocations]
                unused = line.quantity - sum(weights, ZERO)
                shares = split_cents(amount, weights + [unused])
                group["unallocated_cents"] += shares[-1]
                for allocation, share in zip(line.allocations, shares):
                    ticket = allocation.item.ticket
                    if item_listing_id(allocation.item):
                        row = group["properties"][item_listing_id(allocation.item)]
                        row["name"] = item_property(allocation.item).get("name", "Property")
                        row["allocated_cents"] += share
                        row["categories"][allocation.item.category] += share
                        confirmed = sum((c.quantity for c in allocation.confirmations if c.created_at.date() < end), ZERO)
                        row["confirmed_cents"] += split_cents(share, [confirmed, allocation.quantity - confirmed])[0]
                    else:
                        quantities = defaultdict(Decimal)
                        names = {}
                        for c in allocation.confirmations:
                            if c.created_at.date() < end:
                                quantities[c.listing_id] += c.quantity
                                names[c.listing_id] = c.property_name
                        ids = sorted(quantities)
                        distributed = sum(quantities.values(), ZERO)
                        portions = split_cents(share, [quantities[i] for i in ids] + [allocation.quantity - distributed])
                        group["unallocated_cents"] += portions[-1]
                        for listing_id, value in zip(ids, portions):
                            row = group["properties"][listing_id]
                            row["name"] = names[listing_id]
                            row["allocated_cents"] += value
                            row["confirmed_cents"] += value
                            row["categories"][allocation.item.category] += value
        result = []
        for currency, group in sorted(groups.items()):
            result.append({"currency": currency, "purchased_cents": group["purchased_cents"], "refund_cents": group["refund_cents"],
                           "net_cents": group["purchased_cents"] - group["refund_cents"], "unallocated_cents": group["unallocated_cents"],
                           "properties": [{"listing_id": key, **value, "categories": dict(value["categories"])} for key, value in sorted(group["properties"].items())]})
        return {"month": month, "basis": "Purchases by purchase date; refunds by refund date. Confirmed values use property confirmations through month-end. Currencies are reported separately.", "currencies": result}


def safe_url(value):
    if not value:
        return None
    from urllib.parse import urlparse
    value = required(value, "Source URL", 4000)
    if urlparse(value).scheme not in ("https", "http"):
        raise WorkflowError("Source links must use http or https.")
    return value


def request_query():
    return select(Ticket).options(
        selectinload(Ticket.items).selectinload(RequestItem.allocations).selectinload(Allocation.line).selectinload(OrderLine.order).selectinload(PurchaseOrder.shipments).selectinload(Shipment.lines),
        selectinload(Ticket.items).selectinload(RequestItem.allocations).selectinload(Allocation.line).selectinload(OrderLine.allocations).selectinload(Allocation.confirmations),
        selectinload(Ticket.items).selectinload(RequestItem.allocations).selectinload(Allocation.confirmations),
        selectinload(Ticket.messages),
    )


def order_query():
    return select(PurchaseOrder).options(
        selectinload(PurchaseOrder.lines).selectinload(OrderLine.allocations).selectinload(Allocation.item).selectinload(RequestItem.ticket),
        selectinload(PurchaseOrder.lines).selectinload(OrderLine.allocations).selectinload(Allocation.confirmations),
        selectinload(PurchaseOrder.lines).selectinload(OrderLine.refunds),
        selectinload(PurchaseOrder.shipments).selectinload(Shipment.lines),
        selectinload(PurchaseOrder.attachments),
    )
