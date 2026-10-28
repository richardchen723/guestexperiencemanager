from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from uuid import uuid4
import pytest
from sqlalchemy import select,text,func
from sqlalchemy.exc import DBAPIError
from dashboard.sales import validation as v,imports
from dashboard.sales.models import Activity,Deal,Notice,Intake,Proposal,Contact,Settings,Command
from dashboard.sales.service import SalesService
from dashboard.sales.notifications import generate,relevant
from .conftest import NOW,USERS,run,lead,prepared,sent,sign


def test_portfolio_relationships_and_unknown_money(service):
    d=lead(service)
    assert len(d['properties'])==2 and len(d['contacts'])==1
    assert d['annual_management_revenue'] is None
    assert service.listing({'search':'alex@example.invalid'})['total']==1
    assert service.listing({'market':'Whidbey Island'})['total']==1
    d=run(service,'relationship',d['id'],version=d['version'],kind='contact',record={'name':'Jordan Bennett','property_ids':[d['properties'][0]['id']]})
    assert len(d['contacts'])==2
    assert service.reports({})['accounts']==1
    assert service.reports({})['properties']==2


def test_active_invariant_and_stale_update(service):
    d=lead(service,next_action=None,assigned_user_id=None)
    with pytest.raises(v.SalesError,match='assigned salesperson'): run(service,'transition',d['id'],version=d['version'],stage=1)
    d=run(service,'patch',d['id'],version=d['version'],assigned_user_id=1,next_action={'title':'Call','due_local':'2026-10-29T10:00','assigned_user_id':1})
    d=run(service,'transition',d['id'],version=d['version'],stage=1)
    with pytest.raises(v.SalesError,match='changed'): run(service,'patch',d['id'],version=1,summary='Stale')
    with pytest.raises(v.SalesError,match='primary next action'): run(service,'task_action',d['id'],version=d['version'],task_id=d['primary_task']['id'],action='complete',outcome='Done')
    assert service.detail(d['id'])['primary_task']['status']=='open'


def test_qualification_and_stage_gates(service):
    d=lead(service)
    with pytest.raises(v.SalesError,match='Approve qualification'): run(service,'transition',d['id'],version=d['version'],stage=2)
    with pytest.raises(v.SalesError,match='poor-fit'): run(service,'qualify',d['id'],version=d['version'],fit='poor',decision='approved',readiness='yes',owner_needs='yes',operational_fit='yes',owner_expectations='yes')
    d=run(service,'transition',d['id'],version=d['version'],stage=1)
    with pytest.raises(v.SalesError,match='reason'): run(service,'transition',d['id'],version=d['version'],stage=0)


def test_permissions_block_outbound_and_merge_preserves_optout(service):
    d=lead(service);cid=d['contacts'][0]['id']
    with pytest.raises(v.SalesError,match='permission'): run(service,'add_activity',d['id'],version=d['version'],kind='email',direction='outbound',contact_id=cid,body='Hello')
    d=run(service,'permission',d['id'],version=d['version'],contact_id=cid,channel='email',status='opted_out',basis='Asked to stop')
    second=lead(service)
    matches=service.deal_duplicates(second['id'])['candidates']; assert any(c['id']==cid for c in matches)
    c2=second['contacts'][0]
    run(service,'merge',kind='contact',from_id=cid,into_id=c2['id'],from_version=d['contacts'][0]['version'],into_version=c2['version'],reason='Reviewed owner identity')
    assert service.permission_state(c2['id'],'email')['status']=='opted_out'
    service.manager=False
    current=service.detail(second['id'])
    with pytest.raises(v.SalesError,match='management'): run(service,'permission',current['id'],version=current['version'],contact_id=c2['id'],channel='email',status='allowed',basis='Attempt overwrite')


def test_phone_requires_dnc_and_expiry(service):
    d=lead(service);cid=d['contacts'][0]['id']
    d=run(service,'permission',d['id'],version=d['version'],contact_id=cid,channel='phone',status='allowed',basis='Requested call',dnc_result='unknown')
    with pytest.raises(v.SalesError,match='DNC'): service.assert_contact_allowed(cid,'phone')
    d=run(service,'permission',d['id'],version=d['version'],contact_id=cid,channel='phone',status='allowed',basis='Reviewed',dnc_result='clear',expires_at='2026-10-29T00:00:00Z')
    service.assert_contact_allowed(cid,'phone');service.now+=timedelta(days=2)
    with pytest.raises(v.SalesError): service.assert_contact_allowed(cid,'phone')


def test_inbound_does_not_reset_last_outbound(service):
    d=prepared(service);out=d['last_outbound_at']
    service.now+=timedelta(hours=1)
    d=run(service,'add_activity',d['id'],version=d['version'],kind='email',direction='inbound',body='Owner reply')
    assert d['last_outbound_at']==out and d['last_inbound_at']!=out and d['needs_reply']


