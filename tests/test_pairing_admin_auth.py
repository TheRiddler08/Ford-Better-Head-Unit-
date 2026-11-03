"""Run the pairing shell with fake SSH/SCP; no network or machine changes."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

SCRIPT=Path(__file__).resolve().parents[1]/'duet/setup-offline-updates.sh'


class PairingAuthTests(unittest.TestCase):
    def exercise(self,identity=False,fail=False):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'duet').mkdir();(root/'deploy').mkdir();(root/'bin').mkdir()
            # Root CI may exercise the logic, but production retains its root guard.
            script=root/'duet/setup-offline-updates.sh'
            script.write_text(SCRIPT.read_text().replace('[[ "$EUID" != 0 ]]','[[ 1 != 0 ]]'))
            home=root/'home';(home/'.ssh').mkdir(parents=True)
            (home/'.ssh/drive-console-courier.pub').write_text('test-public-key')
            current=home/'.local/share/drive-console/courier-current';current.mkdir(parents=True)
            for name in ('offline_updates.py','runtime_assets.py','offline_payload.py','home_assistant.py'):
                (root/'deploy'/name).write_text('fixture');(current/name).write_text('fixture')
            mock='''#!/usr/bin/env python3
import json,os,sys
from pathlib import Path
with open(os.environ['TEST_LOG'],'a') as f:f.write(json.dumps({'tool':Path(sys.argv[0]).name,'args':sys.argv[1:]})+'\\n')
raise SystemExit(1 if os.environ.get('TEST_FAIL')=='1' else 0)
'''
            for name in ('ssh','scp'):
                tool=root/'bin'/name;tool.write_text(mock);tool.chmod(0o755)
            log=root/'calls.jsonl';env=dict(os.environ,HOME=str(home),PATH=str(root/'bin')+':'+os.environ['PATH'],TEST_LOG=str(log),TEST_FAIL='1' if fail else '0')
            env.pop('CAR_ADMIN_IDENTITY',None)
            if identity:
                key=root/'admin.key';key.write_text('fixture');env['CAR_ADMIN_IDENTITY']=str(key)
            result=subprocess.run(['bash',str(script),'car@172.16.42.2'],env=env,capture_output=True,text=True)
            calls=[json.loads(line) for line in log.read_text().splitlines()]
            return result,calls
    def test_every_connection_excludes_agent_and_restricted_key_auth(self):
        result,calls=self.exercise()
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual([call['tool'] for call in calls],['ssh','scp','ssh'])
        for call in calls:
            for option in ('IdentityAgent=none','ControlPath=none','PubkeyAuthentication=no','BatchMode=no'):
                self.assertIn(option,call['args'])
            self.assertNotIn('StrictHostKeyChecking=no',call['args'])
    def test_explicit_admin_key_is_supported_without_using_agent(self):
        result,calls=self.exercise(identity=True)
        self.assertEqual(result.returncode,0,result.stderr)
        for call in calls:
            self.assertIn('IdentitiesOnly=yes',call['args']);self.assertIn('-i',call['args'])
            self.assertNotIn('PubkeyAuthentication=no',call['args'])
    def test_authentication_failure_names_the_failed_phase(self):
        result,calls=self.exercise(fail=True)
        self.assertEqual(result.returncode,1);self.assertEqual(len(calls),1)
        self.assertIn('administrative SSH login (exit 1)',result.stderr)
