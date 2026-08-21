#!/usr/bin/env python3
"""Auditable KPI calculations over Hostaway and Brain snapshot data."""

from __future__ import annotations

import logging
import math
from collections import defaultdict
from copy import deepcopy
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from statistics import median
from typing import Any, Iterable

from sqlalchemy import func
from sqlalchemy.orm import selectinload

import dashboard.config as config
from brain.ai import STAY_OUTCOME_PROMPT_VERSION
from brain.models import (
    CalendarSnapshot,
    StayOutcomeClassification,
    get_session as get_brain_session,
)
from brain.scoring import is_confirmed_reservation_status
from brain.stay_outcomes import (
    initial_stay_outcome_checkout_cutoff,
    is_stay_outcome_eligible,
    stay_outcome_activation_time,
)
from dashboard.portfolio_mapping import TAG_PORTFOLIO_NAMES, portfolio_name_for_listing
from database.models import (
    Conversation,
    Listing,
    ListingTag,
    MessageMetadata,
    Reservation,
    Review,
    SyncLog,
    get_session as get_main_session,
)

logger = logging.getLogger(__name__)

REVIEW_COHORT_MIN_DAYS = 15
MAX_CUSTOM_RANGE_DAYS = 366
RESPONSE_SLA_MINUTES = 15
REVIEW_ELIGIBLE_CHANNELS = {"airbnbofficial", "bookingcom"}
OUTCOME_LABELS = ("smooth", "recovered", "unresolved", "needs_review")


@dataclass(frozen=True)
class ReportingPeriod:
    """One inclusive reporting range shared by every KPI."""

    key: str
    label: str
    start_date: date
    end_date: date

    @property
    def end_exclusive(self) -> date:
        return self.end_date + timedelta(days=1)

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "start_date": self.start_date.isoformat(),
            "end_date": self.end_date.isoformat(),
            "day_count": (self.end_date - self.start_date).days + 1,
            "max_custom_days": MAX_CUSTOM_RANGE_DAYS,
        }


KPI_DEFINITIONS = {
    "response": {
        "title": "Human reply time",
        "short_formula": "Median elapsed minutes from the first guest message in a sequence to the next non-automated host message.",
        "why": "Shows how quickly the team responds when a guest starts or resumes a conversation.",
        "numerator": "Elapsed minutes for each answered inbound message sequence.",
        "denominator": "Answered inbound guest message sequences initiated inside the selected reporting period.",
        "raw_fields": [
            "messages_metadata.conversation_id",
            "messages_metadata.is_incoming",
            "messages_metadata.created_at",
            "messages_metadata.sender_name",
        ],
        "inclusions": [
            "Consecutive guest messages are treated as one inbound sequence.",
            "The clock starts at the first message in that sequence.",
            "The 15-minute rate is calculated only for sequences that received a human reply.",
        ],
        "exclusions": [
            "Messages labelled Host (Automated) or automation are excluded as replies.",
            "Unanswered sequences are not included in the median; those open for more than four hours are shown separately.",
        ],
        "window": "The selected reporting period; the trend uses calendar-week buckets within that same range.",
        "source": "Hostaway guest messages synchronized into messages_metadata.",
    },
    "advocacy": {
        "title": "Guest advocacy",
        "short_formula": "Perfect-score rate = published guest reviews rated exactly 10/10 ÷ all published, rated guest reviews.",
        "why": "Tracks the share of guests who gave the strongest possible public rating.",
        "numerator": "Published guest-origin reviews with overall_rating = 10.",
        "denominator": "Published guest-origin reviews with a numeric rating inside the selected reporting period.",
        "raw_fields": [
            "reviews.overall_rating",
            "reviews.review_date",
            "reviews.origin",
            "reviews.status",
            "reviews.reservation_id",
            "reservations.channel_name",
            "reservations.departure_date",
        ],
        "inclusions": [
            "Review coverage uses eligible stays checked out in the selected period and at least 15 days before today.",
            "Coverage numerator counts distinct eligible stays with a published, rated guest review.",
        ],
        "exclusions": [
            "Host-written reviews, unrated reviews, rejected/pending reviews, cancelled stays, and channels without validated review eligibility.",
        ],
        "window": "The selected review-date range. Review coverage uses mature checkouts from the same selected range.",
        "source": "Hostaway reviews and reservations.",
    },
    "revenue": {
        "title": "Gross stay value",
        "short_formula": "Reservation total_price is spread evenly across its occupied nights; nights inside the selected reporting period are summed.",
        "why": "Measures booking value attached to nights actually stayed in the reporting window without pretending it is accounting revenue.",
        "numerator": "Allocated Hostaway total_price for confirmed reservation nights in the window.",
        "denominator": "ADR guardrail = the same allocated gross stay value ÷ confirmed reservation-nights.",
        "raw_fields": [
            "reservations.total_price",
            "reservations.currency",
            "reservations.arrival_date",
            "reservations.departure_date",
            "listings.check_out_time",
            "listings.timezone_name",
            "listings.city",
            "listings.state",
            "reservations.status",
            "reservations.reservation_date",
        ],
        "inclusions": [
            "Confirmed reservation statuses with valid arrival/departure dates and a numeric total_price.",
            "A stay crossing the window boundary contributes only the nights inside the window.",
        ],
        "exclusions": [
            "Cancelled, declined, inquiry, expired and pending reservations.",
            "Values are not combined when more than one currency is present.",
        ],
        "window": "Selected stay dates. The cancellation guardrail uses reservations created inside the same selected period.",
        "source": "Hostaway reservations. This is gross booking value, not net lodging revenue or recognized accounting revenue.",
    },
    "forward": {
        "title": "Forward booking health",
        "short_formula": "OTB occupancy = reserved nights ÷ (reserved nights + available nights) for each forward horizon.",
        "why": "Shows how much currently sellable inventory is already booked and whether bookings increased versus the prior weekly snapshot.",
        "numerator": "Hostaway calendar dates with status reserved/booked.",
        "denominator": "Reserved plus available calendar dates. Blocked and unknown dates are excluded.",
        "raw_fields": [
            "brain.calendar_snapshots.listing_id",
            "brain.calendar_snapshots.calendar_date",
            "brain.calendar_snapshots.snapshot_date",
            "brain.calendar_snapshots.status",
            "brain.calendar_snapshots.is_available",
        ],
        "inclusions": [
            "Latest complete Hostaway calendar snapshot for properties in the selected portfolio.",
            "Pickup compares only listing-date pairs present in both the latest and the snapshot at least seven days earlier.",
        ],
        "exclusions": [
            "Blocked nights are intentionally excluded from the sellable-night denominator.",
            "Missing calendar rows are not assumed to be available.",
        ],
        "window": "Latest available snapshot on or before the selected end date; horizons are 0–14, 15–30 and 31–60 nights from that snapshot.",
        "source": "Daily Hostaway calendar snapshots stored by STR Signal Brain.",
    },
    "outcome": {
        "title": "Stay outcome health",
        "short_formula": "Guest outcome rate = (smooth + recovered stays) ÷ all confidently classified stays.",
        "why": "Separates prevention, successful recovery and unresolved guest-impacting problems.",
        "numerator": "Completed stays classified smooth or recovered from their Hostaway conversation.",
        "denominator": "Smooth + recovered + unresolved completed stays; needs-review stays are excluded and shown in coverage.",
        "raw_fields": [
            "reservations.arrival_date",
            "reservations.departure_date",
            "messages_metadata.message_id",
            "messages_metadata.is_incoming",
            "messages_metadata.created_at",
            "messages_metadata.content_preview",
            "brain.stay_outcome_classifications.*",
        ],
        "inclusions": [
            "Messages from 48 hours before check-in through 24 hours after checkout.",
            "Each confirmed stay is analyzed once, no earlier than 24 hours after its scheduled Hostaway checkout time.",
            "Property timezone uses Hostaway timezone_name when present; otherwise the synchronized US city/state supplies an auditable fallback.",
            "Only material or critical guest-impacting issues change a smooth stay into a problem stay.",
            "Recovered requires credible message evidence that every material/critical issue was resolved.",
        ],
        "exclusions": [
            "Reviews are not used in this classification, keeping Guest Advocacy independent.",
            "Cancelled/no-show/inquiry stays and automated host messages are excluded.",
            "Low-confidence, contradictory or missing conversations are needs review, not silently counted as success.",
        ],
        "window": "Completed stays checked out inside the selected reporting period; classifications finalize 24 hours after scheduled checkout.",
        "source": (
            f"Hostaway conversations classified and cached with prompt rubric {STAY_OUTCOME_PROMPT_VERSION}. "
            f"Conversation analysis uses {config.KPI_STAY_OUTCOME_MODEL}; each reservation is permanently classified once."
        ),
    },
}


