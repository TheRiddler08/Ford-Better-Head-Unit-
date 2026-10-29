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
import threading
import importlib
import tempfile
import time
import zipfile

REPOSITORY='TheRiddler08/Ford-Better-Head-Unit-'
ORIGIN='https://github.com/'+REPOSITORY+'.git'
NAMESPACE='car-dashboard-courier'
MAX_PACKAGE=8*1024**3
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
def locked(path,blocking=False):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('a') as f:
        fcntl.flock(f,fcntl.LOCK_EX|(0 if blocking else fcntl.LOCK_NB))
        yield f


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


def support(name):
    if name not in ('runtime_assets','offline_payload'):raise ValueError('Unknown courier component')
    directory=Path(__file__).resolve().parent
    if os.geteuid()==0:
        for path in (directory,directory/(name+'.py'),directory/'runtime_assets.py'):
            stat=path.stat()
            if stat.st_uid!=0 or stat.st_mode & 0o022:raise ValueError('Install the root-owned courier bootstrap first')
    if __package__:return importlib.import_module(__package__+'.'+name)
    if str(directory) not in sys.path:sys.path.insert(0,str(directory))
    return importlib.import_module(name)


def delivery_id(manifest):return manifest.get('payloadId',manifest['commit'])


COMPONENTS=('offline_updates.py','runtime_assets.py','offline_payload.py','home_assistant.py')


def publish_version(git,commit,base):
    """Publish a complete immutable bootstrap version before switching its link."""
    base=Path(base);versions=base/'courier-versions';versions.mkdir(parents=True,exist_ok=True)
    destination=versions/commit
    sources={name:git('show',commit+':deploy/'+name) for name in COMPONENTS}
    for name,content in sources.items():ast.parse(content,filename=name)
    if destination.exists():
        for name,content in sources.items():
            if (destination/name).read_bytes()!=content:raise ValueError('Published courier version was modified; refusing overwrite')
    else:
        with tempfile.TemporaryDirectory(dir=versions) as temp:
            temp=Path(temp)
            for name,content in sources.items():
                (temp/name).write_bytes(content);os.chmod(temp/name,0o644)
            os.chmod(temp,0o755);os.replace(temp,destination)
    current=base/'courier-current'
    changed=not current.exists() or current.resolve()!=destination.resolve()
    if current.exists() and not current.is_symlink():raise ValueError('Courier selector is not a managed symlink')
    link=base/('courier-next-'+str(os.getpid()))
    if link.exists() or link.is_symlink():link.unlink()
    link.symlink_to(Path('courier-versions')/commit);os.replace(link,current)
    return changed


def prepare(state,key,include_runtime=True):
    """Download on the Duet; no Docker or amd64 executable runs there."""
    state=Path(state);state.mkdir(parents=True,exist_ok=True)
    with locked(state/'prepare.lock'):
        key=ensure_key(key)
        repo=state/'cache.git'
        if not repo.exists():run(['git','init','--bare',repo])
        run(['git','--git-dir',repo,'fetch','--no-tags',ORIGIN,'refs/heads/main'],timeout=180)
        commit=run(['git','--git-dir',repo,'rev-parse','FETCH_HEAD']).decode().strip()
        run(['git','--git-dir',repo,'update-ref','refs/heads/main',commit])
        files={};runtime=None
        if include_runtime:
            spec=json.loads(run(['git','--git-dir',repo,'show',commit+':deploy/offline-runtime.json']))
            files,runtime=support('runtime_assets').build(state/'runtime-cache',spec)
        identity=hashlib.sha256(json.dumps({'commit':commit,'runtime':runtime},sort_keys=True).encode()).hexdigest()
        previous=json.loads((state/'prepared.json').read_text()) if (state/'prepared.json').exists() else {}
        if ((runtime and previous.get('payloadId')==identity) or (not runtime and previous.get('schema')==1 and previous.get('commit')==commit)) and (state/'ready.zip').exists():
            updated=publish_version(lambda *a:run(['git','--git-dir',repo,*a]),commit,state.parent)
            return dict(previous,unchanged=True,workerUpdated=updated)
        with tempfile.TemporaryDirectory(dir=state) as temp:
            temp=Path(temp);bundle=temp/'update.bundle'
            run(['git','--git-dir',repo,'bundle','create',bundle,'refs/heads/main'],timeout=180)
            manifest={'schema':2 if runtime else 1,'repository':REPOSITORY,'branch':'main','commit':commit,
                      'bundleSha256':digest(bundle),'created':int(time.time())}
            if runtime:manifest.update(payloadId=identity,runtime=runtime)
            (temp/'manifest.json').write_text(json.dumps(manifest,sort_keys=True))
            run(['ssh-keygen','-Y','sign','-f',key,'-n',NAMESPACE,temp/'manifest.json'])
            with zipfile.ZipFile(temp/'ready.zip','w',compression=zipfile.ZIP_STORED) as package:
                for name in ['manifest.json','manifest.json.sig','update.bundle']:package.write(temp/name,name)
                for name,path in files.items():package.write(path,name)
            if (temp/'ready.zip').stat().st_size>MAX_PACKAGE:raise ValueError('Package exceeds 8 GB')
            # Hold this lock only while publishing. An in-progress transfer keeps
            # reading its old archive while a new download proceeds independently.
            with locked(state/'courier.lock',blocking=True):
                os.replace(temp/'ready.zip',state/'ready.zip');save_json(state/'prepared.json',manifest)
        refreshed=refresh_duet_runtime(repo,commit,state.parent)
        save_json(state/'duet-runtime.json',refreshed)
        updated=publish_version(lambda *a:run(['git','--git-dir',repo,*a]),commit,state.parent)
        return dict(manifest,duetRuntime=refreshed,workerUpdated=updated)


