"""libsodium sealed boxes at the actual radio boundary, with durable framing.

Cryptography is PyNaCl's standard Curve25519/XSalsa20-Poly1305 SealedBox.
The inner application's HMAC/signature still authenticates the sender. Public
keys are pinned through the existing authenticated HTTPS/SSH enrollment path.
No plaintext downgrade is accepted. Framing is not a new cryptographic format.
"""
from contextlib import closing
import hashlib
import os
from pathlib import Path
import sqlite3
import struct
import time
from nacl.public import PrivateKey, PublicKey, SealedBox
from nacl.exceptions import CryptoError

HEADER=struct.Struct('>B6sBB')
VERSION=3
MAGIC=b'car-radio-v1\0'


def identity(path):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    try:
        fd=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    except FileExistsError:
        return PrivateKey(path.read_bytes())
    key=PrivateKey.generate()
    with os.fdopen(fd,'wb') as stream:stream.write(bytes(key));stream.flush();os.fsync(stream.fileno())
    return key


def pin(path,public):
    raw=bytes.fromhex(public)
    PublicKey(raw)
    from control.update_agent import atomic
    path=Path(path)
    if path.exists() and path.read_bytes()!=raw:raise ValueError('Pinned radio encryption key changed; trusted rotation required')
    atomic(path,raw)


class RadioBox:
    def __init__(self,private,peer,database,direction,max_bytes=46):
        if direction not in (b'U',b'D') or not HEADER.size<max_bytes<=242:raise ValueError('Invalid encrypted radio payload budget')
        self.key=identity(private);self.peer=Path(peer);self.direction=direction;self.max_bytes=max_bytes
        self.path=Path(database);self.path.parent.mkdir(parents=True,exist_ok=True)
        with self.db() as db:db.executescript('''
          CREATE TABLE IF NOT EXISTS sealed_out(id TEXT PRIMARY KEY, wire BLOB, next INTEGER DEFAULT 0);
          CREATE TABLE IF NOT EXISTS sealed_in(id BLOB, part INTEGER, total INTEGER, wire BLOB, ts REAL,
            PRIMARY KEY(id,part));
        ''')
    def db(self):
        # Closing is explicit at call sites; sqlite context alone only commits.
        return sqlite3.connect(self.path,timeout=15)
    def frames(self,raw):
        if not raw or len(raw)>512:raise ValueError('Application radio message too large')
        peer=PublicKey(self.peer.read_bytes()) # Missing pin fails closed.
        ident=hashlib.sha256(bytes(peer)+self.direction+bytes([self.max_bytes])+raw).hexdigest()
        with closing(self.db()) as db,db:
            db.execute('PRAGMA synchronous=FULL')
            row=db.execute('SELECT wire FROM sealed_out WHERE id=?',(ident,)).fetchone()
            wire=row[0] if row else SealedBox(peer).encrypt(MAGIC+self.direction+raw)
            if not row:db.execute('INSERT INTO sealed_out(id,wire) VALUES(?,?)',(ident,wire))
        size=self.max_bytes-HEADER.size;count=(len(wire)+size-1)//size
        if count>255:raise ValueError('Encrypted message exceeds regional fragment budget')
        short=hashlib.sha256(wire).digest()[:6]
        return ident,[HEADER.pack(VERSION,short,i,count)+wire[i*size:(i+1)*size] for i in range(count)]
    def next(self,raw):
        ident,frames=self.frames(raw)
        with closing(self.db()) as db:cursor=db.execute('SELECT next FROM sealed_out WHERE id=?',(ident,)).fetchone()[0]
        return frames[cursor%len(frames)]
    def sent(self,raw):
        ident,frames=self.frames(raw)
        with closing(self.db()) as db,db:
            db.execute('UPDATE sealed_out SET next=next+1 WHERE id=?',(ident,))
            cursor=db.execute('SELECT next FROM sealed_out WHERE id=?',(ident,)).fetchone()[0]
        return cursor%len(frames)==0
    def accept(self,frame):
        if not HEADER.size<len(frame)<=242:raise ValueError('Invalid encrypted frame size')
        version,ident,index,count=HEADER.unpack(frame[:HEADER.size])
        if version!=VERSION or not 0<=index<count<=255:raise ValueError('Plaintext or invalid radio frame rejected')
        data=frame[HEADER.size:]
        with closing(self.db()) as db,db:
            db.execute('PRAGMA synchronous=FULL')
            db.execute('DELETE FROM sealed_in WHERE ts<?',(time.time()-7*86400,))
            rows=db.execute('SELECT part,total,wire FROM sealed_in WHERE id=? ORDER BY part',(ident,)).fetchall()
            if not rows and db.execute('SELECT COUNT(DISTINCT id) FROM sealed_in').fetchone()[0]>=32:raise ValueError('Encrypted receive queue full')
            for part,total,wire in rows:
                if total!=count or part==index and wire!=data:raise ValueError('Conflicting encrypted fragment')
            db.execute('INSERT OR IGNORE INTO sealed_in VALUES(?,?,?,?,?)',(ident,index,count,data,time.time()))
            rows=db.execute('SELECT part,wire FROM sealed_in WHERE id=? ORDER BY part',(ident,)).fetchall()
            if sum(len(row[1]) for row in rows)>1024:raise ValueError('Encrypted message exceeds bounds')
            if len(rows)<count:return None
            wire=b''.join(row[1] for row in rows)
            if hashlib.sha256(wire).digest()[:6]!=ident:raise ValueError('Corrupt encrypted message')
            try:raw=SealedBox(self.key).decrypt(wire)
            except CryptoError as exc:raise ValueError('Radio decryption failed') from exc
            opposite=b'D' if self.direction==b'U' else b'U'
            if not raw.startswith(MAGIC+opposite):raise ValueError('Wrong encrypted radio direction')
            db.execute('DELETE FROM sealed_in WHERE id=?',(ident,))
            return raw[len(MAGIC)+1:]
