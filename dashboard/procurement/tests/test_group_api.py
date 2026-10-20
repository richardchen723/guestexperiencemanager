"""Real database/API coverage for deferred group allocations."""
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import create_engine, text

from dashboard.procurement.tests.test_api import qa_app, qa, client_for, post, create
from dashboard.procurement.tests.test_workflow import PROPERTIES


@pytest.fixture
def group_qa(qa,monkeypatch):
    from dashboard.procurement import routes
    original=routes.reference_data
    properties={key:{**value,'tags':[*value['tags'],'QA group']} for key,value in PROPERTIES.items()}
    def references(): return properties,original()[1]
    monkeypatch.setattr(routes,'reference_data',references)
    qa.group_properties=properties
    return qa


def intake(client,app,**changes):
    data={'listing_id':None,'group_tag':'QA group','group_coverage':'entire_group',
          'requester_staff_id':None,'requester_name':'Operations',
          'items':[{'name':'Toilet paper','unit':'rolls'}]}
    data.update(changes)
    return create(client,app,**data)


def allocation(ticket,**changes):
    body={'version':ticket['version'],'item_id':next(i['id'] for i in ticket['items'] if i['listing_id'] is None),
          'allocations':[{'listing_id':101,'quantity':'4'},{'listing_id':102,'quantity':'6'}]}
    body.update(changes)
    return body


def test_group_api_intake_allocation_retry_and_source_replay(group_qa):
    admin=client_for(group_qa)
    source={'channel':'qa','message_id':'group-request-1','body':'Need paper for the group'}
    ticket=intake(admin,group_qa,source=source)
    assert ticket['group']['coverage']=='entire_group' and len(ticket['items'])==1
    assert ticket['listing_ids']==[] and ticket['allocation_pending']
    from uuid import uuid4
    key=str(uuid4()); body=allocation(ticket)
    response=post(admin,f"requests/{ticket['id']}/allocate-properties",body,key=key)
    assert response.status_code==200,response.json
    assert [i['quantity'] for i in response.json['items']]==[4,6]
    assert response.json['listing_ids']==[101,102] and not response.json['allocation_pending']
    assert post(admin,f"requests/{ticket['id']}/allocate-properties",body,key=key).json==response.json
    assert post(admin,f"requests/{ticket['id']}/allocate-properties",body).status_code==409
    # Source identity survives staff allocation and removal of the original tag.
    for prop in group_qa.group_properties.values(): prop['tags']=[]
    replay=intake(admin,group_qa,source=source)
    assert replay['id']==ticket['id'] and replay['source_replayed']
    events=admin.get('/procurement/api/events').json['events']
    assert events[-1]['action']=='properties_allocated'
    assert events[-1]['affected_listing_ids']==[101,102]
    assert events[-1]['group']['tag']=='QA group'


def test_housekeeper_group_intake_is_scoped_before_and_after_allocation(group_qa):
    admin=client_for(group_qa); cleaner=client_for(group_qa,'cleaner')
    bootstrap=cleaner.get('/procurement/api/bootstrap').json
    assert bootstrap['property_groups']==[{'tag':'QA group','can_request_entire_group':False},
                                          {'tag':'cabin','can_request_entire_group':True}]
    # A cleaner cannot claim an entire group containing properties outside their assignments.
    response=post(cleaner,'requests',{'title':'Group intake','group_tag':'QA group','group_coverage':'entire_group',
        'items':[{'name':'Paper','unit':'rolls'}]})
    assert response.status_code==403,response.json
    own=intake(cleaner,group_qa,group_coverage='assign_later',requester_staff_id=2)
    assert [p['id'] for p in own['group']['properties']]==[102]
    assert own['items'][0]['listing_id'] is None
    assert post(cleaner,f"requests/{own['id']}/allocate-properties",allocation(own)).status_code==403
    shared=intake(admin,group_qa,source={'channel':'qa','body':'Private group note'})
    assert cleaner.get(f"/procurement/api/requests/{shared['id']}").status_code==403
    assert [r['id'] for r in cleaner.get('/procurement/api/requests').json['requests']]==[own['id']]
    response=post(admin,f"requests/{shared['id']}/allocate-properties",allocation(shared))
    assert response.status_code==200,response.json
    scoped=cleaner.get(f"/procurement/api/requests/{shared['id']}")
    assert scoped.status_code==200
    assert scoped.json['listing_ids']==[102]
    assert [p['id'] for p in scoped.json['group']['properties']]==[102]
    assert scoped.json['messages']==scoped.json['activity']==[]
    assert 'Searenity Haven' not in scoped.get_data(as_text=True)
    assert 'Private group note' not in scoped.get_data(as_text=True)
    # Changing assignments revokes the creator's access to their old pending group request.
    from dashboard.procurement.models import get_session,Staff
    with get_session() as db:
        db.get(Staff,2).listing_ids=[];db.commit()
    assert cleaner.get(f"/procurement/api/requests/{own['id']}").status_code==403
    assert cleaner.get('/procurement/api/requests').json['requests']==[]


