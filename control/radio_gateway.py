"""Durable LoRaWAN modem-adapter boundary and shared Ethernet radio relay.

The configured, trusted local modem utility speaks JSON lines:
stdin {id, uplink:hex} / {poll:true}; stdout {sent:id} / {downlink:hex}.
No vendor serial commands or guessed OBD/GPS ports are issued by this bridge.
"""
import hashlib
import json
from pathlib import Path
import select
import signal
import socket
import sqlite3
import subprocess
import time
import uuid
from control.update_agent import LAN,PORT,atomic
from control.update_transfer import Journal,decode,encode,ACK,APPLIED,MANIFEST,DATA
from control.lorawan_protocol import authenticate_application_frame


class AdapterStatus:
    """Process heartbeat and radio activity are separate observations."""
    def __init__(self,path):self.path=Path(path);self.value={};self.last_write=None
    def event(self,phase,event=None,**fields):
        self.value.update(fields,phase=phase)
        if event:self.value.update(lastEvent=event,lastActivityAt=time.time())
        self.heartbeat(force=True)
    def heartbeat(self,force=False):
        now=time.monotonic()
        if force or self.last_write is None or now-self.last_write>=30:
            atomic(self.path,json.dumps(dict(self.value,time=time.time()),allow_nan=False).encode());self.last_write=now


def suppress_deferred_gps(journal,home,key):
    """Retire old queued GPS frames locally when GPS radio reporting is deferred."""
    from contextlib import closing
    from control.lorawan_protocol import split_message
    path=Path(home)/'.local/share/car-data/radio-catalog.sqlite3'
    if not path.exists():return
    with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)) as source:
        rows=source.execute("SELECT id,doc FROM jobs WHERE json_extract(doc,'$.type')='gps'").fetchall()
    with journal.db() as db:
        for ident,doc in rows:
            for frame in split_message('status',1,{'id':ident,'catalog':json.loads(doc)},key):
                db.execute('UPDATE uplinks SET sent=2 WHERE id=?',(hashlib.sha256(frame).hexdigest(),))


