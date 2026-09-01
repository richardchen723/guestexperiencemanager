#!/usr/bin/env python3
"""Portfolio-wide listing-quality audit backed by Hostaway and guest channels."""

from __future__ import annotations

import html
import ipaddress
import json
import logging
import os
import re
import statistics
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import requests

import dashboard.config as config
from brain.channel_page_audit import (
    automation_blocked_page_message,
    build_deep_channel_inspection,
    channel_destination_valid,
    deep_content_is_sparse,
    extract_google_vr_website_url,
    extract_deep_page_content,
    listing_amenities,
    render_deep_public_pages,
    rendered_page_error_message,
)
from brain.models import (
    ListingAuditRun,
    ListingAuditSnapshot,
    Portfolio,
    PortfolioListing,
    as_json_safe,
    get_session as get_brain_session,
    init_listing_audit_tables,
)
from database.models import Listing, get_session as get_main_session
from sync.api_client import HostawayAPIClient

logger = logging.getLogger(__name__)

AUDIT_TIMEZONE = os.getenv("LISTING_AUDIT_TIMEZONE", "America/New_York")
PUBLIC_PAGE_TIMEOUT = int(os.getenv("LISTING_AUDIT_PAGE_TIMEOUT_SECONDS", "12"))
PUBLIC_PAGE_WORKERS = max(1, min(int(os.getenv("LISTING_AUDIT_PAGE_WORKERS", "8")), 16))
HOSTAWAY_DETAIL_WORKERS = max(1, min(int(os.getenv("LISTING_AUDIT_HOSTAWAY_WORKERS", "8")), 16))
CONFIRMED_RENDER_FAILURE_KINDS = {"rendered_error", "http_error", "not_found", "invalid_domain", "non_html"}
COVER_IMAGE_TIMEOUT = max(2, min(int(os.getenv("LISTING_AUDIT_IMAGE_TIMEOUT_SECONDS", "5")), 15))
COVER_IMAGE_WORKERS = max(1, min(int(os.getenv("LISTING_AUDIT_IMAGE_WORKERS", "8")), 16))
COVER_IMAGE_CANDIDATE_LIMIT = max(1, min(int(os.getenv("LISTING_AUDIT_IMAGE_CANDIDATE_LIMIT", "5")), 20))
CHECK_PUBLIC_PAGES = os.getenv("LISTING_AUDIT_CHECK_PUBLIC_PAGES", "true").lower() not in {
    "0",
    "false",
    "no",
}

CHANNEL_LABELS = {
    "airbnb": "Airbnb",
    "vrbo": "Vrbo",
    "bookingcom": "Booking.com",
    "googlevr": "Google Vacation Rentals",
    "direct": "Direct booking",
}
AUDIT_SCOPE = "listing_quality_v2"
LISTING_FIELD_GROUPS = {
    "amenities": "amenities",
    "guest_notes": "policies",
    "house_rules": "policies",
    "title": "content",
    "description": "content",
    "location": "content",
    "page": "content",
}
LISTING_FIELD_PROBLEM_STATUSES = {"source_missing", "not_found_on_page", "mismatch", "partial"}
LISTING_CHECK_DEFINITIONS = {
    "links": {
        "label": "Channel links",
        "description": "Connection gaps, missing URLs, and confirmed guest-page errors.",
    },
    "amenities": {
        "label": "Amenities",
        "description": "Amenity coverage and channel-to-Hostaway consistency.",
    },
    "policies": {
        "label": "Guest policies",
        "description": "House rules, guest notes, and policy consistency.",
    },
    "content": {
        "label": "Listing content",
        "description": "Titles, descriptions, photos, location, and public-page details.",
    },
}
TECHNICAL_PAGE_PAYLOAD_MARKERS = (
    "window.productionhostname",
    "window.__gcstate__",
    "webpackchunk",
    "__next_data__",
    "function(){",
)
GOOGLE_VR_WEBSITE_CRITICAL_STATUSES = {"missing", "invalid", "not_found", "unavailable"}

TITLE_PATTERN = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
META_DESCRIPTION_PATTERN = re.compile(
    r'<meta[^>]+(?:name|property)=["\'](?:description|og:description)["\'][^>]+content=["\'](.*?)["\']',
    re.IGNORECASE | re.DOTALL,
)
TAG_PATTERN = re.compile(r"<[^>]+>")


