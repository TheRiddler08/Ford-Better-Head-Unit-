"""Compact authenticated sensor batches, carried by the existing sealed relay.

GPS has no metric ID. Local collection is read-only; no ECU commands are issued.
Samples coalesce behind one batch instead of building an obsolete history queue.
"""
from contextlib import closing
import hashlib
import hmac
import json
import math
from pathlib import Path
import shutil
import socket
import sqlite3
import struct
import time
from urllib.request import build_opener,ProxyHandler

HEADER=struct.Struct('>BBIB')
ENTRY=struct.Struct('>Bi')
ROLES={'duet':1,'tiny':2,'pi':3,'laptop':4}
METRICS={
 'speed':(1,100,'km/h'),'rpm':(2,1,'rpm'),'coolant':(3,100,'°C'),
 'voltage':(4,100,'V'),'fuel':(5,100,'%'),'load':(6,100,'%'),
 'throttle':(7,100,'%'),'intake':(8,100,'°C'),
 'uptime':(9,1,'s'),'free_space':(10,1,'MiB')}
TAG=b'car-sensor-batch-v1\0'


def encode(role,values,key,stamp=None):
    if role not in ROLES or len(key)<32:raise ValueError('Invalid telemetry identity')
    stamp=int(time.time() if stamp is None else stamp)
    if not 0<=stamp<2**32:raise ValueError('Invalid telemetry timestamp')
    entries=[]
    for name,value in values.items():
        if name not in METRICS:raise ValueError('Sensor is not allowlisted')
        if type(value) not in (int,float) or not math.isfinite(value):raise ValueError('Invalid sensor value')
        code,scale,_=METRICS[name];number=round(value*scale)
        if not -(2**31)<=number<2**31:raise ValueError('Sensor value outside encoding range')
        entries.append(ENTRY.pack(code,number))
    result=[]
    for offset in range(0,len(entries),6):
        batch=entries[offset:offset+6];raw=HEADER.pack(5,ROLES[role],stamp,len(batch))+b''.join(batch)
        result.append(raw+hmac.new(key,TAG+raw,hashlib.sha256).digest()[:8])
    return result


def decode(raw,key):
    if len(key)<32 or not HEADER.size+ENTRY.size+8<=len(raw)<=46:raise ValueError('Invalid telemetry frame size')
    version,role,stamp,count=HEADER.unpack(raw[:HEADER.size])
    if version!=5 or role not in ROLES.values() or not 1<=count<=6 or len(raw)!=HEADER.size+count*ENTRY.size+8:raise ValueError('Invalid telemetry header')
    if not hmac.compare_digest(raw[-8:],hmac.new(key,TAG+raw[:-8],hashlib.sha256).digest()[:8]):raise ValueError('Telemetry authentication failed')
    values={};registry={row[0]:(name,row[1]) for name,row in METRICS.items()}
    for pos in range(HEADER.size,len(raw)-8,ENTRY.size):
        code,value=ENTRY.unpack(raw[pos:pos+ENTRY.size])
        if code not in registry:raise ValueError('Unknown telemetry metric')
        name,scale=registry[code]
        if name in values:raise ValueError('Duplicate telemetry metric')
        values[name]=value/scale
    return {'source':next(name for name,code in ROLES.items() if code==role),'observedAt':stamp,'values':values}


def store(path,sample,now=None,transport='unknown'):
    now=time.time() if now is None else now
    if transport not in ('unknown','ip','lorawan'):raise ValueError('Invalid sensor transport')
    if not 0<=now-sample['observedAt']<=86400:return False
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    with closing(sqlite3.connect(path)) as db,db:
        db.execute('CREATE TABLE IF NOT EXISTS sensors(role TEXT,name TEXT,value REAL,observed REAL,received REAL,PRIMARY KEY(role,name))')
        if 'transport' not in {row[1] for row in db.execute('PRAGMA table_info(sensors)')}:
            db.execute("ALTER TABLE sensors ADD COLUMN transport TEXT NOT NULL DEFAULT 'unknown'")
        for name,value in sample['values'].items():
            if name not in METRICS:raise ValueError('Sensor not allowlisted')
            db.execute('INSERT INTO sensors(role,name,value,observed,received,transport) VALUES(?,?,?,?,?,?) ON CONFLICT(role,name) DO UPDATE SET value=excluded.value,observed=excluded.observed,received=excluded.received,transport=excluded.transport WHERE excluded.observed>sensors.observed',
                       (sample['source'],name,value,sample['observedAt'],now,transport))
    path.chmod(0o600);return True


