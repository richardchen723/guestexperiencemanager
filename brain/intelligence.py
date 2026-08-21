#!/usr/bin/env python3
"""Codex-run intelligence layer over the normalized Brain data foundation."""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import func

import dashboard.config as config
from brain.aggregator import FOUNDATION_SOURCE_FRESHNESS_TARGETS
from brain.models import (
    BusinessFact,
    BusinessMetricSnapshot,
    CodexIntelligenceInsight,
    CodexIntelligenceRun,
    DataIngestionRun,
    Portfolio,
    PortfolioListing,
    as_json_safe,
    get_session,
    stable_hash,
)
from database.models import Listing, get_session as get_main_session


INTELLIGENCE_PACKET_VERSION = "codex-intelligence-packet-v2"
CODEX_GENERATED_BY = "codex_subscription"
DEFAULT_PACKET_DIR = Path(config.PROJECT_ROOT) / "data" / "brain" / "intelligence" / "packets"

PRICING_BOOKING_METRICS = (
    "booking_occupancy_rate",
    "booking_health_severity",
    "booked_nights_next_30d",
    "reservation_revenue_next_30d",
    "pricelabs_avg_price_30d",
    "pricelabs_avg_available_price_30d",
    "pricelabs_max_min_stay_30d",
    "pricelabs_unbookable_days_30d",
)
GUEST_OPS_METRICS = (
    "guest_message_count_7d",
    "guest_risk_score_max_90d",
    "review_avg_rating_90d",
)
METRIC_TARGET_HORIZONS = {
    "booking_occupancy_rate": 30,
    "booked_nights_next_30d": 30,
    "reservation_revenue_next_30d": 30,
    "pricelabs_avg_price_30d": 30,
    "pricelabs_avg_available_price_30d": 30,
    "pricelabs_max_min_stay_30d": 30,
    "pricelabs_unbookable_days_30d": 30,
}


