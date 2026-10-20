"""Real application authentication, PostgreSQL transactions and worker concurrency."""
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import select
from sqlalchemy.schema import CreateSchema

from .conftest import email, reply


@pytest.fixture(scope="session")
def app(engine):
    from dashboard.app import create_app
    from dashboard.auth.models import Base as AuthBase, User, UserFeaturePermission, get_session
    from database.models import Listing, get_session as main_session
    # Existing startup creates shared tables through AuthBase; precreate all of
    # its schemas exactly as the existing procurement integration suite does.
    with engine.begin() as conn:
        for schema in {t.schema for t in AuthBase.metadata.tables.values() if t.schema}:
            conn.execute(CreateSchema(schema, if_not_exists=True))
    app = create_app()
    app.config.update(TESTING=True)
    users = {}
    with get_session() as db:
        for name, role, approved in (("admin","admin",True),("agent","user",True),("denied","user",True),("pending","user",False)):
            address = f"inquiry-qa-{name}@example.invalid"
            user = db.scalar(select(User).where(User.email == address))
            if not user:
                user = User(email=address,name=name.title(),role=role,is_approved=approved)
                db.add(user); db.flush()
            users[name] = user.user_id
            permission = db.scalar(select(UserFeaturePermission).where(UserFeaturePermission.user_id == user.user_id, UserFeaturePermission.feature_key == "inquiries"))
            if not permission:
                db.add(UserFeaturePermission(user_id=user.user_id,feature_key="inquiries",is_enabled=name in ("admin","agent")))
        db.commit()
    with main_session(None) as db:
        if not db.get(Listing,101):
            db.add(Listing(listing_id=101,name="Fair Haven",internal_listing_name="Fair Haven",timezone_name="America/Los_Angeles"))
            db.commit()
    app.qa_users = users
    return app


def client(app, role="admin"):
    c = app.test_client()
    with c.session_transaction() as s:
        s.update(logged_in=True,user_id=app.qa_users[role],inquiries_csrf="qa-csrf")
    return c


def post(c,path,data,method="POST",csrf=True):
    return c.open("/inquiries/api/"+path,method=method,json=data,headers={"X-CSRF-Token":"qa-csrf"} if csrf else {})


def test_anonymous_unapproved_and_missing_permission_denied(app, factory):
    assert app.test_client().get('/inquiries/').status_code == 302
    assert app.test_client().get('/inquiries/api/leads').status_code == 401
    for role in ('pending','denied'):
        c = client(app,role)
        assert c.get('/inquiries/').status_code in (302,403)
        for path in ('leads','bootstrap','failures'):
            assert c.get('/inquiries/api/'+path).status_code == 403


def test_page_is_native_and_guest_records_are_not_cached(app,factory):
    response = client(app).get('/inquiries/')
    assert response.status_code == 200
    assert b'id="inquiryApp"' in response.data and b'app-sidebar' in response.data
    assert response.headers['Cache-Control'] == 'private, no-store'


def test_browser_writes_require_csrf_and_object_json(app,factory):
    c = client(app)
    assert post(c,'intake',email(),csrf=False).status_code == 403
    assert post(c,'intake',[]).status_code == 400
    assert c.post('/inquiries/api/intake',data='{',content_type='application/json',headers={'X-CSRF-Token':'qa-csrf'}).status_code == 400


def test_large_submission_rejected(app,factory):
    assert post(client(app),'intake',email(body='x'*1100000)).status_code == 413


def test_real_app_agent_can_assign_and_requires_followup_accountability(app,factory):
    c = client(app,'agent')
    result = post(c,'intake',email())
    assert result.status_code == 200 and result.json['processing_status'] == 'processed'
    lead_id = result.json['lead_id']
    assert post(c,f'leads/{lead_id}',{'version':1,'status':'Follow-up Required'},method='PATCH').status_code == 400
    saved = post(c,f'leads/{lead_id}',{'version':1,'status':'Follow-up Required','assigned_user_id':app.qa_users['agent'],
                                      'next_action':'Call guest','follow_up_at':'2026-10-21T10:00:00-07:00'},method='PATCH')
    assert saved.status_code == 200 and saved.json['version'] == 2
    assert post(c,f'leads/{lead_id}',{'version':1,'internal_notes':'stale'},method='PATCH').status_code == 409


def test_admin_settings_and_failures_denied_to_agents(app,factory):
    c = client(app,'agent')
    assert c.get('/inquiries/api/failures').status_code == 403
    assert post(c,'settings',{'version':1,'reminder_minutes':30},method='PATCH').status_code == 403
    assert post(c,'failures/1/retry',{}).status_code == 403


