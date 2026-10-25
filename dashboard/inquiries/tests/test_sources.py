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
