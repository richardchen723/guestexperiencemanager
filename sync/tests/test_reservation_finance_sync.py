"""Exercise real importer commits against a disposable database; mock HTTP only."""
from datetime import datetime
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine,event
from sqlalchemy.orm import sessionmaker

from database.models import Guest,Listing,Reservation,SyncLog
import sync.sync_reservations as module


@pytest.fixture
def run(monkeypatch):
    monkeypatch.setattr('requests.sessions.Session.request',lambda *a,**k:pytest.fail('Unexpected network request'))
    engine=create_engine('sqlite:///:memory:')
    @event.listens_for(engine,'connect')
    def attach(conn,_): conn.execute("ATTACH DATABASE ':memory:' AS public")
    for table in (Listing.__table__,Guest.__table__,Reservation.__table__,SyncLog.__table__):table.create(engine)
    factory=sessionmaker(bind=engine)
    monkeypatch.setattr(module,'init_models',lambda _:None)
    monkeypatch.setattr(module,'get_session',lambda _:factory())
    monkeypatch.setattr(module,'VERBOSE',False)
    client=MagicMock();monkeypatch.setattr(module,'HostawayAPIClient',lambda:client)
    def execute(stored_tax,fields,*,newer=False,older=False,repeat=False,detail=None,components=None):
        with factory() as s:
            s.add(Listing(listing_id=1));s.add(Guest(guest_id=1,guest_external_account_id='fixture'))
            s.add(Reservation(reservation_id=1,listing_id=1,guest_id=1,tax_amount=stored_tax,
                total_price=1000,currency='USD',cleaning_fee=100,security_deposit_fee=0,
                updated_on=datetime(2026,9,1),latest_activity_on=datetime(2026,9,1),last_synced_at=datetime(2026,9,1),
                status='new',host_note='unrelated retained note'))
            s.commit()
        raw={'id':1,'listingMapId':1,'guestExternalAccountId':'fixture','channelName':'bookingcom',
             'totalPrice':1000,'currency':'USD','cleaningFee':100,'securityDepositFee':0,'status':'new',
             'updatedOn':'2026-09-02 00:00:00' if newer else '2026-08-31 00:00:00' if older else '2026-09-01 00:00:00',
             'latestActivityOn':'2026-09-01 00:00:00'}
        raw.update(fields);client.get_reservations_page.return_value=[raw]
        client.get_reservation_detail.return_value=detail
        client.get_reservation_finance_fields.return_value=components
        result=module.sync_reservations(full_sync=True,progress_tracker=MagicMock(),sync_run_id=1)
        if repeat: result=module.sync_reservations(full_sync=True,progress_tracker=MagicMock(),sync_run_id=2)
        assert result['status']=='success'
        with factory() as s:
            row=s.get(Reservation,1)
            stored_log=s.query(SyncLog).order_by(SyncLog.sync_id.desc()).first()
            return result,{'tax':row.tax_amount,'total':row.total_price,'cleaning':row.cleaning_fee,
                           'last_synced':row.last_synced_at,'host_note':row.host_note,
                           'updated_on':row.updated_on,
                           'detail_calls':client.get_reservation_detail.call_count,
                           'field_states':json.loads(stored_log.listing_stats)['1']['financial_field_states']}
    yield execute
    engine.dispose()


@pytest.mark.parametrize('newer',[False,True])
@pytest.mark.parametrize('fields',[{}, {'taxAmount':None},{'taxAmount':'NaN'}])
def test_sparse_null_invalid_tax_preserves_existing_value(run,newer,fields):
    _,row=run(75,fields,newer=newer)
    assert row['tax']==75
    expected='absent' if not fields else 'null' if fields['taxAmount'] is None else 'invalid'
    assert row['field_states']['tax_amount:'+expected]==1


