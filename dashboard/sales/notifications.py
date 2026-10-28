"""Durable reminders and an adapter for the shared notification framework (#817).

Imports of that framework are lazy: this module works before the shared release
is installed. Capture/preview never sends. Delivery uses the shared worker's
existing destination allowlists, subscriptions, preferences, and rollout gate.
"""
from datetime import timedelta
from sqlalchemy import select
from .models import Agreement, Deal, Handoff, Notice, Task, Settings
from .validation import fingerprint

EVENTS={'created':'Opportunity captured','assigned':'Salesperson assigned','reply':'Owner reply needs attention',
        'stage_changed':'Sales stage changed','handoff':'Signed agreement needs operations acceptance',
        'handoff_updated':'Operations handoff updated','reminder':'Sales follow-up is overdue',
        'escalation':'Sales follow-up needs management attention','unassigned':'Opportunity needs an owner',
        'nurture':'Nurture opportunity is due for review'}


def relevant(db, notice, now):
    deal=db.get(Deal,notice.deal_id,populate_existing=True)
    if not deal or deal.is_test: return False
    settings=db.get(Settings,1,populate_existing=True)
    delay = getattr(settings, {'reminder':'reminder_minutes','escalation':'escalation_minutes','unassigned':'unassigned_minutes','nurture':'reminder_minutes'}.get(notice.event_type,''), 0)
    if delay is None: return False
    if notice.task_id:
        task=db.get(Task,notice.task_id,populate_existing=True)
        return bool(task and task.status=='open' and task.version==notice.task_version and task.due_at+timedelta(minutes=delay)<=now and deal.outcome=='open')
    if notice.event_type=='unassigned': return deal.outcome=='open' and deal.assigned_user_id is None
    if notice.event_type=='reply': return deal.needs_reply
    if notice.event_type=='nurture': return deal.outcome=='nurture' and deal.revisit_at and deal.revisit_at<=now and notice.payload.get('revisit_at')==deal.revisit_at.isoformat()
    if notice.event_type in ('handoff','handoff_updated'):
        agreement=db.scalar(select(Agreement).where(Agreement.deal_id==deal.id))
        handoff=db.get(Handoff,agreement.id) if agreement else None
        return bool(handoff and (handoff.status=='pending' if notice.event_type=='handoff' else handoff.status!='complete'))
    return deal.outcome=='open'


def generate(service):
    """Run on a schedule even when no browser is open. Caller owns transaction."""
    service.lock_key('sales-mutations')
    settings=service.settings()
    for task,deal in service.db.execute(select(Task,Deal).join(Deal,Deal.id==Task.deal_id).where(Task.status=='open',Deal.outcome=='open',Deal.is_test.is_(False))):
        for event,delay in (('reminder',settings.reminder_minutes),('escalation',settings.escalation_minutes)):
            if delay is not None and service.now>=task.due_at+timedelta(minutes=delay):
                service.notice(deal,event,key=f'{event}:task:{task.id}:{task.version}',task=task)
    for deal in service.db.scalars(select(Deal).where(Deal.is_test.is_(False),Deal.outcome.in_(('open','nurture')))):
        if settings.unassigned_minutes is not None and deal.outcome=='open' and not deal.assigned_user_id and service.now>=deal.created_at+timedelta(minutes=settings.unassigned_minutes):
            service.notice(deal,'unassigned',key=f'unassigned:{deal.id}')
        if settings.reminder_minutes is not None and deal.outcome=='nurture' and deal.revisit_at and service.now>=deal.revisit_at:
            service.notice(deal,'nurture',key=f'nurture:{deal.id}:{deal.revisit_at.isoformat()}',payload={'revisit_at':deal.revisit_at.isoformat()})
    service.db.flush()
    for row in service.db.scalars(select(Notice).where(Notice.status=='pending').with_for_update()):
        if not relevant(service.db,row,service.now): row.status='superseded'
    service.db.flush()


def definitions():
    from dashboard.notifications.contracts import EventDefinition
    return [EventDefinition(name='sales.deal.'+key,resource_type='sales.deal',headline=title,
                            fields=(),context=current_context,notify_responsible=True,allow_empty_changes=True)
            for key,title in EVENTS.items()]


def register(registry):
    for definition in definitions(): registry.register(definition)
    return registry


def capture_pending(service,limit=100):
    from dashboard.notifications.contracts import Actor
    from dashboard.notifications.outbox import capture
    definitions_by_name={d.name:d for d in definitions()}
    service.lock_key('sales-mutations')
    rows=service.db.scalars(select(Notice).where(Notice.status=='pending').order_by(Notice.id).limit(limit).with_for_update(skip_locked=True)).all()
    for row in rows:
        if not relevant(service.db,row,service.now):
            row.status='superseded';continue
        deal=service.db.get(Deal,row.deal_id)
        event=capture(service.db,definitions_by_name['sales.deal.'+row.event_type],resource=f'sales.deal:{deal.id}',
            command_key='sales-notice:'+str(row.id),actor=Actor(row.payload['actor'],row.payload['actor_name'][:200],source='sales'),
            changes={},snapshot={'title':deal.title,'code':f'EH-{deal.id:04d}'},occurred_at=row.created_at,
            facts={'notice_id':row.id})
        row.shared_event_id,row.status=event.id,'captured'
    service.db.flush()
    return len(rows)


