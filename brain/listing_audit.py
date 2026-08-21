#!/usr/bin/env python3
"""Portfolio-wide listing health audit backed by Hostaway and PriceLabs."""

from __future__ import annotations

import html
import ipaddress
import json
import logging
import os
import re
import statistics
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import requests
from sqlalchemy import func

import dashboard.config as config
from brain.models import (
    BookingHealthAnalysis,
    ListingAuditRun,
    ListingAuditSnapshot,
    Portfolio,
    PortfolioListing,
    PriceLabsSnapshot,
    as_json_safe,
    get_session as get_brain_session,
    init_listing_audit_tables,
)
from database.models import Listing, get_session as get_main_session
from sync.api_client import HostawayAPIClient

logger = logging.getLogger(__name__)

AUDIT_TIMEZONE = os.getenv("LISTING_AUDIT_TIMEZONE", "Asia/Kuala_Lumpur")
PUBLIC_PAGE_TIMEOUT = int(os.getenv("LISTING_AUDIT_PAGE_TIMEOUT_SECONDS", "12"))
PUBLIC_PAGE_WORKERS = max(1, min(int(os.getenv("LISTING_AUDIT_PAGE_WORKERS", "8")), 16))
HOSTAWAY_DETAIL_WORKERS = max(1, min(int(os.getenv("LISTING_AUDIT_HOSTAWAY_WORKERS", "8")), 16))
CHECK_PUBLIC_PAGES = os.getenv("LISTING_AUDIT_CHECK_PUBLIC_PAGES", "true").lower() not in {
    "0",
    "false",
    "no",
}

CHANNEL_LABELS = {
    "airbnb": "Airbnb",
    "vrbo": "Vrbo",
    "bookingcom": "Booking.com",
    "direct": "Direct booking",
}

TITLE_PATTERN = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
META_DESCRIPTION_PATTERN = re.compile(
    r'<meta[^>]+(?:name|property)=["\'](?:description|og:description)["\'][^>]+content=["\'](.*?)["\']',
    re.IGNORECASE | re.DOTALL,
)
TAG_PATTERN = re.compile(r"<[^>]+>")


@dataclass(frozen=True)
class PriceLabsAuditContext:
    """Latest PriceLabs refresh plus the safest snapshot to use for the audit."""

    latest: Any | None
    effective: Any | None
    using_fallback: bool = False


