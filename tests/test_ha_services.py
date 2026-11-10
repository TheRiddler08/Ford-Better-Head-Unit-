"""Exercise the native HA feature proxies without installing the HA runtime."""
import ast,asyncio,json,types,unittest,tempfile,hashlib
from pathlib import Path
from unittest.mock import MagicMock
try:
    import aiohttp
    from aiohttp import web
    from aiohttp.test_utils import TestClient,TestServer
    from multidict import MultiDict
except ModuleNotFoundError:raise unittest.SkipTest('Normal HA HTTP test dependencies required')

module=types.ModuleType('car_services_test');module.HomeAssistantView=type('HomeAssistantView',(),{})
source=ast.parse((Path(__file__).resolve().parents[1]/'home_assistant/custom_components/car_console/services.py').read_text())
source.body=[node for node in source.body if not(isinstance(node,ast.ImportFrom) and node.module=='homeassistant.components.http')]
exec(compile(source,'car_services','exec'),module.__dict__)


class ServiceTests(unittest.IsolatedAsyncioTestCase):
    def test_ui_identity_checks_actual_asset_and_registered_resource_without_owner_data(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'www').mkdir();(root/'.storage').mkdir();raw=b'const testBuild="abcdef123456";';digest=hashlib.sha256(raw).hexdigest();url='/local/drive-console.'+digest[:20]+'.js'
            (root/'www'/url.removeprefix('/local/')).write_bytes(raw)
            (root/'drive-console-resource.json').write_text(json.dumps({'build':'abcdef123456','url':url,'sha256':digest}))
            (root/'.storage/lovelace_resources').write_text(json.dumps({'data':{'items':[{'id':'drive_console_local','type':'module','url':url}]}}))
            (root/'.storage/auth').write_text('private owner authentication')
            state=module.ui_state(root);self.assertTrue(state['assetMatch']);self.assertTrue(state['resourceRegistered']);self.assertNotIn('private',json.dumps(state))
            (root/'www'/url.removeprefix('/local/')).write_bytes(b'old source');self.assertFalse(module.ui_state(root)['assetMatch'])
            self.assertTrue(module.CarUIView.requires_auth)
    async def asyncSetUp(self):
        self.requests=[];self.followed=False
        async def endpoint(request):
            self.requests.append((request.path,dict(request.query),dict(request.headers)))
            if request.path=='/api/update-status':raise web.HTTPFound('/unexpected')
            if request.path=='/unexpected':self.followed=True
            return web.json_response({'schema':1,'method':request.method,'data':await request.json() if request.method=='POST' else None})
        async def video(request):
            self.requests.append((request.path,dict(request.query),dict(request.headers)))
            body=bytes(range(256))*4096
            if request.headers.get('Range')=='bytes=1000-1999':return web.Response(body=body[1000:2000],status=206,headers={'Content-Range':'bytes 1000-1999/'+str(len(body)),'Accept-Ranges':'bytes'},content_type='video/mp4')
            return web.Response(body=body,content_type='video/mp4')
        async def csv(request):
            self.requests.append((request.path,dict(request.query),dict(request.headers)))
            response=web.StreamResponse(headers={'Content-Type':'text/csv','Content-Disposition':'attachment; filename="gps.csv"'})
            await response.prepare(request)
            for _ in range(100):await response.write(b'time,lat,lon\n')
            await response.write_eof();return response
        upstream=web.Application();upstream.router.add_route('*','/api/dashcam/clip',video);upstream.router.add_get('/api/report/export',csv);upstream.router.add_route('*','/{path:.*}',endpoint)
        self.upstream=TestServer(upstream);await self.upstream.start_server()
        self.session=aiohttp.ClientSession();origin=str(self.upstream.make_url('')).rstrip('/')
        self.service=module.CarServiceView(self.session,origin);self.download=module.CarDownloadView(self.session,origin)
        @web.middleware
        async def auth(request,handler):
            if request.headers.get('Authorization')!='Bearer test-ha':return web.Response(status=401)
            request['hass_user']=types.SimpleNamespace(is_admin=request.headers.get('X-Test-Admin')=='yes');return await handler(request)
        app=web.Application(middlewares=[auth]);app.router.add_route('*','/service/{feature}',lambda request:self.service.forward(request,request.match_info['feature'],request.method=='POST'))
        app.router.add_get('/download/{kind}',lambda request:self.download.get(request,request.match_info['kind']))
        self.client=TestClient(TestServer(app));await self.client.start_server()

    async def asyncTearDown(self):await self.client.close();await self.session.close();await self.upstream.close()

    async def test_authenticated_reads_and_admin_mutations_use_only_fixed_local_paths(self):
        self.assertTrue(self.service.requires_auth);self.assertEqual((await self.client.get('/service/system')).status,401)
        response=await self.client.get('/service/system',headers={'Authorization':'Bearer test-ha'});self.assertEqual(response.status,200)
        self.assertNotIn('Authorization',self.requests[-1][2])
        response=await self.client.post('/service/preferences',json={'revision':1},headers={'Authorization':'Bearer test-ha'});self.assertEqual(response.status,403)
        response=await self.client.post('/service/preferences',json={'revision':1},headers={'Authorization':'Bearer test-ha','X-Test-Admin':'yes'});self.assertEqual(response.status,200)
        self.assertEqual((await response.json())['data'],{'revision':1})
        for path in ['/service/anything','/service/system?url=http://example.org','/service/report?kind=../../etc/passwd','/service/system?after=1&after=2']:
            self.assertEqual((await self.client.get(path,headers={'Authorization':'Bearer test-ha'})).status,400)
        self.assertEqual((await self.client.get('/service/updates',headers={'Authorization':'Bearer test-ha'})).status,503);self.assertFalse(self.followed)

    async def test_video_range_and_csv_stream_strip_only_validated_ha_signature(self):
        headers={'Authorization':'Bearer test-ha','Range':'bytes=1000-1999'}
        response=await self.client.get('/download/clip?id=tiny:'+('a'*64)+'.mp4&authSig=scoped-test-signature',headers=headers)
        self.assertEqual(response.status,206);self.assertEqual(len(await response.read()),1000)
        self.assertIn('Content-Range',response.headers);self.assertNotIn('authSig',self.requests[-1][1]);self.assertNotIn('Authorization',self.requests[-1][2])
        response=await self.client.get('/download/csv?kind=gps&authSig=scoped-test-signature',headers={'Authorization':'Bearer test-ha'})
        self.assertEqual(response.status,200);self.assertEqual(await response.read(),b'time,lat,lon\n'*100)
        self.assertEqual((await self.client.get('/download/clip?id=../../secret',headers=headers)).status,400)

    async def test_map_overlay_viewports_stay_on_local_backend_and_mutations_need_admin(self):
        headers={'Authorization':'Bearer test-ha'}
        response=await self.client.get('/service/map-layers?west=-101&east=-99&south=39&north=41&zoom=14&layers=cameras,coverage&alertLat=40&alertLon=-100&alertMeters=300',headers=headers)
        self.assertEqual(response.status,200);self.assertEqual(self.requests[-1][0],'/api/map-layers')
        self.assertEqual(self.requests[-1][1]['alertLat'],'40');self.assertNotIn('Authorization',self.requests[-1][2])
        self.assertEqual((await self.client.post('/service/map-layers',json={'action':'refresh'},headers=headers)).status,403)
        response=await self.client.post('/service/map-layers',json={'action':'refresh'},headers=dict(headers,**{'X-Test-Admin':'yes'}))
        self.assertEqual(response.status,200);self.assertEqual((await response.json())['data'],{'action':'refresh'})

    async def test_home_owner_proxy_requires_admin_and_never_accepts_a_target_url(self):
        class Response:
            status=200
            def __init__(self):self.content=self
            async def __aenter__(self):return self
            async def __aexit__(self,*args):pass
            async def iter_chunked(self,size):yield b'{"phase":"pending"}'
        session=MagicMock();session.request.return_value=Response();home=module.CarHomeView(session)
        class Request(dict):
            query=MultiDict();headers={'X-Car-Home-Token':'private-owner-token-'*3}
        request=Request(hass_user=types.SimpleNamespace(is_admin=False))
        self.assertEqual((await home.get(request,'status')).status,403);session.request.assert_not_called()
        request['hass_user'].is_admin=True;response=await home.get(request,'status');self.assertEqual(response.status,200)
        args=session.request.call_args
        self.assertEqual(args.args[1],'http://127.0.0.1:8791/api/software/status');self.assertFalse(args.kwargs['allow_redirects'])
        self.assertEqual(args.kwargs['headers']['Authorization'],'Bearer '+request.headers['X-Car-Home-Token'])
        request.query=MultiDict(url='https://example.org');self.assertEqual((await home.get(request,'status')).status,400)
        self.assertEqual((await home.get(request,'credentials')).status,400)
