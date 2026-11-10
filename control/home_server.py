"""Home-side configuration control panel and low-airtime downlink spool.

Runs ONLY on the home Linux server (loopback, via SSH/Tailscale tunnel).
A real LoRaWAN network-server bridge must relay queued frames.
"""
from __future__ import annotations
import hmac
import json
import os
import re
import threading
import time
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from control.config_core import DEFAULT, apply_changes, read_config, write_config, diff
from control.lorawan_protocol import split_message
from control.layouts import LayoutStore, FRAME_HOST, FRAME_POLICY, validate_project

DEVICE_RE = re.compile(r"^[a-zA-Z0-9_-]{1,48}$")
LOCK = threading.RLock()
ROOT = Path(os.environ.get("CAR_HOME_STATE", str(Path.home()/".local/share/car-home-control")))
UI = Path(__file__).parent / "web"
TOKEN = os.getenv("CAR_PANEL_TOKEN", "")
KEY_HEX = os.getenv("CAR_LINK_KEY", "")
HOST = os.getenv("CAR_PANEL_BIND", "127.0.0.1")
PORT = int(os.getenv("CAR_PANEL_PORT","8790"))
DEVICE_TOKEN = os.getenv("CAR_DEVICE_TOKEN", "")
DEVICE_NAME = os.getenv("CAR_DEVICE_NAME", "car-1")

def software():
    from control.software import Software
    with LOCK:return Software(ROOT/'software')


def radio_public_key():
    from control.radio_crypto import identity
    return bytes(identity(ROOT/"radio/private.key").public_key).hex()


def radio_key():
    if not KEY_HEX:return software().key()
    try:
        value=bytes.fromhex(KEY_HEX)
    except ValueError:
        value=b""
    if len(value)<32:
        raise RuntimeError("CAR_LINK_KEY must be at least 32 random bytes encoded as hex")
    return value

def authorized(header):
    return bool(TOKEN) and hmac.compare_digest(header or "", "Bearer "+TOKEN)

def safe_device(name):
    if not DEVICE_RE.fullmatch(name):
        raise ValueError("Device identifier must contain only letters, digits, _ or -")
    return name

def paths(name):
    name=safe_device(name)
    return ROOT/"devices"/name/"config.json", ROOT/"devices"/name/"outbox.json"

def read_outbox(path):
    try:
        value=json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value,list):return []
        return value
    except (OSError,ValueError):
        return []

def save_outbox(path, entries):
    path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix(".tmp")
    tmp.write_text(json.dumps(entries,separators=(",",":")),encoding="utf-8")
    os.replace(tmp,path)

def next_sequence(device):
    """Monotonic per-device transport sequence, independent of UI revision."""
    safe_device(device)
    path=ROOT/"devices"/device/"next-sequence.txt"
    try:
        last=int(path.read_text(encoding="ascii").strip())
    except (OSError,ValueError):
        last=0
    sequence=max(last+1,int(time.time()))
    if sequence>=2**32:
        raise RuntimeError("Sequence exhausted; rotate link key and re-pair")
    path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_suffix(".tmp")
    temp.write_text(str(sequence),encoding="ascii")
    os.replace(temp,path)
    return sequence


def queue_change(name, old_rev, changes):
    """Persist desired config and exact ordered frames before sending anything."""
    with LOCK:
        cfg_file,queue_file=paths(name)
        current=read_config(cfg_file)
        if current["revision"]!=old_rev:
            raise ValueError("Revision conflict. Reload configuration before saving.")
        next_config=apply_changes(current,old_rev+1,changes)
        compact=diff(current,next_config)
        if not compact:return {"revision":current["revision"],"frames":0}
        sequence=next_sequence(name)
        frames=split_message("configuration",sequence,
                             {"revision":next_config["revision"],"changes":compact},radio_key()) if KEY_HEX else []
        existing=read_outbox(queue_file)
        item={"revision":sequence,"configRevision":next_config["revision"],"created":int(time.time()),
              "kind":"configuration","frames":[b.hex() for b in frames],
              "transmitted":0,"acked":False}
        # Preserve in-flight revisions; each must be applied in order on car.
        if len(existing)>=64:raise ValueError("Too many pending revisions; reconcile offline device first")
        existing.append(item)
        save_outbox(queue_file,existing)
        write_config(cfg_file,next_config)
        return {"revision":next_config["revision"],"sequence":sequence,"frames":len(frames),"bytes":sum(map(len,frames))}

