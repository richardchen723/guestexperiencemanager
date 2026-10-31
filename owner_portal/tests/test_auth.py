import json
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

import pytest
from sqlalchemy import select

from owner_portal import auth
from owner_portal.access import AccessError, digest
from owner_portal.models import Invitation, LoginFlow, LoginSession, User, utcnow


ORIGIN = 'https://owners.example.invalid'


def csrf(client, prefix=''):
    client.get(prefix + '/login', base_url=ORIGIN)
    with client.session_transaction(base_url=ORIGIN) as cookie:
        return cookie['csrf']


def flow(client, setup=None, prefix=''):
    token = csrf(client, prefix)
    if setup:
        response = client.post(prefix + '/auth/invitation', base_url=ORIGIN, json={'token': setup['invite']['token']}, headers={'X-CSRF-Token': token})
        assert response.status_code == 200
    response = client.post(prefix + '/auth/google', base_url=ORIGIN, data={'csrf': token})
    assert response.status_code == 302
    return {k: v[0] for k, v in parse_qs(urlsplit(response.location).query).items()}


@pytest.mark.parametrize('prefix', ['', '/owners'])
def test_complete_google_flow_uses_actual_signature_verifier(app, factory, setup, monkeypatch, prefix):
    # Signed synthetic Google-format JWT; cert transport and code exchange only are mocked.
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives import serialization
    from google.auth import crypt, jwt
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    public = key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    if prefix:
        from owner_portal.mount import OwnerMount
        app.config.update(OWNER_PATH_PREFIX=prefix, APPLICATION_ROOT=prefix)
        app.wsgi_app = OwnerMount(app.wsgi_app, prefix)
    client = app.test_client()
    params = flow(client, setup, prefix)
    assert params['redirect_uri'] == ORIGIN + prefix + '/auth/google/callback'
    assert params['scope'] == 'openid email'
    assert params['code_challenge_method'] == 'S256'
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
    def exchange(url, *, data, timeout):
        import base64
        import hashlib
        assert url == 'https://oauth2.googleapis.com/token' and timeout == 15
        assert data['redirect_uri'] == params['redirect_uri']
        assert data['client_id'] == 'owner-test-client'
        assert data['client_secret'] == 'owner-test-secret'
        assert data['grant_type'] == 'authorization_code'
        challenge = base64.urlsafe_b64encode(hashlib.sha256(data['code_verifier'].encode()).digest()).decode().rstrip('=')
        assert challenge == params['code_challenge']
        return Tokens()
    monkeypatch.setattr(auth.requests, 'post', exchange)
    monkeypatch.setattr(auth, 'GoogleRequest', lambda: lambda *args, **kwargs: Certs())
    response = client.get(prefix + '/auth/google/callback', base_url=ORIGIN, query_string={'state': params['state'], 'code': 'synthetic-code'})
    assert response.status_code == 302 and response.location == prefix + '/'
    assert '__Host-owner_portal=' in response.headers['Set-Cookie']
    assert all(value in response.headers['Set-Cookie'] for value in ('Secure', 'HttpOnly', 'SameSite=Lax'))
    result = client.get(prefix + '/api/context', base_url=ORIGIN).get_json()
    assert len(result['accounts']) == 1 and result['accounts'][0]['properties'][0]['name'] == 'Cedar House'
    assert client.get(prefix + '/', base_url=ORIGIN).status_code == 200
    assert client.get(prefix + '/auth/google/callback', base_url=ORIGIN, query_string={'state': params['state'], 'code': 'synthetic-code'}).status_code == 401
    with factory() as db:
        assert db.scalar(select(Invitation.accepted_at)) is not None
        assert len(db.scalars(select(LoginSession)).all()) == 1
    with client.session_transaction(base_url=ORIGIN) as cookie:
        token = cookie['csrf']
    assert client.post(prefix + '/logout', base_url=ORIGIN, data={'csrf': token}).status_code == 302
    assert client.get(prefix + '/api/context', base_url=ORIGIN).status_code == 401


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
