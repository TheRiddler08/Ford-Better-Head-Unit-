#!/usr/bin/env python3
"""Tiny Home Assistant bootstrap. Root-owned installed copy reconciles Git assets."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from urllib.request import build_opener, ProxyHandler
from zoneinfo import ZoneInfo

IMAGE='ghcr.io/home-assistant/home-assistant:2026.10.4'
NAME='car-home-assistant'
BASE=Path('/var/lib/car-home-assistant')
SETTINGS=Path('/etc/car-dashboard/home-assistant-settings.json')
STATUS=Path('/etc/car-dashboard/home-assistant.json')
ASSETS=['__init__.py','config_flow.py','entity.py','sensor.py','binary_sensor.py','device_tracker.py','manifest.json','strings.json']
LABEL='org.drive-console.managed'


def run(args,timeout=180):
    args=[str(a) for a in args]
    environment=None
    if args[0]=='docker':
        args.insert(1,'--host=unix:///var/run/docker.sock')
        environment=dict(os.environ)
        for key in ('DOCKER_HOST','DOCKER_CONTEXT','DOCKER_TLS','DOCKER_TLS_VERIFY','DOCKER_CERT_PATH'):environment.pop(key,None)
    result=subprocess.run(args,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=timeout,env=environment)
    if result.returncode:
        if 'check_config' in args:raise RuntimeError('Home Assistant configuration check failed; inspect the private local HA log')
        raise RuntimeError(' '.join(str(a) for a in args[:3])+': '+result.stderr.decode(errors='replace')[-2500:])
    return result.stdout


def save(path,data,mode=0o644):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    if path.is_symlink():raise ValueError('Refusing a symlink: '+str(path))
    with tempfile.NamedTemporaryFile(dir=path.parent,delete=False) as f:
        f.write(data);f.flush();os.fsync(f.fileno());temp=Path(f.name)
    os.chmod(temp,mode);os.replace(temp,path)


def read_json(path,default):
    return json.loads(Path(path).read_text()) if Path(path).exists() else default


def dashboard():
    gauges=[('Speed','sensor.car_speed',0,220),('Engine RPM','sensor.car_rpm',0,6500),
            ('Coolant','sensor.car_coolant',-20,130),('ECU voltage','sensor.car_ecu_voltage',8,16)]
    cards=[{'type':'markdown','content':'# Drive Console\nLocal read-only OBD and GPS. Unavailable values are not current measurements.'}]
    cards += [{'type':'gauge','name':name,'entity':entity,'min':low,'max':high} for name,entity,low,high in gauges]
    cards += [{'type':'entities','title':'Vehicle status','entities':['binary_sensor.car_obd_connection','binary_sensor.car_gps_fix',
        'sensor.car_raw_fuel_level','sensor.car_engine_load','sensor.car_throttle','sensor.car_intake_temperature','device_tracker.car_location']},
        {'type':'markdown','content':'## Make it yours\nUse **Overview → Edit dashboard → Take control** for the native visual editor. This Car starter is a read-only YAML example.\n\n[Original dashboard and full archive on the Duet](http://127.0.0.1:8877/cockpit.html) · [Archive](http://127.0.0.1:8877/records)'}]
    return {'title':'Car starter','views':[{'title':'Drive','path':'drive','icon':'mdi:car','cards':cards}]}


def configuration(timezone):
    ZoneInfo(timezone)
    return {'default_config':{},'homeassistant':{'name':'Car','time_zone':timezone,'unit_system':'metric'},
        'http':{'server_host':'127.0.0.1','server_port':8123,'ip_ban_enabled':True,'login_attempts_threshold':5},
        'car_console':{},'lovelace':{'mode':'storage','dashboards':{'car-starter':{'mode':'yaml','title':'Car starter',
            'icon':'mdi:car','show_in_sidebar':True,'filename':'car-dashboard.yaml'}}},
        'recorder':{'purge_keep_days':10,'commit_interval':1}}


def managed_files(repo,timezone):
    repo=Path(repo).resolve();result={}
    for name in ASSETS:
        source=repo/'home_assistant/custom_components/car_console'/name
        if not source.resolve().is_relative_to(repo):raise ValueError('Integration asset escapes checkout')
        result['custom_components/car_console/'+name]=source.read_bytes()
    result['configuration.yaml']=(json.dumps(configuration(timezone),indent=2)+'\n').encode()
    result['car-dashboard.yaml']=(json.dumps(dashboard(),indent=2)+'\n').encode()
    return result


def sync_assets(repo,config_dir,timezone,registry):
    config_dir=Path(config_dir);old=read_json(registry,{})
    hashes=dict(old);plan=[];preserved=[]
    for relative,content in managed_files(repo,timezone).items():
        target=config_dir/relative
        if not target.resolve().is_relative_to(config_dir.resolve()) or target.is_symlink():
            raise ValueError('Unsafe HA config path: '+relative)
        digest=hashlib.sha256(content).hexdigest()
        previous=target.read_bytes() if target.exists() else None
        if previous is not None:
            current=hashlib.sha256(previous).hexdigest()
            if current==digest:hashes[relative]=digest;continue
            if old.get(relative)!=current:
                preserved.append(relative);continue
        plan.append((relative,target,content,previous));hashes[relative]=digest
    applied=[]
    try:
        for relative,target,content,previous in plan:
            save(target,content,0o600);applied.append((target,previous))
        save(registry,json.dumps(hashes).encode(),0o600)
    except Exception:
        for target,previous in reversed(applied):
            if previous is None:target.unlink(missing_ok=True)
            else:save(target,previous,0o600)
        raise
    return [item[0] for item in plan],preserved


def grant_display_tunnel(path):
    """Extend only this repo's exact restricted display-key lines, never shell keys."""
    path=Path(path)
    if not path.exists():return False
    if path.is_symlink() or path.parent.is_symlink():raise ValueError('Refusing symlinked SSH configuration')
    content=path.read_text();lines=[]
    for line in content.splitlines(keepends=True):
        if (line.rstrip().endswith(' drive-console-duet') and
            'command="/bin/false",restrict,port-forwarding,permitopen="127.0.0.1:8787"' in line and
            'permitopen="127.0.0.1:8123"' not in line):
            line=line.replace('permitopen="127.0.0.1:8787"','permitopen="127.0.0.1:8787",permitopen="127.0.0.1:8123"',1)
        lines.append(line)
    updated=''.join(lines)
    if updated==content:return False
    stat=path.stat();save(path,updated.encode(),0o600);os.chown(path,stat.st_uid,stat.st_gid)
    return True


