"""Optional browser integration: python3 tests/browser_ui_builder.py (Playwright + Chromium)."""
import json, os, sqlite3, sys, tempfile, threading, time
from pathlib import Path
from http.server import ThreadingHTTPServer
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from control import home_server as home
from control import layouts
from control.tailscale_agent import sync_once
from app import server as car
from playwright.sync_api import sync_playwright, expect

def serve(handler):
    server=ThreadingHTTPServer(('127.0.0.1',0),handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    return server,thread

with tempfile.TemporaryDirectory() as temp:
    root=Path(temp)
    home.ROOT=root/'home';home.TOKEN='browser-test-admin-'+'a'*32;home.DEVICE_TOKEN='browser-test-device-'+'b'*32;home.DEVICE_NAME='car-1';home.KEY_HEX=''
    car.LAYOUT_FILE=root/'car/layout.json';car.CONFIG_FILE=root/'car/config.json';car.DB_PATH=root/'sample.sqlite3'
    with sqlite3.connect(car.DB_PATH) as db:
        db.execute('CREATE TABLE readings(ts INTEGER, name TEXT, value TEXT, number REAL, unit TEXT)')
        db.execute('INSERT INTO readings VALUES(?,?,?,?,?)',(int(time.time()),'RPM','1350',1350,'rpm'))
    hs,ht=serve(home.Handler);cs,ct=serve(car.Handler)
    hb='http://127.0.0.1:'+str(hs.server_port);cb='http://127.0.0.1:'+str(cs.server_port)
    try:
        with sync_playwright() as p:
            browser=p.chromium.launch(executable_path=os.getenv('CHROMIUM_PATH','/usr/bin/chromium'),headless=True,args=['--no-sandbox'])
            page=browser.new_page(viewport={'width':1700,'height':1100})
            errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
            page.goto(hb);page.locator('#adminToken').fill(home.TOKEN);page.locator('#connect').click()
            expect(page.locator('#builderStatus')).to_contain_text('Draft loaded')
            page.locator('#loadStarter').click()
            expect(page.frame_locator('#builderPreview').locator('[data-sensor="SPEED"]')).to_contain_text('42')
            page.locator('#undoLayout').click()
            page.locator('[data-add="heading"]').click()
            page.get_by_label('Text / label',exact=True).fill('MY CUSTOM COCKPIT')
            page.get_by_label('Text / label',exact=True).dispatch_event('change')
            page.locator('[data-add="sensor"]').click()
            page.wait_for_timeout(800)
            frame=page.frame_locator('#builderPreview')
            expect(frame.locator('[data-sensor="SPEED"]')).to_contain_text('42')
            # Move an actual component with the pointer.
            hit=page.locator('.builder-hit.selected');bounds=hit.bounding_box()
            page.mouse.move(bounds['x']+40,bounds['y']+40);page.mouse.down();page.mouse.move(bounds['x']+100,bounds['y']+80,steps=6);page.mouse.up()
            # Resize with the actual corner handle, not just a property edit.
            handle=page.locator('.builder-hit.selected .builder-resize');bounds=handle.bounding_box()
            old_width=int(page.locator('#builderInspector input[aria-label="Width"]').input_value())
            page.mouse.move(bounds['x']+8,bounds['y']+8);page.mouse.down();page.mouse.move(bounds['x']+48,bounds['y']+32,steps=4);page.mouse.up()
            assert int(page.locator('#builderInspector input[aria-label="Width"]').input_value())>old_width
            page.once('dialog',lambda dialog:dialog.accept('Details'));page.locator('#addPage').click()
            expect(page.locator('#builderPages option')).to_have_count(2)
            page.locator('[data-add="text"]').click()
            page.locator('#builderPages').select_option('0')
            # Export and import retain a real project; validation happens on home.
            with page.expect_download() as downloaded:
                page.locator('#exportLayout').click()
            export_path=root/'export.json';downloaded.value.save_as(export_path)
            page.once('dialog',lambda dialog:dialog.accept())
            page.locator('#importLayout').set_input_files(export_path)
            expect(page.locator('#builderPages option')).to_have_count(2)
            page.locator('#saveDraft').click();expect(page.locator('#builderStatus')).to_have_text('Draft saved on home server.')
            page.once('dialog',lambda dialog:dialog.accept());page.locator('#publishLayout').click()
            expect(page.locator('#builderStatus')).to_contain_text('queued for car')
            sync_once(hb,home.DEVICE_TOKEN,car.CONFIG_FILE,car.LAYOUT_FILE)
            display=browser.new_page();display.goto(cb)
            expect(display.locator('#layoutName')).to_have_text('My cockpit')
            expect(display.frame_locator('#customFrame').get_by_text('MY CUSTOM COCKPIT')).to_be_visible()
            # Full source mode supports JS while denying parent DOM/API access.
            page.locator('#builderMode').select_option('code')
            page.locator('#sourcehtml').fill('<h1 id="custom">HTML editor</h1><strong data-sensor="RPM">—</strong>')
            page.locator('#sourcecss').fill('h1 { color: rgb(12, 240, 180); }')
            page.locator('#sourcejs').fill("document.addEventListener('DOMContentLoaded',()=>{document.getElementById('custom').textContent='JavaScript works';try{parent.document.body;document.body.dataset.parent='allowed';}catch(e){document.body.dataset.parent='blocked';}fetch('/api/config').catch(()=>{document.body.dataset.network='blocked';});});")
            page.locator('#runPreview').click()
            expect(page.frame_locator('#builderPreview').locator('#custom')).to_have_text('JavaScript works')
            expect(page.frame_locator('#builderPreview').locator('body')).to_have_attribute('data-parent','blocked')
            expect(page.frame_locator('#builderPreview').locator('body')).to_have_attribute('data-network','blocked')
            page.once('dialog',lambda dialog:dialog.accept());page.locator('#publishLayout').click()
            expect(page.locator('#builderStatus')).to_contain_text('v2 queued')
            sync_once(hb,home.DEVICE_TOKEN,car.CONFIG_FILE,car.LAYOUT_FILE)
            display.reload()
            expect(display.frame_locator('#customFrame').locator('#custom')).to_have_text('JavaScript works')
            expect(display.frame_locator('#customFrame').locator('[data-sensor="RPM"]')).to_contain_text('1350')
            expect(display.frame_locator('#customFrame').locator('body')).to_have_attribute('data-parent','blocked')
            display.get_by_role('link',name='Standard dashboard / Recovery').click()
            expect(display.locator('#shell')).to_be_visible()
            page.locator('#disableLayout').click();expect(page.locator('#builderStatus')).to_contain_text('Standard dashboard queued')
            sync_once(hb,home.DEVICE_TOKEN,car.CONFIG_FILE,car.LAYOUT_FILE)
            display.goto(cb);expect(display.locator('#shell')).to_be_visible()
            page.locator('#layoutVersions').select_option('1');page.locator('#restoreLayout').click()
            expect(page.locator('#builderStatus')).to_contain_text('v4 queued')
            sync_once(hb,home.DEVICE_TOKEN,car.CONFIG_FILE,car.LAYOUT_FILE)
            # An already-open standard root page detects publication automatically.
            display.evaluate('watchCustomLayout()')
            expect(display.locator('#customFrame')).to_be_visible()
            expect(display.frame_locator('#customFrame').get_by_text('MY CUSTOM COCKPIT')).to_be_visible()
            page.wait_for_timeout(500)
            assert page.locator('#builderPreview').get_attribute('sandbox')=='allow-scripts'
            page.set_viewport_size({'width':390,'height':844})
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), 'Mobile page overflows'
            assert not errors,errors
            print('PASS: visual editor, dragging, source editing, JS sandbox, publish, Tailscale delivery, telemetry, recovery, mobile layout')
            browser.close()
    finally:
        for server,thread in [(hs,ht),(cs,ct)]:server.shutdown();server.server_close();thread.join()
