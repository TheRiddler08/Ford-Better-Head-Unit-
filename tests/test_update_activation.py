import json
import os
from pathlib import Path
from types import SimpleNamespace
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch
from duet import launcher_lock as display
from deploy import fleet


class ActivationTests(unittest.TestCase):
    def test_display_rollback_restores_complete_managed_helper_set(self):
        with tempfile.TemporaryDirectory() as temp:
            base=Path(temp);backup=base/'duet-backups'/'release';backup.mkdir(parents=True)
            for name in display.HELPERS:
                previous=b'#!/bin/sh\ntrue\n' if name.endswith('.sh') else b'VERSION=1\n'
                (backup/name).write_bytes(previous);(base/name).write_bytes(b'bad candidate')
            display.rollback(base,{'backup':str(backup)})
            for name in display.HELPERS:self.assertEqual((base/name).read_bytes(),(backup/name).read_bytes())
            with self.assertRaises(ValueError):display.rollback(base,{'backup':str(base.parent)})

    def test_display_restart_does_not_kill_unrelated_processes(self):
        child=subprocess.Popen(['sh','-c','sleep 30'],start_new_session=True)
        other=subprocess.Popen(['sh','-c','sleep 30'],start_new_session=True)
        try:
            display.terminate(child)
            self.assertIsNotNone(child.poll())
            self.assertIsNone(other.poll())
        finally:display.terminate(other)

    def test_reboot_requires_fresh_root_owned_park_signal(self):
        with tempfile.TemporaryDirectory() as temp:
            config={'role':'tiny','home':temp,'repo':temp,'user':'car'}
            original_exists=Path.exists;original_stat=Path.stat
            power='/run/car-dashboard/power-state.json'
            def exists(path):
                if str(path) in ('/var/run/reboot-required',power,'/run/systemd/system'):return True
                return original_exists(path)
            for parked,age,owner,expected in [(True,20,0,False),(False,0,0,False),(True,0,1000,False),(True,0,0,True)]:
                def stat(path,*args,**kwargs):
                    if str(path)==power:return SimpleNamespace(st_uid=owner,st_mode=0o644)
                    return original_stat(path,*args,**kwargs)
                def load(path,default):return {'parked':parked,'checkedAt':time.time()-age} if str(path)==power else default
                with patch.object(Path,'exists',exists),patch.object(Path,'stat',stat),patch.object(fleet,'load',side_effect=load),patch.object(fleet,'save'),patch.object(fleet,'run') as run:
                    fleet.maintenance(config,update_locked=True)
                    self.assertEqual(any(call.args[0]==['systemctl','reboot'] for call in run.call_args_list),expected)
