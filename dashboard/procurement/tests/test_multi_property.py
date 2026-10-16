"""Property-specific requirements, combined orders and independent receipts."""
from datetime import date, datetime, timedelta, timezone
import pytest

from dashboard.procurement.models import Location, Staff
from dashboard.procurement.service import WorkflowError, item_progress, order_totals, ticket_flags, ticket_status
from dashboard.procurement.tests.test_workflow import (
    service, request_data, new_ticket, order_data, purchase, deliver, confirm,
)


def multi(service, **changes):
    data = request_data(listing_id=None, listing_ids=[101, 102], items=[
        {"listing_id": 101, "name": "Toilet paper", "unit": "rolls", "quantity": "4"},
        {"listing_id": 102, "name": "Toilet paper", "unit": "rolls", "quantity": "6"},
    ])
    data.update(changes)
    return service.create_ticket(data)[0]


def combined(service, ticket, **changes):
    data = order_data(lines=[{"name": "Toilet paper", "unit": "rolls", "quantity": "10", "unit_price": "1.00",
                            "allocations": [{"item_id": i.id, "quantity": str(i.quantity)} for i in ticket.items]}])
    data.update(changes)
    return purchase(service, **data)


def test_multi_property_request_keeps_explicit_members_and_per_property_quantities(service):
    ticket = multi(service)
    assert ticket.listing_id is None
    value = service.ticket_dict(ticket, detail=True)
    assert value["listing_ids"] == [101, 102]
    assert [i["listing_id"] for i in value["items"]] == [101, 102]
    assert [i["quantity"] for i in value["items"]] == [4, 6]
    assert value["property_count"] == 2 and value["ready_properties"] == 0
    # Later directory changes never expand the members of an existing request.
    service.properties = {**service.properties, 103: {"id": 103, "name": "Another cabin", "tags": ["cabin"]}}
    assert service.ticket_dict(ticket)["listing_ids"] == [101, 102]


@pytest.mark.parametrize("updates,match", [
    ({"listing_ids": [101, 101]}, "once"),
    ({"listing_ids": [True]}, "valid properties"),
    ({"listing_ids": "101"}, "valid properties"),
    ({"listing_ids": [999]}, "existing property"),
    ({"listing_id": 101}, "omit listing_id"),
    ({"items": [{"name": "Paper", "unit": "rolls", "quantity": "2"}]}, "property for each"),
    ({"items": [{"listing_id": 101, "name": "Paper", "unit": "rolls", "quantity": "2"}]}, "Each selected property"),
    ({"items": [{"listing_id": 999, "name": "Paper", "unit": "rolls", "quantity": "2"}]}, "existing property"),
    ({"listing_ids": [101], "items": [{"listing_id": 102, "name": "Paper", "unit": "rolls", "quantity": "2"}]}, "selected request property"),
    ({"storage_location_id": 2}, "not both"),
    ({"requester_staff_id": 3}, "every selected property"),
])
def test_invalid_property_selections_are_rejected(service, updates, match):
    with pytest.raises(WorkflowError, match=match):
        multi(service, **updates)


def test_source_replay_uses_property_set_not_selection_order(service):
    source = {"channel": "whatsapp:test", "message_id": "multi-original", "body": "Both properties need paper"}
    first = multi(service, source=source)
    again, replayed = service.create_ticket(request_data(listing_id=None, listing_ids=[102,101], source=source,
        items=[{"listing_id": 102, "name": "Paper", "unit": "rolls", "quantity": "1"}]))
    assert replayed and again.id == first.id


