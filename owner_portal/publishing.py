"""Staff-only statement publication. Never imported by the owner application."""
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import func, select, text

from .access import AccessError
from .admin import OwnerAdmin, identity, record
from .calculations import number
from .models import ListingMapping, Property, PropertyGrant, PublishedStatement, StatementDocument, utcnow


def candidates(db, account_id):
    account_id = identity(account_id)
    # Read only a financial summary, not workbook contents, guest rows or notes.
    rows = db.execute(text('''SELECT r.bookkeeping_workspace_revision_id AS id, r.status,
        p.period_start, p.period_end, p.portfolio_id, portfolio.default_currency,
        r.summary_snapshot::jsonb->'summary_cards' AS summary,
        r.summary_snapshot::jsonb->>'listing_mapping_count' AS mapping_count
        FROM bookkeeping.bookkeeping_workspace_revisions r
        JOIN bookkeeping.bookkeeping_periods p ON p.bookkeeping_period_id=r.period_id
        JOIN bookkeeping.bookkeeping_portfolios portfolio ON portfolio.bookkeeping_portfolio_id=p.portfolio_id
        WHERE r.status IN ('approved','exported') ORDER BY p.period_start DESC,r.bookkeeping_workspace_revision_id DESC''')).mappings()
    result = []
    for row in rows:
        source_ids = set(db.execute(text('''SELECT listing_id FROM bookkeeping.bookkeeping_listing_mappings
            WHERE portfolio_id=:id AND is_active=true'''), {'id': row['portfolio_id']}).scalars())
        mapped = db.execute(select(Property, ListingMapping.listing_id).join(ListingMapping).where(ListingMapping.listing_id.in_(source_ids))).all()
        properties = {p.id: p for p, _ in mapped}
        reason = None
        if row['default_currency'] != 'USD':
            reason = 'Only verified USD statements can be published.'
        if not source_ids or len(mapped) != len(source_ids) or str(len(source_ids)) != str(row['mapping_count']):
            reason = 'Every home in this revision must have a verified owner property mapping.'
        period_end = row['period_end'] + timedelta(days=1)
        if row['period_end'] >= utcnow().date():
            reason = 'Only completed statement periods can be published.'
        for prop in properties.values():
            grant = db.scalar(select(PropertyGrant).where(PropertyGrant.account_id == account_id, PropertyGrant.property_id == prop.id,
                PropertyGrant.revoked_at.is_(None), PropertyGrant.reporting_from <= row['period_start'],
                (PropertyGrant.reporting_until.is_(None) | (PropertyGrant.reporting_until >= period_end))))
            if not prop.active or not grant:
                reason = 'This owner needs access to every statement home for the entire reporting period.'
        summary = row['summary'] or {}
        values = {key: number(summary.get(source)) for key, source in [('revenue', 'owner_revenue'), ('expenses', 'owner_expenses'), ('management_fee', 'management_fee'), ('net_income', 'net_income')]}
        if any(v is None for v in values.values()):
            reason = 'The approved revision is missing a complete financial summary.'
        elif abs(values['revenue']-values['expenses']-values['management_fee']-values['net_income']) > Decimal('.02'):
            reason = 'The approved summary does not reconcile.'
        if any(summary.get(k, 0) for k in ('expense_items_needing_review','revenue_items_needing_review','pending_change_proposals')):
            reason = 'Resolve the review items and approve a new revision first.'
        existing = db.scalar(select(PublishedStatement.id).where(PublishedStatement.account_id == account_id, PublishedStatement.revision_id == row['id']))
        if existing:
            reason = 'This revision has already been published to this owner.'
        result.append({'revision_id': row['id'], 'period_start': row['period_start'].isoformat(),
            'period_end_exclusive': period_end.isoformat(), 'property_ids': sorted(properties),
            'properties': [properties[k].name for k in sorted(properties)], 'reason': reason,
            **{k: float(v) if v is not None else None for k,v in values.items()}})
    return result


