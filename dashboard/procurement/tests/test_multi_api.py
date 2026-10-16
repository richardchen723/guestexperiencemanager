"""Real PostgreSQL transactions and access control for shared procurement."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date
import io
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text

from dashboard.procurement.tests.test_api import qa_app, qa, client_for, post, create, draft
from dashboard.procurement.tests.test_workflow import request_data, order_data


def shared(client, qa):
    return create(client,qa,listing_id=None,listing_ids=[101,102],requester_staff_id=None,requester_name='Staff',
                  source={'channel':'manual','body':'Private notes for both properties'},
                  items=[{'listing_id':101,'name':'Toilet paper','unit':'rolls','quantity':'4'},
                         {'listing_id':102,'name':'Toilet paper','unit':'rolls','quantity':'6'}])


def buy_shared(client, ticket):
    response = post(client,'orders',order_data(lines=[{'name':'Toilet paper','unit':'rolls','quantity':'10','unit_price':'1',
        'allocations':[{'item_id':i['id'],'quantity':i['quantity']} for i in ticket['items']]}]))
    assert response.status_code == 200,response.json
    order = response.json
    order = post(client,f"orders/{order['id']}/approve",{'version':order['version']}).json
    response = post(client,f"orders/{order['id']}/place",{'version':order['version'],'reference':'QA-MULTI-'+str(order['id']),'purchased_on':date.today().isoformat()})
    assert response.status_code == 200,response.json
    return response.json


def batch_body(client,ticket,order):
    latest = client.get(f"/procurement/api/requests/{ticket['id']}").json
    return {'version':latest['version'],'note':'Housekeepers checked their closets','evidence':{'reference':'wa-shared-confirmation'},'confirm_delivery':True,
            'confirmations':[{'allocation_id':a['id'],'listing_id':a['listing_id'],'staff_id':1,'quantity':a['quantity']} for a in order['lines'][0]['allocations']]}


def test_shared_request_housekeeper_can_read_own_portion_and_cannot_mutate_other_property(qa):
    admin = client_for(qa); ticket = shared(admin,qa); order = buy_shared(admin,ticket)
    cleaner = client_for(qa,'cleaner')
    result = cleaner.get(f"/procurement/api/requests/{ticket['id']}")
    assert result.status_code == 200
    assert result.json['listing_ids'] == [102]
    assert len(result.json['items']) == 1
    assert result.json['messages'] == result.json['activity'] == []
    assert 'Searenity Haven' not in result.get_data(as_text=True)
    assert 'private shipping address' not in result.get_data(as_text=True)
    assert [t['id'] for t in cleaner.get('/procurement/api/requests').json['requests']] == [ticket['id']]
    wrong = order['lines'][0]['allocations'][0]
    response = post(cleaner,f"requests/{ticket['id']}/confirm",{'version':result.json['version'],'allocation_id':wrong['id'],'listing_id':101,'staff_id':2,'quantity':1,'confirm_delivery':True,'note':'Forbidden'})
    assert response.status_code == 403
    response = post(cleaner,f"requests/{ticket['id']}/problem",{'version':result.json['version'],'allocation_id':wrong['id'],'quantity':1,'kind':'lost','reason':'Forbidden'})
    assert response.status_code == 403


def test_batch_receipt_rolls_back_all_rows_if_any_quantity_is_invalid(qa):
    admin = client_for(qa); ticket = shared(admin,qa); order = buy_shared(admin,ticket)
    body = batch_body(admin,ticket,order)
    body['confirmations'][1]['quantity'] = 7
    response = post(admin,f"requests/{ticket['id']}/confirm-batch",body)
    assert response.status_code == 400,response.json
    current = admin.get(f"/procurement/api/requests/{ticket['id']}").json
    assert current['version'] == body['version']
    assert [i['confirmed'] for i in current['items']] == [0,0]
    assert current['ready_properties'] == 0
    assert not any(e['action'] == 'property_confirmed' for e in current['activity'])


def test_partial_batch_receipts_are_idempotent_and_complete_only_selected_properties(qa):
    admin = client_for(qa); ticket = shared(admin,qa); order = buy_shared(admin,ticket)
    body = batch_body(admin,ticket,order)
    body['confirmations'][1]['quantity'] = 2
    key = str(uuid4())
    first = post(admin,f"requests/{ticket['id']}/confirm-batch",body,key=key)
    again = post(admin,f"requests/{ticket['id']}/confirm-batch",body,key=key)
    assert first.status_code == again.status_code == 200
    assert first.json == again.json
    assert [i['confirmed'] for i in first.json['items']] == [4,2]
    assert first.json['ready_properties'] == 1
    assert first.json['status'] == 'in_progress'
    assert post(admin,f"requests/{ticket['id']}/confirm-batch",body).status_code == 409


def test_housekeeper_batch_is_scoped_and_does_not_require_proxy_evidence(qa):
    admin = client_for(qa); ticket = shared(admin,qa); order = buy_shared(admin,ticket)
    cleaner = client_for(qa,'cleaner'); value = cleaner.get(f"/procurement/api/requests/{ticket['id']}").json
    item = value['items'][0]; allocation = item['allocations'][0]
    response = post(cleaner,f"requests/{ticket['id']}/confirm-batch",{'version':value['version'],'confirm_delivery':True,'note':'Closet checked',
        'confirmations':[{'allocation_id':allocation['id'],'listing_id':102,'staff_id':2,'quantity':6}]})
    assert response.status_code == 200,response.json
    assert response.json['status'] == 'completed' and response.json['ready_properties'] == 1
    assert admin.get(f"/procurement/api/requests/{ticket['id']}").json['ready_properties'] == 1


def test_housekeeper_replay_respects_changed_assignments(qa):
    from dashboard.auth.models import get_session
    from dashboard.procurement.models import Staff
    admin = client_for(qa); ticket = shared(admin,qa); buy_shared(admin,ticket)
    cleaner = client_for(qa,'cleaner'); value = cleaner.get(f"/procurement/api/requests/{ticket['id']}").json
    body = {'version':value['version'],'confirm_delivery':True,'note':'Checked property 102',
            'confirmations':[{'allocation_id':value['items'][0]['allocations'][0]['id'],'listing_id':102,'staff_id':2,'quantity':1}]}
    key = str(uuid4())
    assert post(cleaner,f"requests/{ticket['id']}/confirm-batch",body,key=key).status_code == 200
    with get_session() as db:
        db.get(Staff,2).listing_ids = [101]
        db.commit()
    replay = post(cleaner,f"requests/{ticket['id']}/confirm-batch",body,key=key)
    assert replay.status_code == 200,replay.json
    assert replay.json['listing_ids'] == [101]
    assert 'Appleseed Cabin' not in replay.get_data(as_text=True)


def test_duplicate_batch_rows_roll_back_instead_of_double_confirming(qa):
    admin = client_for(qa); ticket = shared(admin,qa); order = buy_shared(admin,ticket)
    body = batch_body(admin,ticket,order)
    body['confirmations'] = [body['confirmations'][0],body['confirmations'][0]]
    response = post(admin,f"requests/{ticket['id']}/confirm-batch",body)
    assert response.status_code == 400
    assert [i['confirmed'] for i in admin.get(f"/procurement/api/requests/{ticket['id']}").json['items']] == [0,0]


def test_competing_combined_buyers_cannot_reserve_the_same_shared_need_twice(qa):
    admin = client_for(qa); ticket = shared(admin,qa)
    data = order_data(lines=[{'name':'Toilet paper','unit':'rolls','quantity':'10','unit_price':'1',
                            'allocations':[{'item_id':i['id'],'quantity':i['quantity']} for i in ticket['items']]}])
    def buy(_):
        return post(client_for(qa),'orders',data).status_code
    with ThreadPoolExecutor(max_workers=2) as executor:
        statuses = list(executor.map(buy,range(2)))
    assert sorted(statuses) == [200,400]
    current = admin.get(f"/procurement/api/requests/{ticket['id']}").json
    assert [i['to_order'] for i in current['items']] == [0,0]
    assert len(admin.get('/procurement/api/orders').json['orders']) == 1


def test_shipment_events_identify_only_affected_request_properties(qa):
    admin = client_for(qa); ticket = shared(admin,qa)
    order = post(admin,'orders',order_data(lines=[{'name':'Toilet paper','unit':'rolls','quantity':i['quantity'],'unit_price':'1',
                        'allocations':[{'item_id':i['id'],'quantity':i['quantity']}]} for i in ticket['items']])).json
    order = post(admin,f"orders/{order['id']}/approve",{'version':order['version']}).json
    order = post(admin,f"orders/{order['id']}/place",{'version':order['version'],'reference':'QA-SCOPED-SHIP','purchased_on':date.today().isoformat()}).json
    response = post(admin,f"orders/{order['id']}/shipments",{'version':order['version'],'lines':[{'line_id':order['lines'][0]['id'],'quantity':4}]})
    assert response.status_code == 200,response.json
    events = admin.get('/procurement/api/events').json['events']
    shipment = [e for e in events if e['action'] == 'shipment_created']
    assert len(shipment) == 1
    assert shipment[0]['listing_ids'] == [101,102]
    assert shipment[0]['affected_listing_ids'] == [101]


@pytest.mark.parametrize('field,value', [('listing_ids',[True]),('listing_ids',[101,101]),('listing_ids',{'id':101}),('confirmations',[None])])
def test_malformed_shared_payloads_return_validation_errors(qa,field,value):
    data = request_data(owner_user_id=qa.qa_users['admin'])
    data[field] = value
    response = post(client_for(qa),'requests',data)
    assert response.status_code == 400,response.json


def test_legacy_database_upgrade_preserves_item_ids_allocations_and_receipts(qa):
    import os
    from dashboard.procurement.models import init_procurement_database
    admin = client_for(qa); ticket = create(admin,qa); order = draft(admin,ticket)
    order = post(admin,f"orders/{order['id']}/approve",{'version':order['version']}).json
    order = post(admin,f"orders/{order['id']}/place",{'version':order['version'],'reference':'QA-LEGACY-UPGRADE','purchased_on':date.today().isoformat()}).json
    current = admin.get(f"/procurement/api/requests/{ticket['id']}").json
    response = post(admin,f"requests/{ticket['id']}/confirm",{'version':current['version'],'allocation_id':current['items'][0]['allocations'][0]['id'],
        'listing_id':101,'staff_id':1,'quantity':2,'note':'Legacy property receipt','evidence':{'reference':'legacy-wa'},'confirm_delivery':True})
    assert response.status_code == 200,response.json
    receipt = admin.post(f"/procurement/api/orders/{order['id']}/receipts",data={'file':(io.BytesIO(b'%PDF-1.4\nQA receipt'),'legacy.pdf')},
                         headers={'X-CSRF-Token':'qa-csrf','Idempotency-Key':str(uuid4())})
    assert receipt.status_code == 200,receipt.json
    engine = create_engine(os.environ['PROCUREMENT_QA_DATABASE_URL'])
    assert engine.url.database.startswith('codex_procurement_qa_') and engine.url.host in (None,'localhost','127.0.0.1')
    try:
        # Emulate the deployed schema, then run the real additive migration.
        with engine.begin() as conn:
            conn.execute(text('ALTER TABLE procurement.requests DROP COLUMN property_snapshots'))
            conn.execute(text('ALTER TABLE procurement.request_items DROP COLUMN listing_id'))
            conn.execute(text('ALTER TABLE procurement.order_lines DROP COLUMN destination'))
        init_procurement_database(engine)
        init_procurement_database(engine)  # Safe on repeated startup.
        current = admin.get(f"/procurement/api/requests/{ticket['id']}")
        assert current.status_code == 200,current.json
        assert current.json['listing_ids'] == [101]
        assert current.json['items'][0]['id'] == ticket['items'][0]['id']
        assert current.json['items'][0]['listing_id'] == 101
        assert current.json['items'][0]['reserved'] == 4
        assert current.json['items'][0]['confirmed'] == 2
        purchase = admin.get(f"/procurement/api/orders/{order['id']}")
        assert purchase.status_code == 200,purchase.json
        assert purchase.json['lines'][0]['destination']['id'] == 1
        assert purchase.json['lines'][0]['allocations'][0]['listing_id'] == 101
        assert purchase.json['attachments'][0]['name'] == 'legacy.pdf'
        report = admin.get('/procurement/api/report?month='+date.today().strftime('%Y-%m')).json
        assert report['currencies'][0]['properties'][0]['confirmed_cents'] == 350
    finally:
        init_procurement_database(engine)
        engine.dispose()
