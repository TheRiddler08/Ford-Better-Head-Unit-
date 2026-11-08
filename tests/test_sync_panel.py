"""Authenticated file controls use the actual IP exchange and data journals."""
import importlib.util
import json
from pathlib import Path
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import build_opener,ProxyHandler,Request
from unittest.mock import patch
from control import home_server as panel
from control.file_sync import home_files
from control.update_agent import Agent


@unittest.skipUnless(importlib.util.find_spec('cryptography') and importlib.util.find_spec('nacl'),'Normal home dependencies required')
class SyncPanelTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.state=self.root/'server';self.state.mkdir();self.admin='a'*40;self.device='d'*40
        for key,value in [('ROOT',self.state),('TOKEN',self.admin),('DEVICE_TOKEN',self.device),('KEY_HEX','')]:
            patcher=patch.object(panel,key,value);patcher.start();self.addCleanup(patcher.stop)
        self.server=ThreadingHTTPServer(('127.0.0.1',0),panel.Handler)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        def stop():self.server.shutdown();self.server.server_close();self.thread.join()
        self.addCleanup(stop);self.base=f'http://127.0.0.1:{self.server.server_port}'
        self.http=build_opener(ProxyHandler({}))

    def request(self,path,data=None,token=None):
        headers={'Authorization':'Bearer '+(token or self.admin),'Content-Type':'application/json'}
        request=Request(self.base+path,data=None if data is None else json.dumps(data).encode(),headers=headers)
        with self.http.open(request,timeout=5) as response:return json.loads(response.read())

    def select(self):
        view=self.request('/api/sync?role=tiny')
        return self.request('/api/sync',{'role':'tiny','files':['notes.txt'],'revision':view['revision']})

    def test_admin_access_selection_versions_and_safe_selected_paths(self):
        for path in ['/api/sync?role=tiny','/api/sync/file?role=tiny&name=notes.txt']:
            with self.assertRaises(HTTPError) as error:self.request(path,token=self.device)
            self.assertEqual(error.exception.code,401)
        old=self.select();self.assertEqual(old['files'],['notes.txt'])
        current=self.request('/api/sync',{'role':'tiny','files':['notes.txt','settings.json'],'revision':old['revision']})
        with self.assertRaises(HTTPError):self.request('/api/sync',{'role':'tiny','files':[],'revision':old['revision']})
        for name in ['../private.txt','unselected.txt','script.py']:
            from urllib.parse import quote
            with self.assertRaises(HTTPError) as error:self.request('/api/sync/file?role=tiny&name='+quote(name,safe=''))
            self.assertEqual(error.exception.code,400)
        self.assertEqual(self.request('/api/sync?role=duet')['files'],[])
        file=self.request('/api/sync/file?role=tiny&name=settings.json')
        with self.assertRaises(HTTPError):self.request('/api/sync/file',{'action':'save','role':'tiny','name':'settings.json','sha256':file['sha256'],'source':'{bad json'})
        self.assertFalse((self.state/'sync/tiny/settings.json').exists())

    def test_owner_conflict_choice_round_trips_through_real_authenticated_ip(self):
        self.select();home=self.root/'car';creds=home/'.config/car-dashboard';creds.mkdir(parents=True)
        (creds/'device-token').write_text(self.device);repo=self.root/'repo';repo.mkdir()
        local=home/'car-console/sync';local.mkdir(parents=True);(local/'notes.txt').write_text('common')
        agent=Agent({'role':'tiny','home':str(home),'repo':str(repo),'sync_files':['notes.txt'],'home_url':self.base})
        agent.exchange();remote=self.state/'sync/tiny/notes.txt'
        self.assertEqual(self.request('/api/sync?role=tiny')['peer']['files'],['notes.txt'])
        (local/'notes.txt').write_text('car edit');remote.write_text('home edit');agent.exchange()
        file=self.request('/api/sync/file?role=tiny&name=notes.txt');self.assertEqual(file['conflicts'][0]['incoming'],'car edit')
        result=self.request('/api/sync/file',{'action':'resolve','role':'tiny','name':'notes.txt','id':file['conflicts'][0]['id'],'sha256':file['sha256'],'choice':'home'})
        self.assertIsNotNone(result['pending']) # A local owner choice is not remote application.
        for _ in range(5):agent.exchange()
        self.assertEqual((local/'notes.txt').read_text(),'home edit');self.assertEqual(remote.read_text(),'home edit')
        self.assertFalse(home_files(self.state,'tiny',agent.key()).pending())
        status=self.request('/api/software/status')
        self.assertNotIn('home edit',json.dumps(status));self.assertNotIn('car edit',json.dumps(status))