def queue_code_patch(name, kind, signed_delta):
    """Queue only patches verified with the car's pinned PUBLIC signing key."""
    from control.patches import verify_delta
    public=os.getenv("CAR_SIGNING_PUBLIC_PEM","")
    if not public or not Path(public).is_file():
        raise ValueError("Public code-signing key not configured")
    verify_delta(signed_delta,public)
    if kind not in ("ui-patch","recorder-patch") or signed_delta["component"]!=("ui" if kind=="ui-patch" else "recorder"):
        raise ValueError("Mismatched component")
    with LOCK:
        _,queue_file=paths(name)
        old=read_outbox(queue_file)
        if len(old)>=64:
            raise ValueError("Too many pending updates")
        seq=next_sequence(name)
        frames=split_message(kind,seq,signed_delta,radio_key())
        old.append({"revision":seq,"kind":kind,"created":int(time.time()),
                    "frames":[v.hex() for v in frames],"transmitted":0,"acked":False})
        save_outbox(queue_file,old)
        return {"sequence":seq,"fragments":len(frames),"totalBytes":sum(map(len,frames))}


def rewind_chunks(name, message_sequence, missing):
    """For a verified car-origin missing-chunk request, resend from first gap."""
    if not isinstance(missing,list) or len(missing)>1024 or any(type(x) is not int for x in missing):
        return False
    with LOCK:
        _,queue_file=paths(name)
        queue=read_outbox(queue_file)
        for item in queue:
            if item["revision"]==message_sequence and missing:
                allowed=[i for i in missing if 0<=i<len(item["frames"])]
                if not allowed:return False
                item["transmitted"]=min(item["transmitted"],min(allowed))
                save_outbox(queue_file,queue)
                return True
        return False


def export_next(name):
    """For ChirpStack bridge: one queued frame at a time, never flood downlinks."""
    with LOCK:
        _,queue_file=paths(name)
        queue=read_outbox(queue_file)
        for item in queue:
            if item.get('acked') or not item.get('frames'):continue
            if item.get('transmitted',0)>=len(item['frames']):
                if time.time()-item.get('lastTransmit',0)<300:return None
                item['transmitted']=0;save_outbox(queue_file,queue)
            index=item.get('transmitted',0)
            return {'revision':item['revision'],'index':index,'frame_hex':item['frames'][index]}

        return None

def mark_transmitted(name, revision, index):
    """Call only after network-server queue successfully accepts the downlink."""
    with LOCK:
        _,queue_file=paths(name)
        queue=read_outbox(queue_file)
        for item in queue:
            if item["revision"]==revision and item["transmitted"]==index:
                item["transmitted"]=index+1
                item["lastTransmit"]=time.time()
                save_outbox(queue_file,queue)
                return True
        return False

def ack_revision(name, revision):
    """Only invoke upon an authenticated car-side acknowledgment."""
    with LOCK:
        _,queue_file=paths(name)
        queue=read_outbox(queue_file)
        found=False
        for item in queue:
            if item["revision"]==revision:
                item["acked"]=True
                found=True
        if found:
            queue=[x for x in queue if not x.get("acked")]
            save_outbox(queue_file,queue)
        return found

