"""Credential transport tests use a fake NetworkManager, never host Wi-Fi."""
import json
import os
from contextlib import redirect_stdout
import io
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from deploy import wifi_profiles as wifi
from deploy import fleet


class WifiProfileTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.profiles=self.root/'profiles'
        self.config=self.root/'fleet.json';self.config.write_text(json.dumps({'role':'pi','enabled':True}))
        self.status=self.root/'status.json';self.settings=self.root/'private'
        for name,value in [('ROOT',self.settings),('FLEET',self.config),('STATUS',self.status),('PROFILES',self.profiles)]:
            p=patch.object(wifi,name,value);p.start();self.addCleanup(p.stop)
        self.row={'ssid':' New:Home; WiFi ','security':'wpa-psk','psk':' spaces ; \\ and $() password ','hidden':False}

    def payload(self,**values):return dict(schema=1,profiles=[dict(self.row,**values)])

    def test_keyfile_load_uses_no_password_argv_and_private_permissions(self):
        calls=[]
        def nm(*args,**kwargs):calls.append(args);return ''
        with patch.object(wifi,'nm',side_effect=nm):
            result=wifi.import_profiles(self.payload())
        path=wifi.profile_path(self.row);text=path.read_text()
        self.assertEqual(path.stat().st_mode&0o777,0o600)
        encoded=';'.join(str(b) for b in self.row['ssid'].encode())+';'
        self.assertIn('ssid='+encoded,text);self.assertIn('id=\\sNew:Home;\\sWiFi\\s (car)',text)
        self.assertIn('method=auto',text);self.assertNotIn('interface-name=',text)
        self.assertEqual(result['imported'],1)
        self.assertIn(('connection','load',str(path)),calls)
        self.assertNotIn(self.row['psk'],json.dumps(calls))
        self.assertNotIn(self.row['psk'],self.status.read_text());self.assertNotIn(self.row['ssid'],self.status.read_text())

    def test_unchanged_profiles_do_not_rewrite_pi_sd_or_reload_nm(self):
        with patch.object(wifi,'identities',return_value=[]),patch.object(wifi,'nm') as nm,patch.object(wifi,'try_autoconnect',return_value='waiting-for-wifi'):
            wifi.import_profiles(self.payload());nm.reset_mock()
            before=wifi.profile_path(self.row).stat().st_mtime_ns
            result=wifi.import_profiles(self.payload())
            nm.assert_not_called();self.assertEqual(result['imported'],0)
            self.assertEqual(wifi.profile_path(self.row).stat().st_mtime_ns,before)

    def test_manual_profile_and_managed_autoconnect_optout_are_preserved(self):
        import uuid
        managed=str(uuid.uuid5(uuid.NAMESPACE_URL,'car-wifi:'+self.row['ssid']+'\0wpa-psk'))
        for ident in ('1'*36,managed):
            def field(key,name):return self.row['ssid'] if name.endswith('.ssid') else 'no'
            with patch.object(wifi,'identities',return_value=[ident]),patch.object(wifi,'field',side_effect=field),patch.object(wifi,'nm') as nm:
                self.assertEqual(wifi.import_profiles(self.payload())['preserved'],1);nm.assert_not_called()
        self.assertFalse(self.profiles.exists())

    def test_changed_password_can_add_managed_alternative_without_overwriting_manual_profile(self):
        def field(ident,key):return self.row['ssid'] if key.endswith('.ssid') else 'yes'
        with patch.object(wifi,'identities',return_value=['1'*36]),patch.object(wifi,'field',side_effect=field),patch.object(wifi,'nm') as nm,patch.object(wifi,'try_autoconnect',return_value='waiting-for-wifi'):
            result=wifi.import_profiles(self.payload())
        self.assertEqual(result['preserved'],1);self.assertEqual(result['imported'],1)
        self.assertTrue(wifi.profile_path(self.row).is_file())
        self.assertFalse(any('modify' in c.args or 'delete' in c.args for c in nm.call_args_list))

    def test_payload_validation_is_atomic_before_import_and_rejects_profile_injection(self):
        bad=[dict(self.row,ssid='evil\n[ipv4]'),dict(self.row,security='wpa-eap'),dict(self.row,psk='short'),dict(self.row,hidden='false'),dict(self.row,interface='wlan0')]
        for row in bad:
            with self.assertRaises(ValueError):wifi.import_profiles(dict(schema=1,profiles=[self.row,row]))
        self.assertFalse(self.profiles.exists())

    def test_disabled_role_never_accepts_credentials(self):
        self.config.write_text(json.dumps({'role':'pi','wifi_sync':False}))
        with patch.object(wifi,'nm') as nm:
            self.assertEqual(wifi.import_profiles(self.payload())['phase'],'disabled');nm.assert_not_called()
        self.assertFalse(self.profiles.exists())

    def test_import_rejects_symlink_and_leaves_target_unchanged(self):
        self.profiles.mkdir();target=self.root/'unrelated';target.write_text('keep')
        wifi.profile_path(self.row).symlink_to(target)
        with patch.object(wifi,'identities',return_value=[]),self.assertRaises(ValueError):wifi.import_profiles(self.payload())
        self.assertEqual(target.read_text(),'keep')

    def test_nm_preserves_significant_spaces_and_errors_never_include_credentials(self):
        secret=self.row['psk']
        with patch.object(wifi.subprocess,'run',return_value=subprocess.CompletedProcess([],0,(secret+'\n').encode(),b'')):
            self.assertEqual(wifi.nm('private',secret=True),secret)
        with patch.object(wifi.subprocess,'run',return_value=subprocess.CompletedProcess([],1,b'',secret.encode())),self.assertRaisesRegex(ValueError,'^NetworkManager operation failed$'):
            wifi.nm('private',secret=True)

    def test_export_skips_hotspots_enterprise_keyring_and_private_other_user(self):
        ids=['hotspot','enterprise','keyring','other','disabled','personal']
        requested=[]
        def field(ident,key,secret=False):
            requested.append((ident,key,secret))
            values={'802-11-wireless.mode':'ap' if ident=='hotspot' else 'infrastructure',
                'connection.autoconnect':'no' if ident=='disabled' else 'yes',
                'connection.permissions':'user:someone:' if ident=='other' else 'user:owner:;' if ident=='personal' else '',
                '802-11-wireless-security.key-mgmt':'wpa-eap' if ident=='enterprise' else 'wpa-psk',
                '802-11-wireless-security.psk-flags':'1 (agent-owned)' if ident=='keyring' else '0 (none)',
                '802-11-wireless.ssid':self.row['ssid'],'802-11-wireless-security.psk':self.row['psk'],
                '802-11-wireless.hidden':'no'}
            return values[key]
        with patch.object(wifi,'identities',return_value=ids),patch.object(wifi,'field',side_effect=field):
            self.assertEqual(wifi.export_profiles('owner'),[self.row])
        self.assertEqual([r[0] for r in requested if r[2]],['personal'])

    def test_only_paired_fixed_peers_receive_stdin_over_pinned_ssh(self):
        self.config.write_text(json.dumps({'role':'duet','user':'owner'}))
        self.settings.mkdir();(self.settings/'peers.json').write_text(json.dumps({'pi':{'user':'car'},'impostor':{'user':'attacker'}}))
        with patch.object(wifi,'export_profiles',return_value=[self.row]),patch.object(wifi,'active_network_shared',return_value=True),patch.object(wifi.subprocess,'run',return_value=subprocess.CompletedProcess([],0,b'{"phase":"received"}',b'')) as run:
            wifi.sync_once()
        run.assert_called_once();args=run.call_args.args[0]
        self.assertEqual(args[-1],'car@172.16.42.3');self.assertIn('172.16.42.1',args)
        self.assertIn('StrictHostKeyChecking=yes',args);self.assertIn('BatchMode=yes',args)
        self.assertNotIn(self.row['psk'],json.dumps(args));self.assertNotIn(self.row['ssid'],json.dumps(args))
        self.assertEqual(json.loads(run.call_args.kwargs['input'])['profiles'],[self.row])
        self.assertNotIn(self.row['psk'],self.status.read_text());self.assertNotIn('ssid',self.status.read_text())

    def test_unpaired_export_never_reads_secrets(self):
        self.config.write_text(json.dumps({'role':'duet','user':'owner'}))
        with patch.object(wifi,'export_profiles') as export,patch.object(wifi.subprocess,'run') as run:
            wifi.sync_once();export.assert_not_called();run.assert_not_called()
        self.assertEqual(json.loads(self.status.read_text())['phase'],'pairing-required')

    def test_keyring_only_active_profile_is_pending_instead_of_claiming_it_was_shared(self):
        self.config.write_text(json.dumps({'role':'duet','user':'owner'}));self.settings.mkdir()
        (self.settings/'peers.json').write_text(json.dumps({'pi':{'user':'car'}}))
        with patch.object(wifi,'export_profiles',return_value=[]),patch.object(wifi,'active_network_shared',return_value=False),patch.object(wifi.subprocess,'run') as run:
            wifi.sync_once();run.assert_not_called()
        self.assertEqual(json.loads(self.status.read_text())['phase'],'profiles-unavailable')

    def test_receive_wrapper_has_fixed_command_without_arguments_or_environment(self):
        wrapper=self.root/'receive'
        with patch.object(wifi,'RECEIVER',wrapper):wifi.install_wrapper(Path('/root/version/wifi_profiles.py'))
        self.assertIn('[ "$#" -eq 0 ]',wrapper.read_text());self.assertIn('env -i',wrapper.read_text())
        self.assertEqual(wrapper.stat().st_mode&0o777,0o755)

    def test_new_helpers_are_part_of_verified_role_bundle(self):
        self.assertEqual(fleet.SOURCES['wifi_profiles.py'],'deploy/wifi_profiles.py')
        self.assertEqual(fleet.SOURCES['native_firewall.py'],'deploy/native_firewall.py')

    def test_old_signed_receiver_gets_new_helpers_from_exact_commit_not_checkout(self):
        inputs=self.root/'inputs';inputs.mkdir()
        for name in ('fleet.py','wake.py','standby.py'):(inputs/name).write_bytes(b'# signed receiver input\n')
        repo=self.root/'repo';(repo/'deploy').mkdir(parents=True)
        (repo/'deploy/wifi_profiles.py').write_bytes(b'# untrusted working-tree edit\n')
        argv=['fleet.py','install','--role','tiny','--repo',str(repo),'--user','owner','--source',str(inputs),'--commit','a'*40]
        with patch.object(fleet.sys,'argv',argv),patch.object(fleet.os,'geteuid',return_value=0),patch.object(fleet,'load',return_value={}),patch.object(fleet,'source_bytes',return_value=b'# exact verified commit\n') as source,patch.object(fleet,'install',return_value={'ok':True}) as install,redirect_stdout(io.StringIO()):
            fleet.main()
        self.assertEqual(install.call_args.args[3]['wifi_profiles.py'],b'# exact verified commit\n')
        source.assert_any_call({'user':'owner','repo':str(repo)},'a'*40,'deploy/wifi_profiles.py')

    def test_pairing_key_is_separate_private_and_peer_host_keys_are_pinned(self):
        self.config.write_text(json.dumps({'role':'duet'}))
        public=wifi.sender_key()
        self.assertEqual(wifi.public_key(public),public)
        self.assertEqual((self.settings/'duet_ed25519').stat().st_mode&0o777,0o600)
        self.assertEqual(self.settings.stat().st_mode&0o777,0o700)
        wifi.enroll('pi','car',public)
        self.assertEqual((self.settings/'known_hosts').read_text(),'172.16.42.3 '+public+'\n')
        with self.assertRaises(ValueError):wifi.enroll('unknown','car',public)
        with self.assertRaises(ValueError):wifi.public_key('ssh-ed25519 '+('A'*68))

    def test_receiver_connects_only_disconnected_autoconnect_enabled_wifi(self):
        calls=[]
        def nm(*args,**kwargs):
            calls.append(args)
            if args==('radio','wifi'):return 'enabled'
            if 'DEVICE,TYPE,STATE' in args:return 'wlan0:wifi:connected\nwlan1:wifi:disconnected'
            if 'GENERAL.AUTOCONNECT' in args:return 'yes'
            if 'SSID' in args:return self.row['ssid']
            return ''
        with patch.object(wifi,'nm',side_effect=nm):self.assertEqual(wifi.try_autoconnect([self.row]),'activation-requested')
        activate=[c for c in calls if 'up' in c]
        self.assertEqual(len(activate),1);self.assertEqual(activate[0][-1],'wlan1')
        self.assertNotIn(self.row['psk'],json.dumps(calls))
        with patch.object(wifi,'nm',return_value='disabled') as call:
            self.assertEqual(wifi.try_autoconnect([self.row]),'not-requested');call.assert_called_once()


if __name__=='__main__':unittest.main()