def test_proposal_approval_revision_and_history_immutable(service):
    d=prepared(service);p=d['proposals'][0]
    with pytest.raises(v.SalesError,match='approve'): run(service,'proposal_action',d['id'],version=d['version'],proposal_id=p['id'],action='sent',note='Sent',contact_id=d['contacts'][0]['id'])
    service.manager=False
    with pytest.raises(v.SalesError,match='management'): run(service,'proposal_action',d['id'],version=d['version'],proposal_id=p['id'],action='approved',note='Approve')
    service.manager=True
    with pytest.raises(DBAPIError):
        with service.db.begin_nested(): service.db.execute(text('UPDATE sales.proposals SET document_url=:url WHERE id=:id'),{'id':p['id'],'url':'https://example.invalid/edit'})
    assert service.db.get(Proposal,p['id']).terms['currency']=='USD'


def test_partial_win_and_durable_handoff(service):
    d=sent(service);result=sign(service,d)
    assert result['outcome']=='won' and result['board_stage']==6
    assert result['property_count']==1 and result['annual_management_revenue']=='20000.00'
    remaining=service.detail(result['remaining_deal_id'])
    assert remaining['stage']==0 and remaining['outcome']=='open' and remaining['property_count']==1
    assert remaining['source_id']==result['source_id']
    assert service.reports({})['signed_properties']==1 and service.reports({})['won_management_revenue']=='20000.00'
    with pytest.raises(v.SalesError): sign(service,result)
    service.actor='user:2';service.manager=False
    result=run(service,'onboarding',result['id'],version=result['version'],action='accept')
    assert result['board_stage']==7 and result['outcome']=='won'
    with pytest.raises(v.SalesError,match='checklist'): run(service,'onboarding',result['id'],version=result['version'],action='complete')
    result=run(service,'onboarding',result['id'],version=result['version'],action='complete',checklist=[{'title':'Launch verified','done':True}],listing_links=[{'property_id':result['agreement']['scope']['properties'][0]['property_id'],'listing_id':101}])
    assert result['handoff']['status']=='complete'
    assert service.reports({})['awaiting_launch']==0


def test_partial_win_requires_explicit_remaining_scope(service):
    d=sent(service)
    with pytest.raises(v.SalesError,match='remaining'): sign(service,d,remaining_action=None)
    assert service.detail(d['id'])['outcome']=='open'


def test_csv_preview_commit_replay_and_formula_escape(service):
    source=run(service,'source_update',name='Campaign',kind='campaign')['id']
    values={'source_id':source,'csv':'title,owner_name,contact_name,email,property_name\nPortfolio,Owner,Person,person@example.invalid,Cabin\n'}
    result=imports.preview(service,values)
    assert result['valid'] and service.db.scalar(select(func.count()).select_from(Deal))==0
    committed=imports.commit_csv(service,{**values,'preview_fingerprint':result['fingerprint'],'duplicates_reviewed':True})
    again=imports.commit_csv(service,{**values,'preview_fingerprint':result['fingerprint'],'duplicates_reviewed':True})
    assert committed['records'][0]['deal_id']==again['records'][0]['deal_id']
    assert imports.safe_cell('  =WEBSERVICE("url")').startswith("'")
    assert imports.safe_cell('normal')=='normal'


def test_intake_failure_preserves_original_and_retry(service):
    source=run(service,'source_update',name='Research',kind='research')['id']
    payload={'source_id':source,'external_id':'research-1','original':'Original malformed owner record','lead':{'title':'Owner','contacts':[{'name':'Person','email':'bad'}]}}
    result=imports.ingest(service,payload)
    assert result['status']=='failed' and service.db.get(Intake,result['intake_id']).original==payload
    retried=imports.retry(service,result['intake_id'],{'lead':{'title':'Corrected owner'}})
    assert retried['title']=='Corrected owner'
    assert imports.ingest(service,payload)['deal_id']==retried['id']
    with pytest.raises(v.SalesError,match='different data'): imports.ingest(service,{**payload,'original':'Changed'})


def test_notice_generation_replay_and_reschedule_supersession(service):
    d=lead(service)
    run(service,'update_settings',version=1,reminder_minutes=0,escalation_minutes=60)
    generate(service);generate(service)
    notices=service.db.scalars(select(Notice).where(Notice.event_type=='reminder')).all();assert len(notices)==1
    d=run(service,'task_action',d['id'],version=d['version'],task_id=d['primary_task']['id'],action='reschedule',due_at='2026-10-30T18:00:00Z')
    generate(service)
    assert not relevant(service.db,notices[0],service.now) and notices[0].status=='superseded'


