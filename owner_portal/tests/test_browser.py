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


def test_staff_setup_invitation_preview_and_revocation(staff_app, factory, setup, engine):
    from playwright.sync_api import sync_playwright, expect
    with engine.begin() as conn:
        conn.execute(text('CREATE TABLE public.listings (listing_id int, internal_listing_name text, name text)'))
        conn.execute(text("INSERT INTO public.listings VALUES (303,'Synthetic Willow House','Willow')"))
    # Real setup page/content; substitute only the surrounding staff shell and auth.
    staff_app.static_folder = str(ROOT / 'dashboard' / 'static')
    staff_app.jinja_loader = ChoiceLoader([DictLoader({'base.html': '''<!doctype html><html><head>
        <link rel="stylesheet" href="/static/css/style.css">{% block extra_head %}{% endblock %}
        </head><body><main>{% block content %}{% endblock %}</main>{% block extra_scripts %}{% endblock %}</body></html>'''}),
        FileSystemLoader(str(ROOT / 'dashboard' / 'templates'))])
    with factory.begin() as db:
        sign_in(db, {'sub': 'synthetic-owner', 'email': 'owner@example.invalid'}, digest(setup['invite']['token']))
    try:
        with server(staff_app) as origin, sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            context = browser.new_context(ignore_https_errors=True, viewport={'width': 1360, 'height': 1100})
            context.add_cookies([cookie(staff_app, {'user_id': 1, 'logged_in': True, 'owner_access_csrf': 'synthetic-csrf'}, origin)])
            page = context.new_page()
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(origin + '/owner-access/')
            expect(page.locator('#oa-memberships').get_by_text('owner@example.invalid')).to_be_visible()
            page.get_by_role('button', name='Preview', exact=True).click()
            expect(page.locator('#oa-preview-content').get_by_text('Cedar House', exact=False)).to_be_visible()
            expect(page.locator('#oa-preview-content').get_by_text('Private Pine House', exact=False)).to_have_count(0)
            screenshot(page, 'staff-access-desktop.png')
            form = page.locator('form[data-action="properties"]')
            form.get_by_label('Owner-facing name').fill('Willow House')
            form.get_by_label('Timezone').fill('America/Los_Angeles')
            form.get_by_label('Hostaway listings').select_option('303')
            form.get_by_role('checkbox').check()
            form.get_by_role('button').click()
            expect(page.locator('#oa-properties option').filter(has_text='Willow House')).to_have_count(1)
            invite = page.locator('form[data-action="invitations"]')
            invite.get_by_role('combobox').select_option(str(setup['account']))
            invite.get_by_label('Google account email').fill('second@example.invalid')
            invite.get_by_label('Finalized owner statements').check()
            invite.get_by_role('button').click()
            expect(page.locator('#oa-invitation')).to_be_visible()
            assert '#token=' in page.locator('#oa-link').input_value()
            page.on('dialog', lambda dialog: dialog.accept())
            page.locator('#oa-memberships').get_by_role('button', name='Revoke', exact=True).click()
            expect(page.locator('#oa-memberships').get_by_text('Revoked', exact=True)).to_be_visible()
            assert not errors
            browser.close()
    finally:
        with engine.begin() as conn:
            conn.execute(text('DROP TABLE public.listings'))
