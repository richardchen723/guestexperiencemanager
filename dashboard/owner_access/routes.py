"""Human admin only. API keys and the internal service user cannot grant access."""
import os
import secrets

from flask import Blueprint, g, jsonify, render_template, request, session
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from dashboard.auth.api_keys import _get_api_key_from_request
from dashboard.auth.models import get_session, get_user_by_id
from dashboard.config import API_SERVICE_EMAIL
from owner_portal.access import AccessError
from owner_portal.admin import OwnerAdmin

bp = Blueprint('owner_access', __name__, url_prefix='/owner-access')


def enabled():
    return os.getenv('OWNER_ACCESS_ENABLED', '').lower() == 'true'


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
        if request.content_length is None or request.content_length > 16384:
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
        data['listings'] = [dict(row) for row in db.execute(text('''SELECT listing_id AS id,
            coalesce(internal_listing_name, name, listing_id::text) AS name FROM public.listings
            WHERE listing_id NOT IN (SELECT listing_id FROM owners.listing_mappings) ORDER BY listing_id''')).mappings()]
        return jsonify(data)


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
            elif action == 'properties':
                ids = set(db.execute(text('SELECT listing_id FROM public.listings')).scalars())
                result = service.create_property(payload, ids)
            elif action == 'grants':
                result = service.grant(payload)
            elif action == 'invitations':
                origin = os.getenv('OWNER_PORTAL_ORIGIN', '').rstrip('/')
                from urllib.parse import urlsplit
                parsed = urlsplit(origin)
                if parsed.scheme != 'https' or not parsed.netloc or parsed.path or parsed.query or parsed.fragment or parsed.username:
                    raise AccessError('Configure the dedicated HTTPS owner portal origin before creating invitations.', 409)
                result = service.invite(payload)
                result['url'] = origin + '/invite#token=' + result.pop('token')
            elif action == 'revoke':
                result = service.revoke(payload.get('kind'), payload.get('id'))
            elif action == 'preview':
                result = service.preview(payload.get('membership_id'))
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


def register_owner_access_routes(app):
    app.register_blueprint(bp)
    app.context_processor(lambda: {'owner_access_enabled': enabled()})
