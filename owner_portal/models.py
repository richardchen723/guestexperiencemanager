"""Additive owner-only schema; no staff identities, guests, or source payloads."""
from datetime import datetime, timezone

from sqlalchemy import (Boolean, CheckConstraint, Column, Date, DateTime, ForeignKey,
                        Integer, LargeBinary, MetaData, Numeric, String, UniqueConstraint, text)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import declarative_base

Base = declarative_base(metadata=MetaData(schema='owners'))


def utcnow():
    return datetime.now(timezone.utc)


class Account(Base):
    __tablename__ = 'accounts'
    id = Column(Integer, primary_key=True)
    name = Column(String(160), nullable=False)
    active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)


class Property(Base):
    __tablename__ = 'properties'
    id = Column(Integer, primary_key=True)
    name = Column(String(160), nullable=False)
    timezone = Column(String(80), nullable=False)
    currency = Column(String(3), nullable=False, default='USD')
    # A display grouping only. It never supplies owner entitlement.
    portfolio_label = Column(String(160))
    active = Column(Boolean, nullable=False, default=True)
    __table_args__ = (CheckConstraint("currency = 'USD'", name='property_usd'),)


class ListingMapping(Base):
    __tablename__ = 'listing_mappings'
    id = Column(Integer, primary_key=True)
    property_id = Column(Integer, ForeignKey('owners.properties.id'), nullable=False)
    listing_id = Column(Integer, nullable=False, unique=True)
    # Phase one supports verified single physical units only; bundles stay unassigned.
    verified_by = Column(String(80), nullable=False)
    verified_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)


class User(Base):
    __tablename__ = 'users'
    id = Column(Integer, primary_key=True)
    google_subject = Column(String(255), nullable=False, unique=True)
    email = Column(String(320), nullable=False)
    active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)


class Membership(Base):
    __tablename__ = 'memberships'
    id = Column(Integer, primary_key=True)
    account_id = Column(Integer, ForeignKey('owners.accounts.id'), nullable=False)
    user_id = Column(Integer, ForeignKey('owners.users.id'), nullable=False)
    performance = Column(Boolean, nullable=False, default=True)
    statements = Column(Boolean, nullable=False, default=False)
    revoked_at = Column(DateTime(timezone=True))
    __table_args__ = (UniqueConstraint('account_id', 'user_id', name='membership_account_user'),)


class PropertyGrant(Base):
    __tablename__ = 'property_grants'
    id = Column(Integer, primary_key=True)
    account_id = Column(Integer, ForeignKey('owners.accounts.id'), nullable=False)
    property_id = Column(Integer, ForeignKey('owners.properties.id'), nullable=False)
    reporting_from = Column(Date, nullable=False)
    reporting_until = Column(Date)  # Exclusive; independent of when access was granted.
    revoked_at = Column(DateTime(timezone=True))
    __table_args__ = (
        UniqueConstraint('account_id', 'property_id', name='grant_account_property'),
        CheckConstraint('reporting_until IS NULL OR reporting_until > reporting_from', name='grant_date_range'),
    )


class Invitation(Base):
    __tablename__ = 'invitations'
    id = Column(Integer, primary_key=True)
    account_id = Column(Integer, ForeignKey('owners.accounts.id'), nullable=False)
    email = Column(String(320), nullable=False)
    token_hash = Column(String(64), nullable=False, unique=True)
    performance = Column(Boolean, nullable=False)
    statements = Column(Boolean, nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    accepted_at = Column(DateTime(timezone=True))
    accepted_user_id = Column(Integer, ForeignKey('owners.users.id'))
    revoked_at = Column(DateTime(timezone=True))


class LoginFlow(Base):
    __tablename__ = 'login_flows'
    state_hash = Column(String(64), primary_key=True)
    nonce_hash = Column(String(64), nullable=False)
    binding_hash = Column(String(64), nullable=False)
    invitation_hash = Column(String(64))
    expires_at = Column(DateTime(timezone=True), nullable=False)
    consumed_at = Column(DateTime(timezone=True))


class LoginSession(Base):
    __tablename__ = 'login_sessions'
    token_hash = Column(String(64), primary_key=True)
    user_id = Column(Integer, ForeignKey('owners.users.id'), nullable=False, index=True)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    revoked_at = Column(DateTime(timezone=True))


class AuditEvent(Base):
    __tablename__ = 'audit_events'
    id = Column(Integer, primary_key=True)
    actor = Column(String(80), nullable=False)
    action = Column(String(80), nullable=False)
    target_type = Column(String(40), nullable=False)
    target_id = Column(Integer)
    details = Column(JSONB, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)


class PerformanceMonth(Base):
    """Prepared aggregates only; the web role cannot update these rows."""
    __tablename__ = 'performance_months'
    property_id = Column(Integer, ForeignKey('owners.properties.id'), primary_key=True)
    month = Column(Date, primary_key=True)
    as_of = Column(Date, nullable=False)
    prepared_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    metrics = Column(JSONB, nullable=False)


class FinanceCache(Base):
    """Worker-only, validated accommodation amount. No guest/source payloads."""
    __tablename__ = 'finance_cache'
    reservation_id = Column(Integer, primary_key=True)
    fingerprint = Column(String(64), nullable=False)
    accommodation = Column(Numeric(14, 2))
    reason = Column(String(80))
    checked_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)


