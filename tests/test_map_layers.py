"""Local, offline map layers; no public download or real GPS used by tests."""
from contextlib import closing
import gzip
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock,patch
from control import map_layers as layers,coverage
from deploy import fleet

GATEWAY='ac1f09fffe217e15'


class MapLayerTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'layers.sqlite3'
        self.camera={'osmId':123,'osmType':'node','lat':40.0,'lon':-100.0,'brand':'Flock Safety','operator':'<script>not executable</script>','direction':270}

    def query(self,**values):
        options=dict(west=-101,east=-99,south=39,north=41,zoom=14,layers='cameras,coverage,estimates');options.update(values)
        return layers.query(self.path,{key:[str(value)] for key,value in options.items()})

    def snapshot(self,**values):
        result={'instance':'a'*64,'next':1,'hasMore':False,'gateways':{GATEWAY:{'name':'Home gateway','role':'fixed','lat':40,'lon':-100}},
            'items':[{'id':1,'gateway':GATEWAY,'lat':40,'lon':-100,'received':1000,'rssi':-90,'snr':5,'eligible':1,'reason':'eligible'}]}
        result.update(values);return result

    def test_offline_camera_query_filters_clusters_and_retains_source(self):
        rows=[self.camera,dict(self.camera,osmId=124,brand='Motorola',lon=-100.0001),dict(self.camera,osmId=125,lon=-100.0002)]
        self.assertEqual(layers.import_cameras(self.path,rows,now=1000),3)
        data=self.query();self.assertEqual(len(data['cameras']),2);self.assertEqual(data['cameras'][0]['osm_id'],'123')
        self.assertEqual(data['cameraStatus']['fetchedAt'],1000)
        options={'west':['-101'],'east':['-99'],'south':['39'],'north':['41'],'zoom':['5'],'layers':['cameras'],'manufacturer':['all']}
        data=layers.query(self.path,options)
        self.assertTrue(all(row['cluster'] for row in data['cameras']));self.assertEqual(sum(row['count'] for row in data['cameras']),3)
        self.assertIn('OpenStreetMap',data['attribution'])
        self.assertEqual(layers.query(self.path,{'layers':['']})['cameras'],[])

    def test_invalid_and_empty_import_never_erase_usable_cache(self):
        layers.import_cameras(self.path,[self.camera])
        for rows in ([],[self.camera,dict(self.camera,osmId=124,lat=float('nan'))],[dict(self.camera,lat=True)]):
            with self.assertRaises(ValueError):layers.import_cameras(self.path,rows)
            self.assertEqual(len(self.query()['cameras']),1)

    def test_geojson_and_osm_import_use_stable_ids_and_no_external_urls(self):
        document={'type':'FeatureCollection','features':[{'type':'Feature','geometry':{'type':'Point','coordinates':[-100,40]},
            'properties':{'@id':'node/123','manufacturer':'Flock Safety','operator':'Example','website':'http://127.0.0.1/private'}}]}
        layers.import_cameras(self.path,document)
        item=self.query()['cameras'][0];self.assertEqual(item['id'],'node:123');self.assertNotIn('website',item)
        layers.import_cameras(self.path,{'elements':[dict(self.camera,tags={'manufacturer':'Flock Safety'})]})
        self.assertEqual(len(self.query()['cameras']),1)

    def test_nearby_alert_uses_local_gps_and_works_outside_browsed_viewport(self):
        layers.import_cameras(self.path,[self.camera])
        values={'west':['-80'],'east':['-79'],'south':['30'],'north':['31'],'zoom':['14'],'layers':['cameras'],
            'alertLat':['40'],'alertLon':['-100'],'alertMeters':['300']}
        result=layers.query(self.path,values)
        self.assertEqual(result['cameras'],[]);self.assertEqual(result['nearest'][0]['meters'],0)
        values.pop('alertLat');values.pop('alertLon');values.pop('alertMeters')
        self.assertEqual(layers.query(self.path,values)['nearest'],[])

    def test_antimeridian_camera_query_keeps_eastern_alaska(self):
        layers.import_cameras(self.path,[dict(self.camera,lat=52,lon=179),dict(self.camera,osmId=124,lat=52,lon=-179)])
        result=layers.query(self.path,{'west':['170'],'east':['-170'],'south':['50'],'north':['55'],'zoom':['14'],'layers':['cameras']})
        self.assertEqual(len(result['cameras']),2)

    def test_unknown_mobile_stale_and_missing_packets_do_not_become_measured_range(self):
        data=self.snapshot();data['gateways']['0011223344556677']={'role':'mobile'}
        data['items']+=[dict(data['items'][0],id=2,gateway='0011223344556677'),dict(data['items'][0],id=3,eligible=0,reason='gps_stale_or_clock_invalid')]
        layers.import_coverage(self.path,data);result=self.query()
        self.assertEqual(len(result['receptions']),1);self.assertEqual(result['coverageStatus']['count'],1)
        self.assertEqual(result['gateways'][0]['radius'],0)
        data=self.snapshot();data['gateways'][GATEWAY]['role']='mobile'
        layers.import_coverage(self.path,data);self.assertEqual(self.query()['receptions'],[])
        layers.action(self.path,{'action':'gateway','id':GATEWAY,'lat':40,'lon':-100,'name':'Local estimate','role':'fixed','radius':5})
        self.assertEqual(self.query()['receptions'],[])

    def test_gateway_edits_survive_home_sync_and_hidden_gateways_stay_hidden(self):
        layers.action(self.path,{'action':'gateway','id':GATEWAY,'name':'My fixed site','lat':41,'lon':-99,'role':'fixed','radius':12})
        layers.import_coverage(self.path,self.snapshot())
        row=self.query()['gateways'][0]
        self.assertEqual((row['label'],row['lat'],row['radius']),('My fixed site',41,12))
        layers.action(self.path,{'action':'hide-gateway','id':GATEWAY});layers.import_coverage(self.path,self.snapshot())
        self.assertEqual(self.query()['gateways'][0]['hidden'],1);self.assertEqual(self.query()['receptions'],[])

    def test_same_home_page_deduplicates_and_new_home_epoch_replaces_cache(self):
        layers.import_coverage(self.path,self.snapshot());layers.import_coverage(self.path,self.snapshot())
        self.assertEqual(len(self.query()['receptions']),1)
        layers.import_coverage(self.path,self.snapshot(instance='b'*64,items=[],next=0))
        self.assertEqual(self.query()['receptions'],[])

    def test_download_failure_retains_last_snapshot_and_uses_bounded_retry(self):
        layers.import_cameras(self.path,[self.camera],source='DeFlock / FlockHopper USA export',now=1000)
        agent=MagicMock();agent.call.side_effect=OSError('offline')
        opener=MagicMock();opener.open.side_effect=OSError('offline')
        with patch.object(layers,'build_opener',return_value=opener),patch('control.update_agent.Agent',return_value=agent):
            layers.tick(self.path,{},now=100000);layers.tick(self.path,{},now=100015)
        self.assertEqual(opener.open.call_count,1);self.assertEqual(self.query()['cameraStatus']['count'],1)
        self.assertEqual(self.query()['worker']['phase'],'download-unavailable-cache-retained')

    def test_public_download_is_fixed_country_snapshot_and_contains_no_gps_query(self):
        response=MagicMock();response.__enter__.return_value=response;response.read.return_value=gzip.compress(json.dumps([self.camera]).encode())
        opener=MagicMock();opener.open.return_value=response
        agent=MagicMock();agent.call.return_value=self.snapshot()
        with patch.object(layers,'build_opener',return_value=opener),patch('control.update_agent.Agent',return_value=agent):layers.tick(self.path,{},now=100000)
        request=opener.open.call_args.args[0]
        self.assertEqual(request.full_url,layers.CAMERA_URL);self.assertIsNone(request.data)
        self.assertNotIn('Authorization',request.headers);self.assertNotIn('?',request.full_url)
        self.assertEqual(self.query()['cameraStatus']['count'],1)
        agent.exchange.assert_not_called();agent.radio.assert_not_called()

    def test_home_epoch_change_refetches_from_zero_before_replacing_cache(self):
        layers.import_coverage(self.path,self.snapshot())
        agent=MagicMock();agent.call.side_effect=[self.snapshot(instance='b'*64,items=[],next=1),self.snapshot(instance='b'*64)]
        with patch('control.update_agent.Agent',return_value=agent):layers.tick(self.path,{'map_camera_download':False},now=100000)
        self.assertEqual([call.args[0] for call in agent.call.call_args_list],['/api/device/map-coverage?after=1','/api/device/map-coverage?after=0'])
        self.assertEqual(len(self.query()['receptions']),1)

    def test_real_home_http_feed_requires_device_credential_and_strips_raw_data(self):
        import threading
        from http.server import ThreadingHTTPServer
        from urllib.request import Request,urlopen
        from urllib.error import HTTPError
        from control import home_server
        root=Path(self.temp.name)/'home';root.mkdir()
        (root/'gateways.json').write_text(json.dumps({GATEWAY:{'role':'fixed','lat':40,'lon':-100,'secret':'not forwarded'}}))
        with patch.object(home_server,'ROOT',root),patch.object(home_server,'DEVICE_TOKEN','test-device-'+'d'*32),patch.object(home_server,'TOKEN','test-owner-'+'a'*32):
            http=ThreadingHTTPServer(('127.0.0.1',0),home_server.Handler)
            thread=threading.Thread(target=http.serve_forever,daemon=True);thread.start()
            try:
                url='http://127.0.0.1:'+str(http.server_port)+'/api/device/map-coverage?after=0'
                for token in ('','test-owner-'+'a'*32):
                    with self.assertRaises(HTTPError) as error:urlopen(Request(url,headers={'Authorization':'Bearer '+token}),timeout=3)
                    self.assertEqual(error.exception.code,401)
                with urlopen(Request(url,headers={'Authorization':'Bearer test-device-'+'d'*32}),timeout=3) as response:data=json.load(response)
                self.assertEqual(data['gateways'][GATEWAY]['role'],'fixed');self.assertNotIn('secret',json.dumps(data))
                registration={'id':GATEWAY,'name':'Registered site','role':'mobile','lat':41,'lon':-99}
                payload=json.dumps(registration).encode();target='http://127.0.0.1:'+str(http.server_port)+'/api/gateways'
                with self.assertRaises(HTTPError) as error:urlopen(Request(target,data=payload,headers={'Authorization':'Bearer test-device-'+'d'*32,'Content-Type':'application/json'}),timeout=3)
                self.assertEqual(error.exception.code,401)
                with urlopen(Request(target,data=payload,headers={'Authorization':'Bearer test-owner-'+'a'*32,'Content-Type':'application/json'}),timeout=3) as response:updated=json.load(response)
                self.assertEqual(updated['gateways'][GATEWAY]['role'],'mobile')
                self.assertEqual(json.loads((root/'gateways.json').read_text())[GATEWAY]['secret'],'not forwarded')
            finally:http.shutdown();thread.join();http.server_close()

    def test_home_registration_edits_nested_locations_and_can_clear_them(self):
        path=Path(self.temp.name)/'gateways.json'
        path.write_text(json.dumps({GATEWAY:{'role':'fixed','location':{'latitude':40,'longitude':-100,'altitude':10}}}))
        data={'id':GATEWAY,'name':'Site','role':'fixed','lat':41,'lon':-99}
        self.assertEqual(coverage.register_gateway(path,data)['gateways'][GATEWAY]['lat'],41)
        data.update(lat=None,lon=None)
        self.assertNotIn('lat',coverage.register_gateway(path,data)['gateways'][GATEWAY])
        self.assertEqual(json.loads(path.read_text())[GATEWAY]['location']['altitude'],10)

    def test_home_feed_whitelists_fields_and_requires_positive_eligibility(self):
        source=Path(self.temp.name)/'home.sqlite3'
        event={'deduplicationId':'event','object':{'carGpsV1':{'lat':40,'lon':-100,'ts':1000,'gatewayMode':'client','gatewayModeAt':1000}},'rxInfo':[{'gatewayId':GATEWAY,'rssi':-90,'snr':5,'private':'do not forward'}]}
        coverage.ingest(source,event,{GATEWAY:{'role':'fixed'}},1001)
        report=coverage.map_report(source,{GATEWAY:{'role':'fixed','token':'private','lat':40,'lon':-100}})
        raw=json.dumps(report);self.assertNotIn('raw',raw);self.assertNotIn('private',raw);self.assertNotIn('token',raw)
        layers.import_coverage(self.path,report);self.assertEqual(len(self.query()['receptions']),1)

    def test_worker_is_tiny_only_and_explicit_optout_wins(self):
        self.assertIn(('car-map-layers','control.map_layers'),fleet.application_workers('tiny',{}))
        for role in ('pi','duet','laptop'):self.assertNotIn(('car-map-layers','control.map_layers'),fleet.application_workers(role,{}))
        self.assertNotIn(('car-map-layers','control.map_layers'),fleet.application_workers('tiny',{'map_layers':False}))

    def test_bundled_country_snapshot_is_checked_and_does_not_replace_a_user_import(self):
        repo=Path(__file__).resolve().parents[1]
        layers.seed_snapshot(self.path,repo)
        result=self.query();self.assertEqual(result['cameraStatus']['count'],61768)
        self.assertEqual(result['cameraStatus']['sourceDate'],'2026-10-08')
        self.assertEqual(self.path.stat().st_mode&0o777,0o600)
        layers.import_cameras(self.path,[self.camera]);layers.seed_snapshot(self.path,repo)
        self.assertEqual(self.query()['cameraStatus']['count'],1)


if __name__=='__main__':unittest.main()
