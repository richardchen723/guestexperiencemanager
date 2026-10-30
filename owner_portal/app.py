"""Isolated owner WSGI application. No internal routes or source-database models."""
import os
import secrets
from datetime import timedelta
from urllib.parse import urlsplit

from flask import Flask, jsonify, redirect, render_template, request, session, url_for
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker

from . import auth
from .access import AccessError, context, current_user, digest
from .models import Invitation, LoginSession, utcnow
from .mount import OwnerMount, normalize_prefix, public_base


def check_database_boundary(engine):
    """Fail closed if the web credential can read internal business tables."""
    with engine.connect() as conn:
        unsafe = conn.execute(text('''SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname=current_user
                AND (rolsuper OR rolcreaterole OR rolcreatedb OR rolbypassrls)) OR EXISTS (
            SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname NOT IN ('owners','pg_catalog','information_schema')
                AND n.nspname NOT LIKE 'pg_toast%' AND n.nspname NOT LIKE 'pg_temp%'
                AND c.relkind IN ('r','p','v','m','f')
                AND (has_table_privilege(c.oid,'SELECT,INSERT,UPDATE,DELETE')
                     OR has_any_column_privilege(c.oid,'SELECT,INSERT,UPDATE')))
        ''')).scalar()
        if unsafe:
            raise RuntimeError('Owner web database credentials must have access only to the owners schema.')


