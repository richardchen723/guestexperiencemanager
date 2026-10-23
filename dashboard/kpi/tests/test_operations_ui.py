"""Opt-in browser QA against local synthetic fixtures, never Cotton data.

Run OPERATIONAL_UI_QA=1 python -m pytest .../test_operations_ui.py.
"""
import os
import threading
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from dashboard.kpi.operations import build_operations
from dashboard.kpi.snapshot_store import empty_snapshot
from dashboard.kpi.tests.test_operations_integration import make_app

pytestmark = pytest.mark.skipif(os.getenv("OPERATIONAL_UI_QA") != "1", reason="Opt-in local browser QA")


@pytest.fixture
def ui():
    from playwright.sync_api import sync_playwright
    from werkzeug.serving import make_server
    data = build_operations({"allow_estimates": True, "listings": [{"id": 1, "name": "QA fixture property", "timezone": "America/New_York"}], "issues_readable": True, "issues": [{"id": 1, "listing_id": 1, "operational_status": "need_attention", "resolution_state": "resolved"}]}, as_of=datetime(2026, 10, 23, 12, tzinfo=timezone.utc))
    data["snapshot_state"] = {"status": "available", "calculated_at": data["generated_at"], "window_as_of": data["as_of"], "next_due_at": "2026-10-25T12:00:00+00:00", "refresh_status": "completed", "schedule_status": "scheduled"}
    with patch("dashboard.kpi.routes.check_feature_access", return_value=None), patch("dashboard.auth.decorators.is_logged_in", return_value=True), patch("dashboard.auth.decorators.is_approved", return_value=True), patch("dashboard.auth.decorators.authenticate_request_api_key", return_value=True), patch("dashboard.kpi.routes.get_current_user", return_value=None), patch("dashboard.kpi.routes.operational_dashboard", return_value=data):
        server = make_server("127.0.0.1", 0, make_app(), threaded=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                context = browser.new_context(viewport={"width": 1440, "height": 1000})
                page = context.new_page()
                yield page, f"http://127.0.0.1:{server.server_port}", data
                context.close(); browser.close()
        finally:
            server.shutdown(); thread.join(timeout=3)


def test_six_cards_navigation_reload_back_forward_and_mobile(ui, tmp_path):
    from playwright.sync_api import expect
    page, base, _ = ui
    errors, methods, urls = [], [], []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on("request", lambda request: methods.append(request.method))
    page.on("request", lambda request: urls.append(request.url))
    page.goto(base + "/kpi/")
    expect(page.locator('#opsLoading')).to_be_hidden()
    expect(page.locator('.ops-card')).to_have_count(6)
    expect(page.locator('[data-goal="issues"] [data-field="value"]')).to_have_text('1')
    assert page.locator('.ops-badge[data-state="verified"]').count() == 0
    assert page.locator('.ops-grid').evaluate('el => getComputedStyle(el).gridTemplateColumns.split(" ").length') == 3
    page.screenshot(path=str(tmp_path / "overview-desktop.png"), full_page=True)
    page.locator('[data-goal="issues"] a').click()
    page.wait_for_url('**/kpi/operations/issues/')
    expect(page.locator('#opsDetail')).to_be_visible()
    expect(page.locator('summary', has_text='Source provenance & freshness')).to_be_visible()
    assert page.locator('dialog').count() == 0
    page.reload(); expect(page.locator('#opsDetail')).to_be_visible()
    page.go_back(); page.wait_for_url('**/kpi/')
    expect(page.locator('.ops-card')).to_have_count(6)
    page.go_forward(); page.wait_for_url('**/kpi/operations/issues/')
    expect(page.locator('#opsDetail')).to_be_visible()
    page.locator('#breakdownSearch').fill('not-a-property')
    expect(page.locator('#opsBreakdown')).to_contain_text('No matching')
    page.locator('#breakdownSearch').fill('fixture')
    page.screenshot(path=str(tmp_path / "detail-desktop.png"), full_page=True)
    for key in ('revenue', 'content', 'reviews', 'response', 'issues', 'cleaning'):
        page.goto(base + f'/kpi/operations/{key}/'); expect(page.locator('#opsDetail')).to_be_visible()
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(base + '/kpi/'); expect(page.locator('#opsLoading')).to_be_hidden()
    assert page.locator('.ops-grid').evaluate('el => getComputedStyle(el).gridTemplateColumns.split(" ").length') == 1
    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
    page.screenshot(path=str(tmp_path / "overview-mobile.png"), full_page=True)
    assert not errors and set(methods) == {'GET'}
    assert not any('/kpi/api/data' in url for url in urls)


def test_loading_failure_retry_empty_and_repeated_clicks(ui):
    from playwright.sync_api import expect
    page, base, data = ui
    pending = []
    page.route('**/kpi/api/operations', lambda route: pending.append(route))
    page.goto(base + '/kpi/', wait_until='domcontentloaded')
    expect(page.locator('#opsLoading')).to_be_visible()
    expect(page.locator('#opsRefresh')).to_be_disabled()
    page.wait_for_timeout(50)
    assert len(pending) == 1
    pending.pop().fulfill(status=503, json={"error": "fixture failure"})
    expect(page.locator('#opsError')).to_be_visible()
    expect(page.locator('#opsGrid')).to_be_hidden()
    page.locator('#opsRetry').click()
    page.wait_for_timeout(50)
    pending.pop().fulfill(json=data)
    expect(page.locator('#opsError')).to_be_hidden()
    expect(page.locator('#opsGrid')).to_be_visible()
    page.locator('#opsRefresh').click()
    page.locator('#opsRefresh').evaluate('(el) => { el.click(); el.click(); el.click(); }')
    page.wait_for_timeout(50)
    assert len(pending) == 1
    pending.pop().fulfill(json=empty_snapshot())
    expect(page.locator('[data-goal="issues"] [data-field="value"]')).to_have_text('Not calculated')
    expect(page.locator('[data-goal="content"] [data-field="value"]')).to_have_text('Deferred')
    expect(page.locator('#opsSnapshotNotice')).to_contain_text('Not calculated yet')
    expect(page.locator('.ops-card')).to_have_count(6)


def test_failed_background_refresh_keeps_saved_values_and_shows_notice(ui):
    from playwright.sync_api import expect
    page, base, data = ui
    data["snapshot_state"]["refresh_status"] = "failed"
    page.goto(base + '/kpi/')
    expect(page.locator('[data-goal="issues"] [data-field="value"]')).to_have_text('1')
    expect(page.locator('#opsSnapshotNotice')).to_contain_text('Showing the previous saved results')
    expect(page.locator('#opsRefresh')).to_have_text('Reload snapshot')


def test_overview_uses_snapshot_measure_target_and_provisional_unit_basis(ui):
    from playwright.sync_api import expect
    page, base, data = ui
    reviews = next(x for x in data["metrics"] if x["key"] == "reviews")
    reviews.update(label="Saved Airbnb coverage", target="Saved target ≥61%", value=44, display="44%")
    page.goto(base + '/kpi/')
    expect(page.locator('[data-goal="reviews"] [data-field="label"]')).to_have_text('Saved Airbnb coverage')
    expect(page.locator('[data-goal="reviews"] [data-field="target"]')).to_have_text('Saved target ≥61%')
    expect(page.locator('[data-goal="content"] [data-field="target"]')).to_have_text('Team definition pending')
    expect(page.locator('[data-goal="issues"] [data-field="visual"]')).to_contain_text('catalog entries (unit proxy) · provisional')
    expect(page.locator('[data-goal="issues"] [data-field="target"]')).to_contain_text('Provisional comparison')
    assert 'physical units' not in page.locator('[data-goal="issues"]').inner_text()
