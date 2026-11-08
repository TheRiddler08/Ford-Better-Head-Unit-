"""Automatic small-update client: IP exchange plus Ethernet-radio relay frames."""
import json
import os
from pathlib import Path
import socket
import sqlite3
import time
from urllib.parse import urlsplit
from urllib.request import Request,build_opener,ProxyHandler
from control.tailscale_agent import NoRedirect
from control.update_transfer import Journal,digest,encode,APPLIED,ERROR,decode,canonical
from control.patches import ALLOWED,DUET,UI,install_patch

LAN={'tiny':'172.16.42.2','duet':'172.16.42.1','pi':'172.16.42.3','laptop':'172.16.42.4'}
PORT=8796


def atomic(path,data):
    import tempfile
    from control.storage_policy import guard_state_file
    guard_state_file(path,len(data))
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    if path.exists() and path.read_bytes()==data:return
    with tempfile.NamedTemporaryFile(dir=path.parent,delete=False) as file:
        file.write(data);file.flush();os.fsync(file.fileno());tmp=Path(file.name)
    tmp.chmod(0o600);os.replace(tmp,path)


class Agent:
    def __init__(self,config):
        self.config=config;self.role=config['role'];self.device=config.get('device','car-1')
        self.home=Path(config['home']);self.repo=Path(config['repo'])
        self.state=self.home/'.local/state/drive-console/software'
        self.state.mkdir(parents=True,exist_ok=True)
        self.inventory_fingerprint=None;self.inventory_sent_at=0
        self.journal=Journal(self.state/'journal.sqlite3')
        self.credentials=self.home/'.config/car-dashboard'
        self.public=self.credentials/'update-signing.pub';self.link=self.credentials/'update-link.key'
        self.overrides=Path(config.get('overrides','/var/lib/car-dashboard/overrides' if self.role=='tiny' else str(self.state/'overrides')))
        self.pending=self.state/'receipts.json';self.radio_pending=self.state/'radio-receipts.json';self.ip_ok_until=0
        self.token=Path(config.get('token_file',str(self.credentials/'device-token')))
        self.url=config.get('home_url','http://127.0.0.1:8791')
        url=urlsplit(self.url)
        if url.username or url.password or url.query or url.fragment or url.path not in ('','/') or not (url.scheme=='https' or url.scheme=='http' and url.hostname=='127.0.0.1'):
            raise ValueError('Use HTTPS or the authenticated local SSH tunnel for home updates')
        self.client=build_opener(ProxyHandler({}),NoRedirect()) if url.hostname=='127.0.0.1' else build_opener(NoRedirect())
    def key(self):return bytes.fromhex(self.link.read_text().strip())
    def call(self,path,data=None):
        token=self.token.read_text().strip()
        if len(token)<32:raise ValueError('Home device token is not configured')
        request=Request(self.url.rstrip('/')+path,data=None if data is None else json.dumps(data).encode(),headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
        with self.client.open(request,timeout=10) as response:
            raw=response.read(1048577)
            if len(raw)>1048576:raise ValueError('Home response exceeds bounds')
            return json.loads(raw)
    def enroll(self):
        response=self.call('/api/device/software/credentials')
        public=response['publicKey'].encode();key=bytes.fromhex(response['linkKey'])
        from cryptography.hazmat.primitives.serialization import load_pem_public_key
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        if not isinstance(load_pem_public_key(public),Ed25519PublicKey) or len(key)<32:raise ValueError('Invalid home update credentials')
        if self.public.exists() and self.public.read_bytes()!=public:raise ValueError('Pinned home signing key changed; trusted rotation is required')
        if self.link.exists() and self.key()!=key:raise ValueError('Pinned radio link key changed')
        atomic(self.public,public);atomic(self.link,key.hex().encode())
        from control.radio_crypto import pin
        if 'radioPublicKey' not in response:raise ValueError('Home radio encryption enrollment needs an updated server')
        pin(self.credentials/'radio-home.pub',response['radioPublicKey'])
    def receipt(self,raw):
        queue=json.loads(self.pending.read_text()) if self.pending.exists() else []
        value=raw.hex()
        if value not in queue:queue.append(value)
        # Durable backpressure: don't silently discard a receipt on transport loss.
        if len(queue)>65536:raise ValueError('Receipt queue is full; restore an uplink')
        atomic(self.pending,json.dumps(queue).encode())
        radio=json.loads(self.radio_pending.read_text()) if self.radio_pending.exists() else []
        if value not in radio:radio.append(value)
        atomic(self.radio_pending,json.dumps(radio).encode())
    def inventory(self):
        if self.role in ('pi','laptop'):return []
        rows=[]
        for path in sorted(ALLOWED):
            if path.startswith('duet/')!=(self.role=='duet'):continue
            overlay=self.overrides/path;source=overlay if overlay.is_file() else self.repo/path
            if self.role=='duet':
                actual=self.home/'.local/share/drive-console'/Path(path).name
                if actual.is_file():source=actual
            if not source.is_file() or source.is_symlink():continue
            raw=source.read_bytes()
            if len(raw)>256000:continue
            try:text=raw.decode()
            except UnicodeError:continue
            candidate={'path':path,'source':text,'sha256':digest(raw)}
            if len(json.dumps(rows+[candidate]).encode())<=750000:rows.append(candidate)
        # Keep the authenticated exchange below its HTTP request limit.
        return rows
    def exchange(self):
        if not self.public.exists() or not self.link.exists() or not (self.credentials/'radio-home.pub').exists():self.enroll()
        queued=json.loads(self.pending.read_text()) if self.pending.exists() else []
        sources=self.inventory()
        fingerprint=digest(canonical([(row['path'],row['sha256']) for row in sources]))
        data={'role':self.role,'receipts':queued[:128]}
        from control.file_sync import car_files
        files=car_files(self.config);files.scan();data['fileObjects']=files.pending();data['fileReceipts']=files.receipts()
        if self.config.get('telemetry_radio_enabled',True):
            from control.telemetry import encode,collect
            data['telemetryFrames']=[raw.hex() for raw in encode(self.role,collect(self.home,self.role),self.key())]
        result=self.state/'radio-test-result.json'
        if self.role=='tiny' and result.exists():
            report=json.loads(result.read_text())
            if report.get('phase') in ('running','passed','timed-out'):data['radioDiagnostic']=report
        if self.role=='tiny' or self.role=='pi' and (self.config.get('radio_adapter') or self.config.get('radio_model')):
            from control.radio_crypto import identity
            data['radioPublicKey']=bytes(identity(self.credentials/'radio-private.key').public_key).hex()
        if fingerprint!=self.inventory_fingerprint or time.monotonic()-self.inventory_sent_at>3600:data['sources']=sources
        response=self.call('/api/device/software/exchange',data)
        for item in response.get('fileResults',[]):files.receive(item['doc'],item['id'],via_ip=True)
        for item in response.get('fileObjects',[]):files.receive(item['doc'],item['id'])
        with files.events.db() as db:
            for ident in response.get('fileReceiptsAccepted',[]):db.execute('UPDATE jobs SET done=1 WHERE id=?',(ident,))
        if 'sources' in data:self.inventory_fingerprint=fingerprint;self.inventory_sent_at=time.monotonic()
        # Preserve receipts appended locally during a later exchange/restart.
        atomic(self.pending,json.dumps(queued[128:]).encode())
        radio=json.loads(self.radio_pending.read_text()) if self.radio_pending.exists() else []
        atomic(self.radio_pending,json.dumps([value for value in radio if value not in queued[:128]]).encode())
        self.ip_ok_until=time.monotonic()+20
        for frame in response.get('frames',[]):self.accept(bytes.fromhex(frame))
        self.stage()
    def accept(self,raw,transport="ip"):
        response=self.journal.receive(raw,self.key(),self.public,self.device,self.role,transport)
        if response:self.receipt(response)
    def stage(self):
        for ident,descriptor,payload in self.journal.ready():
            try:
                delta=json.loads(payload)
                if delta.get('path')!=descriptor['path'] or delta.get('base')!=descriptor['base'] or delta.get('target')!=descriptor['target']:raise ValueError('Patch does not match signed transfer descriptor')
                current=self.overrides/descriptor['path']
                if not current.is_file():current=self.repo/descriptor['path']
                if self.role=='duet':current=self.home/'.local/share/drive-console'/Path(descriptor['path']).name
                if descriptor['path'] not in UI and (self.state/'activation-request.json').exists():
                    previous=json.loads((self.state/'activation-request.json').read_text())
                    if previous['id']!=ident and any(row['id']==previous['id'] and row['phase']=='staged' for row in self.journal.status()):continue
                recorder=descriptor['path'].startswith('recorder/')
                destination=self.state/'staged-overrides' if recorder else self.overrides
                if not (current.is_file() and digest(current.read_bytes())==descriptor['target']):
                    install_patch(delta,self.repo,destination,self.public,source_override=current if self.role=='duet' or recorder else None)
                elif recorder:atomic(destination/descriptor['path'],current.read_bytes())
                if descriptor['path'] in UI:
                    atomic(self.overrides/'active.json',json.dumps({'generation':ident,'time':time.time()}).encode())
                    self.journal.applied(ident);self.receipt(encode(APPLIED,ident,0,1,b'applied',self.key()))
                else:
                    # Root's fixed maintenance handler restarts only adopted
                    # services; no command is accepted from the network payload.
                    request={'id':ident,'path':descriptor['path'],'base':descriptor['base'],'sha256':descriptor['target'],'role':self.role,'time':time.time()}
                    atomic(self.state/'activation-request.json',json.dumps(request).encode())
                    self.journal.phase(ident,'staged')
            except Exception as exc:
                self.journal.phase(ident,'failed',type(exc).__name__)
                self.receipt(encode(ERROR,ident,0,1,b'patch-rejected',self.key()))
        result=Path('/etc/car-dashboard/software-activation.json')
        if result.exists():
            receipt=json.loads(result.read_text())
            if receipt.get('phase')=='applied':
                ident=receipt['id']
                if any(row['id']==ident and row['phase']=='staged' for row in self.journal.status()):
                    self.journal.applied(ident);self.receipt(encode(APPLIED,ident,0,1,b'applied',self.key()))
            elif receipt.get('phase')=='failed':
                ident=receipt['id']
                if any(row['id']==ident and row['phase']=='staged' for row in self.journal.status()):
                    if self.role=='duet':
                        row=next(value for value in self.journal.status() if value['id']==ident)
                        rejected=self.overrides/row['path']
                        if rejected.is_file():os.replace(rejected,rejected.with_suffix(rejected.suffix+'.failed-'+ident))
                    self.journal.phase(ident,'failed','activation-rolled-back')
                    self.receipt(encode(ERROR,ident,0,1,b'activation-failed',self.key()))
    def legacy(self,raw):
        from control.lorawan_protocol import HEADER,authenticate_application_frame
        from control.car_agent import new_assembler,load_agent_state,process_frame
        authenticate_application_frame(raw,self.key())
        _,kind,sequence,*_=HEADER.unpack(raw[:HEADER.size])
        if kind!=1 or self.role!='tiny':return
        state=self.state/'legacy-state.json';last=self.state/'legacy-ack.json'
        saved=load_agent_state(state)
        cached=json.loads(last.read_text()) if last.exists() else {}
        if sequence==saved.get('last_sequence') and cached.get('sequence')==sequence:
            outgoing=cached.get('frames',[])
        else:
            if sequence==saved.get('last_sequence'):saved['last_sequence']=sequence-1
            agent=new_assembler(self.key(),saved)
            config=self.home/'.config/car-dashboard/ui-config.json'
            if not config.exists() and Path('/etc/car-dashboard/ui-config.json').exists():
                atomic(config,Path('/etc/car-dashboard/ui-config.json').read_bytes())
            outgoing=[value.hex() for value in process_frame(agent,raw,state,config,self.key())]
            if outgoing:atomic(last,json.dumps({'sequence':sequence,'frames':outgoing}).encode())
        radio=json.loads(self.radio_pending.read_text()) if self.radio_pending.exists() else []
        for value in outgoing:
            if value not in radio:radio.append(value)
        atomic(self.radio_pending,json.dumps(radio).encode())

    def radio(self,receiver):
        if not self.public.exists() or not self.link.exists():return
        try:
            while True:
                data,peer=receiver.recvfrom(128)
                if peer[0] not in LAN.values():continue
                if data[:1]==b'R':
                    from control.lorawan_protocol import authenticate_application_frame
                    authenticate_application_frame(data[1:],self.key())
                    radio=json.loads(self.radio_pending.read_text()) if self.radio_pending.exists() else []
                    value=data[1:].hex()
                    atomic(self.radio_pending,json.dumps([entry for entry in radio if entry!=value]).encode())
                    ip=json.loads(self.pending.read_text()) if self.pending.exists() else []
                    atomic(self.pending,json.dumps([entry for entry in ip if entry!=value]).encode())
                elif data[:1]==b'D':
                    if data[1:2]==b'\x01':self.legacy(data[1:])
                    else:self.accept(data[1:],transport='lorawan')
        except BlockingIOError:pass
        self.stage()
        queued=json.loads(self.radio_pending.read_text()) if self.radio_pending.exists() else []
        if queued and time.monotonic()>self.ip_ok_until:
            with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as sender:
                sender.setsockopt(socket.SOL_SOCKET,socket.SO_BROADCAST,1);sender.bind((LAN[self.role],0))
                # Relay does persisted rate limiting. Its TX receipt can retire
                # this transport queue entry; durable payload state recreates an
                # ACK if home retries after actual over-air loss.
                for value in queued[:4]:sender.sendto(b'U'+bytes.fromhex(value),('172.16.42.255',PORT))


def main():
    config=json.loads(Path('/etc/car-dashboard/fleet.json').read_text())
    override=Path('/etc/car-dashboard/update-link.json')
    if override.exists():config.update(json.loads(override.read_text()))
    agent=Agent(config);last_ip=0
    stamp=override.stat().st_mtime_ns if override.exists() else 0
    last_status=None;last_status_time=0
    with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as receiver:
        receiver.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1);receiver.bind(('',PORT));receiver.setblocking(False)
        while True:
            current=override.stat().st_mtime_ns if override.exists() else 0
            if current!=stamp:
                config=json.loads(Path('/etc/car-dashboard/fleet.json').read_text())
                if override.exists():config.update(json.loads(override.read_text()))
                agent=Agent(config);stamp=current;last_ip=0
            phase='connected' if time.monotonic()<agent.ip_ok_until else 'waiting-home-or-receiving-radio';detail=None
            try:agent.radio(receiver)
            except sqlite3.Error:phase='waiting-local-state-space';detail='SQLite state storage unavailable or full'
            except (OSError,ValueError):phase='waiting-radio-or-trust'
            if time.monotonic()-last_ip>10:
                try:agent.exchange();phase='connected'
                except Exception as exc:phase='waiting-home-connection-or-credentials';detail=type(exc).__name__
                last_ip=time.monotonic()
            status={'phase':phase,'detail':detail,'updates':agent.journal.status()}
            fingerprint=digest(canonical(status))
            if fingerprint!=last_status or time.monotonic()-last_status_time>30:
                atomic(agent.state/'status.json',json.dumps(dict(status,time=time.time())).encode())
                last_status=fingerprint;last_status_time=time.monotonic()
            time.sleep(1)


if __name__=='__main__':main()
