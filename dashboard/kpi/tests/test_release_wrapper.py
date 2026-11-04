"""Dry-run the real release wrapper; no service or production access occurs."""
import json
from types import SimpleNamespace

import pytest

from deployment import operational_kpi_release as release


@pytest.fixture
def wrapper(tmp_path,monkeypatch):
    app=tmp_path/'app';stage=tmp_path/'stage';backup=tmp_path/'backup'
    backup.mkdir()
    monkeypatch.setattr(release,'APP',app)
    rows=[];originals=[]
    for path in sorted(release.PATHS):
        target=app/path;target.parent.mkdir(parents=True,exist_ok=True);target.write_text('new '+path)
        staged=stage/'files'/path;staged.parent.mkdir(parents=True,exist_ok=True);staged.write_bytes(target.read_bytes())
        previous=backup/'preimages'/path;previous.parent.mkdir(parents=True,exist_ok=True);previous.write_text('old '+path)
        row={'path':path,'installed_sha256':release.sha(target),'expected_preimage_sha256':release.sha(previous),'new_file':path.endswith(('revenue_trend.py','release_rollback.py'))}
        rows.append(row)
        if not row['new_file']: originals.append(row)
    for name,value in [('originals',originals),('snapshot-before',{'generation':1,'last_good_snapshot_id':'old'}),('snapshot-expected',{'generation':2,'last_good_snapshot_id':'new'})]:
        release.write_json(backup/(name+'.json'),value)
    r=release.Release.__new__(release.Release);r.stage=stage;r.backup=backup;r.data={'files':rows,'feature_commit':'testcommit'}
    trace=[]
    monkeypatch.setattr(release,'run',lambda *args,**kwargs:trace.append(args) or SimpleNamespace(stdout='inactive',returncode=0))
    monkeypatch.setattr(release,'health',lambda:True)
    monkeypatch.setattr(r,'replace',lambda source,target,info: trace.append(('file',info['path'])) or target.write_bytes(source.read_bytes()))
    return r,trace,monkeypatch


def test_restore_pointer_precedes_all_seven_files_and_stops_both_workers(wrapper):
    r,trace,monkeypatch=wrapper
    def snapshot(action,request=None,**kwargs):
        trace.append(('snapshot',action));assert request['before']['last_good_snapshot_id']=='old'
        assert request['expected']['last_good_snapshot_id']=='new'
        return {'status':'snapshot_pointer_restored','snapshot_id':'old'}
    monkeypatch.setattr(r,'snapshot',snapshot)
    result=r.rollback()
    assert result['files_restored']==7 and result['history_retained']
    assert trace.index(('snapshot','restore')) < next(i for i,event in enumerate(trace) if event[0]=='file')
    assert ('systemctl','stop','hostaway-operational-kpi.service') in trace
    assert ('systemctl','stop','hostaway-operational-kpi-release-refresh.service') in trace
    assert ('systemctl','disable','--now',release.TIMER) in trace
    assert trace[-1]==('systemctl','start','hostaway-dashboard')


def test_file_drift_blocks_before_snapshot_or_files_change(wrapper):
    r,trace,monkeypatch=wrapper
    (release.APP/'dashboard/kpi/operations.py').write_text('later unrelated edit')
    monkeypatch.setattr(r,'snapshot',lambda *a,**k:pytest.fail('No snapshot mutation on file drift'))
    with pytest.raises(AssertionError,match='file drift'): r.rollback()
    assert not any(event[0]=='file' for event in trace)


def test_snapshot_drift_blocks_all_file_restores_and_keeps_new_ui(wrapper):
    r,trace,monkeypatch=wrapper
    def drift(*args,**kwargs): raise ValueError('state drift')
    monkeypatch.setattr(r,'snapshot',drift)
    with pytest.raises(ValueError,match='state drift'): r.rollback()
    assert not any(event[0]=='file' for event in trace)
    assert trace[-1]==('systemctl','start','hostaway-dashboard')
    r.gate(installed=True)


@pytest.mark.parametrize('status',['busy','not_due','failed'])
def test_refresh_exit_zero_is_not_sufficient_to_enable_timer(wrapper,status):
    r,trace,monkeypatch=wrapper
    state={'generation':2,'last_good_snapshot_id':'new'}
    monkeypatch.setattr(r,'snapshot',lambda *a,**k:state)
    def run(*args,**kwargs):
        trace.append(args)
        return SimpleNamespace(stdout=json.dumps({'status':status}) if args[0]=='systemd-run' else 'inactive',returncode=0)
    monkeypatch.setattr(release,'run',run)
    with pytest.raises(AssertionError,match='did not publish'): r.refresh()
    assert not (r.backup/'refreshed.json').exists()
    assert not any(event[:3]==('systemctl','enable','--now') for event in trace)


@pytest.mark.parametrize('status',['published','already_current'])
def test_refresh_requires_matching_published_checkpoint_and_enable_uses_it(wrapper,status):
    r,trace,monkeypatch=wrapper
    before={'generation':2,'last_good_snapshot_id':'new'}
    after={'generation':3 if status=='published' else 2,'last_good_snapshot_id':'published-id','status':'completed'}
    current=[before]
    monkeypatch.setattr(r,'snapshot',lambda *a,**k:current[0])
    result={'status':status,'snapshot_id':'published-id','next_due_at':'2026-10-25T12:00:00Z'}
    def run(*args,**kwargs):
        trace.append(args)
        if args[0]=='systemd-run':
            current[0]=after
            return SimpleNamespace(stdout='Transient unit output\n'+json.dumps(result)+'\n',returncode=0)
        return SimpleNamespace(stdout='active' if args[:3]==('systemctl','is-active',release.TIMER) and ('systemctl','enable','--now',release.TIMER) in trace else 'inactive',returncode=0)
    monkeypatch.setattr(release,'run',run)
    assert r.refresh()==result
    assert json.loads((r.backup/'snapshot-expected.json').read_text())==after
    assert r.enable()['snapshot_id']=='published-id'
    assert ('systemctl','enable','--now',release.TIMER) in trace
