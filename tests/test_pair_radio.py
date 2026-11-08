import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import io
from contextlib import redirect_stdout
from unittest.mock import patch

from control.pair_radio import REMOTE, key_pair, merge_options, target, step, home_credentials
from deploy import fleet


class PairingTests(unittest.TestCase):
    def test_timeout_names_stage_without_printing_captured_secret(self):
        output=io.StringIO()
        error=subprocess.TimeoutExpired(['ssh'],120,output=b'secret-device-token',stderr=b'secret-device-token')
        with patch('control.pair_radio.subprocess.run',side_effect=error),redirect_stdout(output):
            with self.assertRaisesRegex(ValueError,'Home SSH timed out after 120 seconds') as caught:
                step('Home SSH',['ssh'],timeout=120,capture=True)
        self.assertIn('Home SSH',output.getvalue())
        self.assertNotIn('secret-device-token',output.getvalue()+str(caught.exception))

    def test_home_ssh_has_bounded_connection_and_keeps_credentials_private(self):
        token='private-device-token-'*3
        response=subprocess.CompletedProcess([],0,stdout=json.dumps({'deviceToken':token}),stderr='')
        output=io.StringIO()
        with patch('control.pair_radio.subprocess.run',return_value=response) as run,redirect_stdout(output):
            self.assertEqual(home_credentials('cayden@100.121.219.15','ssh-ed25519 AAAA'),token)
        command=run.call_args.args[0]
        self.assertIn('StrictHostKeyChecking=ask',command)
        self.assertIn('ConnectTimeout=10',command)
        self.assertIn('NumberOfPasswordPrompts=1',command)
        self.assertIn('ControlPath=none',command)
        self.assertEqual(run.call_args.kwargs['timeout'],120)
        self.assertTrue(run.call_args.kwargs['capture_output'])
        self.assertNotIn(token,output.getvalue()+' '.join(command))

    def test_preserves_existing_settings_and_explicit_optouts(self):
        previous={'device':'car-1','home_url':'https://old.example',
                  'radio_self_test':False,'max_app_bytes':46,'other':'preserve'}
        merged=merge_options(previous,'cayden@100.121.219.15','/dev/serial/by-id/rak')
        self.assertFalse(merged['radio_self_test'])
        self.assertEqual(merged['max_app_bytes'],46)
        self.assertEqual(merged['other'],'preserve')
        self.assertEqual(previous['home_url'],'https://old.example')
        self.assertEqual(merged['home_url'],'http://127.0.0.1:8791')
        with self.assertRaises(ValueError):merge_options({'radio_adapter':['/existing']},'u@home','/dev/serial/by-id/rak')

    def test_invalid_targets_and_ports_cannot_inject_shell_or_unit(self):
        for bad in ('-u@host','u@host\nExecStart=/bin/sh','u@host%h',
                    'u@host;id','u@host -p22','u@$(id)','root'):
            with self.subTest(bad=bad),self.assertRaises(ValueError):target(bad)
        with self.assertRaises(ValueError):
            merge_options({},'u@host','/dev/ttyUSB0')
        with self.assertRaises(ValueError):
            fleet.home_tunnel_unit({'user':'cayden','home':'/home/%h'},{'home_ssh_target':'u@host'})

    def test_key_and_remote_enrollment_preserve_auth_and_never_rotate(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);local=root/'car/.ssh/key';public=key_pair(local)
            self.assertEqual(key_pair(local),public)
            self.assertEqual(local.stat().st_mode&0o777,0o600)
            home=root/'home';env=home/'.config/car-home/control.env';env.parent.mkdir(parents=True)
            token='d'*48;env.write_text('CAR_DEVICE_TOKEN='+token+'\nCAR_PANEL_TOKEN=never-copy-this\n')
            auth=home/'.ssh/authorized_keys';auth.parent.mkdir()
            auth.write_text('ssh-ed25519 existing-user-key existing-comment')
            def enroll():
                return subprocess.run(['/usr/bin/python3','-c',REMOTE,public],
                                      env=dict(os.environ,HOME=str(home)),capture_output=True,text=True)
            first=enroll();self.assertEqual(first.returncode,0,first.stderr)
            self.assertEqual(json.loads(first.stdout),{'deviceToken':token})
            self.assertNotIn('never-copy-this',first.stdout)
            content=auth.read_text();lines=content.splitlines()
            self.assertEqual(lines[0],'ssh-ed25519 existing-user-key existing-comment')
            self.assertEqual(len(lines),2)
            self.assertTrue(lines[1].startswith('restrict,port-forwarding,permitopen="127.0.0.1:8790",command="/bin/false" '))
            self.assertEqual(auth.stat().st_mode&0o777,0o600)
            second=enroll();self.assertEqual(second.returncode,0,second.stderr)
            self.assertEqual(auth.read_text(),content)
            auth.write_text('ssh-ed25519 '+public.split()[1]+'\n')
            third=enroll();self.assertNotEqual(third.returncode,0)
            self.assertNotIn(token,third.stdout+third.stderr)
            self.assertNotIn('restrict',auth.read_text())

    def test_verified_role_installs_persistent_loopback_tunnel_and_respects_optout(self):
        config={'role':'tiny','user':'cayden','home':'/home/cayden','repo':'/home/cayden/car-dashboard'}
        link={'home_ssh_target':'cayden@100.121.219.15'}
        for enabled in (True,False):
            units={};calls=[]
            def load(path,default):
                return config if path==fleet.CONFIG else dict(link,home_tunnel_enabled=enabled)
            with patch.object(fleet,'load',side_effect=load),patch.object(fleet,'unit',side_effect=lambda name,text:units.update({name:text})),patch.object(fleet,'run',side_effect=lambda cmd,**_:calls.append(cmd)):
                fleet.systemd_units('tiny')
            text=units['car-home-tunnel.service']
            self.assertIn('User=cayden\n',text)
            self.assertIn('StrictHostKeyChecking=yes',text)
            self.assertIn('BatchMode=yes',text)
            self.assertIn('127.0.0.1:8791:127.0.0.1:8790',text)
            self.assertIn(['systemctl','enable' if enabled else 'disable','--now','car-home-tunnel.service'],calls)
            self.assertEqual(any('try-restart' in cmd and 'car-home-tunnel.service' in cmd for cmd in calls),enabled)

    def test_link_change_invalidates_role_reconciliation_fingerprint(self):
        config={'role':'tiny','home':'/home/cayden'}
        with patch.object(fleet,'load',return_value={}):old=fleet.settings_fingerprint(config)
        with patch.object(fleet,'load',return_value={'home_ssh_target':'u@home'}):new=fleet.settings_fingerprint(config)
        self.assertNotEqual(old,new)
        with patch.object(fleet,'load',return_value={'home_ssh_target':'u@home','home_tunnel_enabled':False}):disabled=fleet.settings_fingerprint(config)
        self.assertNotEqual(new,disabled)