def create_app(config=None):
    app = Flask(__name__)
    app.config.from_mapping(
        SECRET_KEY=os.getenv('OWNER_PORTAL_SECRET_KEY'), DATABASE_URL=os.getenv('OWNER_PORTAL_DATABASE_URL'),
        OWNER_ORIGIN=os.getenv('OWNER_PORTAL_ORIGIN', ''),
        OWNER_PATH_PREFIX=os.getenv('OWNER_PORTAL_PATH_PREFIX', ''),
        LOGIN_ENABLED=os.getenv('OWNER_PORTAL_LOGIN_ENABLED', '').lower() == 'true',
        GOOGLE_CLIENT_ID=os.getenv('OWNER_GOOGLE_CLIENT_ID'), GOOGLE_CLIENT_SECRET=os.getenv('OWNER_GOOGLE_CLIENT_SECRET'),
        SESSION_COOKIE_NAME='__Host-owner_portal', SESSION_COOKIE_SECURE=True, SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE='Lax', SESSION_COOKIE_DOMAIN=None, SESSION_COOKIE_PATH='/',
        PERMANENT_SESSION_LIFETIME=timedelta(hours=12), MAX_CONTENT_LENGTH=16384,
    )
    if config:
        app.config.update(config)
    app.config['OWNER_PATH_PREFIX'] = normalize_prefix(app.config['OWNER_PATH_PREFIX'])
    app.config['APPLICATION_ROOT'] = app.config['OWNER_PATH_PREFIX'] or '/'
    origin = urlsplit(app.config['OWNER_ORIGIN'])
    if not app.config['TESTING']:
        public_base(app.config['OWNER_ORIGIN'], app.config['OWNER_PATH_PREFIX'])
        if not app.secret_key or len(app.secret_key) < 32 or app.secret_key == os.getenv('SECRET_KEY'):
            raise RuntimeError('Configure an independent owner secret (at least 32 characters).')
        if app.config['LOGIN_ENABLED'] and (not app.config['GOOGLE_CLIENT_ID'] or not app.config['GOOGLE_CLIENT_SECRET']):
            raise RuntimeError('Dedicated owner Google OAuth credentials are required to enable sign-in.')
    if not app.config['DATABASE_URL']:
        raise RuntimeError('OWNER_PORTAL_DATABASE_URL is required; staff credentials are never inherited.')
    engine = create_engine(app.config['DATABASE_URL'], pool_pre_ping=True)
    if engine.dialect.name != 'postgresql':
        raise RuntimeError('Owner access requires PostgreSQL.')
    if not app.config['TESTING']:
        check_database_boundary(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    app.extensions['owner_db'] = factory

    @app.before_request
    def protect():
        if not app.testing and request.host != origin.netloc:
            return jsonify(error='Invalid owner host.'), 400
        session.setdefault('csrf', secrets.token_urlsafe(32))
        if request.path.startswith('/auth/') and not app.config['LOGIN_ENABLED']:
            raise AccessError('Owner sign-in is being prepared. Please check back soon.', 503)
        if request.method not in ('GET', 'HEAD', 'OPTIONS'):
            provided = request.headers.get('X-CSRF-Token') or request.form.get('csrf', '')
            if not provided.isascii() or not secrets.compare_digest(session['csrf'], provided):
                return jsonify(error='Reload this page before continuing.'), 403

    @app.after_request
    def privacy(response):
        response.headers.update({'Cache-Control': 'private, no-store', 'Referrer-Policy': 'no-referrer',
            'X-Content-Type-Options': 'nosniff', 'X-Frame-Options': 'DENY',
            'Content-Security-Policy': "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self' https://accounts.google.com"})
        if not app.testing:
            response.headers['Strict-Transport-Security'] = 'max-age=31536000'
        return response

    @app.errorhandler(AccessError)
    def denied(error):
        if request.path.startswith('/api/') or request.is_json:
            return jsonify(error=str(error)), error.status
        return render_template('login.html', error=str(error)), error.status

    @app.get('/health')
    def health():
        with factory() as db:
            db.execute(select(Invitation.id).limit(1))
        return jsonify(status='ok')

    @app.get('/login')
    @app.get('/invite', endpoint='invite_page')
    def login():
        if request.path == '/login':
            session.pop('invitation_hash', None)
        return render_template('login.html')

    @app.post('/auth/invitation')
    def stage_invitation():
        data = request.get_json(silent=True)
        raw = data.get('token') if isinstance(data, dict) else None
        if not isinstance(raw, str) or not 32 <= len(raw) <= 128:
            raise AccessError('This invitation is unavailable.')
        with factory() as db:
            invite = db.scalar(select(Invitation).where(Invitation.token_hash == digest(raw), Invitation.revoked_at.is_(None),
                Invitation.accepted_at.is_(None), Invitation.expires_at > utcnow()))
            if not invite:
                raise AccessError('This invitation is unavailable.')
        session['invitation_hash'] = digest(raw)
        return jsonify(ready=True)

    @app.post('/auth/google')
    def google_start():
        with factory.begin() as db:
            return auth.start(db)

    @app.get('/auth/google/callback')
    def google_callback():
        with factory.begin() as db:
            flow = auth.consume_flow(db)
        claims = auth.google_identity(flow)
        with factory.begin() as db:
            raw = auth.sign_in(db, claims, flow['invitation_hash'])
            previous = session.get('owner_token')
            if previous:
                old = db.get(LoginSession, digest(previous))
                if old:
                    old.revoked_at = utcnow()
        session.clear()
        session.update(owner_token=raw, csrf=secrets.token_urlsafe(32))
        session.permanent = True
        return redirect(url_for('home'))

    @app.post('/logout')
    def logout():
        with factory.begin() as db:
            previous = db.get(LoginSession, digest(session.get('owner_token', '')))
            if previous:
                previous.revoked_at = utcnow()
        session.clear()
        return redirect(url_for('login'))

    @app.get('/api/context')
    def owner_context():
        with factory() as db:
            user = current_user(db, session.get('owner_token'))
            return jsonify(context(db, user.id))

    @app.get('/')
    def home():
        if not session.get('owner_token'):
            return redirect(url_for('login'))
        with factory() as db:
            user = current_user(db, session.get('owner_token'))
            return render_template('home.html', data=context(db, user.id))

    if app.config['OWNER_PATH_PREFIX']:
        app.wsgi_app = OwnerMount(app.wsgi_app, app.config['OWNER_PATH_PREFIX'])
    return app
