"""Business-level regression tests; all data is isolated from Cotton Candy."""

from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker

from dashboard.procurement.models import (
    AuditEvent, Base, CatalogItem, Location, PurchaseOrder, Staff,
)
from dashboard.procurement.service import (
    ProcurementService, WorkflowError, cents, item_progress, order_totals,
    quantity, split_cents, ticket_flags, ticket_status,
)


PROPERTIES = {
    101: {"id": 101, "name": "Searenity Haven", "city": "Seattle", "timezone": "America/Los_Angeles", "tags": ["waterfront"]},
    102: {"id": 102, "name": "Appleseed Cabin", "city": "Atlanta", "timezone": "America/New_York", "tags": ["cabin"]},
}
USERS = {1: {"id": 1, "name": "Purchasing Manager"}, 2: {"id": 2, "name": "Regional Manager"}}


@pytest.fixture
def service():
    engine = create_engine("sqlite://", execution_options={"schema_translate_map": {"procurement": None}})
    @event.listens_for(engine, "connect")
    def foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add_all([
        Staff(id=1, name="Maria", user_id=3, listing_ids=[101, 102], active=True),
        Staff(id=2, name="Inactive housekeeper", listing_ids=[101], active=False),
        Staff(id=3, name="Other cleaner", user_id=4, listing_ids=[102], active=True),
    ])
    session.flush()
    session.add_all([
        Location(id=1, label="Maria supply storage", kind="housekeeper", address="Test address", staff_id=1, active=True),
        Location(id=2, label="Regional supply closet", kind="storage", address="Test storage", active=True),
    ])
    session.commit()
    yield ProcurementService(session, actor="Purchasing Manager", user_id=1, can_approve=True, properties=PROPERTIES, users=USERS)
    session.close()
    engine.dispose()


def request_data(**updates):
    data = {"title": "Supply restock", "listing_id": 101, "requester_staff_id": 1,
            "required_by": "2027-01-02T10:00:00-08:00", "owner_user_id": 1,
            "items": [{"name": "Toilet paper", "unit": "rolls", "quantity": "4"}]}
    data.update(updates)
    return data


def new_ticket(service, **updates):
    return service.create_ticket(request_data(**updates))[0]


def order_data(ticket=None, **updates):
    data = {"vendor": "Test Retailer", "location_id": 1, "currency": "USD", "tax": "1.00", "shipping": "2.00", "discount": "0.00",
            "lines": [{"name": "Toilet paper", "unit": "rolls", "quantity": "4", "unit_price": "1.00",
                       "allocations": [{"item_id": ticket.items[0].id, "quantity": "4"}] if ticket else []}]}
    data.update(updates)
    return data


def purchase(service, ticket=None, **updates):
    order = service.save_order(order_data(ticket, **updates))
    service.order_action(order.id, "approve", {"version": order.version})
    service.order_action(order.id, "place", {"version": order.version, "reference": f"TEST-{order.id}", "purchased_on": date.today().isoformat()})
    service.session.flush()
    return order


def deliver(service, order, qty=None):
    service.create_shipment(order.id, {"version": order.version, "lines": [{"line_id": order.lines[0].id, "quantity": qty or order.lines[0].quantity}]})
    service.session.flush()
    service.deliver_shipment(order.shipments[-1].id, {"version": order.version})
    return order


def confirm(service, ticket, allocation=None, qty="4", **updates):
    allocation = allocation or ticket.items[0].allocations[0]
    data = {"version": ticket.version, "allocation_id": allocation.id, "quantity": qty, "listing_id": ticket.listing_id or 101,
            "staff_id": 1, "note": "Maria confirmed usable supplies in the property closet.", "evidence": {"reference": "whatsapp:message-42"}}
    data.update(updates)
    return service.confirm(ticket.id, data)


def test_delivery_never_completes_request_without_property_confirmation(service):
    ticket = new_ticket(service)
    assert ticket_status(ticket) == "new"
    order = purchase(service, ticket)
    assert ticket_status(ticket) == "in_progress"
    deliver(service, order)
    assert ticket_status(ticket) == "awaiting_confirmation"
    assert item_progress(ticket.items[0])["confirmed"] == 0
    confirm(service, ticket)
    assert ticket_status(ticket) == "completed"
    assert not order.reconciled_at  # Operations can finish while finance remains open.
    assert service.session.scalars(select(AuditEvent).where(AuditEvent.action == "property_confirmed")).one().detail["housekeeper"] == "Maria"


