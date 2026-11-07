"""Navigation persistence, input validation, local routing and raster pack checks."""
import ast,asyncio,copy,json,math,re,time,types,unittest,sqlite3,tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import patch,MagicMock
from aiohttp import ClientError,ClientTimeout,web
from app.server import mbtiles_tile

ROOT=Path(__file__).resolve().parents[1]
nav=types.ModuleType('navigation_test')
nav.__dict__.update(asyncio=asyncio,json=json,math=math,re=re,time=time,datetime=datetime,web=web,ClientError=ClientError,ClientTimeout=ClientTimeout,HomeAssistantView=type('HomeAssistantView',(),{}))
parsed=ast.parse((ROOT/'home_assistant/custom_components/car_console/__init__.py').read_text())
parsed.body=[node for node in parsed.body if isinstance(node,(ast.FunctionDef,ast.ClassDef)) and node.name in ('nav_point','navigation_default','validate_navigation','CarNavigationView','CarRouteView','CarGPSHistoryView')]
exec(compile(parsed,'navigation_api','exec'),nav.__dict__)

class Request:
    def __init__(self,data=None,query=None):self.data=data;self.query=query or {};self.content_length=len(json.dumps(data).encode())
    async def json(self):return copy.deepcopy(self.data)

class Store:
    def __init__(self,hass,version,key):self.hass=hass;self.key=key
    async def async_load(self):return copy.deepcopy(self.hass.get(self.key))
    async def async_save(self,data):self.hass[self.key]=copy.deepcopy(data)

class Response:
    status=200
    def __init__(self,data):self.data=json.dumps(data).encode();self.content=self
    async def __aenter__(self):return self
    async def __aexit__(self,*_):pass
    async def iter_chunked(self,size):yield self.data

class NavigationTests(unittest.IsolatedAsyncioTestCase):
    async def test_saved_defaults_markers_and_manual_trip_survive_reopen_and_conflicts(self):
        storage=types.ModuleType('homeassistant.helpers.storage');storage.Store=Store
        with patch.dict('sys.modules',{'homeassistant.helpers.storage':storage}):
            hass={};view=nav.CarNavigationView(hass)
            self.assertTrue(view.requires_auth)
            data=nav.navigation_default();self.assertEqual(data['settings']['routingRegions'],['us'])
            data['revision']=1;data['settings'].update(lat=40,lon=-100,zoom=8)
            data['markers']=[{'id':'home','name':'My place','lat':40,'lon':-100}]
            data['trips']=[{'id':'trip','name':'Manual journey','origin':{'lat':40,'lon':-100},'destination':{'lat':41,'lon':-99},'departure':'2026-11-07T09:00:00-05:00','arrival':'2026-11-07T10:00:00-05:00','status':'completed','notes':'Manually supplied times'}]
            self.assertEqual((await view.post(Request(data))).status,200)
            other=nav.CarNavigationView(hass)
            saved=json.loads((await other.get(Request())).body)
            self.assertEqual(saved['trips'],data['trips'])
            self.assertEqual(saved['settings']['lat'],40)
            self.assertEqual((await other.post(Request(data))).status,409)
            bad=copy.deepcopy(data);bad['revision']=2;bad['trips'][0]['arrival']='2026-11-07T08:00:00-05:00'
            self.assertEqual((await view.post(Request(bad))).status,400)
            self.assertEqual(json.loads((await view.get(Request())).body),data)
    async def test_only_actual_router_geometry_is_returned(self):
        session=MagicMock();session.get.return_value=Response({'code':'Ok','routes':[{'geometry':{'coordinates':[[-100,40],[-99,41]]},'distance':1234,'duration':120,'legs':[{'steps':[{'maneuver':{'type':'turn','modifier':'left'},'name':'Main Street'}]}]}]})
        view=nav.CarRouteView(session);self.assertTrue(view.requires_auth)
        result=await view.post(Request({'waypoints':[{'lat':40,'lon':-100},{'lat':41,'lon':-99}]}))
        self.assertEqual(result.status,200)
        data=json.loads(result.body);self.assertEqual(data['geometry'][0],{'lat':40,'lon':-100})
        self.assertEqual(data['instructions'],['turn left Main Street'])
        self.assertTrue(session.get.call_args.args[0].startswith('http://127.0.0.1:5000/route/v1/driving/'))
        session.get.side_effect=OSError('offline')
        result=await view.post(Request({'waypoints':[{'lat':40,'lon':-100},{'lat':41,'lon':-99}]}))
        self.assertEqual(result.status,503);self.assertNotIn('geometry',json.loads(result.body))
    async def test_bad_positions_and_trip_types_are_rejected(self):
        for point in ({'lat':True,'lon':0},{'lat':91,'lon':0},{'lat':0,'lon':float('nan')}):
            with self.assertRaises(ValueError):nav.nav_point(point)
        data=nav.navigation_default();data['settings']['routingRegions']=['us','west-virginia']
        with self.assertRaises(ValueError):nav.validate_navigation(data)
    async def test_raster_mbtiles_tms_rows_and_no_empty_file_creation(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'usa.mbtiles'
            self.assertIsNone(mbtiles_tile(1,0,0,path));self.assertFalse(path.exists())
            png=b'\x89PNG\r\n\x1a\nfixture'
            with sqlite3.connect(path) as db:
                db.execute('CREATE TABLE tiles(zoom_level INTEGER,tile_column INTEGER,tile_row INTEGER,tile_data BLOB)')
                db.execute('INSERT INTO tiles VALUES(1,0,1,?)',(png,))
            self.assertEqual(mbtiles_tile(1,0,0,path),(png,'image/png'))
            self.assertIsNone(mbtiles_tile(1,0,1,path))
