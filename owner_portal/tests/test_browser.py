"""Opt-in browser workflows with synthetic identities and a local HTTPS server."""
import os
from contextlib import contextmanager
from pathlib import Path
from threading import Thread

import pytest
from jinja2 import ChoiceLoader, DictLoader, FileSystemLoader
from sqlalchemy import text
from werkzeug.serving import make_server

from owner_portal.access import digest
from owner_portal.auth import sign_in
from .test_admin import staff_app  # Reuse the isolated staff auth fixture.

pytestmark = pytest.mark.skipif(os.getenv('OWNER_BROWSER_QA') != '1', reason='Set OWNER_BROWSER_QA=1 for Chromium workflows.')
ROOT = Path(__file__).resolve().parents[2]


@contextmanager
def server(app):
    server = make_server('127.0.0.1', 0, app, ssl_context='adhoc')
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'https://127.0.0.1:{server.server_port}'
    finally:
        server.shutdown()
        thread.join(timeout=3)


def cookie(app, values, origin):
    return {'name': app.config['SESSION_COOKIE_NAME'],
        'value': app.session_interface.get_signing_serializer(app).dumps(values),
        'url': origin, 'secure': True, 'httpOnly': True, 'sameSite': 'Lax'}


def screenshot(page, name):
    output = ROOT / 'output' / 'owner-847-qa'
    output.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(output / name), full_page=True)


@pytest.mark.parametrize('prefix', ['', '/owners'])
def test_owner_login_and_property_views_at_two_sizes(app, factory, setup, prefix):
    from playwright.sync_api import sync_playwright, expect
    if prefix:
        from owner_portal.mount import OwnerMount
        app.config.update(OWNER_PATH_PREFIX=prefix, APPLICATION_ROOT=prefix)
        app.wsgi_app = OwnerMount(app.wsgi_app, prefix)
    with factory.begin() as db:
        raw = sign_in(db, {'sub': 'synthetic-owner', 'email': 'owner@example.invalid'}, digest(setup['invite']['token']))
    with server(app) as origin, sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context(ignore_https_errors=True, viewport={'width': 1280, 'height': 900})
        page = context.new_page()
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(origin + prefix + '/login')
        expect(page.get_by_role('button', name='Continue with Google')).to_be_visible()
        screenshot(page, 'owner-login-desktop.png')
        context.add_cookies([cookie(app, {'owner_token': raw, 'csrf': 'synthetic-csrf'}, origin)])
        page.goto(origin + prefix + '/')
        expect(page.get_by_role('heading', name='Cedar House')).to_be_visible()
        expect(page.get_by_text('Private Pine House')).to_have_count(0)
        screenshot(page, 'owner-home-desktop.png')
        page.set_viewport_size({'width': 390, 'height': 844})
        page.get_by_text('Reporting access', exact=True).click()
        expect(page.get_by_text('From 2026-01-01', exact=False)).to_be_visible()
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        screenshot(page, 'owner-home-mobile.png')
        page.get_by_role('button', name='Sign out').click()
        expect(page.get_by_role('button', name='Continue with Google')).to_be_visible()
        # Exercise the form redirect under the real CSP, without contacting Google.
        from base64 import b64encode
        cdp = context.new_cdp_session(page)
        cdp.send('Fetch.enable', {'patterns': [{'urlPattern': 'https://accounts.google.com/*', 'requestStage': 'Request'}]})
        cdp.on('Fetch.requestPaused', lambda event: cdp.send('Fetch.fulfillRequest', {
            'requestId': event['requestId'], 'responseCode': 200,
            'responseHeaders': [{'name': 'Content-Type', 'value': 'text/html'}],
            'body': b64encode(b'<p>Synthetic Google authorization</p>').decode()}))
        page.get_by_role('button', name='Continue with Google').click(no_wait_after=True)
        expect(page.get_by_text('Synthetic Google authorization')).to_be_visible()
        assert not errors
        browser.close()


