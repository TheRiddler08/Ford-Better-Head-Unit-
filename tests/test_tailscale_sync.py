import copy
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from http.server import ThreadingHTTPServer

from control import home_server as home
from control.config_core import DEFAULT, read_config, write_config
from control.tailscale_agent import sync_once


class TailscaleSyncTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.token = "device-test-token-" + "x"*32
        for name, value in {"ROOT": self.root / "home", "TOKEN": "admin-"+"a"*32,
                            "DEVICE_TOKEN": self.token, "DEVICE_NAME": "car-1",
                            "KEY_HEX": ""}.items():
            patcher = patch.object(home, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), home.Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)
        self.base = "http://127.0.0.1:"+str(self.server.server_port)
        self.config = self.root / "car.json"

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def test_delivery_coalesces_revisions_and_repeat_ack(self):
        home.queue_change("car-1", 0, {"theme": "day"})
        home.queue_change("car-1", 1, {"home.map.visible": False})
        # Code packages must remain pending; this transport never executes them.
        _, queue = home.paths("car-1")
        entries = home.read_outbox(queue)
        entries.append({"kind": "ui-patch", "revision": 999})
        home.save_outbox(queue, entries)
        self.assertEqual(sync_once(self.base, self.token, self.config), 2)
        saved = read_config(self.config)
        self.assertEqual(saved["theme"], "day")
        self.assertFalse(saved["home"]["map"]["visible"])
        self.assertEqual(home.read_outbox(queue), [{"kind": "ui-patch", "revision": 999}])
        self.assertEqual(sync_once(self.base, self.token, self.config), 2)

    def test_failed_save_never_acknowledges(self):
        home.queue_change("car-1", 0, {"theme": "day"})
        with patch("control.tailscale_agent.write_config", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                sync_once(self.base, self.token, self.config)
        self.assertEqual(len(home.read_outbox(home.paths("car-1")[1])), 1)

    def test_restart_after_lost_ack(self):
        home.queue_change("car-1", 0, {"theme": "day"})
        # Simulate a crash after save, before the ACK arrives.
        write_config(self.config, read_config(home.paths("car-1")[0]))
        self.assertEqual(sync_once(self.base, self.token, self.config), 1)
        self.assertEqual(home.read_outbox(home.paths("car-1")[1]), [])

    def test_auth_and_device_scope(self):
        for route, token in [("/api/device/config", "wrong"),
                             ("/api/config", self.token),
                             ("/api/device/config", home.TOKEN)]:
            with self.assertRaises(HTTPError) as error:
                urlopen(Request(self.base+route, headers={"Authorization": "Bearer "+token}))
            self.assertEqual(error.exception.code, 401)
        request = Request(self.base+"/api/device/ack", data=json.dumps({"revision": 99}).encode(),
                          headers={"Authorization": "Bearer "+self.token})
        with self.assertRaises(HTTPError) as error:
            urlopen(request)
        self.assertEqual(error.exception.code, 400)

    def test_rollback_conflict_and_destination_rejected(self):
        newer = copy.deepcopy(DEFAULT)
        newer["revision"] = 5
        write_config(self.config, newer)
        with self.assertRaises(ValueError):
            sync_once(self.base, self.token, self.config)
        newer["revision"] = 0
        newer["theme"] = "day"
        write_config(self.config, newer)
        with self.assertRaises(ValueError):
            sync_once(self.base, self.token, self.config)
        with self.assertRaises(ValueError):
            sync_once("http://example.com", self.token, self.config)