def test_partial_purchase_and_confirmation_finish_properties_independently(service):
    ticket = multi(service)
    order = purchase(service, lines=[{"name": "Toilet paper", "unit": "rolls", "quantity": "7", "unit_price": "1.00",
        "allocations": [{"item_id": ticket.items[0].id, "quantity": "4"}, {"item_id": ticket.items[1].id, "quantity": "3"}]}])
    assert [item_progress(i)["to_order"] for i in ticket.items] == [0, 3]
    deliver(service, order, "7")
    assert ticket_status(ticket) == "in_progress"  # Property 102 still needs a purchase.
    confirm(service, ticket, ticket.items[0].allocations[0], "4", listing_id=101)
    value = service.ticket_dict(ticket)
    assert value["ready_properties"] == 1
    assert value["properties"][0]["status"] == "completed"
    assert value["properties"][1]["status"] == "in_progress"
    confirm(service, ticket, ticket.items[1].allocations[0], "2", listing_id=102)
    assert item_progress(ticket.items[1])["remaining"] == 4
    next_order = purchase(service, vendor="Second shop", lines=[{"name": "Toilet paper", "unit": "rolls", "quantity": "3", "unit_price": "2.00",
        "allocations": [{"item_id": ticket.items[1].id, "quantity": "3"}]}])
    deliver(service, next_order)
    confirm(service, ticket, ticket.items[1].allocations[0], "1", listing_id=102)
    confirm(service, ticket, ticket.items[1].allocations[1], "3", listing_id=102)
    assert ticket_status(ticket) == "completed"
    assert service.ticket_dict(ticket)["ready_properties"] == 2


def test_combined_purchase_spans_requests_and_report_costs_stay_with_properties(service):
    ticket = multi(service)
    other = new_ticket(service, items=[{"name":"Toilet paper","unit":"rolls","quantity":"2"}])
    order = purchase(service, tax="1.03", shipping="2.01", lines=[{"name":"Toilet paper","unit":"rolls","quantity":"24","pack_size":"24","unit_price":"17.49",
        "allocations":[{"item_id":i.id,"quantity":str(i.quantity)} for i in ticket.items + other.items]}])
    assert order_totals(order)["total_cents"] == 2053
    allocations = service.order_dict(order)["lines"][0]["allocations"]
    assert [a["listing_id"] for a in allocations] == [101,102,101]
    report = service.report(date.today().strftime('%Y-%m'))['currencies'][0]
    assert {p['listing_id'] for p in report['properties']} == {101,102}
    assert sum(p['allocated_cents'] for p in report['properties']) + report['unallocated_cents'] == 2053
    assert sum(a['allocated_cents'] for a in allocations) == sum(p['allocated_cents'] for p in report['properties'])
    assert report['unallocated_cents'] > 0


def test_batch_receipts_accept_partial_quantities_and_require_assigned_housekeepers(service):
    ticket = multi(service)
    order = combined(service, ticket)
    rows = [{"allocation_id": i.allocations[0].id, "listing_id": i.listing_id, "staff_id": 1, "quantity": "2"} for i in ticket.items]
    service.confirm_batch(ticket.id, {"version":ticket.version,"confirmations":rows,"note":"Both closets checked","evidence":{"reference":"wa-100"},"confirm_delivery":True})
    assert [item_progress(i)['confirmed'] for i in ticket.items] == [2,2]
    assert ticket_status(ticket) == 'in_progress'
    with pytest.raises(WorkflowError, match='assigned'):
        service.confirm_batch(ticket.id, {"version":ticket.version,"confirmations":[{**rows[0],"staff_id":3}],"note":"wrong cleaner","evidence":{"reference":"wa-101"},"confirm_delivery":True})
    with pytest.raises(WorkflowError, match='once'):
        service.confirm_batch(ticket.id, {"version":ticket.version,"confirmations":[rows[0],rows[0]],"note":"duplicate","evidence":{"reference":"wa-102"},"confirm_delivery":True})


