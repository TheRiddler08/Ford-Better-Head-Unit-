"""HA reconnects, request races, editable schedules and revision-aware home tools."""
import json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.headunit_prefs import DEFAULT,validate
from deploy.home_assistant import classic_resource
from playwright.sync_api import sync_playwright,expect

module,_=classic_resource(Path(__file__).resolve().parents[1])
html='''<script type="module">
import '/module.js';
window.clock=Date.now();Date.now=()=>clock;
window.calls=[];window.signed=[];window.prefs=PREFS;window.configRevision=0;window.selection={files:[],revision:'selection-0'};
window.failure=null;window.delayed=null;window.release=null;window.hang=false;
window.h={auth:{},connected:true,user:{id:'owner',is_admin:true},states:{},callWS:async data=>{signed.push(data);return {path:'/signed.csv'};},fetchWithAuth:async(path,options={})=>{
 const feature=path.split('/').at(-1).split('?')[0],body=options.body?JSON.parse(options.body):null;
 calls.push({path,body});
 if(hang)return new Promise(()=>{}); // A stalled HA authentication wrapper may not reach fetch.
 if(failure===feature){failure=null;return Response.json({error:'Temporary backend outage'},{status:503});}
 let value={};
 if(path.includes('/home/')){
   if(feature==='config')value={revision:configRevision,vehicle:{nickname:'Car'}};
   if(feature==='change'){if(body.baseRevision!==configRevision)return Response.json({error:'Stale revision'},{status:409});value={revision:++configRevision};}
   if(feature==='sync'){
     if(body){if(body.revision!==selection.revision)return Response.json({error:'Stale selection'},{status:409});selection={files:body.files,revision:'selection-'+calls.length};}
     value=selection;
   }
   if(feature==='status')value={phase:'connected'};
 }else{
   if(feature==='preferences'){
     if(body){if(body.revision!==prefs.revision+1)return Response.json({error:'Stale preferences'},{status:409});prefs=body;}
     value=prefs;
   }
   if(feature==='updates')value={revision:'new-version',softwareUpdates:{updates:[]}};
   if(feature==='reports')value={tables:{values:{count:2}}};
   if(feature==='report')value={items:[{id:123,name:'RPM',number:1200}],next:123,hasMore:true,columns:['id','name','number']};
   if(feature==='system')value={role:'tiny',network:{phase:'ready',fresh:true,peers:{}},power:{phase:'pending'},reboot:{phase:'pending'}};
 }
 value=structuredClone(value);
 if(delayed===feature){delayed=null;await new Promise(resolve=>window.release=resolve);}
 return Response.json(value);
}};
window.make=(feature,seconds=15)=>{const card=document.createElement('drive-console-service');card.setConfig({type:'custom:drive-console-service',feature,refresh_seconds:seconds});document.body.append(card);card.hass=h;return card;};
window.card=make('preferences');
</script>'''.replace('PREFS',json.dumps(DEFAULT))

