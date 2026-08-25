#!/usr/bin/env python3
"""Read model for portfolio- and rental-unit-organized guest issues."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, time, timedelta
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import selectinload

import dashboard.config as config
from brain.guest_experience import analysis_window, calendar_months_before
from brain.models import (
    ComprehensiveStayAnalysis,
    GuestExperienceAnalysisRun,
    GuestReviewIssueAnalysis,
    PropertyGuestIssue,
    PropertyGuestIssueNote,
    get_session as get_brain_session,
)
from dashboard.portfolio_mapping import TAG_PORTFOLIO_NAMES, portfolio_name_for_listing
from dashboard.auth.models import User, get_session as get_user_session
from dashboard.stay_issues.workflow import ISSUE_STATUS_LABELS, issue_operational_status
from database.models import (
    Listing,
    ListingPhoto,
    ListingTag,
    MessageMetadata,
    Review,
    get_session as get_main_session,
)


QUALITY_ORDER = ("unresolved", "recovered", "smooth", "muted")
WINDOW_PRESETS = {
    "1m": "Past month",
    "1w": "Past week",
    "custom": "Custom",
}


def resolve_dashboard_window(
    reference_time: datetime,
    *,
    window_key: str = "1m",
    start_date: str | date | None = None,
    end_date: str | date | None = None,
) -> dict[str, Any]:
    """Resolve a UI reporting window inside the analyzer's retained range."""
    available_start, available_end = analysis_window(reference_time)
    key = window_key if window_key in WINDOW_PRESETS else "1m"
    notice = None

    if key == "1m":
        selected_start, selected_end = available_start, available_end
    elif key == "1w":
        selected_start = max(available_start, available_end - timedelta(days=7))
        selected_end = available_end
    elif key == "custom":
        parsed_start = _parse_iso_date(start_date)
        parsed_end = _parse_iso_date(end_date)
        minimum_date = available_start.date()
        maximum_date = available_end.date()
        if parsed_start is None or parsed_end is None:
            selected_start, selected_end = available_start, available_end
            notice = "Choose both a start date and an end date."
        else:
            bounded_start = max(parsed_start, minimum_date)
            bounded_end = min(parsed_end, maximum_date)
            if bounded_start > bounded_end:
                selected_start, selected_end = available_start, available_end
                notice = "Choose a start date on or before the end date."
            else:
                selected_start = max(
                    available_start,
                    datetime.combine(bounded_start, time.min),
                )
                selected_end = min(
                    available_end,
                    datetime.combine(bounded_end, time.max),
                )
                if bounded_start != parsed_start or bounded_end != parsed_end:
                    notice = "Custom dates are limited to the analyzed one-month range."
    else:
        selected_start, selected_end = available_start, available_end

    return {
        "key": key,
        "name": WINDOW_PRESETS[key],
        "start": selected_start,
        "end": selected_end,
        "start_date": selected_start.date().isoformat(),
        "end_date": selected_end.date().isoformat(),
        "available_start_date": available_start.date().isoformat(),
        "available_end_date": available_end.date().isoformat(),
        "label": (
            f"{selected_start.strftime('%b %-d, %Y')} – "
            f"{selected_end.strftime('%b %-d, %Y')}"
        ),
        "notice": notice,
    }


def _parse_iso_date(value: str | date | None) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not value:
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