def test_nurture_cancels_tasks_and_revisit(service):
    d=lead(service)
    d=run(service,'transition',d['id'],version=d['version'],outcome='nurture',reason='Call next quarter',revisit_local='2026-11-02T09:00')
    assert d['primary_task'] is None
    service.now+=timedelta(days=10)
    assert service.listing({'queue':'nurture_due'})['total']==1


def test_test_rows_excluded_and_sources_immutable(service):
    d=lead(service,is_test=True)
    assert service.reports({})['opportunities']==0 and service.listing({})['total']==0
    assert service.listing({'record_scope':'tests'})['total']==1
    with pytest.raises(v.SalesError,match='Unsupported'): run(service,'patch',d['id'],version=d['version'],source_id=100)
    service.manager=False
    with pytest.raises(v.SalesError):service.detail(d['id'])


def test_concurrent_replays_and_stale_edits(factory):
    key=str(uuid4())
    def capture(_):
        with factory() as db:
            svc=SalesService(db,actor='user:1',actor_name='Sales',users=USERS,now=NOW)
            result=svc.execute('create',{'command_id':key,'title':'Only once'},svc.create);db.commit();return result['id']
    with ThreadPoolExecutor(max_workers=4) as pool: ids=list(pool.map(capture,range(4)))
    assert len(set(ids))==1
    def edit(title):
        with factory() as db:
            svc=SalesService(db,actor='user:1',actor_name='Sales',users=USERS,now=NOW)
            try: result=svc.execute('patch',{'command_id':str(uuid4()),'version':1,'title':title},lambda values:svc.patch(ids[0],values));db.commit();return 200
            except v.SalesError as exc: db.rollback();return exc.status
    with ThreadPoolExecutor(max_workers=2) as pool: assert sorted(pool.map(edit,['One','Two']))==[200,409]


@pytest.mark.parametrize('wall',['2026-03-08T02:30','2026-11-01T01:30'])
def test_dst_requires_real_unambiguous_time(wall):
    with pytest.raises(v.SalesError):v.local_instant(wall,'America/Los_Angeles')
    if '11-01' in wall:
        assert v.local_instant(wall,'America/Los_Angeles',fold=1)-v.local_instant(wall,'America/Los_Angeles',fold=0)==timedelta(hours=1)

@pytest.mark.parametrize('amount',['NaN','Infinity','-1','2.345',True])
def test_money_rejects_invalid_values(amount):
    with pytest.raises(v.SalesError):v.money(amount,'Amount')


def test_contact_change_invalidates_old_permission_and_optout_cannot_be_laundered(service):
    d=lead(service);c=d['contacts'][0]
    d=run(service,'permission',d['id'],version=d['version'],contact_id=c['id'],channel='email',status='allowed',basis='Owner requested email')
    run(service,'edit_record','contact',c['id'],version=c['version'],email='different@example.invalid')
    assert service.permission_state(c['id'],'email')['status']=='needs_review'
    d=service.detail(d['id'])
    d=run(service,'permission',d['id'],version=d['version'],contact_id=c['id'],channel='email',status='opted_out',basis='Stop')
    service.manager=False
    with pytest.raises(v.SalesError,match='management'):run(service,'permission',d['id'],version=d['version'],contact_id=c['id'],channel='email',status='needs_review',basis='Reset')


def test_disabled_reminders_cancel_queued_notice(service):
    lead(service)
    run(service,'update_settings',version=1,reminder_minutes=0)
    generate(service)
    notice=service.db.scalar(select(Notice).where(Notice.event_type=='reminder'))
    run(service,'update_settings',version=2,reminder_minutes=None)
    generate(service)
    assert notice.status=='superseded'


def test_pipeline_honors_attention_filter_and_tasks_include_supporting_work(service):
    d=lead(service,next_action={'title':'Future primary','assigned_user_id':1,'due_at':'2026-11-05T16:00:00Z'})
    run(service,'task_action',d['id'],version=d['version'],action='create',title='Overdue supporting task',assigned_user_id=2,due_at='2026-10-27T18:00:00Z')
    assert sum(c['total'] for c in service.pipeline({'queue':'overdue'})['columns'])==0
    tasks=service.tasks_listing({'queue':'overdue','assigned_user_id':'2'})
    assert tasks['total']==1 and tasks['tasks'][0]['title']=='Overdue supporting task'


def test_replay_key_cannot_be_reused_for_different_data(service):
    key=str(uuid4())
    service.execute('create',{'command_id':key,'title':'One'},service.create)
    with pytest.raises(v.SalesError,match='different data'):
        service.execute('create',{'command_id':key,'title':'Two'},service.create)