def test_failure_commits_original_and_admin_can_retry(app,factory):
    c = client(app)
    response = post(c,'intake',email(extraction={'reference':'EH-ABC12345','fields':{'email':'invalid'}}))
    assert response.json['processing_status'] == 'failed'
    failures = c.get('/inquiries/api/failures').json['failures']
    assert len(failures) == 1 and failures[0]['body'] == email()['body']
    result = post(c,f'failures/{failures[0]["id"]}/retry',{})
    assert result.json['processing_status'] == 'processed'
    assert c.get('/inquiries/api/failures').json['failures'] == []


def test_simultaneous_intake_retries_create_one_message_and_activity(app,factory):
    def ingest(_):
        return post(client(app),'intake',email())
    with ThreadPoolExecutor(max_workers=5) as pool:
        results = list(pool.map(ingest,range(5)))
    assert [r.status_code for r in results] == [200]*5
    assert len({r.json['lead_id'] for r in results}) == 1
    lead = client(app).get('/inquiries/api/leads/1').json
    assert len(lead['messages']) == 1 and len(lead['activities']) == 1


def test_concurrent_staff_updates_reject_stale_writer(app,factory):
    c = client(app)
    lead_id = post(c,'intake',email()).json['lead_id']
    def save(note):
        return post(client(app),f'leads/{lead_id}',{'version':1,'internal_notes':note},method='PATCH')
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(save,('First update','Second update')))
    assert sorted(r.status_code for r in results) == [200,409]


def test_restricted_guest_issue_key_cannot_read_or_ingest(app,factory):
    from dashboard.auth.api_keys import create_api_key
    key = create_api_key('inquiry QA restricted key',app.qa_users['admin'],scopes=['guest_issues:read'])
    c = app.test_client()
    assert c.get('/inquiries/api/leads',headers={'X-API-Key':key}).status_code == 403
    assert c.post('/inquiries/api/intake',json=email(),headers={'X-API-Key':key}).status_code == 403


def test_supported_full_access_key_can_ingest_but_not_administer(app,factory):
    from dashboard.auth.api_keys import create_api_key
    key = create_api_key('inquiry QA worker key',app.qa_users['admin'])
    c = app.test_client()
    result = c.post('/inquiries/api/intake',json=email(internal_test=True),headers={'X-API-Key':key})
    assert result.status_code == 200 and result.json['processing_status'] == 'processed'
    assert c.get('/inquiries/api/leads?record_scope=internal-tests',headers={'X-API-Key':key}).json['total'] == 1
    assert c.get('/inquiries/api/failures',headers={'X-API-Key':key}).status_code == 403


def test_internal_tests_require_admin_and_are_separate_from_genuine_statistics(app, factory):
    agent, admin = client(app, 'agent'), client(app)
    data = email(internal_test=True)
    assert post(agent, 'intake', data).status_code == 403
    assert agent.get('/inquiries/api/leads?record_scope=internal-tests').status_code == 403
    result = post(admin, 'intake', data)
    assert result.status_code == 200 and result.json['processing_status'] == 'processed'
    lead_id = result.json['lead_id']
    genuine = admin.get('/inquiries/api/leads').json
    assert genuine['leads'] == [] and genuine['stats']['total'] == 0
    tests = admin.get('/inquiries/api/leads?record_scope=internal-tests').json
    assert tests['total'] == 1 and tests['leads'][0]['is_test'] is True
    assert tests['stats'] == genuine['stats']
    assert post(admin, f'leads/{lead_id}', {'version':1, 'is_test':False}, method='PATCH').status_code == 400
    assert post(admin, f'leads/{lead_id}', {'version':1, 'status':'Booked'}, method='PATCH').status_code == 200
    metrics = admin.get('/inquiries/api/leads?record_scope=internal-tests').json['stats']
    assert metrics['booked'] == 0 and metrics['conversion_rate'] is None
    assert post(admin, 'intake', data).json['replayed'] is True


def test_failed_internal_test_retains_classification_on_admin_retry(app, factory):
    c = client(app)
    data = email(internal_test=True, extraction={'reference':'EH-ABC12345','fields':{'email':'invalid'}})
    result = post(c, 'intake', data).json
    assert result['processing_status'] == 'failed'
    retried = post(c, f'failures/{result["message_id"]}/retry', {}).json
    assert retried['processing_status'] == 'processed'
    assert c.get(f'/inquiries/api/leads/{retried["lead_id"]}').json['is_test'] is True
    assert c.get('/inquiries/api/leads').json['stats']['total'] == 0
