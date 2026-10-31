from datetime import date
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from owner_portal.models import (Account, AuditEvent, LoginSession, Membership, OwnerRequest,
                                 PropertyGrant, PublishedStatement, StatementDocument, User, utcnow)
from .test_access import accept
from .test_admin import staff_app, staff_client
from .test_reporting import seed


@pytest.fixture
def preview_app(staff_app, factory, monkeypatch):
    from dashboard.owner_access import preview
    monkeypatch.setattr(preview, 'get_session', factory)
    return staff_app


def test_account_preview_before_invitation_is_scoped_and_audited(preview_app, factory, setup):
    sid = seed(factory, setup)
    client = staff_client(preview_app)
    base = f'/owner-access/preview/{setup["account"]}/'
    data = client.get(base + 'api/dashboard?month=2026-11').json
    assert [a['id'] for a in data['accounts']] == [setup['account']]
    assert data['account']['permissions'] == {'performance': True, 'statements': True}
    month = next(m for m in data['months'] if m['month'] == '2026-11')
    assert month['value']['revenue'] == 900
    assert month['previous']['reason'] == 'outside_reporting_dates'
    assert 'NEVER PUBLIC' not in str(data) and '999999' not in str(data)
    assert data['statements'][0]['id'] == sid
    page = client.get(base)
    assert page.status_code == 200 and b'Admin preview' in page.data
    assert b'data-readonly="true"' in page.data and b'/preview/assets/dashboard.js' in page.data
    assert b'Sign out' not in page.data and b'invitation-endpoint' not in page.data
    assert page.headers['Cache-Control'] == 'private, no-store'
    assert page.headers['X-Frame-Options'] == 'DENY'
    assert client.get(base + f'statements/{sid}').status_code == 200
    assert client.get(base + f'statements/{sid}?download=1').headers['Content-Disposition'].startswith('attachment;')
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(User)) == 0
        assert db.scalar(select(func.count()).select_from(LoginSession)) == 0
        assert db.scalar(select(func.count()).select_from(Membership)) == 0
        events = db.scalars(select(AuditEvent).where(AuditEvent.action == 'dashboard.previewed')).all()
        assert len(events) == 2 and all(e.actor == 'staff:1' and e.target_id == setup['account'] for e in events)
    assert client.get(base + f'api/dashboard?account={setup["other_account"]}').status_code == 403
    assert client.get(base + f'api/dashboard?property={setup["other_property"]}').status_code == 403
    assert client.get(base + 'api/dashboard?month=2026-99').status_code == 400


def test_person_preview_matches_owner_and_rechecks_permissions(preview_app, factory, setup, app):
    token, user_id = accept(factory, setup['invite'])
    sid = seed(factory, setup)
    with factory() as db:
        member_id = db.scalar(select(Membership.id).where(Membership.user_id == user_id))
    owner = app.test_client()
    with owner.session_transaction() as cookie:
        cookie['owner_token'] = token
    client = staff_client(preview_app)
    base = f'/owner-access/preview/{setup["account"]}/person/{member_id}/'
    assert client.get(base + 'api/dashboard?month=2026-11').json == owner.get('/api/dashboard?month=2026-11').json
    assert client.get(base + f'statements/{sid}').status_code == 403
    with factory.begin() as db:
        member = db.get(Membership, member_id)
        member.performance, member.statements = False, True
    data = client.get(base + 'api/dashboard?month=2026-11').json
    assert data['months'] == [] and data['statements'][0]['id'] == sid
    assert client.get(base + f'statements/{sid}').status_code == 200
    assert client.get(f'/owner-access/preview/{setup["other_account"]}/person/{member_id}/').status_code == 404
    with factory.begin() as db:
        db.get(Membership, member_id).revoked_at = utcnow()
    assert client.get(base).status_code == 404
    assert client.get(base + 'api/dashboard').status_code == 404
    assert client.get(base + f'statements/{sid}').status_code == 404


