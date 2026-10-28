import copy
from pathlib import Path
import re
from types import SimpleNamespace

import pytest
from flask import Flask
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from dashboard.visibility import routes
from dashboard.visibility.models import Base, Report
from dashboard.visibility.service import ReportError, latest, rate, save, validate


def fixture_report():
    providers, samples = [], []
    for provider in ["chatgpt", "gemini", "claude", "muse"]:
        providers.append(dict(id=provider, label=provider.title(), observed_date="2026-10-27", model="QA model", mode="Fresh", cohort="qa-v1", notes="QA evidence only", citation_basis="unavailable", planned=1, scored=0, blocked=1, errors=0, excluded=0, incorrect_facts=0, unverified_facts=0, retired_errors=0, **{f: {"n": None, "d": 0} for f in ["mentions", "top_three", "citations", "facts"]}))
        samples.append(dict(provider=provider, prompt_id="test", trial=1, status="blocked", prompt="QA prompt", answer="", notes="Access unavailable", model="", mode="", started_at="", completed_at="", search_evidence="", citation_complete=None, citations=[]))
    return dict(schema_version=1, site="enchantedhavens.com", week_start="2026-10-26", kind="baseline", status="blocked", title="QA example — not measured", summary="No results were fabricated.", observed_start="2026-10-27T00:00:00Z", observed_end="2026-10-27T01:00:00Z", protocol="QA-only fixture", comparison={"basis": "baseline", "note": "No comparison yet"}, providers=providers, samples=samples, search_metrics=[], insights=[], actions=[], changes=[], limitations=["QA-only fixture"], sources=[])


@pytest.fixture
def sessions():
    engine = create_engine("sqlite://", execution_options={"schema_translate_map": {"visibility": None}})
    Base.metadata.create_all(engine)
    yield sessionmaker(bind=engine)
    engine.dispose()


def test_replay_correction_and_stale_write(sessions):
    with sessions() as db:
        data = fixture_report()
        first = save(db, data, "first", 0, "qa")
        db.commit()
        assert save(db, data, "first", 0, "qa") == {**first, "replayed": True}
        changed = copy.deepcopy(data); changed["summary"] = "Corrected with new evidence"
        with pytest.raises(ReportError, match="different content"):
            save(db, changed, "first", 0, "qa")
        save(db, changed, "second", 1, "qa"); db.commit()
        with pytest.raises(ReportError, match="newer report"):
            save(db, data, "third", 1, "qa")
        records, total = latest(db)
        assert total == 1 and records[0]["revision"] == 2
        assert "samples" not in records[0]
        assert db.scalar(select(Report).where(Report.id == first["id"])).payload["summary"] == data["summary"]


def test_other_site_never_lists(sessions):
    with sessions() as db:
        save(db, fixture_report(), "first", 0, "qa"); db.commit()
        row = db.scalar(select(Report)); row.site = "another.example"; db.commit()
        assert latest(db) == ([], 0)


@pytest.mark.parametrize("mutation", [
    lambda r: r.update(site="other.example"),
    lambda r: r.update(status="complete"),
    lambda r: r.update(week_start="2026-10-27"),
    lambda r: r.update(observed_end="2099-01-01T00:00:00Z"),
    lambda r: r["providers"][0]["citations"].update(n=0),
    lambda r: r["providers"][0].update(scored=1),
    lambda r: r["samples"].pop(),
    lambda r: r["samples"][0].update(citations=["javascript:alert(1)"]),
    lambda r: r["sources"].append({"label":"secret", "url":"https://example.com/?access_token=private"}),
])
def test_invalid_or_misleading_report_rejected(mutation):
    r = fixture_report(); mutation(r)
    with pytest.raises(ReportError): validate(r)


def test_null_and_zero_are_distinct():
    assert rate({"n":None,"d":0}) == "Not measured"
    assert rate({"n":0,"d":3}) == "0/3 · 0.0%"


