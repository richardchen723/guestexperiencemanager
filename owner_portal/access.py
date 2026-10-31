"""The only owner authorization projection. Re-evaluated on every request."""
import hashlib
from datetime import date

from sqlalchemy import select

from .models import Account, LoginSession, Membership, Property, PropertyGrant, User, utcnow


class AccessError(ValueError):
    def __init__(self, message='Access is unavailable.', status=403):
        super().__init__(message)
        self.status = status


def digest(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def current_user(db, token):
    if not isinstance(token, str) or len(token) > 256:
        raise AccessError('Please sign in.', 401)
    user = db.scalar(select(User).join(LoginSession, LoginSession.user_id == User.id).where(
        LoginSession.token_hash == digest(token), LoginSession.revoked_at.is_(None),
        LoginSession.expires_at > utcnow(), User.active.is_(True)))
    if user is None:
        raise AccessError('Please sign in.', 401)
    return user


def context(db, user_id):
    accounts = []
    rows = db.execute(select(Account, Membership).join(Membership, Membership.account_id == Account.id).where(
        Membership.user_id == user_id, Membership.revoked_at.is_(None), Account.active.is_(True)).order_by(Account.id))
    for account, member in rows:
        properties = []
        for prop, grant in db.execute(select(Property, PropertyGrant).join(PropertyGrant,
                PropertyGrant.property_id == Property.id).where(PropertyGrant.account_id == account.id,
                PropertyGrant.revoked_at.is_(None), Property.active.is_(True)).order_by(Property.name, Property.id)):
            properties.append({'id': prop.id, 'name': prop.name, 'timezone': prop.timezone,
                'currency': prop.currency, 'portfolio': prop.portfolio_label,
                'reporting_from': grant.reporting_from.isoformat(),
                'reporting_until_exclusive': grant.reporting_until.isoformat() if grant.reporting_until else None})
        accounts.append({'id': account.id, 'name': account.name,
            'permissions': {'performance': member.performance, 'statements': member.statements},
            'properties': properties})
    # Explicit allowlist: no user email, Google subject, source IDs, guest data or ORM serialization.
    return {'accounts': accounts, 'currency': 'USD',
        'performance_available': any(a['permissions']['performance'] for a in accounts),
        'statements_available': any(a['permissions']['statements'] for a in accounts)}


def authorize_property(db, user_id, account_id, property_id, start, end, *, permission='performance'):
    """Future metric/download handlers must call this before reading prepared data.

    Never silently clip a period: the client must select a fully authorized interval.
    This also prevents comparison data from leaking beyond historical access.
    """
    if permission not in ('performance', 'statements') or not isinstance(start, date) or not isinstance(end, date) or start >= end:
        raise AccessError('Invalid reporting interval.', 400)
    grant = db.scalar(select(PropertyGrant).join(Account).join(Membership,
        Membership.account_id == Account.id).join(Property, Property.id == PropertyGrant.property_id).join(
        User, User.id == Membership.user_id).where(
        Account.id == account_id, Account.active.is_(True), Membership.user_id == user_id,
        User.active.is_(True), Membership.revoked_at.is_(None), getattr(Membership, permission).is_(True),
        PropertyGrant.property_id == property_id, PropertyGrant.revoked_at.is_(None),
        Property.active.is_(True), PropertyGrant.reporting_from <= start,
        (PropertyGrant.reporting_until.is_(None) | (PropertyGrant.reporting_until >= end))))
    if grant is None:
        raise AccessError()
    return grant
