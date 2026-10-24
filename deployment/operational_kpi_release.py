"""Allowlisted KPI overlay with guarded file AND snapshot-pointer rollback.

Run explicitly as root, with a reviewed consumer-local stage/manifest. No git
reset, pull, stash, migration, credential change or source-data update occurs.
"""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import tempfile
import time
import urllib.request

APP = Path('/opt/hostaway-messages/app')
PYTHON = '/opt/hostaway-messages/venv/bin/python3'
TIMER = 'hostaway-operational-kpi.timer'
WORKERS = ('hostaway-operational-kpi.service', 'hostaway-operational-kpi-release-refresh.service')
REVISION = 'operational-kpi.2026-10-24-details.v1'
PATHS = {'dashboard/kpi/estimates.py', 'dashboard/kpi/operations.py', 'dashboard/kpi/revenue_trend.py',
         'dashboard/kpi/snapshot_job.py', 'dashboard/kpi/snapshot_store.py', 'dashboard/kpi/release_rollback.py',
         'dashboard/static/css/operations-kpi.css', 'dashboard/static/js/operations-kpi.js', 'dashboard/templates/kpi/operations.html'}


def run(*args, check=True, input=None):
    return subprocess.run(args, check=check, input=input, capture_output=True, text=True)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path, data):
    fd, temporary = tempfile.mkstemp(prefix='.checkpoint-',dir=path.parent)
    try:
        with os.fdopen(fd,'w') as out:
            out.write(json.dumps(data,indent=2)+'\n');out.flush();os.fsync(out.fileno())
        os.replace(temporary,path)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)


