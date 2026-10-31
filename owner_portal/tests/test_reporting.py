from datetime import date, timedelta
import json

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import ProgrammingError

from owner_portal.access import AccessError
from owner_portal.calculations import month_shift
from owner_portal.models import Membership, PerformanceMonth, PropertyGrant, PublishedStatement, utcnow
from owner_portal.reporting import dashboard, get_statement
from .test_access import accept


def seed(factory,setup):
    now=utcnow()
    metric={'stayed':600,'booked':300,'revenue':900,'low':1000,'high':1200,'nights':9,'sellable':30,'blocked':0,'unknown_nights':0,'nights_low':10,'nights_high':12,'source_fresh':True,'forecast_reason':None,'guest_name':'NEVER PUBLIC','source_ids':[123]}
    with factory.begin() as db:
        for year in (2025,2026):
            db.add(PerformanceMonth(property_id=setup['property'],month=date(year,11,1),as_of=date(2026,11,1),prepared_at=now,metrics=metric))
        db.add(PerformanceMonth(property_id=setup['other_property'],month=date(2026,11,1),as_of=date(2026,11,1),prepared_at=now,metrics=metric|{'revenue':999999}))
        row=PublishedStatement(account_id=setup['account'],account_name='Bennett household',revision_id=1,version=1,period_start=date(2026,9,1),period_end=date(2026,10,1),property_ids=[setup['property']],property_names=['Cedar House'],revenue=1000,expenses=100,management_fee=200,net_income=700,published_by='staff:1')
        db.add(row);db.flush();return row.id


def test_dashboard_comparisons_totals_and_downloads_respect_permissions(factory,setup,app):
    token,user=accept(factory,setup['invite']);sid=seed(factory,setup)
    client=app.test_client()
    with client.session_transaction() as session:session['owner_token']=token
    data=client.get('/api/dashboard?month=2026-11').json
    selected=next(m for m in data['months'] if m['month']=='2026-11')
    assert selected['value']['revenue']==900
    assert selected['previous']=={'available':False,'reason':'outside_reporting_dates'}
    assert 'NEVER PUBLIC' not in json.dumps(data) and '999999' not in json.dumps(data) and 'source_ids' not in json.dumps(data)
    assert data['statements']==[]
    assert client.get(f'/statements/{sid}').status_code==403
    assert client.get(f'/api/dashboard?account={setup["other_account"]}').status_code==403
    assert client.get(f'/api/dashboard?property={setup["other_property"]}').status_code==403
    assert client.get('/api/dashboard?month=2026-19').status_code==400
    with factory.begin() as db:
        db.get(PropertyGrant,setup['grant']).reporting_from=date(2025,1,1)
        db.scalar(select(Membership).where(Membership.user_id==user)).statements=True
    selected=next(m for m in client.get('/api/dashboard?month=2026-11').json['months'] if m['month']=='2026-11')
    assert selected['previous']['revenue']==900
    assert client.get(f'/statements/{sid}?download=1').status_code==200
    with factory.begin() as db:db.get(PropertyGrant,setup['grant']).revoked_at=utcnow()
    assert client.get(f'/statements/{sid}').status_code==403
    assert client.get('/api/dashboard?month=2026-11').json['statements']==[]


def test_statement_entire_frozen_scope_and_withdrawal(factory,setup):
    _,user=accept(factory,setup['invite']);sid=seed(factory,setup)
    with factory.begin() as db:
        db.scalar(select(Membership).where(Membership.user_id==user)).statements=True
        row=db.get(PublishedStatement,sid);row.property_ids=[setup['property'],setup['other_property']]
    with factory() as db:
        with pytest.raises(AccessError):get_statement(db,user,sid)
    with factory.begin() as db:
        row=db.get(PublishedStatement,sid);row.property_ids=[setup['property']];row.withdrawn_at=utcnow()
    with factory() as db:
        with pytest.raises(AccessError):get_statement(db,user,sid)


def test_stale_data_has_no_forecast_and_web_role_cannot_write_or_read_cache(factory,setup,app):
    token,user=accept(factory,setup['invite']);seed(factory,setup)
    with factory.begin() as db:db.get(PerformanceMonth,(setup['property'],date(2026,11,1))).prepared_at=utcnow()-timedelta(days=3)
    with app.extensions['owner_db']() as db:
        selected=next(m for m in dashboard(db,user,month=date(2026,11,1))['months'] if m['month']=='2026-11')
        assert selected['value']['low'] is None and selected['value']['stale']
        with pytest.raises(ProgrammingError):db.execute(text('SELECT * FROM owners.finance_cache'))
        db.rollback()
        with pytest.raises(ProgrammingError):db.execute(text('UPDATE owners.performance_months SET metrics=\'{}\''))


def test_partial_month_is_not_clipped(factory,setup):
    _,user=accept(factory,setup['invite']);seed(factory,setup)
    with factory.begin() as db:db.get(PropertyGrant,setup['grant']).reporting_from=date(2026,11,15)
    with factory() as db:
        row=next(m for m in dashboard(db,user,month=date(2026,11,1))['months'] if m['month']=='2026-11')
        assert not row['value']['available']
