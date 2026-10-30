"""Synthetic fixtures only, in an explicitly named disposable PostgreSQL DB."""
import os

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from owner_portal.admin import OwnerAdmin
from owner_portal.app import create_app
from owner_portal.cli import runtime_sql
from owner_portal.models import Base, migrate


@pytest.fixture(scope='session')
def engine():
    url = os.getenv('OWNER_QA_DATABASE_URL')
    if not url:
        pytest.skip('Set OWNER_QA_DATABASE_URL to a disposable local PostgreSQL database.')
    parsed = make_url(url)
    assert parsed.host in ('localhost', '127.0.0.1') and parsed.database.startswith('codex_owner_qa_')
    engine = create_engine(url, pool_size=8)
    with engine.begin() as conn:
        migrate(conn)
        if not conn.execute(text("SELECT 1 FROM pg_roles WHERE rolname='owner_qa_web' ")).scalar():
            conn.execute(text('CREATE ROLE owner_qa_web LOGIN'))
        conn.execute(text(runtime_sql('owner_qa_web')))
        conn.execute(text('CREATE TABLE IF NOT EXISTS public.private_guest_fixture (name text)'))
    yield engine
    engine.dispose()


@pytest.fixture
def factory(engine):
    with engine.begin() as conn:
        conn.execute(text('TRUNCATE ' + ', '.join('owners.' + t.name for t in Base.metadata.sorted_tables) + ' RESTART IDENTITY CASCADE'))
    return sessionmaker(engine, expire_on_commit=False)


@pytest.fixture
def runtime_url(engine):
    return engine.url.set(username='owner_qa_web', password=None).render_as_string(hide_password=False)


@pytest.fixture
def app(factory, runtime_url):
    return create_app({'TESTING': True, 'SECRET_KEY': 'owner-qa-independent-secret-with-32-characters',
        'DATABASE_URL': runtime_url, 'OWNER_ORIGIN': 'https://owners.example.invalid',
        'GOOGLE_CLIENT_ID': 'owner-test-client', 'GOOGLE_CLIENT_SECRET': 'owner-test-secret'})


@pytest.fixture
def setup(factory):
    with factory.begin() as db:
        admin = OwnerAdmin(db, 'staff:1')
        a = admin.create_account({'name': 'Bennett household'})['id']
        b = admin.create_account({'name': 'Morgan household'})['id']
        p = admin.create_property({'name': 'Cedar House', 'timezone': 'America/Los_Angeles',
            'listing_ids': [101], 'single_unit_confirmed': True, 'portfolio_label': 'Coastal homes'}, {101})['id']
        q = admin.create_property({'name': 'Private Pine House', 'timezone': 'America/Los_Angeles',
            'listing_ids': [202], 'single_unit_confirmed': True}, {202})['id']
        grant = admin.grant({'account_id': a, 'property_id': p, 'reporting_from': '2026-01-01',
            'reporting_until_exclusive': '2027-01-01'})['id']
        admin.grant({'account_id': b, 'property_id': q, 'reporting_from': '2020-01-01'})
        invite = admin.invite({'account_id': a, 'email': 'owner@example.invalid', 'performance': True, 'statements': False})
    return {'account': a, 'other_account': b, 'property': p, 'other_property': q, 'grant': grant, 'invite': invite}