def snapshot(path,now=None,max_age=1800):
    now=time.time() if now is None else now;result={role:{'values':{name:None for name in METRICS},'observed':{name:None for name in METRICS},'observedAt':None,'receivedAt':None,'transport':'none'} for role in ROLES}
    path=Path(path)
    if not path.exists():return result
    with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)) as db:
        # Read older databases during rolling home-service upgrades as well.
        transport='transport' if 'transport' in {row[1] for row in db.execute('PRAGMA table_info(sensors)')} else "'unknown'"
        for role,name,value,observed,received,via in db.execute('SELECT role,name,value,observed,received,'+transport+' FROM sensors'):
            if role not in result or name not in METRICS:continue
            if received>(result[role]['receivedAt'] or 0):result[role].update(receivedAt=received,transport=via)
            result[role]['observedAt']=max(result[role]['observedAt'] or 0,observed)
            result[role]['observed'][name]=observed
            if 0<=now-observed<=max_age:result[role]['values'][name]=value
    return result


def collect(home,role):
    values={'uptime':int(float(Path('/proc/uptime').read_text().split()[0])),
            'free_space':shutil.disk_usage(home).free//1024**2}
    if role=='tiny':
        try:
            with build_opener(ProxyHandler({})).open('http://127.0.0.1:8787/api/home-assistant/state',timeout=3) as response:
                raw=response.read(65537)
            if len(raw)>65536:raise ValueError('Local sensor response too large')
            for name,row in json.loads(raw).get('metrics',{}).items():
                if name in METRICS and row.get('fresh') and type(row.get('value')) in (int,float) and math.isfinite(row['value']):values[name]=row['value']
        except (OSError,ValueError,TypeError):pass
    return values


def worker():
    from control.update_agent import atomic,LAN,PORT
    config=json.loads(Path('/etc/car-dashboard/fleet.json').read_text());home=Path(config['home']);role=config['role']
    state=home/'.local/state/drive-console/software';queue=state/'telemetry-pending.json';next_batch=0
    with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as receiver,socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as sender:
        receiver.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1);receiver.bind(('',PORT));receiver.settimeout(1)
        sender.setsockopt(socket.SOL_SOCKET,socket.SO_BROADCAST,1);sender.bind((LAN[role],0))
        next_send=0
        while True:
            options=json.loads(Path('/etc/car-dashboard/update-link.json').read_text())
            if not options.get('telemetry_radio_enabled',True):time.sleep(5);continue
            keyfile=home/'.config/car-dashboard/update-link.key'
            if not keyfile.exists():time.sleep(5);continue
            key=bytes.fromhex(keyfile.read_text().strip())
            ip_path=state/'status.json';ip=json.loads(ip_path.read_text()) if ip_path.exists() else {}
            if ip.get('phase')=='connected' and 0<=time.time()-ip.get('time',0)<=30:
                atomic(queue,b'[]');time.sleep(5);continue
            pending=json.loads(queue.read_text()) if queue.exists() else []
            try:
                data,peer=receiver.recvfrom(128)
                if peer[0] in LAN.values() and data[:2]==b'R\x05' and pending and data[1:].hex()==pending[0]:
                    decode(data[1:],key);pending.pop(0);atomic(queue,json.dumps(pending).encode())
            except (socket.timeout,ValueError):pass
            if not pending and time.monotonic()>=next_batch:
                pending=[raw.hex() for raw in encode(role,collect(home,role),key)]
                atomic(queue,json.dumps(pending).encode());next_batch=time.monotonic()+max(300,int(options.get('telemetry_interval',900)))
            if pending and time.monotonic()>=next_send:
                sender.sendto(b'U'+bytes.fromhex(pending[0]),('172.16.42.255',PORT));next_send=time.monotonic()+5


if __name__=='__main__':worker()
