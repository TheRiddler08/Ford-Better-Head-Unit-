"""Signed transport-neutral update journal. Radio and IP use identical frames.

Receipt ACKs prove durable chunk storage, never successful installation. Ed25519
signatures plus complete SHA256 validation gate staging; no partial file executes.
"""
import base64
from contextlib import contextmanager
import hashlib
import hmac
import json
import math
from pathlib import Path
import sqlite3
import struct
import time
import zlib
from control.storage_policy import bounded_db

HEADER=struct.Struct('>BB8sHH')
MANIFEST,DATA,ACK,APPLIED,ERROR=range(5)
MAX_FRAME=46
MAX_PAYLOAD=1024*1024
MAX_MANIFEST=4096
CHUNK=20
NAMESPACE=b'car-resumable-update-v1\0'


def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True).encode()
def digest(data):return hashlib.sha256(data).hexdigest()


def sign(document,keyfile):
    from cryptography.hazmat.primitives.serialization import load_pem_private_key
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    key=load_pem_private_key(Path(keyfile).read_bytes(),password=None)
    if not isinstance(key,Ed25519PrivateKey):raise ValueError('Ed25519 signing key required')
    body=dict(document);body['signature']=base64.b64encode(key.sign(NAMESPACE+canonical(document))).decode()
    return body


def verify(document,public_key):
    from cryptography.hazmat.primitives.serialization import load_pem_public_key
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    expected={'v','device','role','sequence','path','base','target','sha256','size','chunk','signature'}
    if not isinstance(document,dict) or set(document)!=expected:raise ValueError('Invalid update descriptor')
    body={key:value for key,value in document.items() if key!='signature'}
    if body['v']!=1 or body['role'] not in ('tiny','duet'):raise ValueError('Small source updates target Tiny/Duet; Pi system updates use the Git path')
    if type(body['sequence']) is not int or not 0<body['sequence']<2**63:raise ValueError('Invalid sequence')
    if type(body['size']) is not int or not 0<body['size']<=MAX_PAYLOAD:raise ValueError('Invalid update size')
    if type(body['chunk']) is not int or not 4<=body['chunk']<=24:raise ValueError('Invalid frame budget')
    if math.ceil(body['size']/body['chunk'])>65535:raise ValueError('Too many update chunks')
    for field in ('base','target','sha256'):
        if not isinstance(body[field],str) or len(body[field])!=64 or any(c not in '0123456789abcdef' for c in body[field]):raise ValueError('Invalid hash')
    from control.patches import ALLOWED
    if body['path'] not in ALLOWED:raise ValueError('Path not approved for a small code update')
    if body['path'].startswith('duet/')!=(body['role']=='duet'):raise ValueError('Path belongs to another device role')
    key=load_pem_public_key(Path(public_key).read_bytes())
    if not isinstance(key,Ed25519PublicKey):raise ValueError('Ed25519 public key required')
    try:key.verify(base64.b64decode(document['signature'],validate=True),NAMESPACE+canonical(body))
    except Exception as exc:raise ValueError('Update descriptor signature failed') from exc
    return body


def encode(kind,ident,index,count,payload,key):
    if len(key)<32 or kind not in (MANIFEST,DATA,ACK,APPLIED,ERROR):raise ValueError('Invalid frame authentication/type')
    if not 0<=index<count<=65535 or len(payload)>24:raise ValueError('Invalid frame shape')
    header=HEADER.pack(2,kind,bytes.fromhex(ident[:16]),index,count)
    return header+hmac.new(key,header+payload,hashlib.sha256).digest()[:8]+payload


def decode(frame,key):
    if len(key)<32 or not HEADER.size+8<=len(frame)<=MAX_FRAME:raise ValueError('Invalid update frame size/key')
    version,kind,short,index,count=HEADER.unpack(frame[:HEADER.size]);payload=frame[HEADER.size+8:]
    if version!=2 or kind not in (MANIFEST,DATA,ACK,APPLIED,ERROR) or not 0<=index<count<=65535:raise ValueError('Invalid update frame header')
    expected=hmac.new(key,frame[:HEADER.size]+payload,hashlib.sha256).digest()[:8]
    if not hmac.compare_digest(expected,frame[HEADER.size:HEADER.size+8]):raise ValueError('Update frame authentication failed')
    return kind,short.hex(),index,count,payload


