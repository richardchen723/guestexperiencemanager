"""Transactional owner-sales operations, with authenticated actors supplied by routes."""
from datetime import timedelta
from decimal import Decimal
import hashlib

from sqlalchemy import func, or_, select, text

from . import validation as v
from .models import (Account, AccountContact, Activity, Command, Contact, Deal, DealContact,
                     DealProperty, Handoff, Agreement, Notice, Permission, Property,
                     PropertyAccount, PropertyContact, Settings, Source, StageHistory,
                     Task, utcnow)
from .workflows import WorkflowMixin
from .relationships import RelationshipMixin
from .queries import QueryMixin


class SalesService(WorkflowMixin, RelationshipMixin, QueryMixin):
    def __init__(self, db, *, actor, actor_name, users, manager=False, can_export=False,
                 now=None, listings=None):
        self.db = db
        self.actor = actor
        self.actor_name = actor_name
        self.users = users
        self.manager = manager
        self.can_export = can_export or manager
        self.now = now or utcnow()
        self.listings = listings or {}

    def lock_key(self, key):
        number = int.from_bytes(hashlib.sha256(key.encode()).digest()[:8], "big", signed=True)
        self.db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": number})

    def execute(self, operation, payload, callback):
        if not isinstance(payload, dict):
            raise v.SalesError("A JSON object is required.")
        data = dict(payload)
        key = v.command_id(data.pop("command_id", None))
        digest = v.fingerprint({"operation": operation, "data": data})
        self.lock_key("sales-command:" + self.actor + ":" + key)
        self.lock_key("sales-mutations")
        previous = self.db.get(Command, (self.actor, key))
        if previous:
            if previous.fingerprint != digest:
                raise v.SalesError("This command was already used for different data. Start a new operation.", 409)
            return {**previous.response, "replayed": True}
        result = v.jsonable(callback(data))
        self.db.add(Command(actor=self.actor, key=key, fingerprint=digest, response=result))
        self.db.flush()
        return result

    def settings(self):
        setting = self.db.get(Settings, 1)
        if not setting:
            raise v.SalesError("Sales database migration is required.", 503)
        return setting

    def require_manager(self):
        if not self.manager:
            raise v.SalesError("Sales management access is required.", 403)

    def user(self, value, required=False):
        value = v.integer(value, "Assigned user", optional=not required)
        if value is not None and value not in self.users:
            raise v.SalesError("Choose an approved team member with Sales CRM access.")
        return value

    def row(self, model, identity, *, lock=False):
        identity = v.integer(identity, "Record ID")
        statement = select(model).where(model.id == identity)
        if lock:
            statement = statement.with_for_update()
        result = self.db.scalar(statement.execution_options(populate_existing=True))
        if not result:
            raise v.SalesError("Sales record not found.", 404)
        if getattr(result, "merged_into_id", None):
            raise v.SalesError(f"This record was merged into record {result.merged_into_id}.", 409)
        return result

    def deal(self, identity, version=None, *, lock=False):
        deal = self.row(Deal, identity, lock=lock)
        if deal.is_test and not self.manager:
            raise v.SalesError("Sales management access is required for test records.", 403)
        if version is not None and (type(version) is not int or version != deal.version):
            raise v.SalesError("This opportunity changed. Reload it before saving; your edits have not been applied.", 409)
        return deal

    def edit(self, identity, data):
        if "version" not in data:
            raise v.SalesError("The opportunity version is required.")
        return self.deal(identity, data["version"], lock=True)

    def changed(self, deal):
        deal.version += 1
        deal.updated_at = self.now

    def log(self, deal, kind, body=None, changes=None, **extra):
        activity = Activity(deal_id=deal.id if deal else None, kind=kind, body=body,
                            changes=v.jsonable(changes or {}), actor=self.actor,
                            actor_name=self.actor_name, occurred_at=self.now, **extra)
        self.db.add(activity)
        self.db.flush()
        return activity

    def history(self, deal, reason=None, stage=None):
        self.db.add(StageHistory(deal_id=deal.id, stage=deal.stage if stage is None else stage, outcome=deal.outcome,
                                 reason=reason, actor=self.actor, entered_at=self.now))

    def notice(self, deal, event_type, key=None, task=None, payload=None):
        key = key or f"{deal.id}:{deal.version}:{event_type}"
        if self.db.scalar(select(Notice.id).where(Notice.key == key)):
            return
        self.db.add(Notice(key=key, deal_id=deal.id, task_id=task.id if task else None,
                           task_version=task.version if task else None, event_type=event_type,
                           payload={"actor": self.actor, "actor_name": self.actor_name,
                                    **v.jsonable(payload or {})}, created_at=self.now))

    def primary(self, deal):
        return self.db.scalar(select(Task).where(Task.deal_id == deal.id, Task.status == "open", Task.is_primary.is_(True)))

    def validate_active(self, deal):
        if deal.outcome == "open" and deal.stage > 0:
            if not deal.assigned_user_id or not self.primary(deal):
                raise v.SalesError("Active opportunities need an assigned salesperson and a primary next action with a due time.")

    def task_time(self, data):
        if data.get("due_local"):
            return v.local_instant(data["due_local"], self.settings().timezone, fold=data.get("fold"))
        return v.instant(data.get("due_at"), "Due time", required=True)

    def set_primary(self, deal, data):
        v.fields(data, {"title", "assigned_user_id", "due_at", "due_local", "fold", "kind"})
        title = v.string(data.get("title"), "Next action", 500, True)
        assigned = self.user(data.get("assigned_user_id", deal.assigned_user_id), required=True)
        due = self.task_time(data)
        existing = self.primary(deal)
        if existing:
            existing.title, existing.assigned_user_id, existing.due_at = title, assigned, due
            existing.version += 1
            existing.updated_at = self.now
            result = existing
        else:
            result = Task(deal_id=deal.id, title=title, assigned_user_id=assigned, due_at=due,
                          is_primary=True, kind=v.choice(data.get("kind", "followup"), ("followup", "meeting", "research"), "task kind"))
            self.db.add(result)
        self.db.flush()
        return result

    def source(self, data):
        if data.get("source_id"):
            source = self.row(Source, data["source_id"])
            if not source.active:
                raise v.SalesError("This source is retired for new opportunities.")
            return source
        name = v.string(data.get("source_name", "Manual entry"), "Source name", 160, True)
        self.lock_key("sales-source:" + name.casefold())
        source = self.db.scalar(select(Source).where(func.lower(Source.name) == name.lower()))
        if not source:
            source = Source(name=name, kind="manual")
            self.db.add(source)
            self.db.flush()
        if not source.active:
            raise v.SalesError("This source is retired for new opportunities.")
        return source

    def create(self, data):
        self.lock_key("sales-relationships")
        v.fields(data, {"title", "account", "account_id", "contacts", "properties", "source_id", "source_name",
                        "source_reference", "assigned_user_id", "summary", "priority", "next_action", "is_test"})
        if data.get("is_test"):
            self.require_manager()
        source = self.source(data)
        account = None
        if data.get("account_id"):
            account = self.row(Account, data["account_id"])
        elif data.get("account"):
            values = data["account"]
            v.fields(values, {"name", "kind"})
            account = Account(name=v.string(values.get("name"), "Owner account name", 240, True),
                              kind=v.choice(values.get("kind", "individual"), ("individual", "household", "company"), "account kind"))
            self.db.add(account)
            self.db.flush()
        deal = Deal(title=v.string(data.get("title"), "Opportunity title", 240, True),
                    account_id=account.id if account else None, source_id=source.id,
                    source_reference=v.url(data.get("source_reference"), "Source reference"),
                    assigned_user_id=self.user(data.get("assigned_user_id")),
                    summary=v.string(data.get("summary"), "Summary"),
                    priority=v.choice(data.get("priority", "normal"), ("low", "normal", "high"), "priority"),
                    is_test=v.boolean(data.get("is_test", False), "Internal test"), created_at=self.now, updated_at=self.now)
        self.db.add(deal)
        self.db.flush()
        properties = data.get("properties", [])
        contacts = data.get("contacts", [])
        if not isinstance(properties, list) or len(properties) > 100 or not isinstance(contacts, list) or len(contacts) > 50:
            raise v.SalesError("Provide at most 100 properties and 50 contacts.")
        for item in properties:
            self.attach_property(deal, item)
        for item in contacts:
            self.attach_contact(deal, item)
        if data.get("next_action"):
            self.set_primary(deal, data["next_action"])
        self.log(deal, "created", "Opportunity captured", {"source_id": source.id})
        self.history(deal)
        self.notice(deal, "created")
        self.db.flush()
        return self.detail(deal.id)

    def patch(self, identity, data):
        v.fields(data, {"version", "title", "summary", "assigned_user_id", "priority", "next_action",
                        "discovery_notes", "expected_close_date", "acknowledge_reply", "acquisition_cost"})
        deal = self.edit(identity, data)
        changes = {}
        for key in ("title", "summary", "discovery_notes"):
            if key in data:
                value = v.string(data[key], key.replace("_", " "), 240 if key == "title" else 10000, key == "title")
                if value != getattr(deal, key):
                    changes[key] = {"before": getattr(deal, key), "after": value}
                    setattr(deal, key, value)
        for key, validator in (("assigned_user_id", lambda x: self.user(x)),
                               ("priority", lambda x: v.choice(x, ("low", "normal", "high"), "priority")),
                               ("expected_close_date", lambda x: v.day(x, "Expected close date")),
                               ("acquisition_cost", lambda x: v.money(x, "Acquisition cost"))):
            if key in data:
                value = validator(data[key])
                if value != getattr(deal, key):
                    changes[key] = {"before": getattr(deal, key), "after": value}
                    setattr(deal, key, value)
        if data.get("next_action"):
            if deal.outcome not in ("open", "nurture"):
                raise v.SalesError("This opportunity is closed; use onboarding for signed agreements.")
            task = self.set_primary(deal, data["next_action"])
            changes["next_action"] = {"title": task.title, "due_at": task.due_at, "assigned_user_id": task.assigned_user_id}
        if data.get("acknowledge_reply") is True:
            deal.needs_reply = False
            changes["needs_reply"] = False
        self.validate_active(deal)
        if changes:
            self.changed(deal)
            self.log(deal, "updated", changes=changes)
            if "assigned_user_id" in changes:
                self.notice(deal, "assigned", payload={"assigned_user_id": deal.assigned_user_id})
        self.db.flush()
        return self.detail(deal.id)

    def add_activity(self, identity, data):
        v.fields(data, {"version", "kind", "body", "contact_id", "direction", "outcome", "channel", "source_url", "occurred_at", "next_action"})
        deal = self.edit(identity, data)
        kind = v.choice(data.get("kind"), ("note", "call", "email", "meeting", "research", "whatsapp", "sms"), "activity kind")
        contact = data.get("contact_id")
        if contact is not None:
            self.row(Contact, contact)
            if not self.db.get(DealContact, (deal.id, contact)):
                raise v.SalesError("Choose a contact linked to this opportunity.")
        direction = v.choice(data.get("direction", "internal"), ("internal", "inbound", "outbound"), "direction")
        if kind in ("note", "research") and direction != "internal":
            raise v.SalesError("Notes and research are internal activities.")
        channel = data.get("channel") or ({"call": "phone", "email": "email", "sms": "sms", "whatsapp": "whatsapp"}.get(kind))
        if channel:
            v.choice(channel, v.CHANNELS, "channel")
        if direction == "outbound":
            if not contact or not channel:
                raise v.SalesError("Outbound communication needs a contact and channel.")
            self.assert_contact_allowed(contact, channel)
        occurred = v.instant(data.get("occurred_at"), "Activity time") or self.now
        if occurred > self.now + timedelta(minutes=5):
            raise v.SalesError("Completed activities cannot be dated in the future. Create an appointment task instead.")
        body = v.string(data.get("body"), "Activity details", 10000, True)
        outcome = v.string(data.get("outcome"), "Outcome", 80)
        activity = Activity(deal_id=deal.id, contact_id=contact, kind=kind, actor=self.actor,
                            actor_name=self.actor_name, body=body, direction=direction, channel=channel,
                            outcome=outcome, source_url=v.url(data.get("source_url")), occurred_at=occurred)
        self.db.add(activity)
        if direction == "inbound":
            deal.last_inbound_at = max(filter(None, (deal.last_inbound_at, occurred)))
            deal.needs_reply = True
        elif direction == "outbound":
            deal.last_outbound_at = max(filter(None, (deal.last_outbound_at, occurred)))
        if data.get("next_action"):
            self.set_primary(deal, data["next_action"])
        self.changed(deal)
        self.db.flush()
        if direction == "inbound":
            self.notice(deal, "reply", key=f"reply:{activity.id}")
        return self.detail(deal.id)

    def task_action(self, identity, data):
        v.fields(data, {"version", "task_id", "action", "title", "assigned_user_id", "due_at", "due_local", "fold", "kind", "outcome", "next_action"})
        deal = self.edit(identity, data)
        action = v.choice(data.get("action"), ("create", "complete", "reschedule", "cancel"), "task action")
        if action == "create":
            if deal.outcome != "open":
                raise v.SalesError("Supporting sales tasks require an open opportunity.")
            task = Task(deal_id=deal.id, title=v.string(data.get("title"), "Task title", 500, True),
                        assigned_user_id=self.user(data.get("assigned_user_id", deal.assigned_user_id), True),
                        due_at=self.task_time(data), kind=v.choice(data.get("kind", "followup"), ("followup", "meeting", "research"), "task kind"))
            self.db.add(task)
        else:
            task = self.row(Task, data.get("task_id"), lock=True)
            if task.deal_id != deal.id or task.status != "open":
                raise v.SalesError("Choose an open task on this opportunity.")
            task.version += 1
            task.updated_at = self.now
            if action == "reschedule":
                task.due_at = self.task_time(data)
            else:
                task.status = "completed" if action == "complete" else "cancelled"
                task.completed_at = self.now
                task.outcome = v.string(data.get("outcome"), "Task outcome", 3000, True)
                self.db.flush()
                if data.get("next_action"):
                    self.set_primary(deal, data["next_action"])
        self.validate_active(deal)
        self.changed(deal)
        self.db.flush()
        self.log(deal, "task", f"Task {action}: {task.title}", {"task_id": task.id, "due_at": task.due_at, "outcome": task.outcome})
        return self.detail(deal.id)

    def update_settings(self, data):
        self.require_manager()
        allowed = {"version", "timezone", "reminder_minutes", "escalation_minutes", "unassigned_minutes",
                   "qualification_guidance", "assignment_guidance", "commercial_guidance", "onboarding_guidance"}
        v.fields(data, allowed)
        row = self.db.scalar(select(Settings).where(Settings.id == 1).with_for_update().execution_options(populate_existing=True))
        if type(data.get("version")) is not int or data["version"] != row.version:
            raise v.SalesError("Sales settings changed; reload before saving.", 409)
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
        if "timezone" in data:
            try:
                ZoneInfo(data["timezone"])
            except (ZoneInfoNotFoundError, TypeError):
                raise v.SalesError("Choose a valid IANA timezone.") from None
            row.timezone = data["timezone"]
        for key in allowed - {"version", "timezone"}:
            if key in data:
                setattr(row, key, v.integer(data[key], key, minimum=0, maximum=525600, optional=True) if key.endswith("minutes") else v.string(data[key], key))
        row.version += 1
        row.updated_at = self.now
        self.log(None, "settings", "Sales settings changed", {k: getattr(row, k) for k in allowed})
        return self.settings_dict()