@pytest.mark.parametrize('rows',[
    [],[{'listing_id':101,'quantity':'0'}],
    [{'listing_id':101,'quantity':'4'},{'listing_id':101,'quantity':'4'}],
    [{'listing_id':101,'quantity':'4'},{'listing_id':99999,'quantity':'1'}],
    [{'listing_id':101,'quantity':'7'},{'listing_id':102,'quantity':'6'}],
    [{'listing_id':101,'quantity':'1.0001'}],
    [None], [101], [{'listing_id':True,'quantity':'1'}],
    [{'listing_id':101.5,'quantity':'1'}],
])
def test_invalid_allocations_roll_back_all_properties_and_quantity_changes(group_qa,rows):
    admin=client_for(group_qa)
    ticket=intake(admin,group_qa,items=[{'name':'Toilet paper','unit':'rolls','quantity':'12'}])
    response=post(admin,f"requests/{ticket['id']}/allocate-properties",allocation(ticket,allocations=rows))
    assert response.status_code==400,response.json
    current=admin.get(f"/procurement/api/requests/{ticket['id']}").json
    assert current['version']==ticket['version'] and current['listing_ids']==[]
    assert len(current['items'])==1 and current['items'][0]['quantity']==12
    assert not any(e['action']=='properties_allocated' for e in current['activity'])


@pytest.mark.parametrize('total',[None,'20','0'])
def test_known_group_total_cannot_be_cleared_or_changed_during_allocation(group_qa,total):
    admin=client_for(group_qa)
    ticket=intake(admin,group_qa,items=[{'name':'Toilet paper','unit':'rolls','quantity':'12'}])
    response=post(admin,f"requests/{ticket['id']}/allocate-properties",allocation(ticket,total_quantity=total))
    assert response.status_code==400
    assert admin.get(f"/procurement/api/requests/{ticket['id']}").json['items'][0]['quantity']==12


def test_concurrent_group_allocations_cannot_oversubscribe_the_same_need(group_qa):
    admin=client_for(group_qa)
    ticket=intake(admin,group_qa,items=[{'name':'Toilet paper','unit':'rolls','quantity':'10'}])
    def attempt(prop):
        client=client_for(group_qa)
        return post(client,f"requests/{ticket['id']}/allocate-properties",allocation(ticket,allocations=[{'listing_id':prop,'quantity':'7'}])).status_code
    with ThreadPoolExecutor(max_workers=2) as pool: results=list(pool.map(attempt,[101,102]))
    assert sorted(results)==[200,409]
    current=admin.get(f"/procurement/api/requests/{ticket['id']}").json
    assert sorted(i['quantity'] for i in current['items'])==[3,7]
    assert current['allocation_pending'] and len(current['listing_ids'])==1


def test_group_snapshot_migration_is_additive_and_idempotent(group_qa):
    from dashboard.procurement.models import init_procurement_database
    import os
    admin=client_for(group_qa)
    legacy=create(admin,group_qa)
    engine=create_engine(os.environ['PROCUREMENT_QA_DATABASE_URL'])
    try:
        with engine.begin() as conn: conn.execute(text('ALTER TABLE procurement.requests DROP COLUMN group_snapshot'))
        init_procurement_database(engine);init_procurement_database(engine)
        result=admin.get(f"/procurement/api/requests/{legacy['id']}")
        assert result.status_code==200,result.json
        assert result.json['group'] is None and result.json['listing_ids']==[101]
        assert result.json['items']==legacy['items']
    finally:
        init_procurement_database(engine);engine.dispose()