def test_partial_shipments_and_partial_confirmations_stay_open(service):
    ticket = new_ticket(service)
    order = purchase(service, ticket)
    deliver(service, order, "2")
    confirm(service, ticket, qty="1")
    assert ticket_status(ticket) == "in_progress"
    assert item_progress(ticket.items[0])["remaining"] == 3
    confirm(service, ticket, qty="1")
    deliver(service, order, "2")
    assert ticket_status(ticket) == "awaiting_confirmation"
    confirm(service, ticket, qty="2")
    assert ticket_status(ticket) == "completed"


def test_request_can_be_fulfilled_by_multiple_retailers(service):
    ticket = new_ticket(service)
    data = order_data(ticket)
    data["lines"][0]["quantity"] = "2"
    data["lines"][0]["allocations"][0]["quantity"] = "2"
    first = purchase(service, **data)
    second = purchase(service, **{**data, "vendor": "Second Retailer"})
    deliver(service, first)
    deliver(service, second)
    for allocation in ticket.items[0].allocations:
        confirm(service, ticket, allocation, "2")
    assert ticket_status(ticket) == "completed"


def test_one_bulk_line_can_supply_two_property_requests_without_double_delivery(service):
    a = new_ticket(service, items=[{"name": "Toilet paper", "unit": "rolls", "quantity": "2"}])
    b = new_ticket(service, listing_id=102, items=[{"name": "Toilet paper", "unit": "rolls", "quantity": "2"}])
    data = order_data()
    data["lines"][0]["allocations"] = [{"item_id": a.items[0].id, "quantity": "2"}, {"item_id": b.items[0].id, "quantity": "2"}]
    order = purchase(service, **data)
    deliver(service, order, "2")
    confirm(service, a, qty="2")
    with pytest.raises(WorkflowError, match="only delivered"):
        confirm(service, b, qty="2")
    deliver(service, order, "2")
    confirm(service, b, qty="2")
    assert ticket_status(a) == ticket_status(b) == "completed"
    report = service.report(date.today().strftime("%Y-%m"))["currencies"][0]
    assert sum(p["allocated_cents"] for p in report["properties"]) == 700
    assert sum(p["confirmed_cents"] for p in report["properties"]) == 700


def test_shared_stock_remains_unallocated_until_later_request(service):
    order = purchase(service)
    deliver(service, order)
    report = service.report(date.today().strftime("%Y-%m"))["currencies"][0]
    assert report["unallocated_cents"] == 700
    ticket = new_ticket(service)
    service.add_allocation(order.id, {"version": order.version, "line_id": order.lines[0].id, "item_id": ticket.items[0].id, "quantity": "4"})
    confirm(service, ticket)
    report = service.report(date.today().strftime("%Y-%m"))["currencies"][0]
    assert report["unallocated_cents"] == 0
    assert report["properties"][0]["allocated_cents"] == 700


def test_shared_storage_request_requires_eventual_confirmation_at_real_property(service):
    ticket = new_ticket(service, listing_id=None, storage_location_id=2)
    order = purchase(service, ticket)
    deliver(service, order)
    assert ticket_status(ticket) == "awaiting_confirmation"
    assert service.report(date.today().strftime("%Y-%m"))["currencies"][0]["unallocated_cents"] == 700
    confirm(service, ticket, listing_id=102)
    report = service.report(date.today().strftime("%Y-%m"))["currencies"][0]
    assert report["properties"][0]["listing_id"] == 102
    assert report["properties"][0]["confirmed_cents"] == 700


def test_missing_property_and_date_are_visible_but_quantity_is_optional(service):
    ticket = new_ticket(service, listing_id=None, required_by=None, items=[{"name": "Paper towels", "unit": "rolls"}])
    assert "Needs information" in ticket_flags(ticket)
    assert ticket_status(ticket) == "new"
    missing = service.ticket_dict(ticket)["missing_information"]
    assert [entry["field"] for entry in missing] == ["property", "required_by"]
    with pytest.raises(WorkflowError, match="destination and quantity"):
        service.save_order(order_data(ticket))