class Journal:
    def __init__(self,path):
        self.path=Path(path);self.path.parent.mkdir(parents=True,exist_ok=True)
        with self.db() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, short TEXT UNIQUE, doc TEXT, direction TEXT, phase TEXT, updated REAL, error TEXT);
            CREATE TABLE IF NOT EXISTS parts(id TEXT, kind INTEGER, idx INTEGER, count INTEGER, data BLOB, sent REAL DEFAULT 0, PRIMARY KEY(id,kind,idx));
            CREATE TABLE IF NOT EXISTS receipts(id TEXT, kind INTEGER, idx INTEGER, PRIMARY KEY(id,kind,idx));
            CREATE TABLE IF NOT EXISTS installed(device TEXT, role TEXT, path TEXT, sequence INTEGER, target TEXT, PRIMARY KEY(device,role,path));
            CREATE TABLE IF NOT EXISTS snapshots(device TEXT,role TEXT,path TEXT,sha TEXT,source TEXT,updated REAL,PRIMARY KEY(device,role,path));
            CREATE TABLE IF NOT EXISTS budgets(name TEXT,day INTEGER,count INTEGER,last REAL,PRIMARY KEY(name,day));
            CREATE TABLE IF NOT EXISTS events(id TEXT,type TEXT,transport TEXT,idx INTEGER,ts REAL);
            CREATE INDEX IF NOT EXISTS update_event_identity ON events(id,ts);
            ''')
        self.path.chmod(0o600)
    @contextmanager
    def db(self):
        db=bounded_db(sqlite3.connect(self.path,timeout=20))
        db.execute('PRAGMA journal_mode=WAL');db.execute('PRAGMA synchronous=FULL')
        try:
            with db:yield db
        finally:db.close()
    def outgoing(self,document,payload,public_key):
        body=verify(document,public_key)
        if len(payload)!=body['size'] or digest(payload)!=body['sha256']:raise ValueError('Payload does not match signed descriptor')
        ident=digest(canonical(document));packed=zlib.compress(canonical(document),9)
        if len(packed)>MAX_MANIFEST:raise ValueError('Manifest too large')
        with self.db() as db:
            db.execute('INSERT OR IGNORE INTO jobs VALUES(?,?,?,?,?,?,?)',(ident,ident[:16],canonical(document).decode(),'out','queued',time.time(),None))
            for kind,data,size in ((MANIFEST,packed,body['chunk']),(DATA,payload,body['chunk'])):
                count=math.ceil(len(data)/size)
                for index in range(count):db.execute('INSERT OR IGNORE INTO parts(id,kind,idx,count,data) VALUES(?,?,?,?,?)',(ident,kind,index,count,data[index*size:(index+1)*size]))
        return ident
    def next(self,ident,key,retry_after=60):
        with self.db() as db:
            phase=db.execute('SELECT phase FROM jobs WHERE id=? AND direction=?',(ident,'out')).fetchone()
            if not phase or phase[0] in ('applied','failed'):return None
            row=db.execute('''SELECT p.kind,p.idx,p.count,p.data FROM parts p LEFT JOIN receipts r ON p.id=r.id AND p.kind=r.kind AND p.idx=r.idx
                WHERE p.id=? AND r.id IS NULL AND p.sent<=? ORDER BY p.kind,p.sent,p.idx LIMIT 1''',(ident,time.time()-retry_after)).fetchone()
            if not row:
                # All chunks received is not installation success. Re-send the
                # final data frame periodically to solicit the applied receipt.
                pending=db.execute('SELECT COUNT(*) FROM parts p LEFT JOIN receipts r ON p.id=r.id AND p.kind=r.kind AND p.idx=r.idx WHERE p.id=? AND r.id IS NULL',(ident,)).fetchone()[0]
                if pending:return None
                row=db.execute('SELECT kind,idx,count,data FROM parts WHERE id=? AND kind=1 ORDER BY idx DESC LIMIT 1',(ident,)).fetchone()
                sent=db.execute('SELECT sent FROM parts WHERE id=? AND kind=? AND idx=?',(ident,row[0],row[1])).fetchone()[0]
                if sent>time.time()-retry_after:return None
            kind,index,count,data=row
            # Never advance to data until every manifest receipt exists.
            if kind==DATA and db.execute('SELECT COUNT(*) FROM parts p LEFT JOIN receipts r ON p.id=r.id AND p.kind=r.kind AND p.idx=r.idx WHERE p.id=? AND p.kind=0 AND r.id IS NULL',(ident,)).fetchone()[0]:return None
            return encode(kind,ident,index,count,data,key)
    def mark_sent(self,frame,key,transport="unknown"):
        kind,short,index,_,_=decode(frame,key)
        with self.db() as db:
            db.execute('UPDATE parts SET sent=? WHERE id=(SELECT id FROM jobs WHERE short=?) AND kind=? AND idx=?',(time.time(),short,kind,index))
            db.execute('INSERT INTO events VALUES(?,?,?,?,?)',(short,'sent:'+str(kind),transport,index,time.time()))
    def receive(self,frame,key,public_key,device,role,transport="unknown"):
        kind,short,index,count,payload=decode(frame,key)
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT id,doc,direction,phase FROM jobs WHERE short=?',(short,)).fetchone()
            db.execute('INSERT INTO events VALUES(?,?,?,?,?)',(short,'received:'+str(kind),transport,index,time.time()))
            if kind in (ACK,APPLIED,ERROR):
                if not row or row[2]!='out':return None
                ident=row[0]
                if row[3] in ('applied','failed'):return None
                if kind==ACK:
                    if payload not in (b'm',b'd',b'M'):raise ValueError('Unknown chunk receipt')
                    if payload==b'M':
                        total=db.execute('SELECT COUNT(*) FROM parts WHERE id=? AND kind=0',(ident,)).fetchone()[0]
                        if index!=0 or count!=total:raise ValueError('Manifest receipt count mismatch')
                        db.execute('INSERT OR IGNORE INTO receipts SELECT id,kind,idx FROM parts WHERE id=? AND kind=0',(ident,))
                        return None
                    source=MANIFEST if payload==b'm' else DATA
                    existing=db.execute('SELECT count FROM parts WHERE id=? AND kind=? AND idx=?',(ident,source,index)).fetchone()
                    if not existing or existing[0]!=count:raise ValueError('Receipt does not match sent chunk')
                    db.execute('INSERT OR IGNORE INTO receipts VALUES(?,?,?)',(ident,source,index))
                elif kind==APPLIED:db.execute('UPDATE jobs SET phase=?,updated=? WHERE id=?',('applied',time.time(),ident))
                else:db.execute('UPDATE jobs SET phase=?,error=? WHERE id=?',('failed',payload.decode(errors='replace'),ident))
                return None
            if row and row[2]!='in':raise ValueError('Transfer direction collision')
            if not row:
                if kind!=MANIFEST:raise ValueError('Signed manifest required before data')
                if db.execute("SELECT COUNT(*) FROM jobs WHERE direction='in' AND phase NOT IN ('applied','failed','ignored')").fetchone()[0]>=8:raise ValueError('Too many partial updates')
                db.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?,?)',(short,short,None,'in','manifest',time.time(),None))
                row=(short,None,'in','manifest')
            ident,doc,_,phase=row
            if phase=='ignored':return None
            if phase=='failed':return encode(ERROR,ident,0,1,b'update-failed',key)
            if phase=='applied':return encode(APPLIED,ident,0,1,b'applied',key)
            if kind==MANIFEST:
                if count>math.ceil(MAX_MANIFEST/4):raise ValueError('Manifest fragment count exceeds limit')
            elif not doc:raise ValueError('Verified manifest required')
            else:
                body=json.loads(doc)
                if count!=math.ceil(body['size']/body['chunk']):raise ValueError('Incorrect payload fragment count')
                expected=min(body['chunk'],body['size']-index*body['chunk'])
                if len(payload)!=expected:raise ValueError('Incorrect payload fragment size')
            other=db.execute('SELECT data,count FROM parts WHERE id=? AND kind=? AND idx=?',(ident,kind,index)).fetchone()
            if other and (other[0]!=payload or other[1]!=count):raise ValueError('Conflicting fragment')
            counts=db.execute('SELECT DISTINCT count FROM parts WHERE id=? AND kind=?',(ident,kind)).fetchall()
            if counts and counts!=[(count,)]:raise ValueError('Inconsistent fragment count')
            db.execute('INSERT OR IGNORE INTO parts(id,kind,idx,count,data) VALUES(?,?,?,?,?)',(ident,kind,index,count,payload))
            db.execute('UPDATE jobs SET updated=? WHERE id=?',(time.time(),ident))
            received=db.execute('SELECT COUNT(*) FROM parts WHERE id=? AND kind=?',(ident,kind)).fetchone()[0]
            if received==count:
                data=b''.join(r[0] for r in db.execute('SELECT data FROM parts WHERE id=? AND kind=? ORDER BY idx',(ident,kind)))
                if kind==MANIFEST:
                    decoder=zlib.decompressobj();raw=decoder.decompress(data,MAX_MANIFEST+1)
                    if len(raw)>MAX_MANIFEST or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:raise ValueError('Manifest expands beyond limit')
                    document=json.loads(raw);body=verify(document,public_key)
                    full=digest(canonical(document))
                    if full[:16]!=short:raise ValueError('Update identity mismatch')
                    if body['device']!=device or body['role']!=role:
                        db.execute('UPDATE jobs SET phase=?,doc=? WHERE id=?',('ignored',canonical(document).decode(),ident))
                        db.execute('DELETE FROM parts WHERE id=?',(ident,))
                        return None
                    old=db.execute('SELECT sequence,target FROM installed WHERE device=? AND role=? AND path=?',(device,role,body['path'])).fetchone()
                    if old and body['sequence']<=old[0] and body['target']!=old[1]:raise ValueError('Replayed/superseded update')
                    # Keep the short internal row key stable across both transports.
                    db.execute('UPDATE jobs SET doc=?,phase=? WHERE id=?',(canonical(document).decode(),phase if phase not in ('manifest','receiving') else 'receiving',ident))
                elif len(data)!=body['size'] or digest(data)!=body['sha256']:raise ValueError('Complete payload checksum mismatch')
                else:db.execute('UPDATE jobs SET phase=? WHERE id=?',(phase if phase in ('ready','staged') else 'ready',ident))
            if kind==MANIFEST:
                # An unverified recipient must never ACK fragments intended for
                # another role. One receipt confirms the entire signed manifest.
                return encode(ACK,short,0,count,b'M',key) if received==count else None
            return encode(ACK,short,index,count,b'd',key)
    def ready(self):
        with self.db() as db:
            rows=db.execute("SELECT id,doc FROM jobs WHERE direction='in' AND phase='ready' ORDER BY updated").fetchall()
            return [(ident,json.loads(doc),b''.join(r[0] for r in db.execute('SELECT data FROM parts WHERE id=? AND kind=1 ORDER BY idx',(ident,)))) for ident,doc in rows]
    def applied(self,ident):
        with self.db() as db:
            document=json.loads(db.execute('SELECT doc FROM jobs WHERE id=?',(ident,)).fetchone()[0])
            db.execute('UPDATE jobs SET phase=?,updated=? WHERE id=?',('applied',time.time(),ident))
            db.execute('INSERT INTO installed VALUES(?,?,?,?,?) ON CONFLICT(device,role,path) DO UPDATE SET sequence=MAX(installed.sequence,excluded.sequence),target=excluded.target',(document['device'],document['role'],document['path'],document['sequence'],document['target']))
    def phase(self,ident,phase,error=None):
        with self.db() as db:db.execute('UPDATE jobs SET phase=?,error=?,updated=? WHERE id=?',(phase,error,time.time(),ident))
    def status(self):
        with self.db() as db:
            return [{'id':ident,'role':json.loads(doc)['role'] if doc else None,'path':json.loads(doc)['path'] if doc else None,'phase':phase,'error':error,
                     'received':db.execute('SELECT COUNT(*) FROM parts WHERE id=?',(ident,)).fetchone()[0],
                     'total':db.execute('SELECT COUNT(*) FROM parts WHERE id=?',(ident,)).fetchone()[0],
                     'lastTransport':(db.execute('SELECT transport FROM events WHERE id=? ORDER BY ts DESC LIMIT 1',(ident[:16],)).fetchone() or [None])[0],
                     'acknowledged':db.execute('SELECT COUNT(*) FROM receipts WHERE id=?',(ident,)).fetchone()[0]}
                    for ident,doc,phase,error in db.execute('SELECT id,doc,phase,error FROM jobs ORDER BY updated DESC LIMIT 32')]
    def budget(self,name,spacing,limit):
        day=int(time.time()//86400)
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT count,last FROM budgets WHERE name=? AND day=?',(name,day)).fetchone()
            if row and (row[0]>=limit or time.time()-row[1]<spacing):return False
            db.execute('INSERT OR REPLACE INTO budgets VALUES(?,?,?,?)',(name,day,(row[0]+1) if row else 1,time.time()))
            return True