class GuestIssueDashboardService:
    """Build a UI-ready issue hierarchy without making external API calls."""

    def __init__(self, *, main_session=None, brain_session=None, now=None):
        self.main_session = main_session or get_main_session(config.MAIN_DATABASE_PATH)
        self.brain_session = brain_session or get_brain_session()
        self._owns_main_session = main_session is None
        self._owns_brain_session = brain_session is None
        self.now = now or datetime.utcnow()

    def close(self):
        if self._owns_main_session:
            self.main_session.close()
        if self._owns_brain_session:
            self.brain_session.close()

    def get_dashboard(
        self,
        *,
        view: str = "active",
        window_key: str = "1m",
        start_date: str | date | None = None,
        end_date: str | date | None = None,
    ) -> dict[str, Any]:
        if view not in {"active", "resolved", "archived"}:
            view = "active"
        selected_window = resolve_dashboard_window(
            self.now,
            window_key=window_key,
            start_date=start_date,
            end_date=end_date,
        )
        window_start = selected_window["start"]
        window_end = selected_window["end"]
        archive_cutoff = calendar_months_before(self.now, 1)
        listings = (
            self.main_session.query(Listing)
            .options(
                selectinload(Listing.tags).selectinload(ListingTag.tag),
                selectinload(Listing.photos),
            )
            .filter(func.lower(func.coalesce(Listing.status, "")) != "deleted")
            .order_by(Listing.internal_listing_name, Listing.name, Listing.listing_id)
            .all()
        )
        listing_ids = [int(row.listing_id) for row in listings]
        stay_analyses = (
            self.brain_session.query(ComprehensiveStayAnalysis)
            .filter(
                ComprehensiveStayAnalysis.listing_id.in_(listing_ids or [-1]),
                ComprehensiveStayAnalysis.checkout_at >= window_start,
                ComprehensiveStayAnalysis.checkout_at <= window_end,
            )
            .all()
        )
        review_analyses = (
            self.brain_session.query(GuestReviewIssueAnalysis)
            .filter(
                GuestReviewIssueAnalysis.listing_id.in_(listing_ids or [-1]),
                GuestReviewIssueAnalysis.review_date >= window_start.date(),
                GuestReviewIssueAnalysis.review_date <= window_end.date(),
            )
            .all()
        )
        issues = (
            self.brain_session.query(PropertyGuestIssue)
            .filter(
                PropertyGuestIssue.listing_id.in_(listing_ids or [-1]),
                PropertyGuestIssue.source_date >= window_start.date(),
                PropertyGuestIssue.source_date <= window_end.date(),
            )
            .order_by(PropertyGuestIssue.source_date.desc(), PropertyGuestIssue.issue_id.desc())
            .all()
        )
        issue_ids = [int(issue.issue_id) for issue in issues]
        notes = (
            self.brain_session.query(PropertyGuestIssueNote)
            .filter(PropertyGuestIssueNote.issue_id.in_(issue_ids or [-1]))
            .order_by(PropertyGuestIssueNote.created_at, PropertyGuestIssueNote.note_id)
            .all()
        )
        latest_run = (
            self.brain_session.query(GuestExperienceAnalysisRun)
            .order_by(GuestExperienceAnalysisRun.started_at.desc())
            .first()
        )
        operator_ids = {issue.resolved_by_user_id for issue in issues if issue.resolved_by_user_id}
        operator_ids.update(note.author_user_id for note in notes if note.author_user_id)
        operator_names: dict[int, str] = {}
        if operator_ids:
            user_session = get_user_session()
            try:
                operator_names = {
                    user.user_id: (user.name or user.email or f"Team member {user.user_id}")
                    for user in user_session.query(User).filter(User.user_id.in_(operator_ids)).all()
                }
            finally:
                user_session.close()

        stay_by_listing: dict[int, list[Any]] = defaultdict(list)
        review_by_listing: dict[int, list[Any]] = defaultdict(list)
        issues_by_listing: dict[int, list[Any]] = defaultdict(list)
        notes_by_issue: dict[int, list[Any]] = defaultdict(list)
        for row in stay_analyses:
            stay_by_listing[int(row.listing_id)].append(row)
        for row in review_analyses:
            review_by_listing[int(row.listing_id)].append(row)
        for row in issues:
            issues_by_listing[int(row.listing_id)].append(row)
        for row in notes:
            notes_by_issue[int(row.issue_id)].append(row)

        portfolios: dict[str, list[dict[str, Any]]] = defaultdict(list)
        all_quality_counts = {quality: 0 for quality in QUALITY_ORDER}
        selected_status_counts = {status: 0 for status in ISSUE_STATUS_LABELS}
        selected_issue_count = review_issue_count = 0
        properties_with_selected_issues = 0
        active_issue_count = ticketed_issue_count = recently_resolved_count = archived_issue_count = 0

        for issue in issues:
            workflow_status = issue.workflow_status or "open"
            if workflow_status == "resolved":
                if issue.resolved_at and issue.resolved_at < archive_cutoff:
                    archived_issue_count += 1
                else:
                    recently_resolved_count += 1
            else:
                active_issue_count += 1
                if workflow_status == "ticketed":
                    ticketed_issue_count += 1

        for listing in listings:
            listing_id = int(listing.listing_id)
            tag_names = [row.tag.name for row in listing.tags if row.tag]
            portfolio_name = portfolio_name_for_listing(listing_id, tag_names) or "Unassigned"
            quality_counts = {quality: 0 for quality in QUALITY_ORDER}
            for analysis in stay_by_listing.get(listing_id, []):
                quality = str(analysis.stay_quality or "").lower()
                if quality in quality_counts:
                    quality_counts[quality] += 1
                    all_quality_counts[quality] += 1

            formatted_issues = []
            for issue in issues_by_listing.get(listing_id, []):
                formatted = self._format_issue(
                    issue,
                    archive_cutoff=archive_cutoff,
                    resolver_name=operator_names.get(issue.resolved_by_user_id),
                    notes=notes_by_issue.get(int(issue.issue_id), []),
                    operator_names=operator_names,
                )
                if _issue_matches_view(formatted, view):
                    formatted_issues.append(formatted)
                    selected_status_counts[formatted["operational_status"]] += 1
            if formatted_issues:
                properties_with_selected_issues += 1
            selected_issue_count += len(formatted_issues)
            review_issue_count += sum(issue["source_kind"] == "review" for issue in formatted_issues)

            # The issue workspace is an action queue; issue-free rentals stay out of
            # the way until a selected view contains work for them.
            if not formatted_issues:
                continue

            portfolios[portfolio_name].append({
                "listing_id": listing_id,
                "listing_name": listing.internal_listing_name or listing.name or f"Rental unit {listing_id}",
                "location": ", ".join(part for part in (listing.city, listing.state) if part),
                "thumbnail_url": _cover_url(listing.photos),
                "portfolio_name": portfolio_name,
                "issues": formatted_issues,
                "issue_count": len(formatted_issues),
                "stay_analysis_count": len(stay_by_listing.get(listing_id, [])),
                "review_analysis_count": len(review_by_listing.get(listing_id, [])),
                "quality_counts": quality_counts,
                "search_text": " ".join(
                    [listing.internal_listing_name or listing.name or "", portfolio_name]
                    + [issue["summary"] + " " + issue["category_label"] for issue in formatted_issues]
                ).lower(),
            })

        portfolio_order = {name: index for index, name in enumerate(TAG_PORTFOLIO_NAMES)}
        formatted_portfolios = []
        for portfolio_name, units in sorted(
            portfolios.items(),
            key=lambda item: (portfolio_order.get(item[0], 999), item[0].lower()),
        ):
            units.sort(key=lambda unit: (-unit["issue_count"], unit["listing_name"].lower()))
            formatted_portfolios.append({
                "name": portfolio_name,
                "units": units,
                "unit_count": len(units),
                "issue_count": sum(unit["issue_count"] for unit in units),
            })

        return {
            "window": selected_window,
            "view": view,
            "archive_cutoff": archive_cutoff,
            "summary": {
                "issue_count": selected_issue_count,
                "active_issue_count": active_issue_count,
                "open_issue_count": active_issue_count - ticketed_issue_count,
                "ticketed_issue_count": ticketed_issue_count,
                "recently_resolved_count": recently_resolved_count,
                "archived_issue_count": archived_issue_count,
                "review_issue_count": review_issue_count,
                "property_count": len(listings),
                "properties_with_issues": properties_with_selected_issues,
                "stay_analysis_count": len(stay_analyses),
                "review_analysis_count": len(review_analyses),
                "quality_counts": all_quality_counts,
                "status_counts": selected_status_counts,
            },
            "portfolios": formatted_portfolios,
            "latest_run": _format_run(latest_run),
        }

    def _format_issue(
        self,
        issue: PropertyGuestIssue,
        *,
        archive_cutoff: datetime,
        resolver_name: str | None = None,
        notes: list[Any] | None = None,
        operator_names: dict[int, str] | None = None,
    ) -> dict[str, Any]:
        references = []
        for reference in issue.source_references or []:
            source_type = str(reference.get("source_type") or "")
            source_id = reference.get("source_id")
            if source_type == "message" and source_id is not None:
                references.append({
                    "label": _message_reference_label(reference),
                    "url": f"/workspace/guest-issues/sources/messages/{source_id}",
                    "source_type": "message",
                })
            elif source_type == "review" and source_id is not None:
                part = str(reference.get("source_part") or "public_review")
                references.append({
                    "label": _review_reference_label(part),
                    "url": f"/workspace/guest-issues/sources/reviews/{source_id}#{part.replace('_', '-')}",
                    "source_type": "review",
                })
        workflow_status = issue.workflow_status or "open"
        operational_status = issue_operational_status(issue)
        operator_names = operator_names or {}
        is_archived = bool(
            workflow_status == "resolved"
            and issue.resolved_at
            and issue.resolved_at < archive_cutoff
        )
        return {
            "issue_id": issue.issue_id,
            "source_kind": issue.source_kind,
            "source_label": "Guest review" if issue.source_kind == "review" else "Stay messages",
            "source_date": issue.source_date,
            "category": issue.issue_category,
            "category_label": str(issue.issue_category or "Other").replace("_", " ").title(),
            "summary": issue.summary,
            "details": issue.details,
            "severity": issue.severity,
            "resolution_state": issue.resolution_state or "feedback",
            "workflow_status": workflow_status,
            "operational_status": operational_status,
            "workflow_label": ISSUE_STATUS_LABELS[operational_status],
            "is_archived": is_archived,
            "resolution_comment": issue.resolution_comment,
            "resolution_method": issue.resolution_method,
            "resolved_at": issue.resolved_at,
            "resolved_by_user_id": issue.resolved_by_user_id,
            "resolved_by_name": resolver_name,
            "linked_ticket_id": issue.linked_ticket_id,
            "ticket_url": (
                f"/tickets/{issue.linked_ticket_id}/page" if issue.linked_ticket_id else None
            ),
            "create_ticket_url": f"/tickets/create?guest_issue_id={issue.issue_id}",
            "reservation_id": issue.reservation_id,
            "review_id": issue.review_id,
            "references": references,
            "notes": [
                {
                    "note_id": note.note_id,
                    "body": note.body,
                    "note_type": note.note_type,
                    "note_type_label": {
                        "status_change": "Status update",
                        "resolution": "Resolution",
                    }.get(note.note_type, "Note"),
                    "created_at": note.created_at,
                    "author_user_id": note.author_user_id,
                    "author_name": operator_names.get(note.author_user_id, "Team member"),
                }
                for note in (notes or [])
            ],
        }


