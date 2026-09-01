#!/usr/bin/env python3
"""Read models for the Workspace listing-health audit page."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from brain.models import ListingAuditRun, ListingAuditSnapshot, get_session
from brain.listing_audit import (
    AUDIT_TIMEZONE,
    CHANNEL_LABELS,
    CONFIRMED_RENDER_FAILURE_KINDS,
    LISTING_CHECK_DEFINITIONS,
    build_listing_checks,
    configured_channel_urls,
    confirmed_channel_link_problem,
    google_vr_website_finding,
    listing_actions_from_checks,
    listing_quality_score,
    listing_quality_severity,
    public_page_finding_message,
)

SEVERITY_ORDER = {"critical": 0, "high": 1, "watch": 2, "healthy": 3}
CHANNEL_PROBLEM_ORDER = {
    "critical": 0,
    "high": 1,
    "watch": 2,
    "not_exported": 3,
    "not_configured": 3,
}
def prepare_listing_quality_item(item: dict[str, Any]) -> None:
    """Combine persisted source checks with current and weekly channel evidence."""
    derived_checks = build_listing_checks(
        None,
        item.get("online_assets") or [],
        inherited_actions=item.get("action_items") or [],
    )
    checks = merge_listing_checks(item.get("listing_checks") or {}, derived_checks)
    item["action_items"] = listing_actions_from_checks(checks)
    item["listing_checks"] = checks
    item["issue_count"] = sum(check["issue_count"] for check in checks.values())
    item["health_score"] = listing_quality_score(item.get("online_assets") or [])
    item["severity"] = listing_quality_severity(item["health_score"], checks)


def merge_listing_checks(*models: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Merge stored and derived checks without duplicating the same finding."""
    merged = {
        key: {
            "key": key,
            **definition,
            "reviewed_count": 0,
            "findings": [],
            "status": "pending",
        }
        for key, definition in LISTING_CHECK_DEFINITIONS.items()
    }
    seen: dict[str, set[tuple[str, str]]] = {key: set() for key in merged}
    for model in models:
        for key, target in merged.items():
            source_check = (model or {}).get(key) or {}
            target["reviewed_count"] = max(
                int(target["reviewed_count"] or 0),
                int(source_check.get("reviewed_count") or 0),
            )
            for finding in source_check.get("findings") or []:
                source_label = str(finding.get("source") or target["label"])
                normalized = {
                    **finding,
                    "source": source_label,
                    "message": public_page_finding_message(
                        {"summary": finding.get("message")},
                        source_label,
                    ),
                    "priority": str(finding.get("priority") or "medium"),
                }
                if not normalized["message"]:
                    continue
                signature = (normalized["source"].casefold(), normalized["message"].casefold())
                if signature in seen[key]:
                    continue
                seen[key].add(signature)
                target["findings"].append(normalized)

    priority_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    for check in merged.values():
        check["findings"].sort(key=lambda finding: (
            priority_order.get(finding["priority"], 9),
            finding["source"].casefold(),
            finding["message"].casefold(),
        ))
        check["issue_count"] = len(check["findings"])
        check["status"] = "issue" if check["issue_count"] else "clear" if check["reviewed_count"] else "pending"
    return merged


def channel_problem_unit(item: dict[str, Any], asset: dict[str, Any]) -> dict[str, Any]:
    """Build a safe, actionable channel-card drill-down row."""
    status = asset.get("status") or "not_connected"
    configured = bool(asset.get("configured"))
    inspection = asset.get("deep_inspection") or {}
    issues = inspection.get("issues") or []
    actions = asset.get("actions") or []
    page = asset.get("page") or {}
    issue_codes = {issue.get("code") for issue in issues}
    website_finding = (
        google_vr_website_finding(page)
        if asset.get("channel") == "googlevr" and page.get("status") == "ok"
        else None
    )

    if not asset.get("url"):
        reason = "The public guest-page URL is not stored."
    elif page.get("failure_kind") == "rendered_error":
        reason = public_page_finding_message(page, str(asset.get("label") or "channel"))
    elif page.get("status") in {"not_found", "invalid_domain", "non_html"} or page.get("failure_kind") in CONFIRMED_RENDER_FAILURE_KINDS:
        reason = public_page_finding_message(page, str(asset.get("label") or "channel"))
    elif website_finding:
        reason = website_finding[1]
    elif not configured:
        reason = actions[0] if actions else "Hostaway does not show this listing as connected to the channel."
    elif issues:
        issue_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        primary_issue = min(issues, key=lambda issue: issue_order.get(issue.get("priority"), 9))
        reason = primary_issue.get("message") or "Review the detailed channel finding."
    elif actions:
        reason = actions[0]
    else:
        reason = public_page_finding_message(page, str(asset.get("label") or "channel"))

    page_status = (page.get("status") or "not_checked").replace("_", " ")
    if page.get("failure_kind") == "rendered_error":
        page_status = "error"
    elif "deep_content_unverified" in issue_codes:
        page_status = "content unverified"
    elif page.get("status") == "missing_url":
        page_status = "URL missing"
    elif website_finding:
        website_status = str((page.get("website_link") or {}).get("status") or "unverified")
        page_status = f"website {website_status.replace('_', ' ')}"

    return {
        "listing_id": item["listing_id"],
        "listing_name": item["listing_name"],
        "portfolio_name": item.get("portfolio_name") or "Unassigned",
        "health_score": item["health_score"],
        "severity": item["severity"],
        "channel_status": status,
        "connection_status": status.replace("_", " "),
        "page_status": page_status,
        "review_reason": reason,
        "issue_count": len(issues),
        "url": asset.get("url"),
        "configured": configured,
        "website_link_status": (page.get("website_link") or {}).get("status"),
    }


