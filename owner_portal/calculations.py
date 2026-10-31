"""Pure owner metrics. Unknown amounts never become zero; checkout is excluded."""
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from math import sqrt

CONFIRMED = {'new', 'modified', 'confirmed'}
CENT = Decimal('.01')


def number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        result = Decimal(str(value))
        return result if result.is_finite() and abs(result) < Decimal('1e11') else None
    except (InvalidOperation, ValueError, TypeError):
        return None


def money(value):
    return float(Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP))


def month_shift(value, offset):
    index = value.year * 12 + value.month - 1 + offset
    return date(index // 12, index % 12 + 1, 1)


def days(start, end):
    return (start + timedelta(days=i) for i in range((end - start).days))


def accommodation_amount(raw, components):
    """Only reconciled USD financeField components; never total minus generic tax.

    Airbnb's generic taxAmount can be zero while financeField includes taxes.
    Included discounts are ambiguous (room vs fee): withhold rather than guess.
    """
    total = number(raw.get('totalPrice'))
    if raw.get('currency') != 'USD' or total is None or total < 0:
        return None, 'unverified_usd_total'
    if not isinstance(components, list) or not components:
        return None, 'components_unavailable'
    ids, included, room = set(), [], []
    for field in components:
        if not isinstance(field, dict) or type(field.get('isDeleted')) is not int or field['isDeleted'] not in (0, 1):
            return None, 'invalid_component'
        if field['isDeleted']:
            continue
        key, flag = field.get('id'), field.get('isIncludedInTotalPrice')
        if type(key) is not int or key in ids or type(flag) is not int or flag not in (0, 1):
            return None, 'ambiguous_component'
        ids.add(key)
        if field.get('currency') not in (None, 'USD'):
            return None, 'conflicting_currency'
        if not flag:
            continue
        amount = number(field.get('total'))
        if amount is None or amount < 0 or field.get('type') not in {'accommodation', 'fee', 'tax', 'commissions'}:
            return None, 'unresolved_adjustment'
        included.append(amount)
        if field.get('type') == 'accommodation':
            room.append(amount)
    if len(room) != 1 or abs(sum(included, Decimal(0)) - total) > CENT:
        return None, 'components_do_not_reconcile'
    return room[0].quantize(CENT), None


def calendar_state(row):
    status = str(row.get('status') or '').lower()
    if status in {'reserved', 'booked'}:
        return 'booked'
    if status == 'available' and row.get('is_available') is True:
        return 'open'
    if status == 'blocked' and row.get('is_available') is False:
        return 'blocked'
    return 'unknown'


def pickup_range(history, today, lead):
    """80% Wilson range for open nights subsequently booked at similar lead.

    A transparent planning scenario, not a calibrated prediction interval.
    Source history is bounded to 90 completed stay dates, with fresh snapshots.
    """
    successes = observations = 0
    lead = max(1, lead)
    for day in days(today - timedelta(days=90), today):
        records = history.get(day, [])
        before = [r for r in records if 0 <= (day - timedelta(days=lead) - r['snapshot_date']).days <= 2]
        final = [r for r in records if 0 <= (day - r['snapshot_date']).days <= 1]
        if not before or not final:
            continue
        initial = calendar_state(max(before, key=lambda r: r['snapshot_date']))
        outcome = calendar_state(max(final, key=lambda r: r['snapshot_date']))
        if initial == 'open' and outcome in {'open', 'booked'}:
            observations += 1
            successes += outcome == 'booked'
    if observations < 30:
        return None
    z, n = 1.28155, observations
    p = successes / n
    center = (p + z*z/(2*n)) / (1 + z*z/n)
    width = z * sqrt(p*(1-p)/n + z*z/(4*n*n)) / (1 + z*z/n)
    return max(0, center-width), min(1, center+width)


def build_month(start, today, reservations, calendars, *, source_fresh=True):
    end = month_shift(start, 1)
    span = list(days(start, end))
    booked, owner, amounts = set(), set(), defaultdict(list)
    uncertain = set()
    invalid_dates = False
    # Distinct source IDs are kept distinct. Overlapping real bookings are a
    # conflict, not two sold physical nights or double revenue.
    for row in reservations:
        arrival, departure = row['arrival_date'], row['departure_date']
        if not arrival or not departure or arrival >= departure:
            invalid_dates = invalid_dates or (row.get('status') or '').lower() in CONFIRMED
            continue
        overlap = set(days(max(start, arrival), min(end, departure))) if arrival < end and departure > start else set()
        if not overlap:
            continue
        status = (row.get('status') or '').lower()
        if status == 'ownerstay' or row.get('channel_name') == 'customIcal':
            if status == 'ownerstay' or status in CONFIRMED:
                owner.update(overlap)
            continue
        if status not in CONFIRMED:
            continue
        if booked & overlap:
            uncertain.update(overlap)
        booked.update(overlap)
        amount = number(row.get('accommodation'))
        # Allocate integer cents deterministically across the entire stay.
        cents = int(amount * 100) if amount is not None else None
        length = (departure-arrival).days
        for day in overlap:
            index = (day-arrival).days
            amounts[day].append(None if cents is None else Decimal(cents//length + (index < cents % length))/100)
    history = defaultdict(list)
    for row in calendars:
        if row['snapshot_date'] <= today:
            history[row['calendar_date']].append(row)
    known, blocked, open_future, unknown, unmatched = {}, set(owner), [], set(), set()
    for day in span:
        latest = [r for r in history[day] if r['snapshot_date'] <= min(today, day)]
        record = max(latest, key=lambda r: r['snapshot_date']) if latest else None
        state = calendar_state(record) if record and (min(today, day)-record['snapshot_date']).days <= 2 else 'unknown'
        if day in booked:
            if day in owner:
                uncertain.add(day)
            known[day] = 'booked'
        elif day in owner or state == 'blocked':
            blocked.add(day)
            known[day] = 'blocked'
        elif state == 'open':
            known[day] = 'open'
            if day >= today:
                open_future.append((day, record))
        else:
            # Reserved calendar nights with no reconciled reservation cannot
            # silently produce zero revenue or fabricated guest occupancy.
            unknown.add(day)
            if state == 'booked':
                unmatched.add(day)
    stayed = [d for d in span if d < today]
    future = [d for d in span if d >= today]
    def revenue(which):
        history_start = min((r['arrival_date'] for r in reservations if r['arrival_date']), default=None)
        if invalid_dates or uncertain.intersection(which) or unmatched.intersection(which) or (which and which[-1] < today and (history_start is None or start < history_start.replace(day=1))):
            return None
        values = [v for day in which for v in amounts[day]]
        return None if any(v is None for v in values) else money(sum(values, Decimal(0)))
    stay_value, book_value = revenue(stayed), revenue(future)
    revenue_total = money(Decimal(str(stay_value))+Decimal(str(book_value))) if stay_value is not None and book_value is not None else None
    capacity = len(span) - len(blocked) if not unknown and not uncertain and not invalid_dates else None
    result = {'stayed': stay_value, 'booked': book_value, 'revenue': revenue_total,
              'nights': len(booked) if not unknown and not uncertain and not invalid_dates else None,
              'sellable': capacity, 'blocked': len(blocked), 'unknown_nights': len(unknown),
              'low': None, 'high': None, 'nights_low': None, 'nights_high': None,
              'forecast_reason': 'history_insufficient', 'source_fresh': source_fresh}
    if end <= today:
        result.update(low=revenue_total, high=revenue_total, nights_low=result['nights'], nights_high=result['nights'], forecast_reason=None)
        return result
    if revenue_total is None or capacity is None:
        result['forecast_reason'] = 'incomplete_source'
        return result
    if not source_fresh:
        result['forecast_reason'] = 'source_stale'
        return result
    extra_low = extra_high = night_low = night_high = 0.0
    available = {d for d, _ in open_future}
    for day, row in open_future:
        price = number(row.get('price'))
        # Exclude gaps too short for the current minimum stay. Boundary gaps
        # are conservative: no assumption about nights outside this month.
        run = 1
        for direction in (-1, 1):
            adjacent = day + timedelta(days=direction)
            while adjacent in available:
                run += 1
                adjacent += timedelta(days=direction)
        if run < max(1, row.get('minimum_stay') or 1):
            continue
        interval = pickup_range(history, today, (day-today).days)
        if interval is None or price is None or price <= 0:
            return result
        lo, hi = interval
        extra_low += lo * float(price)
        extra_high += hi * float(price)
        night_low += lo
        night_high += hi
    result.update(low=money(Decimal(str(revenue_total+extra_low))), high=money(Decimal(str(revenue_total+extra_high))),
                  nights_low=round(len(booked)+night_low, 1), nights_high=round(len(booked)+night_high, 1), forecast_reason=None)
    return result