class Release:
    def __init__(self, stage, backup):
        self.stage, self.backup = Path(stage).resolve(), Path(backup).resolve()
        self.data = json.loads((self.stage/'manifest.json').read_text())
        rows = self.data['files']
        assert len(rows) == len(PATHS) and {row['path'] for row in rows} == PATHS
        assert sum(row['new_file'] for row in rows) == 2
        for row in rows:
            p = PurePosixPath(row['path'])
            assert not p.is_absolute() and '..' not in p.parts
        assert self.backup.parent == Path('/opt/hostaway-messages/kpi-releases')
        assert self.backup.name == self.data['feature_commit'][:12]

    def snapshot(self, action, request=None, *, allow_running=False, require_revision=False):
        command = ['sudo', '-u', 'hostaway', 'env', f'PYTHONPATH={APP}', PYTHON,
                   str(self.stage/'files/dashboard/kpi/release_rollback.py'), action]
        if allow_running: command.append('--allow-running')
        if require_revision: command += ['--require-revision', REVISION]
        result = run(*command, input=json.dumps(request) if request else None)
        return json.loads(result.stdout)

    def stopped(self):
        for unit in (TIMER, *WORKERS):
            assert run('systemctl', 'is-active', unit, check=False).stdout.strip() not in {'active','activating','deactivating','reloading'}, 'Worker/timer is not stopped: '+unit

    def stop(self):
        run('systemctl', 'disable', '--now', TIMER)
        for unit in WORKERS: run('systemctl', 'stop', unit, check=False)
        self.stopped()

    def gate(self, installed=False):
        for row in self.data['files']:
            path = APP/row['path']
            assert not path.is_symlink() and not any(p.is_symlink() for p in path.parents if p != APP.parent), 'Symlink path blocked'
            assert sha(self.stage/'files'/row['path']) == row['installed_sha256'], 'Staged content mismatch'
            if installed:
                assert path.is_file() and sha(path) == row['installed_sha256'], 'Installed file drift: '+row['path']
            elif row['new_file']:
                assert not path.exists(), 'New path already exists: '+row['path']
            else:
                assert path.is_file() and sha(path) == row['expected_preimage_sha256'], 'Preimage drift: '+row['path']

    def replace(self, source, target, info):
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix='.operational-kpi-', dir=target.parent)
        try:
            with os.fdopen(fd, 'wb') as out:
                out.write(source.read_bytes()); out.flush(); os.fsync(out.fileno())
            os.chown(temporary, info['uid'], info['gid']); os.chmod(temporary, info['mode'])
            os.replace(temporary, target)
        finally:
            if os.path.exists(temporary): os.unlink(temporary)

    def rollback(self):
        self.stop()
        originals = json.loads((self.backup/'originals.json').read_text())
        # Check every file before changing either saved state or source code.
        for row in originals:
            assert not (APP/row['path']).is_symlink()
            assert sha(APP/row['path']) in {row['installed_sha256'],row['expected_preimage_sha256']}, 'Rollback file drift: '+row['path']
            assert sha(self.backup/'preimages'/row['path']) == row['expected_preimage_sha256']
        before = json.loads((self.backup/'snapshot-before.json').read_text())
        expected = json.loads((self.backup/'snapshot-expected.json').read_text())
        run('systemctl', 'stop', 'hostaway-dashboard')
        # A state drift error leaves new files in place; never start the old UI
        # against an unreviewed newer cleaning-percentage snapshot.
        try:
            result = self.snapshot('restore', {'before':before, 'expected':expected})
        except Exception:
            # When the entire new release is still intact it can safely serve
            # the newer pointer. Never start partially restored/older code.
            try: self.gate(installed=True)
            except AssertionError: pass
            else: run('systemctl','start','hostaway-dashboard')
            raise
        assert result['status'] == 'snapshot_pointer_restored'
        for row in originals:
            assert sha(APP/row['path']) in {row['installed_sha256'],row['expected_preimage_sha256']}
            self.replace(self.backup/'preimages'/row['path'], APP/row['path'], row)
        for row in originals: assert sha(APP/row['path']) == row['expected_preimage_sha256']
        run('systemctl', 'start', 'hostaway-dashboard')
        assert health(), 'Rollback health check failed'
        return {'status':'rolled_back','files_restored':len(originals),'snapshot':result,'timer_disabled':True,'workers_stopped':True,'history_retained':True}

    def apply(self):
        self.gate()
        assert not self.backup.exists(), 'Backup exists; inspect outcome instead of retrying'
        self.stop()
        before = self.snapshot('capture')
        self.backup.mkdir(mode=0o750)
        originals = []
        for row in self.data['files']:
            if row['new_file']: continue
            path = APP/row['path']; attrs = path.stat()
            dest = self.backup/'preimages'/row['path']; dest.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(path,dest)
            originals.append({**row,'uid':attrs.st_uid,'gid':attrs.st_gid,'mode':attrs.st_mode & 0o777})
        write_json(self.backup/'originals.json', originals)
        write_json(self.backup/'snapshot-before.json', before)
        write_json(self.backup/'snapshot-expected.json', before)
        write_json(self.backup/'manifest.json', self.data)
        service = {'uid':int(run('id','-u','hostaway').stdout),'gid':int(run('id','-g','hostaway').stdout),'mode':0o644}
        run('systemctl','stop','hostaway-dashboard')
        try:
            self.gate(); self.stopped()
            assert self.snapshot('capture') == before, 'Snapshot changed during release preparation'
            for row in self.data['files']:
                self.replace(self.stage/'files'/row['path'], APP/row['path'], next((x for x in originals if x['path']==row['path']),service))
            self.gate(installed=True)
            run('systemctl','start','hostaway-dashboard')
            assert health(), 'Dashboard health failed after overlay'
        except Exception:
            self.rollback()
            raise
        return {'status':'overlay_installed','feature_commit':self.data['feature_commit'],'files_verified':len(PATHS),'timer_disabled':True}

    def refresh(self):
        self.gate(installed=True); self.stopped()
        expected = json.loads((self.backup/'snapshot-expected.json').read_text())
        assert self.snapshot('capture') == expected, 'Snapshot changed before the controlled refresh'
        outcome = run('systemd-run','--unit=hostaway-operational-kpi-release-refresh','--wait','--pipe','--collect',
                      '-p','User=hostaway','-p','Group=hostaway','-p',f'WorkingDirectory={APP}',
                      '-p','EnvironmentFile=/opt/hostaway-messages/.env','-p','TimeoutStartSec=90min',
                      '-p','NoNewPrivileges=true',PYTHON,'-m','dashboard.kpi.snapshot_job','refresh-once','--revision',REVISION,check=False)
        # Checkpoint even a failed attempt, so guarded rollback can fence it.
        self.stopped()
        after = self.snapshot('capture',allow_running=True)
        assert after['generation'] in {expected['generation'],expected['generation']+1}, 'Unexpected competing calculation'
        write_json(self.backup/'snapshot-expected.json',after)
        results = []
        for line in outcome.stdout.splitlines():
            try: results.append(json.loads(line))
            except json.JSONDecodeError: pass
        result = next((x for x in reversed(results) if isinstance(x,dict) and 'status' in x),{})
        assert outcome.returncode == 0 and result.get('status') in {'published','already_current'}, 'Controlled refresh did not publish; timer stays disabled'
        assert self.snapshot('capture',require_revision=True) == after
        assert after['last_good_snapshot_id'] == result['snapshot_id'] and after['status']=='completed'
        write_json(self.backup/'refreshed.json',result)
        return result

    def enable(self):
        self.gate(installed=True)
        result=json.loads((self.backup/'refreshed.json').read_text())
        assert result['status'] in {'published','already_current'}
        assert self.snapshot('capture',require_revision=True) == json.loads((self.backup/'snapshot-expected.json').read_text())
        run('systemctl','enable','--now',TIMER)
        assert run('systemctl','is-active',TIMER).stdout.strip()=='active'
        return {'status':'timer_enabled','snapshot_id':result['snapshot_id'],'next_due_at':result['next_due_at']}


def health():
    for _ in range(40):
        try:
            with urllib.request.urlopen('http://127.0.0.1:5001/health',timeout=2) as response:
                if response.status==200 and json.load(response).get('status')=='healthy': return True
        except Exception: pass
        time.sleep(1)
    return False


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('precheck','apply','refresh','enable','rollback','verify'))
    parser.add_argument('--stage',required=True);parser.add_argument('--backup',required=True)
    args=parser.parse_args()
    release=Release(args.stage,args.backup)
    with open('/tmp/operational-kpi-controlled-release.lock','a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        if args.action=='precheck': release.gate();result={'status':'preimages_match','files':len(PATHS)}
        elif args.action=='verify': release.gate(installed=True);result={'status':'installed_hashes_verified','health':health()}
        else: result=getattr(release,args.action)()
    print(json.dumps(result))


if __name__=='__main__': main()
