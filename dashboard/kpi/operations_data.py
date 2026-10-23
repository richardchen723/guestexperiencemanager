"""Bounded, cached, read-only adapters to existing Cotton/Hostaway databases.

No refresh, review queue, classification, API extraction, schema creation or
workflow service is invoked. Existing tables lack several required contracts;
we expose that absence instead of upgrading snapshot columns to verified facts.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from threading import Lock
from time import monotonic

from sqlalchemy import func, select, text

from dashboard.kpi.operations import as_utc, build_operations, months_before

logger = logging.getLogger(__name__)
CACHE_SECONDS = 60
MAX_MESSAGES = 50000
_cache = None
_cached_at = 0
_lock = Lock()


def _read(factory, statement):
    session = factory()
    try:
        # PostgreSQL protects this request even if a future adapter accidentally
        # adds a write. SQLite is used only by isolated integration tests.
        if session.get_bind().dialect.name == "postgresql":
            session.execute(text("SET TRANSACTION READ ONLY"))
            session.execute(text("SET LOCAL statement_timeout = '8000ms'"))
        return [dict(row) for row in session.execute(statement).mappings()]
    finally:
        session.rollback()
        session.close()


def load_operational_evidence(*, now=None, main_factory=None, brain_factory=None):
    import dashboard.config as config
    from database.models import Listing, ListingTag, MessageMetadata, Reservation, Review, SyncLog, Tag, get_session
    from brain.models import GuestExperienceAnalysisRun, PropertyGuestIssue, get_session as brain_session

    now = as_utc(now or datetime.now(timezone.utc))
    main_factory = main_factory or (lambda: get_session(config.MAIN_DATABASE_PATH))
    brain_factory = brain_factory or brain_session
    evidence = {"listings": [], "reservations": [], "reviews": [], "messages": [], "issues": [], "sources": {}, "issues_readable": False}
    # UTC padding covers all valid property offsets. Actual inclusion is always
    # checked with property-local windows by the pure aggregation layer.
    earliest = months_before(now.date() - timedelta(days=2), 15)
    start = months_before(now.date() - timedelta(days=2))

    def read_source(name, factory, statement):
        try:
            rows = _read(factory, statement)
            provenance = {"catalog": "Hostaway listings + listing_tags in the configured database", "reservations": "Hostaway reservations in the configured database; financial components/refunds are not established by total_price", "reviews": "Hostaway reviews in the configured database; Cotton review counts are not added", "messages": "Hostaway messages_metadata in the configured database; no conversation text is read", "issues": "Cotton Brain property_guest_issues in the configured database; all operational carry-ins retained"}
            evidence["sources"][name] = {"status": "partial", "checked_at": None, "provenance": provenance.get(name, "Existing synchronized database") + "; completeness unverified", "rows_read": len(rows)}
            return rows
        except Exception:
            logger.warning("Operational KPI source unavailable: %s", name, exc_info=True)
            evidence["sources"][name] = {"status": "error", "checked_at": None, "provenance": "Existing synchronized database", "reason": "Source could not be read. No workflow refresh was attempted."}
            return []

    listings = read_source("catalog", main_factory, select(Listing.listing_id.label("id"), Listing.internal_listing_name.label("internal_name"), Listing.name, Listing.status, Listing.timezone_name.label("timezone"), Listing.last_synced_at).order_by(Listing.listing_id))
    # Tags provide diagnostic portfolio labels only; physical unit and goal-code
    # mappings are deliberately not inferred from them.
    tags = {}
    if listings:
        try:
            for x in _read(main_factory, select(ListingTag.listing_id, Tag.name).join(Tag, ListingTag.tag_id == Tag.tag_id)):
                tags.setdefault(x["listing_id"], []).append(x["name"])
        except Exception:
            logger.warning("Operational KPI portfolio tags unavailable", exc_info=True)
    from dashboard.portfolio_mapping import portfolio_name_for_listing
    for x in listings:
        x["name"] = x.pop("internal_name") or x["name"] or f"Listing {x['id']}"
        x["portfolio"] = portfolio_name_for_listing(x["id"], tags.get(x["id"], [])) or "Unmapped"
    evidence["listings"] = [x for x in listings if str(x.get("status") or "").lower() not in {"deleted", "archived"}]
    # Current archive state must not erase a historical checkout opportunity.
    # Effective management dates and review eligibility remain unverified.
    evidence["historical_listings"] = [x for x in listings if x not in evidence["listings"]]
    ids = [x["id"] for x in listings]
    evidence["reservations"] = read_source("reservations", main_factory, select(Reservation.reservation_id.label("id"), Reservation.listing_id, Reservation.arrival_date.label("arrival"), Reservation.departure_date.label("departure"), Reservation.cancellation_date, Reservation.status, Reservation.channel_name.label("channel"), Reservation.currency).where(Reservation.listing_id.in_(ids), Reservation.arrival_date < now.date() + timedelta(days=2), Reservation.departure_date >= earliest))
    # No guest names, text, contact data, private feedback or response bodies.
    evidence["reviews"] = read_source("reviews", main_factory, select(Review.review_id.label("id"), Review.listing_id, Review.reservation_id, Review.channel_name.label("channel"), Review.overall_rating.label("rating"), Review.review_date.label("date"), Review.origin, Review.status).where(Review.listing_id.in_(ids), Review.review_date >= start, Review.review_date <= now.date() + timedelta(days=1)))
    evidence["sources"]["reviews"]["reason"] = "Current published status and synchronized review_date are observations; historical publication time, eligibility, native scales and portfolio mapping are unverified."
    messages = read_source("messages", main_factory, select(MessageMetadata.message_id.label("id"), MessageMetadata.conversation_id, MessageMetadata.listing_id, MessageMetadata.is_incoming.label("incoming"), MessageMetadata.created_at.label("at"), MessageMetadata.sender_name.label("sender_marker")).where(MessageMetadata.listing_id.in_(ids), MessageMetadata.created_at >= datetime.combine(start, datetime.min.time()), MessageMetadata.created_at <= now.replace(tzinfo=None)).order_by(MessageMetadata.created_at, MessageMetadata.message_id).limit(MAX_MESSAGES + 1))
    for x in messages[:MAX_MESSAGES]:
        marker = str(x.pop("sender_marker") or "").lower()
        x["automated"] = "automated" in marker or "automation" in marker
        x["original_timestamp_verified"] = False
        # created_at is a synchronization timing proxy until original metadata
        # and meaning have been validated. It is never semantic answer proof.
        evidence["messages"].append(x)
    evidence["sources"]["messages"]["reason"] = "Window messages only; carry-in unanswered coverage and sent/meaningful-answer semantics are unverified."
    if len(messages) > MAX_MESSAGES:
        evidence["sources"]["messages"]["reason"] += f" Read capped at {MAX_MESSAGES} rows; coverage incomplete."
    issues = read_source("issues", brain_factory, select(PropertyGuestIssue.issue_id.label("id"), PropertyGuestIssue.listing_id, PropertyGuestIssue.reservation_id, PropertyGuestIssue.source_date.label("date"), PropertyGuestIssue.issue_category.label("category"), PropertyGuestIssue.operational_status, PropertyGuestIssue.workflow_status, PropertyGuestIssue.resolution_state, PropertyGuestIssue.updated_at).order_by(PropertyGuestIssue.issue_id))
    for x in issues:
        # Category strings are not an approved cleaning classification map.
        x["cleaning_candidate"] = any(v in str(x.get("category", "")).lower() for v in ("clean", "hygiene", "housekeeping"))
    evidence["issues"] = issues
    evidence["issues_readable"] = evidence["sources"]["issues"]["status"] != "error"
    # Include operational carry-ins even if the listing is currently missing
    # from the catalog. The denominator remains unverified in that case.
    try:
        syncs = _read(main_factory, select(SyncLog.sync_type, func.max(SyncLog.completed_at).label("checked_at")).where(SyncLog.status == "success", SyncLog.sync_type.in_(["listings", "reservations", "reviews", "messages"])).group_by(SyncLog.sync_type))
        for x in syncs:
            key = "catalog" if x["sync_type"] == "listings" else x["sync_type"]
            evidence["sources"][key]["checked_at"] = as_utc(x["checked_at"]).isoformat() if x["checked_at"] else None
    except Exception:
        logger.warning("Operational KPI sync freshness unavailable", exc_info=True)
    try:
        runs = _read(brain_factory, select(func.max(GuestExperienceAnalysisRun.completed_at).label("checked_at")))
        # Analysis completion is explicitly named; it is not proof of full
        # source ingestion or a complete historical issue-event ledger.
        if runs and runs[0]["checked_at"]:
            evidence["sources"]["issues"]["analysis_completed_at"] = as_utc(runs[0]["checked_at"]).isoformat()
    except Exception:
        logger.warning("Operational KPI issue analysis freshness unavailable", exc_info=True)
    evidence["sources"]["content"] = {"status": "missing", "checked_at": None, "provenance": "No verified historical content-edit ledger exists in the current schema"}
    return evidence


def operational_dashboard():
    """Single-flight, one-minute cache; timestamps stay attached to cached data."""
    global _cache, _cached_at
    with _lock:
        if _cache is None or monotonic() - _cached_at >= CACHE_SECONDS:
            now = datetime.now(timezone.utc)
            _cache = build_operations(load_operational_evidence(now=now), as_of=now)
            _cache["cache_seconds"] = CACHE_SECONDS
            _cached_at = monotonic()
        return _cache
