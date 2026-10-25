"""Browser acceptance checks against the real app and synthetic local records."""
import threading
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from playwright.sync_api import expect, sync_playwright
from werkzeug.serving import make_server

from .test_api import app, client, post
from .conftest import email, reply
from .test_sources import seed_sources


def test_spreadsheet_detail_followup_mobile_and_escaping(app,factory):
    from database.models import Listing, ListingTag, Tag, get_session as main_session
    # Exercise the actual property/tag directory in the disposable QA database.
    with main_session(None) as db:
        for tag_id, name in ((901,'CRM QA coast'),(902,'CRM QA mountain'),(903,'CRM QA empty')):
            if not db.get(Tag,tag_id):
                db.add(Tag(tag_id=tag_id,name=name))
        if not db.get(Listing,102):
            db.add(Listing(listing_id=102,name='Emerald Haven',internal_listing_name='Emerald Haven'))
        db.flush()
        for listing_id, tag_id in ((101,901),(102,902)):
            if not db.get(ListingTag,(listing_id,tag_id)):
                db.add(ListingTag(listing_id=listing_id,tag_id=tag_id))
        db.commit()
    c = client(app)
    lead_id = post(c,'intake',email()).json['lead_id']
    long_note = 'A complete staff note. ' * 30
    post(c,f'leads/{lead_id}',{'version':1,'internal_notes':long_note},method='PATCH')
    post(c,'intake',reply(body='<img src=x onerror="window.inquiryInjected=true">\nWe need two children too.'))
    cookie = c.get_cookie('session')
    server = make_server('127.0.0.1',0,app,threaded=True)
    thread = threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    origin = f'http://127.0.0.1:{server.server_port}'
    screenshots = Path('/tmp/ticket-813-qa');screenshots.mkdir(exist_ok=True)
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(viewport={'width':1440,'height':1000})
            context.add_cookies([{'name':'session','value':cookie.value,'url':origin}])
            page = context.new_page()
            errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
            page.goto(origin+'/inquiries/')
            expect(page.locator('#iqRows tr')).to_have_count(1)
            expect(page.locator('#iqRows')).to_contain_text('Sally Example')
            expect(page.locator('#iqTimezone')).to_have_text('America/Los_Angeles')
            assert page.locator('thead th').count() == 13
            expect(page.locator('#iqRows .iq-origin')).to_have_text('EH website')
            assert page.locator('.iq-sheet').evaluate('(e)=>e.scrollWidth > e.clientWidth')
            expect(page.get_by_label('Tag',exact=True)).to_be_visible()
            expect(page.get_by_label('Property',exact=True)).to_be_visible()
            page.locator('#iqProperty').select_option('102')
            expect(page.locator('#iqRows')).to_contain_text('No inquiries match')
            page.locator('#iqTag').select_option('901')
            expect(page.locator('#iqProperty')).to_have_value('')
            expect(page.locator('#iqProperty option')).to_have_text(['All properties in tag','Fair Haven'])
            expect(page.locator('#iqRows')).to_contain_text('Sally Example')
            with page.expect_response(lambda r: parse_qs(urlsplit(r.url).query).get('listing_id')==['101'] and parse_qs(urlsplit(r.url).query).get('tag_id')==['901']):
                page.locator('#iqProperty').select_option('101')
            expect(page.locator('#iqRows')).to_contain_text('Sally Example')
            page.locator('#iqTag').select_option('902')
            expect(page.locator('#iqProperty')).to_have_value('')
            expect(page.locator('#iqProperty option')).to_have_text(['All properties in tag','Emerald Haven'])
            expect(page.locator('#iqRows')).to_contain_text('No inquiries match')
            page.locator('#iqTag').select_option('903')
            expect(page.locator('#iqProperty')).to_be_disabled()
            page.get_by_role('button',name='Clear filters',exact=True).click()
            expect(page.locator('#iqTag')).to_have_value('')
            expect(page.locator('#iqProperty')).to_be_enabled()
            expect(page.locator('#iqProperty option')).to_have_text(['All properties','Fair Haven','Emerald Haven'])
            expect(page.locator('#iqRows')).to_contain_text('Sally Example')
            page.screenshot(path=str(screenshots/'desktop.png'),full_page=True)
            # Editing inline retrieves the full note, never the 160-character preview.
            page.locator('[data-note]').click()
            expect(page.locator('.iq-note-editor')).to_have_value(long_note.strip())
            page.locator('.iq-note-editor').press('Escape')
            page.locator('[data-open]').click()
            expect(page.locator('#iqPanel')).to_be_visible()
            expect(page.locator('#iqPanel')).to_contain_text('Original inquiry & conversation')
            expect(page.locator('#iqPanel a[href="mailto:sally@example.invalid"]')).to_be_visible()
            expect(page.locator('#iqPanel')).to_contain_text('window.inquiryInjected=true')
            assert page.evaluate('window.inquiryInjected') is None
            assert page.locator('#iqPanel img').count()==0
            page.locator('[name=assigned_user_id]').select_option(str(app.qa_users['agent']))
            page.locator('[name=status]').select_option('Follow-up Required')
            page.locator('[name=next_action]').fill('Call Sally with available dates')
            page.locator('[name=follow_up_at]').fill('2026-10-21T10:30')
            page.locator('[name=acknowledge_reply]').check()
            page.get_by_role('button',name='Save changes',exact=True).click()
            expect(page.locator('#iqSaveState')).to_have_text('Changes saved')
            saved=c.get(f'/inquiries/api/leads/{lead_id}').json
            assert saved['follow_up_at']=='2026-10-21T17:30:00+00:00' and not saved['needs_reply']
            assert saved['internal_notes']==long_note.strip()
            page.screenshot(path=str(screenshots/'detail.png'))
            page.get_by_role('button',name='Close inquiry details').click()
            page.locator('#iqStatus').select_option('Booked')
            expect(page.locator('#iqRows')).to_contain_text('No inquiries match')
            page.get_by_role('button',name='Clear filters',exact=True).click()
            expect(page.locator('#iqRows')).to_contain_text('Sally Example')
            # Refresh never discards a dirty side panel.
            page.locator('[data-open]').click()
            page.locator('[name=internal_notes]').fill('Unsaved local edit')
            page.locator('#iqRefresh').dispatch_event('click')
            expect(page.locator('[name=internal_notes]')).to_have_value('Unsaved local edit')
            page.once('dialog',lambda dialog:dialog.accept())
            page.get_by_role('button',name='Close inquiry details').click()
            page.set_viewport_size({'width':390,'height':844})
            assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
            page.screenshot(path=str(screenshots/'mobile.png'),full_page=True)
            page.locator('[data-open]').click()
            expect(page.locator('#iqPanel')).to_be_visible()
            bounds=page.locator('#iqPanel').bounding_box()
            assert bounds['width'] <= 390 and bounds['y'] == 0 and bounds['height'] == 844
            page.screenshot(path=str(screenshots/'mobile-detail.png'))
            assert errors == [], errors
            browser.close()
    finally:
        server.shutdown();thread.join(timeout=5)


