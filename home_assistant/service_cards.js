/* Native, editable HA cards for the existing local car and authenticated home APIs. */
(()=>{
const FEATURES={overview:'System overview',network:'Car Ethernet and Wi-Fi',power:'Power and laptop sleep',storage:'Shared archive',cameras:'G980H recording library',updates:'Software updates',radio:'Encrypted LoRaWAN',messages:'Radio messages',sync:'Selected local files',reports:'Recorded history and exports',preferences:'Vehicle preferences and schedules',home:'Home control studio'};
const STYLE=`:host{display:block;color:var(--primary-text-color,#ecf3ff)}ha-card{padding:20px;background:var(--ha-card-background,#101e32);border-radius:16px}header{display:flex;justify-content:space-between;align-items:center;gap:12px}h2{font-size:20px;margin:0}h3{font-size:16px}p,small{line-height:1.5}small{color:var(--secondary-text-color,#a7b8ce)}button,input,select,textarea{box-sizing:border-box;font:inherit;color:inherit;background:var(--card-background-color,#162b46);border:1px solid var(--divider-color,#38506c);border-radius:8px;padding:9px;margin:4px 4px 4px 0}button{cursor:pointer}button:disabled{opacity:.5;cursor:default}label{display:block;margin:8px 0}textarea{width:100%;min-height:120px;font-family:monospace}input{max-width:100%}table{border-collapse:collapse;width:100%;font-size:13px}th,td{text-align:left;padding:8px;border-bottom:1px solid var(--divider-color,#38506c);overflow-wrap:anywhere}pre{white-space:pre-wrap;overflow-wrap:anywhere;max-height:400px;overflow:auto}.scroll{overflow:auto;max-height:600px}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:10px}.tile{border:1px solid var(--divider-color,#38506c);border-radius:10px;padding:12px}.notice{white-space:pre-wrap;color:var(--secondary-text-color,#bed0e9)}video{width:100%;max-height:460px}progress{width:100%}.actions{display:flex;gap:8px;flex-wrap:wrap}.good{color:#4ddd9a}.waiting{color:#ffc36a}`;
const localCache=new Map();
const node=(tag,value)=>{const item=document.createElement(tag);if(value!==undefined)item.textContent=String(value);return item;};
const bytes=n=>Number.isFinite(n)?n>=1073741824?(n/1073741824).toFixed(1)+' GiB':n>=1048576?(n/1048576).toFixed(1)+' MiB':n+' B':'unknown';
const date=n=>Number.isFinite(n)?new Date(n*1000).toLocaleString():'not reported';
async function request(hass,path,data,headers={}){
  const response=await hass.fetchWithAuth(path,{method:data===undefined?'GET':'POST',headers:{...headers,...(data===undefined?{}:{'Content-Type':'application/json'})},...(data===undefined?{}:{body:JSON.stringify(data)})});
  const value=await response.json();if(!response.ok)throw Error(value.error||'Service is unavailable');return value;
}
function local(hass,feature,data,query=''){
  const path='/api/car_console/service/'+feature+query;
  if(data!==undefined)return request(hass,path,data).then(value=>{localCache.clear();return value;});
  const old=localCache.get(path);if(old&&Date.now()-old.at<3000)return old.promise;
  const promise=request(hass,path);localCache.set(path,{at:Date.now(),promise});if(localCache.size>16)localCache.delete(localCache.keys().next().value);promise.catch(()=>localCache.delete(path));return promise;
}
class CarServiceCard extends HTMLElement{
  constructor(){super();this.attachShadow({mode:'open'});this._homeToken='';this._last=0;this._draft=false;}
  setConfig(config){if(!FEATURES[config.feature])throw Error('Choose a car feature');this.config={...config};this.render();}
  getCardSize(){return 6;}
  getGridOptions(){return {columns:12,rows:6};}
  static getConfigElement(){return document.createElement('drive-console-service-editor');}
  static getStubConfig(){return {type:'custom:drive-console-service',feature:'overview'};}
  set hass(value){this._hass=value;this.refresh();}
  get hass(){return this._hass;}
  connectedCallback(){this._timer=setInterval(()=>this.refresh(),Math.max(5,Math.min(60,Number(this.config?.refresh_seconds)||15))*1000);this.refresh();}
  disconnectedCallback(){clearInterval(this._timer);this._homeToken='';this.shadowRoot.querySelectorAll('input[type="password"]').forEach(input=>input.value='');this.shadowRoot.querySelector('video')?.pause();}
  render(){
    this._homeBuilt=false;this._hold=false;
    this.shadowRoot.innerHTML=`<style>${STYLE}</style><ha-card><header><h2></h2><button id="refresh">Refresh</button></header><p class="notice" role="status" aria-live="polite"></p><main></main></ha-card>`;
    this.shadowRoot.querySelector('style').textContent=STYLE+'\n'+String(this.config.custom_css||'');
    if(Number.isFinite(Number(this.config.min_height)))this.style.minHeight=Math.max(120,Math.min(1500,Number(this.config.min_height)))+'px';
    this.shadowRoot.querySelector('h2').textContent=this.config.title||FEATURES[this.config.feature];this.body=this.shadowRoot.querySelector('main');this.notice=this.shadowRoot.querySelector('.notice');
    this.shadowRoot.getElementById('refresh').onclick=()=>{if(this._draft){this.say('Finish or discard your current edit before refreshing.');return;}this.refresh(true);};
    this._last=0;
  }
  say(message){this.notice.textContent=message;}
  button(label,action,parent=this.body){const button=node('button',label);button.onclick=()=>this.run(action);parent.append(button);return button;}
  input(label,value='',type='text',parent=this.body){const holder=node('label',label),input=node(type==='textarea'?'textarea':'input');if(type!=='textarea')input.type=type;input.setAttribute('aria-label',label);input.value=value;holder.append(input);parent.append(holder);return input;}
  select(label,items,parent=this.body){const holder=node('label',label),select=node('select');select.setAttribute('aria-label',label);for(const [value,title] of items){const option=node('option',title);option.value=value;select.append(option);}holder.append(select);parent.append(holder);return select;}
  table(rows,fields,parent=this.body){const scroll=node('div');scroll.className='scroll';const table=node('table'),head=node('tr');fields.forEach(([key,label])=>head.append(node('th',label)));table.append(head);for(const row of rows){const tr=node('tr');fields.forEach(([key])=>{const value=row[key];tr.append(node('td',value===null||value===undefined?'—':typeof value==='object'?JSON.stringify(value):String(value)));});table.append(tr);}scroll.append(table);parent.append(scroll);return table;}
  status(title,value,parent=this.body){const card=node('section');card.className='tile';card.append(node('h3',title),node('strong',value?.phase||'pending'));for(const key of ['message','detail','interface','address','gateway','standby','lastMessageAt','lastFixAt','satellitesUsed','satellitesVisible'])if(value?.[key]!==undefined)card.append(node('p',key.replaceAll(/([A-Z])/g,' $1')+': '+(key.endsWith('At')?date(value[key]):value[key])));parent.append(card);}
  async run(work){if(this._busy)return;this._busy=true;try{await work();}catch(error){this.say(error.message);}finally{this._busy=false;}}
  async refresh(force=false){
    if(!this.isConnected||!this._hass?.fetchWithAuth||!this.body||this._busy||this._draft||(!force&&(this._hold||this.config.feature==='home'&&this._homeBuilt))||(!force&&Date.now()-this._last<10000)||(!force&&(this.shadowRoot.activeElement||this.shadowRoot.querySelector('video')?.paused===false)))return;
    this._last=Date.now();await this.run(async()=>{
      this.say('Reading current local observations…');const feature=this.config.feature;
      if(feature==='home'){this.home();return;}
      if(feature==='messages'){await this.messages();return;}
      if(feature==='cameras'){await this.cameras();return;}
      if(feature==='reports'){await this.reports();return;}
      if(feature==='preferences'){await this.preferences();return;}
      if(feature==='sync'){await this.sync();return;}
      if(feature==='updates'){const data=await local(this._hass,'updates');this.body.replaceChildren();this.body.append(node('p','Installed revision: '+data.revision),node('p',data.updateTimer));for(const [name,value] of [['Duet delivery',data.courierProgress],['Last install receipt',data.courier],['Signed source link',data.softwareUpdates],['Reboot gate',data.reboot]])this.status(name,value);if(data.softwareUpdates?.updates)this.table(data.softwareUpdates.updates,[['path','File'],['phase','State'],['acknowledged','Confirmed'],['total','Fragments']]);this.say('A download or stored fragment is separate from a verified applied update. Car updates are carried by the Duet.');return;}
      const data=await local(this._hass,'system');this.body.replaceChildren();
      if(feature==='overview'){
        const grid=node('div');grid.className='grid';for(const [title,value] of [['Ethernet / Wi-Fi',data.network],['GPS recorder',data.gps],['Shared storage',data.storage.worker],['Radio',data.radio],['Software link',data.software]])this.status(title,value,grid);this.body.append(grid);this.say('Recorded observations, not a guarantee of current hardware reachability. Dashboard loading does not wait for these services.');
        const future=node('details');future.append(node('summary','Planned hardware extensions'));for(const [name,phase] of Object.entries(data.future||{}))future.append(node('p',name+': '+phase));this.body.append(future);
        this.table(data.peripherals||[],[['name','Stable USB identity'],['kind','Assigned role']]);
        this.button('Download private-data-free health summary',()=>{const report={schema:1,role:data.role,observedAt:data.observedAt,network:data.network.phase,gatewayRole:data.network.gateway||null,gps:{phase:data.gps.phase,mode:data.gps.gpsMode,satellitesUsed:data.gps.satellitesUsed},storage:{phase:data.storage.worker.phase,poolMounted:data.storage.poolMounted,reserveLow:data.storage.reserveLow},radio:data.radio.phase,software:data.software.phase,assistant:data.assistant};const url=URL.createObjectURL(new Blob([JSON.stringify(report,null,2)],{type:'application/json'}));const link=node('a');link.href=url;link.download='car-health.json';link.click();URL.revokeObjectURL(url);});
      }else if(feature==='network'){
        this.status('This car role: '+data.role,data.network);this.table(Object.entries(data.network.peers||{}).map(([role,mac])=>({role,mac,present:data.network.fresh?(data.network.peerStatus?.[role]?.present?'recent beacon':'remembered / unverified'):'status stale',health:data.network.peerStatus?.[role]?.health||{}})),[['role','Peer'],['mac','Ethernet hardware address'],['present','Presence'],['health','Last reported services']]);this.body.append(node('p','Gateway priority: Pi, Duet, laptop. No private SSH, Wi-Fi or Tailscale credentials are changed here.'));this.say(data.network.fresh?'Fresh local network observation.':'Network status is missing or stale; do not infer a car power-off event.');
      }else if(feature==='power'){
        const laptop=data.network.fresh&&data.network.peerStatus?.laptop?.present?data.network.peerStatus.laptop:null;
        this.status('This device power policy',data.power);if(laptop)this.status('Laptop last reported power policy',{phase:laptop.health.power||'pending'});this.status('Reboot / Park gate',data.reboot);this.body.append(node('p','Sleep requires confirmed car power loss and supported hardware. A missing network alone does not trigger sleep. Boot-on-power and closed-lid wake require firmware support.'));this.say('No forced suspend, reboot or vehicle-control commands are exposed.');
      }else if(feature==='storage'){
        this.status('MooseFS provision',data.storage);this.status('Archive worker',data.storage.worker);this.body.append(node('p','Pool mounted: '+(data.storage.poolMounted?'yes':'no')+' · local free: '+bytes(data.storage.localFreeBytes)+' · replica goal: '+data.storage.replicaGoal),node('p',data.storage.clientOnly?'Pi is a storage client; no bulk fallback to its microSD.':'Active GPS/OBD databases stay local. Originals are retained when archived.'));this.table(data.storage.catalog,[['kind','Archive type'],['objects','Objects'],['bytes','Bytes'],['archived','Archived objects']]);this.say(data.storage.reserveLow?'Local free space is below the archive reserve. No files are automatically deleted.':'A configured replica goal is separate from confirmed replica health.');
      }else if(feature==='radio'){
        this.status('Local adapter',data.radio);this.status('Encrypted round trip',data.radio.diagnostic);this.body.append(node('p','Home public key pinned: '+data.radio.homeKeyPinned+' · private key present: '+data.radio.privateKeyPresent),node('p','Radio data is encrypted at the physical radio boundary. GPS reporting remains deferred. Video, maps and large runtime updates use IP.'));
        this.button('Queue encrypted round-trip test',async()=>{const result=await local(this._hass,'radio-test',{action:'restart'});this.status('Test request',result);this.say('Queued on the normal encrypted radio path; only a matching received echo can mark it passed.');}).disabled=this._hass.user?.is_admin===false;
        this.say('RAK3172 belongs on the Tiny. A configured adapter is separate from a successful over-air test.');
      }
    });
  }
  async signed(path){const result=await this._hass.callWS({type:'auth/sign_path',path,expires:600});return result.path;}
  async cameras(after=''){
    const data=await local(this._hass,'cameras',undefined,after?'?after='+encodeURIComponent(after):'');this.body.replaceChildren();
    this.body.append(node('p','Botslab G980H · original names and channel folders are retained. Direct camera downloading waits for a verified protocol.'));
    const video=node('video');video.controls=true;video.preload='none';this.body.append(video);
    for(const item of data.items||[]){const row=node('section');row.className='tile';row.append(node('strong',item.name),node('p',(item.channel||'unspecified channel')+' · '+bytes(item.size)));this.button('Play local clip',async()=>{video.src=await this.signed('/api/car_console/download/clip?id='+encodeURIComponent(item.id));await video.play();},row);this.body.append(row);}
    if(!data.items?.length)this.body.append(node('p','No imported recordings are available. Place supplied channel folders in the configured dashcam inbox.'));
    if(data.hasMore)this.button('Next recordings',()=>this.cameras(data.next));this.say('Playback streams over the car LAN/IP; recordings are not sent over LoRaWAN.');
  }
  async messages(){
    const data=await local(this._hass,'messages');this.body.replaceChildren();const dest=this.select('To',['home','tiny','duet','pi','laptop'].map(role=>[role,role]));const message=this.input('Text message','','textarea');message.maxLength=240;message.oninput=()=>this._draft=!!message.value;
    this.button('Queue encrypted message',async()=>{await local(this._hass,'messages',{destination:dest.value,text:message.value,action:'text'});this._draft=false;await this.messages();this.say('Queued; delivery means durably saved by the recipient, not read.');}).disabled=this._hass.user?.is_admin===false;
    this.button('Discard draft',()=>{message.value='';this._draft=false;this.say('Draft discarded.');});
    this.button('Request read-only health',async()=>{await local(this._hass,'messages',{destination:dest.value,action:'status-request'});this.say('Read-only health request queued over the existing encrypted service.');});
    this.table(data.items||[],[['source','From'],['destination','To'],['text','Message'],['phase','Delivery']]);this.say('Messages use the existing durable encrypted radio service. GPS and files follow their separate controls.');
    for(const item of data.items||[])if(item.direction==='sent'&&item.phase==='queued')this.button('Cancel local retries: '+item.id.slice(0,12),async()=>{await local(this._hass,'messages',{action:'cancel',id:item.id});this._draft=false;await this.messages();this.say('Local retries cancelled. Already transmitted data cannot be recalled.');});
  }
  async reports(){
    const summary=await local(this._hass,'reports');this.body.replaceChildren();this.table(Object.entries(summary.tables||summary).map(([kind,value])=>({kind,...(typeof value==='object'?value:{count:value})})),[['kind','History'],['count','Rows'],['first','First'],['last','Last']]);
    const kind=this.select('History type',['values','commands','snapshots','snapshot-values','events','gps','gps-messages','stops','gps-stream','gps-sessions','gps-events'].map(value=>[value,value.replaceAll('-',' ')]));const rows=node('div');this.body.append(rows);let cursor=0;
    const load=async(older=false)=>{const data=await local(this._hass,'report',undefined,'?kind='+kind.value+'&limit=100&after='+(older?cursor:0));this._hold=true;cursor=data.next;rows.replaceChildren();this.table(data.items||[],(data.columns||[]).map(value=>[value,value]),rows);more.hidden=!data.hasMore;this.say(data.available===false?'This recorder table is not present yet.':'Recorded rows are shown without fabricating values or joining GPS gaps.');};
    this.button('Load records',()=>load());const more=this.button('Next records',()=>load(true));more.hidden=true;
    this.button('Download complete CSV',async()=>{const link=node('a');link.href=await this.signed('/api/car_console/download/csv?kind='+kind.value);link.download=kind.value+'.csv';link.click();});this.say('Choose a history table. CSV streams from the recorder without buffering the complete archive.');
  }
  async preferences(){
    const prefs=await local(this._hass,'preferences');this.body.replaceChildren();const nickname=this.input('Vehicle nickname',prefs.vehicle.nickname),model=this.input('Vehicle model',prefs.vehicle.model),year=this.input('Model year',prefs.vehicle.year,'number');
    const theme=this.select('Display preference',[['','Use widget settings'],['night','Night'],['day','Day']]),units=this.select('Unit preference',[['','Use widget settings'],['us','US'],['metric','Metric']]);theme.value=prefs.display.theme||'';units.value=prefs.display.units||'';
    const places=this.input('Saved places (advanced JSON)',JSON.stringify(prefs.destinations,null,2),'textarea'),routines=this.input('Scheduled departures (advanced JSON)',JSON.stringify(prefs.routines,null,2),'textarea');
    for(const input of [nickname,model,year,theme,units,places,routines])input.oninput=()=>this._draft=true;
    this.button('Save local preferences',async()=>{const data=structuredClone(prefs);data.revision++;Object.assign(data.vehicle,{nickname:nickname.value,model:model.value,year:Number(year.value)});Object.assign(data.display,{theme:theme.value||null,units:units.value||null});data.destinations=JSON.parse(places.value);data.routines=JSON.parse(routines.value);await local(this._hass,'preferences',data);this._draft=false;this.say('Local preferences saved. Native HA card appearance remains editable with each card’s editor.');});
    this.button('Discard unsaved changes',async()=>{this._draft=false;await this.preferences();});this.say('Navigation markers, route plans and manual arrival/departure trips use the Navigation tab. These scheduled places use the existing local vehicle-preference schema.');
  }
  async sync(){
    this._hold=false;
    const status=await local(this._hass,'sync');this.body.replaceChildren();this.body.append(node('p','Selected names: '+(status.selected.join(', ')||'none')+'. Only files already allowed locally can be edited.'));
    this.table(status.items,[['name','File'],['phase','State'],['error','Needs attention']]);
    for(const name of status.selected)this.button('Open '+name,async()=>{
      if(this._draft)throw Error('Save or close the current edit before opening another file.');this._hold=true;
      const file=await local(this._hass,'sync-file',undefined,'?name='+encodeURIComponent(name)),editor=node('section');editor.append(node('h3',name));const source=this.input('Local text',file.source,'textarea',editor);source.oninput=()=>this._draft=true;
      this.button('Save selected-file edit',async()=>{await local(this._hass,'sync-file',{action:'save',name,sha256:file.sha256,source:source.value});this._draft=false;await this.sync();this.say('Saved locally and queued; remote application still needs its receipt.');},editor);
      for(const conflict of file.conflicts||[]){const incoming=this.input('Verified incoming copy',conflict.incoming??'Common baseline unavailable','textarea',editor);incoming.readOnly=true;for(const [choice,label] of [['car','Use incoming copy'],['home','Keep saved local copy'],['merge','Use edited text']])this.button(label,async()=>{await local(this._hass,'sync-file',{action:'resolve',name,id:conflict.id,sha256:file.sha256,choice,source:source.value});this._draft=false;await this.sync();this.say('Choice journaled. Backups and applied-receipt checks remain in effect.');},editor).disabled=conflict.incoming===null;}
      this.button('Close without saving',async()=>{this._draft=false;await this.sync();},editor);this.body.append(editor);
    });this.say('No scripts, active recorder databases, maps or video enter this small selected-data channel.');
  }
  home(){
    this._homeBuilt=true;
    this.body.replaceChildren();const token=this.input('Separate home owner panel token (memory only)',this._homeToken,'password');token.autocomplete='off';token.oninput=()=>{this._homeToken=token.value;};
    const mode=this.select('Home function',[['status','Integration and delivery status'],['transfers','Signed transfer controls'],['source','Source-copy editor'],['sync','Selected-file studio'],['layout','Custom dashboard project / HTML'],['config','Versioned car settings'],['coverage','Gateway coverage evidence'],['messages','Home message inbox'],['cameras','Home clip catalog']]);
    const role=this.select('Car role',['tiny','duet','pi','laptop'].map(value=>[value,value]));const output=node('section');this.body.append(output);
    const homeCall=(feature,data,query='')=>{if(!this._homeToken)throw Error('Enter the separate home panel token. It is never saved in HA configuration.');return request(this._hass,'/api/car_console/home/'+feature+query,data,{'X-Car-Home-Token':this._homeToken});};
    this.button('Open home function',async()=>{
      if(this._draft)throw Error('Finish or discard the current edit before opening another home function.');
      output.replaceChildren();const feature=mode.value,selectedRole=role.value;const query='?role='+selectedRole;
      if(feature==='transfers'){
        const filter=this.select('Transfer history',[['active','Active'],['finished','Finished'],['all','All']],output),budget=node('p'),list=node('div');output.append(budget,list);let cursor=null;
        const load=async(older=false)=>{
          const data=await homeCall(feature,undefined,query+'&state='+filter.value+(older&&cursor?'&before='+cursor:''));cursor=data.nextCursor;if(!older)list.replaceChildren();
          budget.textContent=data.budget?'Shared downlinks: '+data.budget.reserved+'/'+data.budget.limit+' reservations used · '+data.budget.remaining+' remaining · next eligible '+date(data.budget.nextEligibleAt):'Downlink budget not reported';
          for(const item of data.items){const row=node('section');row.className='tile';row.append(node('strong',item.path+' · '+item.displayPhase),node('p',item.acknowledged+'/'+item.total+' confirmed fragments · priority '+item.priority+' · '+item.decision));
            const details=node('details');details.append(node('summary','Signed transfer details'),node('pre',JSON.stringify(item,null,2)));row.append(details);
            for(const [label,action,value] of [[item.paused?'Resume':'Pause',item.paused?'resume':'pause'],['Retry missing fragments','retry'],[item.policy==='ip-only'?'Use automatic fallback':'Use IP only','policy',item.policy==='ip-only'?'auto':'ip-only'],[item.priority==='high'?'Normal priority':'High priority','priority',item.priority==='high'?'normal':'high']])if(!['applied','failed'].includes(item.phase))this.button(label,async()=>{await homeCall('transfer',{device:item.device,id:item.id,revision:item.revision,action,value});await load();this.say('Sender controls saved. Reception still requires a separate applied receipt.');},row);list.append(row);
          }if(!data.items.length&&!older)list.append(node('p','No matching signed transfers.'));more.hidden=!cursor;
        };
        filter.onchange=()=>this.run(()=>load());this.button('Refresh transfers',()=>load(),output);const more=this.button('Older transfers',()=>load(true),output);await load();return;
      }
      if(feature==='source'){
        const path=this.select('Approved source path',(selectedRole==='duet'?['duet/proxy.py','duet/launch.sh','duet/launcher_lock.py']:['app/web/cockpit.html','app/web/cockpit.css','app/web/cockpit.js','app/web/index.html','app/web/styles.css','app/web/app.js','app/web/update-watch.js','recorder/obd_full_recorder.py','recorder/gps_recorder.py']).map(value=>[value,value]),output);
        this.button('Load reported copy',async()=>{if(this._draft)throw Error('Finish the current source edit first.');const loaded=await homeCall('source',undefined,query+'&path='+encodeURIComponent(path.value));const source=this.input('Editable source copy',loaded.source,'textarea',output);source.oninput=()=>this._draft=true;this.button('Sign and queue change',async()=>{await homeCall('publish',{device:'car-1',role:selectedRole,path:loaded.path,base:loaded.base,source:source.value});this._draft=false;this.say('Signed delta queued; installation remains receipt-driven.');},output);},output);return;
      }
      if(feature==='sync'){
        const data=await homeCall('sync',undefined,query);const names=this.input('Selected home basenames',data.files.join(', '),'text',output);
        this.button('Save home selection',async()=>{await homeCall('sync',{role:selectedRole,revision:data.revision,files:names.value.split(',').map(v=>v.trim()).filter(Boolean)});this.say('Home selection saved. The car allowlist is unchanged.');},output);
        for(const name of data.files)this.button('Edit '+name,async()=>{if(this._draft)throw Error('Finish or discard the current edit first.');const file=await homeCall('sync-file',undefined,query+'&name='+encodeURIComponent(name));const source=this.input('Home file copy',file.source,'textarea',output);source.oninput=()=>this._draft=true;this.button('Save home edit',async()=>{await homeCall('sync-file',{action:'save',role:selectedRole,name,sha256:file.sha256,source:source.value});this._draft=false;this.say('Home edit queued.');},output);for(const conflict of file.conflicts||[]){const incoming=this.input('Reported car copy',conflict.incoming??'Baseline unavailable','textarea',output);incoming.readOnly=true;for(const [choice,label] of [['car','Use car copy'],['home','Keep home copy'],['merge','Use edited text']])this.button(label,async()=>{await homeCall('sync-file',{action:'resolve',role:selectedRole,name,id:conflict.id,sha256:file.sha256,choice,source:source.value});this._draft=false;this.say('Resolution saved with backups; return transfers remain pending receipt.');},output).disabled=conflict.incoming===null;}},output);return;
      }
      const result=await homeCall(feature);const json=this.input('Home '+feature+' data',JSON.stringify(result,null,2),'textarea',output);json.readOnly=feature!=='layout';
      if(feature==='layout'){
        let state=result;json.oninput=()=>this._draft=true;
        this.button('Save custom dashboard draft',async()=>{const next=JSON.parse(json.value);state=await homeCall('layout',{device:'car-1',generation:state.generation,action:'save',project:next.project||next.draft||next});json.value=JSON.stringify(state,null,2);this._draft=false;this.say('Draft saved in the validated home layout store.');},output);
        this.button('Publish saved layout',async()=>{if(this._draft)throw Error('Save the draft before publishing.');state=await homeCall('layout',{device:'car-1',generation:state.generation,action:'publish',project:state.draft});json.value=JSON.stringify(state,null,2);this.say('Home layout publication queued; device acknowledgment remains required.');},output);
        this.button('Use standard dashboard',async()=>{if(this._draft)throw Error('Save or discard the draft first.');state=await homeCall('layout',{device:'car-1',generation:state.generation,action:'disable'});json.value=JSON.stringify(state,null,2);this.say('Standard-dashboard preference queued; the custom project is retained.');},output);
        if(state.history?.length){const version=this.select('Saved layout version',state.history.map(item=>[String(item.revision),'Revision '+item.revision+' · '+date(item.created)]),output);this.button('Restore saved layout',async()=>{if(this._draft)throw Error('Save or discard the draft first.');state=await homeCall('layout',{device:'car-1',generation:state.generation,action:'restore',version:Number(version.value)});json.value=JSON.stringify(state,null,2);this.say('Saved layout restored as a new revision; application remains receipt-driven.');},output);}
      }
      if(feature==='config'){const changes=this.input('Changed settings by field path (advanced JSON)','{}','textarea',output);this.button('Queue validated settings',async()=>{await homeCall('change',{device:'car-1',baseRevision:result.revision,changes:JSON.parse(changes.value)});this.say('Validated settings queued.');},output);}
      this.say('Home functions require the paired local SSH tunnel and separate owner authentication. Local car features remain available when home is offline.');
    });
    this.button('Forget home token',()=>{this._homeToken='';this._draft=false;this.home();this.say('Home owner token cleared from this card.');});
    this.button('Discard current edit',()=>{this._draft=false;this.home();});this.say('Owner controls use the existing restricted home tunnel. No owner token is stored, logged, added to URLs, or sent over LoRaWAN.');
  }
}
class ServiceEditor extends HTMLElement{
  setConfig(config){this.config={...config};this.render();}
  set hass(value){this._hass=value;this.render();}
  connectedCallback(){this.render();}
  render(){if(!this.config||!this._hass)return;if(!this.form){this.form=document.createElement('ha-form');this.append(this.form);this.form.addEventListener('value-changed',event=>{this.config={...this.config,...event.detail.value};this.dispatchEvent(new CustomEvent('config-changed',{detail:{config:this.config},bubbles:true,composed:true}));});}this.form.hass=this._hass;this.form.data=this.config;this.form.schema=[{name:'feature',selector:{select:{options:Object.entries(FEATURES).map(([value,label])=>({value,label}))}}},{name:'title',selector:{text:{}}},{name:'refresh_seconds',selector:{number:{min:5,max:60,mode:'box'}}},{name:'min_height',selector:{number:{min:120,max:1500,mode:'box'}}},{name:'custom_css',selector:{text:{multiline:true}}}];this.form.computeLabel=({name})=>({feature:'Car feature',title:'Card heading',refresh_seconds:'Refresh interval (seconds)',min_height:'Minimum height',custom_css:'Card CSS (advanced)'})[name];}
}
if(!customElements.get?.('drive-console-service'))customElements.define('drive-console-service',CarServiceCard);
if(!customElements.get?.('drive-console-service-editor'))customElements.define('drive-console-service-editor',ServiceEditor);
window.customCards=window.customCards||[];window.customCards.push({type:'drive-console-service',name:'Drive Console services',description:'Native system, storage, radio, camera, archive, settings and home-owner controls',preview:true});
})();
