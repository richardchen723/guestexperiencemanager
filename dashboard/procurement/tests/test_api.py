"""Real Flask/auth/PostgreSQL integration tests against an explicitly disposable DB.

Run with DATABASE_URL and PROCUREMENT_QA_DATABASE_URL both pointing to a local
database whose name starts with codex_procurement_qa_. Never uses the app's .env DB.
"""

import io
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.schema import CreateSchema

from dashboard.procurement.models import Base, CatalogItem, Location, Staff
from dashboard.procurement.tests.test_workflow import PROPERTIES, order_data, request_data


@pytest.fixture(scope="module")
def qa_app():
    url = os.getenv("PROCUREMENT_QA_DATABASE_URL")
    if not url:
        pytest.skip("Set PROCUREMENT_QA_DATABASE_URL to an isolated PostgreSQL QA database.")
    parsed = make_url(url)
    assert parsed.database.startswith("codex_procurement_qa_") and parsed.host in (None, "localhost", "127.0.0.1")
    assert os.environ["DATABASE_URL"] == url
    from dashboard.app import create_app
    from dashboard.auth.models import Base as AuthBase, User, UserFeaturePermission, get_session
    from database.models import Listing, get_session as main_session
    engine = create_engine(url)
    with engine.begin() as conn:
        for schema in {t.schema for t in AuthBase.metadata.tables.values() if t.schema}:
            conn.execute(CreateSchema(schema, if_not_exists=True))
    app = create_app()
    app.config.update(TESTING=True)
    users = {}
    with get_session() as db:
        for name, role, enabled in (("admin", "admin", True), ("manager", "user", True), ("cleaner", "user", True), ("denied", "user", False)):
            email = f"procurement-qa-{name}@example.invalid"
            user = db.scalar(select(User).where(User.email == email))
            if not user:
                user = User(email=email, name=f"QA {name}", role=role, is_approved=True)
                db.add(user)
                db.flush()
            users[name] = user.user_id
            existing = db.scalar(select(UserFeaturePermission).where(UserFeaturePermission.user_id == user.user_id, UserFeaturePermission.feature_key == "procurement"))
            if not existing:
                db.add(UserFeaturePermission(user_id=user.user_id, feature_key="procurement", is_enabled=enabled))
        db.commit()
    with main_session(None) as db:
        for value in PROPERTIES.values():
            if not db.get(Listing, value["id"]):
                db.add(Listing(listing_id=value["id"], name=value["name"], internal_listing_name=value["name"], city=value["city"], timezone_name=value["timezone"]))
        db.commit()
    app.qa_users = users
    yield app
    engine.dispose()


@pytest.fixture
def qa(qa_app):
    from dashboard.auth.models import get_session
    from dashboard.procurement.models import init_procurement_database
    # Safety assertion is deliberately repeated immediately before destructive QA reset.
    assert make_url(os.environ["DATABASE_URL"]).database.startswith("codex_procurement_qa_")
    db = get_session()
    with db.bind.begin() as connection:
        connection.execute(text("TRUNCATE " + ", ".join(f"procurement.{t.name}" for t in Base.metadata.sorted_tables) + " RESTART IDENTITY CASCADE"))
    db.add(Staff(id=1, name="Maria", listing_ids=[101, 102], active=True))
    db.add(Staff(id=2, name="QA cleaner", user_id=qa_app.qa_users["cleaner"], listing_ids=[102], active=True))
    db.flush()
    db.add(Location(id=1, label="Maria storage", kind="housekeeper", address="QA private shipping address", staff_id=1, active=True))
    db.commit()
    db.close()
    return qa_app


def client_for(app, role="admin"):
    client = app.test_client()
    with client.session_transaction() as session:
        session["user_id"] = app.qa_users[role]
        session["logged_in"] = True
        session["procurement_csrf"] = "qa-csrf"
    return client


def post(client, path, data, *, key=None, method="POST", csrf=True):
    return client.open("/procurement/api/" + path, method=method, json=data,
                       headers={"Idempotency-Key": key or str(uuid4()), **({"X-CSRF-Token": "qa-csrf"} if csrf else {})})


def create(client, app, **changes):
    response = post(client, "requests", request_data(owner_user_id=app.qa_users["admin"], **changes))
    assert response.status_code == 200, response.json
    return response.json


