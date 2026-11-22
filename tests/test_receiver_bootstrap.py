"""Old Tiny checkouts must accept a new receiver before full role provisioning."""
from pathlib import Path
import subprocess
import sys
import os
import tempfile
import unittest
from unittest.mock import patch

SCRIPT=Path(__file__).resolve().parents[1]/'deploy/install-courier.sh'
CHECK=SCRIPT.read_text().split("<<'PYROLE'\n",1)[1].split('\nPYROLE',1)[0]


# Same rationale as test_offline_updates: no detached Git maintenance in fixtures.
QUIET_GIT={'GIT_CONFIG_COUNT':'4','GIT_CONFIG_KEY_0':'maintenance.auto','GIT_CONFIG_VALUE_0':'false',
           'GIT_CONFIG_KEY_1':'maintenance.autoDetach','GIT_CONFIG_VALUE_1':'false',
           'GIT_CONFIG_KEY_2':'gc.auto','GIT_CONFIG_VALUE_2':'0',
           'GIT_CONFIG_KEY_3':'gc.autoDetach','GIT_CONFIG_VALUE_3':'false'}


class ReceiverBootstrapTests(unittest.TestCase):
    def setUp(self):
        quiet=patch.dict(os.environ,QUIET_GIT);quiet.start();self.addCleanup(quiet.stop)
    def check(self,current=False,changed=False):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);repo=root/'repo';repo.mkdir();source=root/'bootstrap';source.mkdir()
            subprocess.run(['git','init','-q',str(repo)],check=True)
            files={'README.md':'old Tiny checkout'}
            if current:files.update({'deploy/fleet.py':'# fleet','power/wake.py':'# wake','power/standby.py':'# standby'})
            for name,content in files.items():
                target=repo/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_text(content)
            subprocess.run(['git','-C',str(repo),'add','.'],check=True)
            subprocess.run(['git','-C',str(repo),'-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','-qm','fixture'],check=True)
            for name in ('fleet','wake','standby'):(source/(name+'.py')).write_text('# '+name)
            if changed:(source/'wake.py').write_text('# changed bootstrap')
            return subprocess.run([sys.executable,'-c',CHECK,str(repo),str(source),str(source)],capture_output=True).returncode
    def test_old_checkout_defers_new_role_settings(self):self.assertEqual(self.check(),1)
    def test_matching_committed_sources_allow_role_setup(self):self.assertEqual(self.check(current=True),0)
    def test_mismatched_power_source_also_defers(self):self.assertEqual(self.check(current=True,changed=True),1)