class CodexIntelligenceService:
    """Prepare and store Codex-authored intelligence without using OpenAI API calls."""

    def __init__(self, session=None, main_session=None):
        self.session = session or get_session()
        self.main_session = main_session or get_main_session(config.MAIN_DATABASE_PATH)
        self._owns_session = session is None
        self._owns_main_session = main_session is None

    def close(self):
        if self._owns_session:
            self.session.close()
        if self._owns_main_session:
            self.main_session.close()

    def prepare_packet(
        self,
        *,
        window_days: int = 30,
        run_key: str | None = None,
        packet_dir: str | Path | None = None,
        write_files: bool = True,
    ) -> dict[str, Any]:
        """Build a compact packet for weekly Codex reasoning and store a run ledger row."""
        now = datetime.utcnow()
        window_days = max(min(int(window_days or 30), 120), 7)
        window_start = now - timedelta(days=window_days)
        run_key = run_key or f"codex-weekly-{now.strftime('%Y%m%dT%H%M%SZ')}"
        packet = self._build_packet(run_key=run_key, generated_at=now, window_start=window_start, window_days=window_days)
        packet_hash = stable_hash(packet)
        packet_path = None
        markdown_path = None

        if write_files:
            out_dir = Path(packet_dir or DEFAULT_PACKET_DIR)
            out_dir.mkdir(parents=True, exist_ok=True)
            packet_path = out_dir / f"{run_key}.json"
            markdown_path = out_dir / f"{run_key}.md"
            packet_path.write_text(json.dumps(packet, indent=2, sort_keys=True), encoding="utf-8")
            markdown_path.write_text(self._packet_markdown(packet), encoding="utf-8")

        run = self.session.query(CodexIntelligenceRun).filter(CodexIntelligenceRun.run_key == run_key).first()
        if not run:
            run = CodexIntelligenceRun(run_key=run_key, created_at=now)
            self.session.add(run)
        run.status = "prepared"
        run.cadence = "weekly"
        run.analysis_window_start = window_start
        run.analysis_window_end = now
        run.packet_path = str(packet_path) if packet_path else None
        run.packet_hash = packet_hash
        run.packet_summary = packet["packet_summary"]
        run.source_snapshot = packet["source_snapshot"]
        run.generated_by = CODEX_GENERATED_BY
        run.completed_at = None
        run.error_message = None
        run.generated_at = now
        run.updated_at = now
        self.session.commit()

        return {
            "status": "prepared",
            "run_id": run.codex_intelligence_run_id,
            "run_key": run.run_key,
            "packet_hash": packet_hash,
            "packet_path": str(packet_path) if packet_path else None,
            "markdown_path": str(markdown_path) if markdown_path else None,
            "packet_summary": packet["packet_summary"],
        }

    def import_insights(self, payload: dict[str, Any], *, run_id: int | None = None, run_key: str | None = None) -> dict[str, Any]:
        """Upsert Codex-authored insights from a JSON payload."""
        now = datetime.utcnow()
        insights = payload.get("insights") if isinstance(payload, dict) else None
        if not isinstance(insights, list):
            raise ValueError("Intelligence payload must contain an 'insights' list.")

        run = self._resolve_run(run_id=run_id, run_key=run_key or payload.get("run_key"))
        if not run:
            generated_at = _parse_datetime(payload.get("generated_at")) or now
            key_payload = {"generated_at": generated_at.isoformat(), "summary": payload.get("summary"), "count": len(insights)}
            run = CodexIntelligenceRun(
                run_key=payload.get("run_key") or f"codex-import-{stable_hash(key_payload)[:16]}",
                status="prepared",
                cadence=payload.get("cadence") or "weekly",
                generated_by=CODEX_GENERATED_BY,
                generated_at=generated_at,
                created_at=now,
            )
            self.session.add(run)
            self.session.flush()

        created = 0
        updated = 0
        active_keys: set[str] = set()
        for item in insights:
            if not isinstance(item, dict):
                continue
            insight_key = item.get("insight_key") or self._insight_key(item)
            active_keys.add(insight_key)
            row = self.session.query(CodexIntelligenceInsight).filter(CodexIntelligenceInsight.insight_key == insight_key).first()
            if not row:
                row = CodexIntelligenceInsight(insight_key=insight_key, created_at=now, first_seen_at=now)
                self.session.add(row)
                created += 1
            else:
                updated += 1
            row.run_id = run.codex_intelligence_run_id
            row.category = _text(item.get("category"), "general")
            row.title = _text(item.get("title"), "Untitled intelligence")
            row.summary = _text(item.get("summary"), "")
            row.reasoning = _text(item.get("reasoning"), "")
            row.recommended_action = _text(item.get("recommended_action"), "")
            row.expected_impact = _text(item.get("expected_impact"), "")
            row.confidence = _bounded_float(item.get("confidence"), default=0.7)
            row.severity = _text(item.get("severity"), "medium").lower()
            row.status = _text(item.get("status"), "active").lower()
            row.portfolio_id = _coerce_int(item.get("portfolio_id"))
            row.listing_id = _coerce_int(item.get("listing_id"))
            row.reservation_id = _coerce_int(item.get("reservation_id"))
            row.guest_id = _coerce_int(item.get("guest_id"))
            row.source_fact_ids = _coerce_int_list(item.get("source_fact_ids"))
            row.source_metric_ids = _coerce_int_list(item.get("source_metric_ids"))
            row.evidence_payload = as_json_safe(item.get("evidence") or item.get("evidence_payload") or {})
            row.insight_payload = as_json_safe(item)
            row.last_seen_at = now
            row.updated_at = now

        run.status = "completed"
        run.completed_at = now
        run.packet_summary = {
            **(run.packet_summary or {}),
            "imported_insights": len(active_keys),
            "import_summary": payload.get("summary"),
        }
        run.updated_at = now
        self.session.commit()

        return {
            "status": "completed",
            "run_id": run.codex_intelligence_run_id,
            "run_key": run.run_key,
            "created": created,
            "updated": updated,
            "insights_imported": len(active_keys),
        }

    def list_insights(self, *, category: str | None = None, status: str = "active", limit: int = 100) -> dict[str, Any]:
        """Return stored Codex intelligence rows for APIs and future products."""
        query = self.session.query(CodexIntelligenceInsight)
        if category:
            query = query.filter(CodexIntelligenceInsight.category == category)
        if status and status != "all":
            query = query.filter(CodexIntelligenceInsight.status == status)
        limit = min(max(int(limit or 100), 1), 500)
        rows = query.order_by(CodexIntelligenceInsight.last_seen_at.desc(), CodexIntelligenceInsight.confidence.desc()).limit(limit).all()
        return {
            "generated_at": datetime.utcnow().isoformat(),
            "limit": limit,
            "items": [self._insight_dict(row) for row in rows],
        }

    def _build_packet(self, *, run_key: str, generated_at: datetime, window_start: datetime, window_days: int) -> dict[str, Any]:
        metrics = self._latest_metrics(PRICING_BOOKING_METRICS + GUEST_OPS_METRICS)
        listing_contexts = self._mapped_listing_contexts()
        mapped_listing_ids = set(listing_contexts)
        if mapped_listing_ids:
            metrics = {key: row for key, row in metrics.items() if key[1] in mapped_listing_ids}
        listing_ids = sorted(mapped_listing_ids or {listing_id for (_, listing_id), _metric in metrics.items() if listing_id})
        listing_details = self._listing_details(listing_ids)
        listing_names = {
            listing_id: details["listing_name"]
            for listing_id, details in listing_details.items()
        }
        for listing_id in listing_ids:
            listing_contexts.setdefault(listing_id, {}).update(
                {
                    "public_listing_name": (listing_details.get(listing_id) or {}).get("public_listing_name"),
                    "inventory_mode": (listing_details.get(listing_id) or {}).get("inventory_mode", "individual"),
                    "inventory_class": (listing_details.get(listing_id) or {}).get("inventory_class", "unspecified"),
                    "bedrooms": (listing_details.get(listing_id) or {}).get("bedrooms"),
                    "accommodates": (listing_details.get(listing_id) or {}).get("accommodates"),
                    "bathrooms": (listing_details.get(listing_id) or {}).get("bathrooms"),
                }
            )
        pricing_contexts = self._latest_pricing_contexts(listing_ids)
        pricing_rows = self._pricing_booking_rows(
            metrics,
            listing_names,
            pricing_contexts=pricing_contexts,
            listing_ids=listing_ids,
            listing_contexts=listing_contexts,
            as_of_date=generated_at.date(),
        )
        guest_rows = self._guest_ops_rows(metrics, listing_names, listing_contexts=listing_contexts)
        source_snapshot = self._source_snapshot()
        recent_context = self._recent_context(window_start=window_start)
        pricing_readiness_counts = _count_values(
            (row.get("decision_readiness") or {}).get("status") for row in pricing_rows
        )
        packet_summary = {
            "version": INTELLIGENCE_PACKET_VERSION,
            "window_days": window_days,
            "listing_count": len(listing_ids),
            "pricing_booking_rows": len(pricing_rows),
            "pricing_decision_readiness": pricing_readiness_counts,
            "guest_ops_rows": len(guest_rows),
            "recent_guest_memory_count": recent_context["coverage"]["guest_stay_memories"]["total"],
            "whatsapp_context_count": recent_context["coverage"]["whatsapp_context"]["total"],
            "source_quality_gap_count": len(source_snapshot["source_quality_gaps"]),
        }
        return {
            "run_key": run_key,
            "generated_at": generated_at.isoformat(),
            "analysis_window": {
                "start": window_start.isoformat(),
                "end": generated_at.isoformat(),
                "days": window_days,
            },
            "instructions": [
                "Use Codex reasoning only; do not call OpenAI API from the app.",
                "Find cross-source connections, especially pricing vs booking behavior and guest/team communication overlap.",
                "Use PriceLabs prices on Hostaway-available dates for rate actions; all-date PriceLabs averages are context only.",
                "Compare rates only within the supplied portfolio and inventory-mode peer benchmark. Never use a global cross-portfolio median.",
                "Do not infer weak demand from blocked inventory, inquiries, or stale evidence.",
                "Treat rows marked blocked as data/inventory remediation items, not pricing recommendations.",
                "Return authored insights as JSON with an insights array suitable for brain.jobs intelligence-import.",
            ],
            "reasoning_guardrails": {
                "booking_status_policy": "Only confirmed/modified/owner-stay-like reservations count; inquiry and non-booked statuses are excluded.",
                "occupancy_basis": "Hostaway reserved nights divided by reserved plus available nights; blocked nights are excluded.",
                "reservation_night_basis": "Unique confirmed occupied dates inside the 30-day window.",
                "reservation_revenue_basis": "Hostaway total_price prorated to reservation nights overlapping the 30-day window.",
                "pricing_action_basis": "PriceLabs recommended price on dates Hostaway currently marks available.",
                "comparable_scope": "Other listings in the same Brain portfolio and inventory profile with at least three available priced dates.",
                "bundle_calendar_policy": "Bundle calendar occupancy without direct confirmed reservation nights may reflect component-unit holds and cannot support a demand inference.",
                "occupancy_signal_floor": "Automatic pace signals require at least 14 sellable nights in the 30-day window.",
                "stale_evidence_policy": "A fresh ingestion run does not make old business events current.",
            },
            "packet_summary": packet_summary,
            "source_snapshot": source_snapshot,
            "pricing_booking_rows": pricing_rows,
            "guest_operations_rows": guest_rows,
            "recent_context": recent_context,
            "candidate_questions": [
                "Where is PriceLabs price or min-stay behavior mismatched with booking pace?",
                "Which listings have guest communication intensity or risk that should connect to team WhatsApp operations?",
                "Which patterns are strong enough to become weekly operating recommendations?",
            ],
        }

    def _latest_metrics(self, names: tuple[str, ...]) -> dict[tuple[str, int | None], BusinessMetricSnapshot]:
        rows = (
            self.session.query(BusinessMetricSnapshot)
            .filter(BusinessMetricSnapshot.status != "withdrawn", BusinessMetricSnapshot.metric_name.in_(names))
            .order_by(
                BusinessMetricSnapshot.metric_date.desc(),
                BusinessMetricSnapshot.updated_at.desc(),
                BusinessMetricSnapshot.business_metric_snapshot_id.desc(),
            )
            .all()
        )
        latest: dict[tuple[str, int | None], BusinessMetricSnapshot] = {}
        for row in rows:
            key = (row.metric_name, row.listing_id)
            if key not in latest or _metric_selection_rank(row) > _metric_selection_rank(latest[key]):
                latest[key] = row
        return latest

    def _mapped_listing_ids(self) -> set[int]:
        return set(self._mapped_listing_contexts())

    def _mapped_listing_contexts(self) -> dict[int, dict[str, Any]]:
        rows = (
            self.session.query(PortfolioListing.listing_id, PortfolioListing.portfolio_id, Portfolio.name)
            .join(Portfolio, Portfolio.portfolio_id == PortfolioListing.portfolio_id)
            .all()
        )
        return {
            int(listing_id): {
                "portfolio_id": int(portfolio_id),
                "portfolio_name": portfolio_name,
            }
            for listing_id, portfolio_id, portfolio_name in rows
            if listing_id and portfolio_id
        }

    def _latest_pricing_contexts(self, listing_ids: list[int]) -> dict[int, BusinessFact]:
        if not listing_ids:
            return {}
        rows = (
            self.session.query(BusinessFact)
            .filter(
                BusinessFact.status == "active",
                BusinessFact.fact_type == "pricing_context",
                BusinessFact.listing_id.in_(listing_ids),
            )
            .order_by(
                BusinessFact.listing_id,
                BusinessFact.occurred_at.desc().nullslast(),
                BusinessFact.updated_at.desc(),
                BusinessFact.business_fact_id.desc(),
            )
            .all()
        )
        latest: dict[int, BusinessFact] = {}
        for row in rows:
            if row.listing_id and int(row.listing_id) not in latest:
                latest[int(row.listing_id)] = row
        return latest

    def _pricing_booking_rows(
        self,
        metrics: dict[tuple[str, int | None], BusinessMetricSnapshot],
        listing_names: dict[int, str],
        *,
        pricing_contexts: dict[int, BusinessFact] | None = None,
        listing_ids: list[int] | None = None,
        listing_contexts: dict[int, dict[str, Any]] | None = None,
        as_of_date: date | None = None,
    ) -> list[dict[str, Any]]:
        listing_ids = sorted(listing_ids or {listing_id for (name, listing_id) in metrics if listing_id and name in PRICING_BOOKING_METRICS})
        listing_contexts = listing_contexts or {}
        as_of_date = as_of_date or date.today()
        rows: list[dict[str, Any]] = []
        for listing_id in listing_ids:
            metric_payload = {name: self._metric_summary(metrics.get((name, listing_id))) for name in PRICING_BOOKING_METRICS}
            avg_price = _metric_value(metric_payload.get("pricelabs_avg_price_30d"))
            available_price_metric = metric_payload.get("pricelabs_avg_available_price_30d")
            available_price = _metric_value(available_price_metric)
            available_price_days = _metric_payload_int(available_price_metric, "available_days")
            available_price_median = _metric_payload_float(available_price_metric, "median_available_price")
            mean_to_median_ratio = _metric_payload_float(available_price_metric, "mean_to_median_ratio")
            event_or_override_days = _metric_payload_int(available_price_metric, "event_or_override_available_days")
            rate_distribution_skewed = bool(mean_to_median_ratio is not None and mean_to_median_ratio >= 1.25)
            occupancy = _metric_value(metric_payload.get("booking_occupancy_rate"))
            min_stay = _metric_value(metric_payload.get("pricelabs_max_min_stay_30d"))
            booked_nights = _metric_value(metric_payload.get("booked_nights_next_30d"))
            calendar_booked_nights = _metric_payload_int(metric_payload.get("booking_occupancy_rate"), "booked_nights")
            reservation_booked_nights = float(booked_nights or 0.0)
            bundle_reservation_coverage_ratio = (
                reservation_booked_nights / calendar_booked_nights if calendar_booked_nights > 0 else None
            )
            revenue_metric = metric_payload.get("reservation_revenue_next_30d") or {}
            revenue_metric_status = str(revenue_metric.get("status") or "").strip().lower()
            revenue_value_incomplete = bool(
                booked_nights
                and revenue_metric_status in {"watch", "not_available"}
                and _metric_payload_int(revenue_metric, "unverified_value_reservation_count") > 0
            )
            occupancy_diagnosis = _metric_payload_text(metric_payload.get("booking_occupancy_rate"), "diagnosis")
            sellable_nights = _metric_payload_int(metric_payload.get("booking_occupancy_rate"), "sellable_nights")
            occupancy_signal_ready = sellable_nights >= 14
            context = listing_contexts.get(listing_id) or {}
            peer_prices = []
            peer_listing_ids = []
            excluded_inventory_profile_peer_count = 0
            for peer_id in listing_ids:
                if peer_id == listing_id:
                    continue
                peer_context = listing_contexts.get(peer_id) or {}
                if not context.get("portfolio_id") or peer_context.get("portfolio_id") != context.get("portfolio_id"):
                    continue
                if not _is_inventory_mode_comparable(context, peer_context):
                    excluded_inventory_profile_peer_count += 1
                    continue
                peer_metric = metrics.get(("pricelabs_avg_available_price_30d", peer_id))
                peer_days = _metric_row_payload_int(peer_metric, "available_days")
                if peer_metric and peer_metric.numeric_value is not None and peer_days >= 3:
                    peer_prices.append(float(peer_metric.numeric_value))
                    peer_listing_ids.append(peer_id)
            peer_median = _median(peer_prices)
            rate_ratio = available_price / peer_median if available_price is not None and peer_median else None
            flags = []
            if occupancy_diagnosis == "inventory_blocked":
                flags.append("inventory_blocked_demand_not_measurable")
            bundle_calendar_occupancy_without_direct_reservations = (
                context.get("inventory_mode") == "aggregate_bundle"
                and calendar_booked_nights > 0
                and reservation_booked_nights <= 0
            )
            bundle_calendar_occupancy_not_reconciled = bool(
                context.get("inventory_mode") == "aggregate_bundle"
                and calendar_booked_nights > 0
                and bundle_reservation_coverage_ratio is not None
                and bundle_reservation_coverage_ratio < 0.8
            )
            if bundle_calendar_occupancy_without_direct_reservations:
                flags.append("bundle_calendar_occupancy_without_direct_reservations")
            elif bundle_calendar_occupancy_not_reconciled:
                flags.append("bundle_calendar_occupancy_not_reconciled_to_reservations")
            if rate_distribution_skewed:
                flags.append(
                    "available_rate_distribution_skewed_by_event_or_override_dates"
                    if event_or_override_days
                    else "available_rate_distribution_skewed"
                )
            if revenue_value_incomplete:
                flags.append("reservation_value_coverage_incomplete")
            if not bundle_calendar_occupancy_not_reconciled and not rate_distribution_skewed and occupancy_signal_ready and occupancy is not None and available_price is not None and rate_ratio is not None and len(peer_prices) >= 2 and occupancy < 0.35 and rate_ratio >= 1.1:
                flags.append("soft_booking_pace_with_high_portfolio_available_rate")
            if not bundle_calendar_occupancy_not_reconciled and occupancy_signal_ready and occupancy is not None and available_price_days > 0 and min_stay is not None and occupancy < 0.45 and min_stay >= 3:
                flags.append("strict_min_stay_with_soft_booking_pace")
            if not bundle_calendar_occupancy_not_reconciled and occupancy_signal_ready and occupancy is not None and occupancy >= 0.8 and available_price_days <= 3:
                flags.append("high_booking_pace_with_limited_open_inventory")
            elif not bundle_calendar_occupancy_not_reconciled and not rate_distribution_skewed and occupancy_signal_ready and occupancy is not None and available_price is not None and rate_ratio is not None and len(peer_prices) >= 2 and occupancy >= 0.8 and rate_ratio >= 1.0:
                flags.append("rate_strength_with_high_booking_pace")
            source_gaps = []
            if avg_price is None:
                pricing_context = (pricing_contexts or {}).get(listing_id)
                source_gap = _pricing_context_source_gap(pricing_context)
                if source_gap:
                    flags.append("pricelabs_daily_price_unavailable")
                    source_gaps.append(source_gap)
            elif available_price is None or available_price_days <= 0:
                flags.append("no_available_dates_for_pricing_action")
            if revenue_value_incomplete:
                source_gaps.append(
                    {
                        "source_key": "hostaway_reservations",
                        "fact_type": "reservation_stay",
                        "status": "incomplete_value_coverage",
                        "message": "Occupied reservation dates include records without a verified booking value.",
                        "coverage_ratio": _metric_payload_float(revenue_metric, "reservation_value_coverage_ratio"),
                        "unverified_reservation_count": _metric_payload_int(
                            revenue_metric,
                            "unverified_value_reservation_count",
                        ),
                    }
                )
            readiness_reasons = []
            required_metrics = (
                metric_payload.get("booking_occupancy_rate"),
                metric_payload.get("pricelabs_avg_available_price_30d"),
            )
            metric_dates = [_metric_date_value(item) for item in required_metrics]
            metric_dates = [item for item in metric_dates if item is not None]
            stale_metric_names = [
                name
                for name in ("booking_occupancy_rate", "pricelabs_avg_available_price_30d")
                if _metric_age_days(metric_payload.get(name), as_of_date=as_of_date) > 2
            ]
            if stale_metric_names:
                readiness_reasons.append(f"stale metrics: {', '.join(stale_metric_names)}")
            if metric_dates and (max(metric_dates) - min(metric_dates)).days > 1:
                readiness_reasons.append("pricing and booking metrics are not date-aligned")
            if rate_distribution_skewed:
                detail = " because event or date-override prices are present" if event_or_override_days else ""
                readiness_reasons.append(f"30-day available-rate mean is materially above its median{detail}")
            if occupancy_diagnosis == "inventory_blocked":
                readiness_status = "blocked"
                readiness_reasons.append("Hostaway shows no sellable inventory in the measured window")
            elif bundle_calendar_occupancy_not_reconciled:
                readiness_status = "blocked"
                if bundle_calendar_occupancy_without_direct_reservations:
                    readiness_reasons.append(
                        "bundle calendar occupancy has no direct confirmed reservation nights and may reflect component-unit holds"
                    )
                else:
                    readiness_reasons.append(
                        "bundle calendar booked nights materially exceed attributed reservation nights and may reflect component-unit holds"
                    )
            elif avg_price is None:
                readiness_status = "blocked"
                readiness_reasons.append("PriceLabs daily prices are unavailable")
            elif available_price is None or available_price_days <= 0:
                readiness_status = "blocked"
                readiness_reasons.append("no Hostaway-available dates have a matched PriceLabs rate")
            elif stale_metric_names:
                readiness_status = "blocked"
            elif available_price_days < 3:
                readiness_status = "caution"
                readiness_reasons.append("fewer than three available priced dates")
            elif len(peer_prices) < 2:
                readiness_status = "caution"
                readiness_reasons.append("fewer than two comparable portfolio peers")
            elif readiness_reasons:
                readiness_status = "caution"
            else:
                readiness_status = "ready"
            rows.append(
                {
                    "listing_id": listing_id,
                    "listing_name": listing_names.get(listing_id),
                    **context,
                    "flags": flags,
                    "source_gaps": source_gaps,
                    "decision_readiness": {
                        "status": readiness_status,
                        "reasons": readiness_reasons,
                        "allowed_conclusion": _pricing_allowed_conclusion(
                            readiness_status,
                            occupancy_diagnosis,
                            bundle_demand_ambiguous=bundle_calendar_occupancy_not_reconciled,
                            rate_distribution_skewed=rate_distribution_skewed,
                        ),
                    },
                    "reservation_calendar_reconciliation": {
                        "calendar_booked_nights": calendar_booked_nights,
                        "reservation_booked_nights": reservation_booked_nights,
                        "reservation_to_calendar_booked_ratio": (
                            round(bundle_reservation_coverage_ratio, 4)
                            if bundle_reservation_coverage_ratio is not None
                            else None
                        ),
                        "bundle_demand_ambiguous": bundle_calendar_occupancy_not_reconciled,
                    },
                    "pricing_basis": {
                        "metric": "pricelabs_avg_available_price_30d",
                        "available_priced_days": available_price_days,
                        "all_date_average": avg_price,
                        "available_date_average": available_price,
                        "available_date_median": available_price_median,
                        "mean_to_median_ratio": mean_to_median_ratio,
                        "event_or_override_available_days": event_or_override_days,
                    },
                    "comparable_rate": {
                        "scope": "same_portfolio_inventory_profile_peers",
                        "portfolio_id": context.get("portfolio_id"),
                        "portfolio_name": context.get("portfolio_name"),
                        "inventory_mode": context.get("inventory_mode") or "individual",
                        "inventory_class": context.get("inventory_class") or "unspecified",
                        "peer_count": len(peer_prices),
                        "peer_listing_ids": peer_listing_ids,
                        "excluded_inventory_profile_peer_count": excluded_inventory_profile_peer_count,
                        "peer_median_available_price": peer_median,
                        "listing_to_peer_median_ratio": round(rate_ratio, 4) if rate_ratio is not None else None,
                        "minimum_available_days_per_peer": 3,
                    },
                    "metrics": metric_payload,
                }
            )
        return rows

    def _guest_ops_rows(
        self,
        metrics: dict[tuple[str, int | None], BusinessMetricSnapshot],
        listing_names: dict[int, str],
        *,
        listing_contexts: dict[int, dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        listing_contexts = listing_contexts or {}
        listing_ids = sorted({listing_id for (name, listing_id) in metrics if listing_id and name in GUEST_OPS_METRICS})
        rows = []
        for listing_id in listing_ids:
            metric_payload = {name: self._metric_summary(metrics.get((name, listing_id))) for name in GUEST_OPS_METRICS}
            risk = _metric_value(metric_payload.get("guest_risk_score_max_90d"))
            messages = _metric_value(metric_payload.get("guest_message_count_7d"))
            review = _metric_value(metric_payload.get("review_avg_rating_90d"))
            flags = []
            if _is_high_guest_risk(risk):
                flags.append("high_guest_risk")
            if messages is not None and messages >= 10:
                flags.append("high_recent_message_volume")
            if _is_low_review_rating(review):
                flags.append("review_rating_watch")
            rows.append(
                {
                    "listing_id": listing_id,
                    "listing_name": listing_names.get(listing_id),
                    **(listing_contexts.get(listing_id) or {}),
                    "flags": flags,
                    "metrics": metric_payload,
                }
            )
        return rows

    def _recent_context(self, *, window_start: datetime) -> dict[str, Any]:
        guest_query = (
            self.session.query(BusinessFact)
            .filter(BusinessFact.status == "active", BusinessFact.fact_type == "guest_stay_memory", BusinessFact.occurred_at >= window_start)
        )
        whatsapp_query = (
            self.session.query(BusinessFact)
            .filter(
                BusinessFact.status == "active",
                BusinessFact.fact_type.in_(("whatsapp_message", "team_thread_memory")),
                BusinessFact.occurred_at >= window_start,
            )
        )
        guest_total = guest_query.count()
        whatsapp_total = whatsapp_query.count()
        guest_memories = (
            guest_query.order_by(BusinessFact.occurred_at.desc().nullslast(), BusinessFact.updated_at.desc())
            .limit(25)
            .all()
        )
        whatsapp_rows = (
            whatsapp_query
            .order_by(BusinessFact.occurred_at.desc().nullslast(), BusinessFact.updated_at.desc())
            .limit(25)
            .all()
        )
        return {
            "guest_stay_memories": [self._fact_summary(row) for row in guest_memories],
            "whatsapp_context": [self._fact_summary(row) for row in whatsapp_rows],
            "coverage": {
                "guest_stay_memories": {
                    "total": int(guest_total or 0),
                    "included": len(guest_memories),
                    "truncated": int(guest_total or 0) > len(guest_memories),
                },
                "whatsapp_context": {
                    "total": int(whatsapp_total or 0),
                    "included": len(whatsapp_rows),
                    "truncated": int(whatsapp_total or 0) > len(whatsapp_rows),
                },
            },
        }

    def _source_snapshot(self) -> dict[str, Any]:
        fact_rows = (
            self.session.query(
                BusinessFact.fact_type,
                func.count(BusinessFact.business_fact_id),
                func.max(BusinessFact.occurred_at),
                func.max(BusinessFact.updated_at),
            )
            .filter(BusinessFact.status == "active")
            .group_by(BusinessFact.fact_type)
            .all()
        )
        fact_counts = {fact_type: count for fact_type, count, _occurred_at, _updated_at in fact_rows}
        fact_freshness = {
            fact_type: {
                "count": int(count or 0),
                "latest_occurred_at": occurred_at.isoformat() if occurred_at else None,
                "latest_updated_at": updated_at.isoformat() if updated_at else None,
            }
            for fact_type, count, occurred_at, updated_at in fact_rows
        }
        latest_runs = {}
        for row in (
            self.session.query(DataIngestionRun)
            .filter(DataIngestionRun.status == "completed")
            .order_by(DataIngestionRun.source_key, DataIngestionRun.completed_at.desc().nullslast())
            .all()
        ):
            if row.source_key not in latest_runs:
                latest_runs[row.source_key] = {
                    "completed_at": row.completed_at.isoformat() if row.completed_at else None,
                    "facts_written": row.facts_written,
                    "records_seen": row.records_seen,
                }
        return {
            "fact_counts": {key: int(value or 0) for key, value in sorted(fact_counts.items())},
            "fact_freshness": {key: value for key, value in sorted(fact_freshness.items())},
            "latest_runs": latest_runs,
            "source_quality_gaps": _source_quality_gaps(fact_freshness, now=datetime.utcnow()),
        }

    def _metric_summary(self, row: BusinessMetricSnapshot | None) -> dict[str, Any] | None:
        if not row:
            return None
        return {
            "id": row.business_metric_snapshot_id,
            "metric_date": row.metric_date.isoformat() if row.metric_date else None,
            "value": row.numeric_value,
            "text": row.text_value,
            "status": row.status,
            "horizon_days": row.horizon_days,
            "confidence": getattr(row, "confidence", None),
            "source_keys": as_json_safe(getattr(row, "source_keys", None) or []),
            "computed_at": row.computed_at.isoformat() if getattr(row, "computed_at", None) else None,
            "updated_at": row.updated_at.isoformat() if getattr(row, "updated_at", None) else None,
            "payload": as_json_safe(row.metric_payload or {}),
        }

    def _fact_summary(self, row: BusinessFact) -> dict[str, Any]:
        return {
            "id": row.business_fact_id,
            "fact_type": row.fact_type,
            "source_key": row.source_key,
            "portfolio_id": row.portfolio_id,
            "listing_id": row.listing_id,
            "reservation_id": row.reservation_id,
            "guest_id": row.guest_id,
            "occurred_at": row.occurred_at.isoformat() if row.occurred_at else None,
            "text": row.text_value,
            "numeric_value": row.numeric_value,
            "payload": as_json_safe(row.fact_payload or {}),
        }

    def _listing_details(self, listing_ids: list[int]) -> dict[int, dict[str, Any]]:
        if not listing_ids:
            return {}
        rows = self.main_session.query(Listing).filter(Listing.listing_id.in_(listing_ids)).all()
        return {
            int(row.listing_id): {
                "listing_name": row.internal_listing_name or row.name or str(row.listing_id),
                "public_listing_name": row.name,
                "inventory_mode": _listing_inventory_mode(row.internal_listing_name, row.name),
                "inventory_class": _listing_inventory_class(
                    row.internal_listing_name,
                    row.name,
                    bedrooms=row.bedrooms,
                ),
                "bedrooms": row.bedrooms,
                "accommodates": row.accommodates,
                "bathrooms": row.bathrooms,
            }
            for row in rows
        }

    def _listing_names(self, listing_ids: list[int]) -> dict[int, str]:
        return {
            listing_id: details["listing_name"]
            for listing_id, details in self._listing_details(listing_ids).items()
        }

    def _resolve_run(self, *, run_id: int | None, run_key: str | None) -> CodexIntelligenceRun | None:
        if run_id:
            return self.session.get(CodexIntelligenceRun, run_id)
        if run_key:
            return self.session.query(CodexIntelligenceRun).filter(CodexIntelligenceRun.run_key == run_key).first()
        return None

    def _insight_key(self, item: dict[str, Any]) -> str:
        payload = {
            "category": item.get("category"),
            "title": item.get("title"),
            "listing_id": item.get("listing_id"),
            "reservation_id": item.get("reservation_id"),
            "summary": item.get("summary"),
        }
        return f"codex:{_text(item.get('category'), 'general')}:{stable_hash(payload)[:20]}"

    def _insight_dict(self, row: CodexIntelligenceInsight) -> dict[str, Any]:
        return {
            "codex_intelligence_insight_id": row.codex_intelligence_insight_id,
            "run_id": row.run_id,
            "insight_key": row.insight_key,
            "category": row.category,
            "title": row.title,
            "summary": row.summary,
            "reasoning": row.reasoning,
            "recommended_action": row.recommended_action,
            "expected_impact": row.expected_impact,
            "confidence": row.confidence,
            "severity": row.severity,
            "status": row.status,
            "portfolio_id": row.portfolio_id,
            "listing_id": row.listing_id,
            "reservation_id": row.reservation_id,
            "guest_id": row.guest_id,
            "source_fact_ids": as_json_safe(row.source_fact_ids or []),
            "source_metric_ids": as_json_safe(row.source_metric_ids or []),
            "evidence": as_json_safe(row.evidence_payload or {}),
            "payload": as_json_safe(row.insight_payload or {}),
            "first_seen_at": row.first_seen_at.isoformat() if row.first_seen_at else None,
            "last_seen_at": row.last_seen_at.isoformat() if row.last_seen_at else None,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        }

    def _packet_markdown(self, packet: dict[str, Any]) -> str:
        lines = [
            f"# Codex Intelligence Packet: {packet['run_key']}",
            "",
            f"Generated: {packet['generated_at']}",
            f"Window: {packet['analysis_window']['days']} days",
            "",
            "## Summary",
            "```json",
            json.dumps(packet["packet_summary"], indent=2, sort_keys=True),
            "```",
            "",
            "## Pricing And Booking",
        ]
        for row in packet["pricing_booking_rows"]:
            flags = ", ".join(row.get("flags") or []) or "no automatic flag"
            readiness = (row.get("decision_readiness") or {}).get("status") or "unknown"
            lines.append(f"- {row.get('listing_name') or row.get('listing_id')}: [{readiness}] {flags}")
        lines.extend(["", "## Guest And Operations"])
        for row in packet["guest_operations_rows"]:
            flags = ", ".join(row.get("flags") or []) or "no automatic flag"
            lines.append(f"- {row.get('listing_name') or row.get('listing_id')}: {flags}")
        lines.extend(
            [
                "",
                "## Full Packet",
                "```json",
                json.dumps(packet, indent=2, sort_keys=True),
                "```",
                "",
            ]
        )
        return "\n".join(lines)


def _text(value: Any, default: str) -> str:
    text = str(value or "").strip()
    return text or default


def _coerce_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _coerce_int_list(value: Any) -> list[int]:
    if not value:
        return []
    if not isinstance(value, list):
        value = [value]
    result = []
    for item in value:
        coerced = _coerce_int(item)
        if coerced is not None:
            result.append(coerced)
    return result


def _bounded_float(value: Any, *, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    return min(max(number, 0.0), 1.0)


def _metric_value(summary: dict[str, Any] | None) -> float | None:
    if not summary:
        return None
    value = summary.get("value")
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _metric_payload_int(summary: dict[str, Any] | None, key: str) -> int:
    payload = (summary or {}).get("payload") or {}
    try:
        return int(payload.get(key) or 0)
    except (TypeError, ValueError):
        return 0


def _metric_payload_float(summary: dict[str, Any] | None, key: str) -> float | None:
    payload = (summary or {}).get("payload") or {}
    value = payload.get(key)
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _metric_payload_text(summary: dict[str, Any] | None, key: str) -> str | None:
    payload = (summary or {}).get("payload") or {}
    value = payload.get(key)
    return str(value).strip() if value not in (None, "") else None


def _metric_row_payload_int(row: Any, key: str) -> int:
    payload = getattr(row, "metric_payload", None) or {}
    try:
        return int(payload.get(key) or 0)
    except (TypeError, ValueError):
        return 0


def _metric_date_value(summary: dict[str, Any] | None) -> date | None:
    value = (summary or {}).get("metric_date")
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _metric_age_days(summary: dict[str, Any] | None, *, as_of_date: date) -> int:
    metric_date = _metric_date_value(summary)
    return (as_of_date - metric_date).days if metric_date else 10_000


def _pricing_allowed_conclusion(
    readiness_status: str,
    occupancy_diagnosis: str | None,
    *,
    bundle_demand_ambiguous: bool = False,
    rate_distribution_skewed: bool = False,
) -> str:
    if bundle_demand_ambiguous:
        return "Reconcile possible component-unit holds to direct bundle reservations before inferring demand or changing price."
    if occupancy_diagnosis == "inventory_blocked":
        return "Verify whether calendar blocks are intentional; do not infer demand weakness or change price."
    if readiness_status == "blocked":
        return "Resolve the stated data or availability gap before making a pricing recommendation."
    if rate_distribution_skewed:
        return "Review event/override dates and ordinary open dates separately; do not infer a broad rate change from the 30-day mean."
    if readiness_status == "caution":
        return "Use the row for manual review only; corroborate open dates and peer fit before changing price."
    return "Evaluate targeted changes on currently available dates using booking pace and comparable portfolio peers."


def _listing_inventory_mode(*names: str | None) -> str:
    text = " ".join(str(name or "") for name in names).lower()
    bundle_patterns = (
        r"\bcombined\b",
        r"\bbook\s+(?:any\s+)?[2-9]\b",
        r"\b[2-9]\s+cabins?\b",
    )
    if any(re.search(pattern, text) for pattern in bundle_patterns):
        return "aggregate_bundle"
    internal_name = str(names[0] or "")
    if internal_name.count(",") >= 2:
        return "aggregate_bundle"
    return "individual"


def _listing_inventory_class(
    *names: str | None,
    bedrooms: int | None = None,
) -> str:
    if _listing_inventory_mode(*names) == "aggregate_bundle":
        return "aggregate_bundle"
    if bedrooms is not None:
        try:
            bedroom_count = int(bedrooms)
        except (TypeError, ValueError):
            bedroom_count = 0
        if bedroom_count > 0:
            return f"{bedroom_count}_bedroom"
    text = " ".join(str(name or "") for name in names).lower()
    bedroom_match = re.search(r"\b([1-9])\s*(?:br|bed(?:room)?s?)\b", text)
    if bedroom_match:
        return f"{int(bedroom_match.group(1))}_bedroom"
    if re.search(r"\bstudio\b", text):
        return "studio"
    return "unspecified"


def _is_inventory_mode_comparable(target: dict[str, Any], peer: dict[str, Any]) -> bool:
    target_mode = str(target.get("inventory_mode") or "individual")
    peer_mode = str(peer.get("inventory_mode") or "individual")
    if target_mode != peer_mode:
        return False
    target_class = str(target.get("inventory_class") or "unspecified")
    peer_class = str(peer.get("inventory_class") or "unspecified")
    if target_class != peer_class:
        return False
    try:
        target_capacity = int(target.get("accommodates") or 0)
        peer_capacity = int(peer.get("accommodates") or 0)
    except (TypeError, ValueError):
        return False
    if target_capacity and peer_capacity:
        capacity_ratio = min(target_capacity, peer_capacity) / max(target_capacity, peer_capacity)
        return capacity_ratio >= 0.67
    return True


def _count_values(values: Any) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        key = str(value or "unknown")
        counts[key] = counts.get(key, 0) + 1
    return counts


def _pricing_context_source_gap(row: BusinessFact | None) -> dict[str, Any] | None:
    if not row:
        return {
            "source_key": "pricelabs",
            "fact_type": "pricing_context",
            "status": "missing",
            "message": "No active PriceLabs pricing context is available for this mapped listing.",
        }
    payload = row.fact_payload or {}
    status = payload.get("status") or row.text_value
    raw_payload = payload.get("payload") if isinstance(payload.get("payload"), dict) else {}
    prices_payload = raw_payload.get("prices") if isinstance(raw_payload, dict) else {}
    source_statuses = payload.get("source_statuses") if isinstance(payload.get("source_statuses"), dict) else {}
    prices_status = source_statuses.get("prices")
    error_status = prices_status or (prices_payload or {}).get("error_status")
    message = payload.get("error_message") or (prices_payload or {}).get("error")
    if status == "ok" and prices_status in (None, "ok") and not message:
        return None
    return {
        "source_key": "pricelabs",
        "fact_type": "pricing_context",
        "source_fact_id": row.business_fact_id,
        "status": status,
        "error_status": error_status,
        "message": message or "PriceLabs daily price data is unavailable for this listing.",
        "source_statuses": as_json_safe(source_statuses or {}),
    }


def _source_quality_gaps(fact_freshness: dict[str, dict[str, Any]], *, now: datetime) -> list[dict[str, Any]]:
    gaps: list[dict[str, Any]] = []
    for target in FOUNDATION_SOURCE_FRESHNESS_TARGETS:
        if target.get("kind") != "fact_occurred":
            continue
        fact_type = str(target.get("fact_type") or "")
        freshness = fact_freshness.get(fact_type) or {}
        latest_at = _parse_datetime(freshness.get("latest_occurred_at"))
        max_age_hours = float(target.get("max_age_hours") or 0)
        if latest_at is None:
            gaps.append(
                {
                    "source_key": target.get("source_key"),
                    "fact_type": fact_type,
                    "name": target.get("name"),
                    "status": "missing",
                    "message": f"{target.get('name')} has no active fact evidence.",
                }
            )
            continue
        age_hours = round((now - latest_at).total_seconds() / 3600, 1)
        if max_age_hours and age_hours > max_age_hours:
            gaps.append(
                {
                    "source_key": target.get("source_key"),
                    "fact_type": fact_type,
                    "name": target.get("name"),
                    "status": "stale",
                    "latest_at": latest_at.isoformat(),
                    "age_hours": age_hours,
                    "max_age_hours": max_age_hours,
                    "message": f"{target.get('name')} is {round(age_hours / 24, 1)} days old; target is {round(max_age_hours / 24, 1)} days.",
                }
            )
    return gaps


def _metric_selection_rank(row: Any) -> tuple[str, int, datetime, int]:
    target_horizon = METRIC_TARGET_HORIZONS.get(getattr(row, "metric_name", None))
    horizon_score = int(target_horizon is not None and getattr(row, "horizon_days", None) == target_horizon)
    return (
        row.metric_date.isoformat() if getattr(row, "metric_date", None) else "",
        horizon_score,
        getattr(row, "updated_at", None) or datetime.min,
        int(getattr(row, "business_metric_snapshot_id", 0) or 0),
    )


def _is_high_guest_risk(risk: float | None) -> bool:
    if risk is None:
        return False
    return risk >= (0.65 if risk <= 1 else 65)


def _is_low_review_rating(review: float | None) -> bool:
    if review is None:
        return False
    return review < (4.6 if review <= 5 else 8.0)


def _median(values: list[float]) -> float | None:
    values = sorted(values)
    if not values:
        return None
    midpoint = len(values) // 2
    if len(values) % 2:
        return values[midpoint]
    return (values[midpoint - 1] + values[midpoint]) / 2


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None
