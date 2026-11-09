"""Serve installed assets to Chromium; verify design and a real UI-version rollout."""
import json,sys,shutil,tempfile,threading
from pathlib import Path
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from deploy.home_assistant import classic_dashboard,sync_assets
from playwright.sync_api import sync_playwright,expect
from app.system_status import snapshot

ROOT=Path(__file__).resolve().parents[1]

with tempfile.TemporaryDirectory() as directory:
    root=Path(directory);repo=root/'repo';repo.mkdir();config=root/'config';registry=root/'managed.json'
    shutil.copytree(ROOT/'home_assistant',repo/'home_assistant');shutil.copytree(ROOT/'app/web',repo/'app/web')
    sync_assets(repo,config,'UTC',registry)
    def metadata():return json.loads((config/'drive-console-resource.json').read_text())
    def register():
        path=config/'.storage/lovelace_resources';path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps({'data':{'items':[{'id':'drive_console_local','type':'module','url':metadata()['url']}]}}))
    register();first=metadata();(config/'www/drive-console.js').write_text('/* owner legacy module */')
    seen=[]
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            path=self.path.split('?')[0];seen.append(path)
            if path.startswith('/local/'):
                file=config/'www'/path.removeprefix('/local/')
                if not file.is_file():self.send_error(404);return
                raw=file.read_bytes();mime='text/javascript'
            elif path=='/api/car_console/ui':
                # Read the installed file and registry, as the HA view does.
                doc=metadata();file=config/'www'/doc['url'].removeprefix('/local/')
                import hashlib
                raw=json.dumps(dict(build=doc['build'],assetMatch=hashlib.sha256(file.read_bytes()).hexdigest()==doc['sha256'],resourceRegistered=True)).encode();mime='application/json'
            elif path=='/api/car_console/navigation':
                raw=json.dumps({'revision':0,'settings':{'lat':39.5,'lon':-98.35,'zoom':5,'followGPS':True,'usaOnly':True,'routingRegions':['us']},'markers':[],'trips':[],'route':{'waypoints':[],'geometry':[],'distance':None,'duration':None,'instructions':[]}}).encode();mime='application/json'
            elif path=='/api/car_console/service/system':
                data=snapshot(root,'tiny',root/'etc',root/'run',root/'state');raw=json.dumps(data).encode();mime='application/json'
            else:
                html='''<!doctype html><meta name="viewport" content="width=device-width"><style>body{margin:0;background:#11161b}hui-card{display:block;min-width:0}</style><script type="module">
import 'MODULE';
window.config=CONFIG;
window.h={connected:true,user:{id:'fixture-owner',is_admin:true},states:{},fetchWithAuth:(path,options)=>fetch(path,options)};
window.openView=(path)=>{
 window.dashboard?.remove();const view=config.views.find(view=>view.path===path);const layout=document.createElement('drive-console-layout');layout.setConfig(view);
 layout.hass=h;layout.lovelace={config,urlPath:'drive-console',editMode:false,setEditMode:()=>window.edited=true};
 layout.cards=view.cards.map(value=>{const wrap=document.createElement('hui-card');const card=document.createElement(value.type.startsWith('custom:')?value.type.replace('custom:',''):'div');if(card.setConfig){card.setConfig(value);card.hass=h;}else card.textContent=value.title||'';wrap.append(card);return wrap;});
 document.body.append(layout);window.dashboard=layout;
};openView('drive');
</script>'''.replace('MODULE',metadata()['url']).replace('CONFIG',json.dumps(classic_dashboard()))
                raw=html.encode();mime='text/html'
            self.send_response(200);self.send_header('Content-Type',mime);self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
        def log_message(self,*args):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        with sync_playwright() as p:
            browser=p.chromium.launch(executable_path='/usr/bin/chromium',args=['--no-sandbox']);page=browser.new_page(viewport={'width':1600,'height':950});errors=[]
            page.on('pageerror',lambda error:errors.append(str(error)))
            page.route('https://**/*',lambda route:route.abort())
            page.goto(f'http://127.0.0.1:{server.server_port}/drive-console/drive')
            layout=page.locator('drive-console-layout');expect(layout.locator('#uiVersion')).to_contain_text(first['build'])
            expect(layout.locator('#uiVersion')).to_have_attribute('title',__import__('re').compile('Asset verified: true'))
            expect(layout.locator('#pageTabs')).to_be_hidden();expect(layout.locator('.dock')).to_be_hidden()
            expect(layout.get_by_role('button',name='Edit dashboard',exact=True)).to_be_visible()
            assert page.locator('drive-console-widget[data-widget=clock]').evaluate("el=>getComputedStyle(el.shadowRoot.querySelector('.clock-scenery')).display")=='none'
            assert page.locator('drive-console-widget[data-widget=clock]').evaluate("el=>getComputedStyle(el.shadowRoot.querySelector('.clock-card')).backgroundImage")=='none'
            expect(page.locator('drive-console-widget[data-widget=clock]')).to_contain_text('Today')
            page.screenshot(path='/tmp/car-dashboard-desktop.png')
            page.set_viewport_size({'width':390,'height':844})
            assert layout.evaluate('el=>el.shadowRoot.querySelector("#shell").scrollWidth')<=390
            page.screenshot(path='/tmp/car-dashboard-mobile.png')
            page.set_viewport_size({'width':1440,'height':950});page.evaluate("openView('health')")
            expect(page.locator('drive-console-service')).to_contain_text('Needs attention')
            layout.locator('#quickTheme').click()
            expect(page.locator('drive-console-service')).to_have_attribute('data-theme','day')
            assert page.locator('drive-console-service').evaluate("el=>getComputedStyle(el.shadowRoot.querySelector('ha-card')).backgroundColor")=='rgb(255, 255, 255)'
            layout.locator('#quickTheme').click()
            page.screenshot(path='/tmp/car-dashboard-health.png')
            # A preserved old alias must not pin either the new file or registry.
            source=repo/'home_assistant/drive_console.js';source.write_text(source.read_text().replace("p.brand_text||'Car'","p.brand_text||'Car updated'"))
            changed,preserved=sync_assets(repo,config,'UTC',registry);register();second=metadata()
            assert first['build']!=second['build'];assert 'www/drive-console.js' in preserved
            page.evaluate('window.now=Date.now()+61000;Date.now=()=>now;dashboard._uiCheckedAt=0;dashboard.checkUI()')
            expect(layout.locator('#uiVersion')).to_contain_text('update ready');expect(layout.locator('#uiReload')).to_be_visible()
            page.evaluate('dashboard._lovelace.editMode=true');layout.locator('#uiReload').click()
            expect(layout.locator('#uiVersion')).to_contain_text('Save your changes')
            page.evaluate('dashboard._lovelace.editMode=false')
            # HA may load a changed resource before a full reload. Registration
            # must not throw or pretend that old component classes are current.
            page.add_script_tag(url=second['url'],type='module')
            expect(page.locator('#drive-console-update-notice')).to_be_visible()
            page.locator('#drive-console-update-notice').get_by_role('button',name='Reload dashboard').click()
            expect(page.locator('drive-console-layout').locator('#uiVersion')).to_contain_text(second['build'])
            expect(page.locator('drive-console-layout').locator('.small-brand')).to_have_text('Car updated')
            assert '/local/drive-console.js' not in seen;assert not errors,errors
            browser.close()
    finally:server.shutdown();server.server_close();thread.join()
print('Versioned asset rollout, preserved edits, loaded-build check, safe reload and desktop/mobile dashboard passed')
