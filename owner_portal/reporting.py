"""Owner read model: authorize every month, comparison and document first."""
from datetime import date, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select

from .access import AccessError, OwnerReportingAccess
from .calculations import month_shift
from .models import PerformanceMonth, PublishedStatement, StatementDocument, utcnow

METRICS = ('stayed', 'booked', 'revenue', 'nights', 'sellable', 'blocked', 'unknown_nights',
           'low', 'high', 'nights_low', 'nights_high', 'stayed_known', 'booked_known', 'revenue_known',
           'calendar_nights', 'missing_revenue_nights', 'reservation_count', 'unallocated_adjustments',
           'reconstructed_nights', 'extra_low', 'extra_high', 'cancellation_low', 'cancellation_high', 'low_known', 'high_known', 'nights_known')
REASONS = {'history_insufficient', 'incomplete_source', 'source_stale', 'rates_unavailable', None}


def metric(db, user_id, account_id, property_id, month, *, access=None):
    access = access or OwnerReportingAccess(db, user_id)
    try:
        access.authorize(account_id, property_id, month, month_shift(month, 1))
    except AccessError:
        return {'available': False, 'reason': 'outside_reporting_dates'}
    row = db.get(PerformanceMonth, (property_id, month))
    if not row:
        return {'available': False, 'reason': 'preparing'}
    # Never serialize arbitrary JSON or ORM fields onto the public API.
    result = {key: row.metrics.get(key) for key in METRICS}
    from .care import public_care
    result['care'] = public_care(row.metrics.get('care'))
    result['forecast_method'] = row.metrics.get('forecast_method') if row.metrics.get('forecast_method') in ('seasonal_pickup','recent_pickup') else None
    for key in ('stayed','booked','revenue','low','high'):
        if result[key+'_known'] is None:
            result[key+'_known'] = result[key]
    stale = utcnow()-row.prepared_at > timedelta(hours=48) or row.metrics.get('source_fresh') is not True
    result.update(available=True, as_of=row.as_of.isoformat(), updated_at=row.prepared_at.isoformat(), stale=stale,
                  forecast_reason=row.metrics.get('forecast_reason') if row.metrics.get('forecast_reason') in REASONS else 'incomplete_source')
    if stale and month_shift(month, 1) > row.as_of:
        result.update(low=None, high=None, low_known=None, high_known=None, nights_low=None, nights_high=None, extra_low=None, extra_high=None,
                      cancellation_low=None, cancellation_high=None, forecast_reason='source_stale')
    return result


def total(rows):
    authorized = [r for r in rows if r['available']]
    if not authorized:
        return {'available': False, 'reason': 'outside_reporting_dates' if any(row.get('reason') == 'outside_reporting_dates' for row in rows) else 'preparing'}
    result = {'available': True, 'as_of': min(r['as_of'] for r in authorized), 'updated_at': min(r['updated_at'] for r in authorized),
              'stale': any(r['stale'] for r in authorized), 'property_count':len(rows),
              'revenue_coverage':sum(r.get('revenue') is not None for r in authorized),
              'forecast_coverage':sum(r.get('low') is not None for r in authorized),
              'available_properties':len(authorized)}
    for key in METRICS:
        values = [r.get(key) for r in authorized]
        if key.endswith('_known') or key in ('missing_revenue_nights','unallocated_adjustments','reconstructed_nights','reservation_count'):
            result[key] = round(sum(v for v in values if v is not None),2) if any(v is not None for v in values) else None
        else:
            result[key] = round(sum(values), 2) if len(authorized)==len(rows) and all(v is not None for v in values) else None
    result['forecast_reason'] = next((r['forecast_reason'] for r in authorized if r.get('forecast_reason')), None)
    result['forecast_method'] = 'seasonal_pickup' if any(r.get('forecast_method')=='seasonal_pickup' for r in authorized) else 'recent_pickup'
    # Preserve confirmed charges for every covered home, plus the forecast
    # additions for homes with a range. The UI explicitly labels its coverage.
    if result['forecast_coverage'] and all(r.get('revenue_known') is not None for r in authorized):
        for key in ('low','high'):
            result[key+'_known'] = round(sum(r[key] if r.get(key) is not None else r.get(key+'_known') if r.get(key+'_known') is not None else r['revenue_known'] for r in authorized),2)
    result['care'] = {'available':all(r.get('care',{}).get('available') for r in authorized),
        'completed':sum(r.get('care',{}).get('completed',0) for r in authorized),
        'open':sum(r.get('care',{}).get('open',0) for r in authorized)}
    return result


