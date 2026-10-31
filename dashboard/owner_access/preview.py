"""Read-only owner views inside the staff app; never creates an owner session."""
from datetime import date
from pathlib import Path

from flask import Blueprint, g, jsonify, make_response, render_template, request, send_from_directory, url_for
from sqlalchemy import select

from dashboard.auth.models import get_session
from owner_portal import reporting
from owner_portal.access import AccessError, account_projection, authorize_property
from owner_portal.admin import record
from owner_portal.models import Account, Membership, Property, PropertyGrant, StatementDocument, User

bp = Blueprint('preview', __name__, url_prefix='/preview')
OWNER_ROOT = Path(__file__).resolve().parents[2] / 'owner_portal'
ASSETS = {'portal.css', 'preview.js', 'dashboard.css', 'dashboard.js', 'statement.css', 'statement.js'}


class PreviewAccess:
    """Server-constructed scope. Only registered behind the human-admin guard."""
    def __init__(self, db, account_id, membership_id=None):
        self.db = db
        self.account = db.get(Account, account_id)
        if not self.account or not self.account.active:
            raise AccessError('Owner account unavailable.', 404)
        self.member, self.user = None, None
        if membership_id is not None:
            self.member = db.get(Membership, membership_id)
            if not self.member or self.member.account_id != account_id or self.member.revoked_at:
                raise AccessError('Owner access unavailable.', 404)
            self.user = db.get(User, self.member.user_id)
            if not self.user or not self.user.active:
                raise AccessError('Owner access unavailable.', 404)

    def context(self):
        permissions = {'performance': self.member.performance, 'statements': self.member.statements} if self.member else {'performance': True, 'statements': True}
        account = account_projection(self.db, self.account, **permissions)
        return {'accounts': [account], 'currency': 'USD',
                'performance_available': permissions['performance'], 'statements_available': permissions['statements']}

    def authorize(self, account_id, property_id, start, end, *, permission='performance'):
        if account_id != self.account.id:
            raise AccessError()
        if self.member:
            return authorize_property(self.db, self.user.id, account_id, property_id, start, end, permission=permission)
        if permission not in ('performance', 'statements') or not isinstance(start, date) or not isinstance(end, date) or start >= end:
            raise AccessError('Invalid reporting interval.', 400)
        grant = self.db.scalar(select(PropertyGrant).join(Account).join(Property, Property.id == PropertyGrant.property_id).where(
            Account.id == account_id, Account.active.is_(True), PropertyGrant.property_id == property_id,
            PropertyGrant.revoked_at.is_(None), Property.active.is_(True), PropertyGrant.reporting_from <= start,
            (PropertyGrant.reporting_until.is_(None) | (PropertyGrant.reporting_until >= end))))
        if not grant:
            raise AccessError()
        return grant

    def audit(self, action, **details):
        record(self.db, g.owner_access_actor, action, self.account,
               membership_id=self.member.id if self.member else None, **details)
        self.db.commit()


def portal_url(account_id, membership_id):
    def build(endpoint, **values):
        if endpoint == 'static':
            return url_for('owner_access.preview.asset', **values)
        endpoint = {'dashboard_data': 'dashboard_data_route'}.get(endpoint, endpoint)
        return url_for('owner_access.preview.' + endpoint, account_id=account_id, membership_id=membership_id, **values)
    return build


def template_context(scope):
    account_id = scope.account.id
    options = [{'name': 'Account overview · all reports', 'url': url_for('.home', account_id=account_id), 'selected': scope.member is None}]
    for member, user in scope.db.execute(select(Membership, User).join(User, User.id == Membership.user_id).where(
            Membership.account_id == account_id, Membership.revoked_at.is_(None), User.active.is_(True)).order_by(User.email)):
        options.append({'name': user.email, 'url': url_for('.home', account_id=account_id, membership_id=member.id),
                        'selected': scope.member is not None and scope.member.id == member.id})
    return {'owner_template_prefix': 'owner_preview/', 'portal_url': portal_url(account_id, scope.member.id if scope.member else None),
            'admin_preview': {'account': scope.account.name, 'options': options,
                              'back': url_for('owner_access.page', _anchor=f'owner={account_id}')}}


