"""Timezone-aware checkout helpers for the review workflow."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import dashboard.config as config


DEFAULT_CHECKOUT_HOUR = 11

# Hostaway does not currently populate timezone_name for every synchronized
# listing. Resolve the known US location before falling back to the team's
# configured application timezone.
US_STATE_TIMEZONES = {
    "AK": "America/Anchorage", "AL": "America/Chicago", "AR": "America/Chicago",
    "AZ": "America/Phoenix", "CA": "America/Los_Angeles", "CO": "America/Denver",
    "CT": "America/New_York", "DC": "America/New_York", "DE": "America/New_York",
    "FL": "America/New_York", "GA": "America/New_York", "HI": "Pacific/Honolulu",
    "IA": "America/Chicago", "ID": "America/Denver", "IL": "America/Chicago",
    "IN": "America/Indiana/Indianapolis", "KS": "America/Chicago", "KY": "America/New_York",
    "LA": "America/Chicago", "MA": "America/New_York", "MD": "America/New_York",
    "ME": "America/New_York", "MI": "America/Detroit", "MN": "America/Chicago",
    "MO": "America/Chicago", "MS": "America/Chicago", "MT": "America/Denver",
    "NC": "America/New_York", "ND": "America/Chicago", "NE": "America/Chicago",
    "NH": "America/New_York", "NJ": "America/New_York", "NM": "America/Denver",
    "NV": "America/Los_Angeles", "NY": "America/New_York", "OH": "America/New_York",
    "OK": "America/Chicago", "OR": "America/Los_Angeles", "PA": "America/New_York",
    "RI": "America/New_York", "SC": "America/New_York", "SD": "America/Chicago",
    "TN": "America/Chicago", "TX": "America/Chicago", "UT": "America/Denver",
    "VA": "America/New_York", "VT": "America/New_York", "WA": "America/Los_Angeles",
    "WI": "America/Chicago", "WV": "America/New_York", "WY": "America/Denver",
}
US_CITY_TIMEZONE_OVERRIDES = {
    ("pigeon forge", "TN"): "America/New_York",
    ("sevierville", "TN"): "America/New_York",
    ("el paso", "TX"): "America/Denver",
}


def _valid_timezone_name(value: Any) -> Optional[str]:
    name = str(value or "").strip()
    if not name:
        return None
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return None
    return name


def application_timezone_name() -> str:
    """Return the configured operations timezone, safely defaulting to Eastern."""
    return _valid_timezone_name(config.APPLICATION_TIMEZONE) or "America/New_York"


def listing_timezone(listing: Any | None) -> tuple[str, str]:
    """Resolve a listing's IANA timezone and record which source won."""
    explicit = _valid_timezone_name(getattr(listing, "timezone_name", None))
    if explicit:
        return explicit, "hostaway_timezone"

    state = str(getattr(listing, "state", None) or "").strip().upper()
    city = str(getattr(listing, "city", None) or "").strip().lower()
    city_timezone = US_CITY_TIMEZONE_OVERRIDES.get((city, state))
    if city_timezone:
        return city_timezone, "city_state_fallback"
    state_timezone = US_STATE_TIMEZONES.get(state)
    if state_timezone:
        return state_timezone, "state_fallback"
    return application_timezone_name(), "application_fallback"


def checkout_clock(value: Any) -> time:
    """Normalize Hostaway's integer checkout time in hour or HHMM form."""
    try:
        numeric = int(value)
    except (TypeError, ValueError):
        return time(DEFAULT_CHECKOUT_HOUR)
    if 0 <= numeric <= 23:
        return time(numeric)
    if 100 <= numeric <= 2359:
        hour, minute = divmod(numeric, 100)
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return time(hour, minute)
    return time(DEFAULT_CHECKOUT_HOUR)


def scheduled_checkout_at_local(reservation: Any, listing: Any | None = None) -> Optional[datetime]:
    """Build the scheduled checkout instant in the property's local timezone."""
    departure_date = getattr(reservation, "departure_date", None)
    if not departure_date:
        return None
    timezone_name, _ = listing_timezone(listing)
    return datetime.combine(
        departure_date,
        checkout_clock(getattr(listing, "check_out_time", None)),
        tzinfo=ZoneInfo(timezone_name),
    )


def scheduled_checkout_at_utc(reservation: Any, listing: Any | None = None) -> Optional[datetime]:
    local_checkout = scheduled_checkout_at_local(reservation, listing)
    return local_checkout.astimezone(timezone.utc) if local_checkout else None


def review_window_bounds(
    reservation: Any,
    listing: Any | None = None,
    *,
    window_days: int,
) -> tuple[Optional[datetime], Optional[datetime]]:
    """Return UTC bounds while retaining the same local clock across DST."""
    local_checkout = scheduled_checkout_at_local(reservation, listing)
    if not local_checkout:
        return None, None
    local_expiration = local_checkout + timedelta(days=window_days)
    return local_checkout.astimezone(timezone.utc), local_expiration.astimezone(timezone.utc)


def reference_time_utc(
    value: Optional[datetime] = None,
    *,
    legacy_today: Optional[date] = None,
) -> datetime:
    """Normalize runtime/test clocks to one aware UTC instant."""
    if value is None and legacy_today is not None:
        value = datetime.combine(
            legacy_today,
            time.max,
            tzinfo=ZoneInfo(application_timezone_name()),
        )
    if value is None:
        return datetime.now(timezone.utc)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def application_local_date(value: datetime) -> date:
    return reference_time_utc(value).astimezone(ZoneInfo(application_timezone_name())).date()