class ListingAuditRunner:
    """Collect, score, and persist one listing audit run."""

    def __init__(self, *, brain_session=None, main_session=None, hostaway_client=None):
        self.brain_session = brain_session or get_brain_session()
        self.main_session = main_session or get_main_session(config.MAIN_DATABASE_PATH)
        self.hostaway = hostaway_client or HostawayAPIClient()
        self._owns_brain_session = brain_session is None
        self._owns_main_session = main_session is None

    def close(self):
        if self._owns_brain_session:
            self.brain_session.close()
        if self._owns_main_session:
            self.main_session.close()

    def run(self, *, cadence: str = "daily", deep: bool = False) -> dict[str, Any]:
        cadence = "weekly" if cadence == "weekly" else "daily"
        init_listing_audit_tables()
        local_today = datetime.now(ZoneInfo(AUDIT_TIMEZONE)).date()
        audit_run = ListingAuditRun(
            cadence=cadence,
            status="running",
            snapshot_date=local_today,
            started_at=datetime.utcnow(),
        )
        self.brain_session.add(audit_run)
        self.brain_session.commit()

        try:
            refresh_result = self._refresh_sources(cadence=cadence, deep=deep)
            details = self._active_listing_details()
            listing_ids = sorted(details)
            analyses = self._latest_booking_analyses(listing_ids)
            pricelabs = self._latest_pricelabs_snapshots(listing_ids)
            portfolio_map, portfolio_names = self._portfolio_maps(listing_ids)
            page_results = self._public_page_results(details)

            severity_counts = {"critical": 0, "high": 0, "watch": 0, "healthy": 0}
            for listing_id in listing_ids:
                result = build_listing_audit_result(
                    details[listing_id],
                    booking_analysis=analyses.get(listing_id),
                    pricelabs_snapshot=pricelabs.get(listing_id),
                    public_pages={
                        channel: page_results.get((listing_id, channel))
                        for channel in CHANNEL_LABELS
                    },
                    portfolio_id=portfolio_map.get(listing_id),
                    portfolio_name=portfolio_names.get(portfolio_map.get(listing_id)),
                )
                severity_counts[result["severity"]] += 1
                self.brain_session.add(
                    ListingAuditSnapshot(
                        run_id=audit_run.listing_audit_run_id,
                        portfolio_id=result["portfolio_id"],
                        listing_id=result["listing_id"],
                        listing_name=result["listing_name"],
                        snapshot_date=local_today,
                        severity=result["severity"],
                        health_score=result["health_score"],
                        booking_health=as_json_safe(result["booking_health"]),
                        pricing_health=as_json_safe(result["pricing_health"]),
                        market_comparison=as_json_safe(result["market_comparison"]),
                        online_assets=as_json_safe(result["online_assets"]),
                        action_items=as_json_safe(result["action_items"]),
                        source_statuses=as_json_safe(result["source_statuses"]),
                        raw_payload=as_json_safe(result["raw_payload"]),
                    )
                )

            audit_run.status = "completed"
            audit_run.listing_count = len(listing_ids)
            audit_run.critical_count = severity_counts["critical"]
            audit_run.high_count = severity_counts["high"]
            audit_run.watch_count = severity_counts["watch"]
            audit_run.healthy_count = severity_counts["healthy"]
            audit_run.source_statuses = as_json_safe(
                {
                    "hostaway": "ok" if listing_ids else "empty",
                    "source_refresh": refresh_result,
                    "public_page_checks": {
                        "enabled": CHECK_PUBLIC_PAGES,
                        "requested": len(page_results),
                        "ok": sum(1 for item in page_results.values() if item.get("status") == "ok"),
                    },
                }
            )
            audit_run.completed_at = datetime.utcnow()
            self.brain_session.commit()
            return {
                "status": "completed",
                "audit_run_id": audit_run.listing_audit_run_id,
                "cadence": cadence,
                "snapshot_date": local_today.isoformat(),
                "listing_count": len(listing_ids),
                "severity_counts": severity_counts,
            }
        except Exception as exc:
            self.brain_session.rollback()
            persisted_run = self.brain_session.query(ListingAuditRun).get(audit_run.listing_audit_run_id)
            if persisted_run:
                persisted_run.status = "error"
                persisted_run.error_message = str(exc)[:4000]
                persisted_run.completed_at = datetime.utcnow()
                self.brain_session.commit()
            logger.error("Listing audit failed: %s", exc, exc_info=True)
            raise

    def _refresh_sources(self, *, cadence: str, deep: bool) -> dict[str, Any]:
        from brain.services import BrainRunService
        from sync.sync_manager import incremental_sync

        hostaway_pull = incremental_sync(force=bool(deep), include_messages=False)
        runner = BrainRunService()
        try:
            result = runner.refresh_source_snapshots(
                run_type=f"listing_audit_source_{cadence}",
                pull_hostaway=False,
                force_hostaway=False,
                include_booking_analysis=True,
            )
            result["hostaway_pull"] = as_json_safe(hostaway_pull)
            return result
        finally:
            runner.close()

    def _active_listing_details(self) -> dict[int, dict[str, Any]]:
        summaries: list[dict[str, Any]] = []
        offset = 0
        while True:
            page = self.hostaway.get_listings_page(limit=100, offset=offset)
            if page is None:
                raise RuntimeError(f"Hostaway listing inventory failed at offset {offset}")
            if not page:
                break
            summaries.extend(page)
            if len(page) < 100:
                break
            offset += 100

        details: dict[int, dict[str, Any]] = {}
        with ThreadPoolExecutor(max_workers=HOSTAWAY_DETAIL_WORKERS) as executor:
            futures = {
                executor.submit(self.hostaway.get_listing, int(row["id"])): row
                for row in summaries
                if row.get("id")
            }
            for future in as_completed(futures):
                summary = futures[future]
                listing_id = int(summary["id"])
                try:
                    detail = future.result() or summary
                except Exception as exc:
                    logger.warning("Hostaway listing detail failed for %s: %s", listing_id, exc)
                    detail = summary
                details[listing_id] = {**summary, **detail}
        return details

    def _latest_booking_analyses(self, listing_ids: list[int]) -> dict[int, BookingHealthAnalysis]:
        if not listing_ids:
            return {}
        self.brain_session.expire_all()
        rows = (
            self.brain_session.query(BookingHealthAnalysis)
            .filter(BookingHealthAnalysis.listing_id.in_(listing_ids))
            .order_by(
                BookingHealthAnalysis.snapshot_date.desc(),
                BookingHealthAnalysis.updated_at.desc(),
                BookingHealthAnalysis.booking_health_analysis_id.desc(),
            )
            .all()
        )
        latest: dict[int, BookingHealthAnalysis] = {}
        for row in rows:
            latest.setdefault(int(row.listing_id), row)
        return latest

    def _latest_pricelabs_snapshots(self, listing_ids: list[int]) -> dict[int, PriceLabsAuditContext]:
        if not listing_ids:
            return {}
        rows = (
            self.brain_session.query(PriceLabsSnapshot)
            .filter(PriceLabsSnapshot.listing_id.in_(listing_ids))
            .order_by(
                PriceLabsSnapshot.snapshot_date.desc(),
                PriceLabsSnapshot.created_at.desc(),
                PriceLabsSnapshot.pricelabs_snapshot_id.desc(),
            )
            .all()
        )
        grouped: dict[int, list[PriceLabsSnapshot]] = {}
        for row in rows:
            grouped.setdefault(int(row.listing_id), []).append(row)
        return {
            listing_id: resolve_pricelabs_audit_context(listing_rows)
            for listing_id, listing_rows in grouped.items()
        }

    def _portfolio_maps(self, listing_ids: list[int]) -> tuple[dict[int, int], dict[int, str]]:
        mappings = (
            self.brain_session.query(PortfolioListing)
            .filter(PortfolioListing.listing_id.in_(listing_ids))
            .all()
        ) if listing_ids else []
        portfolio_map = {int(row.listing_id): int(row.portfolio_id) for row in mappings}
        portfolio_ids = sorted(set(portfolio_map.values()))
        names = {
            int(row.portfolio_id): row.name
            for row in self.brain_session.query(Portfolio).filter(Portfolio.portfolio_id.in_(portfolio_ids)).all()
        } if portfolio_ids else {}
        return portfolio_map, names

    def _public_page_results(self, details: dict[int, dict[str, Any]]) -> dict[tuple[int, str], dict[str, Any]]:
        if not CHECK_PUBLIC_PAGES:
            return {}
        targets: dict[tuple[int, str], str] = {}
        for listing_id, detail in details.items():
            for channel, url in channel_urls(detail).items():
                if url and is_safe_public_url(url):
                    targets[(listing_id, channel)] = url

        results: dict[tuple[int, str], dict[str, Any]] = {}
        with ThreadPoolExecutor(max_workers=PUBLIC_PAGE_WORKERS) as executor:
            futures = {
                executor.submit(fetch_public_page, url, channel): key
                for key, url in targets.items()
                for channel in (key[1],)
            }
            for future in as_completed(futures):
                key = futures[future]
                try:
                    results[key] = future.result()
                except Exception as exc:
                    results[key] = {
                        "status": "unavailable",
                        "url": targets[key],
                        "summary": "The public page check failed.",
                        "error": str(exc)[:500],
                    }
        return results