def resolve_reporting_period(
    period_key: str | None,
    custom_start: str | date | None = None,
    custom_end: str | date | None = None,
    *,
    today: date | None = None,
) -> ReportingPeriod:
    """Validate a preset or custom KPI reporting range."""
    reference = today or date.today()
    key = (period_key or "current_month").strip().lower()
    current_month_start = reference.replace(day=1)

    if key == "current_month":
        start_date, end_date = current_month_start, reference
        label = "Current month"
    elif key == "last_month":
        end_date = current_month_start - timedelta(days=1)
        start_date = end_date.replace(day=1)
        label = "Last month"
    elif key == "last_3_months":
        start_date = _month_start_offset(current_month_start, -2)
        end_date = reference
        label = "Last 3 months"
    elif key == "custom":
        start_date = _parse_reporting_date(custom_start, "from")
        end_date = _parse_reporting_date(custom_end, "to")
        label = "Custom range"
    else:
        raise ValueError("Choose Current month, Last month, Last 3 months, or Custom range.")

    if start_date > end_date:
        raise ValueError("The From date must be on or before the To date.")
    if end_date > reference:
        raise ValueError("The reporting period cannot end in the future.")
    day_count = (end_date - start_date).days + 1
    if day_count > MAX_CUSTOM_RANGE_DAYS:
        raise ValueError(f"The reporting period cannot exceed {MAX_CUSTOM_RANGE_DAYS} days.")

    return ReportingPeriod(
        key=key,
        label=f"{label} · {_format_range_label(start_date, end_date)}",
        start_date=start_date,
        end_date=end_date,
    )