def current_context(db,event):
    from dashboard.notifications.contracts import CurrentContext,ResponsiblePerson
    from .models import utcnow
    notice=db.get(Notice,event.data['facts']['notice_id'],populate_existing=True)
    deal=db.get(Deal,int(event.resource_key.split(':')[1]),populate_existing=True)
    users=eligible_users(db)
    owners=[];action='Review the opportunity and its next action.'
    if notice.event_type in ('escalation','unassigned'):
        owners=[u for u in users.values() if u['manager']]
        action='Review ownership and the overdue follow-up; coordinate a next step.'
    elif notice.event_type in ('handoff','handoff_updated'):
        agreement=db.scalar(select(Agreement).where(Agreement.deal_id==deal.id)); handoff=db.get(Handoff,agreement.id) if agreement else None
        if handoff and handoff.assigned_user_id in users: owners=[users[handoff.assigned_user_id]]
        action='Accept the operations handoff and confirm the onboarding next step.' if handoff and handoff.status=='pending' else 'Review the operations checklist and target launch.'
    else:
        task=db.get(Task,notice.task_id,populate_existing=True) if notice.task_id else None
        identity=task.assigned_user_id if task else deal.assigned_user_id
        if identity in users: owners=[users[identity]]
        if task: action=task.title
        elif notice.event_type=='reply': action='Review the owner reply and record the next action.'
        elif notice.event_type=='nurture': action='Review the nurture opportunity and decide whether to reopen it.'
    people=tuple(ResponsiblePerson(f'user:{u["id"]}',u['name']) for u in owners)
    blocked='sales_notice_superseded' if not relevant(db,notice,utcnow()) else 'responsibility_unresolved' if not people else None
    return CurrentContext(fingerprint([deal.version,notice.id,blocked,action,[p.principal for p in people]]),f'EH-{deal.id:04d}',deal.title,
                          f'/sales/leads/{deal.id}',property_label='Owner sales',responsible=people,next_action=action,
                          requires_action=True,blocked_reason=blocked,action_owners=people)


def eligible_users(db):
    from dashboard.auth.models import User,UserFeaturePermission
    users=db.scalars(select(User).where(User.is_approved.is_(True),User.deleted_at.is_(None))).all()
    grants={(p.user_id,p.feature_key):p.is_enabled for p in db.scalars(select(UserFeaturePermission).where(UserFeaturePermission.feature_key.in_(('sales','sales_management'))))}
    return {u.user_id:{'id':u.user_id,'name':u.name or u.email,'manager':u.is_admin() or grants.get((u.user_id,'sales_management'),False),'user':u}
            for u in users if u.is_admin() or grants.get((u.user_id,'sales'),False)}


class SalesAudiencePolicy:
    """Private recipients only until a separately reviewed group policy exists."""
    def __init__(self,db,fallback=None): self.db,self.fallback=db,fallback
    def eligibility(self,destination,event,context):
        from dashboard.notifications.contracts import Eligibility
        if not event.resource_key.startswith('sales.deal:'):
            return self.fallback.eligibility(destination,event,context) if self.fallback else Eligibility(False,'unsupported_resource')
        if destination.kind!='person': return Eligibility(False,'sales_private_audience_required')
        user=next((v['user'] for v in eligible_users(self.db).values() if f'user:{v["id"]}'==destination.principal),None)
        allowed=bool(user and user.whatsapp_notifications_enabled and user.whatsapp_number and destination.target==user.whatsapp_number)
        return Eligibility(allowed,'current_sales_access_and_preference' if allowed else 'sales_access_or_preference_denied')
    def can_follow(self,actor,destination,scope):
        if not scope.key.startswith('sales.deal:'): return bool(self.fallback and self.fallback.can_follow(actor,destination,scope))
        return destination.kind=='person' and destination.principal==actor.principal and actor.principal in {f'user:{i}' for i in eligible_users(self.db)}
    def mention_is_current(self,destination,mapping,event,context):
        return bool(not event.resource_key.startswith('sales.deal:') and self.fallback and self.fallback.mention_is_current(destination,mapping,event,context))
    def context_for_destination(self,destination,event,context):
        if not event.resource_key.startswith('sales.deal:') and self.fallback and hasattr(self.fallback,'context_for_destination'):
            return self.fallback.context_for_destination(destination,event,context)
        return context
