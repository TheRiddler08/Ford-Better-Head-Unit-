"""Selected non-executable text data sync using existing authenticated event frames.

Small deltas use radio; larger objects use the authenticated home IP exchange.
Both share a transfer ID, base/target hashes and applied/conflict receipt. This
never installs code, changes system paths, or mirrors unselected vehicle data.
"""
from contextlib import closing
import difflib
import hashlib
import json
from pathlib import Path
import re
import socket
import sqlite3
import time
import uuid
from control.patches import apply_hunks
from control.update_agent import atomic

LIMIT=16384


def digest(text):return hashlib.sha256(text.encode()).hexdigest()


def filename(name):
    if not isinstance(name,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,59}\.(txt|json)',name) or '..' in name:
        raise ValueError('Sync files must be selected .txt/.json basenames')
    return name


def validate(doc):
    if not isinstance(doc,dict) or doc.get('type') not in ('file','file-receipt'):raise ValueError('Invalid file sync object')
    expected={'type','source','destination','name','base','target','hunks'} if doc['type']=='file' else {'type','source','destination','name','transferId','target','status'}
    if set(doc)!=expected:raise ValueError('Invalid file sync fields')
    from control.telemetry import ROLES
    if doc['source'] not in (*ROLES,'home') or doc['destination'] not in (*ROLES,'home') or ('home' not in (doc['source'],doc['destination'])) or doc['source']==doc['destination']:raise ValueError('Invalid sync direction')
    filename(doc['name'])
    for key in ('base','target') if doc['type']=='file' else ('target',):
        if not isinstance(doc[key],str) or not re.fullmatch('[a-f0-9]{64}',doc[key]):raise ValueError('Invalid sync hash')
    if doc['type']=='file':
        if not isinstance(doc['hunks'],list) or len(doc['hunks'])>100:raise ValueError('Invalid delta')
    elif doc['status'] not in ('applied','conflict','not-selected') or not isinstance(doc['transferId'],str) or not re.fullmatch('sync:[a-f0-9]{32}',doc['transferId']):raise ValueError('Invalid sync receipt')
    if len(json.dumps(doc).encode())>131072:raise ValueError('Sync object exceeds limit')


