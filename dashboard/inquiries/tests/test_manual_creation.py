"""Manual staff capture uses the same access, workflow and audit protections."""
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from dashboard.inquiries.models import Activity, Guest, Lead, SourceMessage
from dashboard.inquiries.service import InquiryError
from .test_api import app, client, post


def manual(**fields):
    return {"creation_id": str(uuid4()), "guest_name": "Manual Guest", "phone": "+1 425 555 0101", **fields}


def test_phone_only_manual_lead_preserves_provenance_and_staff_fields(service, db):
    result = service.create(manual(listing_id=101, details="Guest called about a family stay.",
                                   budget="USD 1,200", guest_count=4))
    assert result["source"] == "manual" and result["status"] == "New"
    assert result["guest_name"] == "Manual Guest" and result["email"] is None
    assert result["qualification"] == {"budget": "USD 1,200", "guest_count": 4}
    assert result["property_label"] == "Fair Haven"
    assert result["updated_by"] == "Emma"
    assert result["received_at"] == service.now.isoformat()
    assert result["last_inbound_at"] is None and result["last_contact_at"] is None
    assert result["original_notes"] == result["details"]
    assert result["messages"] == [] and result["needs_reply"] is False
    assert len(result["activities"]) == 1
    assert result["activities"][0]["actor"] == "Emma"
    assert result["activities"][0]["action"] == "Lead created manually"
    assert {"listing_id", "details", "budget", "guest_count"} <= set(db.get(Lead, result["id"]).staff_fields)
    assert db.scalar(select(func.count()).select_from(SourceMessage)) == 0


@pytest.mark.parametrize("fields, message", [
    ({"guest_name": "  "}, "Guest name is required"),
    ({"phone": "  "}, "email address or phone number"),
    ({"email": "invalid"}, "valid guest email"),
    ({"check_in": "2026-11-20", "check_out": "2026-11-19"}, "Checkout must be after"),
    ({"listing_id": 101, "tag_id": 1}, "property or a tag"),
    ({"assigned_user_id": 9999}, "available assigned user"),
    ({"status": "Follow-up Required"}, "responsible agent"),
    ({"source": "email"}, "Unknown inquiry fields"),
    ({"creation_id": "bad"}, "valid creation_id"),
    ({"internal_test": "yes"}, "must be a boolean"),
])
def test_invalid_manual_capture_does_not_create_partial_records(service, db, fields, message):
    with pytest.raises(InquiryError, match=message):
        service.create(manual(**fields))
    assert db.scalar(select(func.count()).select_from(Lead)) == 0
    assert db.scalar(select(func.count()).select_from(Guest)) == 0
    assert db.scalar(select(func.count()).select_from(Activity)) == 0


def test_same_guest_can_have_separate_manual_trips_and_retry_keeps_staff_edits(service, db):
    data = manual(email="GUEST@example.invalid")
    first = service.create(data)
    service.patch(first["id"], {"version": 1, "internal_notes": "Follow up tomorrow"})
    retried = service.create(data)
    assert retried["id"] == first["id"] and retried["replayed"] is True
    assert retried["internal_notes"] == "Follow up tomorrow" and retried["version"] == 2
    second = service.create({**data, "creation_id": str(uuid4())})
    assert second["id"] != first["id"] and second["guest_id"] == first["guest_id"]
    with pytest.raises(InquiryError) as conflict:
        service.create({**data, "guest_name": "Changed submission"})
    assert conflict.value.status == 409
    assert db.scalar(select(func.count()).select_from(Lead)) == 2


def test_manual_endpoint_enforces_access_csrf_and_test_record_permissions(app, factory):
    data = manual()
    assert app.test_client().post('/inquiries/api/leads', json=data).status_code == 401
    for role in ('pending', 'denied'):
        assert post(client(app, role), 'leads', data).status_code == 403
    agent = client(app, 'agent')
    assert post(agent, 'leads', data, csrf=False).status_code == 403
    assert post(agent, 'leads', []).status_code == 400
    assert post(agent, 'leads', manual(internal_test=True)).status_code == 403
    response = post(agent, 'leads', manual(email='guest@example.invalid', assigned_user_id=app.qa_users['agent'],
                    status='Follow-up Required', next_action='Call guest', follow_up_at='2026-11-01T10:30:00-08:00'))
    assert response.status_code == 200 and response.json['updated_by'] == 'Agent'
    listing = agent.get('/inquiries/api/leads').json
    assert listing['total'] == 1 and listing['stats']['follow_up'] == 1
    admin = client(app)
    assert post(admin, 'leads', manual(internal_test=True)).status_code == 200
    assert admin.get('/inquiries/api/leads').json['total'] == 1
    tests = admin.get('/inquiries/api/leads?record_scope=internal-tests').json
    assert tests['total'] == 1 and tests['leads'][0]['is_test'] is True
    assert tests['stats']['total'] == 1


def test_concurrent_manual_submission_retries_create_one_lead_and_audit(app, factory):
    data = manual()
    with ThreadPoolExecutor(max_workers=5) as pool:
        responses = list(pool.map(lambda _: post(client(app, 'agent'), 'leads', data), range(5)))
    assert [r.status_code for r in responses] == [200] * 5
    assert len({r.json['id'] for r in responses}) == 1
    assert sum(not r.json['replayed'] for r in responses) == 1
    assert len(responses[0].json['activities']) == 1


def test_restricted_key_cannot_create_manual_leads(app, factory):
    from dashboard.auth.api_keys import create_api_key
    key = create_api_key('manual lead QA restricted', app.qa_users['admin'], scopes=['guest_issues:read'])
    response = app.test_client().post('/inquiries/api/leads', json=manual(), headers={'X-API-Key': key})
    assert response.status_code == 403