def build_listing_audit_result(
    detail: dict[str, Any],
    *,
    booking_analysis: Any | None,
    pricelabs_snapshot: Any | None,
    public_pages: dict[str, dict[str, Any] | None] | None = None,
    portfolio_id: int | None = None,
    portfolio_name: str | None = None,
) -> dict[str, Any]:
    """Create the persisted, browser-safe result for one listing."""
    listing_id = int(detail.get("id") or detail.get("listingId"))
    listing_name = (
        detail.get("internalListingName")
        or detail.get("name")
        or detail.get("externalListingName")
        or f"Property {listing_id}"
    )
    public_pages = public_pages or {}
    assets = [
        build_channel_asset(detail, channel, public_pages.get(channel))
        for channel in CHANNEL_LABELS
    ]
    booking_health = booking_health_payload(booking_analysis)
    pricing_health, market_comparison = pricing_market_payload(pricelabs_snapshot)

    booking_score = booking_health["score"]
    configured_asset_scores = [
        float(asset["score"])
        for asset in assets
        if asset["status"] not in {"not_configured", "not_exported"}
    ]
    asset_score = round(statistics.mean(configured_asset_scores), 1) if configured_asset_scores else 25.0
    pricing_score = float(pricing_health["score"])
    health_score = round((booking_score * 0.45) + (asset_score * 0.35) + (pricing_score * 0.20), 1)
    severity = severity_for_score(health_score)

    actions: list[dict[str, Any]] = []
    for action in booking_health.get("actions") or []:
        actions.append({"priority": priority_for_severity(booking_health["severity"]), "category": "Booking", "text": action})
    for action in pricing_health.get("actions") or []:
        actions.append({"priority": "high" if pricing_health["score"] < 60 else "medium", "category": "Pricing", "text": action})
    for asset in assets:
        for action in asset.get("actions") or []:
            actions.append({
                "priority": "high" if asset["status"] in {"critical", "not_found"} else "medium",
                "category": asset["label"],
                "text": action,
            })
    actions = dedupe_actions(actions)[:12]

    source_statuses = {
        "hostaway": "ok",
        "booking_health": booking_health.get("status"),
        "pricelabs": pricing_health.get("status"),
        "online_assets": {asset["channel"]: asset["status"] for asset in assets},
    }
    return {
        "portfolio_id": portfolio_id,
        "listing_id": listing_id,
        "listing_name": listing_name,
        "severity": severity,
        "health_score": health_score,
        "booking_health": booking_health,
        "pricing_health": pricing_health,
        "market_comparison": market_comparison,
        "online_assets": assets,
        "action_items": actions,
        "source_statuses": source_statuses,
        "raw_payload": {
            "portfolio_name": portfolio_name,
            "thumbnail_url": detail.get("thumbnailUrl"),
            "city": detail.get("city"),
            "state": detail.get("state"),
            "currency": detail.get("currencyCode") or detail.get("currency"),
            "photo_count": len(detail.get("listingImages") or []),
        },
    }


