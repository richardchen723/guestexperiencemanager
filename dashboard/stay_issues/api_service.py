"""PII-minimized read model for the external Guest Issues API."""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import joinedload

import dashboard.config as config
from brain.models import PropertyGuestIssue, get_session as get_brain_session
from dashboard.api.conventions import (
    ApiParameterError,
    pagination_payload,
    parse_api_datetime,
    parse_positive_int,
)
from dashboard.auth.models import User, get_session as get_user_session
from dashboard.portfolio_mapping import portfolio_name_for_listing
from dashboard.stay_issues.workflow import (
    ISSUE_PRIORITIES,
    ISSUE_STATUS_LABELS,
    issue_operational_status,
    issue_priority,
    issue_reported_at,
)
from dashboard.tickets.models import Ticket
from database.models import (
    Listing,
    ListingTag,
    MessageMetadata,
    Reservation,
    get_session as get_main_session,
)


DEFAULT_PER_PAGE = 50
MAX_PER_PAGE = 100
SORT_FIELDS = frozenset({"issue_id", "reported_at", "updated_at", "priority", "status"})
RECENCY_WINDOWS = {
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
}
PRIORITY_SCORE = {priority: len(ISSUE_PRIORITIES) - index for index, priority in enumerate(ISSUE_PRIORITIES)}
STATUS_SCORE = {status: index for index, status in enumerate(ISSUE_STATUS_LABELS)}
EMAIL_RE = re.compile(r"(?<![\w.+-])[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}(?![\w.-])", re.I)
PHONE_RE = re.compile(r"(?<!\w)\+?\d[\d\s().-]{7,}\d(?!\w)")
KEY_RE = re.compile(r"\b(?:hk_|sk-)[A-Za-z0-9_-]{8,}\b", re.I)
BEARER_RE = re.compile(r"\bBearer\s+[A-Za-z0-9._~+/-]{8,}=*", re.I)
NAMED_SECRET_RE = re.compile(
    r"\b(?:api[_ -]?key|access[_ -]?token|password|secret)\s*[:=]\s*[^\s,;]+",
    re.I,
)
URL_SECRET_RE = re.compile(r"([?&](?:key|token|secret|password)=)[^&#\s]+", re.I)


