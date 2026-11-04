from datetime import date, datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import select, text

from owner_portal.models import CalendarMonth, CalendarObservation, FinanceCache, PerformanceMonth
from owner_portal.prepare import refresh


@pytest.fixture
def sources(factory,setup):
    with factory.begin() as db:
        db.execute(text('CREATE SCHEMA IF NOT EXISTS brain'))
        db.execute(text('CREATE TABLE public.listings (listing_id int,last_synced_at timestamp)'))
        db.execute(text('CREATE TABLE public.reservations (reservation_id int,listing_id int,channel_name text,arrival_date date,departure_date date,status text,total_price numeric,currency text,updated_on timestamp,guest_name text,reservation_date timestamp,cancellation_date timestamp)'))
        db.execute(text('CREATE VIEW owners.care_source AS SELECT 1::int property_id, 1::int work_id, null::text category, null::text status, now() happened_at, null::text kind WHERE false'))
        db.execute(text('CREATE TABLE brain.calendar_snapshots (listing_id int,calendar_date date,snapshot_date date,status text,is_available boolean,price numeric,minimum_stay int,raw_payload jsonb)'))
        db.execute(text("INSERT INTO public.listings VALUES(101,'2026-11-01'),(202,'2026-11-01')"))
        db.execute(text("INSERT INTO public.reservations VALUES(1,101,'direct','2026-11-01','2026-11-03','new',234,'USD','2026-11-01','PRIVATE GUEST','2026-10-01',null)"))
    yield
    with factory.begin() as db:
        db.execute(text('DROP VIEW owners.care_source'))
        db.execute(text('DROP TABLE public.reservations,public.listings,brain.calendar_snapshots'))


def test_refresh_end_to_end_sanitizes_caches_and_invalidates_changed_bookings(engine,factory,setup,sources):
    class Client:
        calls=0
        def get_reservation_detail(self,id):
            self.calls+=1
            with factory.begin() as db:
                assert db.scalar(text('SELECT pg_try_advisory_xact_lock(847, :account)'), {'account':setup['other_account']})
            return {'id':1,'listingMapId':101,'arrivalDate':'2026-11-01','departureDate':'2026-11-03','status':'new','currency':'USD','totalPrice':234,'guestName':'PRIVATE GUEST','financeField':[{'id':1,'isDeleted':0,'isIncludedInTotalPrice':1,'type':'accommodation','total':200},{'id':2,'isDeleted':0,'isIncludedInTotalPrice':1,'type':'tax','total':34}]}
        def get_calendar(self,id,start,end):
            from owner_portal.calculations import days
            return [{'date':d.isoformat(),'isAvailable':1,'status':'available','price':100,'minimumStay':1,'guestName':'PRIVATE GUEST'} for d in days(start,end)]
    client=Client();now=datetime(2026,11,1,12,tzinfo=timezone.utc)
    result=refresh(engine,client,now=now)
    assert result['finance_checked']==1
    with factory() as db:
        row=db.get(PerformanceMonth,(setup['property'],date(2026,11,1)))
        assert row.metrics['revenue']==200 and 'PRIVATE GUEST' not in str(row.metrics)
        assert db.get(FinanceCache,1).accommodation==Decimal('200')
        cal=db.get(CalendarMonth,(setup['property'],date(2026,11,1))).calendar
        assert cal['stays'][0]['name']=='PRIVATE GUEST'
        assert 'channel' not in str(cal) and 'reservation_id' not in str(cal)
        assert db.get(CalendarMonth,(setup['property'],date(2027,11,1))) is not None
        observation=db.get(CalendarObservation,(101,date(2027,1,15),date(2026,11,1)))
        assert observation.price==Decimal('100') and observation.status=='available'
        assert 'PRIVATE GUEST' not in str(observation.__dict__)
    refresh(engine,client,now=now)
    assert client.calls==1
    with factory.begin() as db:
        db.execute(text("UPDATE public.reservations SET arrival_date='2026-12-02',departure_date='2026-12-04',guest_name='Jamie Morgan' WHERE reservation_id=1"))
    refresh(engine,client,now=now)
    with factory() as db:
        assert db.get(CalendarMonth,(setup['property'],date(2026,11,1))).calendar['stays']==[]
        assert db.get(CalendarMonth,(setup['property'],date(2026,12,1))).calendar['stays'][0]['name']=='Jamie Morgan'
    with factory.begin() as db:db.execute(text("UPDATE public.reservations SET status='cancelled' WHERE reservation_id=1"))
    refresh(engine,client,now=now)
    with factory() as db:
        row=db.get(PerformanceMonth,(setup['property'],date(2026,11,1)))
        assert row.metrics['revenue']==0 and row.metrics['nights']==0
        assert db.get(CalendarMonth,(setup['property'],date(2026,12,1))).calendar['stays']==[]


def test_worker_role_reads_name_only_and_publishes_calendar(engine,factory,setup,sources):
    from sqlalchemy import create_engine
    from owner_portal.cli import prepare_sql
    with engine.begin() as c:
        c.execute(text('ALTER TABLE public.reservations ADD COLUMN guest_email text'))
        if not c.scalar(text("SELECT 1 FROM pg_roles WHERE rolname='owner_qa_prepare'")):
            c.execute(text('CREATE ROLE owner_qa_prepare LOGIN'))
        c.execute(text(prepare_sql('owner_qa_prepare')))
        assert c.scalar(text("SELECT has_column_privilege('owner_qa_prepare','public.reservations','guest_name','SELECT')"))
        assert not c.scalar(text("SELECT has_column_privilege('owner_qa_prepare','public.reservations','guest_email','SELECT')"))
        assert not c.scalar(text("SELECT has_column_privilege('owner_qa_web','public.reservations','guest_name','SELECT')"))
    worker=create_engine(engine.url.set(username='owner_qa_prepare'))
    class Client:
        def get_reservation_detail(self,id):return None
        def get_reservation_finance_fields(self,id):return None
        def get_calendar(self,*args):return []
    try:
        assert refresh(worker,Client(),now=datetime(2026,11,1,12,tzinfo=timezone.utc))['calendar_months']>0
    finally:worker.dispose()
    with factory() as db:
        assert db.get(CalendarMonth,(setup['property'],date(2026,11,1))).calendar['stays'][0]['name']=='PRIVATE GUEST'