def main():
    def stop(*_):raise SystemExit(0)
    signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
    fleet=json.loads(Path('/etc/car-dashboard/fleet.json').read_text());role=fleet['role']
    state=Path(fleet['home'])/'.local/state/drive-console/software'
    state.mkdir(parents=True,exist_ok=True)
    status=AdapterStatus(state/'radio-status.json');journal=Journal(state/'radio-queue.sqlite3')
    with journal.db() as db:
        db.execute('CREATE TABLE IF NOT EXISTS uplinks(id TEXT PRIMARY KEY,frame TEXT,sent INTEGER DEFAULT 0,attempt REAL DEFAULT 0)')
        db.execute('CREATE TABLE IF NOT EXISTS modem_attempts(id TEXT PRIMARY KEY,inner_id TEXT,created REAL)')
        if 'budget' not in {row[1] for row in db.execute('PRAGMA table_info(modem_attempts)')}:
            db.execute('ALTER TABLE modem_attempts ADD COLUMN budget INTEGER DEFAULT 11')
    while True:
        config=Path('/etc/car-dashboard/update-link.json')
        options=json.loads(config.read_text()) if config.exists() else {}
        stamp=config.stat().st_mtime_ns if config.exists() else 0
        try:
            from control.rak3172 import adapter_command
            command=adapter_command(options)
        except ValueError:
            status.event('invalid-adapter-config');time.sleep(5);continue
        keypath=Path(fleet['home'])/'.config/car-dashboard/update-link.key'
        if not command or not keypath.exists():
            status.event('unconfigured',detail='Radio model/adapter and link credentials required');time.sleep(5);continue
        if not isinstance(command,list) or not command or not Path(command[0]).is_absolute() or not all(isinstance(v,str) for v in command):
            status.event('invalid-adapter-config');time.sleep(5);continue
        key=bytes.fromhex(keypath.read_text().strip())
        process=None
        try:
            from control.radio_crypto import RadioBox
            def make_cipher(budget):return RadioBox(Path(fleet['home'])/'.config/car-dashboard/radio-private.key',
                            Path(fleet['home'])/'.config/car-dashboard/radio-home.pub',
                            state/'sealed-radio.sqlite3',b'U',budget)
            cipher=make_cipher(int(options.get('max_app_bytes',11 if options.get('radio_model')=='RAK3172' else 46)))
            if not cipher.peer.exists():raise ValueError('Pinned home encryption key required')
            if not options.get('telemetry_radio_enabled',True):
                with journal.db() as db:db.execute("UPDATE uplinks SET sent=2 WHERE substr(frame,1,2)='05'")
            if role=='tiny' and not options.get('gps_radio_enabled',False):suppress_deferred_gps(journal,fleet['home'],key)
            diagnostic=None
            if role=='tiny' and options.get('radio_model')=='RAK3172' and options.get('radio_self_test',True):
                from control.radio_diagnostic import Diagnostic
                diagnostic=Diagnostic(state/'radio-test-result.json',key)
                diagnostic.start()
            with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as receiver,socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as sender:
                receiver.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1);receiver.bind(('',PORT))
                sender.setsockopt(socket.SOL_SOCKET,socket.SO_BROADCAST,1);sender.bind((LAN[role],0))
                process=subprocess.Popen(command,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,bufsize=0)
                status.value={};status.event('adapter-running-awaiting-network')
                next_poll=0;next_idle=0;buffer=b''
                while process.poll() is None:
                    status.heartbeat()
                    if (config.stat().st_mtime_ns if config.exists() else 0)!=stamp:break
                    ready,_,_=select.select([receiver,process.stdout],[],[],1)
                    if receiver in ready:
                        data,peer=receiver.recvfrom(128)
                        if peer[0] in LAN.values() and data[:1]==b'F':
                            authenticate_application_frame(data[1:],key)
                            if data[1:3]==b'\x01\x06':
                                with journal.db() as db:db.execute('UPDATE uplinks SET sent=2 WHERE id=?',(hashlib.sha256(data[1:]).hexdigest(),))
                        if peer[0] in LAN.values() and data[:1]==b'U':
                            authenticate_application_frame(data[1:],key)
                            if data[1:2]==b'\x05' and not options.get('telemetry_radio_enabled',True):continue
                            ident=hashlib.sha256(data[1:]).hexdigest()
                            with journal.db() as db:
                                db.execute('INSERT INTO uplinks(id,frame) VALUES(?,?) ON CONFLICT(id) DO UPDATE SET sent=CASE WHEN uplinks.sent=2 THEN 0 ELSE uplinks.sent END',(ident,data[1:].hex()))
                                if db.execute('SELECT sent FROM uplinks WHERE id=?',(ident,)).fetchone()[0]==1:sender.sendto(b'R'+data[1:],('172.16.42.255',PORT))
                    if process.stdout in ready:
                        import os
                        block=os.read(process.stdout.fileno(),4096)
                        if not block:break
                        buffer+=block
                        if len(buffer)>16384:raise ValueError('Adapter output exceeded limit')
                        while b'\n' in buffer:
                            line,buffer=buffer.split(b'\n',1);message=json.loads(line)
                            if 'downlink' in message:
                                raw=cipher.accept(bytes.fromhex(message['downlink']))
                                if raw is None:continue
                                authenticate_application_frame(raw,key)
                                status.event('adapter-running','authenticated-downlink')
                                if raw[:1]==b'\x04':
                                    if diagnostic:diagnostic.receive(raw)
                                    continue
                                kind,short,index,count,_=decode(raw,key) if raw[:1]==b'\x02' else (None,None,None,None,None)
                                if kind in (MANIFEST,DATA):
                                    receipt=encode(ACK,short,0 if kind==MANIFEST else index,count,b'M' if kind==MANIFEST else b'd',key)
                                    applied=encode(APPLIED,short,0,1,b'applied',key)
                                    with journal.db() as db:db.execute('UPDATE uplinks SET sent=2,attempt=0 WHERE id IN (?,?)',(hashlib.sha256(receipt).hexdigest(),hashlib.sha256(applied).hexdigest()))
                                if raw[:1]==b'\x01':
                                    with journal.db() as db:db.execute("UPDATE uplinks SET sent=2,attempt=0 WHERE substr(frame,1,4)='0105' AND substr(frame,5,8)=?",(raw[2:6].hex(),))
                                sender.sendto(b'D'+raw,('172.16.42.255',PORT))
                            elif 'sent' in message:
                                with journal.db() as db:
                                    row=db.execute('SELECT uplinks.frame,uplinks.id,modem_attempts.budget FROM uplinks JOIN modem_attempts ON uplinks.id=modem_attempts.inner_id WHERE modem_attempts.id=?',(message['sent'],)).fetchone()
                                    db.execute('DELETE FROM modem_attempts WHERE id=?',(message['sent'],))
                                    sent_cipher=make_cipher(row[2]) if row and row[2]!=cipher.max_bytes else cipher
                                    if row and sent_cipher.sent(bytes.fromhex(row[0])):
                                        db.execute('UPDATE uplinks SET sent=1 WHERE id=?',(row[1],))
                                        sender.sendto(b'R'+bytes.fromhex(row[0]),('172.16.42.255',PORT))
                                        status.event('adapter-running','modem-tx-complete')
                            elif isinstance(message.get('status'),dict) and options.get('radio_model')=='RAK3172':
                                from control.rak3172 import payload_ceiling
                                dr=message['status'].get('dr')
                                observed={}
                                if dr in ('0','1','2','3','4'):
                                    if options.get('radio_auto_payload',True):
                                        budget=min(payload_ceiling(options),11 if dr=='0' else 46)
                                        if budget!=cipher.max_bytes:cipher=make_cipher(budget)
                                    observed.update(dataRate=int(dr),payloadBytes=cipher.max_bytes)
                                if message['status'].get('njs') in ('0','1'):observed['joined']=message['status']['njs']=='1'
                                status.event('adapter-not-joined' if observed.get('joined') is False else 'adapter-running','modem-status-observed',**observed)
                    spacing=max(60,int(options.get('uplink_spacing',60)));limit=min(200,int(options.get('uplinks_per_day',120)))
                    if diagnostic:
                        test=diagnostic.outgoing()
                        with journal.db() as db:
                            if test:
                                ident=hashlib.sha256(test).hexdigest()
                                db.execute('INSERT OR IGNORE INTO uplinks(id,frame) VALUES(?,?)',(ident,test.hex()))
                                db.execute('UPDATE uplinks SET sent=0 WHERE id=? AND sent=1',(ident,))
                            else:db.execute("UPDATE uplinks SET sent=2 WHERE substr(frame,1,2)='04'")
                    # Class A needs actual uplinks to receive home messages.
                    # A compact sealed keepalive contains no telemetry or GPS.
                    if options.get('radio_model')=='RAK3172' and options.get('radio_keepalive',True) and time.monotonic()>=next_idle:
                        with journal.db() as db:busy=db.execute('SELECT 1 FROM uplinks WHERE sent=0 LIMIT 1').fetchone()
                        if not busy:
                            from control.radio_diagnostic import packet
                            import secrets
                            nonce_path=state/'radio-keepalive.json'
                            if not nonce_path.exists():atomic(nonce_path,json.dumps({'nonce':secrets.token_hex(16)}).encode())
                            pulse=packet(3,bytes.fromhex(json.loads(nonce_path.read_text())['nonce']),key)
                            ident=hashlib.sha256(pulse).hexdigest()
                            with journal.db() as db:
                                db.execute('INSERT OR IGNORE INTO uplinks(id,frame) VALUES(?,?)',(ident,pulse.hex()))
                                db.execute('UPDATE uplinks SET sent=0 WHERE id=?',(ident,))
                            next_idle=time.monotonic()+max(60,int(options.get('idle_uplink_spacing',900)))
                    with journal.db() as db:
                        db.execute("UPDATE uplinks SET sent=0 WHERE sent=1 AND substr(frame,1,4)='0106' AND attempt<?",(time.time()-600,))
                        row=db.execute("SELECT id,frame FROM uplinks WHERE sent=0 AND attempt<? ORDER BY CASE WHEN substr(frame,1,2)='04' THEN 3 WHEN substr(frame,1,2)='05' THEN 2 WHEN substr(frame,1,4)='0106' THEN 1 ELSE 0 END,attempt LIMIT 1",(time.time()-spacing,)).fetchone()
                    if row and journal.budget('modem-uplinks',spacing,limit):
                        attempt=uuid.uuid4().hex
                        packet={'id':attempt,'uplink':cipher.next(bytes.fromhex(row[1])).hex()} if row else {'poll':True}
                        if row:
                            with journal.db() as db:
                                db.execute('DELETE FROM modem_attempts WHERE created<?',(time.time()-86400,))
                                db.execute('INSERT INTO modem_attempts(id,inner_id,created,budget) VALUES(?,?,?,?)',(attempt,row[0],time.time(),cipher.max_bytes))
                        _,writable,_=select.select([],[process.stdin],[],2)
                        if not writable:raise TimeoutError('Modem adapter input stalled')
                        process.stdin.write(json.dumps(packet).encode()+b'\n');process.stdin.flush()
                        if row:
                            with journal.db() as db:db.execute('UPDATE uplinks SET attempt=? WHERE id=?',(time.time(),row[0]))
                        next_poll=time.monotonic()+spacing
                status.event('adapter-disconnected-retrying')
        except (OSError,ValueError,TypeError,sqlite3.Error):
            status.event('adapter-disconnected-retrying')
        finally:
            if process and process.poll() is None:
                process.terminate()
                try:process.wait(timeout=5)
                except subprocess.TimeoutExpired:process.kill();process.wait()
        time.sleep(5)


if __name__=='__main__':main()
