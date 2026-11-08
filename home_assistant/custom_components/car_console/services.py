"""Authenticated, fixed-destination access to the existing car/home services."""
import asyncio
import json
import re
from urllib.parse import urlencode
from aiohttp import ClientError,ClientTimeout,web
from homeassistant.components.http import HomeAssistantView

GET={'system':'/api/system/status','storage':'/api/car-storage','updates':'/api/update-status',
     'cameras':'/api/dashcam','reports':'/api/report/summary','messages':'/api/messages',
     'preferences':'/api/headunit-prefs','sync':'/api/local-sync','sync-file':'/api/local-sync/file'}
POST={'messages':'/api/messages','preferences':'/api/headunit-prefs','sync-file':'/api/local-sync/file','radio-test':'/api/radio-test'}
HOME={'status':'/api/software/status','transfers':'/api/software/transfers','transfer':'/api/software/transfer',
      'source':'/api/software/source','publish':'/api/software/publish','sync':'/api/sync','sync-file':'/api/sync/file',
      'config':'/api/config','change':'/api/change','layout':'/api/layout','layout-status':'/api/layout-status',
      'coverage':'/api/coverage','messages':'/api/messages','cameras':'/api/dashcam','pending':'/api/pending'}
HOME_POST={'transfer','publish','sync','sync-file','change','layout','messages'}
REPORTS={'values','commands','snapshots','snapshot-values','events','gps','gps-messages','stops','gps-stream','gps-sessions','gps-events'}
PARAMS={'device','role','state','before','id','path','name','after','limit','from','to','snapshot','status','kind'}


def query_string(query):
    if any((key not in PARAMS and key!='authSig') or len(query.getall(key))!=1 or len(query[key])>2048 for key in query):raise ValueError('Invalid service query')
    return urlencode({key:query[key] for key in query if key!='authSig'})


class CarServiceView(HomeAssistantView):
    url='/api/car_console/service/{feature}'
    name='api:car_console:service'
    requires_auth=True
    def __init__(self,session,origin):self.session=session;self.origin=origin
    async def get(self,request,feature):return await self.forward(request,feature,False)
    async def post(self,request,feature):return await self.forward(request,feature,True)
    async def forward(self,request,feature,post):
        if post and not getattr(request.get('hass_user'),'is_admin',False):return web.json_response({'error':'Administrator required'},status=403)
        try:
            if feature=='report' and not post:
                kind=request.query.get('kind','values')
                if kind not in REPORTS:raise ValueError('Unknown report')
                path='/api/report/'+kind
            else:
                path=(POST if post else GET).get(feature)
                if not path:raise ValueError('Unknown service action')
            query=query_string(request.query);body=None
            if post:
                if request.content_length is None or request.content_length>131072:return web.json_response({'error':'Request exceeds limit'},status=413)
                body=await request.json()
                if not isinstance(body,dict):raise ValueError('Expected an object')
            url=self.origin+path+('?' +query if query else '')
            async with self.session.request('POST' if post else 'GET',url,json=body,allow_redirects=False,timeout=ClientTimeout(total=8)) as response:
                if response.status not in (200,201,400,403,404,409,413,415,503):return web.json_response({'error':'Local service unavailable'},status=503)
                data=bytearray()
                async for chunk in response.content.iter_chunked(65536):
                    data.extend(chunk)
                    if len(data)>2097152:return web.json_response({'error':'Service response exceeds limit'},status=502)
                parsed=json.loads(data)
                return web.json_response(parsed,status=response.status,headers={'Cache-Control':'no-store'})
        except (ValueError,TypeError):return web.json_response({'error':'Invalid service request or response'},status=400)
        except (OSError,ClientError,asyncio.TimeoutError):return web.json_response({'error':'Local service unavailable; dashboard remains usable'},status=503)


