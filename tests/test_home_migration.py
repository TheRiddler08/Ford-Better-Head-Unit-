import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import threading
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from unittest.mock import patch

from home_server.migrate_control import migrate


class HomeMigrationTests(unittest.TestCase):
    def test_installer_health_check_accepts_matching_token_and_rejects_conflict(self):
        source=(Path(__file__).parents[1]/'home_server/install.sh').read_text()
        program=source.split("<<'PYHEALTH'\n",1)[1].split('\nPYHEALTH\n',1)[0]
        expected='device-token-'+('x'*40)
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200 if self.headers.get('Authorization')=='Bearer '+expected else 401)
                self.end_headers()
            def log_message(self,*_):pass
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            with tempfile.TemporaryDirectory() as directory:
                path=Path(directory)/'control.env'
                for token,success in ((expected,True),('different-private-device-token',False)):
                    path.write_text(f'CAR_DEVICE_TOKEN={token}\nCAR_PANEL_PORT={server.server_port}\n')
                    result=subprocess.run(['/usr/bin/python3','-c',program,str(path)],capture_output=True,text=True)
                    self.assertEqual(result.returncode==0,success,result.stdout+result.stderr)
                    self.assertNotIn(token,result.stdout+result.stderr)
                    if not success:self.assertIn('rejected its saved device token',result.stderr)
        finally:server.shutdown();server.server_close();thread.join()

    def test_only_known_legacy_unit_is_retired(self):
        with patch('home_server.migrate_control.subprocess.run',side_effect=[subprocess.CompletedProcess([],0,'loaded\n',''),subprocess.CompletedProcess([],0)]) as run:
            self.assertTrue(migrate())
        self.assertEqual(run.call_args_list[1].args[0],['systemctl','--user','disable','--now','car-home-panel.service'])
        self.assertTrue(all('pkill' not in call.args[0] for call in run.call_args_list))

    def test_no_legacy_or_user_manager_leaves_other_services_alone(self):
        for result in (subprocess.CompletedProcess([],0,'not-found\n',''),subprocess.CompletedProcess([],1,'','no bus')):
            with self.subTest(result=result),patch('home_server.migrate_control.subprocess.run',return_value=result) as run:
                self.assertFalse(migrate())
                run.assert_called_once()

    def test_first_env_creation_preserves_legacy_credentials_and_link_key(self):
        source=(Path(__file__).parents[1]/'home_server/install.sh').read_text()
        program=source.split('<<\'PY\'\n',1)[1].split('\nPY\n',1)[0]
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);folder=root/'.config/car-home';folder.mkdir(parents=True)
            panel='p'*48;device='d'*48;key='ab'*32
            (folder/'panel-token').write_text(panel+'\n');(folder/'device-token').write_text(device+'\n')
            link=root/'.local/share/car-home-control/software/update-link.key';link.parent.mkdir(parents=True);link.write_text(key)
            path=folder/'control.env'
            env={name:value for name,value in os.environ.items() if not name.startswith('CAR_')}
            result=subprocess.run(['/usr/bin/python3','-c',program,str(path)],env=dict(env,HOME=str(root)),capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertIn('CAR_PANEL_TOKEN='+panel,path.read_text())
            self.assertIn('CAR_DEVICE_TOKEN='+device,path.read_text())
            self.assertIn('CAR_LINK_KEY='+key,path.read_text())
            for secret in (panel,device,key):self.assertNotIn(secret,result.stdout+result.stderr)
            self.assertEqual(path.stat().st_mode&0o777,0o600)