class ListingAuditDashboardService:
    def __init__(self, session=None):
        self.session = session or get_session()
        self._owns_session = session is None

    def close(self):
        if self._owns_session:
            self.session.close()

    def get_dashboard(self, *, portfolio_name: str | None = None) -> dict[str, Any]:
        latest_run = (
            self.session.query(ListingAuditRun)
            .filter(ListingAuditRun.status == "completed")
            .order_by(ListingAuditRun.completed_at.desc(), ListingAuditRun.listing_audit_run_id.desc())
            .first()
        )
        latest_weekly = (
            self.session.query(ListingAuditRun)
            .filter(ListingAuditRun.status == "completed", ListingAuditRun.cadence == "weekly")
            .order_by(ListingAuditRun.completed_at.desc(), ListingAuditRun.listing_audit_run_id.desc())
            .first()
        )
        recent_runs = (
            self.session.query(ListingAuditRun)
            .order_by(ListingAuditRun.started_at.desc(), ListingAuditRun.listing_audit_run_id.desc())
            .limit(8)
            .all()
        )
        if not latest_run:
            return empty_dashboard(recent_runs=recent_runs)

        snapshots = (
            self.session.query(ListingAuditSnapshot)
            .filter(ListingAuditSnapshot.run_id == latest_run.listing_audit_run_id)
            .all()
        )
        all_items = [snapshot_dict(snapshot) for snapshot in snapshots]
        if latest_weekly and latest_weekly.listing_audit_run_id != latest_run.listing_audit_run_id:
            weekly_snapshots = (
                self.session.query(ListingAuditSnapshot)
                .filter(ListingAuditSnapshot.run_id == latest_weekly.listing_audit_run_id)
                .all()
            )
            merge_deep_inspections(
                all_items,
                [snapshot_dict(snapshot) for snapshot in weekly_snapshots],
                source_run_id=latest_weekly.listing_audit_run_id,
            )
        elif latest_weekly:
            annotate_deep_inspection_source(all_items, latest_weekly.listing_audit_run_id)
        items, portfolio_options, selected_portfolio = scope_items_by_portfolio(
            all_items,
            portfolio_name,
        )
        return dashboard_payload(
            latest_run,
            items,
            latest_weekly=latest_weekly,
            recent_runs=recent_runs,
            portfolio_options=portfolio_options,
            selected_portfolio=selected_portfolio,
        )


def scope_items_by_portfolio(
    items: list[dict[str, Any]],
    requested_portfolio: str | None,
) -> tuple[list[dict[str, Any]], list[str], str | None]:
    """Resolve a case-insensitive portfolio name and return the matching audit slice."""
    portfolio_options = sorted({
        item["portfolio_name"]
        for item in items
        if item.get("portfolio_name")
    }, key=str.lower)
    requested = (requested_portfolio or "").strip()
    selected_portfolio = next(
        (name for name in portfolio_options if name.casefold() == requested.casefold()),
        None,
    )
    scoped_items = [
        item
        for item in items
        if selected_portfolio is None or item["portfolio_name"] == selected_portfolio
    ]
    return scoped_items, portfolio_options, selected_portfolio