def verify_package(package,allowed_signers,destination):
    """Verify signed metadata before extracting large, exactly named artifacts."""
    destination=Path(destination)
    with zipfile.ZipFile(package) as archive:
        entries=archive.infolist();names=[entry.filename for entry in entries]
        if len(names)!=len(set(names)) or not {'manifest.json','manifest.json.sig','update.bundle'}<=set(names):raise ValueError('Unexpected package entries')
        for name in ('manifest.json','manifest.json.sig'):
            entry=archive.getinfo(name)
            if entry.compress_type!=zipfile.ZIP_STORED or not 0<entry.file_size<=16384:raise ValueError('Invalid signed metadata')
            (destination/name).write_bytes(archive.read(name))
        manifest_bytes=(destination/'manifest.json').read_bytes()
        run(['ssh-keygen','-Y','verify','-f',allowed_signers,'-I','duet','-n',NAMESPACE,
             '-s',destination/'manifest.json.sig'],data=manifest_bytes)
        manifest=json.loads(manifest_bytes)
        base={'schema','repository','branch','commit','bundleSha256','created'}
        if not isinstance(manifest,dict) or manifest.get('schema') not in (1,2):raise ValueError('Invalid update manifest')
        if set(manifest)!=(base if manifest['schema']==1 else base|{'payloadId','runtime'}):raise ValueError('Invalid update manifest fields')
        if manifest['repository']!=REPOSITORY or manifest['branch']!='main' or not isinstance(manifest['commit'],str) or not SHA.fullmatch(manifest['commit']):raise ValueError('Invalid repository update')
        hashes={'update.bundle':manifest['bundleSha256']}
        if manifest['schema']==2:
            if not re.fullmatch('[0-9a-f]{64}',manifest['payloadId']):raise ValueError('Invalid delivery identity')
            runtime=manifest['runtime'];image=runtime['image']
            if not re.fullmatch(r'ghcr.io/home-assistant/home-assistant:20[0-9]{2}\.[0-9]{1,2}\.[0-9]+',image['reference']):raise ValueError('Unexpected image publisher')
            if not re.fullmatch('sha256:[0-9a-f]{64}',image['imageId']):raise ValueError('Invalid image identity')
            identity=hashlib.sha256(json.dumps({'commit':manifest['commit'],'runtime':runtime},sort_keys=True).encode()).hexdigest()
            if manifest['payloadId']!=identity:raise ValueError('Delivery identity does not match signed runtime')
            hashes['ha-image.tar.gz']=image['sha256']
            for version,info in runtime['ubuntu'].items():
                if version not in ('22.04','24.04','26.04'):raise ValueError('Unexpected Ubuntu artifact')
                hashes['ubuntu-'+version+'.tar.gz']=info['sha256']
        if set(names)!=set(hashes)|{'manifest.json','manifest.json.sig'}:raise ValueError('Unexpected archive files')
        for name,expected in hashes.items():
            if not re.fullmatch('[0-9a-f]{64}',expected):raise ValueError('Invalid artifact hash')
            entry=archive.getinfo(name)
            if entry.compress_type!=zipfile.ZIP_STORED or not 0<entry.file_size<=MAX_PACKAGE:raise ValueError('Invalid artifact size/compression')
            with archive.open(entry) as source,(destination/name).open('wb') as target:shutil.copyfileobj(source,target,1024*1024)
            if digest(destination/name)!=expected:raise ValueError('Artifact checksum mismatch: '+name)
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


