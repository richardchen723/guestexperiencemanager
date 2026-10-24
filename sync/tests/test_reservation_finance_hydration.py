from copy import deepcopy
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from sync.api_client import HostawayAPIClient
from sync.reservation_financials import hydrate_financials

NATIVE=json.loads(Path(__file__).with_name('fixtures').joinpath('synthetic_reservation_financials.json').read_text())[0]


@pytest.mark.parametrize('list_resource',[None,[],[{'id':1,'type':'tax'}]])
def test_empty_or_incomplete_list_resource_hydrates_exact_detail(list_resource):
    raw=deepcopy(NATIVE);raw['financeField']=list_resource
    client=MagicMock();client.get_reservation_detail.return_value=deepcopy(NATIVE)
    result=hydrate_financials(client,raw)
    assert result.values['tax_amount']==80.0
    client.get_reservation_detail.assert_called_once_with(10001)
    client.get_reservation_finance_fields.assert_not_called()


def test_incomplete_detail_resource_uses_exact_finance_endpoint():
    raw=deepcopy(NATIVE);raw['financeField']=[]
    detail=deepcopy(raw)
    client=MagicMock();client.get_reservation_detail.return_value=detail
    client.get_reservation_finance_fields.return_value=deepcopy(NATIVE['financeField'])
    assert hydrate_financials(client,raw).values['tax_amount']==80.0
    client.get_reservation_finance_fields.assert_called_once_with(10001)


@pytest.mark.parametrize('invalid',['invalid','NaN',True,False,float('nan'),float('inf'),'','  '])
@pytest.mark.parametrize('embedded_components',[True,False])
def test_invalid_detail_tax_stops_embedded_and_secondary_component_fallback(invalid,embedded_components):
    raw=deepcopy(NATIVE);raw['financeField']=[]
    detail=deepcopy(NATIVE);detail['taxAmount']=invalid
    if not embedded_components:
        detail['financeField']=[]
    client=MagicMock();client.get_reservation_detail.return_value=detail
    client.get_reservation_finance_fields.return_value=deepcopy(NATIVE['financeField'])
    result=hydrate_financials(client,raw)
    assert 'tax_amount' not in result.values
    assert result.reason=='invalid_generic_tax' and result.states['tax_amount']=='invalid'
    assert result.states['tax_hydration']=='invalid_generic_tax'
    client.get_reservation_finance_fields.assert_not_called()


@pytest.mark.parametrize('tax',[0,'0.00',80.0])
def test_valid_detail_tax_including_zero_does_not_need_component_fallback(tax):
    raw=deepcopy(NATIVE);raw['financeField']=[]
    detail=deepcopy(raw);detail['taxAmount']=tax
    client=MagicMock();client.get_reservation_detail.return_value=detail
    result=hydrate_financials(client,raw)
    assert result.values['tax_amount']==float(tax) and result.tax_basis=='generic_taxAmount'
    client.get_reservation_finance_fields.assert_not_called()


@pytest.mark.parametrize('state',['absent','null'])
def test_absent_and_null_detail_tax_remain_distinct_during_valid_component_fallback(state):
    raw=deepcopy(NATIVE);raw['financeField']=[]
    detail=deepcopy(NATIVE)
    if state=='absent':detail.pop('taxAmount')
    client=MagicMock();client.get_reservation_detail.return_value=detail
    result=hydrate_financials(client,raw)
    assert result.values['tax_amount']==80.0
    assert result.states['tax_amount']==state+'_resolved_from_components'


def test_wrong_detail_id_is_rejected_without_secondary_finance_read():
    raw=deepcopy(NATIVE);raw['financeField']=[]
    wrong=deepcopy(NATIVE);wrong['id']=19999
    client=MagicMock();client.get_reservation_detail.return_value=wrong
    result=hydrate_financials(client,raw)
    assert 'tax_amount' not in result.values and result.reason=='hydration_failed_or_wrong_identity'
    client.get_reservation_finance_fields.assert_not_called()


@pytest.mark.parametrize('stored',[0,75])
def test_hydration_never_replaces_known_positive_or_zero(stored):
    raw=deepcopy(NATIVE);raw['financeField']=[];client=MagicMock()
    result=hydrate_financials(client,raw,existing_tax=stored)
    assert 'tax_amount' not in result.values
    client.get_reservation_detail.assert_not_called()
    client.get_reservation_finance_fields.assert_not_called()


@pytest.mark.parametrize('mutation',[
    lambda d:d.update(updatedOn='2026-10-24 01:00:00'),
    lambda d:d.update(totalPrice=999),lambda d:d.update(cleaningFee=201),
    lambda d:d.update(currency='EUR'),lambda d:d.update(listingMapId=999),
])
def test_hydration_rejects_mismatched_source_snapshot(mutation):
    raw=deepcopy(NATIVE);raw['financeField']=[]
    detail=deepcopy(NATIVE);mutation(detail)
    client=MagicMock();client.get_reservation_detail.return_value=detail
    result=hydrate_financials(client,raw)
    assert 'tax_amount' not in result.values
    client.get_reservation_finance_fields.assert_not_called()


def test_hydration_failure_preserves_unknown_tax():
    raw=deepcopy(NATIVE);raw['financeField']=[]
    client=MagicMock();client.get_reservation_detail.side_effect=RuntimeError('fixture failure')
    result=hydrate_financials(client,raw)
    assert 'tax_amount' not in result.values and result.reason=='hydration_failed'


def test_exact_api_detail_rejects_wrong_id_and_never_uses_cursor_filters():
    client=HostawayAPIClient.__new__(HostawayAPIClient);calls=[]
    client._make_request=lambda endpoint,params=None:calls.append((endpoint,params)) or {'result':{'id':19999}}
    assert client.get_reservation_detail(10001) is None
    assert calls==[('reservations/10001',{'includeResources':1})]
