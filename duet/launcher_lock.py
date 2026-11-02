#!/usr/bin/env python3
"""Single kiosk supervisor with automatic validated-release restart and rollback."""
from __future__ import annotations
import fcntl
import hashlib
import json
import os
from pathlib import Path
import signal
import shutil
import subprocess
import sys
import tempfile
import time
from urllib.request import build_opener,ProxyHandler

HELPERS=('proxy.py','launcher_lock.py','launch.sh')


def save(path,value):
    with tempfile.NamedTemporaryFile(mode='w',dir=path.parent,delete=False) as file:
        json.dump(value,file);file.flush();os.fsync(file.fileno());pending=Path(file.name)
    pending.chmod(0o600);os.replace(pending,path)


def terminate(child):
    if child.poll() is not None:return
    try:os.killpg(child.pid,signal.SIGTERM)
    except ProcessLookupError:return
    try:child.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:os.killpg(child.pid,signal.SIGKILL)
        except ProcessLookupError:pass
        child.wait()


def healthy():
    try:
        with build_opener(ProxyHandler({})).open('http://127.0.0.1:8877/__start',timeout=1) as response:return response.status==200
    except OSError:return False


def rollback(base,request):
    backup=Path(request['backup']).resolve()
    if not backup.is_relative_to((base/'duet-backups').resolve()):raise ValueError('Backup is outside managed display versions')
    for name in HELPERS:
        source=backup/name
        if not source.is_file() or source.is_symlink():raise ValueError('Incomplete rollback version')
        if name.endswith('.py'):compile(source.read_bytes(),name,'exec')
        else:subprocess.run(['bash','-n',str(source)],check=True,timeout=5)
    for name in HELPERS:
        with tempfile.NamedTemporaryFile(dir=base,delete=False) as file:
            file.write((backup/name).read_bytes());pending=Path(file.name)
        pending.chmod(0o755 if name.endswith('.sh') else 0o644);os.replace(pending,base/name)


def main():
    if len(sys.argv)<2:return 2
    state=Path.home()/'.local/state/drive-console';state.mkdir(parents=True,exist_ok=True)
    base=Path(sys.argv[1]).resolve().parent;marker=base/'duet-activation.json'
    with (state/'launcher.lock').open('a+') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:return 0
        save(state/'supervisor.json',{'pid':os.getpid(),'version':2,'source':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()})
        child=None
        def stop(signum,*_):
            if child is not None:terminate(child)
            raise SystemExit(128+signum)
        for value in (signal.SIGTERM,signal.SIGINT,signal.SIGHUP):signal.signal(value,stop)
        seen=None;request=None;started=0
        try:
            while True:
                desired=json.loads(marker.read_text()) if marker.exists() else {}
                changed=desired.get('phase')=='pending' and desired.get('id')!=seen
                if child is None or changed:
                    if child is not None:terminate(child)
                    request=desired if desired.get('phase')=='pending' else None
                    seen=desired.get('id');started=time.monotonic()
                    child=subprocess.Popen(['/bin/bash',*sys.argv[1:]],env=dict(os.environ,DRIVE_CONSOLE_LAUNCHER_CHILD='1'),start_new_session=True,close_fds=True)
                if request:
                    if child.poll() is not None or time.monotonic()-started>30 and not healthy():
                        terminate(child);rollback(base,request)
                        save(marker,dict(request,phase='failed',reason='display-health-check',time=time.time()))
                        child=subprocess.Popen(['/bin/bash',*sys.argv[1:]],env=dict(os.environ,DRIVE_CONSOLE_LAUNCHER_CHILD='1'),start_new_session=True,close_fds=True)
                        request=None
                    elif time.monotonic()-started>30 and healthy():
                        save(marker,dict(request,phase='applied',time=time.time()));request=None
                if child.poll() is not None:return child.returncode
                time.sleep(1)
        finally:
            if child is not None:terminate(child)


if __name__=='__main__':sys.exit(main())
