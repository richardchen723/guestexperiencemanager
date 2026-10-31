"""Privileged scheduled preparation, entirely separate from the owner web app.

Reads an allowlist of source columns and the Hostaway read API. Writes only
sanitized aggregates plus a worker-only amount cache. Never imports staff Flask.
"""
import argparse
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import os
from zoneinfo import ZoneInfo

from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

from .calculations import accommodation_amount, build_month, calendar_state, month_shift, number
from .models import FinanceCache, ListingMapping, PerformanceMonth, Property, utcnow


def fingerprint(row):
    keys = ('reservation_id', 'listing_id', 'arrival_date', 'departure_date', 'updated_on', 'total_price', 'currency', 'status')
    return hashlib.sha256(json.dumps([row[k] for k in keys], default=str).encode()).hexdigest()


def validate_detail(row, detail, fields):
    if not isinstance(detail, dict) or detail.get('id') != row['reservation_id'] or detail.get('listingMapId') != row['listing_id']:
        return None, 'source_identity_mismatch'
    if any(str(detail.get(key, ''))[:10] != str(row[col]) for key, col in [('arrivalDate', 'arrival_date'), ('departureDate', 'departure_date')]):
        return None, 'source_dates_changed'
    if detail.get('status') != row['status'] or detail.get('currency') != row['currency'] or number(detail.get('totalPrice')) != number(row['total_price']):
        return None, 'source_changed'
    return accommodation_amount(detail, fields)


def physical_calendar(rows, listing_ids):
    grouped = defaultdict(list)
    for row in rows:
        grouped[(row['calendar_date'], row['snapshot_date'])].append(row)
    result = []
    for (day, snapshot), records in grouped.items():
        opened = [r for r in records if calendar_state(r) == 'open']
        booked = [r for r in records if calendar_state(r) == 'booked']
        blocked = [r for r in records if calendar_state(r) == 'blocked']
        if booked:
            chosen = booked[0]
        elif opened:
            chosen = min(opened, key=lambda r: float(number(r['price']) or 0))
        elif len(blocked) == len(listing_ids):
            chosen = blocked[0]
        else:
            chosen = {'status': 'unknown', 'is_available': None, 'price': None, 'minimum_stay': None}
        result.append({k: chosen.get(k) for k in ('status', 'is_available', 'price', 'minimum_stay')} | {'calendar_date': day, 'snapshot_date': snapshot})
    return result


def refresh(engine, client, *, now=None):
    now = now or utcnow()
    # Hold one physical connection across commits for the session-level lock.
    with engine.connect() as connection, Session(bind=connection) as db:
        # Serialize worker runs. Web requests never acquire this lock.
        if not db.scalar(text('SELECT pg_try_advisory_lock(847, 2)')):
            raise RuntimeError('An owner refresh is already running.')
        try:
            return _refresh(db, client, now)
        finally:
            db.execute(text('SELECT pg_advisory_unlock(847, 2)'))
            db.commit()


