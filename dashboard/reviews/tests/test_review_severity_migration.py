import os
from unittest.mock import patch

from sqlalchemy import create_engine, inspect, text

from dashboard.tickets.models import _migrate_review_queue_severity_fields


def test_review_severity_migration_is_idempotent_for_existing_sqlite_table():
    engine = create_engine('sqlite:///:memory:')
    with engine.begin() as connection:
        connection.execute(text(
            'CREATE TABLE review_queue_states ('
            'reservation_id INTEGER PRIMARY KEY, listing_id INTEGER NOT NULL)'
        ))

    with patch.dict(os.environ, {'DATABASE_URL': ''}):
        _migrate_review_queue_severity_fields(engine)
        _migrate_review_queue_severity_fields(engine)

    columns = {column['name'] for column in inspect(engine).get_columns('review_queue_states')}
    assert {
        'risk_override_key',
        'risk_overridden_at',
        'risk_overridden_by',
        'ai_risk_key',
        'ai_risk_confidence',
        'ai_risk_good_review_likelihood',
        'ai_risk_reasons',
    }.issubset(columns)
