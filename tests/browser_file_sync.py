"""Use the authenticated home panel to choose files, resolve and edit them."""
import sys,tempfile,threading
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from http.server import ThreadingHTTPServer
from control import home_server as panel
from control.file_sync import Files,home_files
from control.event_transfer import Events
from playwright.sync_api import sync_playwright,expect

KEY=bytes(range(32))
with tempfile.TemporaryDirectory() as directory:
    root=Path(directory);panel.ROOT=root/'server';panel.ROOT.mkdir();panel.TOKEN='private-panel-test-token-'*3;panel.KEY_HEX=KEY.hex()
    http=ThreadingHTTPServer(('127.0.0.1',0),panel.Handler);thread=threading.Thread(target=http.serve_forever,daemon=True);thread.start()
    try:
        with sync_playwright() as p:
            browser=p.chromium.launch(executable_path='/usr/bin/chromium',args=['--no-sandbox']);page=browser.new_page();errors=[]
            page.on('pageerror',lambda error:errors.append(str(error)));base=f'http://127.0.0.1:{http.server_port}';page.goto(base)
            assert page.request.get(base+'/api/sync?role=tiny').status==401
            page.locator('#adminToken').fill(panel.TOKEN);page.locator('#connect').click();expect(page.locator('#syncSelection')).to_have_value('')
            page.locator('#syncSelection').fill('notes.txt');page.locator('#syncSaveSelection').click()
            expect(page.locator('#syncFiles')).to_contain_text('notes.txt');page.get_by_role('button',name='Open notes.txt',exact=True).click()
            page.locator('#syncSource').fill('common\n');page.locator('#syncSaveFile').click();expect(page.locator('#syncNotice')).to_contain_text('Waiting for remote application')
            home=home_files(panel.ROOT,'tiny',KEY);car=Files(root/'car',root/'car.db',Events(root/'car-events.db',KEY,destination='tiny',types=('file','file-receipt')),'tiny','home',['notes.txt'])
            car.root.mkdir();item=home.pending()[0];reply=car.receive(item['doc'],item['id'],via_ip=True);home.receive(reply['doc'],reply['id'])
            (car.root/'notes.txt').write_text('car copy <img src=x onerror="window.bad=true">\n');(home.root/'notes.txt').write_text('home copy\n')
            car.scan();home.scan();item=car.pending()[0];reply=home.receive(item['doc'],item['id'],via_ip=True);car.receive(reply['doc'],reply['id'])
            page.get_by_role('button',name='Open notes.txt',exact=True).click();expect(page.locator('#syncConflict')).to_be_visible()
            expect(page.locator('#syncIncoming')).to_have_value('car copy <img src=x onerror="window.bad=true">\n')
            assert page.evaluate('window.bad') is None
            page.locator('#syncSource').fill('merged from browser 🚗\n');page.locator('#syncMerge').click()
            expect(page.locator('#syncNotice')).to_contain_text('Return transfer queued')
            for _ in range(5):
                for sender,receiver in ((car,home),(home,car)):
                    sender.scan()
                    for item in sender.pending():
                        reply=receiver.receive(item['doc'],item['id'],via_ip=True)
                        if reply:sender.receive(reply['doc'],reply['id'],via_ip=True)
                    for item in sender.receipts():receiver.receive(item['doc'],item['id'],via_ip=True)
            assert car.read('notes.txt')==home.read('notes.txt')=='merged from browser 🚗\n'
            page.get_by_role('button',name='Open notes.txt',exact=True).click()
            page.locator('#syncSource').fill('stale browser edit');(home.root/'notes.txt').write_text('newer external edit\n')
            page.locator('#syncSaveFile').click();expect(page.locator('#syncNotice')).to_contain_text('File changed since it was loaded')
            assert home.read('notes.txt')=='newer external edit\n'
            page.locator('#syncRole').select_option('duet');expect(page.locator('#syncRole')).to_have_value('tiny')
            expect(page.locator('#syncSource')).to_have_value('stale browser edit');page.locator('#syncDiscard').click()
            page.locator('#syncRole').select_option('duet');expect(page.locator('#syncFiles')).to_contain_text('No home data files selected')
            assert 'notes.txt' not in page.evaluate('JSON.stringify(localStorage)')
            assert not errors,errors;browser.close()
            print('Selected-file editor, conflict preview, merged delivery, stale-tab protection and role isolation passed')
    finally:http.shutdown();http.server_close();thread.join()
