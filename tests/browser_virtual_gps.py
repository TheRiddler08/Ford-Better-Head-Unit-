"""Optional Chromium check of external laptop-location capture."""
from pathlib import Path
import sys,time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from recorder.virtual_gps import BrowserSource
from playwright.sync_api import sync_playwright, expect

with BrowserSource(0) as source, sync_playwright() as p:
    browser=p.chromium.launch(executable_path='/usr/bin/chromium',args=['--no-sandbox'])
    context=browser.new_context(geolocation={'latitude':40.125,'longitude':-76.25,'accuracy':20},permissions=['geolocation'])
    page=context.new_page()
    page.goto(f'http://127.0.0.1:{source.server.server_port}/#token={source.token}')
    page.get_by_role('button',name="Share this laptop's location").click()
    expect(page.locator('#status')).to_contain_text('Sharing:',timeout=15000)
    assert source.fix['latitude']==40.125
    assert b'4007.50000,N,07615.00000,W' in source.data()
    context.set_geolocation({'latitude':40.25,'longitude':-76.5,'accuracy':10})
    expect(page.locator('#status')).to_contain_text('40.25',timeout=15000)
    assert source.fix['latitude']==40.25
    page.evaluate("latest.timestamp=Date.now()-35000;send()")
    expect(page.locator('#status')).to_contain_text('Location paused')
    page.evaluate("window.refreshCalls=0;const originalGet=navigator.geolocation.getCurrentPosition.bind(navigator.geolocation);navigator.geolocation.getCurrentPosition=(...args)=>{window.refreshCalls++;return originalGet(...args);};window.dispatchEvent(new Event('focus'));")
    assert page.evaluate('window.refreshCalls')>=1
    context.set_geolocation({'latitude':40.26,'longitude':-76.5,'accuracy':10})
    expect(page.locator('#status')).to_contain_text('Sharing:',timeout=15000)
    code=page.evaluate("async()=>{const r=await fetch('/fix',{method:'POST',headers:{Authorization:'Bearer '+token,'Content-Type':'application/json'},body:JSON.stringify({latitude:40,longitude:-76,accuracy:20,timestamp:0})});return r.status;}")
    assert code==202
    page.get_by_role('button',name='Stop sharing',exact=True).click()
    page.wait_for_timeout(250)
    assert source.fix is None
    assert b'GPRMC,,V' in source.data()
    browser.close()
print('Browser geolocation → external NMEA source and stop passed')