def image_id(image):
    # Pin the pulled official image digest rather than following a moving tag.
    info=json.loads(run(['docker','image','inspect',image]))[0]
    matches=[ref for ref in info.get('RepoDigests',[]) if ref.startswith('ghcr.io/home-assistant/home-assistant@sha256:')]
    if not matches:raise ValueError('Official image has no verified repository digest')
    return matches[0]


def check_http(timeout=120,bridge_since=None):
    client=build_opener(ProxyHandler({}));end=time.monotonic()+timeout
    while True:
        try:
            with client.open('http://127.0.0.1:8123/',timeout=3) as response:
                if response.status==200:
                    if bridge_since is None:return True
                    try:
                        health=BASE/'config/car-console-health.json'
                        if not health.resolve().is_relative_to((BASE/'config').resolve()) or health.stat().st_size>4096:raise ValueError('Invalid bridge health file')
                        status=json.loads(health.read_text())
                        if status.get('schema')==1 and status.get('ready') and status.get('entities',0)>=11 and status.get('checkedAt',0)>=bridge_since:return True
                    except (OSError,ValueError,TypeError):pass
        except OSError:pass
        if time.monotonic()>=end:return False
        time.sleep(2)


def wait_backend(url):
    client=build_opener(ProxyHandler({}));end=time.monotonic()+30
    while True:
        try:
            with client.open(url,timeout=5) as response:
                data=json.load(response)
                if isinstance(data,dict) and data.get('schema')==1:return
        except (OSError,ValueError):pass
        if time.monotonic()>=end:raise ValueError('Dashboard car bridge did not become ready; update its code first')
        time.sleep(1)


def service_active():
    return subprocess.run(['systemctl','is-active','--quiet','car-home-assistant.service']).returncode==0


def record_status(settings,ready,preserved=None):
    save(STATUS,json.dumps({'installed':True,'enabled':settings.get('enabled',True),'ready':ready,
        'image':settings['image'],'ownerSetup':'Create your owner account in the Home Assistant first-run screen',
        'preservedFiles':preserved or []}).encode())


def validate_config(settings):
    run(['docker','run','--rm','--pull=never','--network','none','--volume',str(BASE/'config')+':/config',
         '--env','CAR_CONSOLE_URL='+settings['backend'],'--entrypoint','python3',settings['image'],
         '-m','homeassistant','--script','check_config','--config','/config'],timeout=300)