def confirmed_channel_link_problem(asset: dict[str, Any]) -> bool:
    """Return true for a connection gap or a confirmed guest-path failure."""
    if not asset.get("configured"):
        return True
    if not asset.get("url"):
        return False
    page = asset.get("page") or {}
    website_status = str((page.get("website_link") or {}).get("status") or "")
    if (
        asset.get("channel") == "googlevr"
        and page.get("status") == "ok"
        and website_status in GOOGLE_VR_WEBSITE_CRITICAL_STATUSES
    ):
        return True
    status = page.get("status")
    if (
        status == "blocked"
        or page.get("failure_kind") == "automation_blocked"
        or automation_blocked_page_message(" ".join(str(page.get(key) or "") for key in ("summary", "title", "error")))
    ):
        return False
    if status in {"missing_url", "not_found", "invalid_domain", "non_html"}:
        return True
    if page.get("failure_kind") in CONFIRMED_RENDER_FAILURE_KINDS:
        return True
    try:
        return int(page.get("http_status") or 0) >= 400
    except (TypeError, ValueError):
        return False


def public_page_finding_message(page: dict[str, Any], label: str) -> str:
    """Keep script payloads and generated page state out of operator-facing cards."""
    summary = " ".join(_text(page.get("summary")).split())
    if any(marker in summary.casefold() for marker in TECHNICAL_PAGE_PAYLOAD_MARKERS):
        return f"The {label} guest page returned unreadable technical content; open the page and verify it manually."
    return summary or f"The {label} guest page did not return a valid listing."


def google_vr_website_finding(page: dict[str, Any]) -> tuple[str, str] | None:
    """Return the operator finding for Google VR's outbound Website link."""
    website = page.get("website_link") or {}
    status = str(website.get("status") or "")
    if not status or status in {"ok", "found"}:
        return None
    if status == "missing":
        return (
            "critical",
            "The Google Vacation Rentals page is missing its Website link. Add the guest-facing booking website so guests can continue from Google to the property site.",
        )
    if status == "invalid":
        return (
            "critical",
            "The Website link on Google Vacation Rentals is invalid or unsafe. Replace it with a valid public guest-facing URL.",
        )
    if status == "not_found":
        return (
            "critical",
            "The Website link on Google Vacation Rentals returns a page-not-found response. Repair the destination immediately.",
        )
    if status == "unavailable":
        http_status = website.get("http_status")
        suffix = f" (HTTP {http_status})" if http_status else ""
        return (
            "critical",
            f"The Website link on Google Vacation Rentals is not working{suffix}. Repair the destination immediately.",
        )
    if status in {"blocked", "unverified"}:
        return (
            "high",
            "Manually verify the Website link on Google Vacation Rentals; the audit could not confirm that the destination opens successfully.",
        )
    return None


def classify_listing_action(action: dict[str, Any]) -> str | None:
    """Map a recommendation to the listing-quality area persisted by this audit."""
    body = str(action.get("text") or "").casefold()
    text = f"{action.get('category') or ''} {body}".casefold()
    if any(token in text for token in ("url", "public page", "guest page", "not exported", "not connected", "connection")):
        return "links"
    if any(token in body for token in (
        "occupancy", "night stay", "night gap", "minimum stay", "min stay", "open dates", "pickup",
        "demand", "revenue", "rate", "pricing", "discount", "adr", "restriction",
    )):
        return None
    if "amenit" in text or "facilit" in text:
        return "amenities"
    if any(token in text for token in (
        "house rule", "guest note", "policy", "policies", "cancellation", "check-in", "check in",
        "checkout", "check-out", "check out",
    )):
        return "policies"
    if any(token in text for token in (
        "title", "description", "photo", "content", "copy", "location", "address", "placeholder",
        "presentation", "merchandising", "messaging", "guest benefit", "stay experience",
    )):
        return "content"
    return None


