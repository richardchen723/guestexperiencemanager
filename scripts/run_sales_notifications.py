#!/usr/bin/env python3
"""Sales-aware shared notification worker. Preview by default; explicit live gates.

Requires the shared #817 notification framework installed in this checkout.
This is a replacement entry point for the shared runner, with all existing
Tickets/Procurement definitions and policies retained through composition.
"""
import argparse
from collections import Counter
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true', help='Plan and dispatch under the shared rollout allowlist')
    parser.add_argument('--limit', type=int, default=100)
    args = parser.parse_args()
    if not 1 <= args.limit <= 1000:
        parser.error('--limit must be 1–1000')
    url = os.environ.get('DATABASE_URL')
    origin = os.environ.get('NOTIFICATIONS_PUBLIC_ORIGIN')
    if not url or not origin:
        parser.error('DATABASE_URL and NOTIFICATIONS_PUBLIC_ORIGIN are required')
    try:
        from sqlalchemy import create_engine
        from sqlalchemy.engine import make_url
        from sqlalchemy.orm import sessionmaker
        from dashboard.notifications.adapters import ApplicationPolicy, definitions
        from dashboard.notifications.planner import plan_next
        from dashboard.notifications.templates import canonical_link
        from dashboard.sales.notifications import register, SalesAudiencePolicy, generate, capture_pending
        from dashboard.sales.service import SalesService
        if make_url(url).get_backend_name() != 'postgresql':
            parser.error('Use an explicit PostgreSQL database')
        canonical_link(origin, '/')
        registry = register(definitions())
        policy = lambda db: SalesAudiencePolicy(db, ApplicationPolicy(db))
        transport = rollout = None
        if args.live:
            if os.getenv('NOTIFICATIONS_DELIVERY_ENABLED') != '1' or os.getenv('NOTIFICATIONS_CAPTURE_ENABLED') != '1':
                parser.error('Live mode requires both shared capture and delivery gates set to 1')
            from dashboard.notifications.rollout import load_rollout
            from dashboard.notifications.transport import OpenClawTransport
            from dashboard.notifications.worker import plan_next_live, run_once
            rollout = load_rollout(os.environ['NOTIFICATIONS_ROLLOUT_FILE'])
            for name in rollout.event_types:
                registry.get(name, 1)
            token = Path(os.environ['NOTIFICATIONS_BRIDGE_TOKEN_FILE'])
            if not token.is_file() or token.stat().st_mode & 0o077:
                parser.error('Bridge token file must be private (0600)')
            transport = OpenClawTransport(os.environ['NOTIFICATIONS_BRIDGE_ORIGIN'], token.read_text().strip())
        engine = create_engine(url, hide_parameters=True)
        counts = Counter()
        try:
            sessions = sessionmaker(engine)
            with sessions() as db, db.begin():
                service = SalesService(db, actor='system:sales-reminders', actor_name='Sales reminders', users={})
                generate(service)
                counts['captured'] = capture_pending(service, args.limit)
            for _ in range(args.limit):
                with sessions() as db, db.begin():
                    result = plan_next_live(db, registry, policy(db), origin=origin, rollout=rollout) if args.live else plan_next(db, registry, policy(db), origin=origin)
                    if result:
                        counts[result.status] += 1
                claim = run_once(sessions, registry, policy, transport, allow_send=True) if args.live else None
                if claim:
                    counts[claim.action] += 1
                if not result and not claim:
                    break
        finally:
            engine.dispose()
        print(f'Notification work: {dict(counts)}. '+('Provider acceptance is not a read receipt.' if args.live else 'Preview only; no messages sent.'))
        return 0
    except Exception as exc:
        # Never print provider/database errors that might contain credentials.
        print(f'Sales notification worker stopped ({type(exc).__name__}); inspect configuration and persisted delivery history.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