def _refresh(db, client, now):
    properties = db.scalars(select(Property).where(Property.active.is_(True))).all()
    counts = {'properties': 0, 'months': 0, 'finance_checked': 0, 'finance_unavailable': 0}
    for prop in properties:
        today = now.astimezone(ZoneInfo(prop.timezone)).date()
        start, end = month_shift(today, -14), month_shift(today, 4)
        listing_ids = list(db.scalars(select(ListingMapping.listing_id).where(ListingMapping.property_id == prop.id)))
        if not listing_ids:
            continue
        rows = [dict(r) for r in db.execute(text('''SELECT reservation_id, listing_id, channel_name,
            arrival_date, departure_date, status, total_price, currency, updated_on
            FROM public.reservations WHERE listing_id = ANY(:ids)
            AND ((arrival_date < :end AND departure_date > :start)
                OR arrival_date IS NULL OR departure_date IS NULL OR arrival_date >= departure_date)
            AND lower(status) IN ('new','modified','confirmed','ownerstay')'''),
            {'ids': listing_ids, 'start': start, 'end': end}).mappings()]
        pending = []
        for row in rows:
            if row['status'].lower() == 'ownerstay' or row['channel_name'] == 'customIcal':
                continue
            cached = db.get(FinanceCache, row['reservation_id'])
            valid = cached and cached.fingerprint == fingerprint(row)
            # Recheck older records weekly too: finance fields can change without
            # a reservation timestamp change. Failed amounts retry daily.
            age = (now-cached.checked_at).total_seconds() if cached else float('inf')
            if valid and age < (86400 if cached.accommodation is None else 7*86400):
                row['accommodation'] = cached.accommodation
            else:
                pending.append(row)
        def hydrate(row):
            try:
                detail = client.get_reservation_detail(row['reservation_id'])
                fields = detail.get('financeField') if isinstance(detail, dict) else None
                if not isinstance(fields, list) or not fields or not all('isIncludedInTotalPrice' in f for f in fields):
                    fields = client.get_reservation_finance_fields(row['reservation_id'])
                return row, validate_detail(row, detail, fields)
            except Exception:
                return row, (None, 'source_temporarily_unavailable')
        with ThreadPoolExecutor(max_workers=2) as pool:
            for row, (amount, reason) in pool.map(hydrate, pending):
                row['accommodation'] = amount
                db.merge(FinanceCache(reservation_id=row['reservation_id'], fingerprint=fingerprint(row), accommodation=amount, reason=reason, checked_at=now))
                counts['finance_checked'] += 1
                counts['finance_unavailable'] += amount is None
        # Keep cache durable between properties, without publishing a partial property.
        db.commit()
        calendars = [dict(r) for r in db.execute(text('''SELECT listing_id,calendar_date,snapshot_date,
            status,is_available,price,minimum_stay FROM brain.calendar_snapshots
            WHERE listing_id=ANY(:ids) AND calendar_date>=:start AND calendar_date<:end
            AND snapshot_date<=:today'''), {'ids': listing_ids, 'start': start, 'end': end, 'today': today}).mappings()]
        # The staff calendar refresh covers only 60 days. Read the requested
        # owner horizon explicitly so later months do not look empty.
        for listing_id in listing_ids:
            try:
                fresh_calendar = client.get_calendar(listing_id, today, end-timedelta(days=1))
            except Exception:
                fresh_calendar = None
            if not isinstance(fresh_calendar, list):
                continue
            sanitized = []
            for item in fresh_calendar:
                try:
                    day = date.fromisoformat(str(item.get('date'))[:10])
                except (TypeError, ValueError):
                    continue
                if not today <= day < end:
                    continue
                available = item.get('isAvailable')
                minimum = item.get('minimumStay')
                sanitized.append({'listing_id':listing_id, 'calendar_date':day, 'snapshot_date':today,
                    'status':item.get('status'), 'is_available':available in (True,1,'1') if available in (True,False,1,0,'1','0') else None,
                    'price':number(item.get('price')), 'minimum_stay':int(minimum) if str(minimum).isdigit() else None})
            replaced = {r['calendar_date'] for r in sanitized}
            calendars = [r for r in calendars if not (r['listing_id']==listing_id and r['snapshot_date']==today and r['calendar_date'] in replaced)] + sanitized
        syncs = list(db.execute(text('SELECT last_synced_at FROM public.listings WHERE listing_id=ANY(:ids)'), {'ids': listing_ids}).scalars())
        fresh = len(syncs) == len(listing_ids) and all(s and (now-s.replace(tzinfo=s.tzinfo or timezone.utc)).total_seconds() < 48*3600 for s in syncs)
        calendar = physical_calendar(calendars, listing_ids)
        for offset in range(-14, 4):
            month = month_shift(today, offset)
            metrics = build_month(month, today, rows, calendar, source_fresh=fresh)
            db.merge(PerformanceMonth(property_id=prop.id, month=month, as_of=today, prepared_at=now, metrics=metrics))
            counts['months'] += 1
        db.commit()
        counts['properties'] += 1
    return counts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    # Credentials are explicit and only supplied to this offline worker.
    engine = create_engine(os.environ['OWNER_PREPARE_DATABASE_URL'], pool_pre_ping=True, hide_parameters=True)
    from .source_client import HostawayReadClient
    try:
        print(json.dumps(refresh(engine, HostawayReadClient())))
    finally:
        engine.dispose()


if __name__ == '__main__':
    main()