def dashboard_payload(
    run: Any,
    items: list[dict[str, Any]],
    *,
    latest_weekly: Any | None = None,
    recent_runs: list[Any] | None = None,
    portfolio_options: list[str] | None = None,
    selected_portfolio: str | None = None,
) -> dict[str, Any]:
    now_local = datetime.now(ZoneInfo(AUDIT_TIMEZONE))
    snapshot_date = getattr(run, "snapshot_date", None)
    freshness_days = (now_local.date() - snapshot_date).days if snapshot_date else None
    for item in items:
        prepare_listing_quality_item(item)
    items.sort(key=lambda item: (
        SEVERITY_ORDER.get(item["severity"], 9),
        -item["issue_count"],
        item["health_score"],
        item["listing_name"].lower(),
    ))
    channel_coverage = {}
    for channel, label in CHANNEL_LABELS.items():
        channel_items = [
            (item, asset)
            for item in items
            for asset in item["online_assets"]
            if asset.get("channel") == channel
        ]
        assets = [asset for _, asset in channel_items]
        missing_units = [channel_problem_unit(item, asset) for item, asset in channel_items if not asset.get("configured")]
        missing_units.sort(key=lambda unit: (unit["portfolio_name"].lower(), unit["listing_name"].lower()))
        attention_units = [
            channel_problem_unit(item, asset)
            for item, asset in channel_items
            if asset.get("configured") and asset.get("status") not in {"healthy", "not_configured", "not_exported"}
        ]
        attention_units.sort(key=lambda unit: (
            CHANNEL_PROBLEM_ORDER.get(unit["channel_status"], 9),
            unit["health_score"],
            unit["listing_name"].lower(),
        ))
        problem_units = [
            channel_problem_unit(item, asset)
            for item, asset in channel_items
            if not asset.get("configured") or not asset.get("url") or confirmed_channel_link_problem(asset)
        ]
        problem_units.sort(key=lambda unit: (
            CHANNEL_PROBLEM_ORDER.get(unit["channel_status"], 9),
            unit["health_score"],
            unit["listing_name"].lower(),
        ))
        channel_coverage[channel] = {
            "label": label,
            "total": len(assets),
            "configured": sum(1 for asset in assets if asset.get("configured")),
            "missing_count": len(missing_units),
            "missing_units": missing_units,
            "healthy": sum(1 for asset in assets if asset.get("status") == "healthy"),
            "needs_attention": len(attention_units),
            "attention_units": attention_units,
            "problem_count": len(problem_units),
            "problem_units": problem_units,
            "deep_reviewed": sum(1 for asset in assets if asset.get("deep_inspection")),
            "deep_issues": sum(len((asset.get("deep_inspection") or {}).get("issues") or []) for asset in assets),
        }

    top_actions = []
    for item in items:
        for action in item["action_items"][:2]:
            top_actions.append({
                **action,
                "listing_id": item["listing_id"],
                "listing_name": item["listing_name"],
                "severity": item["severity"],
                "health_score": item["health_score"],
            })
    priority_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    top_actions.sort(key=lambda item: (priority_order.get(item.get("priority"), 9), item["health_score"]))

    portfolios = portfolio_options or sorted({
        item["portfolio_name"]
        for item in items
        if item.get("portfolio_name")
    }, key=str.lower)
    summary = {
        "listing_count": len(items),
        "critical_count": sum(1 for item in items if item["severity"] == "critical"),
        "high_count": sum(1 for item in items if item["severity"] == "high"),
        "watch_count": sum(1 for item in items if item["severity"] == "watch"),
        "healthy_count": sum(1 for item in items if item["severity"] == "healthy"),
        "average_score": round(sum(item["health_score"] for item in items) / len(items), 1) if items else 0,
        "link_issue_count": sum(item["listing_checks"]["links"]["issue_count"] for item in items),
        "link_reviewed_count": sum(item["listing_checks"]["links"]["reviewed_count"] for item in items),
        "confirmed_link_issue_count": sum(
            1
            for item in items
            for asset in item["online_assets"]
            if asset.get("configured") and asset.get("url") and confirmed_channel_link_problem(asset)
        ),
        "missing_public_url_count": sum(
            1
            for item in items
            for asset in item["online_assets"]
            if asset.get("configured") and not asset.get("url")
        ),
        "amenities_issue_count": sum(item["listing_checks"]["amenities"]["issue_count"] for item in items),
        "amenities_reviewed_count": sum(item["listing_checks"]["amenities"]["reviewed_count"] for item in items),
        "policies_issue_count": sum(item["listing_checks"]["policies"]["issue_count"] for item in items),
        "policies_reviewed_count": sum(item["listing_checks"]["policies"]["reviewed_count"] for item in items),
        "content_issue_count": sum(item["listing_checks"]["content"]["issue_count"] for item in items),
        "content_reviewed_count": sum(item["listing_checks"]["content"]["reviewed_count"] for item in items),
        "connection_gap_count": sum(
            1
            for item in items
            for asset in item["online_assets"]
            if not asset.get("configured")
        ),
        "channel_coverage": channel_coverage,
        "deep_reviewed_count": sum(
            1
            for item in items
            for asset in item["online_assets"]
            if asset.get("deep_inspection")
        ),
        "deep_issue_count": sum(
            len((asset.get("deep_inspection") or {}).get("issues") or [])
            for item in items
            for asset in item["online_assets"]
        ),
    }
    return {
        "has_data": True,
        "timezone": AUDIT_TIMEZONE,
        "run": run_dict(run),
        "latest_weekly_run": run_dict(latest_weekly) if latest_weekly else None,
        "recent_runs": [run_dict(item) for item in (recent_runs or [])],
        "summary": summary,
        "top_actions": top_actions[:12],
        "items": items,
        "portfolios": portfolios,
        "selected_portfolio": selected_portfolio,
        "profile_label": selected_portfolio or "All properties",
        "freshness_days": freshness_days,
        "is_stale": freshness_days is None or freshness_days > 1,
        "has_deep_review": bool(summary["deep_reviewed_count"]),
    }


