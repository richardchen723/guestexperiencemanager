#!/usr/bin/env python3
"""
Bookkeeping database models and helpers.
"""

import os
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional

import sqlalchemy
from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    DDL,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    event,
)
from sqlalchemy.orm import relationship
from sqlalchemy.types import JSON

from dashboard.auth.models import Base, get_engine, get_session as get_user_session
import dashboard.config as config


BOOKKEEPING_SCHEMA = "bookkeeping"

UPLOAD_STAGE_REVENUE = "revenue"
UPLOAD_STAGE_EXPENSE = "expense"
UPLOAD_STAGE_CORROBORATION = "corroboration"

REVENUE_SOURCES = [
    "airbnb",
    "booking_com",
    "vrbo",
    "hopper",
    "direct_bookings",
    "google",
]
DEFAULT_REVENUE_CHANNELS = list(REVENUE_SOURCES)
REVENUE_SOURCE_ALIASES = {
    "bdc": "booking_com",
    "booking": "booking_com",
    "booking.com": "booking_com",
    "direct": "direct_bookings",
    "direct_bookings": "direct_bookings",
}

SPECIAL_UPLOAD_SOURCES = [
    "direct_refund",
    "expense_evidence",
    "bank_statement",
    "credit_card_statement",
    "stripe_statement",
]

EXPENSE_CATEGORIES = [
    "cleaning",
    "maintenance",
    "supplies",
    "misc",
    "software_fee",
    "direct_refund",
]

EDITABLE_ROW_TYPES = [
    "listing_mapping",
    "revenue_item",
    "expense_item",
]

WORKSPACE_REVISION_STATUSES = [
    "approved",
    "exported",
]

CHANGE_PROPOSAL_STATUSES = [
    "pending",
    "accepted",
    "rejected",
]

PROCESSING_BATCH_STATUSES = [
    "queued",
    "processing",
    "completed",
    "completed_with_errors",
    "failed",
]


def _decimal_to_float(value: Optional[Decimal]) -> Optional[float]:
    if value is None:
        return None
    return float(value)


def normalize_revenue_channels(channels: Optional[Any]) -> List[str]:
    if channels is None or channels == "":
        return list(DEFAULT_REVENUE_CHANNELS)
    if isinstance(channels, str):
        raw_channels = [part.strip() for part in channels.split(",")]
    else:
        raw_channels = [str(part).strip() for part in (channels or [])]

    normalized_channels: List[str] = []
    for value in raw_channels:
        if not value:
            continue
        canonical = REVENUE_SOURCE_ALIASES.get(value.strip().lower(), value.strip().lower())
        if canonical not in REVENUE_SOURCES:
            continue
        if canonical not in normalized_channels:
            normalized_channels.append(canonical)

    ordered_channels = [source for source in REVENUE_SOURCES if source in normalized_channels]
    return ordered_channels