def draft(client, ticket):
    data = order_data()
    data["lines"][0]["allocations"] = [{"item_id": ticket["items"][0]["id"], "quantity": "4"}]
    response = post(client, "orders", data)
    assert response.status_code == 200, response.json
    return response.json


def test_page_uses_existing_shell_and_authentication(qa):
    assert qa.test_client().get("/procurement/").status_code == 302
    assert qa.test_client().get("/procurement/api/requests").status_code == 401
    assert client_for(qa,"denied").get("/procurement/api/requests").status_code == 403
    response = client_for(qa).get("/procurement/")
    assert response.status_code == 200
    assert b'procurementApp' in response.data and b'Procurement' in response.data


def test_post_requires_csrf_and_idempotency_key(qa):
    client = client_for(qa)
    assert post(client,"requests",request_data(),csrf=False).status_code == 403
    response = client.post("/procurement/api/requests",json=request_data(),headers={"X-CSRF-Token":"qa-csrf"})
    assert response.status_code == 400 and "Idempotency" in response.json["error"]


def test_identical_retry_returns_original_and_conflicting_key_is_rejected(qa):
    client = client_for(qa)
    key = str(uuid4())
    data = request_data(owner_user_id=qa.qa_users["admin"])
    a = post(client,"requests",data,key=key)
    b = post(client,"requests",data,key=key)
    assert a.status_code == b.status_code == 200
    assert a.json == b.json
    conflict = post(client,"requests",{**data,"title":"Different request"},key=key)
    assert conflict.status_code == 409
    assert len(client.get("/procurement/api/requests").json["requests"]) == 1


def test_simultaneous_retries_create_exactly_one_request(qa):
    key = str(uuid4())
    data = request_data(owner_user_id=qa.qa_users["admin"])
    def send(_):
        return post(client_for(qa),"requests",data,key=key)
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(send,range(6)))
    assert [r.status_code for r in results] == [200] * 6
    assert len({r.json["id"] for r in results}) == 1


def test_concurrent_buyers_cannot_reserve_the_same_needed_quantity(qa):
    client = client_for(qa)
    ticket = create(client,qa)
    data = order_data()
    data["lines"][0]["allocations"] = [{"item_id":ticket["items"][0]["id"],"quantity":"4"}]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: post(client_for(qa),"orders",data),range(2)))
    assert sorted(r.status_code for r in results) == [200,400]
    assert len(client.get("/procurement/api/orders").json["orders"]) == 1


def test_housekeeper_scope_blocks_other_properties_purchases_and_directory(qa):
    admin = client_for(qa)
    a = create(admin,qa)
    b = create(admin,qa,listing_id=102)
    cleaner = client_for(qa,"cleaner")
    assert cleaner.get(f'/procurement/api/requests/{a["id"]}').status_code == 403
    assert cleaner.get(f'/procurement/api/requests/{b["id"]}').status_code == 200
    assert [t["id"] for t in cleaner.get("/procurement/api/requests").json["requests"]] == [b["id"]]
    assert cleaner.get("/procurement/api/orders").status_code == 403
    assert cleaner.get("/procurement/api/report").status_code == 403
    assert cleaner.get("/procurement/api/bootstrap").json["locations"] == []
    assert post(cleaner,"directory/staff",{"name":"Impersonator"}).status_code == 403


def test_manager_can_draft_but_only_admin_can_approve(qa):
    client = client_for(qa,"manager")
    order = draft(client,create(client,qa))
    assert post(client,f'orders/{order["id"]}/approve',{"version":order["version"]}).status_code == 403
    assert post(client_for(qa),f'orders/{order["id"]}/approve',{"version":order["version"]}).status_code == 200


