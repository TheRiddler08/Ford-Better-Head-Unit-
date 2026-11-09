import copy
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch
from deploy import home_assistant as ha

ROOT=Path(__file__).resolve().parents[1]


class ClassicDashboardTests(unittest.TestCase):
    def migrate(self,state,url='/local/drive-console.js?v=fixture'):
        modules={name:types.ModuleType(name) for name in ('homeassistant','homeassistant.core','homeassistant.helpers','homeassistant.helpers.storage','homeassistant.util','homeassistant.util.yaml','homeassistant.components','homeassistant.components.lovelace','homeassistant.components.lovelace.dashboard')}
        class Store:
            def __init__(self,hass,version,key):self.key=key
            async def async_load(self):return copy.deepcopy(state.get(self.key))
            async def async_save(self,value):state[self.key]=copy.deepcopy(value)
        modules['homeassistant.core'].HomeAssistant=lambda path:object()
        modules['homeassistant.helpers.storage'].Store=Store
        modules['homeassistant.util.yaml'].load_yaml_dict=lambda path:({'url':url} if path.endswith('resource.json') else ha.classic_dashboard())
        modules['homeassistant.components.lovelace.dashboard'].DashboardsCollection=types.SimpleNamespace(CREATE_SCHEMA=lambda data:data)
        with patch.dict(sys.modules,modules),redirect_stdout(io.StringIO()):exec(ha.CLASSIC_MIGRATION,{})

    def test_creates_native_storage_dashboard_without_touching_starter_or_owner(self):
        state={'auth':'owner sentinel','lovelace.car_starter':{'config':{'title':'Edited existing starter'}},
               'lovelace_dashboards':{'items':[{'id':'car_starter','url_path':'car-starter'}]},
               'lovelace_resources':{'items':[{'id':'other','url':'/local/other.js','type':'module'}]}}
        before=copy.deepcopy(state)
        self.migrate(state)
        self.assertEqual(state['auth'],before['auth'])
        self.assertEqual(state['lovelace.car_starter'],before['lovelace.car_starter'])
        self.assertEqual(state['lovelace_resources']['items'][0],before['lovelace_resources']['items'][0])
        self.assertEqual(state['lovelace_dashboards']['items'][-1]['mode'],'storage')
        config=state['lovelace.drive_console']['config']
        self.assertEqual(config['views'][0]['type'],'custom:drive-console-layout')
        self.assertEqual(len(config['views'][0]['cards']),7)
        self.assertEqual([view['path'] for view in config['views']],['drive','navigation','media','vehicle','health','updates','settings','devices','storage','recordings','radio','reports','home-control'])
        self.assertTrue(all(card['type']=='custom:drive-console-widget' for card in config['views'][0]['cards']))

    def test_module_updates_preserve_dashboard_edits_and_deliberate_deletion(self):
        state={};self.migrate(state)
        state['lovelace.drive_console']['config']['views'][0]['cards'][0]['title']='My custom heading'
        self.migrate(state,url='/local/drive-console.js?v=new')
        self.assertEqual(state['lovelace.drive_console']['config']['views'][0]['cards'][0]['title'],'My custom heading')
        self.assertEqual(state['lovelace_resources']['items'][0]['url'],'/local/drive-console.js?v=new')
        state['lovelace_dashboards']['items']=[];del state['lovelace.drive_console']
        self.migrate(state,url='/local/drive-console.js?v=later')
        self.assertEqual(state['lovelace_dashboards']['items'],[])
        self.assertNotIn('lovelace.drive_console',state)

    def test_single_page_upgrade_keeps_edits_and_adds_independent_tabs_once(self):
        old={'type':'custom:drive-console-widget','widget':'clock','title':'My edited clock'}
        home={'title':'My home','path':'drive','type':'custom:drive-console-layout','cards':[old]}
        navigation={'title':'My navigation','path':'navigation','cards':[{'type':'markdown','content':'Keep this page'}]}
        before={'config':{'views':[copy.deepcopy(home),copy.deepcopy(navigation)]}}
        state={'lovelace_dashboards':{'items':[{'id':'drive_console','url_path':'drive-console'}]},
               'lovelace.drive_console':copy.deepcopy(before),
               'car_console_classic_migration':{'completed':True,'resource':'/local/drive-console.js?v=old'}}
        self.migrate(state)
        views=state['lovelace.drive_console']['config']['views']
        self.assertEqual(views[0]['cards'][0],old)
        self.assertEqual(views[1],navigation)
        self.assertEqual(state['car_console_tabs_backup'],before)
        self.assertEqual(sum(view['path']=='navigation' for view in views),1)
        views[:]=[view for view in views if view['path']!='media']
        views[0]['cards'][0]['title']='Later home edit'
        self.migrate(state,url='/local/drive-console.js?v=next')
        self.assertFalse(any(view['path']=='media' for view in state['lovelace.drive_console']['config']['views']))
        self.assertEqual(state['lovelace.drive_console']['config']['views'][0]['cards'][0]['title'],'Later home edit')

    def test_seed_cards_are_independent_across_pages(self):
        config=ha.classic_dashboard()
        home=config['views'][0]['cards']
        navigation=config['views'][1]['cards']
        next(card for card in navigation if card['widget']=='map')['title']='Navigation only'
        self.assertNotIn('title',next(card for card in home if card['widget']=='map'))

    def test_feature_upgrade_preserves_edited_tabs_and_never_recreates_later_deletions(self):
        health={'path':'health','title':'My diagnostics','cards':[{'type':'markdown','content':'Owner edit'}]}
        old={'config':{'views':[health,{'path':'drive','cards':[]}]}}
        state={'lovelace_dashboards':{'items':[{'id':'drive_console','url_path':'drive-console'}]},
               'lovelace.drive_console':copy.deepcopy(old),
               'car_console_classic_migration':{'completed':True,'dashboardSchema':2,'resource':'/local/drive-console.js?v=old'}}
        self.migrate(state);views=state['lovelace.drive_console']['config']['views']
        self.assertEqual(views[0]['cards'][0],health['cards'][0]);self.assertEqual(views[0]['title'],'My diagnostics')
        self.assertEqual(state['car_console_features_backup'],old)
        self.assertTrue(any(view['path']=='recordings' for view in views))
        self.assertFalse(any(view.get('path')=='updates' for view in views)) # An old deleted tab stays deleted.
        views[:]=[view for view in views if view.get('path')!='recordings'];before=copy.deepcopy(views)
        self.migrate(state,url='/local/drive-console.js?v=later');self.assertEqual(state['lovelace.drive_console']['config']['views'],before)

    def test_every_implemented_service_has_an_editable_native_card(self):
        features={card['feature'] for view in ha.classic_dashboard()['views'] for card in view['cards'] if card.get('type')=='custom:drive-console-service'}
        self.assertEqual(features,{'overview','network','power','storage','cameras','updates','radio','messages','sync','reports','preferences','home'})
        links=[card for view in ha.classic_dashboard()['views'] for card in view['cards'] if card.get('widget')=='link']
        self.assertTrue(all(card['url'].startswith('/') and card.get('new_tab') is False for card in links))

    def test_generated_module_reuses_original_art_and_is_versioned_by_content(self):
        module,metadata=ha.classic_resource(ROOT)
        self.assertNotIn(b'const ORIGINAL = null;',module)
        self.assertIn(b'truck-art',module)
        self.assertIn(b'YOUR LOCAL HEAD UNIT',module)
        import hashlib
        self.assertEqual(metadata['sha256'],hashlib.sha256(module).hexdigest())
        self.assertIn(metadata['sha256'][:20],metadata['url'])

    def test_shipped_bundle_matches_editable_sources_and_theme(self):
        from dev.build_drive_console import bundle
        self.assertEqual((ROOT/'home_assistant/drive_console.js').read_text(),bundle())

    def test_reconcile_tracks_resource_versions_without_reseeding_deleted_dashboards(self):
        import tempfile
        with tempfile.TemporaryDirectory() as temp:
            base=Path(temp);storage=base/'config/.storage';storage.mkdir(parents=True)
            (storage/'car_console_starter_migration').write_text('{}')
            (storage/'car_console_dashboard_link').write_text('{"data":{"completed":true}}')
            metadata=base/'config/drive-console-resource.json'
            metadata.write_text(json.dumps({'url':'/local/drive-console.js?v=one'}))
            marker=storage/'car_console_classic_migration'
            marker.write_text(json.dumps({'data':{'completed':True,'resource':'/local/drive-console.js?v=one'}}))
            with patch.object(ha,'BASE',base):
                self.assertFalse(ha.starter_pending())
                metadata.write_text(json.dumps({'url':'/local/drive-console.js?v=two'}))
                self.assertTrue(ha.starter_pending())

    def test_existing_drive_console_and_conflicting_resource_are_preserved(self):
        config={'views':[{'title':'Custom'}]}
        state={'lovelace_dashboards':{'items':[{'id':'personal','url_path':'drive-console'}]},'lovelace.personal':{'config':config}}
        self.migrate(state)
        self.assertEqual(state['lovelace.personal']['config'],config)
        self.assertNotIn('lovelace.drive_console',state)
        conflict={'lovelace_resources':{'items':[{'id':'drive_console_local','url':'/local/other.js','type':'module'}]}}
        before=copy.deepcopy(conflict)
        with self.assertRaises(ValueError):self.migrate(conflict)
        self.assertEqual(conflict,before)

    def test_managed_starter_gets_one_link_without_resetting_edits_or_restoring_deletions(self):
        starter={'config':{'views':[{'title':'My page','cards':[{'type':'markdown','content':'Keep my notes','tap_action':None}]}]}}
        state={'car_console_starter_migration':{'completed':True},'lovelace.car_starter':copy.deepcopy(starter),
               'lovelace_dashboards':{'items':[{'id':'car_starter','url_path':'car-starter'}]}}
        url='/local/drive-console.'+'a'*20+'.js';self.migrate(state,url)
        cards=state['lovelace.car_starter']['config']['views'][0]['cards']
        self.assertEqual(cards[0]['tap_action']['navigation_path'],'/drive-console');self.assertEqual(cards[1:],starter['config']['views'][0]['cards'])
        self.assertEqual(state['car_console_starter_link_backup'],starter)
        cards.pop(0);before=copy.deepcopy(state['lovelace.car_starter']);self.migrate(state,url)
        self.assertEqual(state['lovelace.car_starter'],before)
