from datetime import date
import json

import pytest
from sqlalchemy import select, text

from owner_portal.access import AccessError
from owner_portal.models import PublishedStatement
from owner_portal.publishing import candidates, publish, withdraw
from .test_admin import staff_app, staff_client


@pytest.fixture
def bookkeeping(factory):
    with factory.begin() as db:
        db.execute(text('CREATE SCHEMA IF NOT EXISTS bookkeeping'))
        for sql in [
            'CREATE TABLE bookkeeping.bookkeeping_portfolios (bookkeeping_portfolio_id int, default_currency text)',
            'CREATE TABLE bookkeeping.bookkeeping_periods (bookkeeping_period_id int, portfolio_id int, period_start date, period_end date)',
            'CREATE TABLE bookkeeping.bookkeeping_workspace_revisions (bookkeeping_workspace_revision_id int,period_id int,status text,summary_snapshot jsonb)',
            'CREATE TABLE bookkeeping.bookkeeping_listing_mappings (portfolio_id int,listing_id int,is_active boolean)',
        ]:db.execute(text(sql))
        db.execute(text("INSERT INTO bookkeeping.bookkeeping_portfolios VALUES(1,'USD')"))
        db.execute(text("INSERT INTO bookkeeping.bookkeeping_periods VALUES(1,1,'2026-09-01','2026-09-30')"))
        db.execute(text('INSERT INTO bookkeeping.bookkeeping_listing_mappings VALUES(1,101,true)'))
        snapshot={'summary_cards':{'owner_revenue':1000,'owner_expenses':100,'management_fee':200,'net_income':700},'listing_mapping_count':1,'guest_name':'PRIVATE GUEST','notes':'PRIVATE NOTES'}
        db.execute(text("INSERT INTO bookkeeping.bookkeeping_workspace_revisions VALUES(1,1,'approved',CAST(:data AS jsonb))"),{'data':json.dumps(snapshot)})
    yield
    with factory.begin() as db:
        for table in ['bookkeeping_workspace_revisions','bookkeeping_periods','bookkeeping_portfolios','bookkeeping_listing_mappings']:
            db.execute(text('DROP TABLE bookkeeping.'+table))


def test_staff_publication_freezes_summary_scope_versions_and_withdrawal(factory,setup,bookkeeping,staff_app):
    client=staff_client(staff_app)
    payload={'account_id':setup['account'],'revision_id':1,'property_ids':[setup['property']],'scope_reviewed':True}
    path='/owner-access/api/publish-statement';headers={'X-CSRF-Token':'synthetic-csrf'}
    assert client.post(path,json=payload).status_code==403
    assert client.post(path,json=payload|{'scope_reviewed':False},headers=headers).status_code==400
    result=client.post(path,json=payload,headers=headers)
    assert result.status_code==200
    sid=result.json['id']
    assert client.post(path,json=payload,headers=headers).status_code==409
    with factory.begin() as db:
        row=db.get(PublishedStatement,sid)
        assert row.property_ids==[setup['property']] and row.net_income==700 and row.period_end==date(2026,10,1)
        db.execute(text("UPDATE bookkeeping.bookkeeping_workspace_revisions SET summary_snapshot=jsonb_set(summary_snapshot,'{summary_cards,net_income}','99999')"))
    with factory() as db:assert db.get(PublishedStatement,sid).net_income==700
    assert client.post('/owner-access/api/withdraw-statement',json={'account_id':setup['account'],'statement_id':sid},headers=headers).status_code==200
    with factory() as db:assert db.get(PublishedStatement,sid).withdrawn_at is not None


def test_full_statement_scope_and_currency_are_required(factory,setup,bookkeeping):
    with factory.begin() as db:
        assert candidates(db,setup['account'])[0]['reason'] is None
        assert candidates(db,setup['other_account'])[0]['reason']
        db.execute(text("UPDATE bookkeeping.bookkeeping_portfolios SET default_currency='MYR'"))
        assert candidates(db,setup['account'])[0]['reason']
        with pytest.raises(AccessError):publish(db,'staff:1',{'account_id':setup['account'],'revision_id':1,'property_ids':[setup['property']],'scope_reviewed':True})
