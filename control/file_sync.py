"""Selected non-executable text data sync using existing authenticated event frames.

Small deltas use radio; larger objects use the authenticated home IP exchange.
Both share a transfer ID, base/target hashes and applied/conflict receipt. This
never installs code, changes system paths, or mirrors unselected vehicle data.
"""
from contextlib import closing,contextmanager
import difflib
import fcntl
from functools import wraps
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import sqlite3
import time
import threading
import uuid
from control.patches import apply_hunks
from control.update_agent import atomic

LIMIT=16384


class LocalFileError(ValueError):
    """A local file needs attention; other files and software updates may proceed."""


def serialized(method):
    @wraps(method)
    def locked(self,*args,**kwargs):
        with self.lock():return method(self,*args,**kwargs)
    return locked


def digest(text):return hashlib.sha256(text.encode()).hexdigest()


def filename(name):
    if not isinstance(name,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,59}\.(txt|json)',name) or '..' in name:
        raise ValueError('Sync files must be selected .txt/.json basenames')
    return name


def selected_names(value):
    if not isinstance(value,list) or len(value)>4:raise ValueError('Select at most four small data files per device')
    return sorted({filename(name) for name in value})


def home_selection(root,role):
    from control.telemetry import ROLES
    if role not in ROLES:raise ValueError('Unknown car role')
    path=Path(root)/'sync-settings.json'
    if path.is_symlink():raise ValueError('Symlinked sync settings rejected')
    options=json.loads(path.read_text()) if path.exists() else {'files':[]}
    selected=selected_names(options.get('roles',{}).get(role,options.get('files',[])))
    return {'role':role,'files':selected,'revision':digest(json.dumps(options,sort_keys=True))}


