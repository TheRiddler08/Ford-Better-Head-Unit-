"""Bounded local system observations for the native HA dashboard.

No network probes, service restarts, device commands, coordinates or credentials.
Configuration is distinct from an observed healthy runtime.
"""
from contextlib import closing
import json
from pathlib import Path
import re
import shutil
import sqlite3
import time


def read(path,limit=262144):
    try:
        path=Path(path)
        if path.is_symlink() or not path.is_file() or path.stat().st_size>limit:return {}
        value=json.loads(path.read_text())
        return value if isinstance(value,dict) else {}
    except (OSError,ValueError):return {}


def observation(path,now,ttl=90):
    value=read(path)
    if not value:return {'phase':'pending','observedAt':None,'fresh':False}
    stamp=next((value[k] for k in ('updated','time','checkedAt') if type(value.get(k)) in (int,float)),None)
    age=now-stamp if stamp is not None else None
    result={key:value[key] for key in ('phase','storage','role','interface','address','gateway','wifiInternet','standby','peers','peerStatus','missing','replicaGoal','contributionBytes','mount','ready','enabled','installed','model','sleepMode','mode','ac','lidClosed','piRegistered','lastMessageAt','lastFixAt','satellitesUsed','satellitesVisible','gpsMode') if key in value}
    for key in ('detail','message'):
        if isinstance(value.get(key),str):result[key]=value[key][:400] if not re.search(r'password\s*[=:]|token\s*[=:]|secret\s*[=:]',value[key],re.I) else 'Private diagnostic detail omitted'
    result.update(observedAt=stamp,fresh=bool(age is not None and 0<=age<=ttl))
    if not result.get('phase'):result['phase']=value.get('storage') if isinstance(value.get('storage'),str) else 'configured'
    if stamp is not None and not result['fresh']:result['phase']='stale'
    return result


def snapshot(home,role,settings='/etc/car-dashboard',runtime='/run/car-dashboard',state='/var/lib/car-dashboard-fleet',gps_db=None,now=None):
    home=Path(home);settings=Path(settings);runtime=Path(runtime);state=Path(state);now=time.time() if now is None else now
    software=home/'.local/state/drive-console/software';library=home/'.local/share/car-data'
    network=observation(runtime/'fleet-status.json',now,45)
    if network['phase']=='pending':network=observation(settings/'fleet-status.json',now,45)
    storage=observation(state/'storage.json',now);storage['worker']=observation(library/'status.json',now,150)
    try:
        usage=shutil.disk_usage(home);storage.update(localFreeBytes=usage.free,localTotalBytes=usage.total,
            reserveLow=usage.free<2*1024**3)
    except OSError:storage['localFreeBytes']=None
    try:
        from control.car_data import mounted
        storage['poolMounted']=mounted('/car-data')
    except (OSError,ValueError):storage['poolMounted']=False
    counts=[];database=library/'library.sqlite3'
    if database.is_file() and not database.is_symlink():
        try:
            with closing(sqlite3.connect(database.resolve().as_uri()+'?mode=ro',uri=True,timeout=.2)) as db:
                counts=[{'kind':kind,'objects':count,'bytes':size or 0,'archived':archived or 0} for kind,count,size,archived in db.execute('SELECT kind,COUNT(*),SUM(size),SUM(archived) FROM objects GROUP BY kind')]
        except sqlite3.Error:storage['catalogPhase']='unavailable'
    storage['catalog']=counts
    storage['clientOnly']=role=='pi';storage['activeDatabases']='local';storage['replicaGoal']=storage.get('replicaGoal',2)
    gps_path=Path(gps_db) if gps_db else home/'car-console/obd-data/gps.sqlite3'
    gps=observation(gps_path.with_suffix('.status.json'),now,15)
    radio=observation(software/'radio-status.json',now,180)
    radio['diagnostic']=read(software/'radio-test-result.json')
    radio['diagnostic']={k:v for k,v in radio['diagnostic'].items() if k in ('phase','message','started','finished','elapsedSeconds')}
    radio['gpsReporting']='deferred'
    radio['homeKeyPinned']=(home/'.config/car-dashboard/radio-home.pub').is_file()
    radio['privateKeyPresent']=(home/'.config/car-dashboard/radio-private.key').is_file()
    link=read(settings/'update-link.json');radio['configured']=bool(link.get('radio_model') and link.get('radio_device'))
    peripherals=[]
    for device in Path('/dev/serial/by-id').glob('*'):
        if device.is_symlink() and device.exists():
            kind='LoRaWAN' if isinstance(link.get('radio_device'),str) and device.resolve()==Path(link['radio_device']).resolve() else 'GPS' if 'u-blox' in device.name else 'unassigned serial device'
            peripherals.append({'name':device.name,'kind':kind})
    return {'schema':1,'role':role,'observedAt':now,'network':network,'storage':storage,'gps':gps,
      'radio':radio,'software':observation(software/'status.json',now,60),
      'power':observation(settings/'laptop-power-status.json',now,90),
      'reboot':observation(settings/'reboot-status.json',now),
      'navigation':observation(settings/'navigation-status.json',now),
      'assistant':{k:v for k,v in read(settings/'home-assistant.json').items() if k in ('installed','enabled','ready','runtime')},
      'peripherals':peripherals,
      'future':{'usbCameraAI':'planned; separate Pi 5 and AI HAT','homeAI':'planned; hotspot/IP only','gatewayMesh':'not configured'},
      'limits':{'radioVideo':False,'gpsEncryptedAtRest':False,'cameraProtocol':'G980H download protocol unverified'}}