@pytest.mark.parametrize('provided', [{}, {'quantity': None}])
def test_request_without_quantity_is_valid_and_waits_for_staff_planning(service, provided):
    ticket = new_ticket(service, items=[{'name':'Toilet paper','unit':'rolls',**provided}])
    value = service.ticket_dict(ticket)
    assert value['missing_information'] == [] and 'Needs information' not in value['flags']
    assert value['items'][0]['quantity'] is None
    assert value['items'][0]['requested'] is None
    assert value['status'] == 'new' and value['ready_properties'] == 0
    assert value['fulfilled_items'] == 0


def test_missing_information_clears_after_clarification_and_keeps_other_flags(service):
    ticket = new_ticket(service, required_by=None, owner_user_id=None)
    value = service.ticket_dict(ticket)
    assert value["missing_information"] == [{"field": "required_by", "message": "Set the needed-by date and time."}]
    assert {"Needs information", "Unassigned"} <= set(value["flags"])
    service.update_ticket(ticket.id, {"version": ticket.version, "required_by": "2027-01-02T10:00:00-08:00"})
    value = service.ticket_dict(ticket)
    assert value["missing_information"] == [] and "Needs information" not in value["flags"]
    assert "Unassigned" in value["flags"]


def test_shared_storage_and_closed_requests_do_not_report_unneeded_information(service):
    ticket = new_ticket(service, listing_id=None, storage_location_id=2, required_by=None)
    assert [entry["field"] for entry in service.ticket_dict(ticket)["missing_information"]] == ["required_by"]
    service.cancel_ticket(ticket.id, {"version": ticket.version, "reason": "No longer needed"})
    assert service.ticket_dict(ticket)["missing_information"] == []
    complete = new_ticket(service, required_by=None)
    order = purchase(service, complete)
    deliver(service, order)
    confirm(service, complete)
    assert service.ticket_dict(complete)["status"] == "completed"
    assert service.ticket_dict(complete)["missing_information"] == []


def test_source_replay_returns_original_but_similar_messages_are_not_silently_merged(service):
    source = {"channel": "whatsapp", "message_id": "wamid-1", "body": "Need toilet paper"}
    first, replay = service.create_ticket(request_data(source=source))
    assert replay is False
    second, replay = service.create_ticket(request_data(source=source))
    assert second.id == first.id and replay is True
    third, replay = service.create_ticket(request_data(source={**source, "message_id": "wamid-2"}))
    assert third.id != first.id and not replay
    assert service.ticket_dict(first, detail=True)["possible_duplicates"][0]["id"] == third.id


def test_same_message_can_be_split_into_requests_for_two_properties(service):
    source = {"channel": "whatsapp", "message_id": "wamid-multiple", "body": "Supplies for both homes"}
    first, _ = service.create_ticket(request_data(source=source))
    second, replay = service.create_ticket(request_data(listing_id=102, source=source))
    assert first.id != second.id and not replay


def test_revision_invalidates_approval_and_preserves_historical_snapshot(service):
    ticket = new_ticket(service)
    order = service.save_order(order_data(ticket))
    service.order_action(order.id, "approve", {"version": order.version})
    original_approval = dict(order.approval)
    assert original_approval["snapshot"]["destination"]["address"] == "Test address"
    data = order_data(ticket, version=order.version, change_reason="Price increased")
    data["lines"][0]["unit_price"] = "2.00"
    service.save_order(data, order.id)
    assert order.state == "draft" and order.approval is None
    with pytest.raises(WorkflowError, match="Approve the current"):
        service.order_action(order.id, "place", {"version": order.version})
    assert original_approval["snapshot"]["total_cents"] == 700


def test_address_change_or_inactive_housekeeper_prevents_placing_approved_order(service):
    order = service.save_order(order_data(new_ticket(service)))
    service.order_action(order.id, "approve", {"version": order.version})
    service.session.get(Location, 1).address = "New address"
    with pytest.raises(WorkflowError, match="address changed"):
        service.order_action(order.id, "place", {"version": order.version, "reference": "x", "purchased_on": date.today().isoformat()})
    assert order.destination["address"] == "Test address"
    service.session.get(Staff, 1).active = False
    with pytest.raises(WorkflowError, match="inactive"):
        service.order_action(order.id, "place", {"version": order.version})


