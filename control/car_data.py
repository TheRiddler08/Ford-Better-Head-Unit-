"""Local recording spool and adapters to the existing MooseFS shared archive.

No distributed filesystem or camera protocol is implemented here. Packaged
MooseFS owns chunking/replication. Active SQLite files stay on local disks.
"""
from contextlib import closing, contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import socket
import sqlite3
import subprocess
import tempfile
import time
from control.update_agent import atomic

ROLES=('tiny','duet','pi','laptop')
VIDEO={'.mp4','.mov','.mkv','.ts'}
CAMERA={'manufacturer':'Botslab','model':'G980H','channels':4,
        'access':'pending-verified-download-protocol'}
OBJECT=re.compile(r'^[0-9a-f]{64}\.(?:mp4|mov|mkv|ts|sqlite3|json|gpx|geojson|txt)$')
RESERVE=2*1024**3


@contextmanager
def shared_objects(mount,role):
    """Pin the real FUSE mount so a disconnect cannot write into the SD mountpoint."""
    if role not in ROLES:raise ValueError('Unknown car role')
    fd=os.open(mount,os.O_RDONLY|os.O_DIRECTORY)
    opened=[fd]
    try:
        info=Path('/proc/self/fdinfo/'+str(fd)).read_text()
        ident=next(line.split()[1] for line in info.splitlines() if line.startswith('mnt_id:'))
        valid=any(line.split()[0]==ident and line.split(' - ',1)[1].split()[0] in ('fuse.mfs','fuse.moosefs')
                  for line in Path('/proc/self/mountinfo').read_text().splitlines())
        if not valid:raise ValueError('Shared archive unavailable; Pi will not use SD for bulk data')
        for name in (role,'objects'):
            try:os.mkdir(name,dir_fd=fd)
            except FileExistsError:pass
            fd=os.open(name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd);opened.append(fd)
            child=Path('/proc/self/fdinfo/'+str(fd)).read_text()
            child_id=next(line.split()[1] for line in child.splitlines() if line.startswith('mnt_id:'))
            if child_id!=ident:raise ValueError('Archive directory leaves the shared filesystem')
        yield Path('/proc/self/fd')/str(fd)
    finally:
        for descriptor in reversed(opened):os.close(descriptor)


def shared_goal(target):
    if not shutil.which('mfssetgoal'):raise ValueError('MooseFS replication tool missing')
    fd=os.open(target,os.O_RDONLY|os.O_DIRECTORY)
    try:
        subprocess.run(['mfssetgoal','2','/proc/self/fd/'+str(fd)],pass_fds=(fd,),capture_output=True,check=True,timeout=15)
    finally:os.close(fd)


def mounted(path):
    """Never fall back to writing into an unmounted /car-data directory."""
    wanted=str(Path(path).resolve())
    for line in Path('/proc/self/mountinfo').read_text().splitlines():
        parts=line.split();separator=parts.index('-')
        if parts[4]==wanted and parts[separator+1] in ('fuse.mfs','fuse.moosefs'):return True
    return False


def sha(path):
    value=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for data in iter(lambda:stream.read(1024*1024),b''):value.update(data)
    return value.hexdigest()


def copy_verified(source,target):
    """Publish a complete immutable copy; never delete the original."""
    source=Path(source);target=Path(target)
    if source.is_symlink() or not source.is_file():raise ValueError('Regular source file required')
    expected=sha(source)
    target.parent.mkdir(parents=True,exist_ok=True)
    if target.exists():
        if target.is_symlink() or sha(target)!=expected:raise ValueError('Archive destination conflicts with source')
        return expected
    with tempfile.NamedTemporaryFile(dir=target.parent,delete=False) as output:
        pending=Path(output.name)
        try:
            with source.open('rb') as stream:shutil.copyfileobj(stream,output,1024*1024)
            output.flush();os.fsync(output.fileno())
            if sha(pending)!=expected:raise ValueError('Source changed during copy')
            pending.chmod(0o644);os.replace(pending,target)
            fd=os.open(target.parent,os.O_DIRECTORY)
            try:os.fsync(fd)
            finally:os.close(fd)
        finally:
            if pending.exists():pending.unlink()
    return expected