def test_all_source_columns_and_detail_labels_on_desktop_and_mobile(app, factory, monkeypatch):
    c = client(app)
    ids, sources = seed_sources(c, monkeypatch)
    cookie = c.get_cookie('session')
    server = make_server('127.0.0.1', 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = f'http://127.0.0.1:{server.server_port}'
    screenshots = Path('/tmp/inquiry-source-qa')
    screenshots.mkdir(exist_ok=True)
    expected = {'enchanted-havens-email': 'EH website', 'luminary-resorts-email': 'LR website',
                'manual': 'Manual entry', 'partner-referral': sources[2]['label']}
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(viewport={'width': 1586, 'height': 1000})
            context.add_cookies([{'name': 'session', 'value': cookie.value, 'url': origin}])
            page = context.new_page()
            errors = []
            page.on('pageerror', lambda e: errors.append(str(e)))
            page.goto(origin + '/inquiries/')
            expect(page.locator('#iqRows tr')).to_have_count(4)
            expect(page.get_by_role('columnheader', name='Source', exact=True)).to_be_visible()
            for source, lead_id in ids.items():
                expect(page.locator(f'#iqRows tr[data-id="{lead_id}"] .iq-origin')).to_have_text(expected[source])
            assert page.locator('#iqRows .iq-origin img').count() == 0
            assert page.evaluate('window.sourceInjected') is None
            page.screenshot(path=str(screenshots / 'desktop.png'), full_page=True)
            for source, lead_id in ids.items():
                page.locator(f'[data-open="{lead_id}"]').click()
                expect(page.locator('#iqPanel .iq-muted').first).to_contain_text(expected[source])
                page.get_by_role('button', name='Close inquiry details').click()
            page.set_viewport_size({'width': 390, 'height': 844})
            page.locator('.iq-sheet').evaluate('(e) => e.scrollLeft = 0')
            assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
            expect(page.locator(f'#iqRows tr[data-id="{ids["manual"]}"] .iq-origin')).to_be_visible()
            page.screenshot(path=str(screenshots / 'mobile.png'), full_page=True)
            assert errors == []
            browser.close()
    finally:
        server.shutdown()
        thread.join(timeout=5)
