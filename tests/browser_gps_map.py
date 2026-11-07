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
    page.route('http://127.0.0.1:19998/**',lambda r:r.fulfill(body=module if r.request.url.endswith('module.js') else html,content_type='text/javascript' if r.request.url.endswith('module.js') else 'text/html'))
    page.route('https://tile.openstreetmap.org/**',lambda r:r.fulfill(body=png,content_type='image/png'))
    page.goto('http://127.0.0.1:19998/')
    expect(page.locator('#connectionDetail')).to_contain_text('GPS: fix available')
    expect(page.locator('#mapModeText')).to_contain_text('Online map · GPS connected')
    expect(page.locator('#mapAttribution')).to_be_visible()
    page.evaluate("mapCard.setConfig({widget:'map',online_fallback:false})")
    expect(page.locator('#mapBadge')).to_have_text('GPS · NO MAP')
    expect(page.locator('#mapEmpty')).to_contain_text('GPS connected')
    page.evaluate("h.states['device_tracker.car_location'].state='unavailable';mapCard.hass=h;statusCard.hass=h")
    expect(page.locator('#connectionDetail')).to_contain_text('GPS: unavailable')
    expect(page.locator('#mapBadge')).to_have_text('NO GPS')
    browser.close()
print('GPS status, online fallback, offline opt-out and no-fix rendering passed')
