"""Read-only car entities backed by the independent local dashboard recorder."""
from datetime import timedelta
import logging
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
                if not data.startswith(b'\x89PNG\r\n\x1a\n'):return web.Response(status=502)
                return web.Response(body=bytes(data),content_type='image/png',headers={'Cache-Control':'private, max-age=86400'})
        except (OSError,ClientError,asyncio.TimeoutError):
            return web.Response(status=503)

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