def save_home_selection(root,role,names,expected):
    names=selected_names(names);root=Path(root);root.mkdir(parents=True,exist_ok=True)
    fd=os.open(root/'sync-settings.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    try:
        fcntl.flock(fd,fcntl.LOCK_EX)
        current=home_selection(root,role)
        if current['revision']!=expected:raise ValueError('File selection changed; reload before saving')
        path=root/'sync-settings.json';options=json.loads(path.read_text()) if path.exists() else {'files':[]}
        options.setdefault('roles',{})[role]=names;atomic(path,json.dumps(options).encode())
    finally:os.close(fd)
    return home_selection(root,role)


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
        self.root=Path(root);self.state=Path(state);self.events=events;self.role=role;self.peer=peer
        self.mutex=threading.RLock();self.lock_depth=0;self.resolving_name=None
        self.selected=set(selected_names(selected));self.state.parent.mkdir(parents=True,exist_ok=True)
        with closing(self.db()) as db,db:
            db.execute('CREATE TABLE IF NOT EXISTS files(name TEXT PRIMARY KEY,confirmed TEXT,pending TEXT,doc TEXT,phase TEXT,last REAL)')
            db.execute('CREATE TABLE IF NOT EXISTS applied(id TEXT PRIMARY KEY,hash TEXT,reply TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS problems(name TEXT PRIMARY KEY,detail TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS conflicts(id TEXT PRIMARY KEY,name TEXT,doc TEXT,incoming TEXT,local_hash TEXT,updated REAL,resolved INTEGER DEFAULT 0)')
            db.execute('CREATE TABLE IF NOT EXISTS resolutions(id TEXT PRIMARY KEY,name TEXT,chosen TEXT,local_hash TEXT,car_hash TEXT,phase TEXT)')
            paused=db.execute('SELECT name,pending FROM files WHERE pending IS NOT NULL').fetchall()
            for name,ident in paused:
                if name not in self.selected:
                    db.execute("UPDATE files SET phase='paused-unselected' WHERE name=?",(name,))
                    with closing(self.events.db()) as events_db,events_db:events_db.execute('UPDATE jobs SET done=2 WHERE id=?',(ident,))
        self.state.chmod(0o600)

    @contextmanager
    def lock(self):
        # Shared by the HTTP editor, IP receiver and background workers.
        with self.mutex:
            if self.lock_depth:
                yield;return
            fd=os.open(self.state.with_suffix('.lock'),os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
            try:
                fcntl.flock(fd,fcntl.LOCK_EX);self.lock_depth=1
                try:yield
                finally:self.lock_depth=0
            finally:os.close(fd)

    def db(self):
        from control.storage_policy import bounded_db
        db=bounded_db(sqlite3.connect(self.state,timeout=10));db.execute('PRAGMA synchronous=FULL');return db

    def read(self,name):
        if name not in self.selected:raise ValueError('File not selected')
        if self.root.is_symlink() or self.root.parent.is_symlink():raise LocalFileError('Symlinked sync folder rejected')
        path=self.root/filename(name)
        if path.is_symlink():raise LocalFileError('Symlinked sync file rejected')
        if not path.exists():return ''
        try:
            if not path.is_file() or path.stat().st_size>LIMIT:raise LocalFileError('Sync file exceeds 16 KiB limit or is not a regular file')
            raw=path.read_bytes()
            if len(raw)>LIMIT:raise LocalFileError('Sync file exceeds 16 KiB limit')
            return raw.decode('utf-8')
        except UnicodeError:raise LocalFileError('Sync file is not valid UTF-8') from None
        except OSError:raise LocalFileError('Sync file cannot be read') from None

    def problem(self,name,detail=None):
        with closing(self.db()) as db,db:
            if detail:
                db.execute('INSERT INTO problems VALUES(?,?) ON CONFLICT(name) DO UPDATE SET detail=excluded.detail',(name,detail))
                row=db.execute('SELECT pending FROM files WHERE name=?',(name,)).fetchone()
                if row and row[0]:
                    with closing(self.events.db()) as events_db,events_db:events_db.execute('UPDATE jobs SET done=2 WHERE id=? AND done=0',(row[0],))
            else:db.execute('DELETE FROM problems WHERE name=?',(name,))

    def scan(self):
        self.resume_resolutions()
        for name in sorted(self.selected):
            try:self.scan_file(name)
            except LocalFileError as error:self.problem(name,str(error))

    @serialized
    def scan_file(self,name):
        with closing(self.db()) as db:
            if self.resolving_name!=name and db.execute("SELECT 1 FROM resolutions WHERE name=? AND phase IN ('prepared','changed-locally')",(name,)).fetchone():return
        current=self.read(name)
        with closing(self.db()) as db:was_blocked=db.execute('SELECT 1 FROM problems WHERE name=?',(name,)).fetchone()
        if not (self.root/name).exists():return # No implicit deletions.
        self.problem(name)
        with closing(self.db()) as db:row=db.execute('SELECT confirmed,pending,doc,phase,last FROM files WHERE name=?',(name,)).fetchone()
        if row and row[1]:
            if was_blocked or row[4]<time.time()-600:
                with closing(self.events.db()) as db:sent=db.execute('SELECT done FROM jobs WHERE id=?',(row[1],)).fetchone()
                ident=row[1]
                if sent and sent[0]==1:
                    ident='sync:'+uuid.uuid4().hex
                    with closing(self.db()) as db,db:db.execute('UPDATE files SET pending=? WHERE name=?',(ident,name))
                self.queue(ident,json.loads(row[2]))
            return
        baseline=row[0] if row else ''
        if current==baseline:return
        hunks=[[a,b,current[c:d]] for tag,a,b,c,d in difflib.SequenceMatcher(a=baseline,b=current,autojunk=False).get_opcodes() if tag!='equal']
        doc={'type':'file','source':self.role,'destination':self.peer,'name':name,'base':digest(baseline),'target':digest(current),'hunks':hunks}
        try:validate(doc)
        except ValueError:raise LocalFileError('Delta too complex; simplify this file before syncing') from None
        ident='sync:'+uuid.uuid4().hex
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
        with closing(self.db()) as db:rows=db.execute('SELECT pending,doc FROM files WHERE pending IS NOT NULL AND name NOT IN (SELECT name FROM problems)').fetchall()
        return [{'id':ident,'doc':json.loads(doc)} for ident,doc in rows if json.loads(doc)['name'] in self.selected]

    def receive_available(self,doc,ident,via_ip=False):
        """Defer a blocked local file without rejecting unrelated source updates."""
        try:return self.receive(doc,ident,via_ip)
        except LocalFileError as error:
            self.problem(doc['name'],str(error))
            return None # No applied receipt: sender must retain its original.

    @serialized
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
        if name in self.selected and (self.root.is_symlink() or self.root.parent.is_symlink() or (self.root/name).is_symlink()):raise LocalFileError('Symlinked sync target rejected')
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
                try:
                    self.root.mkdir(parents=True,exist_ok=True);atomic(self.root/name,changed.encode())
                except OSError:raise LocalFileError('Sync target cannot be written') from None
                status='applied'
            if status=='applied':
                self.problem(name)
                with closing(self.db()) as db,db:
                    db.execute('INSERT INTO files VALUES(?,?,NULL,NULL,?,?) ON CONFLICT(name) DO UPDATE SET confirmed=CASE WHEN files.pending IS NULL THEN excluded.confirmed ELSE files.confirmed END,phase=CASE WHEN files.pending IS NULL THEN excluded.phase ELSE files.phase END',
                               (name,self.read(name),'applied',time.time()))
        reply={'type':'file-receipt','source':self.role,'destination':self.peer,'name':name,'transferId':ident,'target':doc['target'],'status':status}
        if status=='applied':
            with closing(self.db()) as db,db:
                db.execute('INSERT INTO applied VALUES(?,?,?)',(ident,fingerprint,json.dumps(reply)))
                db.execute("UPDATE conflicts SET resolved=1 WHERE name=? AND json_extract(doc,'$.target')=?",(name,doc['target']))
        elif status=='conflict':
            with closing(self.db()) as db,db:
                if not db.execute('SELECT 1 FROM conflicts WHERE id=?',(ident,)).fetchone() and db.execute('SELECT COUNT(*) FROM conflicts WHERE resolved=0').fetchone()[0]>=32:
                    raise LocalFileError('Unresolved conflict limit reached; review existing conflicts before receiving more')
                row=db.execute('SELECT confirmed FROM files WHERE name=?',(name,)).fetchone()
                base=row[0] if row else '';incoming=None
                if digest(base)==doc['base']:
                    try:
                        candidate=apply_hunks(base,doc['hunks'])
                        if len(candidate.encode())<=LIMIT and digest(candidate)==doc['target']:incoming=candidate
                    except (ValueError,TypeError):pass
                db.execute("INSERT INTO files VALUES(?,?,NULL,NULL,'conflict',?) ON CONFLICT(name) DO UPDATE SET phase=CASE WHEN files.pending IS NULL THEN 'conflict' ELSE files.phase END",(name,'',time.time()))
                db.execute('INSERT INTO conflicts VALUES(?,?,?,?,?,?,0) ON CONFLICT(id) DO NOTHING',
                    (ident,name,json.dumps(doc),incoming,digest(previous),time.time()))
        receipt='sync-receipt:'+ident[5:]+':'+uuid.uuid4().hex[:8]
        if not via_ip:self.events.enqueue(receipt,reply)
        return {'id':receipt,'doc':reply}

    def backup(self,name,text):
        folder=self.state.parent/(self.state.stem+'-backups')
        if folder.is_symlink():raise LocalFileError('Symlinked backup folder rejected')
        folder.mkdir(parents=True,exist_ok=True)
        path=folder/(filename(name)+'.'+digest(text)+'.bak')
        if path.is_symlink():raise LocalFileError('Symlinked backup rejected')
        if not path.exists():
            if len(list(folder.iterdir()))>=32:raise LocalFileError('Backup limit reached; archive backups before editing more files')
            atomic(path,text.encode())
        elif path.read_bytes()!=text.encode():raise LocalFileError('Existing backup checksum failed')
        return path.name

    @serialized
    def inspect(self,name):
        text=self.read(filename(name))
        with closing(self.db()) as db:
            row=db.execute('SELECT phase,pending FROM files WHERE name=?',(name,)).fetchone()
            recovery=db.execute("SELECT phase,chosen FROM resolutions WHERE name=? AND phase IN ('prepared','changed-locally') ORDER BY rowid DESC LIMIT 1",(name,)).fetchone()
            problem=db.execute('SELECT detail FROM problems WHERE name=?',(name,)).fetchone()
            conflicts=[{'id':ident,'target':json.loads(doc)['target'],'incoming':incoming,'reportedAt':updated}
                for ident,doc,incoming,updated in db.execute("SELECT id,doc,incoming,updated FROM conflicts WHERE name=? AND (resolved=0 OR id IN (SELECT id FROM resolutions WHERE phase='changed-locally')) ORDER BY updated DESC LIMIT 8",(name,))]
        return {'name':name,'source':text,'sha256':digest(text),'exists':(self.root/name).exists(),
            'phase':'resolution-changed-locally' if recovery and recovery[0]=='changed-locally' else 'resolution-pending' if recovery else row[0] if row else 'idle',
            'pending':row[1] if row else None,'conflicts':conflicts,'error':problem[0] if problem else None,
            'recovery':{'phase':recovery[0],'saved':recovery[1]} if recovery else None}

    @serialized
    def edit(self,name,expected,text):
        name=filename(name);current=self.read(name)
        if not isinstance(expected,str) or digest(current)!=expected:raise ValueError('File changed since it was loaded; reload before saving')
        if not isinstance(text,str) or len(text.encode())>LIMIT:raise ValueError('Edited file must be UTF-8 text up to 16 KiB')
        if name.endswith('.json'):
            try:json.loads(text,parse_constant=lambda _:(_ for _ in ()).throw(ValueError()))
            except (ValueError,TypeError):raise ValueError('Enter valid JSON before saving') from None
        with closing(self.db()) as db:
            row=db.execute('SELECT pending,phase FROM files WHERE name=?',(name,)).fetchone()
            if db.execute("SELECT 1 FROM resolutions WHERE name=? AND phase='changed-locally'",(name,)).fetchone():raise ValueError('Review the interrupted resolution before editing this file')
        if row and (row[0] or row[1]=='conflict'):raise ValueError('Finish the pending transfer or review its conflict before editing')
        if current!=text:
            if (self.root/name).exists():self.backup(name,current)
            self.root.mkdir(parents=True,exist_ok=True);atomic(self.root/name,text.encode())
            try:self.scan_file(name)
            except LocalFileError as error:self.problem(name,str(error))
        return self.inspect(name)

    @serialized
    def resolve(self,name,ident,expected,choice,text=None):
        name=filename(name);current=self.read(name)
        if digest(current)!=expected:raise ValueError('File changed since it was loaded; reload before resolving')
        if choice not in ('car','home','merge'):raise ValueError('Choose car, home or merged text')
        with closing(self.db()) as db:
            row=db.execute('SELECT incoming,resolved FROM conflicts WHERE id=? AND name=?',(ident,name)).fetchone()
            recovering=db.execute("SELECT 1 FROM resolutions WHERE id=? AND phase='changed-locally'",(ident,)).fetchone()
        if not row or row[0] is None or row[1] and not recovering:raise ValueError('A verified active conflict is required; reload the file')
        chosen=row[0] if choice=='car' else current if choice=='home' else text
        if not isinstance(chosen,str) or len(chosen.encode())>LIMIT:raise ValueError('Resolved text must fit the 16 KiB limit')
        if name.endswith('.json') and choice!='car':
            try:json.loads(chosen,parse_constant=lambda _:(_ for _ in ()).throw(ValueError()))
            except (TypeError,ValueError):raise ValueError('Enter valid JSON before resolving') from None
        with closing(self.db()) as db:previous=db.execute('SELECT chosen,phase FROM resolutions WHERE id=?',(ident,)).fetchone()
        if previous and previous[1]=='prepared':raise ValueError('A saved resolution is pending; reload after recovery')
        if previous and previous[0]!=chosen:self.backup(name,previous[0])
        with closing(self.db()) as db,db:
            db.execute("INSERT INTO resolutions VALUES(?,?,?,?,?,'prepared') ON CONFLICT(id) DO UPDATE SET chosen=excluded.chosen,local_hash=excluded.local_hash,car_hash=excluded.car_hash,phase='prepared'",(ident,name,chosen,expected,digest(row[0])))
        self.problem(name,'Saved resolution is waiting to finish')
        self.resume_resolutions()
        return self.inspect(name)

    @serialized
    def resume_resolutions(self):
        with closing(self.db()) as db:rows=db.execute("SELECT id,name,chosen,local_hash,car_hash FROM resolutions WHERE phase='prepared'").fetchall()
        for ident,name,chosen,local_hash,car_hash in rows:
            if name not in self.selected:continue
            self.resolving_name=name
            try:
                current=self.read(name);sha=digest(current)
                if sha not in (local_hash,car_hash,digest(chosen)):
                    with closing(self.db()) as db,db:db.execute("UPDATE resolutions SET phase='changed-locally' WHERE id=?",(ident,))
                    self.problem(name,'File changed during resolution; review both originals again');continue
                with closing(self.db()) as db:accepted=db.execute('SELECT 1 FROM applied WHERE id=?',(ident,)).fetchone()
                if not accepted:self.accept_conflict(name,ident,sha)
                current=self.read(name)
                if current!=chosen:self.edit(name,digest(current),chosen)
                with closing(self.db()) as db,db:
                    db.execute("UPDATE resolutions SET phase='complete' WHERE id=?",(ident,));db.execute('DELETE FROM problems WHERE name=?',(name,))
            except LocalFileError as error:self.problem(name,str(error))
            except OSError:self.problem(name,'Saved resolution cannot be written yet; retrying automatically')
            finally:self.resolving_name=None

    @serialized
    def accept_conflict(self,name,ident,expected):
        name=filename(name);current=self.read(name)
        if not isinstance(expected,str) or digest(current)!=expected:raise ValueError('File changed since it was loaded; reload before accepting')
        with closing(self.db()) as db:
            row=db.execute('SELECT doc,incoming,resolved,updated FROM conflicts WHERE id=? AND name=?',(ident,name)).fetchone()
            pending=db.execute('SELECT pending FROM files WHERE name=?',(name,)).fetchone()
        if not row:raise ValueError('Conflict is no longer available; reload the file')
        if row[2]:return self.inspect(name)
        doc=json.loads(row[0]);incoming=row[1]
        if incoming is None or digest(incoming)!=doc['target']:raise ValueError('A verified common baseline is missing; compare the originals manually')
        if current!=incoming:self.backup(name,current)
        self.root.mkdir(parents=True,exist_ok=True);atomic(self.root/name,incoming.encode())
        reply={'type':'file-receipt','source':self.role,'destination':self.peer,'name':name,'transferId':ident,'target':doc['target'],'status':'applied'}
        fingerprint=hashlib.sha256(json.dumps(doc,sort_keys=True).encode()).hexdigest()
        if pending and pending[0]:
            with closing(self.events.db()) as db,db:db.execute('UPDATE jobs SET done=2 WHERE id=?',(pending[0],))
        with closing(self.db()) as db,db:
            db.execute("UPDATE files SET confirmed=?,pending=NULL,doc=NULL,phase='applied',last=? WHERE name=?",(incoming,time.time(),name))
            db.execute('INSERT OR IGNORE INTO applied VALUES(?,?,?)',(ident,fingerprint,json.dumps(reply)))
            db.execute('UPDATE conflicts SET resolved=1 WHERE name=? AND updated<=?',(name,row[3]))
            db.execute('DELETE FROM problems WHERE name=?',(name,))
        self.events.enqueue('sync-receipt:'+ident[5:]+':'+uuid.uuid4().hex[:8],reply)
        return self.inspect(name)

    def status(self):
        with closing(self.db()) as db:
            rows={name:{'name':name,'phase':phase,'id':pending} for name,phase,pending in db.execute('SELECT name,phase,pending FROM files')}
            for name,detail in db.execute('SELECT name,detail FROM problems'):
                if name in self.selected:rows[name]=dict(rows.get(name,{'name':name,'id':None}),phase='blocked',error=detail)
            return list(rows.values())

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
    selected=home_selection(root,role)['files']
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
                    if result and result['doc'].get('type') in ('file','file-receipt'):files.receive_available(result['doc'],result['id'])
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