def test_full_api_purchase_to_property_confirmation_and_financial_report(qa):
    client = client_for(qa)
    ticket = create(client,qa)
    order = draft(client,ticket)
    def action(path,data):
        response = post(client,path,data)
        assert response.status_code == 200, response.json
        return response.json
    order = action(f'orders/{order["id"]}/approve',{"version":order["version"]})
    order = action(f'orders/{order["id"]}/place',{"version":order["version"],"reference":"QA-WALMART-42","purchased_on":date.today().isoformat()})
    order = action(f'orders/{order["id"]}/shipments',{"version":order["version"],"lines":[{"line_id":order["lines"][0]["id"],"quantity":"4"}]})
    order = action(f'shipments/{order["shipments"][0]["id"]}/deliver',{"version":order["version"]})
    ticket = client.get(f'/procurement/api/requests/{ticket["id"]}').json
    assert ticket["status"] == "awaiting_confirmation"
    ticket = action(f'requests/{ticket["id"]}/confirm',{"version":ticket["version"],"allocation_id":ticket["items"][0]["allocations"][0]["id"],"staff_id":1,"quantity":"4","listing_id":101,"note":"Maria confirmed usable supplies at the rental.","evidence":{"reference":"wamid-confirm-qa"}})
    assert ticket["status"] == "completed"
    report = client.get("/procurement/api/report?month="+date.today().strftime("%Y-%m")).json
    assert report["currencies"][0]["net_cents"] == 700
    assert report["currencies"][0]["properties"][0]["confirmed_cents"] == 700


def test_receipt_upload_is_private_validated_and_retry_safe(qa):
    client = client_for(qa)
    order = draft(client,create(client,qa))
    key = str(uuid4())
    def upload(content):
        return client.post(f'/procurement/api/orders/{order["id"]}/receipts',data={"file":(io.BytesIO(content),"receipt.pdf")},
                           headers={"X-CSRF-Token":"qa-csrf","Idempotency-Key":key},content_type="multipart/form-data")
    assert upload(b"<script>invalid</script>").status_code == 400
    response = upload(b"%PDF-1.4\nQA receipt")
    assert response.status_code == 200, response.json
    replay = upload(b"%PDF-1.4\nQA receipt")
    assert replay.status_code == 200 and len(replay.json["attachments"]) == 1
    attachment_id = response.json["attachments"][0]["id"]
    download = client.get(f'/procurement/api/receipts/{attachment_id}')
    assert download.status_code == 200 and download.headers["X-Content-Type-Options"] == "nosniff"
    assert client_for(qa,"cleaner").get(f'/procurement/api/receipts/{attachment_id}').status_code == 403


@pytest.mark.parametrize("data", [[], {"items":{}}, {"items":[None]}, {"listing_id":"oops"}, {"source":[]}, {"items":[{"name":"Supply","unit":"rolls","catalog_id":True}]}])
def test_malformed_payloads_return_validation_errors_not_server_errors(qa,data):
    response = post(client_for(qa),"requests",data)
    assert response.status_code == 400, response.json


def test_database_changes_are_rolled_back_when_a_later_line_is_invalid(qa):
    client = client_for(qa)
    ticket = create(client,qa)
    data = order_data()
    data["lines"][0]["allocations"] = [{"item_id":ticket["items"][0]["id"],"quantity":"4"}]
    data["lines"].append({"name":"Invalid item","unit":"rolls","quantity":"-2","unit_price":"1.00"})
    assert post(client,"orders",data).status_code == 400
    assert client.get("/procurement/api/orders").json["orders"] == []
    assert client.get(f'/procurement/api/requests/{ticket["id"]}').json["items"][0]["to_order"] == 4


def test_real_api_key_can_capture_requests_but_cannot_approve(qa):
    from dashboard.auth.api_keys import create_api_key
    key = create_api_key("Disposable procurement QA integration",qa.qa_users["admin"])
    client = qa.test_client()
    headers = {"X-API-Key":key,"Idempotency-Key":str(uuid4())}
    response = client.post("/procurement/api/requests",json=request_data(owner_user_id=qa.qa_users["admin"]),headers=headers)
    assert response.status_code == 200, response.json
    order = draft(client_for(qa),response.json)
    headers["Idempotency-Key"] = str(uuid4())
    response = client.post(f'/procurement/api/orders/{order["id"]}/approve',json={"version":order["version"]},headers=headers)
    assert response.status_code == 403


