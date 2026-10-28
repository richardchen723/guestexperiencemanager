"""Additive owner-sales schema. No migrations or database access on import."""
from datetime import datetime, timezone

from sqlalchemy import (Boolean, CheckConstraint, Column, Date, DateTime, ForeignKey,
                        Index, Integer, JSON, MetaData, Numeric, String, Text,
                        UniqueConstraint, text)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import declarative_base

Base = declarative_base(metadata=MetaData(schema="sales"))
Document = JSONB().with_variant(JSON(), "sqlite")


def utcnow():
    return datetime.now(timezone.utc)


class Versioned:
    version = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)


class Account(Versioned, Base):
    __tablename__ = "accounts"
    id = Column(Integer, primary_key=True)
    name = Column(String(240), nullable=False)
    kind = Column(String(30), nullable=False, default="individual")
    merged_into_id = Column(Integer, ForeignKey("sales.accounts.id"))


class Contact(Versioned, Base):
    __tablename__ = "contacts"
    id = Column(Integer, primary_key=True)
    name = Column(String(240), nullable=False)
    email = Column(String(320))
    email_key = Column(String(320), index=True)
    phone = Column(String(80))
    phone_key = Column(String(40), index=True)
    preferred_channel = Column(String(30))
    merged_into_id = Column(Integer, ForeignKey("sales.contacts.id"))


class AccountContact(Base):
    __tablename__ = "account_contacts"
    account_id = Column(Integer, ForeignKey("sales.accounts.id"), primary_key=True)
    contact_id = Column(Integer, ForeignKey("sales.contacts.id"), primary_key=True)
    role = Column(String(100), nullable=False, default="owner")


class Property(Versioned, Base):
    __tablename__ = "properties"
    id = Column(Integer, primary_key=True)
    name = Column(String(240), nullable=False)
    address = Column(String(500))
    address_key = Column(String(500), index=True)
    market = Column(String(160), index=True)
    property_type = Column(String(100))
    bedrooms = Column(Integer)
    bathrooms = Column(Numeric(5, 1))
    amenities = Column(Document, nullable=False, default=list)
    rental_status = Column(String(40))
    listing_id = Column(Integer, index=True)  # Optional Hostaway link, never synthetic.
    merged_into_id = Column(Integer, ForeignKey("sales.properties.id"))


class PropertyAccount(Base):
    __tablename__ = "property_accounts"
    property_id = Column(Integer, ForeignKey("sales.properties.id"), primary_key=True)
    account_id = Column(Integer, ForeignKey("sales.accounts.id"), primary_key=True)


class PropertyContact(Base):
    __tablename__ = "property_contacts"
    property_id = Column(Integer, ForeignKey("sales.properties.id"), primary_key=True)
    contact_id = Column(Integer, ForeignKey("sales.contacts.id"), primary_key=True)
    role = Column(String(100), nullable=False, default="decision-maker")


class Source(Versioned, Base):
    __tablename__ = "sources"
    id = Column(Integer, primary_key=True)
    name = Column(String(160), nullable=False, unique=True)
    kind = Column(String(40), nullable=False, default="manual")
    active = Column(Boolean, nullable=False, default=True)


class Deal(Versioned, Base):
    __tablename__ = "deals"
    __table_args__ = (CheckConstraint("stage BETWEEN 0 AND 5", name="sales_stage_range"),
                     CheckConstraint("currency = 'USD'", name="sales_deal_usd"),)
    id = Column(Integer, primary_key=True)
    title = Column(String(240), nullable=False)
    account_id = Column(Integer, ForeignKey("sales.accounts.id"), index=True)
    source_id = Column(Integer, ForeignKey("sales.sources.id"), nullable=False, index=True)
    source_reference = Column(Text)
    assigned_user_id = Column(Integer, index=True)
    stage = Column(Integer, nullable=False, default=0, index=True)
    outcome = Column(String(30), nullable=False, default="open", index=True)
    outcome_reason = Column(Text)
    revisit_at = Column(DateTime(timezone=True))
    priority = Column(String(20), nullable=False, default="normal")
    summary = Column(Text)
    discovery_notes = Column(Text)
    qualification = Column(Document, nullable=False, default=dict)
    assessment = Column(Document, nullable=False, default=dict)
    currency = Column(String(3), nullable=False, default="USD")
    acquisition_cost = Column(Numeric(14, 2))
    expected_close_date = Column(Date)
    last_inbound_at = Column(DateTime(timezone=True))
    last_outbound_at = Column(DateTime(timezone=True))
    needs_reply = Column(Boolean, nullable=False, default=False)
    is_test = Column(Boolean, nullable=False, default=False, index=True)
    parent_deal_id = Column(Integer, ForeignKey("sales.deals.id"))
    closed_at = Column(DateTime(timezone=True))