def test_housekeeper_shared_request_has_only_own_properties_receipts_and_no_private_history(service):
    ticket = multi(service, requester_staff_id=None, requester_name='Staff intake', source={"body":"Private shared note", "channel":"manual"})
    order = combined(service, ticket)
    deliver(service, order, '10')
    service.staff_scope = service.session.get(Staff, 3)  # Assigned only to 102.
    value = service.ticket_dict(service.ticket(ticket.id), detail=True)
    assert value['listing_ids'] == [102]
    assert len(value['items']) == 1 and value['items'][0]['listing_id'] == 102
    assert value['requester_name'] == 'Shared request' and value['requester_staff_id'] is None
    assert value['messages'] == value['activity'] == value['possible_duplicates'] == []
    assert 'Test address' not in str(value) and 'Searenity Haven' not in str(value)
    with pytest.raises(WorkflowError, match='outside'):
        confirm(service, ticket, ticket.items[0].allocations[0], '1', listing_id=101, staff_id=3)
    with pytest.raises(WorkflowError, match='outside'):
        service.fulfillment_issue(ticket.id, {'version':ticket.version,'allocation_id':ticket.items[0].allocations[0].id,'quantity':'1','kind':'lost','reason':'forbidden'})
    confirm(service,ticket,ticket.items[1].allocations[0],'6',listing_id=102,staff_id=3,evidence={})
    assert service.ticket_dict(ticket)['status'] == 'completed'
    assert ticket_status(ticket) == 'awaiting_confirmation'  # Manager still sees the other property.


def test_confirmation_cannot_cross_properties_inside_the_same_request(service):
    ticket = multi(service)
    combined(service, ticket)
    with pytest.raises(WorkflowError, match='original destination'):
        confirm(service, ticket, ticket.items[0].allocations[0], '1', listing_id=102, confirm_delivery=True)


def test_return_reopens_only_the_property_that_returned_supplies(service):
    ticket = multi(service); order = combined(service,ticket); deliver(service,order,'10')
    for item in ticket.items:
        confirm(service,ticket,item.allocations[0],str(item.quantity),listing_id=item.listing_id)
    assert ticket_status(ticket) == 'completed'
    service.confirm(ticket.id,{'version':ticket.version,'allocation_id':ticket.items[0].allocations[0].id,
        'listing_id':101,'staff_id':1,'quantity':'1','note':'One roll returned','evidence':{'reference':'wa-return'}},returning=True)
    value = service.ticket_dict(ticket)
    assert value['ready_properties'] == 1
    assert value['properties'][1]['status'] == 'completed'
    assert [i['to_order'] for i in value['items']] == [1,0]


def test_reserved_property_members_and_quantities_cannot_be_rewritten(service):
    ticket = multi(service)
    combined(service, ticket)
    with pytest.raises(WorkflowError, match='cannot change'):
        service.update_ticket(ticket.id, {'version':ticket.version,'listing_ids':[101]})
    with pytest.raises(WorkflowError, match='cannot be rewritten'):
        service.update_ticket(ticket.id, {'version':ticket.version,'items':[{'id':ticket.items[0].id,'listing_id':102,'name':'Paper','unit':'rolls','quantity':'8'}]})


def test_unpurchased_property_can_be_removed_with_its_unpurchased_requirements(service):
    ticket = multi(service)
    service.update_ticket(ticket.id, {'version':ticket.version,'listing_ids':[101], 'items':[{'id':ticket.items[1].id,'remove':True}]})
    assert service.ticket_dict(ticket)['listing_ids'] == [101]
    assert len(ticket.items) == 1 and ticket.items[0].listing_id == 101


def test_historical_requester_is_preserved_after_roster_assignments_change(service):
    ticket = multi(service)
    service.session.get(Staff,1).listing_ids = [101]
    service.update_ticket(ticket.id,{'version':ticket.version,'title':'Clarified shared restock','requester_staff_id':1})
    assert ticket.requester_staff_id == 1 and ticket.requester_name == 'Maria'
    assert service.ticket_dict(ticket)['listing_ids'] == [101,102]


def test_unknown_quantities_do_not_become_purchasable_or_complete(service):
    ticket = multi(service, items=[{'listing_id':101,'name':'Toilet paper','unit':'rolls','quantity':'4'}, {'listing_id':102,'name':'Toilet paper','unit':'rolls'}])
    assert 'Needs information' in service.ticket_dict(ticket)['flags']
    with pytest.raises(WorkflowError, match='quantity before'):
        service.save_order(order_data(lines=[{'name':'Toilet paper','unit':'rolls','quantity':'2','unit_price':'1','allocations':[{'item_id':ticket.items[1].id,'quantity':'2'}]}]))