def _issue_matches_view(issue: dict[str, Any], view: str) -> bool:
    if view == "archived":
        return bool(issue["is_archived"])
    if view == "resolved":
        return issue["workflow_status"] == "resolved" and not issue["is_archived"]
    return issue["workflow_status"] in {"open", "ticketed"}


def get_message_source(main_session, message_id: int) -> dict[str, Any] | None:
    message = main_session.query(MessageMetadata).filter(
        MessageMetadata.message_id == message_id
    ).first()
    if not message:
        return None
    listing = main_session.query(Listing).filter(Listing.listing_id == message.listing_id).first()
    return {
        "kind": "message",
        "title": "Guest message" if message.is_incoming else "Support message",
        "source_id": message.message_id,
        "content": message.content_preview or "",
        "sender": message.sender_name or message.sender_type or "Unknown",
        "direction": "Guest to support" if message.is_incoming else "Support to guest",
        "created_at": message.created_at,
        "reservation_id": message.reservation_id,
        "conversation_id": message.conversation_id,
        "listing_id": message.listing_id,
        "listing_name": (
            listing.internal_listing_name or listing.name if listing else f"Rental unit {message.listing_id}"
        ),
    }


def get_review_source(main_session, review_id: int) -> dict[str, Any] | None:
    review = main_session.query(Review).filter(Review.review_id == review_id).first()
    if not review:
        return None
    listing = main_session.query(Listing).filter(Listing.listing_id == review.listing_id).first()
    return {
        "kind": "review",
        "title": "Guest review",
        "source_id": review.review_id,
        "public_review": review.review_text,
        "private_feedback": review.private_feedback,
        "overall_rating": review.overall_rating,
        "sub_ratings": [
            {"category": row.rating_category, "value": row.rating_value}
            for row in review.sub_ratings
        ],
        "reviewer_name": review.reviewer_name,
        "review_date": review.review_date,
        "channel_name": review.channel_name,
        "reservation_id": review.reservation_id,
        "listing_id": review.listing_id,
        "listing_name": (
            listing.internal_listing_name or listing.name if listing else f"Rental unit {review.listing_id}"
        ),
    }


def _cover_url(photos: list[ListingPhoto]) -> str | None:
    if not photos:
        return None
    ordered = sorted(photos, key=lambda photo: (photo.display_order is None, photo.display_order or 0))
    return ordered[0].thumbnail_url or ordered[0].photo_url


def _format_run(run: GuestExperienceAnalysisRun | None) -> dict[str, Any] | None:
    if not run:
        return None
    return {
        "run_id": run.run_id,
        "status": run.status,
        "started_at": run.started_at,
        "completed_at": run.completed_at,
        "stays_analyzed": run.stays_analyzed,
        "reviews_analyzed": run.reviews_analyzed,
        "error_count": run.error_count,
    }


def _message_reference_label(reference: dict[str, Any]) -> str:
    role = str(reference.get("role") or "context").replace("_", " ").title()
    return f"{role} message"


def _review_reference_label(part: str) -> str:
    return {
        "public_review": "Public review",
        "private_feedback": "Private feedback",
        "overall_rating": "Overall rating",
        "sub_rating": "Category rating",
    }.get(part, "Guest review")