def dashboard(db, user_id, *, account_id=None, property_id=None, month=None, access=None):
    access = access or OwnerReportingAccess(db, user_id)
    ctx = access.context()
    accounts = ctx['accounts']
    account = next((a for a in accounts if a['id'] == account_id), None) if account_id else next(iter(accounts), None)
    if not account:
        if account_id:
            raise AccessError()
        return {'accounts': [], 'account': None, 'months': [], 'statements': [], 'properties': [], 'currency': 'USD'}
    homes = account['properties']
    if property_id:
        homes = [p for p in homes if p['id'] == property_id]
        if not homes:
            raise AccessError()
    today = utcnow().astimezone(ZoneInfo(homes[0]['timezone'])).date() if homes else utcnow().date()
    if month is None:
        # Default to the first month common to all selected grants, without
        # silently clipping a requested month or changing any entitlement.
        starts = [date.fromisoformat(p['reporting_from']) for p in homes]
        first = max([month_shift(today, 0)] + starts)
        month = first if first.day == 1 else month_shift(first, 1)
    if month.day != 1 or abs((month.year-today.year)*12+month.month-today.month) > 120:
        raise AccessError('Choose a valid reporting month.', 400)
    response = {'accounts': accounts, 'account': account, 'properties': homes, 'property_id': property_id,
                'month': month.isoformat()[:7], 'currency': 'USD', 'months': [],
                'statements': list_statements(db, user_id, account['id'], property_id, access=access) if account['permissions']['statements'] else []}
    if account['permissions']['performance']:
        from .requests import owner_list
        response['requests'] = owner_list(db,user_id,account['id'],[p['id'] for p in homes], access=access)
        available_months = set()
        for prop in homes:
            for value in db.scalars(select(PerformanceMonth.month).where(PerformanceMonth.property_id==prop['id']).order_by(PerformanceMonth.month)):
                if value.isoformat() >= prop['reporting_from'] and (not prop['reporting_until_exclusive'] or month_shift(value,1).isoformat() <= prop['reporting_until_exclusive']):
                    available_months.add(value)
        response['available_months'] = [v.isoformat()[:7] for v in sorted(available_months)]
        first = month_shift(month,-2)
        if available_months and min(available_months)<=month<=max(available_months):
            first = max(min(available_months), min(first,month_shift(max(available_months),-5)))
        for offset in range(6):
            start = month_shift(first, offset)
            previous = month_shift(start, -12)
            rows = []
            comparison = []
            for prop in homes:
                value = metric(db, user_id, account['id'], prop['id'], start, access=access)
                prior = metric(db, user_id, account['id'], prop['id'], previous, access=access)
                rows.append({'id': prop['id'], 'name': prop['name'], 'value': value, 'previous': prior})
                comparison.append(prior)
            matched = [r for r in rows if r['value'].get('revenue') is not None and r['previous'].get('revenue') is not None]
            response['months'].append({'month': start.isoformat()[:7], 'value': total([r['value'] for r in rows]),
                'previous': total(comparison), 'properties': rows,
                'comparison': {'homes':len(matched), 'total':len(homes), 'value':total([r['value'] for r in matched]),
                               'previous':total([r['previous'] for r in matched])}})
    return response


def statement_authorized(db, user_id, row, *, access=None):
    access = access or OwnerReportingAccess(db, user_id)
    if row.withdrawn_at or not row.property_ids:
        raise AccessError('This statement is unavailable.', 404)
    for property_id in row.property_ids:
        access.authorize(row.account_id, property_id, row.period_start, row.period_end, permission='statements')


def statement_view(row):
    return {'id': row.id, 'account': row.account_name, 'properties': row.property_names,
            'period_start': row.period_start.isoformat(), 'period_end_exclusive': row.period_end.isoformat(),
            'version': row.version, 'published_at': row.published_at.isoformat(), 'currency': 'USD',
            **{k: float(getattr(row, k)) for k in ('revenue', 'expenses', 'management_fee', 'net_income')}}


def list_statements(db, user_id, account_id, property_id=None, *, access=None):
    result = []
    for row in db.scalars(select(PublishedStatement).where(PublishedStatement.account_id == account_id,
            PublishedStatement.withdrawn_at.is_(None)).order_by(PublishedStatement.period_start.desc(), PublishedStatement.version.desc())):
        if property_id and property_id not in row.property_ids:
            continue
        try:
            statement_authorized(db, user_id, row, access=access)
        except AccessError:
            continue
        result.append(statement_view(row)|{'has_pdf':db.scalar(select(StatementDocument.statement_id).where(StatementDocument.statement_id==row.id)) is not None})
    return result


def get_statement(db, user_id, statement_id, *, access=None):
    row = db.get(PublishedStatement, statement_id)
    if not row:
        raise AccessError('This statement is unavailable.', 404)
    statement_authorized(db, user_id, row, access=access)
    return statement_view(row)|{'has_pdf':db.scalar(select(StatementDocument.statement_id).where(StatementDocument.statement_id==row.id)) is not None}