@pytest.fixture
def staff_browser_app(staff_app, engine):
    with engine.begin() as conn:
        conn.execute(text('CREATE TABLE public.listings (listing_id int, internal_listing_name text, name text, timezone_name text)'))
        conn.execute(text("INSERT INTO public.listings VALUES (303,'Synthetic Willow House','Willow','America/Los_Angeles'), (404,'Synthetic Birch House','Birch',NULL)"))
    # Keep the real shell's stylesheet order, including global touch-target rules.
    import re
    base = (ROOT / 'dashboard' / 'templates' / 'base.html').read_text()
    def styles(fragment):
        return ''.join(f'<link rel="stylesheet" href="/static/{name}">' for name in
            re.findall(r"filename='(css/[^']+)'", fragment))
    before, after = base.split('{% block extra_head %}{% endblock %}', 1)
    shell_styles = styles(before) + '{% block extra_head %}{% endblock %}' + styles(after.split('</head>')[0])
    staff_app.static_folder = str(ROOT / 'dashboard' / 'static')
    staff_app.jinja_loader = ChoiceLoader([DictLoader({'base.html': '''<!doctype html><html><head>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        ''' + shell_styles + '''</head><body><main>{% block content %}{% endblock %}</main>{% block extra_scripts %}{% endblock %}</body></html>'''}),
        FileSystemLoader(str(ROOT / 'dashboard' / 'templates'))])
    yield staff_app
    with engine.begin() as conn:
        conn.execute(text('DROP TABLE public.listings'))


def staff_context(browser, app, origin, width=1360):
    context = browser.new_context(ignore_https_errors=True, viewport={'width': width, 'height': 1000})
    context.add_cookies([cookie(app, {'user_id': 1, 'logged_in': True, 'owner_access_csrf': 'synthetic-csrf'}, origin)])
    return context


def test_staff_account_workspace_and_atomic_home_flow(staff_browser_app, factory, setup):
    from playwright.sync_api import sync_playwright, expect
    from sqlalchemy import select
    from owner_portal.models import Property, PropertyGrant
    with factory.begin() as db:
        sign_in(db, {'sub': 'synthetic-owner', 'email': 'owner@example.invalid'}, digest(setup['invite']['token']))
    with server(staff_browser_app) as origin, sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = staff_context(browser, staff_browser_app, origin)
        page = context.new_page()
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(origin + '/owner-access/')
        expect(page.locator('#oa-memberships').get_by_text('owner@example.invalid')).to_be_visible()
        expect(page.locator('#oa-detail').get_by_text('Private Pine House')).to_have_count(0)
        page.get_by_role('button', name='Review access', exact=True).click()
        expect(page.get_by_role('dialog').get_by_text('Cedar House')).to_be_visible()
        expect(page.get_by_role('dialog').get_by_text('Private Pine House')).to_have_count(0)
        page.get_by_role('button', name='Done', exact=True).click()
        page.get_by_role('button', name='Add home', exact=False).click()
        page.get_by_label('Find a home').fill('Willow')
        page.get_by_role('radio', name='Synthetic Willow House', exact=False).check()
        page.get_by_role('button', name='Continue', exact=False).click()
        expect(page.get_by_label('Home name')).to_have_value('Synthetic Willow House')
        expect(page.get_by_label('Property timezone')).to_have_value('America/Los_Angeles')
        page.get_by_label('Home name').fill('Willow House')
        page.get_by_label('Reporting starts on').fill('2026-01-01')
        page.get_by_label('Reporting ends on').fill('2025-12-31')
        page.get_by_role('checkbox', name='I’ve checked', exact=False).check()
        page.get_by_role('button', name='Connect home', exact=True).click()
        expect(page.locator('#oa-dialog-error')).to_contain_text('on or after')
        expect(page.get_by_label('Home name')).to_have_value('Willow House')
        page.get_by_label('Reporting ends on').fill('2026-12-31')
        page.get_by_role('button', name='Back', exact=False).click()
        page.get_by_role('button', name='Continue', exact=False).click()
        expect(page.get_by_label('Home name')).to_have_value('Willow House')
        expect(page.get_by_label('Reporting ends on')).to_have_value('2026-12-31')
        screenshot(page, 'staff-connect-home.png')
        page.get_by_role('button', name='Connect home', exact=True).click()
        expect(page.locator('#oa-grants').get_by_text('Willow House', exact=True)).to_be_visible()
        with factory() as db:
            home = db.scalar(select(Property).where(Property.name == 'Willow House'))
            grant = db.scalar(select(PropertyGrant).where(PropertyGrant.property_id == home.id))
            assert grant.account_id == setup['account']
            assert str(grant.reporting_until) == '2027-01-01'
        row = page.locator('#oa-grants .oa-row').filter(has_text='Willow House')
        row.get_by_role('button', name='Edit dates').click()
        expect(page.get_by_label('Reporting ends on')).to_have_value('2026-12-31')
        page.get_by_label('Reporting ends on').fill('2026-11-30')
        page.get_by_role('button', name='Save dates').click()
        expect(row).to_contain_text('Nov 30, 2026')
        page.get_by_role('button', name='Invite person', exact=False).click()
        page.get_by_label('Google account email').fill('second@example.invalid')
        page.get_by_label('Finalized owner statements', exact=False).check()
        page.get_by_role('button', name='Create invitation link').click()
        expect(page.get_by_role('heading', name='Your invitation link is ready')).to_be_visible()
        assert '#token=' in page.locator('#oa-link').input_value()
        page.get_by_role('button', name='Done', exact=True).click()
        expect(page.locator('#oa-link')).to_have_count(0)
        expect(page.locator('#oa-invitations')).to_contain_text('second@example.invalid')
        screenshot(page, 'staff-access-desktop.png')
        page.set_viewport_size({'width': 390, 'height': 844})
        screenshot(page, 'staff-access-mobile.png')
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), page.evaluate("[...document.querySelectorAll('body *')].filter(e => e.getBoundingClientRect().right > innerWidth).map(e => e.className)")
        page.locator('#oa-memberships').get_by_role('button', name='Remove', exact=True).click()
        expect(page.get_by_role('dialog')).to_contain_text('owner@example.invalid')
        page.get_by_role('button', name='Remove access').click()
        expect(page.locator('#oa-memberships').get_by_text('owner@example.invalid')).to_have_count(0)
        page.get_by_role('button', name='Morgan household', exact=False).click()
        expect(page.locator('#oa-grants').get_by_text('Private Pine House')).to_be_visible()
        expect(page.locator('#oa-grants').get_by_text('Willow House')).to_have_count(0)
        assert not errors
        browser.close()


