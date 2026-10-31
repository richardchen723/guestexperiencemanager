"""Human admin only. API keys and the internal service user cannot grant access."""
import os
import secrets
from zoneinfo import available_timezones

from flask import Blueprint, g, jsonify, render_template, request, session
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from dashboard.auth.api_keys import _get_api_key_from_request
from dashboard.auth.models import get_session, get_user_by_id
from dashboard.config import API_SERVICE_EMAIL
from owner_portal.access import AccessError
from owner_portal.admin import OwnerAdmin
from owner_portal.mount import public_base
from owner_portal import publishing, requests as owner_requests
from owner_portal.models import PublishedStatement, OwnerRequest
from sqlalchemy import select

bp = Blueprint('owner_access', __name__, url_prefix='/owner-access')


def enabled():
    return os.getenv('OWNER_ACCESS_ENABLED', '').lower() == 'true'


def portal_base():
    return public_base(os.getenv('OWNER_PORTAL_ORIGIN', ''), os.getenv('OWNER_PORTAL_PATH_PREFIX', ''))


def invitations_ready():
    try:
        portal_base()
    except ValueError:
        return False
    return os.getenv('OWNER_PORTAL_LOGIN_ENABLED', '').lower() == 'true'


@bp.before_request
def protect():
    if not enabled():
        return jsonify(error='Owner access setup is not enabled.'), 404
    if _get_api_key_from_request(request) or request.headers.get('Authorization') or getattr(g, 'api_user', None):
        return jsonify(error='Use an approved administrator browser session.'), 403
    user = get_user_by_id(session.get('user_id')) if session.get('logged_in') and session.get('user_id') else None
    if not user or user.deleted_at or not user.is_approved or not user.is_admin() or user.email == API_SERVICE_EMAIL:
        return jsonify(error='An approved administrator is required.'), 403
    g.owner_access_actor = f'staff:{user.user_id}'
    if request.method not in ('GET', 'HEAD', 'OPTIONS'):
        csrf = session.get('owner_access_csrf', '')
        provided = request.headers.get('X-CSRF-Token', '')
        if not csrf or not provided.isascii() or not secrets.compare_digest(csrf, provided):
            return jsonify(error='Reload this page before saving.'), 403
        maximum = 5*1024*1024+16384 if request.path=='/owner-access/api/upload-statement' else 16384
        if request.content_length is None or request.content_length > maximum:
            return jsonify(error='A bounded JSON request is required.'), 413


@bp.after_request
def private(response):
    response.headers['Cache-Control'] = 'private, no-store'
    response.headers['Referrer-Policy'] = 'no-referrer'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    return response


@bp.get('/')
def page():
    session.setdefault('owner_access_csrf', secrets.token_urlsafe(32))
    return render_template('owner_access/index.html', owner_access_csrf=session['owner_access_csrf'])


@bp.get('/api/setup')
def setup():
    with get_session() as db:
        data = OwnerAdmin(db, g.owner_access_actor).overview()
        data['invitation_ready'] = invitations_ready()
        data['timezones'] = sorted(available_timezones())
        data['listings'] = [dict(row) for row in db.execute(text('''SELECT listing_id AS id,
            coalesce(internal_listing_name, name, listing_id::text) AS name, timezone_name AS timezone FROM public.listings
            WHERE listing_id NOT IN (SELECT listing_id FROM owners.listing_mappings) ORDER BY listing_id''')).mappings()]
        return jsonify(data)


@bp.get('/statements')
def statements_page():
    session.setdefault('owner_access_csrf', secrets.token_urlsafe(32))
    return render_template('owner_access/statements.html', owner_access_csrf=session['owner_access_csrf'])


@bp.get('/api/statements')
def statements_data():
    try:
        account_id = int(request.args.get('account', '0'))
        with get_session() as db:
            account = OwnerAdmin(db, g.owner_access_actor).account(account_id)
            published = [{'id': r.id, 'period': r.period_start.isoformat()[:7], 'version': r.version,
                'properties': r.property_names, 'net_income': float(r.net_income), 'withdrawn': r.withdrawn_at is not None}
                for r in db.scalars(select(PublishedStatement).where(PublishedStatement.account_id == account.id)
                    .order_by(PublishedStatement.period_start.desc(), PublishedStatement.version.desc()))]
            return jsonify(candidates=publishing.candidates(db, account.id), published=published)
    except (ValueError, AccessError) as error:
        return jsonify(error=str(error)), getattr(error, 'status', 400)


