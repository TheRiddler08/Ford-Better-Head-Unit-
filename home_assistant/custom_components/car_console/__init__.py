"""Read-only car entities backed by the independent local dashboard recorder."""
from datetime import timedelta
import logging
import os
from urllib.parse import urlsplit
from aiohttp import ClientTimeout
from homeassistant.const import Platform
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

DOMAIN='car_console'
PLATFORMS=[Platform.SENSOR,Platform.BINARY_SENSOR,Platform.DEVICE_TRACKER]
CONFIG_SCHEMA=cv.empty_config_schema(DOMAIN)


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
    session=async_get_clientsession(hass)
    url=backend_url()
    async def update():
        try:
            async with session.get(url,timeout=ClientTimeout(total=5)) as response:
                response.raise_for_status()
                data=await response.json()
            if data.get('schema')!=1 or not isinstance(data.get('metrics'),dict):
                raise ValueError('Unexpected car data schema')
            return data
        except Exception as exc:
            raise UpdateFailed('Local car data unavailable') from exc
    coordinator=DataUpdateCoordinator(hass,logging.getLogger(__name__),name='Car Console',
        config_entry=entry,update_method=update,update_interval=timedelta(seconds=5))
    await coordinator.async_config_entry_first_refresh()
    hass.data.setdefault(DOMAIN,{})[entry.entry_id]=coordinator
    await hass.config_entries.async_forward_entry_setups(entry,PLATFORMS)
    return True


async def async_unload_entry(hass,entry):
    unloaded=await hass.config_entries.async_unload_platforms(entry,PLATFORMS)
    if unloaded:hass.data[DOMAIN].pop(entry.entry_id,None)
    return unloaded