def annotate_deep_inspection_source(items: list[dict[str, Any]], source_run_id: int) -> None:
    for item in items:
        for asset in item.get("online_assets") or []:
            inspection = asset.get("deep_inspection")
            if inspection:
                inspection["source_run_id"] = source_run_id


def merge_deep_inspections(
    current_items: list[dict[str, Any]],
    weekly_items: list[dict[str, Any]],
    *,
    source_run_id: int,
) -> None:
    """Carry the latest weekly channel findings onto fresher daily metrics."""
    weekly_by_listing = {int(item["listing_id"]): item for item in weekly_items}
    for item in current_items:
        weekly_item = weekly_by_listing.get(int(item["listing_id"]))
        if not weekly_item:
            continue
        weekly_assets = {
            asset.get("channel"): asset
            for asset in weekly_item.get("online_assets") or []
            if asset.get("deep_inspection")
        }
        existing_actions = {
            str(action.get("text") or "").strip().casefold()
            for action in item.get("action_items") or []
        }
        for asset in item.get("online_assets") or []:
            weekly_asset = weekly_assets.get(asset.get("channel"))
            if not weekly_asset:
                continue
            inspection = dict(weekly_asset["deep_inspection"])
            inspection["source_run_id"] = source_run_id
            asset["deep_inspection"] = inspection
            if asset.get("configured"):
                rank = {"critical": 0, "high": 1, "watch": 2, "healthy": 3}
                deep_status = inspection.get("status") or "healthy"
                current_status = asset.get("status") or "healthy"
                if rank.get(deep_status, 9) < rank.get(current_status, 9):
                    asset["status"] = deep_status
                    asset["status_source"] = "weekly_deep_review"
                score_caps = {"critical": 35.0, "high": 60.0, "watch": 78.0}
                if deep_status in score_caps:
                    asset["score"] = min(float(asset.get("score") or 0), score_caps[deep_status])
            for issue in inspection.get("issues") or []:
                text = str(issue.get("message") or "").strip()
                if not text or text.casefold() in existing_actions:
                    continue
                item.setdefault("action_items", []).append({
                    "priority": issue.get("priority") or "medium",
                    "category": asset.get("label") or asset.get("channel") or "Channel",
                    "text": text,
                    "source": "weekly_deep_review",
                })
                existing_actions.add(text.casefold())
        priority_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        item["action_items"].sort(key=lambda action: priority_order.get(action.get("priority"), 9))


