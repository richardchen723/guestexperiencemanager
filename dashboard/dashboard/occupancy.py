"""Monthly booked-night estimates over the current operational listing scope."""

from collections import Counter
from datetime import date, timedelta


# Hostaway's new/modified reservations are bookings; inquiry and ownerStay are not.
BOOKED_STATUSES = {'new', 'modified', 'confirmed'}
INACTIVE_STATUSES = {'deleted', 'archived', 'inactive'}
ICAL_SOURCES = {'ical', 'customical', 'importedical', 'icalimport'}


def normalize(value):
    return ''.join(c for c in str(value or '').lower() if c.isalnum())


def month_start(day: date, offset: int = 0) -> date:
    year, month = divmod(day.year * 12 + day.month - 1 + offset, 12)
    return date(year, month + 1, 1)


def build_occupancy(listings, reservations, *, today: date, source_available=True):
    """Deduplicate listing-night pairs, using [arrival, departure) intervals.

    This is a current-catalog estimate, not sellable-inventory occupancy. The
    database has no effective management dates or verified physical-unit map;
    calendar capacity is explicit rather than mislabeled as available nights.
    """
    listing_ids = {x.listing_id for x in listings
                   if normalize(x.status) not in INACTIVE_STATUSES}
    start, end = month_start(today, -6), month_start(today, 6)
    occupied = set()
    excluded = Counter()
    for row in reservations:
        if row.listing_id not in listing_ids:
            continue
        if normalize(row.status) not in BOOKED_STATUSES or row.cancellation_date:
            excluded['non_booking'] += 1
            continue
        if any(normalize(value) in ICAL_SOURCES for value in (row.source, row.channel_name)):
            excluded['ical'] += 1
            continue
        arrival, departure = row.arrival_date, row.departure_date
        if row.is_dates_unspecified or not arrival or not departure or departure <= arrival:
            excluded['invalid_dates'] += 1
            continue
        night, stop = max(arrival, start), min(departure, end)
        while night < stop:
            occupied.add((row.listing_id, night))
            night += timedelta(days=1)
    by_month = Counter(night.replace(day=1) for _, night in occupied)
    months = []
    for offset in range(-6, 6):
        first, next_month = month_start(today, offset), month_start(today, offset + 1)
        capacity = len(listing_ids) * (next_month - first).days
        booked = by_month[first] if source_available else None
        months.append({
            'month': first.strftime('%Y-%m'),
            'occupancy_rate': round(booked / capacity * 100, 2)
            if source_available and capacity else None,
            'booked_nights': booked,
            'capacity_nights': capacity if source_available else None,
            'listing_count': len(listing_ids) if source_available else None,
            'period': 'historical' if offset < 0 else 'on_books',
            'is_current_month': offset == 0,
            'listing_id': None,
            'listing_name': 'Current operational listings',
        })
    return {'months': months, 'metadata': {
        'status': 'estimate' if source_available and listing_ids else 'unavailable',
        'listing_count': len(listing_ids) if source_available else None,
        'window_start': start.isoformat(),
        'window_end_exclusive': end.isoformat(),
        'reporting_timezone': 'UTC',
        'excluded_reservations': dict(excluded),
    }}