@pytest.mark.parametrize("action", ["approve", "place", "cancel", "reconcile"])
def test_stale_order_edits_cannot_overwrite_newer_work(service, action):
    order = service.save_order(order_data(new_ticket(service)))
    with pytest.raises(WorkflowError, match="changed since") as error:
        service.order_action(order.id, action, {"version": order.version - 1})
    assert error.value.status == 409


def test_purchase_requires_admin_approval_and_api_cannot_approve(service):
    order = service.save_order(order_data(new_ticket(service)))
    service.can_approve = False
    with pytest.raises(WorkflowError, match="administrator"):
        service.order_action(order.id, "approve", {"version": order.version})
    with pytest.raises(WorkflowError, match="Approve the current"):
        service.order_action(order.id, "place", {"version": order.version})


def test_cancel_draft_releases_reserved_need_and_reason_is_audited(service):
    ticket = new_ticket(service)
    order = service.save_order(order_data(ticket))
    assert item_progress(ticket.items[0])["to_order"] == 0
    service.order_action(order.id, "cancel", {"version": order.version, "reason": "Wrong retailer"})
    assert item_progress(ticket.items[0])["to_order"] == 4
    service.cancel_ticket(ticket.id, {"version": ticket.version, "reason": "Duplicate request confirmed"})
    assert ticket_status(ticket) == "cancelled"


def test_cancel_remainder_can_resolve_partially_fulfilled_request(service):
    ticket = new_ticket(service)
    data = order_data(ticket)
    data["lines"][0]["quantity"] = "2"
    data["lines"][0]["allocations"][0]["quantity"] = "2"
    order = purchase(service, **data)
    deliver(service, order)
    confirm(service, ticket, qty="2")
    service.cancel_item(ticket.id, {"version": ticket.version, "item_id": ticket.items[0].id, "quantity": "2", "reason": "Housekeeper confirmed existing stock is sufficient"})
    assert ticket_status(ticket) == "completed"


def test_return_reopens_need_and_refund_is_recorded_independently(service):
    ticket = new_ticket(service)
    order = purchase(service, ticket)
    deliver(service, order)
    confirm(service, ticket)
    service.confirm(ticket.id, {"version": ticket.version, "allocation_id": ticket.items[0].allocations[0].id,
                               "quantity": "1", "listing_id": 101, "staff_id": 1, "note": "Defective roll returned", "evidence": {"reference": "wamid-return"}}, returning=True)
    assert ticket_status(ticket) != "completed"
    assert item_progress(ticket.items[0])["to_order"] == 1
    service.refund(order.id, {"version": order.version, "line_id": order.lines[0].id, "amount": "1.75", "reason": "Refund received", "refunded_on": date.today().isoformat()})
    report = service.report(date.today().strftime("%Y-%m"))["currencies"][0]
    assert report["net_cents"] == 525
    assert report["properties"][0]["allocated_cents"] == 525
    assert item_progress(ticket.items[0])["remaining"] == 1


def test_overordering_overallocation_overshipping_and_overconfirmation_are_rejected(service):
    ticket = new_ticket(service)
    order = purchase(service, ticket)
    with pytest.raises(WorkflowError, match="unreserved"):
        service.save_order(order_data(ticket))
    deliver(service, order)
    with pytest.raises(WorkflowError, match="exceed"):
        service.create_shipment(order.id, {"version": order.version, "lines": [{"line_id": order.lines[0].id, "quantity": "1"}]})
    with pytest.raises(WorkflowError, match="only delivered"):
        confirm(service, ticket, qty="5")


def test_confirmation_requires_correct_active_housekeeper_and_evidence(service):
    ticket = new_ticket(service)
    order = purchase(service, ticket)
    deliver(service, order)
    with pytest.raises(WorkflowError, match="assigned"):
        confirm(service, ticket, staff_id=3)
    with pytest.raises(WorkflowError, match="active"):
        confirm(service, ticket, staff_id=2)
    with pytest.raises(WorkflowError, match="reference"):
        confirm(service, ticket, evidence={})
    with pytest.raises(WorkflowError, match="original destination"):
        confirm(service, ticket, listing_id=102)


