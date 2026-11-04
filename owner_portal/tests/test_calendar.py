from datetime import date, timedelta

import pytest
from sqlalchemy import select, text

from owner_portal.access import AccessError
from owner_portal.calendar import build_calendar, calendar_view, display_name, public_calendar
from owner_portal.models import CalendarMonth, Membership, PropertyGrant, utcnow
from .test_access import accept
from .test_preview import preview_app
from .test_admin import staff_app, staff_client


def booking(start, end, **extra):
    return dict(arrival_date=date.fromisoformat(start), departure_date=date.fromisoformat(end),
                status='confirmed', guest_name='Alex Rivera', **extra)


def test_cross_month_leap_day_checkout_and_cancelled_privacy():
    rows = [booking('2028-01-30', '2028-02-02', reservation_id=1),
            booking('2028-02-02', '2028-02-03', reservation_id=2),
            booking('2028-02-29', '2028-03-04', reservation_id=3),
            booking('2028-02-10', '2028-02-11', reservation_id=4, channel_name='customIcal')]
    rows += [{**booking('2028-02-14','2028-02-16',reservation_id=5), 'status':'cancelled', 'guest_name':'Never show'},
             {**booking('2028-02-18','2028-02-20',reservation_id=6), 'status':'ownerstay', 'guest_name':'Owner private name'}]
    result=build_calendar(date(2028,2,1),date(2028,2,1),rows,[])
    assert len(result['days'])==29 and len(result['stays'])==4
    assert result['stays'][0]['start']=='2028-02-01' and result['stays'][0]['continues_before']
    assert result['stays'][-1]['end']=='2028-03-01' and result['stays'][-1]['continues_after']
    states={r['date']:r['state'] for r in result['days']}
    assert states['2028-02-02']=='booked' and states['2028-02-03']=='unknown'
    assert states['2028-02-10']=='blocked' and states['2028-02-18']=='blocked'
    assert 'Never show' not in str(result) and 'Owner private name' not in str(result)
    assert 'reservation_id' not in str(result) and 'channel' not in str(result)


@pytest.mark.parametrize('name',['test@example.invalid','Call 5551234','https://guest.invalid',None,''])
def test_contact_like_name_is_not_published(name):
    assert display_name(name)=='Reserved'


def test_projection_allowlist_and_stale_future_availability():
    month=date(2026,11,1)
    source={'days':[{'date':'2026-11-01','state':'open','phone':'SECRET'}],
            'stays':[dict(start='2026-11-02',end='2026-11-03',name='<img src=x onerror=alert(1)>',kind='reservation',email='SECRET',channel='SECRET',reservation_id=99),
                     dict(start='2026-10-31',end='2026-11-02',name='Outside grant',kind='reservation')]}
    result=public_calendar(source,month)
    assert len(result['stays'])==1 and 'SECRET' not in str(result) and 'Outside grant' not in str(result)
    assert set(result['stays'][0])=={'start','end','name','kind','continues_before','continues_after'}
    result=build_calendar(month,month,[],[dict(calendar_date=date(2026,11,5),snapshot_date=date(2026,10,20),status='available',is_available=True)])
    assert all(d['state']=='unknown' for d in result['days'])


def seed_calendar(factory, setup):
    with factory.begin() as db:
        for prop,name in [(setup['property'],'Alex Rivera'),(setup['other_property'],'PRIVATE OTHER OWNER')]:
            for month in [date(2025,11,1),date(2026,11,1),date(2027,1,1)]:
                start=month.replace(day=2);end=month.replace(day=5)
                payload=build_calendar(month,month,[{**booking(start.isoformat(),end.isoformat()),'guest_name':name}],[])
                payload['phone']='NEVER PUBLIC';payload['stays'][0]['email']='NEVER PUBLIC'
                db.add(CalendarMonth(property_id=prop,month=month,prepared_at=utcnow(),source_fresh=True,calendar=payload))


def test_calendar_scope_permission_revocation_and_date_bounds(factory,setup):
    _,user=accept(factory,setup['invite']);seed_calendar(factory,setup)
    with factory() as db:
        result=calendar_view(db,user,month=date(2026,11,1))
        assert result['available_months']==['2026-11']
        assert result['properties'][0]['stays'][0]['name']=='Alex Rivera'
        assert 'NEVER PUBLIC' not in str(result) and 'PRIVATE OTHER OWNER' not in str(result)
        for kw in [dict(account_id=setup['other_account']),dict(property_id=setup['other_property']),dict(month=date(2026,11,2))]:
            with pytest.raises(AccessError):calendar_view(db,user,**kw)
        for month in [date(2025,11,1),date(2027,1,1)]:
            row=calendar_view(db,user,month=month)['properties'][0]
            assert row['reason']=='outside_reporting_dates' and 'stays' not in row
        assert calendar_view(db,user,month=date(2026,10,1))['properties'][0]['reason']=='preparing'
    with factory.begin() as db:db.get(PropertyGrant,setup['grant']).reporting_from=date(2026,11,2)
    with factory() as db:assert calendar_view(db,user,month=date(2026,11,1))['properties'][0]['reason']=='outside_reporting_dates'
    with factory.begin() as db:db.scalar(select(Membership).where(Membership.user_id==user)).performance=False
    with factory() as db:
        with pytest.raises(AccessError):calendar_view(db,user)


def test_calendar_http_private_and_runtime_role_isolation(app,factory,setup,engine):
    token,user=accept(factory,setup['invite']);seed_calendar(factory,setup)
    client=app.test_client()
    assert client.get('/api/calendar').status_code==401
    with client.session_transaction() as s:s['owner_token']=token
    response=client.get('/api/calendar?month=2026-11')
    assert response.status_code==200 and response.headers['Cache-Control']=='private, no-store'
    assert response.json['properties'][0]['stays'][0]['name']=='Alex Rivera'
    assert client.get('/api/calendar?month=garbage').status_code==400
    with engine.connect() as c:
        assert c.scalar(text("SELECT has_table_privilege('owner_qa_web','owners.calendar_months','SELECT')"))
        assert not c.scalar(text("SELECT has_table_privilege('owner_qa_web','owners.calendar_observations','SELECT')"))
    with factory.begin() as db:
        db.get(CalendarMonth,(setup['property'],date(2026,11,1))).prepared_at=utcnow()-timedelta(days=3)
    assert client.get('/api/calendar?month=2026-11').json['properties'][0]['stale']
    with factory.begin() as db:db.get(PropertyGrant,setup['grant']).revoked_at=utcnow()
    assert client.get('/api/calendar?month=2026-11').json['properties']==[]
    assert client.get(f'/api/calendar?property={setup["property"]}').status_code==403


def test_admin_preview_matches_owner_and_person_permission(preview_app,factory,setup):
    _,user=accept(factory,setup['invite']);seed_calendar(factory,setup)
    client=staff_client(preview_app);base=f'/owner-access/preview/{setup["account"]}/'
    with factory() as db:
        expected=calendar_view(db,user,month=date(2026,11,1))
        member=db.scalar(select(Membership).where(Membership.user_id==user)).id
    assert client.get(base+'api/calendar?month=2026-11').json==expected
    assert client.get(base+f'person/{member}/api/calendar?month=2026-11').json==expected
    assert client.get(base+f'api/calendar?property={setup["other_property"]}').status_code==403
    assert client.get('/owner-access/preview/assets/calendar.js').status_code==200
    with factory.begin() as db:db.get(Membership,member).performance=False
    assert client.get(base+f'person/{member}/api/calendar').status_code==403