def test_missing_quantity_identifies_its_property_and_housekeepers_only_see_their_portion(service):
    from dashboard.procurement.service import ProcurementService
    ticket = multi(service, items=[{'listing_id':101,'name':'Toilet paper','unit':'rolls'},
                                   {'listing_id':102,'name':'Paper towels','unit':'rolls','quantity':'6'}])
    value = service.ticket_dict(ticket)
    assert value['missing_information'] == [{'field':'quantity','item_id':ticket.items[0].id,'listing_id':101,
        'message':'Enter the quantity for Toilet paper (rolls) at Searenity Haven.'}]
    cleaner = ProcurementService(service.session, actor='Cleaner', staff_scope=service.session.get(Staff,3), properties=service.properties)
    scoped = cleaner.ticket_dict(ticket)
    assert scoped['listing_ids'] == [102] and scoped['missing_information'] == []
    assert 'Needs information' not in scoped['flags']


def multiple_delivery_order(service):
    ticket = multi(service)
    service.session.add(Location(id=3,label='Other receiving address',kind='housekeeper',address='Other QA address',staff_id=3,active=True))
    service.session.flush()
    order = purchase(service, lines=[{'name':'Toilet paper','unit':'rolls','quantity':str(i.quantity),'unit_price':'1','location_id':location,
        'allocations':[{'item_id':i.id,'quantity':str(i.quantity)}]} for i,location in zip(ticket.items,[1,3])])
    return ticket,order


def test_multiple_destinations_require_separate_shipments_and_approvals_capture_each_address(service):
    ticket,order = multiple_delivery_order(service)
    assert len(service.order_dict(order)['destinations']) == 2
    assert len(order.approval['snapshot']['destinations']) == 2
    with pytest.raises(WorkflowError,match='one delivery destination'):
        service.create_shipment(order.id, {'version':order.version,'lines':[{'line_id':l.id,'quantity':str(l.quantity)} for l in order.lines]})
    # Invalid business writes are rolled back by the endpoint. This direct test
    # uses a fresh fixture for the independent successful shipment test below.


def test_delivery_to_one_destination_does_not_deliver_the_other(service):
    ticket,order = multiple_delivery_order(service)
    service.create_shipment(order.id, {'version':order.version,'lines':[{'line_id':order.lines[0].id,'quantity':'4'}]})
    service.deliver_shipment(order.shipments[0].id, {'version':order.version})
    assert [item_progress(i)['delivered'] for i in ticket.items] == [4,0]
    confirm(service,ticket,ticket.items[0].allocations[0],'4',listing_id=101)
    assert service.ticket_dict(ticket)['ready_properties'] == 1


def test_delivery_flags_do_not_leak_from_another_propertys_shipment(service):
    ticket,order = multiple_delivery_order(service)
    service.create_shipment(order.id, {'version':order.version,'expected_at':(datetime.now(timezone.utc)-timedelta(days=1)).isoformat(),
                                       'lines':[{'line_id':order.lines[0].id,'quantity':'4'}]})
    assert 'Delivery delayed' in ticket_flags(ticket)
    assert 'Delivery delayed' in ticket_flags(ticket,items=[ticket.items[0]])
    assert 'Delivery delayed' not in ticket_flags(ticket,items=[ticket.items[1]])


def test_changed_second_address_requires_reapproval(service):
    service.session.add(Location(id=3,label='Other address',kind='storage',address='Original address',active=True));service.session.flush()
    order = service.save_order(order_data(lines=[{'name':'Paper','unit':'rolls','quantity':'1','unit_price':'1','location_id':location} for location in [1,3]]))
    service.order_action(order.id,'approve',{'version':order.version})
    service.session.get(Location,3).address = 'Changed address'
    with pytest.raises(WorkflowError,match='changed after approval'):
        service.order_action(order.id,'place',{'version':order.version,'reference':'multi-address','purchased_on':date.today().isoformat()})


def test_partial_retail_packs_are_not_split_between_destinations(service):
    with pytest.raises(WorkflowError,match='whole packs'):
        service.save_order(order_data(lines=[{'name':'Paper','unit':'rolls','quantity':'12','pack_size':'24','unit_price':'17.49'}]))
