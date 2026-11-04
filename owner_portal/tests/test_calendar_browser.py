"""Synthetic calendar interactions under the production CSP, in both engines."""
import os
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path
from threading import Thread
from zoneinfo import ZoneInfo

import pytest
from werkzeug.serving import make_server

from owner_portal.admin import OwnerAdmin
from owner_portal.calendar import build_calendar
from owner_portal.calculations import days, month_shift
from owner_portal.models import CalendarMonth, PropertyGrant, utcnow
from .test_access import accept
from .test_browser import cookie

pytestmark=pytest.mark.skipif(os.getenv('OWNER_BROWSER_QA')!='1',reason='Opt-in browser calendar workflows.')


@contextmanager
def http_server(app):
    # Loopback-only test transport avoids WebKit's ad-hoc certificate quirks.
    app.config.update(SESSION_COOKIE_SECURE=False,SESSION_COOKIE_NAME='owner_calendar_qa')
    server=make_server('127.0.0.1',0,app,threaded=True)
    thread=Thread(target=server.serve_forever,daemon=True);thread.start()
    try:yield f'http://127.0.0.1:{server.server_port}'
    finally:server.shutdown();thread.join(timeout=3)


def calendar_fixture(factory,setup):
    token,user=accept(factory,setup['invite'])
    today=utcnow().astimezone(ZoneInfo('America/Los_Angeles')).date();month=month_shift(today,0)
    with factory.begin() as db:
        admin=OwnerAdmin(db,'staff:1');grant=db.get(PropertyGrant,setup['grant']);grant.reporting_from=month_shift(today,-24);grant.reporting_until=None
        other=admin.create_property({'name':'Willow Lodge','timezone':'America/Los_Angeles','listing_ids':[303],'single_unit_confirmed':True},{303})['id']
        admin.grant({'account_id':setup['account'],'property_id':other,'reporting_from':month_shift(today,-24).isoformat()})
        for prop in [setup['property'],other]:
            for offset in range(-12,13):
                m=month_shift(month,offset);end=month_shift(m,1)
                bookings=[]
                for n,(a,b,name) in enumerate([(-2,3,'Alex Rivera'),(3,5,'Jamie Morgan'),(6,10,'Taylor Bennett'),(7,9,'Overlapping stay'),(17,21,'<img src=x onerror=alert(1)>'),(25,36,'A very long synthetic guest name for mobile layout')]):
                    bookings.append(dict(reservation_id=n+1,arrival_date=m+timedelta(days=a),departure_date=m+timedelta(days=b),status='confirmed',guest_name=name))
                calendars=[dict(calendar_date=d,snapshot_date=today,status='available',is_available=True) for d in days(m,end)]
                payload=build_calendar(m,today,bookings,calendars)
                payload['days'][12]['state']='blocked';payload['days'][13]['state']='unknown'
                db.add(CalendarMonth(property_id=prop,month=m,prepared_at=utcnow(),source_fresh=True,calendar=payload))
    return token,month


@pytest.mark.parametrize('engine_name',['chromium','webkit'])
@pytest.mark.parametrize('scheme',['light','dark'])
@pytest.mark.parametrize('width',[1280,390])
def test_owner_calendar_views_navigation_privacy(app,factory,setup,engine_name,scheme,width):
    from playwright.sync_api import sync_playwright,expect
    from owner_portal.mount import OwnerMount
    app.config.update(OWNER_PATH_PREFIX='/owners',APPLICATION_ROOT='/owners');app.wsgi_app=OwnerMount(app.wsgi_app,'/owners')
    token,month=calendar_fixture(factory,setup)
    with http_server(app) as origin,sync_playwright() as p:
        browser=getattr(p,engine_name).launch();context=browser.new_context(viewport={'width':width,'height':900},color_scheme=scheme)
        auth=cookie(app,{'owner_token':token,'csrf':'synthetic-csrf'},origin);auth['secure']=False;context.add_cookies([auth])
        page=context.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
        page.goto(origin+'/owners/');page.get_by_role('button',name='Calendar',exact=True).click()
        expect(page.locator('.oc-timeline-row')).to_have_count(2)
        expect(page.locator('#ov-calendar')).to_contain_text('Alex Rivera')
        assert page.locator('#ov-calendar img').count()==0
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        assert page.locator('.oc-scroll').evaluate('(el)=>el.scrollWidth>el.clientWidth')
        # Frozen labels remain usable at the far end of the timeline.
        page.locator('.oc-scroll').evaluate('(el)=>el.scrollLeft=el.scrollWidth')
        expect(page.get_by_role('button',name='Cedar House →',exact=True)).to_be_in_viewport()
        page.locator('.oc-scroll').evaluate('(el)=>el.scrollLeft=0')
        output=Path('output/owner-847-calendar');output.mkdir(parents=True,exist_ok=True)
        page.screenshot(path=str(output/f'{engine_name}-{scheme}-{width}-timeline.png'),full_page=True)
        page.get_by_role('button',name='Cedar House →',exact=True).click()
        expect(page.locator('.oc-month')).to_be_visible()
        expect(page.locator('.oc-timeline')).to_have_count(0)
        expect(page.locator('.oc-state-blocked')).not_to_have_count(0)
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        page.get_by_text('View stay list (6)',exact=True).click()
        expect(page.locator('.oc-stay-list')).to_contain_text('A very long synthetic guest name for mobile layout')
        page.screenshot(path=str(output/f'{engine_name}-{scheme}-{width}-month.png'),full_page=True)
        page.get_by_role('button',name='Next month',exact=True).click()
        expect(page.locator('#ov-month')).to_have_value(month_shift(month,1).isoformat()[:7]);expect(page.locator('.oc-month')).to_be_visible()
        page.get_by_role('button',name='This month',exact=True).click()
        expect(page.locator('#ov-month')).to_have_value(month.isoformat()[:7]);expect(page.locator('.oc-month')).to_be_visible()
        page.get_by_label('Month',exact=True).select_option(month_shift(month,12).isoformat()[:7])
        expect(page.locator('.oc-month')).to_be_visible();expect(page.get_by_role('button',name='Next month',exact=True)).to_be_disabled()
        page.get_by_role('button',name='All properties',exact=False).click();expect(page.locator('.oc-timeline-row')).to_have_count(2)
        page.get_by_role('button',name='Snapshot',exact=True).click();expect(page.locator('#ov-calendar')).to_be_hidden()
        page.get_by_role('button',name='Calendar',exact=True).click();expect(page.locator('.oc-timeline-row')).to_have_count(2)
        assert not errors
        browser.close()
