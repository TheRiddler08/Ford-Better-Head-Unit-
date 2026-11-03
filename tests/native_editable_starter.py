"""Run only in an isolated HA image with a disposable /config test directory."""
import asyncio
import json
import os
from pathlib import Path
import sys
sys.path.insert(0,'/src')
from deploy.home_assistant import STARTER_MIGRATION,configuration,dashboard
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.components.lovelace.dashboard import LovelaceStorage

assert os.getenv('CAR_DISPOSABLE_VALIDATION')=='1','Disposable HA container required'
config=Path('/config');config.mkdir(parents=True,exist_ok=True)
if not (config/'configuration.yaml').exists():
    (config/'configuration.yaml').write_text(json.dumps(configuration('UTC')))
    (config/'car-dashboard.yaml').write_text(json.dumps(dashboard()))
exec(STARTER_MIGRATION)

async def prepare():
    hass=HomeAssistant('/config')
    registry=Store(hass,1,'lovelace_dashboards')
    data=await registry.async_load()
    data['items'].append({'id':'other_dashboard','url_path':'other-dashboard','mode':'storage',
        'title':'Keep my dashboard','show_in_sidebar':True,'require_admin':False})
    await registry.async_save(data)
    await Store(hass,1,'lovelace.other_dashboard').async_save({'config':{'views':[{'title':'Keep me','cards':[]}]}})

async def edit_and_check(expected_edit=False):
    hass=HomeAssistant('/config')
    registry=await Store(hass,1,'lovelace_dashboards').async_load()
    items=[item for item in registry['items'] if item['url_path']=='car-starter']
    assert len(items)==1
    dashboard=LovelaceStorage(hass,items[0]);assert dashboard.mode=='storage'
    config=await dashboard.async_load(False)
    if expected_edit:assert config['views'][0]['title']=='My editable car'
    else:
        assert len([card for card in config['views'][0]['cards'] if card['type']=='gauge'])==4
        config['views'][0]['title']='My editable car';await dashboard.async_save(config)
    assert any(item['url_path']=='other-dashboard' for item in registry['items'])

asyncio.run(prepare())
auth=Path('/config/.storage/auth');auth.write_bytes(b'private owner placeholder')
other=Path('/config/.storage/lovelace.other_dashboard');before=other.read_bytes()
asyncio.run(edit_and_check())
exec(STARTER_MIGRATION)
asyncio.run(edit_and_check(expected_edit=True))
assert auth.read_bytes()==b'private owner placeholder'
assert other.read_bytes()==before
print('PASS: native editable Car starter load/save; repeat migration preserves user edits, other dashboards and owner placeholder')