class Handler(BaseHTTPRequestHandler):
    def response(self, data, status=200, mime="application/json; charset=utf-8", policy=None):
        raw=data if isinstance(data,bytes) else json.dumps(data,separators=(",",":")).encode()
        self.send_response(status)
        for key,val in {"Content-Type":mime,"Content-Length":str(len(raw)),
                        "Cache-Control":"no-store","X-Content-Type-Options":"nosniff",
                        "Content-Security-Policy":policy or "default-src 'self'; script-src 'self'; style-src 'self'; frame-src 'self'; base-uri 'none'; frame-ancestors 'none'"}.items():
            self.send_header(key,val)
        self.end_headers()
        self.wfile.write(raw)

    def fail(self,error,status=400):
        self.response({"error":str(error)},status)

    def access(self):
        if not authorized(self.headers.get("Authorization")):
            self.fail("Unauthorized",401)
            return False
        return True

    def request_json(self):
        length=int(self.headers.get("Content-Length","0"))
        limit=1048576 if self.path.startswith("/api/device/software/") or self.path.startswith("/api/software/") else 131072 if self.path=="/api/sync/file" else 512000 if self.path=="/api/layout" else 8192
        if length<=0 or length>limit:raise ValueError("Invalid request length")
        return json.loads(self.rfile.read(length))

    def device_access(self):
        if not DEVICE_TOKEN or not hmac.compare_digest(
                self.headers.get("Authorization", ""), "Bearer "+DEVICE_TOKEN):
            self.fail("Unauthorized",401)
            return False
        return True

    def do_GET(self):
        req=urlsplit(self.path)
        if req.path=="/layout-host":
            return self.response(FRAME_HOST,mime="text/html; charset=utf-8",policy=FRAME_POLICY)
        if req.path=="/api/device/software/credentials":
            if not self.device_access():return
            service=software()
            try:radio=radio_public_key()
            except ImportError:return self.fail("Radio encryption dependencies pending; run the normal home installer",503)
            return self.response({"publicKey":service.public.read_text(),"linkKey":service.key().hex(),"radioPublicKey":radio})
        if req.path=="/api/device/layout":
            if not self.device_access():return
            return self.response(LayoutStore(ROOT/"layouts.sqlite3").get(DEVICE_NAME)["published"])
        if req.path=="/api/device/config":
            if not self.device_access():return
            with LOCK:
                cfg_file,_=paths(DEVICE_NAME)
                return self.response({"device":DEVICE_NAME,"config":read_config(cfg_file)})
        if req.path=='/api/device/map-coverage':
            if not self.device_access():return
            try:
                from control.coverage import map_report
                after=int(parse_qs(req.query).get('after',['0'])[0])
                if after<0:raise ValueError('Invalid coverage cursor')
                registry=ROOT/'gateways.json'
                return self.response(map_report(ROOT/'coverage.sqlite3',json.loads(registry.read_text()) if registry.exists() else {},after))
            except (OSError,ValueError,TypeError):return self.fail('Coverage unavailable',503)
        if req.path in ("/","/panel.js","/panel.css","/builder.js","/builder.css","/layout-runtime.js","/software.js","/sync.js","/transfers.js","/radio-messages.js","/radio-messages.css"):
            name,mime={"/radio-messages.js":("radio-messages.js","text/javascript; charset=utf-8"),"/radio-messages.css":("radio-messages.css","text/css; charset=utf-8"),"/":("index.html","text/html; charset=utf-8"),
                       "/panel.js":("panel.js","text/javascript; charset=utf-8"),
                       "/panel.css":("panel.css","text/css; charset=utf-8"),
                       "/builder.js":("builder.js","text/javascript; charset=utf-8"),
                       "/software.js":("software.js","text/javascript; charset=utf-8"),
                       "/sync.js":("sync.js","text/javascript; charset=utf-8"),
                       "/transfers.js":("transfers.js","text/javascript; charset=utf-8"),
                       "/layout-runtime.js":("layout-runtime.js","text/javascript; charset=utf-8"),
                       "/builder.css":("builder.css","text/css; charset=utf-8")}[req.path]
            return self.response((UI/name).read_bytes(),mime=mime)
        if not self.access():return
        try:
            if req.path=='/api/gateways':
                from control.coverage import gateway_view
                registry=ROOT/'gateways.json'
                return self.response({'gateways':gateway_view(json.loads(registry.read_text()) if registry.exists() else {})})
            name=parse_qs(req.query).get("device",["car-1"])[0]
            cfg_file,queue_file=paths(name)
            if req.path=="/api/gps-relay":
                from control.event_transfer import Events
                return self.response({'items':Events(ROOT/'radio/catalog.sqlite3',radio_key()).catalog('gps')})
            if req.path=="/api/messages":
                from control.messages import listing
                from control.event_transfer import Events
                return self.response(listing(Events(ROOT/'radio/catalog.sqlite3',radio_key(),destination='home')))
            if req.path=="/api/dashcam":
                from control.event_transfer import Events
                return self.response({'items':Events(ROOT/'radio/catalog.sqlite3',radio_key()).catalog(),
                                      'videoTransport':'Wi-Fi/Ethernet; LoRaWAN carries metadata only'})
            if req.path=="/api/coverage":
                from control.coverage import report
                after=int(parse_qs(req.query).get('after',['0'])[0])
                if after<0:raise ValueError('Invalid coverage cursor')
                return self.response(report(ROOT/'coverage.sqlite3',after))
            if req.path=="/api/software/source":
                query=parse_qs(req.query)
                return self.response(software().source(name,query.get("role",["tiny"])[0],query.get("path",[""])[0]))
            if req.path in ('/api/software/transfers','/api/software/transfer'):
                from control.transfer_status import transfers
                query=parse_qs(req.query);role=query.get('role',['all'])[0];before=query.get('before',[None])[0]
                return self.response(transfers(software().journal,name,None if role=='all' else role,query.get('state',['active'])[0],
                    int(before) if before is not None else None,query.get('id',[None])[0] if req.path.endswith('/transfer') else None))
            if req.path in ('/api/sync','/api/sync/file'):
                from control.file_sync import home_files,home_selection
                query=parse_qs(req.query);role=query.get('role',['tiny'])[0]
                selected=home_selection(ROOT,role);files=home_files(ROOT,role,radio_key())
                if req.path=='/api/sync/file':return self.response(files.inspect(query.get('name',[''])[0]))
                files.scan();rows={row['name']:row for row in files.status()}
                peer=ROOT/'sync-peer'/(role+'.json')
                return self.response(dict(selected,peer=json.loads(peer.read_text()) if peer.exists() else None,
                    items=[rows.get(name,{'name':name,'phase':'idle','id':None}) for name in selected['files']]))
            if req.path=="/api/software/status":
                result=ROOT/'radio/diagnostic-result.json'
                from control.home_ha import overview
                monitoring=overview(ROOT)
                return self.response({"updates":software().journal.status(),"homeAssistant":monitoring['bridge'],'carLinks':monitoring['roles'],"radioConfigured":bool(os.getenv("CAR_CHIRPSTACK_APP_ID") and os.getenv("CAR_CHIRPSTACK_DEV_EUI")),"radio":json.loads((ROOT/"radio-status.json").read_text()) if (ROOT/"radio-status.json").exists() else {"phase":"unconfigured"},"radioDiagnostic":json.loads(result.read_text()) if result.exists() else {"phase":"pending","message":"Waiting for configured car radio, pinned encryption keys and home bridge."}})
            if req.path=="/api/layout-status":
                state=LayoutStore(ROOT/"layouts.sqlite3").get(name)
                return self.response({"generation":state["generation"],"revision":state["published"]["revision"],
                    "enabled":state["published"]["enabled"],"acknowledgedRevision":state["acknowledgedRevision"]})
            if req.path=="/api/layout":
                return self.response(LayoutStore(ROOT/"layouts.sqlite3").get(name))
            if req.path=="/api/config":
                return self.response(read_config(cfg_file))
            if req.path=="/api/pending":
                return self.response({"device":name,"items":[{"revision":v.get("configRevision",v["revision"]),
                   "sequence":v["revision"],"kind":v.get("kind"),"fragments":len(v.get("frames",[])),"queued":v.get("transmitted",0),
                   "acked":v.get("acked",False)} for v in read_outbox(queue_file)]})
            if req.path=="/api/radio-next":
                return self.response({"next":export_next(name)})
            return self.fail("Unknown endpoint",404)
        except (ValueError,OSError) as exc:
            return self.fail(exc)

    def do_POST(self):
        if self.path=="/api/device/software/exchange":
            if not self.device_access():return
            try:
                data=self.request_json();role=data["role"];service=software()
                if role not in ("tiny","duet","pi","laptop"):raise ValueError("Invalid role")
                telemetry=data.get('telemetryFrames',[])
                if not isinstance(telemetry,list) or len(telemetry)>2:raise ValueError('Too many sensor batches')
                from control.telemetry import decode as decode_sensors,store as store_sensors
                for raw in telemetry:
                    if not isinstance(raw,str) or len(raw)>92:raise ValueError('Invalid sensor batch')
                    sample=decode_sensors(bytes.fromhex(raw),radio_key())
                    if sample['source']!=role:raise ValueError('Sensor batch belongs to another role')
                    store_sensors(ROOT/'radio/sensors.sqlite3',sample,transport='ip')
                if role in ('tiny','pi') and 'radioPublicKey' in data:
                    from control.radio_crypto import pin
                    pin(ROOT/'radio/car.pub',data['radioPublicKey'])
                if role=='tiny' and 'radioDiagnostic' in data:
                    from control.radio_diagnostic import record_report
                    record_report(ROOT/'radio/diagnostic-result.json',data['radioDiagnostic'])
                service.receipts(DEVICE_NAME,role,data.get("receipts",[]))
                if "sources" in data:service.inventory(DEVICE_NAME,role,data["sources"])
                from control.file_sync import home_files
                files=home_files(ROOT,role,radio_key());results=[]
                if 'fileSelection' in data:
                    from control.file_sync import selected_names
                    from control.update_agent import atomic
                    atomic(ROOT/'sync-peer'/(role+'.json'),json.dumps({'files':selected_names(data['fileSelection']),'reportedAt':time.time()}).encode())
                objects=data.get('fileObjects',[])
                if not isinstance(objects,list) or len(objects)>4:raise ValueError('Too many sync objects')
                receipts=data.get('fileReceipts',[])
                if not isinstance(receipts,list) or len(receipts)>8:raise ValueError('Too many sync receipts')
                accepted=[]
                for item in receipts:
                    if item['doc'].get('type')!='file-receipt':raise ValueError('Receipt required')
                    files.receive_available(item['doc'],item['id'],via_ip=True);accepted.append(item['id'])
                for item in objects:
                    result=files.receive_available(item['doc'],item['id'],via_ip=True)
                    if result:results.append(result)
                files.scan()
                service.journal.note_ip(DEVICE_NAME,role)
                return self.response({"frames":service.outgoing(DEVICE_NAME,role),'fileResults':results,'fileObjects':files.pending(),'fileReceiptsAccepted':accepted})
            except (KeyError,TypeError,ValueError,OSError) as exc:return self.fail(exc)
        if self.path=="/api/device/layout-ack":
            if not self.device_access():return
            try:
                data=self.request_json()
                if not isinstance(data,dict) or set(data)!={"revision"}:
                    raise ValueError("Invalid layout acknowledgment")
                LayoutStore(ROOT/"layouts.sqlite3").ack(DEVICE_NAME,data["revision"])
                return self.response({"ok":True})
            except (TypeError,ValueError,OSError) as exc:
                return self.fail(exc)
        if self.path=="/api/device/ack":
            if not self.device_access():return
            try:
                data=self.request_json()
                if not isinstance(data,dict) or set(data)!={"revision"} or type(data["revision"]) is not int:
                    raise ValueError("Invalid acknowledgment")
                with LOCK:
                    cfg_file,queue_file=paths(DEVICE_NAME)
                    if not 0<=data["revision"]<=read_config(cfg_file)["revision"]:
                        raise ValueError("Invalid acknowledgment revision")
                    queue=read_outbox(queue_file)
                    # Configuration snapshots supersede older config deltas only.
                    queue=[item for item in queue if not (
                        item.get("kind")=="configuration" and
                        item.get("configRevision",float("inf"))<=data["revision"])]
                    save_outbox(queue_file,queue)
                return self.response({"ok":True})
            except (TypeError,ValueError,OSError) as exc:
                return self.fail(exc)
        if not self.access():return
        try:
            data=self.request_json()
            if not isinstance(data,dict):raise ValueError("Invalid object")
            if self.path=='/api/gateways':
                from control.coverage import register_gateway
                with LOCK:return self.response(register_gateway(ROOT/'gateways.json',data))
            if self.path=="/api/messages":
                from control.messages import enqueue,cancel
                from control.event_transfer import Events
                events=Events(ROOT/'radio/catalog.sqlite3',radio_key(),destination='home')
                if data.get('action')=='cancel':return self.response(cancel(events,data.get('id')))
                return self.response(enqueue(events,'home',data.get('destination','tiny'),data.get('text'),data.get('action','text'),data.get('clientId')))
            if self.path=="/api/software/publish":
                return self.response(software().publish(safe_device(data.get("device","car-1")),data["role"],data["path"],data["base"],data["source"]))
            if self.path=="/api/software/transfer":
                return self.response(software().delivery(safe_device(data.get('device','car-1')),data['id'],data['action'],data['revision'],data.get('value')))
            if self.path in ('/api/sync','/api/sync/file'):
                from control.file_sync import home_files,save_home_selection
                role=data.get('role','tiny')
                if self.path=='/api/sync':return self.response(save_home_selection(ROOT,role,data['files'],data['revision']))
                files=home_files(ROOT,role,radio_key())
                if data.get('action')=='save':return self.response(files.edit(data['name'],data['sha256'],data['source']))
                if data.get('action')=='resolve':return self.response(files.resolve(data['name'],data['id'],data['sha256'],data['choice'],data.get('source')))
                raise ValueError('Unknown file operation')
            if self.path=="/api/layout":
                if data.get("action")=="validate":
                    return self.response({"project":validate_project(data.get("project"))})
                name=safe_device(data.get("device","car-1"))
                return self.response(LayoutStore(ROOT/"layouts.sqlite3").update(
                    name,data["generation"],data["action"],data.get("project"),data.get("version")))
            if self.path=="/api/package":
                return self.response(queue_code_patch(data.get("device","car-1"),data["kind"],data["package"]))
            if self.path=="/api/change":
                return self.response(queue_change(data.get("device","car-1"),
                    data["baseRevision"],data["changes"]))
            # Link-layer bridge must use a second, restricted token in deployment.
            # Exposing ack/mark only on the admin API avoids unauthenticated faked delivery.
            if self.path=="/api/transmitted":
                return self.response({"ok":mark_transmitted(data["device"],data["revision"],data["index"])})
            if self.path=="/api/ack":
                return self.response({"ok":ack_revision(data["device"],data["revision"])})
            return self.fail("Unknown endpoint",404)
        except (KeyError,TypeError,ValueError,OverflowError,OSError) as exc:
            return self.fail(exc)

    def log_message(self,fmt,*args):
        # Never log authentication headers or body.
        print("home-control",self.client_address[0],fmt%args,flush=True)

def main():
    if not TOKEN or len(TOKEN)<24:
        raise SystemExit("Set CAR_PANEL_TOKEN (random >=24 characters) in environment.")
    safe_device(DEVICE_NAME)
    if KEY_HEX:radio_key()
    if DEVICE_TOKEN and (len(DEVICE_TOKEN)<32 or DEVICE_TOKEN==TOKEN):
        raise SystemExit("CAR_DEVICE_TOKEN must be a separate random token of at least 32 characters")
    if HOST not in ("127.0.0.1","::1","localhost"):
        raise SystemExit("Home panel must bind to loopback; access via SSH tunnel or authenticated proxy.")
    ROOT.mkdir(parents=True,exist_ok=True)
    print(f"Home control panel: http://{HOST}:{PORT}; state={ROOT}",flush=True)
    ThreadingHTTPServer((HOST,PORT),Handler).serve_forever()

if __name__=="__main__":
    main()
