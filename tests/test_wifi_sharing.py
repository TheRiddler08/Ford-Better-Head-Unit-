import json
import hashlib
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from deploy import fleet


class WifiSharingTests(unittest.TestCase):
    def test_dns_failure_can_still_detect_verified_wifi_without_destination_dns(self):
        probes=[]
        def run(args,**kwargs):
            if args[0]=='nmcli':
                return '100 (connected)' if 'GENERAL.STATE' in args else '192.168.1.1'
            probes.append(args)
            return subprocess.CompletedProcess(args,6 if args[-1]=='https://github.com/' else 0)
        with patch.object(fleet,'devices',return_value=[('wlan0','wifi')]),patch.object(fleet,'run',side_effect=run):
            nic,dns=fleet.wifi_uplink()
        self.assertEqual(nic,'wlan0');self.assertIn('192.168.1.1',dns)
        self.assertEqual(len(probes),2)
        for command in probes:
            self.assertIn('--interface',command);self.assertIn('wlan0',command)
            self.assertNotIn('--insecure',command);self.assertNotIn('-k',command)

    def route_commands(self,routes,dns='172.16.42.1',domain='~.',default='yes'):
        calls=[]
        def run(args,**kwargs):
            calls.append(args)
            if args[:3]==['ip','-j','-4']:return json.dumps(routes)
            if args[0]=='resolvectl' and len(args)==3:
                return 'Link 2 (eth0): '+{'dns':dns,'domain':domain,'default-route':default}[args[1]]
            return subprocess.CompletedProcess(args,1) if kwargs.get('check') is False else ''
        sharing=fleet.Sharing();sharing.route_state=('eth0','172.16.42.1')
        with patch.object(fleet,'run',side_effect=run),patch.object(fleet.shutil,'which',return_value='/usr/bin/resolvectl'),patch.object(fleet.time,'monotonic',return_value=100):
            sharing.route('eth0','172.16.42.1')
        return calls

    def test_cached_gateway_is_restored_after_networkmanager_removes_route(self):
        calls=self.route_commands([])
        self.assertIn(['ip','-4','route','replace','default','via','172.16.42.1','dev','eth0','proto','242','metric','50'],calls)
        self.assertIn(['resolvectl','dns','eth0','172.16.42.1'],calls)
        self.assertFalse(any('del' in call or 'revert' in call for call in calls))

    def test_dns_only_reset_is_repaired_without_deleting_healthy_route(self):
        routes=[{'gateway':'172.16.42.1','metric':50}]
        for options in ({'dns':'192.168.1.1'},{'domain':''},{'default':'no'}):
            calls=self.route_commands(routes,**options)
            self.assertIn(['resolvectl','domain','eth0','~.'],calls)
            self.assertFalse(any('del' in call or 'revert' in call for call in calls))

    def test_healthy_route_and_dns_are_checked_without_rewriting_them(self):
        calls=self.route_commands([{'gateway':'172.16.42.1','metric':50}])
        self.assertFalse(any('replace' in call or len(call)>3 and call[0]=='resolvectl' for call in calls))

    def test_changed_configuration_is_not_hidden_by_same_commit_fast_path(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);commit='a'*40
            config={'role':'pi','auto_update':False,'commit':commit,'user':'owner','repo':temp,'interface':'eth0'}
            (root/'installed.json').write_text(json.dumps({'commit':commit,'missing':[],
                'configurationHash':hashlib.sha256(json.dumps(config,sort_keys=True).encode()).hexdigest()}))
            with patch.object(fleet,'STATE',root),patch.object(fleet,'git',return_value=commit),patch.object(fleet,'approved_root_source',return_value=True),patch.object(fleet,'source_bytes',return_value=b'VERSION=1\n'),patch.object(fleet,'publish',return_value=root/'version'),patch.object(fleet.subprocess,'run') as run:
                fleet.refresh_locked(config);run.assert_not_called()
                config['interface']='eth1'
                fleet.refresh_locked(config);run.assert_called_once()
                self.assertIn('--retry-missing',run.call_args.args[0])

    @unittest.skipIf(os.getuid()==0,'Role installation fixture must have an ordinary owner')
    def test_storage_retries_leave_network_running_but_manual_repair_still_restarts(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);repo=root/'repo';repo.mkdir();(root/'systemd').mkdir()
            state=root/'state';config=root/'config.json';actual=Path
            account=SimpleNamespace(pw_uid=repo.stat().st_uid,pw_gid=repo.stat().st_gid,pw_dir=str(root/'home'))
            def paths(value):
                if str(value)=='/run/systemd/system':return root/'systemd'
                if str(value).startswith('/etc/'):return root/'etc'/str(value)[5:]
                return actual(value)
            with patch.object(fleet,'Path',side_effect=paths),patch.object(fleet,'STATE',state),patch.object(fleet,'CONFIG',config),patch.object(fleet.os,'geteuid',return_value=0),patch.object(fleet.pwd,'getpwnam',return_value=account),patch.object(fleet,'native_dependencies',return_value=[]),patch.object(fleet,'publish',return_value=root/'unchanged-version'),patch.object(fleet,'advance_duet'),patch.object(fleet.shutil,'which',return_value=None),patch.object(fleet,'systemd_units') as units,patch.object(fleet,'install_storage',return_value=['storage:mfsmount']) as storage:
                fleet.install('duet',repo,'owner',{},commit='a'*40,retry_missing=True)
                units.assert_called_once();units.reset_mock()
                fleet.install('duet',repo,'owner',{},commit='a'*40,retry_missing=True)
                units.assert_not_called();self.assertEqual(storage.call_count,2)
                fleet.install('duet',repo,'owner',{},commit='a'*40)
                units.assert_called_once()
                units.reset_mock()
                changed=json.loads(config.read_text());changed['interface']='eth1';config.write_text(json.dumps(changed))
                fleet.install('duet',repo,'owner',{},commit='a'*40,retry_missing=True)
                units.assert_called_once()


if __name__=='__main__':unittest.main()
