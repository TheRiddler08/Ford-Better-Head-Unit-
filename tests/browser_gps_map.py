"""Isolated browser regression for coherent GPS status and map tile transports."""
from pathlib import Path
import sys,base64
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from deploy.home_assistant import classic_resource
from playwright.sync_api import sync_playwright,expect
module,_=classic_resource(Path(__file__).resolve().parents[1])
html='''<script type="module">
import '/module.js';
window.h={connected:true,states:{'device_tracker.car_location':{state:'not_home',attributes:{latitude:38.36,longitude:-81.62}},'binary_sensor.car_gps_fix':{state:'off'}},fetchWithAuth:async()=>new Response('',{status:404})};
for(const kind of ['map','status']){const card=document.createElement('drive-console-widget');card.style.cssText='width:600px;height:500px';card.setConfig({widget:kind});document.body.append(card);card.hass=h;window[kind+'Card']=card;}
</script>'''
png=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=')
with sync_playwright() as p:
    browser=p.chromium.launch(executable_path='/usr/bin/chromium',args=['--no-sandbox'])
    page=browser.new_page()
    page.route('http://127.0.0.1:19998/**',lambda r:r.fulfill(body=module if r.request.url.endswith('module.js') else html,content_type='text/javascript' if r.request.url.endswith('module.js') else 'text/html',headers={'Referrer-Policy':'no-referrer'}))
    calls=[]
    blocked=[False]
    def tile_request(route):
        calls.append(route.request.headers)
        route.fulfill(body=png,status=403 if blocked[0] else 200,content_type='image/png',headers={'Access-Control-Allow-Origin':'*'})
    page.route('https://basemap.nationalmap.gov/**',tile_request)
    page.goto('http://127.0.0.1:19998/')
    expect(page.locator('#connectionDetail')).to_contain_text('GPS: connected')
    expect(page.locator('#mapModeText')).to_contain_text('Online USA map · GPS connected')
    expect(page.locator('#mapAttribution')).to_be_visible()
    assert calls and all(c.get('referer')=='http://127.0.0.1:19998/' for c in calls)
    assert all('authorization' not in c for c in calls)
    page.evaluate("mapCard.setConfig({widget:'map',online_fallback:false})")
    expect(page.locator('#mapBadge')).to_have_text('GPS · NO MAP')
    expect(page.locator('#mapEmpty')).to_be_hidden()
    page.evaluate("h.states['device_tracker.car_location'].state='unavailable';mapCard.hass=h;statusCard.hass=h")
    expect(page.locator('#connectionDetail')).to_contain_text('GPS: disconnected')
    expect(page.locator('#mapBadge')).to_have_text('GPS DISCONNECTED')
    page.evaluate("h.states['binary_sensor.car_gps_fix'].attributes={receiver_connected:true,receiver_state:'waiting-fix'};mapCard.hass=h;statusCard.hass=h")
    expect(page.locator('#connectionDetail')).to_contain_text('GPS: waiting for fix')
    expect(page.locator('#mapBadge')).to_have_text('WAITING FOR FIX')
    blocked[0]=True
    page.reload()
    expect(page.locator('#mapModeText')).to_contain_text('map provider refused access')
    expect(page.locator('#mapEmpty')).to_be_hidden()
    assert page.locator('.leaflet-tile-loaded img:visible').count()==0
    count=len(calls)
    page.evaluate("h.states['device_tracker.car_location'].attributes.latitude=38.37;mapCard.hass=h")
    page.wait_for_timeout(300)
    assert len(calls)==count
    browser.close()
print('GPS status, online fallback, offline opt-out and no-fix rendering passed')
