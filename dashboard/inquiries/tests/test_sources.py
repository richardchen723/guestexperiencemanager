"""Origins remain visible across replies, staff edits and source retirement."""
import json
from uuid import uuid4

import pytest

from dashboard.inquiries.service import DEFAULT_SOURCES, source_label
from .conftest import email, reply
from .test_api import app, client, post


def test_source_fallback_does_not_guess_a_website_from_a_brand():
    assert source_label("luminary-resorts-email") == "LR website"
    assert source_label("partner-referral", brand="Travel partner") == "Travel partner"
    assert source_label("future-channel") == "future-channel"
    assert source_label("manual", [{"id": "manual", "label": "Wrong origin"}]) == "Manual entry"


def seed_sources(c, monkeypatch):
    sources = [*DEFAULT_SOURCES,
               {"id": "luminary-resorts-email", "mailbox": "larry@enchantedhavens.com", "brand": "Luminary Resorts",
                "subject_prefixes": ["New stay inquiry LR-"]},
               {"id": "partner-referral", "mailbox": "larry@enchantedhavens.com", "brand": "Travel partner",
                "label": 'Partner <img src=x onerror="window.sourceInjected=true">',
                "subject_prefixes": ["Partner inquiry"]}]
    monkeypatch.setenv("INQUIRY_EMAIL_SOURCES", json.dumps(sources))
    ids = {}
    for source, reference, subject, name in (
        ("enchanted-havens-email", "EH-ABC12345", "New stay inquiry EH-ABC12345", "EH Guest"),
        ("luminary-resorts-email", "LR-ABC12345", "New stay inquiry LR-ABC12345", "LR Guest"),
        ("partner-referral", "PARTNER-1234", "Partner inquiry", "Partner Guest"),
    ):
        result = post(c, "intake", email(source=source, message_id=source, thread_id=source, subject=subject,
                                      extraction={"reference": reference, "fields": {"guest_name": name, "email": "guest@example.invalid"}}))
        assert result.json["processing_status"] == "processed", result.json
        ids[source] = result.json["lead_id"]
    manual = post(c, "leads", {"creation_id": str(uuid4()), "guest_name": "Manual Guest", "phone": "+1 425 555 0100"})
    assert manual.status_code == 200
    ids["manual"] = manual.json["id"]
    return ids, sources


def test_list_detail_and_bootstrap_labels_with_retired_sources(app, factory, monkeypatch):
    c = client(app)
    ids, sources = seed_sources(c, monkeypatch)
    rows = c.get("/inquiries/api/leads").json["leads"]
    expected = {"enchanted-havens-email": "EH website", "luminary-resorts-email": "LR website",
                "manual": "Manual entry", "partner-referral": sources[2]["label"]}
    assert {r["source"]: r["source_label"] for r in rows} == expected
    bootstrap = c.get("/inquiries/api/bootstrap").json
    assert {s["id"]: s["label"] for s in bootstrap["sources"]} == {k: v for k, v in expected.items() if k != "manual"}
    for source, lead_id in ids.items():
        detail = c.get(f"/inquiries/api/leads/{lead_id}").json
        assert detail["source"] == source and detail["source_label"] == expected[source]

    # Reply and staff changes must retain the opportunity's original attribution.
    result = post(c, "intake", reply(thread_id="enchanted-havens-email", sender="guest@example.invalid"))
    assert result.json["lead_id"] == ids["enchanted-havens-email"]
    detail = c.get(f'/inquiries/api/leads/{ids["enchanted-havens-email"]}').json
    saved = post(c, f'leads/{detail["id"]}', {"version": detail["version"], "internal_notes": "Reviewed"}, method="PATCH")
    assert saved.json["source_label"] == "EH website"

    # Historical leads stay attributable even when intake is no longer configured.
    monkeypatch.setenv("INQUIRY_EMAIL_SOURCES", "[]")
    rows = c.get("/inquiries/api/leads").json["leads"]
    expected["partner-referral"] = "Travel partner"
    assert {r["source"]: r["source_label"] for r in rows} == expected


