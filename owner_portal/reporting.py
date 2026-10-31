"""Owner read model: authorize every month, comparison and document first."""
from datetime import date, timedelta

from sqlalchemy import select

from .access import AccessError, authorize_property, context
from .calculations import month_shift
from .models import PerformanceMonth, PublishedStatement, utcnow

METRICS = ('stayed', 'booked', 'revenue', 'nights', 'sellable', 'blocked', 'unknown_nights',
           'low', 'high', 'nights_low', 'nights_high')
REASONS = {'history_insufficient', 'incomplete_source', 'source_stale', 'rates_unavailable', None}


def metric(db, user_id, account_id, property_id, month):
    try:
        authorize_property(db, user_id, account_id, property_id, month, month_shift(month, 1))
    except AccessError:
        return {'available': False, 'reason': 'outside_reporting_dates'}
    row = db.get(PerformanceMonth, (property_id, month))
    if not row:
        return {'available': False, 'reason': 'preparing'}
    # Never serialize arbitrary JSON or ORM fields onto the public API.
    result = {key: row.metrics.get(key) for key in METRICS}
    stale = utcnow()-row.prepared_at > timedelta(hours=48) or row.metrics.get('source_fresh') is not True
    result.update(available=True, as_of=row.as_of.isoformat(), updated_at=row.prepared_at.isoformat(), stale=stale,
                  forecast_reason=row.metrics.get('forecast_reason') if row.metrics.get('forecast_reason') in REASONS else 'incomplete_source')
    if stale and month_shift(month, 1) > row.as_of:
        result.update(low=None, high=None, nights_low=None, nights_high=None, forecast_reason='source_stale')
    return result


def total(rows):
    if not rows or not all(row['available'] for row in rows):
        return {'available': False, 'reason': 'outside_reporting_dates' if any(row.get('reason') == 'outside_reporting_dates' for row in rows) else 'preparing'}
    result = {'available': True, 'as_of': min(r['as_of'] for r in rows), 'updated_at': min(r['updated_at'] for r in rows),
              'stale': any(r['stale'] for r in rows)}
    for key in METRICS:
        values = [r.get(key) for r in rows]
        result[key] = round(sum(values), 2) if all(v is not None for v in values) else None
    result['forecast_reason'] = next((r['forecast_reason'] for r in rows if r.get('forecast_reason')), None)
    return result


def dashboard(db, user_id, *, account_id=None, property_id=None, month=None):
    ctx = context(db, user_id)
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
    today = utcnow().date()
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
                'statements': list_statements(db, user_id, account['id'], property_id) if account['permissions']['statements'] else []}
    if account['permissions']['performance']:
        for offset in range(-2, 4):
            start = month_shift(month, offset)
            previous = month_shift(start, -12)
            rows = []
            comparison = []
            for prop in homes:
                value = metric(db, user_id, account['id'], prop['id'], start)
                prior = metric(db, user_id, account['id'], prop['id'], previous)
                rows.append({'id': prop['id'], 'name': prop['name'], 'value': value, 'previous': prior})
                comparison.append(prior)
            response['months'].append({'month': start.isoformat()[:7], 'value': total([r['value'] for r in rows]),
                                       'previous': total(comparison), 'properties': rows})
    return response


def statement_authorized(db, user_id, row):
    if row.withdrawn_at or not row.property_ids:
        raise AccessError('This statement is unavailable.', 404)
    for property_id in row.property_ids:
        authorize_property(db, user_id, row.account_id, property_id, row.period_start, row.period_end, permission='statements')


def statement_view(row):
    return {'id': row.id, 'account': row.account_name, 'properties': row.property_names,
            'period_start': row.period_start.isoformat(), 'period_end_exclusive': row.period_end.isoformat(),
            'version': row.version, 'published_at': row.published_at.isoformat(), 'currency': 'USD',
            **{k: float(getattr(row, k)) for k in ('revenue', 'expenses', 'management_fee', 'net_income')}}


def list_statements(db, user_id, account_id, property_id=None):
    result = []
    for row in db.scalars(select(PublishedStatement).where(PublishedStatement.account_id == account_id,
            PublishedStatement.withdrawn_at.is_(None)).order_by(PublishedStatement.period_start.desc(), PublishedStatement.version.desc())):
        if property_id and property_id not in row.property_ids:
            continue
        try:
            statement_authorized(db, user_id, row)
        except AccessError:
            continue
        result.append(statement_view(row))
    return result


def get_statement(db, user_id, statement_id):
    row = db.get(PublishedStatement, statement_id)
    if not row:
        raise AccessError('This statement is unavailable.', 404)
    statement_authorized(db, user_id, row)
    return statement_view(row)