@pytest.mark.parametrize('tax',[0,125])
def test_unchanged_timestamp_refreshes_valid_finance_only(run,tax):
    result,row=run(None,{'taxAmount':tax})
    assert row['tax']==tax and result['records_updated']==1
    assert row['host_note']=='unrelated retained note'
    assert row['last_synced']>datetime(2026,9,1)


def test_older_payload_cannot_regress_finance(run):
    result,row=run(75,{'taxAmount':125},older=True)
    assert row['tax']==75 and result['records_updated']==0


def test_older_finance_with_newer_activity_cannot_regress_amount_or_watermark(run):
    _,row=run(75,{'taxAmount':125,'totalPrice':500,
        'latestActivityOn':'2026-09-03 00:00:00'},older=True,repeat=True)
    assert (row['tax'],row['total'],row['cleaning'])==(75,1000,100)
    assert row['updated_on']==datetime(2026,9,1)


@pytest.mark.parametrize('stored_tax',[75,0])
@pytest.mark.parametrize('newer',[False,True])
def test_component_fallback_never_overwrites_known_tax_and_reports_conflict(run,stored_tax,newer):
    native=json.loads(Path(__file__).with_name('fixtures').joinpath('synthetic_reservation_financials.json').read_text())[0]
    fields={k:native[k] for k in ['financeField','taxAmount','totalPrice','cleaningFee']}
    _,row=run(stored_tax,fields,newer=newer)
    assert row['tax']==stored_tax
    assert row['field_states']['tax_amount:null_component_conflict_preserved']==1


def test_full_sync_component_refresh_is_idempotent(run):
    native=json.loads(Path(__file__).with_name('fixtures').joinpath('synthetic_reservation_financials.json').read_text())[0]
    fields={k:native[k] for k in ['financeField','taxAmount','totalPrice','cleaningFee']}
    result,row=run(None,fields,repeat=True)
    assert row['tax']==80.0 and result['records_updated']==0


def hydration_fixture():
    native=json.loads(Path(__file__).with_name('fixtures').joinpath('synthetic_reservation_financials.json').read_text())[0]
    detail={**native,'id':1,'listingMapId':1,'updatedOn':'2026-09-01 00:00:00'}
    fields={k:detail[k] for k in ['taxAmount','totalPrice','cleaningFee','securityDepositFee']}
    fields['financeField']=[]
    return fields,detail


def test_normal_importer_hydrates_empty_list_finance_for_unchanged_row(run):
    fields,detail=hydration_fixture()
    _,row=run(None,fields,detail=detail)
    assert row['tax']==80.0 and row['detail_calls']==1
    assert row['host_note']=='unrelated retained note'


def test_normal_importer_rejects_wrong_hydrated_identity(run):
    fields,detail=hydration_fixture();detail['id']=2
    _,row=run(None,fields,detail=detail)
    assert row['tax'] is None and row['detail_calls']==1


@pytest.mark.parametrize('stored',[0,75])
def test_normal_importer_never_hydrates_over_known_tax(run,stored):
    fields,detail=hydration_fixture()
    _,row=run(stored,fields,detail=detail)
    assert row['tax']==stored and row['detail_calls']==0


def test_financial_hydration_budget_preserves_unknown_and_records_diagnostic(run,monkeypatch):
    monkeypatch.setattr(module,'MAX_FINANCIAL_HYDRATIONS',0)
    fields,detail=hydration_fixture()
    _,row=run(None,fields,detail=detail)
    assert row['tax'] is None and row['detail_calls']==0
    assert row['field_states']['tax_unknown:financial_hydration_budget_exhausted']==1


def test_all_omitted_financial_keys_are_preserved_in_sparse_newer_payload(monkeypatch,run):
    # The field mapper's output is what gets merged into the ordinary mapping.
    original=module.map_financials
    monkeypatch.setattr(module,'map_financials',lambda raw,**kwargs:original({'taxAmount':None},**kwargs))
    _,row=run(75,{},newer=True)
    assert (row['tax'],row['total'],row['cleaning'])==(75,1000,100)
