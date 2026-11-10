"""Real Leaflet overlay controls, offline toggles, safe popups and saved options."""
import json,base64,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from deploy.home_assistant import classic_resource
from playwright.sync_api import sync_playwright,expect

ROOT=Path(__file__).resolve().parents[1];module,_=classic_resource(ROOT)
html='''<script type="module">
import {defaultNavigation} from '/module.js';
window.saved=JSON.parse(localStorage.getItem('nav')||'null')||defaultNavigation();
saved.settings.zoom=14;
window.failLayers=false;window.requests=[];
window.layerData={cameras:[{id:'node:123',lat:39.5,lon:-98.35,label:'<img src=x onerror=alert(1)>',brand:'Flock Safety',operator:'Test operator',direction:270,osm_type:'node',osm_id:'123'}],
 receptions:[{id:1,lat:39.501,lon:-98.35,gateway:'ac1f09fffe217e15',rssi:-82,snr:8,received:1794070000}],
 gateways:[{id:'ac1f09fffe217e15',label:'Test fixed gateway',role:'fixed',lat:39.502,lon:-98.35,radius:4,hidden:0}],
 cameraStatus:{phase:'cached',count:1,fetchedAt:1794070000,sourceDate:'2026-10-08'},coverageStatus:{phase:'cached',count:1,syncedAt:1794070000},nearest:[{label:'Flock Safety',meters:20}],worker:{}};
window.h={connected:true,user:{id:'owner',is_admin:true},states:{'device_tracker.car_location':{state:'unavailable',attributes:{}}},fetchWithAuth:async(path,options={})=>{
 requests.push(path);
 if(path==='/api/car_console/navigation'){if(options.method==='POST'){const data=JSON.parse(options.body);if(data.revision!==saved.revision+1)return Response.json({error:'Conflict'},{status:409});saved=data;localStorage.setItem('nav',JSON.stringify(data));}return Response.json(saved);}
 if(path.startsWith('/api/car_console/service/map-layers')){
  if(options.method==='POST'){window.lastAction=JSON.parse(options.body);return Response.json({phase:'saved'});}
  if(failLayers)return Response.json({error:'Offline cache unavailable'},{status:503});return Response.json(layerData);}
 return new Response('',{status:404});}};
const card=document.createElement('drive-console-widget');card.style.cssText='display:block;width:900px;height:800px';card.setConfig({widget:'map'});document.body.append(card);card.hass=h;window.card=card;
</script>'''
png=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=')
with sync_playwright() as p:
    browser=p.chromium.launch(executable_path='/usr/bin/chromium',args=['--no-sandbox'])
    page=browser.new_page(viewport={'width':1100,'height':1000},has_touch=True)
    errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
    page.route('http://127.0.0.1:19996/**',lambda route:route.fulfill(body=module if route.request.url.endswith('module.js') else html,content_type='text/javascript' if route.request.url.endswith('module.js') else 'text/html'))
    page.route('https://basemap.nationalmap.gov/**',lambda route:route.fulfill(body=png,content_type='image/png',headers={'Access-Control-Allow-Origin':'*'}))
    page.goto('http://127.0.0.1:19996/')
    print('Loaded overlay fixture',flush=True)
    page.locator('.map-layers > summary').click()
    expect(page.locator('#layerStatus')).to_contain_text('Camera cache: 1')
    expect(page.locator('#layerStatus')).to_contain_text('source snapshot 2026-10-08')
    page.locator('#layerCameras').check();page.locator('#layerCoverage').check();page.locator('#layerEstimates').check()
    page.wait_for_function('saved.settings.layers?.cameras && saved.settings.layers?.coverage && saved.settings.layers?.estimates')
    page.wait_for_function('card._mapLayerGroups?.cameras.getLayers().length===1 && card._mapLayerGroups.coverage.getLayers().length===2 && card._mapLayerGroups.estimates.getLayers().length===1')
    assert page.evaluate('card._mapLayerGroups.estimates.getLayers()[0].options.dashArray')=='7 7'
    # Data labels stay plain text; source URLs are fixed OpenStreetMap links.
    page.evaluate('card._mapLayerGroups.cameras.getLayers()[0].openPopup()')
    expect(page.locator('.map-layer-popup')).to_contain_text('<img src=x onerror=alert(1)>')
    assert page.locator('.map-layer-popup img').count()==0
    expect(page.locator('.map-layer-popup a')).to_have_attribute('href','https://www.openstreetmap.org/node/123')
    page.locator('.map-layers > summary').click()
    page.get_by_text('Use as route waypoint',exact=True).click()
    page.wait_for_function('saved.route.waypoints.length===1')
    page.evaluate('card._leaflet.closePopup()')
    page.locator('.map-layers > summary').click()
    # Opacity and manufacturer selection persist through a desktop reload.
    page.locator('#layerOpacity').fill('0.4');page.locator('#layerOpacity').dispatch_event('change')
    page.locator('#layerManufacturer').select_option('all');page.locator('#layerAlerts').check()
    expect(page.locator('#layerAlert')).to_contain_text('current GPS fix')
    page.wait_for_function('saved.settings.layers.opacity===0.4 && saved.settings.layers.manufacturer==="all"')
    print('Camera, measured reception and estimate controls passed; checking reload',flush=True)
    page.reload();page.locator('.map-layers > summary').click()
    expect(page.locator('#layerCameras')).to_be_checked();expect(page.locator('#layerCoverage')).to_be_checked()
    expect(page.locator('#layerManufacturer')).to_have_value('all');expect(page.locator('#layerOpacity')).to_have_value('0.4')
    # A normal GPS fix (including virtual serial input) enables local alerts.
    page.evaluate("h.states['device_tracker.car_location']={state:'not_home',attributes:{latitude:39.5,longitude:-98.35}};card.hass=h")
    expect(page.locator('#layerAlert')).to_contain_text('Reported camera nearby')
    assert page.evaluate('requests.filter(p=>p.includes("alertLat")).every(p=>p.startsWith("/api/car_console/service/map-layers"))')
    # Disabling still works if the local API becomes unavailable.
    page.evaluate('failLayers=true');page.locator('#layerCameras').uncheck()
    page.wait_for_function('card._mapLayerGroups.cameras.getLayers().length===0')
    expect(page.locator('#layerStatus')).to_contain_text('last displayed data retained')
    assert page.evaluate('card._mapLayerGroups.coverage.getLayers().length')==2
    # Re-enabling can immediately use the last displayed camera data offline.
    page.locator('#layerCameras').check()
    page.wait_for_function('card._mapLayerGroups.cameras.getLayers().length===1')
    summary=page.locator('.map-layers > summary').bounding_box()
    page.touchscreen.tap(summary['x']+20,summary['y']+8)
    expect(page.locator('.map-layers')).not_to_have_attribute('open','')
    closed=page.locator('.map-layers > summary').bounding_box()
    assert abs((closed['x']+closed['width'])-(summary['x']+summary['width']))<3
    summary=closed
    page.touchscreen.tap(summary['x']+20,summary['y']+8)
    expect(page.locator('.map-layers')).to_have_attribute('open','')
    page.screenshot(path='/tmp/car-map-layers.png')
    assert not errors,errors
    browser.close()
print('Map layer persistence, offline toggles, range distinctions, safe popups, route waypoints and local GPS alerts passed')
