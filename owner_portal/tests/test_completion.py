from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import json

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import ProgrammingError

from owner_portal.access import AccessError
from owner_portal.calculations import accommodation_amount, build_month, days, month_shift
from owner_portal.care import prepare_care, public_care
from owner_portal.forecast import seasonal_pickup, cancellation_range
from owner_portal.models import Membership, OwnerRequest, PerformanceMonth, PropertyGrant, utcnow
from owner_portal.reporting import total, dashboard
from owner_portal import requests
from .test_calculations import reservation, calendar
from .test_access import accept
from .test_admin import staff_app, staff_client


def test_optional_zero_fields_room_discounts_and_refunds_have_distinct_meanings():
    values=[('baseRate','accommodation',1000),('pricePerExtraPerson','accommodation',100),('couponDiscount','discount',-100),('cleaningFee','fee',200),('salesTax','tax',100),('refundSum','other',0)]
    fields=[dict(id=i,name=n,type=t,total=v,isDeleted=0,isIncludedInTotalPrice=1) for i,(n,t,v) in enumerate(values)]
    assert accommodation_amount({'currency':'USD','totalPrice':1300},fields)==(Decimal('1000'),None)
    fields[-1]['total']=-50
    assert accommodation_amount({'currency':'USD','totalPrice':1250},fields)==(Decimal('1000'),'unallocated_adjustments')
    fields[-1]['name']='unknownAdjustment'
    assert accommodation_amount({'currency':'USD','totalPrice':1250},fields)[0] is None
    fields[-1]['total']=0;fields[2]['isIncludedInTotalPrice']=0
    assert accommodation_amount({'currency':'USD','totalPrice':1400},fields)==(Decimal('1100'),None)
    assert accommodation_amount({'currency':'USD','totalPrice':1401},fields)[0] is None
    fields.append(dict(id=99,name='hostChannelFee',type='commissions',total=140,isDeleted=0,isIncludedInTotalPrice=1))
    assert accommodation_amount({'currency':'USD','totalPrice':1260},fields)==(Decimal('1100'),None)
    fields[-1]['isIncludedInTotalPrice']=0
    assert accommodation_amount({'currency':'USD','totalPrice':1260},fields)==(Decimal('1100'),None)


def test_historical_calendar_backfill_does_not_erase_known_nights_or_invent_pace():
    start=date(2025,10,1);end=date(2025,11,1);today=date(2026,10,31)
    rows=[reservation(start,start+timedelta(days=10),1000)]
    old=calendar(start,end,today)
    for r in old:r['snapshot_date']=today
    old[-1].update(status='blocked',is_available=False)
    data=build_month(start,today,rows,old)
    assert data['revenue']==1000 and data['nights']==10 and data['sellable']==30
    assert data['reconstructed_nights']==31
    missing=build_month(start,today,rows,[])
    assert missing['nights']==10 and missing['calendar_nights']==31 and missing['sellable'] is None


def test_partial_revenue_keeps_verified_subtotal_and_never_invents_full_total():
    start=date(2026,10,1);end=date(2026,11,1)
    result=build_month(start,end,[reservation(start,start+timedelta(days=2),100),reservation(start+timedelta(days=2),start+timedelta(days=4),None)],calendar(start,end,end))
    assert result['revenue'] is None and result['revenue_known']==100 and result['missing_revenue_nights']==2
    result.update(available=True,as_of='2026-11-01',updated_at='2026-11-01',stale=False)
    other=result|{'revenue':200,'revenue_known':200,'stayed':200,'stayed_known':200,'missing_revenue_nights':0}
    combined=total([result,other])
    assert combined['revenue'] is None and combined['revenue_known']==300 and combined['revenue_coverage']==1 and combined['property_count']==2
    assert total([other,{'available':False,'reason':'outside_reporting_dates'}])['revenue_known']==200


