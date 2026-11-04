import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from deploy import offline_updates as courier
from deploy import fleet


class PasswordlessUpdateTests(unittest.TestCase):
    def test_full_runtime_update_uses_existing_key_without_setup_or_service_commands(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);key=root/'key';key.write_text('fixture')
            with patch.object(courier,'prepare',return_value={'commit':'a'*40}) as prepare,patch.object(courier,'deliver',return_value={'ok':True}) as deliver:
                result=courier.update_now(root,key,'car@172.16.42.2')
            prepare.assert_called_once_with(root,key,True,blocking=True)
            deliver.assert_called_once_with(root,key,'car@172.16.42.2')
            self.assertEqual(result['phase'],'installed')

    def test_busy_and_offline_updates_remain_queued_without_asking_for_admin_auth(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);key=root/'key';key.touch()
            for error,phase in ((BlockingIOError(),'installation-in-progress'),(RuntimeError('Tiny unavailable'),'queued')):
                with patch.object(courier,'prepare',return_value={'commit':'a'*40}),patch.object(courier,'deliver',side_effect=error),patch.object(courier,'worker_running',return_value=True):
                    self.assertEqual(courier.update_now(root,key,'car@172.16.42.2')['phase'],phase)
            with patch.object(courier,'prepare') as prepare:
                with self.assertRaises(ValueError):courier.update_now(root,root/'missing','car@172.16.42.2')
                prepare.assert_not_called()

    def test_pairing_shortcut_requires_matching_successful_full_install(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);key=root/'key';key.touch()
            receipt={'ok':True,'provisioning':1,'target':'car@172.16.42.2'}
            (root/'delivered.json').write_text(json.dumps(receipt))
            self.assertTrue(courier.paired(root,key,'car@172.16.42.2'))
            self.assertFalse(courier.paired(root,key,'other@172.16.42.2'))
            receipt['ok']=False;(root/'delivered.json').write_text(json.dumps(receipt))
            self.assertFalse(courier.paired(root,key,'car@172.16.42.2'))

    def test_delivery_cannot_select_display_keys_or_prompt_for_password(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);commit='a'*40;key=root/'key';key.touch()
            with zipfile.ZipFile(root/'ready.zip','w') as package:package.writestr('manifest.json',json.dumps({'schema':1,'commit':commit}))
            response={'ok':True,'commit':commit}
            with patch.object(courier.subprocess,'run',return_value=subprocess.CompletedProcess([],0,json.dumps(response).encode(),b'')) as run:
                courier.deliver(root,key,'car@172.16.42.2')
            args=run.call_args.args[0]
            for value in ('BatchMode=yes','IdentitiesOnly=yes','IdentityAgent=none','ControlPath=none','PasswordAuthentication=no','StrictHostKeyChecking=yes'):
                self.assertIn(value,args)
            self.assertIn('/dev/null',args)
            self.assertNotIn('sudo',args)

    def test_idle_reload_targets_only_our_owned_watcher_and_never_busy_installs(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);state=root/'courier';state.mkdir();proc=root/'proc';entry=proc/'999999';entry.mkdir(parents=True)
            script=root/'courier-current/offline_updates.py';script.parent.mkdir();script.touch()
            (state/'worker.lock').write_text('999999')
            argv=['/usr/bin/python3','-u',str(script),'watch','--state',str(state)]
            (entry/'cmdline').write_bytes(b'\0'.join(v.encode() for v in argv)+b'\0')
            with patch.object(courier.os,'pidfd_open',side_effect=OSError()),patch.object(courier.os,'kill') as kill:
                with courier.locked(state/'courier.lock'):
                    self.assertFalse(courier.reload_worker_when_idle(state,wait=0,proc=proc))
                    kill.assert_not_called()
                self.assertTrue(courier.reload_worker_when_idle(state,wait=0,proc=proc))
                kill.assert_called_once_with(999999,signal.SIGTERM);kill.reset_mock()
                (entry/'cmdline').write_bytes(b'/usr/bin/ssh\0-N\0')
                self.assertFalse(courier.reload_worker_when_idle(state,wait=0,proc=proc))
                kill.assert_not_called()

    def test_unchanged_pi_check_fetches_without_tests_or_checkout_rewrites(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);commit='a'*40
            config={'role':'pi','user':'owner','repo':temp,'commit':commit,'auto_update':True}
            installed={'commit':commit,'missing':[],'configurationHash':hashlib.sha256(json.dumps(config,sort_keys=True).encode()).hexdigest()}
            def load(path,default):
                if Path(path).name=='installed.json':return installed
                if Path(path).name=='last-fetch.json':return {'time':0}
                return {'wifiInternet':True}
            calls=[]
            def git(config,*args,**kwargs):
                calls.append(args)
                if args[:2]==('remote','get-url'):return fleet.ORIGIN
                if args[0]=='symbolic-ref':return 'main'
                if args[0]=='status':return ''
                return commit
            with patch.object(fleet,'STATE',root),patch.object(fleet,'load',side_effect=load),patch.object(fleet,'save'),patch.object(fleet,'git',side_effect=git),patch.object(fleet.time,'time',return_value=270),patch.object(fleet,'approved_root_source',return_value=True),patch.object(fleet.subprocess,'run') as run:
                fleet.refresh_locked(config)
                run.assert_not_called()
            self.assertTrue(any(args[0]=='fetch' for args in calls))
            self.assertFalse(any(args[0] in ('merge','show','reset') for args in calls))

    def test_scheduled_checks_fit_within_five_minutes(self):
        self.assertLessEqual(courier.CHECK_INTERVAL,240)
        self.assertLessEqual(fleet.UPDATE_CHECK_INTERVAL+30,300)
        with patch.object(fleet,'source_bytes',return_value=b'#!/bin/sh\nexit 0\n'),patch.object(fleet,'save'),patch.object(fleet.os,'chmod'),patch.object(fleet,'run'),patch.object(fleet.subprocess,'run',return_value=subprocess.CompletedProcess([],0)),patch.object(fleet,'unit') as units:
            fleet.install_app_updates({'commit':'a'*40,'repo':'/home/car/car-dashboard','user':'car'})
        timers={call.args[0]:call.args[1] for call in units.call_args_list}
        self.assertIn('OnCalendar=*-*-* *:0/4:00',timers['car-dashboard-update.timer'])
        self.assertIn('RandomizedDelaySec=15',timers['car-dashboard-update.timer'])


if __name__=='__main__':unittest.main()
