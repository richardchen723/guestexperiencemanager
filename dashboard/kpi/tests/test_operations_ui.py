"""Opt-in browser QA against local synthetic fixtures, never Cotton data.

Run OPERATIONAL_UI_QA=1 python -m pytest .../test_operations_ui.py.
"""
import os
import threading
from datetime import date, datetime, timedelta, timezone
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
        expect(page.get_by_text('Assumptions, coverage & calculation rules', exact=True)).to_have_count(0)
        expect(page.get_by_text('Property-local windows', exact=True)).to_have_count(0)
        expect(page.locator('#opsMethod, #opsPropertyWindows')).to_have_count(0)
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


def test_revenue_four_column_comparison_currency_signed_changes_and_partial_weeks(ui, tmp_path):
    from playwright.sync_api import expect
    from dashboard.kpi.tests.test_estimates import booking, data as evidence
    page, base, saved = ui
    source = evidence(); source['allow_estimates'] = True
    source['reservations'] = []
    for i, (when, current, prior) in enumerate([(date(2026, 7, 23), 1234.5, 1000), (date(2026, 7, 30), 750, 1000), (date(2026, 8, 6), 0, 100), (date(2026, 8, 13), 100, 0), (date(2026, 8, 20), 100, None), (date(2026, 8, 27), 50, 50)]):
        source['reservations'].append(booking(i*2+1, arrival=when, departure=when+timedelta(days=1), total_price=current+50))
        if prior is not None:
            previous = when.replace(year=2025)
            source['reservations'].append(booking(i*2+2, 2025, arrival=previous, departure=previous+timedelta(days=1), total_price=prior+50))
    saved['metrics'][0] = build_operations(source, as_of=datetime(2026, 10, 23, 12, tzinfo=timezone.utc))['metrics'][0]
    page.goto(base + '/kpi/operations/revenue/')
    expect(page.locator('#opsDetail')).to_be_visible()
    headers = page.locator('#opsTrend th')
    expect(headers).to_have_text(['Date', 'Booking value in USD', 'Booking value same week last year in USD', 'YoY change'])
    assert headers.evaluate_all("nodes => nodes.every(x => x.scope === 'col')")
    expect(page.locator('#opsTrend caption')).to_contain_text('Same 1 common listing')
    expect(page.locator('#opsTrend caption')).to_contain_text('before unreconciled refunds')
    expect(page.locator('#opsTrendScope')).to_contain_text('Same 1 common listing')
    def row(week): return page.locator('#opsTrend tbody tr').filter(has_text=week)
    expect(row('2026-07-20').locator('td')).to_have_count(4)
    expect(row('2026-07-20').locator('td').nth(0)).to_contain_text('Partial week · 4 days/listing')
    expect(row('2026-07-20').locator('td').nth(0)).to_have_attribute('title', 'Current: 2026-07-23 → 2026-07-27 (exclusive). Prior: 2025-07-23 → 2025-07-27 (exclusive).')
    expect(row('2026-07-20').locator('td').nth(1)).to_have_text('$1,234.50')
    expect(row('2026-07-20').locator('td').nth(2)).to_have_text('$1,000.00')
    expect(row('2026-07-20').locator('td').nth(3)).to_have_text('+23.45%')
    expect(row('2026-07-27').locator('td').nth(3)).to_have_text('-25.00%')
    expect(row('2026-08-03').locator('td').nth(3)).to_have_text('-100.00%')
    expect(row('2026-08-10').locator('td').nth(2)).to_have_text('$0.00')
    expect(row('2026-08-10').locator('td').nth(3)).to_have_text('N/A')
    expect(row('2026-08-17').locator('td').nth(2)).to_have_text('N/A')
    expect(row('2026-08-24').locator('td').nth(3)).to_have_text('0.00%')
    expect(page.locator('#detailStatus')).to_have_text('Estimated')
    expect(page.locator('#opsSnapshotNotice')).to_be_hidden()
    expect(page.locator('#opsMethod, #opsPropertyWindows')).to_have_count(0)
    chart = page.locator('#opsTrendChart svg')
    expect(chart).to_have_attribute('data-y-max','1500')
    expect(page.locator('.ops-chart-legend')).to_contain_text('This year')
    expect(page.locator('.ops-chart-legend')).to_contain_text('Previous year')
    expect(page.locator('.ops-chart-legend')).to_contain_text('* Partial week')
    expect(page.locator('.ops-y-tick')).to_have_text(['$0','$500','$1K','$1.5K'])
    expect(page.locator('.ops-revenue-line.current')).to_have_count(1)
    expect(page.locator('.ops-revenue-line.prior')).to_have_count(2)  # Missing prior week is a gap.
    assert page.locator('.ops-revenue-line.prior').first.evaluate("x => getComputedStyle(x).strokeDasharray") != 'none'
    expected = saved['metrics'][0]['trend']
    for key, cls in [('booking_value_usd','current'),('prior_booking_value_usd','prior')]:
        actual = page.locator(f'.ops-revenue-dot.{cls}').evaluate_all("nodes => nodes.map(x => [x.dataset.date, Number(x.dataset.value)])")
        assert actual == [[row['date'],row[key]] for row in expected if row[key] is not None]
    point = page.locator('.ops-chart-hit[data-date="2026-07-20"]')
    point.focus()
    expect(page.locator('.ops-chart-tooltip')).to_be_visible()
    expect(page.locator('.ops-chart-tooltip')).to_contain_text('This year $1,234.50 · Previous year $1,000.00 · YoY +23.45%')
    expect(page.locator('.ops-chart-tooltip')).to_contain_text('Partial week')
    expect(page.locator('.ops-chart-tooltip')).to_contain_text('2025-07-23 → 2025-07-27')
    page.screenshot(path=str(tmp_path/'revenue-comparison-desktop.png'),full_page=True)
    page.reload(); expect(headers).to_have_count(4)
    page.set_viewport_size({'width':390,'height':844})
    expect(page.locator('.ops-x-tick').first).to_have_text('Jul 20*')
    expect(page.locator('.ops-x-tick').last).to_have_text('Oct 19*')
    expect(chart).to_have_attribute('class','ops-chart ops-revenue-chart')
    page.wait_for_function('Number(document.querySelector("#opsTrendChart svg").getAttribute("viewBox").split(" ")[2]) < 400')
    assert chart.evaluate('x => Number(x.getAttribute("viewBox").split(" ")[2])') < 400
    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
    expect(page.locator('.ops-table-scroll-hint')).to_be_visible()
    page.locator('#opsTrend').focus(); page.keyboard.press('ArrowRight')
    page.wait_for_function('document.getElementById("opsTrend").scrollLeft > 0')
    page.locator('#opsTrend').evaluate('x => x.scrollLeft = 0')
    page.screenshot(path=str(tmp_path/'revenue-comparison-mobile.png'),full_page=True)


