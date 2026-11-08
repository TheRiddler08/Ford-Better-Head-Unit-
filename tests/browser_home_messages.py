"""Authenticated home-panel messaging uses the same UI as the car console."""
import sys,tempfile,threading
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from http.server import ThreadingHTTPServer
from control import home_server
from playwright.sync_api import sync_playwright,expect
with tempfile.TemporaryDirectory() as directory:
 home_server.ROOT=Path(directory);home_server.TOKEN='private-test-panel-token-'*3;home_server.KEY_HEX=bytes(range(32)).hex()
 http=ThreadingHTTPServer(('127.0.0.1',0),home_server.Handler)
 thread=threading.Thread(target=http.serve_forever,daemon=True);thread.start()
 try:
  with sync_playwright() as p:
   browser=p.chromium.launch(executable_path='/usr/bin/chromium',args=['--no-sandbox']);page=browser.new_page();errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
   base=f'http://127.0.0.1:{http.server_port}';page.goto(base)
   assert page.request.get(base+'/api/messages').status==401
   page.locator('#adminToken').fill(home_server.TOKEN);page.locator('#connect').click();expect(page.locator('#editor')).to_be_visible()
   page.locator('#messageDestination').select_option('duet');page.locator('#messageText').fill('Home to Duet');page.locator('#sendMessage').click();expect(page.locator('#messageList')).to_contain_text('Home to Duet');expect(page.locator('#messageList')).to_contain_text('home → duet')
   page.get_by_role('button',name='Cancel local retries').click();expect(page.locator('#messageList')).to_contain_text('cancelled')
   page.locator('#messageText').fill('Home draft');page.reload();expect(page.locator('#messageText')).to_have_value('Home draft')
   assert home_server.TOKEN not in page.evaluate('JSON.stringify(localStorage)')
   assert not errors,errors
   browser.close();print('Home panel authentication, shared messaging UI, cancellation and private draft storage passed')
 finally:http.shutdown();http.server_close();thread.join()