def definitions_for_period(period: ReportingPeriod) -> dict[str, dict[str, Any]]:
    """Attach the exact selected range semantics to every KPI definition."""
    definitions = deepcopy(KPI_DEFINITIONS)
    dates = _format_range_label(period.start_date, period.end_date)
    definitions["response"]["window"] = (
        f"Guest message sequences initiated {dates}. Weekly trend buckets are clipped to those dates."
    )
    definitions["advocacy"]["window"] = (
        f"Reviews published {dates}. Review coverage uses eligible stays checked out in that range "
        f"and at least {REVIEW_COHORT_MIN_DAYS} days before today."
    )
    definitions["revenue"]["window"] = (
        f"Occupied stay dates {dates}. Booking cancellations use reservations created during those same dates."
    )
    definitions["forward"]["window"] = (
        f"Latest available Hostaway calendar snapshot on or before {_format_single_date(period.end_date)}; "
        "forward horizons begin on that snapshot date."
    )
    definitions["outcome"]["window"] = (
        f"Confirmed stays checked out {dates}; each stay finalizes 24 hours after its scheduled Hostaway checkout time."
    )
    return definitions


class KPIService:
    """Build the portfolio-filtered KPI payload without making external API calls."""

    def __init__(self, *, main_session=None, brain_session=None, now: datetime | None = None):
        self.main_session = main_session or get_main_session(config.MAIN_DATABASE_PATH)
        self.brain_session = brain_session or get_brain_session()
        self._owns_main_session = main_session is None
        self._owns_brain_session = brain_session is None
        self.now = now or datetime.utcnow()
        self.today = self.now.date()

    def close(self):
        if self._owns_main_session:
            self.main_session.close()
        if self._owns_brain_session:
            self.brain_session.close()

    def get_dashboard_data(
        self,
        portfolio_name: str | None = None,
        *,
        period_key: str | None = None,
        custom_start: str | date | None = None,
        custom_end: str | date | None = None,
    ) -> dict[str, Any]:
        reporting_period = resolve_reporting_period(
            period_key,
            custom_start,
            custom_end,
            today=self.today,
        )
        definitions = definitions_for_period(reporting_period)
        listings = self._active_listings()
        scope = build_portfolio_scope(listings, portfolio_name)
        listing_ids = scope["listing_ids"]
        if not listing_ids:
            return {
                "scope": _public_scope(scope),
                "metrics": empty_metrics("No active properties are mapped to this portfolio."),
                "definitions": definitions,
                "freshness": self._freshness([]),
                "reporting_period": reporting_period.as_dict(),
            }

        period_start = reporting_period.start_date
        period_end_exclusive = reporting_period.end_exclusive
        period_start_at = datetime.combine(period_start, time.min)
        period_end_at = min(datetime.combine(period_end_exclusive, time.min), self.now)

        response_conversation_ids = [
            row[0]
            for row in (
                self.main_session.query(MessageMetadata.conversation_id)
                .filter(
                    MessageMetadata.listing_id.in_(listing_ids),
                    MessageMetadata.is_incoming == 1,
                    MessageMetadata.created_at >= period_start_at,
                    MessageMetadata.created_at < period_end_at,
                )
                .distinct()
                .all()
            )
        ]
        response_messages = (
            self.main_session.query(MessageMetadata)
            .filter(
                MessageMetadata.conversation_id.in_(response_conversation_ids or [-1]),
                MessageMetadata.created_at >= period_start_at,
                MessageMetadata.created_at <= self.now,
            )
            .order_by(MessageMetadata.conversation_id, MessageMetadata.created_at, MessageMetadata.message_id)
            .all()
        )
        response = calculate_response_metric(
            response_messages,
            current_start=period_start_at,
            current_end=period_end_at,
            now=self.now,
        )

        review_rows = (
            self.main_session.query(Review)
            .filter(
                Review.listing_id.in_(listing_ids),
                Review.review_date >= period_start,
                Review.review_date < period_end_exclusive,
            )
            .all()
        )
        mature_end_exclusive = min(
            period_end_exclusive,
            self.today - timedelta(days=REVIEW_COHORT_MIN_DAYS - 1),
        )
        cohort_reservations = (
            self.main_session.query(Reservation)
            .filter(
                Reservation.listing_id.in_(listing_ids),
                Reservation.departure_date >= period_start,
                Reservation.departure_date < mature_end_exclusive,
            )
            .all()
        )
        cohort_ids = [row.reservation_id for row in cohort_reservations]
        cohort_reviews = (
            self.main_session.query(Review)
            .filter(Review.reservation_id.in_(cohort_ids or [-1]))
            .all()
        )
        advocacy = calculate_advocacy_metric(
            review_rows,
            cohort_reservations,
            cohort_reviews,
            range_start=period_start,
            range_end_exclusive=period_end_exclusive,
        )

        revenue_reservations = (
            self.main_session.query(Reservation)
            .filter(
                Reservation.listing_id.in_(listing_ids),
                Reservation.arrival_date < period_end_exclusive,
                Reservation.departure_date > period_start,
            )
            .all()
        )
        recent_bookings = (
            self.main_session.query(Reservation)
            .filter(
                Reservation.listing_id.in_(listing_ids),
                Reservation.reservation_date >= period_start_at,
                Reservation.reservation_date < datetime.combine(period_end_exclusive, time.min),
            )
            .all()
        )
        revenue = calculate_revenue_metric(
            revenue_reservations,
            recent_bookings,
            current_start=period_start,
            current_end=period_end_exclusive,
        )

        forward = self._forward_booking_metric(listing_ids, as_of_date=reporting_period.end_date)
        outcome = self._stay_outcome_metric(
            listing_ids,
            current_start=period_start,
            current_end=period_end_exclusive,
        )
        return {
            "scope": _public_scope(scope),
            "metrics": {
                "response": response,
                "advocacy": advocacy,
                "revenue": revenue,
                "forward": forward,
                "outcome": outcome,
            },
            "definitions": definitions,
            "freshness": self._freshness(listing_ids),
            "reporting_period": reporting_period.as_dict(),
        }

    def resolve_scope(self, portfolio_name: str | None = None) -> dict[str, Any]:
        """Resolve the canonical listing scope for KPI reads and source refreshes."""
        return build_portfolio_scope(self._active_listings(), portfolio_name)

    def _active_listings(self) -> list[Listing]:
        return (
            self.main_session.query(Listing)
            .options(selectinload(Listing.tags).selectinload(ListingTag.tag))
            .filter(func.lower(func.coalesce(Listing.status, "")) != "deleted")
            .order_by(Listing.internal_listing_name, Listing.name, Listing.listing_id)
            .all()
        )

    def _forward_booking_metric(self, listing_ids: list[int], *, as_of_date: date) -> dict[str, Any]:
        latest = (
            self.brain_session.query(func.max(CalendarSnapshot.snapshot_date))
            .filter(
                CalendarSnapshot.listing_id.in_(listing_ids),
                CalendarSnapshot.snapshot_date <= as_of_date,
            )
            .scalar()
        )
        if not latest:
            return unavailable_metric(
                f"No Hostaway calendar snapshot is available on or before {_format_range_label(as_of_date, as_of_date)}."
            )
        rows = (
            self.brain_session.query(CalendarSnapshot)
            .filter(
                CalendarSnapshot.listing_id.in_(listing_ids),
                CalendarSnapshot.snapshot_date == latest,
                CalendarSnapshot.calendar_date >= latest,
                CalendarSnapshot.calendar_date < latest + timedelta(days=60),
            )
            .all()
        )
        prior = (
            self.brain_session.query(func.max(CalendarSnapshot.snapshot_date))
            .filter(
                CalendarSnapshot.listing_id.in_(listing_ids),
                CalendarSnapshot.snapshot_date <= latest - timedelta(days=7),
            )
            .scalar()
        )
        prior_rows = []
        if prior:
            prior_rows = (
                self.brain_session.query(CalendarSnapshot)
                .filter(
                    CalendarSnapshot.listing_id.in_(listing_ids),
                    CalendarSnapshot.snapshot_date == prior,
                    CalendarSnapshot.calendar_date >= latest,
                    CalendarSnapshot.calendar_date < latest + timedelta(days=60),
                )
                .all()
            )
        return calculate_forward_metric(
            rows,
            prior_rows,
            snapshot_date=latest,
            prior_snapshot_date=prior,
            expected_listing_count=len(listing_ids),
        )

    def _stay_outcome_metric(
        self,
        listing_ids: list[int],
        *,
        current_start: date,
        current_end: date,
    ) -> dict[str, Any]:
        finalized_end = min(current_end, self.today)
        activation_at = stay_outcome_activation_time(reference_time=self.now)
        earliest_checkout_at = initial_stay_outcome_checkout_cutoff(activation_at)
        query_start = max(current_start, earliest_checkout_at.date())
        reservations = (
            self.main_session.query(Reservation)
            .filter(
                Reservation.listing_id.in_(listing_ids),
                Reservation.departure_date >= query_start,
                Reservation.departure_date < finalized_end,
                Reservation.arrival_date.isnot(None),
            )
            .all()
        )
        reservations = [row for row in reservations if is_confirmed_reservation_status(row.status)]
        outcome_listing_ids = sorted({int(row.listing_id) for row in reservations})
        outcome_listings = {
            int(row.listing_id): row
            for row in self.main_session.query(Listing)
            .filter(Listing.listing_id.in_(outcome_listing_ids or [-1]))
            .all()
        }
        reservations = [
            row
            for row in reservations
            if is_stay_outcome_eligible(
                row,
                outcome_listings.get(int(row.listing_id)),
                reference_time=self.now,
                earliest_checkout_at=earliest_checkout_at,
            )
        ]
        reservation_ids = [row.reservation_id for row in reservations]
        try:
            classifications = (
                self.brain_session.query(StayOutcomeClassification)
                .filter(
                    StayOutcomeClassification.reservation_id.in_(reservation_ids or [-1]),
                    StayOutcomeClassification.record_status == "current",
                )
                .order_by(StayOutcomeClassification.classified_at.desc())
                .all()
            )
        except Exception as exc:
            logger.warning("Stay outcome table is not available: %s", exc)
            self.brain_session.rollback()
            classifications = []
        result = calculate_outcome_metric(
            reservations,
            classifications,
            current_start=current_start,
            current_end=finalized_end,
        )
        result["classification_enabled"] = bool(config.KPI_ENABLE_STAY_OUTCOME_CLASSIFICATION)
        if not config.KPI_ENABLE_STAY_OUTCOME_CLASSIFICATION and not result.get("classified_stays"):
            result["status"] = "unavailable"
            result["reason"] = (
                "Conversation classification is off pending approval to process guest message text "
                "with the configured OpenAI API."
            )
        return result

    def _freshness(self, listing_ids: list[int]) -> dict[str, Any]:
        listing_filter = [Listing.listing_id.in_(listing_ids)] if listing_ids else []
        reservation_filter = [Reservation.listing_id.in_(listing_ids)] if listing_ids else []
        review_filter = [Review.listing_id.in_(listing_ids)] if listing_ids else []
        source_check_times = dict(
            self.main_session.query(SyncLog.sync_type, func.max(SyncLog.completed_at))
            .filter(
                SyncLog.sync_type.in_(("listings", "reservations", "messages", "reviews")),
                SyncLog.status.in_(("success", "partial")),
                SyncLog.completed_at.isnot(None),
            )
            .group_by(SyncLog.sync_type)
            .all()
        )

        def latest_source_check(sync_type: str, fallback: datetime | None) -> datetime | None:
            return source_check_times.get(sync_type) or fallback

        sources = [
            _freshness_item(
                "Properties",
                latest_source_check(
                    "listings",
                    self.main_session.query(func.max(Listing.last_synced_at)).filter(*listing_filter).scalar(),
                ),
            ),
            _freshness_item(
                "Reservations",
                latest_source_check(
                    "reservations",
                    self.main_session.query(func.max(Reservation.last_synced_at)).filter(*reservation_filter).scalar(),
                ),
            ),
            _freshness_item(
                "Messages",
                latest_source_check(
                    "messages",
                    self.main_session.query(func.max(Conversation.last_synced_at)).filter(
                        Conversation.listing_id.in_(listing_ids or [-1])
                    ).scalar(),
                ),
            ),
            _freshness_item(
                "Reviews",
                latest_source_check(
                    "reviews",
                    self.main_session.query(func.max(Review.last_synced_at)).filter(*review_filter).scalar(),
                ),
            ),
            _freshness_item(
                "Calendar",
                self.brain_session.query(func.max(CalendarSnapshot.created_at)).filter(CalendarSnapshot.listing_id.in_(listing_ids or [-1])).scalar(),
            ),
        ]
        try:
            latest_outcome = self.brain_session.query(func.max(StayOutcomeClassification.classified_at)).filter(
                StayOutcomeClassification.listing_id.in_(listing_ids or [-1]),
                StayOutcomeClassification.record_status == "current",
            ).scalar()
        except Exception:
            self.brain_session.rollback()
            latest_outcome = None
        sources.append(_freshness_item("Stay outcomes", latest_outcome))
        # The headline represents the least-recent available required source,
        # not the freshest source. Otherwise one recent review could make an
        # old calendar or message snapshot appear current.
        required_timestamps = [
            item["timestamp"]
            for item in sources
            if item["name"] != "Stay outcomes" and item["timestamp"]
        ]
        return {
            "latest_at": min(required_timestamps) if required_timestamps else None,
            "sources": sources,
        }