class Store:
    def __init__(self,state,role,mount='/car-data',storage_client=None):
        if role not in ROLES:raise ValueError('Unknown car role')
        self.state=Path(state);self.state.mkdir(parents=True,exist_ok=True)
        self.role=role;self.mount=Path(mount);self.objects=self.state/'objects'
        self.storage_client=role=='pi' if storage_client is None else storage_client
        if not self.storage_client:self.objects.mkdir(exist_ok=True)
        with closing(self.db()) as db,db:db.executescript('''
          CREATE TABLE IF NOT EXISTS objects(id TEXT PRIMARY KEY,name TEXT,kind TEXT,size INTEGER,
            created REAL,metadata TEXT,archived INTEGER DEFAULT 0);
          CREATE TABLE IF NOT EXISTS seen(path TEXT PRIMARY KEY,size INTEGER,mtime INTEGER,first REAL,done INTEGER DEFAULT 0);
          CREATE TABLE IF NOT EXISTS checkpoints(name TEXT PRIMARY KEY,stamp REAL);
        ''')
    def db(self):
        from control.storage_policy import bounded_db
        db=bounded_db(sqlite3.connect(self.state/'library.sqlite3',timeout=10));db.row_factory=sqlite3.Row
        db.execute('PRAGMA journal_mode=WAL');db.execute('PRAGMA synchronous=FULL');return db
    def add(self,path,kind,metadata=None):
        path=Path(path)
        ident=sha(path)+path.suffix.lower()
        if not OBJECT.fullmatch(ident):raise ValueError('Unsupported archive type')
        with closing(self.db()) as db:
            if db.execute('SELECT 1 FROM objects WHERE id=?',(ident,)).fetchone():return ident
        info={'name':path.name,'kind':kind,'size':path.stat().st_size,'created':time.time(),**(metadata or {})}
        if self.storage_client:
            with shared_objects(self.mount,self.role) as target:
                if shutil.disk_usage(target).free<path.stat().st_size+RESERVE:raise ValueError('Shared archive reserve reached')
                shared_goal(target)
                copy_verified(path,target/ident)
                if (target/(ident+'.json')).is_symlink():raise ValueError('Refusing symlinked archive metadata')
                atomic(target/(ident+'.json'),json.dumps(info).encode())
        else:
            if shutil.disk_usage(self.state).free<path.stat().st_size+RESERVE:raise ValueError('Local archive reserve reached')
            copy_verified(path,self.objects/ident)
            atomic(self.objects/(ident+'.json'),json.dumps(info).encode())
        with closing(self.db()) as db,db:db.execute('INSERT OR IGNORE INTO objects(id,name,kind,size,created,metadata,archived) VALUES(?,?,?,?,?,?,?)',
            (ident,info['name'],kind,info['size'],info['created'],json.dumps(info),int(self.storage_client)))
        return ident
    def ingest_camera(self,inbox,now=None):
        now=time.time() if now is None else now;inbox=Path(inbox).resolve()
        for path in sorted(inbox.rglob('*'))[:10000]:
            if path.is_symlink() or not path.is_file() or path.suffix.lower() not in VIDEO:continue
            if not path.resolve().is_relative_to(inbox):continue
            stat=path.stat()
            with closing(self.db()) as db,db:
                row=db.execute('SELECT * FROM seen WHERE path=?',(str(path),)).fetchone()
                if not row or row['size']!=stat.st_size or row['mtime']!=stat.st_mtime_ns:
                    db.execute('INSERT OR REPLACE INTO seen VALUES(?,?,?,?,0)',(str(path),stat.st_size,stat.st_mtime_ns,now));continue
                if row['done'] or now-row['first']<30 or now-stat.st_mtime<30:continue
            if not shutil.which('ffprobe'):continue
            reply=subprocess.run(['ffprobe','-v','error','-show_entries','format=duration:format_tags=creation_time:stream=codec_type,codec_name,width,height','-of','json',str(path)],capture_output=True,timeout=15)
            if reply.returncode:continue
            info=json.loads(reply.stdout)
            if not any(row.get('codec_type')=='video' for row in info.get('streams',[])):continue
            relative=path.relative_to(inbox)
            # Preserve user/camera channel directories without guessing filename codes.
            channel=relative.parts[0] if len(relative.parts)>1 else 'unspecified'
            self.add(path,'dashcam',{'probe':info,'camera':'Botslab G980H','channel':channel,
                                    'name':str(relative),'sourceMtime':stat.st_mtime,
                                    'cameraTime':info.get('format',{}).get('tags',{}).get('creation_time')})
            with closing(self.db()) as db,db:db.execute('UPDATE seen SET done=1 WHERE path=?',(str(path),))
    def snapshot(self,path,name,interval=3600):
        if self.storage_client:raise ValueError('Pi bulk snapshots belong on Tiny; no local SD spool')
        path=Path(path)
        if not path.is_file():return
        with closing(self.db()) as db:
            row=db.execute('SELECT stamp FROM checkpoints WHERE name=?',(name,)).fetchone()
        if row and time.time()-row[0]<interval:return
        if shutil.disk_usage(self.state).free<path.stat().st_size*2+RESERVE:raise ValueError('Snapshot disk reserve reached')
        with tempfile.TemporaryDirectory(dir=self.state) as temp:
            backup=Path(temp)/(name+'.sqlite3');deadline=time.monotonic()+30
            def progress(*_):
                if time.monotonic()>deadline:raise TimeoutError('Snapshot deferred: database busy')
            with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)) as source,closing(sqlite3.connect(backup)) as target:
                source.backup(target,pages=256,progress=progress,sleep=.01)
            self.add(backup,name+'-snapshot')
        with closing(self.db()) as db,db:db.execute('INSERT OR REPLACE INTO checkpoints VALUES(?,?)',(name,time.time()))
    def archive(self):
        if not mounted(self.mount):return 'waiting-for-moosefs'
        if self.storage_client:
            with closing(self.db()) as db:rows=db.execute('SELECT id FROM objects WHERE archived=0 ORDER BY created LIMIT 20').fetchall()
            if rows:
                with shared_objects(self.mount,self.role) as target:
                    shared_goal(target)
                    for row in rows:
                        ident=row['id'];copy_verified(self.objects/ident,target/ident)
                        copy_verified(self.objects/(ident+'.json'),target/(ident+'.json'))
                        with closing(self.db()) as db,db:db.execute('UPDATE objects SET archived=1 WHERE id=?',(ident,))
            return 'shared-client-connected-no-sd-contribution'
        # Pin the actual FUSE mount for every car role, including Tiny/Duet.
        # A disconnect between the initial check and a copy must never redirect
        # bulk writes into the empty local mountpoint directory.
        with shared_objects(self.mount,self.role) as target:
            shared_goal(target)
            with closing(self.db()) as db:rows=db.execute('SELECT id FROM objects WHERE archived=0 ORDER BY created LIMIT 20').fetchall()
            for row in rows:
                ident=row['id'];copy_verified(self.objects/ident,target/ident)
                copy_verified(self.objects/(ident+'.json'),target/(ident+'.json'))
                with closing(self.db()) as db,db:db.execute('UPDATE objects SET archived=1 WHERE id=?',(ident,))
        return 'shared-archive-connected'


