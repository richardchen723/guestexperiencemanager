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
GRANT SELECT, INSERT, UPDATE ON owners.users, owners.memberships,
    owners.login_flows, owners.login_sessions TO {role};
GRANT SELECT, UPDATE ON owners.invitations TO {role};
GRANT INSERT ON owners.audit_events TO {role};
GRANT SELECT (id) ON owners.audit_events TO {role};
GRANT USAGE ON SEQUENCE owners.users_id_seq, owners.memberships_id_seq,
    owners.audit_events_id_seq TO {role};
-- No public/brain/bookkeeping/sales/users source table privileges.
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('migrate', 'runtime-sql'))
    parser.add_argument('--role', default='owner_portal_web')
    args = parser.parse_args()
    if args.action == 'runtime-sql':
        print(runtime_sql(args.role))
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