def test_housekeeper_can_only_access_assigned_requests_and_confirm_as_self(service):
    a = new_ticket(service)
    b = new_ticket(service, listing_id=102)
    order = purchase(service, b)
    deliver(service, order)
    service.staff_scope = service.session.get(Staff, 3)
    with pytest.raises(WorkflowError, match="outside"):
        service.ticket(a.id)
    with pytest.raises(WorkflowError, match="Purchasing access"):
        service.save_order(order_data())
    with pytest.raises(WorkflowError, match="yourself"):
        confirm(service, b, staff_id=1)
    confirm(service, b, staff_id=3, evidence={})
    assert ticket_status(b) == "completed"


def test_substitution_and_standard_unit_mismatches_require_resolution(service):
    ticket = new_ticket(service)
    data = order_data(ticket)
    data["lines"][0]["unit"] = "packs"
    with pytest.raises(WorkflowError, match="units must match"):
        service.save_order(data)
    service.session.rollback()


@pytest.mark.parametrize("value", ["0", "-1", "NaN", "Infinity", "0.0001", "100000000", True, None])
def test_invalid_quantities_are_rejected(value):
    with pytest.raises(WorkflowError):
        quantity(value)


@pytest.mark.parametrize("value", ["-1", "NaN", "Infinity", "1.001", True])
def test_invalid_money_is_rejected(value):
    with pytest.raises(WorkflowError):
        cents(value)


@pytest.mark.parametrize("total,weights", [(1,[1,1,1]), (100,[1,2,3]), (8265,[1998,1749,1850,1500]), (-101,[2,2,2]), (7,[Decimal('.001'),Decimal('.002')])])
def test_cent_allocation_preserves_exact_totals(total, weights):
    assert sum(split_cents(total, weights)) == total
    assert split_cents(total, weights) == split_cents(total, weights)


def test_multiple_currencies_are_never_summed_together(service):
    purchase(service, currency="USD")
    purchase(service, currency="MYR", vendor="MYR retailer")
    report = service.report(date.today().strftime("%Y-%m"))
    assert {c["currency"] for c in report["currencies"]} == {"USD", "MYR"}
    assert all(c["net_cents"] == 700 for c in report["currencies"])


def test_staff_response_and_required_by_deadlines_are_distinct(service):
    ticket = new_ticket(service, priority="critical")
    assert ticket.response_due_at < datetime.utcnow() + timedelta(minutes=31)
    ticket.response_due_at = datetime.utcnow() - timedelta(minutes=1)
    assert "Staff response overdue" in ticket_flags(ticket)
    service.update_ticket(ticket.id, {"version": ticket.version, "review": True})
    assert "Staff response overdue" not in ticket_flags(ticket)
    ticket.required_by = datetime.utcnow() - timedelta(minutes=1)
    assert "Overdue" in ticket_flags(ticket)


def test_additional_item_reopens_completed_request_without_changing_purchased_items(service):
    ticket = new_ticket(service)
    order = purchase(service, ticket)
    deliver(service, order)
    confirm(service, ticket)
    service.update_ticket(ticket.id, {"version": ticket.version, "items": [{"name": "Trash bags", "unit": "bags", "quantity": "20"}]})
    assert ticket_status(ticket) == "in_progress"
    assert len(ticket.items) == 2


def test_invoice_must_be_attached_and_final_charged_amount_must_match(service):
    from dashboard.procurement.models import Attachment
    order = purchase(service)
    with pytest.raises(WorkflowError, match="attach its receipt"):
        service.order_action(order.id, "reconcile", {"version": order.version, "actual_charged": "7.00"})
    service.session.add(Attachment(order=order, name="receipt.pdf", mime_type="application/pdf", content=b"%PDF-test"))
    with pytest.raises(WorkflowError, match="differs"):
        service.order_action(order.id, "reconcile", {"version": order.version, "actual_charged": "8.00"})
    service.order_action(order.id, "reconcile", {"version": order.version, "actual_charged": "7.00"})
    assert order.actual_charged_cents == 700 and order.reconciled_at


