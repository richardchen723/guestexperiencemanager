"""Recorded cleaning issues joined to the same eligible stay denominator."""
from datetime import date

import pytest

from dashboard.kpi.estimates import estimate_cleaning
from dashboard.kpi.tests.test_estimates import W, booking, data


def evidence():
    d=data()
    d['listings'].append({'id':2,'name':'Other property'})
    d['reservations']=[booking(1,arrival=date(2026,7,23)),booking(2),booking(3,listing_id=2,arrival=date(2026,10,22),departure=date(2026,10,24)),booking(4,arrival=date(2026,7,22)),booking(5,arrival=date(2026,10,23)),booking(6,status='cancelled')]
    d['issues']=[{'id':i,'listing_id':lid,'reservation_id':rid,'category':category,'operational_status':status} for i,lid,rid,category,status in [(1,1,1,'cleanliness','resolved'),(2,1,1,'housekeeping','need_attention'),(3,1,2,'linens','resolved'),(4,2,3,'cleanliness','resolved'),(5,1,4,'cleanliness','resolved'),(6,1,5,'cleanliness','resolved'),(7,2,1,'cleanliness','resolved')]]
    d['issues'].append(d['issues'][0])
    return d


def test_deduplication_boundaries_property_week_and_global_counts_agree():
    r=estimate_cleaning(evidence(),{**W,2:W[1]})
    assert r['display']=='2 of 3'
    assert r['value']==66.67
    assert r['cleaning_weekly']['counts']=={'affected_stays':2,'total_stays':3,'percentage':66.67}
    assert sum(row['affected_stays'] for row in r['trend'])==2
    assert sum(row['total_stays'] for row in r['trend'])==3
    assert sum(row['affected_stays'] for row in r['cleaning_weekly']['properties'])==2
    assert sum(row['total_stays'] for row in r['cleaning_weekly']['properties'])==3
    assert sum(row['affected_stays'] for row in r['cleaning_weekly']['property_rows'])==2
    assert r['trend'][0]['partial_week'] and r['trend'][-1]['partial_week']
    assert r['trend'][0]['total_stays']==r['trend'][0]['affected_stays']==1
    assert r['trend'][-1]['value']==100
    assert any(row['total_stays']==0 and row['value'] is None for row in r['trend'])
    assert r['cleaning_weekly']['ambiguous_only_stays']==1
    assert r['breakdown'][0]['strict_source_records']>=1
    assert r['goal_status']=='unknown'


@pytest.mark.parametrize('missing', ['reservations','issues'])
def test_failed_or_missing_source_is_unknown_not_zero(missing):
    d=evidence()
    if missing=='reservations': d['sources']={'reservations':{'status':'error'}}
    else: d['issues_readable']=False
    r=estimate_cleaning(d,W)
    assert r['value'] is None and r['numerator'] is None
    assert all(row['value'] is None and row['affected_stays'] is None for row in r['trend'])
    assert all(row['percentage'] is None for row in r['cleaning_weekly']['properties'])


def test_observed_no_issues_is_zero_but_no_stays_has_no_percentage():
    d=data();d.update(reservations=[booking()],issues=[])
    r=estimate_cleaning(d,W)
    assert r['value']==0 and r['display']=='0 of 1'
    d['reservations']=[]
    r=estimate_cleaning(d,W)
    assert r['value'] is None and r['display']=='No stay data'
    assert r['cleaning_weekly']['counts']=={'affected_stays':0,'total_stays':0,'percentage':None}