class DealProperty(Base):
    __tablename__ = "deal_properties"
    deal_id = Column(Integer, ForeignKey("sales.deals.id"), primary_key=True)
    property_id = Column(Integer, ForeignKey("sales.properties.id"), primary_key=True)
    rental_revenue = Column(Numeric(14, 2))
    management_revenue = Column(Numeric(14, 2))
    disposition = Column(String(30), nullable=False, default="proposed")


class DealContact(Base):
    __tablename__ = "deal_contacts"
    deal_id = Column(Integer, ForeignKey("sales.deals.id"), primary_key=True)
    contact_id = Column(Integer, ForeignKey("sales.contacts.id"), primary_key=True)
    role = Column(String(100), nullable=False, default="decision-maker")


class Task(Versioned, Base):
    __tablename__ = "tasks"
    __table_args__ = (Index("uq_sales_primary_open", "deal_id", unique=True,
                           postgresql_where=text("is_primary AND status = 'open'")),)
    id = Column(Integer, primary_key=True)
    deal_id = Column(Integer, ForeignKey("sales.deals.id"), nullable=False, index=True)
    title = Column(String(500), nullable=False)
    assigned_user_id = Column(Integer, nullable=False, index=True)
    due_at = Column(DateTime(timezone=True), nullable=False, index=True)
    status = Column(String(20), nullable=False, default="open")
    is_primary = Column(Boolean, nullable=False, default=False)
    kind = Column(String(30), nullable=False, default="followup")
    outcome = Column(Text)
    completed_at = Column(DateTime(timezone=True))


class Activity(Base):
    __tablename__ = "activities"
    id = Column(Integer, primary_key=True)
    deal_id = Column(Integer, ForeignKey("sales.deals.id"), index=True)
    contact_id = Column(Integer, ForeignKey("sales.contacts.id"))
    kind = Column(String(40), nullable=False)
    actor = Column(String(240), nullable=False)
    actor_name = Column(String(240), nullable=False)
    body = Column(Text)
    changes = Column(Document, nullable=False, default=dict)
    direction = Column(String(20))
    outcome = Column(String(80))
    channel = Column(String(30))
    source_url = Column(Text)
    occurred_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)


class StageHistory(Base):
    __tablename__ = "stage_history"
    id = Column(Integer, primary_key=True)
    deal_id = Column(Integer, ForeignKey("sales.deals.id"), nullable=False, index=True)
    stage = Column(Integer, nullable=False)
    outcome = Column(String(30), nullable=False)
    reason = Column(Text)
    actor = Column(String(240), nullable=False)
    entered_at = Column(DateTime(timezone=True), nullable=False)


class Permission(Base):
    __tablename__ = "contact_permissions"
    id = Column(Integer, primary_key=True)
    contact_id = Column(Integer, ForeignKey("sales.contacts.id"), nullable=False, index=True)
    channel = Column(String(30), nullable=False)
    status = Column(String(30), nullable=False)
    basis = Column(Text, nullable=False)
    source_url = Column(Text)
    dnc_result = Column(String(40))
    endpoint_value = Column(String(320))
    actor = Column(String(240), nullable=False)
    reviewed_at = Column(DateTime(timezone=True), nullable=False)
    expires_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)


class Proposal(Base):
    __tablename__ = "proposals"
    __table_args__ = (UniqueConstraint("deal_id", "number", name="uq_sales_proposal_version"),)
    id = Column(Integer, primary_key=True)
    deal_id = Column(Integer, ForeignKey("sales.deals.id"), nullable=False, index=True)
    number = Column(Integer, nullable=False)
    terms = Column(Document, nullable=False)
    document_url = Column(Text, nullable=False)
    created_by = Column(String(240), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)


class ProposalAction(Base):
    __tablename__ = "proposal_actions"
    id = Column(Integer, primary_key=True)
    proposal_id = Column(Integer, ForeignKey("sales.proposals.id"), nullable=False, index=True)
    action = Column(String(30), nullable=False)
    actor = Column(String(240), nullable=False)
    note = Column(Text)
    occurred_at = Column(DateTime(timezone=True), nullable=False)


