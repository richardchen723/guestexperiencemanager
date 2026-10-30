"""Invitation-only Google OIDC. No staff cookies, OAuth credentials, or roles."""
import base64
import hashlib
import secrets
from datetime import timedelta
from urllib.parse import urlencode

import requests
from flask import current_app, redirect, request, session
from google.auth.transport.requests import Request as GoogleRequest
from google.auth.exceptions import GoogleAuthError
from google.oauth2 import id_token
from sqlalchemy import select, text

from .access import AccessError, digest
from .admin import email_address, record
from .models import Account, Invitation, LoginFlow, LoginSession, Membership, User, utcnow


def start(db):
    state, nonce, binding, verifier = (secrets.token_urlsafe(32) for _ in range(4))
    db.add(LoginFlow(state_hash=digest(state), nonce_hash=digest(nonce), binding_hash=digest(binding),
        invitation_hash=session.get('invitation_hash'), expires_at=utcnow() + timedelta(minutes=10)))
    session['oauth'] = {'binding': binding, 'verifier': verifier}
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
    return redirect('https://accounts.google.com/o/oauth2/v2/auth?' + urlencode({
        'client_id': current_app.config['GOOGLE_CLIENT_ID'],
        'redirect_uri': current_app.config['OWNER_ORIGIN'] + '/auth/google/callback',
        'response_type': 'code', 'scope': 'openid email', 'state': state, 'nonce': nonce,
        'code_challenge': challenge, 'code_challenge_method': 'S256', 'prompt': 'select_account',
    }))


def consume_flow(db):
    oauth = session.pop('oauth', {})
    state = request.args.get('state', '')
    if not state or len(state) > 256 or not oauth.get('binding'):
        raise AccessError('Sign-in expired. Please start again.', 401)
    flow = db.scalar(select(LoginFlow).where(LoginFlow.state_hash == digest(state)).with_for_update())
    if not flow or flow.consumed_at or flow.expires_at <= utcnow() or not secrets.compare_digest(flow.binding_hash, digest(oauth['binding'])):
        raise AccessError('Sign-in expired. Please start again.', 401)
    flow.consumed_at = utcnow()
    return {'nonce_hash': flow.nonce_hash, 'invitation_hash': flow.invitation_hash, 'verifier': oauth['verifier']}


def google_identity(flow):
    code = request.args.get('code', '')
    if not code or len(code) > 4096 or request.args.get('error'):
        raise AccessError('Google sign-in was not completed.', 401)
    try:
        response = requests.post('https://oauth2.googleapis.com/token', data={
            'code': code, 'client_id': current_app.config['GOOGLE_CLIENT_ID'],
            'client_secret': current_app.config['GOOGLE_CLIENT_SECRET'],
            'redirect_uri': current_app.config['OWNER_ORIGIN'] + '/auth/google/callback',
            'grant_type': 'authorization_code', 'code_verifier': flow['verifier'],
        }, timeout=15)
        response.raise_for_status()
        transport = GoogleRequest()
        def fetch_keys(url, **kwargs):
            return transport(url, timeout=15, **kwargs)
        claims = id_token.verify_oauth2_token(response.json()['id_token'], fetch_keys,
            current_app.config['GOOGLE_CLIENT_ID'])
    except (requests.RequestException, GoogleAuthError, ValueError, KeyError):
        raise AccessError('Google sign-in could not be verified. Please try again.', 401) from None
    validate_claims(claims, flow['nonce_hash'], current_app.config['GOOGLE_CLIENT_ID'])
    return claims


def validate_claims(claims, nonce_hash, audience):
    # Signature, expiry, issuer and audience are checked by google-auth above.
    if (claims.get('iss') not in ('accounts.google.com', 'https://accounts.google.com')
            or claims.get('aud') != audience or claims.get('azp', audience) != audience
            or claims.get('email_verified') is not True
            or not isinstance(claims.get('sub'), str) or not 1 <= len(claims['sub']) <= 255
            or not isinstance(claims.get('nonce'), str)
            or not secrets.compare_digest(digest(claims['nonce']), nonce_hash)):
        raise AccessError('Google sign-in could not be verified.', 401)
    email_address(claims.get('email'))


def sign_in(db, claims, invitation_hash):
    subject = claims['sub']
    email = email_address(claims['email'])
    # Serialize first sign-ins for the same Google subject without identifying by email.
    db.execute(text('SELECT pg_advisory_xact_lock(hashtextextended(:subject, 847))'), {'subject': subject})
    user = db.scalar(select(User).where(User.google_subject == subject))
    if user and not user.active:
        raise AccessError()
    if invitation_hash:
        candidate = db.scalar(select(Invitation).where(Invitation.token_hash == invitation_hash))
        if not candidate:
            raise AccessError('This invitation is unavailable.', 403)
        db.execute(text('SELECT pg_advisory_xact_lock(847, :id)'), {'id': candidate.account_id})
        account = db.get(Account, candidate.account_id)
        db.refresh(candidate, with_for_update=True)
        if (not account.active or candidate.revoked_at or candidate.accepted_at
                or candidate.expires_at <= utcnow() or candidate.email != email):
            raise AccessError('This invitation is unavailable for this Google account.', 403)
        if user is None:
            user = User(google_subject=subject, email=email)
            db.add(user)
            db.flush()
        member = db.scalar(select(Membership).where(Membership.account_id == account.id, Membership.user_id == user.id))
        if member is None:
            member = Membership(account_id=account.id, user_id=user.id)
            db.add(member)
        member.performance, member.statements = candidate.performance, candidate.statements
        member.revoked_at = None
        candidate.accepted_at, candidate.accepted_user_id = utcnow(), user.id
        record(db, f'owner:{user.id}', 'invitation.accepted', candidate)
    if user is None or not db.scalar(select(Membership.id).join(Account).where(
            Membership.user_id == user.id, Membership.revoked_at.is_(None), Account.active.is_(True)).limit(1)):
        raise AccessError('An active owner invitation is required.', 403)
    user.email = email
    raw = secrets.token_urlsafe(32)
    db.add(LoginSession(token_hash=digest(raw), user_id=user.id, expires_at=utcnow() + timedelta(hours=12)))
    record(db, f'owner:{user.id}', 'session.created', user)
    return raw
