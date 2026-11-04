"""Read-only calendar with a deliberately small, independently authorized DTO."""
from datetime import date, timedelta
import re
import unicodedata
from zoneinfo import ZoneInfo

from sqlalchemy import select

from .access import AccessError, OwnerReportingAccess
from .calculations import CONFIRMED, calendar_state, days, month_shift
from .models import CalendarMonth, utcnow


def display_name(value):
    """A name field is not permission to publish an embedded email or phone."""
    if not isinstance(value, str):
        return 'Reserved'
    value = ' '.join(''.join(c for c in value if not unicodedata.category(c).startswith('C')).split())
    if not value or '@' in value or re.search(r'https?://|www\.|\d{3}', value, re.I):
        return 'Reserved'
    return value[:160]


def build_calendar(month, today, reservations, calendars):
    end = month_shift(month, 1)
    latest = {}
    for row in calendars:
        day, snapshot = row['calendar_date'], row['snapshot_date']
        if month <= day < end and snapshot <= today and (day not in latest or snapshot > latest[day]['snapshot_date']):
            latest[day] = row
    states = {}
    for day in days(month, end):
        row = latest.get(day)
        # Retrospective reads can describe historical dates. Old observations
        # cannot promise that a future date is still available.
        fresh = row and (today-row['snapshot_date']).days <= 2
        states[day] = calendar_state(row) if row and (fresh or day < today) else 'unknown'
    stays = []
    guest_days = set()
    seen = set()
    for row in reservations:
        start, stop = row.get('arrival_date'), row.get('departure_date')
        status = str(row.get('status') or '').lower()
        if status not in CONFIRMED | {'ownerstay'} or not start or not stop or start >= stop or start >= end or stop <= month:
            continue
        key = row.get('reservation_id')
        if key is not None and key in seen:
            continue
        seen.add(key)
        begin, finish = max(start, month), min(stop, end)
        if row.get('channel_name') == 'customIcal':
            # iCal titles can contain internal block notes, not guest names.
            for day in days(begin, finish):
                if states[day] != 'booked':
                    states[day] = 'blocked'
            continue
        owner = status == 'ownerstay'
        stays.append({'start': begin.isoformat(), 'end': finish.isoformat(),
                      'continues_before': start < month, 'continues_after': stop > end,
                      'name': 'Owner stay' if owner else display_name(row.get('guest_name')),
                      'kind': 'owner' if owner else 'reservation'})
        for day in days(begin, finish):
            if not owner:
                guest_days.add(day)
            states[day] = 'booked' if day in guest_days else 'blocked'
    return {'days': [{'date': day.isoformat(), 'state': state} for day, state in states.items()],
            'stays': sorted(stays, key=lambda s: (s['start'], s['end'], s['name']))}


def public_calendar(payload, month):
    """Rebuild an allowlist even if a future worker writes additional fields."""
    end = month_shift(month, 1)
    source = payload if isinstance(payload, dict) else {}
    states = {r.get('date'): r.get('state') for r in source.get('days', []) if isinstance(r, dict)}
    result = {'days': [{'date': day.isoformat(), 'state': states.get(day.isoformat())
                       if states.get(day.isoformat()) in {'open', 'booked', 'blocked'} else 'unknown'}
                      for day in days(month, end)], 'stays': []}
    for row in source.get('stays', []):
        if not isinstance(row, dict) or row.get('kind') not in {'reservation', 'owner'}:
            continue
        try:
            start, stop = date.fromisoformat(row['start']), date.fromisoformat(row['end'])
        except (KeyError, ValueError, TypeError):
            continue
        if not month <= start < stop <= end:
            continue
        result['stays'].append({'start': start.isoformat(), 'end': stop.isoformat(),
            'name': 'Owner stay' if row['kind'] == 'owner' else display_name(row.get('name')),
            'kind': row['kind'], 'continues_before': row.get('continues_before') is True,
            'continues_after': row.get('continues_after') is True})
    return result


def calendar_view(db, user_id, *, account_id=None, property_id=None, month=None, access=None):
    access = access or OwnerReportingAccess(db, user_id)
    accounts = access.context()['accounts']
    account = next((a for a in accounts if a['id'] == account_id), None) if account_id else next(iter(accounts), None)
    if not account or not account['permissions']['performance']:
        raise AccessError()
    homes = [p for p in account['properties'] if property_id is None or p['id'] == property_id]
    if property_id and not homes:
        raise AccessError()
    today = utcnow().astimezone(ZoneInfo(homes[0]['timezone'])).date() if homes else utcnow().date()
    month = month or month_shift(today, 0)
    if month.day != 1 or abs((month.year-today.year)*12+month.month-today.month) > 120:
        raise AccessError('Choose a valid calendar month.', 400)
    available, properties = set(), []
    for prop in homes:
        for value in db.scalars(select(CalendarMonth.month).where(CalendarMonth.property_id == prop['id'])):
            if value.isoformat() >= prop['reporting_from'] and (not prop['reporting_until_exclusive'] or month_shift(value, 1).isoformat() <= prop['reporting_until_exclusive']):
                available.add(value.isoformat()[:7])
        item = {'id': prop['id'], 'name': prop['name'], 'timezone': prop['timezone']}
        properties.append(item)
        try:
            access.authorize(account['id'], prop['id'], month, month_shift(month, 1))
        except AccessError:
            item.update(available=False, reason='outside_reporting_dates')
            continue
        row = db.get(CalendarMonth, (prop['id'], month))
        if row is None:
            item.update(available=False, reason='preparing')
            continue
        item.update(available=True, updated_at=row.prepared_at.isoformat(),
                    stale=not row.source_fresh or utcnow()-row.prepared_at > timedelta(hours=48),
                    **public_calendar(row.calendar, month))
    return {'account_id': account['id'], 'property_id': property_id, 'month': month.isoformat()[:7],
            'today': today.isoformat(), 'properties': properties, 'available_months': sorted(available)}
