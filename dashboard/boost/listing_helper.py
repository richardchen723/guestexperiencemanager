#!/usr/bin/env python3
"""
Helper to fetch listing data from the database and Hostaway API
for the boost campaign listing dropdown.
"""

import logging
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Tuple

from database.models import Listing
from dashboard.boost.models import get_session

logger = logging.getLogger(__name__)


def get_all_listings() -> List[Dict[str, Any]]:
    """Return all listings from the database for the campaign dropdown."""
    session = get_session()
    try:
        rows = (
            session.query(
                Listing.listing_id,
                Listing.name,
                Listing.internal_listing_name,
                Listing.city,
                Listing.state,
            )
            .order_by(Listing.name)
            .all()
        )
        return [
            {
                "id": r.listing_id,
                "name": r.name or r.internal_listing_name or f"Listing {r.listing_id}",
                "city": r.city,
                "state": r.state,
            }
            for r in rows
        ]
    finally:
        session.close()


def get_listing_details(listing_id: int) -> Optional[Dict[str, Any]]:
    """Fetch listing details including Airbnb URL and lat/lng from Hostaway API."""
    try:
        from sync.api_client import HostawayAPIClient

        client = HostawayAPIClient()
        data = client.get_listing(listing_id)
        if not data:
            return None
        return {
            "id": data.get("id"),
            "name": data.get("name") or data.get("internalListingName"),
            "airbnb_url": data.get("airbnbListingUrl"),
            "lat": data.get("lat"),
            "lng": data.get("lng"),
            "city": data.get("city"),
            "state": data.get("state"),
        }
    except Exception as e:
        logger.error(f"Failed to fetch listing {listing_id} from Hostaway: {e}")
        return None


def get_available_windows(
    listing_id: int,
    window_start: date,
    window_end: date,
    min_nights: int,
) -> List[Tuple[date, date]]:
    """Fetch the listing calendar from Hostaway and return contiguous
    available windows of at least *min_nights* nights.

    Each window is a (first_available_date, last_available_date) tuple.
    A valid check-in can start on first_available_date and check-out can be
    at most last_available_date + 1 day.

    Returns an empty list if no windows are found or the API call fails.
    """
    try:
        from sync.api_client import HostawayAPIClient

        client = HostawayAPIClient()
        days = client.get_calendar(
            listing_id,
            start_date=window_start.isoformat(),
            end_date=window_end.isoformat(),
        )
    except Exception as e:
        logger.error(f"Failed to fetch calendar for listing {listing_id}: {e}")
        return []

    if not days:
        logger.warning(f"No calendar data for listing {listing_id}")
        return []

    available_dates = set()
    for day in days:
        d = day.get("date")
        is_avail = day.get("isAvailable")
        status = day.get("status", "")
        if d and is_avail == 1 and status not in ("reserved", "blocked"):
            available_dates.add(date.fromisoformat(d))

    if not available_dates:
        logger.warning(f"No available dates for listing {listing_id} "
                       f"between {window_start} and {window_end}")
        return []

    sorted_dates = sorted(available_dates)

    windows: List[Tuple[date, date]] = []
    run_start = sorted_dates[0]
    prev = sorted_dates[0]

    for d in sorted_dates[1:]:
        if d == prev + timedelta(days=1):
            prev = d
        else:
            run_length = (prev - run_start).days + 1
            if run_length >= min_nights:
                windows.append((run_start, prev))
            run_start = d
            prev = d

    run_length = (prev - run_start).days + 1
    if run_length >= min_nights:
        windows.append((run_start, prev))

    logger.info(f"Listing {listing_id}: found {len(windows)} available windows "
                f"(>= {min_nights} nights) between {window_start} and {window_end}")
    return windows
