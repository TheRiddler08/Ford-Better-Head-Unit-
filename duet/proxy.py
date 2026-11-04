#!/usr/bin/env python3
"""Loopback-only proxy for the Duet touchscreen, with a reconnecting offline page.

The Tiny's dashboard remains bound to 127.0.0.1:8787. An authenticated SSH
local forward on the Duet provides its upstream at 127.0.0.1:8788.
No service on the USB network is exposed directly by this proxy.
"""
from __future__ import annotations

import argparse
import http.client
import json
import os
from pathlib import Path
import signal
import time
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

MAX_RESPONSE = 16 * 1024 * 1024
MAX_REQUEST = 12000

OFFLINE_HTML = b"""<!doctype html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="10">
<title>Drive Console - Reconnecting</title>
<style>html{color-scheme:dark;font-family:system-ui;background:#081422;color:#f3f7ff}
body{min-height:85vh;display:grid;place-items:center;text-align:center;margin:0;padding:20px}
section{max-width:500px}h1{font-size:2rem}p{color:#9eb7cc}</style></head>
<body><section><h1>Connecting to the ThinkCentre...</h1>
<p>Your display is working. Waiting for the USB connection and head-unit computer.
Vehicle data and controls are temporarily unavailable.</p>
<p>The screen will reconnect automatically.</p></section>
<script>setInterval(async()=>{try{const r=await fetch('/__link',{cache:'no-store'});
if(r.ok)location.replace('/');}catch(e){}},2000)</script></body></html>"""


START_HTML=b"""<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Car dashboard</title><style>body{background:#081422;color:#eaf5ff;font:20px system-ui;padding:10vh 8vw}a{display:inline-block;color:#8dddff;padding:18px;border:1px solid #37546c;border-radius:12px;margin:12px 12px 12px 0}p{color:#a4bbd0}</style><h1>Car dashboard</h1><p id="status">Connecting to the Tiny...</p><a href="/cockpit.html">Open standard dashboard / Recovery</a><a href="/records">Reports and GPS archive</a><script>async function check(){try{const r=await fetch('/__start-status',{cache:'no-store'});const data=await r.json();document.getElementById('status').textContent=data.message;if(data.target){const target=new URL(data.target,location.href);if(target.port==='8878')target.searchParams.set('ha_cache_bust',String(Date.now()));location.replace(target.href);return;}}catch(_){}setTimeout(check,500);}check();</script></html>"""


_HA_REFRESH_AT=None
_HA_REFRESH_LOCK=threading.Lock()


def refresh_ha_tunnel(upstream_port=8788,force=False,proc=Path('/proc')):
    """Reconnect only this user's managed display SSH client, never sshd/Tailscale.

    SSH key permitopen changes apply on new connections, not existing sessions.
    The launcher's existing reconnect loop restarts the selected client.
    """
    global _HA_REFRESH_AT
    with _HA_REFRESH_LOCK:
        now=time.monotonic()
        if not force and _HA_REFRESH_AT is not None and now-_HA_REFRESH_AT<60:return 0
        _HA_REFRESH_AT=now
        key=os.fsencode(str(Path.home()/'.ssh/drive-console-duet'))
        forward=f'127.0.0.1:{upstream_port}:127.0.0.1:8787'.encode()
        count=0
        for entry in proc.iterdir():
            if not entry.name.isdigit() or int(entry.name)==os.getpid():continue
            fd=None
            try:
                if entry.stat().st_uid!=os.getuid():continue
                # A pidfd, where supported, prevents signalling a reused PID.
                if hasattr(os,'pidfd_open') and hasattr(signal,'pidfd_send_signal'):
                    try:fd=os.pidfd_open(int(entry.name))
                    except OSError:fd=None # Older kernels may expose Python's API without the syscall.
                raw=(entry/'cmdline').read_bytes();argv=raw.rstrip(b'\0').split(b'\0')
                if not argv or Path(os.fsdecode(argv[0])).name!='ssh' or b'-N' not in argv:continue
                if not any(argv[i:i+2]==[b'-i',key] for i in range(len(argv)-1)):continue
                if not any(argv[i:i+2]==[b'-L',forward] for i in range(len(argv)-1)):continue
                if fd is not None:signal.pidfd_send_signal(fd,signal.SIGTERM)
                elif (entry/'cmdline').read_bytes()==raw:os.kill(int(entry.name),signal.SIGTERM)
                else:continue
                count+=1
            except (OSError,ValueError):continue
            finally:
                if fd is not None:os.close(fd)
        return count


