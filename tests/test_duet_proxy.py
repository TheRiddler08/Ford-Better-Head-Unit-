"""Tests for the Duet local kiosk proxy; no real USB hardware required."""
from __future__ import annotations

import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, build_opener, ProxyHandler as URLProxyHandler

from duet.proxy import ProxyHandler

class TinyStub(BaseHTTPRequestHandler):
    def do_GET(self):
        body = (
            b'{"ok":true,"databaseExists":true}'
            if self.path == "/api/health" else
            b"<html><h1>Drive Console</h1></html>"
        )
        self.send_response(200)
        self.send_header("Content-Type", "application/json" if self.path.startswith("/api/") else "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        body=b'{"saved":true}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        pass

class ProxyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.upstream = ThreadingHTTPServer(("127.0.0.1", 0), TinyStub)
        cls.upstream_port = cls.upstream.server_port
        cls.worker = threading.Thread(target=cls.upstream.serve_forever, daemon=True)
        cls.worker.start()
        cls.Handler = type("DuetTestHandler", (ProxyHandler,),
                           {"upstream_port": cls.upstream_port, "local_port": 0})
        cls.proxy = ThreadingHTTPServer(("127.0.0.1",0),cls.Handler)
        cls.Handler.local_port = cls.proxy.server_port
        cls.proxy_worker = threading.Thread(target=cls.proxy.serve_forever,daemon=True)
        cls.proxy_worker.start()
        cls.url="http://127.0.0.1:"+str(cls.proxy.server_port)
        cls.client=build_opener(URLProxyHandler({}))

    @classmethod
    def tearDownClass(cls):
        cls.proxy.shutdown()
        cls.proxy.server_close()
        cls.upstream.shutdown()
        cls.upstream.server_close()

    def test_get_and_health(self):
        with self.client.open(self.url+"/",timeout=3) as r:
            self.assertIn(b"Drive Console",r.read())
        with self.client.open(self.url+"/__link",timeout=3) as r:
            self.assertEqual(r.status,200)

    def test_origin_guard_for_writes(self):
        body=json.dumps({"revision":1}).encode()
        def request(origin, content_type):
            headers={"Origin":origin, "Content-Type":content_type}
            return Request(self.url+"/api/headunit-prefs",data=body,headers=headers,method="POST")
        with self.assertRaises(HTTPError) as e:
            self.client.open(request("https://evil.example","application/json"),timeout=3)
        self.assertEqual(e.exception.code,403)
        with self.assertRaises(HTTPError) as e:
            self.client.open(request(self.url,"text/plain"),timeout=3)
        self.assertEqual(e.exception.code,415)
        with self.client.open(request(self.url,"application/json"),timeout=3) as r:
            self.assertTrue(json.load(r)["saved"])

    def test_disconnected_page_and_link_check(self):
        old=self.Handler.upstream_port
        self.Handler.upstream_port=0
        try:
            with self.assertRaises(HTTPError) as e:
                self.client.open(self.url+"/",timeout=3)
            self.assertEqual(e.exception.code,503)
            self.assertIn(b"reconnect automatically",e.exception.read())
            with self.assertRaises(HTTPError) as e:
                self.client.open(self.url+"/__link",timeout=3)
            self.assertEqual(e.exception.code,503)
        finally:
            self.Handler.upstream_port=old

if __name__ == "__main__":
    unittest.main()