def build_portfolio_scope(listings: Iterable[Any], requested: str | None) -> dict[str, Any]:
    listing_portfolios: dict[int, str] = {}
    counts: dict[str, int] = defaultdict(int)
    for listing in listings or []:
        tag_names = [link.tag.name for link in getattr(listing, "tags", []) if getattr(link, "tag", None)]
        portfolio = portfolio_name_for_listing(listing.listing_id, tag_names) or "Unmapped"
        listing_portfolios[int(listing.listing_id)] = portfolio
        counts[portfolio] += 1
    ordered_names = [name for name in TAG_PORTFOLIO_NAMES if counts.get(name)]
    ordered_names.extend(sorted(name for name in counts if name not in set(TAG_PORTFOLIO_NAMES)))
    selected = (requested or "all").strip()
    if selected.lower() == "all" or selected not in counts:
        selected = "all"
        listing_ids = sorted(listing_portfolios)
    else:
        listing_ids = sorted(listing_id for listing_id, name in listing_portfolios.items() if name == selected)
    return {
        "selected": selected,
        "listing_ids": listing_ids,
        "property_count": len(listing_ids),
        "portfolios": [{"name": name, "property_count": counts[name]} for name in ordered_names],
    }


def calculate_response_metric(
    messages: Iterable[Any],
    *,
    current_start: datetime,
    current_end: datetime,
    now: datetime,
) -> dict[str, Any]:
    grouped: dict[int, list[Any]] = defaultdict(list)
    for message in messages or []:
        if getattr(message, "conversation_id", None) is not None:
            grouped[int(message.conversation_id)].append(message)
    episodes = []
    automated_excluded = 0
    outgoing_host_messages = 0
    for conversation_messages in grouped.values():
        pending_at = None
        pending_count = 0
        for message in sorted(conversation_messages, key=lambda row: (row.created_at or datetime.min, row.message_id or 0)):
            if not message.created_at:
                continue
            if bool(message.is_incoming):
                if pending_at is None:
                    pending_at = message.created_at
                pending_count += 1
                continue
            outgoing_host_messages += 1
            if _is_automated_message(message):
                automated_excluded += 1
                continue
            if pending_at is not None and message.created_at >= pending_at:
                episodes.append(
                    {
                        "started_at": pending_at,
                        "response_minutes": (message.created_at - pending_at).total_seconds() / 60.0,
                        "incoming_count": pending_count,
                        "answered": True,
                    }
                )
                pending_at = None
                pending_count = 0
        if pending_at is not None:
            episodes.append(
                {
                    "started_at": pending_at,
                    "response_minutes": None,
                    "incoming_count": pending_count,
                    "answered": False,
                }
            )

    current = [
        episode
        for episode in episodes
        if current_start <= episode["started_at"] < current_end
    ]
    answered = [episode for episode in current if episode["response_minutes"] is not None]
    values = [episode["response_minutes"] for episode in answered]
    open_over_four_hours = sum(
        1 for episode in current
        if not episode["answered"] and (now - episode["started_at"]).total_seconds() >= 4 * 3600
    )
    trend = []
    trend_end_exclusive = current_end.date()
    if current_end.time() != time.min:
        trend_end_exclusive += timedelta(days=1)
    for week_start, week_end in _week_buckets(current_start.date(), trend_end_exclusive):
        week_values = [
            episode["response_minutes"]
            for episode in episodes
            if episode["response_minutes"] is not None
            and week_start <= episode["started_at"].date() < week_end
        ]
        trend.append(
            {
                "week": week_start.isoformat(),
                "median_minutes": round(median(week_values), 1) if week_values else None,
                "sample_size": len(week_values),
            }
        )
    if not values:
        status = "unavailable"
        reason = "No answered guest message sequences were found in the selected reporting period."
    elif outgoing_host_messages >= 100 and automated_excluded == 0:
        status = "degraded"
        reason = "Automation labels are missing from this message snapshot, so scheduled Hostaway messages may still be included."
    else:
        status = "ok"
        reason = None
    return {
        "status": status,
        "reason": reason,
        "median_minutes": round(median(values), 1) if values else None,
        "p90_minutes": round(_percentile(values, 0.9), 1) if values else None,
        "within_15_minutes_pct": _pct(sum(value <= RESPONSE_SLA_MINUTES for value in values), len(values)),
        "answered_sequences": len(answered),
        "open_over_4_hours": open_over_four_hours,
        "automated_messages_excluded": automated_excluded,
        "outgoing_host_messages": outgoing_host_messages,
        "trend": trend,
        "raw": {
            "answered_sequence_count": len(answered),
            "all_inbound_sequence_count": len(current),
            "within_15_minutes_count": sum(value <= RESPONSE_SLA_MINUTES for value in values),
        },
    }


