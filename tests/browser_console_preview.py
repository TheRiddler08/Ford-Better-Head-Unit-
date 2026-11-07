"""Optional isolated Chromium check of raw-code sandbox and preview state delivery."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from deploy.home_assistant import classic_resource
from playwright.sync_api import sync_playwright

module,_=classic_resource(Path(__file__).resolve().parents[1])
page_html='''<script type="module">
import '/module.js';
const card=document.createElement('drive-console-widget');
card.setConfig({widget:'html',html:'<h1 id="value">Waiting</h1>',javascript:`
try { parent.document.body; document.body.dataset.escape='yes'; }
catch(e) { document.body.dataset.escape='blocked'; }
addEventListener('drive-data', e=>{ document.getElementById('value').textContent=e.detail.mode+':'+e.detail.states['sensor.car_speed'].state; });`});
document.body.append(card);window.card=card;
card.hass={connected:true,states:{'sensor.car_speed':{state:'12',attributes:{}}}};
</script>'''
with sync_playwright() as p:
    browser=p.chromium.launch(executable_path='/usr/bin/chromium',args=['--no-sandbox'])
    page=browser.new_page()
    page.route('http://127.0.0.1:19999/**',lambda route:route.fulfill(body=module if route.request.url.endswith('module.js') else page_html,content_type='text/javascript' if route.request.url.endswith('module.js') else 'text/html'))
    page.goto('http://127.0.0.1:19999/')
    frame=page.frame_locator('drive-console-widget iframe')
    frame.locator('#value').filter(has_text='live:12').wait_for()
    assert frame.locator('body').get_attribute('data-escape')=='blocked'
    page.evaluate("card.hass={connected:true,states:{'sensor.car_speed':{state:'35',attributes:{}}}}")
    frame.locator('#value').filter(has_text='live:35').wait_for()
    page.evaluate("card.hass={connected:true,states:{'sensor.car_speed':{state:'12',attributes:{}}}}")
    frame.locator('#value').filter(has_text='live:12').wait_for()
    browser.close()
print('HTML sandbox and normal state delivery passed')