def test_preview_statements_require_entire_scope_and_active_access(preview_app, factory, setup):
    sid = seed(factory, setup)
    with factory.begin() as db:
        db.add(StatementDocument(statement_id=sid, content=b'%PDF-synthetic', sha256='synthetic'))
    client = staff_client(preview_app)
    base = f'/owner-access/preview/{setup["account"]}/'
    assert client.get(base + f'statements/{sid}/pdf').data == b'%PDF-synthetic'
    other = f'/owner-access/preview/{setup["other_account"]}/statements/{sid}'
    for suffix in ('', '?download=1', '/pdf'):
        assert client.get(other + suffix).status_code == 403
    with factory.begin() as db:
        db.get(PublishedStatement, sid).property_ids = [setup['property'], setup['other_property']]
    assert client.get(base + f'statements/{sid}').status_code == 403
    with factory.begin() as db:
        db.get(PublishedStatement, sid).property_ids = [setup['property']]
        db.get(PropertyGrant, setup['grant']).revoked_at = utcnow()
    assert client.get(base + f'statements/{sid}/pdf').status_code == 403
    assert client.get(base + 'api/dashboard').json['properties'] == []
    with factory.begin() as db:
        db.get(Account, setup['account']).active = False
    assert client.get(base).status_code == 404


def test_preview_cannot_respond_or_create_owner_session(preview_app, factory, setup, app):
    with factory.begin() as db:
        row = OwnerRequest(account_id=setup['account'], property_id=setup['property'], title='Test decision',
                           summary='Synthetic owner-safe request', reporting_date=date(2026, 10, 1), published_by='staff:1')
        db.add(row); db.flush(); request_id = row.id
    client = staff_client(preview_app)
    base = f'/owner-access/preview/{setup["account"]}/'
    assert client.get(base + 'api/dashboard?month=2026-11').json['requests'][0]['id'] == request_id
    response = client.post(base + f'api/requests/{request_id}/respond', json={'response': 'approved'}, headers={'X-CSRF-Token': 'synthetic-csrf'})
    assert response.status_code in (404, 405)
    with client.session_transaction() as cookie:
        assert 'owner_token' not in cookie
    with factory() as db:
        assert db.get(OwnerRequest, request_id).response is None
    assert app.test_client().get(base).status_code == 404


@pytest.mark.parametrize('kind', ['signed-out', 'owner-cookie', 'team-member', 'unapproved', 'service', 'api-key'])
def test_every_preview_read_is_admin_only(preview_app, setup, monkeypatch, kind):
    from dashboard.owner_access import routes, preview
    client = staff_client(preview_app)
    if kind in ('signed-out', 'owner-cookie'):
        client = preview_app.test_client()
        if kind == 'owner-cookie':
            with client.session_transaction() as cookie:
                cookie['owner_token'] = 'an-owner-session-is-not-a-staff-session'
    elif kind in ('team-member', 'unapproved', 'service'):
        monkeypatch.setattr(routes, 'get_user_by_id', lambda _: SimpleNamespace(user_id=1, deleted_at=None,
            email=routes.API_SERVICE_EMAIL if kind == 'service' else 'test@example.invalid',
            is_approved=kind != 'unapproved', is_admin=lambda: kind != 'team-member'))
    def unexpected_db():
        raise AssertionError('Unauthorized preview reached data access')
    monkeypatch.setattr(preview, 'get_session', unexpected_db)
    headers = {'X-API-Key': 'synthetic-key'} if kind == 'api-key' else {}
    base = f'/owner-access/preview/{setup["account"]}/'
    for suffix in ('', 'api/dashboard', 'statements/1', 'statements/1/pdf', 'person/1/', 'person/1/api/dashboard'):
        assert client.get(base + suffix, headers=headers).status_code == 403
    assert client.get('/owner-access/preview/assets/dashboard.js', headers=headers).status_code == 403