def calculate_advocacy_metric(
    reviews: Iterable[Any],
    cohort_reservations: Iterable[Any],
    cohort_reviews: Iterable[Any],
    *,
    range_start: date,
    range_end_exclusive: date,
) -> dict[str, Any]:
    rated = [review for review in reviews or [] if _is_published_guest_review(review)]
    perfect_count = sum(1 for review in rated if math.isclose(float(review.overall_rating), 10.0, abs_tol=0.01))
    low_count = sum(1 for review in rated if float(review.overall_rating) < 8.0)
    eligible_reservations = [
        reservation
        for reservation in cohort_reservations or []
        if is_confirmed_reservation_status(getattr(reservation, "status", None))
        and _normalize_channel(getattr(reservation, "channel_name", None) or getattr(reservation, "source", None)) in REVIEW_ELIGIBLE_CHANNELS
    ]
    eligible_ids = {reservation.reservation_id for reservation in eligible_reservations}
    reviewed_ids = {
        review.reservation_id
        for review in cohort_reviews or []
        if review.reservation_id in eligible_ids and _is_published_guest_review(review)
    }
    trend = []
    for week_start, week_end in _week_buckets(range_start, range_end_exclusive):
        week_reviews = [review for review in rated if week_start <= review.review_date < week_end]
        week_perfect = sum(1 for review in week_reviews if math.isclose(float(review.overall_rating), 10.0, abs_tol=0.01))
        trend.append(
            {
                "week": week_start.isoformat(),
                "perfect_score_pct": _pct(week_perfect, len(week_reviews)),
                "sample_size": len(week_reviews),
            }
        )
    return {
        "status": "ok" if rated else "unavailable",
        "reason": None if rated else "No published, rated guest reviews were found in the selected reporting period.",
        "perfect_score_pct": _pct(perfect_count, len(rated)),
        "average_rating": round(sum(float(review.overall_rating) for review in rated) / len(rated), 2) if rated else None,
        "low_score_pct": _pct(low_count, len(rated)),
        "review_coverage_pct": _pct(len(reviewed_ids), len(eligible_ids)),
        "review_count": len(rated),
        "eligible_stay_count": len(eligible_ids),
        "reviewed_stay_count": len(reviewed_ids),
        "trend": trend,
        "raw": {
            "perfect_review_count": perfect_count,
            "rated_published_guest_review_count": len(rated),
            "low_review_count": low_count,
            "eligible_review_cohort_stays": len(eligible_ids),
            "reviewed_review_cohort_stays": len(reviewed_ids),
        },
    }