@pytest.mark.parametrize("label", [None, 7, "", "   ", "x" * 121])
def test_invalid_configured_label_fails_with_configuration_error(app, factory, monkeypatch, label):
    monkeypatch.setenv("INQUIRY_EMAIL_SOURCES", json.dumps([{**DEFAULT_SOURCES[0], "label": label}]))
    response = client(app).get("/inquiries/api/bootstrap")
    assert response.status_code == 503
    assert "administrator configuration" in response.json["error"]


def test_source_filter_combines_with_other_filters_and_counts_all_pages(app, factory, monkeypatch):
    c = client(app)
    ids, sources = seed_sources(c, monkeypatch)
    manual = c.get(f'/inquiries/api/leads/{ids["manual"]}').json
    post(c, f'leads/{manual["id"]}', {"version": manual["version"], "status": "Booked"}, method="PATCH")
    for source, lead_id in ids.items():
        response = c.get('/inquiries/api/leads', query_string={"source": source, "per_page": 1}).json
        assert response["total"] == response["stats"]["total"] == 1
        assert [lead["id"] for lead in response["leads"]] == [lead_id]
        assert {s["id"] for s in response["source_options"]} == set(ids)
    booked = c.get('/inquiries/api/leads?source=manual&status=Booked').json
    assert booked["stats"]["booked"] == 1 and booked["stats"]["conversion_rate"] == 100
    assert c.get('/inquiries/api/leads?source=manual&status=New').json["total"] == 0
    assert c.get('/inquiries/api/leads?source=manual&search=EH%20Guest').json["total"] == 0
    assert c.get('/inquiries/api/leads?source=enchanted-havens-email&listing_id=102').json["total"] == 0
    assert c.get('/inquiries/api/leads?source=not-recorded').json["total"] == 0
    second = post(c, 'leads', {"creation_id": str(uuid4()), "guest_name": "Second Manual", "phone": "+1 425 555 0101"}).json
    first_page = c.get('/inquiries/api/leads?source=manual&per_page=1').json
    second_page = c.get('/inquiries/api/leads?source=manual&per_page=1&page=2').json
    assert first_page["total"] == second_page["total"] == first_page["stats"]["total"] == 2
    assert {first_page["leads"][0]["id"], second_page["leads"][0]["id"]} == {manual["id"], second["id"]}

    # Options include disabled historical sources even when another filter is empty.
    monkeypatch.setenv("INQUIRY_EMAIL_SOURCES", "[]")
    retired = c.get('/inquiries/api/leads?source=luminary-resorts-email').json
    assert retired["total"] == 1
    assert {s["id"]: s["label"] for s in retired["source_options"]}["partner-referral"] == "Travel partner"


def test_source_options_respect_test_scope_and_discover_new_channels(app, factory):
    from dashboard.inquiries.models import Lead
    c = client(app)
    assert len(c.get('/inquiries/api/leads').json["source_options"]) == 3
    result = post(c, 'leads', {"creation_id": str(uuid4()), "guest_name": "QA only", "phone": "+1 425 555 0101", "internal_test": True}).json
    with factory() as db:
        lead = db.get(Lead, result["id"])
        lead.source, lead.brand = "test-only-channel", "Test channel"
        db.commit()
    genuine = c.get('/inquiries/api/leads').json
    assert 'test-only-channel' not in {s["id"] for s in genuine["source_options"]}
    tests = c.get('/inquiries/api/leads?record_scope=internal-tests&source=test-only-channel').json
    assert tests["total"] == 1 and tests["stats"]["total"] == 0
    assert {s["id"]: s["label"] for s in tests["source_options"]}["test-only-channel"] == "Test channel"
    assert client(app, 'agent').get('/inquiries/api/leads?record_scope=internal-tests').status_code == 403
    # A newly recorded source appears on the next list refresh without reloading bootstrap.
    with factory() as db:
        db.get(Lead, result["id"]).is_test = False
        db.commit()
    refreshed = c.get('/inquiries/api/leads?source=test-only-channel').json
    assert refreshed["total"] == 1
    assert {s["id"]: s["label"] for s in refreshed["source_options"]}["test-only-channel"] == "Test channel"