def roots(home,role,mount='/car-data'):
    result={role:Path(home)/'.local/share/car-data/objects'}
    if mounted(mount):
        for other in ROLES:
            if other!=role:result[other]=Path(mount)/other/'objects'
    return result


def clips(home,role,after='',mount='/car-data'):
    result=[]
    for source,root in roots(home,role,mount).items():
        if not root.is_dir():continue
        for path in root.iterdir():
            ident=source+':'+path.name
            if ident<=after or not OBJECT.fullmatch(path.name) or path.suffix not in VIDEO or path.is_symlink():continue
            meta=path.with_name(path.name+'.json')
            try:
                if meta.stat().st_size>65536:continue
                value=json.loads(meta.read_text())
            except (OSError,ValueError):continue
            result.append({'id':ident,**value})
    result.sort(key=lambda row:row['id']);more=len(result)>200;result=result[:200]
    return {'items':result,'next':result[-1]['id'] if result else after,'hasMore':more,'cameraAccess':CAMERA}


def clip_path(home,role,ident,mount='/car-data'):
    source,filename=ident.split(':',1)
    if not OBJECT.fullmatch(filename) or Path(filename).suffix not in VIDEO:raise ValueError('Invalid recording ID')
    root=roots(home,role,mount).get(source)
    if root is None:raise FileNotFoundError('Recording device unavailable')
    path=root/filename
    if path.is_symlink() or not path.is_file():raise FileNotFoundError('Recording unavailable')
    return path


def recorder_paths(home):
    environment=Path('/etc/car-dashboard/dashboard.env');env={}
    if environment.exists():
        import shlex
        for line in environment.read_text().splitlines():
            if line.startswith(('CAR_DB=','CAR_GPS_DB=')):
                key,value=line.split('=',1);parts=shlex.split(value);env[key]=parts[0] if len(parts)==1 else value
    db=Path(env.get('CAR_DB',str(Path(home)/'car-console/obd-data/vehicle.sqlite3')))
    return db,Path(env.get('CAR_GPS_DB',str(db.with_name('gps.sqlite3'))))