@bp.get('/requests')
def requests_page():
    session.setdefault('owner_access_csrf', secrets.token_urlsafe(32))
    return render_template('owner_access/requests.html',owner_access_csrf=session['owner_access_csrf'])


@bp.get('/api/requests')
def requests_data():
    try:
        with get_session() as db:
            account = OwnerAdmin(db,g.owner_access_actor).account(int(request.args.get('account','0')))
            return jsonify(requests=[{'id':r.id,'title':r.title,'property_id':r.property_id,
                'response':r.response,'withdrawn':r.withdrawn_at is not None,
                'published_at':r.published_at.isoformat(),'amount':float(r.amount) if r.amount is not None else None}
                for r in db.scalars(select(OwnerRequest).where(OwnerRequest.account_id==account.id).order_by(OwnerRequest.published_at.desc()))])
    except (ValueError,AccessError) as error:
        return jsonify(error=str(error)),getattr(error,'status',400)


@bp.post('/api/<action>')
def command(action):
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify(error='A JSON object is required.'), 400
    with get_session() as db:
        service = OwnerAdmin(db, g.owner_access_actor)
        try:
            if action == 'accounts':
                result = service.create_account(payload)
            elif action in ('properties', 'add-property'):
                ids = set(db.execute(text('SELECT listing_id FROM public.listings')).scalars())
                result = (service.add_property if action == 'add-property' else service.create_property)(payload, ids)
            elif action == 'grants':
                result = service.grant(payload)
            elif action == 'invitations':
                if not invitations_ready():
                    raise AccessError('Owner sign-in must be configured and enabled before creating invitations.', 409)
                result = service.invite(payload)
                result['url'] = portal_base() + '/invite#token=' + result.pop('token')
            elif action == 'revoke':
                result = service.revoke(payload.get('kind'), payload.get('id'))
            elif action == 'preview':
                result = service.preview(payload.get('membership_id'))
            elif action == 'publish-statement':
                result = publishing.publish(db, g.owner_access_actor, payload)
            elif action == 'withdraw-statement':
                result = publishing.withdraw(db, g.owner_access_actor, payload)
            elif action == 'publish-request':
                result = owner_requests.publish(db,g.owner_access_actor,payload)
            elif action == 'withdraw-request':
                result = owner_requests.withdraw(db,g.owner_access_actor,payload)
            else:
                raise AccessError('Unknown setup action.', 404)
            db.commit()
            return jsonify(result)
        except AccessError as error:
            db.rollback()
            return jsonify(error=str(error)), error.status
        except IntegrityError:
            db.rollback()
            return jsonify(error='This listing is already mapped or a related record changed. Reload and review.'), 409


@bp.post('/api/upload-statement')
def upload_statement():
    import json
    try:
        payload=json.loads(request.form.get('metadata','null'))
        file=request.files.get('file')
        if not isinstance(payload,dict) or not file:
            raise AccessError('Select a finalized PDF and complete its details.',400)
        content=file.read(5*1024*1024+1)
        with get_session() as db:
            result=publishing.upload(db,g.owner_access_actor,payload,content)
            db.commit()
            return jsonify(result)
    except (ValueError,AccessError) as error:
        return jsonify(error=str(error)),getattr(error,'status',400)


def register_owner_access_routes(app):
    from jinja2 import ChoiceLoader, FileSystemLoader, PrefixLoader
    from .preview import OWNER_ROOT
    app.jinja_loader = ChoiceLoader([app.jinja_loader, PrefixLoader({'owner_preview': FileSystemLoader(OWNER_ROOT / 'templates')})])
    app.register_blueprint(bp)
    app.context_processor(lambda: {'owner_access_enabled': enabled()})


from .preview import bp as preview_bp
bp.register_blueprint(preview_bp)