def test_seasonal_pickup_uses_matched_lead_and_final_outcomes_not_future_snapshots():
    today=date(2026,10,1);target=date(2026,11,15);history={};rows=[]
    for year in (2024,2025):
        start=date(year,11,1)
        for i,day in enumerate(days(start,month_shift(start,1))):
            history[day]=[dict(snapshot_date=today,status='available',is_available=True)]
            if i%2:
                rows.append(reservation(day,day+timedelta(days=1),100,reservation_date=datetime.combine(day-timedelta(days=10),datetime.min.time())))
    lo,hi=seasonal_pickup(rows,history,today,target,history_start=date(2024,1,1))
    assert .3<lo<.5<hi<.7
    for r in rows:r['reservation_date']=r['reservation_date']-timedelta(days=200)
    lo,hi=seasonal_pickup(rows,history,today,target,history_start=date(2024,1,1))
    assert lo==0 and hi<.1


def test_cancellation_allowance_requires_real_dates_and_enough_history():
    today=date(2026,10,1);target=date(2026,11,1);rows=[]
    for i in range(60):
        arrival=date(2025,11,1)+timedelta(days=i%30)
        r=reservation(arrival,arrival+timedelta(days=1),100,reservation_date=arrival-timedelta(days=90),cancellation_date=None)
        if i%4==0:r.update(status='cancelled',cancellation_date=arrival-timedelta(days=10))
        rows.append(r)
    lo,hi=cancellation_range(rows,today,target)
    assert .1<lo<.25<hi<.4
    assert cancellation_range(rows[:10],today,target) is None


def test_missing_past_finance_does_not_remove_occupancy_forecast_or_cancel_in_house_stays():
    start=date(2026,10,1);today=date(2026,10,31);end=date(2026,11,1)
    result=build_month(start,today,[reservation(start,end,None)],calendar(start,end,today))
    assert result['revenue'] is None and result['low'] is None
    assert result['low_known']==0 and result['nights_low']==31 and result['nights_high']==31
    assert result['cancellation_high']==0


def test_care_requires_completion_events_and_only_emits_fixed_labels():
    rows=[dict(work_id=1,category='maintenance',status='Resolved',happened_at=datetime(2026,10,4),kind='current',title='PRIVATE'),
          dict(work_id=2,category='cleaning',status='Resolved',happened_at=datetime(2026,10,5),kind='transition'),
          dict(work_id=2,category='cleaning',status='Closed',happened_at=datetime(2026,10,6),kind='transition'),
          dict(work_id=3,category='maintenance',status='In Progress',happened_at=datetime(2026,10,7),kind='current')]
    result=public_care(prepare_care(rows,date(2026,10,1),date(2026,10,31),'America/Chicago'))
    assert result['completed']==1 and result['open']==1 and 'PRIVATE' not in json.dumps(result)
    assert 'work_id' not in json.dumps(result)


def test_requests_are_recipient_scoped_csrf_protected_and_response_only(factory,setup,app,staff_app):
    token,user=accept(factory,setup['invite'])
    with factory.begin() as db:
        g=db.get(PropertyGrant,setup['grant']);g.reporting_from=date(2020,1,1);g.reporting_until=None
    staff=staff_client(staff_app);payload={'account_id':setup['account'],'property_id':setup['property'],'title':'Replace umbrella','summary':'Replace worn patio umbrella.','amount':'125.50','owner_safe_reviewed':True}
    headers={'X-CSRF-Token':'synthetic-csrf'}
    assert staff.post('/owner-access/api/publish-request',json=payload).status_code==403
    bad=staff.post('/owner-access/api/publish-request',json=payload|{'property_id':setup['other_property']},headers=headers)
    assert bad.status_code==409
    saved=staff.post('/owner-access/api/publish-request',json=payload,headers=headers);assert saved.status_code==200
    rid=saved.json['id'];client=app.test_client()
    with client.session_transaction() as session:session.update(owner_token=token,csrf='request-csrf')
    assert client.get('/api/dashboard').json['requests'][0]['amount']==125.5
    path=f'/api/requests/{rid}/respond'
    assert client.post(path,json={'response':'approved'}).status_code==403
    assert client.post(path,json={'response':'approved'},headers={'X-CSRF-Token':'request-csrf'}).status_code==200
    assert client.post(path,json={'response':'discuss'},headers={'X-CSRF-Token':'request-csrf'}).status_code==409
    with app.extensions['owner_db']() as db:
        with pytest.raises(ProgrammingError):db.execute(text("UPDATE owners.requests SET amount=0"))
    with factory.begin() as db:db.get(PropertyGrant,setup['grant']).revoked_at=utcnow()
    assert client.get('/api/dashboard').json['requests']==[]
    assert client.post(path,json={'response':'approved'},headers={'X-CSRF-Token':'request-csrf'}).status_code==403


