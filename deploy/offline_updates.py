#!/usr/bin/env python3
"""Signed, offline Git courier. No serial/radio integration or public server."""
import argparse
import ast
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile

REPOSITORY='TheRiddler08/Ford-Better-Head-Unit-'
ORIGIN='https://github.com/'+REPOSITORY+'.git'
NAMESPACE='car-dashboard-courier'
MAX_PACKAGE=2*1024**3
SHA=re.compile(r'^[0-9a-f]{40}$')
TARGET=re.compile(r'^[A-Za-z0-9_.-]+@[A-Za-z0-9_.:-]+$')


def run(args, cwd=None, data=None, timeout=120):
    result=subprocess.run([str(a) for a in args],cwd=cwd,input=data,stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE,timeout=timeout)
    if result.returncode:
        # Commands contain paths, not token values. SSH keys are never read here.
        raise RuntimeError(' '.join(str(a) for a in args[:3])+': '+result.stderr.decode(errors='replace')[-3000:])
    return result.stdout


@contextlib.contextmanager
def locked(path):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('a') as f:
        fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
        yield


def save_json(path,value):
    path=Path(path)
    with tempfile.NamedTemporaryFile(mode='w',dir=path.parent,delete=False) as f:
        json.dump(value,f);f.flush();os.fsync(f.fileno());pending=Path(f.name)
    os.replace(pending,path)


