import copy
import json
from pathlib import Path

import pytest

from sync.reservation_financials import amount, component_tax, map_financials
from sync.api_client import HostawayAPIClient

SAMPLES=json.loads(Path(__file__).with_name('fixtures').joinpath('synthetic_reservation_financials.json').read_text())


@pytest.mark.parametrize('rid,expected,reason',[
    (10001,80.0,None),(10004,120.0,None),
    (10006,None,'component_total_mismatch'),
    (10002,None,'no_tax_component_unknown'),
    (10003,None,'no_tax_component_unknown'),
    (10005,None,'no_tax_component_unknown'),
])
def test_six_synthetic_financial_scenarios(rid,expected,reason):
    raw=next(r for r in SAMPLES if r['id']==rid)
    r=map_financials(raw)
    assert r.values.get('tax_amount')==expected and r.reason==reason
    if expected is not None:
        assert r.tax_basis=='reconciled_financeField_total'


@pytest.mark.parametrize('raw,state', [({},'absent'),({'taxAmount':None},'null'),
    ({'taxAmount':'NaN'},'invalid'),({'taxAmount':True},'invalid')])
def test_unknown_generic_tax_never_emits_an_erasing_write(raw,state):
    r=map_financials(raw)
    assert r.states['tax_amount']==state
    assert 'tax_amount' not in r.values


@pytest.mark.parametrize('key,column',[('taxAmount','tax_amount'),('totalPrice','total_price'),
    ('cleaningFee','cleaning_fee'),('securityDepositFee','security_deposit_fee')])
def test_real_zero_is_preserved(key,column):
    assert map_financials({key:0}).values[column]==0
    assert map_financials({key:'0.00'}).values[column]==0


@pytest.mark.parametrize('value',[None,True,False,'NaN',float('inf'),'-Infinity',{},[],'',1e15])
def test_invalid_amounts_stay_unknown(value):
    assert amount(value) is None


@pytest.mark.parametrize('mutation,reason',[
    (lambda c:c[3].update(isDeleted=None),'unknown_deletion_flag'),
    (lambda c:c[3].update(isIncludedInTotalPrice=None),'unknown_inclusion_flag'),
    (lambda c:c[3].update(isIncludedInTotalPrice=0),'tax_not_included_in_total'),
    (lambda c:c[3].update(total='NaN'),'invalid_component_total'),
    (lambda c:c.append(copy.deepcopy(c[3])),'missing_or_duplicate_component_id'),
    (lambda c:c[3].update(id=[]),'missing_or_duplicate_component_id'),
    (lambda c:c[3].update(total=-1),'negative_tax_requires_review'),
])
def test_ambiguous_components_are_not_mapped(mutation,reason):
    raw=copy.deepcopy(SAMPLES[0]);mutation(raw['financeField'])
    r=map_financials(raw)
    assert 'tax_amount' not in r.values and r.reason==reason


def test_component_total_uses_total_instead_of_unit_value_and_ignores_duplicate_resource():
    raw=copy.deepcopy(SAMPLES[0])
    raw['financeField'][3]['value']=1
    raw['reservationFees']=copy.deepcopy(raw['financeField'])
    assert map_financials(raw).values['tax_amount']==80.0


def test_deleted_tax_is_not_counted_and_absence_does_not_become_zero():
    raw=copy.deepcopy(SAMPLES[0]);raw['financeField'][3]['isDeleted']=1
    raw['totalPrice']=920
    assert map_financials(raw).reason=='deleted_tax_only_unknown'


@pytest.mark.parametrize('cleaning,reason',[(201,'cleaning_component_mismatch'),(None,'unverified_generic_cleaning')])
def test_matching_total_with_unknown_or_conflicting_cleaning_is_not_repaired(cleaning,reason):
    raw=copy.deepcopy(SAMPLES[0]);raw['cleaningFee']=cleaning
    r=map_financials(raw)
    assert 'tax_amount' not in r.values and r.reason==reason


def test_non_usd_and_unvalidated_airbnb_components_require_review():
    raw=copy.deepcopy(SAMPLES[0]);raw['currency']='EUR'
    assert map_financials(raw).reason=='unverified_usd_total'
    raw['currency']='USD';raw['channelName']='airbnbOfficial'
    assert map_financials(raw).reason=='unvalidated_channel_component_semantics'


def test_conflicting_component_currency_is_not_inferred_from_usd_reservation():
    raw=copy.deepcopy(SAMPLES[0]);raw['financeField'][3]['currency']='EUR'
    assert map_financials(raw).reason=='conflicting_component_currency'


def test_included_aggregate_is_rejected_even_when_typed_as_price():
    raw=copy.deepcopy(SAMPLES[0]);raw['financeField'][0].update(type='price',isIncludedInTotalPrice=1)
    assert map_financials(raw).reason=='included_aggregate_component'


def test_api_requests_resources_without_invalid_cursor_sort_combination():
    client=HostawayAPIClient.__new__(HostawayAPIClient)
    calls=[]
    client._make_request=lambda endpoint,params:calls.append((endpoint,params)) or {'result':[]}
    client.get_reservations_page(after_id=123,include_resources=True)
    assert calls==[('reservations',{'afterId':123,'includeResources':1})]
    with pytest.raises(ValueError,match='afterId'):
        client.get_reservations_page(after_id=123,sort_order='asc')
    assert len(calls)==1
