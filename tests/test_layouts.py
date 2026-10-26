import copy
import json
from pathlib import Path
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from control import home_server as home
from control.layouts import LayoutStore, project_default, read_delivery, validate_project
from control.tailscale_agent import sync_once


class LayoutTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = LayoutStore(self.root/'layouts.sqlite3')

    def test_draft_publish_restore_and_conflicts(self):
        project = project_default()
        project['mode'] = 'code'
        project['html'] = '<h1>My dashboard</h1>'
        state = self.store.update('car-1', 0, 'save', project)
        self.assertFalse(state['published']['enabled'])
        self.assertEqual(state['published']['revision'], 0)
        with self.assertRaises(ValueError):
            self.store.update('car-1', 0, 'publish', project)
        state = self.store.update('car-1', 1, 'publish', project)
        self.assertEqual(state['published']['revision'], 1)
        state = self.store.update('car-1', 2, 'disable')
        self.assertFalse(state['published']['enabled'])
        state = self.store.update('car-1', 3, 'restore', version=1)
        self.assertTrue(state['published']['enabled'])
        self.assertEqual(state['published']['revision'], 3)
        self.assertEqual(state['published']['project'], project)
        self.assertEqual(LayoutStore(self.store.path).get('car-1'), state)
        self.assertEqual(self.store.get('other')['generation'], 0)

    def test_ack_does_not_invalidate_draft_generation(self):
        state = self.store.update('car-1', 0, 'publish', project_default())
        self.store.ack('car-1', 1)
        state = self.store.update('car-1', state['generation'], 'save', project_default())
        self.assertEqual(state['acknowledgedRevision'], 1)
        for revision in [-1, 2, True, '1']:
            with self.assertRaises(ValueError):
                self.store.ack('car-1', revision)

    def test_validation_and_history_bounds(self):
        project = project_default()
        for field, value in [('width', -2), ('js', 'x'*400001), ('pages', []), ('mode', 'shell')]:
            invalid = copy.deepcopy(project)
            invalid[field] = value
            with self.assertRaises(ValueError):
                validate_project(invalid)
        for generation in range(23):
            state = self.store.update('car-1', generation, 'publish', project)
        self.assertEqual(len(state['history']), 20)
        self.assertEqual(state['published']['revision'], 23)


class LayoutDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.token = 'test-device-'+'d'*32
        self.admin = 'test-admin-'+'a'*32
        for key, value in {'ROOT': self.root/'home', 'DEVICE_TOKEN': self.token,
                           'TOKEN': self.admin, 'DEVICE_NAME': 'car-1', 'KEY_HEX': ''}.items():
            p = patch.object(home, key, value)
            p.start()
            self.addCleanup(p.stop)
        self.store = LayoutStore(home.ROOT/'layouts.sqlite3')
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), home.Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)
        self.base = 'http://127.0.0.1:'+str(self.server.server_port)
        self.layout_path = self.root/'car/layout.json'
        self.config_path = self.root/'car/config.json'

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def test_actual_http_save_publish_sync_ack_disable(self):
        project = project_default()
        project['mode'] = 'code'
        project['html'] = '<h1>Custom cockpit</h1>'
        project['js'] = "document.addEventListener('DOMContentLoaded',()=>{document.body.dataset.works='yes';});"
        request = Request(self.base+'/api/layout', data=json.dumps({
            'device': 'car-1', 'generation': 0, 'action': 'publish', 'project': project}).encode(),
            headers={'Authorization': 'Bearer '+self.admin})
        with urlopen(request) as response:
            self.assertEqual(json.load(response)['published']['revision'], 1)
        sync_once(self.base, self.token, self.config_path, self.layout_path)
        self.assertEqual(read_delivery(self.layout_path)['project'], project)
        self.assertEqual(self.store.get('car-1')['acknowledgedRevision'], 1)
        # A fresh agent retries safely after a restart.
        sync_once(self.base, self.token, self.config_path, self.layout_path)
        self.store.update('car-1', 1, 'disable')
        sync_once(self.base, self.token, self.config_path, self.layout_path)
        self.assertFalse(read_delivery(self.layout_path)['enabled'])

    def test_failed_layout_write_keeps_pending(self):
        self.store.update('car-1', 0, 'publish', project_default())
        with patch('control.tailscale_agent.write_delivery', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                sync_once(self.base, self.token, self.config_path, self.layout_path)
        self.assertEqual(self.store.get('car-1')['acknowledgedRevision'], 0)

    def test_device_cannot_edit_layout_or_read_admin_drafts(self):
        for path in ['/api/layout?device=other', '/api/layout']:
            with self.assertRaises(HTTPError) as error:
                urlopen(Request(self.base+path, headers={'Authorization': 'Bearer '+self.token}))
            self.assertEqual(error.exception.code, 401)


if __name__ == '__main__':
    unittest.main()
