"""Explicit staff decisions. No inference from portfolios or sales relationships."""
import re
import secrets
from datetime import date, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select, text

from .access import AccessError, context, digest
from .models import (Account, AuditEvent, Invitation, ListingMapping, LoginSession,
                     Membership, Property, PropertyGrant, User, utcnow)


def label(value, limit=160):
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > limit:
        raise AccessError('A valid name or value is required.', 400)
    return value.strip()


def email_address(value):
    email = label(value, 320).lower()
    if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', email):
        raise AccessError('Enter a valid email address.', 400)
    return email


def identity(value):
    if isinstance(value, bool) or not isinstance(value, int) or not 0 < value <= 2147483647:
        raise AccessError('A valid record ID is required.', 400)
    return value


def permission_flags(data):
    values = [data.get('performance'), data.get('statements')]
    if any(type(value) is not bool for value in values) or not any(values):
        raise AccessError('Choose performance, statements, or both.', 400)
    return dict(zip(('performance', 'statements'), values))


def record(db, actor, action, row, **details):
    db.flush()
    db.add(AuditEvent(actor=actor, action=action, target_type=row.__tablename__, target_id=row.id, details=details))


class OwnerAdmin:
    def __init__(self, db, actor):
        self.db, self.actor = db, actor

    def account(self, account_id):
        account_id = identity(account_id)
        self.db.execute(text('SELECT pg_advisory_xact_lock(847, :id)'), {'id': account_id})
        account = self.db.scalar(select(Account).where(Account.id == account_id))
        if not account or not account.active:
            raise AccessError('Account is unavailable.', 404)
        return account

    def create_account(self, data):
        account = Account(name=label(data.get('name')))
        self.db.add(account)
        record(self.db, self.actor, 'account.created', account)
        return {'id': account.id}

    def create_property(self, data, available_listing_ids):
        try:
            timezone = label(data.get('timezone'), 80)
            ZoneInfo(timezone)
        except (ValueError, ZoneInfoNotFoundError):
            raise AccessError('A valid IANA property timezone is required.', 400) from None
        ids = data.get('listing_ids')
        if not isinstance(ids, list) or not ids or len(ids) > 30 or len({identity(i) for i in ids}) != len(ids):
            raise AccessError('Select distinct Hostaway listings for this physical unit.', 400)
        if not set(ids).issubset(available_listing_ids) or data.get('single_unit_confirmed') is not True:
            raise AccessError('Verify that all selected listings represent this one physical unit. Bundles need a separate mapping review.', 400)
        prop = Property(name=label(data.get('name')), timezone=timezone,
            portfolio_label=label(data['portfolio_label']) if data.get('portfolio_label') else None)
        self.db.add(prop)
        self.db.flush()
        for listing_id in ids:
            self.db.add(ListingMapping(property_id=prop.id, listing_id=listing_id, verified_by=self.actor))
        record(self.db, self.actor, 'property.mapped', prop, listing_ids=ids)
        return {'id': prop.id}

    def grant(self, data):
        account = self.account(data.get('account_id'))
        prop = self.db.get(Property, identity(data.get('property_id')))
        if not prop or not prop.active:
            raise AccessError('Property is unavailable.', 404)
        try:
            start = date.fromisoformat(data['reporting_from'])
            end = date.fromisoformat(data['reporting_until_exclusive']) if data.get('reporting_until_exclusive') else None
            if end and end <= start:
                raise ValueError()
        except (KeyError, TypeError, ValueError):
            raise AccessError('Enter a reporting start date and a later end date, if any.', 400) from None
        grant = self.db.scalar(select(PropertyGrant).where(PropertyGrant.account_id == account.id, PropertyGrant.property_id == prop.id))
        previous = None
        if grant:
            previous = {'from': str(grant.reporting_from), 'until': str(grant.reporting_until), 'revoked': bool(grant.revoked_at)}
        else:
            grant = PropertyGrant(account_id=account.id, property_id=prop.id)
            self.db.add(grant)
        grant.reporting_from, grant.reporting_until, grant.revoked_at = start, end, None
        record(self.db, self.actor, 'property.granted', grant, previous=previous, reporting_from=str(start), reporting_until=str(end))
        return {'id': grant.id}

    def invite(self, data):
        account = self.account(data.get('account_id'))
        email = email_address(data.get('email'))
        flags = permission_flags(data)
        # Replacement cancels previous links, including links with broader permissions.
        for previous in self.db.scalars(select(Invitation).where(Invitation.account_id == account.id,
                Invitation.email == email, Invitation.accepted_at.is_(None), Invitation.revoked_at.is_(None))):
            previous.revoked_at = utcnow()
        raw = secrets.token_urlsafe(32)
        invitation = Invitation(account_id=account.id, email=email, token_hash=digest(raw),
            expires_at=utcnow() + timedelta(days=7), **flags)
        self.db.add(invitation)
        record(self.db, self.actor, 'invitation.created', invitation, **flags)
        return {'id': invitation.id, 'token': raw, 'expires_at': invitation.expires_at.isoformat()}

    def revoke(self, kind, row_id):
        model = {'membership': Membership, 'grant': PropertyGrant, 'invitation': Invitation}.get(kind)
        if model is None:
            raise AccessError('Invalid access type.', 400)
        row = self.db.get(model, identity(row_id))
        if row is None:
            raise AccessError('Access record is unavailable.', 404)
        self.account(row.account_id)  # Same lock order as invitation acceptance.
        self.db.refresh(row, with_for_update=True)
        row.revoked_at = utcnow()
        if kind == 'membership':
            user = self.db.get(User, row.user_id)
            for invite in self.db.scalars(select(Invitation).where(Invitation.account_id == row.account_id,
                    Invitation.email == user.email, Invitation.accepted_at.is_(None), Invitation.revoked_at.is_(None))):
                invite.revoked_at = utcnow()
            # End all sessions for this identity; other memberships survive a fresh sign-in.
            for login in self.db.scalars(select(LoginSession).where(LoginSession.user_id == user.id, LoginSession.revoked_at.is_(None))):
                login.revoked_at = utcnow()
        record(self.db, self.actor, kind + '.revoked', row)
        return {'revoked': True}

    def preview(self, membership_id):
        member = self.db.get(Membership, identity(membership_id))
        if not member or member.revoked_at:
            raise AccessError('Membership is unavailable.', 404)
        user = self.db.get(User, member.user_id)
        if not user.active:
            raise AccessError('Owner identity is inactive.', 404)
        projection = context(self.db, user.id)
        projection['accounts'] = [a for a in projection['accounts'] if a['id'] == member.account_id]
        record(self.db, self.actor, 'membership.previewed', member)
        return projection

    def overview(self):
        return {
            'accounts': [{'id': a.id, 'name': a.name} for a in self.db.scalars(select(Account).where(Account.active.is_(True)).order_by(Account.id))],
            'properties': [{'id': p.id, 'name': p.name, 'timezone': p.timezone, 'portfolio': p.portfolio_label} for p in self.db.scalars(select(Property).order_by(Property.id))],
            'grants': [{'id': g.id, 'account_id': g.account_id, 'property_id': g.property_id, 'from': str(g.reporting_from), 'until_exclusive': str(g.reporting_until) if g.reporting_until else None, 'revoked': bool(g.revoked_at)} for g in self.db.scalars(select(PropertyGrant).order_by(PropertyGrant.id))],
            'memberships': [{'id': m.id, 'account_id': m.account_id, 'email': u.email, 'performance': m.performance, 'statements': m.statements, 'revoked': bool(m.revoked_at)} for m, u in self.db.execute(select(Membership, User).join(User).order_by(Membership.id))],
            'invitations': [{'id': i.id, 'account_id': i.account_id, 'email': i.email, 'performance': i.performance, 'statements': i.statements, 'expires_at': i.expires_at.isoformat(), 'accepted': bool(i.accepted_at), 'revoked': bool(i.revoked_at)} for i in self.db.scalars(select(Invitation).order_by(Invitation.id.desc()).limit(200))],
        }