def reconcile():
    if not SETTINGS.exists():return
    settings=read_json(SETTINGS,{})
    if not settings.get('enabled',True):record_status(settings,False);return
    # Back up only managed assets. Never read or rewrite HA authentication storage.
    config=BASE/'config';registry=BASE/'managed.json'
    old={}
    for name in managed_files(settings['repo'],settings['timezone']):
        target=config/name
        if target.is_symlink() or not target.resolve().is_relative_to(config.resolve()):raise ValueError('Unsafe HA config path: '+name)
        if target.exists():old[name]=target.read_bytes()
    previous=registry.read_bytes() if registry.exists() else None
    changed,preserved=sync_assets(settings['repo'],config,settings['timezone'],registry)
    active=service_active()
    restarted=False
    bridge_since=time.time()-15
    try:
        if changed:
            validate_config(settings)
            if active:
                restarted=True
                run(['systemctl','stop','car-home-assistant.service'])
                (BASE/'config/car-console-health.json').unlink(missing_ok=True)
                bridge_since=time.time()
                run(['systemctl','start','car-home-assistant.service'])
        ready=check_http(120 if active else 0,bridge_since=bridge_since if active else None)
        if active and not ready:raise RuntimeError('Home Assistant did not become ready')
        record_status(settings,ready,preserved)
    except Exception:
        for relative in changed:
            if relative in old:save(config/relative,old[relative],0o600)
            else:(config/relative).unlink(missing_ok=True)
        if previous is not None:save(registry,previous,0o600)
        if active and restarted:run(['systemctl','restart','car-home-assistant.service'])
        raise


def create_container(settings):
    run(['docker','create','--pull=never','--name',NAME,'--network','host','--label',LABEL+'=true',
         '--security-opt','no-new-privileges=true','--volume',str(BASE/'config')+':/config',
         '--env','TZ='+settings['timezone'],'--env','CAR_CONSOLE_URL='+settings['backend'],settings['image']],timeout=600)


def trim_failed_backups(candidate):
    failures=[]
    for folder in (BASE/'backups').iterdir():
        if not folder.is_dir() or folder.is_symlink() or not folder.name.isdigit():continue
        marker=folder/'result.json'
        if not marker.exists():continue
        data=json.loads(marker.read_text())
        if data.get('candidate')==candidate and data.get('result')=='rolled_back':failures.append(folder)
    for folder in sorted(failures,key=lambda p:int(p.name))[:-2]:shutil.rmtree(folder)


def upgrade_image(settings,new_image):
    previous=dict(settings);backup=BASE/'backups'/str(time.time_ns())
    backup.mkdir(parents=True,mode=0o700);os.chmod(backup,0o700)
    active=service_active();renamed=False;created=False;snapshot_complete=False
    if active:run(['systemctl','stop','car-home-assistant.service'])
    try:
        # Stop first so SQLite/WAL and HA's private storage have a consistent backup.
        shutil.copytree(BASE/'config',backup/'config',symlinks=True)
        snapshot_complete=True
        save(backup/'settings.json',json.dumps(previous).encode(),0o600)
        registry=BASE/'managed.json'
        if registry.exists():save(backup/'managed.json',registry.read_bytes(),0o600)
        sync_assets(settings['repo'],BASE/'config',settings['timezone'],registry)
        candidate=dict(settings,image=new_image)
        validate_config(candidate)
        run(['docker','rename',NAME,NAME+'-rollback']);renamed=True
        create_container(candidate);created=True
        if active:
            (BASE/'config/car-console-health.json').unlink(missing_ok=True)
            started=time.time()
            run(['systemctl','start','car-home-assistant.service'])
            if not check_http(180,bridge_since=started):raise RuntimeError('New HA image failed readiness')
        settings['image']=new_image
        save(SETTINGS,json.dumps(settings).encode(),0o600)
        run(['docker','rm',NAME+'-rollback'])
    except Exception:
        if created:
            if active:run(['systemctl','stop','car-home-assistant.service'])
            run(['docker','rm',NAME])
        if renamed:run(['docker','rename',NAME+'-rollback',NAME])
        if snapshot_complete:
            failed=backup/'failed-config'
            (BASE/'config').rename(failed)
            shutil.copytree(backup/'config',BASE/'config',symlinks=True)
            if (backup/'managed.json').exists():save(BASE/'managed.json',(backup/'managed.json').read_bytes(),0o600)
        settings.clear();settings.update(previous)
        save(SETTINGS,json.dumps(previous).encode(),0o600)
        if active:run(['systemctl','start','car-home-assistant.service'])
        recovered=not active or check_http(120)
        save(backup/'result.json',json.dumps({'candidate':new_image,'result':'rolled_back' if recovered else 'rollback-needs-attention'}).encode(),0o600)
        if recovered:trim_failed_backups(new_image)
        raise


