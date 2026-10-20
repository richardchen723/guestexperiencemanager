"""Additive CRM tables; original messages and audit entries are append-only."""

from datetime import datetime, timezone

from sqlalchemy import Column, Date, DateTime, ForeignKey, Integer, JSON, MetaData, String, Text, UniqueConstraint, Boolean
from sqlalchemy.orm import declarative_base
from sqlalchemy.dialects.postgresql import JSONB

from dashboard.auth.models import get_session


def utcnow():
    return datetime.now(timezone.utc)


Base = declarative_base(metadata=MetaData(schema="inquiries"))
Document = JSONB().with_variant(JSON(), "sqlite")


class Guest(Base):
    __tablename__ = "guests"
    id = Column(Integer, primary_key=True)
    email_key = Column(String(320), unique=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)


class Lead(Base):
    __tablename__ = "leads"
    __table_args__ = (UniqueConstraint("source", "reference", name="uq_inquiry_reference"),)
    id = Column(Integer, primary_key=True)
    version = Column(Integer, nullable=False, default=1)
    is_test = Column(Boolean, nullable=False, default=False, server_default="false", index=True)
    guest_id = Column(Integer, ForeignKey("inquiries.guests.id"), nullable=False)
    source = Column(String(80), nullable=False)
    brand = Column(String(160))
    reference = Column(String(160))
    received_at = Column(DateTime(timezone=True), nullable=False, index=True)
    guest_name = Column(String(240))
    email = Column(String(320), index=True)
    phone = Column(String(100))
    listing_id = Column(Integer, index=True)
    tag_id = Column(Integer, index=True)
    property_text = Column(String(500))
    check_in = Column(Date, index=True)
    check_out = Column(Date)
    original_notes = Column(Text, nullable=False)
    details = Column(Text)
    trip_type = Column(String(240))
    qualification = Column(Document, nullable=False, default=dict)
    internal_notes = Column(Text)
    assigned_user_id = Column(Integer, index=True)
    status = Column(String(40), nullable=False, default="New", index=True)
    next_action = Column(Text)
    follow_up_at = Column(DateTime(timezone=True), index=True)
    last_contact_at = Column(DateTime(timezone=True))
    last_inbound_at = Column(DateTime(timezone=True))
    needs_reply = Column(Boolean, nullable=False, default=False, index=True)
    review_flags = Column(Document, nullable=False, default=list)
    staff_fields = Column(Document, nullable=False, default=list)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_by = Column(String(240), nullable=False, default="Larry")


class SourceMessage(Base):
    __tablename__ = "messages"
    __table_args__ = (UniqueConstraint("mailbox", "message_id", name="uq_inquiry_message"),)
    id = Column(Integer, primary_key=True)
    is_test = Column(Boolean, nullable=False, default=False, server_default="false")
    lead_id = Column(Integer, ForeignKey("inquiries.leads.id"), index=True)
    mailbox = Column(String(320), nullable=False)
    source = Column(String(80), nullable=False)
    message_id = Column(String(240), nullable=False)
    thread_id = Column(String(240), index=True)
    internet_message_id = Column(String(500), index=True)
    in_reply_to = Column(String(500))
    reference = Column(String(160))
    sender = Column(String(320), nullable=False)
    subject = Column(Text, nullable=False)
    body = Column(Text, nullable=False)
    received_at = Column(DateTime(timezone=True), nullable=False)
    source_url = Column(Text)
    fingerprint = Column(String(64), nullable=False)
    processing_status = Column(String(30), nullable=False, default="pending", index=True)
    error = Column(Text)
    attempts = Column(Integer, nullable=False, default=0)
    extraction = Column(Document, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)


class Activity(Base):
    __tablename__ = "activities"
    id = Column(Integer, primary_key=True)
    lead_id = Column(Integer, ForeignKey("inquiries.leads.id"), nullable=False, index=True)
    message_id = Column(Integer, ForeignKey("inquiries.messages.id"))
    actor = Column(String(240), nullable=False)
    action = Column(String(80), nullable=False)
    changes = Column(Document, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)


class Settings(Base):
    __tablename__ = "settings"
    id = Column(Integer, primary_key=True)
    version = Column(Integer, nullable=False, default=1)
    timezone = Column(String(80), nullable=False, default="America/Los_Angeles")
    # Null means timing has not been agreed; overdue/unassigned queues still work.
    reminder_minutes = Column(Integer)
    escalation_minutes = Column(Integer)
    unassigned_minutes = Column(Integer)


def init_inquiry_database():
    """Call under the application's existing cross-process initialization lock."""
    from sqlalchemy.schema import CreateSchema
    from sqlalchemy import text
    with get_session() as db:
        db.execute(CreateSchema("inquiries", if_not_exists=True))
        Base.metadata.create_all(db.connection())
        # Additive upgrade: existing records remain genuine and unchanged.
        if db.bind.dialect.name == "postgresql":
            db.execute(text("ALTER TABLE inquiries.leads ADD COLUMN IF NOT EXISTS is_test BOOLEAN NOT NULL DEFAULT false"))
            db.execute(text("ALTER TABLE inquiries.messages ADD COLUMN IF NOT EXISTS is_test BOOLEAN NOT NULL DEFAULT false"))
        if not db.get(Settings, 1):
            db.add(Settings(id=1))
        db.commit()
