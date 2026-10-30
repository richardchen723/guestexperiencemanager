from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import ProgrammingError

from owner_portal.access import AccessError, authorize_property, context, current_user, digest
from owner_portal.admin import OwnerAdmin
from owner_portal.app import check_database_boundary
from owner_portal.auth import sign_in
from owner_portal.models import AuditEvent, Invitation, LoginSession, Membership, User, utcnow


CLAIMS = {'sub': 'synthetic-google-subject', 'email': 'owner@example.invalid'}


def accept(factory, invite):
    with factory.begin() as db:
        token = sign_in(db, CLAIMS, digest(invite['token']))
        user = current_user(db, token)
        return token, user.id


def test_authorized_projection_and_historical_scope(factory, setup):
    token, user = accept(factory, setup['invite'])
    with factory() as db:
        result = context(db, user)
        assert [a['id'] for a in result['accounts']] == [setup['account']]
        assert [p['id'] for p in result['accounts'][0]['properties']] == [setup['property']]
        assert result['accounts'][0]['permissions'] == {'performance': True, 'statements': False}
        assert 'Private Pine' not in str(result) and 'listing_id' not in str(result) and 'email' not in str(result)
        authorize_property(db, user, setup['account'], setup['property'], date(2026, 1, 1), date(2027, 1, 1))
        for account, prop, start, end, permission in (
            (setup['other_account'], setup['other_property'], date(2026,1,1), date(2026,2,1), 'performance'),
            (setup['account'], setup['other_property'], date(2026,1,1), date(2026,2,1), 'performance'),
            (setup['account'], setup['property'], date(2025,12,31), date(2026,2,1), 'performance'),
            (setup['account'], setup['property'], date(2026,12,1), date(2027,1,2), 'performance'),
            (setup['account'], setup['property'], date(2026,1,1), date(2026,2,1), 'statements'),
        ):
            with pytest.raises(AccessError):
                authorize_property(db, user, account, prop, start, end, permission=permission)


def test_membership_revocation_ends_sessions_and_pending_links(factory, setup):
    token, user_id = accept(factory, setup['invite'])
    with factory.begin() as db:
        admin = OwnerAdmin(db, 'staff:1')
        replacement = admin.invite({'account_id': setup['account'], 'email': CLAIMS['email'], 'performance': True, 'statements': True})
        member = db.scalar(select(Membership).where(Membership.user_id == user_id))
        admin.revoke('membership', member.id)
    with factory() as db:
        with pytest.raises(AccessError): current_user(db, token)
        assert context(db, user_id)['accounts'] == []
        with pytest.raises(AccessError): sign_in(db, CLAIMS, digest(replacement['token']))


def test_property_revocation_and_preview_are_audited(factory, setup):
    token, user_id = accept(factory, setup['invite'])
    with factory.begin() as db:
        admin = OwnerAdmin(db, 'staff:1')
        member = db.scalar(select(Membership).where(Membership.user_id == user_id))
        assert len(admin.preview(member.id)['accounts'][0]['properties']) == 1
        assert len(db.scalars(select(LoginSession)).all()) == 1  # Preview never impersonates.
        admin.revoke('grant', setup['grant'])
    with factory() as db:
        assert current_user(db, token).id == user_id
        assert context(db, user_id)['accounts'][0]['properties'] == []
        assert {'membership.previewed', 'grant.revoked'} <= set(db.scalars(select(AuditEvent.action)))


@pytest.mark.parametrize('condition', ['email', 'expired', 'revoked', 'disabled'])
def test_invitation_failures_create_no_identity(factory, setup, condition):
    with factory.begin() as db:
        invitation = db.get(Invitation, setup['invite']['id'])
        if condition == 'email': invitation.email = 'different@example.invalid'
        if condition == 'expired': invitation.expires_at = utcnow() - timedelta(seconds=1)
        if condition == 'revoked': invitation.revoked_at = utcnow()
        if condition == 'disabled': OwnerAdmin(db, 'staff:1').account(setup['account']).active = False
    with pytest.raises(AccessError): accept(factory, setup['invite'])
    with factory() as db:
        assert db.scalar(select(User.id)) is None
        assert db.scalar(select(LoginSession.token_hash)) is None


def test_concurrent_invitation_redemption_is_single_use(factory, setup):
    def attempt():
        try:
            accept(factory, setup['invite'])
            return 'accepted'
        except AccessError:
            return 'rejected'
    with ThreadPoolExecutor(max_workers=2) as executor:
        assert sorted(executor.map(lambda _: attempt(), range(2))) == ['accepted', 'rejected']
    with factory() as db:
        assert len(db.scalars(select(Membership)).all()) == 1
        assert len(db.scalars(select(LoginSession)).all()) == 1


def test_no_email_based_account_takeover_or_uninvited_signup(factory, setup):
    accept(factory, setup['invite'])
    with factory.begin() as db:
        with pytest.raises(AccessError): sign_in(db, {**CLAIMS, 'sub': 'different-google-subject'}, None)
    with factory() as db:
        assert len(db.scalars(select(User)).all()) == 1


def test_new_invitation_replaces_old_permissions(factory, setup):
    with factory.begin() as db:
        invite = OwnerAdmin(db, 'staff:1').invite({'account_id': setup['account'], 'email': CLAIMS['email'], 'performance': False, 'statements': True})
    with pytest.raises(AccessError): accept(factory, setup['invite'])
    _, user_id = accept(factory, invite)
    with factory() as db:
        assert context(db, user_id)['accounts'][0]['permissions'] == {'performance': False, 'statements': True}


def test_runtime_cannot_read_guest_sources_and_can_accept_invite(factory, setup, runtime_url, engine):
    runtime = create_engine(runtime_url)
    check_database_boundary(runtime)
    with runtime.connect() as conn:
        with pytest.raises(ProgrammingError): conn.execute(text('SELECT * FROM public.private_guest_fixture'))
    from sqlalchemy.orm import sessionmaker
    token, user_id = accept(sessionmaker(runtime, expire_on_commit=False), setup['invite'])
    with factory() as db:
        assert current_user(db, token).id == user_id
    with pytest.raises(RuntimeError): check_database_boundary(engine)
    runtime.dispose()
