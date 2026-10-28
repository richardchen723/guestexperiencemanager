"""Explicit owner/property/contact links, provenance and conservative merging."""
from sqlalchemy import func, or_, select

from . import validation as v
from .models import (Account, AccountContact, Contact, Deal, DealContact, DealProperty,
                     Permission, Property, PropertyAccount, PropertyContact, Source, Proposal)


class RelationshipMixin:
    def attach_contact(self, deal, data):
        v.fields(data, {"id", "name", "email", "phone", "preferred_channel", "role", "property_ids"})
        if data.get("id"):
            contact = self.row(Contact, data["id"], lock=True)
        else:
            email = v.email(data.get("email"))
            phone = v.string(data.get("phone"), "Phone", 80)
            contact = Contact(name=v.string(data.get("name"), "Contact name", 240, True), email=email,
                              email_key=email.casefold() if email else None, phone=phone, phone_key=v.phone_key(phone),
                              preferred_channel=v.choice(data["preferred_channel"], v.CHANNELS, "preferred channel") if data.get("preferred_channel") else None)
            self.db.add(contact)
            self.db.flush()
        role = v.string(data.get("role", "decision-maker"), "Contact role", 100, True)
        if not self.db.get(DealContact, (deal.id, contact.id)):
            self.db.add(DealContact(deal_id=deal.id, contact_id=contact.id, role=role))
        if deal.account_id and not self.db.get(AccountContact, (deal.account_id, contact.id)):
            self.db.add(AccountContact(account_id=deal.account_id, contact_id=contact.id, role=role))
        property_ids = data.get("property_ids", [])
        if not isinstance(property_ids, list) or len(property_ids) > 100:
            raise v.SalesError("Choose a list of at most 100 properties for this contact.")
        for pid in property_ids:
            pid = v.integer(pid, "Property ID")
            if not self.db.get(DealProperty, (deal.id, pid)):
                raise v.SalesError("Contact property roles must refer to this opportunity's properties.")
            if not self.db.get(PropertyContact, (pid, contact.id)):
                self.db.add(PropertyContact(property_id=pid, contact_id=contact.id, role=role))
        self.db.flush()
        return contact

    def attach_property(self, deal, data):
        allowed = {"id", "name", "address", "market", "property_type", "bedrooms", "bathrooms", "amenities", "rental_status", "rental_revenue", "management_revenue"}
        v.fields(data, allowed)
        if data.get("id"):
            prop = self.row(Property, data["id"], lock=True)
        else:
            address = v.string(data.get("address"), "Property address", 500)
            amenities = data.get("amenities", [])
            if not isinstance(amenities, list) or len(amenities) > 50:
                raise v.SalesError("Amenities must be a list of at most 50 descriptions.")
            prop = Property(name=v.string(data.get("name"), "Property name", 240, True),
                            address=address, address_key=v.normalized(address) or None,
                            market=v.string(data.get("market"), "Market", 160),
                            property_type=v.string(data.get("property_type"), "Property type", 100),
                            bedrooms=v.integer(data.get("bedrooms"), "Bedrooms", 0, 100, True),
                            bathrooms=v.money(data.get("bathrooms"), "Bathrooms", maximum=100),
                            amenities=[v.string(a, "Amenity", 160, True) for a in amenities],
                            rental_status=v.choice(data["rental_status"], ("self_managed", "professionally_managed", "not_rented", "unknown"), "rental status") if data.get("rental_status") else None)
            self.db.add(prop)
            self.db.flush()
        if self.db.get(DealProperty, (deal.id, prop.id)):
            raise v.SalesError("This property is already linked to the opportunity.")
        self.db.add(DealProperty(deal_id=deal.id, property_id=prop.id,
                                 rental_revenue=v.money(data.get("rental_revenue"), "Rental revenue"),
                                 management_revenue=v.money(data.get("management_revenue"), "Annual management revenue")))
        if deal.account_id and not self.db.get(PropertyAccount, (prop.id, deal.account_id)):
            self.db.add(PropertyAccount(property_id=prop.id, account_id=deal.account_id))
        self.db.flush()
        return prop

    def relationship(self, identity, data):
        v.fields(data, {"version", "kind", "record"})
        self.lock_key("sales-relationships")
        deal = self.edit(identity, data)
        if deal.outcome == "won":
            raise v.SalesError("Signed scope is immutable. Link operational listings through onboarding.")
        kind = v.choice(data.get("kind"), ("contact", "property"), "relationship kind")
        record = self.attach_contact(deal, data.get("record")) if kind == "contact" else self.attach_property(deal, data.get("record"))
        self.changed(deal)
        self.log(deal, "relationship", f"Linked {kind}: {record.name}", {"kind": kind, "id": record.id})
        return self.detail(deal.id)

    def edit_record(self, kind, identity, data):
        self.lock_key("sales-relationships")
        model = {"contact": Contact, "property": Property, "account": Account}.get(kind)
        if model is None:
            raise v.SalesError("Unknown record type.")
        allowed = {"contact": {"name", "email", "phone", "preferred_channel"},
                   "property": {"name", "address", "market", "property_type", "rental_status", "bedrooms", "bathrooms", "amenities"},
                   "account": {"name", "kind"}}[kind]
        v.fields(data, allowed | {"version"})
        record = self.row(model, identity, lock=True)
        if type(data.get("version")) is not int or record.version != data["version"]:
            raise v.SalesError("This relationship record changed; reload before editing.", 409)
        changes = {}
        for key in allowed & set(data):
            value = data[key]
            if key == "email": value = v.email(value)
            elif key == "bedrooms": value = v.integer(value, "Bedrooms", 0, 100, True)
            elif key == "bathrooms": value = v.money(value, "Bathrooms", maximum=100)
            elif key == "amenities":
                if not isinstance(value, list) or len(value) > 50: raise v.SalesError("Provide at most 50 amenities.")
                value = [v.string(a, "Amenity", 160, True) for a in value]
            elif key == "preferred_channel": value = v.choice(value, (*v.CHANNELS, None), "preferred channel")
            elif key == "kind": value = v.choice(value, ("individual", "household", "company"), "account kind")
            elif key == "rental_status": value = v.choice(value, ("self_managed", "professionally_managed", "not_rented", "unknown", None), "rental status")
            else: value = v.string(value, key, getattr(model.__table__.columns[key].type, "length", None) or 240, key == "name")
            if getattr(record, key) != value:
                changes[key] = {"before": getattr(record, key), "after": value}
                setattr(record, key, value)
        if kind == "contact":
            record.email_key = record.email.casefold() if record.email else None
            record.phone_key = v.phone_key(record.phone)
        elif kind == "property": record.address_key = v.normalized(record.address) or None
        if changes:
            record.version += 1
            record.updated_at = self.now
            self.log(None, "relationship_updated", f"{kind} {identity}", changes)
            for deal_id in self.record_deals(kind, identity):
                deal = self.deal(deal_id, lock=True)
                self.changed(deal)
                self.log(deal, "relationship_updated", f"Updated {kind}: {record.name}", changes)
        return {"id": record.id, "version": record.version}

    def record_deals(self, kind, identity):
        if kind == "contact": statement = select(DealContact.deal_id).where(DealContact.contact_id == identity)
        elif kind == "property": statement = select(DealProperty.deal_id).where(DealProperty.property_id == identity)
        else: statement = select(Deal.id).where(Deal.account_id == identity)
        return sorted(set(self.db.scalars(statement)))

    def contact_family(self, identity):
        return list(self.db.scalars(select(Contact.id).where(or_(Contact.id == identity, Contact.merged_into_id == identity))))

    def permission_state(self, identity, channel):
        family = self.contact_family(identity)
        records = self.db.scalars(select(Permission).where(Permission.contact_id.in_(family), Permission.channel == channel)
                                 .order_by(Permission.created_at.desc(), Permission.id.desc())).all()
        latest = {}
        for row in records:
            latest.setdefault(row.contact_id, row)
        statuses = []
        for row in latest.values():
            status = row.status
            current_contact = self.db.get(Contact, identity)
            current_endpoint = current_contact.email_key if channel == "email" else current_contact.phone_key
            if status == "allowed" and (row.endpoint_value != current_endpoint or (row.expires_at and row.expires_at <= self.now)):
                status = "needs_review"
            statuses.append((status, row))
        rank = {"opted_out": 4, "blocked": 3, "needs_review": 2, "allowed": 1}
        if not statuses:
            return {"channel": channel, "status": "needs_review", "basis": "No permission review recorded."}
        status, row = max(statuses, key=lambda item: rank[item[0]])
        return {"channel": channel, "status": status, "basis": row.basis, "reviewed_at": row.reviewed_at,
                "expires_at": row.expires_at, "dnc_result": row.dnc_result, "source_url": row.source_url}

    def assert_contact_allowed(self, identity, channel):
        state = self.permission_state(identity, channel)
        if state["status"] != "allowed":
            raise v.SalesError(f"{channel.title()} contact is {state['status'].replace('_', ' ')}. Record the permission review first.")
        if channel in ("phone", "sms") and state.get("dnc_result") not in ("clear", "not_applicable_reviewed"):
            raise v.SalesError("A current DNC review is required for this channel.")

    def permission(self, identity, data):
        v.fields(data, {"version", "contact_id", "channel", "status", "basis", "source_url", "dnc_result", "expires_at", "expires_local", "fold"})
        self.lock_key("sales-relationships")
        deal = self.edit(identity, data)
        contact = self.row(Contact, data.get("contact_id"), lock=True)
        if not self.db.get(DealContact, (deal.id, contact.id)):
            raise v.SalesError("Choose a contact linked to this opportunity.")
        channel = v.choice(data.get("channel"), v.CHANNELS, "channel")
        status = v.choice(data.get("status"), ("allowed", "needs_review", "blocked", "opted_out"), "permission status")
        if status not in ("blocked", "opted_out") and self.permission_state(contact.id, channel)["status"] in ("blocked", "opted_out"):
            self.require_manager()
        if status == "allowed" and not (contact.email_key if channel == "email" else contact.phone_key):
            raise v.SalesError("Record the contact email address or phone number before allowing this channel.")
        expires = v.local_instant(data["expires_local"], self.settings().timezone, fold=data.get("fold")) if data.get("expires_local") else v.instant(data.get("expires_at"), "Permission expiry")
        if expires and expires <= self.now:
            raise v.SalesError("Permission expiry must be in the future.")
        for cid in self.contact_family(contact.id):
            self.db.add(Permission(contact_id=cid, channel=channel, status=status,
                                  basis=v.string(data.get("basis"), "Permission basis / evidence", 3000, True),
                                  source_url=v.url(data.get("source_url")), dnc_result=v.choice(data.get("dnc_result"), (None, "clear", "listed", "unknown", "not_applicable_reviewed"), "DNC result"),
                                  endpoint_value=contact.email_key if channel == "email" else contact.phone_key,
                                  actor=self.actor, reviewed_at=self.now, expires_at=expires, created_at=self.now))
        for did in self.record_deals("contact", contact.id):
            affected = self.deal(did, lock=True)
            self.changed(affected)
            self.log(affected, "permission", f"{contact.name}: {channel} {status}", {"contact_id": contact.id, "channel": channel, "status": status})
        return self.detail(deal.id)

    def deal_duplicates(self, identity):
        deal = self.deal(identity)
        candidates = []
        for contact in self.db.scalars(select(Contact).join(DealContact).where(DealContact.deal_id == deal.id)):
            candidates += [{**c, "from_id": contact.id, "from_name": contact.name, "from_version": contact.version} for c in self.duplicates({"email": contact.email, "phone": contact.phone})["candidates"] if c["id"] != contact.id]
        for prop in self.db.scalars(select(Property).join(DealProperty).where(DealProperty.deal_id == deal.id)):
            candidates += [{**c, "from_id": prop.id, "from_name": prop.name, "from_version": prop.version} for c in self.duplicates({"address": prop.address})["candidates"] if c["id"] != prop.id]
        if deal.account_id:
            account = self.row(Account, deal.account_id)
            candidates += [{**c, "from_id": account.id, "from_name": account.name, "from_version": account.version} for c in self.duplicates({"name": account.name})["candidates"] if c["id"] != account.id]
        return {"candidates": candidates}

    def duplicates(self, data):
        """Suggestions only: an equal email can represent a household, not one person."""
        v.fields(data, {"email", "phone", "address", "name"})
        email = v.email(data.get("email"))
        phone = v.phone_key(data.get("phone"))
        address = v.normalized(data.get("address"))
        result = []
        terms = []
        if email: terms.append(Contact.email_key == email.casefold())
        if phone: terms.append(Contact.phone_key == phone)
        if terms:
            for c in self.db.scalars(select(Contact).where(Contact.merged_into_id.is_(None), or_(*terms)).limit(30)):
                result.append({"kind": "contact", "id": c.id, "name": c.name, "version": c.version,
                               "reason": "Matching email or phone", "email": c.email, "phone": c.phone})
        if address:
            for p in self.db.scalars(select(Property).where(Property.merged_into_id.is_(None), Property.address_key == address).limit(30)):
                result.append({"kind": "property", "id": p.id, "name": p.name, "version": p.version, "reason": "Matching property address", "address": p.address})
        name = v.string(data.get("name"), "Name", 240)
        if name:
            for a in self.db.scalars(select(Account).where(Account.merged_into_id.is_(None), func.lower(Account.name) == name.lower()).limit(30)):
                result.append({"kind": "account", "id": a.id, "name": a.name, "version": a.version, "reason": "Matching name — verify identity"})
        return {"candidates": result}

    def merge(self, data):
        self.require_manager()
        v.fields(data, {"kind", "from_id", "into_id", "from_version", "into_version", "reason"})
        kind = v.choice(data.get("kind"), ("account", "contact", "property"), "merge kind")
        self.lock_key("sales-relationships")
        model = {"account": Account, "contact": Contact, "property": Property}[kind]
        a, b = data.get("from_id"), data.get("into_id")
        if a == b: raise v.SalesError("Choose two different records.")
        records = {i: self.row(model, i, lock=True) for i in sorted([v.integer(a,"Source ID"), v.integer(b,"Target ID")])}
        source, target = records[a], records[b]
        if source.version != data.get("from_version") or target.version != data.get("into_version"):
            raise v.SalesError("A merge record changed; review the latest records first.", 409)
        reason = v.string(data.get("reason"), "Merge review reason", 3000, True)
        deal_ids = sorted(set(self.record_deals(kind, a) + self.record_deals(kind, b)))
        deals = [self.deal(i, lock=True) for i in deal_ids]
        # Signed property identifiers are contractual evidence; keep them stable.
        if kind == "property" and (any(d.outcome == "won" for d in deals) or self.db.scalar(select(Proposal.id).where(Proposal.deal_id.in_(deal_ids)).limit(1))):
            raise v.SalesError("Properties with proposal or agreement snapshots cannot be merged. Keep the commercial scope intact.")
        if kind == "property" and source.listing_id and target.listing_id and source.listing_id != target.listing_id:
            raise v.SalesError("Properties linked to different operational listings cannot be merged.")
        if kind == "contact": mappings = [(DealContact, "contact_id", "deal_id"), (AccountContact,"contact_id","account_id"), (PropertyContact,"contact_id","property_id")]
        elif kind == "property": mappings = [(DealProperty,"property_id","deal_id"), (PropertyAccount,"property_id","account_id"), (PropertyContact,"property_id","contact_id")]
        else: mappings = [(AccountContact,"account_id","contact_id"), (PropertyAccount,"account_id","property_id")]
        for cls, replace, other in mappings:
            for link in self.db.scalars(select(cls).where(getattr(cls, replace) == a)).all():
                existing = self.db.scalar(select(cls).where(getattr(cls, replace) == b, getattr(cls, other) == getattr(link, other)))
                if existing:
                    if cls is DealProperty and (existing.rental_revenue != link.rental_revenue or existing.management_revenue != link.management_revenue):
                        raise v.SalesError("Conflicting property estimates need review before merging.")
                    self.db.delete(link)
                else:
                    setattr(link, replace, b)
        if kind == "account":
            for d in deals:
                if d.account_id == a: d.account_id = b
        preserved = {}
        for column in model.__table__.columns:
            key = column.name
            if key in ("id", "version", "merged_into_id", "created_at", "updated_at"): continue
            old, new = getattr(source, key), getattr(target, key)
            if new is None and old is not None: setattr(target, key, old)
            elif old != new: preserved[key] = {"source": old, "target": new}
        # Flatten aliases so inherited opt-outs are always evaluated.
        for alias in self.db.scalars(select(model).where(model.merged_into_id == a)):
            alias.merged_into_id = b
        source.merged_into_id = b
        source.version += 1
        target.version += 1
        source.updated_at = target.updated_at = self.now
        for d in deals:
            self.changed(d)
            self.log(d, "merge", reason, {"kind": kind, "from_id": a, "into_id": b, "preserved_conflicts": preserved})
        self.log(None, "merge", reason, {"kind": kind, "from_id": a, "into_id": b, "preserved_conflicts": preserved})
        self.db.flush()
        return {"kind": kind, "id": b, "version": target.version, "affected_deal_ids": deal_ids}

    def source_update(self, data):
        self.require_manager()
        v.fields(data, {"id", "version", "name", "kind", "active"})
        if data.get("id"):
            source = self.row(Source, data["id"], lock=True)
            if data.get("version") != source.version:
                raise v.SalesError("Source changed; reload before editing.",409)
            source.version += 1
        else:
            source = Source()
            self.db.add(source)
        source.name = v.string(data.get("name"), "Source name", 160, True)
        source.kind = v.choice(data.get("kind", "manual"), ("manual", "website", "referral", "realtor", "research", "campaign"), "source kind")
        source.active = v.boolean(data.get("active", True), "Source enabled")
        source.updated_at = self.now
        self.db.flush()
        self.log(None, "source", "Marketing source updated", {"id": source.id, "name": source.name, "active": source.active})
        return {"id": source.id, "version": source.version}