def fetch_upstream(host, port, path, method="GET", body=None, content_type=None, timeout=3):
    conn = http.client.HTTPConnection(host, port, timeout=timeout)
    try:
        headers = {"Accept": "*/*", "Cache-Control": "no-store"}
        if body is not None:
            headers["Content-Type"] = content_type or "application/json"
            headers["Origin"] = "http://127.0.0.1:8787"
        conn.request(method, path, body=body, headers=headers)
        response = conn.getresponse()
        data = response.read(MAX_RESPONSE + 1)
        if len(data) > MAX_RESPONSE:
            raise ValueError("Upstream response too large")
        return response.status, dict(response.getheaders()), data
    finally:
        conn.close()


class ProxyHandler(BaseHTTPRequestHandler):
    upstream_host = "127.0.0.1"
    upstream_port = 8788
    local_port = 8877

    def reply(self, code, body, content_type, extra=None):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        if extra:
            for k, v in extra.items():
                if k.lower() in ("content-security-policy", "referrer-policy"):
                    self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def do_HEAD(self):
        return self.dispatch()

    def do_GET(self):
        return self.dispatch()

    def do_POST(self):
        return self.dispatch()

    def start_status(self):
        # Reading the reporter is bounded; a slow recorder must never hide an
        # already-running native frontend. Preserve explicit disabled settings.
        settings={};code=None
        try:
            code,_,raw=fetch_upstream(self.upstream_host,self.upstream_port,
                "/api/home-assistant",timeout=.25)
            try:settings=json.loads(raw)
            except (ValueError,UnicodeError):pass
            if not isinstance(settings,dict):settings={}
        except (OSError,TimeoutError,http.client.HTTPException,ValueError):pass
        if code==200 and (settings.get('enabled') is False or settings.get('installed') is False):
            return {'target':'/','message':'Opening the standard dashboard'}
        conn=None
        try:
            conn=http.client.HTTPConnection('127.0.0.1',8878,timeout=.5)
            conn.request('GET','/manifest.json');response=conn.getresponse()
            manifest=json.loads(response.read(65536))
            names=' '.join(str(manifest.get(key,'')) for key in ('name','short_name')) if isinstance(manifest,dict) else ''
            identity=names.lower().replace(' ','')
            if response.status==200 and ('homeassistant' in identity or 'carassistant' in identity):
                return {'target':'http://127.0.0.1:8878/drive-console','message':'Car Assistant dashboard is ready'}
            if response.status==200:
                return {'target':None,'message':'Port 8878 was not identified as Car Assistant. Check the kiosk log.'}
        except (OSError,ValueError,http.client.HTTPException):pass
        finally:
            if conn is not None:conn.close()
        if code==200 and settings.get('installed') and settings.get('enabled'):
            if settings.get('ready') is True:
                refreshed=refresh_ha_tunnel(self.upstream_port)
                if refreshed:return {'target':None,'message':'Refreshing the Home Assistant SSH tunnel; retrying automatically.'}
            return {'target':None,'message':'Waiting for Car Assistant and its SSH tunnel. Recovery is available below.'}
        if code in (200,404):return {'target':'/','message':'Opening the standard dashboard'}
        return {'target':None,'message':'Waiting for the Tiny wired connection. The display will reconnect automatically.'}

    def stream_csv(self):
        """Export without buffering/capping the lifetime archive in tablet RAM."""
        conn=http.client.HTTPConnection(self.upstream_host,self.upstream_port,timeout=15)
        started=False
        try:
            headers={"Accept":"*/*"}
            if self.headers.get("Range"):headers["Range"]=self.headers["Range"]
            conn.request("GET",self.path,headers=headers)
            response=conn.getresponse()
            if response.status not in (200,206):
                return self.reply(response.status,response.read(65536),response.getheader("Content-Type","text/plain"))
            self.protocol_version="HTTP/1.1"
            self.send_response(response.status)
            self.send_header("Content-Type",response.getheader("Content-Type","application/octet-stream"))
            for header in ("Content-Disposition","Accept-Ranges","Content-Range"):
                if response.getheader(header):self.send_header(header,response.getheader(header))
            self.send_header("Transfer-Encoding","chunked")
            self.send_header("Cache-Control","no-store")
            self.send_header("Connection","close")
            self.send_header("X-Content-Type-Options","nosniff")
            self.end_headers();started=True
            while True:
                chunk=response.read(65536)
                if not chunk:break
                self.wfile.write((format(len(chunk),"x")+"\r\n").encode()+chunk+b"\r\n")
            self.wfile.write(b"0\r\n\r\n")
        except (OSError,TimeoutError,http.client.HTTPException):
            if not started:self.reply(503,OFFLINE_HTML,"text/html; charset=utf-8")
            # A truncated chunked stream deliberately has no success terminator.
        finally:
            self.close_connection=True
            conn.close()

    def dispatch(self):
        url = urlsplit(self.path)
        if self.command=="GET" and url.path in ("/__start","/home-assistant"):
            return self.reply(200,START_HTML,"text/html; charset=utf-8",{'Content-Security-Policy':"default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'"})
        if self.command=="GET" and url.path=="/__start-status":
            return self.reply(200,json.dumps(self.start_status()).encode(),"application/json")
        if self.command=="GET" and url.path in ("/api/report/export","/api/dashcam/clip"):
            return self.stream_csv()
        if url.path == "/__link":
            try:
                status, _, _ = fetch_upstream(
                    self.upstream_host, self.upstream_port, "/api/health"
                )
                status = 200 if status == 200 else 503
            except (OSError, TimeoutError, http.client.HTTPException, ValueError):
                status = 503
            return self.reply(status, b"ok" if status == 200 else b"offline", "text/plain")
        if self.command == "POST":
            # A web page on another origin may not change vehicle preferences
            # through this localhost proxy.
            expected = "http://127.0.0.1:" + str(self.local_port)
            if self.headers.get("Origin") != expected:
                return self.reply(403, b'{"error":"Untrusted origin"}', "application/json")
            if self.headers.get("Content-Type", "").split(";")[0].strip().lower() != "application/json":
                return self.reply(415, b'{"error":"JSON required"}', "application/json")
            try:
                size = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                size = 0
            if not 0 < size <= MAX_REQUEST:
                return self.reply(413, b'{"error":"Invalid request size"}', "application/json")
            body = self.rfile.read(size)
        elif self.command in ("GET", "HEAD"):
            body = None
        else:
            return self.reply(405, b"", "text/plain")

        try:
            path = self.path
            # Only origin-form paths, never arbitrary absolute URLs.
            if not path.startswith("/") or path.startswith("//"):
                raise ValueError("Invalid path")
            # Always use GET upstream for HEAD: keeps the original server simple.
            method = "GET" if self.command == "HEAD" else self.command
            status, headers, payload = fetch_upstream(
                self.upstream_host, self.upstream_port, path, method, body
            )
            # Do not propagate hop-by-hop headers or set cookies across hostnames.
            safe = {k: v for k, v in headers.items()
                    if k.lower() in ("content-security-policy", "referrer-policy")}
            content_type = next(
                (v for k, v in headers.items() if k.lower() == "content-type"),
                "application/octet-stream",
            )
            self.reply(status, payload, content_type, safe)
        except (OSError, TimeoutError, http.client.HTTPException, ValueError):
            if self.command == "GET" and url.path in ("/", "/cockpit.html", "/index.html"):
                self.reply(503, OFFLINE_HTML, "text/html; charset=utf-8",
                           {"Content-Security-Policy": "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'"})
            else:
                self.reply(503, b'{"error":"ThinkCentre disconnected"}', "application/json")

    def log_message(self, fmt, *args):
        # Kiosk health checks are frequent; keep logs quiet unless an error occurs.
        if not self.path.startswith("/__link"):
            super().log_message(fmt, *args)


def main():
    parser = argparse.ArgumentParser(description="Duet offline-capable USB head unit proxy")
    parser.add_argument("--port", type=int, default=8877)
    parser.add_argument("--upstream-port", type=int, default=8788)
    parser.add_argument("--refresh-ha-tunnel",action="store_true",help="Reconnect only your managed display SSH tunnel; safe from an SSH terminal")
    args = parser.parse_args()
    if args.refresh_ha_tunnel:
        count=refresh_ha_tunnel(args.upstream_port,force=True)
        print(json.dumps({"displayTunnelsReconnecting":count,"message":"The kiosk reconnect loop should restore forwarding in a few seconds" if count else "No owned managed display SSH tunnel was found; inspect kiosk.log"}))
        return
    handler = type("ConfiguredProxy", (ProxyHandler,),
                   {"local_port": args.port, "upstream_port": args.upstream_port})
    with ThreadingHTTPServer(("127.0.0.1", args.port), handler) as server:
        print("Duet display at http://127.0.0.1:" + str(args.port), flush=True)
        server.serve_forever()


if __name__ == "__main__":
    main()
