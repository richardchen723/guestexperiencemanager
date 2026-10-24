"""Guarded release rollback of the saved pointer; snapshot history is retained.

Only the explicit deployment helper invokes this. HTTP and scheduled jobs do
not import it. The caller must stop the timer and worker before restore.
"""
from datetime import datetime

from sqlalchemy import select

from dashboard.kpi.operations import as_utc
from dashboard.kpi.snapshot_store import OperationalSnapshot, OperationalSnapshotState, STATE_KEY, session_factory


def state_document(state):
    return {column.name: as_utc(value).isoformat() if isinstance(value, datetime) else value
            for column in OperationalSnapshotState.__table__.columns
            for value in (getattr(state, column.name),)}


def capture_state(*, factory=session_factory, allow_running=False, require_revision=None):
    """Small control-state projection, without reservation or metric payloads."""
    with factory() as session:
        state = session.get(OperationalSnapshotState, STATE_KEY)
        if state is None:
            raise ValueError('Expected existing snapshot state; nothing was changed')
        if state.status == 'running' and not allow_running:
            raise ValueError('Snapshot worker is running; stop and inspect before release')
        saved = session.get(OperationalSnapshot, state.last_good_snapshot_id) if state.last_good_snapshot_id else None
        if state.last_good_snapshot_id and saved is None:
            raise ValueError('Saved snapshot pointer is missing its history row')
        if require_revision and (not saved or saved.payload.get('calculation_revision') != require_revision):
            raise ValueError('Expected calculation revision was not published')
        return state_document(state)


def restore_state(before, expected, *, factory=session_factory):
    """Compare all control fields under lock, then fence and restore old pointer.

    ``expected`` is a recorded checkpoint, never an unchecked fresh read used
    to authorize a restore. Any intervening job or edit blocks rollback before
    files change. The monotonically increasing generation fences old workers.
    """
    columns = {c.name: c for c in OperationalSnapshotState.__table__.columns}
    if set(before) != set(columns) or set(expected) != set(columns):
        raise ValueError('Incomplete release snapshot checkpoint')
    if before['state_key'] != STATE_KEY or expected['state_key'] != STATE_KEY or before['status'] == 'running':
        raise ValueError('Invalid pre-release snapshot checkpoint')
    with factory() as session:
        state = session.execute(select(OperationalSnapshotState).where(OperationalSnapshotState.state_key == STATE_KEY).with_for_update()).scalar_one()
        if state_document(state) != expected:
            raise ValueError('Snapshot state drifted after the recorded checkpoint; rollback blocked')
        previous_id = before['last_good_snapshot_id']
        if previous_id:
            previous = session.get(OperationalSnapshot, previous_id)
            if previous is None or previous.contract_version != before['contract_version']:
                raise ValueError('Previous compatible saved snapshot is unavailable; rollback blocked')
        generation = state.generation + 1
        for name, column in columns.items():
            value = before[name]
            if value is not None and column.type.python_type is datetime:
                value = as_utc(value)
            setattr(state, name, value)
        state.generation = generation
        state.lease_token = None
        state.lease_expires_at = None
        session.commit()
    return {'status': 'snapshot_pointer_restored', 'snapshot_id': previous_id,
            'generation': generation, 'next_due_at': before['next_due_at'], 'history_retained': True}


def main():
    import argparse
    import json
    import sys
    from dotenv import load_dotenv
    parser = argparse.ArgumentParser(description='Explicit guarded operational KPI release rollback')
    parser.add_argument('action', choices=('capture', 'restore'))
    parser.add_argument('--allow-running', action='store_true')
    parser.add_argument('--require-revision')
    args = parser.parse_args()
    load_dotenv('/opt/hostaway-messages/.env', override=False)
    if args.action == 'capture':
        result = capture_state(allow_running=args.allow_running, require_revision=args.require_revision)
    else:
        request = json.load(sys.stdin)
        result = restore_state(request['before'], request['expected'])
    print(json.dumps(result))


if __name__ == '__main__':
    main()