def build_listing_checks(
    detail: dict[str, Any] | None,
    assets: list[dict[str, Any]],
    *,
    inherited_actions: list[dict[str, Any]] | None = None,
) -> dict[str, dict[str, Any]]:
    """Build link, amenity, policy, and content findings for one listing."""
    checks = {
        key: {
            "key": key,
            **definition,
            "reviewed_count": 0,
            "findings": [],
            "status": "pending",
        }
        for key, definition in LISTING_CHECK_DEFINITIONS.items()
    }
    seen_findings: dict[str, set[tuple[str, str]]] = {key: set() for key in checks}

    def add_finding(
        group: str,
        *,
        source: str,
        message: str,
        priority: str = "medium",
        field: str | None = None,
    ) -> None:
        source_text = str(source or LISTING_CHECK_DEFINITIONS[group]["label"])
        message_text = " ".join(str(message or "").split())
        if not message_text:
            return
        signature = (source_text.casefold(), message_text.casefold())
        if signature in seen_findings[group]:
            return
        seen_findings[group].add(signature)
        checks[group]["findings"].append({
            "source": source_text,
            "message": message_text,
            "priority": priority,
            "field": field,
        })

    if detail is not None:
        amenities = listing_amenities(detail)
        checks["amenities"]["reviewed_count"] += 1
        if not amenities:
            add_finding(
                "amenities",
                source="Hostaway",
                message="Add the property's amenities in Hostaway so every connected channel has a complete source list.",
                priority="high",
                field="amenities",
            )
        elif len(amenities) < 10:
            add_finding(
                "amenities",
                source="Hostaway",
                message=f"Review amenity coverage; Hostaway contains only {len(amenities)} named amenities.",
                priority="medium",
                field="amenities",
            )

        house_rules = _text(detail.get("houseRules"))
        checks["policies"]["reviewed_count"] += 1
        if not house_rules:
            add_finding(
                "policies",
                source="Hostaway",
                message="Add guest-facing house rules in Hostaway before comparing policies across channels.",
                priority="high",
                field="house_rules",
            )
        elif len(house_rules) < 24:
            add_finding(
                "policies",
                source="Hostaway",
                message="Expand the house rules so the core guest expectations are explicit.",
                priority="medium",
                field="house_rules",
            )

        airbnb_connected = any(asset.get("channel") == "airbnb" and asset.get("configured") for asset in assets)
        if airbnb_connected:
            checks["policies"]["reviewed_count"] += 1
            if not _text(detail.get("airbnbNotes")):
                add_finding(
                    "policies",
                    source="Airbnb",
                    message="Add Airbnb guest notes for arrival details and important stay expectations.",
                    priority="medium",
                    field="guest_notes",
                )

        source_title = _text(detail.get("name") or detail.get("externalListingName"))
        source_description = _text(detail.get("description"))
        source_location = _join_text(detail.get("city"), detail.get("state"), detail.get("country"))
        photo_count = len(detail.get("listingImages") or [])
        checks["content"]["reviewed_count"] += 4
        if not source_title:
            add_finding("content", source="Hostaway", message="Add a guest-facing listing title.", priority="high", field="title")
        elif len(source_title) < 24:
            add_finding("content", source="Hostaway", message=f"Strengthen the listing title; it is only {len(source_title)} characters.", field="title")
        if not source_description:
            add_finding("content", source="Hostaway", message="Add a complete guest-facing listing description.", priority="high", field="description")
        elif len(source_description) < 220:
            add_finding("content", source="Hostaway", message=f"Expand the listing description; it is only {len(source_description)} characters.", field="description")
        if not source_location:
            add_finding("content", source="Hostaway", message="Add the listing location used by connected channels.", priority="high", field="location")
        if photo_count == 0:
            add_finding("content", source="Hostaway", message="Add listing photos before publishing to guest channels.", priority="high", field="photos")
        elif photo_count < 20:
            add_finding("content", source="Hostaway", message=f"Increase and reorder photo coverage; only {photo_count} source photos are available.", field="photos")

    for asset in assets:
        label = asset.get("label") or asset.get("channel") or "Channel"
        page = asset.get("page") or {}
        if not asset.get("configured"):
            add_finding("links", source=label, message=f"{label} is not connected for this listing.", priority="low", field="link")
        elif not asset.get("url"):
            add_finding("links", source=label, message=f"Store the public {label} URL so the guest page can be checked.", field="link")
        else:
            checks["links"]["reviewed_count"] += 1
            if confirmed_channel_link_problem(asset):
                add_finding(
                    "links",
                    source=label,
                    message=public_page_finding_message(page, str(label)),
                    priority="critical" if page.get("status") in {"not_found", "invalid_domain"} else "high",
                    field="link",
                )

        if asset.get("channel") == "googlevr" and page.get("status") == "ok" and "website_link" in page:
            checks["links"]["reviewed_count"] += 1
            website_finding = google_vr_website_finding(page)
            if website_finding:
                priority, message = website_finding
                add_finding(
                    "links",
                    source="Google Vacation Rentals Website",
                    message=message,
                    priority=priority,
                    field="website_link",
                )

        inspection = asset.get("deep_inspection") or {}
        issue_fields: set[str] = set()
        for issue in inspection.get("issues") or []:
            field_key = str(issue.get("field") or "page")
            group = "links" if field_key == "link" else LISTING_FIELD_GROUPS.get(field_key, "content")
            issue_fields.add(field_key)
            add_finding(
                group,
                source=label,
                message=issue.get("message") or "Review the detailed channel finding.",
                priority=issue.get("priority") or "medium",
                field=field_key,
            )
        for field_key, field in (inspection.get("fields") or {}).items():
            group = LISTING_FIELD_GROUPS.get(field_key)
            if not group or field.get("status") == "not_applicable":
                continue
            checks[group]["reviewed_count"] += 1
            if field.get("status") in LISTING_FIELD_PROBLEM_STATUSES and field_key not in issue_fields:
                label_text = field.get("label") or field_key.replace("_", " ").title()
                add_finding(
                    group,
                    source=label,
                    message=f"Review {label_text.lower()} on {label}; it is {str(field.get('status')).replace('_', ' ')}.",
                    field=field_key,
                )

        for text in asset.get("actions") or []:
            action = {"category": label, "text": text, "priority": "high" if asset.get("status") == "critical" else "medium"}
            group = classify_listing_action(action)
            if not group or group == "links":
                continue
            add_finding(group, source=label, message=text, priority=action["priority"])

    for action in inherited_actions or []:
        group = action.get("check_group") or classify_listing_action(action)
        if not group or group == "links":
            continue
        add_finding(
            group,
            source=str(action.get("category") or action.get("source") or LISTING_CHECK_DEFINITIONS[group]["label"]),
            message=str(action.get("text") or action.get("message") or ""),
            priority=str(action.get("priority") or "medium"),
            field=action.get("field"),
        )

    priority_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    for check in checks.values():
        check["findings"].sort(key=lambda finding: (
            priority_order.get(finding["priority"], 9),
            finding["source"].casefold(),
            finding["message"].casefold(),
        ))
        check["issue_count"] = len(check["findings"])
        check["status"] = "issue" if check["issue_count"] else "clear" if check["reviewed_count"] else "pending"
    return checks


