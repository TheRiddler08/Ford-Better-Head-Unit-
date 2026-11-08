"""Every new feature card renders inside HA; owner edits use the existing APIs."""
import json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from deploy.home_assistant import classic_resource
from app.headunit_prefs import DEFAULT
from control.layouts import project_default
from playwright.sync_api import sync_playwright,expect

module,_=classic_resource(Path(__file__).resolve().parents[1])
features=['overview','network','power','storage','cameras','updates','radio','messages','sync','reports','preferences','home']
fixtures={
 'system':{'role':'tiny','observedAt':1000,'network':{'phase':'ready','fresh':True,'gateway':'duet','peers':{'duet':'02:11:22:33:44:55'},'peerStatus':{'duet':{'present':True,'health':{'storage':'waiting-for-moosefs'}}}},'gps':{'phase':'waiting-fix','gpsMode':1},'storage':{'worker':{'phase':'waiting-for-moosefs'},'poolMounted':False,'localFreeBytes':10000000000,'replicaGoal':2,'clientOnly':False,'catalog':[]},'radio':{'phase':'unconfigured','diagnostic':{'phase':'pending'},'homeKeyPinned':False,'privateKeyPresent':False},'software':{'phase':'connected'},'power':{'phase':'pending'},'reboot':{'phase':'pending-verified-park'},'future':{'usbCameraAI':'planned'},'assistant':{'ready':True}},
 'cameras':{'items':[{'id':'tiny:'+('a'*64)+'.mp4','name':'front/clip.mp4','channel':'front','size':1000}],'hasMore':False},
 'updates':{'revision':'fixture','updateTimer':'enabled','courierProgress':{'phase':'waiting'},'courier':{'phase':'applied'},'softwareUpdates':{'phase':'connected','updates':[]},'reboot':{'phase':'pending-verified-park'}},
 'messages':{'items':[]},'reports':{'gps':{'count':2,'first':1000,'last':1001}},
 'report':{'items':[{'id':1,'ts':1000,'name':'RPM','number':1200}],'columns':['id','ts','name','number'],'hasMore':False,'next':1},
 'preferences':DEFAULT,'sync':{'selected':['notes.txt'],'items':[{'name':'notes.txt','phase':'idle'}]},
 'sync-file':{'name':'notes.txt','sha256':'b'*64,'source':'local original','conflicts':[]},'radio-test':{'phase':'running'}}
home={'status':{'homeAssistant':{'phase':'mqtt-connected'}},'transfers':{'items':[{'path':'app/web/cockpit.js','id':'f'*64,'device':'car-1','revision':2,'phase':'receiving','displayPhase':'receiving','acknowledged':1,'total':3,'priority':'normal','decision':'ip-preferred'}],'nextCursor':None,'budget':{'reserved':2,'limit':60,'remaining':58,'nextEligibleAt':1000}},'layout':{'generation':0,'draft':project_default(),'history':[{'revision':1,'created':1000}]},'source':{'role':'tiny','path':'app/web/cockpit.js','base':'c'*64,'source':'const x=1;'},'sync':{'files':[],'revision':'d'*64},'config':{'revision':0}}
html='''<script type="module">
import '/module.js';
window.calls=[];window.signed=[];const fixtures=FIXTURES,home=HOME;
window.h={connected:true,user:{is_admin:true},states:{},callWS:async data=>{signed.push(data);return {path:'/signed.csv'};},fetchWithAuth:async(path,options={})=>{
 const feature=path.split('/').at(-1).split('?')[0];calls.push({path,headers:options.headers,body:options.body?JSON.parse(options.body):null});
 if(path.includes('/home/'))return Response.json(home[feature]||{});
 return Response.json(fixtures[feature]||{});
}};
window.cards={};for(const feature of FEATURES){const card=document.createElement('drive-console-service');card.setConfig({type:'custom:drive-console-service',feature});document.body.append(card);card.hass=h;cards[feature]=card;}
</script>'''.replace('FIXTURES',json.dumps(fixtures)).replace('HOME',json.dumps(home)).replace('FEATURES',json.dumps(features))