def test_receipt_cost_corrections_require_approval_and_preserve_original_purchase(service):
    from dashboard.procurement.models import Attachment
    order = purchase(service, new_ticket(service))
    approved_total = order.approval["snapshot"]["total_cents"]
    service.session.add(Attachment(order=order, name="invoice.pdf", mime_type="application/pdf", content=b"%PDF-invoice"))
    service.can_approve = False
    with pytest.raises(WorkflowError, match="administrator"):
        service.order_action(order.id,"adjust-costs",{"version":order.version,"tax":"1.25","reason":"Final tax"})
    service.can_approve = True
    service.order_action(order.id,"adjust-costs",{"version":order.version,"tax":"1.25","reason":"Final receipt tax differs"})
    assert order_totals(order)["total_cents"] == 725
    assert order.approval["snapshot"]["total_cents"] == approved_total == 700
    service.order_action(order.id,"reconcile",{"version":order.version,"actual_charged":"7.25"})
    assert order.actual_charged_cents == 725


def test_receipt_attachment_does_not_invalidate_purchase_approval(service):
    from dashboard.procurement.models import Attachment
    order = service.save_order(order_data(new_ticket(service)))
    service.order_action(order.id,"approve",{"version":order.version})
    service.session.add(Attachment(order=order,name="receipt.pdf",mime_type="application/pdf",content=b"%PDF-receipt"))
    order.version += 1
    service.order_action(order.id,"place",{"version":order.version,"reference":"TEST-RECEIPT","purchased_on":date.today().isoformat()})
    assert order.state == "ordered"


@pytest.mark.parametrize("delivered_first", [False, True])
def test_unreceived_or_damaged_supplies_release_replacement_need(service, delivered_first):
    ticket = new_ticket(service)
    order = purchase(service,ticket)
    if delivered_first:
        deliver(service,order)
    allocation = ticket.items[0].allocations[0]
    service.fulfillment_issue(ticket.id,{"version":ticket.version,"allocation_id":allocation.id,"quantity":"4",
                                         "kind":"damaged" if delivered_first else "lost","reason":"Housekeeper reported unusable or missing supplies."})
    assert item_progress(ticket.items[0])["to_order"] == 4
    assert item_progress(ticket.items[0])["confirmed"] == 0
    assert "Fulfillment issue" in ticket_flags(ticket)
    replacement = purchase(service,ticket,vendor="Replacement retailer")
    deliver(service,replacement)
    confirm(service,ticket,allocation=ticket.items[0].allocations[-1])
    assert ticket_status(ticket) == "completed"


def test_problem_cannot_erase_already_confirmed_supplies(service):
    ticket = new_ticket(service)
    order = purchase(service,ticket)
    deliver(service,order)
    confirm(service,ticket)
    with pytest.raises(WorkflowError,match="already confirmed"):
        service.fulfillment_issue(ticket.id,{"version":ticket.version,"allocation_id":ticket.items[0].allocations[0].id,
                                             "quantity":"1","kind":"damaged","reason":"Needs a return"})


def test_report_only_includes_activity_in_selected_month_and_separates_later_refunds(service):
    ticket = new_ticket(service)
    order = purchase(service,ticket)
    order.purchased_on = date(2025,1,10)
    assert service.report("2025-02")["currencies"] == []
    service.refund(order.id,{"version":order.version,"line_id":order.lines[0].id,"amount":"1.00","reason":"Later refund","refunded_on":"2025-02-04"})
    january = service.report("2025-01")["currencies"][0]
    february = service.report("2025-02")["currencies"][0]
    assert january["purchased_cents"] == 700 and january["refund_cents"] == 0
    assert february["purchased_cents"] == 0 and february["refund_cents"] == 100
    assert february["net_cents"] == -100
    assert february["properties"][0]["allocated_cents"] == -100


def test_housekeeper_can_confirm_physical_receipt_before_carrier_scan(service):
    ticket = new_ticket(service)
    purchase(service,ticket)
    confirm(service,ticket,confirm_delivery=True)
    assert ticket_status(ticket) == "completed"
    assert item_progress(ticket.items[0])["delivered"] == 4