class CarHomeView(HomeAssistantView):
    url='/api/car_console/home/{feature}'
    name='api:car_console:home'
    requires_auth=True
    def __init__(self,session):self.session=session
    async def get(self,request,feature):return await self.forward(request,feature,False)
    async def post(self,request,feature):return await self.forward(request,feature,True)
    async def forward(self,request,feature,post):
        if not getattr(request.get('hass_user'),'is_admin',False):return web.json_response({'error':'Administrator required'},status=403)
        token=request.headers.get('X-Car-Home-Token','')
        if not 24<=len(token)<=256 or any(ord(c)<33 or ord(c)>126 for c in token):return web.json_response({'error':'Enter the separate home panel token; it is kept only in this card memory'},status=401)
        if feature not in HOME or post and feature not in HOME_POST:return web.json_response({'error':'Unknown home action'},status=400)
        try:
            query=query_string(request.query);body=None
            if post:
                if request.content_length is None or request.content_length>2097152:return web.json_response({'error':'Request exceeds limit'},status=413)
                body=await request.json()
                if not isinstance(body,dict):raise ValueError('Expected object')
            # Restricted tunnel established by normal Tiny pairing. No client URL,
            # redirect, device credential endpoint or arbitrary host is accepted.
            url='http://127.0.0.1:8791'+HOME[feature]+('?' +query if query else '')
            async with self.session.request('POST' if post else 'GET',url,json=body,headers={'Authorization':'Bearer '+token},allow_redirects=False,timeout=ClientTimeout(total=12)) as response:
                if response.status not in (200,201,400,401,403,404,409,413,503):return web.json_response({'error':'Home connection unavailable'},status=503)
                data=bytearray()
                async for chunk in response.content.iter_chunked(65536):
                    data.extend(chunk)
                    if len(data)>2097152:return web.json_response({'error':'Home response exceeds limit'},status=502)
                return web.json_response(json.loads(data),status=response.status,headers={'Cache-Control':'no-store'})
        except (ValueError,TypeError):return web.json_response({'error':'Invalid home request or response'},status=400)
        except (OSError,ClientError,asyncio.TimeoutError):return web.json_response({'error':'Home tunnel unavailable; local features continue offline'},status=503)


class CarDownloadView(HomeAssistantView):
    url='/api/car_console/download/{kind}'
    name='api:car_console:download'
    requires_auth=True
    def __init__(self,session,origin):self.session=session;self.origin=origin
    async def get(self,request,kind):
        response=None
        try:
            query=query_string(request.query)
            if kind=='clip':
                if not re.fullmatch(r'(tiny|duet|pi|laptop):[a-f0-9]{64}\.(mp4|mov|mkv|ts)',request.query.get('id','')):raise ValueError('Invalid clip')
                path='/api/dashcam/clip'
            elif kind=='csv' and request.query.get('kind') in REPORTS:path='/api/report/export'
            else:raise ValueError('Unknown download')
            headers={}
            if requested:=request.headers.get('Range'):
                if not re.fullmatch(r'bytes=\d*-\d*',requested) or requested=='bytes=-':raise ValueError('Single byte range required')
                headers['Range']=requested
            async with self.session.get(self.origin+path+'?'+query,headers=headers,allow_redirects=False,timeout=ClientTimeout(total=None,sock_connect=5,sock_read=30)) as upstream:
                if upstream.status not in (200,206,416):return web.Response(status=404)
                allowed={key:upstream.headers[key] for key in ('Content-Type','Content-Length','Content-Range','Accept-Ranges','Content-Disposition') if key in upstream.headers}
                allowed.update({'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})
                response=web.StreamResponse(status=upstream.status,headers=allowed);await response.prepare(request)
                async for chunk in upstream.content.iter_chunked(65536):await response.write(chunk)
                await response.write_eof();return response
        except ValueError:return web.Response(status=400)
        except (OSError,ClientError,asyncio.TimeoutError):
            if response is not None:response.force_close();return response
            return web.Response(status=503)