class GuestIssueApiService:
    """Query Guest Issues without exposing raw messages or guest identity."""

    def __init__(
        self,
        *,
        main_session=None,
        brain_session=None,
        user_session=None,
        now: datetime | None = None,
    ):
        self.main_session = main_session or get_main_session(config.MAIN_DATABASE_PATH)
        self.brain_session = brain_session or get_brain_session()
        self.user_session = user_session or get_user_session()
        self._owns_main_session = main_session is None
        self._owns_brain_session = brain_session is None
        self._owns_user_session = user_session is None
        self.now = now or datetime.utcnow()

    def close(self) -> None:
        if self._owns_main_session:
            self.main_session.close()
        if self._owns_brain_session:
            self.brain_session.close()
        if self._owns_user_session:
            self.user_session.close()

    def list_issues(self, args) -> dict[str, Any]:
        options = self._parse_options(args)
        query = self.brain_session.query(PropertyGuestIssue)
        if options["listing_id"] is not None:
            query = query.filter(PropertyGuestIssue.listing_id == options["listing_id"])
        if options["reservation_id"] is not None:
            query = query.filter(PropertyGuestIssue.reservation_id == options["reservation_id"])
        if options["reported_from"] is not None:
            query = query.filter(PropertyGuestIssue.created_at >= options["reported_from"])
        if options["reported_to"] is not None:
            query = query.filter(PropertyGuestIssue.created_at <= options["reported_to"])
        if options["updated_since"] is not None:
            query = query.filter(PropertyGuestIssue.updated_at >= options["updated_since"])

        issues = query.all()
        context = self._load_context(issues)
        issues = [
            issue for issue in issues
            if self._matches_python_filters(issue, context, options)
        ]
        self._sort_issues(issues, options["sort"], options["order"])
        total = len(issues)
        start = (options["page"] - 1) * options["per_page"]
        selected = issues[start:start + options["per_page"]]
        return {
            "data": [self._serialize(issue, context) for issue in selected],
            "pagination": pagination_payload(
                page=options["page"],
                per_page=options["per_page"],
                total=total,
            ),
            "meta": {
                "sort": options["sort"],
                "order": options["order"],
                "filters": self._public_filters(options),
            },
        }

    def get_issue(self, issue_id: int) -> dict[str, Any] | None:
        issue = self.brain_session.query(PropertyGuestIssue).filter(
            PropertyGuestIssue.issue_id == issue_id
        ).first()
        if not issue:
            return None
        context = self._load_context([issue])
        return {"data": self._serialize(issue, context)}

    def _parse_options(self, args) -> dict[str, Any]:
        page = parse_positive_int(args.get("page"), parameter="page", default=1)
        per_page = parse_positive_int(
            args.get("per_page"),
            parameter="per_page",
            default=DEFAULT_PER_PAGE,
            maximum=MAX_PER_PAGE,
        )
        listing_id = _optional_positive_id(args.get("listing_id"), "listing_id")
        reservation_id = _optional_positive_id(args.get("reservation_id"), "reservation_id")
        statuses = _parse_csv(args.get("status"))
        invalid_statuses = statuses - set(ISSUE_STATUS_LABELS)
        if invalid_statuses:
            raise ApiParameterError(
                "status contains an unsupported value",
                parameter="status",
            )
        raw_priorities = _parse_csv(args.get("priority"))
        priority_lookup = {priority.lower(): priority for priority in ISSUE_PRIORITIES}
        if raw_priorities - set(priority_lookup):
            raise ApiParameterError(
                "priority contains an unsupported value",
                parameter="priority",
            )
        priorities = {priority_lookup[value] for value in raw_priorities}

        reported_from = parse_api_datetime(
            args.get("reported_from"),
            parameter="reported_from",
        )
        reported_to = parse_api_datetime(
            args.get("reported_to"),
            parameter="reported_to",
            end_of_day=True,
        )
        recency = str(args.get("recency") or "").strip().lower() or None
        if recency:
            if recency not in {"today", *RECENCY_WINDOWS}:
                raise ApiParameterError(
                    "recency must be today, 24h, 7d, or 30d",
                    parameter="recency",
                )
            if reported_from is not None or reported_to is not None:
                raise ApiParameterError(
                    "recency cannot be combined with reported_from or reported_to",
                    parameter="recency",
                )
            if recency == "today":
                reported_from = self.now.replace(hour=0, minute=0, second=0, microsecond=0)
            else:
                reported_from = self.now - RECENCY_WINDOWS[recency]
            reported_to = self.now
        if reported_from and reported_to and reported_from > reported_to:
            raise ApiParameterError(
                "reported_from must be before or equal to reported_to",
                parameter="reported_from",
            )

        updated_since = parse_api_datetime(
            args.get("updated_since"),
            parameter="updated_since",
        )
        sort = str(args.get("sort") or "reported_at").strip().lower()
        if sort not in SORT_FIELDS:
            raise ApiParameterError(
                f"sort must be one of {', '.join(sorted(SORT_FIELDS))}",
                parameter="sort",
            )
        order = str(args.get("order") or "desc").strip().lower()
        if order not in {"asc", "desc"}:
            raise ApiParameterError("order must be asc or desc", parameter="order")
        portfolio = str(args.get("portfolio") or "").strip() or None
        if portfolio and len(portfolio) > 100:
            raise ApiParameterError(
                "portfolio must be 100 characters or fewer",
                parameter="portfolio",
            )
        return {
            "page": page,
            "per_page": per_page,
            "listing_id": listing_id,
            "reservation_id": reservation_id,
            "statuses": statuses,
            "priorities": priorities,
            "portfolio": portfolio,
            "reported_from": reported_from,
            "reported_to": reported_to,
            "updated_since": updated_since,
            "recency": recency,
            "sort": sort,
            "order": order,
        }

    def _load_context(self, issues: list[PropertyGuestIssue]) -> dict[str, Any]:
        listing_ids = {int(issue.listing_id) for issue in issues if issue.listing_id is not None}
        reservation_ids = {
            int(issue.reservation_id) for issue in issues if issue.reservation_id is not None
        }
        ticket_ids = {
            int(issue.linked_ticket_id) for issue in issues if issue.linked_ticket_id is not None
        }
        user_ids = {
            int(user_id)
            for issue in issues
            for user_id in (issue.resolved_by_user_id, issue.priority_updated_by_user_id)
            if user_id is not None
        }
        message_ids = {
            int(reference["source_id"])
            for issue in issues
            for reference in (issue.source_references or [])
            if isinstance(reference, dict)
            and reference.get("source_type") == "message"
            and _is_int(reference.get("source_id"))
        }

        listings = {
            int(row.listing_id): row
            for row in self.main_session.query(Listing).filter(
                Listing.listing_id.in_(listing_ids or {-1})
            ).all()
        }
        tags_by_listing: dict[int, list[str]] = defaultdict(list)
        for row in self.main_session.query(ListingTag).options(
            joinedload(ListingTag.tag)
        ).filter(ListingTag.listing_id.in_(listing_ids or {-1})).all():
            if row.tag and row.tag.name:
                tags_by_listing[int(row.listing_id)].append(row.tag.name)
        portfolios = {
            listing_id: (
                portfolio_name_for_listing(listing_id, tags_by_listing.get(listing_id, []))
                or "Unassigned"
            )
            for listing_id in listing_ids
        }
        reservations = {
            int(row.reservation_id): row
            for row in self.main_session.query(Reservation).filter(
                Reservation.reservation_id.in_(reservation_ids or {-1})
            ).all()
        }
        conversation_by_message = {
            int(row.message_id): int(row.conversation_id)
            for row in self.main_session.query(MessageMetadata).filter(
                MessageMetadata.message_id.in_(message_ids or {-1})
            ).all()
            if row.conversation_id is not None
        }
        tickets = {
            int(row.ticket_id): row
            for row in self.user_session.query(Ticket).options(
                joinedload(Ticket.assigned_user)
            ).filter(Ticket.ticket_id.in_(ticket_ids or {-1})).all()
        }
        for ticket in tickets.values():
            if ticket.assigned_user_id:
                user_ids.add(int(ticket.assigned_user_id))
        users = {
            int(row.user_id): row
            for row in self.user_session.query(User).filter(
                User.user_id.in_(user_ids or {-1})
            ).all()
        }
        return {
            "listings": listings,
            "portfolios": portfolios,
            "reservations": reservations,
            "conversation_by_message": conversation_by_message,
            "tickets": tickets,
            "users": users,
        }

    @staticmethod
    def _matches_python_filters(issue, context, options) -> bool:
        if options["statuses"] and issue_operational_status(issue) not in options["statuses"]:
            return False
        if options["priorities"] and issue_priority(issue) not in options["priorities"]:
            return False
        if options["portfolio"]:
            actual = context["portfolios"].get(int(issue.listing_id), "Unassigned")
            if actual.casefold() != options["portfolio"].casefold():
                return False
        return True

    @staticmethod
    def _sort_issues(issues, sort: str, order: str) -> None:
        reverse = order == "desc"
        # The second stable sort makes issue_id the deterministic tiebreaker.
        issues.sort(key=lambda issue: int(issue.issue_id), reverse=reverse)
        issues.sort(key=lambda issue: _sort_value(issue, sort), reverse=reverse)

    def _serialize(self, issue, context) -> dict[str, Any]:
        listing = context["listings"].get(int(issue.listing_id))
        reservation = context["reservations"].get(int(issue.reservation_id)) if issue.reservation_id else None
        ticket = context["tickets"].get(int(issue.linked_ticket_id)) if issue.linked_ticket_id else None
        sensitive_values = _reservation_sensitive_values(reservation)
        safe_references = _safe_source_references(issue.source_references)
        message_ids = [
            int(reference["source_id"])
            for reference in safe_references
            if reference["source_type"] == "message"
        ]
        conversation_ids = sorted({
            context["conversation_by_message"][message_id]
            for message_id in message_ids
            if message_id in context["conversation_by_message"]
        })
        operational_status = issue_operational_status(issue)
        resolver = context["users"].get(int(issue.resolved_by_user_id)) if issue.resolved_by_user_id else None
        assignee = ticket.assigned_user if ticket and ticket.assigned_user_id else None
        return {
            "issue_id": int(issue.issue_id),
            "listing": {
                "listing_id": int(issue.listing_id),
                "name": (
                    (listing.internal_listing_name or listing.name)
                    if listing else f"Rental unit {issue.listing_id}"
                ),
            },
            "portfolio": context["portfolios"].get(int(issue.listing_id), "Unassigned"),
            "category": issue.issue_category,
            "summary": redact_sensitive_text(issue.summary, sensitive_values, maximum=500),
            "details": redact_sensitive_text(issue.details, sensitive_values, maximum=4000),
            "suggested_improvement": redact_sensitive_text(
                issue.suggested_improvement,
                sensitive_values,
                maximum=2000,
            ),
            "reported_at": _isoformat(issue_reported_at(issue)),
            "source_date": issue.source_date.isoformat() if issue.source_date else None,
            "status": {
                "workflow": issue.workflow_status or "open",
                "operational": operational_status,
                "label": ISSUE_STATUS_LABELS[operational_status],
            },
            "priority": issue_priority(issue),
            "severity": issue.severity,
            "references": {
                "reservation_id": int(issue.reservation_id) if issue.reservation_id else None,
                "conversation_ids": conversation_ids,
                "review_id": int(issue.review_id) if issue.review_id else None,
                "source_references": safe_references,
            },
            "linked_ticket_id": int(issue.linked_ticket_id) if issue.linked_ticket_id else None,
            "assignee": _user_reference(assignee),
            "resolution": {
                "state": issue.resolution_state,
                "method": issue.resolution_method,
                "details": redact_sensitive_text(
                    issue.resolution_comment,
                    sensitive_values,
                    maximum=2000,
                ),
                "resolved_at": _isoformat(issue.resolved_at),
                "resolved_by": _user_reference(resolver),
            },
            "source": {
                "kind": issue.source_kind,
                "reference_count": len(safe_references),
            },
            "created_at": _isoformat(issue.created_at),
            "updated_at": _isoformat(issue.updated_at),
        }

    @staticmethod
    def _public_filters(options) -> dict[str, Any]:
        return {
            "listing_id": options["listing_id"],
            "portfolio": options["portfolio"],
            "status": sorted(options["statuses"]),
            "priority": sorted(options["priorities"]),
            "reported_from": _isoformat(options["reported_from"]),
            "reported_to": _isoformat(options["reported_to"]),
            "recency": options["recency"],
            "reservation_id": options["reservation_id"],
            "updated_since": _isoformat(options["updated_since"]),
        }


