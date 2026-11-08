"""Home source-copy editor and shared Wi-Fi/LoRaWAN software queue."""
import json
import fcntl
import os
from pathlib import Path
import secrets
import time
from control import patches
from control.update_transfer import Journal,canonical,digest,sign,decode,ACK,APPLIED,ERROR


class Software:
    def __init__(self,root):
        self.root=Path(root);self.root.mkdir(parents=True,exist_ok=True);self.root.chmod(0o700)
        self.journal=Journal(self.root/'software.sqlite3')
        self.private=self.root/'update-signing.pem';self.public=self.root/'update-signing.pub'
        self.keyfile=self.root/'update-link.key'
        with (self.root/'keys.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX)
            if not self.private.exists():
                from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
                from cryptography.hazmat.primitives.serialization import Encoding,PrivateFormat,NoEncryption
                self.write(self.private,Ed25519PrivateKey.generate().private_bytes(Encoding.PEM,PrivateFormat.PKCS8,NoEncryption()),0o600)
            from cryptography.hazmat.primitives.serialization import load_pem_private_key,Encoding,PublicFormat
            public=load_pem_private_key(self.private.read_bytes(),None).public_key().public_bytes(Encoding.PEM,PublicFormat.SubjectPublicKeyInfo)
            self.write(self.public,public,0o644)
            if not self.keyfile.exists():
                value=os.getenv('CAR_LINK_KEY') or secrets.token_hex(32)
                if len(bytes.fromhex(value))<32:raise ValueError('Application link key must have at least 32 bytes')
                self.write(self.keyfile,value.encode(),0o600)
    @staticmethod
    def write(path,data,mode):
        import tempfile
        if path.exists() and path.read_bytes()==data:
            path.chmod(mode);return
        with tempfile.NamedTemporaryFile(dir=path.parent,delete=False) as file:
            file.write(data);file.flush();os.fsync(file.fileno());temp=Path(file.name)
        temp.chmod(mode);os.replace(temp,path)
    def key(self):
        configured=os.getenv('CAR_LINK_KEY','')
        return bytes.fromhex(configured) if configured else bytes.fromhex(self.keyfile.read_text())
    def inventory(self,device,role,rows):
        if role not in ('tiny','duet','pi','laptop') or not isinstance(rows,list) or len(rows)>16:raise ValueError('Invalid inventory')
        with self.journal.db() as db:
            for row in rows:
                path=row.get('path');source=row.get('source')
                if path not in patches.ALLOWED or not isinstance(source,str) or len(source.encode())>256000:raise ValueError('Invalid source snapshot')
                if path.startswith('duet/')!=(role=='duet'):raise ValueError('Wrong source role')
                sha=digest(source.encode())
                if sha!=row.get('sha256'):raise ValueError('Snapshot digest mismatch')
                db.execute('INSERT OR REPLACE INTO snapshots VALUES(?,?,?,?,?,?)',(device,role,path,sha,source,time.time()))
    def source(self,device,role,path):
        if path not in patches.ALLOWED:raise ValueError('Source path is not editable over this link')
        with self.journal.db() as db:row=db.execute('SELECT sha,source,updated FROM snapshots WHERE device=? AND role=? AND path=?',(device,role,path)).fetchone()
        if not row:raise ValueError('No current device copy yet; connect the device over Wi-Fi once')
        return {'path':path,'base':row[0],'source':row[1],'reportedAt':row[2],'role':role}
    def publish(self,device,role,path,base,modified):
        with (self.root/'publish.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX)
            return self._publish(device,role,path,base,modified)
    def _publish(self,device,role,path,base,modified):
        source=self.source(device,role,path)
        if base!=source['base']:raise ValueError('The current device copy changed; reload before editing')
        if modified==source['source']:return {'id':None,'bytes':0,'frames':0,'phase':'already-current'}
        if path.endswith('.py'):
            try:compile(modified,path,'exec')
            except SyntaxError as exc:raise ValueError('Source syntax error on line '+str(exc.lineno)) from exc
        if path.endswith('.sh'):
            import subprocess
            try:subprocess.run(['bash','-n'],input=modified.encode(),check=True,timeout=10,capture_output=True)
            except subprocess.SubprocessError as exc:raise ValueError('Shell syntax check failed') from exc
        with self.journal.db() as db:
            pending=db.execute("SELECT doc FROM jobs WHERE direction='out' AND phase NOT IN ('applied','failed')").fetchall()
            if any((d:=json.loads(row[0])).get('device')==device and d['role']==role and d['path']==path for row in pending):raise ValueError('A change for this file is still pending; finish it before publishing another')
            sequences=[json.loads(row[0])['sequence'] for row in db.execute("SELECT doc FROM jobs WHERE direction='out'")]
        delta=patches.sign_delta(patches.make_delta(path,source['source'],modified),self.private)
        payload=canonical(delta)
        document=sign({'v':1,'device':device,'role':role,'sequence':max([int(time.time()*1000)]+[value+1 for value in sequences]),
                       'path':path,'base':base,'target':delta['target'],'sha256':digest(payload),'size':len(payload),'chunk':int(os.getenv('CAR_UPDATE_CHUNK_BYTES','20'))},self.private)
        ident=self.journal.outgoing(document,payload,self.public)
        return {'id':ident,'bytes':len(payload),'frames':(len(payload)+document['chunk']-1)//document['chunk'],'phase':'queued'}
    def outgoing(self,device,role=None,limit=8,mark=True,transport='ip'):
        if transport not in ('ip','lorawan'):raise ValueError('Invalid update transport')
        result=[]
        with self.journal.db() as db:
            rows=db.execute("SELECT j.id,j.doc FROM jobs j LEFT JOIN delivery d ON j.id=d.id WHERE direction='out' AND phase NOT IN ('applied','failed') ORDER BY COALESCE(d.priority,0) DESC,j.updated").fetchall()
        for ident,doc in rows:
            document=json.loads(doc)
            if document['device']!=device or role and document['role']!=role:continue
            if transport=='lorawan' and self.journal.ip_recent(device,document['role']):continue
            while len(result)<limit:
                frame=self.journal.next(ident,self.key(),transport=transport)
                if frame is None:break
                result.append(frame.hex())
                if mark:self.journal.mark_sent(frame,self.key(),transport)
                else:break
            if len(result)>=limit:break
        return result

    def delivery(self,device,ident,action,revision,value=None):
        """Change only local scheduling; preserve descriptor, identity and ACKs."""
        if not isinstance(ident,str) or len(ident)!=64 or any(c not in '0123456789abcdef' for c in ident):raise ValueError('Invalid transfer ID')
        if type(revision) is not int or revision<0:raise ValueError('Load the current transfer revision first')
        with self.journal.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute("SELECT doc,phase FROM jobs WHERE id=? AND direction='out'",(ident,)).fetchone()
            if not row or json.loads(row[0]).get('device')!=device:raise ValueError('Unknown outgoing transfer for this car')
            if row[1] in ('applied','failed'):raise ValueError('Transfer already finished; load the latest device copy for a new change')
            current=db.execute('SELECT paused,policy,priority,revision FROM delivery WHERE id=?',(ident,)).fetchone() or (0,'auto',0,0)
            paused,policy,priority,version=current
            if revision!=version:raise ValueError('Transfer controls changed; refresh before trying again')
            if action=='pause':paused=1
            elif action=='resume':paused=0
            elif action=='policy' and value in ('auto','ip-only'):policy=value
            elif action=='priority' and value in ('normal','high'):priority=int(value=='high')
            elif action=='retry':
                # Re-solicit an applied receipt once all data is acknowledged.
                # Do not erase receipts, reset signatures or bypass radio quotas.
                db.execute('''UPDATE parts SET sent=0 WHERE id=? AND (
                    NOT EXISTS(SELECT 1 FROM receipts r WHERE r.id=parts.id AND r.kind=parts.kind AND r.idx=parts.idx)
                    OR (kind=1 AND idx=(SELECT MAX(idx) FROM parts WHERE id=? AND kind=1)))''',(ident,ident))
            else:raise ValueError('Unknown delivery action or value')
            db.execute('INSERT INTO delivery VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET paused=excluded.paused,policy=excluded.policy,priority=excluded.priority,revision=excluded.revision,updated=excluded.updated',
                       (ident,paused,policy,priority,version+1,time.time()))
        return {'id':ident,'paused':bool(paused),'policy':policy,'priority':'high' if priority else 'normal','revision':version+1}
    def receipts(self,device,role,frames,transport="ip"):
        if not isinstance(frames,list) or len(frames)>256:raise ValueError('Too many receipts')
        for frame in frames:
            raw=bytes.fromhex(frame);kind,short,*_=decode(raw,self.key())
            if kind not in (ACK,APPLIED,ERROR):raise ValueError('Only receipts accepted from device')
            with self.journal.db() as db:row=db.execute('SELECT doc FROM jobs WHERE short=?',(short,)).fetchone()
            if not row:continue
            document=json.loads(row[0])
            if document['device']!=device or document['role']!=role:raise ValueError('Receipt belongs to another role')
            self.journal.receive(raw,self.key(),self.public,device,role,transport)
