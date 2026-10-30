from types import SimpleNamespace

import pytest
from flask import Flask
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from owner_portal.access import AccessError
from owner_portal.admin import OwnerAdmin
from owner_portal.models import Account, AuditEvent, Invitation, ListingMapping


@pytest.fixture
def staff_app(factory, monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'synthetic-test-key')
    monkeypatch.setenv('DATABASE_URL', str(factory.kw['bind'].url))
    monkeypatch.setenv('SECRET_KEY', 'synthetic-independent-staff-secret')
    from dashboard.owner_access import routes
    monkeypatch.setenv('OWNER_ACCESS_ENABLED', 'true')
    monkeypatch.setenv('OWNER_PORTAL_ORIGIN', 'https://owners.example.invalid')
    monkeypatch.setenv('OWNER_PORTAL_LOGIN_ENABLED', 'true')
    monkeypatch.setattr(routes, 'get_session', factory)
    monkeypatch.setattr(routes, 'get_user_by_id', lambda value: SimpleNamespace(user_id=value, deleted_at=None,
        email='admin@example.invalid', is_approved=True, is_admin=lambda: True))
    app = Flask(__name__)
    app.config.update(TESTING=True, SECRET_KEY='independent-staff-secret')
    routes.register_owner_access_routes(app)
    return app


def staff_client(app):
    client = app.test_client()
    with client.session_transaction() as cookie:
        cookie.update(user_id=1, logged_in=True, owner_access_csrf='synthetic-csrf')
    return client


def test_admin_setup_requires_browser_admin_and_csrf(staff_app, factory, monkeypatch):
    from dashboard.owner_access import routes
    client = staff_client(staff_app)
    path = '/owner-access/api/accounts'
    headers = {'X-CSRF-Token': 'synthetic-csrf'}
    assert staff_app.test_client().post(path, json={'name': 'Account'}, headers=headers).status_code == 403
    assert client.post(path, json={'name': 'Account'}, headers={**headers, 'X-API-Key': 'full-internal-key'}).status_code == 403
    assert client.post(path, json={'name': 'Account'}).status_code == 403
    response = client.post(path, json={'name': 'Account'}, headers=headers)
    assert response.status_code == 200
    monkeypatch.setattr(routes, 'get_user_by_id', lambda value: SimpleNamespace(user_id=value, deleted_at=None,
        email='staff@example.invalid', is_approved=True, is_admin=lambda: False))
    assert client.post(path, json={'name': 'Not allowed'}, headers=headers).status_code == 403
    with factory() as db:
        assert len(db.scalars(select(Account)).all()) == 1


def test_setup_disabled_by_default_and_service_user_denied(staff_app, monkeypatch):
    from dashboard.owner_access import routes
    client = staff_client(staff_app)
    headers = {'X-CSRF-Token': 'synthetic-csrf'}
    monkeypatch.setenv('OWNER_ACCESS_ENABLED', 'false')
    assert client.post('/owner-access/api/accounts', json={'name': 'No'}, headers=headers).status_code == 404
    monkeypatch.setenv('OWNER_ACCESS_ENABLED', 'true')
    monkeypatch.setattr(routes, 'get_user_by_id', lambda value: SimpleNamespace(user_id=value, deleted_at=None,
        email=routes.API_SERVICE_EMAIL, is_approved=True, is_admin=lambda: True))
    assert client.post('/owner-access/api/accounts', json={'name': 'No'}, headers=headers).status_code == 403


def test_invitation_only_returns_raw_token_once(staff_app, factory, setup):
    client = staff_client(staff_app)
    response = client.post('/owner-access/api/invitations', json={'account_id': setup['account'],
        'email': 'new@example.invalid', 'performance': True, 'statements': True}, headers={'X-CSRF-Token': 'synthetic-csrf'})
    assert response.status_code == 200
    raw = response.json['url'].split('#token=')[1]
    assert response.headers['Cache-Control'] == 'private, no-store'
    with factory() as db:
        overview = OwnerAdmin(db, 'staff:1').overview()
        assert raw not in str(overview) and 'token_hash' not in str(overview)
        assert raw not in str(db.scalars(select(AuditEvent.details)).all())
        assert db.get(Invitation, response.json['id']).token_hash != raw


def test_mapping_needs_explicit_verified_physical_unit(factory, setup):
    data = {'name': 'Home', 'timezone': 'America/Los_Angeles', 'listing_ids': [303], 'single_unit_confirmed': True}
    for change in ({'single_unit_confirmed': False}, {'listing_ids': [404]}, {'timezone': 'not/a/timezone'}, {'listing_ids': [303, 303]}):
        with factory.begin() as db, pytest.raises(AccessError):
            OwnerAdmin(db, 'staff:1').create_property({**data, **change}, {303})
    with pytest.raises(IntegrityError), factory.begin() as db:
        OwnerAdmin(db, 'staff:1').create_property({**data, 'listing_ids': [101]}, {101})
    with factory() as db:
        assert len(db.scalars(select(ListingMapping)).all()) == 2


def test_register_and_grant_is_atomic(staff_app, factory, setup, engine):
    from sqlalchemy import text
    from owner_portal.models import Property, PropertyGrant
    client = staff_client(staff_app)
    headers = {'X-CSRF-Token': 'synthetic-csrf'}
    data = {'account_id': setup['account'], 'name': 'Willow House', 'timezone': 'America/Chicago',
        'listing_ids': [303], 'single_unit_confirmed': True, 'reporting_from': '2026-01-01'}
    with engine.begin() as conn:
        conn.execute(text('CREATE TABLE public.listings (listing_id int)'))
        conn.execute(text('INSERT INTO public.listings VALUES (303)'))
    try:
        # The mapping is flushed before date validation; rollback must undo all of it.
        response = client.post('/owner-access/api/add-property', json={**data, 'reporting_until_exclusive': '2025-01-01'}, headers=headers)
        assert response.status_code == 400
        with factory() as db:
            assert not db.scalar(select(Property).where(Property.name == 'Willow House'))
            assert not db.scalar(select(ListingMapping).where(ListingMapping.listing_id == 303))
            assert len(db.scalars(select(AuditEvent).where(AuditEvent.action == 'property.mapped')).all()) == 2
        assert client.post('/owner-access/api/add-property', json=data).status_code == 403
        response = client.post('/owner-access/api/add-property', json=data, headers=headers)
        assert response.status_code == 200
        with factory() as db:
            grant = db.get(PropertyGrant, response.json['id'])
            assert grant.account_id == setup['account'] and grant.property_id == response.json['property_id']
            assert db.get(Property, grant.property_id).currency == 'USD'
        duplicate = client.post('/owner-access/api/add-property', json={**data, 'account_id': setup['other_account']}, headers=headers)
        assert duplicate.status_code == 409
        with factory() as db:
            assert len(db.scalars(select(Property).where(Property.name == 'Willow House')).all()) == 1
    finally:
        with engine.begin() as conn:
            conn.execute(text('DROP TABLE public.listings'))