def redact_sensitive_text(value, sensitive_values=(), *, maximum: int) -> str | None:
    if value is None:
        return None
    text = str(value)[:maximum]
    for sensitive in sorted(
        {str(item).strip() for item in sensitive_values if str(item).strip()},
        key=len,
        reverse=True,
    ):
        if len(sensitive) >= 3:
            text = re.sub(re.escape(sensitive), "[REDACTED]", text, flags=re.I)
    text = EMAIL_RE.sub("[REDACTED]", text)
    text = PHONE_RE.sub("[REDACTED]", text)
    text = KEY_RE.sub("[REDACTED]", text)
    text = BEARER_RE.sub("Bearer [REDACTED]", text)
    text = NAMED_SECRET_RE.sub("[REDACTED]", text)
    text = URL_SECRET_RE.sub(r"\1[REDACTED]", text)
    return text


def _reservation_sensitive_values(reservation) -> set[str]:
    if not reservation:
        return set()
    return {
        value
        for value in (
            reservation.guest_name,
            reservation.guest_first_name,
            reservation.guest_last_name,
            reservation.guest_email,
            reservation.guest_phone,
            reservation.guest_address,
            reservation.confirmation_code,
            reservation.channel_reservation_id,
        )
        if value
    }


def _safe_source_references(references) -> list[dict[str, Any]]:
    safe = []
    for reference in references or []:
        if not isinstance(reference, dict):
            continue
        source_type = str(reference.get("source_type") or "").strip().lower()
        source_id = reference.get("source_id")
        if source_type not in {"message", "review"} or not _is_int(source_id):
            continue
        item = {"source_type": source_type, "source_id": int(source_id)}
        if source_type == "review":
            source_part = str(reference.get("source_part") or "").strip().lower()
            if source_part in {"public_review", "private_feedback", "host_response"}:
                item["source_part"] = source_part
        safe.append(item)
    return safe


def _optional_positive_id(value, parameter: str) -> int | None:
    if value in (None, ""):
        return None
    return parse_positive_int(value, parameter=parameter, default=1)


def _parse_csv(value) -> set[str]:
    return {
        item.strip().lower()
        for item in str(value or "").split(",")
        if item.strip()
    }


def _sort_value(issue, sort: str):
    if sort == "issue_id":
        return int(issue.issue_id)
    if sort == "reported_at":
        return issue_reported_at(issue)
    if sort == "updated_at":
        return issue.updated_at or datetime.min
    if sort == "priority":
        return PRIORITY_SCORE[issue_priority(issue)]
    return STATUS_SCORE[issue_operational_status(issue)]


def _user_reference(user) -> dict[str, Any] | None:
    if not user:
        return None
    return {
        "user_id": int(user.user_id),
        "name": user.name or f"Team member {user.user_id}",
    }


def _isoformat(value) -> str | None:
    if value is None:
        return None
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return value.isoformat(timespec="seconds") + "Z"


def _is_int(value) -> bool:
    try:
        int(value)
        return True
    except (TypeError, ValueError):
        return False
