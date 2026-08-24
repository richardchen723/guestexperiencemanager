#!/usr/bin/env python3
"""Read models for the Workspace listing-health audit page."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from brain.models import ListingAuditRun, ListingAuditSnapshot, get_session
from brain.listing_audit import AUDIT_TIMEZONE, CHANNEL_LABELS

SEVERITY_ORDER = {"critical": 0, "high": 1, "watch": 2, "healthy": 3}
CHANNEL_PROBLEM_ORDER = {
    "critical": 0,
    "high": 1,
    "watch": 2,
    "not_exported": 3,
    "not_configured": 3,
}


def channel_problem_unit(item: dict[str, Any], asset: dict[str, Any]) -> dict[str, Any]:
    """Build a safe, actionable channel-card drill-down row."""
    status = asset.get("status") or "not_connected"
    configured = bool(asset.get("configured"))
    inspection = asset.get("deep_inspection") or {}
    issues = inspection.get("issues") or []
    actions = asset.get("actions") or []
    page = asset.get("page") or {}
    issue_codes = {issue.get("code") for issue in issues}

    if not configured:
        reason = actions[0] if actions else "Hostaway does not show this listing as connected to the channel."
    elif issues:
        issue_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        primary_issue = min(issues, key=lambda issue: issue_order.get(issue.get("priority"), 9))
        reason = primary_issue.get("message") or "Review the detailed channel finding."
    elif actions:
        reason = actions[0]
    else:
        reason = page.get("summary") or "This channel page needs review."

    page_status = (page.get("status") or "not_checked").replace("_", " ")
    if "deep_content_unverified" in issue_codes:
        page_status = "content unverified"
    elif page.get("status") == "missing_url":
        page_status = "URL missing"

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
        items.sort(key=lambda item: (SEVERITY_ORDER.get(item["severity"], 9), item["health_score"], item["listing_name"].lower()))
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
        problem_units = attention_units + missing_units
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
        for action in item["action_items"][:3]:
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


def snapshot_dict(snapshot: Any) -> dict[str, Any]:
    raw = snapshot.raw_payload or {}
    return {
        "listing_audit_snapshot_id": snapshot.listing_audit_snapshot_id,
        "listing_id": snapshot.listing_id,
        "listing_name": snapshot.listing_name,
        "portfolio_id": snapshot.portfolio_id,
        "portfolio_name": raw.get("portfolio_name") or "Unassigned",
        "health_score": round(float(snapshot.health_score or 0), 1),
        "severity": snapshot.severity or "watch",
        "booking_health": snapshot.booking_health or {},
        "pricing_health": snapshot.pricing_health or {},
        "market_comparison": snapshot.market_comparison or {},
        "online_assets": snapshot.online_assets or [],
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
