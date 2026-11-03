import copy
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch
from deploy import home_assistant as ha


class EditableStarterTests(unittest.TestCase):
    def migrate(self,state,config=None,seed=None):
        modules={name:types.ModuleType(name) for name in ('homeassistant','homeassistant.core','homeassistant.helpers','homeassistant.helpers.storage','homeassistant.util','homeassistant.util.yaml','homeassistant.components','homeassistant.components.lovelace','homeassistant.components.lovelace.dashboard')}
        class Store:
            def __init__(self,hass,version,key):self.key=key
            async def async_load(self):return copy.deepcopy(state.get(self.key))
            async def async_save(self,value):state[self.key]=copy.deepcopy(value)
        modules['homeassistant.core'].HomeAssistant=lambda path:object()
        modules['homeassistant.helpers.storage'].Store=Store
        modules['homeassistant.util.yaml'].load_yaml_dict=lambda path:copy.deepcopy((config or {}) if path.endswith('configuration.yaml') else (seed or ha.dashboard()))
        modules['homeassistant.components.lovelace.dashboard'].DashboardsCollection=types.SimpleNamespace(CREATE_SCHEMA=lambda data:data)
        with patch.dict(sys.modules,modules),redirect_stdout(io.StringIO()):exec(ha.STARTER_MIGRATION,{'__name__':'fixture'})
    def test_starter_config_is_no_longer_yaml_registered(self):
        self.assertNotIn('car-starter',ha.configuration('UTC')['lovelace'].get('dashboards',{}))
    def test_seed_keeps_cards_and_other_dashboard_then_preserves_edits(self):
        other={'id':'other','url_path':'other-dashboard','title':'Keep'}
        state={'lovelace_dashboards':{'items':[other]},'auth':'private sentinel'}
        self.migrate(state)
        self.assertEqual(state['lovelace_dashboards']['items'][0],other)
        target=state['lovelace.car_starter']['config']
        self.assertEqual(sum(card['type']=='gauge' for card in target['views'][0]['cards']),4)
        target['views'][0]['title']='My edits';before=copy.deepcopy(state)
        self.migrate(state)
        self.assertEqual(state,before);self.assertEqual(state['auth'],'private sentinel')
    def test_existing_editable_starter_is_not_replaced(self):
        existing={'id':'existing','url_path':'car-starter','title':'My car'}
        state={'lovelace_dashboards':{'items':[existing]},'lovelace.existing':{'config':{'views':[]}}}
        self.migrate(state)
        self.assertEqual(state['lovelace_dashboards']['items'],[existing]);self.assertNotIn('lovelace.car_starter',state)
    def test_interrupted_migration_preserves_saved_layout(self):
        state={'lovelace.car_starter':{'config':{'views':[{'title':'Keep partial migration edits'}]}}}
        before=copy.deepcopy(state['lovelace.car_starter']);self.migrate(state)
        self.assertEqual(state['lovelace.car_starter'],before)
        self.assertEqual(state['lovelace_dashboards']['items'][0]['url_path'],'car-starter')
    def test_conflicting_id_or_custom_yaml_definition_is_not_overwritten(self):
        for config,state in [({}, {'lovelace_dashboards':{'items':[{'id':'car_starter','url_path':'other-dashboard'}]}}),
                             ({'lovelace':{'dashboards':{'car-starter':{'mode':'yaml'}}}}, {})]:
            before=copy.deepcopy(state)
            with self.assertRaises(RuntimeError):self.migrate(state,config=config)
            self.assertEqual(state,before)
    def test_failed_restart_restores_only_migrated_stores_and_keeps_owner(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);base=root/'ha';config=base/'config';registry=base/'managed.json'
            ha.sync_assets(Path(__file__).resolve().parents[1],config,'UTC',registry)
            storage=config/'.storage';storage.mkdir()
            (storage/'auth').write_bytes(b'owner secret unchanged')
            old=b'{"items":[{"id":"other"}]}'
            (storage/'lovelace_dashboards').write_bytes(old)
            settings=root/'settings.json';settings.write_text(json.dumps({'repo':str(Path(__file__).resolve().parents[1]),'timezone':'UTC','enabled':True,'image':'fixture','forkArchive':ha.FORK_ARCHIVE_SHA,'backend':'http://127.0.0.1:8787/api/home-assistant/state'}))
            def migrate(_):
                for name in ha.STARTER_STORES:(storage/name).write_text('partial migration')
            with patch.object(ha,'BASE',base),patch.object(ha,'SETTINGS',settings),patch.object(ha,'service_active',return_value=True),patch.object(ha,'validate_config'),patch.object(ha,'migrate_starter',side_effect=migrate),patch.object(ha,'check_http',return_value=False),patch.object(ha,'run') as run:
                with self.assertRaises(RuntimeError):ha.reconcile()
            self.assertEqual((storage/'auth').read_bytes(),b'owner secret unchanged')
            self.assertEqual((storage/'lovelace_dashboards').read_bytes(),old)
            self.assertFalse((storage/'lovelace.car_starter').exists());self.assertFalse((storage/'car_console_starter_migration').exists())
            run.assert_any_call(['systemctl','restart','car-home-assistant.service'])