def publish(db, actor, data):
    account = OwnerAdmin(db, actor).account(data.get('account_id'))  # Serializes versions and grant edits.
    if data.get('scope_reviewed') is not True:
        raise AccessError('Review the approved revision and confirm that it covers exactly these homes.', 400)
    revision_id = identity(data.get('revision_id'))
    candidate = next((r for r in candidates(db, account.id) if r['revision_id'] == revision_id), None)
    if not candidate or candidate['reason']:
        raise AccessError(candidate['reason'] if candidate else 'Approved revision unavailable.', 409)
    if data.get('property_ids') != candidate['property_ids']:
        raise AccessError('The statement scope changed. Reload and review it again.', 409)
    start = date.fromisoformat(candidate['period_start'])
    version = (db.scalar(select(func.max(PublishedStatement.version)).where(PublishedStatement.account_id == account.id, PublishedStatement.period_start == start)) or 0)+1
    row = PublishedStatement(account_id=account.id, account_name=account.name, revision_id=revision_id, version=version,
        period_start=start, period_end=date.fromisoformat(candidate['period_end_exclusive']),
        property_ids=candidate['property_ids'], property_names=candidate['properties'], published_by=actor,
        **{k: candidate[k] for k in ('revenue','expenses','management_fee','net_income')})
    db.add(row)
    record(db, actor, 'statement.published', row, revision_id=revision_id, property_ids=candidate['property_ids'])
    return {'id': row.id, 'version': version}


def withdraw(db, actor, data):
    account = OwnerAdmin(db, actor).account(data.get('account_id'))
    row = db.get(PublishedStatement, identity(data.get('statement_id')))
    if not row or row.account_id != account.id:
        raise AccessError('Statement unavailable.', 404)
    row.withdrawn_at = utcnow()
    record(db, actor, 'statement.withdrawn', row)
    return {'withdrawn': True}


def upload(db, actor, data, content):
    """Explicit finalized PDF publication, independent of bookkeeping portfolios."""
    import hashlib
    from .calculations import month_shift
    account = OwnerAdmin(db,actor).account(data.get('account_id'))
    if data.get('scope_reviewed') is not True:
        raise AccessError('Confirm the final PDF, its totals, exact homes and owner-safe contents.',400)
    if not isinstance(content,bytes) or not content.startswith(b'%PDF-') or b'%%EOF' not in content[-1024:] or len(content)>5*1024*1024:
        raise AccessError('Upload a complete PDF no larger than 5 MB.',400)
    try:
        start = date.fromisoformat(str(data.get('month'))+'-01');end = month_shift(start,1)
    except (ValueError,TypeError):raise AccessError('Choose a valid statement month.',400)
    if end>utcnow().date():
        raise AccessError('Only completed statement months can be finalized.',400)
    ids=data.get('property_ids')
    if not isinstance(ids,list) or not ids or len(ids)>100 or any(type(i) is not int or i<=0 for i in ids) or len(set(ids))!=len(ids):
        raise AccessError('Select the exact homes included in this statement.',400)
    props=[]
    for prop_id in sorted(ids):
        prop=db.get(Property,prop_id)
        grant=db.scalar(select(PropertyGrant).where(PropertyGrant.account_id==account.id,PropertyGrant.property_id==prop_id,
            PropertyGrant.revoked_at.is_(None),PropertyGrant.reporting_from<=start,
            (PropertyGrant.reporting_until.is_(None)|(PropertyGrant.reporting_until>=end))))
        if not prop or not prop.active or not grant:
            raise AccessError('This owner needs reporting access to every selected home for the entire month.',409)
        props.append(prop)
    if data.get('currency')!='USD':
        raise AccessError('Statement currency must be USD.',400)
    values={k:number(data.get(k)) for k in ('revenue','expenses','management_fee','net_income')}
    if any(v is None or v!=v.quantize(Decimal('.01')) for v in values.values()):
        raise AccessError('Enter each finalized total in USD, to two decimal places.',400)
    if abs(values['revenue']-values['expenses']-values['management_fee']-values['net_income'])>Decimal('.02'):
        raise AccessError('Revenue minus expenses and management fee must equal net income.',400)
    digest=hashlib.sha256(content).hexdigest()
    duplicate=db.scalar(select(StatementDocument.statement_id).join(PublishedStatement).where(
        PublishedStatement.account_id==account.id,PublishedStatement.period_start==start,StatementDocument.sha256==digest))
    if duplicate:
        raise AccessError('This PDF has already been published to this owner for this month.',409)
    version=(db.scalar(select(func.max(PublishedStatement.version)).where(PublishedStatement.account_id==account.id,PublishedStatement.period_start==start)) or 0)+1
    row=PublishedStatement(account_id=account.id,account_name=account.name,revision_id=None,version=version,
        period_start=start,period_end=end,property_ids=[p.id for p in props],property_names=[p.name for p in props],published_by=actor,**values)
    db.add(row);db.flush();db.add(StatementDocument(statement_id=row.id,sha256=digest,content=content))
    record(db,actor,'statement.uploaded',row,property_ids=row.property_ids,sha256=digest)
    return {'id':row.id,'version':version}
