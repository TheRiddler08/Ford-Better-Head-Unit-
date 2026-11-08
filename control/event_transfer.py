"""Durable small catalog messages using the existing authenticated radio frames.

Only metadata (not video) enters this airtime-limited queue. The radio adapter
adds libsodium encryption before sending any of these application frames.
"""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import time
from control.lorawan_protocol import HEADER,Assembler,split_message,authenticate_application_frame,canonical


class Events:
    def __init__(self,path,key,destination=None,types=('dashcam','gps','message')):
        self.destination=destination
        self.types=types
        self.path=Path(path);self.path.parent.mkdir(parents=True,exist_ok=True);self.key=key
        with closing(self.db()) as db,db:db.executescript('''
          CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,doc TEXT,cursor INTEGER DEFAULT 0,done INTEGER DEFAULT 0);
          CREATE TABLE IF NOT EXISTS parts(identity TEXT,idx INTEGER,frame BLOB,PRIMARY KEY(identity,idx));
          CREATE TABLE IF NOT EXISTS received(id TEXT PRIMARY KEY,doc TEXT,ts REAL);
          CREATE TABLE IF NOT EXISTS replies(id TEXT PRIMARY KEY,frames TEXT,cursor INTEGER DEFAULT 0);
        ''')
    def db(self):
        from control.storage_policy import bounded_db
        return bounded_db(sqlite3.connect(self.path,timeout=10))
    def enqueue(self,ident,doc):
        if len(canonical(doc))>(1600 if doc.get('type')=='message' else 800):raise ValueError('Catalog message exceeds radio budget')
        with closing(self.db()) as db,db:
            old=db.execute('SELECT doc FROM jobs WHERE id=?',(ident,)).fetchone()
            if old and json.loads(old[0])!=doc:raise ValueError('Event identity conflicts with saved content')
            if doc.get('type')=='message' and not old and db.execute("SELECT COUNT(*) FROM jobs WHERE done=0 AND json_extract(doc,'$.type')='message'").fetchone()[0]>=100:
                raise ValueError('Message queue full; wait for delivery')
            db.execute('INSERT OR IGNORE INTO jobs(id,doc) VALUES(?,?)',(ident,json.dumps(doc)))
    def outgoing(self,ack=False,kinds=None):
        with closing(self.db()) as db:
            if ack:
                row=db.execute('SELECT id,frames,cursor FROM replies ORDER BY id LIMIT 1').fetchone()
                if not row:return None
                return bytes.fromhex(json.loads(row[1])[row[2]])
            clause='';args=[]
            if kinds:
                clause=' AND json_extract(doc,\'$.type\') IN ('+','.join('?' for _ in kinds)+')';args=list(kinds)
            row=db.execute("SELECT id,doc,cursor FROM jobs WHERE done=0"+clause+" ORDER BY CASE WHEN json_extract(doc,'$.type')='message' THEN 0 ELSE 1 END,cursor,id LIMIT 1",args).fetchone()
        if not row:return None
        frames=split_message('status',1,{'catalog':json.loads(row[1]),'id':row[0]},self.key)
        return frames[row[2]%len(frames)]
    def sent(self,raw,ack=False):
        with closing(self.db()) as db,db:
            if ack:
                row=db.execute('SELECT id,frames,cursor FROM replies ORDER BY id LIMIT 1').fetchone()
                if not row or bytes.fromhex(json.loads(row[1])[row[2]])!=raw:return
                if row[2]+1>=len(json.loads(row[1])):db.execute('DELETE FROM replies WHERE id=?',(row[0],))
                else:db.execute('UPDATE replies SET cursor=cursor+1 WHERE id=?',(row[0],))
            else:
                for row in db.execute('SELECT id,doc,cursor FROM jobs WHERE done=0').fetchall():
                    frames=split_message('status',1,{'catalog':json.loads(row[1]),'id':row[0]},self.key)
                    if frames[row[2]%len(frames)]==raw:
                        db.execute('UPDATE jobs SET cursor=cursor+1 WHERE id=?',(row[0],));return
    def receive(self,raw):
        authenticate_application_frame(raw,self.key)
        _,kind,seq,index,count,digest,*_=HEADER.unpack(raw[:HEADER.size])
        if kind!=6:return
        if count>64:raise ValueError('Catalog radio fragment count exceeds budget')
        ident=str(seq)+':'+digest.hex()
        with closing(self.db()) as db,db:
            if not db.execute('SELECT 1 FROM parts WHERE identity=?',(ident,)).fetchone() and db.execute('SELECT COUNT(DISTINCT identity) FROM parts').fetchone()[0]>=64:raise ValueError('Catalog receive queue full')
            old=db.execute('SELECT frame FROM parts WHERE identity=? AND idx=?',(ident,index)).fetchone()
            if old and old[0]!=raw:raise ValueError('Conflicting catalog fragment')
            db.execute('INSERT OR IGNORE INTO parts VALUES(?,?,?)',(ident,index,raw))
            parts=db.execute('SELECT frame FROM parts WHERE identity=? ORDER BY idx',(ident,)).fetchall()
            if len(parts)!=count:return
            assembler=Assembler(self.key);result=None
            for part in parts:result=assembler.accept(part[0])
            doc=result['payload']
            if 'received' in doc:
                db.execute('UPDATE jobs SET done=1 WHERE id=? AND done=0',(doc['received'],))
            else:
                event=doc['id'];catalog=doc['catalog']
                if not isinstance(event,str) or len(event)>100 or not isinstance(catalog,dict) or catalog.get('type') not in ('dashcam','gps','message'):raise ValueError('Invalid catalog message')
                if catalog['type'] not in self.types:
                    db.execute('DELETE FROM parts WHERE identity=?',(ident,));return
                if catalog.get('type')=='message':
                    from control.messages import validate
                    validate(catalog)
                    if self.destination and catalog['destination']!=self.destination:
                        db.execute('DELETE FROM parts WHERE identity=?',(ident,));return
                old=db.execute('SELECT doc FROM received WHERE id=?',(event,)).fetchone()
                if old and json.loads(old[0])!=catalog:raise ValueError('Received identity conflicts with saved content')
                db.execute('INSERT OR IGNORE INTO received VALUES(?,?,?)',(event,json.dumps(catalog),time.time()))
                frames=[frame.hex() for frame in split_message('status',1,{'received':event},self.key)]
                db.execute('INSERT OR IGNORE INTO replies(id,frames) VALUES(?,?)',(event,json.dumps(frames)))
            db.execute('DELETE FROM parts WHERE identity=?',(ident,))
            return doc.get('catalog') if 'catalog' in doc else {'received':doc['received']}

    def completed_frames(self,ident=None,raw=None):
        with closing(self.db()) as db:
            rows=db.execute('SELECT id,doc FROM jobs WHERE done IN (1,2)'+(' AND id=?' if ident else ''),() if ident is None else (ident,)).fetchall()
        for name,doc in rows:
            frames=split_message('status',1,{'catalog':json.loads(doc),'id':name},self.key)
            if raw is None or raw in frames:return frames if raw is None else [raw]
        return []
    def catalog(self,kind='dashcam'):
        with closing(self.db()) as db:
            rows=db.execute('SELECT id,doc FROM received ORDER BY ts DESC')
            result=[]
            for row in rows:
                doc=json.loads(row[1])
                if doc.get('type')==kind:result.append(dict(doc,id=row[0]))
                if len(result)>=200:break
            return result