class CalendarObservation(Base):
    """Worker-only history beyond the staff feed's 60-day forward horizon."""
    __tablename__ = 'calendar_observations'
    listing_id = Column(Integer, primary_key=True)
    calendar_date = Column(Date, primary_key=True)
    snapshot_date = Column(Date, primary_key=True)
    status = Column(String(30))
    is_available = Column(Boolean)
    price = Column(Numeric(14, 2))
    minimum_stay = Column(Integer)


class PublishedStatement(Base):
    """An immutable, explicitly reviewed recipient and scope snapshot."""
    __tablename__ = 'published_statements'
    id = Column(Integer, primary_key=True)
    account_id = Column(Integer, ForeignKey('owners.accounts.id'), nullable=False)
    revision_id = Column(Integer)  # Null for an explicitly uploaded finalized statement.
    version = Column(Integer, nullable=False)
    period_start = Column(Date, nullable=False)
    period_end = Column(Date, nullable=False)  # Exclusive.
    property_ids = Column(JSONB, nullable=False)
    property_names = Column(JSONB, nullable=False)
    account_name = Column(String(160), nullable=False)
    revenue = Column(Numeric(14, 2), nullable=False)
    expenses = Column(Numeric(14, 2), nullable=False)
    management_fee = Column(Numeric(14, 2), nullable=False)
    net_income = Column(Numeric(14, 2), nullable=False)
    published_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    published_by = Column(String(80), nullable=False)
    withdrawn_at = Column(DateTime(timezone=True))
    __table_args__ = (
        UniqueConstraint('account_id', 'revision_id', name='statement_recipient_revision'),
        UniqueConstraint('account_id', 'period_start', 'version', name='statement_version'),
        CheckConstraint('period_end > period_start', name='statement_interval'),
    )


class StatementDocument(Base):
    __tablename__ = 'statement_documents'
    statement_id = Column(Integer, ForeignKey('owners.published_statements.id'), primary_key=True)
    sha256 = Column(String(64), nullable=False)
    content = Column(LargeBinary, nullable=False)
    __table_args__ = (CheckConstraint('octet_length(content) <= 5242880', name='statement_pdf_size'),)


class OwnerRequest(Base):
    """Explicit staff publication; no automatic copying of internal ticket text."""
    __tablename__ = 'requests'
    id = Column(Integer, primary_key=True)
    account_id = Column(Integer, ForeignKey('owners.accounts.id'), nullable=False)
    property_id = Column(Integer, ForeignKey('owners.properties.id'), nullable=False)
    title = Column(String(120), nullable=False)
    summary = Column(String(1000), nullable=False)
    amount = Column(Numeric(14, 2))
    published_by = Column(String(80), nullable=False)
    published_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    reporting_date = Column(Date, nullable=False)
    response = Column(String(20))
    responded_by = Column(Integer, ForeignKey('owners.users.id'))
    responded_at = Column(DateTime(timezone=True))
    withdrawn_at = Column(DateTime(timezone=True))
    __table_args__ = (CheckConstraint("amount IS NULL OR amount >= 0", name='request_positive_amount'),
                     CheckConstraint("response IS NULL OR response IN ('approved','discuss')", name='request_response'),)


def migrate(connection):
    """Run explicitly as an administrator, never from app startup or a request."""
    if connection.dialect.name != 'postgresql':
        raise ValueError('Owner access requires PostgreSQL.')
    connection.execute(text('SELECT pg_advisory_xact_lock(847, 1)'))
    connection.execute(text('CREATE SCHEMA IF NOT EXISTS owners'))
    Base.metadata.create_all(connection)
    connection.execute(text('ALTER TABLE owners.published_statements ALTER COLUMN revision_id DROP NOT NULL'))