class Files:
    def __init__(self,root,state,events,role,peer,selected):
        if len(selected)>4:raise ValueError('Select at most four small data files per device')
        self.root=Path(root);self.state=Path(state);self.events=events;self.role=role;self.peer=peer
        self.selected={filename(name) for name in selected};self.state.parent.mkdir(parents=True,exist_ok=True)
        with closing(self.db()) as db,db:
            db.execute('CREATE TABLE IF NOT EXISTS files(name TEXT PRIMARY KEY,confirmed TEXT,pending TEXT,doc TEXT,phase TEXT,last REAL)')
            db.execute('CREATE TABLE IF NOT EXISTS applied(id TEXT PRIMARY KEY,hash TEXT,reply TEXT)')
            paused=db.execute('SELECT name,pending FROM files WHERE pending IS NOT NULL').fetchall()
            for name,ident in paused:
                if name not in self.selected:
                    db.execute("UPDATE files SET phase='paused-unselected' WHERE name=?",(name,))
                    with closing(self.events.db()) as events_db,events_db:events_db.execute('UPDATE jobs SET done=2 WHERE id=?',(ident,))
        self.state.chmod(0o600)

    def db(self):
        from control.storage_policy import bounded_db
        db=bounded_db(sqlite3.connect(self.state,timeout=10));db.execute('PRAGMA synchronous=FULL');return db

    def read(self,name):
        if name not in self.selected:raise ValueError('File not selected')
        if self.root.is_symlink() or self.root.parent.is_symlink():raise ValueError('Symlinked sync folder rejected')
        path=self.root/filename(name)
        if path.is_symlink():raise ValueError('Symlinked sync file rejected')
        if not path.exists():return ''
        if not path.is_file() or path.stat().st_size>LIMIT:raise ValueError('Sync file exceeds 16 KiB limit')
        return path.read_bytes().decode('utf-8')

    def scan(self):
        for name in sorted(self.selected):
            if not (self.root/name).exists():continue # No implicit deletions.
            current=self.read(name)
            with closing(self.db()) as db:row=db.execute('SELECT confirmed,pending,doc,phase,last FROM files WHERE name=?',(name,)).fetchone()
            if row and row[1]:
                if row[4]<time.time()-600:
                    with closing(self.events.db()) as db:sent=db.execute('SELECT done FROM jobs WHERE id=?',(row[1],)).fetchone()
                    ident=row[1]
                    if sent and sent[0]==1:
                        ident='sync:'+uuid.uuid4().hex
                        with closing(self.db()) as db,db:db.execute('UPDATE files SET pending=? WHERE name=?',(ident,name))
                    self.queue(ident,json.loads(row[2]))
                continue
            baseline=row[0] if row else ''
            if current==baseline:continue
            hunks=[[a,b,current[c:d]] for tag,a,b,c,d in difflib.SequenceMatcher(a=baseline,b=current,autojunk=False).get_opcodes() if tag!='equal']
            doc={'type':'file','source':self.role,'destination':self.peer,'name':name,'base':digest(baseline),'target':digest(current),'hunks':hunks}
            validate(doc);ident='sync:'+uuid.uuid4().hex
            from control.lorawan_protocol import canonical
            phase='waiting-ip' if len(canonical(doc))>800 else 'queued'
            with closing(self.db()) as db,db:db.execute('INSERT INTO files VALUES(?,?,?,?,?,?) ON CONFLICT(name) DO UPDATE SET pending=excluded.pending,doc=excluded.doc,phase=excluded.phase,last=excluded.last',
                (name,baseline,ident,json.dumps(doc),phase,time.time()))
            self.queue(ident,doc)

    def queue(self,ident,doc):
        from control.lorawan_protocol import canonical
        if len(canonical(doc))<=800:
            self.events.enqueue(ident,doc)
            with closing(self.events.db()) as db,db:db.execute('UPDATE jobs SET done=0,cursor=0 WHERE id=? AND done IN (1,2)',(ident,))
        with closing(self.db()) as db,db:db.execute('UPDATE files SET last=? WHERE pending=?',(time.time(),ident))

    def pending(self):
        with closing(self.db()) as db:rows=db.execute('SELECT pending,doc FROM files WHERE pending IS NOT NULL').fetchall()
        return [{'id':ident,'doc':json.loads(doc)} for ident,doc in rows if json.loads(doc)['name'] in self.selected]

    def receive(self,doc,ident,via_ip=False):
        validate(doc)
        if doc['destination']!=self.role or doc['source']!=self.peer:raise ValueError('Sync object belongs to another peer')
        name=doc['name']
        if doc['type']=='file-receipt':
            with closing(self.db()) as db,db:
                row=db.execute('SELECT confirmed,pending,doc FROM files WHERE name=?',(name,)).fetchone()
                if not row or row[1]!=doc['transferId'] or json.loads(row[2])['target']!=doc['target']:return
                if doc['status']=='applied':
                    changed=apply_hunks(row[0],json.loads(row[2])['hunks'])
                    if digest(changed)!=doc['target']:raise ValueError('Sync baseline checksum failed')
                    db.execute('UPDATE files SET confirmed=?,pending=NULL,doc=NULL,phase=?,last=? WHERE name=?',(changed,'applied',time.time(),name))
                    with closing(self.events.db()) as events_db,events_db:events_db.execute('UPDATE jobs SET done=1 WHERE id=?',(doc['transferId'],))
                else:db.execute('UPDATE files SET phase=? WHERE name=?',(doc['status'],name))
            return
        if not isinstance(ident,str) or not re.fullmatch('sync:[a-f0-9]{32}',ident):raise ValueError('Invalid transfer identity')
        if name in self.selected and (self.root.is_symlink() or self.root.parent.is_symlink() or (self.root/name).is_symlink()):raise ValueError('Symlinked sync target rejected')
        fingerprint=hashlib.sha256(json.dumps(doc,sort_keys=True).encode()).hexdigest()
        with closing(self.db()) as db:old=db.execute('SELECT hash,reply FROM applied WHERE id=?',(ident,)).fetchone()
        if old:
            if old[0]!=fingerprint:raise ValueError('Transfer identity conflicts with completed data')
            reply=json.loads(old[1]);receipt='sync-receipt:'+ident[5:]+':'+uuid.uuid4().hex[:8]
            if not via_ip:self.events.enqueue(receipt,reply)
            return {'id':receipt,'doc':reply}
        status='not-selected'
        if name in self.selected:
            previous=self.read(name)
            if digest(previous)==doc['target']:status='applied'
            elif digest(previous)!=doc['base']:status='conflict'
            else:
                changed=apply_hunks(previous,doc['hunks'])
                if len(changed.encode())>LIMIT or digest(changed)!=doc['target']:raise ValueError('Sync checksum or size failed')
                self.root.mkdir(parents=True,exist_ok=True);atomic(self.root/name,changed.encode());status='applied'
            if status=='applied':
                with closing(self.db()) as db,db:
                    db.execute('INSERT INTO files VALUES(?,?,NULL,NULL,?,?) ON CONFLICT(name) DO UPDATE SET confirmed=CASE WHEN files.pending IS NULL THEN excluded.confirmed ELSE files.confirmed END,phase=CASE WHEN files.pending IS NULL THEN excluded.phase ELSE files.phase END',
                               (name,self.read(name),'applied',time.time()))
        reply={'type':'file-receipt','source':self.role,'destination':self.peer,'name':name,'transferId':ident,'target':doc['target'],'status':status}
        if status=='applied':
            with closing(self.db()) as db,db:db.execute('INSERT INTO applied VALUES(?,?,?)',(ident,fingerprint,json.dumps(reply)))
        elif status=='conflict':
            with closing(self.db()) as db,db:db.execute("INSERT INTO files VALUES(?,?,NULL,NULL,'conflict',?) ON CONFLICT(name) DO UPDATE SET phase=CASE WHEN files.pending IS NULL THEN 'conflict' ELSE files.phase END",(name,'',time.time()))
        receipt='sync-receipt:'+ident[5:]+':'+uuid.uuid4().hex[:8]
        if not via_ip:self.events.enqueue(receipt,reply)
        return {'id':receipt,'doc':reply}

    def status(self):
        with closing(self.db()) as db:return [{'name':name,'phase':phase,'id':pending} for name,phase,pending in db.execute('SELECT name,phase,pending FROM files')]

    def receipts(self):
        with closing(self.events.db()) as db:rows=db.execute("SELECT id,doc FROM jobs WHERE done=0 AND json_extract(doc,'$.type')='file-receipt' LIMIT 8").fetchall()
        return [{'id':ident,'doc':json.loads(doc)} for ident,doc in rows]