def listing_quality_score(assets: list[dict[str, Any]]) -> float:
    scores = [float(asset.get("score") or 0) for asset in assets if asset.get("configured")]
    return round(statistics.mean(scores), 1) if scores else 0.0


def listing_quality_severity(score: float, checks: dict[str, dict[str, Any]]) -> str:
    priorities = {
        finding.get("priority") or "medium"
        for check in checks.values()
        for finding in check.get("findings") or []
    }
    if "critical" in priorities:
        return "critical"
    if "high" in priorities:
        return "high"
    if priorities or score < 85:
        return "watch"
    return "healthy"


def listing_actions_from_checks(checks: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    actions = [
        {
            "priority": finding.get("priority") or "medium",
            "category": finding.get("source") or check.get("label") or "Listing",
            "text": finding.get("message") or "Review the listing-quality finding.",
            "check_group": group,
            "field": finding.get("field"),
        }
        for group, check in checks.items()
        for finding in check.get("findings") or []
    ]
    return dedupe_actions(actions)[:18]


def merge_rendered_page_result(original: dict[str, Any], rendered: dict[str, Any]) -> dict[str, Any]:
    """Prefer rendered content only when it succeeds or confirms a real page failure."""
    browser_render = {
        "attempted": True,
        "status": rendered.get("status") or "unavailable",
        "error": rendered.get("error"),
    }
    if rendered.get("status") == "ok" or rendered.get("failure_kind") in CONFIRMED_RENDER_FAILURE_KINDS:
        result = dict(rendered)
        result["browser_render"] = browser_render
        return result
    result = dict(original)
    if (original.get("website_link") or {}).get("status") == "missing":
        result["website_link"] = dict(rendered.get("website_link") or {
            "status": "unverified",
            "url": None,
            "source": "rendered_google_page",
        })
    result["browser_render"] = browser_render
    return result


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
            portfolio_map, portfolio_names = self._portfolio_maps(listing_ids)
            page_results = self._public_page_results(details, deep=deep)

            severity_counts = {"critical": 0, "high": 0, "watch": 0, "healthy": 0}
            finding_counts = {"links": 0, "amenities": 0, "policies": 0, "content": 0}
            for listing_id in listing_ids:
                result = build_listing_audit_result(
                    details[listing_id],
                    public_pages={
                        channel: page_results.get((listing_id, channel))
                        for channel in CHANNEL_LABELS
                    },
                    deep=deep,
                    portfolio_id=portfolio_map.get(listing_id),
                    portfolio_name=portfolio_names.get(portfolio_map.get(listing_id)),
                )
                severity_counts[result["severity"]] += 1
                for group in finding_counts:
                    finding_counts[group] += int(result["listing_checks"][group]["issue_count"] or 0)
                self.brain_session.add(
                    ListingAuditSnapshot(
                        run_id=audit_run.listing_audit_run_id,
                        portfolio_id=result["portfolio_id"],
                        listing_id=result["listing_id"],
                        listing_name=result["listing_name"],
                        snapshot_date=local_today,
                        severity=result["severity"],
                        health_score=result["health_score"],
                        audit_scope=AUDIT_SCOPE,
                        listing_checks=as_json_safe(result["listing_checks"]),
                        issue_count=result["issue_count"],
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
            audit_run.audit_scope = AUDIT_SCOPE
            audit_run.finding_counts = as_json_safe(finding_counts)
            audit_run.source_statuses = as_json_safe(
                {
                    "hostaway": "ok" if listing_ids else "empty",
                    "audit_scope": AUDIT_SCOPE,
                    "inspection_mode": "weekly_deep" if deep else "daily_listing_quality",
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
                "finding_counts": finding_counts,
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
        from sync.sync_listings import sync_listings

        listing_sync = sync_listings(full_sync=bool(deep))
        return {
            "status": listing_sync.get("status") or "unknown",
            "audit_scope": AUDIT_SCOPE,
            "cadence": cadence,
            "listings": as_json_safe(listing_sync),
            "excluded_sources": ["reservations", "booking_health", "pricelabs", "market_occupancy"],
        }

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

        with ThreadPoolExecutor(max_workers=COVER_IMAGE_WORKERS) as executor:
            futures = {
                executor.submit(resolve_cover_image, detail): listing_id
                for listing_id, detail in details.items()
            }
            for future in as_completed(futures):
                listing_id = futures[future]
                try:
                    details[listing_id]["_audit_cover_image"] = future.result()
                except Exception as exc:
                    logger.warning("Listing cover image check failed for %s: %s", listing_id, exc)
                    details[listing_id]["_audit_cover_image"] = fallback_cover_image(details[listing_id])
        return details

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

    def _public_page_results(
        self,
        details: dict[int, dict[str, Any]],
        *,
        deep: bool = False,
    ) -> dict[tuple[int, str], dict[str, Any]]:
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
                executor.submit(fetch_public_page, url, channel, deep=deep): key
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
        render_targets: dict[tuple[int, str], tuple[str, str]] = {}
        if deep:
            render_targets.update({
                key: (targets[key], key[1])
                for key, result in results.items()
                if deep_content_is_sparse(result)
            })
        render_targets.update({
            key: (targets[key], key[1])
            for key, result in results.items()
            if key[1] == "googlevr"
            and result.get("status") == "ok"
            and (result.get("website_link") or {}).get("status") == "missing"
        })
        if render_targets:
            rendered_results = render_deep_public_pages(render_targets)
            for key, rendered in rendered_results.items():
                results[key] = merge_rendered_page_result(results[key], rendered)

        website_targets = {
            key: str((result.get("website_link") or {}).get("url"))
            for key, result in results.items()
            if key[1] == "googlevr"
            and (result.get("website_link") or {}).get("status") == "found"
            and (result.get("website_link") or {}).get("url")
        }
        if website_targets:
            with ThreadPoolExecutor(max_workers=PUBLIC_PAGE_WORKERS) as executor:
                futures = {
                    executor.submit(fetch_google_vr_website, url): key
                    for key, url in website_targets.items()
                }
                for future in as_completed(futures):
                    key = futures[future]
                    try:
                        results[key]["website_link"] = future.result()
                    except Exception as exc:
                        results[key]["website_link"] = {
                            "status": "unavailable",
                            "url": website_targets[key],
                            "error": str(exc)[:500],
                        }
        return results


def build_listing_audit_result(
    detail: dict[str, Any],
    *,
    public_pages: dict[str, dict[str, Any] | None] | None = None,
    deep: bool = False,
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
        build_channel_asset(detail, channel, public_pages.get(channel), deep=deep)
        for channel in CHANNEL_LABELS
    ]
    cover_image = detail.get("_audit_cover_image") or fallback_cover_image(detail)
    listing_checks = build_listing_checks(detail, assets)
    health_score = listing_quality_score(assets)
    severity = listing_quality_severity(health_score, listing_checks)
    actions = listing_actions_from_checks(listing_checks)
    issue_count = sum(int(check["issue_count"] or 0) for check in listing_checks.values())

    source_statuses = {
        "hostaway": "ok",
        "audit_scope": AUDIT_SCOPE,
        "inspection_mode": "weekly_deep" if deep else "daily_listing_quality",
        "online_assets": {asset["channel"]: asset["status"] for asset in assets},
    }
    return {
        "portfolio_id": portfolio_id,
        "listing_id": listing_id,
        "listing_name": listing_name,
        "severity": severity,
        "health_score": health_score,
        "audit_scope": AUDIT_SCOPE,
        "listing_checks": listing_checks,
        "issue_count": issue_count,
        "online_assets": assets,
        "action_items": actions,
        "source_statuses": source_statuses,
        "raw_payload": {
            "portfolio_name": portfolio_name,
            "thumbnail_url": cover_image.get("url"),
            "cover_image_source": cover_image.get("source"),
            "cover_image_status": cover_image.get("status"),
            "channel_thumbnail_url": normalize_url(detail.get("thumbnailUrl")),
            "city": detail.get("city"),
            "state": detail.get("state"),
            "currency": detail.get("currencyCode") or detail.get("currency"),
            "photo_count": len(detail.get("listingImages") or []),
            "audit_scope": AUDIT_SCOPE,
        },
    }


def build_channel_asset(
    detail: dict[str, Any],
    channel: str,
    page_result: dict[str, Any] | None = None,
    *,
    deep: bool = False,
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
    elif channel == "googlevr":
        export_status = _text(
            detail.get("googleExportStatus")
            or detail.get("googleVrExportStatus")
            or detail.get("googleVacationRentalsExportStatus")
        )
        title = _text(detail.get("name") or detail.get("externalListingName"))
        description = _join_text(detail.get("description"))
        configured = export_status == "exported" or bool(url)
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
            "domain_valid": bool(url),
            "inspection_mode": "deep" if deep else "link",
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
            has_url=bool(url),
            page_status=page.get("status"),
        )
        if page.get("status") in {"not_found", "invalid_domain"}:
            status = "critical"
        elif not url or page.get("status") == "missing_url":
            status = "watch" if score >= 62 else "high"
        elif score >= 82 and page.get("status") in {"ok", "not_checked", "missing_url"}:
            status = "healthy"
        elif score >= 62:
            status = "watch"
        else:
            status = "high"

    website_finding = (
        google_vr_website_finding(page)
        if channel == "googlevr" and page.get("status") == "ok" and "website_link" in page
        else None
    )
    if website_finding:
        website_priority, _ = website_finding
        if website_priority == "critical":
            status = "critical"
            score = min(score, 25.0)
        elif status != "critical":
            status = "high"
            score = min(score, 55.0)

    actions: list[str] = []
    label = CHANNEL_LABELS[channel]
    if not configured:
        if channel == "direct":
            actions.append("Add the direct-booking URL for this property so availability, copy, and conversion can be checked.")
        else:
            actions.append(f"Confirm whether this property should be live on {label}; Hostaway does not show it as exported.")
    else:
        if website_finding:
            actions.append(website_finding[1])
        if not url:
            actions.append(f"Store the public {label} URL in Hostaway or the audit URL mapping.")
        if len(effective_title) < 24:
            actions.append(f"Strengthen the {label} title with the property’s clearest guest benefit and location cue.")
        if len(effective_description) < 220:
            actions.append(f"Expand the {label} description with the stay experience, differentiators, and practical expectations.")
        if photo_count < 20:
            actions.append(f"Increase and reorder {label} photo coverage; only {photo_count} source photos are available.")
        if page.get("status") == "not_found":
            actions.append(f"Repair the {label} public URL or export immediately; the guest page appears unavailable.")
        elif page.get("status") == "invalid_domain":
            actions.append(f"Repair the {label} public URL; it redirects outside the expected channel domain.")
        elif page.get("status") in {"blocked", "unavailable"}:
            actions.append(f"Manually verify the {label} guest page because the automated public check could not confirm it.")

    deep_inspection = None
    if deep:
        deep_inspection = build_deep_channel_inspection(
            detail=detail,
            channel=channel,
            label=label,
            page=page,
            source_title=title,
            source_description=description,
        )
        page.pop("_deep_search_text", None)
        if configured:
            rank = {"critical": 0, "high": 1, "watch": 2, "healthy": 3}
            deep_status = deep_inspection.get("status") or "healthy"
            if rank.get(deep_status, 9) < rank.get(status, 9):
                status = deep_status
            score_caps = {"critical": 35.0, "high": 60.0, "watch": 78.0}
            if deep_status in score_caps:
                score = min(score, score_caps[deep_status])

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
        "actions": actions[:6],
        "deep_inspection": deep_inspection,
    }


def channel_profile_score(*, title: str, description: str, photo_count: int, has_url: bool, page_status: str | None) -> float:
    score = 25.0
    score += 10.0 if has_url else 0.0
    score += min(len(title) / 35.0, 1.0) * 20.0
    score += min(len(description) / 300.0, 1.0) * 25.0
    score += min(max(photo_count, 0) / 20.0, 1.0) * 20.0
    if page_status in {"not_found", "invalid_domain"}:
        score -= 35.0
    elif page_status in {"blocked", "unavailable", "non_html"}:
        score -= 8.0
    return max(0.0, min(score, 100.0))


def channel_urls(detail: dict[str, Any]) -> dict[str, str | None]:
    listing_id = str(detail.get("id") or detail.get("listingId") or "")
    overrides = configured_channel_urls().get(listing_id, {})
    return {
        "airbnb": normalize_url(overrides.get("airbnb") or detail.get("airbnbListingUrl")),
        "vrbo": normalize_url(overrides.get("vrbo") or detail.get("vrboListingUrl")),
        "bookingcom": normalize_url(overrides.get("bookingcom") or overrides.get("booking.com")),
        "googlevr": normalize_url(
            overrides.get("googlevr")
            or overrides.get("google_vacation_rentals")
            or overrides.get("google")
            or detail.get("googleVrListingUrl")
            or detail.get("googleVacationRentalsListingUrl")
            or detail.get("googleVacationRentalUrl")
            or detail.get("googleListingUrl")
        ),
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


def cover_image_candidates(detail: dict[str, Any]) -> list[tuple[str, str]]:
    """Return stable cover candidates with Hostaway gallery images first."""
    ordered_images: list[tuple[float, int, str]] = []
    images = detail.get("listingImages") or []
    if isinstance(images, list):
        for index, image in enumerate(images):
            if not isinstance(image, dict):
                continue
            url = normalize_url(image.get("url"))
            if not url:
                continue
            sort_order = _number(image.get("sortOrder"))
            ordered_images.append((sort_order if sort_order is not None else float("inf"), index, url))

    candidates: list[tuple[str, str]] = []
    seen: set[str] = set()
    for _, _, url in sorted(ordered_images)[:COVER_IMAGE_CANDIDATE_LIMIT]:
        if url not in seen:
            candidates.append((url, "hostaway_gallery"))
            seen.add(url)

    channel_thumbnail = normalize_url(detail.get("thumbnailUrl"))
    if channel_thumbnail and channel_thumbnail not in seen:
        candidates.append((channel_thumbnail, "channel_thumbnail"))
    return candidates


def fallback_cover_image(detail: dict[str, Any]) -> dict[str, Any]:
    """Choose the best unvalidated cover for pure/offline audit construction."""
    candidates = cover_image_candidates(detail)
    if not candidates:
        return {"url": None, "source": "placeholder", "status": "missing"}
    url, source = candidates[0]
    return {"url": url, "source": source, "status": "unchecked"}


def resolve_cover_image(detail: dict[str, Any]) -> dict[str, Any]:
    """Validate cover candidates once per audit so page rendering stays fast."""
    checked_at = datetime.utcnow().isoformat()
    attempted = 0
    last_http_status = None
    for url, source in cover_image_candidates(detail):
        attempted += 1
        result = fetch_image_status(url)
        last_http_status = result.get("http_status")
        if result.get("status") == "ok":
            return {
                "url": result.get("url") or url,
                "source": source,
                "status": "ok",
                "checked_at": checked_at,
                "http_status": last_http_status,
                "content_type": result.get("content_type"),
                "attempted": attempted,
            }
    return {
        "url": None,
        "source": "placeholder",
        "status": "unavailable" if attempted else "missing",
        "checked_at": checked_at,
        "http_status": last_http_status,
        "attempted": attempted,
    }


def fetch_image_status(url: str) -> dict[str, Any]:
    if not is_safe_public_url(url):
        return {"status": "unsafe", "url": url}
    response = None
    try:
        response = requests.get(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124 Safari/537.36",
                "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
            },
            timeout=COVER_IMAGE_TIMEOUT,
            allow_redirects=True,
            stream=True,
        )
        content_type = str(response.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
        final_url = normalize_url(response.url) or url
        available = response.status_code in {200, 206} and content_type.startswith("image/")
        return {
            "status": "ok" if available else "unavailable",
            "url": final_url,
            "http_status": response.status_code,
            "content_type": content_type,
        }
    except Exception as exc:
        return {"status": "unavailable", "url": url, "error": str(exc)[:500]}
    finally:
        if response is not None:
            response.close()


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


def fetch_google_vr_website(url: str | None) -> dict[str, Any]:
    """Verify the outbound Website destination exposed by Google Vacation Rentals."""
    checked_at = datetime.utcnow().isoformat()
    normalized = normalize_url(url)
    if not normalized or not is_safe_public_url(normalized):
        return {
            "status": "invalid",
            "url": normalized,
            "requested_url": url,
            "checked_at": checked_at,
            "summary": "The Google Vacation Rentals Website destination is not a valid public URL.",
        }

    response = None
    try:
        response = requests.get(
            normalized,
            headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124 Safari/537.36",
                "Accept-Language": "en-US,en;q=0.9",
            },
            timeout=PUBLIC_PAGE_TIMEOUT,
            allow_redirects=True,
            stream=True,
        )
        final_url = normalize_url(response.url) or normalized
        if not is_safe_public_url(final_url):
            status = "invalid"
            summary = "The Website link redirected to an invalid or unsafe destination."
        elif response.status_code in {404, 410}:
            status = "not_found"
            summary = f"The Website link returned HTTP {response.status_code}."
        elif response.status_code in {401, 403, 429}:
            status = "blocked"
            summary = f"The Website destination blocked the automated check with HTTP {response.status_code}."
        elif response.status_code >= 400:
            status = "unavailable"
            summary = f"The Website link returned HTTP {response.status_code}."
        else:
            status = "ok"
            summary = "The Website link opened successfully."
        return {
            "status": status,
            "url": final_url,
            "requested_url": normalized,
            "checked_at": checked_at,
            "http_status": response.status_code,
            "redirected": bool(response.history) or final_url.rstrip("/") != normalized.rstrip("/"),
            "summary": summary,
            "source": "google_vr_website_button",
        }
    except Exception as exc:
        return {
            "status": "unavailable",
            "url": normalized,
            "requested_url": normalized,
            "checked_at": checked_at,
            "summary": "The Website link did not return a public page.",
            "error": str(exc)[:500],
            "source": "google_vr_website_button",
        }
    finally:
        if response is not None:
            response.close()


def fetch_public_page(url: str, channel: str, *, deep: bool = False) -> dict[str, Any]:
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
            "requested_url": url,
            "checked_at": checked_at,
            "domain_valid": channel_destination_valid(url, channel),
            "redirected": False,
            "inspection_mode": "deep" if deep else "link",
            "summary": f"{CHANNEL_LABELS.get(channel, channel)} did not return a public page.",
            "error": str(exc)[:500],
        }

    text = response.text or ""
    title = clean_html(match_html(TITLE_PATTERN, text))
    description = clean_html(match_html(META_DESCRIPTION_PATTERN, text))
    visible = clean_html(TAG_PATTERN.sub(" ", text))[:1800]
    visible_page_text = f"{title} {visible[:700]}"
    visible_error = rendered_page_error_message(visible_page_text)
    automation_block = automation_blocked_page_message(visible_page_text)
    lower = f"{title} {visible[:700]}".lower()
    final_url = response.url or url
    content_type = str(response.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
    domain_valid = channel_destination_valid(final_url, channel)
    failure_kind = None
    if response.status_code in {404, 410} or "page not found" in lower:
        status = "not_found"
        failure_kind = "not_found"
    elif not domain_valid:
        status = "invalid_domain"
        failure_kind = "invalid_domain"
    elif automation_block:
        status = "blocked"
        failure_kind = "automation_blocked"
    elif response.status_code >= 400:
        status = "unavailable"
        failure_kind = "http_error"
    elif content_type and "html" not in content_type:
        status = "non_html"
        failure_kind = "non_html"
    elif visible_error:
        status = "unavailable"
        failure_kind = "rendered_error"
    else:
        status = "ok"
    result = {
        "status": status,
        "url": final_url,
        "requested_url": url,
        "checked_at": checked_at,
        "http_status": response.status_code,
        "content_type": content_type,
        "domain_valid": domain_valid,
        "redirected": bool(response.history) or final_url.rstrip("/") != url.rstrip("/"),
        "inspection_mode": "deep" if deep else "link",
        "title": title,
        "meta_description": description,
        "summary": visible_error or description or title or visible[:280] or "Public page responded without extractable text.",
    }
    if failure_kind:
        result["failure_kind"] = failure_kind
    if channel == "googlevr":
        website_url = extract_google_vr_website_url(text, page_url=final_url) if status == "ok" else None
        if website_url:
            result["website_link"] = fetch_google_vr_website(website_url)
        else:
            result["website_link"] = {
                "status": "missing" if status == "ok" else "unverified",
                "url": None,
                "checked_at": checked_at,
                "source": "static_google_page",
            }
    if deep and status == "ok":
        deep_content = extract_deep_page_content(
            text,
            fallback_title=title,
            fallback_description=description,
        )
        result["_deep_search_text"] = deep_content.pop("_search_text", "")
        result["deep_content"] = deep_content
    return result


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
