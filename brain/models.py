#!/usr/bin/env python3
"""
Database models for STR Signal Brain.
"""

import hashlib
import json
import logging
import os
from datetime import date, datetime
from pathlib import Path
from typing import Any

import sqlalchemy
from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship, sessionmaker
from sqlalchemy.types import JSON

try:
    from dotenv import load_dotenv

    env_path = Path(__file__).resolve().parent.parent / ".env"
    if env_path.exists():
        load_dotenv(env_path)
    else:
        load_dotenv()
except ImportError:
    pass

BRAIN_SCHEMA = "brain"
logger = logging.getLogger(__name__)

Base = declarative_base()

SIGNAL_STATUSES = ("new", "acknowledged", "watching", "resolved", "ignored", "escalated")
SIGNAL_CATEGORIES = (
    "guest_experience",
    "review_risk",
    "operational_open_loop",
    "checkin_checkout_risk",
    "repeated_issue",
    "revenue_booking_health",
    "owner_decision",
)
SIGNAL_SEVERITIES = ("low", "medium", "high", "critical")
SIGNAL_AUDIENCES = ("operator", "revenue")


def _json_type():
    return JSONB if os.getenv("DATABASE_URL") else JSON


class Portfolio(Base):
    """Portfolio-first grouping for listings."""

    __tablename__ = "portfolios"
    __table_args__ = (
        UniqueConstraint("name", name="uq_brain_portfolios_name"),
        {"schema": BRAIN_SCHEMA} if os.getenv("DATABASE_URL") else {},
    )

    portfolio_id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, nullable=False, index=True)
    description = Column(Text)
    status = Column(String, nullable=False, default="healthy", index=True)
    sort_order = Column(Integer, nullable=False, default=0)
    is_active = Column(Boolean, nullable=False, default=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    listings = relationship("PortfolioListing", back_populates="portfolio", cascade="all, delete-orphan")
    users = relationship("PortfolioUser", back_populates="portfolio", cascade="all, delete-orphan")


class PortfolioListing(Base):
    """Maps a Hostaway listing to exactly one Brain portfolio."""

    __tablename__ = "portfolio_listings"
    __table_args__ = (
        UniqueConstraint("listing_id", name="uq_brain_portfolio_listing_listing_id"),
        Index("idx_brain_portfolio_listings_portfolio", "portfolio_id"),
        {"schema": BRAIN_SCHEMA} if os.getenv("DATABASE_URL") else {},
    )

    portfolio_listing_id = Column(Integer, primary_key=True, autoincrement=True)
    portfolio_id = Column(
        Integer,
        ForeignKey(f"{BRAIN_SCHEMA}.portfolios.portfolio_id", ondelete="CASCADE") if os.getenv("DATABASE_URL") else ForeignKey("portfolios.portfolio_id", ondelete="CASCADE"),
        nullable=False,
    )
    listing_id = Column(Integer, nullable=False, index=True)
    listing_name_override = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    portfolio = relationship("Portfolio", back_populates="listings")


class PortfolioUser(Base):
    """Portfolio-scoped brief recipients for approved Brain users."""

    __tablename__ = "portfolio_users"
    __table_args__ = (
        UniqueConstraint("portfolio_id", "user_id", name="uq_brain_portfolio_user"),
        {"schema": BRAIN_SCHEMA} if os.getenv("DATABASE_URL") else {},
    )

    portfolio_user_id = Column(Integer, primary_key=True, autoincrement=True)
    portfolio_id = Column(
        Integer,
        ForeignKey(f"{BRAIN_SCHEMA}.portfolios.portfolio_id", ondelete="CASCADE") if os.getenv("DATABASE_URL") else ForeignKey("portfolios.portfolio_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id = Column(Integer, nullable=False, index=True)
    role = Column(String, nullable=False, default="operator")
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    portfolio = relationship("Portfolio", back_populates="users")


class SignalRun(Base):
    """A morning, afternoon, nightly, or manual Brain analysis run."""

    __tablename__ = "signal_runs"
    __table_args__ = (
        Index("idx_brain_signal_runs_started", "started_at"),
        {"schema": BRAIN_SCHEMA} if os.getenv("DATABASE_URL") else {},
    )

    signal_run_id = Column(Integer, primary_key=True, autoincrement=True)
    run_type = Column(String, nullable=False, index=True)
    status = Column(String, nullable=False, default="running", index=True)
    prompt_version = Column(String)
    model = Column(String)
    input_hash = Column(String)
    source_counts = Column(_json_type())
    usage = Column(_json_type())
    raw_output = Column(_json_type())
    error_message = Column(Text)
    started_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    completed_at = Column(DateTime)

    signals = relationship("Signal", back_populates="run")


class Signal(Base):
    """A ranked, evidence-backed item that deserves attention."""

    __tablename__ = "signals"
    __table_args__ = (
        UniqueConstraint("dedupe_key", name="uq_brain_signals_dedupe_key"),
        Index("idx_brain_signals_status_rank", "status", "rank_score"),
        Index("idx_brain_signals_portfolio_status", "portfolio_id", "status"),
        {"schema": BRAIN_SCHEMA} if os.getenv("DATABASE_URL") else {},
    )

    signal_id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(
        Integer,
        ForeignKey(f"{BRAIN_SCHEMA}.signal_runs.signal_run_id", ondelete="SET NULL") if os.getenv("DATABASE_URL") else ForeignKey("signal_runs.signal_run_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    portfolio_id = Column(
        Integer,
        ForeignKey(f"{BRAIN_SCHEMA}.portfolios.portfolio_id", ondelete="SET NULL") if os.getenv("DATABASE_URL") else ForeignKey("portfolios.portfolio_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    listing_id = Column(Integer, nullable=True, index=True)
    reservation_id = Column(Integer, nullable=True, index=True)
    category = Column(String, nullable=False, index=True)
    severity = Column(String, nullable=False, default="medium", index=True)
    confidence = Column(Float, nullable=False, default=0.5)
    title = Column(String, nullable=False)
    summary = Column(Text)
    why_it_matters = Column(Text)
    suggested_action = Column(Text)
    owner_or_manager = Column(String, nullable=False, default="operator", index=True)
    status = Column(String, nullable=False, default="new", index=True)
    rank_score = Column(Float, nullable=False, default=0.0, index=True)
    dedupe_key = Column(String, nullable=False)
    source = Column(String, nullable=False, default="deterministic")
    first_seen_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    last_seen_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    resolved_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    run = relationship("SignalRun", back_populates="signals")
    evidence = relationship("SignalEvidence", back_populates="signal", cascade="all, delete-orphan")


class SignalEvidence(Base):
    """Stored evidence cited by a signal or Ask Brain answer."""

    __tablename__ = "signal_evidence"
    __table_args__ = (
        Index("idx_brain_signal_evidence_signal", "signal_id"),
        Index("idx_brain_signal_evidence_source", "source_type", "source_id"),
        {"schema": BRAIN_SCHEMA} if os.getenv("DATABASE_URL") else {},
    )

    evidence_id = Column(Integer, primary_key=True, autoincrement=True)
    signal_id = Column(
        Integer,
        ForeignKey(f"{BRAIN_SCHEMA}.signals.signal_id", ondelete="CASCADE") if os.getenv("DATABASE_URL") else ForeignKey("signals.signal_id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    source_type = Column(String, nullable=False, index=True)
    source_id = Column(String, nullable=True, index=True)
    listing_id = Column(Integer, nullable=True, index=True)
    reservation_id = Column(Integer, nullable=True, index=True)
    occurred_at = Column(DateTime)
    summary = Column(Text, nullable=False)
    excerpt = Column(Text)
    url = Column(String)
    evidence_metadata = Column(_json_type())
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    signal = relationship("Signal", back_populates="evidence")


class DataSource(Base):
    """A source Brain can ingest into the analytical foundation."""

    __tablename__ = "data_sources"
    __table_args__ = (
        UniqueConstraint("source_key", name="uq_brain_data_sources_key"),
        Index("idx_brain_data_sources_status", "status", "is_active"),
        {"schema": BRAIN_SCHEMA} if os.getenv("DATABASE_URL") else {},
    )

    data_source_id = Column(Integer, primary_key=True, autoincrement=True)
    source_key = Column(String, nullable=False, index=True)
    name = Column(String, nullable=False)
    category = Column(String, nullable=False, index=True)
    connector_type = Column(String, nullable=False, default="database")
    cadence_minutes = Column(Integer, nullable=True)
    freshness_threshold_minutes = Column(Integer, nullable=False, default=2160)
    status = Column(String, nullable=False, default="missing", index=True)
    is_active = Column(Boolean, nullable=False, default=True, index=True)
    last_ingested_at = Column(DateTime)
    last_success_at = Column(DateTime)
    last_error_at = Column(DateTime)
    last_error_message = Column(Text)
    description = Column(Text)
    source_metadata = Column(_json_type())
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    runs = relationship("DataIngestionRun", back_populates="source")


class DataIngestionRun(Base):
    """Ledger entry for one pull or materialization pass from a source."""

    __tablename__ = "data_ingestion_runs"
    __table_args__ = (
        Index("idx_brain_data_ingestion_source_started", "source_key", "started_at"),
        Index("idx_brain_data_ingestion_status", "status"),
        {"schema": BRAIN_SCHEMA} if os.getenv("DATABASE_URL") else {},
    )

    data_ingestion_run_id = Column(Integer, primary_key=True, autoincrement=True)
    data_source_id = Column(
        Integer,
        ForeignKey(f"{BRAIN_SCHEMA}.data_sources.data_source_id", ondelete="SET NULL") if os.getenv("DATABASE_URL") else ForeignKey("data_sources.data_source_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    brain_run_id = Column(
        Integer,
        ForeignKey(f"{BRAIN_SCHEMA}.signal_runs.signal_run_id", ondelete="SET NULL") if os.getenv("DATABASE_URL") else ForeignKey("signal_runs.signal_run_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    source_key = Column(String, nullable=False, index=True)
    mode = Column(String, nullable=False, default="incremental", index=True)
    status = Column(String, nullable=False, default="running", index=True)
    records_seen = Column(Integer, nullable=False, default=0)
    facts_written = Column(Integer, nullable=False, default=0)
    facts_created = Column(Integer, nullable=False, default=0)
    facts_updated = Column(Integer, nullable=False, default=0)
    facts_unchanged = Column(Integer, nullable=False, default=0)
    facts_withdrawn = Column(Integer, nullable=False, default=0)
    started_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    completed_at = Column(DateTime)
    watermark_start = Column(_json_type())
    watermark_end = Column(_json_type())
    record_counts = Column(_json_type())
    run_metadata = Column(_json_type())
    error_message = Column(Text)

    source = relationship("DataSource", back_populates="runs")
    facts = relationship("BusinessFact", back_populates="ingestion_run")


class BusinessFact(Base):
    """Normalized, provenance-backed fact for future Brain decision products."""

    __tablename__ = "business_facts"
    __table_args__ = (
        UniqueConstraint("fact_key", name="uq_brain_business_facts_key"),
        Index("idx_brain_business_facts_type_time", "fact_type", "occurred_at"),
        Index("idx_brain_business_facts_listing_type", "listing_id", "fact_type"),
        Index("idx_brain_business_facts_reservation", "reservation_id"),
        Index("idx_brain_business_facts_source", "source_key", "source_id"),
        Index("idx_brain_business_facts_status", "status"),
        {"schema": BRAIN_SCHEMA} if os.getenv("DATABASE_URL") else {},
    )

    business_fact_id = Column(Integer, primary_key=True, autoincrement=True)
    ingestion_run_id = Column(
        Integer,
        ForeignKey(f"{BRAIN_SCHEMA}.data_ingestion_runs.data_ingestion_run_id", ondelete="SET NULL") if os.getenv("DATABASE_URL") else ForeignKey("data_ingestion_runs.data_ingestion_run_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    fact_key = Column(String, nullable=False)
    fact_type = Column(String, nullable=False, index=True)
    grain = Column(String, nullable=False, default="event", index=True)
    source_key = Column(String, nullable=False, index=True)
    source_table = Column(String, nullable=True)
    source_id = Column(String, nullable=False, index=True)
    portfolio_id = Column(Integer, nullable=True, index=True)
    listing_id = Column(Integer, nullable=True, index=True)
    reservation_id = Column(Integer, nullable=True, index=True)
    guest_id = Column(Integer, nullable=True, index=True)
    occurred_at = Column(DateTime, nullable=True, index=True)
    effective_start = Column(DateTime, nullable=True, index=True)
    effective_end = Column(DateTime, nullable=True, index=True)
    numeric_value = Column(Float, nullable=True)
    text_value = Column(Text, nullable=True)
    confidence = Column(Float, nullable=False, default=1.0)
    status = Column(String, nullable=False, default="active", index=True)
    fact_payload = Column(_json_type())
    fact_hash = Column(String, nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    ingestion_run = relationship("DataIngestionRun", back_populates="facts")


class BusinessMetricSnapshot(Base):
    """Decision-ready metric derived from normalized facts."""

    __tablename__ = "business_metric_snapshots"
    __table_args__ = (
        UniqueConstraint("metric_key", name="uq_brain_business_metric_snapshots_key"),
        Index("idx_brain_business_metric_listing_date", "listing_id", "metric_date"),
        Index("idx_brain_business_metric_portfolio_date", "portfolio_id", "metric_date"),
        Index("idx_brain_business_metric_name_date", "metric_name", "metric_date"),
        Index("idx_brain_business_metric_category_status", "category", "status"),
        {"schema": BRAIN_SCHEMA} if os.getenv("DATABASE_URL") else {},
    )

    business_metric_snapshot_id = Column(Integer, primary_key=True, autoincrement=True)
    metric_key = Column(String, nullable=False)
    metric_name = Column(String, nullable=False, index=True)
    category = Column(String, nullable=False, index=True)
    grain = Column(String, nullable=False, default="listing", index=True)
    metric_date = Column(Date, nullable=False, default=date.today, index=True)
    horizon_days = Column(Integer, nullable=True, index=True)
    portfolio_id = Column(Integer, nullable=True, index=True)
    listing_id = Column(Integer, nullable=True, index=True)
    numeric_value = Column(Float, nullable=True)
    text_value = Column(Text, nullable=True)
    status = Column(String, nullable=False, default="ok", index=True)
    confidence = Column(Float, nullable=False, default=1.0)
    source_keys = Column(_json_type())
    metric_payload = Column(_json_type())
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    computed_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class CodexIntelligenceRun(Base):
    """A Codex-subscription intelligence pass over the normalized data foundation."""

    __tablename__ = "codex_intelligence_runs"
    __table_args__ = (
        UniqueConstraint("run_key", name="uq_brain_codex_intelligence_runs_key"),
        Index("idx_brain_codex_intelligence_runs_status", "status"),
        Index("idx_brain_codex_intelligence_runs_generated", "generated_at"),
        {"schema": BRAIN_SCHEMA} if os.getenv("DATABASE_URL") else {},
    )

    codex_intelligence_run_id = Column(Integer, primary_key=True, autoincrement=True)
    run_key = Column(String, nullable=False, index=True)
    status = Column(String, nullable=False, default="prepared", index=True)
    cadence = Column(String, nullable=False, default="weekly", index=True)
    analysis_window_start = Column(DateTime, nullable=True, index=True)
    analysis_window_end = Column(DateTime, nullable=True, index=True)
    packet_path = Column(String)
    packet_hash = Column(String, index=True)
    packet_summary = Column(_json_type())
    source_snapshot = Column(_json_type())
    generated_by = Column(String, nullable=False, default="codex_subscription")
    completed_at = Column(DateTime)
    error_message = Column(Text)
    generated_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    insights = relationship("CodexIntelligenceInsight", back_populates="run", cascade="all, delete-orphan")


class CodexIntelligenceInsight(Base):
    """Stored cross-source intelligence authored by Codex from data packets."""

    __tablename__ = "codex_intelligence_insights"
    __table_args__ = (
        UniqueConstraint("insight_key", name="uq_brain_codex_intelligence_insights_key"),
        Index("idx_brain_codex_intelligence_insights_category_status", "category", "status"),
        Index("idx_brain_codex_intelligence_insights_listing", "listing_id", "category"),
        Index("idx_brain_codex_intelligence_insights_run", "run_id"),
        Index("idx_brain_codex_intelligence_insights_last_seen", "last_seen_at"),
        {"schema": BRAIN_SCHEMA} if os.getenv("DATABASE_URL") else {},
    )

    codex_intelligence_insight_id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(
        Integer,
        ForeignKey(f"{BRAIN_SCHEMA}.codex_intelligence_runs.codex_intelligence_run_id", ondelete="SET NULL") if os.getenv("DATABASE_URL") else ForeignKey("codex_intelligence_runs.codex_intelligence_run_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    insight_key = Column(String, nullable=False)
    category = Column(String, nullable=False, index=True)
    title = Column(String, nullable=False)
    summary = Column(Text, nullable=False)
    reasoning = Column(Text)
    recommended_action = Column(Text)
    expected_impact = Column(String)
    confidence = Column(Float, nullable=False, default=0.7)
    severity = Column(String, nullable=False, default="medium", index=True)
    status = Column(String, nullable=False, default="active", index=True)
    portfolio_id = Column(Integer, nullable=True, index=True)
    listing_id = Column(Integer, nullable=True, index=True)
    reservation_id = Column(Integer, nullable=True, index=True)
    guest_id = Column(Integer, nullable=True, index=True)
    source_fact_ids = Column(_json_type())
    source_metric_ids = Column(_json_type())
    evidence_payload = Column(_json_type())
    insight_payload = Column(_json_type())
    first_seen_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    last_seen_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    run = relationship("CodexIntelligenceRun", back_populates="insights")


class CalendarSnapshot(Base):
    """Hostaway calendar snapshot used for booking health."""

    __tablename__ = "calendar_snapshots"
    __table_args__ = (
        UniqueConstraint("listing_id", "calendar_date", "snapshot_date", name="uq_brain_calendar_snapshot"),
        Index("idx_brain_calendar_listing_date", "listing_id", "calendar_date"),
        {"schema": BRAIN_SCHEMA} if os.getenv("DATABASE_URL") else {},
    )

    calendar_snapshot_id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(Integer, nullable=True, index=True)
    listing_id = Column(Integer, nullable=False, index=True)
    calendar_date = Column(Date, nullable=False, index=True)
    snapshot_date = Column(Date, default=date.today, nullable=False, index=True)
    is_available = Column(Boolean)
    status = Column(String)
    price = Column(Float)
    minimum_stay = Column(Integer)
    maximum_stay = Column(Integer)
    raw_payload = Column(_json_type())
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class PriceLabsSnapshot(Base):
    """Read-only PriceLabs pricing/rules snapshot."""

    __tablename__ = "pricelabs_snapshots"
    __table_args__ = (
        Index("idx_brain_pricelabs_listing_snapshot", "listing_id", "snapshot_date"),
        {"schema": BRAIN_SCHEMA} if os.getenv("DATABASE_URL") else {},
    )

    pricelabs_snapshot_id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(Integer, nullable=True, index=True)
    listing_id = Column(Integer, nullable=False, index=True)
    external_listing_id = Column(String)
    snapshot_date = Column(Date, default=date.today, nullable=False, index=True)
    status = Column(String, nullable=False, default="unknown")
    confidence = Column(Float, nullable=False, default=0.0)
    raw_payload = Column(_json_type())
    error_message = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class BookingHealthSnapshot(Base):
    """Exception-focused booking health output for one listing/horizon."""

    __tablename__ = "booking_health_snapshots"
    __table_args__ = (
        UniqueConstraint("listing_id", "horizon_days", "snapshot_date", name="uq_brain_booking_health_snapshot"),
        Index("idx_brain_booking_health_portfolio", "portfolio_id", "snapshot_date"),
        {"schema": BRAIN_SCHEMA} if os.getenv("DATABASE_URL") else {},
    )

    booking_health_snapshot_id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(Integer, nullable=True, index=True)
    portfolio_id = Column(Integer, nullable=True, index=True)
    listing_id = Column(Integer, nullable=False, index=True)
    horizon_days = Column(Integer, nullable=False, index=True)
    snapshot_date = Column(Date, default=date.today, nullable=False, index=True)
    occupancy_rate = Column(Float, nullable=False, default=0.0)
    booked_nights = Column(Integer, nullable=False, default=0)
    available_nights = Column(Integer, nullable=False, default=0)
    expected_occupancy_rate = Column(Float)
    diagnosis = Column(String, nullable=False, default="insufficient_data")
    confidence = Column(Float, nullable=False, default=0.4)
    recommended_action = Column(Text)
    raw_metrics = Column(_json_type())
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class BookingHealthAnalysis(Base):
    """Daily per-listing booking-health analysis for the Booking Health page."""

    __tablename__ = "booking_health_analyses"
    __table_args__ = (
        UniqueConstraint("listing_id", "snapshot_date", name="uq_brain_booking_health_analysis_listing_date"),
        Index("idx_brain_booking_health_analysis_portfolio", "portfolio_id", "snapshot_date"),
        {"schema": BRAIN_SCHEMA} if os.getenv("DATABASE_URL") else {},
    )

    booking_health_analysis_id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(Integer, nullable=True, index=True)
    portfolio_id = Column(Integer, nullable=True, index=True)
    listing_id = Column(Integer, nullable=False, index=True)
    listing_name = Column(String)
    snapshot_date = Column(Date, default=date.today, nullable=False, index=True)
    severity = Column(String, nullable=False, default="watch", index=True)
    confidence = Column(Float, nullable=False, default=0.45)
    booking_pattern = Column(Text)
    pricelabs_opinion = Column(Text)
    airbnb_page_opinion = Column(Text)
    opinion = Column(Text)
    action_items = Column(_json_type())
    horizons = Column(_json_type())
    source_statuses = Column(_json_type())
    raw_payload = Column(_json_type())
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class ListingAuditRun(Base):
    """One portfolio-wide listing audit execution."""

    __tablename__ = "listing_audit_runs"
    __table_args__ = (
        Index("idx_brain_listing_audit_runs_cadence_completed", "cadence", "completed_at"),
        Index("idx_brain_listing_audit_runs_status_started", "status", "started_at"),
        {"schema": BRAIN_SCHEMA} if os.getenv("DATABASE_URL") else {},
    )

    listing_audit_run_id = Column(Integer, primary_key=True, autoincrement=True)
    cadence = Column(String, nullable=False, default="daily", index=True)
    status = Column(String, nullable=False, default="running", index=True)
    snapshot_date = Column(Date, default=date.today, nullable=False, index=True)
    listing_count = Column(Integer, nullable=False, default=0)
    critical_count = Column(Integer, nullable=False, default=0)
    high_count = Column(Integer, nullable=False, default=0)
    watch_count = Column(Integer, nullable=False, default=0)
    healthy_count = Column(Integer, nullable=False, default=0)
    source_statuses = Column(_json_type())
    error_message = Column(Text)
    started_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    completed_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    snapshots = relationship(
        "ListingAuditSnapshot",
        back_populates="run",
        cascade="all, delete-orphan",
    )


class ListingAuditSnapshot(Base):
    """Human-readable audit result for one active listing in one run."""

    __tablename__ = "listing_audit_snapshots"
    __table_args__ = (
        UniqueConstraint("run_id", "listing_id", name="uq_brain_listing_audit_snapshot_run_listing"),
        Index("idx_brain_listing_audit_snapshots_date_severity", "snapshot_date", "severity"),
        Index("idx_brain_listing_audit_snapshots_portfolio", "portfolio_id", "snapshot_date"),
        {"schema": BRAIN_SCHEMA} if os.getenv("DATABASE_URL") else {},
    )

    listing_audit_snapshot_id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(
        Integer,
        ForeignKey(
            f"{BRAIN_SCHEMA}.listing_audit_runs.listing_audit_run_id",
            ondelete="CASCADE",
        ) if os.getenv("DATABASE_URL") else ForeignKey(
            "listing_audit_runs.listing_audit_run_id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )
    portfolio_id = Column(Integer, nullable=True, index=True)
    listing_id = Column(Integer, nullable=False, index=True)
    listing_name = Column(String, nullable=False)
    snapshot_date = Column(Date, default=date.today, nullable=False, index=True)
    severity = Column(String, nullable=False, default="watch", index=True)
    health_score = Column(Float, nullable=False, default=0.0)
    booking_health = Column(_json_type())
    pricing_health = Column(_json_type())
    market_comparison = Column(_json_type())
    online_assets = Column(_json_type())
    action_items = Column(_json_type())
    source_statuses = Column(_json_type())
    raw_payload = Column(_json_type())
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    run = relationship("ListingAuditRun", back_populates="snapshots")


class OpenLoop(Base):
    """Unresolved issue inferred from conversations or WhatsApp context."""

    __tablename__ = "open_loops"
    __table_args__ = (
        Index("idx_brain_open_loops_status", "status"),
        Index("idx_brain_open_loops_portfolio", "portfolio_id"),
        {"schema": BRAIN_SCHEMA} if os.getenv("DATABASE_URL") else {},
    )

    open_loop_id = Column(Integer, primary_key=True, autoincrement=True)
    signal_id = Column(Integer, nullable=True, index=True)
    portfolio_id = Column(Integer, nullable=True, index=True)
    listing_id = Column(Integer, nullable=True, index=True)
    reservation_id = Column(Integer, nullable=True, index=True)
    status = Column(String, nullable=False, default="open", index=True)
    title = Column(String, nullable=False)
    summary = Column(Text)
    involved_people = Column(Text)
    last_known_update = Column(Text)
    suggested_next_step = Column(Text)
    last_activity_at = Column(DateTime)
    confidence = Column(Float, nullable=False, default=0.5)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    resolved_at = Column(DateTime)


class DailyBrief(Base):
    """Generated operating brief."""

    __tablename__ = "daily_briefs"
    __table_args__ = (
        Index("idx_brain_daily_briefs_generated", "generated_at"),
        {"schema": BRAIN_SCHEMA} if os.getenv("DATABASE_URL") else {},
    )

    daily_brief_id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(Integer, nullable=True, index=True)
    audience = Column(String, nullable=False, index=True)
    user_id = Column(Integer, nullable=True, index=True)
    channel = Column(String, nullable=False, default="dashboard")
    subject = Column(String, nullable=False)
    body = Column(Text, nullable=False)
    payload = Column(_json_type())
    status = Column(String, nullable=False, default="generated", index=True)
    generated_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    sent_at = Column(DateTime)
    error_message = Column(Text)


class BriefDeliveryLog(Base):
    """Delivery attempt log for dashboard, email, and WhatsApp briefs."""

    __tablename__ = "brief_delivery_logs"
    __table_args__ = (
        Index("idx_brain_brief_delivery_brief", "daily_brief_id"),
        Index("idx_brain_brief_delivery_provider", "provider", "provider_message_id"),
        {"schema": BRAIN_SCHEMA} if os.getenv("DATABASE_URL") else {},
    )

    brief_delivery_log_id = Column(Integer, primary_key=True, autoincrement=True)
    daily_brief_id = Column(
        Integer,
        ForeignKey(f"{BRAIN_SCHEMA}.daily_briefs.daily_brief_id", ondelete="CASCADE") if os.getenv("DATABASE_URL") else ForeignKey("daily_briefs.daily_brief_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    channel = Column(String, nullable=False, index=True)
    recipient = Column(String, nullable=True)
    provider = Column(String, nullable=True, index=True)
    provider_message_id = Column(String, nullable=True, index=True)
    status = Column(String, nullable=False, default="pending", index=True)
    error_message = Column(Text)
    payload = Column(_json_type())
    attempted_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    delivered_at = Column(DateTime)


class PromptArtifact(Base):
    """Prompt input/output artifact for model-aided Brain runs."""

    __tablename__ = "prompt_artifacts"
    __table_args__ = (
        Index("idx_brain_prompt_artifacts_run", "run_id"),
        {"schema": BRAIN_SCHEMA} if os.getenv("DATABASE_URL") else {},
    )

    prompt_artifact_id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(Integer, nullable=True, index=True)
    prompt_version = Column(String, nullable=False, index=True)
    model = Column(String, nullable=True)
    input_hash = Column(String, nullable=False, index=True)
    input_payload = Column(_json_type())
    output_payload = Column(_json_type())
    usage = Column(_json_type())
    status = Column(String, nullable=False, default="ok", index=True)
    error_message = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class GuestStayMemory(Base):
    """Daily summarized memory for one Hostaway reservation/stay."""

    __tablename__ = "guest_stay_memories"
    __table_args__ = (
        UniqueConstraint("reservation_id", "memory_date", name="uq_brain_guest_stay_memory_reservation_date"),
        Index("idx_brain_guest_stay_memories_portfolio", "portfolio_id", "memory_date"),
        Index("idx_brain_guest_stay_memories_listing", "listing_id", "memory_date"),
        Index("idx_brain_guest_stay_memories_status", "status", "risk_score"),
        {"schema": BRAIN_SCHEMA} if os.getenv("DATABASE_URL") else {},
    )

    guest_stay_memory_id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(Integer, nullable=True, index=True)
    portfolio_id = Column(Integer, nullable=True, index=True)
    listing_id = Column(Integer, nullable=False, index=True)
    reservation_id = Column(Integer, nullable=False, index=True)
    guest_id = Column(Integer, nullable=True, index=True)
    guest_name = Column(String)
    channel_name = Column(String)
    arrival_date = Column(Date)
    departure_date = Column(Date)
    reservation_status = Column(String)
    memory_date = Column(Date, default=date.today, nullable=False, index=True)
    window_start_at = Column(DateTime, nullable=False)
    window_end_at = Column(DateTime, nullable=False)
    message_count = Column(Integer, nullable=False, default=0)
    incoming_count = Column(Integer, nullable=False, default=0)
    last_message_at = Column(DateTime)
    review_id = Column(Integer, nullable=True, index=True)
    review_rating = Column(Float)
    review_date = Column(Date)
    status = Column(String, nullable=False, default="ok", index=True)
    risk_level = Column(String, nullable=False, default="low", index=True)
    risk_score = Column(Float, nullable=False, default=0.0, index=True)
    summary = Column(Text, nullable=False)
    risk_summary = Column(Text)
    latest_guest_issue = Column(Text)
    resolution_summary = Column(Text)
    suggested_action = Column(Text)
    memory_hash = Column(String, nullable=False, index=True)
    source_metadata = Column(_json_type())
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class StayOutcomeClassification(Base):
    """Immutable, evidence-backed outcome classification for one completed stay."""

    __tablename__ = "stay_outcome_classifications"
    __table_args__ = (
        UniqueConstraint(
            "reservation_id",
            name="uq_brain_stay_outcome_reservation_once",
        ),
        UniqueConstraint(
            "reservation_id",
            "input_hash",
            "prompt_version",
            name="uq_brain_stay_outcome_input_version",
        ),
        Index("idx_brain_stay_outcome_listing_departure", "listing_id", "departure_date"),
        Index("idx_brain_stay_outcome_current", "record_status", "outcome", "departure_date"),
        {"schema": BRAIN_SCHEMA} if os.getenv("DATABASE_URL") else {},
    )

    stay_outcome_classification_id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(Integer, nullable=True, index=True)
    listing_id = Column(Integer, nullable=False, index=True)
    reservation_id = Column(Integer, nullable=False, index=True)
    arrival_date = Column(Date, nullable=False)
    departure_date = Column(Date, nullable=False, index=True)
    outcome = Column(String, nullable=False, default="needs_review", index=True)
    confidence = Column(Float, nullable=False, default=0.0)
    issue_count = Column(Integer, nullable=False, default=0)
    material_issue_count = Column(Integer, nullable=False, default=0)
    resolved_issue_count = Column(Integer, nullable=False, default=0)
    unresolved_issue_count = Column(Integer, nullable=False, default=0)
    summary = Column(Text, nullable=False)
    issues = Column(_json_type())
    evidence_message_ids = Column(_json_type())
    input_hash = Column(String, nullable=False, index=True)
    prompt_version = Column(String, nullable=False, index=True)
    model = Column(String)
    record_status = Column(String, nullable=False, default="current", index=True)
    classification_source = Column(String, nullable=False, default="openai")
    source_metadata = Column(_json_type())
    classified_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class WhatsAppThread(Base):
    """WhatsApp conversation thread mapped from a provider sender."""

    __tablename__ = "whatsapp_threads"
    __table_args__ = (
        UniqueConstraint("provider", "provider_thread_id", name="uq_brain_whatsapp_thread_provider"),
        Index("idx_brain_whatsapp_threads_phone", "phone_number"),
        {"schema": BRAIN_SCHEMA} if os.getenv("DATABASE_URL") else {},
    )

    whatsapp_thread_id = Column(Integer, primary_key=True, autoincrement=True)
    provider = Column(String, nullable=False, default="twilio")
    provider_thread_id = Column(String, nullable=False)
    phone_number = Column(String, nullable=True, index=True)
    display_name = Column(String)
    mapped_user_id = Column(Integer, nullable=True, index=True)
    portfolio_id = Column(Integer, nullable=True, index=True)
    status = Column(String, nullable=False, default="active", index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class WhatsAppMessage(Base):
    """Inbound or outbound WhatsApp message stored for Brain memory."""

    __tablename__ = "whatsapp_messages"
    __table_args__ = (
        UniqueConstraint("provider", "provider_message_id", name="uq_brain_whatsapp_message_provider"),
        Index("idx_brain_whatsapp_messages_thread", "whatsapp_thread_id", "received_at"),
        {"schema": BRAIN_SCHEMA} if os.getenv("DATABASE_URL") else {},
    )

    whatsapp_message_id = Column(Integer, primary_key=True, autoincrement=True)
    whatsapp_thread_id = Column(
        Integer,
        ForeignKey(f"{BRAIN_SCHEMA}.whatsapp_threads.whatsapp_thread_id", ondelete="CASCADE") if os.getenv("DATABASE_URL") else ForeignKey("whatsapp_threads.whatsapp_thread_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    provider = Column(String, nullable=False, default="twilio")
    provider_message_id = Column(String, nullable=False)
    from_number = Column(String)
    to_number = Column(String)
    sender_name = Column(String)
    direction = Column(String, nullable=False, default="inbound", index=True)
    body = Column(Text)
    media_urls = Column(_json_type())
    raw_payload = Column(_json_type())
    provider_status = Column(String, nullable=True, index=True)
    error_code = Column(String)
    error_message = Column(Text)
    received_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    thread = relationship("WhatsAppThread")


class WhatsAppGroupMemory(Base):
    """Rolling summarized memory for one WhatsApp group and lookback window."""

    __tablename__ = "whatsapp_group_memories"
    __table_args__ = (
        UniqueConstraint("whatsapp_thread_id", "window_days", "memory_date", name="uq_brain_whatsapp_group_memory_window"),
        Index("idx_brain_whatsapp_group_memories_portfolio", "portfolio_id", "memory_date"),
        Index("idx_brain_whatsapp_group_memories_thread", "whatsapp_thread_id", "memory_date"),
        {"schema": BRAIN_SCHEMA} if os.getenv("DATABASE_URL") else {},
    )

    whatsapp_group_memory_id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(Integer, nullable=True, index=True)
    whatsapp_thread_id = Column(
        Integer,
        ForeignKey(f"{BRAIN_SCHEMA}.whatsapp_threads.whatsapp_thread_id", ondelete="CASCADE") if os.getenv("DATABASE_URL") else ForeignKey("whatsapp_threads.whatsapp_thread_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    portfolio_id = Column(Integer, nullable=True, index=True)
    provider = Column(String, nullable=False, default="whatsapp_web", index=True)
    provider_thread_id = Column(String, nullable=False, index=True)
    group_name = Column(String)
    group_kind = Column(String)
    window_days = Column(Integer, nullable=False, default=60, index=True)
    memory_date = Column(Date, default=date.today, nullable=False, index=True)
    window_start_at = Column(DateTime, nullable=False)
    window_end_at = Column(DateTime, nullable=False)
    message_count = Column(Integer, nullable=False, default=0)
    participant_count = Column(Integer, nullable=False, default=0)
    last_message_at = Column(DateTime)
    status = Column(String, nullable=False, default="ok", index=True)
    summary = Column(Text, nullable=False)
    open_loop_summary = Column(Text)
    risk_summary = Column(Text)
    decision_summary = Column(Text)
    cleaning_maintenance_summary = Column(Text)
    memory_hash = Column(String, nullable=False, index=True)
    source_metadata = Column(_json_type())
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    thread = relationship("WhatsAppThread")


class BrainAuditLog(Base):
    """Audit log for Brain user and system actions."""

    __tablename__ = "brain_audit_logs"
    __table_args__ = (
        Index("idx_brain_audit_logs_created", "created_at"),
        {"schema": BRAIN_SCHEMA} if os.getenv("DATABASE_URL") else {},
    )

    brain_audit_log_id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=True, index=True)
    action = Column(String, nullable=False, index=True)
    entity_type = Column(String, nullable=False, index=True)
    entity_id = Column(String, nullable=True, index=True)
    audit_metadata = Column("metadata", _json_type())
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


_engine_cache = {}
_sessionmaker_cache = {}


def get_engine():
    """Create or retrieve the cached SQLAlchemy engine for Brain tables."""
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise ValueError("DATABASE_URL environment variable is required for STR Signal Brain")

    if "?" in database_url:
        database_url_with_schema = database_url + "&options=-csearch_path%3Dbrain,public,users"
    else:
        database_url_with_schema = database_url + "?options=-csearch_path%3Dbrain,public,users"

    if database_url_with_schema in _engine_cache:
        return _engine_cache[database_url_with_schema]

    engine = create_engine(
        database_url_with_schema,
        echo=False,
        pool_size=3,
        max_overflow=1,
        pool_timeout=30,
        pool_pre_ping=True,
        pool_recycle=3600,
        pool_reset_on_return="commit",
        connect_args={
            "connect_timeout": 15,
            "application_name": "str_signal_brain",
        },
    )
    _engine_cache[database_url_with_schema] = engine
    return engine


def get_session():
    """Create a Brain database session."""
    engine = get_engine()
    if engine not in _sessionmaker_cache:
        _sessionmaker_cache[engine] = sessionmaker(bind=engine)
    return _sessionmaker_cache[engine]()


def init_brain_database():
    """Initialize the Brain schema and tables."""
    engine = get_engine()
    if os.getenv("DATABASE_URL"):
        with engine.begin() as conn:
            conn.execute(sqlalchemy.text("SELECT pg_advisory_xact_lock(779481502)"))
            conn.execute(sqlalchemy.text(f"CREATE SCHEMA IF NOT EXISTS {BRAIN_SCHEMA}"))
            Base.metadata.create_all(conn)
            _migrate_brain_tables(conn)
    else:
        Base.metadata.create_all(engine)
        _migrate_brain_tables(engine)
    return engine


def init_kpi_tables():
    """Create the additive KPI tables without re-initializing the full Brain schema."""
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(sqlalchemy.text(f"CREATE SCHEMA IF NOT EXISTS {BRAIN_SCHEMA}"))
        StayOutcomeClassification.__table__.create(bind=conn, checkfirst=True)
        _ensure_stay_outcome_once_index(conn)
    return engine


def init_listing_audit_tables():
    """Create the additive Workspace listing-audit tables."""
    engine = get_engine()
    with engine.begin() as conn:
        if os.getenv("DATABASE_URL"):
            conn.execute(sqlalchemy.text(f"CREATE SCHEMA IF NOT EXISTS {BRAIN_SCHEMA}"))
        ListingAuditRun.__table__.create(bind=conn, checkfirst=True)
        ListingAuditSnapshot.__table__.create(bind=conn, checkfirst=True)
    return engine


def _migrate_brain_tables(bind):
    """Apply additive Brain migrations not covered by create_all."""
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        return

    def migrate(conn):
        _ensure_stay_outcome_once_index(conn)
        result = conn.execute(
            sqlalchemy.text(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = :schema AND table_name = 'whatsapp_messages'
                """
            ),
            {"schema": BRAIN_SCHEMA},
        )
        columns = {row[0] for row in result.fetchall()}
        for column_name, column_type in (
            ("provider_status", "VARCHAR"),
            ("error_code", "VARCHAR"),
            ("error_message", "TEXT"),
        ):
            if column_name not in columns:
                conn.execute(sqlalchemy.text(f"ALTER TABLE {BRAIN_SCHEMA}.whatsapp_messages ADD COLUMN {column_name} {column_type}"))

    if hasattr(bind, "execute"):
        migrate(bind)
    else:
        with bind.begin() as conn:
            migrate(conn)


def _ensure_stay_outcome_once_index(conn):
    """Add database-level one-analysis enforcement without deleting legacy rows."""
    table_exists = conn.execute(
        sqlalchemy.text(
            """
            SELECT to_regclass(:table_name)
            """
        ),
        {"table_name": f"{BRAIN_SCHEMA}.stay_outcome_classifications"},
    ).scalar()
    if not table_exists:
        return
    duplicate = conn.execute(
        sqlalchemy.text(
            f"""
            SELECT reservation_id
            FROM {BRAIN_SCHEMA}.stay_outcome_classifications
            GROUP BY reservation_id
            HAVING COUNT(*) > 1
            LIMIT 1
            """
        )
    ).first()
    if duplicate:
        logger.warning(
            "Stay-outcome table contains duplicate reservation %s; one-time unique index was not added",
            duplicate[0],
        )
        return
    conn.execute(
        sqlalchemy.text(
            f"""
            CREATE UNIQUE INDEX IF NOT EXISTS uq_brain_stay_outcome_reservation_once
            ON {BRAIN_SCHEMA}.stay_outcome_classifications (reservation_id)
            """
        )
    )


def as_json_safe(value: Any):
    """Return a JSON-serializable value."""
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): as_json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [as_json_safe(v) for v in value]
    return str(value)


def stable_hash(payload: Any) -> str:
    """Create a stable hash for prompt inputs, evidence bundles, or dedupe keys."""
    encoded = json.dumps(as_json_safe(payload), sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
