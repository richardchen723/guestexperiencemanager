"""Explicit migration and least-privilege runtime grant generation."""
import argparse
import os
import re

from sqlalchemy import create_engine

from .models import migrate


def runtime_sql(role):
    if not re.fullmatch(r'[a-z][a-z0-9_]{0,62}', role):
        raise ValueError('Use a lowercase PostgreSQL role identifier.')
    return f'''-- Run as schema administrator for an existing dedicated LOGIN role.
-- Do not use a staff/application role or grant membership in one.
GRANT USAGE ON SCHEMA owners TO {role};
GRANT SELECT ON owners.accounts, owners.properties, owners.property_grants TO {role};
GRANT SELECT ON owners.performance_months, owners.published_statements TO {role};
GRANT SELECT ON owners.statement_documents TO {role};
GRANT SELECT ON owners.requests TO {role};
GRANT UPDATE (response, responded_by, responded_at) ON owners.requests TO {role};
GRANT SELECT, INSERT, UPDATE ON owners.users, owners.memberships,
    owners.login_flows, owners.login_sessions TO {role};
GRANT SELECT, UPDATE ON owners.invitations TO {role};
GRANT INSERT ON owners.audit_events TO {role};
GRANT SELECT (id) ON owners.audit_events TO {role};
GRANT USAGE ON SEQUENCE owners.users_id_seq, owners.memberships_id_seq,
    owners.audit_events_id_seq TO {role};
-- No public/brain/bookkeeping/sales/users source table privileges.
'''


def prepare_sql(role):
    if not re.fullmatch(r'[a-z][a-z0-9_]{0,62}', role):
        raise ValueError('Use a lowercase PostgreSQL role identifier.')
    return f'''GRANT USAGE ON SCHEMA owners, public, brain TO {role};
GRANT SELECT ON owners.properties, owners.listing_mappings TO {role};
GRANT SELECT ON owners.finance_reconciliations TO {role};
GRANT SELECT, INSERT, UPDATE ON owners.finance_cache, owners.performance_months TO {role};
GRANT SELECT, INSERT, UPDATE ON owners.calendar_observations TO {role};
GRANT SELECT ON owners.care_source TO {role};
GRANT SELECT (reservation_id, listing_id, channel_name, arrival_date, departure_date,
    status, total_price, currency, updated_on, reservation_date, cancellation_date) ON public.reservations TO {role};
GRANT SELECT (listing_id, last_synced_at) ON public.listings TO {role};
GRANT SELECT (listing_id, calendar_date, snapshot_date, status, is_available, price,
    minimum_stay) ON brain.calendar_snapshots TO {role};
-- No guest columns, raw calendar payloads, messaging, statements or access writes.
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('migrate', 'runtime-sql', 'prepare-sql'))
    parser.add_argument('--role', default='owner_portal_web')
    args = parser.parse_args()
    if args.action in ('runtime-sql', 'prepare-sql'):
        print((runtime_sql if args.action == 'runtime-sql' else prepare_sql)(args.role))
        return
    engine = create_engine(os.environ['OWNER_ADMIN_DATABASE_URL'])
    try:
        with engine.begin() as conn:
            migrate(conn)
        print('Owner schema created. No accounts, access grants, invitations, or emails created.')
    finally:
        engine.dispose()


if __name__ == '__main__':
    main()
