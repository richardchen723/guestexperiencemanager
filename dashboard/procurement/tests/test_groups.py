"""Group intake, frozen scope, explicit allocation and scoped fulfillment."""
from copy import deepcopy

import pytest

from dashboard.procurement.models import Staff
from dashboard.procurement.service import ProcurementService, WorkflowError, item_progress, ticket_status
from dashboard.procurement.tests.test_workflow import service, request_data, purchase, deliver, confirm


@pytest.fixture
def grouped(service):
    service.properties = {key:{**value,'tags':[*value['tags'],'QA group']} for key,value in service.properties.items()}
    return service


def group_request(service, **changes):
    data = request_data(listing_id=None,group_tag='QA group',group_coverage='entire_group',
        items=[{'name':'Toilet paper','unit':'rolls'}])
    data.update(changes)
    return service.create_ticket(data)[0]


def assign(service,ticket,rows,**changes):
    return service.allocate_properties(ticket.id,{'version':ticket.version,'item_id':ticket.items[0].id,
        'allocations':[{'listing_id':p,'quantity':q} for p,q in rows],**changes})


@pytest.mark.parametrize('coverage',['entire_group','assign_later'])
def test_group_only_intake_is_one_need_not_one_copy_per_property(grouped,coverage):
    ticket = group_request(grouped,group_coverage=coverage)
    value = grouped.ticket_dict(ticket)
    assert value['group']['tag']=='QA group' and value['group']['coverage']==coverage
    assert len(value['group']['properties'])==2
    assert value['listing_ids']==[] and len(value['items'])==1
    assert value['items'][0]['listing_id'] is None
    assert value['items'][0]['quantity'] is None
    assert value['missing_information']==[] and 'Needs information' not in value['flags']
    assert value['allocation_pending'] and 'Allocation pending' in value['flags']
    assert value['status']=='new' and value['ready_properties']==0


def test_selected_properties_keep_group_context_without_creating_sibling_needs(grouped):
    ticket=group_request(grouped,group_coverage='selected_properties',listing_ids=[101],
        items=[{'listing_id':101,'name':'Paper','unit':'rolls','quantity':'3'}])
    value=grouped.ticket_dict(ticket)
    assert value['listing_ids']==[101] and not value['allocation_pending']
    assert len(value['items'])==1 and value['items'][0]['quantity']==3


def test_group_membership_is_frozen_for_allocation_and_source_replay(grouped):
    source={'channel':'qa','message_id':'group-source','body':'Need paper for our group'}
    ticket=group_request(grouped,source=source)
    original=deepcopy(ticket.group_snapshot)
    grouped.properties={**grouped.properties,103:{'id':103,'name':'Late addition','tags':['QA group']}}
    grouped.properties[101]={**grouped.properties[101],'tags':[]}
    with pytest.raises(WorkflowError,match='saved group'):
        assign(grouped,ticket,[(103,'2')])
    assign(grouped,ticket,[(101,'2')])
    assert ticket.group_snapshot==original
    replay,found=grouped.create_ticket(request_data(listing_id=None,group_tag='QA group',group_coverage='entire_group',
        items=[{'name':'Different followup','unit':'packs'}],source=source))
    assert found and replay.id==ticket.id and len(ticket.items)==1


@pytest.mark.parametrize('changes,match',[
    ({'group_tag':'Unknown group'},'existing property group'),
    ({'group_coverage':'everything'},'entire group'),
    ({'group_coverage':'selected_properties'},'at least one property'),
    ({'storage_location_id':2},'shared storage'),
])
def test_invalid_group_scope_is_rejected(grouped,changes,match):
    with pytest.raises(WorkflowError,match=match): group_request(grouped,**changes)


def test_group_cannot_include_properties_outside_its_scope(grouped):
    grouped.properties[102]={**grouped.properties[102],'tags':[]}
    with pytest.raises(WorkflowError,match='saved property group'):
        group_request(grouped,listing_ids=[102],items=[{'listing_id':102,'name':'Paper','unit':'rolls'}])


@pytest.mark.parametrize('rows,total,match',[
    ([],None,'between 1 and 100'),
    ([(101,'2'),(101,'3')],None,'only once'),
    ([(101,'0')],None,'positive'),
    ([(101,'-1')],None,'positive'),
    ([(101,'0.0001')],None,'decimal'),
    ([(101,'7')],'6','exceed'),
])
def test_property_allocation_validation_keeps_unassigned_need(grouped,rows,total,match):
    ticket=group_request(grouped)
    with pytest.raises(WorkflowError,match=match): assign(grouped,ticket,rows,total_quantity=total)
    assert ticket.items[0].listing_id is None and ticket.version==1