def test_direct_receipt_at_second_property_does_not_duplicate_the_delivery_pool(service):
    a = new_ticket(service,items=[{"name":"Toilet paper","unit":"rolls","quantity":"2"}])
    b = new_ticket(service,listing_id=102,items=[{"name":"Toilet paper","unit":"rolls","quantity":"2"}])
    data = order_data()
    data["lines"][0]["allocations"] = [{"item_id":a.items[0].id,"quantity":"2"},{"item_id":b.items[0].id,"quantity":"2"}]
    purchase(service,**data)
    confirm(service,b,qty="2",confirm_delivery=True)
    assert item_progress(b.items[0])["delivered"] == 2
    assert item_progress(a.items[0])["delivered"] == 0


def test_tracking_can_be_updated_and_cancelled_without_erasing_history(service):
    ticket = new_ticket(service)
    order = purchase(service,ticket)
    service.create_shipment(order.id,{"version":order.version,"lines":[{"line_id":order.lines[0].id,"quantity":"4"}]})
    shipment = order.shipments[0]
    service.update_shipment(shipment.id,{"version":order.version,"tracking":"Corrected tracking","expected_at":"2027-01-10T12:00:00Z"})
    assert shipment.tracking == "Corrected tracking" and "Delivery at risk" in ticket_flags(ticket)
    service.update_shipment(shipment.id,{"version":order.version,"cancelled_reason":"Retailer replaced tracking"})
    assert shipment.cancelled_reason
    assert service.order_dict(order)["lines"][0]["unshipped"] == 4
    with pytest.raises(WorkflowError,match="Cancelled shipments"):
        service.deliver_shipment(shipment.id,{"version":order.version})
    assert "Delivery at risk" not in ticket_flags(ticket)


def test_large_monetary_values_do_not_overflow_database_integers(service):
    order = purchase(service,lines=[{"name":"Bulk supplies","unit":"lots","quantity":"1","unit_price":"30000000.00"}],tax="0",shipping="0")
    assert order_totals(order)["total_cents"] == 3000000000


def test_receipt_content_is_not_loaded_in_purchase_lists(service):
    from dashboard.procurement.models import Attachment
    from dashboard.procurement.service import order_query
    from sqlalchemy import inspect
    order = purchase(service)
    attachment = Attachment(order=order,name="receipt.pdf",mime_type="application/pdf",content=b"%PDF-private")
    service.session.add(attachment)
    service.session.commit()
    service.session.expunge_all()
    fetched = service.session.scalars(order_query()).one()
    assert "content" in inspect(fetched.attachments[0]).unloaded


def test_pack_pricing_preserves_actual_receipt_without_fractional_cent_unit_prices(service):
    a = new_ticket(service,items=[{"name":"Toilet paper","unit":"rolls","quantity":"12"}])
    b = new_ticket(service,listing_id=102,items=[{"name":"Toilet paper","unit":"rolls","quantity":"12"}])
    order = purchase(service,tax="0",shipping="0",lines=[{"name":"Toilet paper","unit":"rolls","quantity":"24","pack_size":"24","unit_price":"17.49",
        "allocations":[{"item_id":a.items[0].id,"quantity":"12"},{"item_id":b.items[0].id,"quantity":"12"}]}])
    assert order_totals(order)["total_cents"] == 1749
    report = service.report(date.today().strftime("%Y-%m"))["currencies"][0]
    assert sorted(p["allocated_cents"] for p in report["properties"]) == [874,875]
    assert report["net_cents"] == 1749


def test_catalog_units_cannot_be_silently_changed_and_purchased_product_names_are_preserved(service):
    catalog = CatalogItem(name="Toilet paper",unit="rolls",category="paper products",active=True)
    service.session.add(catalog)
    service.session.flush()
    ticket = new_ticket(service,items=[{"catalog_id":catalog.id,"quantity":"4"}])
    with pytest.raises(WorkflowError,match="never convert silently"):
        service.catalog_fields({"catalog_id":catalog.id,"name":"Toilet paper","unit":"packs"})
    order = purchase(service,ticket,lines=[{"catalog_id":catalog.id,"name":"Premium recycled toilet paper","unit":"rolls","quantity":"4","unit_price":"1.00",
        "substitution_reason":"Administrator approved an equivalent product","allocations":[{"item_id":ticket.items[0].id,"quantity":"4"}]}])
    assert order.lines[0].name == "Premium recycled toilet paper"
    assert order.approval["snapshot"]["lines"][0]["name"] == "Premium recycled toilet paper"
