#!/usr/bin/env python3
"""Tiny Home Assistant bootstrap. Root-owned installed copy reconciles Git assets."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import pwd
import re
import shutil
import socket
import subprocess
import sys
import tarfile
import tempfile
import time
from urllib.request import build_opener, ProxyHandler
from zoneinfo import ZoneInfo

IMAGE='ghcr.io/home-assistant/home-assistant:2026.10.4'
# Release tooling pins the packaged source and production frontend here. The
# root-owned manager never executes an unverified editable checkout as a build.
FORK_ARCHIVE_SHA='a626db2f0702b52d2627929832ea821e87c166c500ce2065584a85fe2fac6a8a'
NAME='car-home-assistant'
BASE=Path('/var/lib/car-home-assistant')
SETTINGS=Path('/etc/car-dashboard/home-assistant-settings.json')
STATUS=Path('/etc/car-dashboard/home-assistant.json')
ASSETS=['__init__.py','services.py','config_flow.py','entity.py','sensor.py','binary_sensor.py','device_tracker.py','manifest.json','strings.json']
LABEL='org.drive-console.managed'


def run(args,timeout=180):
    args=[str(a) for a in args]
    environment=None
    if args[0]=='docker':
        args.insert(1,'--host=unix:///var/run/docker.sock')
        environment=dict(os.environ)
        for key in ('DOCKER_HOST','DOCKER_CONTEXT','DOCKER_TLS','DOCKER_TLS_VERIFY','DOCKER_CERT_PATH','BUILDX_BUILDER'):environment.pop(key,None)
        if len(args)>2 and args[2]=='build':environment['BUILDX_BUILDER']='default'
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
        {'type':'markdown','content':'## Make it yours\nEdit **Car starter** using its pencil / edit dashboard control. Your changes are saved in Home Assistant and preserved during updates.\n\n[Original dashboard and full archive on the Duet](http://127.0.0.1:8877/cockpit.html) · [Archive](http://127.0.0.1:8877/records)'}]
    return {'title':'Car starter','views':[{'title':'Drive','path':'drive','icon':'mdi:car','cards':cards}]}


def configuration(timezone):
    ZoneInfo(timezone)
    return {'default_config':{},'homeassistant':{'name':'Car Assistant','time_zone':timezone,'unit_system':'metric'},
        'http':{'server_host':'127.0.0.1','server_port':8123,'ip_ban_enabled':True,'login_attempts_threshold':5},
        'car_console':{},'lovelace':{'mode':'storage'},
        'logger':{'default':'warning','logs':{'homeassistant.bootstrap':'info'}},
        'recorder':{'purge_keep_days':10,'commit_interval':1}}


def classic_dashboard():
    def widget(kind,**options):return dict(type='custom:drive-console-widget',widget=kind,**options)
    def service(feature):return {'type':'custom:drive-console-service','feature':feature}
    def page(title,path,cards,layout='grid'):
        return {'title':title,'path':path,'type':'custom:drive-console-layout','show_icon_and_title':True,
            'cards':[widget('page',page_layout=layout),*cards]}
    home=[widget(kind,placement=placement) for kind,placement in
          [('clock','left'),('media','left'),('map','map'),('vehicleArt','right'),('gauges','right'),('metrics','right')]]
    return {'title':'Drive Console','views':[
        page('Home','drive',home,'cockpit'),
        page('Navigation','navigation',[widget('map',min_height=520),widget('status')]),
        page('Media','media',[widget('media',min_height=420),widget('text',title='Local music',
            body='Choose audio files from this device. Playback stays in this browser; files are not uploaded.')]),
        page('Vehicle','vehicle',[widget('vehicleArt'),widget('gauges'),widget('metrics'),widget('status')]),
        page('Health','health',[service('overview'),widget('status'),{'type':'entities','title':'Car connections',
            'entities':['binary_sensor.car_obd_connection','binary_sensor.car_gps_fix','sensor.car_ecu_voltage']},
            widget('link',title='Recorded history',body='Inspect stored measurements, requests and GPS history in this dashboard.',
                button_label='Open reports',url='/drive-console/reports',new_tab=False)]),
        page('Updates','updates',[service('updates'),widget('status'),widget('link',title='Car updates',
            body='Inspect signed source transfers and their application receipts.',
            button_label='Open home controls',url='/drive-console/home-control',new_tab=False)]),
        page('Settings','settings',[service('preferences'),widget('text',title='Make each tab yours',body=
            'Use the pencil to edit this tab. Edit its Page settings card for layout, colors, navigation, header and bottom controls. Add, move, edit or remove any card. Each tab saves its own configuration.'),
            widget('link',title='Assistant settings',button_label='Open Assistant settings',url='/config',new_tab=False)]),
        page('Devices','devices',[service('network'),service('power')]),
        page('Storage','storage',[service('storage'),service('sync')]),
        page('Recordings','recordings',[service('cameras')]),
        page('Radio','radio',[service('radio'),service('messages')]),
        page('Reports','reports',[service('reports')]),
        page('Home control','home-control',[service('home')])]}


def classic_resource(repo):
    repo=Path(repo).resolve()
    def read(name):
        path=repo/name
        if not path.resolve().is_relative_to(repo):raise ValueError('Drive Console asset escapes checkout')
        if path.stat().st_size>2*1024**2:raise ValueError('Drive Console asset exceeds size limit')
        return path.read_text()
    html=read('app/web/cockpit.html')
    def fragment(pattern):
        match=re.search(pattern,html,re.S)
        if not match:raise ValueError('Original Drive Console artwork is missing')
        return match.group(0)
    widgets={kind:fragment(r'<section[^>]*data-widget="'+kind+r'".*?</section>')
             for kind in ('clock','media','map','vehicleArt','gauges','metrics')}
    css=read('app/web/cockpit.css').replace(':root{',':host{').replace('html[data-theme="day"]',':host([data-theme="day"])').replace('html{',':host{').replace('body{',':host{')
    original={'css':css,'widgets':widgets,'rail':fragment(r'<nav class="rail".*?</nav>'),
              'header':fragment(r'<header class="topbar".*?</header>'),
              'footer':fragment(r'<footer class="dock".*?</footer>')}
    source=read('home_assistant/drive_console.js')
    if source.count('const ORIGINAL = null;')!=1:raise ValueError('Unexpected Drive Console module template')
    module=source.replace('const ORIGINAL = null;','const ORIGINAL = '+json.dumps(original)+';').encode()
    digest=hashlib.sha256(module).hexdigest()
    return module,{'schema':1,'dashboardSchema':3,'url':'/local/drive-console.js?v='+digest[:20],'sha256':digest}


def managed_files(repo,timezone):
    repo=Path(repo).resolve();result={}
    for name in ASSETS:
        source=repo/'home_assistant/custom_components/car_console'/name
        if not source.resolve().is_relative_to(repo):raise ValueError('Integration asset escapes checkout')
        result['custom_components/car_console/'+name]=source.read_bytes()
    result['configuration.yaml']=(json.dumps(configuration(timezone),indent=2)+'\n').encode()
    result['car-dashboard.yaml']=(json.dumps(dashboard(),indent=2)+'\n').encode()
    module,resource=classic_resource(repo)
    result['www/drive-console.js']=module
    result['drive-console-resource.json']=(json.dumps(resource)+'\n').encode()
    result['drive-console-dashboard.yaml']=(json.dumps(classic_dashboard(),indent=2)+'\n').encode()
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


def car_assistant_image(repo,base_image):
    if not FORK_ARCHIVE_SHA:return base_image
    archive=Path(repo)/'car_assistant/runtime.tar.gz'
    if archive.is_symlink():raise ValueError('Refusing symlinked Car Assistant runtime')
    with archive.open('rb') as source:raw=source.read(256*1024**2+1)
    if len(raw)>256*1024**2:raise ValueError('Car Assistant archive exceeds size limit')
    if hashlib.sha256(raw).hexdigest()!=FORK_ARCHIVE_SHA:
        raise ValueError('Car Assistant runtime does not match the trusted installer')
    if not re.fullmatch(r'(?:sha256:[0-9a-f]{64}|ghcr.io/home-assistant/home-assistant@sha256:[0-9a-f]{64})',base_image):
        raise ValueError('Car Assistant requires an immutable verified base image')
    base=json.loads(run(['docker','image','inspect',base_image]))[0]
    base_id=base['Id']
    if not re.fullmatch('sha256:[0-9a-f]{64}',base_id):raise ValueError('Unexpected Docker base identity')
    if base.get('Architecture')!='amd64':raise ValueError('Car Assistant base must be amd64')
    if base.get('Config',{}).get('Labels',{}).get('io.hass.version')!='2026.10.4':
        raise ValueError('Car Assistant needs the carried Home Assistant 2026.10.4 dependency base; deliver a full runtime update')
    identity=hashlib.sha256((FORK_ARCHIVE_SHA+base_id).encode()).hexdigest()
    tag='car-assistant:'+identity
    try:cached=json.loads(run(['docker','image','inspect',tag]))[0]
    except RuntimeError as exc:
        if 'No such' not in str(exc):raise
    else:
        labels=cached.get('Config',{}).get('Labels',{})
        if labels.get('org.drive-console.source-sha256')!=FORK_ARCHIVE_SHA or labels.get('org.drive-console.base')!=base_id:
            raise ValueError('Existing Car Assistant image has unexpected provenance')
        return cached['Id']
    with tempfile.TemporaryDirectory(prefix='car-assistant-build-') as temp:
        temp=Path(temp);root=temp/'rootfs';root.mkdir()
        total=0
        # Extract the same immutable bytes that were hashed, not a reopened
        # user-owned path that could change after verification.
        with tarfile.open(fileobj=io.BytesIO(raw),mode='r:gz') as source:
            for member in source:
                path=Path(member.name)
                if (not member.isfile() or path.is_absolute() or '..' in path.parts or
                    not member.name.startswith(('usr/src/homeassistant/homeassistant/',
                        'usr/local/lib/python3.14/site-packages/hass_frontend/','usr/share/car-assistant/'))):
                    raise ValueError('Unsafe Car Assistant runtime member')
                total+=member.size
                if total>1024**3:raise ValueError('Car Assistant runtime exceeds size limit')
                target=root/path;target.parent.mkdir(parents=True,exist_ok=True)
                with source.extractfile(member) as src,target.open('wb') as dst:shutil.copyfileobj(src,dst)
                target.chmod(0o644)
        # BuildKit interprets a bare sha256 ID in FROM as a registry name. Give
        # the already verified local image a deterministic local tag first.
        base_tag='car-assistant-dependency-base:'+base_id[7:]
        run(['docker','tag',base_id,base_tag])
        recipe=('FROM '+base_tag+'\nCOPY rootfs/ /\nLABEL org.opencontainers.image.title="Car Assistant" '
                'org.opencontainers.image.source="https://github.com/TheRiddler08/Ford-Better-Head-Unit-" '
                'org.drive-console.source-sha256="'+
                FORK_ARCHIVE_SHA+'" org.drive-console.base="'+base_id+'"\n')
        (temp/'Dockerfile').write_text(recipe)
        run(['docker','build','--pull=false','--network=none','--tag',tag,temp],timeout=1200)
    return json.loads(run(['docker','image','inspect',tag]))[0]['Id']


def ensure_car_assistant(settings):
    # Older checkouts must remain bootstrappable by a newer receiver manager.
    if not FORK_ARCHIVE_SHA or not (Path(settings['repo'])/'car_assistant/runtime.tar.gz').exists():return
    if settings.get('forkArchive')==FORK_ARCHIVE_SHA:return
    base=settings.get('baseImage',settings['image'])
    candidate=car_assistant_image(settings['repo'],base)
    upgrade_image(settings,candidate)
    settings.update(baseImage=base,forkArchive=FORK_ARCHIVE_SHA)
    save(SETTINGS,json.dumps(settings).encode(),0o600)


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
        'image':settings['image'],'runtime':'Car Assistant' if settings.get('forkArchive') else 'Home Assistant',
        'forkArchive':settings.get('forkArchive'),'ownerSetup':'Create your owner account in the Home Assistant first-run screen',
        'preservedFiles':preserved or []}).encode())


def validate_config(settings):
    run(['docker','run','--rm','--pull=never','--network','none','--volume',str(BASE/'config')+':/config',
         '--env','CAR_CONSOLE_URL='+settings['backend'],'--entrypoint','python3',settings['image'],
         '-m','homeassistant','--script','check_config','--config','/config'],timeout=300)


# Runs inside the pinned HA image while the real HA instance is stopped. Use HA's
# own Store API/schema; never read owner accounts, tokens, or other auth storage.
STARTER_MIGRATION = r'''
import asyncio,json
from pathlib import Path
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.util.yaml import load_yaml_dict
from homeassistant.components.lovelace.dashboard import DashboardsCollection

async def migrate():
    hass=HomeAssistant('/config')
    marker=Store(hass,1,'car_console_starter_migration')
    if await marker.async_load():
        print(json.dumps({'starterDashboard':'already-migrated'}));return
    config=load_yaml_dict('/config/configuration.yaml')
    if 'car-starter' in config.get('lovelace',{}).get('dashboards',{}):
        raise RuntimeError('Custom configuration.yaml still declares car-starter in YAML; preserved without changing it')
    registry=Store(hass,1,'lovelace_dashboards')
    data=await registry.async_load() or {'items':[]}
    if not isinstance(data.get('items'),list):raise RuntimeError('Unexpected dashboard registry')
    existing=next((item for item in data['items'] if item.get('url_path')=='car-starter'),None)
    if existing:
        await marker.async_save({'completed':True,'preservedExisting':True})
        print(json.dumps({'starterDashboard':'preserved-existing-editable'}));return
    if any(item.get('id')=='car_starter' for item in data['items']):
        raise RuntimeError('Dashboard ID car_starter belongs to another URL; preserving existing dashboards')
    seed=load_yaml_dict('/config/car-dashboard.yaml')
    if not isinstance(seed.get('views'),list):raise RuntimeError('Invalid starter dashboard template')
    dashboard=Store(hass,1,'lovelace.car_starter')
    saved=await dashboard.async_load()
    # Recover a previous interrupted migration without replacing saved edits.
    if not saved or saved.get('config') is None:await dashboard.async_save({'config':seed})
    item=DashboardsCollection.CREATE_SCHEMA({'url_path':'car-starter','mode':'storage',
        'title':'Car starter','icon':'mdi:car','show_in_sidebar':True,'require_admin':False})
    data['items'].append(dict(item,id='car_starter'))
    await registry.async_save(data)
    await marker.async_save({'completed':True})
    print(json.dumps({'starterDashboard':'editable'}))

asyncio.run(migrate())
'''
CLASSIC_MIGRATION = r'''
import asyncio,json
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.util.yaml import load_yaml_dict
from homeassistant.components.lovelace.dashboard import DashboardsCollection

async def migrate_classic():
    hass=HomeAssistant('/config')
    metadata=load_yaml_dict('/config/drive-console-resource.json')
    url=metadata['url']
    if not url.startswith('/local/drive-console.js?v='):raise ValueError('Unexpected local Drive Console resource')
    marker=Store(hass,1,'car_console_classic_migration')
    previous=await marker.async_load() or {}
    resources=Store(hass,1,'lovelace_resources')
    data=await resources.async_load() or {'items':[]}
    item=next((row for row in data['items'] if row.get('id')=='drive_console_local'),None)
    if item is not None and not item.get('url','').startswith('/local/drive-console.js?v='):
        raise ValueError('Drive Console resource ID belongs to another resource')
    updated=False
    if item is None:
        data['items'].append({'id':'drive_console_local','type':'module','url':url});updated=True
    elif item.get('url')!=url:item['url']=url;updated=True
    if updated:await resources.async_save(data)
    if previous.get('completed'):
        # Add native tabs once to our original single-page dashboard. Never
        # replace an existing page/card, or recreate a deleted dashboard/tab.
        if previous.get('dashboardSchema',1)<2 and not previous.get('preservedExisting'):
            registry=await Store(hass,1,'lovelace_dashboards').async_load() or {'items':[]}
            owned=next((row for row in registry['items'] if row.get('id')=='drive_console' and row.get('url_path')=='drive-console'),None)
            store=Store(hass,1,'lovelace.drive_console')
            saved=await store.async_load() if owned else None
            if saved and isinstance(saved.get('config',{}).get('views'),list):
                import copy
                backup=Store(hass,1,'car_console_tabs_backup')
                if await backup.async_load() is None:await backup.async_save(copy.deepcopy(saved))
                views=saved['config']['views']
                paths={view.get('path') for view in views}
                for view in views:
                    if view.get('type')=='custom:drive-console-layout' and isinstance(view.get('cards'),list):
                        if not any(card.get('widget')=='page' for card in view['cards']):
                            view['cards'].append({'type':'custom:drive-console-widget','widget':'page',
                                'page_layout':'cockpit' if view.get('path')=='drive' else 'grid'})
                seed=load_yaml_dict('/config/drive-console-dashboard.yaml')
                views.extend(copy.deepcopy(view) for view in seed['views'] if view.get('path') not in paths and view.get('path')!='drive')
                await store.async_save(saved)
        if previous.get('dashboardSchema',1)<3 and not previous.get('preservedExisting'):
            import copy
            registry=await Store(hass,1,'lovelace_dashboards').async_load() or {'items':[]}
            owned=next((row for row in registry['items'] if row.get('id')=='drive_console' and row.get('url_path')=='drive-console'),None)
            store=Store(hass,1,'lovelace.drive_console')
            saved=await store.async_load() if owned else None
            if saved and isinstance(saved.get('config',{}).get('views'),list):
                backup=Store(hass,1,'car_console_features_backup')
                if await backup.async_load() is None:await backup.async_save(copy.deepcopy(saved))
                views=saved['config']['views'];paths={view.get('path') for view in views}
                seed=load_yaml_dict('/config/drive-console-dashboard.yaml')
                added={'devices','storage','recordings','radio','reports','home-control'}
                views.extend(copy.deepcopy(view) for view in seed['views'] if view.get('path') in added and view.get('path') not in paths)
                for view in views:
                    feature={'health':'overview','updates':'updates','settings':'preferences'}.get(view.get('path'))
                    if feature and isinstance(view.get('cards'),list) and not any(card.get('type')=='custom:drive-console-service' and card.get('feature')==feature for card in view['cards']):
                        view['cards'].append({'type':'custom:drive-console-service','feature':feature})
                await store.async_save(saved)
        if previous.get('resource')!=url or previous.get('dashboardSchema',1)<3:
            await marker.async_save(dict(previous,resource=url,dashboardSchema=3))
        return
    registry=Store(hass,1,'lovelace_dashboards')
    dashboards=await registry.async_load() or {'items':[]}
    existing=next((row for row in dashboards['items'] if row.get('url_path')=='drive-console'),None)
    if existing is None:
        if any(row.get('id')=='drive_console' for row in dashboards['items']):
            raise ValueError('Drive Console ID belongs to another dashboard')
        store=Store(hass,1,'lovelace.drive_console')
        saved=await store.async_load()
        if not saved or saved.get('config') is None:
            await store.async_save({'config':load_yaml_dict('/config/drive-console-dashboard.yaml')})
        item=DashboardsCollection.CREATE_SCHEMA({'url_path':'drive-console','mode':'storage',
            'title':'Drive Console','icon':'mdi:car','show_in_sidebar':True,'require_admin':False})
        dashboards['items'].append(dict(item,id='drive_console'))
        await registry.async_save(dashboards)
    await marker.async_save({'completed':True,'resource':url,'preservedExisting':existing is not None,'dashboardSchema':3})
    print(json.dumps({'driveConsole':'editable','url':'/drive-console'}))

asyncio.run(migrate_classic())
'''
DASHBOARD_MIGRATION=STARTER_MIGRATION+'\n'+CLASSIC_MIGRATION
STARTER_STORES=('lovelace_dashboards','lovelace.car_starter','car_console_starter_migration',
    'lovelace.drive_console','car_console_classic_migration','lovelace_resources','car_console_tabs_backup','car_console_features_backup')


def starter_pending():
    directory=BASE/'config/.storage'
    marker=directory/'car_console_starter_migration'
    if directory.is_symlink() or marker.is_symlink():raise ValueError('Refusing symlinked dashboard storage')
    if not marker.exists():return True
    classic=directory/'car_console_classic_migration'
    if classic.is_symlink():raise ValueError('Refusing symlinked dashboard storage')
    if not classic.exists():return True
    resource_path=BASE/'config/drive-console-resource.json'
    if resource_path.is_symlink():raise ValueError('Refusing symlinked dashboard resource metadata')
    metadata=read_json(resource_path,{})
    stored=read_json(classic,{})
    return (not stored.get('data',{}).get('completed')
        or stored.get('data',{}).get('resource')!=metadata.get('url')
        or stored.get('data',{}).get('dashboardSchema',1)<metadata.get('dashboardSchema',1))


def starter_snapshot():
    result={}
    for key in STARTER_STORES:
        path=BASE/'config/.storage'/key
        if path.is_symlink():raise ValueError('Refusing symlinked dashboard storage')
        result[key]=path.read_bytes() if path.exists() else None
    return result


def restore_starter(snapshot):
    for key,raw in snapshot.items():
        path=BASE/'config/.storage'/key
        if raw is None:path.unlink(missing_ok=True)
        else:save(path,raw,0o600)


def migrate_starter(settings):
    run(['docker','run','--rm','--pull=never','--network','none',
         '--volume',str(BASE/'config')+':/config','--entrypoint','python3',
         settings['image'],'-c',DASHBOARD_MIGRATION],timeout=120)


def reconcile():
    if not SETTINGS.exists():return
    settings=read_json(SETTINGS,{})
    if not settings.get('enabled',True):record_status(settings,False);return
    ensure_car_assistant(settings)
    # Back up only managed assets. Never read or rewrite HA authentication storage.
    config=BASE/'config';registry=BASE/'managed.json'
    old={}
    for name in managed_files(settings['repo'],settings['timezone']):
        target=config/name
        if target.is_symlink() or not target.resolve().is_relative_to(config.resolve()):raise ValueError('Unsafe HA config path: '+name)
        if target.exists():old[name]=target.read_bytes()
    previous=registry.read_bytes() if registry.exists() else None
    pending=starter_pending()
    changed,preserved=sync_assets(settings['repo'],config,settings['timezone'],registry)
    pending=pending or 'drive-console-resource.json' in changed
    active=service_active()
    restarted=False;storage_before=None
    bridge_since=time.time()-15
    try:
        if changed or pending:
            validate_config(settings)
            if active:
                restarted=True
                run(['systemctl','stop','car-home-assistant.service'])
            if pending:
                storage_before=starter_snapshot()
                migrate_starter(settings)
            if active:
                (BASE/'config/car-console-health.json').unlink(missing_ok=True)
                bridge_since=time.time()
                run(['systemctl','start','car-home-assistant.service'])
        ready=check_http(120 if active else 0,bridge_since=bridge_since if active else None)
        if active and not ready:raise RuntimeError('Home Assistant did not become ready')
        record_status(settings,ready,preserved)
    except Exception:
        if storage_before is not None:
            if active and restarted:run(['systemctl','stop','car-home-assistant.service'])
            restore_starter(storage_before)
        for relative in changed:
            if relative in old:save(config/relative,old[relative],0o600)
            else:(config/relative).unlink(missing_ok=True)
        if previous is not None:save(registry,previous,0o600)
        if active and restarted:run(['systemctl','restart','car-home-assistant.service'])
        raise


def create_container(settings):
    run(['docker','create','--pull=never','--name',NAME,'--network','host','--label',LABEL+'=true',
         '--cpu-shares','2048','--security-opt','no-new-privileges=true','--volume',str(BASE/'config')+':/config',
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
        base_image=settings['image']
        settings['image']=car_assistant_image(repo,base_image)
        if FORK_ARCHIVE_SHA:settings.update(baseImage=base_image,forkArchive=FORK_ARCHIVE_SHA)
        config=BASE/'config';config.mkdir(parents=True,exist_ok=True)
        if any(config.iterdir()) and not (BASE/'managed.json').exists():raise ValueError('Existing unmanaged HA configuration; refusing replacement')
        sync_assets(repo,config,timezone,BASE/'managed.json')
        validate_config(settings)
        create_container(settings)
    elif offline_image or FORK_ARCHIVE_SHA:
        if offline_image:base_image=offline_image
        else:
            run(['docker','pull',IMAGE],timeout=900)
            base_image=image_id(IMAGE)
        candidate=car_assistant_image(repo,base_image)
        if info['Image']!=candidate:upgrade_image(settings,candidate)
        if FORK_ARCHIVE_SHA:settings.update(baseImage=base_image,forkArchive=FORK_ARCHIVE_SHA)
    save(SETTINGS,json.dumps(settings).encode(),0o600)
    save('/usr/local/lib/car-dashboard/home_assistant.py',Path(__file__).read_bytes(),0o644)
    wrapper=b'#!/bin/sh\nset -eu\n[ "$#" -eq 0 ] || exit 2\nexec /usr/bin/python3 -I /usr/local/lib/car-dashboard/home_assistant.py reconcile\n'
    save('/usr/local/sbin/car-ha-reconcile',wrapper,0o755)
    save('/etc/systemd/system/car-home-assistant.service',b'''[Unit]
Description=Local car Home Assistant Container
Requires=docker.service
After=docker.service
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
