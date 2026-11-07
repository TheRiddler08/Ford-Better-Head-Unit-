"""Read-only car entities backed by the independent local dashboard recorder."""
from datetime import timedelta, datetime
import logging
import math
import re
import os
import json
import time
import tempfile
from pathlib import Path
from urllib.parse import urlsplit
from aiohttp import ClientError, ClientTimeout, ClientSession, TCPConnector, web
import asyncio
from homeassistant.components.http import HomeAssistantView
from homeassistant.const import Platform
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

DOMAIN='car_console'
PLATFORMS=[Platform.SENSOR,Platform.BINARY_SENSOR,Platform.DEVICE_TRACKER]
CONFIG_SCHEMA=cv.empty_config_schema(DOMAIN)



def coordinates(z, x, y):
    if any(not str(v).isdigit() or len(str(v))>8 for v in (z,x,y)):
        raise ValueError('Invalid tile coordinates')
    z,x,y=map(int,(z,x,y))
    if not 0<=z<=18 or not 0<=x<2**z or not 0<=y<2**z:
        raise ValueError('Invalid tile coordinates')
    return z,x,y


class CarTileView(HomeAssistantView):
    url='/api/car_console/tiles/{z}/{x}/{y}.png'
    name='api:car_console:tiles'
    requires_auth=True

    def __init__(self, session, origin):
        self.session=session
        self.origin=origin

    async def get(self, request, z, x, y):
        try:z,x,y=coordinates(z,x,y)
        except ValueError:return web.Response(status=400)
        # Origin comes from backend_url's validated loopback address, never input.
        try:
            async with self.session.get(f'{self.origin}/tiles/{z}/{x}/{y}.png',timeout=ClientTimeout(total=3),allow_redirects=False) as response:
                if response.status!=200:return web.Response(status=404)
                data=bytearray()
                async for chunk in response.content.iter_chunked(65536):
                    data.extend(chunk)
                    if len(data)>2097152:return web.Response(status=502)
                mime='image/png' if data.startswith(b'\x89PNG\r\n\x1a\n') else 'image/jpeg' if data.startswith(b'\xff\xd8\xff') else None
                if not mime:return web.Response(status=502)
                return web.Response(body=bytes(data),content_type=mime,headers={'Cache-Control':'private, max-age=86400'})
        except (OSError,ClientError,asyncio.TimeoutError):
            return web.Response(status=503)

def nav_point(value):
    if not isinstance(value,dict):raise ValueError('Expected a coordinate')
    lat,lon=value.get('lat'),value.get('lon')
    if any(isinstance(n,bool) or not isinstance(n,(int,float)) or not math.isfinite(n) for n in (lat,lon)) or not -90<=lat<=90 or not -180<=lon<=180:
        raise ValueError('Invalid coordinates')
    return {'lat':lat,'lon':lon}


def navigation_default():
    return {'revision':0,'settings':{'lat':39.5,'lon':-98.35,'zoom':5,'followGPS':True,'usaOnly':True,'routingRegions':['us']},'markers':[],'trips':[],
        'route':{'waypoints':[],'geometry':[],'distance':None,'duration':None,'instructions':[]}}


def validate_navigation(data):
    if not isinstance(data,dict) or len(json.dumps(data,allow_nan=False).encode())>2000000:raise ValueError('Navigation data exceeds limit')
    if set(data)!=set(navigation_default()) or type(data['revision']) is not int or data['revision']<0:raise ValueError('Invalid navigation document')
    settings=data['settings'];nav_point(settings)
    if type(settings.get('zoom')) is not int or not 2<=settings['zoom']<=18:raise ValueError('Invalid map zoom')
    if any(type(settings.get(k)) is not bool for k in ('followGPS','usaOnly')):raise ValueError('Invalid map settings')
    regions=settings.get('routingRegions',['us'])
    if not isinstance(regions,list) or not 1<=len(regions)<=51 or any(not isinstance(region,str) or not re.fullmatch('[a-z][a-z-]{1,31}',region) for region in regions) or ('us' in regions and regions!=['us']):raise ValueError('Invalid routing regions')
    def label(value,limit):
        if not isinstance(value,str) or len(value)>limit or any(ord(c)<32 and c not in '\n\t' for c in value):raise ValueError('Invalid text')
    for key in ('markers','trips'):
        if not isinstance(data[key],list) or len(data[key])>500:raise ValueError('Too many saved entries')
        ids=set()
        for item in data[key]:
            if not isinstance(item,dict):raise ValueError('Invalid saved entry')
            label(item.get('id'),64);label(item.get('name'),160)
            if not item['id'] or item['id'] in ids:raise ValueError('Duplicate entry')
            ids.add(item['id'])
            if key=='markers':nav_point(item)
            else:
                for place in ('origin','destination'):nav_point(item[place])
                if 'waypoints' in item:
                    if not isinstance(item['waypoints'],list) or not 2<=len(item['waypoints'])<=25:raise ValueError('Invalid saved trip waypoints')
                    for point in item['waypoints']:nav_point(point)
                label(item.get('notes',''),4000)
                if item.get('status') not in ('planned','completed'):raise ValueError('Invalid trip status')
                stamps=[]
                for name in ('departure','arrival'):
                    value=item.get(name)
                    if value:
                        label(value,64)
                        stamp=datetime.fromisoformat(value.replace('Z','+00:00'))
                        if stamp.tzinfo is None:raise ValueError('Trip times require a timezone')
                        stamps.append(stamp)
                if len(stamps)==2 and stamps[1]<stamps[0]:raise ValueError('Arrival is before departure')
    route=data['route']
    if not isinstance(route,dict):raise ValueError('Invalid route')
    for name,limit in (('waypoints',32),('geometry',30000)):
        if not isinstance(route.get(name),list) or len(route[name])>limit:raise ValueError('Too many route points')
        for point in route[name]:nav_point(point)
    if not isinstance(route.get('instructions'),list) or len(route['instructions'])>2000:raise ValueError('Too many directions')
    for instruction in route['instructions']:label(instruction,1000)
    for name in ('distance','duration'):
        n=route.get(name)
        if n is not None and (isinstance(n,bool) or not isinstance(n,(float,int)) or not math.isfinite(n) or n<0):raise ValueError('Invalid route measurement')
    return json.loads(json.dumps(data,allow_nan=False))