def progress(phase,commit=None):
    save_json(Path('/etc/car-dashboard/courier-progress.json'),{'phase':phase,'commit':commit,'time':int(time.time())})


def receive(repo,user,allowed_signers):
    if os.geteuid()!=0:raise ValueError('Receiver must run through installed root wrapper')
    repo=Path(repo).resolve()
    owner=pwd.getpwnam(user)
    if owner.pw_uid==0 or repo.stat().st_uid!=owner.pw_uid:raise ValueError('Checkout must belong to the unprivileged dashboard user')
    prefix=['runuser','-u',user,'--']
    def git(*args):return run(prefix+['git','-C',repo,*args],timeout=180)
    def active(unit):return subprocess.run(['systemctl','is-active','--quiet',unit]).returncode==0
    if not active('car-dashboard.service'):raise ValueError('Dashboard is stopped or not installed; refusing unattended activation')
    progress('receiving')
    with locked('/run/lock/car-dashboard-update.lock') as update_lock,tempfile.TemporaryDirectory(prefix='car-courier-') as temp:
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
        progress('verifying')
        manifest=verify_package(archive,allowed_signers,temp)
        archive.unlink()  # Reclaim the incoming copy before installing large artifacts.
        progress('installing',manifest['commit'])
        os.chmod(temp/'update.bundle',0o644)
        runtime_result={}
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
            if manifest.get('runtime'):
                runtime_result.update(support('offline_payload').install(temp,manifest['runtime'],repo,user,Path(__file__).resolve().with_name('home_assistant.py'),update_lock.fileno()))
            for unit,path in adopted:
                if any(name.startswith('recorder/') for name in changed) and path in changed:
                    run(['systemctl','restart',unit])
                    if not active(unit):raise RuntimeError(unit+' did not restart')
        result=apply_checkout(repo,temp/'update.bundle',manifest['commit'],git,validate,restart_old)
        if manifest.get('runtime'):
            publish_version(git,manifest['commit'],Path('/usr/local/lib/car-dashboard'))
            manager=Path('/usr/local/lib/car-dashboard/home_assistant.py')
            if manager.exists():
                source=git('show',manifest['commit']+':deploy/home_assistant.py')
                with tempfile.NamedTemporaryFile(dir=manager.parent,delete=False) as f:f.write(source);pending_manager=Path(f.name)
                os.chmod(pending_manager,0o644);os.replace(pending_manager,manager)
        result['installed']=int(time.time())
        result['payloadId']=delivery_id(manifest)
        if runtime_result:result['runtime']=runtime_result
        receipt=Path('/etc/car-dashboard/courier-last.json')
        pending=receipt.with_suffix('.tmp')
        pending.write_text(json.dumps(result));os.chmod(pending,0o644);os.replace(pending,receipt)
        progress('complete',manifest['commit'])
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
            if previous.get('payloadId',previous.get('commit'))==delivery_id(manifest) and previous.get('target')==target:
                return dict(previous,skipped=True)
        failure_path=state/'failed.json'
        failure=json.loads(failure_path.read_text()) if failure_path.exists() else {}
        same=failure.get('payloadId')==delivery_id(manifest) and failure.get('target')==target
        if same and 0<failure.get('nextAttempt',0)-time.time()<=3600:
            return {'ok':False,'deferred':True,'payloadId':delivery_id(manifest),'nextAttempt':failure['nextAttempt']}
        with package.open('rb') as src:
            result=subprocess.run(['ssh','-T','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes',
                '-o','ConnectTimeout=5','-o','ServerAliveInterval=15','-o','ServerAliveCountMax=3',
                '-i',str(Path(key).expanduser()),target],stdin=src,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=3600)
        if result.returncode:
            transient=result.returncode==255 or b'Resource temporarily unavailable' in result.stderr
            attempts=(failure.get('attempts',0)+1) if same else 1
            delay=30 if transient else min(3600,300*2**min(attempts-1,4))
            save_json(failure_path,{'payloadId':delivery_id(manifest),'target':target,'attempts':attempts,'nextAttempt':time.time()+delay})
            raise RuntimeError('Tiny did not acknowledge update: '+result.stderr.decode(errors='replace')[-2000:])
        response=json.loads(result.stdout.decode().splitlines()[-1])
        if response.get('ok') is not True or response.get('commit')!=manifest['commit'] or response.get('payloadId',response.get('commit'))!=delivery_id(manifest):
            raise ValueError('Tiny receipt does not match prepared update')
        response.update(target=target,delivered=int(time.time()))
        save_json(receipt,response)
        if failure_path.exists():failure_path.unlink()
        return response


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['prepare','deliver','watch','status','receive'])
    parser.add_argument('--state',default=str(Path.home()/'.local/share/drive-console/courier'))
    parser.add_argument('--key',default=str(Path.home()/'.ssh/drive-console-courier'))
    parser.add_argument('--target',default='cayden@172.16.42.2')
    parser.add_argument('--code-only',action='store_true',help='Explicit legacy mode; does not carry HA/runtime dependencies')
    parser.add_argument('--repo');parser.add_argument('--user');parser.add_argument('--allowed-signers')
    args=parser.parse_args()
    try:
        if args.action=='prepare':result=prepare(args.state,args.key,not args.code_only)
        elif args.action=='deliver':result=deliver(args.state,args.key,args.target)
        elif args.action=='receive':
            if not all([args.repo,args.user,args.allowed_signers]):raise ValueError('Missing receiver settings')
            result=receive(args.repo,args.user,args.allowed_signers)
        elif args.action=='status':
            result={name:json.loads((Path(args.state)/name).read_text()) if (Path(args.state)/name).exists() else None for name in ['prepared.json','delivered.json','duet-runtime.json','activity.json','failed.json']}
        else:
            Path(args.state).mkdir(parents=True,exist_ok=True)
            worker_lock=(Path(args.state)/'worker.lock').open('a')
            fcntl.flock(worker_lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            worker_lock.seek(0);worker_lock.truncate();worker_lock.write(str(os.getpid()));worker_lock.flush()
            stop=threading.Event();restart=threading.Event();transferring=threading.Lock()
            def downloads():
                while not stop.is_set():
                    delay=3600
                    try:
                        save_json(Path(args.state)/'activity.json',{'phase':'downloading','started':int(time.time())})
                        result=prepare(args.state,args.key,not args.code_only)
                        save_json(Path(args.state)/'activity.json',{'phase':'ready','checked':int(time.time()),'payloadId':delivery_id(result)})
                        print(json.dumps({'prepared':result}),flush=True)
                        if result.get('workerUpdated'):restart.set();return
                    except Exception as exc:
                        save_json(Path(args.state)/'activity.json',{'phase':'retrying-download','error':str(exc)[-500:],'checked':int(time.time())})
                        print('Download unavailable; previous package retained: '+str(exc),file=sys.stderr,flush=True);delay=120
                    stop.wait(delay)
            def deliveries():
                while not stop.is_set():
                    with transferring:
                        if stop.is_set():return
                        try:print(json.dumps({'delivery':deliver(args.state,args.key,args.target)}),flush=True)
                        except Exception as exc:print('Tiny unavailable; queued update retained: '+str(exc),file=sys.stderr,flush=True)
                    stop.wait(30)
            threading.Thread(target=downloads,daemon=True).start()
            threading.Thread(target=deliveries,daemon=True).start()
            while not stop.wait(1):
                if restart.is_set():
                    with transferring:
                        stop.set()
                        # No active SSH transfer is killed by self-upgrade.
                        os.execv(sys.executable,[sys.executable,str(Path(args.state).parent/'courier-current/offline_updates.py'),*sys.argv[1:]])
        print(json.dumps(result),flush=True)
        if args.action=='deliver' and result.get('ok') is False:return 1
    except (Exception,KeyboardInterrupt) as exc:
        if args.action=='receive' and os.geteuid()==0:
            try:progress('failed')
            except OSError:pass
        print('Courier: '+str(exc),file=sys.stderr)
        return 1
    return 0


if __name__=='__main__':sys.exit(main())
