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

DEVICE_RE = re.compile(r"^[a-zA-Z0-9_-]{1,48}$")
LOCK = threading.RLock()
ROOT = Path(os.environ.get("CAR_HOME_STATE", str(Path.home()/".local/share/car-home-control")))
UI = Path(__file__).parent / "web"
TOKEN = os.getenv("CAR_PANEL_TOKEN", "")
KEY_HEX = os.getenv("CAR_LINK_KEY", "")
HOST = os.getenv("CAR_PANEL_BIND", "127.0.0.1")
PORT = int(os.getenv("CAR_PANEL_PORT","8790"))

def radio_key():
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
        frames=split_message("configuration",next_config["revision"],
                             {"revision":next_config["revision"],"changes":compact},radio_key())
        existing=read_outbox(queue_file)
        item={"revision":next_config["revision"],"created":int(time.time()),
              "kind":"configuration","frames":[b.hex() for b in frames],
              "transmitted":0,"acked":False}
        # Preserve in-flight revisions; each must be applied in order on car.
        if len(existing)>=64:raise ValueError("Too many pending revisions; reconcile offline device first")
        existing.append(item)
        save_outbox(queue_file,existing)
        write_config(cfg_file,next_config)
        return {"revision":next_config["revision"],"frames":len(frames),"bytes":sum(map(len,frames))}

def export_next(name):
    """For ChirpStack bridge: one queued frame at a time, never flood downlinks."""
    with LOCK:
        _,queue_file=paths(name)
        queue=read_outbox(queue_file)
        for item in queue:
            if not item.get("acked") and item.get("transmitted",0)<len(item.get("frames",[])):
                i=item["transmitted"]
                return {"revision":item["revision"],"index":i,"frame_hex":item["frames"][i]}
        return None

def mark_transmitted(name, revision, index):
    """Call only after network-server queue successfully accepts the downlink."""
    with LOCK:
        _,queue_file=paths(name)
        queue=read_outbox(queue_file)
        for item in queue:
            if item["revision"]==revision and item["transmitted"]==index:
                item["transmitted"]=index+1
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
    def response(self, data, status=200, mime="application/json; charset=utf-8"):
        raw=data if isinstance(data,bytes) else json.dumps(data,separators=(",",":")).encode()
        self.send_response(status)
        for key,val in {"Content-Type":mime,"Content-Length":str(len(raw)),
                        "Cache-Control":"no-store","X-Content-Type-Options":"nosniff",
                        "Content-Security-Policy":"default-src 'self'; script-src 'self'; style-src 'self'; base-uri 'none'; frame-ancestors 'none'"}.items():
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
        if length<=0 or length>8192:raise ValueError("Invalid request length")
        return json.loads(self.rfile.read(length))

    def do_GET(self):
        req=urlsplit(self.path)
        if req.path in ("/","/panel.js","/panel.css"):
            name,mime={"/":("index.html","text/html; charset=utf-8"),
                       "/panel.js":("panel.js","text/javascript; charset=utf-8"),
                       "/panel.css":("panel.css","text/css; charset=utf-8")}[req.path]
            return self.response((UI/name).read_bytes(),mime=mime)
        if not self.access():return
        try:
            name=parse_qs(req.query).get("device",["car-1"])[0]
            cfg_file,queue_file=paths(name)
            if req.path=="/api/config":
                return self.response(read_config(cfg_file))
            if req.path=="/api/pending":
                return self.response({"device":name,"items":[{"revision":v["revision"],
                   "fragments":len(v.get("frames",[])),"queued":v.get("transmitted",0),
                   "acked":v.get("acked",False)} for v in read_outbox(queue_file)]})
            if req.path=="/api/radio-next":
                return self.response({"next":export_next(name)})
            return self.fail("Unknown endpoint",404)
        except (ValueError,OSError) as exc:
            return self.fail(exc)

    def do_POST(self):
        if not self.access():return
        try:
            data=self.request_json()
            if not isinstance(data,dict):raise ValueError("Invalid object")
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
    radio_key()
    if HOST not in ("127.0.0.1","::1","localhost"):
        raise SystemExit("Home panel must bind to loopback; access via SSH tunnel or authenticated proxy.")
    ROOT.mkdir(parents=True,exist_ok=True)
    print(f"Home control panel: http://{HOST}:{PORT}; state={ROOT}",flush=True)
    ThreadingHTTPServer((HOST,PORT),Handler).serve_forever()

if __name__=="__main__":
    main()
