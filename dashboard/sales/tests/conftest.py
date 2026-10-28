"""Never use a live database: tests require an explicitly named local QA DB."""
import os
from datetime import datetime,timezone
from uuid import uuid4
import pytest
from sqlalchemy import create_engine,text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker
from dashboard.sales.models import Base,migrate
from dashboard.sales.service import SalesService

NOW=datetime(2026,10,28,18,tzinfo=timezone.utc)
USERS={1:{'id':1,'name':'Salesperson'},2:{'id':2,'name':'Operations'}}

@pytest.fixture(scope='session')
def engine():
    url=os.environ.get('SALES_QA_DATABASE_URL')
    if not url: pytest.skip('Set SALES_QA_DATABASE_URL to a disposable local PostgreSQL database.')
    parsed=make_url(url)
    assert parsed.host in ('localhost','127.0.0.1') and parsed.database.startswith('codex_sales_qa_')
    engine=create_engine(url,pool_size=8)
    with engine.begin() as conn: migrate(conn)
    yield engine
    engine.dispose()

@pytest.fixture
def factory(engine):
    with engine.begin() as conn:
        conn.execute(text('TRUNCATE '+', '.join('sales.'+t.name for t in Base.metadata.sorted_tables)+' RESTART IDENTITY CASCADE'))
        migrate(conn)
    return sessionmaker(bind=engine,expire_on_commit=False)

@pytest.fixture
def service(factory):
    with factory() as db:
        yield SalesService(db,actor='user:1',actor_name='Salesperson',users=USERS,manager=True,now=NOW,listings={101:{'id':101,'name':'Existing listing'}})


def run(service,method,*args,**data):
    with service.db.begin_nested():
        result=service.execute(method+str(args),{'command_id':str(uuid4()),**data},lambda values:getattr(service,method)(*args,values))
    return result


def lead(service,**changes):
    values={'title':'Bennett coastal portfolio','account':{'name':'Bennett household','kind':'household'},'source_name':'Referral',
        'assigned_user_id':1,'contacts':[{'name':'Alex Bennett','email':'alex@example.invalid','phone':'+1 202 555 0100'}],
        'properties':[{'name':'Cedar House','address':'123 Fictional Lane','market':'Whidbey Island'},
                      {'name':'Harbor Cottage','address':'456 Fictional Lane','market':'Hood Canal / Belfair'}],
        'next_action':{'title':'Research owner goals','assigned_user_id':1,'due_at':'2026-10-28T16:00:00Z'}}
    values.update(changes)
    return run(service,'create',**values)


def prepared(service):
    d=lead(service)
    d=run(service,'qualify',d['id'],version=d['version'],fit='strong',decision='approved',readiness='Ready this quarter',owner_needs='Local management',operational_fit='Coverage reviewed',owner_expectations='Conservative estimates',sources=['https://example.invalid/evidence'])
    d=run(service,'permission',d['id'],version=d['version'],contact_id=d['contacts'][0]['id'],channel='email',status='allowed',basis='Owner requested proposal',source_url='https://example.invalid/consent')
    d=run(service,'add_activity',d['id'],version=d['version'],kind='email',direction='outbound',contact_id=d['contacts'][0]['id'],body='Contacted owner about goals')
    d=run(service,'patch',d['id'],version=d['version'],discovery_notes='Owner wants local full-service management')
    d=run(service,'assess',d['id'],version=d['version'],assumptions='Comparable seasonal rentals; estimate, not a guarantee',service_strategy='Full-service management',properties=[{'property_id':p['id'],'rental_revenue':'80000.00','management_revenue':'20000.00'} for p in d['properties']])
    d=run(service,'transition',d['id'],version=d['version'],stage=4)
    d=run(service,'create_proposal',d['id'],version=d['version'],document_url='https://example.invalid/proposal',terms={'currency':'USD','fee_percent':'25','fee_basis':'Rental revenue','services':'Full service','responsibilities':'Owner approves capital expenses','properties':[{'property_id':p['id'],'rental_revenue':p['rental_revenue'],'management_revenue':p['management_revenue']} for p in d['properties']]})
    return d


def sent(service):
    d=prepared(service);pid=d['proposals'][0]['id']
    d=run(service,'proposal_action',d['id'],version=d['version'],proposal_id=pid,action='approved',note='Commercial terms reviewed')
    return run(service,'proposal_action',d['id'],version=d['version'],proposal_id=pid,action='sent',note='Sent document to owner',contact_id=d['contacts'][0]['id'],channel='email')


def sign(service,d,**changes):
    data={'version':d['version'],'proposal_id':d['proposals'][0]['id'],'document_url':'https://example.invalid/signed','signed_at':'2026-10-28T17:00:00Z','effective_date':'2026-11-01','signatory_ids':[d['contacts'][0]['id']],'property_ids':[d['properties'][0]['id']],'remaining_action':'split','remaining_reason':'Second property deferred','operations_user_id':2,'commitments':'Confirm access, inventory and launch plan','target_date':'2026-11-15'}
    data.update(changes)
    return run(service,'sign',d['id'],**data)
