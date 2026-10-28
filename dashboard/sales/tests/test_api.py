"""Actual application authentication, CSRF, scopes, and CSV review proof."""
from uuid import uuid4
import pytest
from sqlalchemy import select
from sqlalchemy.schema import CreateSchema

@pytest.fixture(scope='session')
def app(engine):
    from dashboard.app import create_app
    from dashboard.auth.models import Base,User,UserFeaturePermission,get_session
    with engine.begin() as conn:
        for schema in {t.schema for t in Base.metadata.tables.values() if t.schema}: conn.execute(CreateSchema(schema,if_not_exists=True))
    app=create_app();app.config.update(TESTING=True)
    users={}
    with get_session() as db:
        for name,role,approved in [('admin','admin',True),('sales','user',True),('denied','user',True),('pending','user',False)]:
            email=f'sales-qa-{name}@example.invalid';user=db.scalar(select(User).where(User.email==email))
            if not user:
                user=User(email=email,name=name.title(),role=role,is_approved=approved);db.add(user);db.flush()
            users[name]=user.user_id
            grant=db.scalar(select(UserFeaturePermission).where(UserFeaturePermission.user_id==user.user_id,UserFeaturePermission.feature_key=='sales'))
            if not grant:db.add(UserFeaturePermission(user_id=user.user_id,feature_key='sales',is_enabled=name in ('sales','admin')))
        db.commit()
    app.qa_users=users
    return app


def client(app,role='admin'):
    c=app.test_client()
    with c.session_transaction() as s:s.update(logged_in=True,user_id=app.qa_users[role],sales_csrf='qa-csrf')
    return c


def post(c,path,data,method='POST',headers=None):
    return c.open('/sales/api/'+path,method=method,json={'command_id':str(uuid4()),**data},headers={'X-CSRF-Token':'qa-csrf'} if headers is None else headers)


def test_real_page_feature_and_no_cache(app,factory):
    assert app.test_client().get('/sales/api/leads').status_code==401
    for role in ('denied','pending'):assert client(app,role).get('/sales/api/bootstrap').status_code==403
    response=client(app).get('/sales/')
    assert response.status_code==200 and b'id="sales-app"' in response.data and b'app-sidebar' in response.data
    assert response.headers['Cache-Control']=='private, no-store'


def test_csrf_json_and_large_body(app,factory):
    c=client(app)
    assert post(c,'leads',{'title':'Owner'},headers={}).status_code==403
    assert c.post('/sales/api/leads',json=[],headers={'X-CSRF-Token':'qa-csrf'}).status_code==400
    assert post(c,'leads',{'title':'x'*1100000}).status_code==413


def test_browser_endpoints_and_permissions(app,factory):
    c=client(app,'sales');d=post(c,'leads',{'title':'Test owner'}).json
    assert d['id']
    for path in ['bootstrap','leads','pipeline','reports','tasks',f'leads/{d["id"]}',f'leads/{d["id"]}/duplicates','directory/contacts']:
        response=c.get('/sales/api/'+path);assert response.status_code==200,(path,response.json)
    assert c.get('/sales/api/export.csv').status_code==403
    assert c.get('/sales/api/failures').status_code==403
    assert post(c,'settings',{'version':1},method='PATCH').status_code==403
    assert post(c,'sources',{'name':'Test'}).status_code==403
    assert client(app).get('/sales/api/export.csv').status_code==200


def test_agent_scope_intake_and_actor(app,factory):
    from dashboard.auth.api_keys import create_api_key
    c=client(app);source=post(c,'sources',{'name':'Agent research','kind':'research'}).json
    key=create_api_key('Research worker',app.qa_users['admin'],['sales:intake'])
    agent=app.test_client();headers={'X-API-Key':key}
    payload={'source_id':source['id'],'external_id':'external-1','original':'Prospect research evidence','lead':{'title':'Agent prospect'}}
    response=post(agent,'intake',payload,headers=headers);assert response.status_code==200,response.json
    assert agent.get('/sales/api/leads',headers=headers).status_code==403
    assert post(agent,'leads',{'title':'Unauthorized'},headers=headers).status_code==403
    d=c.get('/sales/api/leads/'+str(response.json['deal_id'])).json
    assert d['activities'][0]['actor'].startswith('api_key:') and d['activities'][0]['actor_name']=='Research worker'
    read=create_api_key('Read only',app.qa_users['admin'],['sales:read'])
    assert agent.get('/sales/api/leads',headers={'X-API-Key':read}).status_code==200
    assert post(agent,'intake',payload,headers={'X-API-Key':read}).status_code==403
    denied=create_api_key('No feature',app.qa_users['denied'],['sales:read'])
    assert agent.get('/sales/api/leads',headers={'X-API-Key':denied}).status_code==403


def test_csv_preview_proof_and_atomic_commit(app,factory):
    c=client(app);source=post(c,'sources',{'name':'CSV Campaign','kind':'campaign'}).json
    data={'source_id':source['id'],'csv':'title,contact_name,email\nFirst owner,Alex,alex@example.invalid\n'}
    preview=c.post('/sales/api/import/preview',json=data,headers={'X-CSRF-Token':'qa-csrf'})
    assert preview.status_code==200 and preview.json['valid'],preview.json
    r=preview.json
    assert c.get('/sales/api/leads').json['total']==0
    commit={**data,'preview_fingerprint':r['fingerprint'],'preview_token':r['preview_token'],'duplicates_reviewed':True}
    assert post(c,'import/commit',{**commit,'csv':data['csv']+'bad'}).status_code==400
    result=post(c,'import/commit',commit);assert result.status_code==200,result.json
    assert post(c,'import/commit',commit).status_code==200
    assert c.get('/sales/api/leads').json['total']==1