def test_partial_group_allocations_purchases_and_receipts_never_close_early(grouped):
    ticket=group_request(grouped,items=[{'name':'Toilet paper','unit':'rolls','quantity':'12'}])
    assign(grouped,ticket,[(101,'4')])
    pending=next(i for i in ticket.items if i.listing_id is None)
    first=next(i for i in ticket.items if i.listing_id==101)
    assert pending.quantity==8 and first.quantity==4
    order=purchase(grouped,lines=[{'name':'Toilet paper','unit':'rolls','quantity':'4','unit_price':'1',
        'allocations':[{'item_id':first.id,'quantity':'4'}]}])
    deliver(grouped,order)
    confirm(grouped,ticket,first.allocations[0],listing_id=101)
    value=grouped.ticket_dict(ticket)
    assert value['allocation_pending'] and value['status']=='in_progress' and value['ready_properties']==1
    grouped.allocate_properties(ticket.id,{'version':ticket.version,'item_id':pending.id,
        'allocations':[{'listing_id':102,'quantity':'8'}]})
    assert not grouped.ticket_dict(ticket)['allocation_pending']
    second=purchase(grouped,lines=[{'name':'Toilet paper','unit':'rolls','quantity':'8','unit_price':'1',
        'allocations':[{'item_id':pending.id,'quantity':'8'}]}])
    deliver(grouped,second,'8')
    assert ticket_status(ticket)=='awaiting_confirmation'
    confirm(grouped,ticket,pending.allocations[0],'2',listing_id=102)
    assert item_progress(pending)['remaining']==6 and ticket_status(ticket)=='awaiting_confirmation'
    confirm(grouped,ticket,pending.allocations[0],'6',listing_id=102)
    assert ticket_status(ticket)=='completed' and grouped.ticket_dict(ticket)['ready_properties']==2


def test_cannot_purchase_or_cancel_unassigned_group_quantities(grouped):
    ticket=group_request(grouped,items=[{'name':'Toilet paper','unit':'rolls','quantity':'12'}])
    with pytest.raises(WorkflowError,match='destination and quantity'):
        purchase(grouped,ticket)
    with pytest.raises(WorkflowError,match='Assign properties first'):
        grouped.cancel_item(ticket.id,{'version':ticket.version,'item_id':ticket.items[0].id,'quantity':'2','reason':'test'})


def test_housekeepers_can_submit_within_their_scope_and_only_see_own_pending_requests(grouped):
    scope=grouped.session.get(Staff,3)
    cleaner=ProcurementService(grouped.session,actor='Housekeeper',staff_scope=scope,properties=grouped.properties,users=grouped.users)
    with pytest.raises(WorkflowError,match='outside your property'):
        group_request(cleaner)
    ticket=group_request(cleaner,group_coverage='assign_later')
    value=cleaner.ticket_dict(cleaner.ticket(ticket.id),detail=True)
    assert [p['id'] for p in value['group']['properties']]==[102]
    assert value['listing_ids']==[] and len(value['items'])==1 and value['messages']==[]
    other=group_request(grouped,requester_staff_id=None,requester_name='Private staff intake')
    with pytest.raises(WorkflowError,match='outside your property'): cleaner.ticket(other.id)
    with pytest.raises(WorkflowError,match='Purchasing access'): assign(cleaner,ticket,[(102,'2')])
    assign(grouped,ticket,[(102,'2')])
    assert cleaner.ticket_dict(ticket)['listing_ids']==[102]


def test_group_scope_and_reserved_property_allocations_cannot_be_rewritten(grouped):
    ticket=group_request(grouped)
    assign(grouped,ticket,[(101,'4'),(102,'6')])
    first=ticket.items[0]
    purchase(grouped,lines=[{'name':first.name,'unit':first.unit,'quantity':str(first.quantity),'unit_price':'1',
        'allocations':[{'item_id':first.id,'quantity':str(first.quantity)}]}])
    with pytest.raises(WorkflowError,match='cannot change'):
        grouped.update_ticket(ticket.id,{'version':ticket.version,'group_tag':None})
    with pytest.raises(WorkflowError,match='cannot be rewritten'):
        grouped.update_ticket(ticket.id,{'version':ticket.version,'items':[{'id':first.id,'listing_id':102,'name':first.name,'unit':first.unit,'quantity':'4'}]})
