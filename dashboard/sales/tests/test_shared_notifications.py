"""Optional integration contract test against the installed shared #817 package."""
import os
from types import SimpleNamespace
import pytest
from sqlalchemy import select,text
from .conftest import run,lead,NOW


def test_shared_capture_and_stale_context(service,engine,monkeypatch):
    import dashboard.notifications
    path=os.environ.get('SHARED_NOTIFICATIONS_QA_PATH')
    if path and path+"/notifications" not in dashboard.notifications.__path__: dashboard.notifications.__path__.append(path+"/notifications")
    pytest.importorskip('dashboard.notifications.contracts')
    from dashboard.notifications.models import migrate,NotificationEvent,Base
    from dashboard.notifications.contracts import Registry
    from dashboard.sales.notifications import capture_pending,register,current_context,SalesAudiencePolicy
    import dashboard.sales.notifications as adapter
    import dashboard.sales.models as models
    migrate(engine)
    with engine.begin() as conn:conn.execute(text('TRUNCATE '+', '.join('notifications.'+t.name for t in Base.metadata.sorted_tables)+' CASCADE'))
    monkeypatch.setattr(models,'utcnow',lambda:NOW)
    person=SimpleNamespace(whatsapp_notifications_enabled=True,whatsapp_number='+15555550100')
    monkeypatch.setattr(adapter,'eligible_users',lambda db:{1:{'id':1,'name':'Sales','manager':True,'user':person}})
    d=lead(service)
    assert capture_pending(service)==1
    assert capture_pending(service)==0
    event=service.db.scalar(select(NotificationEvent))
    registry=register(Registry());assert registry.get(event.event_type,1)
    context=current_context(service.db,event)
    assert context.path==f'/sales/leads/{d["id"]}' and context.responsible[0].principal=='user:1'
    from dashboard.notifications.models import Destination
    from dashboard.notifications.planner import plan_event
    service.db.add(Destination(provider='openclaw', account='qa', target='+15555550100', kind='person', principal='user:1',
        verified_at=NOW, allowed_resource_types=['sales.deal'], include_general=True, created_at=NOW))
    service.db.flush()
    policy=SalesAudiencePolicy(service.db)
    result=plan_event(service.db,event.id,registry,policy,origin='https://example.invalid')
    assert result.status=='preview'

    assert policy.eligibility(SimpleNamespace(kind='person',principal='user:1',target='+15555550100'),event,context).allowed
    assert not policy.eligibility(SimpleNamespace(kind='group'),event,context).allowed
    run(service,'transition',d['id'],version=d['version'],outcome='lost',reason='Not proceeding')
    assert current_context(service.db,event).blocked_reason=='sales_notice_superseded'