@pytest.fixture
def app(sessions, monkeypatch):
    root = Path(__file__).resolve().parents[2]
    app = Flask(__name__, template_folder=str(root / "templates"), static_folder=str(root / "static"))
    app.secret_key = "isolated-qa-only"
    app.config["TESTING"] = True
    routes.register_visibility_routes(app)
    monkeypatch.setattr(routes, "get_session", sessions)
    monkeypatch.setattr(routes, "identity", lambda: ("qa-owner", False))
    # Render the real shell without importing the production application factory.
    shell = (root / "templates/base.html").read_text()
    for endpoint in set(re.findall(r"url_for\('([^']+)'", shell)) | {"auth.login"}:
        if endpoint not in app.view_functions:
            app.add_url_rule("/stub/" + endpoint, endpoint, lambda: "QA shell link")
    @app.context_processor
    def context():
        return dict(current_user=SimpleNamespace(is_admin=lambda:True,name="QA administrator",picture_url=None,email="qa@example.test"), feature_access={},accessible_feature_keys=[],can_manage_api_keys=False)
    return app


def test_private_reads_csrf_and_api_detail(app, sessions):
    client = app.test_client()
    response = client.get("/visibility/")
    assert response.status_code == 200 and b"first weekly report" in response.data
    assert response.headers["Cache-Control"] == "private, no-store"
    assert "noindex" in response.headers["X-Robots-Tag"]
    body = {"report":fixture_report(),"expected_revision":0}
    assert client.post("/visibility/api/reports",json=body).status_code == 403
    with client.session_transaction() as session: csrf = session["visibility_csrf"]
    result = client.post("/visibility/api/reports",json=body,headers={"X-CSRF-Token":csrf,"Idempotency-Key":"qa-first"})
    assert result.status_code == 200
    rid = result.json["id"]
    assert client.get(f"/visibility/reports/{rid}").status_code == 200
    listing = client.get("/visibility/api/reports").json
    assert "samples" not in listing["reports"][0]
    assert len(client.get(f"/visibility/api/reports/{rid}").json["report"]["samples"]) == 4
    assert client.get("/visibility/reports/999999").status_code == 404


def test_unauthenticated_and_forbidden(app, monkeypatch):
    for status in [401,403]:
        def deny(): raise ReportError("Denied",status)
        monkeypatch.setattr(routes,"identity",deny)
        assert app.test_client().get("/visibility/api/reports").status_code == status
        assert app.test_client().get("/visibility/").status_code == (302 if status == 401 else 403)


def test_model_text_is_escaped(app,sessions):
    report=fixture_report();report["summary"]='<script>alert("untrusted")</script>'
    with sessions() as db:
        save(db,report,"xss",0,"qa");db.commit()
    html=app.test_client().get("/visibility/").data.decode()
    assert '<script>alert("untrusted")</script>' not in html
    assert '&lt;script&gt;' in html


@pytest.mark.parametrize('approved,admin,feature,deleted,scope,status',[
    (True,True,True,None,True,200),
    (False,True,True,None,True,403),
    (True,False,True,None,True,403),
    (True,True,False,None,True,403),
    (True,True,True,'deleted',True,403),
    (True,True,True,None,False,403),
])
def test_real_identity_gate(monkeypatch,approved,admin,feature,deleted,scope,status):
    monkeypatch.setenv('OPENAI_API_KEY','qa-unused-no-network')
    monkeypatch.setenv('DATABASE_URL','postgresql://qa@127.0.0.1:1/unused')
    monkeypatch.setenv('SECRET_KEY','isolated-test-only')
    from dashboard.auth import api_keys,models,features
    user=SimpleNamespace(user_id=42,is_approved=approved,deleted_at=deleted,is_admin=lambda:admin)
    key=SimpleNamespace(created_by=42,api_key_id=7,allows_scope=lambda s:scope)
    monkeypatch.setattr(api_keys,'_get_api_key_from_request',lambda req:'qa-only')
    monkeypatch.setattr(api_keys,'verify_api_key',lambda token:key)
    monkeypatch.setattr(models,'get_user_by_id',lambda uid:user)
    monkeypatch.setattr(features,'user_can_access_feature',lambda u,k:feature)
    app=Flask(__name__)
    with app.test_request_context('/visibility/api/reports'):
        if status==200: assert routes.identity()==('user:42 / api-key:7',True)
        else:
            with pytest.raises(ReportError) as exc:routes.identity()
            assert exc.value.status==status