def dashboard_data(scope):
    try:
        account = int(request.args['account']) if 'account' in request.args else scope.account.id
        prop = int(request.args['property']) if request.args.get('property') not in (None, 'all') else None
        month = date.fromisoformat(request.args['month'] + '-01') if 'month' in request.args else None
        if account != scope.account.id or (prop is not None and prop <= 0):
            raise AccessError()
    except (ValueError, TypeError) as error:
        if isinstance(error, AccessError):
            raise
        raise AccessError('Choose a valid property and month.', 400) from None
    return reporting.dashboard(scope.db, None, account_id=account, property_id=prop, month=month, access=scope)


@bp.errorhandler(AccessError)
def denied(error):
    return jsonify(error=str(error)), error.status


@bp.after_request
def privacy(response):
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
    if response.mimetype == 'application/pdf':
        response.headers['Content-Security-Policy'] = "sandbox; default-src 'none'"
    return response


@bp.get('/assets/<filename>')
def asset(filename):
    if filename not in ASSETS:
        raise AccessError('Asset unavailable.', 404)
    return send_from_directory(OWNER_ROOT / 'static', filename)


@bp.get('/<int:account_id>/')
@bp.get('/<int:account_id>/person/<int:membership_id>/')
def home(account_id, membership_id=None):
    with get_session() as db:
        scope = PreviewAccess(db, account_id, membership_id)
        data = dashboard_data(scope)
        view = template_context(scope)
        scope.audit('dashboard.previewed', month=data.get('month'), property_id=data.get('property_id'))
        return render_template('owner_preview/home.html', data=data, **view)


@bp.get('/<int:account_id>/api/dashboard')
@bp.get('/<int:account_id>/person/<int:membership_id>/api/dashboard')
def dashboard_data_route(account_id, membership_id=None):
    with get_session() as db:
        scope = PreviewAccess(db, account_id, membership_id)
        data = dashboard_data(scope)
        scope.audit('dashboard.previewed', month=data.get('month'), property_id=data.get('property_id'))
        return jsonify(data)


@bp.get('/<int:account_id>/statements/<int:statement_id>')
@bp.get('/<int:account_id>/person/<int:membership_id>/statements/<int:statement_id>')
def statement(account_id, statement_id, membership_id=None):
    with get_session() as db:
        scope = PreviewAccess(db, account_id, membership_id)
        data = reporting.get_statement(db, None, statement_id, access=scope)
        view = template_context(scope)
        scope.audit('statement.previewed', statement_id=statement_id)
        download = request.args.get('download') == '1'
        response = make_response(render_template('owner_preview/statement_download.html' if download else 'owner_preview/statement.html', statement=data, **view))
        if download:
            response.headers['Content-Disposition'] = f'attachment; filename="owner-statement-{data["period_start"]}-v{data["version"]}.html"'
        return response


@bp.get('/<int:account_id>/statements/<int:statement_id>/pdf')
@bp.get('/<int:account_id>/person/<int:membership_id>/statements/<int:statement_id>/pdf')
def statement_pdf(account_id, statement_id, membership_id=None):
    with get_session() as db:
        scope = PreviewAccess(db, account_id, membership_id)
        data = reporting.get_statement(db, None, statement_id, access=scope)
        document = db.get(StatementDocument, statement_id)
        if document is None:
            raise AccessError('PDF unavailable.', 404)
        response = make_response(document.content)
        scope.audit('statement.previewed', statement_id=statement_id, format='pdf')
    response.headers['Content-Type'] = 'application/pdf'
    response.headers['Content-Disposition'] = f'attachment; filename="owner-statement-{data["period_start"]}-v{data["version"]}.pdf"'
    return response