class CarNavigationView(HomeAssistantView):
    url='/api/car_console/navigation'
    name='api:car_console:navigation'
    requires_auth=True
    def __init__(self,hass):
        from homeassistant.helpers.storage import Store
        self.store=Store(hass,1,'car_console_navigation')
        self.lock=asyncio.Lock()
    async def get(self,request):
        async with self.lock:data=await self.store.async_load() or navigation_default()
        return web.json_response(data)
    async def post(self,request):
        if request.content_length is None or request.content_length>2000000:return web.json_response({'error':'Navigation document exceeds limit'},status=413)
        try:
            data=validate_navigation(await request.json())
            async with self.lock:
                previous=await self.store.async_load() or navigation_default()
                if data['revision']!=previous['revision']+1:return web.json_response({'error':'Navigation changed in another window. Reload before saving.'},status=409)
                await self.store.async_save(data)
            return web.json_response(data)
        except (ValueError,TypeError,KeyError,OverflowError):return web.json_response({'error':'Invalid navigation data'},status=400)


class CarGPSHistoryView(HomeAssistantView):
    url='/api/car_console/gps-history'
    name='api:car_console:gps-history'
    requires_auth=True
    def __init__(self,session,origin):self.session=session;self.origin=origin
    async def get(self,request):
        try:
            after=int(request.query.get('after','0'))
            start=float(request.query.get('from',str(time.time()-86400)))
            end=float(request.query.get('to',str(time.time())))
            if after<0 or not all(math.isfinite(v) for v in (start,end)) or start>end:raise ValueError('Invalid history range')
            async with self.session.get(f'{self.origin}/api/report/gps?limit=2000&after={after}&from={start}&to={end}',allow_redirects=False,timeout=ClientTimeout(total=5)) as response:
                if response.status!=200:return web.json_response({'error':'GPS history unavailable'},status=503)
                raw=bytearray()
                async for chunk in response.content.iter_chunked(65536):
                    raw.extend(chunk)
                    if len(raw)>2000000:return web.json_response({'error':'History page too large'},status=502)
                return web.json_response(json.loads(raw))
        except (ValueError,TypeError,ClientError,OSError,asyncio.TimeoutError):return web.json_response({'error':'Invalid or unavailable GPS history range'},status=400)


