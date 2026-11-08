import importlib.util
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from http.server import ThreadingHTTPServer
from control import home_server as panel
from control.update_agent import Agent
from control.software import Software
from control.update_transfer import digest,decode,ACK


@unittest.skipUnless(importlib.util.find_spec('cryptography'),'native cryptography is not installed yet')
class LinkTests(unittest.TestCase):
    @unittest.skipUnless(importlib.util.find_spec('nacl'),'PyNaCl enrollment dependency pending')
    def test_blocked_files_do_not_interrupt_signed_source_update_or_other_recovery(self):
        from control.file_sync import car_files,home_files
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);repo=root/'repo';(repo/'app/web').mkdir(parents=True)
            (repo/'app/web/cockpit.js').write_text('let speed = 10;\n')
            home=root/'device';creds=home/'.config/car-dashboard';creds.mkdir(parents=True)
            token='d'*40;(creds/'device-token').write_text(token)
            local=home/'car-console/sync';local.mkdir(parents=True)
            (local/'notes.txt').write_text('x'*20000);(local/'other.txt').write_text('valid car data')
            state=root/'server';state.mkdir();(state/'sync-settings.json').write_text(json.dumps({'files':['notes.txt','other.txt']}))
            remote=state/'sync/tiny';remote.mkdir(parents=True);(remote/'other.txt').write_bytes(b'\xff')
            server=ThreadingHTTPServer(('127.0.0.1',0),panel.Handler);thread=threading.Thread(target=server.serve_forever,daemon=True)
            with patch.object(panel,'ROOT',state),patch.object(panel,'DEVICE_TOKEN',token),patch.object(panel,'DEVICE_NAME','car-1'):
                thread.start()
                try:
                    config={'role':'tiny','home':str(home),'repo':str(repo),'overrides':str(root/'overlays'),
                        'sync_files':['notes.txt','other.txt'],'home_url':f'http://127.0.0.1:{server.server_port}'}
                    agent=Agent(config);agent.exchange();service=Software(state/'software')
                    source=service.source('car-1','tiny','app/web/cockpit.js')
                    service.publish('car-1','tiny',source['path'],source['base'],'let speed = 20;\n')
                    for _ in range(50):
                        agent.exchange()
                        if service.journal.status()[0]['phase']=='applied':break
                    else:self.fail('A blocked data file interrupted signed source delivery')
                    self.assertEqual((root/'overlays/app/web/cockpit.js').read_text(),'let speed = 20;\n')
                    self.assertEqual(next(row for row in car_files(config).status() if row['name']=='notes.txt')['phase'],'blocked')
                    self.assertEqual(home_files(state,'tiny',agent.key()).status()[0]['phase'],'blocked')
                    pending_id=car_files(config).pending()[0]['id']
                    (remote/'other.txt').unlink();(local/'notes.txt').write_text('repaired local file')
                    agent.exchange();agent.exchange()
                    self.assertEqual((remote/'notes.txt').read_text(),'repaired local file')
                    self.assertEqual((remote/'other.txt').read_text(),'valid car data')
                    self.assertFalse(car_files(config).pending())
                    with home_files(state,'tiny',agent.key()).db() as db:self.assertIsNotNone(db.execute('SELECT 1 FROM applied WHERE id=?',(pending_id,)).fetchone())
                finally:server.shutdown();server.server_close();thread.join()

    @unittest.skipUnless(importlib.util.find_spec('nacl'),'PyNaCl enrollment dependency pending')
    def test_real_http_selected_file_sync_is_bidirectional_and_confirms_applied(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);repo=root/'repo';repo.mkdir();home=root/'device';creds=home/'.config/car-dashboard';creds.mkdir(parents=True)
            token='d'*40;(creds/'device-token').write_text(token)
            local=home/'car-console/sync';local.mkdir(parents=True);(local/'notes.txt').write_text('car '+'x'*3000)
            state=root/'server';state.mkdir();(state/'sync-settings.json').write_text(json.dumps({'files':['notes.txt']}))
            server=ThreadingHTTPServer(('127.0.0.1',0),panel.Handler);thread=threading.Thread(target=server.serve_forever,daemon=True)
            with patch.object(panel,'ROOT',state),patch.object(panel,'DEVICE_TOKEN',token),patch.object(panel,'DEVICE_NAME','car-1'):
                thread.start()
                try:
                    config={'role':'tiny','home':str(home),'repo':str(repo),'overrides':str(root/'overrides'),'sync_files':['notes.txt'],'home_url':f'http://127.0.0.1:{server.server_port}'}
                    agent=Agent(config);agent.exchange()
                    remote=state/'sync/tiny/notes.txt';self.assertEqual(remote.read_text(),(local/'notes.txt').read_text())
                    from control.file_sync import car_files,home_files
                    self.assertEqual(car_files(config).status()[0]['phase'],'applied')
                    remote.write_text('home '+'y'*7000)
                    agent.exchange();agent.exchange()
                    self.assertEqual((local/'notes.txt').read_text(),remote.read_text())
                    self.assertEqual(home_files(state,'tiny',agent.key()).pending(),[])
                finally:server.shutdown();server.server_close();thread.join()

    @unittest.skipUnless(importlib.util.find_spec('nacl'),'PyNaCl is installed during role provisioning; allow old receiver bootstrap')
    def test_real_authenticated_http_editor_publish_and_automatic_overlay(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);repo=root/'repo';(repo/'app/web').mkdir(parents=True)
            (repo/'app/web/cockpit.js').write_text('let speed = 10;\n')
            home=root/'device';(home/'.config/car-dashboard').mkdir(parents=True)
            token='d'*40;(home/'.config/car-dashboard/device-token').write_text(token)
            server=ThreadingHTTPServer(('127.0.0.1',0),panel.Handler)
            thread=threading.Thread(target=server.serve_forever,daemon=True)
            with patch.object(panel,'ROOT',root/'server'),patch.object(panel,'DEVICE_TOKEN',token),patch.object(panel,'DEVICE_NAME','car-1'):
                thread.start()
                try:
                    agent=Agent({'role':'tiny','device':'car-1','home':str(home),'repo':str(repo),'overrides':str(root/'overlays'),'home_url':f'http://127.0.0.1:{server.server_port}'})
                    agent.exchange()
                    from control.radio_crypto import identity
                    self.assertEqual((root/'server/radio/car.pub').read_bytes(),bytes(identity(home/'.config/car-dashboard/radio-private.key').public_key))
                    from control.radio_diagnostic import Diagnostic
                    diagnostic=Diagnostic(agent.state/'radio-test-result.json',agent.key())
                    diagnostic.start()
                    agent.exchange()
                    report=json.loads((root/'server/radio/diagnostic-result.json').read_text())
                    self.assertEqual(report['phase'],'running')
                    self.assertEqual(report['source'],'authenticated-car-report')
                    service=Software(root/'server/software')
                    source=service.source('car-1','tiny','app/web/cockpit.js')
                    job=service.publish('car-1','tiny',source['path'],source['base'],'let speed = 20;\n')
                    for _ in range(50):
                        agent.exchange()
                        if service.journal.status()[0]['phase']=='applied':break
                    else:self.fail('Automatic IP update did not reach applied state')
                    self.assertEqual((root/'overlays/app/web/cockpit.js').read_text(),'let speed = 20;\n')
                    self.assertEqual((repo/'app/web/cockpit.js').read_text(),'let speed = 10;\n')
                    self.assertEqual(service.journal.status()[0]['lastTransport'],'ip')
                    self.assertEqual(service.source('car-1','tiny',source['path'])['source'],'let speed = 20;\n')
                finally:server.shutdown();server.server_close();thread.join()

    @unittest.skipUnless(importlib.util.find_spec('nacl'),'PyNaCl enrollment dependency pending')
    def test_explicit_pi_radio_still_enrolls_bidirectional_public_keys(self):
        from control.radio_crypto import RadioBox
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);home=root/'pi';repo=root/'repo';repo.mkdir()
            creds=home/'.config/car-dashboard';creds.mkdir(parents=True)
            token='d'*40;(creds/'device-token').write_text(token)
            server=ThreadingHTTPServer(('127.0.0.1',0),panel.Handler)
            thread=threading.Thread(target=server.serve_forever,daemon=True)
            with patch.object(panel,'ROOT',root/'server'),patch.object(panel,'DEVICE_TOKEN',token):
                thread.start()
                try:
                    agent=Agent({'role':'pi','radio_model':'RAK3172','home':str(home),'repo':str(repo),'home_url':f'http://127.0.0.1:{server.server_port}'})
                    agent.exchange()
                    car=RadioBox(creds/'radio-private.key',creds/'radio-home.pub',root/'car.db',b'U')
                    remote=RadioBox(root/'server/radio/private.key',root/'server/radio/car.pub',root/'remote.db',b'D')
                    _,frames=remote.frames(b'authenticated inner frame')
                    for frame in frames:raw=car.accept(frame)
                    self.assertEqual(raw,b'authenticated inner frame')
                finally:server.shutdown();server.server_close();thread.join()

    def test_wrong_signing_key_is_not_silently_replaced(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);agent=Agent({'role':'tiny','home':str(root),'repo':str(root),'overrides':str(root/'overlays')})
            one=Software(root/'one');two=Software(root/'two')
            agent.credentials.mkdir(parents=True);agent.public.write_bytes(one.public.read_bytes());agent.link.write_text(one.key().hex())
            with patch.object(agent,'call',return_value={'publicKey':two.public.read_text(),'linkKey':two.key().hex()}):
                with self.assertRaises(ValueError):agent.enroll()
            self.assertEqual(agent.public.read_bytes(),one.public.read_bytes())

    def test_device_snapshot_copy_is_hash_checked(self):
        with tempfile.TemporaryDirectory() as temp:
            service=Software(Path(temp))
            with self.assertRaises(ValueError):service.inventory('car-1','tiny',[{'path':'app/web/cockpit.js','source':'changed','sha256':'0'*64}])
            with self.assertRaises(ValueError):service.inventory('car-1','tiny',[{'path':'../../secret','source':'secret','sha256':digest(b'secret')}])

    def test_duet_copy_uses_actual_helpers_and_failed_candidate_is_retired(self):
        from control.update_transfer import encode
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);home=root/'device';repo=root/'repo';repo.mkdir();(repo/'duet').mkdir()
            actual=home/'.local/share/drive-console';actual.mkdir(parents=True)
            (actual/'proxy.py').write_text('VALUE=1\n');(repo/'duet/proxy.py').write_text('VALUE=9\n')
            agent=Agent({'role':'duet','home':str(home),'repo':str(repo)})
            agent.credentials.mkdir(parents=True)
            service=Software(root/'server');agent.public.write_bytes(service.public.read_bytes());agent.link.write_text(service.key().hex())
            shadow=agent.overrides/'duet/proxy.py';shadow.parent.mkdir(parents=True);shadow.write_text('VALUE=77\n')
            row=next(row for row in agent.inventory() if row['path']=='duet/proxy.py')
            self.assertEqual(row['source'],'VALUE=1\n')
            service.inventory('car-1','duet',[row])
            job=service.publish('car-1','duet',row['path'],row['sha256'],'VALUE=2\n')
            with service.journal.db() as db:parts=db.execute('SELECT kind,idx,count,data FROM parts WHERE id=? ORDER BY kind,idx',(job['id'],)).fetchall()
            for kind,index,count,data in parts:agent.accept(encode(kind,job['id'],index,count,data,service.key()))
            agent.stage();self.assertEqual(shadow.read_text(),'VALUE=2\n')
            self.assertEqual(agent.journal.status()[0]['phase'],'staged')
            result=root/'activation-result.json';result.write_text(json.dumps({'id':job['id'][:16],'phase':'failed'}))
            real_path=Path
            with patch('control.update_agent.Path',side_effect=lambda value:result if str(value)=='/etc/car-dashboard/software-activation.json' else real_path(value)):
                agent.stage()
            self.assertFalse(shadow.exists())
            self.assertEqual(next(row for row in agent.inventory() if row['path']=='duet/proxy.py')['source'],'VALUE=1\n')

    def test_recorder_payload_is_staged_before_any_active_file_changes(self):
        from control.update_transfer import encode
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);home=root/'device';repo=root/'repo';(repo/'recorder').mkdir(parents=True)
            path='recorder/gps_recorder.py';(repo/path).write_text('VERSION=1\n')
            agent=Agent({'role':'tiny','home':str(home),'repo':str(repo),'overrides':str(root/'active')})
            agent.credentials.mkdir(parents=True);service=Software(root/'server')
            agent.public.write_bytes(service.public.read_bytes());agent.link.write_text(service.key().hex())
            service.inventory('car-1','tiny',[{'path':path,'source':'VERSION=1\n','sha256':digest(b'VERSION=1\n')}])
            job=service.publish('car-1','tiny',path,digest(b'VERSION=1\n'),'VERSION=2\n')
            with service.journal.db() as db:parts=db.execute('SELECT kind,idx,count,data FROM parts WHERE id=? ORDER BY kind,idx',(job['id'],)).fetchall()
            for kind,index,count,data in parts:agent.accept(encode(kind,job['id'],index,count,data,service.key()))
            agent.stage()
            self.assertEqual((agent.state/'staged-overrides'/path).read_text(),'VERSION=2\n')
            self.assertFalse((agent.overrides/path).exists())
            self.assertEqual((repo/path).read_text(),'VERSION=1\n')
            self.assertEqual(agent.journal.status()[0]['phase'],'staged')