with sync_playwright() as p:
    browser=p.chromium.launch(executable_path='/usr/bin/chromium',args=['--no-sandbox']);page=browser.new_page();errors=[]
    page.on('pageerror',lambda error:errors.append(str(error)))
    page.route('http://127.0.0.1:19993/**',lambda route:route.fulfill(body=module if route.request.url.endswith('module.js') else html,content_type='text/javascript' if route.request.url.endswith('module.js') else 'text/html'))
    page.goto('http://127.0.0.1:19993/')
    def card(feature):return page.locator('drive-console-service').nth(features.index(feature))
    for feature in features:expect(card(feature).locator('main')).not_to_be_empty()
    expect(card('network')).to_contain_text('recent beacon');expect(card('radio')).to_contain_text('GPS reporting remains deferred')
    card('preferences').get_by_label('Vehicle nickname',exact=True).fill('Native HA car');card('preferences').get_by_role('button',name='Save local preferences',exact=True).click()
    page.wait_for_function("calls.some(c=>c.path.endsWith('/preferences')&&c.body?.vehicle?.nickname==='Native HA car')")
    card('sync').get_by_role('button',name='Open notes.txt',exact=True).click();card('sync').get_by_label('Local text',exact=True).fill('edited in HA')
    card('sync').get_by_role('button',name='Save selected-file edit',exact=True).click();page.wait_for_function("calls.some(c=>c.path.endsWith('/sync-file')&&c.body?.source==='edited in HA')")
    card('reports').get_by_role('button',name='Load records',exact=True).click();expect(card('reports')).to_contain_text('RPM')
    card('radio').get_by_role('button',name='Queue encrypted round-trip test',exact=True).click();page.wait_for_function("calls.some(c=>c.path.endsWith('/radio-test')&&c.body?.action==='restart')")
    owner=card('home');owner.get_by_label('Separate home owner panel token (memory only)',exact=True).fill('private-owner-panel-token-test')
    owner.get_by_label('Home function',exact=True).select_option('source');owner.get_by_role('button',name='Open home function',exact=True).click()
    owner.get_by_role('button',name='Load reported copy',exact=True).click();owner.get_by_label('Editable source copy',exact=True).fill('const x=2;')
    owner.get_by_role('button',name='Sign and queue change',exact=True).click();page.wait_for_function("calls.some(c=>c.path.endsWith('/home/publish')&&c.body?.source==='const x=2;')")
    assert page.evaluate("calls.find(c=>c.path.endsWith('/home/publish')).headers['X-Car-Home-Token']")=='private-owner-panel-token-test'
    assert not page.evaluate("JSON.stringify(localStorage).includes('private-owner')")
    assert not page.evaluate("JSON.stringify(cards.home.config).includes('private-owner')")
    owner.get_by_label('Home function',exact=True).select_option('layout');owner.get_by_role('button',name='Open home function',exact=True).click()
    owner.get_by_role('button',name='Publish saved layout',exact=True).click();page.wait_for_function("calls.some(c=>c.path.endsWith('/home/layout')&&c.body?.action==='publish'&&c.body?.project)")
    owner.get_by_role('button',name='Restore saved layout',exact=True).click();page.wait_for_function("calls.some(c=>c.path.endsWith('/home/layout')&&c.body?.action==='restore'&&c.body?.version===1)")
    owner.get_by_label('Home function',exact=True).select_option('transfers');owner.get_by_role('button',name='Open home function',exact=True).click()
    owner.get_by_role('button',name='High priority',exact=True).click();page.wait_for_function("calls.some(c=>c.path.endsWith('/home/transfer')&&c.body?.action==='priority'&&c.body?.value==='high'&&c.body?.revision===2)")
    owner.get_by_label('Transfer history',exact=True).select_option('finished');page.wait_for_function("calls.some(c=>c.path.includes('/home/transfers?role=tiny&state=finished'))")
    page.evaluate('cards.home.remove()');assert page.evaluate('cards.home._homeToken')==''
    assert not errors,errors;browser.close()
print('All native HA feature cards render; preferences, sync, reports, encrypted tests and private home-owner controls passed')