def radio_catalog(store,home,role):
    from control.event_transfer import Events
    from control.update_agent import LAN,PORT
    key=Path(home)/'.config/car-dashboard/update-link.key'
    if not key.exists():return 'waiting-for-enrollment'
    events=Events(store.state/'radio-catalog.sqlite3',bytes.fromhex(key.read_text().strip()),destination=role,types=('dashcam','gps'))
    settings=Path('/etc/car-dashboard/update-link.json')
    gps_enabled=json.loads(settings.read_text()).get('gps_radio_enabled',False) if settings.exists() else False
    if role=='tiny' and gps_enabled:
        gps=recorder_paths(home)[1]
        with closing(events.db()) as db:
            pending=db.execute("SELECT COUNT(*) FROM jobs WHERE id LIKE 'gps:%' AND done=0").fetchone()[0]
            last=db.execute("SELECT MAX(id) FROM jobs WHERE id LIKE 'gps:%'").fetchone()[0]
        # One pending GPS sample limits radio backlog. The full track stays local.
        if not pending and gps.is_file():
            with closing(sqlite3.connect(gps.resolve().as_uri()+'?mode=ro',uri=True)) as db:
                db.row_factory=sqlite3.Row
                fix=db.execute('SELECT id,ts,lat,lon,mode FROM fixes ORDER BY id DESC LIMIT 1').fetchone()
            if fix and 0<=time.time()-fix['ts']<=60:
                ident='gps:tiny:'+str(int(fix['ts']//300)).zfill(16)
                if ident!=last:
                    events.enqueue(ident,{'type':'gps','source':'tiny','ts':fix['ts'],'lat':fix['lat'],'lon':fix['lon'],
                                          'gatewayMode':'unknown','gatewayModeAt':fix['ts']})
    with closing(store.db()) as db:rows=db.execute("SELECT id,metadata FROM objects WHERE kind='dashcam' ORDER BY created").fetchall()
    for ident,metadata in rows:
        info=json.loads(metadata)
        events.enqueue(role+':'+ident,{'type':'dashcam','source':role,'name':info['name'][:64],
                                      'size':info['size'],'importedAt':info['created'],'channel':info.get('channel','unspecified')})
    with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as sender:
        sender.setsockopt(socket.SOL_SOCKET,socket.SO_BROADCAST,1);sender.bind((LAN[role],0))
        raw=events.outgoing(ack=True) or events.outgoing(kinds=['dashcam','gps'] if gps_enabled else ['dashcam'])
        if raw:sender.sendto(b'U'+raw,('172.16.42.255',PORT))
    return 'queued-encrypted-catalog; gps-radio-enabled' if gps_enabled else 'queued-encrypted-catalog; gps-radio-deferred'


def main():
    config=json.loads(Path('/etc/car-dashboard/fleet.json').read_text());home=Path(config['home']);role=config['role']
    store=Store(home/'.local/share/car-data',role,storage_client=role=='pi' and config.get('pi_storage_client',True))
    inbox=home/'car-console/dashcam-inbox'
    if not store.storage_client:inbox.mkdir(parents=True,exist_ok=True)
    from control.update_agent import PORT,LAN
    receiver=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);receiver.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
    receiver.bind(('',PORT));receiver.setblocking(False)
    while True:
        status={'camera':CAMERA,'inbox':str(inbox),'updated':time.time()}
        try:
            status['storage']=store.archive()
            if not store.storage_client:store.ingest_camera(inbox)
            from control.event_transfer import Events
            key=home/'.config/car-dashboard/update-link.key'
            if key.exists():
                events=Events(store.state/'radio-catalog.sqlite3',bytes.fromhex(key.read_text().strip()),destination=role,types=('dashcam','gps'))
                while True:
                    try:data,peer=receiver.recvfrom(128)
                    except BlockingIOError:break
                    if peer[0] not in LAN.values():continue
                    if data[:3]==b'R\x01\x06':events.sent(data[1:])
                    elif data[:3]==b'D\x01\x06':events.receive(data[1:])
            status['radio']=radio_catalog(store,home,role)
            if role=='tiny':
                db,gps=recorder_paths(home)
                store.snapshot(db,'obd');store.snapshot(gps,'gps')
            for directory in (home/'car-console/routes',home/'car-console/scheduled-routes'):
                if directory.exists():
                    for path in sorted(directory.rglob('*'))[:1000]:
                        if path.is_file() and not path.is_symlink() and path.suffix.lower() in ('.json','.gpx','.geojson','.txt'):store.add(path,'route',{'relativePath':str(path.relative_to(directory))})
            for ui in (home/'.config/car-dashboard/ui-config.json',home/'.config/car-dashboard/layout.json',
                       Path('/etc/car-dashboard/ui-config.json'),Path('/etc/car-dashboard/layout.json')):
                if ui.exists() and ui.is_file() and not ui.is_symlink():store.add(ui,'dashboard')
            status['storage']=store.archive()
        except (OSError,ValueError,sqlite3.Error,subprocess.SubprocessError) as exc:status['storage']='pending';status['detail']=str(exc)[:256]
        atomic(store.state/'status.json',json.dumps(status).encode());time.sleep(60)


if __name__=='__main__':main()