with sync_playwright() as p:
    browser=p.chromium.launch(executable_path='/usr/bin/chromium',args=['--no-sandbox']);page=browser.new_page();errors=[]
    page.on('pageerror',lambda error:errors.append(str(error)))
    page.route('http://127.0.0.1:19994/**',lambda route:route.fulfill(body=module if route.request.url.endswith('module.js') else html,content_type='text/javascript' if route.request.url.endswith('module.js') else 'text/html'))
    page.goto('http://127.0.0.1:19994/');card=page.locator('drive-console-service').first
    expect(card.get_by_label('Vehicle nickname',exact=True)).to_be_visible()
    card.get_by_role('button',name='Add saved place',exact=True).click()
    card.get_by_label('Place 1 name',exact=True).fill('Test destination')
    card.get_by_label('Place 1 latitude',exact=True).fill('40')
    card.get_by_label('Place 1 longitude',exact=True).fill('-100')
    card.get_by_label('Place 1 usual travel minutes',exact=True).fill('30')
    card.get_by_role('button',name='Add scheduled departure',exact=True).click()
    card.get_by_label('Departure 1 name',exact=True).fill('Test departure')
    card.get_by_label('Departure 1 local time',exact=True).fill('08:15')
    card.get_by_label('Departure 1 Saturday',exact=True).check()
    card.get_by_role('button',name='Remove place 1',exact=True).click()
    expect(card).to_contain_text('Change or remove departures')
    card.get_by_role('button',name='Save local preferences',exact=True).click()
    page.wait_for_function('prefs.revision===1&&!card._busy')
    saved=page.evaluate('prefs');validate(saved);assert saved['routines'][0]['time']=='08:15';assert 5 in saved['routines'][0]['days']
    card.get_by_label('Vehicle nickname',exact=True).fill('Second save')
    card.get_by_role('button',name='Save local preferences',exact=True).click()
    page.wait_for_function('prefs.revision===2&&!card._busy')
    # Failed writes and reconnects retain a draft instead of pretending it saved.
    card.get_by_label('Vehicle nickname',exact=True).fill('Unsaved draft')
    page.evaluate("failure='preferences'")
    card.get_by_role('button',name='Save local preferences',exact=True).click()
    expect(card).to_contain_text('Temporary backend outage')
    page.evaluate('h.connected=false;card.hass=h')
    expect(card.locator('.freshness')).to_contain_text('Connection unavailable')
    page.evaluate('h.connected=true;card.hass=h')
    expect(card.get_by_label('Vehicle nickname',exact=True)).to_have_value('Unsaved draft')
    page.evaluate('card.remove();window.network=make("network",60)')
    network=page.locator('drive-console-service').first;expect(network).to_contain_text('This car role')
    page.wait_for_function('!network._busy')
    count=page.evaluate("calls.filter(c=>c.path.endsWith('/system')).length")
    page.evaluate('clock+=10000;network.hass=h')
    assert page.evaluate("calls.filter(c=>c.path.endsWith('/system')).length")==count
    page.evaluate('clock+=51000;network.hass=h')
    page.wait_for_function("calls.filter(c=>c.path.endsWith('/system')).length>"+str(count))
    page.wait_for_function('!network._busy');page.evaluate("failure='system'")
    network.get_by_role('button',name='Refresh',exact=True).click()
    expect(network.locator('.freshness')).to_contain_text('Refresh failed')
    expect(network).to_contain_text('This car role')
    # A forced read bypasses cache and the new feature wins an in-flight race.
    page.evaluate("delayed='system'");network.get_by_role('button',name='Refresh',exact=True).click()
    page.wait_for_function('release!==null');page.evaluate("network.setConfig({type:'custom:drive-console-service',feature:'updates'});release()")
    expect(network).to_contain_text('Installed revision: new-version')
    expect(network).not_to_contain_text('This car role')
    page.wait_for_function('!network._busy');page.evaluate('network.remove();window.owner=make("home")')
    owner=page.locator('drive-console-service').first
    owner.get_by_label('Separate home owner panel token (memory only)',exact=True).fill('local-private-owner-token-testing')
    owner.get_by_label('Home function',exact=True).select_option('config');owner.get_by_role('button',name='Open home function',exact=True).click()
    changes=owner.get_by_label('Changed settings by field path (advanced JSON)',exact=True);changes.fill('{"vehicle.nickname":"One"}')
    owner.get_by_role('button',name='Open home function',exact=True).click();expect(changes).to_have_value('{"vehicle.nickname":"One"}')
    expect(owner).to_contain_text('Finish or discard')
    owner.get_by_role('button',name='Queue validated settings',exact=True).click();page.wait_for_function('configRevision===1&&!owner._busy')
    changes.fill('{"vehicle.nickname":"Two"}');owner.get_by_role('button',name='Queue validated settings',exact=True).click();page.wait_for_function('configRevision===2&&!owner._busy')
    owner.get_by_label('Home function',exact=True).select_option('sync');owner.get_by_role('button',name='Open home function',exact=True).click()
    names=owner.get_by_label('Selected home basenames',exact=True);names.fill('one.txt');owner.get_by_role('button',name='Save home selection',exact=True).click();page.wait_for_function('selection.files[0]==="one.txt"&&!owner._busy')
    names.fill('two.txt');owner.get_by_role('button',name='Save home selection',exact=True).click();page.wait_for_function('selection.files[0]==="two.txt"&&!owner._busy')
    # Forget remains immediate even while the old owner request is in flight.
    page.evaluate("delayed='status';release=null");owner.get_by_label('Home function',exact=True).select_option('status');owner.get_by_role('button',name='Open home function',exact=True).click();page.wait_for_function('release!==null')
    owner.get_by_role('button',name='Forget home token',exact=True).click();assert page.evaluate('owner._homeToken')==''
    page.evaluate('release()');page.wait_for_function('!owner._busy');expect(owner.get_by_label('Separate home owner panel token (memory only)',exact=True)).to_have_value('')
    owner.get_by_label('Separate home owner panel token (memory only)',exact=True).fill('local-private-owner-token-testing')
    page.evaluate("owner.hass={...h,user:{id:'other-owner',is_admin:true}}")
    expect(owner.get_by_label('Separate home owner panel token (memory only)',exact=True)).to_have_value('')
    page.evaluate('owner.remove();window.report=make("reports")');report=page.locator('drive-console-service').first
    report.get_by_label('Recorded from (local time)',exact=True).fill('2026-01-01T12:00')
    report.get_by_label('Recorded through (local time)',exact=True).fill('2026-01-02T12:00')
    report.get_by_label('Exact sensor or event name (optional)',exact=True).fill('RPM')
    report.get_by_role('button',name='Load records',exact=True).click();expect(report).to_contain_text('1200');page.wait_for_function('!window.report._busy')
    loaded=page.evaluate("calls.filter(c=>c.path.includes('/service/report?')).at(-1).path")
    assert 'from=' in loaded and 'to=' in loaded and 'name=RPM' in loaded
    report.get_by_label('History type',exact=True).select_option('gps');expect(report.get_by_role('button',name='Next records',exact=True)).to_be_hidden()
    report.get_by_role('button',name='Download complete CSV',exact=True).click();page.wait_for_function('signed.length>0')
    assert 'kind=gps' in page.evaluate('signed.at(-1).path') and 'from=' in page.evaluate('signed.at(-1).path')
    page.wait_for_function('!window.report._busy');page.evaluate('report.remove();window.timeoutCard=make("updates")');timeout_card=page.locator('drive-console-service').first
    expect(timeout_card).to_contain_text('Installed revision: new-version');page.wait_for_function('!timeoutCard._busy')
    page.evaluate('window.originalTimeout=window.setTimeout;window.setTimeout=(fn,ms,...args)=>originalTimeout(fn,ms===20000?30:ms,...args);hang=true')
    timeout_card.get_by_role('button',name='Refresh',exact=True).click();expect(timeout_card).to_contain_text('Service timed out')
    page.wait_for_function('!timeoutCard._busy');expect(timeout_card).to_contain_text('Installed revision: new-version')
    page.evaluate('hang=false;window.setTimeout=originalTimeout')
    page.evaluate("const widget=document.createElement('drive-console-widget');widget.setConfig({type:'custom:drive-console-widget',widget:'status'});document.body.append(widget);widget.hass=h")
    page.locator('drive-console-widget').get_by_role('button',name='Reports & GPS history →',exact=True).click()
    assert page.url.endswith('/drive-console/reports')
    assert not errors,errors;browser.close()
print('Schedule forms, repeat saves, draft retention, refresh cadence, stale reads, request races and owner-token clearing passed')
