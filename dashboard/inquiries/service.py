"""Transactional inquiry intake and staff workflow; no guest-facing messaging."""

import hashlib
import json
import re
from datetime import date, datetime, timedelta, timezone
from email.utils import parseaddr
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from uuid import UUID

from sqlalchemy import func, or_, select

from .models import Activity, Guest, Lead, Settings, SourceMessage, utcnow
from .parser import parse_email


STATUSES = ("New", "In Progress", "Follow-up Required", "Booked", "Closed — Not Booked")
QUALIFICATION_FIELDS = {"guest_count", "adults", "children", "pets", "stay_requirements", "date_flexibility",
                        "alternative_dates", "budget", "preferred_contact_method", "quote_status"}
GUEST_FIELDS = {"guest_name", "email", "phone", "listing_id", "tag_id", "property_text", "check_in", "check_out",
                "details", "trip_type"} | QUALIFICATION_FIELDS
STAFF_FIELDS = GUEST_FIELDS | {"internal_notes", "assigned_user_id", "status", "next_action", "follow_up_at", "last_contact_at"}
DEFAULT_SOURCES = [{"id": "enchanted-havens-email", "mailbox": "larry@enchantedhavens.com", "brand": "Enchanted Havens",
                    "subject_prefixes": ["New stay inquiry EH-"], "senders": ["yunhang.chen@gmail.com"]}]
SOURCE_LABELS = {"manual": "Manual entry", "enchanted-havens-email": "EH website",
                 "luminary-resorts-email": "LR website"}


def source_label(source, sources=(), brand=None):
    """Label the recorded origin, including retired and future intake sources."""
    if source == "manual":
        return SOURCE_LABELS[source]
    policy = next((s for s in sources if s["id"] == source), {})
    return policy.get("label") or SOURCE_LABELS.get(source) or policy.get("brand") or brand or source


class InquiryError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def text_value(value, name, limit=10000, required=False):
    if value is None or value == "":
        if required:
            raise InquiryError(f"{name} is required.")
        return None
    if not isinstance(value, str) or len(value) > limit:
        raise InquiryError(f"{name} must be text of at most {limit} characters.")
    cleaned = value.strip()
    if required and not cleaned:
        raise InquiryError(f"{name} is required.")
    return cleaned or None


def instant(value, name):
    if not value:
        return None
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if not result.tzinfo:
            raise ValueError()
        return result.astimezone(timezone.utc)
    except (TypeError, ValueError, AttributeError):
        raise InquiryError(f"{name} must include its UTC offset.") from None


def iso(value):
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    return value.isoformat() if isinstance(value, (date, datetime)) else value


def get_value(lead, key):
    return (lead.qualification or {}).get(key) if key in QUALIFICATION_FIELDS else getattr(lead, key)


def set_value(lead, key, value):
    if key in QUALIFICATION_FIELDS:
        lead.qualification = {**(lead.qualification or {}), key: value}
    else:
        setattr(lead, key, value)