def build_channel_asset(
    detail: dict[str, Any],
    channel: str,
    page_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Normalize one marketplace or direct-booking asset."""
    listing_id = int(detail.get("id") or detail.get("listingId") or 0)
    overrides = configured_channel_urls().get(str(listing_id), {})
    urls = channel_urls(detail)
    url = overrides.get(channel) or urls.get(channel)
    photo_count = len(detail.get("listingImages") or [])

    if channel == "airbnb":
        export_status = _text(detail.get("airbnbExportStatus"))
        title = _text(detail.get("airbnbName") or detail.get("name"))
        description = _join_text(
            detail.get("airbnbSummary"),
            detail.get("airbnbSpace"),
            detail.get("airbnbNeighborhoodOverview"),
        )
        configured = export_status == "exported" or bool(url)
    elif channel == "vrbo":
        export_status = _text(detail.get("vrboExportStatus"))
        title = _text(detail.get("homeawayPropertyHeadline") or detail.get("homeawayPropertyName") or detail.get("name"))
        description = _join_text(detail.get("homeawayPropertyDescription"))
        configured = export_status == "exported" or bool(url)
    elif channel == "bookingcom":
        export_status = _text(detail.get("bookingcomExportStatus"))
        title = _text(detail.get("bookingcomPropertyName") or detail.get("bookingcomPropertyRoomName") or detail.get("name"))
        description = _join_text(detail.get("bookingcomPropertyDescription"))
        configured = export_status == "exported"
    else:
        export_status = "connected" if url else "missing"
        title = _text(detail.get("name") or detail.get("externalListingName"))
        description = _join_text(detail.get("description"))
        configured = bool(url)

    page = dict(page_result or {})
    if not page:
        page = {
            "status": "not_checked" if url else "missing_url",
            "url": url,
            "summary": "No public URL is available for an automated page check." if not url else "Public page check is pending.",
        }
    page_title = _text(page.get("title"))
    page_description = _text(page.get("meta_description"))
    effective_title = page_title or title
    effective_description = page_description or description

    if not configured:
        score = 20.0 if title and description else 5.0
        status = "not_configured" if channel == "direct" else "not_exported"
    else:
        score = channel_profile_score(
            title=effective_title,
            description=effective_description,
            photo_count=photo_count,
            has_url=bool(url) or channel == "bookingcom",
            page_status=page.get("status"),
        )
        if page.get("status") == "not_found":
            status = "critical"
        elif score >= 82 and page.get("status") in {"ok", "not_checked", "missing_url"}:
            status = "healthy"
        elif score >= 62:
            status = "watch"
        else:
            status = "high"

    actions: list[str] = []
    label = CHANNEL_LABELS[channel]
    if not configured:
        if channel == "direct":
            actions.append("Add the direct-booking URL for this property so availability, copy, and conversion can be checked.")
        else:
            actions.append(f"Confirm whether this property should be live on {label}; Hostaway does not show it as exported.")
    else:
        if not url and channel != "bookingcom":
            actions.append(f"Store the public {label} URL in Hostaway or the audit URL mapping.")
        if len(effective_title) < 24:
            actions.append(f"Strengthen the {label} title with the property’s clearest guest benefit and location cue.")
        if len(effective_description) < 220:
            actions.append(f"Expand the {label} description with the stay experience, differentiators, and practical expectations.")
        if photo_count < 20:
            actions.append(f"Increase and reorder {label} photo coverage; only {photo_count} source photos are available.")
        if page.get("status") == "not_found":
            actions.append(f"Repair the {label} public URL or export immediately; the guest page appears unavailable.")
        elif page.get("status") in {"blocked", "unavailable"}:
            actions.append(f"Manually verify the {label} guest page because the automated public check could not confirm it.")

    return {
        "channel": channel,
        "label": label,
        "status": status,
        "score": round(score, 1),
        "configured": configured,
        "export_status": export_status or "missing",
        "url": url,
        "title": effective_title,
        "description_excerpt": effective_description[:280],
        "title_length": len(effective_title),
        "description_length": len(effective_description),
        "photo_count": photo_count,
        "page": page,
        "actions": actions[:4],
    }


def booking_health_payload(analysis: Any | None) -> dict[str, Any]:
    if not analysis:
        return {
            "status": "missing",
            "severity": "high",
            "score": 35.0,
            "horizons": [],
            "pattern": "No current forward-booking analysis is available.",
            "actions": ["Refresh Hostaway reservations and forward calendar data."],
        }
    severity = _text(getattr(analysis, "severity", None)) or "watch"
    score = {"healthy": 92.0, "watch": 72.0, "high": 52.0, "critical": 28.0}.get(severity, 65.0)
    horizons = []
    for item in getattr(analysis, "horizons", None) or []:
        row = dict(item or {})
        rate = _number(row.get("occupancy_rate"))
        if rate is not None:
            row["occupancy_percent"] = round(rate * 100 if rate <= 1 else rate, 1)
        horizons.append(row)
    return {
        "status": "ok",
        "severity": severity,
        "score": score,
        "snapshot_date": getattr(analysis, "snapshot_date", None).isoformat() if getattr(analysis, "snapshot_date", None) else None,
        "horizons": horizons,
        "pattern": _text(getattr(analysis, "booking_pattern", None) or getattr(analysis, "opinion", None)),
        "opinion": _text(getattr(analysis, "opinion", None)),
        "actions": [str(item) for item in (getattr(analysis, "action_items", None) or []) if item][:6],
    }


def resolve_pricelabs_audit_context(rows: list[Any]) -> PriceLabsAuditContext:
    """Prefer the latest refresh, but retain prior good data after transient failures."""
    if not rows:
        return PriceLabsAuditContext(latest=None, effective=None)
    latest = rows[0]
    if pricelabs_snapshot_is_usable(latest):
        return PriceLabsAuditContext(latest=latest, effective=latest)
    if pricelabs_snapshot_is_transient_failure(latest):
        fallback = next((row for row in rows[1:] if pricelabs_snapshot_is_usable(row)), None)
        if fallback:
            return PriceLabsAuditContext(latest=latest, effective=fallback, using_fallback=True)
    return PriceLabsAuditContext(latest=latest, effective=latest)


def pricelabs_snapshot_is_usable(snapshot: Any | None) -> bool:
    if not snapshot:
        return False
    status = _text(getattr(snapshot, "status", None)).lower()
    raw = getattr(snapshot, "raw_payload", None) or {}
    return status in {"ok", "partial"} and bool(raw.get("prices") or raw.get("metrics"))


def pricelabs_snapshot_is_transient_failure(snapshot: Any | None) -> bool:
    status = _text(getattr(snapshot, "status", None)).lower()
    return status in {"unavailable", "unknown", "error", "api_error"}


def pricelabs_refresh_issue(snapshot: Any | None) -> tuple[str, str]:
    error = _text(getattr(snapshot, "error_message", None)).lower()
    if "429" in error or "too many requests" in error or "rate limit" in error:
        return "rate_limited", "rate-limited"
    if "timeout" in error or "timed out" in error:
        return "timed_out", "timed out"
    if any(token in error for token in ("500", "502", "503", "504")):
        return "service_error", "temporarily unavailable"
    return "temporarily_unavailable", "temporarily unavailable"


def pricing_market_payload(snapshot: Any | None) -> tuple[dict[str, Any], dict[str, Any]]:
    context = snapshot if isinstance(snapshot, PriceLabsAuditContext) else PriceLabsAuditContext(snapshot, snapshot)
    latest_snapshot = context.latest
    effective_snapshot = context.effective
    if not effective_snapshot:
        return (
            {
                "status": "missing",
                "connection_status": "unknown",
                "freshness_status": "missing",
                "using_fallback": False,
                "score": 25.0,
                "summary": "No current PriceLabs snapshot is available.",
                "actions": ["Connect or refresh PriceLabs before changing rates or stay restrictions."],
            },
            {"status": "missing", "summary": "Market benchmark unavailable."},
        )

    raw = getattr(effective_snapshot, "raw_payload", None) or {}
    prices_payload = raw.get("prices") or {}
    price_rows = prices_payload.get("data") or []
    usable_rows = [row for row in price_rows[:90] if isinstance(row, dict)]
    future_prices = [_number(row.get("price")) for row in usable_rows if not _truthy(row.get("unbookable"))]
    future_prices = [value for value in future_prices if value is not None]
    min_stays = [_number(row.get("min_stay")) for row in usable_rows]
    min_stays = [int(value) for value in min_stays if value and value > 0]
    unbookable_days = sum(1 for row in usable_rows if _truthy(row.get("unbookable")))

    metrics = ((raw.get("metrics") or {}).get("data") or {})
    listing_metrics = metrics.get("listing_level") or {}
    market_metrics = metrics.get("market_level") or {}
    listing_occupancy = horizon_metric(listing_metrics.get("occupancy"), 30)
    market_occupancy = horizon_metric(market_metrics.get("occupancy"), 30)
    difference = None
    if listing_occupancy is not None and market_occupancy is not None:
        difference = round(listing_occupancy - market_occupancy, 1)

    effective_status = _text(getattr(effective_snapshot, "status", None)) or "unknown"
    refresh_status = _text(getattr(latest_snapshot, "status", None)) or effective_status
    display_status = "stale" if context.using_fallback else effective_status
    score = 88.0 if effective_status == "ok" else 58.0 if effective_status == "partial" else 30.0
    actions: list[str] = []
    refresh_issue_code = None
    refresh_issue_label = None
    if context.using_fallback:
        refresh_issue_code, refresh_issue_label = pricelabs_refresh_issue(latest_snapshot)
        actions.append(
            f"PriceLabs is connected, but the latest refresh was {refresh_issue_label}; retry the data refresh before making time-sensitive pricing changes."
        )
        score = max(70.0, score - 6.0)
    elif effective_status != "ok":
        normalized_status = effective_status.lower()
        if normalized_status == "listing_toggle_off":
            actions.append("PriceLabs is connected, but pricing sync is turned off for this listing; confirm whether that is intentional.")
        elif normalized_status == "not_configured":
            actions.append("Configure PriceLabs API access before relying on automated rate guidance.")
        else:
            actions.append("No successful PriceLabs snapshot is available; verify the listing mapping and API access, then retry the refresh.")
    if difference is not None and difference <= -10:
        actions.append(f"The 30-day PriceLabs occupancy is {abs(difference):.0f} points behind market; review price position and restrictions on open dates.")
        score -= 12
    if min_stays and max(min_stays) >= 4 and (listing_occupancy is None or listing_occupancy < 60):
        actions.append("Minimum-stay rules reach four nights or more while forward occupancy is soft; inspect short gaps and weekday restrictions.")
        score -= 8
    if unbookable_days >= 5:
        actions.append(f"PriceLabs marks {unbookable_days} of the next {len(usable_rows)} reviewed days unbookable; confirm those restrictions are intentional.")
        score -= 8
    score = max(0.0, min(score, 100.0))

    avg_price = round(statistics.mean(future_prices), 2) if future_prices else None
    currency = prices_payload.get("currency") or "USD"
    summary_parts = []
    if avg_price is not None:
        summary_parts.append(f"Average recommended rate {currency} {avg_price:,.0f}")
    if min_stays:
        summary_parts.append(f"minimum stay {min(min_stays)}–{max(min_stays)} nights")
    if listing_occupancy is not None:
        summary_parts.append(f"30-day occupancy {listing_occupancy:.0f}%")

    comparison_status = "unavailable"
    comparison_summary = "PriceLabs did not return a comparable 30-day market occupancy metric."
    if difference is not None:
        comparison_status = "ahead" if difference >= 5 else "behind" if difference <= -5 else "in_line"
        direction = "ahead of" if difference > 0 else "behind" if difference < 0 else "in line with"
        comparison_summary = f"This listing is {abs(difference):.0f} occupancy points {direction} the 30-day market benchmark."

    summary = "; ".join(summary_parts) or f"PriceLabs status is {effective_status}."
    effective_date = getattr(effective_snapshot, "snapshot_date", None)
    normalized_effective_status = effective_status.lower()
    connection_status = (
        "connected"
        if pricelabs_snapshot_is_usable(effective_snapshot) or normalized_effective_status == "listing_toggle_off"
        else "not_configured"
        if normalized_effective_status == "not_configured"
        else "unknown"
    )
    if context.using_fallback:
        date_label = effective_date.isoformat() if effective_date else "an earlier run"
        summary = (
            f"Connected; using the last successful PriceLabs data from {date_label} because the latest refresh was {refresh_issue_label}. "
            f"{summary}."
        )

    return (
        {
            "status": display_status,
            "connection_status": connection_status,
            "freshness_status": "stale" if context.using_fallback else "current",
            "using_fallback": context.using_fallback,
            "refresh_status": refresh_status,
            "refresh_issue": refresh_issue_code,
            "data_snapshot_date": effective_date.isoformat() if effective_date else None,
            "score": round(score, 1),
            "summary": summary,
            "average_price": avg_price,
            "min_price": min(future_prices) if future_prices else None,
            "max_price": max(future_prices) if future_prices else None,
            "currency": currency,
            "min_stay_min": min(min_stays) if min_stays else None,
            "min_stay_max": max(min_stays) if min_stays else None,
            "unbookable_days": unbookable_days,
            "actions": actions,
        },
        {
            "status": comparison_status,
            "listing_occupancy": listing_occupancy,
            "market_occupancy": market_occupancy,
            "difference_points": difference,
            "summary": comparison_summary,
        },
    )


def channel_profile_score(*, title: str, description: str, photo_count: int, has_url: bool, page_status: str | None) -> float:
    score = 25.0
    score += 10.0 if has_url else 0.0
    score += min(len(title) / 35.0, 1.0) * 20.0
    score += min(len(description) / 300.0, 1.0) * 25.0
    score += min(max(photo_count, 0) / 20.0, 1.0) * 20.0
    if page_status == "not_found":
        score -= 35.0
    elif page_status in {"blocked", "unavailable"}:
        score -= 8.0
    return max(0.0, min(score, 100.0))


def channel_urls(detail: dict[str, Any]) -> dict[str, str | None]:
    listing_id = str(detail.get("id") or detail.get("listingId") or "")
    overrides = configured_channel_urls().get(listing_id, {})
    return {
        "airbnb": normalize_url(overrides.get("airbnb") or detail.get("airbnbListingUrl")),
        "vrbo": normalize_url(overrides.get("vrbo") or detail.get("vrboListingUrl")),
        "bookingcom": normalize_url(overrides.get("bookingcom") or overrides.get("booking.com")),
        "direct": normalize_url(overrides.get("direct") or first_url(detail.get("bookingEngineUrls"))),
    }


def configured_channel_urls() -> dict[str, dict[str, str]]:
    raw = os.getenv("LISTING_AUDIT_CHANNEL_URLS_JSON", "").strip()
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        logger.warning("LISTING_AUDIT_CHANNEL_URLS_JSON is not valid JSON")
        return {}
    if not isinstance(payload, dict):
        return {}
    result: dict[str, dict[str, str]] = {}
    for listing_id, channels in payload.items():
        if isinstance(channels, dict):
            result[str(listing_id)] = {
                str(channel).lower(): str(url)
                for channel, url in channels.items()
                if normalize_url(url)
            }
    return result


def first_url(value: Any) -> str | None:
    if isinstance(value, str):
        return normalize_url(value)
    if isinstance(value, dict):
        for key in ("url", "bookingEngineUrl", "bookingUrl", "link", "value"):
            found = normalize_url(value.get(key))
            if found:
                return found
        for nested in value.values():
            found = first_url(nested)
            if found:
                return found
    if isinstance(value, list):
        for nested in value:
            found = first_url(nested)
            if found:
                return found
    return None


def normalize_url(value: Any) -> str | None:
    text = str(value or "").strip()
    return text if text.startswith(("https://", "http://")) else None


def is_safe_public_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return False
        if parsed.hostname.lower() in {"localhost", "localhost.localdomain"}:
            return False
        try:
            return not ipaddress.ip_address(parsed.hostname).is_private
        except ValueError:
            return True
    except ValueError:
        return False


def fetch_public_page(url: str, channel: str) -> dict[str, Any]:
    """Fetch public guest-facing metadata without using stored browser sessions."""
    checked_at = datetime.utcnow().isoformat()
    try:
        response = requests.get(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124 Safari/537.36",
                "Accept-Language": "en-US,en;q=0.9",
            },
            timeout=PUBLIC_PAGE_TIMEOUT,
            allow_redirects=True,
        )
    except Exception as exc:
        return {
            "status": "unavailable",
            "url": url,
            "checked_at": checked_at,
            "summary": f"{CHANNEL_LABELS.get(channel, channel)} did not return a public page.",
            "error": str(exc)[:500],
        }

    text = response.text or ""
    title = clean_html(match_html(TITLE_PATTERN, text))
    description = clean_html(match_html(META_DESCRIPTION_PATTERN, text))
    visible = clean_html(TAG_PATTERN.sub(" ", text))[:1800]
    lower = f"{title} {visible[:700]}".lower()
    if response.status_code in {404, 410} or "page not found" in lower:
        status = "not_found"
    elif response.status_code >= 400:
        status = "unavailable"
    elif any(token in lower for token in ("verify you are human", "captcha", "access denied", "robot check")):
        status = "blocked"
    else:
        status = "ok"
    return {
        "status": status,
        "url": response.url or url,
        "checked_at": checked_at,
        "http_status": response.status_code,
        "title": title,
        "meta_description": description,
        "summary": description or title or visible[:280] or "Public page responded without extractable text.",
    }


def horizon_metric(values: Any, horizon: int) -> float | None:
    if not isinstance(values, dict):
        return None
    value = _number(values.get(str(horizon), values.get(horizon)))
    if value is None:
        return None
    return round(value * 100 if 0 <= value <= 1 else value, 1)


def severity_for_score(score: float) -> str:
    if score < 45:
        return "critical"
    if score < 65:
        return "high"
    if score < 82:
        return "watch"
    return "healthy"


def priority_for_severity(severity: str) -> str:
    return "critical" if severity == "critical" else "high" if severity == "high" else "medium"


def dedupe_actions(actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    seen: set[str] = set()
    result = []
    for action in sorted(actions, key=lambda item: order.get(item.get("priority"), 9)):
        key = re.sub(r"\s+", " ", _text(action.get("text")).lower()).strip()
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(action)
    return result


def match_html(pattern: re.Pattern[str], text: str) -> str:
    match = pattern.search(text or "")
    return match.group(1) if match else ""


def clean_html(value: Any) -> str:
    return " ".join(html.unescape(str(value or "")).split())


def _join_text(*values: Any) -> str:
    return " ".join(_text(value) for value in values if _text(value))


def _text(value: Any) -> str:
    return str(value or "").strip()


def _number(value: Any) -> float | None:
    try:
        if value in (None, "") or isinstance(value, bool):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _truthy(value: Any) -> bool:
    return value is True or str(value).strip().lower() in {"1", "true", "yes", "unbookable"}