def calculate_revenue_metric(
    reservations: Iterable[Any],
    recent_bookings: Iterable[Any],
    *,
    current_start: date,
    current_end: date,
) -> dict[str, Any]:
    nightly_values = []
    currencies = set()
    for reservation in reservations or []:
        if not is_confirmed_reservation_status(getattr(reservation, "status", None)):
            continue
        if not reservation.arrival_date or not reservation.departure_date or reservation.departure_date <= reservation.arrival_date:
            continue
        if reservation.total_price is None:
            continue
        nights = (reservation.departure_date - reservation.arrival_date).days
        if nights <= 0:
            continue
        currency = str(reservation.currency or "Unknown").upper()
        currencies.add(currency)
        per_night = float(reservation.total_price) / nights
        stay_date = max(reservation.arrival_date, current_start)
        stop = min(reservation.departure_date, current_end)
        while stay_date < stop:
            nightly_values.append({"date": stay_date, "value": per_night, "currency": currency})
            stay_date += timedelta(days=1)
    current_values = [item for item in nightly_values if current_start <= item["date"] < current_end]
    current_currencies = {item["currency"] for item in current_values}
    status = "ok"
    reason = None
    if not current_values:
        status = "unavailable"
        reason = "No confirmed reservation value falls inside the selected stay dates."
    elif len(current_currencies) > 1:
        status = "unavailable"
        reason = "More than one currency is present; values are not combined without an approved FX policy."
    currency = next(iter(current_currencies)) if len(current_currencies) == 1 else None
    gross_value = sum(item["value"] for item in current_values) if status == "ok" else None
    created = [
        row for row in (recent_bookings or [])
        if is_confirmed_reservation_status(getattr(row, "status", None))
        or _is_cancelled_status(getattr(row, "status", None))
    ]
    cancelled = [row for row in created if _is_cancelled_status(getattr(row, "status", None))]
    trend = []
    for week_start, week_end in _week_buckets(current_start, current_end):
        week_values = [item["value"] for item in nightly_values if week_start <= item["date"] < week_end]
        trend.append(
            {
                "week": week_start.isoformat(),
                "gross_stay_value": round(sum(week_values), 2) if week_values and len(currencies) == 1 else None,
                "reservation_nights": len(week_values),
            }
        )
    return {
        "status": status,
        "reason": reason,
        "gross_stay_value": round(gross_value, 2) if gross_value is not None else None,
        "adr": round(gross_value / len(current_values), 2) if gross_value is not None and current_values else None,
        "reservation_nights": len(current_values),
        "currency": currency,
        "cancellation_rate_pct": _pct(len(cancelled), len(created)),
        "bookings_created": len(created),
        "cancelled_bookings": len(cancelled),
        "trend": trend,
        "raw": {
            "allocated_total_price": round(gross_value, 2) if gross_value is not None else None,
            "confirmed_reservation_nights": len(current_values),
            "currencies": sorted(currencies),
            "cancelled_recent_bookings": len(cancelled),
            "recent_bookings": len(created),
        },
    }


