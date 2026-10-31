from datetime import date, timedelta
from decimal import Decimal

import pytest

from owner_portal.calculations import accommodation_amount, build_month, days, month_shift, pickup_range
from owner_portal.prepare import physical_calendar, validate_detail


def reservation(start, end, amount=100, **kwargs):
    return dict(arrival_date=start, departure_date=end, accommodation=amount, status='new', channel_name='direct', **kwargs)


def calendar(start, end, today, status='available', **kwargs):
    return [dict(calendar_date=d, snapshot_date=min(d, today), status=status, is_available=status=='available', price=100, minimum_stay=1, **kwargs) for d in days(start,end)]


def test_room_revenue_reconciles_including_airbnb_tax_not_generic_tax():
    raw={'currency':'USD','totalPrice':1340,'taxAmount':0}
    fields=[{'id':i,'isDeleted':0,'isIncludedInTotalPrice':1,'type':kind,'total':value} for i,(kind,value) in enumerate([('accommodation',1000),('fee',200),('tax',140)],1)]
    assert accommodation_amount(raw,fields)==(Decimal('1000.00'),None)
    for mutation in ('missing','duplicate','discount','currency','negative','mismatch'):
        changed=[dict(f) for f in fields]
        if mutation=='missing':changed.pop()
        if mutation=='duplicate':changed.append(changed[0])
        if mutation=='discount':changed[0]['type']='discount'
        if mutation=='currency':changed[0]['currency']='MYR'
        if mutation=='negative':changed[0]['total']=-1000
        if mutation=='mismatch':changed[0]['total']=1001
        assert accommodation_amount(raw,changed)[0] is None


def test_stay_month_allocation_preserves_cents_and_checkout_is_not_a_night():
    today=date(2026,3,2)
    rows=[reservation(date(2026,1,30),date(2026,2,2),'100.01')]
    jan=build_month(date(2026,1,1),today,rows,calendar(date(2026,1,1),date(2026,2,1),today))
    feb=build_month(date(2026,2,1),today,rows,calendar(date(2026,2,1),date(2026,3,1),today))
    assert jan['revenue']==66.68 and feb['revenue']==33.33
    assert jan['nights']==2 and feb['nights']==1
    assert jan['sellable']==31 and feb['sellable']==28


@pytest.mark.parametrize('amount,expected', [(None,None),(0,0)])
def test_unknown_is_distinct_from_zero(amount,expected):
    start=date(2026,10,1);end=date(2026,11,1)
    result=build_month(start,end,[reservation(start,start+timedelta(days=1),amount)],calendar(start,end,end))
    assert result['revenue']==expected
    assert result['nights']==1


def test_cancellations_owner_blocks_imported_ical_and_conflicts():
    start=date(2026,10,1);end=date(2026,11,1)
    rows=[reservation(start,start+timedelta(days=2)),reservation(start+timedelta(days=2),start+timedelta(days=3))]
    rows[1]['status']='cancelled'
    owner=reservation(start+timedelta(days=3),start+timedelta(days=5));owner['status']='ownerStay';rows.append(owner)
    imported=reservation(start+timedelta(days=5),start+timedelta(days=6),0);imported['channel_name']='customIcal';rows.append(imported)
    result=build_month(start,end,rows,calendar(start,end,end))
    assert result['revenue']==100 and result['nights']==2 and result['sellable']==28 and result['blocked']==3
    rows.append(reservation(start,start+timedelta(days=1),200))
    conflict=build_month(start,end,rows,calendar(start,end,end))
    assert conflict['revenue'] is None and conflict['nights'] is None


def test_unmatched_calendar_reservation_withholds_revenue_and_occupancy():
    start=date(2026,10,1);end=date(2026,11,1)
    result=build_month(start,end,[],calendar(start,end,end,'reserved'))
    assert result['revenue'] is None and result['sellable'] is None and result['unknown_nights']==31


def test_no_calendar_history_keeps_confirmed_revenue_but_not_occupancy():
    start=date(2026,11,1)
    result=build_month(start,start,[reservation(start,date(2026,11,4),300)],[])
    assert result['booked']==300 and result['revenue']==300
    assert result['nights'] is None and result['low'] is None


def test_leap_year_month_and_stale_forecast():
    start=date(2028,2,1)
    assert month_shift(start,1)==date(2028,3,1)
    result=build_month(start,start,[reservation(start,date(2028,3,1),2900)],calendar(start,date(2028,3,1),start),source_fresh=False)
    assert result['sellable']==29 and result['revenue']==2900 and result['low'] is None
    assert result['forecast_reason']=='source_stale'


def test_pickup_uses_only_open_nights_at_matched_lead_and_completed_outcomes():
    today=date(2026,11,1);history={}
    for index,d in enumerate(days(today-timedelta(days=90),today)):
        history[d]=[dict(snapshot_date=d-timedelta(days=30),status='available',is_available=True),dict(snapshot_date=d,status='reserved' if index%2 else 'available',is_available=index%2==0)]
    lo,hi=pickup_range(history,today,30)
    assert .4<lo<.5<hi<.6
    assert pickup_range(history,today,60) is None


def test_forecast_additions_cannot_exceed_open_capacity():
    today=date(2026,11,1);end=date(2026,12,1)
    history=[]
    for index,d in enumerate(days(today-timedelta(days=90),today)):
        for lead in range(1,33):history.append(dict(calendar_date=d,snapshot_date=d-timedelta(days=lead),status='available',is_available=True))
        history.append(dict(calendar_date=d,snapshot_date=d,status='reserved' if index%2 else 'available',is_available=index%2==0))
    rows=[reservation(today,today+timedelta(days=2),200)]
    result=build_month(today,today,rows,history+calendar(today,end,today))
    assert 200<result['low']<result['high']<3000
    assert 2<result['nights_low']<result['nights_high']<30


def test_alternative_listings_count_one_physical_day():
    start=date(2026,11,1)
    a=calendar(start,start+timedelta(days=1),start,'blocked',listing_id=1)
    b=calendar(start,start+timedelta(days=1),start,listing_id=2)
    merged=physical_calendar(a+b,[1,2]);assert len(merged)==1 and merged[0]['status']=='available'
    assert physical_calendar(a,[1,2])[0]['status']=='unknown'


def test_changed_source_identity_is_not_published():
    row={'reservation_id':1,'listing_id':2,'arrival_date':date(2026,11,1),'departure_date':date(2026,11,2),'status':'new','total_price':100,'currency':'USD'}
    assert validate_detail(row,{'id':2},[])[0] is None


def test_invalid_confirmed_dates_do_not_produce_zero_revenue():
    start=date(2026,11,1);end=date(2026,12,1)
    result=build_month(start,start,[reservation(None,None,100)],calendar(start,end,start))
    assert result['revenue'] is None and result['sellable'] is None and result['nights'] is None