def digest(path):
    h=hashlib.sha256()
    with open(path,'rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''):h.update(chunk)
    return h.hexdigest()


def ensure_key(path):
    path=Path(path).expanduser();path.parent.mkdir(parents=True,exist_ok=True)
    if not path.exists():run(['ssh-keygen','-q','-t','ed25519','-N','','-f',path,'-C','car-duet-update-courier'])
    if not path.with_suffix(path.suffix+'.pub').exists():raise ValueError('Courier public key missing')
    if path.stat().st_mode & 0o077:raise ValueError('Courier private key must be chmod 600')
    return path


def refresh_duet_runtime(repo, commit, destination):
    """Refresh only already-installed, unmodified managed display helpers."""
    destination=Path(destination)
    result={}
    for name in ('proxy.py','launcher_lock.py','launch.sh'):
        target=destination/name
        if not target.exists() or target.is_symlink():continue
        history=run(['git','--git-dir',repo,'rev-list','--objects','refs/heads/main','--','duet/'+name]).decode().splitlines()
        known={line.split(' ',1)[0] for line in history if line.endswith(' duet/'+name)}
        current=run(['git','--git-dir',repo,'hash-object',target]).decode().strip()
        if current not in known:
            result[name]='local customization preserved';continue
        source=run(['git','--git-dir',repo,'show',commit+':duet/'+name])
        if name.endswith('.py'):ast.parse(source)
        else:run(['bash','-n'],data=source)
        if target.read_bytes()==source:
            result[name]='current';continue
        with tempfile.NamedTemporaryFile(dir=destination,delete=False) as file:
            file.write(source);pending=Path(file.name)
        os.chmod(pending,0o755 if name.endswith('.sh') else 0o644)
        os.replace(pending,target)
        result[name]='updated; active at next display restart'
    return result


def prepare(state,key):
    """Download all Git objects so the Tiny needs no Internet or remote base."""
    state=Path(state);state.mkdir(parents=True,exist_ok=True)
    with locked(state/'courier.lock'):
        key=ensure_key(key)
        repo=state/'cache.git'
        if not repo.exists():run(['git','init','--bare',repo])
        run(['git','--git-dir',repo,'fetch','--no-tags',ORIGIN,'refs/heads/main'],timeout=180)
        commit=run(['git','--git-dir',repo,'rev-parse','FETCH_HEAD']).decode().strip()
        run(['git','--git-dir',repo,'update-ref','refs/heads/main',commit])
        # Keep the previous ready archive until a completely signed replacement exists.
        with tempfile.TemporaryDirectory(dir=state) as temp:
            temp=Path(temp);bundle=temp/'update.bundle'
            run(['git','--git-dir',repo,'bundle','create',bundle,'refs/heads/main'],timeout=180)
            manifest={'schema':1,'repository':REPOSITORY,'branch':'main','commit':commit,
                      'bundleSha256':digest(bundle),'created':int(time.time())}
            (temp/'manifest.json').write_text(json.dumps(manifest,sort_keys=True))
            run(['ssh-keygen','-Y','sign','-f',key,'-n',NAMESPACE,temp/'manifest.json'])
            with zipfile.ZipFile(temp/'ready.zip','w',compression=zipfile.ZIP_STORED) as package:
                for name in ['manifest.json','manifest.json.sig','update.bundle']:package.write(temp/name,name)
            if (temp/'ready.zip').stat().st_size>MAX_PACKAGE:raise ValueError('Package exceeds 2 GB')
            os.replace(temp/'ready.zip',state/'ready.zip')
            save_json(state/'prepared.json',manifest)
        refreshed=refresh_duet_runtime(repo,commit,state.parent)
        save_json(state/'duet-runtime.json',refreshed)
        return dict(manifest,duetRuntime=refreshed)


def verify_package(package,allowed_signers,destination):
    """Extract exact files without trusting paths; verify signature before Git."""
    destination=Path(destination)
    with zipfile.ZipFile(package) as archive:
        expected={'manifest.json','manifest.json.sig','update.bundle'}
        entries=archive.infolist()
        if len(entries)!=3 or {e.filename for e in entries}!=expected:raise ValueError('Unexpected package entries')
        for entry in entries:
            cap=MAX_PACKAGE if entry.filename=='update.bundle' else 8192
            if entry.compress_type!=zipfile.ZIP_STORED or entry.file_size>cap or entry.file_size<1:
                raise ValueError('Invalid archive size or compression')
            with archive.open(entry) as src,(destination/entry.filename).open('wb') as dst:shutil.copyfileobj(src,dst,1024*1024)
    manifest_bytes=(destination/'manifest.json').read_bytes()
    run(['ssh-keygen','-Y','verify','-f',allowed_signers,'-I','duet','-n',NAMESPACE,
         '-s',destination/'manifest.json.sig'],data=manifest_bytes)
    manifest=json.loads(manifest_bytes)
    if (not isinstance(manifest,dict) or set(manifest)!={'schema','repository','branch','commit','bundleSha256','created'}
        or manifest['schema']!=1 or manifest['repository']!=REPOSITORY or manifest['branch']!='main'
        or not isinstance(manifest['commit'],str) or not SHA.fullmatch(manifest['commit'])):
        raise ValueError('Invalid update manifest')
    if digest(destination/'update.bundle')!=manifest['bundleSha256']:raise ValueError('Bundle checksum mismatch')
    return manifest


def apply_checkout(repo,bundle,commit,git,validate,rollback_services=lambda:None):
    """Fast-forward only. Never discard local work, including ignored collisions."""
    repo=Path(repo)
    if git('symbolic-ref','--short','HEAD').strip()!=b'main':raise ValueError('Tiny checkout must be on main')
    if git('status','--porcelain').strip():raise ValueError('Local changes or untracked files; refusing update')
    if git('remote','get-url','origin').decode().strip()!=ORIGIN:raise ValueError('Unexpected Tiny Git origin')
    old=git('rev-parse','HEAD').decode().strip()
    git('bundle','verify',str(bundle))
    git('fetch','--no-tags',str(bundle),'refs/heads/main')
    actual=git('rev-parse','FETCH_HEAD').decode().strip()
    if actual!=commit:raise ValueError('Bundle tip does not match signed commit')
    git('merge-base','--is-ancestor',old,commit)
    changed=git('diff','--name-only',old,commit).decode().splitlines()
    old_files=set(git('ls-tree','-r','--name-only',old).decode().splitlines())
    for name in changed:
        path=repo/name
        if name not in old_files and (path.exists() or path.is_symlink()):
            raise ValueError('Update would overwrite a local ignored file: '+name)
        for parent in path.parents:
            if parent==repo:break
            relative=str(parent.relative_to(repo))
            if relative not in old_files and (parent.is_symlink() or (parent.exists() and not parent.is_dir())):
                raise ValueError('Update would replace a local path: '+relative)
    applied=False
    try:
        if old!=commit:git('merge','--ff-only',commit);applied=True
        validate(changed)
    except Exception:
        if applied:
            # --keep refuses to clobber edits made concurrently by the owner.
            git('reset','--keep',old)
            rollback_services()
        raise
    return {'ok':True,'commit':commit,'previous':old,'changed':old!=commit}


def receive(repo,user,allowed_signers):
    if os.geteuid()!=0:raise ValueError('Receiver must run through installed root wrapper')
    repo=Path(repo).resolve()
    owner=pwd.getpwnam(user)
    if owner.pw_uid==0 or repo.stat().st_uid!=owner.pw_uid:raise ValueError('Checkout must belong to the unprivileged dashboard user')
    prefix=['runuser','-u',user,'--']
    def git(*args):return run(prefix+['git','-C',repo,*args],timeout=180)
    def active(unit):return subprocess.run(['systemctl','is-active','--quiet',unit]).returncode==0
    if not active('car-dashboard.service'):raise ValueError('Dashboard is stopped or not installed; refusing unattended activation')
    with locked('/run/lock/car-dashboard-update.lock'),tempfile.TemporaryDirectory(prefix='car-courier-') as temp:
        temp=Path(temp);os.chmod(temp,0o755)
        archive=temp/'incoming.zip'
        with archive.open('wb') as f:
            size=0
            while True:
                chunk=sys.stdin.buffer.read(1024*1024)
                if not chunk:break
                size+=len(chunk)
                if size>MAX_PACKAGE:raise ValueError('Package too large')
                f.write(chunk)
        manifest=verify_package(archive,allowed_signers,temp)
        os.chmod(temp/'update.bundle',0o644)
        adopted=[]
        for unit,path in [('car-obd-monitor.service','recorder/obd_full_recorder.py'),('car-gps-recorder.service','recorder/gps_recorder.py')]:
            if active(unit):
                content=run(['systemctl','cat',unit]).decode()
                if str(repo/path) in content:adopted.append((unit,path))
        def restart_old():
            run(['systemctl','restart','car-dashboard.service'])
            for unit,_ in adopted:run(['systemctl','restart',unit])
        def validate(changed):
            # Execute repository code only as its ordinary owner.
            run(prefix+['/usr/bin/python3','-B','-m','unittest','discover','-s','tests','-v'],cwd=repo,timeout=300)
            run(['systemctl','restart','car-dashboard.service'])
            healthy=False
            for _ in range(15):
                try:
                    run(prefix+['/usr/bin/python3',repo/'deploy/check-health.py'],cwd=repo,timeout=10)
                    healthy=True;break
                except RuntimeError:time.sleep(1)
            if not healthy:raise RuntimeError('Dashboard post-update health check failed')
            for unit,path in adopted:
                if any(name.startswith('recorder/') for name in changed) and path in changed:
                    run(['systemctl','restart',unit])
                    if not active(unit):raise RuntimeError(unit+' did not restart')
        result=apply_checkout(repo,temp/'update.bundle',manifest['commit'],git,validate,restart_old)
        result['installed']=int(time.time())
        receipt=Path('/etc/car-dashboard/courier-last.json')
        pending=receipt.with_suffix('.tmp')
        pending.write_text(json.dumps(result));os.chmod(pending,0o644);os.replace(pending,receipt)
        return result


def deliver(state,key,target):
    if not TARGET.fullmatch(target):raise ValueError('Expected SSH user@host')
    state=Path(state)
    with locked(state/'courier.lock'):
        package=state/'ready.zip'
        if not package.exists():raise ValueError('Download an update first: prepare')
        with zipfile.ZipFile(package) as archive:manifest=json.loads(archive.read('manifest.json'))
        receipt=state/'delivered.json'
        if receipt.exists():
            previous=json.loads(receipt.read_text())
            if previous.get('commit')==manifest['commit'] and previous.get('target')==target:
                return dict(previous,skipped=True)
        with package.open('rb') as src:
            result=subprocess.run(['ssh','-T','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes',
                '-o','ConnectTimeout=5','-o','ServerAliveInterval=15','-o','ServerAliveCountMax=3',
                '-i',str(Path(key).expanduser()),target],stdin=src,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=600)
        if result.returncode:raise RuntimeError('Tiny did not acknowledge update: '+result.stderr.decode(errors='replace')[-2000:])
        response=json.loads(result.stdout.decode().splitlines()[-1])
        if response.get('ok') is not True or response.get('commit')!=manifest['commit']:
            raise ValueError('Tiny receipt does not match prepared update')
        response.update(target=target,delivered=int(time.time()))
        save_json(receipt,response)
        return response


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['prepare','deliver','watch','status','receive'])
    parser.add_argument('--state',default=str(Path.home()/'.local/share/drive-console/courier'))
    parser.add_argument('--key',default=str(Path.home()/'.ssh/drive-console-courier'))
    parser.add_argument('--target',default='cayden@172.16.42.2')
    parser.add_argument('--repo');parser.add_argument('--user');parser.add_argument('--allowed-signers')
    args=parser.parse_args()
    try:
        if args.action=='prepare':result=prepare(args.state,args.key)
        elif args.action=='deliver':result=deliver(args.state,args.key,args.target)
        elif args.action=='receive':
            if not all([args.repo,args.user,args.allowed_signers]):raise ValueError('Missing receiver settings')
            result=receive(args.repo,args.user,args.allowed_signers)
        elif args.action=='status':
            result={name:json.loads((Path(args.state)/name).read_text()) if (Path(args.state)/name).exists() else None for name in ['prepared.json','delivered.json','duet-runtime.json']}
        else:
            Path(args.state).mkdir(parents=True,exist_ok=True)
            worker_lock=(Path(args.state)/'worker.lock').open('a')
            fcntl.flock(worker_lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            next_refresh=0
            while True:
                # Deliver a cached package before attempting Internet access.
                # A disconnected Wi-Fi fetch must not delay the car reconnection.
                try:print(json.dumps({'delivery':deliver(args.state,args.key,args.target)}),flush=True)
                except Exception as exc:print('Tiny unavailable; queued update retained: '+str(exc),file=sys.stderr,flush=True)
                if time.monotonic()>=next_refresh:
                    try:print(json.dumps({'prepared':prepare(args.state,args.key)}),flush=True);next_refresh=time.monotonic()+3600
                    except Exception as exc:print('Download unavailable; retaining last package: '+str(exc),file=sys.stderr,flush=True);next_refresh=time.monotonic()+120
                time.sleep(30)
        print(json.dumps(result),flush=True)
    except (Exception,KeyboardInterrupt) as exc:
        print('Courier: '+str(exc),file=sys.stderr)
        return 1
    return 0


if __name__=='__main__':sys.exit(main())