def calculate_forward_metric(
    rows: Iterable[Any],
    prior_rows: Iterable[Any],
    *,
    snapshot_date: date,
    prior_snapshot_date: date | None,
    expected_listing_count: int,
) -> dict[str, Any]:
    current_map = {(int(row.listing_id), row.calendar_date): _calendar_state(row) for row in rows or []}
    prior_map = {(int(row.listing_id), row.calendar_date): _calendar_state(row) for row in prior_rows or []}
    horizons = []
    segments = ((0, 14, "0–14 nights"), (14, 30, "15–30 nights"), (30, 60, "31–60 nights"))
    for start_offset, end_offset, label in segments:
        start = snapshot_date + timedelta(days=start_offset)
        end = snapshot_date + timedelta(days=end_offset)
        keys = [key for key in current_map if start <= key[1] < end]
        booked = sum(current_map[key] == "reserved" for key in keys)
        available = sum(current_map[key] == "available" for key in keys)
        blocked = sum(current_map[key] == "blocked" for key in keys)
        sellable = booked + available
        comparable = [key for key in keys if key in prior_map]
        pickup = None
        if prior_snapshot_date and comparable:
            pickup = sum(current_map[key] == "reserved" for key in comparable) - sum(
                prior_map[key] == "reserved" for key in comparable
            )
        horizons.append(
            {
                "key": f"{start_offset}_{end_offset}",
                "label": label,
                "start_date": start.isoformat(),
                "end_date_exclusive": end.isoformat(),
                "occupancy_pct": _pct(booked, sellable),
                "booked_nights": booked,
                "available_nights": available,
                "blocked_nights": blocked,
                "sellable_nights": sellable,
                "pickup_nights": pickup,
                "pickup_comparable_nights": len(comparable),
            }
        )
    covered_listings = {int(row.listing_id) for row in rows or []}
    property_coverage_pct = _pct(len(covered_listings), expected_listing_count)
    return {
        "status": "ok" if rows and (property_coverage_pct or 0) >= 80 else "degraded" if rows else "unavailable",
        "reason": None if rows else "No calendar rows are available in the latest snapshot.",
        "snapshot_date": snapshot_date.isoformat(),
        "prior_snapshot_date": prior_snapshot_date.isoformat() if prior_snapshot_date else None,
        "property_coverage_pct": property_coverage_pct,
        "covered_properties": len(covered_listings),
        "expected_properties": expected_listing_count,
        "horizons": horizons,
        "raw": {
            "latest_snapshot_rows": len(current_map),
            "prior_snapshot_rows": len(prior_map),
        },
    }


