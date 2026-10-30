import json
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

import pytest
from sqlalchemy import select

from owner_portal import auth
from owner_portal.access import AccessError, digest
from owner_portal.models import Invitation, LoginFlow, LoginSession, User, utcnow


ORIGIN = 'https://owners.example.invalid'


def csrf(client):
    client.get('/login', base_url=ORIGIN)
    with client.session_transaction(base_url=ORIGIN) as cookie:
        return cookie['csrf']


def flow(client, setup=None):
    token = csrf(client)
    if setup:
        response = client.post('/auth/invitation', base_url=ORIGIN, json={'token': setup['invite']['token']}, headers={'X-CSRF-Token': token})
        assert response.status_code == 200
    response = client.post('/auth/google', base_url=ORIGIN, data={'csrf': token})
    assert response.status_code == 302
    return {k: v[0] for k, v in parse_qs(urlsplit(response.location).query).items()}


def test_complete_google_flow_uses_actual_signature_verifier(app, factory, setup, monkeypatch):
    # Signed synthetic Google-format JWT; cert transport and code exchange only are mocked.
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives import serialization
    from google.auth import crypt, jwt
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    public = key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    client = app.test_client()
    params = flow(client, setup)
    now = int(utcnow().timestamp())
    claims = {'sub': 'google-synthetic-owner', 'email': 'owner@example.invalid', 'email_verified': True,
        'iss': 'https://accounts.google.com', 'aud': 'owner-test-client', 'iat': now, 'exp': now+300, 'nonce': params['nonce']}
    encoded = jwt.encode(crypt.RSASigner.from_string(private, key_id='test-key'), claims).decode()
    class Tokens:
        def raise_for_status(self): pass
        def json(self): return {'id_token': encoded}
    class Certs:
        status = 200
        data = json.dumps({'test-key': public.decode()}).encode()
    monkeypatch.setattr(auth.requests, 'post', lambda *args, **kwargs: Tokens())
    monkeypatch.setattr(auth, 'GoogleRequest', lambda: lambda *args, **kwargs: Certs())
    response = client.get('/auth/google/callback', base_url=ORIGIN, query_string={'state': params['state'], 'code': 'synthetic-code'})
    assert response.status_code == 302 and response.location == '/'
    assert '__Host-owner_portal=' in response.headers['Set-Cookie']
    assert all(value in response.headers['Set-Cookie'] for value in ('Secure', 'HttpOnly', 'SameSite=Lax'))
    result = client.get('/api/context', base_url=ORIGIN).get_json()
    assert len(result['accounts']) == 1 and result['accounts'][0]['properties'][0]['name'] == 'Cedar House'
    assert client.get('/', base_url=ORIGIN).status_code == 200
    assert client.get('/auth/google/callback', base_url=ORIGIN, query_string={'state': params['state'], 'code': 'synthetic-code'}).status_code == 401
    with factory() as db:
        assert db.scalar(select(Invitation.accepted_at)) is not None
        assert len(db.scalars(select(LoginSession)).all()) == 1
    with client.session_transaction(base_url=ORIGIN) as cookie:
        token = cookie['csrf']
    assert client.post('/logout', base_url=ORIGIN, data={'csrf': token}).status_code == 302
    assert client.get('/api/context', base_url=ORIGIN).status_code == 401


@pytest.mark.parametrize('change', [{'email_verified': False}, {'aud': 'staff-client'}, {'iss': 'https://evil.invalid'}, {'azp': 'other'}, {'nonce': 'wrong'}, {'sub': ''}])
def test_reject_untrusted_identity_claims(change):
    claims = {'email_verified': True, 'email': 'owner@example.invalid', 'sub': '123', 'nonce': 'expected', 'aud': 'owner-client', 'iss': 'https://accounts.google.com'}
    with pytest.raises(AccessError): auth.validate_claims({**claims, **change}, digest('expected'), 'owner-client')


def test_cross_browser_state_and_expired_state_denied(app, factory):
    client = app.test_client()
    params = flow(client)
    other = app.test_client()
    csrf(other)
    assert other.get('/auth/google/callback', base_url=ORIGIN, query_string={'state': params['state'], 'code': 'fake'}).status_code == 401
    with factory.begin() as db:
        db.get(LoginFlow, digest(params['state'])).expires_at = utcnow() - timedelta(seconds=1)
    assert client.get('/auth/google/callback', base_url=ORIGIN, query_string={'state': params['state'], 'code': 'fake'}).status_code == 401


def test_cookie_and_api_key_isolation_and_csrf(app):
    client = app.test_client()
    assert client.get('/api/context', base_url=ORIGIN, headers={'X-API-Key': 'staff-key'}).status_code == 401
    with client.session_transaction(base_url=ORIGIN) as cookie:
        cookie['logged_in'] = True
        cookie['user_id'] = 1
    assert client.get('/api/context', base_url=ORIGIN).status_code == 401
    assert client.post('/auth/google', base_url=ORIGIN).status_code == 403
    assert client.post('/auth/invitation', base_url=ORIGIN, json={'token': 'x'*40}).status_code == 403
    for route in ['/api/reservations', '/messages', '/sales/', '/owner-access/', '/api/statements', '/api/snapshot']:
        assert client.get(route, base_url=ORIGIN).status_code == 404
    response = client.get('/login', base_url=ORIGIN)
    assert response.headers['Referrer-Policy'] == 'no-referrer'
    assert response.headers['Cache-Control'] == 'private, no-store'
    assert "frame-ancestors 'none'" in response.headers['Content-Security-Policy']
