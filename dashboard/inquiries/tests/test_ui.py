"""Browser acceptance checks against the real app and synthetic local records."""
import threading
from pathlib import Path

from playwright.sync_api import expect, sync_playwright
from werkzeug.serving import make_server

from .test_api import app, client, post
from .conftest import email, reply


def test_spreadsheet_detail_followup_mobile_and_escaping(app,factory):
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
            assert page.locator('thead th').count() == 12
            assert page.locator('.iq-sheet').evaluate('(e)=>e.scrollWidth > e.clientWidth')
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
