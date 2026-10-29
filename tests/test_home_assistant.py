import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from app.ha_bridge import snapshot
from deploy.home_assistant import configuration, managed_files, sync_assets, grant_display_tunnel

ROOT=Path(__file__).resolve().parents[1]


class HomeAssistantTests(unittest.TestCase):
    def test_units_individual_freshness_and_no_fabricated_values(self):
        values={'SPEED':{'number':60,'unit':'mph','ts':100},
                'RPM':{'number':1350,'unit':'revolutions_per_minute','ts':100},
                'COOLANT_TEMP':{'number':212,'unit':'degree_Fahrenheit','ts':0}}
        data=snapshot(values,{'fix':False},now=110)
        self.assertAlmostEqual(data['metrics']['speed']['value'],96.56064)
        self.assertTrue(data['metrics']['speed']['fresh'])
        self.assertEqual(data['metrics']['coolant']['value'],100)
        self.assertFalse(data['metrics']['coolant']['fresh'])
        self.assertIsNone(data['metrics']['fuel']['value'])
        self.assertIsNone(data['gps'])
        values['RPM']['ts']=float('nan')
        json.dumps(snapshot(values,{},110),allow_nan=False)

    def test_gps_old_fix_is_unavailable(self):
        stale={'fix':True,'lat':40,'lon':-74,'mode':3,'time':'2020-01-01T00:00:00Z'}
        self.assertIsNone(snapshot({},stale,now=2000000000)['gps'])
        good=dict(stale,time='2026-10-29T00:00:00Z')
        self.assertEqual(snapshot({},good,now=1793232000)['gps']['lat'],40)
        self.assertIsNone(snapshot({},dict(good,lat=True),now=1793232000)['gps'])

    def test_configuration_loopback_and_native_editor(self):
        data=configuration('America/New_York')
        self.assertEqual(data['http']['server_host'],'127.0.0.1')
        self.assertEqual(data['lovelace']['mode'],'storage')
        self.assertIn('car_console',data)
        self.assertEqual(len(managed_files(ROOT,'UTC')),10)

    def test_repeatable_managed_assets_preserve_edits_and_auth(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);config=root/'config';registry=root/'managed.json'
            first,preserved=sync_assets(ROOT,config,'UTC',registry)
            self.assertEqual(len(first),10)
            self.assertEqual(sync_assets(ROOT,config,'UTC',registry),([],[]))
            auth=config/'.storage/auth';auth.parent.mkdir();auth.write_text('private auth placeholder')
            path=config/'configuration.yaml';path.write_text('my custom configuration')
            _,preserved=sync_assets(ROOT,config,'America/New_York',registry)
            self.assertIn('configuration.yaml',preserved)
            self.assertEqual(path.read_text(),'my custom configuration')
            self.assertEqual(auth.read_text(),'private auth placeholder')

    def test_display_key_scope_only_and_idempotence(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'authorized_keys'
            unrelated='ssh-ed25519 AAAA unrelated\n'
            managed='command="/bin/false",restrict,port-forwarding,permitopen="127.0.0.1:8787" ssh-ed25519 BBBB drive-console-duet\n'
            path.write_text(unrelated+managed)
            self.assertTrue(grant_display_tunnel(path))
            self.assertTrue(path.read_text().startswith(unrelated))
            self.assertEqual(path.read_text().count('permitopen="127.0.0.1:8123"'),1)
            self.assertFalse(grant_display_tunnel(path))

    def test_reconcile_validation_failure_restores_managed_assets(self):
        from deploy import home_assistant as ha
        from unittest.mock import patch
        import shutil
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);repo=root/'repo';repo.mkdir()
            shutil.copytree(ROOT/'home_assistant',repo/'home_assistant')
            base=root/'installed';config=base/'config';registry=base/'managed.json'
            sync_assets(repo,config,'UTC',registry)
            old=(config/'custom_components/car_console/manifest.json').read_bytes()
            manifest=repo/'home_assistant/custom_components/car_console/manifest.json'
            data=json.loads(manifest.read_text());data['version']='0.1.1';manifest.write_text(json.dumps(data))
            settings=root/'settings.json';settings.write_text(json.dumps({'repo':str(repo),'timezone':'UTC','enabled':True,'image':'test-image','backend':'http://127.0.0.1:8787/api/home-assistant/state'}))
            with patch.object(ha,'BASE',base),patch.object(ha,'SETTINGS',settings),patch.object(ha,'STATUS',root/'status.json'),patch.object(ha,'service_active',return_value=True),patch.object(ha,'validate_config',side_effect=RuntimeError('invalid config')),patch.object(ha,'run') as run:
                with self.assertRaises(RuntimeError):ha.reconcile()
                run.assert_not_called()  # A rejected config must not stop working HA.
            self.assertEqual((config/'custom_components/car_console/manifest.json').read_bytes(),old)

    def test_duet_startup_keeps_legacy_and_waits_for_ha(self):
        from unittest.mock import patch
        from duet.proxy import ProxyHandler
        handler=object.__new__(ProxyHandler)
        with patch('duet.proxy.fetch_upstream',return_value=(404,{},b'{}')):
            self.assertEqual(handler.start_status()['target'],'/')
        installed=json.dumps({'installed':True,'enabled':True}).encode()
        with patch('duet.proxy.fetch_upstream',return_value=(200,{},installed)),patch('duet.proxy.http.client.HTTPConnection',side_effect=OSError('tunnel down')):
            self.assertIsNone(handler.start_status()['target'])
        with patch('duet.proxy.fetch_upstream',side_effect=OSError('disconnected')):
            self.assertIsNone(handler.start_status()['target'])

    def test_symlinked_assets_rejected_before_writing(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);config=root/'config';registry=root/'managed.json'
            sync_assets(ROOT,config,'UTC',registry)
            target=config/'configuration.yaml';target.unlink()
            outside=root/'private';outside.write_text('preserve')
            target.symlink_to(outside)
            with self.assertRaises(ValueError):sync_assets(ROOT,config,'UTC',registry)
            self.assertEqual(outside.read_text(),'preserve')
