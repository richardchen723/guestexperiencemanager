#!/usr/bin/env python3
"""
Boost database models - campaigns, sessions, rankings, and proxies.
Uses the 'boost' schema in PostgreSQL alongside the existing user/bookkeeping schemas.
"""

import os
from datetime import date, datetime
from typing import Any, Dict, List, Optional

import sqlalchemy
from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    DDL,
    event,
)
from sqlalchemy.orm import relationship
from sqlalchemy.types import JSON

from dashboard.auth.models import Base, get_engine, get_session as get_user_session
import dashboard.config as config


BOOST_SCHEMA = "boost"


class BoostCampaign(Base):
    """Campaign configuration for listing boost automation."""

    __tablename__ = "boost_campaigns"
    __table_args__ = (
        {"schema": BOOST_SCHEMA} if os.getenv("DATABASE_URL") else {},
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), nullable=False)
    platform = Column(String(50), nullable=False, default="airbnb")
    search_area = Column(String(500), nullable=False)
    date_window_start = Column(Date, nullable=False)
    date_window_end = Column(Date, nullable=False)
    min_nights = Column(Integer, nullable=False, default=2)
    max_nights = Column(Integer, nullable=False, default=5)
    target_listing_id = Column(Integer, nullable=True)
    target_listing_url = Column(Text, nullable=True)
    target_listing_name = Column(String(500), nullable=True)
    target_lat = Column(Float, nullable=True)
    target_lng = Column(Float, nullable=True)
    sessions_per_day = Column(Integer, nullable=False, default=3)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    sessions = relationship(
        "BoostSession",
        back_populates="campaign",
        cascade="all, delete-orphan",
        order_by="desc(BoostSession.started_at)",
    )
    rankings = relationship(
        "BoostRanking",
        back_populates="campaign",
        cascade="all, delete-orphan",
        order_by="desc(BoostRanking.date)",
    )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "platform": self.platform,
            "search_area": self.search_area,
            "date_window_start": self.date_window_start.isoformat() if self.date_window_start else None,
            "date_window_end": self.date_window_end.isoformat() if self.date_window_end else None,
            "min_nights": self.min_nights,
            "max_nights": self.max_nights,
            "target_listing_id": self.target_listing_id,
            "target_listing_url": self.target_listing_url,
            "target_listing_name": self.target_listing_name,
            "target_lat": self.target_lat,
            "target_lng": self.target_lng,
            "sessions_per_day": self.sessions_per_day,
            "is_active": self.is_active,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class BoostSession(Base):
    """Individual automation run record."""

    __tablename__ = "boost_sessions"
    _fk_schema = f"{BOOST_SCHEMA}." if os.getenv("DATABASE_URL") else ""
    __table_args__ = (
        {"schema": BOOST_SCHEMA} if os.getenv("DATABASE_URL") else {},
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    campaign_id = Column(Integer, ForeignKey(f"{_fk_schema}boost_campaigns.id"), nullable=False)
    proxy_used = Column(String(500), nullable=True)
    search_dates = Column(JSON, nullable=True)
    target_found = Column(Boolean, nullable=True)
    target_page_number = Column(Integer, nullable=True)
    target_position_on_page = Column(Integer, nullable=True)
    total_pages_browsed = Column(Integer, nullable=True, default=0)
    other_listings_browsed = Column(JSON, nullable=True)
    status = Column(String(50), nullable=False, default="pending")
    error_message = Column(Text, nullable=True)
    started_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    completed_at = Column(DateTime, nullable=True)
    session_log = Column(JSON, nullable=True)

    campaign = relationship("BoostCampaign", back_populates="sessions")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "campaign_id": self.campaign_id,
            "proxy_used": self.proxy_used,
            "search_dates": self.search_dates,
            "target_found": self.target_found,
            "target_page_number": self.target_page_number,
            "target_position_on_page": self.target_position_on_page,
            "total_pages_browsed": self.total_pages_browsed,
            "other_listings_browsed": self.other_listings_browsed,
            "status": self.status,
            "error_message": self.error_message,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "session_log": self.session_log,
        }


class BoostRanking(Base):
    """Daily aggregated ranking data."""

    __tablename__ = "boost_rankings"
    _fk_schema = f"{BOOST_SCHEMA}." if os.getenv("DATABASE_URL") else ""
    __table_args__ = (
        UniqueConstraint("campaign_id", "date", name="uq_boost_rankings_campaign_date"),
        {"schema": BOOST_SCHEMA} if os.getenv("DATABASE_URL") else {},
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    campaign_id = Column(Integer, ForeignKey(f"{_fk_schema}boost_campaigns.id"), nullable=False)
    date = Column(Date, nullable=False)
    avg_page_number = Column(Float, nullable=True)
    avg_position = Column(Float, nullable=True)
    best_position = Column(Integer, nullable=True)
    worst_position = Column(Integer, nullable=True)
    sessions_count = Column(Integer, nullable=False, default=0)
    found_count = Column(Integer, nullable=False, default=0)

    campaign = relationship("BoostCampaign", back_populates="rankings")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "campaign_id": self.campaign_id,
            "date": self.date.isoformat() if self.date else None,
            "avg_page_number": self.avg_page_number,
            "avg_position": self.avg_position,
            "best_position": self.best_position,
            "worst_position": self.worst_position,
            "sessions_count": self.sessions_count,
            "found_count": self.found_count,
        }


class BoostProxy(Base):
    """Proxy server for automation sessions."""

    __tablename__ = "boost_proxies"
    __table_args__ = (
        UniqueConstraint("host", "port", name="uq_boost_proxies_host_port"),
        {"schema": BOOST_SCHEMA} if os.getenv("DATABASE_URL") else {},
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    host = Column(String(255), nullable=False)
    port = Column(Integer, nullable=False)
    username = Column(String(255), nullable=True)
    password = Column(String(255), nullable=True)
    protocol = Column(String(20), nullable=False, default="http")
    is_active = Column(Boolean, nullable=False, default=True)
    last_used_at = Column(DateTime, nullable=True)
    fail_count = Column(Integer, nullable=False, default=0)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "host": self.host,
            "port": self.port,
            "username": self.username,
            "has_password": bool(self.password),
            "protocol": self.protocol,
            "is_active": self.is_active,
            "last_used_at": self.last_used_at.isoformat() if self.last_used_at else None,
            "fail_count": self.fail_count,
        }

    def to_playwright_proxy(self) -> Dict[str, Any]:
        """Format proxy for Playwright's browser launch options."""
        proxy = {
            "server": f"{self.protocol}://{self.host}:{self.port}",
        }
        if self.username:
            proxy["username"] = self.username
        if self.password:
            proxy["password"] = self.password
        return proxy


if os.getenv("DATABASE_URL"):
    event.listen(
        BoostCampaign.__table__,
        "before_create",
        DDL(f"CREATE SCHEMA IF NOT EXISTS {BOOST_SCHEMA}"),
    )


def init_boost_database():
    """Ensure boost schema and tables exist."""

    engine = get_engine(config.USERS_DATABASE_PATH)

    if os.getenv("DATABASE_URL"):
        with engine.begin() as conn:
            conn.execute(sqlalchemy.text(f"CREATE SCHEMA IF NOT EXISTS {BOOST_SCHEMA}"))

    Base.metadata.create_all(
        engine,
        tables=[
            BoostCampaign.__table__,
            BoostSession.__table__,
            BoostRanking.__table__,
            BoostProxy.__table__,
        ],
    )
    return engine


def get_session():
    """Reuse the shared PostgreSQL session factory."""
    return get_user_session()
