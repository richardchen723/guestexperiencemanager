"""Tests only use an explicitly named, local, disposable PostgreSQL database."""
import os
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker
from sqlalchemy.schema import CreateSchema

from dashboard.inquiries.models import Base, Settings
from dashboard.inquiries.service import InquiryService


PROPERTIES = {101: {"id": 101, "name": "Fair Haven", "aliases": ["Fair"], "tag_ids": [1]},
              102: {"id": 102, "name": "Emerald Haven", "aliases": [], "tag_ids": []}}
TAGS = {1: {"id": 1, "name": "Enchanted Havens"}}
USERS = {1: {"id": 1, "name": "Emma"}, 2: {"id": 2, "name": "Ethan"}}
NOW = datetime(2026, 10, 20, 18, tzinfo=timezone.utc)


@pytest.fixture(scope="session")
def engine():
    url = os.getenv("INQUIRY_QA_DATABASE_URL")
    if not url:
        pytest.skip("Set INQUIRY_QA_DATABASE_URL to a disposable local PostgreSQL database.")
    parsed = make_url(url)
    assert parsed.database.startswith("codex_inquiry_qa_") and parsed.host in (None, "localhost", "127.0.0.1")
    engine = create_engine(url, pool_size=10)
    with engine.begin() as conn:
        conn.execute(CreateSchema("inquiries", if_not_exists=True))
        Base.metadata.create_all(conn)
    yield engine
    engine.dispose()


@pytest.fixture
def factory(engine):
    assert make_url(str(engine.url)).database.startswith("codex_inquiry_qa_")
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE " + ", ".join(f'inquiries.{t.name}' for t in Base.metadata.sorted_tables) + " RESTART IDENTITY CASCADE"))
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        db.add(Settings(id=1, timezone="America/Los_Angeles", version=1))
        db.commit()
    return factory


@pytest.fixture
def db(factory):
    with factory() as session:
        yield session


@pytest.fixture
def service(db):
    return InquiryService(db, properties=PROPERTIES, tags=TAGS, users=USERS, actor="Emma", now=NOW)


def email(*, message_id="m1", thread_id="t1", reference="EH-ABC12345", received_at="2026-10-20T17:00:00+00:00", **changes):
    result = {"source": "enchanted-havens-email", "mailbox": "larry@enchantedhavens.com", "message_id": message_id,
              "thread_id": thread_id, "internet_message_id": f"<{message_id}@example.invalid>", "received_at": received_at,
              "sender": "Enchanted Havens <yunhang.chen@gmail.com>", "subject": f"New stay inquiry {reference} from Sally Example",
              "body": f"Reference: {reference}\nName: Sally Example\nEmail: sally@example.invalid\nPhone: (425)555-0100\nTrip type: Celebration\nProperty: Fair Haven\nDates: Nov 17–Nov 20, 2026\nGuests: 3\nNotes: Three sisters and a small dog."}
    result.update(changes)
    return result


def reply(**changes):
    result = email(message_id="m2", received_at="2026-10-20T17:30:00+00:00", sender="Sally <sally@example.invalid>",
                   subject="Re: New stay inquiry EH-ABC12345 from Sally Example", body="We now need November 22–25 for four adults and two children.")
    result["extraction"] = {"reference":"EH-ABC12345", "fields":{"check_in":"2026-11-22", "check_out":"2026-11-25", "guest_count":6, "adults":4,"children":2}}
    result.update(changes)
    return result
