from datetime import date, datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import select, text

from owner_portal.models import CalendarObservation, FinanceCache, PerformanceMonth
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
        observation=db.get(CalendarObservation,(101,date(2027,1,15),date(2026,11,1)))
        assert observation.price==Decimal('100') and observation.status=='available'
        assert 'PRIVATE GUEST' not in str(observation.__dict__)
    refresh(engine,client,now=now)
    assert client.calls==1
    with factory.begin() as db:db.execute(text("UPDATE public.reservations SET status='cancelled' WHERE reservation_id=1"))
    refresh(engine,client,now=now)
    with factory() as db:
        row=db.get(PerformanceMonth,(setup['property'],date(2026,11,1)))
        assert row.metrics['revenue']==0 and row.metrics['nights']==0
