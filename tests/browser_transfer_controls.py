"""Authenticated transfer inspector controls and distinguishes receipt/application."""
import sys,tempfile,threading
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from http.server import ThreadingHTTPServer
from control import home_server as panel
from control.software import Software
from control.update_transfer import digest,encode,APPLIED
from playwright.sync_api import sync_playwright,expect

with tempfile.TemporaryDirectory() as directory:
    root=Path(directory);panel.ROOT=root;panel.TOKEN='private-test-panel-token-'*3;panel.KEY_HEX=''
    http=ThreadingHTTPServer(('127.0.0.1',0),panel.Handler);thread=threading.Thread(target=http.serve_forever,daemon=True);thread.start()
    try:
        service=Software(root/'software');source='const speed = 1;'
        service.inventory('car-1','tiny',[{'path':'app/web/cockpit.js','source':source,'sha256':digest(source.encode())}])
        with sync_playwright() as p:
            browser=p.chromium.launch(executable_path='/usr/bin/chromium',args=['--no-sandbox']);page=browser.new_page();errors=[]
            page.on('pageerror',lambda error:errors.append(str(error)));base=f'http://127.0.0.1:{http.server_port}';page.goto(base)
            assert page.request.get(base+'/api/software/transfers').status==401
            page.locator('#adminToken').fill(panel.TOKEN);page.locator('#connect').click();expect(page.locator('#transferList')).to_contain_text('No matching')
            page.locator('#softwarePath').select_option('app/web/cockpit.js');page.locator('#loadSoftware').click();expect(page.locator('#softwareSource')).to_have_value(source)
            page.locator('#softwareSource').fill(source+'\n// newer');page.locator('#publishSoftware').click()
            box=page.locator('.transfer-card');expect(box).to_have_count(1)
            ident=box.get_attribute('data-transfer-id');expect(page.locator('#transferBudget')).to_contain_text('reservations used')
            box.get_by_role('button',name='Pause sending',exact=True).click();expect(box).to_contain_text('Sender is paused')
            assert service.outgoing('car-1')==[]
            box.get_by_role('button',name='Use IP only',exact=True).click();expect(box.get_by_role('button',name='Use automatic fallback')).to_be_visible()
            box.get_by_role('button',name='Resume sending',exact=True).click();expect(box).to_contain_text('Waiting for IP delivery')
            box.get_by_role('button',name='High priority',exact=True).click();expect(box).to_contain_text('Priority: high')
            box.get_by_role('button',name='Retry missing fragments',exact=True).click();expect(page.locator('#transferNotice')).to_contain_text('Retry scheduled')
            with service.journal.db() as db:db.execute('INSERT INTO receipts SELECT id,kind,idx FROM parts WHERE id=?',(ident,))
            page.locator('#transferRefresh').click();expect(box).to_contain_text('awaiting-application');expect(box).to_contain_text('100%')
            box.locator('summary').click();expect(box).to_contain_text('Radio frames for one pass')
            service.receipts('car-1','tiny',[encode(APPLIED,ident,0,1,b'applied',service.key()).hex()])
            page.locator('#transferFilter').select_option('finished');expect(box).to_contain_text('Transfer finished')
            expect(box.get_by_role('button')).to_have_count(0);assert not errors,errors;browser.close()
            print('Owner-only inspector, pause/resume, IP-only policy, priority, retry and applied-receipt state passed')
    finally:http.shutdown();http.server_close();thread.join()
