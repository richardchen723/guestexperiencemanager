"""Staff can capture phone leads on desktop/mobile and safely retry a lost reply."""
import threading
from pathlib import Path

from playwright.sync_api import expect, sync_playwright
from werkzeug.serving import make_server

from .test_api import app, client


def test_manual_capture_validation_retry_filters_and_mobile(app, factory):
    c = client(app, 'agent')
    cookie = c.get_cookie('session')
    server = make_server('127.0.0.1', 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = f'http://127.0.0.1:{server.server_port}'
    screenshots = Path('/tmp/ticket-818-qa')
    screenshots.mkdir(exist_ok=True)
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(viewport={'width': 1440, 'height': 1000})
            context.add_cookies([{'name': 'session', 'value': cookie.value, 'url': origin}])
            page = context.new_page()
            errors = []
            page.on('pageerror', lambda e: errors.append(str(e)))
            page.goto(origin + '/inquiries/')
            expect(page.get_by_role('button', name='New lead', exact=True)).to_be_enabled()
            page.locator('#iqStatus').select_option('Booked')
            page.get_by_role('button', name='New lead', exact=True).click()
            expect(page.locator('[name=guest_name]')).to_be_focused()
            page.get_by_label('Guest name', exact=True).fill('Phone enquiry guest')
            page.get_by_label('Phone number', exact=True).fill('+1 425 555 0102')
            page.get_by_label('Property / tag', exact=True).select_option('listing:101')
            page.get_by_label('Assigned agent', exact=True).select_option(str(app.qa_users['agent']))
            page.get_by_label('Lead status', exact=True).select_option('Follow-up Required')
            page.get_by_label('Current inquiry details', exact=True).fill('<img src=x onerror="window.manualInjected=true"> Phone inquiry')
            page.get_by_label('Budget as supplied (include currency)', exact=True).fill('USD 1,200')
            page.get_by_role('button', name='Create lead', exact=True).click()
            expect(page.locator('#iqPanelError')).to_contain_text('responsible agent, next action, and due')
            expect(page.get_by_label('Guest name', exact=True)).to_have_value('Phone enquiry guest')
            page.get_by_label('Next action', exact=True).fill('Call guest with availability')
            page.locator('[name=follow_up_at]').fill('2026-11-10T10:30')
            page.screenshot(path=str(screenshots / 'desktop-new-lead.png'), full_page=True)

            attempts = []

            def lose_first_success(route):
                attempts.append(route.request.post_data_json)
                if len(attempts) == 1:
                    assert route.fetch().status == 200
                    route.abort()
                else:
                    route.continue_()

            page.route('**/inquiries/api/leads', lose_first_success)
            page.get_by_role('button', name='Create lead', exact=True).click()
            expect(page.locator('#iqPanelError')).to_be_visible()
            expect(page.get_by_role('button', name='Create lead', exact=True)).to_be_enabled()
            page.get_by_role('button', name='Create lead', exact=True).click()
            expect(page.locator('#iqSaveState')).to_have_text('Lead created')
            assert len(attempts) == 2 and attempts[0] == attempts[1]
            leads = c.get('/inquiries/api/leads').json['leads']
            assert len(leads) == 1
            saved = c.get(f'/inquiries/api/leads/{leads[0]["id"]}').json
            assert saved['follow_up_at'] == '2026-11-10T18:30:00+00:00'
            assert saved['qualification']['budget'] == 'USD 1,200'
            assert saved['messages'] == [] and len(saved['activities']) == 1
            expect(page.locator('#iqStatus')).to_have_value('Booked')
            expect(page.locator('#iqRows')).to_contain_text('No inquiries match')
            expect(page.locator('#iqPanel')).to_contain_text('Manual entry')
            expect(page.locator('#iqPanel')).to_contain_text('Lead created manually')
            expect(page.locator('#iqPanel .iq-original')).to_contain_text('window.manualInjected=true')
            assert page.locator('#iqPanel img').count() == 0
            assert page.evaluate('window.manualInjected') is None
            page.get_by_role('button', name='Close inquiry details', exact=True).click()
            expect(page.locator('#iqPanel')).to_be_hidden()
            page.get_by_role('button', name='Clear filters', exact=True).click()
            expect(page.locator('#iqRows')).to_contain_text('Phone enquiry guest')
            expect(page.locator('#iqRows .iq-origin')).to_have_text('Manual entry')
            page.screenshot(path=str(screenshots / 'desktop-created-lead.png'), full_page=True)

            page.set_viewport_size({'width': 390, 'height': 844})
            page.get_by_role('button', name='New lead', exact=True).click()
            expect(page.locator('#iqPanel')).to_be_visible()
            page.get_by_label('Guest name', exact=True).fill('Email enquiry guest')
            page.get_by_label('Email address', exact=True).fill('manual@example.invalid')
            expect(page.locator('#iqDetailForm .form-stepper-progress')).to_have_count(0)
            page.screenshot(path=str(screenshots / 'mobile-new-lead.png'), full_page=True)
            page.get_by_label('Guest count', exact=True).fill('5')
            page.get_by_label('Stay requirements', exact=True).fill('Need a crib')
            page.once('dialog', lambda dialog: dialog.dismiss())
            page.get_by_role('button', name='Close inquiry details', exact=True).click()
            expect(page.locator('#iqPanel')).to_be_visible()
            page.get_by_role('button', name='Create lead', exact=True).click()
            expect(page.locator('#iqSaveState')).to_have_text('Lead created')
            assert c.get('/inquiries/api/leads').json['total'] == 2
            mobile_lead = c.get('/inquiries/api/leads').json['leads'][0]
            assert mobile_lead['qualification'] == {'guest_count': 5, 'stay_requirements': 'Need a crib'}
            assert errors == []
            browser.close()
    finally:
        server.shutdown()
        thread.join(timeout=5)
