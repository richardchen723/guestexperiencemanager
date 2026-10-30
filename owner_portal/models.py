"""Additive owner-only schema; no staff identities, guests, or source payloads."""
from datetime import datetime, timezone

from sqlalchemy import (Boolean, CheckConstraint, Column, Date, DateTime, ForeignKey,
                        Integer, MetaData, String, UniqueConstraint, text)
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


def migrate(connection):
    """Run explicitly as an administrator, never from app startup or a request."""
    if connection.dialect.name != 'postgresql':
        raise ValueError('Owner access requires PostgreSQL.')
    connection.execute(text('SELECT pg_advisory_xact_lock(847, 1)'))
    connection.execute(text('CREATE SCHEMA IF NOT EXISTS owners'))
    Base.metadata.create_all(connection)