class Agreement(Base):
    __tablename__ = "agreements"
    __table_args__ = (UniqueConstraint("deal_id", name="uq_sales_agreement_deal"),)
    id = Column(Integer, primary_key=True)
    deal_id = Column(Integer, ForeignKey("sales.deals.id"), nullable=False)
    proposal_id = Column(Integer, ForeignKey("sales.proposals.id"), nullable=False)
    document_url = Column(Text, nullable=False)
    signed_at = Column(DateTime(timezone=True), nullable=False)
    effective_date = Column(Date, nullable=False)
    signatories = Column(Document, nullable=False)
    scope = Column(Document, nullable=False)
    recorded_by = Column(String(240), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)


class Handoff(Versioned, Base):
    __tablename__ = "handoffs"
    agreement_id = Column(Integer, ForeignKey("sales.agreements.id"), primary_key=True)
    status = Column(String(30), nullable=False, default="pending")
    assigned_user_id = Column(Integer, nullable=False, index=True)
    target_date = Column(Date)
    commitments = Column(Text, nullable=False)
    checklist = Column(Document, nullable=False, default=list)
    operational_links = Column(Document, nullable=False, default=list)
    accepted_by = Column(String(240))
    accepted_at = Column(DateTime(timezone=True))
    completed_at = Column(DateTime(timezone=True))


class Command(Base):
    __tablename__ = "commands"
    actor = Column(String(240), primary_key=True)
    key = Column(String(36), primary_key=True)
    fingerprint = Column(String(64), nullable=False)
    response = Column(Document, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)


class Intake(Base):
    __tablename__ = "intake"
    __table_args__ = (UniqueConstraint("source_id", "external_id", name="uq_sales_intake_origin"),)
    id = Column(Integer, primary_key=True)
    source_id = Column(Integer, ForeignKey("sales.sources.id"), nullable=False)
    external_id = Column(String(240), nullable=False)
    fingerprint = Column(String(64), nullable=False)
    original = Column(Document, nullable=False)
    deal_id = Column(Integer, ForeignKey("sales.deals.id"))
    status = Column(String(30), nullable=False, default="pending")
    error = Column(Text)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)


class Notice(Base):
    __tablename__ = "notices"
    id = Column(Integer, primary_key=True)
    key = Column(String(240), nullable=False, unique=True)
    deal_id = Column(Integer, ForeignKey("sales.deals.id"), nullable=False, index=True)
    task_id = Column(Integer, ForeignKey("sales.tasks.id"))
    task_version = Column(Integer)
    event_type = Column(String(80), nullable=False)
    payload = Column(Document, nullable=False, default=dict)
    status = Column(String(30), nullable=False, default="pending", index=True)
    error = Column(Text)
    shared_event_id = Column(String(36))
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)


class Settings(Versioned, Base):
    __tablename__ = "settings"
    id = Column(Integer, primary_key=True)
    timezone = Column(String(80), nullable=False, default="America/Los_Angeles")
    reminder_minutes = Column(Integer)
    escalation_minutes = Column(Integer)
    unassigned_minutes = Column(Integer)
    qualification_guidance = Column(Text)
    assignment_guidance = Column(Text)
    commercial_guidance = Column(Text)
    onboarding_guidance = Column(Text)


def migrate(connection):
    """Explicitly invoked additive migration; no sample data or user grants."""
    from sqlalchemy.schema import CreateSchema
    connection.execute(text("SELECT pg_advisory_xact_lock(84320261028)"))
    connection.execute(CreateSchema("sales", if_not_exists=True))
    Base.metadata.create_all(connection)
    connection.execute(text("ALTER TABLE sales.contact_permissions ADD COLUMN IF NOT EXISTS endpoint_value VARCHAR(320)"))
    connection.execute(text("INSERT INTO sales.settings (id, version, timezone, created_at, updated_at) VALUES (1, 1, 'America/Los_Angeles', now(), now()) ON CONFLICT (id) DO NOTHING"))
    connection.execute(text("""CREATE OR REPLACE FUNCTION sales.reject_history_edit() RETURNS trigger AS $$
        BEGIN RAISE EXCEPTION 'Sales history is append-only'; END;
        $$ LANGUAGE plpgsql"""))
    for name in ("activities", "stage_history", "contact_permissions", "proposals", "proposal_actions", "agreements"):
        connection.execute(text(f"DROP TRIGGER IF EXISTS immutable_history ON sales.{name}"))
        connection.execute(text(f"CREATE TRIGGER immutable_history BEFORE UPDATE OR DELETE ON sales.{name} FOR EACH ROW EXECUTE FUNCTION sales.reject_history_edit()"))
