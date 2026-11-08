"""Exercise the local messaging UI with a real local API and private test state."""
import json,tempfile,threading,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from http.server import ThreadingHTTPServer
from app import server
from playwright.sync_api import sync_playwright,expect
with tempfile.TemporaryDirectory() as directory:
 root=Path(directory);creds=root/'.config/car-dashboard';creds.mkdir(parents=True);(creds/'update-link.key').write_text(bytes(range(32)).hex())
 cfg=root/'fleet.json';cfg.write_text(json.dumps({'role':'tiny','home':str(root)}));server.FLEET_FILE=cfg
 http=ThreadingHTTPServer(('127.0.0.1',0),server.Handler);server.PORT=http.server_port
 thread=threading.Thread(target=http.serve_forever,daemon=True);thread.start()
 try:
  with sync_playwright() as p:
   browser=p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox']);page=browser.new_page();errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
   page.goto(f'http://127.0.0.1:{http.server_port}/radio-messages');page.locator('#messageText').fill('Hello <script>never execute</script>');page.get_by_role('button',name='Queue message').click()
   expect(page.locator('#messageList')).to_contain_text('Hello <script>never execute</script>');expect(page.locator('#messageList')).to_contain_text('queued')
   page.reload();expect(page.locator('#messageList')).to_contain_text('Hello <script>never execute</script>');assert not errors,errors
   page.locator('#messageText').fill('Draft survives reload');page.reload();expect(page.locator('#messageText')).to_have_value('Draft survives reload')
   page.locator('#messageSearch').fill('no matching text');expect(page.locator('#messageList')).to_contain_text('No messages match');page.locator('#messageSearch').fill('')
   page.locator('#messageText').fill('é'*121);expect(page.locator('#messageBytes')).to_have_text('242 / 240 bytes');expect(page.locator('#sendMessage')).to_be_disabled()
   page.locator('#messageText').fill('Draft survives reload');page.get_by_role('button',name='Cancel local retries').click();expect(page.locator('#messageList')).to_contain_text('cancelled')
   page.locator('#messageFilter').select_option('received');expect(page.locator('#messageList')).to_contain_text('No messages match');page.locator('#messageFilter').select_option('all')
   page.route('**/api/messages',lambda route:route.abort());page.locator('#refreshMessages').click();expect(page.locator('#messageConnection')).to_contain_text('Connection unavailable');expect(page.locator('#messageList')).to_contain_text('cancelled');expect(page.locator('#messageText')).to_have_value('Draft survives reload')
   page.set_viewport_size({'width':390,'height':844});assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
   page.screenshot(path='/tmp/car-radio-messages.png');browser.close();print('Browser messaging queue, persistence and safe text rendering passed')
 finally:http.shutdown();http.server_close();thread.join()