def ensure_all_channel_assets(listing_id: int, assets: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Keep all supported channels visible while older audit rows age out."""
    existing = {
        str(asset.get("channel")): dict(asset)
        for asset in assets or []
        if asset.get("channel")
    }
    overrides = configured_channel_urls().get(str(listing_id), {})
    normalized: list[dict[str, Any]] = []
    for channel, label in CHANNEL_LABELS.items():
        asset = existing.get(channel)
        if asset:
            asset.setdefault("label", label)
            normalized.append(asset)
            continue
        url = overrides.get(channel)
        if channel == "googlevr":
            url = url or overrides.get("google_vacation_rentals") or overrides.get("google")
        configured = bool(url)
        normalized.append({
            "channel": channel,
            "label": label,
            "status": "watch" if configured else "not_configured" if channel == "direct" else "not_exported",
            "score": 60.0 if configured else 0.0,
            "configured": configured,
            "export_status": "mapped" if configured else "not synced",
            "url": url,
            "title": "",
            "description_excerpt": "",
            "title_length": 0,
            "description_length": 0,
            "photo_count": 0,
            "page": {
                "status": "not_checked" if configured else "missing_url",
                "url": url,
                "domain_valid": bool(url),
                "summary": (
                    "This channel was added after the stored audit. Its public-page check is pending."
                    if configured
                    else "This channel was added after the stored audit; the next audit will refresh its connection and URL."
                ),
            },
            "actions": [
                f"Run the listing audit to refresh {label} connection and public-page evidence."
            ],
            "deep_inspection": None,
            "compatibility_placeholder": True,
        })
    return normalized


def snapshot_dict(snapshot: Any) -> dict[str, Any]:
    raw = snapshot.raw_payload or {}
    listing_id = int(snapshot.listing_id)
    return {
        "listing_audit_snapshot_id": snapshot.listing_audit_snapshot_id,
        "listing_id": listing_id,
        "listing_name": snapshot.listing_name,
        "portfolio_id": snapshot.portfolio_id,
        "portfolio_name": raw.get("portfolio_name") or "Unassigned",
        "health_score": round(float(snapshot.health_score or 0), 1),
        "severity": snapshot.severity or "watch",
        "audit_scope": getattr(snapshot, "audit_scope", None) or raw.get("audit_scope") or "legacy_combined",
        "listing_checks": getattr(snapshot, "listing_checks", None) or {},
        "issue_count": int(getattr(snapshot, "issue_count", 0) or 0),
        "online_assets": ensure_all_channel_assets(listing_id, snapshot.online_assets),
        "action_items": snapshot.action_items or [],
        "source_statuses": snapshot.source_statuses or {},
        "thumbnail_url": raw.get("thumbnail_url"),
        "cover_image_source": raw.get("cover_image_source") or "legacy_thumbnail",
        "cover_image_status": raw.get("cover_image_status") or ("unchecked" if raw.get("thumbnail_url") else "missing"),
        "location": ", ".join(value for value in (raw.get("city"), raw.get("state")) if value),
        "photo_count": int(raw.get("photo_count") or 0),
        "currency": raw.get("currency") or "USD",
    }


def run_dict(run: Any | None) -> dict[str, Any] | None:
    if not run:
        return None
    return {
        "audit_run_id": run.listing_audit_run_id,
        "cadence": run.cadence,
        "status": run.status,
        "snapshot_date": run.snapshot_date.isoformat() if run.snapshot_date else None,
        "listing_count": int(run.listing_count or 0),
        "critical_count": int(run.critical_count or 0),
        "high_count": int(run.high_count or 0),
        "watch_count": int(run.watch_count or 0),
        "healthy_count": int(run.healthy_count or 0),
        "audit_scope": getattr(run, "audit_scope", None) or "legacy_combined",
        "finding_counts": getattr(run, "finding_counts", None) or {},
        "source_statuses": run.source_statuses or {},
        "error_message": run.error_message,
        "started_at": local_datetime(run.started_at),
        "completed_at": local_datetime(run.completed_at),
    }


def local_datetime(value: datetime | None) -> str | None:
    if not value:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=ZoneInfo("UTC"))
    return value.astimezone(ZoneInfo(AUDIT_TIMEZONE)).isoformat()


def empty_dashboard(*, recent_runs: list[Any] | None = None) -> dict[str, Any]:
    return {
        "has_data": False,
        "timezone": AUDIT_TIMEZONE,
        "run": None,
        "latest_weekly_run": None,
        "recent_runs": [run_dict(item) for item in (recent_runs or [])],
        "summary": {
            "listing_count": 0,
            "critical_count": 0,
            "high_count": 0,
            "watch_count": 0,
            "healthy_count": 0,
            "average_score": 0,
            "link_issue_count": 0,
            "link_reviewed_count": 0,
            "confirmed_link_issue_count": 0,
            "missing_public_url_count": 0,
            "amenities_issue_count": 0,
            "amenities_reviewed_count": 0,
            "policies_issue_count": 0,
            "policies_reviewed_count": 0,
            "content_issue_count": 0,
            "content_reviewed_count": 0,
            "connection_gap_count": 0,
            "channel_coverage": {},
            "deep_reviewed_count": 0,
            "deep_issue_count": 0,
        },
        "top_actions": [],
        "items": [],
        "portfolios": [],
        "selected_portfolio": None,
        "profile_label": "All properties",
        "freshness_days": None,
        "is_stale": True,
        "has_deep_review": False,
    }
