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


def fetch_upstream(host, port, path, method="GET", body=None, content_type=None):
    conn = http.client.HTTPConnection(host, port, timeout=3)
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

    def dispatch(self):
        url = urlsplit(self.path)
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
    args = parser.parse_args()
    handler = type("ConfiguredProxy", (ProxyHandler,),
                   {"local_port": args.port, "upstream_port": args.upstream_port})
    with ThreadingHTTPServer(("127.0.0.1", args.port), handler) as server:
        print("Duet display at http://127.0.0.1:" + str(args.port), flush=True)
        server.serve_forever()


if __name__ == "__main__":
    main()
