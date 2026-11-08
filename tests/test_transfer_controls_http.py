"""Pausing and resuming through the owner API preserves the real IP update."""
import importlib.util
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from http.server import ThreadingHTTPServer
from urllib.request import build_opener,ProxyHandler,Request
from urllib.error import HTTPError
from control import home_server as panel
from control.software import Software
from control.update_agent import Agent


@unittest.skipUnless(importlib.util.find_spec('cryptography') and importlib.util.find_spec('nacl'),'Normal home dependencies required')
class TransferHTTPTests(unittest.TestCase):
    def test_admin_controls_pause_real_updates_and_same_identity_resumes_to_applied(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);home=root/'car';creds=home/'.config/car-dashboard';creds.mkdir(parents=True)
            admin='a'*40;device='d'*40;(creds/'device-token').write_text(device)
            repo=root/'repo';(repo/'app/web').mkdir(parents=True);(repo/'app/web/cockpit.js').write_text('const x=1;')
            server=ThreadingHTTPServer(('127.0.0.1',0),panel.Handler);thread=threading.Thread(target=server.serve_forever,daemon=True)
            with patch.object(panel,'ROOT',root/'server'),patch.object(panel,'TOKEN',admin),patch.object(panel,'DEVICE_TOKEN',device),patch.object(panel,'KEY_HEX',''),patch.object(panel,'DEVICE_NAME','car-1'):
                thread.start();base=f'http://127.0.0.1:{server.server_port}';http=build_opener(ProxyHandler({}))
                def request(path,data=None,token=admin):
                    req=Request(base+path,data=json.dumps(data).encode() if data is not None else None,headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
                    with http.open(req,timeout=5) as response:return json.loads(response.read())
                try:
                    agent=Agent({'role':'tiny','home':str(home),'repo':str(repo),'overrides':str(root/'overlays'),'home_url':base});agent.exchange()
                    service=Software(root/'server/software');source=service.source('car-1','tiny','app/web/cockpit.js')
                    ident=service.publish('car-1','tiny',source['path'],source['base'],'const x=2;')['id']
                    queue=request('/api/software/transfers');self.assertEqual(queue['items'][0]['id'],ident)
                    self.assertEqual(queue['items'][0]['decision'],'ip-preferred')
                    for path in ['/api/software/transfers','/api/software/transfer?id='+ident]:
                        with self.assertRaises(HTTPError) as error:request(path,token=device)
                        self.assertEqual(error.exception.code,401)
                    action={'device':'car-1','id':ident,'action':'pause','revision':0}
                    with self.assertRaises(HTTPError):request('/api/software/transfer',action,token=device)
                    request('/api/software/transfer',action)
                    for _ in range(2):agent.exchange()
                    self.assertFalse((root/'overlays/app/web/cockpit.js').exists())
                    self.assertEqual(request('/api/software/transfer?id='+ident)['items'][0]['displayPhase'],'paused')
                    with self.assertRaises(HTTPError):request('/api/software/transfer',dict(action,action='resume'))
                    request('/api/software/transfer',dict(action,action='resume',revision=1))
                    for _ in range(50):
                        agent.exchange()
                        if service.journal.status()[0]['phase']=='applied':break
                    else:self.fail('Resumed signed transfer did not apply')
                    self.assertEqual((root/'overlays/app/web/cockpit.js').read_text(),'const x=2;')
                    row=request('/api/software/transfers?state=finished')['items'][0]
                    self.assertEqual(row['id'],ident);self.assertEqual(row['phase'],'applied')
                    with self.assertRaises(HTTPError):request('/api/software/transfer',dict(action,action='retry',revision=2))
                    raw=json.dumps(queue);self.assertNotIn(device,raw);self.assertNotIn('PRIVATE KEY',raw);self.assertNotIn('const x=2;',raw)
                finally:server.shutdown();server.server_close();thread.join()
