import os
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import select

from owner_portal.admin import OwnerAdmin
from owner_portal.calculations import month_shift
from owner_portal.models import Membership, PerformanceMonth, PropertyGrant, PublishedStatement, utcnow
from .test_access import accept
from .test_browser import cookie, server, screenshot
from .test_publication import bookkeeping
from .test_browser import staff_browser_app, staff_app

pytestmark=pytest.mark.skipif(os.getenv('OWNER_BROWSER_QA')!='1',reason='Opt-in local Chromium tests.')


def test_populated_visual_dashboard_and_statements(app,factory,setup):
    from playwright.sync_api import sync_playwright,expect
    from owner_portal.mount import OwnerMount
    app.config.update(OWNER_PATH_PREFIX='/owners',APPLICATION_ROOT='/owners')
    app.wsgi_app=OwnerMount(app.wsgi_app,'/owners')
    token,user=accept(factory,setup['invite']);today=utcnow().date();thismonth=month_shift(today,0)
    with factory.begin() as db:
        admin=OwnerAdmin(db,'staff:1')
        db.scalar(select(Membership).where(Membership.user_id==user)).statements=True
        grant=db.get(PropertyGrant,setup['grant']);grant.reporting_from=month_shift(today,-24);grant.reporting_until=None
        ids=[setup['property']]
        for listing,name in [(303,'Willow Lodge'),(404,'Pine Cottage')]:
            prop=admin.create_property({'name':name,'timezone':'America/Los_Angeles','listing_ids':[listing],'single_unit_confirmed':True},{listing})['id']
            admin.grant({'account_id':setup['account'],'property_id':prop,'reporting_from':month_shift(today,-24).isoformat()});ids.append(prop)
        for prop in ids:
            for offset in range(-14,4):
                month=month_shift(today,offset);capacity=(month_shift(month,1)-month).days
                revenue=6000+offset*50;stayed=revenue if offset<0 else 4500 if offset==0 else 0
                db.add(PerformanceMonth(property_id=prop,month=month,as_of=today,prepared_at=utcnow(),metrics={'stayed':stayed,'booked':revenue-stayed,'revenue':revenue,'low':revenue if offset<0 else revenue+500,'high':revenue if offset<0 else revenue+1500,'nights':22,'sellable':capacity,'blocked':0,'unknown_nights':0,'nights_low':22 if offset<0 else 25,'nights_high':22 if offset<0 else 28,'source_fresh':True,'forecast_reason':None}))
        db.add(PublishedStatement(account_id=setup['account'],account_name='Bennett household',revision_id=1,version=1,period_start=month_shift(today,-1),period_end=thismonth,property_ids=ids,property_names=['Cedar House','Willow Lodge','Pine Cottage'],revenue=18000,management_fee=3600,expenses=1000,net_income=13400,published_by='staff:1'))
    with server(app) as origin,sync_playwright() as p:
        browser=p.chromium.launch();context=browser.new_context(ignore_https_errors=True,viewport={'width':1280,'height':1100});page=context.new_page();errors=[]
        page.on('pageerror',lambda e:errors.append(str(e)))
        page.on('console',lambda m:errors.append(m.text) if m.type=='error' else None)
        context.add_cookies([cookie(app,{'owner_token':token,'csrf':'synthetic-csrf'},origin)])
        page.goto(origin+'/owners/')
        expect(page.get_by_role('heading',name='Your properties',exact=True)).to_be_visible()
        expect(page.locator('#ov-revenue-value')).to_have_text('$19.5k–$22.5k')
        expect(page.locator('#ov-chart rect')).not_to_have_count(0)
        assert page.get_by_text('Private Pine House',exact=True).count()==0
        screenshot(page,'owner-dashboard-desktop.png')
        page.get_by_label('Property',exact=True).select_option(str(setup['property']))
        expect(page.get_by_role('heading',name='Cedar House',exact=True)).to_be_visible()
        page.get_by_label('Property',exact=True).select_option('all')
        expect(page.get_by_role('heading',name='Your properties',exact=True)).to_be_visible()
        page.set_viewport_size({'width':390,'height':844})
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        screenshot(page,'owner-dashboard-mobile.png')
        page.get_by_role('button',name='Statements',exact=True).click()
        expect(page.locator('#ov-statements .ov-statement-value')).to_contain_text('$13,400')
        screenshot(page,'owner-statements-mobile.png')
        page.get_by_role('link',name='Open statement').click()
        expect(page.get_by_role('button',name='Print / save PDF')).to_be_visible()
        with page.expect_download() as info:page.get_by_role('link',name='Download HTML').click()
        downloaded=info.value.path().read_text()
        assert 'csrf' not in downloaded and 'PRIVATE' not in downloaded and '$13,400.00' in downloaded
        assert not errors
        browser.close()


def test_staff_review_publish_and_withdraw_browser(staff_browser_app,factory,setup,bookkeeping):
    from playwright.sync_api import sync_playwright,expect
    with server(staff_browser_app) as origin,sync_playwright() as p:
        browser=p.chromium.launch();context=browser.new_context(ignore_https_errors=True,viewport={'width':1280,'height':900});page=context.new_page()
        context.add_cookies([cookie(staff_browser_app,{'user_id':1,'logged_in':True,'owner_access_csrf':'synthetic-csrf'},origin)])
        page.goto(origin+'/owner-access/statements')
        page.get_by_role('button',name='Review & publish').click()
        expect(page.get_by_role('dialog')).to_be_visible()
        screenshot(page,'owner-statement-publication-review.png')
        page.get_by_role('checkbox').check()
        page.get_by_role('button',name='Publish finalized statement',exact=True).click()
        expect(page.get_by_text('Finalized statement published.',exact=True)).to_be_visible()
        page.on('dialog',lambda dialog:dialog.accept())
        page.get_by_role('button',name='Withdraw publication').click()
        expect(page.get_by_text('Publication withdrawn.',exact=True)).to_be_visible()
        browser.close()