def test_old_revenue_snapshot_shows_unavailable_comparison_without_fabrication(ui):
    from playwright.sync_api import expect
    page, base, saved = ui
    revenue = saved['metrics'][0]
    for key in ('trend_schema', 'trend_scope', 'trend_alignment'): revenue.pop(key, None)
    revenue['trend'] = [{'date':'2026-07-20','value':999999,'unit':'USD booking proxy'}]
    page.goto(base + '/kpi/operations/revenue/')
    expect(page.locator('#opsTrend th')).to_have_count(4)
    expect(page.locator('#opsTrend caption')).to_contain_text('unavailable in this saved snapshot')
    expect(page.locator('#opsTrend tbody')).to_contain_text('Unavailable until a saved calculation')
    expect(page.locator('#opsTrendChart svg')).to_have_count(0)
    assert '$999,999' not in page.locator('#opsTrend').inner_text()
    page.goto(base+'/kpi/')
    expect(page.locator('.ops-card')).to_have_count(6)
    expect(page.locator('[data-goal="issues"] [data-field="value"]')).to_have_text('1')


@pytest.mark.parametrize('value,maximum', [(0,1),(1250,1500)])
def test_revenue_chart_flat_and_zero_series_have_finite_readable_usd_scale(ui,value,maximum):
    from playwright.sync_api import expect
    from dashboard.kpi.revenue_trend import SCHEMA,weekly_comparison
    from dashboard.kpi.tests.test_estimates import W
    page,base,saved=ui
    daily={}
    for day in (date(2026,8,1),date(2026,8,8)):
        daily[(1,'current',day)]=value
        daily[(1,'prior',day.replace(year=2025))]=value
    revenue=saved['metrics'][0]
    revenue.update(trend_schema=SCHEMA,trend_scope='Same fixed test cohort · booking-value proxy',trend=weekly_comparison(daily,W,{1}))
    page.goto(base+'/kpi/operations/revenue/')
    expect(page.locator('#opsTrendChart svg')).to_have_attribute('data-y-max',str(maximum))
    assert page.locator('.ops-y-tick').count() >= 4
    assert page.locator('.ops-revenue-dot').count()==4
    assert all(float(x) >= 0 for x in page.locator('.ops-revenue-dot').evaluate_all('nodes => nodes.map(x => x.getAttribute("cy"))'))
    assert 'NaN' not in page.locator('#opsTrendChart').inner_html()
    assert f'This year ${value:,.2f}' in page.locator('.ops-chart-hit[data-date="2026-07-27"]').get_attribute('aria-label')


def test_revenue_chart_all_missing_stays_empty_and_leap_spans_are_marked(ui):
    from playwright.sync_api import expect
    from dashboard.kpi.revenue_trend import SCHEMA,weekly_comparison
    from dashboard.kpi.tests.test_estimates import W
    from dashboard.kpi.operations import local_window
    page,base,saved=ui
    revenue=saved['metrics'][0]
    revenue.update(trend_schema=SCHEMA,trend_scope='Same fixed test cohort · booking-value proxy',trend=weekly_comparison({},W,{1}))
    page.goto(base+'/kpi/operations/revenue/')
    expect(page.locator('#opsTrendChart svg')).to_have_count(0)
    expect(page.locator('#opsTrendChart')).to_contain_text('No observed booking values')
    assert '$0.00' not in page.locator('#opsTrend').inner_text()
    w=local_window(datetime(2024,3,10,12,tzinfo=timezone.utc),'America/New_York')
    daily={(1,'current',date(2024,2,29)):70,(1,'prior',date(2023,2,28)):60}
    revenue['trend']=weekly_comparison(daily,{1:w},{1})
    page.reload()
    expect(page.locator('.ops-chart-legend')).to_contain_text('† Unequal calendar lengths')
    expect(page.locator('.ops-span-warning')).to_have_text('†')
    leap_row=page.locator('#opsTrend tbody tr').filter(has_text='2024-02-26')
    expect(leap_row).to_contain_text('Unequal leap-year spans')
    expect(leap_row.locator('td').nth(1)).to_have_text('$70.00')
    expect(leap_row.locator('td').nth(2)).to_have_text('$60.00')
    expect(leap_row.locator('td').nth(3)).to_have_text('N/A')
    page.locator('.ops-chart-hit[data-date="2024-02-26"]').focus()
    expect(page.locator('.ops-chart-tooltip')).to_contain_text('Different calendar day counts')
