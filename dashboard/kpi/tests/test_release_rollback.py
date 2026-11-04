"""Rollback restores the compatible pointer without deleting any snapshots."""
from datetime import timedelta

import pytest

from dashboard.kpi.operations import build_operations
from dashboard.kpi.release_rollback import capture_state, restore_state
from dashboard.kpi.snapshot_job import run_due_snapshot
from dashboard.kpi.snapshot_store import CALCULATION_REVISION, OperationalSnapshot, OperationalSnapshotState, claim_due_snapshot, publish_snapshot, read_saved_snapshot
from dashboard.kpi.tests.test_snapshot_store import NOW, store, run, old_revision


def test_rollback_restores_old_payload_pointer_schedule_and_retains_history(store):
    factory,_=store
    first=run(factory);old_revision(factory)
    before=capture_state(factory=factory)
    later=NOW+timedelta(hours=1)
    new=run_due_snapshot(factory=factory,now=later,clock=lambda:later,loader=lambda **_:{},refresh_revision=CALCULATION_REVISION)
    expected=capture_state(factory=factory)
    assert expected['last_good_snapshot_id']==new['snapshot_id']
    result=restore_state(before,expected,factory=factory)
    assert result['status']=='snapshot_pointer_restored'
    assert result['generation']==expected['generation']+1
    saved=read_saved_snapshot(factory=factory,now=later)
    assert saved['snapshot_state']['snapshot_id']==first['snapshot_id']
    assert 'calculation_revision' not in saved
    assert saved['snapshot_state']['next_due_at']==before['next_due_at']
    with factory() as session: assert session.query(OperationalSnapshot).count()==2


def test_old_per_ten_cleaning_value_is_restored_instead_of_new_percentage(store):
    from dashboard.kpi.tests.test_estimates import booking,data
    factory,_=store
    first=run(factory);old_revision(factory)
    with factory() as session:
        row=session.get(OperationalSnapshot,first['snapshot_id'])
        payload=dict(row.payload);payload['metrics']=[dict(m) for m in payload['metrics']]
        payload['metrics'][5].update(value=.5,display='.5',numerator=1,denominator=20,label='affected booked check-ins per 10 (estimate)')
        row.payload=payload;session.commit()
    before=capture_state(factory=factory)
    source=data();source.update(allow_estimates=True,reservations=[booking(i) for i in range(1,21)],issues=[{'id':1,'listing_id':1,'reservation_id':1,'category':'cleanliness'}])
    later=NOW+timedelta(hours=1)
    run_due_snapshot(factory=factory,now=later,clock=lambda:later,loader=lambda **_:source,refresh_revision=CALCULATION_REVISION)
    assert read_saved_snapshot(factory=factory,now=later)['metrics'][5]['value']==5
    restore_state(before,capture_state(factory=factory),factory=factory)
    old=read_saved_snapshot(factory=factory,now=later)['metrics'][5]
    assert old['value']==.5 and 'cleaning_weekly' not in old


@pytest.mark.parametrize('field,value',[('generation',10),('last_error','later change'),('last_good_snapshot_id',None)])
def test_later_control_edits_block_restore_without_any_pointer_write(store,field,value):
    factory,_=store;run(factory)
    before=capture_state(factory=factory)
    with factory() as session:
        state=session.get(OperationalSnapshotState,'portfolio');setattr(state,field,value);session.commit()
    with pytest.raises(ValueError,match='drifted'):
        restore_state(before,before,factory=factory)
    assert capture_state(factory=factory)[field]==value


def test_running_worker_must_stop_and_is_fenced_even_if_its_publish_returns_late(store):
    factory,_=store;run(factory);old_revision(factory)
    before=capture_state(factory=factory)
    later=NOW+timedelta(hours=1)
    claim=claim_due_snapshot(factory=factory,now=later,refresh_revision=CALCULATION_REVISION)
    with pytest.raises(ValueError,match='worker is running'): capture_state(factory=factory)
    # Deployment helper stops the actual process before recording this state.
    expected=capture_state(factory=factory,allow_running=True)
    restore_state(before,expected,factory=factory)
    assert publish_snapshot(claim,build_operations({},as_of=later),factory=factory,now=later)['status']=='superseded'
    assert capture_state(factory=factory)['lease_token'] is None


def test_bad_or_missing_prior_snapshot_checkpoint_cannot_restore(store):
    factory,_=store;run(factory)
    before=capture_state(factory=factory)
    with pytest.raises(ValueError,match='Incomplete'): restore_state({},before,factory=factory)
    broken={**before,'last_good_snapshot_id':'missing-history'}
    with pytest.raises(ValueError,match='unavailable'): restore_state(broken,before,factory=factory)
    assert capture_state(factory=factory)==before