class CarRouteView(HomeAssistantView):
    url='/api/car_console/route'
    name='api:car_console:route'
    requires_auth=True
    def __init__(self,session):self.session=session
    async def post(self,request):
        if request.content_length is None or request.content_length>16384:return web.json_response({'error':'Too many waypoints'},status=413)
        try:
            points=(await request.json())['waypoints']
            if not isinstance(points,list) or not 2<=len(points)<=25:raise ValueError('Choose 2–25 waypoints')
            points=[nav_point(point) for point in points]
        except (ValueError,KeyError,TypeError):return web.json_response({'error':'Choose valid start and destination coordinates'},status=400)
        path=';'.join(f"{p['lon']},{p['lat']}" for p in points)
        radiuses=';'.join('1000' for _ in points)
        try:
            async with self.session.get(f'http://127.0.0.1:5000/route/v1/driving/{path}?overview=full&geometries=geojson&steps=true&radiuses={radiuses}',allow_redirects=False,timeout=ClientTimeout(total=20)) as response:
                if response.status!=200:raise ValueError('Router rejected the route')
                raw=bytearray()
                async for chunk in response.content.iter_chunked(65536):
                    raw.extend(chunk)
                    if len(raw)>4000000:raise ValueError('Route exceeds limit')
                result=json.loads(raw)
                if result.get('code')!='Ok' or not result.get('routes'):raise ValueError('No road route found in installed region')
                route=result['routes'][0]
                geometry=[{'lat':lat,'lon':lon} for lon,lat in route['geometry']['coordinates']]
                directions=[]
                for leg in route['legs']:
                    for step in leg['steps']:
                        maneuver=step['maneuver'];directions.append(' '.join(str(x) for x in (maneuver.get('type',''),maneuver.get('modifier',''),step.get('name','')) if x))
                value={'waypoints':points,'geometry':geometry,'distance':route['distance'],'duration':route['duration'],'instructions':directions}
                probe=navigation_default();probe['route']=value;validate_navigation(probe)
                return web.json_response(value)
        except (OSError,ClientError,asyncio.TimeoutError,ValueError,KeyError,TypeError):
            return web.json_response({'error':'Road routing unavailable for this region. Install the local OSRM regional pack; your waypoints can still be saved.'},status=503)


def backend_url():
    url=os.getenv('CAR_CONSOLE_URL','http://127.0.0.1:8787/api/home-assistant/state')
    parsed=urlsplit(url)
    if (parsed.scheme!='http' or parsed.hostname!='127.0.0.1' or parsed.username or parsed.password
        or parsed.path!='/api/home-assistant/state' or parsed.query or parsed.fragment):
        raise ValueError('Car Console backend must be the local read-only endpoint')
    return url


async def async_setup(hass,config):
    if DOMAIN in config:
        hass.async_create_task(hass.config_entries.flow.async_init(DOMAIN,context={'source':'import'},data={}))
    return True


async def async_setup_entry(hass,entry):
    url=backend_url()
    # This API is strictly loopback-only; it must work even with no usable LAN
    # interface while the portable display is away from the car.
    session=ClientSession(connector=TCPConnector(local_addr=('127.0.0.1',0)))
    entry.async_on_unload(lambda:hass.async_create_task(session.close()))
    parsed=urlsplit(url)
    hass.http.register_view(CarTileView(session,f'{parsed.scheme}://{parsed.netloc}'))
    hass.http.register_view(CarNavigationView(hass))
    hass.http.register_view(CarRouteView(session))
    hass.http.register_view(CarGPSHistoryView(session,f'{parsed.scheme}://{parsed.netloc}'))
    platforms_ready=False
    async def health(ready):
        if not platforms_ready:return
        count=len(er.async_entries_for_config_entry(er.async_get(hass),entry.entry_id))
        data={'schema':1,'ready':bool(count>=11),'backendReady':bool(ready),'entities':count,'checkedAt':time.time()}
        path=Path(hass.config.path('car-console-health.json'))
        def write():
            with tempfile.NamedTemporaryFile(mode='w',dir=path.parent,delete=False) as f:
                json.dump(data,f);pending=Path(f.name)
            os.replace(pending,path)
        await hass.async_add_executor_job(write)
    async def update():
        try:
            async with session.get(url,timeout=ClientTimeout(total=5)) as response:
                response.raise_for_status()
                data=await response.json()
            if data.get('schema')!=1 or not isinstance(data.get('metrics'),dict):
                raise ValueError('Unexpected car data schema')
            await health(True)
            return data
        except Exception as exc:
            await health(False)
            raise UpdateFailed('Local car data unavailable') from exc
    coordinator=DataUpdateCoordinator(hass,logging.getLogger(__name__),name='Car Console',
        config_entry=entry,update_method=update,update_interval=timedelta(seconds=5))
    # A dashboard must not depend on a running GPS/OBD/recorder service. Seed
    # only an empty schema, never previous readings or fabricated zero values.
    coordinator.data={'schema':1,'metrics':{},'gps':None,'obdFresh':False}
    coordinator.last_update_success=False
    hass.data.setdefault(DOMAIN,{})[entry.entry_id]=coordinator
    await hass.config_entries.async_forward_entry_setups(entry,PLATFORMS)
    platforms_ready=True
    await health(False)
    # Register native entities before any network request. Coordinator listeners
    # schedule subsequent polling even if this first background request fails.
    entry.async_create_background_task(hass,coordinator.async_refresh(),
        'Car Console initial refresh')
    return True


async def async_unload_entry(hass,entry):
    unloaded=await hass.config_entries.async_unload_platforms(entry,PLATFORMS)
    if unloaded:hass.data[DOMAIN].pop(entry.entry_id,None)
    return unloaded
