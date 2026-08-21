#!/usr/bin/env python3
"""Data aggregation foundation for STR Signal Brain."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from statistics import median
from typing import Any, Callable

from sqlalchemy import inspect
from sqlalchemy.exc import SQLAlchemyError

import dashboard.config as config
from brain.models import (
    BookingHealthAnalysis,
    BookingHealthSnapshot,
    BusinessFact,
    BusinessMetricSnapshot,
    CalendarSnapshot,
    DataIngestionRun,
    DataSource,
    GuestStayMemory,
    PortfolioListing,
    PriceLabsSnapshot,
    WhatsAppGroupMemory,
    WhatsAppMessage,
    as_json_safe,
    get_session,
    stable_hash,
)
from brain.scoring import is_confirmed_reservation_status
from database.models import (
    Listing,
    MessageMetadata,
    Reservation,
    Review,
    SyncLog,
    get_session as get_main_session,
)

logger = logging.getLogger(__name__)

FACT_BATCH_FLUSH_SIZE = 250
SOURCE_STREAM_BATCH_SIZE = 500


@dataclass(frozen=True)
class SourceDefinition:
    """Static registry metadata for a Brain source."""

    source_key: str
    name: str
    category: str
    connector_type: str
    cadence_minutes: int
    freshness_threshold_minutes: int
    description: str


@dataclass
class MaterializationResult:
    """Counts returned by one source materializer."""

    status: str = "completed"
    records_seen: int = 0
    facts_created: int = 0
    facts_updated: int = 0
    facts_unchanged: int = 0
    facts_withdrawn: int = 0
    record_counts: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    watermark_start: dict[str, Any] | None = None
    watermark_end: dict[str, Any] | None = None
    error_message: str | None = None

    @property
    def facts_written(self) -> int:
        return self.facts_created + self.facts_updated + self.facts_unchanged


DEFAULT_SOURCE_DEFINITIONS: tuple[SourceDefinition, ...] = (
    SourceDefinition(
        "hostaway_listings",
        "Hostaway Listings",
        "property",
        "hostaway_api",
        720,
        2160,
        "Canonical listing profiles, property attributes, and active inventory.",
    ),
    SourceDefinition(
        "hostaway_reservations",
        "Hostaway Reservations",
        "reservation",
        "hostaway_api",
        720,
        2160,
        "Reservations, stay dates, booking value, channel, guest, and payment status.",
    ),
    SourceDefinition(
        "hostaway_messages",
        "Hostaway Guest Messages",
        "guest_experience",
        "hostaway_api",
        720,
        2160,
        "Guest and host conversation metadata used for stay memory and review-risk reasoning.",
    ),
    SourceDefinition(
        "hostaway_reviews",
        "Hostaway Reviews",
        "guest_experience",
        "hostaway_api",
        720,
        2160,
        "Guest review ratings, text, response state, and listing/reservation attribution.",
    ),
    SourceDefinition(
        "hostaway_calendar",
        "Hostaway Calendar",
        "revenue",
        "hostaway_api",
        720,
        2160,
        "Forward-looking availability, calendar status, pricing, and stay-rule snapshots.",
    ),
    SourceDefinition(
        "pricelabs",
        "PriceLabs",
        "revenue",
        "pricelabs_api",
        720,
        2160,
        "Daily dynamic prices, minimum-stay rules, demand context, pricing reasons, and booking-pattern metrics.",
    ),
    SourceDefinition(
        "booking_health",
        "Booking Health",
        "revenue",
        "brain_derived",
        720,
        2160,
        "Brain-derived occupancy, booking pace, pricing context, and revenue action analysis.",
    ),
    SourceDefinition(
        "whatsapp",
        "WhatsApp Operations",
        "operations",
        "whatsapp_bridge",
        720,
        2160,
        "Team thread messages and rolling operational memory from management groups.",
    ),
    SourceDefinition(
        "guest_stay_memory",
        "Guest Stay Memory",
        "guest_experience",
        "brain_derived",
        720,
        2160,
        "Reservation-level memory summarizing guest issues, resolutions, and review risk.",
    ),
    SourceDefinition(
        "bookkeeping_revenue",
        "Bookkeeping Revenue",
        "finance",
        "bookkeeping_uploads",
        1440,
        4320,
        "Normalized revenue rows imported from channel and direct-booking statements.",
    ),
    SourceDefinition(
        "bookkeeping_expenses",
        "Bookkeeping Expenses",
        "finance",
        "bookkeeping_uploads",
        1440,
        4320,
        "Normalized expense rows, evidence, vendor, property, and reimbursement context.",
    ),
)

FOUNDATION_REQUIRED_FACT_TYPES_BY_SOURCE: dict[str, tuple[str, ...]] = {
    "hostaway_listings": ("property_profile",),
    "hostaway_reservations": ("reservation_stay",),
    "hostaway_messages": ("guest_message",),
    "hostaway_reviews": ("guest_review",),
    "hostaway_calendar": ("calendar_day",),
    "pricelabs": ("pricing_context", "pricelabs_daily_price", "pricelabs_performance_metric"),
    "booking_health": ("booking_health_horizon",),
    "whatsapp": ("whatsapp_message", "team_thread_memory"),
    "guest_stay_memory": ("guest_stay_memory",),
    "bookkeeping_revenue": ("bookkeeping_revenue",),
    "bookkeeping_expenses": ("bookkeeping_expense",),
}

FOUNDATION_REQUIRED_METRIC_NAMES: tuple[str, ...] = (
    "booking_occupancy_rate",
    "booking_health_severity",
    "pricelabs_avg_price_30d",
    "pricelabs_avg_available_price_30d",
    "pricelabs_max_min_stay_30d",
    "pricelabs_unbookable_days_30d",
    "reservation_revenue_next_30d",
    "booked_nights_next_30d",
    "guest_message_count_7d",
    "review_avg_rating_90d",
    "guest_risk_score_max_90d",
    "bookkeeping_revenue_mtd",
    "bookkeeping_expense_mtd",
)

FOUNDATION_LISTING_COVERAGE_TARGETS: tuple[dict[str, Any], ...] = (
    {"kind": "fact", "name": "property_profile", "minimum_pct": 95},
    {"kind": "fact", "name": "calendar_day", "minimum_pct": 95},
    {"kind": "fact", "name": "pricing_context", "minimum_pct": 90},
    {"kind": "fact", "name": "pricelabs_daily_price", "minimum_pct": 90},
    {"kind": "fact", "name": "booking_health_horizon", "minimum_pct": 95},
    {"kind": "metric", "name": "booking_occupancy_rate", "minimum_pct": 95},
    {"kind": "metric", "name": "booking_health_severity", "minimum_pct": 95},
    {"kind": "metric", "name": "pricelabs_avg_price_30d", "minimum_pct": 90},
    {"kind": "metric", "name": "pricelabs_avg_available_price_30d", "minimum_pct": 90},
    {
        "kind": "match",
        "name": "pricing_booking_pattern_match",
        "minimum_pct": 90,
        "required_facts": ("pricelabs_daily_price", "pricelabs_performance_metric", "booking_health_horizon"),
    },
)

FOUNDATION_SOURCE_FRESHNESS_TARGETS: tuple[dict[str, Any], ...] = (
    {"source_key": "hostaway_listings", "name": "Hostaway listings sync", "kind": "sync_log", "sync_type": "listings", "max_age_hours": 36},
    {"source_key": "hostaway_reservations", "name": "Hostaway reservations sync", "kind": "sync_log", "sync_type": "reservations", "max_age_hours": 36},
    {"source_key": "hostaway_messages", "name": "Hostaway messages sync", "kind": "sync_log", "sync_type": "messages", "max_age_hours": 36},
    {"source_key": "hostaway_reviews", "name": "Hostaway reviews sync", "kind": "sync_log", "sync_type": "reviews", "max_age_hours": 72},
    {"source_key": "hostaway_calendar", "name": "Hostaway calendar snapshots", "kind": "fact_occurred", "fact_type": "calendar_day", "max_age_hours": 36},
    {"source_key": "pricelabs", "name": "PriceLabs daily prices", "kind": "fact_occurred", "fact_type": "pricelabs_daily_price", "max_age_hours": 36},
    {"source_key": "booking_health", "name": "Booking-health snapshots", "kind": "fact_occurred", "fact_type": "booking_health_horizon", "max_age_hours": 36},
    {"source_key": "whatsapp", "name": "WhatsApp operations evidence", "kind": "fact_occurred", "fact_type": "whatsapp_message", "max_age_hours": 72},
    {"source_key": "bookkeeping_revenue", "name": "Bookkeeping revenue evidence", "kind": "fact_occurred", "fact_type": "bookkeeping_revenue", "max_age_hours": 45 * 24},
    {"source_key": "bookkeeping_expenses", "name": "Bookkeeping expense evidence", "kind": "fact_occurred", "fact_type": "bookkeeping_expense", "max_age_hours": 45 * 24},
)


def summarize_foundation_audit(gaps: list[dict[str, Any]], *, source_count: int, fact_count: int, metric_count: int) -> dict[str, Any]:
    """Return overall audit status and score from coverage gaps."""
    severity_order = {"critical": 0, "error": 1, "warning": 2, "info": 3}
    ordered_gaps = sorted(gaps or [], key=lambda gap: (severity_order.get(gap.get("severity"), 4), gap.get("key") or ""))
    severity_counts: dict[str, int] = {}
    for gap in ordered_gaps:
        severity = gap.get("severity") or "info"
        severity_counts[severity] = severity_counts.get(severity, 0) + 1

    if severity_counts.get("critical") or fact_count <= 0:
        status = "missing"
    elif severity_counts.get("error"):
        status = "degraded"
    elif severity_counts.get("warning"):
        status = "watch"
    else:
        status = "ok"

    score = 100
    score -= severity_counts.get("critical", 0) * 25
    score -= severity_counts.get("error", 0) * 12
    score -= severity_counts.get("warning", 0) * 5
    if source_count <= 0:
        score -= 25
    if metric_count <= 0:
        score -= 10
    score = max(min(score, 100), 0)

    if status == "ok":
        message = "Data foundation coverage looks ready for downstream decision products."
    elif status == "watch":
        message = "Data foundation is usable, but some coverage gaps should be watched."
    elif status == "degraded":
        message = "Data foundation has coverage gaps that can weaken decision products."
    else:
        message = "Data foundation is missing required coverage for reliable decision products."

    return {
        "status": status,
        "score": score,
        "message": message,
        "severity_counts": severity_counts,
        "gap_count": len(ordered_gaps),
        "gaps": ordered_gaps,
    }


def build_fact_key(fact_type: str, source_key: str, source_id: Any) -> str:
    """Return a stable natural key for a normalized fact."""
    return ":".join(
        [
            _key_part(fact_type),
            _key_part(source_key),
            _key_part(source_id),
        ]
    )


def build_metric_key(
    *,
    metric_name: str,
    grain: str,
    metric_date: date | str,
    portfolio_id: Any = None,
    listing_id: Any = None,
    horizon_days: Any = None,
) -> str:
    """Return a stable natural key for a derived metric snapshot."""
    return ":".join(
        [
            "metric",
            _key_part(metric_name),
            _key_part(grain),
            _key_part(metric_date),
            f"portfolio={_key_part(portfolio_id or 'all')}",
            f"listing={_key_part(listing_id or 'all')}",
            f"horizon={_key_part(horizon_days or 'none')}",
        ]
    )


def data_source_health_status(source: Any, *, now: datetime | None = None) -> dict[str, Any]:
    """Summarize freshness for a single source registry row."""
    now = now or datetime.utcnow()
    status = _attr(source, "status", "missing") or "missing"
    last_success_at = _as_datetime(_attr(source, "last_success_at"))
    threshold = int(_attr(source, "freshness_threshold_minutes", 2160) or 2160)
    age_minutes = None
    effective_status = status
    if last_success_at:
        age_minutes = max((now - last_success_at).total_seconds() / 60.0, 0.0)
        if status == "ok" and age_minutes > threshold:
            effective_status = "stale"
    elif status == "ok":
        effective_status = "missing"

    if effective_status == "ok":
        message = "Source is current."
    elif effective_status == "stale":
        message = "Source is older than its freshness threshold."
    elif effective_status == "error":
        message = _attr(source, "last_error_message") or "Latest ingestion failed."
    elif effective_status in {"missing", "skipped"}:
        message = "No successful ingestion has been recorded yet."
    else:
        message = f"Source status is {effective_status}."

    return {
        "source_key": _attr(source, "source_key"),
        "name": _attr(source, "name"),
        "category": _attr(source, "category"),
        "connector_type": _attr(source, "connector_type"),
        "status": effective_status,
        "stored_status": status,
        "last_success_at": last_success_at.isoformat() if last_success_at else None,
        "last_ingested_at": _iso(_attr(source, "last_ingested_at")),
        "last_error_at": _iso(_attr(source, "last_error_at")),
        "age_minutes": round(age_minutes, 1) if age_minutes is not None else None,
        "freshness_threshold_minutes": threshold,
        "message": message,
    }


def summarize_data_aggregator_health(
    sources: list[dict[str, Any]],
    *,
    fact_count: int,
    latest_completed_at: datetime | str | None = None,
) -> dict[str, Any]:
    """Return compact health for the full data aggregation layer."""
    if not sources:
        return {
            "status": "missing",
            "message": "No data sources are registered yet.",
            "source_count": 0,
            "fact_count": int(fact_count or 0),
            "sources": [],
        }

    status_counts: dict[str, int] = {}
    for source in sources:
        status_counts[source.get("status") or "missing"] = status_counts.get(source.get("status") or "missing", 0) + 1

    if fact_count <= 0:
        status = "missing"
        message = "The data aggregator has registered sources but has not materialized any facts yet."
    elif status_counts.get("error") or status_counts.get("degraded"):
        status = "degraded"
        message = "One or more data sources failed during the latest aggregation pass."
    elif status_counts.get("missing"):
        status = "degraded"
        message = "One or more data sources have not completed a successful aggregation pass."
    elif status_counts.get("stale"):
        status = "stale"
        message = "One or more data sources are older than their freshness threshold."
    else:
        status = "ok"
        message = "The Brain data foundation has current facts across registered sources."

    return {
        "status": status,
        "message": message,
        "source_count": len(sources),
        "ok_source_count": status_counts.get("ok", 0),
        "stale_source_count": status_counts.get("stale", 0),
        "missing_source_count": status_counts.get("missing", 0),
        "error_source_count": status_counts.get("error", 0),
        "fact_count": int(fact_count or 0),
        "latest_completed_at": _iso(latest_completed_at),
        "status_counts": status_counts,
        "sources": sources,
    }


class BrainDataAggregator:
    """Materialize source data into a normalized, provenance-backed fact layer."""

    def __init__(
        self,
        *,
        session=None,
        main_session=None,
        now_fn: Callable[[], datetime] | None = None,
    ):
        self.session = session or get_session()
        self.main_session = main_session or get_main_session(config.MAIN_DATABASE_PATH)
        self._owns_session = session is None
        self._owns_main_session = main_session is None
        self.now_fn = now_fn or datetime.utcnow

    def close(self):
        if self._owns_session:
            self.session.close()
        if self._owns_main_session:
            self.main_session.close()

    def run(
        self,
        *,
        mode: str = "incremental",
        brain_run_id: int | None = None,
        include_financials: bool = True,
        sources: list[str] | tuple[str, ...] | None = None,
        pull: bool = False,
        refresh_metrics: bool = True,
    ) -> dict[str, Any]:
        """Refresh the source registry and materialize normalized facts."""
        pull_result = self._pull_external_sources() if pull else None
        self.ensure_data_sources()
        materializers: dict[str, Callable[[DataIngestionRun], MaterializationResult]] = {
            "hostaway_listings": self._materialize_hostaway_listings,
            "hostaway_reservations": self._materialize_hostaway_reservations,
            "hostaway_messages": self._materialize_hostaway_messages,
            "hostaway_reviews": self._materialize_hostaway_reviews,
            "hostaway_calendar": self._materialize_hostaway_calendar,
            "pricelabs": self._materialize_pricelabs,
            "booking_health": self._materialize_booking_health,
            "whatsapp": self._materialize_whatsapp,
            "guest_stay_memory": self._materialize_guest_stay_memory,
            "bookkeeping_revenue": self._materialize_bookkeeping_revenue,
            "bookkeeping_expenses": self._materialize_bookkeeping_expenses,
        }
        requested_sources = set(sources or materializers.keys())
        if not include_financials:
            requested_sources -= {"bookkeeping_revenue", "bookkeeping_expenses"}

        results = {}
        for source_key, materializer in materializers.items():
            if source_key not in requested_sources:
                continue
            results[source_key] = self._run_source(
                source_key,
                materializer,
                mode=mode,
                brain_run_id=brain_run_id,
            )

        metric_result = self.refresh_metric_snapshots() if refresh_metrics else None
        facts_total = sum(int(item.get("facts_written", 0) or 0) for item in results.values())
        return {
            "status": "completed" if all(item.get("status") == "completed" for item in results.values()) else "degraded",
            "mode": mode,
            "brain_run_id": brain_run_id,
            "source_count": len(results),
            "facts_written": facts_total,
            "metric_snapshots": metric_result,
            "sources": results,
            "pull_result": pull_result,
        }

    def ensure_data_sources(self) -> dict[str, Any]:
        """Create or update the source registry rows."""
        created = 0
        updated = 0
        for definition in DEFAULT_SOURCE_DEFINITIONS:
            source = self.session.query(DataSource).filter(DataSource.source_key == definition.source_key).first()
            if not source:
                source = DataSource(source_key=definition.source_key, status="missing")
                self.session.add(source)
                created += 1
            else:
                updated += 1
            source.name = definition.name
            source.category = definition.category
            source.connector_type = definition.connector_type
            source.cadence_minutes = definition.cadence_minutes
            source.freshness_threshold_minutes = definition.freshness_threshold_minutes
            source.description = definition.description
            source.is_active = True
            source.updated_at = self.now_fn()
        self.session.commit()
        return {"created": created, "updated": updated, "total": len(DEFAULT_SOURCE_DEFINITIONS)}

    def _mapped_listing_portfolios(self) -> dict[int, int | None]:
        return {
            int(row.listing_id): row.portfolio_id
            for row in self.session.query(PortfolioListing).all()
            if row.listing_id is not None
        }

    def _pull_external_sources(self) -> dict[str, Any]:
        """Optionally run the existing Hostaway incremental sync before materializing facts."""
        from sync.sync_manager import incremental_sync

        logger.info("Running Hostaway incremental sync before Brain aggregation")
        return as_json_safe(incremental_sync(force=True))

    def refresh_metric_snapshots(self) -> dict[str, Any]:
        """Build decision-ready metric snapshots from normalized facts."""
        metric_date = self.now_fn().date()
        listing_portfolios = {
            int(row.listing_id): row.portfolio_id
            for row in self.session.query(PortfolioListing).all()
            if row.listing_id
        }
        active_metric_keys: set[str] = set()
        counts = {"created": 0, "updated": 0, "unchanged": 0, "withdrawn": 0}

        def write_metric(**kwargs):
            outcome = self._upsert_metric_snapshot(metric_date=metric_date, listing_portfolios=listing_portfolios, **kwargs)
            active_metric_keys.add(kwargs["metric_key"])
            counts[outcome] = counts.get(outcome, 0) + 1

        self._write_booking_health_metrics(write_metric, metric_date=metric_date)
        self._write_pricelabs_metrics(write_metric, metric_date=metric_date, listing_portfolios=listing_portfolios)
        self._write_reservation_metrics(write_metric, metric_date=metric_date)
        self._write_guest_experience_metrics(write_metric, metric_date=metric_date)
        self._write_finance_metrics(write_metric, metric_date=metric_date)
        counts["withdrawn"] = self._withdraw_missing_metric_snapshots(metric_date=metric_date, active_metric_keys=active_metric_keys)
        self.session.commit()
        return {
            "metric_date": metric_date.isoformat(),
            "snapshots_written": counts["created"] + counts["updated"] + counts["unchanged"],
            **counts,
        }

    def _write_booking_health_metrics(self, write_metric: Callable[..., None], *, metric_date: date):
        facts = self.session.query(BusinessFact).filter(
            BusinessFact.status == "active",
            BusinessFact.fact_type == "booking_health_horizon",
        ).all()
        facts = _latest_booking_health_horizon_facts(facts)
        horizon_facts_by_listing: dict[int, list[BusinessFact]] = {}
        for fact in facts:
            if fact.listing_id:
                horizon_facts_by_listing.setdefault(int(fact.listing_id), []).append(fact)
            payload = fact.fact_payload or {}
            raw_metrics = payload.get("raw_metrics") if isinstance(payload.get("raw_metrics"), dict) else {}
            horizon_days = _coerce_int(payload.get("horizon_days"))
            if not horizon_days:
                continue
            write_metric(
                metric_key=build_metric_key(
                    metric_name="booking_occupancy_rate",
                    grain="listing",
                    metric_date=metric_date,
                    listing_id=fact.listing_id,
                    horizon_days=horizon_days,
                ),
                metric_name="booking_occupancy_rate",
                category="revenue",
                grain="listing",
                horizon_days=horizon_days,
                portfolio_id=fact.portfolio_id,
                listing_id=fact.listing_id,
                numeric_value=fact.numeric_value,
                text_value=fact.text_value,
                status=_booking_metric_status(fact.text_value),
                confidence=fact.confidence,
                source_keys=["booking_health"],
                payload={
                    "diagnosis": fact.text_value,
                    "booked_nights": payload.get("booked_nights"),
                    "available_nights": payload.get("available_nights"),
                    "blocked_nights": raw_metrics.get("blocked_nights"),
                    "sellable_nights": raw_metrics.get("sellable_nights"),
                    "calendar_coverage_days": raw_metrics.get("calendar_coverage_days"),
                    "occupancy_denominator": raw_metrics.get("occupancy_denominator"),
                    "occupancy_source": raw_metrics.get("occupancy_source"),
                    "occupancy_rate_measurable": raw_metrics.get("occupancy_rate_measurable", True),
                    "expected_occupancy_rate": payload.get("expected_occupancy_rate"),
                    "recommended_action": payload.get("recommended_action"),
                    "source_fact_id": fact.business_fact_id,
                },
            )

        analyses = self.session.query(BusinessFact).filter(
            BusinessFact.status == "active",
            BusinessFact.fact_type == "booking_health_analysis",
        ).all()
        analyses = _latest_fresh_booking_health_analysis_facts(analyses, horizon_facts_by_listing)
        analysis_listing_ids: set[int] = set()
        for fact in analyses:
            if fact.listing_id:
                analysis_listing_ids.add(int(fact.listing_id))
            payload = fact.fact_payload or {}
            write_metric(
                metric_key=build_metric_key(
                    metric_name="booking_health_severity",
                    grain="listing",
                    metric_date=metric_date,
                    listing_id=fact.listing_id,
                ),
                metric_name="booking_health_severity",
                category="revenue",
                grain="listing",
                portfolio_id=fact.portfolio_id,
                listing_id=fact.listing_id,
                text_value=payload.get("severity") or "unknown",
                status=_severity_metric_status(payload.get("severity")),
                confidence=fact.confidence,
                source_keys=["booking_health", "pricelabs", "hostaway_calendar"],
                payload={
                    "opinion": payload.get("opinion"),
                    "booking_pattern": payload.get("booking_pattern"),
                    "action_items": payload.get("action_items") or [],
                    "source_statuses": payload.get("source_statuses") or {},
                    "source_fact_id": fact.business_fact_id,
                },
            )

        for listing_id, rows in horizon_facts_by_listing.items():
            if listing_id in analysis_listing_ids:
                continue
            severity = _booking_health_severity_from_diagnoses(row.text_value for row in rows)
            if severity == "unknown":
                continue
            write_metric(
                metric_key=build_metric_key(
                    metric_name="booking_health_severity",
                    grain="listing",
                    metric_date=metric_date,
                    listing_id=listing_id,
                ),
                metric_name="booking_health_severity",
                category="revenue",
                grain="listing",
                portfolio_id=rows[0].portfolio_id,
                listing_id=listing_id,
                text_value=severity,
                status=_severity_metric_status(severity),
                confidence=_average_confidence(rows),
                source_keys=["booking_health"],
                payload={
                    "source": "booking_health_horizon_fallback",
                    "diagnoses": sorted({str(row.text_value or "unknown") for row in rows}),
                    "source_fact_ids": [row.business_fact_id for row in rows],
                },
            )

    def _write_pricelabs_metrics(self, write_metric: Callable[..., None], *, metric_date: date, listing_portfolios: dict[int, int]):
        cutoff = metric_date + timedelta(days=30)
        facts = self.session.query(BusinessFact).filter(
            BusinessFact.status == "active",
            BusinessFact.fact_type == "pricelabs_daily_price",
            BusinessFact.effective_start >= datetime.combine(metric_date, time.min),
            BusinessFact.effective_start < datetime.combine(cutoff, time.min),
        ).all()
        grouped: dict[int, list[BusinessFact]] = {}
        for fact in facts:
            if fact.listing_id and int(fact.listing_id) in listing_portfolios:
                grouped.setdefault(int(fact.listing_id), []).append(fact)
        calendar_facts = self.session.query(BusinessFact).filter(
            BusinessFact.status == "active",
            BusinessFact.fact_type == "calendar_day",
            BusinessFact.effective_start >= datetime.combine(metric_date, time.min),
            BusinessFact.effective_start < datetime.combine(cutoff, time.min),
            BusinessFact.listing_id.in_(list(listing_portfolios) or [-1]),
        ).all()
        latest_calendar_by_listing_date: dict[tuple[int, date], BusinessFact] = {}
        for fact in calendar_facts:
            calendar_date = _parse_date(fact.effective_start)
            if not fact.listing_id or not calendar_date:
                continue
            key = (int(fact.listing_id), calendar_date)
            current = latest_calendar_by_listing_date.get(key)
            if current is None or _calendar_fact_rank(fact) > _calendar_fact_rank(current):
                latest_calendar_by_listing_date[key] = fact
        for listing_id, rows in grouped.items():
            prices = [_coerce_float(row.numeric_value) for row in rows]
            prices = [value for value in prices if value is not None and value > 0]
            available_price_rows: list[BusinessFact] = []
            available_calendar_fact_ids: list[int] = []
            for row in rows:
                calendar_date = _parse_date(row.effective_start)
                calendar_fact = latest_calendar_by_listing_date.get((listing_id, calendar_date)) if calendar_date else None
                calendar_payload = (calendar_fact.fact_payload or {}) if calendar_fact else {}
                if calendar_fact and (
                    calendar_payload.get("is_available") is True
                    or str(calendar_payload.get("status") or "").strip().lower() == "available"
                ):
                    available_price_rows.append(row)
                    available_calendar_fact_ids.append(calendar_fact.business_fact_id)
            available_prices = [
                value
                for value in (_coerce_float(row.numeric_value) for row in available_price_rows)
                if value is not None and value > 0
            ]
            available_price_median = float(median(available_prices)) if available_prices else None
            available_price_average = sum(available_prices) / len(available_prices) if available_prices else None
            holiday_event_rows = [row for row in available_price_rows if _pricelabs_holiday_events(row)]
            high_demand_available_rows = [
                row
                for row in available_price_rows
                if "high" in str((row.fact_payload or {}).get("demand_desc") or "").lower()
            ]
            date_override_rows = [row for row in available_price_rows if _pricelabs_has_date_override(row)]
            event_or_override_fact_ids = {
                row.business_fact_id
                for row in holiday_event_rows + high_demand_available_rows + date_override_rows
            }
            holiday_events = sorted(
                {
                    event
                    for row in holiday_event_rows
                    for event in _pricelabs_holiday_events(row)
                }
            )
            min_stays = [_coerce_float((row.fact_payload or {}).get("min_stay")) for row in rows]
            min_stays = [value for value in min_stays if value is not None]
            unbookable_days = len([row for row in rows if _pricelabs_fact_is_unavailable(row)])
            high_demand_days = len([row for row in rows if "high" in str((row.fact_payload or {}).get("demand_desc") or "").lower()])
            source_fact_ids = [row.business_fact_id for row in rows]
            portfolio_id = rows[0].portfolio_id
            if prices:
                write_metric(
                    metric_key=build_metric_key(metric_name="pricelabs_avg_price_30d", grain="listing", metric_date=metric_date, listing_id=listing_id, horizon_days=30),
                    metric_name="pricelabs_avg_price_30d",
                    category="revenue",
                    grain="listing",
                    horizon_days=30,
                    portfolio_id=portfolio_id,
                    listing_id=listing_id,
                    numeric_value=round(sum(prices) / len(prices), 2),
                    status="ok",
                    confidence=_average_confidence(rows),
                    source_keys=["pricelabs"],
                    payload={"days": len(prices), "min_price": min(prices), "max_price": max(prices), "source_fact_ids": source_fact_ids},
                )
            write_metric(
                metric_key=build_metric_key(
                    metric_name="pricelabs_avg_available_price_30d",
                    grain="listing",
                    metric_date=metric_date,
                    listing_id=listing_id,
                    horizon_days=30,
                ),
                metric_name="pricelabs_avg_available_price_30d",
                category="revenue",
                grain="listing",
                horizon_days=30,
                portfolio_id=portfolio_id,
                listing_id=listing_id,
                numeric_value=round(available_price_average, 2) if available_price_average is not None else None,
                text_value=None if available_prices else "no_available_dates_in_window",
                status="ok" if available_prices else "not_applicable",
                confidence=min(_average_confidence(available_price_rows), 0.95) if available_price_rows else 0.9,
                source_keys=["pricelabs", "hostaway_calendar"],
                payload={
                    "available_days": len(available_prices),
                    "calendar_matched_days": len(available_price_rows),
                    "total_price_days": len(prices),
                    "min_available_price": min(available_prices) if available_prices else None,
                    "max_available_price": max(available_prices) if available_prices else None,
                    "median_available_price": round(available_price_median, 2) if available_price_median is not None else None,
                    "mean_to_median_ratio": (
                        round(available_price_average / available_price_median, 4)
                        if available_price_average is not None and available_price_median
                        else None
                    ),
                    "holiday_event_available_days": len(holiday_event_rows),
                    "high_demand_available_days": len(high_demand_available_rows),
                    "date_override_available_days": len(date_override_rows),
                    "event_or_override_available_days": len(event_or_override_fact_ids),
                    "holiday_events": holiday_events,
                    "source_fact_ids": [row.business_fact_id for row in available_price_rows],
                    "source_calendar_fact_ids": available_calendar_fact_ids,
                    "basis": "PriceLabs recommended price on dates Hostaway marks available",
                },
            )
            if min_stays:
                write_metric(
                    metric_key=build_metric_key(metric_name="pricelabs_max_min_stay_30d", grain="listing", metric_date=metric_date, listing_id=listing_id, horizon_days=30),
                    metric_name="pricelabs_max_min_stay_30d",
                    category="revenue",
                    grain="listing",
                    horizon_days=30,
                    portfolio_id=portfolio_id,
                    listing_id=listing_id,
                    numeric_value=max(min_stays),
                    status="watch" if max(min_stays) >= 4 else "ok",
                    confidence=_average_confidence(rows),
                    source_keys=["pricelabs"],
                    payload={"avg_min_stay": round(sum(min_stays) / len(min_stays), 2), "source_fact_ids": source_fact_ids},
                )
            write_metric(
                metric_key=build_metric_key(metric_name="pricelabs_unbookable_days_30d", grain="listing", metric_date=metric_date, listing_id=listing_id, horizon_days=30),
                metric_name="pricelabs_unbookable_days_30d",
                category="revenue",
                grain="listing",
                horizon_days=30,
                portfolio_id=portfolio_id,
                listing_id=listing_id,
                numeric_value=unbookable_days,
                status="watch" if unbookable_days else "ok",
                confidence=_average_confidence(rows),
                source_keys=["pricelabs"],
                payload={"high_demand_days": high_demand_days, "source_fact_ids": source_fact_ids},
            )

    def _write_reservation_metrics(self, write_metric: Callable[..., None], *, metric_date: date):
        horizon_end = metric_date + timedelta(days=30)
        facts = self.session.query(BusinessFact).filter(
            BusinessFact.status == "active",
            BusinessFact.fact_type == "reservation_stay",
            BusinessFact.effective_end >= datetime.combine(metric_date, time.min),
            BusinessFact.effective_start < datetime.combine(horizon_end, time.min),
        ).all()
        grouped: dict[int, list[BusinessFact]] = {}
        for fact in facts:
            if fact.listing_id:
                grouped.setdefault(int(fact.listing_id), []).append(fact)
        listing_portfolios = self._mapped_listing_portfolios()
        for listing_id in sorted(set(listing_portfolios) | set(grouped)):
            candidate_rows = grouped.get(listing_id, [])
            rows = [
                row
                for row in candidate_rows
                if is_confirmed_reservation_status((row.fact_payload or {}).get("status"))
            ]
            occupied_dates: set[date] = set()
            revenue = 0.0
            overlap_nights_total = 0
            for row in rows:
                overlap_nights = _overlap_nights(row.effective_start, row.effective_end, metric_date, horizon_end)
                overlap_nights_total += overlap_nights
                start = max(_parse_date(row.effective_start) or metric_date, metric_date)
                occupied_dates.update(start + timedelta(days=offset) for offset in range(overlap_nights))
                stay_nights = _overlap_nights(row.effective_start, row.effective_end, date.min, date.max)
                if stay_nights > 0:
                    revenue += (_coerce_float(row.numeric_value) or 0.0) * overlap_nights / stay_nights
            booked_nights = len(occupied_dates)
            source_fact_ids = [row.business_fact_id for row in rows]
            excluded_status_counts: dict[str, int] = {}
            for row in candidate_rows:
                if row in rows:
                    continue
                status = str((row.fact_payload or {}).get("status") or "unknown").strip().lower()
                excluded_status_counts[status] = excluded_status_counts.get(status, 0) + 1
            confidence = _average_confidence(rows) if rows else 0.9
            portfolio_id = listing_portfolios.get(listing_id)
            value_coverage = _reservation_value_coverage(rows)
            unverified_value_count = value_coverage["unverified_value_reservation_count"]
            if rows and unverified_value_count == len(rows):
                revenue_value = None
                revenue_text = "no_verified_reservation_value_coverage"
                revenue_status = "not_available"
                revenue_confidence = min(confidence, 0.4)
            elif unverified_value_count:
                revenue_value = round(revenue, 2)
                revenue_text = "partial_reservation_value_coverage"
                revenue_status = "watch"
                revenue_confidence = min(confidence, 0.7)
            else:
                revenue_value = round(revenue, 2)
                revenue_text = None
                revenue_status = "ok"
                revenue_confidence = confidence
            payload = {
                "reservation_count": len(rows),
                "candidate_reservation_count": len(candidate_rows),
                "excluded_status_counts": excluded_status_counts,
                "source_fact_ids": source_fact_ids,
                "empty_window": not rows,
                **value_coverage,
            }
            write_metric(
                metric_key=build_metric_key(metric_name="reservation_revenue_next_30d", grain="listing", metric_date=metric_date, listing_id=listing_id, horizon_days=30),
                metric_name="reservation_revenue_next_30d",
                category="revenue",
                grain="listing",
                horizon_days=30,
                portfolio_id=portfolio_id,
                listing_id=listing_id,
                numeric_value=revenue_value,
                text_value=revenue_text,
                status=revenue_status,
                confidence=revenue_confidence,
                source_keys=["hostaway_reservations"],
                payload={
                    **payload,
                    "allocation_method": "prorated_by_overlap_nights",
                    "window_start": metric_date.isoformat(),
                    "window_end_exclusive": horizon_end.isoformat(),
                },
            )
            write_metric(
                metric_key=build_metric_key(metric_name="booked_nights_next_30d", grain="listing", metric_date=metric_date, listing_id=listing_id, horizon_days=30),
                metric_name="booked_nights_next_30d",
                category="revenue",
                grain="listing",
                horizon_days=30,
                portfolio_id=portfolio_id,
                listing_id=listing_id,
                numeric_value=booked_nights,
                status="ok",
                confidence=confidence,
                source_keys=["hostaway_reservations"],
                payload={
                    **payload,
                    "count_method": "unique_confirmed_occupied_dates",
                    "overlap_nights_before_deduplication": overlap_nights_total,
                    "overlap_duplicate_nights": max(overlap_nights_total - booked_nights, 0),
                    "window_start": metric_date.isoformat(),
                    "window_end_exclusive": horizon_end.isoformat(),
                },
            )

    def _write_guest_experience_metrics(self, write_metric: Callable[..., None], *, metric_date: date):
        mapped_listing_portfolios = self._mapped_listing_portfolios()
        message_cutoff = datetime.combine(metric_date - timedelta(days=7), time.min)
        message_rows = self.session.query(BusinessFact).filter(
            BusinessFact.status == "active",
            BusinessFact.fact_type == "guest_message",
            BusinessFact.occurred_at >= message_cutoff,
        ).all()
        message_groups = _group_facts_by_listing(message_rows)
        for listing_id in sorted(set(mapped_listing_portfolios) | set(message_groups)):
            rows = message_groups.get(listing_id, [])
            incoming_count = len([row for row in rows if _truthy((row.fact_payload or {}).get("is_incoming"))])
            write_metric(
                metric_key=build_metric_key(metric_name="guest_message_count_7d", grain="listing", metric_date=metric_date, listing_id=listing_id, horizon_days=7),
                metric_name="guest_message_count_7d",
                category="guest_experience",
                grain="listing",
                horizon_days=7,
                portfolio_id=rows[0].portfolio_id if rows else mapped_listing_portfolios.get(listing_id),
                listing_id=listing_id,
                numeric_value=len(rows),
                status="watch" if incoming_count >= 8 else "ok",
                confidence=_average_confidence(rows) if rows else 0.7,
                source_keys=["hostaway_messages"],
                payload={"incoming_count": incoming_count, "source_fact_ids": [row.business_fact_id for row in rows], "empty_window": not rows},
            )

        review_cutoff = datetime.combine(metric_date - timedelta(days=90), time.min)
        review_rows = self.session.query(BusinessFact).filter(
            BusinessFact.status == "active",
            BusinessFact.fact_type == "guest_review",
            BusinessFact.occurred_at >= review_cutoff,
        ).all()
        review_groups = _group_facts_by_listing(review_rows)
        for listing_id in sorted(set(mapped_listing_portfolios) | set(review_groups)):
            rows = review_groups.get(listing_id, [])
            ratings = [_coerce_float(row.numeric_value) for row in rows]
            ratings = [rating for rating in ratings if rating is not None]
            low_count = len([rating for rating in ratings if rating <= 3.5])
            write_metric(
                metric_key=build_metric_key(metric_name="review_avg_rating_90d", grain="listing", metric_date=metric_date, listing_id=listing_id, horizon_days=90),
                metric_name="review_avg_rating_90d",
                category="guest_experience",
                grain="listing",
                horizon_days=90,
                portfolio_id=rows[0].portfolio_id if rows else mapped_listing_portfolios.get(listing_id),
                listing_id=listing_id,
                numeric_value=round(sum(ratings) / len(ratings), 2) if ratings else None,
                text_value=None if ratings else "no_reviews_in_window",
                status="watch" if low_count else "ok",
                confidence=_average_confidence(rows) if rows else 0.7,
                source_keys=["hostaway_reviews"],
                payload={"review_count": len(ratings), "low_review_count": low_count, "source_fact_ids": [row.business_fact_id for row in rows], "empty_window": not rows},
            )

        risk_rows = self.session.query(BusinessFact).filter(
            BusinessFact.status == "active",
            BusinessFact.fact_type == "guest_stay_memory",
            BusinessFact.occurred_at >= review_cutoff,
        ).all()
        risk_rows = _latest_guest_stay_memory_facts(risk_rows)
        risk_groups = _group_facts_by_listing(risk_rows)
        for listing_id in sorted(set(mapped_listing_portfolios) | set(risk_groups)):
            rows = risk_groups.get(listing_id, [])
            risk_scores = [_coerce_float(row.numeric_value) for row in rows]
            risk_scores = [score for score in risk_scores if score is not None]
            max_score = max(risk_scores) if risk_scores else 0
            write_metric(
                metric_key=build_metric_key(metric_name="guest_risk_score_max_90d", grain="listing", metric_date=metric_date, listing_id=listing_id, horizon_days=90),
                metric_name="guest_risk_score_max_90d",
                category="guest_experience",
                grain="listing",
                horizon_days=90,
                portfolio_id=rows[0].portfolio_id if rows else mapped_listing_portfolios.get(listing_id),
                listing_id=listing_id,
                numeric_value=max_score,
                status="attention" if max_score >= 70 else "watch" if max_score >= 40 else "ok",
                confidence=_average_confidence(rows) if rows else 0.7,
                source_keys=["guest_stay_memory"],
                payload={"memory_count": len(rows), "source_fact_ids": [row.business_fact_id for row in rows], "empty_window": not rows},
            )

    def _write_finance_metrics(self, write_metric: Callable[..., None], *, metric_date: date):
        month_start = metric_date.replace(day=1)
        month_start_dt = datetime.combine(month_start, time.min)
        month_end_dt = datetime.combine(metric_date + timedelta(days=1), time.min)
        for fact_type, metric_name, source_key in (
            ("bookkeeping_revenue", "bookkeeping_revenue_mtd", "bookkeeping_revenue"),
            ("bookkeeping_expense", "bookkeeping_expense_mtd", "bookkeeping_expenses"),
        ):
            rows = self.session.query(BusinessFact).filter(
                BusinessFact.status == "active",
                BusinessFact.fact_type == fact_type,
                BusinessFact.occurred_at >= month_start_dt,
                BusinessFact.occurred_at < month_end_dt,
            ).all()
            grouped = _group_facts_by_listing(rows, include_unassigned=True)
            if not grouped:
                write_metric(
                    metric_key=build_metric_key(metric_name=metric_name, grain="business", metric_date=metric_date, horizon_days=None),
                    metric_name=metric_name,
                    category="finance",
                    grain="business",
                    numeric_value=0,
                    status="ok",
                    confidence=0.7,
                    source_keys=[source_key],
                    payload={"transaction_count": 0, "source_fact_ids": [], "empty_window": True},
                )
            for listing_id, listing_rows in grouped.items():
                total = sum(_coerce_float(row.numeric_value) or 0.0 for row in listing_rows)
                write_metric(
                    metric_key=build_metric_key(metric_name=metric_name, grain="listing" if listing_id else "business", metric_date=metric_date, listing_id=listing_id, horizon_days=None),
                    metric_name=metric_name,
                    category="finance",
                    grain="listing" if listing_id else "business",
                    listing_id=listing_id,
                    portfolio_id=listing_rows[0].portfolio_id if listing_id else None,
                    numeric_value=round(total, 2),
                    status="watch" if any((row.fact_payload or {}).get("needs_review") for row in listing_rows) else "ok",
                    confidence=_average_confidence(listing_rows),
                    source_keys=[source_key],
                    payload={"transaction_count": len(listing_rows), "source_fact_ids": [row.business_fact_id for row in listing_rows]},
                )

    def _run_source(
        self,
        source_key: str,
        materializer: Callable[[DataIngestionRun], MaterializationResult],
        *,
        mode: str,
        brain_run_id: int | None,
    ) -> dict[str, Any]:
        source = self.session.query(DataSource).filter(DataSource.source_key == source_key).first()
        if not source:
            source = DataSource(source_key=source_key, name=source_key, category="unknown", connector_type="unknown")
            self.session.add(source)
            self.session.commit()

        started_at = self.now_fn()
        run = DataIngestionRun(
            data_source_id=source.data_source_id,
            brain_run_id=brain_run_id,
            source_key=source_key,
            mode=mode,
            status="running",
            started_at=started_at,
        )
        self.session.add(run)
        self.session.commit()
        try:
            result = materializer(run)
            completed_at = self.now_fn()
            run.status = result.status
            run.records_seen = result.records_seen
            run.facts_written = result.facts_written
            run.facts_created = result.facts_created
            run.facts_updated = result.facts_updated
            run.facts_unchanged = result.facts_unchanged
            run.facts_withdrawn = result.facts_withdrawn
            run.record_counts = as_json_safe(result.record_counts)
            run.run_metadata = as_json_safe(result.metadata)
            run.watermark_start = as_json_safe(result.watermark_start)
            run.watermark_end = as_json_safe(result.watermark_end)
            run.error_message = result.error_message
            run.completed_at = completed_at

            source.last_ingested_at = completed_at
            source.status = "ok" if result.status == "completed" else result.status
            if result.status == "completed":
                source.last_success_at = completed_at
                source.last_error_message = None
            elif result.status == "missing":
                source.last_error_message = result.error_message
            source.updated_at = completed_at
            self.session.commit()
            return self._run_dict(run)
        except Exception as exc:
            logger.warning("Data aggregation failed for %s: %s", source_key, exc, exc_info=True)
            self.session.rollback()
            run = self.session.get(DataIngestionRun, run.data_ingestion_run_id)
            source = self.session.query(DataSource).filter(DataSource.source_key == source_key).first()
            completed_at = self.now_fn()
            if run:
                run.status = "error"
                run.error_message = str(exc)
                run.completed_at = completed_at
            if source:
                source.status = "error"
                source.last_ingested_at = completed_at
                source.last_error_at = completed_at
                source.last_error_message = str(exc)
                source.updated_at = completed_at
            self.session.commit()
            return self._run_dict(run) if run else {"source_key": source_key, "status": "error", "error_message": str(exc)}

    def _materialize_hostaway_listings(self, run: DataIngestionRun) -> MaterializationResult:
        batch = FactBatch(self, run, "hostaway_listings", {"property_profile"})
        rows = self.main_session.query(Listing).order_by(Listing.listing_id).all()
        for row in rows:
            name = row.internal_listing_name or row.name
            batch.upsert(
                fact_type="property_profile",
                grain="listing",
                source_table="listings",
                source_id=row.listing_id,
                listing_id=row.listing_id,
                occurred_at=row.updated_on or row.last_synced_at or row.inserted_on,
                numeric_value=row.base_price,
                text_value=name,
                payload={
                    "listing_id": row.listing_id,
                    "name": row.name,
                    "internal_listing_name": row.internal_listing_name,
                    "status": row.status,
                    "address": row.address,
                    "city": row.city,
                    "state": row.state,
                    "country": row.country,
                    "zipcode": row.zipcode,
                    "timezone_name": row.timezone_name,
                    "base_price": row.base_price,
                    "currency": row.currency,
                    "accommodates": row.accommodates,
                    "bedrooms": row.bedrooms,
                    "bathrooms": row.bathrooms,
                    "beds": row.beds,
                    "account_id": row.account_id,
                    "amenities": row.get_amenities_list(),
                    "custom_fields": row.get_custom_fields_dict(),
                },
            )
        return batch.finish(record_counts={"listings": len(rows)})

    def _materialize_hostaway_reservations(self, run: DataIngestionRun) -> MaterializationResult:
        batch = FactBatch(self, run, "hostaway_reservations", {"reservation_stay"})
        rows = self.main_session.query(Reservation).order_by(Reservation.reservation_id).all()
        for row in rows:
            batch.upsert(
                fact_type="reservation_stay",
                grain="reservation",
                source_table="reservations",
                source_id=row.reservation_id,
                listing_id=row.listing_id,
                reservation_id=row.reservation_id,
                guest_id=row.guest_id,
                occurred_at=row.reservation_date or row.inserted_on or row.updated_on,
                effective_start=row.arrival_date,
                effective_end=row.departure_date,
                numeric_value=row.total_price,
                text_value=row.guest_name,
                payload={
                    "reservation_id": row.reservation_id,
                    "listing_id": row.listing_id,
                    "guest_id": row.guest_id,
                    "guest_name": row.guest_name,
                    "channel_name": row.channel_name,
                    "source": row.source,
                    "confirmation_code": row.confirmation_code,
                    "arrival_date": row.arrival_date,
                    "departure_date": row.departure_date,
                    "nights": row.nights,
                    "number_of_guests": row.number_of_guests,
                    "total_price": row.total_price,
                    "currency": row.currency,
                    "tax_amount": row.tax_amount,
                    "cleaning_fee": row.cleaning_fee,
                    "remaining_balance": row.remaining_balance,
                    "status": row.status,
                    "payment_status": row.payment_status,
                    "is_paid": row.is_paid,
                    "reservation_date": row.reservation_date,
                    "latest_activity_on": row.latest_activity_on,
                },
            )
        latest_sync = self._latest_sync_log("reservations")
        return batch.finish(
            record_counts={"reservations": len(rows)},
            metadata={"latest_sync": latest_sync},
        )

    def _materialize_hostaway_messages(self, run: DataIngestionRun) -> MaterializationResult:
        batch = FactBatch(self, run, "hostaway_messages", {"guest_message"})
        rows = (
            self.main_session.query(MessageMetadata)
            .order_by(MessageMetadata.message_id)
            .yield_per(SOURCE_STREAM_BATCH_SIZE)
        )
        for row in rows:
            batch.upsert(
                fact_type="guest_message",
                grain="message",
                source_table="messages_metadata",
                source_id=row.message_id,
                listing_id=row.listing_id,
                reservation_id=row.reservation_id,
                guest_id=row.guest_id,
                occurred_at=row.created_at,
                text_value=row.content_preview,
                payload={
                    "message_id": row.message_id,
                    "conversation_id": row.conversation_id,
                    "reservation_id": row.reservation_id,
                    "listing_id": row.listing_id,
                    "guest_id": row.guest_id,
                    "sender_type": row.sender_type,
                    "sender_name": row.sender_name,
                    "is_incoming": row.is_incoming,
                    "message_type": row.message_type,
                    "content_preview": row.content_preview,
                    "has_attachment": row.has_attachment,
                    "created_at": row.created_at,
                },
                confidence=0.95,
            )
        latest_sync = self._latest_sync_log("messages")
        return batch.finish(
            record_counts={"messages": batch.records_seen},
            metadata={"latest_sync": latest_sync},
        )

    def _materialize_hostaway_reviews(self, run: DataIngestionRun) -> MaterializationResult:
        batch = FactBatch(self, run, "hostaway_reviews", {"guest_review"})
        rows = self.main_session.query(Review).order_by(Review.review_id).all()
        for row in rows:
            batch.upsert(
                fact_type="guest_review",
                grain="review",
                source_table="reviews",
                source_id=row.review_id,
                listing_id=row.listing_id,
                reservation_id=row.reservation_id,
                guest_id=row.guest_id,
                occurred_at=row.review_date or row.inserted_on,
                numeric_value=row.overall_rating,
                text_value=row.review_text,
                payload={
                    "review_id": row.review_id,
                    "listing_id": row.listing_id,
                    "reservation_id": row.reservation_id,
                    "guest_id": row.guest_id,
                    "channel_name": row.channel_name,
                    "overall_rating": row.overall_rating,
                    "review_text": row.review_text,
                    "reviewer_name": row.reviewer_name,
                    "review_date": row.review_date,
                    "response_text": row.response_text,
                    "response_date": row.response_date,
                    "status": row.status,
                    "origin": row.origin,
                },
            )
        latest_sync = self._latest_sync_log("reviews")
        return batch.finish(record_counts={"reviews": len(rows)}, metadata={"latest_sync": latest_sync})

    def _materialize_hostaway_calendar(self, run: DataIngestionRun) -> MaterializationResult:
        batch = FactBatch(self, run, "hostaway_calendar", {"calendar_day"})
        rows = self.session.query(CalendarSnapshot).order_by(CalendarSnapshot.snapshot_date, CalendarSnapshot.listing_id, CalendarSnapshot.calendar_date).all()
        for row in rows:
            batch.upsert(
                fact_type="calendar_day",
                grain="listing_day",
                source_table="brain.calendar_snapshots",
                source_id=row.calendar_snapshot_id,
                listing_id=row.listing_id,
                occurred_at=row.created_at,
                effective_start=row.calendar_date,
                effective_end=row.calendar_date,
                numeric_value=row.price,
                text_value=row.status,
                payload={
                    "calendar_snapshot_id": row.calendar_snapshot_id,
                    "listing_id": row.listing_id,
                    "calendar_date": row.calendar_date,
                    "snapshot_date": row.snapshot_date,
                    "is_available": row.is_available,
                    "status": row.status,
                    "price": row.price,
                    "minimum_stay": row.minimum_stay,
                    "maximum_stay": row.maximum_stay,
                    "run_id": row.run_id,
                },
            )
        return batch.finish(record_counts={"calendar_snapshots": len(rows)})

    def _materialize_pricelabs(self, run: DataIngestionRun) -> MaterializationResult:
        batch = FactBatch(
            self,
            run,
            "pricelabs",
            {"pricing_context", "pricelabs_daily_price", "pricelabs_performance_metric"},
        )
        rows = _latest_pricelabs_snapshot_rows(self.session.query(PriceLabsSnapshot).all())
        for row in rows:
            raw_payload = row.raw_payload or {}
            prices_payload = raw_payload.get("prices") if isinstance(raw_payload, dict) and "prices" in raw_payload else raw_payload
            metrics_payload = raw_payload.get("metrics") if isinstance(raw_payload, dict) else None
            source_statuses = raw_payload.get("source_statuses") if isinstance(raw_payload, dict) else None
            daily_price_rows = _pricelabs_daily_price_rows(prices_payload)
            batch.upsert(
                fact_type="pricing_context",
                grain="listing_snapshot",
                source_table="brain.pricelabs_snapshots",
                source_id=row.pricelabs_snapshot_id,
                listing_id=row.listing_id,
                occurred_at=row.created_at,
                effective_start=row.snapshot_date,
                effective_end=row.snapshot_date,
                numeric_value=row.confidence,
                text_value=row.status,
                payload={
                    "pricelabs_snapshot_id": row.pricelabs_snapshot_id,
                    "listing_id": row.listing_id,
                    "external_listing_id": row.external_listing_id,
                    "snapshot_date": row.snapshot_date,
                    "status": row.status,
                    "confidence": row.confidence,
                    "error_message": row.error_message,
                    "source_statuses": source_statuses,
                    "last_refreshed_at": _dict_get(prices_payload, "last_refreshed_at"),
                    "currency": _dict_get(prices_payload, "currency"),
                    "los_pricing": _dict_get(prices_payload, "los_pricing"),
                    "payload": row.raw_payload,
                },
                confidence=row.confidence or 0.0,
            )
            for day in daily_price_rows:
                price_date = _parse_date(day.get("date"))
                if not price_date:
                    continue
                price_value, price_source = _pricelabs_daily_price_value(day)
                batch.upsert(
                    fact_type="pricelabs_daily_price",
                    grain="listing_day",
                    source_table="brain.pricelabs_snapshots",
                    source_id=f"{row.pricelabs_snapshot_id}:{price_date.isoformat()}",
                    listing_id=row.listing_id,
                    occurred_at=row.created_at,
                    effective_start=price_date,
                    effective_end=price_date,
                    numeric_value=price_value,
                    text_value=day.get("demand_desc") or day.get("booking_status"),
                    payload={
                        "pricelabs_snapshot_id": row.pricelabs_snapshot_id,
                        "listing_id": row.listing_id,
                        "snapshot_date": row.snapshot_date,
                        "last_refreshed_at": _dict_get(prices_payload, "last_refreshed_at"),
                        "currency": _dict_get(prices_payload, "currency"),
                        "date": price_date,
                        "price": day.get("price"),
                        "user_price": day.get("user_price"),
                        "uncustomized_price": day.get("uncustomized_price"),
                        "effective_price": price_value,
                        "effective_price_source": price_source,
                        "min_stay": day.get("min_stay"),
                        "booking_status": day.get("booking_status"),
                        "booking_status_stly": day.get("booking_status_STLY"),
                        "adr": day.get("ADR"),
                        "adr_stly": day.get("ADR_STLY"),
                        "unbookable": day.get("unbookable"),
                        "booked_date": day.get("booked_date"),
                        "booked_date_stly": day.get("booked_date_STLY"),
                        "weekly_discount": day.get("weekly_discount"),
                        "monthly_discount": day.get("monthly_discount"),
                        "check_in": day.get("check_in"),
                        "check_out": day.get("check_out"),
                        "demand_desc": day.get("demand_desc"),
                        "demand_color": day.get("demand_color"),
                        "market_factors": day.get("market_factors"),
                        "pricing_customizations": day.get("pricing_customizations"),
                        "thresholds": day.get("thresholds"),
                        "reason": day.get("reason"),
                    },
                    confidence=row.confidence or 0.0,
                )
            for metric in _pricelabs_daily_booking_metric_rows(daily_price_rows):
                metric_date = _parse_date(metric.get("date"))
                if not metric_date:
                    continue
                batch.upsert(
                    fact_type="pricelabs_performance_metric",
                    grain="listing_day_metric",
                    source_table="brain.pricelabs_snapshots",
                    source_id=f"{row.pricelabs_snapshot_id}:daily_booking:{metric_date.isoformat()}",
                    listing_id=row.listing_id,
                    occurred_at=row.created_at,
                    effective_start=metric_date,
                    effective_end=metric_date,
                    numeric_value=metric.get("value"),
                    text_value=metric.get("booking_status") or metric["metric"],
                    payload={
                        "pricelabs_snapshot_id": row.pricelabs_snapshot_id,
                        "listing_id": row.listing_id,
                        "snapshot_date": row.snapshot_date,
                        "source_endpoint": "listing_prices",
                        **metric,
                    },
                    confidence=row.confidence or 0.0,
                )
            for metric in _pricelabs_metric_rows(metrics_payload):
                batch.upsert(
                    fact_type="pricelabs_performance_metric",
                    grain="listing_metric",
                    source_table="brain.pricelabs_snapshots",
                    source_id=f"{row.pricelabs_snapshot_id}:{metric['level']}:{metric['metric']}:{metric['window']}",
                    listing_id=row.listing_id,
                    occurred_at=row.created_at,
                    effective_start=row.snapshot_date,
                    effective_end=row.snapshot_date,
                    numeric_value=metric.get("value"),
                    text_value=str(metric.get("raw_value")) if metric.get("value") is None and metric.get("raw_value") is not None else metric["metric"],
                    payload={
                        "pricelabs_snapshot_id": row.pricelabs_snapshot_id,
                        "listing_id": row.listing_id,
                        "snapshot_date": row.snapshot_date,
                        "level": metric["level"],
                        "metric": metric["metric"],
                        "window": metric["window"],
                        "value": metric.get("value"),
                        "raw_value": metric.get("raw_value"),
                    },
                    confidence=row.confidence or 0.0,
                )
        return batch.finish(record_counts={"pricelabs_snapshots": len(rows)})

    def _materialize_booking_health(self, run: DataIngestionRun) -> MaterializationResult:
        batch = FactBatch(self, run, "booking_health", {"booking_health_analysis", "booking_health_horizon"})
        analyses = self.session.query(BookingHealthAnalysis).order_by(
            BookingHealthAnalysis.snapshot_date,
            BookingHealthAnalysis.listing_id,
        ).all()
        for row in analyses:
            batch.upsert(
                fact_type="booking_health_analysis",
                grain="listing_snapshot",
                source_table="brain.booking_health_analyses",
                source_id=row.booking_health_analysis_id,
                portfolio_id=row.portfolio_id,
                listing_id=row.listing_id,
                occurred_at=row.updated_at or row.created_at,
                effective_start=row.snapshot_date,
                effective_end=row.snapshot_date,
                numeric_value=row.confidence,
                text_value=row.opinion,
                payload={
                    "booking_health_analysis_id": row.booking_health_analysis_id,
                    "run_id": row.run_id,
                    "portfolio_id": row.portfolio_id,
                    "listing_id": row.listing_id,
                    "listing_name": row.listing_name,
                    "snapshot_date": row.snapshot_date,
                    "severity": row.severity,
                    "confidence": row.confidence,
                    "booking_pattern": row.booking_pattern,
                    "pricelabs_opinion": row.pricelabs_opinion,
                    "airbnb_page_opinion": row.airbnb_page_opinion,
                    "opinion": row.opinion,
                    "action_items": row.action_items,
                    "horizons": row.horizons,
                    "source_statuses": row.source_statuses,
                },
                confidence=row.confidence or 0.0,
            )

        snapshots = self.session.query(BookingHealthSnapshot).order_by(
            BookingHealthSnapshot.snapshot_date,
            BookingHealthSnapshot.listing_id,
            BookingHealthSnapshot.horizon_days,
        ).all()
        for row in snapshots:
            batch.upsert(
                fact_type="booking_health_horizon",
                grain="listing_horizon",
                source_table="brain.booking_health_snapshots",
                source_id=row.booking_health_snapshot_id,
                portfolio_id=row.portfolio_id,
                listing_id=row.listing_id,
                occurred_at=row.created_at,
                effective_start=row.snapshot_date,
                effective_end=row.snapshot_date,
                numeric_value=row.occupancy_rate,
                text_value=row.diagnosis,
                payload={
                    "booking_health_snapshot_id": row.booking_health_snapshot_id,
                    "run_id": row.run_id,
                    "portfolio_id": row.portfolio_id,
                    "listing_id": row.listing_id,
                    "horizon_days": row.horizon_days,
                    "snapshot_date": row.snapshot_date,
                    "occupancy_rate": row.occupancy_rate,
                    "booked_nights": row.booked_nights,
                    "available_nights": row.available_nights,
                    "expected_occupancy_rate": row.expected_occupancy_rate,
                    "diagnosis": row.diagnosis,
                    "confidence": row.confidence,
                    "recommended_action": row.recommended_action,
                    "raw_metrics": row.raw_metrics,
                },
                confidence=row.confidence or 0.0,
            )
        return batch.finish(record_counts={"booking_health_analyses": len(analyses), "booking_health_snapshots": len(snapshots)})

    def _materialize_whatsapp(self, run: DataIngestionRun) -> MaterializationResult:
        batch = FactBatch(self, run, "whatsapp", {"whatsapp_message", "team_thread_memory"})
        messages = self.session.query(WhatsAppMessage).order_by(WhatsAppMessage.received_at, WhatsAppMessage.whatsapp_message_id).all()
        for row in messages:
            batch.upsert(
                fact_type="whatsapp_message",
                grain="team_message",
                source_table="brain.whatsapp_messages",
                source_id=row.whatsapp_message_id,
                occurred_at=row.received_at,
                text_value=row.body,
                payload={
                    "whatsapp_message_id": row.whatsapp_message_id,
                    "whatsapp_thread_id": row.whatsapp_thread_id,
                    "provider": row.provider,
                    "provider_message_id": row.provider_message_id,
                    "sender_name": row.sender_name,
                    "direction": row.direction,
                    "body": row.body,
                    "media_urls": row.media_urls,
                    "provider_status": row.provider_status,
                    "received_at": row.received_at,
                },
                confidence=0.9,
            )

        memories = self.session.query(WhatsAppGroupMemory).order_by(
            WhatsAppGroupMemory.memory_date,
            WhatsAppGroupMemory.whatsapp_thread_id,
        ).all()
        for row in memories:
            batch.upsert(
                fact_type="team_thread_memory",
                grain="team_thread_window",
                source_table="brain.whatsapp_group_memories",
                source_id=row.whatsapp_group_memory_id,
                portfolio_id=row.portfolio_id,
                occurred_at=row.last_message_at or row.updated_at,
                effective_start=row.window_start_at,
                effective_end=row.window_end_at,
                numeric_value=row.message_count,
                text_value=row.summary,
                payload={
                    "whatsapp_group_memory_id": row.whatsapp_group_memory_id,
                    "run_id": row.run_id,
                    "whatsapp_thread_id": row.whatsapp_thread_id,
                    "portfolio_id": row.portfolio_id,
                    "provider": row.provider,
                    "provider_thread_id": row.provider_thread_id,
                    "group_name": row.group_name,
                    "group_kind": row.group_kind,
                    "window_days": row.window_days,
                    "memory_date": row.memory_date,
                    "message_count": row.message_count,
                    "participant_count": row.participant_count,
                    "last_message_at": row.last_message_at,
                    "status": row.status,
                    "summary": row.summary,
                    "open_loop_summary": row.open_loop_summary,
                    "risk_summary": row.risk_summary,
                    "decision_summary": row.decision_summary,
                    "cleaning_maintenance_summary": row.cleaning_maintenance_summary,
                    "source_metadata": row.source_metadata,
                },
                confidence=0.8,
            )
        fallback_count = 0
        if not memories:
            fallback_count = self._materialize_whatsapp_memory_fallback(batch, messages)
        return batch.finish(
            record_counts={
                "whatsapp_messages": len(messages),
                "whatsapp_group_memories": len(memories),
                "fallback_team_thread_memories": fallback_count,
            }
        )

    def _materialize_whatsapp_memory_fallback(self, batch: FactBatch, messages: list[WhatsAppMessage], *, window_days: int = 60) -> int:
        if not messages:
            return 0
        memory_date = self.now_fn().date()
        window_end = datetime.combine(memory_date + timedelta(days=1), time.min)
        window_start = window_end - timedelta(days=window_days)
        grouped: dict[int, list[WhatsAppMessage]] = {}
        for message in messages:
            if message.received_at and message.received_at < window_start:
                continue
            grouped.setdefault(int(message.whatsapp_thread_id), []).append(message)
        for thread_id, rows in sorted(grouped.items()):
            ordered = sorted(rows, key=lambda item: item.received_at or datetime.min)
            latest = ordered[-1]
            participants = sorted({message.sender_name or message.from_number or "Unknown" for message in ordered})
            text_values = [message.body or "" for message in ordered]
            attention_count = sum(1 for value in text_values if _contains_attention_language(value))
            decision_count = sum(1 for value in text_values if _contains_decision_language(value))
            thread = latest.thread
            group_name = getattr(thread, "display_name", None) or getattr(thread, "provider_thread_id", None) or f"WhatsApp thread {thread_id}"
            status = "watch" if attention_count else "ok"
            batch.upsert(
                fact_type="team_thread_memory",
                grain="team_thread_window",
                source_table="brain.whatsapp_messages",
                source_id=f"fallback:{thread_id}:{memory_date.isoformat()}:{window_days}",
                portfolio_id=getattr(thread, "portfolio_id", None),
                occurred_at=latest.received_at,
                effective_start=window_start,
                effective_end=window_end,
                numeric_value=len(ordered),
                text_value=_fallback_team_thread_summary(group_name, len(ordered), attention_count, decision_count),
                payload={
                    "source": "fallback_from_whatsapp_messages",
                    "whatsapp_thread_id": thread_id,
                    "provider": latest.provider,
                    "provider_thread_id": getattr(thread, "provider_thread_id", None),
                    "group_name": group_name,
                    "window_days": window_days,
                    "memory_date": memory_date,
                    "message_count": len(ordered),
                    "participant_count": len(participants),
                    "participants": participants[:20],
                    "attention_count": attention_count,
                    "decision_count": decision_count,
                    "latest_messages": [
                        {
                            "whatsapp_message_id": message.whatsapp_message_id,
                            "sender_name": message.sender_name,
                            "received_at": message.received_at,
                            "body": _truncate_text(message.body, 240),
                        }
                        for message in ordered[-12:]
                    ],
                },
                confidence=0.6,
            )
        return len(grouped)

    def _materialize_guest_stay_memory(self, run: DataIngestionRun) -> MaterializationResult:
        batch = FactBatch(self, run, "guest_stay_memory", {"guest_stay_memory"})
        rows = self.session.query(GuestStayMemory).order_by(GuestStayMemory.memory_date, GuestStayMemory.reservation_id).all()
        for row in rows:
            batch.upsert(
                fact_type="guest_stay_memory",
                grain="reservation_window",
                source_table="brain.guest_stay_memories",
                source_id=row.guest_stay_memory_id,
                portfolio_id=row.portfolio_id,
                listing_id=row.listing_id,
                reservation_id=row.reservation_id,
                guest_id=row.guest_id,
                occurred_at=row.last_message_at or row.updated_at,
                effective_start=row.window_start_at,
                effective_end=row.window_end_at,
                numeric_value=row.risk_score,
                text_value=row.summary,
                payload={
                    "guest_stay_memory_id": row.guest_stay_memory_id,
                    "run_id": row.run_id,
                    "portfolio_id": row.portfolio_id,
                    "listing_id": row.listing_id,
                    "reservation_id": row.reservation_id,
                    "guest_id": row.guest_id,
                    "guest_name": row.guest_name,
                    "channel_name": row.channel_name,
                    "arrival_date": row.arrival_date,
                    "departure_date": row.departure_date,
                    "reservation_status": row.reservation_status,
                    "memory_date": row.memory_date,
                    "message_count": row.message_count,
                    "incoming_count": row.incoming_count,
                    "last_message_at": row.last_message_at,
                    "review_id": row.review_id,
                    "review_rating": row.review_rating,
                    "review_date": row.review_date,
                    "status": row.status,
                    "risk_level": row.risk_level,
                    "risk_score": row.risk_score,
                    "summary": row.summary,
                    "risk_summary": row.risk_summary,
                    "latest_guest_issue": row.latest_guest_issue,
                    "resolution_summary": row.resolution_summary,
                    "suggested_action": row.suggested_action,
                    "source_metadata": row.source_metadata,
                },
                confidence=0.8,
            )
        fallback_count = 0
        if not rows:
            fallback_count = self._materialize_guest_stay_memory_fallback(batch)
        return batch.finish(record_counts={"guest_stay_memories": len(rows), "fallback_guest_stay_memories": fallback_count})

    def _materialize_guest_stay_memory_fallback(self, batch: FactBatch, *, window_days: int = 365) -> int:
        memory_date = self.now_fn().date()
        window_end = datetime.combine(memory_date + timedelta(days=1), time.min)
        window_start = window_end - timedelta(days=window_days)
        reservation_rows = self.session.query(BusinessFact).filter(
            BusinessFact.status == "active",
            BusinessFact.fact_type == "reservation_stay",
        ).all()
        reservations_by_id = {
            int(row.reservation_id): row
            for row in reservation_rows
            if row.reservation_id is not None
        }
        message_rows = self.session.query(BusinessFact).filter(
            BusinessFact.status == "active",
            BusinessFact.fact_type == "guest_message",
            BusinessFact.occurred_at >= window_start,
            BusinessFact.reservation_id.isnot(None),
        ).all()
        review_rows = self.session.query(BusinessFact).filter(
            BusinessFact.status == "active",
            BusinessFact.fact_type == "guest_review",
            BusinessFact.occurred_at >= window_start,
            BusinessFact.reservation_id.isnot(None),
        ).all()
        messages_by_reservation = _group_facts_by_reservation(message_rows)
        reviews_by_reservation = _group_facts_by_reservation(review_rows)
        reservation_ids = sorted(set(messages_by_reservation) | set(reviews_by_reservation))
        for reservation_id in reservation_ids:
            reservation = reservations_by_id.get(reservation_id)
            messages = messages_by_reservation.get(reservation_id, [])
            reviews = reviews_by_reservation.get(reservation_id, [])
            if not reservation and not messages and not reviews:
                continue
            listing_id = (reservation.listing_id if reservation else None) or (messages[0].listing_id if messages else None) or (reviews[0].listing_id if reviews else None)
            guest_id = (reservation.guest_id if reservation else None) or (messages[0].guest_id if messages else None) or (reviews[0].guest_id if reviews else None)
            payload = _fallback_guest_stay_memory_payload(reservation=reservation, messages=messages, reviews=reviews, window_days=window_days)
            occurred_at = payload.get("last_activity_at") or (reservation.occurred_at if reservation else None) or window_end
            batch.upsert(
                fact_type="guest_stay_memory",
                grain="reservation_window",
                source_table="brain.business_facts",
                source_id=f"fallback:{reservation_id}:{memory_date.isoformat()}:{window_days}",
                portfolio_id=reservation.portfolio_id if reservation else None,
                listing_id=listing_id,
                reservation_id=reservation_id,
                guest_id=guest_id,
                occurred_at=occurred_at,
                effective_start=payload.get("arrival_date") or window_start,
                effective_end=payload.get("departure_date") or occurred_at,
                numeric_value=payload["risk_score"],
                text_value=payload["summary"],
                payload=payload,
                confidence=payload["confidence"],
            )
        return len(reservation_ids)

    def _materialize_bookkeeping_revenue(self, run: DataIngestionRun) -> MaterializationResult:
        models = self._bookkeeping_models()
        if not models or not self._table_exists(models["BookkeepingRevenueItem"]):
            return MaterializationResult(
                status="missing",
                error_message="Bookkeeping revenue table is not available yet.",
                metadata={"reason": "missing_table"},
            )
        RevenueItem = models["BookkeepingRevenueItem"]
        batch = FactBatch(self, run, "bookkeeping_revenue", {"bookkeeping_revenue"})
        rows = self.main_session.query(RevenueItem).order_by(RevenueItem.bookkeeping_revenue_item_id).all()
        for row in rows:
            listing_id = getattr(getattr(row, "listing_mapping", None), "listing_id", None)
            amount = _first_number(row.paid_out_amount, row.gross_amount, row.commission_amount)
            batch.upsert(
                fact_type="bookkeeping_revenue",
                grain="financial_transaction",
                source_table="bookkeeping.bookkeeping_revenue_items",
                source_id=row.bookkeeping_revenue_item_id,
                listing_id=listing_id,
                occurred_at=row.transaction_date or row.booking_date or row.created_at,
                effective_start=row.start_date,
                effective_end=row.end_date,
                numeric_value=amount,
                text_value=row.guest_name or row.reservation_identifier,
                payload={
                    "bookkeeping_revenue_item_id": row.bookkeeping_revenue_item_id,
                    "period_id": row.period_id,
                    "upload_id": row.upload_id,
                    "listing_mapping_id": row.listing_mapping_id,
                    "listing_id": listing_id,
                    "source": row.source,
                    "reservation_identifier": row.reservation_identifier,
                    "confirmation_code": row.confirmation_code,
                    "guest_name": row.guest_name,
                    "property_code": row.property_code,
                    "raw_listing_name": row.raw_listing_name,
                    "transaction_type": row.transaction_type,
                    "currency": row.currency,
                    "transaction_date": row.transaction_date,
                    "booking_date": row.booking_date,
                    "start_date": row.start_date,
                    "end_date": row.end_date,
                    "nights": row.nights,
                    "gross_amount": row.gross_amount,
                    "paid_out_amount": row.paid_out_amount,
                    "commission_amount": row.commission_amount,
                    "hostaway_fee_amount": row.hostaway_fee_amount,
                    "stripe_fee_amount": row.stripe_fee_amount,
                    "cleaning_fee_amount": row.cleaning_fee_amount,
                    "tax_amount": row.tax_amount,
                    "refund_amount": row.refund_amount,
                    "needs_review": row.needs_review,
                    "review_reason": row.review_reason,
                    "normalized_data": row.normalized_data,
                },
                confidence=0.85 if row.needs_review else 0.95,
            )
        return batch.finish(record_counts={"bookkeeping_revenue_items": len(rows)})

    def _materialize_bookkeeping_expenses(self, run: DataIngestionRun) -> MaterializationResult:
        models = self._bookkeeping_models()
        if not models or not self._table_exists(models["BookkeepingExpenseItem"]):
            return MaterializationResult(
                status="missing",
                error_message="Bookkeeping expense table is not available yet.",
                metadata={"reason": "missing_table"},
            )
        ExpenseItem = models["BookkeepingExpenseItem"]
        batch = FactBatch(self, run, "bookkeeping_expenses", {"bookkeeping_expense"})
        rows = self.main_session.query(ExpenseItem).order_by(ExpenseItem.bookkeeping_expense_item_id).all()
        for row in rows:
            total = row.effective_total() if hasattr(row, "effective_total") else _first_number(row.total, row.amount, row.subtotal)
            batch.upsert(
                fact_type="bookkeeping_expense",
                grain="financial_transaction",
                source_table="bookkeeping.bookkeeping_expense_items",
                source_id=row.bookkeeping_expense_item_id,
                occurred_at=row.service_date or row.payment_date or row.created_at,
                numeric_value=total,
                text_value=row.item_name or row.vendor or row.description,
                payload={
                    "bookkeeping_expense_item_id": row.bookkeeping_expense_item_id,
                    "period_id": row.period_id,
                    "upload_id": row.upload_id,
                    "category": row.category,
                    "item_name": row.item_name,
                    "vendor": row.vendor,
                    "property_code": row.property_code,
                    "scope": row.scope,
                    "description": row.description,
                    "amount": row.amount,
                    "service_date": row.service_date,
                    "payment_date": row.payment_date,
                    "payment_method": row.payment_method,
                    "account_holder": row.account_holder,
                    "purchase_type": row.purchase_type,
                    "store_name": row.store_name,
                    "subtotal": row.subtotal,
                    "discount": row.discount,
                    "shipping": row.shipping,
                    "tax": row.tax,
                    "total": row.total,
                    "effective_total": total,
                    "needs_review": row.needs_review,
                    "review_reason": row.review_reason,
                    "extraction_data": row.extraction_data,
                },
                confidence=0.85 if row.needs_review else 0.95,
            )
        return batch.finish(record_counts={"bookkeeping_expense_items": len(rows)})

    def _upsert_fact(
        self,
        *,
        ingestion_run_id: int,
        fact_type: str,
        source_key: str,
        source_table: str,
        source_id: Any,
        grain: str,
        payload: dict[str, Any],
        portfolio_id: int | None = None,
        listing_id: int | None = None,
        reservation_id: int | None = None,
        guest_id: int | None = None,
        occurred_at: Any = None,
        effective_start: Any = None,
        effective_end: Any = None,
        numeric_value: Any = None,
        text_value: str | None = None,
        confidence: float = 1.0,
        cached_fact: BusinessFact | None = None,
    ) -> tuple[str, BusinessFact]:
        fact_key = build_fact_key(fact_type, source_key, source_id)
        safe_payload = as_json_safe(payload or {})
        fact_hash = stable_hash(safe_payload)
        now = self.now_fn()
        fields = {
            "ingestion_run_id": ingestion_run_id,
            "fact_type": fact_type,
            "grain": grain,
            "source_key": source_key,
            "source_table": source_table,
            "source_id": str(source_id),
            "portfolio_id": _coerce_int(portfolio_id),
            "listing_id": _coerce_int(listing_id),
            "reservation_id": _coerce_int(reservation_id),
            "guest_id": _coerce_int(guest_id),
            "occurred_at": _as_datetime(occurred_at),
            "effective_start": _as_datetime(effective_start),
            "effective_end": _as_datetime(effective_end),
            "numeric_value": _coerce_float(numeric_value),
            "text_value": text_value,
            "confidence": _coerce_float(confidence) if confidence is not None else 1.0,
            "status": "active",
            "fact_payload": safe_payload,
            "fact_hash": fact_hash,
        }
        fact = cached_fact
        if fact is None:
            with self.session.no_autoflush:
                fact = self.session.query(BusinessFact).filter(BusinessFact.fact_key == fact_key).first()
        if not fact:
            fact = BusinessFact(fact_key=fact_key, created_at=now, updated_at=now, **fields)
            self.session.add(fact)
            return "created", fact

        changed = fact.fact_hash != fact_hash or any(getattr(fact, key) != value for key, value in fields.items() if key not in {"ingestion_run_id"})
        for key, value in fields.items():
            setattr(fact, key, value)
        fact.updated_at = now
        return ("updated" if changed else "unchanged"), fact

    def _withdraw_missing_facts(
        self,
        *,
        ingestion_run_id: int,
        source_key: str,
        fact_types: set[str],
        active_fact_keys: set[str],
    ) -> int:
        withdrawn = 0
        query = self.session.query(BusinessFact).filter(
            BusinessFact.source_key == source_key,
            BusinessFact.fact_type.in_(fact_types),
            BusinessFact.status == "active",
        )
        now = self.now_fn()
        for fact in query.yield_per(1000):
            if fact.fact_key in active_fact_keys:
                continue
            fact.status = "withdrawn"
            fact.ingestion_run_id = ingestion_run_id
            fact.updated_at = now
            withdrawn += 1
        return withdrawn

    def _upsert_metric_snapshot(
        self,
        *,
        metric_key: str,
        metric_name: str,
        category: str,
        grain: str,
        metric_date: date,
        listing_portfolios: dict[int, int],
        horizon_days: int | None = None,
        portfolio_id: int | None = None,
        listing_id: int | None = None,
        numeric_value: Any = None,
        text_value: str | None = None,
        status: str = "ok",
        confidence: float = 1.0,
        source_keys: list[str] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> str:
        now = self.now_fn()
        listing_id = _coerce_int(listing_id)
        portfolio_id = _coerce_int(portfolio_id) or (listing_portfolios.get(listing_id) if listing_id else None)
        safe_payload = as_json_safe(payload or {})
        fields = {
            "metric_name": metric_name,
            "category": category,
            "grain": grain,
            "metric_date": metric_date,
            "horizon_days": _coerce_int(horizon_days),
            "portfolio_id": portfolio_id,
            "listing_id": listing_id,
            "numeric_value": _coerce_float(numeric_value),
            "text_value": text_value,
            "status": status or "ok",
            "confidence": _coerce_float(confidence) if confidence is not None else 1.0,
            "source_keys": as_json_safe(source_keys or []),
            "metric_payload": safe_payload,
            "computed_at": now,
        }
        snapshot = self.session.query(BusinessMetricSnapshot).filter(
            BusinessMetricSnapshot.metric_key == metric_key
        ).first()
        if not snapshot:
            self.session.add(BusinessMetricSnapshot(metric_key=metric_key, created_at=now, updated_at=now, **fields))
            return "created"
        changed = any(getattr(snapshot, key) != value for key, value in fields.items() if key != "computed_at")
        for key, value in fields.items():
            setattr(snapshot, key, value)
        snapshot.updated_at = now
        return "updated" if changed else "unchanged"

    def _withdraw_missing_metric_snapshots(self, *, metric_date: date, active_metric_keys: set[str]) -> int:
        withdrawn = 0
        now = self.now_fn()
        rows = self.session.query(BusinessMetricSnapshot).filter(
            BusinessMetricSnapshot.metric_date == metric_date,
            BusinessMetricSnapshot.status != "withdrawn",
        ).all()
        for row in rows:
            if row.metric_key in active_metric_keys:
                continue
            row.status = "withdrawn"
            row.updated_at = now
            withdrawn += 1
        return withdrawn

    def _latest_sync_log(self, sync_type: str) -> dict[str, Any] | None:
        try:
            row = self.main_session.query(SyncLog).filter(SyncLog.sync_type == sync_type).order_by(SyncLog.completed_at.desc().nullslast()).first()
        except SQLAlchemyError:
            self.main_session.rollback()
            return None
        if not row:
            return None
        return {
            "sync_id": row.sync_id,
            "sync_run_id": row.sync_run_id,
            "sync_type": row.sync_type,
            "sync_mode": row.sync_mode,
            "status": row.status,
            "records_processed": row.records_processed,
            "records_created": row.records_created,
            "records_updated": row.records_updated,
            "started_at": row.started_at,
            "completed_at": row.completed_at,
            "duration_seconds": row.duration_seconds,
        }

    def _bookkeeping_models(self) -> dict[str, Any] | None:
        try:
            from dashboard.bookkeeping.models import BookkeepingExpenseItem, BookkeepingRevenueItem

            return {
                "BookkeepingExpenseItem": BookkeepingExpenseItem,
                "BookkeepingRevenueItem": BookkeepingRevenueItem,
            }
        except Exception as exc:
            logger.info("Bookkeeping models are not available for aggregation: %s", exc)
            return None

    def _table_exists(self, model: Any) -> bool:
        try:
            bind = self.main_session.get_bind()
            table = model.__table__
            return inspect(bind).has_table(table.name, schema=table.schema)
        except Exception as exc:
            logger.info("Could not inspect table availability for %s: %s", model, exc)
            return False

    def _run_dict(self, run: DataIngestionRun | None) -> dict[str, Any]:
        if not run:
            return {}
        return {
            "data_ingestion_run_id": run.data_ingestion_run_id,
            "brain_run_id": run.brain_run_id,
            "source_key": run.source_key,
            "mode": run.mode,
            "status": run.status,
            "records_seen": run.records_seen,
            "facts_written": run.facts_written,
            "facts_created": run.facts_created,
            "facts_updated": run.facts_updated,
            "facts_unchanged": run.facts_unchanged,
            "facts_withdrawn": run.facts_withdrawn,
            "started_at": run.started_at.isoformat() if run.started_at else None,
            "completed_at": run.completed_at.isoformat() if run.completed_at else None,
            "record_counts": run.record_counts or {},
            "error_message": run.error_message,
        }


class FactBatch:
    """Tracks fact writes and stale withdrawals for one source run."""

    def __init__(
        self,
        aggregator: BrainDataAggregator,
        run: DataIngestionRun,
        source_key: str,
        fact_types: set[str],
        *,
        flush_size: int = FACT_BATCH_FLUSH_SIZE,
    ):
        self.aggregator = aggregator
        self.run = run
        self.source_key = source_key
        self.fact_types = fact_types
        self.flush_size = max(int(flush_size), 1)
        self.active_fact_keys: set[str] = set()
        self.records_seen = 0
        self.facts_created = 0
        self.facts_updated = 0
        self.facts_unchanged = 0
        self.fact_cache: dict[str, BusinessFact] = {}
        self.pending_upserts = 0

    def upsert(self, **kwargs):
        self.records_seen += 1
        fact_key = build_fact_key(kwargs["fact_type"], self.source_key, kwargs["source_id"])
        self.active_fact_keys.add(fact_key)
        outcome, fact = self.aggregator._upsert_fact(
            ingestion_run_id=self.run.data_ingestion_run_id,
            source_key=self.source_key,
            cached_fact=self.fact_cache.get(fact_key),
            **kwargs,
        )
        self.fact_cache[fact_key] = fact
        if outcome == "created":
            self.facts_created += 1
        elif outcome == "updated":
            self.facts_updated += 1
        else:
            self.facts_unchanged += 1
        self.pending_upserts += 1
        if self.pending_upserts >= self.flush_size:
            self._flush_fact_cache()

    def _flush_fact_cache(self):
        """Flush and detach facts so large source runs have bounded memory use."""
        if not self.pending_upserts:
            return
        session = self.aggregator.session
        session.flush()
        detached_ids: set[int] = set()
        for fact in self.fact_cache.values():
            fact_identity = id(fact)
            if fact_identity in detached_ids:
                continue
            detached_ids.add(fact_identity)
            if inspect(fact).session is session:
                session.expunge(fact)
        self.fact_cache.clear()
        self.pending_upserts = 0

    def finish(
        self,
        *,
        record_counts: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> MaterializationResult:
        self._flush_fact_cache()
        facts_withdrawn = self.aggregator._withdraw_missing_facts(
            ingestion_run_id=self.run.data_ingestion_run_id,
            source_key=self.source_key,
            fact_types=self.fact_types,
            active_fact_keys=self.active_fact_keys,
        )
        self.aggregator.session.flush()
        return MaterializationResult(
            records_seen=self.records_seen,
            facts_created=self.facts_created,
            facts_updated=self.facts_updated,
            facts_unchanged=self.facts_unchanged,
            facts_withdrawn=facts_withdrawn,
            record_counts=record_counts or {},
            metadata=metadata or {},
        )


def _key_part(value: Any) -> str:
    return str(value).strip().lower().replace(" ", "_")


def _attr(source: Any, name: str, default: Any = None) -> Any:
    if isinstance(source, dict):
        return source.get(name, default)
    return getattr(source, name, default)


def _iso(value: Any) -> str | None:
    value = _as_datetime(value)
    return value.isoformat() if value else None


def _as_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, time.min)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


def _parse_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def _coerce_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _coerce_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    if isinstance(value, Decimal):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _first_number(*values: Any) -> float | None:
    for value in values:
        number = _coerce_float(value)
        if number is not None:
            return number
    return None


def _first_positive_number(*values: Any) -> tuple[float | None, str | None]:
    for source, value in values:
        number = _coerce_float(value)
        if number is not None and number > 0:
            return number, source
    return None, None


def _dict_get(value: Any, key: str, default: Any = None) -> Any:
    return value.get(key, default) if isinstance(value, dict) else default


def _pricelabs_daily_price_rows(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    rows = payload.get("data")
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def _latest_pricelabs_snapshot_rows(rows: list[Any]) -> list[Any]:
    latest_by_listing: dict[int, Any] = {}
    for row in rows:
        listing_id = _coerce_int(getattr(row, "listing_id", None))
        if not listing_id:
            continue
        current = latest_by_listing.get(listing_id)
        if current is None or _pricelabs_snapshot_rank(row) > _pricelabs_snapshot_rank(current):
            latest_by_listing[listing_id] = row
    return sorted(latest_by_listing.values(), key=lambda item: (getattr(item, "created_at", None) or datetime.min, getattr(item, "listing_id", 0) or 0))


def _latest_booking_health_horizon_facts(rows: list[Any]) -> list[Any]:
    latest_by_listing_horizon: dict[tuple[int, int], Any] = {}
    for row in rows:
        listing_id = _coerce_int(getattr(row, "listing_id", None))
        payload = getattr(row, "fact_payload", None) or {}
        horizon_days = _coerce_int(payload.get("horizon_days"))
        if not listing_id or not horizon_days:
            continue
        key = (listing_id, horizon_days)
        current = latest_by_listing_horizon.get(key)
        if current is None or _booking_health_fact_rank(row) > _booking_health_fact_rank(current):
            latest_by_listing_horizon[key] = row
    return sorted(
        latest_by_listing_horizon.values(),
        key=lambda item: (
            int(getattr(item, "listing_id", 0) or 0),
            int((getattr(item, "fact_payload", None) or {}).get("horizon_days") or 0),
        ),
    )


def _latest_fresh_booking_health_analysis_facts(
    rows: list[Any],
    horizon_facts_by_listing: dict[int, list[Any]],
) -> list[Any]:
    latest_by_listing: dict[int, Any] = {}
    for row in rows:
        listing_id = _coerce_int(getattr(row, "listing_id", None))
        if not listing_id:
            continue
        current = latest_by_listing.get(listing_id)
        if current is None or _booking_health_fact_rank(row) > _booking_health_fact_rank(current):
            latest_by_listing[listing_id] = row

    fresh_rows: list[Any] = []
    for listing_id, row in latest_by_listing.items():
        analysis_at = _as_datetime(getattr(row, "effective_start", None))
        latest_horizon_at = max(
            (
                _as_datetime(getattr(horizon, "effective_start", None)) or datetime.min
                for horizon in horizon_facts_by_listing.get(listing_id, [])
            ),
            default=datetime.min,
        )
        if analysis_at and latest_horizon_at != datetime.min and analysis_at < latest_horizon_at:
            continue
        fresh_rows.append(row)

    return sorted(fresh_rows, key=lambda item: int(getattr(item, "listing_id", 0) or 0))


def _latest_guest_stay_memory_facts(rows: list[Any]) -> list[Any]:
    latest_by_reservation: dict[int, Any] = {}
    for row in rows:
        reservation_id = _coerce_int(getattr(row, "reservation_id", None))
        if not reservation_id:
            continue
        current = latest_by_reservation.get(reservation_id)
        if current is None or _guest_stay_memory_fact_rank(row) > _guest_stay_memory_fact_rank(current):
            latest_by_reservation[reservation_id] = row
    return sorted(
        latest_by_reservation.values(),
        key=lambda item: (
            int(getattr(item, "listing_id", 0) or 0),
            int(getattr(item, "reservation_id", 0) or 0),
        ),
    )


def _guest_stay_memory_fact_rank(row: Any) -> tuple[date, datetime, int]:
    payload = getattr(row, "fact_payload", None) or {}
    return (
        _parse_date(payload.get("memory_date")) or date.min,
        getattr(row, "updated_at", None) or getattr(row, "created_at", None) or datetime.min,
        int(getattr(row, "business_fact_id", 0) or 0),
    )


def _booking_health_fact_rank(row: Any) -> tuple[datetime, datetime, int]:
    return (
        getattr(row, "effective_start", None) or datetime.min,
        getattr(row, "updated_at", None) or getattr(row, "created_at", None) or datetime.min,
        int(getattr(row, "business_fact_id", 0) or 0),
    )


def _pricelabs_snapshot_rank(row: Any) -> tuple[int, datetime, int]:
    raw_payload = getattr(row, "raw_payload", None) or {}
    prices_payload = raw_payload.get("prices") if isinstance(raw_payload, dict) and "prices" in raw_payload else raw_payload
    usable_price_rows = bool(_pricelabs_daily_price_rows(prices_payload))
    return (
        int(usable_price_rows),
        getattr(row, "created_at", None) or datetime.min,
        int(getattr(row, "pricelabs_snapshot_id", 0) or 0),
    )


def _calendar_fact_rank(row: Any) -> tuple[date, datetime, int]:
    payload = getattr(row, "fact_payload", None) or {}
    return (
        _parse_date(payload.get("snapshot_date")) or date.min,
        getattr(row, "updated_at", None) or getattr(row, "created_at", None) or datetime.min,
        int(getattr(row, "business_fact_id", 0) or 0),
    )


def _pricelabs_fact_is_unavailable(row: Any) -> bool:
    payload = getattr(row, "fact_payload", None) or {}
    booking_status = str(payload.get("booking_status") or "").strip().lower()
    return _truthy(payload.get("unbookable")) or booking_status.startswith("booked") or booking_status == "blocked"


def _pricelabs_holiday_events(row: Any) -> list[str]:
    payload = getattr(row, "fact_payload", None) or {}
    reason = payload.get("reason") if isinstance(payload.get("reason"), dict) else {}
    listing_info = reason.get("listing_info") if isinstance(reason.get("listing_info"), dict) else {}
    raw_events = listing_info.get("holiday_events") or payload.get("holiday_events")
    if not raw_events:
        return []
    values = raw_events if isinstance(raw_events, (list, tuple, set)) else str(raw_events).split(",")
    return sorted({str(value).strip() for value in values if str(value).strip()})


def _pricelabs_has_date_override(row: Any) -> bool:
    payload = getattr(row, "fact_payload", None) or {}
    reason = payload.get("reason") if isinstance(payload.get("reason"), dict) else {}
    customizations = reason.get("pricing_customizations") or payload.get("pricing_customizations") or []
    if isinstance(customizations, dict):
        entries = list(customizations.values())
    elif isinstance(customizations, (list, tuple, set)):
        entries = list(customizations)
    else:
        return False
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        key = str(entry.get("key") or "").strip().lower()
        title = str(entry.get("title") or "").strip().lower()
        if key == "date_specific_override" or "date override" in title:
            return True
    return False


def _pricelabs_daily_price_value(day: dict[str, Any]) -> tuple[float | None, str | None]:
    reason = day.get("reason") if isinstance(day.get("reason"), dict) else {}
    listing_info = reason.get("listing_info") if isinstance(reason.get("listing_info"), dict) else {}
    return _first_positive_number(
        ("price", day.get("price")),
        ("listing_info.price", listing_info.get("price")),
        ("user_price", day.get("user_price")),
        ("listing_info.customized_price", listing_info.get("customized_price")),
        ("uncustomized_price", day.get("uncustomized_price")),
        ("listing_info.uncustomized_price", listing_info.get("uncustomized_price")),
    )


def _pricelabs_daily_booking_metric_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    metrics: list[dict[str, Any]] = []
    for day in rows:
        price_date = _parse_date(day.get("date"))
        if not price_date:
            continue
        reason = day.get("reason") if isinstance(day.get("reason"), dict) else {}
        listing_info = reason.get("listing_info") if isinstance(reason.get("listing_info"), dict) else {}
        occupancy = _first_number(day.get("occupancy"), listing_info.get("occupancy"))
        adr = _first_number(day.get("ADR"), day.get("adr"), listing_info.get("ADR"))
        adr_stly = _first_number(day.get("ADR_STLY"), day.get("adr_stly"), listing_info.get("ADR_STLY"))
        num_bookings = _first_number(listing_info.get("num_bookings"))
        avg_los = _first_number(listing_info.get("avg_los"))
        booking_status = day.get("booking_status")
        booked_date = day.get("booked_date")
        if not any(value not in (None, "", "-1") for value in (occupancy, adr, adr_stly, num_bookings, avg_los, booking_status, booked_date)):
            continue
        metrics.append(
            {
                "metric": "daily_booking_pattern",
                "date": price_date,
                "value": _first_number(occupancy, adr, num_bookings),
                "occupancy": occupancy,
                "adr": adr,
                "adr_stly": adr_stly,
                "num_bookings": num_bookings,
                "avg_los": avg_los,
                "booking_status": booking_status,
                "booking_status_stly": day.get("booking_status_STLY"),
                "booked_date": booked_date,
                "booked_date_stly": day.get("booked_date_STLY"),
                "unbookable": day.get("unbookable"),
            }
        )
    return metrics


def _pricelabs_metric_rows(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    rows: list[dict[str, Any]] = []
    for level in ("listing_level", "market_level"):
        metrics = data.get(level) if isinstance(data, dict) else None
        if not isinstance(metrics, dict):
            continue
        for metric_name, metric_value in metrics.items():
            if isinstance(metric_value, dict):
                for window, value in metric_value.items():
                    rows.append(
                        {
                            "level": level,
                            "metric": metric_name,
                            "window": str(window),
                            "value": _coerce_float(value),
                            "raw_value": value,
                        }
                    )
            else:
                rows.append(
                    {
                        "level": level,
                        "metric": metric_name,
                        "window": "current",
                        "value": _coerce_float(metric_value),
                        "raw_value": metric_value,
                    }
                )
    return rows


def _average_confidence(rows: list[Any]) -> float:
    values = [_coerce_float(getattr(row, "confidence", None)) for row in rows or []]
    values = [value for value in values if value is not None]
    if not values:
        return 0.75
    return round(sum(values) / len(values), 3)


def _booking_health_severity_from_diagnoses(diagnoses: Any) -> str:
    statuses = [_booking_metric_status(value) for value in diagnoses]
    if "attention" in statuses:
        return "high"
    if "watch" in statuses:
        return "watch"
    if "ok" in statuses:
        return "healthy"
    return "unknown"


def _booking_metric_status(diagnosis: Any) -> str:
    normalized = str(diagnosis or "").strip().lower()
    if not normalized or normalized == "healthy":
        return "ok"
    if "watch" in normalized or "insufficient" in normalized:
        return "watch"
    return "attention"


def _severity_metric_status(severity: Any) -> str:
    normalized = str(severity or "").strip().lower()
    if normalized in {"healthy", "ok", "low"}:
        return "ok"
    if normalized in {"watch", "medium"}:
        return "watch"
    if normalized in {"high", "critical"}:
        return "attention"
    return "watch"


def _truthy(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "booked", "blocked"}
    return bool(value)


ATTENTION_TERMS = (
    "not working",
    "broken",
    "dirty",
    "issue",
    "problem",
    "complaint",
    "refund",
    "unhappy",
    "locked",
    "leak",
    "smell",
    "noise",
    "missing",
    "urgent",
)

RESOLUTION_TERMS = (
    "fixed",
    "resolved",
    "done",
    "completed",
    "handled",
    "sent",
    "replaced",
    "cleaned",
)

DECISION_TERMS = (
    "approved",
    "decided",
    "confirmed",
    "booked",
    "scheduled",
    "go ahead",
)


def _contains_attention_language(value: Any) -> bool:
    text = str(value or "").lower()
    return any(term in text for term in ATTENTION_TERMS)


def _contains_resolution_language(value: Any) -> bool:
    text = str(value or "").lower()
    return any(term in text for term in RESOLUTION_TERMS)


def _contains_decision_language(value: Any) -> bool:
    text = str(value or "").lower()
    return any(term in text for term in DECISION_TERMS)


def _truncate_text(value: Any, limit: int = 220) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: max(limit - 3, 0)].rstrip() + "..."


def _fallback_team_thread_summary(group_name: str, message_count: int, attention_count: int, decision_count: int) -> str:
    details = [f"{message_count} WhatsApp messages"]
    if attention_count:
        details.append(f"{attention_count} attention signals")
    if decision_count:
        details.append(f"{decision_count} decision signals")
    return f"{group_name}: " + ", ".join(details) + "."


def _fallback_guest_stay_memory_payload(*, reservation: BusinessFact | None, messages: list[BusinessFact], reviews: list[BusinessFact], window_days: int) -> dict[str, Any]:
    reservation_payload = reservation.fact_payload if reservation else {}
    ordered_messages = sorted(messages or [], key=lambda row: row.occurred_at or datetime.min)
    ordered_reviews = sorted(reviews or [], key=lambda row: row.occurred_at or datetime.min)
    incoming_messages = [row for row in ordered_messages if _truthy((row.fact_payload or {}).get("is_incoming"))]
    attention_messages = [row for row in ordered_messages if _contains_attention_language(row.text_value)]
    incoming_attention_messages = [row for row in attention_messages if _truthy((row.fact_payload or {}).get("is_incoming"))]
    resolution_messages = [row for row in ordered_messages if _contains_resolution_language(row.text_value)]
    low_reviews = [row for row in ordered_reviews if (_coerce_float(row.numeric_value) or 5) <= 3.5]
    latest_activity_at = _latest_datetime(
        [row.occurred_at for row in ordered_messages]
        + [row.occurred_at for row in ordered_reviews]
        + ([reservation.occurred_at] if reservation else [])
    )
    last_attention_at = _latest_datetime([row.occurred_at for row in attention_messages])
    last_resolution_at = _latest_datetime([row.occurred_at for row in resolution_messages])
    unresolved_attention = bool(last_attention_at and (not last_resolution_at or last_attention_at > last_resolution_at))
    risk_score = min(
        100,
        len(incoming_attention_messages) * 18
        + len(low_reviews) * 35
        + (15 if unresolved_attention else 0)
        + (10 if len(incoming_messages) >= 8 else 0),
    )
    risk_level = "high" if risk_score >= 70 else "medium" if risk_score >= 40 else "low"
    status = "watch" if risk_score >= 40 else "ok"
    guest_name = reservation_payload.get("guest_name") or "Guest"
    listing_id = reservation.listing_id if reservation else (ordered_messages[0].listing_id if ordered_messages else None)
    issue_text = _truncate_text(incoming_attention_messages[-1].text_value if incoming_attention_messages else "", 180)
    review_rating = _coerce_float(ordered_reviews[-1].numeric_value) if ordered_reviews else None
    summary_bits = [
        f"{guest_name} stay for listing {listing_id}",
        f"{len(ordered_messages)} messages",
        f"{len(incoming_messages)} incoming",
    ]
    if review_rating is not None:
        summary_bits.append(f"latest review {review_rating:g}")
    if issue_text:
        summary_bits.append(f"latest issue: {issue_text}")
    summary = "; ".join(summary_bits) + "."
    risk_summary = "Recent guest-risk language is unresolved." if unresolved_attention else "No unresolved guest-risk language stood out in the fallback window."
    if low_reviews:
        risk_summary = f"{risk_summary} {len(low_reviews)} low review(s) found."
    return {
        "source": "fallback_from_normalized_facts",
        "reservation_id": reservation.reservation_id if reservation else None,
        "listing_id": listing_id,
        "guest_id": reservation.guest_id if reservation else (ordered_messages[0].guest_id if ordered_messages else None),
        "guest_name": guest_name,
        "channel_name": reservation_payload.get("channel_name"),
        "arrival_date": reservation_payload.get("arrival_date") or (reservation.effective_start if reservation else None),
        "departure_date": reservation_payload.get("departure_date") or (reservation.effective_end if reservation else None),
        "reservation_status": reservation_payload.get("status"),
        "memory_date": _parse_date(latest_activity_at) or date.today(),
        "window_days": window_days,
        "message_count": len(ordered_messages),
        "incoming_count": len(incoming_messages),
        "attention_message_count": len(attention_messages),
        "resolution_message_count": len(resolution_messages),
        "review_count": len(ordered_reviews),
        "low_review_count": len(low_reviews),
        "last_activity_at": latest_activity_at,
        "risk_level": risk_level,
        "risk_score": risk_score,
        "status": status,
        "summary": summary,
        "risk_summary": risk_summary,
        "latest_guest_issue": issue_text or None,
        "resolution_summary": "Resolution language found after the latest issue." if not unresolved_attention and resolution_messages else None,
        "suggested_action": "Review the stay thread before guest follow-up." if status == "watch" else None,
        "confidence": 0.65,
        "source_fact_ids": [row.business_fact_id for row in ([reservation] if reservation else []) + ordered_messages + ordered_reviews],
    }


def _latest_datetime(values: list[Any]) -> datetime | None:
    parsed = [_as_datetime(value) for value in values if value is not None]
    parsed = [value for value in parsed if value is not None]
    return max(parsed) if parsed else None


def _overlap_nights(start_value: Any, end_value: Any, window_start: date, window_end: date) -> int:
    start = _parse_date(start_value)
    end = _parse_date(end_value)
    if not start or not end:
        return 0
    clipped_start = max(start, window_start)
    clipped_end = min(end, window_end)
    return max((clipped_end - clipped_start).days, 0)


def _reservation_value_coverage(rows: list[Any]) -> dict[str, Any]:
    covered_count = 0
    unverified_channels: dict[str, int] = {}
    for row in rows or []:
        if (_coerce_float(getattr(row, "numeric_value", None)) or 0.0) > 0:
            covered_count += 1
            continue
        payload = getattr(row, "fact_payload", None) or {}
        channel = str(payload.get("channel_name") or "unknown").strip() or "unknown"
        unverified_channels[channel] = unverified_channels.get(channel, 0) + 1
    row_count = len(rows or [])
    unverified_count = row_count - covered_count
    return {
        "value_covered_reservation_count": covered_count,
        "unverified_value_reservation_count": unverified_count,
        "reservation_value_coverage_ratio": round(covered_count / row_count, 4) if row_count else 1.0,
        "unverified_value_channel_counts": unverified_channels,
    }


def _group_facts_by_listing(rows: list[BusinessFact], *, include_unassigned: bool = False) -> dict[int | None, list[BusinessFact]]:
    grouped: dict[int | None, list[BusinessFact]] = {}
    for row in rows or []:
        listing_id = _coerce_int(getattr(row, "listing_id", None))
        if listing_id is None and not include_unassigned:
            continue
        grouped.setdefault(listing_id, []).append(row)
    return grouped


def _group_facts_by_reservation(rows: list[BusinessFact]) -> dict[int, list[BusinessFact]]:
    grouped: dict[int, list[BusinessFact]] = {}
    for row in rows or []:
        reservation_id = _coerce_int(getattr(row, "reservation_id", None))
        if reservation_id is None:
            continue
        grouped.setdefault(reservation_id, []).append(row)
    return grouped
