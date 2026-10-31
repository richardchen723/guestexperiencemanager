"""Blank tax is an explicit unknown-tax estimate, never a source zero-fill."""
from copy import deepcopy
from datetime import date
import pytest

from dashboard.kpi.estimates import BOOKING_ESTIMATE_POLICY, estimate_revenue
from dashboard.kpi.tests.test_estimates import W, booking, data


@pytest.mark.parametrize('tax',[None,'','  '])
def test_blank_tax_included_consistently_in_current_and_prior_without_mutation(tax):
    d=data();d['reservations']=[booking(tax_amount=tax,security_deposit_fee=40),booking(2,2025,tax_amount=tax,security_deposit_fee=40)]
    original=deepcopy(d)
    r=estimate_revenue(d,W)
    row=r['breakdown'][0]
    assert row['current_proxy_usd']==row['prior_proxy_usd']==180  # 250 - 30 cleaning - 40 known deposit.
    assert row['estimated_growth_pct']==0
    assert r['financial_coverage']['included_unknown_tax']=={'current':1,'prior':1}
    assert r['financial_coverage']['unknown_tax_booking_value_usd']=={'current':180,'prior':180}
    assert r['booking_estimate_policy']==BOOKING_ESTIMATE_POLICY
    assert r['data_status']=='estimated' and r['goal_status']=='unknown'
    assert 'unknown tax may' in r['trend_scope'] and d==original


def test_known_zero_is_distinct_from_absent_tax_and_real_tax_is_subtracted():
    d=data();d['reservations']=[booking(tax_amount=0),booking(2,2025,tax_amount=None)]
    r=estimate_revenue(d,W)
    assert r['breakdown'][0]['current_proxy_usd']==r['breakdown'][0]['prior_proxy_usd']==220
    assert r['financial_coverage']['included_unknown_tax']=={'current':0,'prior':1}
    d['reservations'][0]['tax_amount']=20
    r=estimate_revenue(d,W)
    assert r['breakdown'][0]['current_proxy_usd']==200
    assert r['breakdown'][0]['estimated_growth_pct']==pytest.approx(-9.09)


@pytest.mark.parametrize('field,value',[('tax_amount','bad'),('tax_amount',float('nan')),('tax_amount',float('inf')),('total_price',None),('total_price',float('inf')),('cleaning_fee',None),('cleaning_fee',float('nan')),('currency','EUR'),('security_deposit_fee','invalid'),('security_deposit_fee',float('inf')),('security_deposit_fee',float('nan'))])
def test_blank_tax_policy_does_not_admit_invalid_fields_or_other_currency(field,value):
    d=data();d['reservations']=[booking(**{field:value}),booking(2,2025)]
    r=estimate_revenue(d,W)
    assert r['value'] is None
    assert r['financial_coverage']['excluded_financial_or_currency']=={'current':1,'prior':0}
    assert r['financial_coverage']['included_unknown_tax']=={'current':0,'prior':0}
    assert not any('Bookings excluded for incomplete financial fields / USD'==x['label'] for x in r['diagnostics'])


def test_financial_counts_only_cover_relevant_windows_and_keep_other_filters():
    d=data();d['reservations']=[booking(tax_amount=None),booking(2,2025,tax_amount=None),booking(3,arrival=date(2026,1,1),departure=date(2026,1,2),tax_amount='bad'),booking(4,tax_amount=None,status='cancelled'),booking(5,tax_amount=None,source='ical')]
    r=estimate_revenue(d,W)
    assert r['financial_coverage']['included_unknown_tax']=={'current':1,'prior':1}
    assert r['financial_coverage']['excluded_financial_or_currency']=={'current':0,'prior':0}
    assert sum(x['booking_value_usd'] or 0 for x in r['trend'])==220
    assert sum(x['prior_booking_value_usd'] or 0 for x in r['trend'])==220


def test_blank_tax_does_not_override_negative_adjustments_and_duplicates_are_not_added():
    d=data();current=booking(tax_amount=None)
    d['reservations']=[current,current,booking(2,2025,tax_amount=None),booking(3,tax_amount=None,total_price=0)]
    r=estimate_revenue(d,W)
    assert r['breakdown'][0]['current_proxy_usd']==220
    assert r['financial_coverage']['included_unknown_tax']['current']==1
    assert r['financial_coverage']['excluded_financial_or_currency']['current']==1


def test_blank_tax_boundary_proration_and_newly_comparable_cohort():
    d=data();d['reservations']=[booking(tax_amount=None,arrival=date(2026,7,22),departure=date(2026,7,24)),booking(2,2025,arrival=date(2025,7,22),departure=date(2025,7,24))]
    r=estimate_revenue(d,W)
    assert r['denominator']==1
    assert r['breakdown'][0]['current_proxy_usd']==110 and r['breakdown'][0]['prior_proxy_usd']==100
    assert r['breakdown'][0]['estimated_growth_pct']==10
    assert r['financial_coverage']['common_cohort_unknown_tax_booking_value_usd']=={'current':110,'prior':0}


@pytest.mark.parametrize('deposit',[None,'','  ',0])
def test_truly_blank_or_known_zero_deposit_remains_distinct_from_invalid(deposit):
    d=data();d['reservations']=[booking(tax_amount=None,security_deposit_fee=deposit),booking(2,2025,tax_amount=None,security_deposit_fee=deposit)]
    r=estimate_revenue(d,W)
    assert r['breakdown'][0]['current_proxy_usd']==r['breakdown'][0]['prior_proxy_usd']==220
    assert r['financial_coverage']['excluded_financial_or_currency']=={'current':0,'prior':0}
