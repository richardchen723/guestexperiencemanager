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
from .models import CalendarObservation, FinanceCache, FinanceReconciliation, ListingMapping, PerformanceMonth, Property, utcnow
from .reconciliation import reviewed_amount


def fingerprint(row):
    keys = ('reservation_id', 'listing_id', 'arrival_date', 'departure_date', 'updated_on', 'total_price', 'currency', 'status')
    values = [str(number(row[k]).normalize()) if k=='total_price' and number(row[k]) is not None else row[k] for k in keys]
    return hashlib.sha256(json.dumps(['finance-v2']+values, default=str).encode()).hexdigest()


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
        # Negative key isolates worker serialization from positive account-ID locks.
        if not db.scalar(text('SELECT pg_try_advisory_lock(847, -2)')):
            raise RuntimeError('An owner refresh is already running.')
        try:
            return _refresh(db, client, now)
        finally:
            db.execute(text('SELECT pg_advisory_unlock(847, -2)'))
            db.commit()


def _refresh(db, client, now):
    properties = db.scalars(select(Property).where(Property.active.is_(True))).all()
    counts = {'properties': 0, 'months': 0, 'finance_checked': 0, 'finance_unavailable': 0}
    for prop in properties:
        today = now.astimezone(ZoneInfo(prop.timezone)).date()
        end = month_shift(today, 4)
        listing_ids = list(db.scalars(select(ListingMapping.listing_id).where(ListingMapping.property_id == prop.id)))
        if not listing_ids:
            continue
        rows = [dict(r) for r in db.execute(text('''SELECT reservation_id, listing_id, channel_name,
            arrival_date, departure_date, status, total_price, currency, updated_on, reservation_date, cancellation_date
            FROM public.reservations WHERE listing_id = ANY(:ids)
            AND ((arrival_date < :end)
                OR arrival_date IS NULL OR departure_date IS NULL OR arrival_date >= departure_date)
            AND lower(status) IN ('new','modified','confirmed','ownerstay','cancelled','canceled')'''),
            {'ids': listing_ids, 'end': end}).mappings()]
        history_start = min((r['arrival_date'] for r in rows if r['arrival_date'] and r['departure_date'] and
                            r['arrival_date'] < r['departure_date']), default=today)
        start = min(month_shift(history_start, 0), month_shift(today, -26))
        reviews = {r.reservation_id: r for r in db.scalars(select(FinanceReconciliation).where(
            FinanceReconciliation.reservation_id.in_([r['reservation_id'] for r in rows])))}
        pending = []
        for row in rows:
            if row['status'].lower() not in ('new','modified','confirmed') or row['channel_name'] == 'customIcal':
                continue
            cached = db.get(FinanceCache, row['reservation_id'])
            valid = cached and cached.fingerprint == fingerprint(row)
            review = reviews.get(row['reservation_id'])
            if review and cached and review.reviewed_at > cached.checked_at:
                valid = False
            if cached and cached.reason == 'reviewed_evidence' and (
                    not review or review.revoked_at or review.fingerprint != fingerprint(row)):
                valid = False
            # Recheck older records weekly too: finance fields can change without
            # a reservation timestamp change. Failed amounts retry daily.
            age = (now-cached.checked_at).total_seconds() if cached else float('inf')
            if valid and age < (86400 if cached.accommodation is None else 7*86400):
                row['accommodation'] = cached.accommodation
                row['finance_reason'] = cached.reason
            else:
                pending.append(row)
        def hydrate(row):
            try:
                detail = client.get_reservation_detail(row['reservation_id'])
                fields = detail.get('financeField') if isinstance(detail, dict) else None
                if not isinstance(fields, list) or not fields or not all('isIncludedInTotalPrice' in f for f in fields):
                    fields = client.get_reservation_finance_fields(row['reservation_id'])
                amount, reason = validate_detail(row, detail, fields)
                # Reviewed evidence fills a source finance gap; it never
                # overrides a valid source amount or a changed identity/date.
                if amount is None and reason in ('components_do_not_reconcile', 'components_unavailable'):
                    amount = reviewed_amount(reviews.get(row['reservation_id']), fingerprint(row), detail, fields)
                    if amount is not None:
                        reason = 'reviewed_evidence'
                return row, (amount, reason)
            except Exception:
                return row, (None, 'source_temporarily_unavailable')
        with ThreadPoolExecutor(max_workers=2) as pool:
            for row, (amount, reason) in pool.map(hydrate, pending):
                row['accommodation'] = amount
                row['finance_reason'] = reason
                db.merge(FinanceCache(reservation_id=row['reservation_id'], fingerprint=fingerprint(row), accommodation=amount, reason=reason, checked_at=now))
                counts['finance_checked'] += 1
                counts['finance_unavailable'] += amount is None
        # Keep cache durable between properties, without publishing a partial property.
        db.commit()
        calendars = [dict(r) for r in db.execute(text('''SELECT listing_id,calendar_date,snapshot_date,
            status,is_available,price,minimum_stay FROM brain.calendar_snapshots
            WHERE listing_id=ANY(:ids) AND calendar_date>=:start AND calendar_date<:end
            AND snapshot_date<=:today'''), {'ids': listing_ids, 'start': start, 'end': end, 'today': today}).mappings()]
        observations = db.scalars(select(CalendarObservation).where(CalendarObservation.listing_id.in_(listing_ids),
            CalendarObservation.calendar_date >= start, CalendarObservation.calendar_date < end,
            CalendarObservation.snapshot_date <= today)).all()
        observed = {(r.listing_id, r.calendar_date, r.snapshot_date): {k:getattr(r,k) for k in (
            'listing_id','calendar_date','snapshot_date','status','is_available','price','minimum_stay')} for r in observations}
        calendars = [r for r in calendars if (r['listing_id'],r['calendar_date'],r['snapshot_date']) not in observed] + list(observed.values())
        # Backfill the historical calendar API independently from snapshot
        # history. Never relabel a retrospective read as a past observation.
        for listing_id in listing_ids:
            sanitized = []
            windows = [(today, end)]
            # The source may restrict older calendar dates. Reservation history
            # remains available independently; failures never mean zero blocks.
            for offset in range(-23, 1):
                past = max(month_shift(today, offset), month_shift(history_start, 0))
                stop = min(month_shift(past, 1), today)
                if past >= stop:
                    continue
                recent = {r['calendar_date'] for r in calendars if r['listing_id']==listing_id
                          and past <= r['calendar_date'] < stop and r['snapshot_date'] >= today-timedelta(days=7)}
                if len(recent) < (stop-past).days and (past, stop) not in windows:
                    windows.append((past, stop))
            for begin, stop in windows:
                try:
                    fresh_calendar = client.get_calendar(listing_id, begin, stop-timedelta(days=1))
                except Exception:
                    continue
                if not isinstance(fresh_calendar, list):
                    continue
                for item in fresh_calendar:
                    try:
                        day = date.fromisoformat(str(item.get('date'))[:10])
                    except (TypeError, ValueError):
                        continue
                    if not begin <= day < stop:
                        continue
                    available = item.get('isAvailable')
                    minimum = item.get('minimumStay')
                    sanitized.append({'listing_id':listing_id, 'calendar_date':day, 'snapshot_date':today,
                        'status':item.get('status') if item.get('status') in ('available','reserved','booked','blocked') else 'unknown',
                        'is_available':available in (True,1,'1') if available in (True,False,1,0,'1','0') else None,
                        'price':number(item.get('price')), 'minimum_stay':int(minimum) if str(minimum).isdigit() else None})
            for observation in sanitized:
                db.merge(CalendarObservation(**observation))
            replaced = {r['calendar_date'] for r in sanitized}
            calendars = [r for r in calendars if not (r['listing_id']==listing_id and r['snapshot_date']==today and r['calendar_date'] in replaced)] + sanitized
        syncs = list(db.execute(text('SELECT last_synced_at FROM public.listings WHERE listing_id=ANY(:ids)'), {'ids': listing_ids}).scalars())
        fresh = len(syncs) == len(listing_ids) and all(s and (now-s.replace(tzinfo=s.tzinfo or timezone.utc)).total_seconds() < 48*3600 for s in syncs)
        calendar = physical_calendar(calendars, listing_ids)
        from .care import prepare_care
        care_rows = [dict(r) for r in db.execute(text('SELECT work_id, category, status, happened_at, kind FROM owners.care_source WHERE property_id=:id'), {'id':prop.id}).mappings()]
        month = start
        while month < end:
            metrics = build_month(month, today, rows, calendar, source_fresh=fresh, history_start=history_start)
            metrics['care'] = prepare_care(care_rows, month, today, prop.timezone)
            db.merge(PerformanceMonth(property_id=prop.id, month=month, as_of=today, prepared_at=now, metrics=metrics))
            counts['months'] += 1
            month = month_shift(month, 1)
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