class InquiryService:
    def __init__(self, db, *, properties=None, tags=None, users=None, sources=None, actor="Larry", now=None):
        self.db = db
        self.properties = properties or {}
        self.tags = tags or {}
        self.users = users or {}
        self.sources = DEFAULT_SOURCES if sources is None else sources
        self.actor = actor
        self.now = now or utcnow()
        self._settings = None

    def settings(self):
        if self._settings is None:
            self._settings = self.db.get(Settings, 1) or Settings(id=1, timezone="America/Los_Angeles", version=1)
        return self._settings

    def lead(self, lead_id):
        result = self.db.get(Lead, lead_id)
        if not result:
            raise InquiryError("Inquiry not found.", 404)
        return result

    def activity(self, lead, action, changes, message=None, actor=None):
        self.db.add(Activity(lead_id=lead.id, actor=actor or self.actor, action=action,
                             changes=changes, message_id=message.id if message else None))

    def validate_field(self, key, value):
        if key in ("listing_id", "tag_id", "assigned_user_id"):
            directory = {"listing_id": self.properties, "tag_id": self.tags, "assigned_user_id": self.users}[key]
            if value in (None, ""):
                return None
            if isinstance(value, bool) or not isinstance(value, int) or value not in directory:
                raise InquiryError(f"Choose an available {key.replace('_id', '').replace('_', ' ')}.")
            return value
        if key in ("guest_count", "adults", "children"):
            if value is None or value == "":
                return None
            if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 999 or key == "guest_count" and value == 0:
                raise InquiryError(f"{key} must be a valid whole number.")
            return value
        if key in ("check_in", "check_out"):
            if not value:
                return None
            try:
                return date.fromisoformat(value)
            except (ValueError, TypeError):
                raise InquiryError(f"{key} must be YYYY-MM-DD.") from None
        if key in ("follow_up_at", "last_contact_at"):
            return instant(value, key)
        if key == "status":
            if value not in STATUSES:
                raise InquiryError("Choose a valid lead status.")
            return value
        limits = {"guest_name": 240, "email": 320, "phone": 100, "property_text": 500, "trip_type": 240}
        result = text_value(value, key, limits.get(key, 10000))
        if key == "email" and result:
            if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", result):
                raise InquiryError("Enter a valid guest email.")
            result = result.lower()
        return result

    def validate_state(self, lead):
        if lead.listing_id and lead.tag_id:
            raise InquiryError("Choose a property or a tag, not both.")
        if lead.check_in and lead.check_out and lead.check_out <= lead.check_in:
            raise InquiryError("Checkout must be after check-in.")
        if lead.status == "Follow-up Required" and not (lead.assigned_user_id and lead.next_action and lead.follow_up_at):
            raise InquiryError("Follow-up Required needs a responsible agent, next action, and due date/time.")

    def create(self, payload):
        unknown = set(payload) - STAFF_FIELDS - {"creation_id", "internal_test"}
        if unknown:
            raise InquiryError("Unknown inquiry fields: " + ", ".join(sorted(unknown)))
        try:
            creation_id = UUID(payload.get("creation_id"))
        except (ValueError, TypeError, AttributeError):
            raise InquiryError("A valid creation_id is required to safely retry this submission.") from None
        internal_test = payload.get("internal_test", False)
        if not isinstance(internal_test, bool):
            raise InquiryError("internal_test must be a boolean.")
        lead = Lead(source="manual", reference=f"MANUAL-{creation_id.hex}", received_at=self.now,
                    status="New", version=1, is_test=internal_test, qualification={}, review_flags=[],
                    staff_fields=sorted(GUEST_FIELDS & set(payload)), needs_reply=False,
                    created_at=self.now, updated_at=self.now, updated_by=self.actor)
        for key in STAFF_FIELDS & set(payload):
            set_value(lead, key, self.validate_field(key, payload[key]))
        if not lead.guest_name:
            raise InquiryError("Guest name is required.")
        if not (lead.email or lead.phone):
            raise InquiryError("Enter an email address or phone number for the guest.")
        self.validate_state(lead)
        lead.original_notes = lead.details or ""
        changes = {key: {"before": None, "after": iso(get_value(lead, key))}
                   for key in sorted(STAFF_FIELDS) if get_value(lead, key) is not None}
        changes["is_test"] = {"before": None, "after": internal_test}
        existing = self.db.scalar(select(Lead).where(Lead.source == "manual", Lead.reference == lead.reference))
        if existing:
            original = self.db.scalar(select(Activity).where(Activity.lead_id == existing.id,
                                      Activity.action == "Lead created manually").order_by(Activity.id).limit(1))
            if not original or original.changes != changes:
                raise InquiryError("This submission was already saved with different details. Open the saved inquiry before changing it.", 409)
            return {**self.serialize(existing, detail=True), "replayed": True}
        lead.guest_id = self.identity(lead.email).id
        self.db.add(lead)
        self.db.flush()
        self.activity(lead, "Lead created manually", changes)
        self.db.flush()
        return {**self.serialize(lead, detail=True), "replayed": False}

    def patch(self, lead_id, payload):
        lead = self.lead(lead_id)
        if type(payload.get("version")) is not int or payload["version"] != lead.version:
            raise InquiryError("This inquiry changed. Reload it to review the latest updates before saving.", 409)
        unknown = set(payload) - STAFF_FIELDS - {"version", "acknowledge_reply", "resolve_flags"}
        if unknown:
            raise InquiryError("Unknown inquiry fields: " + ", ".join(sorted(unknown)))
        changes = {}
        protected = set(lead.staff_fields or [])
        for key in STAFF_FIELDS & set(payload):
            value = self.validate_field(key, payload[key])
            old = get_value(lead, key)
            if old != value:
                changes[key] = {"before": iso(old), "after": iso(value)}
                set_value(lead, key, value)
                if key in GUEST_FIELDS:
                    protected.add(key)
        self.validate_state(lead)
        if payload.get("acknowledge_reply") is True and lead.needs_reply:
            lead.needs_reply = False
            changes["needs_reply"] = {"before": True, "after": False}
        if payload.get("resolve_flags") is True and lead.review_flags:
            changes["review_flags"] = {"before": lead.review_flags, "after": []}
            lead.review_flags = []
        if changes:
            lead.staff_fields = sorted(protected)
            lead.version += 1
            lead.updated_at, lead.updated_by = self.now, self.actor
            self.activity(lead, "Staff updated inquiry", changes)
        self.db.flush()
        return self.serialize(lead, detail=True)

    def identity(self, email):
        # Names/phones never silently merge profiles; relay addresses stay unmerged.
        key = email.lower() if email and not any(v in email.lower() for v in ("@guest.airbnb.", "@guest.booking.", "@reply.")) else None
        guest = self.db.scalar(select(Guest).where(Guest.email_key == key)) if key else None
        if not guest:
            guest = Guest(email_key=key)
            self.db.add(guest)
            self.db.flush()
        return guest

    def source_policy(self, source, mailbox):
        return next((s for s in self.sources if s["id"] == source and s["mailbox"].lower() == mailbox.lower()), None)

    def known_guest_emails(self, lead):
        # Keep the reply loop working after an explicit contact-address change.
        # Only applied/audited values count; unknown participant proposals do not.
        guest = self.db.get(Guest, lead.guest_id)
        emails = {lead.email, guest.email_key if guest else None}
        for changes in self.db.scalars(select(Activity.changes).where(Activity.lead_id == lead.id)):
            change = changes.get("email")
            if isinstance(change, dict):
                emails.update((change.get("before"), change.get("after")))
        return {v.lower() for v in emails if v}

    def ingest(self, payload):
        """Persist the source before parsing, then safely apply Larry's extraction.

        Caller commits even a failed processing result, preserving retryable input.
        Writes are serialized with an advisory lock by routes/CLI.
        """
        if not isinstance(payload, dict):
            raise InquiryError("An email object is required.")
        internal_test = payload.get("internal_test", False)
        if not isinstance(internal_test, bool):
            raise InquiryError("internal_test must be a boolean.")
        mailbox = text_value(payload.get("mailbox"), "mailbox", 320, True).lower()
        source = text_value(payload.get("source"), "source", 80, True)
        policy = self.source_policy(source, mailbox)
        if not policy:
            raise InquiryError("This inbox/source is not configured for inquiry intake.", 403)
        values = {key: text_value(payload.get(key), key, limit, required) for key, limit, required in (
            ("message_id", 240, True), ("thread_id", 240, False), ("internet_message_id", 500, False),
            ("in_reply_to", 500, False), ("sender", 320, True), ("subject", 2000, True), ("body", 250000, True))}
        received = instant(payload.get("received_at"), "received_at")
        if not received:
            raise InquiryError("Email metadata received_at is required.")
        # Do not discard whitespace or rewrite original source content.
        values["body"] = payload["body"]
        values["subject"] = payload["subject"]
        fingerprint = hashlib.sha256(json.dumps({**values, "received_at": iso(received)}, sort_keys=True).encode()).hexdigest()
        message = self.db.scalar(select(SourceMessage).where(SourceMessage.mailbox == mailbox, SourceMessage.message_id == values["message_id"]))
        if message:
            if message.fingerprint != fingerprint or message.source != source:
                raise InquiryError("This message ID already belongs to different source content.", 409)
            if message.processing_status in ("processed", "ignored"):
                return {"message_id": message.id, "lead_id": message.lead_id, "processing_status": message.processing_status, "replayed": True}
        else:
            message = SourceMessage(mailbox=mailbox, source=source, **values, received_at=received, fingerprint=fingerprint,
                                    is_test=internal_test,
                                    source_url="https://mail.google.com/mail/u/?authuser=" + mailbox + "#all/" + values["message_id"])
            self.db.add(message)
            self.db.flush()
        message.attempts += 1
        if isinstance(payload.get("extraction"), dict):
            message.extraction = payload["extraction"]
        try:
            # A savepoint prevents partial lead/field/audit writes on parsing failure.
            with self.db.begin_nested():
                result = self.process(message, payload.get("extraction"), policy)
            return result
        except InquiryError as exc:
            message.processing_status = "failed"
            message.error = str(exc)
            return {"message_id": message.id, "lead_id": message.lead_id, "processing_status": "failed", "error": str(exc), "replayed": False}

    def process(self, message, extraction, policy):
        if extraction is None:
            extraction = parse_email(message.subject, message.body)
        if not isinstance(extraction, dict) or not isinstance(extraction.get("fields", {}), dict):
            raise InquiryError("Larry extraction must contain a fields object.")
        fields = extraction.get("fields", {})
        if set(fields) - GUEST_FIELDS:
            raise InquiryError("Email extraction contains unsupported or internal staff fields.")
        reference = text_value(extraction.get("reference"), "reference", 160)
        reference = reference.upper() if reference else None
        message.reference = reference
        message.extraction = extraction
        lead = self.db.scalar(select(Lead).where(Lead.source == message.source, Lead.reference == reference)) if reference else None
        thread_matches = list(self.db.scalars(select(Lead).join(SourceMessage, SourceMessage.lead_id == Lead.id).where(
            SourceMessage.mailbox == message.mailbox, SourceMessage.thread_id == message.thread_id).distinct())) if message.thread_id else []
        if not lead and not reference and message.in_reply_to:
            previous = self.db.scalar(select(SourceMessage).where(SourceMessage.mailbox == message.mailbox,
                                                                  SourceMessage.internet_message_id == message.in_reply_to))
            lead = self.db.get(Lead, previous.lead_id) if previous and previous.lead_id else None
        if not lead and not reference and len(thread_matches) == 1:
            lead = thread_matches[0]
        if not lead and not reference and len(thread_matches) > 1:
            raise InquiryError("This email thread contains multiple inquiries; an inquiry reference is needed.")
        sender = parseaddr(message.sender)[1].lower()
        initial = not re.match(r"(?i)^(?:re|fw|fwd):", message.subject)
        approved = initial and any(message.subject.lower().startswith(v.lower()) for v in policy["subject_prefixes"])
        approved = approved and (not policy.get("senders") or sender in policy["senders"])
        new_trip = bool(reference and extraction.get("new_inquiry") is True and any(sender in self.known_guest_emails(v) for v in thread_matches))
        if not lead and not approved and reference and thread_matches and not new_trip:
            raise InquiryError("A different reference was received in an existing thread. Review whether this is a new trip before importing it.")
        if not lead and not approved and not new_trip:
            message.processing_status = "ignored"
            return {"message_id": message.id, "lead_id": None, "processing_status": "ignored", "replayed": False}
        if not lead:
            normalized = {key: self.validate_field(key, value) for key, value in fields.items()}
            guest = self.identity(normalized.get("email"))
            lead = Lead(guest_id=guest.id, source=message.source, brand=policy.get("brand"), reference=reference,
                        is_test=message.is_test,
                        received_at=message.received_at, original_notes=message.body, qualification={}, review_flags=[], staff_fields=[],
                        status="New", version=1, updated_by="Larry")
            self.db.add(lead)
            self.db.flush()
            created = True
        else:
            created = False
            if message.is_test and not lead.is_test:
                raise InquiryError("An internal test cannot be merged into a genuine inquiry.")
        message.lead_id = lead.id
        # A matched reference alone does not make internal staff mail a guest reply.
        guest_sender = sender in self.known_guest_emails(lead)
        website_sender = sender in policy.get("senders", []) and initial
        changes, flags = {}, list(lead.review_flags or [])
        reasons = extraction.get("review_reasons", [])
        if not isinstance(reasons, list) or any(not isinstance(v, str) or len(v) > 1000 for v in reasons) or len(reasons) > 30:
            raise InquiryError("review_reasons must be a short list of review notes.")
        for reason in reasons:
            flags.append({"message_id": message.id, "reason": reason})
        if not created and not guest_sender and not website_sender:
            # Preserve staff/unknown-party messages for context, without accepting their fields.
            fields = {}
            flags.append({"message_id": message.id, "reason": "Non-guest participant: review this message before applying any changes."})
        normalized = {key: self.validate_field(key, value) for key, value in fields.items()}
        if normalized.get("listing_id") and normalized.get("tag_id"):
            raise InquiryError("Email extraction must choose a property or a tag, not both.")
        if normalized.get("listing_id"):
            normalized["tag_id"] = None
        elif normalized.get("tag_id"):
            normalized["listing_id"] = None
        if normalized.get("property_text") and "listing_id" not in normalized and "tag_id" not in normalized:
            query = normalized["property_text"].strip().rstrip(".").casefold()
            query = re.sub(r"(?i)^I'm interested in\s+", "", query).strip()
            matches = [key for key, prop in self.properties.items() if query in {v.casefold() for v in [prop["name"], *prop.get("aliases", [])] if v}]
            tag_matches = [key for key, tag in self.tags.items() if query == tag["name"].casefold()]
            if len(matches) == 1:
                normalized.update(listing_id=matches[0], tag_id=None)
            elif not matches and len(tag_matches) == 1:
                normalized.update(tag_id=tag_matches[0], listing_id=None)
            else:
                flags.append({"message_id": message.id, "field": "property_text", "reason": "Property could not be matched confidently."})
        for key, value in normalized.items():
            old = get_value(lead, key)
            if old == value:
                continue
            if not created and (key in (lead.staff_fields or []) or message.received_at < (lead.last_inbound_at or lead.received_at)):
                flags.append({"message_id": message.id, "field": key, "current": iso(old), "proposed": iso(value),
                              "reason": "Staff correction or newer guest information retained."})
                continue
            changes[key] = {"before": iso(old), "after": iso(value)}
            set_value(lead, key, value)
        # An explicit field change can invalidate a date pair; retain prior dates for review.
        if lead.check_in and lead.check_out and lead.check_out <= lead.check_in:
            for key in ("check_in", "check_out"):
                if key in changes:
                    previous = changes.pop(key)["before"]
                    set_value(lead, key, date.fromisoformat(previous) if previous else None)
            flags.append({"message_id": message.id, "reason": "Requested date range is inconsistent; review the source message."})
        if created:
            for key in ("guest_name", "email", "check_in", "check_out"):
                if not get_value(lead, key):
                    flags.append({"message_id": message.id, "field": key, "reason": "Missing or unclear information."})
        flags = [f for f in flags if not (
            f.get("reason") == "Missing or unclear information." and f.get("field") and get_value(lead, f["field"]) is not None
            or f.get("reason") == "Property could not be matched confidently." and (lead.listing_id or lead.tag_id)
        )]
        lead.review_flags = flags
        lead.last_inbound_at = max(filter(None, (lead.last_inbound_at, message.received_at)))
        lead.last_contact_at = max(filter(None, (lead.last_contact_at, message.received_at)))
        if not created:
            lead.version += 1
            lead.needs_reply = True
            lead.updated_at, lead.updated_by = self.now, "Larry"
            # Preserve the staff's schedule but make the new response visible independently.
            self.activity(lead, "Larry processed response", {**changes, "attention": "New response needs review"}, message, actor="Larry")
        else:
            self.activity(lead, "Lead created by Larry", changes, message, actor="Larry")
        message.processing_status, message.error = "processed", None
        self.db.flush()
        return {"message_id": message.id, "lead_id": lead.id, "processing_status": "processed", "replayed": False}

    def attention(self, lead):
        active = lead.status not in ("Booked", "Closed — Not Booked")
        overdue = bool(active and lead.follow_up_at and lead.follow_up_at < self.now)
        settings = self.settings()
        age = max(0, int((self.now - lead.received_at).total_seconds() / 60))
        due = active and lead.follow_up_at
        reminder = bool(due and settings.reminder_minutes is not None and lead.follow_up_at <= self.now + timedelta(minutes=settings.reminder_minutes))
        escalated = bool(overdue and settings.escalation_minutes is not None and lead.follow_up_at <= self.now - timedelta(minutes=settings.escalation_minutes))
        return {"overdue": overdue, "unassigned": bool(active and not lead.assigned_user_id), "age_minutes": age,
                "reminder": reminder, "escalated": escalated,
                "unassigned_alert": bool(active and not lead.assigned_user_id and settings.unassigned_minutes is not None and age >= settings.unassigned_minutes)}

    def serialize(self, lead, detail=False):
        result = {key: iso(getattr(lead, key)) for key in ("id", "version", "is_test", "guest_id", "source", "brand", "reference", "received_at",
            "guest_name", "email", "phone", "listing_id", "tag_id", "property_text", "check_in", "check_out", "details", "trip_type",
            "internal_notes", "assigned_user_id", "status", "next_action", "follow_up_at", "last_contact_at", "last_inbound_at", "needs_reply",
            "review_flags", "qualification", "updated_at", "updated_by")}
        prop = self.properties.get(lead.listing_id, {})
        tag = self.tags.get(lead.tag_id, {})
        result.update(source_label=source_label(lead.source, self.sources, lead.brand),
                      property_label=prop.get("name") or tag.get("name") or lead.property_text or "Unassigned",
                      agent_name=self.users.get(lead.assigned_user_id, {}).get("name", "Unassigned"),
                      **self.attention(lead))
        if not detail:
            for key in ("internal_notes", "details"):
                result[key] = (result[key] or "")[:160]
            result["original_notes"] = lead.original_notes[:160]
            return result
        result["original_notes"] = lead.original_notes
        result["messages"] = [{key: iso(getattr(m, key)) for key in ("id", "mailbox", "message_id", "thread_id", "sender", "subject", "body", "received_at", "source_url", "processing_status")}
                              for m in self.db.scalars(select(SourceMessage).where(SourceMessage.lead_id == lead.id).order_by(SourceMessage.received_at, SourceMessage.id))]
        result["activities"] = [{key: iso(getattr(a, key)) for key in ("id", "actor", "action", "changes", "created_at")}
                                for a in self.db.scalars(select(Activity).where(Activity.lead_id == lead.id).order_by(Activity.id.desc()))]
        return result

    def query(self, filters):
        scope = filters.get("record_scope", "genuine")
        if scope not in ("genuine", "internal-tests"):
            raise InquiryError("Unknown record scope.")
        query = select(Lead).where(Lead.is_test.is_(scope == "internal-tests"))
        if filters.get("status"):
            if filters["status"] not in STATUSES:
                raise InquiryError("Unknown status filter.")
            query = query.where(Lead.status == filters["status"])
        for key in ("listing_id", "tag_id", "assigned_user_id"):
            value = filters.get(key)
            if value == "unassigned" and key == "assigned_user_id":
                query = query.where(Lead.assigned_user_id.is_(None))
            elif value:
                try:
                    number = int(value)
                except (TypeError, ValueError):
                    raise InquiryError(f"Invalid {key} filter.") from None
                if key == "tag_id":
                    matching = [p["id"] for p in self.properties.values() if number in p.get("tag_ids", [])]
                    query = query.where(or_(Lead.tag_id == number, Lead.listing_id.in_(matching)))
                else:
                    query = query.where(getattr(Lead, key) == number)
        timezone_name = self.settings().timezone
        for key, op in (("from", "start"), ("to", "end")):
            if filters.get(key):
                try:
                    day = date.fromisoformat(filters[key])
                except (ValueError, TypeError):
                    raise InquiryError("Date filters must use YYYY-MM-DD.") from None
                local = datetime.combine(day + timedelta(days=1 if op == "end" else 0), datetime.min.time(), ZoneInfo(timezone_name))
                query = query.where(Lead.received_at < local if op == "end" else Lead.received_at >= local)
        search = text_value(filters.get("search"), "search", 200)
        if search:
            needle = "%" + search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            property_ids = [p["id"] for p in self.properties.values() if search.casefold() in p["name"].casefold()]
            tag_ids = [t["id"] for t in self.tags.values() if search.casefold() in t["name"].casefold()]
            query = query.where(or_(*(getattr(Lead, k).ilike(needle, escape="\\") for k in ("guest_name", "email", "phone", "property_text", "reference")),
                                    Lead.listing_id.in_(property_ids), Lead.tag_id.in_(tag_ids)))
        attention = filters.get("attention")
        active = Lead.status.notin_(("Booked", "Closed — Not Booked"))
        if attention == "overdue":
            query = query.where(active, Lead.follow_up_at < self.now)
        elif attention == "new_reply":
            query = query.where(Lead.needs_reply.is_(True))
        elif attention == "unassigned":
            query = query.where(active, Lead.assigned_user_id.is_(None))
        elif attention == "review":
            query = query.where(Lead.review_flags != [])
        elif attention:
            raise InquiryError("Unknown attention filter.")
        return query

    def listing(self, filters):
        query = self.query(filters)
        genuine = self.query({**filters, "record_scope": "genuine"})
        counts = dict(self.db.execute(select(Lead.status, func.count()).where(Lead.id.in_(genuine.with_only_columns(Lead.id))).group_by(Lead.status)).all())
        genuine_total = sum(counts.values())
        total = self.db.scalar(select(func.count()).select_from(query.subquery()))
        completed = counts.get("Booked", 0) + counts.get("Closed — Not Booked", 0)
        try:
            page, size = max(1, int(filters.get("page", 1))), min(100, max(1, int(filters.get("per_page", 30))))
        except (ValueError, TypeError):
            raise InquiryError("Pagination must use whole numbers.") from None
        sort = filters.get("sort", "received_at")
        if sort not in ("received_at", "check_in", "status", "assigned_user_id"):
            raise InquiryError("Unknown sort column.")
        if sort == "assigned_user_id":
            from sqlalchemy import case
            column = case(self.users and {k: v["name"].lower() for k, v in self.users.items()} or {0: ""}, value=Lead.assigned_user_id, else_="")
        else:
            column = getattr(Lead, sort)
        order = column.asc().nullslast() if filters.get("direction") == "asc" else column.desc().nullslast()
        rows = self.db.scalars(query.order_by(order, Lead.id.desc()).offset((page - 1) * size).limit(size))
        return {"leads": [self.serialize(v) for v in rows], "total": total, "page": page, "per_page": size,
                "stats": {"total": genuine_total, "new": counts.get("New", 0), "follow_up": counts.get("Follow-up Required", 0),
                          "booked": counts.get("Booked", 0), "conversion_rate": round(counts.get("Booked", 0) / completed * 100, 1) if completed else None}}

    def update_settings(self, payload):
        settings = self.settings()
        if type(payload.get("version")) is not int or payload["version"] != settings.version:
            raise InquiryError("Settings changed. Reload before saving.", 409)
        if set(payload) - {"version", "timezone", "reminder_minutes", "escalation_minutes", "unassigned_minutes"}:
            raise InquiryError("Unknown setting.")
        if "timezone" in payload:
            try:
                ZoneInfo(payload["timezone"])
            except (ZoneInfoNotFoundError, TypeError, ValueError):
                raise InquiryError("Choose a valid IANA timezone.") from None
            settings.timezone = payload["timezone"]
        for key in ("reminder_minutes", "escalation_minutes", "unassigned_minutes"):
            if key in payload:
                value = payload[key]
                if value is not None and (type(value) is not int or not 0 <= value <= 525600):
                    raise InquiryError("Reminder timing must be a non-negative number of minutes or blank.")
                setattr(settings, key, value)
        settings.version += 1
        self.db.add(settings)
        self.db.flush()
        return self.settings_dict()

    def settings_dict(self):
        return {key: getattr(self.settings(), key) for key in ("version", "timezone", "reminder_minutes", "escalation_minutes", "unassigned_minutes")}
