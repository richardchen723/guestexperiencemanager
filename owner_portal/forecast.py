"""Reservation-backed seasonal planning ranges, never fabricated booking pace."""
from datetime import date, datetime, timedelta
from math import sqrt

from .calculations import CONFIRMED, calendar_state, days, month_shift

CANCELLED = {'cancelled', 'canceled'}


def day_value(value):
    return value.date() if isinstance(value, datetime) else value if isinstance(value, date) else None


def interval(successes, observations):
    if observations < 30:
        return None
    z, n = 1.28155, observations  # 80% Wilson sampling range, not calibrated forecast accuracy.
    p = successes / n
    center = (p + z*z/(2*n)) / (1 + z*z/n)
    width = z * sqrt(p*(1-p)/n + z*z/(4*n*n)) / (1 + z*z/n)
    return max(0, center-width), min(1, center+width)


def seasonal_pickup(reservations, history, today, target, *, history_start):
    """Same season over up to three completed years, at matched lead.

    Only final confirmed nights count as conversions; cancelled bookings do not.
    Missing booking/cancellation dates and unknown/blocked final calendar days
    are excluded. Retrospective calendars classify final outcomes only.
    """
    if not history_start:
        return None
    lead = max(1, (target-today).days)
    successes = observations = 0
    seasons = [(year, offset) for year in range(target.year-3,target.year) for offset in (-1,0,1)]
    for year,offset in seasons:
        start = month_shift(date(year, target.month, 1),offset)
        end = month_shift(start,1)
        if end > today or start < history_start.replace(day=1):
            continue
        relevant = [r for r in reservations if r['arrival_date'] and r['departure_date']
                    and r['arrival_date'] < end and r['departure_date'] > start]
        # Do not select only booked days from years whose empty days lack
        # evidence: that would bias the conversion rate upward.
        final_states = {}
        for day in days(start,end):
            records = [r for r in history.get(day,[]) if r['snapshot_date']<=today]
            if records:
                final_states[day]=calendar_state(max(records,key=lambda r:r['snapshot_date']))
        if any(final_states.get(day,'unknown')=='unknown' for day in days(start,end)):
            continue
        for day in days(start, end):
            cutoff = day-timedelta(days=lead)
            stays = [r for r in relevant if r['arrival_date'] <= day < r['departure_date']]
            if any((r.get('status') or '').lower() == 'ownerstay' or r.get('channel_name') == 'customIcal' for r in stays):
                continue
            final = [r for r in stays if (r.get('status') or '').lower() in CONFIRMED]
            state = final_states[day]
            if len(final) > 1 or not final and state != 'open':
                continue
            busy = missing = False
            for r in stays:
                created, cancelled = day_value(r.get('reservation_date')), day_value(r.get('cancellation_date'))
                status = (r.get('status') or '').lower()
                if not created or status in CANCELLED and not cancelled:
                    missing = True
                elif created <= cutoff and (status in CONFIRMED or status in CANCELLED and cancelled > cutoff):
                    busy = True
            if missing or busy:
                continue
            observations += 1
            successes += bool(final)
    return interval(successes, observations)


def cancellation_range(reservations, today, target):
    """Historical cancellations among reservations still held at matched lead.

    Adjacent seasonal months provide a bounded sample. Dates are required and
    each reservation contributes once; a missing sample is disclosed, not zero.
    """
    lead = max(0, (target-today).days)
    successes = observations = 0
    for r in reservations:
        arrival, departure = r['arrival_date'], r['departure_date']
        status = (r.get('status') or '').lower()
        if not arrival or not departure or departure >= today or arrival < today-timedelta(days=3*366):
            continue
        if status not in CONFIRMED | CANCELLED or r.get('channel_name') == 'customIcal':
            continue
        distance = abs(arrival.month-target.month)
        if min(distance, 12-distance) > 1:
            continue
        created, cancelled = day_value(r.get('reservation_date')), day_value(r.get('cancellation_date'))
        cutoff = arrival-timedelta(days=lead)
        if not created or created > cutoff or status in CANCELLED and (not cancelled or cancelled <= cutoff):
            continue
        observations += 1
        successes += status in CANCELLED
    return interval(successes, observations)