def calculate_outcome_metric(
    reservations: Iterable[Any],
    classifications: Iterable[Any],
    *,
    current_start: date,
    current_end: date,
) -> dict[str, Any]:
    reservation_map = {int(row.reservation_id): row for row in reservations or []}
    latest: dict[int, Any] = {}
    for row in classifications or []:
        reservation_id = int(row.reservation_id)
        if reservation_id not in latest:
            latest[reservation_id] = row
    current_reservations = [
        row
        for row in reservation_map.values()
        if current_start <= row.departure_date < current_end
    ]
    counts = {label: 0 for label in OUTCOME_LABELS}
    confidence_values = []
    for reservation in current_reservations:
        classification = latest.get(reservation.reservation_id)
        outcome = getattr(classification, "outcome", None) if classification else "needs_review"
        if outcome not in counts:
            outcome = "needs_review"
        counts[outcome] += 1
        if classification and outcome != "needs_review":
            confidence_values.append(float(classification.confidence or 0.0))
    classified = counts["smooth"] + counts["recovered"] + counts["unresolved"]
    problem_stays = counts["recovered"] + counts["unresolved"]
    coverage = _pct(classified, len(current_reservations))
    trend = []
    for week_start, week_end in _week_buckets(current_start, current_end):
        week_outcomes = []
        for reservation in reservation_map.values():
            if not (week_start <= reservation.departure_date < week_end):
                continue
            classification = latest.get(reservation.reservation_id)
            outcome = getattr(classification, "outcome", None) if classification else None
            if outcome in {"smooth", "recovered", "unresolved"}:
                week_outcomes.append(outcome)
        successful = sum(outcome in {"smooth", "recovered"} for outcome in week_outcomes)
        trend.append(
            {
                "week": week_start.isoformat(),
                "guest_outcome_pct": _pct(successful, len(week_outcomes)),
                "classified_stays": len(week_outcomes),
            }
        )
    if not current_reservations:
        status = "unavailable"
        reason = "No completed stays were found in the selected reporting period."
    elif coverage is None or coverage < 95:
        status = "degraded"
        reason = "Classification coverage is below 95%; the outcome rate is shown as provisional."
    else:
        status = "ok"
        reason = None
    return {
        "status": status,
        "reason": reason,
        "guest_outcome_pct": _pct(counts["smooth"] + counts["recovered"], classified),
        "problem_incidence_pct": _pct(problem_stays, classified),
        "recovery_rate_pct": _pct(counts["recovered"], problem_stays),
        "classification_coverage_pct": coverage,
        "eligible_stays": len(current_reservations),
        "classified_stays": classified,
        "average_confidence_pct": round((sum(confidence_values) / len(confidence_values)) * 100, 1) if confidence_values else None,
        "counts": counts,
        "trend": trend,
        "raw": {
            "successful_classified_stays": counts["smooth"] + counts["recovered"],
            "problem_stays": problem_stays,
            "classified_stays": classified,
            "eligible_completed_stays": len(current_reservations),
        },
    }


def unavailable_metric(reason: str) -> dict[str, Any]:
    return {"status": "unavailable", "reason": reason}


def empty_metrics(reason: str) -> dict[str, Any]:
    return {key: unavailable_metric(reason) for key in KPI_DEFINITIONS}


def _public_scope(scope: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in scope.items() if key != "listing_ids"}


def _week_buckets(start_date: date, end_exclusive: date) -> list[tuple[date, date]]:
    """Return clipped Monday-based buckets covering an inclusive/exclusive range."""
    if end_exclusive <= start_date:
        return []
    buckets: list[tuple[date, date]] = []
    cursor = start_date - timedelta(days=start_date.weekday())
    while cursor < end_exclusive:
        bucket_start = max(cursor, start_date)
        bucket_end = min(cursor + timedelta(days=7), end_exclusive)
        if bucket_start < bucket_end:
            buckets.append((bucket_start, bucket_end))
        cursor += timedelta(days=7)
    return buckets


def _month_start_offset(month_start: date, offset: int) -> date:
    month_index = month_start.year * 12 + (month_start.month - 1) + offset
    year, zero_based_month = divmod(month_index, 12)
    return date(year, zero_based_month + 1, 1)


def _parse_reporting_date(value: str | date | None, field_name: str) -> date:
    if isinstance(value, date):
        return value
    if not value:
        raise ValueError(f"Select a {field_name.title()} date for the custom reporting period.")
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"The {field_name.title()} date must use YYYY-MM-DD format.") from exc


def _format_single_date(value: date) -> str:
    return f"{value.strftime('%b')} {value.day}, {value.year}"


def _format_range_label(start_date: date, end_date: date) -> str:
    if start_date == end_date:
        return _format_single_date(start_date)
    if start_date.year == end_date.year and start_date.month == end_date.month:
        return f"{start_date.strftime('%b')} {start_date.day}–{end_date.day}, {start_date.year}"
    if start_date.year == end_date.year:
        return (
            f"{start_date.strftime('%b')} {start_date.day}–"
            f"{end_date.strftime('%b')} {end_date.day}, {start_date.year}"
        )
    return f"{_format_single_date(start_date)}–{_format_single_date(end_date)}"


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(percentile * len(ordered)) - 1))
    return ordered[index]


def _pct(numerator: int | float, denominator: int | float) -> float | None:
    if not denominator:
        return None
    return round((float(numerator) / float(denominator)) * 100.0, 1)


def _is_automated_message(message: Any) -> bool:
    sender = str(getattr(message, "sender_name", "") or "").lower()
    return "automated" in sender or "automation" in sender


def _is_published_guest_review(review: Any) -> bool:
    if getattr(review, "overall_rating", None) is None or getattr(review, "review_date", None) is None:
        return False
    return str(getattr(review, "origin", "") or "").strip().lower() == "guest" and str(
        getattr(review, "status", "") or ""
    ).strip().lower() == "published"


def _normalize_channel(value: Any) -> str:
    return "".join(character for character in str(value or "").lower() if character.isalnum())


def _is_cancelled_status(value: Any) -> bool:
    normalized = str(value or "").strip().lower()
    return "cancel" in normalized


def _calendar_state(row: Any) -> str:
    status = str(getattr(row, "status", "") or "").strip().lower()
    if status in {"reserved", "booked"}:
        return "reserved"
    if getattr(row, "is_available", None) is True or status == "available":
        return "available"
    if getattr(row, "is_available", None) is False or status == "blocked":
        return "blocked"
    return "unknown"


def _freshness_item(name: str, value: datetime | None) -> dict[str, Any]:
    return {"name": name, "timestamp": value.isoformat() if value else None}