def test_integration_event_cursor_and_attention_queue_are_scoped_to_purchasers(qa):
    client = client_for(qa)
    ticket = create(client,qa,required_by=None,source={"channel":"whatsapp:group-qa","message_id":"wamid-qa-events","body":"Need supplies"})
    response = client.get("/procurement/api/events?after=0")
    assert response.status_code == 200
    assert response.json["events"][0]["ticket_id"] == ticket["id"]
    assert response.json["events"][0]["source_messages"][0]["message_id"] == "wamid-qa-events"
    cursor = response.json["next_cursor"]
    assert client.get(f"/procurement/api/events?after={cursor}").json["events"] == []
    assert client.get("/procurement/api/events?after=oops").status_code == 400
    assert client.get("/procurement/api/attention").json["requests"][0]["id"] == ticket["id"]
    assert client_for(qa,"cleaner").get("/procurement/api/events").status_code == 403


def test_editing_housekeeper_preserves_linked_account_without_making_it_a_purchasing_owner(qa):
    client = client_for(qa)
    bootstrap = client.get("/procurement/api/bootstrap").json
    assert qa.qa_users["cleaner"] in [u["id"] for u in bootstrap["account_users"]]
    assert qa.qa_users["cleaner"] not in [u["id"] for u in bootstrap["users"]]
    response = post(client,"directory/staff",{"id":2,"name":"QA cleaner updated","user_id":qa.qa_users["cleaner"],"listing_ids":[102],"active":True})
    assert response.status_code == 200, response.json
    staff = next(s for s in client.get("/procurement/api/bootstrap").json["staff"] if s["id"] == 2)
    assert staff["user_id"] == qa.qa_users["cleaner"]
    assert client_for(qa,"cleaner").get("/procurement/api/bootstrap").json["can_purchase"] is False


def test_report_csv_download_uses_authoritative_totals_and_protects_property_labels(qa):
    from dashboard.auth.models import get_session
    from dashboard.procurement.models import Ticket
    client = client_for(qa)
    ticket = create(client,qa)
    order = draft(client,ticket)
    order = post(client,f'orders/{order["id"]}/approve',{"version":order["version"]}).json
    post(client,f'orders/{order["id"]}/place',{"version":order["version"],"reference":"QA-CSV","purchased_on":date.today().isoformat()})
    with get_session() as db:
        row = db.get(Ticket,ticket["id"])
        row.property_snapshot = {**row.property_snapshot,"name":"=DANGEROUS(), cabin"}
        db.commit()
    response = client.get("/procurement/api/report.csv?month="+date.today().strftime("%Y-%m"))
    assert response.status_code == 200
    assert "attachment" in response.headers["Content-Disposition"]
    assert response.data.startswith(b"\xef\xbb\xbf")
    assert b"'=DANGEROUS()" in response.data
    assert b"7.00,0.00" in response.data
    assert client_for(qa,"cleaner").get("/procurement/api/report.csv").status_code == 403


def test_request_cannot_be_manually_marked_complete(qa):
    client = client_for(qa)
    ticket = create(client,qa)
    response = post(client,f'requests/{ticket["id"]}',{"version":ticket["version"],"status":"completed"},method="PATCH")
    assert response.status_code == 400
    assert client.get(f'/procurement/api/requests/{ticket["id"]}').json["status"] == "new"


def test_guest_issues_only_api_key_cannot_access_procurement(qa):
    from dashboard.auth.api_keys import create_api_key
    key = create_api_key("Disposable restricted QA key", qa.qa_users["admin"], scopes=["guest_issues:read"])
    client = qa.test_client()
    headers = {"X-API-Key": key, "Idempotency-Key": str(uuid4())}
    assert client.get("/procurement/api/bootstrap", headers=headers).status_code == 403
    assert client.post("/procurement/api/requests", json=request_data(), headers=headers).status_code == 403
    assert client.get("/procurement/api/report", headers=headers).status_code == 403


def test_procurement_contract_is_published_in_html_and_agent_markdown(qa_app):
    client = qa_app.test_client()
    markdown = client.get("/api-docs.md")
    assert markdown.status_code == 200
    assert markdown.mimetype == "text/markdown"
    assert b"## Procurement" in markdown.data
    assert b"POST /procurement/api/requests/{id}/confirm" in markdown.data
    assert b"parent order version" in markdown.data
    html = client.get("/api-docs")
    assert html.status_code == 200
    assert b'id="procurement"' in html.data
    assert b'href="#procurement"' in html.data
    assert b"confirm_delivery" in html.data