class BookkeepingPortfolio(Base):
    """Portfolio configuration for bookkeeping workspaces."""

    __tablename__ = "bookkeeping_portfolios"
    __table_args__ = (
        UniqueConstraint("code", name="uq_bookkeeping_portfolios_code"),
        {"schema": BOOKKEEPING_SCHEMA} if os.getenv("DATABASE_URL") else {},
    )

    bookkeeping_portfolio_id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), nullable=False)
    code = Column(String(100), nullable=False, index=True)
    listing_tag = Column(String(120), nullable=True, index=True)
    property_name = Column(String(255), nullable=True)
    property_address = Column(Text, nullable=True)
    notes = Column(Text, nullable=True)
    default_currency = Column(String(10), nullable=False, default="USD")
    owner_share_percentage = Column(Numeric(5, 2), nullable=False, default=Decimal("100.00"))
    management_fee_percentage = Column(Numeric(5, 2), nullable=False, default=Decimal("20.00"))
    listing_count = Column(Integer, nullable=False, default=0)
    revenue_channels = Column(JSON, nullable=True)
    hostaway_price_per_listing = Column(Numeric(10, 2), nullable=True)
    pricelabs_price_per_listing = Column(Numeric(10, 2), nullable=True)

    _users_fk_schema = "users." if os.getenv("DATABASE_URL") else ""
    created_by = Column(Integer, ForeignKey(f"{_users_fk_schema}users.user_id"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    creator = relationship("User", foreign_keys=[created_by])
    periods = relationship(
        "BookkeepingPeriod",
        back_populates="portfolio",
        cascade="all, delete-orphan",
        order_by="desc(BookkeepingPeriod.period_start)",
    )
    listing_mappings = relationship(
        "BookkeepingListingMapping",
        back_populates="portfolio",
        cascade="all, delete-orphan",
        order_by="BookkeepingListingMapping.official_name",
    )
    correction_rules = relationship(
        "BookkeepingCorrectionRule",
        back_populates="portfolio",
        cascade="all, delete-orphan",
        order_by="desc(BookkeepingCorrectionRule.updated_at)",
    )

    def __repr__(self):
        return f"<BookkeepingPortfolio(id={self.bookkeeping_portfolio_id}, code='{self.code}')>"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "bookkeeping_portfolio_id": self.bookkeeping_portfolio_id,
            "name": self.name,
            "code": self.code,
            "listing_tag": self.listing_tag,
            "portfolio_tag": self.listing_tag,
            "property_name": self.property_name,
            "property_address": self.property_address,
            "notes": self.notes,
            "default_currency": self.default_currency,
            "owner_share_percentage": _decimal_to_float(self.owner_share_percentage),
            "management_fee_percentage": _decimal_to_float(self.management_fee_percentage),
            "listing_count": self.listing_count,
            "revenue_channels": normalize_revenue_channels(self.revenue_channels),
            "hostaway_price_per_listing": _decimal_to_float(self.hostaway_price_per_listing),
            "pricelabs_price_per_listing": _decimal_to_float(self.pricelabs_price_per_listing),
            "created_by": self.created_by,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class BookkeepingListingMapping(Base):
    """Portfolio-specific mapping from Cotton Candy listings to workbook labels."""

    __tablename__ = "bookkeeping_listing_mappings"
    __table_args__ = (
        UniqueConstraint("portfolio_id", "listing_id", name="uq_bookkeeping_listing_mappings_portfolio_listing"),
        {"schema": BOOKKEEPING_SCHEMA} if os.getenv("DATABASE_URL") else {},
    )

    bookkeeping_listing_mapping_id = Column(Integer, primary_key=True, autoincrement=True)
    _bookkeeping_fk_schema = f"{BOOKKEEPING_SCHEMA}." if os.getenv("DATABASE_URL") else ""
    _users_fk_schema = "users." if os.getenv("DATABASE_URL") else ""
    portfolio_id = Column(
        Integer,
        ForeignKey(f"{_bookkeeping_fk_schema}bookkeeping_portfolios.bookkeeping_portfolio_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    listing_id = Column(Integer, nullable=False, index=True)
    listing_name = Column(String(255), nullable=True)
    internal_listing_name = Column(String(255), nullable=True)
    official_name = Column(String(255), nullable=False)
    aliases = Column(JSON, nullable=True)
    is_active = Column(Boolean, nullable=False, default=True, index=True)
    notes = Column(Text, nullable=True)
    created_by = Column(Integer, ForeignKey(f"{_users_fk_schema}users.user_id"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    portfolio = relationship("BookkeepingPortfolio", back_populates="listing_mappings")
    creator = relationship("User", foreign_keys=[created_by])
    revenue_items = relationship("BookkeepingRevenueItem", back_populates="listing_mapping")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "bookkeeping_listing_mapping_id": self.bookkeeping_listing_mapping_id,
            "portfolio_id": self.portfolio_id,
            "listing_id": self.listing_id,
            "listing_name": self.listing_name,
            "internal_listing_name": self.internal_listing_name,
            "official_name": self.official_name,
            "aliases": list(self.aliases or []),
            "is_active": self.is_active,
            "notes": self.notes,
            "created_by": self.created_by,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class BookkeepingPeriod(Base):
    """Month-scoped bookkeeping workspace inside a portfolio."""

    __tablename__ = "bookkeeping_periods"
    __table_args__ = (
        UniqueConstraint(
            "portfolio_id",
            "period_start",
            name="uq_bookkeeping_periods_portfolio_period_start",
        ),
        {"schema": BOOKKEEPING_SCHEMA} if os.getenv("DATABASE_URL") else {},
    )

    bookkeeping_period_id = Column(Integer, primary_key=True, autoincrement=True)
    _bookkeeping_fk_schema = f"{BOOKKEEPING_SCHEMA}." if os.getenv("DATABASE_URL") else ""
    _users_fk_schema = "users." if os.getenv("DATABASE_URL") else ""
    portfolio_id = Column(
        Integer,
        ForeignKey(f"{_bookkeeping_fk_schema}bookkeeping_portfolios.bookkeeping_portfolio_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name = Column(String(255), nullable=False)
    period_start = Column(Date, nullable=False, index=True)
    period_end = Column(Date, nullable=False)
    status = Column(String(50), nullable=False, default="draft", index=True)
    notes = Column(Text, nullable=True)
    created_by = Column(Integer, ForeignKey(f"{_users_fk_schema}users.user_id"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    portfolio = relationship("BookkeepingPortfolio", back_populates="periods")
    creator = relationship("User", foreign_keys=[created_by])
    uploads = relationship(
        "BookkeepingUpload",
        back_populates="period",
        cascade="all, delete-orphan",
        order_by="desc(BookkeepingUpload.created_at)",
    )
    expense_items = relationship(
        "BookkeepingExpenseItem",
        back_populates="period",
        cascade="all, delete-orphan",
        order_by="BookkeepingExpenseItem.service_date",
    )
    revenue_items = relationship(
        "BookkeepingRevenueItem",
        back_populates="period",
        cascade="all, delete-orphan",
        order_by="BookkeepingRevenueItem.source, BookkeepingRevenueItem.row_index",
    )
    conversation_messages = relationship(
        "BookkeepingConversationMessage",
        back_populates="period",
        cascade="all, delete-orphan",
        order_by="BookkeepingConversationMessage.created_at",
    )
    manual_edits = relationship(
        "BookkeepingManualEdit",
        back_populates="period",
        cascade="all, delete-orphan",
        order_by="desc(BookkeepingManualEdit.created_at)",
    )
    change_proposals = relationship(
        "BookkeepingAIChangeProposal",
        back_populates="period",
        cascade="all, delete-orphan",
        order_by="desc(BookkeepingAIChangeProposal.created_at)",
    )
    processing_batches = relationship(
        "BookkeepingProcessingBatch",
        back_populates="period",
        cascade="all, delete-orphan",
        order_by="desc(BookkeepingProcessingBatch.created_at)",
    )
    revisions = relationship(
        "BookkeepingWorkspaceRevision",
        back_populates="period",
        cascade="all, delete-orphan",
        order_by="desc(BookkeepingWorkspaceRevision.created_at)",
    )

    def __repr__(self):
        return f"<BookkeepingPeriod(id={self.bookkeeping_period_id}, name='{self.name}')>"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "bookkeeping_period_id": self.bookkeeping_period_id,
            "portfolio_id": self.portfolio_id,
            "name": self.name,
            "period_start": self.period_start.isoformat() if self.period_start else None,
            "period_end": self.period_end.isoformat() if self.period_end else None,
            "status": self.status,
            "notes": self.notes,
            "created_by": self.created_by,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class BookkeepingUpload(Base):
    """Uploaded revenue or expense evidence attached to a bookkeeping period."""

    __tablename__ = "bookkeeping_uploads"
    __table_args__ = (
        {"schema": BOOKKEEPING_SCHEMA} if os.getenv("DATABASE_URL") else {},
    )

    bookkeeping_upload_id = Column(Integer, primary_key=True, autoincrement=True)
    _bookkeeping_fk_schema = f"{BOOKKEEPING_SCHEMA}." if os.getenv("DATABASE_URL") else ""
    _users_fk_schema = "users." if os.getenv("DATABASE_URL") else ""
    period_id = Column(
        Integer,
        ForeignKey(f"{_bookkeeping_fk_schema}bookkeeping_periods.bookkeeping_period_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    processing_batch_id = Column(
        Integer,
        ForeignKey(f"{_bookkeeping_fk_schema}bookkeeping_processing_batches.bookkeeping_processing_batch_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    stage = Column(String(50), nullable=False, index=True)
    source = Column(String(100), nullable=False, index=True)
    detected_source = Column(String(100), nullable=True)
    sheet_name = Column(String(255), nullable=True)
    upload_status = Column(String(50), nullable=False, default="processed", index=True)
    original_filename = Column(String(500), nullable=False)
    original_relative_path = Column(String(1000), nullable=True)
    stored_path = Column(String(1000), nullable=False)
    content_type = Column(String(255), nullable=True)
    file_extension = Column(String(50), nullable=True)
    file_size = Column(Integer, nullable=False, default=0)
    row_count = Column(Integer, nullable=True)
    headers = Column(JSON, nullable=True)
    preview_rows = Column(JSON, nullable=True)
    parsed_rows = Column(JSON, nullable=True)
    summary = Column(JSON, nullable=True)
    processing_error = Column(Text, nullable=True)
    processing_started_at = Column(DateTime, nullable=True)
    processing_completed_at = Column(DateTime, nullable=True)
    notes = Column(Text, nullable=True)
    uploaded_by = Column(Integer, ForeignKey(f"{_users_fk_schema}users.user_id"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)

    period = relationship("BookkeepingPeriod", back_populates="uploads")
    processing_batch = relationship(
        "BookkeepingProcessingBatch",
        back_populates="uploads",
        foreign_keys=[processing_batch_id],
    )
    uploader = relationship("User", foreign_keys=[uploaded_by])
    expense_items = relationship("BookkeepingExpenseItem", back_populates="upload")
    revenue_items = relationship("BookkeepingRevenueItem", back_populates="upload")

    def __repr__(self):
        return f"<BookkeepingUpload(id={self.bookkeeping_upload_id}, source='{self.source}', stage='{self.stage}')>"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "bookkeeping_upload_id": self.bookkeeping_upload_id,
            "period_id": self.period_id,
            "processing_batch_id": self.processing_batch_id,
            "stage": self.stage,
            "source": self.source,
            "detected_source": self.detected_source,
            "sheet_name": self.sheet_name,
            "upload_status": self.upload_status,
            "original_filename": self.original_filename,
            "original_relative_path": self.original_relative_path,
            "content_type": self.content_type,
            "file_extension": self.file_extension,
            "file_size": self.file_size,
            "row_count": self.row_count,
            "headers": self.headers or [],
            "preview_rows": self.preview_rows or [],
            "summary": self.summary or {},
            "processing_error": self.processing_error,
            "processing_started_at": self.processing_started_at.isoformat() if self.processing_started_at else None,
            "processing_completed_at": self.processing_completed_at.isoformat() if self.processing_completed_at else None,
            "notes": self.notes,
            "uploaded_by": self.uploaded_by,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class BookkeepingProcessingBatch(Base):
    """Background processing tracker for upload batches."""

    __tablename__ = "bookkeeping_processing_batches"
    __table_args__ = (
        {"schema": BOOKKEEPING_SCHEMA} if os.getenv("DATABASE_URL") else {},
    )

    bookkeeping_processing_batch_id = Column(Integer, primary_key=True, autoincrement=True)
    _bookkeeping_fk_schema = f"{BOOKKEEPING_SCHEMA}." if os.getenv("DATABASE_URL") else ""
    _users_fk_schema = "users." if os.getenv("DATABASE_URL") else ""
    period_id = Column(
        Integer,
        ForeignKey(f"{_bookkeeping_fk_schema}bookkeeping_periods.bookkeeping_period_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    stage = Column(String(50), nullable=False, index=True)
    status = Column(String(50), nullable=False, default="queued", index=True)
    total_uploads = Column(Integer, nullable=False, default=0)
    processed_uploads = Column(Integer, nullable=False, default=0)
    successful_uploads = Column(Integer, nullable=False, default=0)
    failed_uploads = Column(Integer, nullable=False, default=0)
    current_upload_id = Column(
        Integer,
        ForeignKey(f"{_bookkeeping_fk_schema}bookkeeping_uploads.bookkeeping_upload_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    current_filename = Column(String(500), nullable=True)
    error_message = Column(Text, nullable=True)
    created_by = Column(Integer, ForeignKey(f"{_users_fk_schema}users.user_id"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    period = relationship("BookkeepingPeriod", back_populates="processing_batches")
    creator = relationship("User", foreign_keys=[created_by])
    current_upload = relationship("BookkeepingUpload", foreign_keys=[current_upload_id], post_update=True)
    uploads = relationship(
        "BookkeepingUpload",
        back_populates="processing_batch",
        foreign_keys="BookkeepingUpload.processing_batch_id",
    )

    def to_dict(self) -> Dict[str, Any]:
        total_uploads = int(self.total_uploads or 0)
        processed_uploads = int(self.processed_uploads or 0)
        failed_uploads = int(self.failed_uploads or 0)
        return {
            "bookkeeping_processing_batch_id": self.bookkeeping_processing_batch_id,
            "period_id": self.period_id,
            "stage": self.stage,
            "status": self.status,
            "total_uploads": total_uploads,
            "processed_uploads": processed_uploads,
            "successful_uploads": int(self.successful_uploads or 0),
            "failed_uploads": failed_uploads,
            "remaining_uploads": max(0, total_uploads - processed_uploads),
            "current_upload_id": self.current_upload_id,
            "current_filename": self.current_filename,
            "error_message": self.error_message,
            "progress_percent": 100.0 if total_uploads <= 0 else round((processed_uploads / total_uploads) * 100.0, 1),
            "created_by": self.created_by,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class BookkeepingRevenueItem(Base):
    """Normalized revenue rows that power the live workbook and exports."""

    __tablename__ = "bookkeeping_revenue_items"
    __table_args__ = (
        {"schema": BOOKKEEPING_SCHEMA} if os.getenv("DATABASE_URL") else {},
    )

    bookkeeping_revenue_item_id = Column(Integer, primary_key=True, autoincrement=True)
    _bookkeeping_fk_schema = f"{BOOKKEEPING_SCHEMA}." if os.getenv("DATABASE_URL") else ""
    _users_fk_schema = "users." if os.getenv("DATABASE_URL") else ""
    period_id = Column(
        Integer,
        ForeignKey(f"{_bookkeeping_fk_schema}bookkeeping_periods.bookkeeping_period_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    upload_id = Column(
        Integer,
        ForeignKey(f"{_bookkeeping_fk_schema}bookkeeping_uploads.bookkeeping_upload_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    listing_mapping_id = Column(
        Integer,
        ForeignKey(f"{_bookkeeping_fk_schema}bookkeeping_listing_mappings.bookkeeping_listing_mapping_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    source = Column(String(100), nullable=False, index=True)
    row_index = Column(Integer, nullable=False, default=0)
    reservation_identifier = Column(String(255), nullable=True, index=True)
    confirmation_code = Column(String(255), nullable=True, index=True)
    guest_name = Column(String(255), nullable=True)
    property_code = Column(String(255), nullable=True, index=True)
    raw_listing_name = Column(String(255), nullable=True)
    transaction_type = Column(String(255), nullable=True)
    currency = Column(String(25), nullable=True)
    transaction_date = Column(Date, nullable=True, index=True)
    booking_date = Column(Date, nullable=True)
    start_date = Column(Date, nullable=True, index=True)
    end_date = Column(Date, nullable=True, index=True)
    nights = Column(Integer, nullable=True)
    gross_amount = Column(Numeric(12, 2), nullable=True)
    paid_out_amount = Column(Numeric(12, 2), nullable=True)
    commission_amount = Column(Numeric(12, 2), nullable=True)
    hostaway_fee_amount = Column(Numeric(12, 2), nullable=True)
    stripe_fee_amount = Column(Numeric(12, 2), nullable=True)
    cleaning_fee_amount = Column(Numeric(12, 2), nullable=True)
    tax_amount = Column(Numeric(12, 2), nullable=True)
    refund_amount = Column(Numeric(12, 2), nullable=True)
    details = Column(Text, nullable=True)
    normalized_data = Column(JSON, nullable=True)
    raw_data = Column(JSON, nullable=True)
    needs_review = Column(Boolean, nullable=False, default=False, index=True)
    review_reason = Column(Text, nullable=True)
    created_by = Column(Integer, ForeignKey(f"{_users_fk_schema}users.user_id"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    period = relationship("BookkeepingPeriod", back_populates="revenue_items")
    upload = relationship("BookkeepingUpload", back_populates="revenue_items")
    listing_mapping = relationship("BookkeepingListingMapping", back_populates="revenue_items")
    creator = relationship("User", foreign_keys=[created_by])

    def to_dict(self) -> Dict[str, Any]:
        return {
            "bookkeeping_revenue_item_id": self.bookkeeping_revenue_item_id,
            "period_id": self.period_id,
            "upload_id": self.upload_id,
            "listing_mapping_id": self.listing_mapping_id,
            "source": self.source,
            "row_index": self.row_index,
            "reservation_identifier": self.reservation_identifier,
            "confirmation_code": self.confirmation_code,
            "guest_name": self.guest_name,
            "property_code": self.property_code,
            "raw_listing_name": self.raw_listing_name,
            "transaction_type": self.transaction_type,
            "currency": self.currency,
            "transaction_date": self.transaction_date.isoformat() if self.transaction_date else None,
            "booking_date": self.booking_date.isoformat() if self.booking_date else None,
            "start_date": self.start_date.isoformat() if self.start_date else None,
            "end_date": self.end_date.isoformat() if self.end_date else None,
            "nights": self.nights,
            "gross_amount": _decimal_to_float(self.gross_amount),
            "paid_out_amount": _decimal_to_float(self.paid_out_amount),
            "commission_amount": _decimal_to_float(self.commission_amount),
            "hostaway_fee_amount": _decimal_to_float(self.hostaway_fee_amount),
            "stripe_fee_amount": _decimal_to_float(self.stripe_fee_amount),
            "cleaning_fee_amount": _decimal_to_float(self.cleaning_fee_amount),
            "tax_amount": _decimal_to_float(self.tax_amount),
            "refund_amount": _decimal_to_float(self.refund_amount),
            "details": self.details,
            "normalized_data": self.normalized_data or {},
            "raw_data": self.raw_data or {},
            "needs_review": self.needs_review,
            "review_reason": self.review_reason,
            "created_by": self.created_by,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class BookkeepingExpenseItem(Base):
    """Structured expense rows that will feed the workbook output."""

    __tablename__ = "bookkeeping_expense_items"
    __table_args__ = (
        {"schema": BOOKKEEPING_SCHEMA} if os.getenv("DATABASE_URL") else {},
    )

    bookkeeping_expense_item_id = Column(Integer, primary_key=True, autoincrement=True)
    _bookkeeping_fk_schema = f"{BOOKKEEPING_SCHEMA}." if os.getenv("DATABASE_URL") else ""
    _users_fk_schema = "users." if os.getenv("DATABASE_URL") else ""
    period_id = Column(
        Integer,
        ForeignKey(f"{_bookkeeping_fk_schema}bookkeeping_periods.bookkeeping_period_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    upload_id = Column(
        Integer,
        ForeignKey(f"{_bookkeeping_fk_schema}bookkeeping_uploads.bookkeeping_upload_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    category = Column(String(50), nullable=False, index=True)
    item_name = Column(String(255), nullable=True)
    vendor = Column(String(255), nullable=True)
    property_code = Column(String(255), nullable=True, index=True)
    scope = Column(String(50), nullable=False, default="property")
    description = Column(Text, nullable=True)
    amount = Column(Numeric(12, 2), nullable=True)
    service_date = Column(Date, nullable=True, index=True)
    payment_date = Column(Date, nullable=True)
    payment_method = Column(String(100), nullable=True)
    account_holder = Column(String(255), nullable=True)
    account_number = Column(String(255), nullable=True)
    purchase_type = Column(String(100), nullable=True)
    store_name = Column(String(255), nullable=True)
    quantity = Column(Numeric(12, 2), nullable=True)
    unit_amount = Column(Numeric(12, 2), nullable=True)
    subtotal = Column(Numeric(12, 2), nullable=True)
    discount = Column(Numeric(12, 2), nullable=True)
    shipping = Column(Numeric(12, 2), nullable=True)
    tax = Column(Numeric(12, 2), nullable=True)
    total = Column(Numeric(12, 2), nullable=True)
    reimbursement_method = Column(String(100), nullable=True)
    reimbursement_date = Column(Date, nullable=True)
    details = Column(Text, nullable=True)
    needs_review = Column(Boolean, nullable=False, default=False, index=True)
    review_reason = Column(Text, nullable=True)
    extraction_data = Column(JSON, nullable=True)
    created_by = Column(Integer, ForeignKey(f"{_users_fk_schema}users.user_id"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    period = relationship("BookkeepingPeriod", back_populates="expense_items")
    upload = relationship("BookkeepingUpload", back_populates="expense_items")
    creator = relationship("User", foreign_keys=[created_by])

    def __repr__(self):
        return f"<BookkeepingExpenseItem(id={self.bookkeeping_expense_item_id}, category='{self.category}')>"

    def effective_total(self) -> float:
        for candidate in (self.total, self.amount, self.subtotal):
            if candidate is not None:
                return float(candidate)
        return 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "bookkeeping_expense_item_id": self.bookkeeping_expense_item_id,
            "period_id": self.period_id,
            "upload_id": self.upload_id,
            "category": self.category,
            "item_name": self.item_name,
            "vendor": self.vendor,
            "property_code": self.property_code,
            "scope": self.scope,
            "description": self.description,
            "amount": _decimal_to_float(self.amount),
            "service_date": self.service_date.isoformat() if self.service_date else None,
            "payment_date": self.payment_date.isoformat() if self.payment_date else None,
            "payment_method": self.payment_method,
            "account_holder": self.account_holder,
            "account_number": self.account_number,
            "purchase_type": self.purchase_type,
            "store_name": self.store_name,
            "quantity": _decimal_to_float(self.quantity),
            "unit_amount": _decimal_to_float(self.unit_amount),
            "subtotal": _decimal_to_float(self.subtotal),
            "discount": _decimal_to_float(self.discount),
            "shipping": _decimal_to_float(self.shipping),
            "tax": _decimal_to_float(self.tax),
            "total": _decimal_to_float(self.total),
            "effective_total": self.effective_total(),
            "reimbursement_method": self.reimbursement_method,
            "reimbursement_date": self.reimbursement_date.isoformat() if self.reimbursement_date else None,
            "details": self.details,
            "needs_review": self.needs_review,
            "review_reason": self.review_reason,
            "extraction_data": self.extraction_data or {},
            "created_by": self.created_by,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class BookkeepingConversationMessage(Base):
    """Conversation log for the bookkeeping copilot."""

    __tablename__ = "bookkeeping_conversation_messages"
    __table_args__ = (
        {"schema": BOOKKEEPING_SCHEMA} if os.getenv("DATABASE_URL") else {},
    )

    bookkeeping_conversation_message_id = Column(Integer, primary_key=True, autoincrement=True)
    _bookkeeping_fk_schema = f"{BOOKKEEPING_SCHEMA}." if os.getenv("DATABASE_URL") else ""
    _users_fk_schema = "users." if os.getenv("DATABASE_URL") else ""
    period_id = Column(
        Integer,
        ForeignKey(f"{_bookkeeping_fk_schema}bookkeeping_periods.bookkeeping_period_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role = Column(String(50), nullable=False, index=True)
    message_text = Column(Text, nullable=False)
    message_metadata = Column(JSON, nullable=True)
    created_by = Column(Integer, ForeignKey(f"{_users_fk_schema}users.user_id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)

    period = relationship("BookkeepingPeriod", back_populates="conversation_messages")
    creator = relationship("User", foreign_keys=[created_by])

    def to_dict(self) -> Dict[str, Any]:
        return {
            "bookkeeping_conversation_message_id": self.bookkeeping_conversation_message_id,
            "period_id": self.period_id,
            "role": self.role,
            "message_text": self.message_text,
            "message_metadata": self.message_metadata or {},
            "created_by": self.created_by,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class BookkeepingManualEdit(Base):
    """Audit trail for operator changes to live bookkeeping rows."""

    __tablename__ = "bookkeeping_manual_edits"
    __table_args__ = (
        {"schema": BOOKKEEPING_SCHEMA} if os.getenv("DATABASE_URL") else {},
    )

    bookkeeping_manual_edit_id = Column(Integer, primary_key=True, autoincrement=True)
    _bookkeeping_fk_schema = f"{BOOKKEEPING_SCHEMA}." if os.getenv("DATABASE_URL") else ""
    _users_fk_schema = "users." if os.getenv("DATABASE_URL") else ""
    period_id = Column(
        Integer,
        ForeignKey(f"{_bookkeeping_fk_schema}bookkeeping_periods.bookkeeping_period_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    row_type = Column(String(50), nullable=False, index=True)
    row_id = Column(Integer, nullable=False, index=True)
    changed_fields = Column(JSON, nullable=True)
    before_data = Column(JSON, nullable=True)
    after_data = Column(JSON, nullable=True)
    edit_note = Column(Text, nullable=False)
    created_by = Column(Integer, ForeignKey(f"{_users_fk_schema}users.user_id"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)

    period = relationship("BookkeepingPeriod", back_populates="manual_edits")
    creator = relationship("User", foreign_keys=[created_by])

    def to_dict(self) -> Dict[str, Any]:
        return {
            "bookkeeping_manual_edit_id": self.bookkeeping_manual_edit_id,
            "period_id": self.period_id,
            "row_type": self.row_type,
            "row_id": self.row_id,
            "changed_fields": list(self.changed_fields or []),
            "before_data": self.before_data or {},
            "after_data": self.after_data or {},
            "edit_note": self.edit_note,
            "created_by": self.created_by,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class BookkeepingCorrectionRule(Base):
    """Portfolio-scoped reusable correction memory from approved human edits."""

    __tablename__ = "bookkeeping_correction_rules"
    __table_args__ = (
        {"schema": BOOKKEEPING_SCHEMA} if os.getenv("DATABASE_URL") else {},
    )

    bookkeeping_correction_rule_id = Column(Integer, primary_key=True, autoincrement=True)
    _bookkeeping_fk_schema = f"{BOOKKEEPING_SCHEMA}." if os.getenv("DATABASE_URL") else ""
    _users_fk_schema = "users." if os.getenv("DATABASE_URL") else ""
    portfolio_id = Column(
        Integer,
        ForeignKey(f"{_bookkeeping_fk_schema}bookkeeping_portfolios.bookkeeping_portfolio_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    row_type = Column(String(50), nullable=False, index=True)
    match_context = Column(JSON, nullable=True)
    target_fields = Column(JSON, nullable=True)
    note = Column(Text, nullable=True)
    source_manual_edit_id = Column(Integer, nullable=True, index=True)
    created_by = Column(Integer, ForeignKey(f"{_users_fk_schema}users.user_id"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    portfolio = relationship("BookkeepingPortfolio", back_populates="correction_rules")
    creator = relationship("User", foreign_keys=[created_by])

    def to_dict(self) -> Dict[str, Any]:
        return {
            "bookkeeping_correction_rule_id": self.bookkeeping_correction_rule_id,
            "portfolio_id": self.portfolio_id,
            "row_type": self.row_type,
            "match_context": self.match_context or {},
            "target_fields": self.target_fields or {},
            "note": self.note,
            "source_manual_edit_id": self.source_manual_edit_id,
            "created_by": self.created_by,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class BookkeepingAIChangeProposal(Base):
    """AI-proposed updates that need a human decision before changing edited rows."""

    __tablename__ = "bookkeeping_ai_change_proposals"
    __table_args__ = (
        {"schema": BOOKKEEPING_SCHEMA} if os.getenv("DATABASE_URL") else {},
    )

    bookkeeping_ai_change_proposal_id = Column(Integer, primary_key=True, autoincrement=True)
    _bookkeeping_fk_schema = f"{BOOKKEEPING_SCHEMA}." if os.getenv("DATABASE_URL") else ""
    _users_fk_schema = "users." if os.getenv("DATABASE_URL") else ""
    period_id = Column(
        Integer,
        ForeignKey(f"{_bookkeeping_fk_schema}bookkeeping_periods.bookkeeping_period_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    row_type = Column(String(50), nullable=False, index=True)
    row_id = Column(Integer, nullable=False, index=True)
    source_upload_id = Column(
        Integer,
        ForeignKey(f"{_bookkeeping_fk_schema}bookkeeping_uploads.bookkeeping_upload_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    current_values = Column(JSON, nullable=True)
    proposed_values = Column(JSON, nullable=True)
    reason = Column(Text, nullable=True)
    status = Column(String(50), nullable=False, default="pending", index=True)
    resolved_by = Column(Integer, ForeignKey(f"{_users_fk_schema}users.user_id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    resolved_at = Column(DateTime, nullable=True)

    period = relationship("BookkeepingPeriod", back_populates="change_proposals")
    source_upload = relationship("BookkeepingUpload", foreign_keys=[source_upload_id])
    resolver = relationship("User", foreign_keys=[resolved_by])

    def to_dict(self) -> Dict[str, Any]:
        return {
            "bookkeeping_ai_change_proposal_id": self.bookkeeping_ai_change_proposal_id,
            "period_id": self.period_id,
            "row_type": self.row_type,
            "row_id": self.row_id,
            "source_upload_id": self.source_upload_id,
            "current_values": self.current_values or {},
            "proposed_values": self.proposed_values or {},
            "reason": self.reason,
            "status": self.status,
            "resolved_by": self.resolved_by,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "resolved_at": self.resolved_at.isoformat() if self.resolved_at else None,
        }


class BookkeepingWorkspaceRevision(Base):
    """Approved/exported snapshots of the live bookkeeping workspace."""

    __tablename__ = "bookkeeping_workspace_revisions"
    __table_args__ = (
        {"schema": BOOKKEEPING_SCHEMA} if os.getenv("DATABASE_URL") else {},
    )

    bookkeeping_workspace_revision_id = Column(Integer, primary_key=True, autoincrement=True)
    _bookkeeping_fk_schema = f"{BOOKKEEPING_SCHEMA}." if os.getenv("DATABASE_URL") else ""
    _users_fk_schema = "users." if os.getenv("DATABASE_URL") else ""
    period_id = Column(
        Integer,
        ForeignKey(f"{_bookkeeping_fk_schema}bookkeeping_periods.bookkeeping_period_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    status = Column(String(50), nullable=False, default="approved", index=True)
    summary_snapshot = Column(JSON, nullable=True)
    workbook_filename = Column(String(500), nullable=True)
    created_by = Column(Integer, ForeignKey(f"{_users_fk_schema}users.user_id"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)

    period = relationship("BookkeepingPeriod", back_populates="revisions")
    creator = relationship("User", foreign_keys=[created_by])

    def to_dict(self) -> Dict[str, Any]:
        return {
            "bookkeeping_workspace_revision_id": self.bookkeeping_workspace_revision_id,
            "period_id": self.period_id,
            "status": self.status,
            "summary_snapshot": self.summary_snapshot or {},
            "workbook_filename": self.workbook_filename,
            "created_by": self.created_by,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


if os.getenv("DATABASE_URL"):
    event.listen(
        BookkeepingPortfolio.__table__,
        "before_create",
        DDL(f"CREATE SCHEMA IF NOT EXISTS {BOOKKEEPING_SCHEMA}"),
    )


def init_bookkeeping_database():
    """Ensure bookkeeping schema and tables exist."""

    engine = get_engine(config.USERS_DATABASE_PATH)

    if os.getenv("DATABASE_URL"):
        with engine.begin() as conn:
            conn.execute(sqlalchemy.text(f"CREATE SCHEMA IF NOT EXISTS {BOOKKEEPING_SCHEMA}"))

    Base.metadata.create_all(
        engine,
        tables=[
            BookkeepingPortfolio.__table__,
            BookkeepingListingMapping.__table__,
            BookkeepingPeriod.__table__,
            BookkeepingProcessingBatch.__table__,
            BookkeepingUpload.__table__,
            BookkeepingRevenueItem.__table__,
            BookkeepingExpenseItem.__table__,
            BookkeepingConversationMessage.__table__,
            BookkeepingManualEdit.__table__,
            BookkeepingCorrectionRule.__table__,
            BookkeepingAIChangeProposal.__table__,
            BookkeepingWorkspaceRevision.__table__,
        ],
    )

    inspector = sqlalchemy.inspect(engine)
    schema = BOOKKEEPING_SCHEMA if os.getenv("DATABASE_URL") else None
    existing_columns = {
        column["name"]
        for column in inspector.get_columns(BookkeepingPortfolio.__tablename__, schema=schema)
    }
    if "listing_tag" not in existing_columns:
        table_name = f"{BOOKKEEPING_SCHEMA}.bookkeeping_portfolios" if os.getenv("DATABASE_URL") else "bookkeeping_portfolios"
        with engine.begin() as conn:
            conn.execute(sqlalchemy.text(f"ALTER TABLE {table_name} ADD COLUMN listing_tag VARCHAR(120)"))
    if "revenue_channels" not in existing_columns:
        table_name = f"{BOOKKEEPING_SCHEMA}.bookkeeping_portfolios" if os.getenv("DATABASE_URL") else "bookkeeping_portfolios"
        with engine.begin() as conn:
            conn.execute(sqlalchemy.text(f"ALTER TABLE {table_name} ADD COLUMN revenue_channels JSON"))
    upload_columns = {
        column["name"]
        for column in inspector.get_columns(BookkeepingUpload.__tablename__, schema=schema)
    }
    upload_table_name = f"{BOOKKEEPING_SCHEMA}.bookkeeping_uploads" if os.getenv("DATABASE_URL") else "bookkeeping_uploads"
    if "processing_batch_id" not in upload_columns:
        with engine.begin() as conn:
            conn.execute(sqlalchemy.text(f"ALTER TABLE {upload_table_name} ADD COLUMN processing_batch_id INTEGER"))
    if "processing_error" not in upload_columns:
        with engine.begin() as conn:
            conn.execute(sqlalchemy.text(f"ALTER TABLE {upload_table_name} ADD COLUMN processing_error TEXT"))
    if "processing_started_at" not in upload_columns:
        with engine.begin() as conn:
            conn.execute(sqlalchemy.text(f"ALTER TABLE {upload_table_name} ADD COLUMN processing_started_at TIMESTAMP"))
    if "processing_completed_at" not in upload_columns:
        with engine.begin() as conn:
            conn.execute(sqlalchemy.text(f"ALTER TABLE {upload_table_name} ADD COLUMN processing_completed_at TIMESTAMP"))
    return engine


def get_session():
    """Reuse the shared PostgreSQL session factory."""

    return get_user_session()