def test_staff_first_owner_with_sign_in_paused(staff_browser_app, monkeypatch):
    from playwright.sync_api import sync_playwright, expect
    monkeypatch.setenv('OWNER_PORTAL_LOGIN_ENABLED', 'false')
    with server(staff_browser_app) as origin, sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = staff_context(browser, staff_browser_app, origin)
        page = context.new_page()
        page.goto(origin + '/owner-access/')
        expect(page.get_by_role('button', name='Set up your first owner', exact=False)).to_be_visible()
        screenshot(page, 'staff-empty-desktop.png')
        page.get_by_role('button', name='Set up your first owner', exact=False).click()
        page.get_by_label('Owner account name').fill('Harlow household')
        page.get_by_role('button', name='Create owner', exact=True).click()
        expect(page.locator('#oa-detail').get_by_role('heading', name='Harlow household')).to_be_visible()
        expect(page.get_by_role('button', name='Invite person', exact=False)).to_be_disabled()
        expect(page.get_by_text('Invitations paused', exact=True)).to_be_visible()
        page.get_by_role('button', name='Add home', exact=False).click()
        page.get_by_label('Find a home').fill('Birch')
        page.get_by_role('radio', name='Synthetic Birch House', exact=False).check()
        page.get_by_role('button', name='Continue', exact=False).click()
        # Missing source timezone requires an explicit choice, never a location-based guess.
        expect(page.get_by_label('Property timezone')).to_have_value('')
        page.set_viewport_size({'width': 390, 'height': 844})
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        screenshot(page, 'staff-connect-home-mobile.png')
        page.get_by_label('Property timezone').select_option('America/Chicago')
        page.get_by_label('Reporting starts on').fill('2025-01-01')
        page.get_by_role('checkbox', name='I’ve checked', exact=False).check()
        page.get_by_role('button', name='Connect home', exact=True).click()
        expect(page.locator('#oa-grants').get_by_text('Synthetic Birch House')).to_be_visible()
        page.reload()
        expect(page.locator('#oa-detail').get_by_role('heading', name='Harlow household')).to_be_visible()
        expect(page.get_by_text('Homes are connected.', exact=False)).to_be_visible()
        browser.close()