def install(repo,user,timezone,offline_image=None):
    if os.uname().machine!='x86_64':raise ValueError('This installer targets the x86-64 Tiny, not the ARM Duet')
    owner=pwd.getpwnam(user);repo=Path(repo).resolve()
    if owner.pw_uid==0 or repo.stat().st_uid!=owner.pw_uid:raise ValueError('Use the ordinary Tiny dashboard account')
    unit=run(['systemctl','cat','car-dashboard.service']).decode()
    if str(repo/'app/server.py') not in unit:raise ValueError('Install the dashboard on the Tiny first; service does not match checkout')
    if not re.fullmatch(r'/[A-Za-z0-9_./-]+',str(repo)):raise ValueError('Unsupported checkout path')
    ZoneInfo(timezone)
    if shutil.disk_usage('/var/lib').free<6*1024**3:raise ValueError('At least 6 GB free space is required for the first image download')
    if offline_image and not re.fullmatch('sha256:[0-9a-f]{64}',offline_image):raise ValueError('Invalid offline image identity')
    if not shutil.which('docker'):
        if offline_image:raise ValueError('Install the carried Ubuntu Docker packages first')
        run(['env','DEBIAN_FRONTEND=noninteractive','apt-get','update'],timeout=300)
        run(['env','DEBIAN_FRONTEND=noninteractive','apt-get','install','-y','docker.io'],timeout=600)
    run(['systemctl','enable','--now','docker.service'])
    version=run(['docker','version','--format','{{.Server.Version}}']).decode().strip()
    if int(version.split('.')[0])<23:raise ValueError('Docker Engine 23+ required; upgrade Docker before retrying')
    old=read_json(SETTINGS,{})
    settings=dict(old) if old else {'repo':str(repo),'user':user,'timezone':timezone,'backend':'http://127.0.0.1:8787/api/home-assistant/state','enabled':True}
    if old and (old.get('repo')!=str(repo) or old.get('user')!=user):raise ValueError('Existing HA installation belongs to another checkout/account')
    # Read only the non-secret port selector from the existing dashboard env file.
    for line in Path('/etc/car-dashboard/dashboard.env').read_text().splitlines():
        if line.startswith('CAR_PORT='):
            port=int(line.split('=',1)[1].strip().strip('"\''))
            if not 1<=port<=65535:raise ValueError('Invalid dashboard port')
            settings['backend']=f'http://127.0.0.1:{port}/api/home-assistant/state'
    if old and settings['backend']!=old.get('backend'):raise ValueError('Dashboard port changed; keep its previous port or explicitly migrate the managed HA container')
    run(['systemctl','restart','car-dashboard.service'])
    wait_backend(settings['backend'])
    settings['enabled']=True
    try:existing=run(['docker','container','inspect',NAME])
    except RuntimeError as exc:
        if 'No such' not in str(exc):raise
        existing=None
    if existing is not None:
        info=json.loads(existing)[0]
        if info.get('Config',{}).get('Labels',{}).get(LABEL)!='true':raise ValueError('Container name belongs to an unmanaged installation')
        if not old:raise ValueError('Managed container exists but settings are missing; recover them first')
    elif old:raise ValueError('HA settings exist but container is missing; recover the installation instead of replacing it blindly')
    else:
        with socket.socket() as sock:
            try:sock.bind(('127.0.0.1',8123))
            except OSError as exc:raise ValueError('Port 8123 is already occupied; preserving existing installation') from exc
    if not old:
        if offline_image:
            info=json.loads(run(['docker','image','inspect',offline_image]))[0]
            if info['Id']!=offline_image or info['Architecture']!='amd64':raise ValueError('Offline Tiny image mismatch')
            settings['image']=offline_image
        else:
            run(['docker','pull',IMAGE],timeout=900)
            settings['image']=image_id(IMAGE)
        config=BASE/'config';config.mkdir(parents=True,exist_ok=True)
        if any(config.iterdir()) and not (BASE/'managed.json').exists():raise ValueError('Existing unmanaged HA configuration; refusing replacement')
        sync_assets(repo,config,timezone,BASE/'managed.json')
        validate_config(settings)
        create_container(settings)
    elif offline_image and info['Image']!=offline_image:
        upgrade_image(settings,offline_image)
    save(SETTINGS,json.dumps(settings).encode(),0o600)
    save('/usr/local/lib/car-dashboard/home_assistant.py',Path(__file__).read_bytes(),0o644)
    wrapper=b'#!/bin/sh\nset -eu\n[ "$#" -eq 0 ] || exit 2\nexec /usr/bin/python3 -I /usr/local/lib/car-dashboard/home_assistant.py reconcile\n'
    save('/usr/local/sbin/car-ha-reconcile',wrapper,0o755)
    save('/etc/systemd/system/car-home-assistant.service',b'''[Unit]
Description=Local car Home Assistant Container
Requires=docker.service
After=docker.service car-dashboard.service
[Service]
ExecStart=/usr/bin/docker --host=unix:///var/run/docker.sock start --attach car-home-assistant
ExecStop=/usr/bin/docker --host=unix:///var/run/docker.sock stop --time 30 car-home-assistant
Restart=always
RestartSec=10
TimeoutStartSec=120
TimeoutStopSec=60
[Install]
WantedBy=multi-user.target
''')
    save('/etc/systemd/system/car-home-assistant-reconcile.service',b'''[Unit]
Description=Refresh managed Home Assistant car assets after Git updates
After=car-home-assistant.service
[Service]
Type=oneshot
ExecStart=/usr/local/sbin/car-ha-reconcile
TimeoutStartSec=450
''')
    save('/etc/systemd/system/car-home-assistant-reconcile.timer',b'''[Unit]
Description=Reconcile car Home Assistant assets from updated Git checkout
[Timer]
OnBootSec=90
OnUnitActiveSec=120
[Install]
WantedBy=timers.target
''')
    save('/etc/systemd/system/car-dashboard-update.service.d/home-assistant.conf',b'[Service]\nTimeoutStartSec=600\n')
    grant_display_tunnel(Path(owner.pw_dir)/'.ssh/authorized_keys')
    run(['systemctl','daemon-reload'])
    run(['systemctl','enable','--now','car-home-assistant.service','car-home-assistant-reconcile.timer'])
    reconcile()
    print('Home Assistant installed. Create the owner account once at http://127.0.0.1:8123/ on Tiny or port 8878 through the Duet tunnel.')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['install','reconcile','disable'])
    parser.add_argument('--repo');parser.add_argument('--user');parser.add_argument('--timezone',default='UTC');parser.add_argument('--offline-image');parser.add_argument('--update-lock-fd',type=int)
    args=parser.parse_args()
    if os.geteuid()!=0:raise SystemExit('Use deploy/install-home-assistant.sh as your ordinary Tiny user')
    BASE.mkdir(parents=True,exist_ok=True)
    # Serialize with Git/courier updates so assets cannot be read mid-checkout.
    import fcntl
    with Path('/run/lock/car-home-assistant.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        if args.update_lock_fd is not None:
            update_lock=os.fdopen(os.dup(args.update_lock_fd),'a')
            actual=os.fstat(update_lock.fileno());expected=Path('/run/lock/car-dashboard-update.lock').stat()
            if (actual.st_dev,actual.st_ino)!=(expected.st_dev,expected.st_ino):raise ValueError('Invalid inherited update lock')
        else:update_lock=Path('/run/lock/car-dashboard-update.lock').open('a')
        try:fcntl.flock(update_lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            if args.action=='reconcile':return
            raise ValueError('A Git update is in progress; retry setup when it finishes')
        if args.action=='install':
            if not args.repo or not args.user:raise ValueError('Missing checkout/account')
            install(args.repo,args.user,args.timezone,args.offline_image)
        elif args.action=='disable':
            if not SETTINGS.exists():raise ValueError('Home Assistant is not installed')
            settings=read_json(SETTINGS,{})
            settings['enabled']=False;save(SETTINGS,json.dumps(settings).encode(),0o600)
            run(['systemctl','disable','--now','car-home-assistant.service'])
            record_status(settings,False)
        else:reconcile()


if __name__=='__main__':
    try:main()
    except Exception as exc:print('Home Assistant setup: '+str(exc),file=sys.stderr);sys.exit(1)