def test_history_navigation_keeps_selected_future_month_inside_chart(factory,setup):
    _,user=accept(factory,setup['invite']);today=utcnow().date()
    with factory.begin() as db:
        grant=db.get(PropertyGrant,setup['grant']);grant.reporting_from=month_shift(today,-36);grant.reporting_until=None
        for offset in range(-36,4):
            db.add(PerformanceMonth(property_id=setup['property'],month=month_shift(today,offset),as_of=today,metrics={'revenue':100,'source_fresh':True}))
    with factory() as db:
        for offset in (-24,0,3):
            month=month_shift(today,offset);data=dashboard(db,user,month=month)
            assert month.isoformat()[:7] in [r['month'] for r in data['months']]
            assert max(r['month'] for r in data['months'])<=month_shift(today,3).isoformat()[:7]
        assert len(data['available_months'])==40


def test_admin_pdf_upload_download_scope_withdrawal_and_immutable_content(factory,setup,app,staff_app):
    from io import BytesIO
    from owner_portal.models import StatementDocument
    token,user=accept(factory,setup['invite'])
    with factory.begin() as db:
        db.get(PropertyGrant,setup['grant']).reporting_from=date(2020,1,1)
        db.scalar(select(Membership).where(Membership.user_id==user)).statements=True
    staff=staff_client(staff_app);headers={'X-CSRF-Token':'synthetic-csrf'}
    pdf=b'%PDF-1.4\n% synthetic bounded publication fixture\n%%EOF\n'
    payload={'account_id':setup['account'],'property_ids':[setup['property']],'month':'2026-09','currency':'USD','scope_reviewed':True,
             'revenue':'1000','expenses':'100','management_fee':'200','net_income':'700'}
    def upload(change=None,body=pdf,headers=headers):
        return staff.post('/owner-access/api/upload-statement',data={'metadata':json.dumps(payload|(change or {})),'file':(BytesIO(body),'../../statement.pdf')},headers=headers)
    assert upload(headers={}).status_code==403
    assert upload({'currency':'MYR'}).status_code==400
    assert upload({'scope_reviewed':False}).status_code==400
    assert upload({'net_income':'701'}).status_code==400
    assert upload({'property_ids':[setup['other_property']]}).status_code==409
    assert upload(body=b'<html>not pdf</html>').status_code==400
    assert upload(body=b'%PDF-'+b'0'*(5*1024*1024)+b'%%EOF').status_code==400
    result=upload();assert result.status_code==200;sid=result.json['id']
    assert upload().status_code==409
    client=app.test_client()
    with client.session_transaction() as cookie:cookie['owner_token']=token
    data=client.get('/api/dashboard').json
    assert data['statements'][0]['has_pdf'] is True
    assert 'sha256' not in json.dumps(data) and '%PDF' not in json.dumps(data)
    downloaded=client.get(f'/statements/{sid}/pdf')
    assert downloaded.data==pdf and downloaded.mimetype=='application/pdf'
    assert downloaded.headers['Content-Disposition'].startswith('attachment;') and 'sandbox' in downloaded.headers['Content-Security-Policy']
    assert app.test_client().get(f'/statements/{sid}/pdf').status_code==401
    with app.extensions['owner_db']() as db:
        with pytest.raises(ProgrammingError):db.execute(text('DELETE FROM owners.statement_documents'))
    assert staff.post('/owner-access/api/withdraw-statement',json={'account_id':setup['account'],'statement_id':sid},headers=headers).status_code==200
    assert client.get(f'/statements/{sid}/pdf').status_code==404
    with factory() as db:assert db.get(StatementDocument,sid).content==pdf