def car_files(config):
    from control.event_transfer import Events
    home=Path(config['home']);state=home/'.local/state/drive-console/software'
    key=bytes.fromhex((home/'.config/car-dashboard/update-link.key').read_text().strip())
    events=Events(state/'file-events.sqlite3',key,destination=config['role'],types=('file','file-receipt'))
    return Files(home/'car-console/sync',state/'file-sync.sqlite3',events,config['role'],'home',config.get('sync_files',[]))


def home_files(root,role,key):
    from control.event_transfer import Events
    options=Path(root)/'sync-settings.json';selected=json.loads(options.read_text()).get('files',[]) if options.exists() else []
    events=Events(Path(root)/'radio/catalog.sqlite3',key,destination='home')
    return Files(Path(root)/'sync'/role,Path(root)/'sync-state'/(role+'.sqlite3'),events,'home',role,selected)


def worker():
    from control.update_agent import LAN,PORT
    config=json.loads(Path('/etc/car-dashboard/fleet.json').read_text());role=config['role'];home=Path(config['home'])
    with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as receiver,socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as sender:
        receiver.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1);receiver.bind(('',PORT));receiver.settimeout(1)
        sender.setsockopt(socket.SOL_SOCKET,socket.SO_BROADCAST,1);sender.bind((LAN[role],0));next_send=0
        while True:
            options=json.loads(Path('/etc/car-dashboard/update-link.json').read_text());settings=dict(config,**options)
            if not (home/'.config/car-dashboard/update-link.key').exists():time.sleep(5);continue
            files=car_files(settings)
            try:
                data,peer=receiver.recvfrom(128)
                if peer[0] in LAN.values() and data[:3]==b'D\x01\x06':
                    result=files.events.receive(data[1:],with_id=True)
                    if result and result['doc'].get('type') in ('file','file-receipt'):files.receive(result['doc'],result['id'])
                elif peer[0] in LAN.values() and data[:3]==b'R\x01\x06':
                    files.events.sent(data[1:],ack=True);files.events.sent(data[1:])
            except (socket.timeout,ValueError):pass
            if time.monotonic()>=next_send:
                files.scan()
                with closing(files.events.db()) as db:completed=db.execute("SELECT id FROM jobs WHERE done IN (1,2) ORDER BY rowid DESC LIMIT 16").fetchall()
                for row in completed:
                    for frame in files.events.completed_frames(row[0]):sender.sendto(b'F'+frame,('172.16.42.255',PORT))
                status_path=home/'.local/state/drive-console/software/status.json'
                ip=json.loads(status_path.read_text()) if status_path.exists() else {}
                if ip.get('phase')!='connected' or time.time()-ip.get('time',0)>30:
                    raw=files.events.outgoing(ack=True) or files.events.outgoing(kinds=['file','file-receipt'])
                    if raw:sender.sendto(b'U'+raw,('172.16.42.255',PORT))
                atomic(home/'.local/state/drive-console/software/file-sync-status.json',json.dumps(files.status()).encode());next_send=time.monotonic()+10


def home_worker():
    from control.home_server import ROOT,radio_key
    from control.telemetry import ROLES
    while True:
        for role in ROLES:home_files(ROOT,role,radio_key()).scan()
        time.sleep(10)


if __name__=='__main__':
    import sys
    if '--status' in sys.argv:
        if '--home' in sys.argv:
            from control.home_server import ROOT,radio_key
            from control.telemetry import ROLES
            print(json.dumps({role:home_files(ROOT,role,radio_key()).status() for role in ROLES},indent=2))
        else:
            config=json.loads(Path('/etc/car-dashboard/fleet.json').read_text())
            config.update(json.loads(Path('/etc/car-dashboard/update-link.json').read_text()))
            print(json.dumps(car_files(config).status(),indent=2))
    else:home_worker() if '--home' in sys.argv else worker()
