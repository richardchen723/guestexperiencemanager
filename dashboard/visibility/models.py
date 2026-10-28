from datetime import datetime, timezone

from sqlalchemy import Column, Date, DateTime, Integer, JSON, MetaData, String, UniqueConstraint, Index, text
from sqlalchemy.orm import declarative_base

Base = declarative_base(metadata=MetaData(schema="visibility"))


class Report(Base):
    __tablename__ = "weekly_reports"
    __table_args__ = (
        UniqueConstraint("site", "week_start", "revision", name="uq_visibility_week_revision"),
        UniqueConstraint("site", "command_key", name="uq_visibility_command"),
        Index("ix_visibility_site_week", "site", "week_start"),
    )
    id = Column(Integer, primary_key=True)
    site = Column(String(100), nullable=False)
    week_start = Column(Date, nullable=False)
    revision = Column(Integer, nullable=False)
    command_key = Column(String(180), nullable=False)
    fingerprint = Column(String(64), nullable=False)
    actor = Column(String(160), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    payload = Column(JSON, nullable=False)
    summary = Column(JSON, nullable=False)


def get_session():
    from database.models import get_session as main_session
    return main_session(None)


def migrate(engine):
    """Explicit, additive migration. Never run on page load or app startup."""
    with engine.begin() as conn:
        if engine.dialect.name == "postgresql":
            conn.execute(text("SELECT pg_advisory_xact_lock(84020261028)"))
            conn.execute(text("CREATE SCHEMA IF NOT EXISTS visibility"))
        Base.metadata.create_all(conn)
