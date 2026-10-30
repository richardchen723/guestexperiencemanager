from urllib.parse import parse_qs, urlsplit

import pytest

from owner_portal.app import create_app
from owner_portal.mount import normalize_prefix, public_base
from .test_admin import staff_app, staff_client


@pytest.fixture
def mounted(factory, runtime_url):
    return create_app({'TESTING': True, 'SECRET_KEY': 'independent-owner-path-test-secret',
        'DATABASE_URL': runtime_url, 'OWNER_ORIGIN': 'https://example.invalid', 'OWNER_PATH_PREFIX': '/owners',
        'LOGIN_ENABLED': True, 'GOOGLE_CLIENT_ID': 'synthetic-owner-client', 'GOOGLE_CLIENT_SECRET': 'synthetic-secret'})


def test_prefixed_links_callback_cookie_and_logout(mounted):
    client = mounted.test_client()
    base = 'https://example.invalid'
    assert client.get('/owners', base_url=base).location == '/owners/'
    home = client.get('/owners/', base_url=base)
    assert home.location == '/owners/login'
    assert 'Path=/' in home.headers['Set-Cookie'] and '__Host-owner_portal=' in home.headers['Set-Cookie']
    response = client.get('/owners/login', base_url=base)
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    for target in ('/owners/static/portal.js', '/owners/auth/google', '/owners/auth/invitation'):
        assert target in html
    with client.session_transaction(base_url=base) as cookie:
        token = cookie['csrf']
    response = client.post('/owners/auth/google', base_url=base, data={'csrf': token})
    assert response.status_code == 302
    params = parse_qs(urlsplit(response.location).query)
    assert params['redirect_uri'] == [base + '/owners/auth/google/callback']
    assert params['code_challenge_method'] == ['S256']
    response = client.post('/owners/logout', base_url=base, data={'csrf': token})
    assert response.location == '/owners/login'
    for path in ('/login', '/auth/google/callback', '/owner-access/', '/owners-other/login'):
        assert client.get(path, base_url=base).status_code == 404


def test_disabled_login_serves_honest_page_and_blocks_auth(mounted):
    mounted.config.update(LOGIN_ENABLED=False, GOOGLE_CLIENT_ID=None, GOOGLE_CLIENT_SECRET=None)
    client = mounted.test_client()
    page = client.get('/owners/login', base_url='https://example.invalid')
    assert page.status_code == 200 and b'coming soon' in page.data
    assert b'Continue with Google' not in page.data
    for path in ('/owners/auth/google', '/owners/auth/invitation'):
        assert client.post(path, base_url='https://example.invalid').status_code == 503
    assert client.get('/owners/auth/google/callback', base_url='https://example.invalid').status_code == 503
    assert client.get('/owners/api/context', base_url='https://example.invalid').status_code == 401


def test_staff_invitations_follow_mount_and_require_enabled_login(staff_app, setup, monkeypatch):
    client = staff_client(staff_app)
    monkeypatch.setenv('OWNER_PORTAL_PATH_PREFIX', '/owners')
    payload = {'account_id': setup['account'], 'email': 'second@example.invalid', 'performance': True, 'statements': True}
    headers = {'X-CSRF-Token': 'synthetic-csrf'}
    response = client.post('/owner-access/api/invitations', json=payload, headers=headers)
    assert response.status_code == 200
    assert response.json['url'].startswith('https://owners.example.invalid/owners/invite#token=')
    monkeypatch.setenv('OWNER_PORTAL_LOGIN_ENABLED', 'false')
    assert client.post('/owner-access/api/invitations', json=payload, headers=headers).status_code == 409


@pytest.mark.parametrize('prefix', ['//other.example', '/owners/../admin', '/owners?x=1', '/owners#secret'])
def test_unsafe_prefix_rejected(prefix):
    with pytest.raises(ValueError):
        normalize_prefix(prefix)
    with pytest.raises(ValueError):
        public_base('https://example.invalid', prefix)
