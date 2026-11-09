/* Native Lovelace widgets using the original local Drive Console artwork/CSS. */
const ORIGINAL = null; // Filled from the original dashboard by the managed installer.
const KINDS = {clock:'Clock',media:'Local music',map:'Offline map',vehicleArt:'Vehicle illustration',gauges:'Driving gauges',metrics:'Vehicle metrics',status:'Connection status',value:'Sensor value',text:'Text',link:'Link / action',html:'HTML / CSS / JavaScript',page:'Page settings'};
const DEFAULTS={speed:'sensor.car_speed',rpm:'sensor.car_rpm',coolant:'sensor.car_coolant',voltage:'sensor.car_ecu_voltage',fuel:'sensor.car_raw_fuel_level',load:'sensor.car_engine_load',gps:'device_tracker.car_location'};
const number = (hass,id) => {
  if(!hass?.connected)return null;
  const value=hass.states?.[id]?.state;
  if(value==null || value==='' || value==='unknown' || value==='unavailable')return null;
  const n=Number(value);return Number.isFinite(n)?n:null;
};
const temperature=(n,source,target)=>{
  if(n===null)return null;
  const c=['°F','F'].includes(source)?(n-32)*5/9:source==='K'?n-273.15:n;
  return target==='C'?c:c*9/5+32;
};
const display=(n,d=0)=>n===null?'—':n.toLocaleString(undefined,{maximumFractionDigits:d});
const text=(root,id,value)=>{const el=root.getElementById(id);if(el && el.textContent!==String(value))el.textContent=String(value);};
const event=(el,name,detail)=>el.dispatchEvent(new CustomEvent(name,{detail,bubbles:true,composed:true}));
const bounded=(v,fallback,min,max)=>v==null || v==='' || typeof v==='boolean' || !Number.isFinite(Number(v))?fallback:Math.min(max,Math.max(min,Number(v)));
const currentPage=config=>config?.cards?.find(c=>c.type==='custom:drive-console-widget' && c.widget==='page')||{};
const targetView=(views,name)=>{
  const alias={home:'drive',diagnostics:'health'}[name]||name;
  return views.find(v=>v.path===alias)||views.find(v=>v.title?.toLowerCase()===String(name).toLowerCase())||(name==='home'?views[0]:undefined);
};
function htmlDocument(config){
  const safe=value=>String(value||'').replace(/<\/script/gi,'<\\/script');
  return `<!doctype html><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data: blob:; connect-src 'none'; form-action 'none'; base-uri 'none'"><style>body{margin:0;color:#eee;background:#101c30;font-family:system-ui}${String(config.css||'').replace(/<\/style/gi,'<\\/style')}</style>${config.html||'<h1>My car dashboard</h1><p id="speed">Waiting for data</p>'}<script>window.driveData={};addEventListener('message',e=>{if(e.source!==parent||e.data?.channel!=='drive-html-data')return;window.driveData=e.data.data;dispatchEvent(new CustomEvent('drive-data',{detail:window.driveData}));});parent.postMessage({channel:'drive-html-ready'},'*');<\/script><script>${safe(config.javascript)}<\/script>`;
}
const navigationBus=new EventTarget();
const defaultNavigation=()=>({revision:0,settings:{lat:39.5,lon:-98.35,zoom:5,followGPS:true,usaOnly:true,routingRegions:['us']},markers:[],trips:[],route:{waypoints:[],geometry:[],distance:null,duration:null,instructions:[]}});
const navID=()=>crypto.randomUUID?.()||Array.from(crypto.getRandomValues(new Uint8Array(16)),n=>n.toString(16).padStart(2,'0')).join('');
const localDateTime=stamp=>{const d=new Date(stamp);return new Date(d.getTime()-d.getTimezoneOffset()*60000).toISOString().slice(0,16);};
const validPoint=p=>p&&Number.isFinite(p.lat)&&Number.isFinite(p.lon)&&Math.abs(p.lat)<=90&&Math.abs(p.lon)<=180;
let onlineProviderBlocked=false;
const onlineTileRequests=new Map();
function onlineTile(url){
  if(onlineProviderBlocked)return Promise.reject(new Error('Map provider refused access'));
  if(onlineTileRequests.has(url))return onlineTileRequests.get(url);
  const request=fetch(url,{mode:'cors',credentials:'omit',cache:'default',referrerPolicy:'strict-origin-when-cross-origin'}).then(async response=>{
    if(response.status===403 || response.status===429)onlineProviderBlocked=true;
    if(!response.ok)throw new Error(`Map provider HTTP ${response.status}`);
    const blob=await response.blob();if(!blob.type.startsWith('image/'))throw new Error('Invalid map tile');return blob;
  }).finally(()=>onlineTileRequests.delete(url));
  onlineTileRequests.set(url,request);return request;
}
function gpsPosition(hass,entity=DEFAULTS.gps){
  const obj=hass?.states?.[entity],a=obj?.attributes||{};
  return hass?.connected && obj && !['unknown','unavailable'].includes(obj.state) && Number.isFinite(a.latitude) && Number.isFinite(a.longitude) && Math.abs(a.latitude)<=90 && Math.abs(a.longitude)<=180?a:null;
}
function gpsLabel(hass,entity=DEFAULTS.gps){
  if(gpsPosition(hass,entity))return 'connected';
  const status=hass?.connected&&hass.states?.['binary_sensor.car_gps_fix'];
  return status&&['on','off'].includes(status.state)&&status.attributes?.receiver_connected===true?'waiting for fix':'disconnected';
}
function go(url,newTab=false){
  let dest;try{dest=new URL(url,location.href);}catch(_){return;}
  if(!['http:','https:'].includes(dest.protocol))return;
  if(newTab){window.open(dest.href,'_blank','noopener');return;}
  if(dest.origin===location.origin){history.pushState(null,'',dest.pathname+dest.search+dest.hash);window.dispatchEvent(new CustomEvent('location-changed',{detail:{replace:false}}));}
  else location.assign(dest.href);
}
function icon(el,value){if(String(value).startsWith('mdi:')){const i=document.createElement('ha-icon');i.icon=value;el.replaceChildren(i);}else el.textContent=value||'◇';}
function markup(kind){
  if(ORIGINAL.widgets[kind])return ORIGINAL.widgets[kind];
  if(kind==='html')return '<section class="glass"><iframe id="htmlFrame" title="Custom dashboard HTML" sandbox="allow-scripts" style="border:0;width:100%;height:100%;min-height:300px"></iframe></section>';
  if(kind==='page')return '<section class="glass status-card"><span class="eyebrow">PAGE SETTINGS</span><strong>Appearance & navigation</strong><p>Controls this tab only: layout, header, sidebar, tabs, bottom controls and colors. Edit this card to customize them.</p></section>';
  if(kind==='text' || kind==='link')return '<section class="glass status-card"><span class="eyebrow" id="cardHeading"></span><p id="cardBody" style="white-space:pre-wrap"></p><button class="subtle-button" id="cardAction"></button></section>';
  if(kind==='value')return '<section class="glass status-card"><span class="eyebrow" id="cardHeading"></span><strong id="sensorValue" style="font-size:40px">—</strong><span id="sensorUnit"></span></section>';
  return '<section class="glass status-card"><span class="eyebrow">CAR CONNECTION</span><strong id="connectionTitle"></strong><p id="connectionDetail"></p><button class="subtle-button" id="reports">Reports & GPS history →</button></section>';
}
function applyWidgetStyle(el){
  const c=el.config||{},p=el.pageStyle||{},root=el.shadowRoot;
  for(const [name,key] of [['--cyan','accent'],['--drive-text','text_color'],['--drive-border','border_color'],['--drive-card-background','surface_color']]){
    const value=c[key]||p[key];if(value && CSS.supports('color',value))el.style.setProperty(name,value);else el.style.removeProperty(name);
  }
  const radius=c.border_radius??p.border_radius;
  el.style.setProperty('--drive-radius',bounded(radius,17,0,60)+'px');
  el.style.setProperty('--drive-font-size',bounded(c.font_size??p.font_size,14,10,36)+'px');
  const section=root.querySelector('section');if(section){section.style.minHeight=c.min_height?bounded(c.min_height,120,60,1500)+'px':'';section.style.height='100%';}
  let custom=root.getElementById('custom-style');if(!custom){custom=document.createElement('style');custom.id='custom-style';root.append(custom);}
  custom.textContent=String(c.custom_css||'');
}
const media=new EventTarget();
let audio,tracks=[],track=-1,objectURL;
function player(){
  if(!audio){audio=new Audio();audio.volume=.65;for(const name of ['play','pause','timeupdate','durationchange','volumechange','error'])audio.addEventListener(name,()=>media.dispatchEvent(new Event('change')));audio.addEventListener('ended',()=>playTrack(track+1));}
  return audio;
}
function playTrack(index){
  if(!tracks.length)return;
  track=(index+tracks.length)%tracks.length;
  if(objectURL)URL.revokeObjectURL(objectURL);
  objectURL=URL.createObjectURL(tracks[track]);player().src=objectURL;
  player().play().catch(()=>media.dispatchEvent(new Event('change')));
}
const elapsed=n=>Number.isFinite(n)?`${Math.floor(n/60)}:${String(Math.floor(n%60)).padStart(2,'0')}`:'0:00';
const EXTRA_CSS=`
:host{display:block;height:100%;min-width:0;color:var(--text);font:14px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
:host([data-theme=day]){color-scheme:light}
section{height:100%}.glass{border-radius:var(--drive-radius,17px);background:var(--drive-card-background,var(--panel-glass));border-color:var(--drive-border,var(--border));color:var(--drive-text,var(--text))}:host{font-size:var(--drive-font-size,14px)}.metric-tiles{height:100%}.map-card{height:100%}.clock-card{min-height:180px}.music-card{min-height:300px}.vehicle-art{min-height:190px}.gauge-card{min-height:210px}
.status-card{padding:22px;display:grid;gap:12px;min-height:130px}.status-card p{color:var(--muted)}
`;
class DriveConsoleWidget extends HTMLElement {
  constructor(){super();this.attachShadow({mode:'open'});this._onMedia=()=>this.updateMedia();this._zoom=5;this._onNavigation=e=>{if(this.config?.widget!=='map')return;this._navigation=structuredClone(e.detail);this._navLoaded=true;this.drawNavigation();};this._htmlMessage=e=>{if(e.source===this.shadowRoot.getElementById('htmlFrame')?.contentWindow && e.data?.channel==='drive-html-ready')this.update();};}
  setConfig(config){
    if(!KINDS[config.widget])throw new Error('Choose a Drive Console widget');
    this.config={...config};this.dataset.widget=config.widget;this._zoom=bounded(config.zoom,5,2,18);this.render();
  }
  getCardSize(){return this.config?.widget==='page'?2:this.config?.widget==='map'?8:this.config?.widget==='media'?5:3;}
  setPageStyle(value){this.pageStyle=value;applyWidgetStyle(this);}
  getGridOptions(){return {columns:12,rows:this.getCardSize()};}
  static getConfigElement(){return document.createElement('drive-console-widget-editor');}
  static getStubConfig(){return {type:'custom:drive-console-widget',widget:'gauges',speed_unit:'mph'};}
  set hass(hass){this._hass=hass;this.update();}
  get hass(){return this._hass;}
  connectedCallback(){navigationBus.addEventListener('saved',this._onNavigation);window.addEventListener('message',this._htmlMessage);this._timer=setInterval(()=>{this.clock();this.replayTick();this.update();},1000);media.addEventListener('change',this._onMedia);this.clock();this.update();}
  disconnectedCallback(){this.removeLeaflet();navigationBus.removeEventListener('saved',this._onNavigation);window.removeEventListener('message',this._htmlMessage);clearInterval(this._timer);media.removeEventListener('change',this._onMedia);this._ticket=(this._ticket||0)+1;}
  render(){
    if(!this.config || !ORIGINAL)return;
    const kind=this.config.widget;
    this.removeLeaflet();this._ticket=(this._ticket||0)+1;
    this.shadowRoot.innerHTML=`<style>${ORIGINAL.css}\n${EXTRA_CSS}</style>`+markup(kind);
    applyWidgetStyle(this);
    if(kind==='map'){this.installNavigation();const ownerStyle=this.shadowRoot.getElementById('custom-style');if(ownerStyle)this.shadowRoot.append(ownerStyle);}
    if(kind==='html')this.shadowRoot.getElementById('htmlFrame').srcdoc=htmlDocument(this.config);
    if(this.config.title){const label=this.shadowRoot.querySelector('.eyebrow,.card-caption span,.map-heading strong');if(label)label.textContent=this.config.title;}
    const c=this.config,r=this.shadowRoot,hide=(selector,flag)=>r.querySelectorAll(selector).forEach(el=>{el.hidden=flag===false;});
    hide('.hero-date',c.show_date);hide('.clock-bottom',c.show_footer);text(r,'cardHeading',c.title||KINDS[kind]);text(r,'cardBody',c.body||'');
    if(kind==='clock'){if(c.footer_text)r.querySelector('.clock-bottom').textContent=c.footer_text;if(c.clock_size)r.getElementById('heroTime').style.fontSize=bounded(c.clock_size,46,24,120)+'px';}
    if(kind==='media'){hide('.album-art',c.show_art);hide('.pick-music',c.show_picker);hide('.track-progress',c.show_seek);hide('.player-controls',c.show_controls);if(c.picker_label)r.querySelector('.pick-music').firstChild.textContent=c.picker_label;}
    if(kind==='map'){hide('.map-side-tools',c.show_map_controls);hide('.map-bottom',c.show_position);if(c.empty_title)r.querySelector('.map-empty strong').textContent=c.empty_title;if(c.empty_message)r.querySelector('.map-empty p').textContent=c.empty_message;if(c.position_label)r.querySelector('.map-bottom-label').textContent=c.position_label;if(c.route_label)text(r,'gpsDetail',c.route_label);if(c.button_label)r.querySelector('.subtle-button').textContent=c.button_label;}
    if(kind==='vehicleArt'){hide('.truck-art',c.show_illustration);if(c.brand_text)r.querySelector('.vehicle-art-title>span:last-child').textContent=c.brand_text;if(c.status_text)r.querySelector('.vehicle-art-subtitle').textContent=c.status_text;if(c.vehicle_color && CSS.supports('color',c.vehicle_color))r.querySelector('path[fill="url(#paint)"]')?.setAttribute('fill',c.vehicle_color);if(c.image_url){let url;try{url=new URL(c.image_url,location.href);}catch(_){}if(url && ['http:','https:'].includes(url.protocol)){const image=new Image();image.className='truck-art';image.style.objectFit='contain';image.alt=c.brand_text||'Vehicle';image.src=url.href;r.querySelector('.truck-art').replaceWith(image);}}}
    if(kind==='gauges'){hide('.gauge-age',c.show_age);hide('#gaugeFreshness',c.show_freshness);for(const [i,label,color] of [[0,c.speed_label,c.speed_color],[1,c.rpm_label,c.rpm_color]]){if(label)r.querySelectorAll('.gauge-caption')[i].textContent=label;if(color && CSS.supports('color',color))r.querySelectorAll('.gauge-ring')[i].style.setProperty('--arc',color);}}
    if(kind==='metrics')for(const [i,key] of ['coolant','voltage','fuel','load'].entries()){const tile=r.querySelectorAll('.metric-tile')[i];if(c[key+'_label'])tile.querySelector('small').textContent=c[key+'_label'];if(c[key+'_icon'])tile.querySelector('.metric-ico').textContent=c[key+'_icon'];if(Array.isArray(c.visible_metrics))tile.hidden=!c.visible_metrics.includes(key);}
    const action=r.getElementById('cardAction');if(action){action.hidden=kind!=='link';action.textContent=c.button_label||'Open';action.onclick=()=>go(c.url||'/config',c.new_tab!==false);}
    const on=(id,fn)=>this.shadowRoot.getElementById(id)?.addEventListener('click',fn);
    on('playPause',()=>{if(!tracks.length){this.shadowRoot.getElementById('mediaInput')?.click();return;}player().paused?player().play().catch(()=>{}):player().pause();});
    on('prevTrack',()=>playTrack(track-1));on('nextTrack',()=>playTrack(track+1));
    this.shadowRoot.getElementById('mediaInput')?.addEventListener('change',e=>{tracks=Array.from(e.target.files||[]);if(tracks.length)playTrack(0);});
    this.shadowRoot.getElementById('mediaSeek')?.addEventListener('input',e=>{if(Number.isFinite(player().duration))player().currentTime=Number(e.target.value)*player().duration/100;});
    on('mapZoomIn',()=>{this._zoom=Math.min(18,this._zoom+1);this._mapKey=null;this.update();});
    on('mapZoomOut',()=>{this._zoom=Math.max(2,this._zoom-1);this._mapKey=null;this.update();});
    on('mapLocate',()=>{this._viewCenter=null;this._followGPS=true;this._mapKey=null;this.update();});
    this.shadowRoot.querySelector('[data-view="navigation"]')?.addEventListener('click',()=>event(this,'drive-console-screen',{screen:'navigation'}));
    on('reports',()=>go('/drive-console/reports'));
    this.clock();this.update();this.updateMedia();
  }
  clock(){if(!this.config || this.config.widget!=='clock')return;const now=new Date();text(this.shadowRoot,'heroTime',now.toLocaleTimeString(undefined,{hour:'numeric',minute:'2-digit',hour12:this.config.hour24?false:undefined}));text(this.shadowRoot,'heroDate',now.toLocaleDateString(undefined,{dateStyle:['full','long','short'].includes(this.config.date_format)?this.config.date_format:'full'}));}
  update(){
    if(!this.config || !this.shadowRoot.firstChild)return;
    const h=this._hass,c=this.config,r=this.shadowRoot;
    if(c.widget==='html'){const states={};for(const id of Object.values(DEFAULTS))if(h?.states?.[id])states[id]=h.states[id];r.getElementById('htmlFrame')?.contentWindow?.postMessage({channel:'drive-html-data',data:{mode:'live',states}},'*');}
    if(c.widget==='gauges'){
      let speed=number(h,c.speed_entity||DEFAULTS.speed);
      const source=h?.states?.[c.speed_entity||DEFAULTS.speed]?.attributes?.unit_of_measurement;
      const unit=c.speed_unit||'mph';
      if(speed!==null && unit==='mph' && source==='km/h')speed*=.621371192;
      if(speed!==null && unit==='km/h' && source==='mph')speed/= .621371192;
      const rpm=number(h,c.rpm_entity||DEFAULTS.rpm);
      text(r,'speedValue',display(speed,bounded(c.speed_decimals,0,0,3)));text(r,'speedUnit',unit.toUpperCase());r.querySelector('.rpm-ring small').textContent=Number(c.rpm_divisor)===1?'RPM':'RPM ×1000';text(r,'rpmValue',display(rpm===null?null:rpm/(Number(c.rpm_divisor)===1?1:1000),bounded(c.rpm_decimals,1,0,3)));
      for(const [id,value,max] of [['speedRing',speed,bounded(c.speed_max,unit==='mph'?140:220,1,1000)],['rpmRing',rpm,bounded(c.rpm_max,6500,100,30000)]])r.getElementById(id)?.style.setProperty('--progress',`${value===null?0:Math.max(0,Math.min(1,value/max))*260}deg`);
      text(r,'gaugeFreshness',speed===null && rpm===null?'WAITING':'LIVE');
      const seen=h?.states?.[c.rpm_entity||DEFAULTS.rpm]?.attributes?.recorded_at;
      text(r,'gaugeAge',rpm===null?'—':seen?`${Math.max(0,Math.floor(Date.now()/1000-Number(seen)))}s ago`:'Live');
    }else if(c.widget==='metrics'){
      const coolant=number(h,c.coolant_entity||DEFAULTS.coolant),voltage=number(h,c.voltage_entity||DEFAULTS.voltage),fuel=number(h,c.fuel_entity||DEFAULTS.fuel),load=number(h,c.load_entity||DEFAULTS.load);
      text(r,'coolantValue',coolant===null?'—':display(temperature(coolant,h?.states?.[c.coolant_entity||DEFAULTS.coolant]?.attributes?.unit_of_measurement,c.temperature_unit))+(c.temperature_unit==='C'?'°C':'°F'));
      text(r,'voltageValue',voltage===null?'—':display(voltage,1)+' V');text(r,'fuelValue',fuel===null?'—':display(fuel)+'%');text(r,'loadValue',load===null?'—':display(load)+'%');
    }else if(c.widget==='value'){const obj=h?.states?.[c.entity],value=number(h,c.entity);text(r,'cardHeading',c.title||obj?.attributes?.friendly_name||'Sensor');text(r,'sensorValue',value===null?'—':display(value,bounded(c.decimals,1,0,6)));text(r,'sensorUnit',c.unit||obj?.attributes?.unit_of_measurement||'');
    }else if(c.widget==='map')this.updateMap();
    else if(c.widget==='status'){
      text(r,'connectionTitle',h?.connected?'Car Assistant connected':'Waiting for Car Assistant');
      const obd=h?.connected && h.states?.[c.obd_entity||'binary_sensor.car_obd_connection']?.state==='on',gps=!!gpsPosition(h,c.gps_entity||DEFAULTS.gps);
      text(r,'connectionDetail',`OBD: ${obd?'connected':'unavailable'} · GPS: ${gpsLabel(h,c.gps_entity||DEFAULTS.gps)}. Measurements are read only.`);
    }
  }
  async loadNavigation(){
    if(this.config?.widget!=='map'||this._navLoading||this._navLoaded||!this._hass?.fetchWithAuth)return;
    this._navLoading=true;
    try{const response=await this._hass.fetchWithAuth('/api/car_console/navigation');if(!response.ok)throw Error('Navigation storage unavailable');this._navigation=await response.json();this._zoom=this.config.zoom==null?this._navigation.settings.zoom:bounded(this.config.zoom,5,2,18);this._followGPS=this._navigation.settings.followGPS;this._navLoaded=true;this._mapKey=null;this.updateMap();this.drawNavigation();}
    catch(e){this._navLoaded=true;this.navNotice(e.message);}finally{this._navLoading=false;}
  }
  navNotice(message){text(this.shadowRoot,'navNotice',message);}
  async saveNavigation(){
    if(!this._hass?.fetchWithAuth){this.navNotice('Navigation storage unavailable');return;}
    // Serialize edits within this card; the server also protects other windows.
    this._navSave=(this._navSave||Promise.resolve()).catch(()=>{}).then(async()=>{
      const data=structuredClone(this._navigation);data.revision++;
      const response=await this._hass.fetchWithAuth('/api/car_console/navigation',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
      if(!response.ok){let error;try{error=(await response.json()).error;}catch(_){}throw Error(error||'Could not save navigation');}
      const saved=await response.json();this._navigation.revision=saved.revision;
      navigationBus.dispatchEvent(new CustomEvent('saved',{detail:structuredClone(this._navigation)}));this.navNotice('Saved on the Tiny');this.drawNavigation();
    }).catch(e=>this.navNotice(e.message));
    return this._navSave;
  }
  installNavigation(){
    const r=this.shadowRoot,surface=r.getElementById('mapSurface');if(!surface)return;
    if(!this._navigation)this._navigation=defaultNavigation();
    this._followGPS??=this._navigation.settings.followGPS;
    const panel=document.createElement('details');panel.className='nav-panel';panel.innerHTML=`<summary>Places, routes & trips</summary><div class="nav-controls">
      <label>Map click <select id="navPick"><option value="inspect">Inspect coordinates</option><option value="marker">Add marker</option><option value="waypoint">Add route waypoint</option></select></label>
      <label>Name <input id="navName" maxlength="160" placeholder="Place or trip name"></label><label>Selected latitude <input id="navLat" type="number" step="any" min="-90" max="90"></label><label>Selected longitude <input id="navLon" type="number" step="any" min="-180" max="180"></label><label>Marker color <input id="navColor" type="color" value="#095b9c"></label><button id="navSelectCoordinate">Select coordinates</button><span id="navSelected">Click or touch the map to choose coordinates.</span>
      <div><button id="navAddMarker">Save selected marker</button><button id="navNewMarker">New marker</button><button id="navAddPoint">Add selected waypoint</button><button id="navFollow">Follow GPS</button></div>
      <h4>Saved places</h4><div id="navMarkers"></div><h4>Route waypoints</h4><ol id="navPoints"></ol>
      <div><button id="navCalculate">Calculate road route</button><button id="navClearRoute">Clear route</button></div><p id="navRouteSummary"></p><ol id="navDirections"></ol>
      <h4>Trip / manual journey</h4><label>Departure <input id="navDeparture" type="datetime-local"></label><label>Arrival <input id="navArrival" type="datetime-local"></label>
      <label>Status <select id="navTripStatus"><option value="planned">Planned</option><option value="completed">Completed</option></select></label><label>Notes <textarea id="navNotes" maxlength="4000"></textarea></label><button id="navSaveTrip">Save trip from route endpoints</button><button id="navNewTrip">New trip</button><div id="navTrips"></div>
      <h4>Recorded GPS history</h4><div><button id="navHistory">Load recorded GPS (trip times or last 24h)</button><button id="navHistoryMore">Load more</button><button id="navReplay">Play / pause replay</button><button id="navHistoryTrip">Use history endpoints for trip</button></div><label>Replay speed <select id="navReplaySpeed"><option>1</option><option>10</option><option selected>60</option></select></label><input id="navReplaySeek" type="range" min="0" max="0" value="0"><p id="navHistoryStatus"></p><h4>USA data coverage</h4><label>Routing pack regions (us = full USA; or comma-separated state slugs)<input id="navRegions" value="us"></label><p>Road routing needs a prepared regional pack on the Tiny. USA is the default; position data does not select your region.</p><h4>Default map position (used without GPS)</h4><label>Latitude <input id="navDefaultLat" type="number" step="any" min="-90" max="90"></label><label>Longitude <input id="navDefaultLon" type="number" step="any" min="-180" max="180"></label><label>Zoom <input id="navDefaultZoom" type="number" min="2" max="18"></label>
      <label><input id="navUSA" type="checkbox"> Limit map browsing to USA extent (including Alaska/Hawaii)</label><div><button id="navDefaultSave">Save default position</button><button id="navDefaultHere">Use current map view</button></div>
      <div><button id="navExport">Export places / trips</button><label>Import JSON <input id="navImport" type="file" accept="application/json,.json"></label><button id="navReload">Reload saved data</button></div><p id="navNotice" role="status"></p></div>`;
    surface.append(panel);
    const style=document.createElement('style');style.textContent=`.map-surface{touch-action:none}.map-empty{display:none!important}.nav-panel{position:absolute;z-index:9;top:78px;left:12px;max-width:min(400px,80%);max-height:65%;overflow:auto;background:#091b2ff5;border:1px solid #315577;border-radius:10px;color:#eef8ff;padding:8px;font:13px system-ui;touch-action:pan-y}.nav-panel summary{cursor:pointer;padding:6px}.nav-controls{display:grid;gap:8px;padding:6px}.nav-controls label{display:grid;gap:4px}.nav-controls button{padding:7px;margin:2px;border:1px solid #407097;border-radius:5px;background:#153855;color:white;cursor:pointer}.nav-controls input,.nav-controls textarea,.nav-controls select{padding:7px;background:#102a42;color:white;border:1px solid #4c6e87;border-radius:4px;min-width:0}.nav-controls h4{margin:10px 0 0}.nav-marker{position:absolute;z-index:4;transform:translate(-50%,-100%);border:1px solid white;background:#095b9c;color:white;border-radius:8px;padding:4px;cursor:pointer;max-width:160px;overflow:hidden;text-overflow:ellipsis}.nav-route-layer{position:absolute;inset:0;z-index:3;pointer-events:none;width:100%;height:100%}`;r.append(style);
    const on=(id,fn)=>r.getElementById(id).onclick=fn;
    on('navFollow',()=>{this._followGPS=!this._followGPS;this._navigation.settings.followGPS=this._followGPS;this._viewCenter=null;this._mapKey=null;this.updateMap();this.saveNavigation();});
    on('navSelectCoordinate',()=>{const point={lat:Number(r.getElementById('navLat').value),lon:Number(r.getElementById('navLon').value)};if(!validPoint(point)||!r.getElementById('navLat').value||!r.getElementById('navLon').value){this.navNotice('Enter valid latitude and longitude');return;}this._selectedPoint=point;this._viewCenter=point;this._followGPS=false;this._mapKey=null;this.updateMap();});
    on('navNewMarker',()=>{this._editingMarkerId=null;r.getElementById('navName').value='';this.drawNavigation();});on('navNewTrip',()=>{this._editingTripId=null;for(const id of ['navName','navDeparture','navArrival','navNotes'])r.getElementById(id).value='';this.drawNavigation();});
    on('navAddMarker',()=>this.addNavPoint('marker'));on('navAddPoint',()=>this.addNavPoint('waypoint'));
    on('navClearRoute',()=>{this._navigation.route={waypoints:[],geometry:[],distance:null,duration:null,instructions:[]};this.saveNavigation();this.drawNavigation();});
    on('navCalculate',()=>this.calculateRoute());on('navSaveTrip',()=>this.saveTrip());
    on('navHistory',()=>this.loadHistory(false));on('navHistoryMore',()=>this.loadHistory(true));on('navReplay',()=>{this._replaying=!this._replaying;this._replayClock=this._history?.[this._replayIndex||0]?.ts;});
    r.getElementById('navReplaySeek').oninput=e=>{this._replayIndex=Number(e.target.value);this._replayClock=this._history?.[this._replayIndex]?.ts;this._replaying=false;this.viewReplay();};
    on('navHistoryTrip',()=>{if(!this._history?.length)return;const first=this._history[0],last=this._history.at(-1);this._navigation.route={waypoints:[{lat:first.lat,lon:first.lon},{lat:last.lat,lon:last.lon}],geometry:[],instructions:[],distance:null,duration:null};r.getElementById('navDeparture').value=localDateTime(first.ts*1000);r.getElementById('navArrival').value=localDateTime(last.ts*1000);r.getElementById('navTripStatus').value='completed';this.saveNavigation();this.drawNavigation();});
    const saveDefault=()=>{const lat=Number(r.getElementById('navDefaultLat').value),lon=Number(r.getElementById('navDefaultLon').value),zoom=Number(r.getElementById('navDefaultZoom').value);if(!validPoint({lat,lon})||!Number.isInteger(zoom)||zoom<2||zoom>18){this.navNotice('Enter valid default coordinates and zoom');return;}Object.assign(this._navigation.settings,{lat,lon,zoom,usaOnly:r.getElementById('navUSA').checked,routingRegions:r.getElementById('navRegions').value.toLowerCase().split(',').map(v=>v.trim()).filter(Boolean)});this._viewCenter=null;this._zoom=zoom;this._mapKey=null;this.saveNavigation();this.updateMap();};
    on('navDefaultSave',saveDefault);on('navDefaultHere',()=>{r.getElementById('navDefaultLat').value=this._mapView.lat;r.getElementById('navDefaultLon').value=this._mapView.lon;r.getElementById('navDefaultZoom').value=this._zoom;saveDefault();});
    on('navReload',()=>{this._navLoaded=false;this.loadNavigation();});
    on('navExport',()=>{const blob=new Blob([JSON.stringify(this._navigation,null,2)],{type:'application/json'}),url=URL.createObjectURL(blob),link=document.createElement('a');link.href=url;link.download='car-navigation.json';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);});
    r.getElementById('navImport').onchange=async e=>{try{const file=e.target.files[0];if(!file||file.size>2000000)throw Error('Choose a navigation JSON file under 2 MB');const data=JSON.parse(await file.text());if(!data.settings||!Array.isArray(data.markers)||!Array.isArray(data.trips)||!data.route)throw Error('Invalid navigation export');await this._navSave;data.revision=this._navigation.revision+1;const response=await this._hass.fetchWithAuth('/api/car_console/navigation',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});if(!response.ok)throw Error((await response.json()).error||'Import rejected');this._navigation=await response.json();navigationBus.dispatchEvent(new CustomEvent('saved',{detail:structuredClone(this._navigation)}));this._mapKey=null;this.updateMap();}catch(error){this.navNotice(error.message);}};
    this.drawNavigation();this.loadNavigation();
  }
  addNavPoint(kind){
    if(this._navLoading){this.navNotice('Loading saved navigation; try again in a moment');return;}
    if(!validPoint(this._selectedPoint)){this.navNotice('Click or touch the map first');return;}
    const point={...this._selectedPoint};if(kind==='marker'){if(this._navigation.markers.length>=500)return;const marker={...point,id:this._editingMarkerId||navID(),name:this.shadowRoot.getElementById('navName').value||'Saved place',color:this.shadowRoot.getElementById('navColor').value};const index=this._navigation.markers.findIndex(m=>m.id===marker.id);if(index>=0)this._navigation.markers[index]=marker;else this._navigation.markers.push(marker);this._editingMarkerId=null;}else{if(this._navigation.route.waypoints.length>=25){this.navNotice('Route supports up to 25 waypoints');return;}this._navigation.route.waypoints.push(point);Object.assign(this._navigation.route,{geometry:[],instructions:[],distance:null,duration:null});}
    this.saveNavigation();this.drawNavigation();
  }
  async calculateRoute(){
    const route=this._navigation.route,identity=JSON.stringify(route.waypoints);if(route.waypoints.length<2){this.navNotice('Choose a start and destination on the map');return;}
    this.navNotice('Calculating with the local road-routing engine…');
    try{const response=await this._hass.fetchWithAuth('/api/car_console/route',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({waypoints:route.waypoints})});const data=await response.json();if(!response.ok)throw Error(data.error||'Route unavailable');if(JSON.stringify(this._navigation.route.waypoints)!==identity)throw Error('Waypoints changed; calculate again');this._navigation.route=data;await this.saveNavigation();this.drawNavigation();}catch(error){this.navNotice(error.message);}
  }
  saveTrip(){
    const r=this.shadowRoot,points=this._navigation.route.waypoints;if(points.length<2){this.navNotice('Choose the departure and arrival points using route waypoints');return;}
    const stamp=id=>{const value=r.getElementById(id).value;return value?new Date(value).toISOString():null;};
    const departure=stamp('navDeparture'),arrival=stamp('navArrival');if(departure&&arrival&&arrival<departure){this.navNotice('Arrival must be after departure');return;}
    const trip={id:this._editingTripId||navID(),name:r.getElementById('navName').value||'Trip',origin:points[0],destination:points.at(-1),waypoints:structuredClone(points),departure,arrival,status:r.getElementById('navTripStatus').value,notes:r.getElementById('navNotes').value};const index=this._navigation.trips.findIndex(t=>t.id===trip.id);if(index>=0)this._navigation.trips[index]=trip;else this._navigation.trips.push(trip);this._editingTripId=null;this.saveNavigation();this.drawNavigation();
  }
  drawNavigation(){
    const r=this.shadowRoot,nav=this._navigation;if(!nav||!r.getElementById('navMarkers'))return;
    text(r,'navAddMarker',this._editingMarkerId?'Update marker':'Save selected marker');text(r,'navSaveTrip',this._editingTripId?'Update trip':'Save trip from route endpoints');
    const button=(label,action)=>{const b=document.createElement('button');b.textContent=label;b.onclick=action;return b;};
    const fill=(id,items,render)=>{const target=r.getElementById(id);target.replaceChildren();for(const [i,item] of items.entries()){const row=document.createElement('div');render(row,item,i);target.append(row);}};
    fill('navMarkers',nav.markers,(row,item,i)=>{const name=document.createElement('span');name.textContent=item.name+' ';row.append(name,button('Edit',()=>{this._editingMarkerId=item.id;this._selectedPoint={lat:item.lat,lon:item.lon};r.getElementById('navName').value=item.name;r.getElementById('navLat').value=item.lat;r.getElementById('navLon').value=item.lon;r.getElementById('navColor').value=item.color||'#095b9c';this.drawNavigation();}),button('View',()=>{this._viewCenter={lat:item.lat,lon:item.lon};this._followGPS=false;this._mapKey=null;this.updateMap();}),button('Route via',()=>{this._selectedPoint={lat:item.lat,lon:item.lon};this.addNavPoint('waypoint');}),button('Delete',()=>{nav.markers.splice(i,1);this.saveNavigation();this.drawNavigation();}));});
    fill('navPoints',nav.route.waypoints,(row,item,i)=>{row.textContent=`${i+1}. ${item.lat.toFixed(5)}, ${item.lon.toFixed(5)} `;row.append(button('↑',()=>{if(i){[nav.route.waypoints[i-1],nav.route.waypoints[i]]=[nav.route.waypoints[i],nav.route.waypoints[i-1]];Object.assign(nav.route,{geometry:[],instructions:[],distance:null,duration:null});this.saveNavigation();this.drawNavigation();}}),button('Remove',()=>{nav.route.waypoints.splice(i,1);Object.assign(nav.route,{geometry:[],instructions:[],distance:null,duration:null});this.saveNavigation();this.drawNavigation();}));});
    fill('navTrips',nav.trips,(row,item,i)=>{row.textContent=`${item.name} · ${item.status} · ${item.departure?new Date(item.departure).toLocaleString():'Departure unset'} → ${item.arrival?new Date(item.arrival).toLocaleString():'Arrival unset'} `;row.append(button('Edit',()=>{this._editingTripId=item.id;r.getElementById('navName').value=item.name;r.getElementById('navDeparture').value=item.departure?localDateTime(new Date(item.departure).getTime()):'';r.getElementById('navArrival').value=item.arrival?localDateTime(new Date(item.arrival).getTime()):'';r.getElementById('navTripStatus').value=item.status;r.getElementById('navNotes').value=item.notes||'';nav.route={waypoints:structuredClone(item.waypoints||[item.origin,item.destination]),geometry:[],instructions:[],distance:null,duration:null};this.drawNavigation();}),button('Load route',()=>{nav.route={waypoints:structuredClone(item.waypoints||[item.origin,item.destination]),geometry:[],instructions:[],distance:null,duration:null};this._viewCenter=item.origin;this._followGPS=false;this._mapKey=null;this.saveNavigation();this.updateMap();}),button('Delete',()=>{nav.trips.splice(i,1);this.saveNavigation();this.drawNavigation();}));});
    const selectedKey=JSON.stringify(this._selectedPoint);if(this._selectedPoint&&selectedKey!==this._selectedFieldKey){for(const [id,key] of [['navLat','lat'],['navLon','lon']])r.getElementById(id).value=this._selectedPoint[key];this._selectedFieldKey=selectedKey;}
    text(r,'navSelected',this._selectedPoint?`Selected: ${this._selectedPoint.lat.toFixed(6)}, ${this._selectedPoint.lon.toFixed(6)}`:'Click or touch the map to choose coordinates.');
    text(r,'navRouteSummary',nav.route.distance!=null?`${(nav.route.distance/1609.344).toFixed(1)} miles · ${Math.round(nav.route.duration/60)} minutes`:`${nav.route.waypoints.length} waypoints · no calculated road route`);
    fill('navDirections',nav.route.instructions,(row,item)=>{row.textContent=item;});
    const settingsKey=JSON.stringify(nav.settings);if(settingsKey!==this._settingsFieldKey){for(const [id,value] of [['navDefaultLat',nav.settings.lat],['navDefaultLon',nav.settings.lon],['navDefaultZoom',nav.settings.zoom]])r.getElementById(id).value=value;r.getElementById('navUSA').checked=nav.settings.usaOnly;r.getElementById('navRegions').value=(nav.settings.routingRegions||['us']).join(',');this._settingsFieldKey=settingsKey;}
    text(r,'navFollow',this._followGPS?'Following GPS':'Follow GPS');
    this.drawLeafletFeatures();
  }
  async loadHistory(more){
    const r=this.shadowRoot;
    try{if(!more||!this._historyRange){const start=r.getElementById('navDeparture').value,end=r.getElementById('navArrival').value;this._historyRange={from:start?new Date(start).getTime()/1000:Date.now()/1000-86400,to:end?new Date(end).getTime()/1000:Date.now()/1000};this._history=[];this._historyCursor=0;}
      const query=new URLSearchParams({...this._historyRange,after:this._historyCursor||0});const response=await this._hass.fetchWithAuth('/api/car_console/gps-history?'+query);const data=await response.json();if(!response.ok)throw Error(data.error||'History unavailable');if(this._history.length+data.items.length>30000)throw Error('Choose a smaller date range (30,000 displayed fixes maximum)');this._history.push(...data.items.filter(point=>validPoint(point)&&Number.isFinite(point.ts)));this._historyCursor=data.next;this._historyMore=data.hasMore;this._replayIndex=0;r.getElementById('navReplaySeek').max=Math.max(0,this._history.length-1);this.navNotice(`${this._history.length} recorded fixes loaded${data.hasMore?' · more available':''}`);this.viewReplay();
    }catch(error){this.navNotice(error.message);}
  }
  viewReplay(){const point=this._history?.[this._replayIndex||0];if(!point)return;this._viewCenter={lat:point.lat,lon:point.lon};this._followGPS=false;this._mapKey=null;text(this.shadowRoot,'navHistoryStatus',`Recorded: ${new Date(point.ts*1000).toLocaleString()} · ${this._replayIndex+1}/${this._history.length}`);this.shadowRoot.getElementById('navReplaySeek').value=this._replayIndex||0;this.updateMap();}
  replayTick(){if(!this._replaying||!this._history?.length)return;this._replayClock=(this._replayClock||this._history[0].ts)+Number(this.shadowRoot.getElementById('navReplaySpeed').value);while((this._replayIndex||0)<this._history.length-1&&this._history[(this._replayIndex||0)+1].ts<=this._replayClock)this._replayIndex=(this._replayIndex||0)+1;if(this._replayIndex===this._history.length-1)this._replaying=false;this.viewReplay();}
  removeLeaflet(){
    this._mapResize?.disconnect();this._mapResize=null;
    if(this._leaflet){this._leaflet.remove();this._leaflet=null;}
    this._leafletLayer=null;this._leafletFeatures=null;this._featureKey=null;this._gpsMarker=null;this._onlineTiles=false;
  }
  leafletPoint(point){const lon=this._navigation?.settings?.usaOnly&&point.lon>=170?point.lon-360:point.lon;return LEAFLET.latLng(point.lat,lon);}
  ensureLeaflet(){
    if(!LEAFLET)return;
    const r=this.shadowRoot,surface=r.getElementById('mapSurface');
    if(!this._leaflet){
      const style=document.createElement('style');style.id='leaflet-style';style.textContent=LEAFLET_CSS+'\n#leafletMap{position:absolute!important;inset:0;width:100%;height:100%;z-index:1;background:#081a2d}.drive-gps-marker{display:grid!important;place-items:center;background:#168cf2;border:4px solid #b9e2ff;border-radius:50%;box-shadow:0 0 12px #168cf2;color:white;font-size:30px}.drive-place-marker{background:transparent;border:0}.drive-place-marker button{transform:translate(-50%,-100%);border:1px solid white;border-radius:7px;padding:4px 7px;color:white;background:#095b9c;white-space:nowrap;max-width:180px;overflow:hidden;text-overflow:ellipsis}.leaflet-control-attribution{font-size:10px!important;max-width:100%;line-height:1.3!important}.leaflet-tile{overflow:hidden}.leaflet-tile img{position:absolute;max-width:none!important}';
      r.insertBefore(style,r.getElementById('custom-style'));
      const host=document.createElement('div');host.id='leafletMap';host.setAttribute('aria-label','Interactive map');surface.prepend(host);
      this._leaflet=LEAFLET.map(host,{zoomControl:false,attributionControl:true,minZoom:2,maxZoom:18,worldCopyJump:true,zoomAnimation:true,fadeAnimation:true,markerZoomAnimation:true});
      this._leaflet.attributionControl.getContainer().id='mapAttribution';this._leaflet.attributionControl.getContainer().hidden=true;
      this._leafletFeatures=LEAFLET.layerGroup().addTo(this._leaflet);
      this._leaflet.on('dragstart zoomstart',()=>{if(this._settingMapView)return;this._mapInteracting=true;this._followGPS=false;this._replaying=false;});
      this._leaflet.on('moveend zoomend',()=>{if(this._settingMapView)return;this._mapInteracting=false;const center=this._leaflet.getCenter().wrap();this._viewCenter={lat:center.lat,lon:center.lng};this._zoom=this._leaflet.getZoom();this._followGPS=false;this._mapKey=null;this.updateMap();});
      this._leaflet.on('click',e=>{const point=e.latlng.wrap();this._selectedPoint={lat:point.lat,lon:point.lng};const mode=r.getElementById('navPick').value;if(mode!=='inspect')this.addNavPoint(mode);this.drawNavigation();});
      this._mapResize=new ResizeObserver(()=>{if(!this._leaflet)return;this._settingMapView=true;try{this._leaflet.invalidateSize({pan:true,animate:false});}finally{this._settingMapView=false;}});this._mapResize.observe(surface);
    }
    if(!this._leafletLayer&&this._hass){
      const owner=this;
      const TileSource=LEAFLET.GridLayer.extend({
        createTile(coords,done){
          const tile=document.createElement('div');tile.dataset.tileKey=`${coords.z}/${coords.x}/${coords.y}`;
          owner.leafletTile(coords,tile).then(()=>{if(!tile._cancelled)done(null,tile);}).catch(()=>{if(!tile._cancelled)done(new Error('Map tile unavailable'),tile);});
          return tile;
        }
      });
      this._leafletLayer=new TileSource({tileSize:256,keepBuffer:2,updateWhenIdle:true,updateWhenZooming:false,minZoom:2,maxZoom:18});
      this._leafletLayer.on('tileunload',event=>{event.tile._cancelled=true;if(event.tile._blobURL)URL.revokeObjectURL(event.tile._blobURL);});
      this._leafletLayer.on('loading load tileload tileerror',()=>this.leafletStatus());
      this._leafletLayer.addTo(this._leaflet);
    }
  }
  async leafletTile(coords,tile){
    const z=coords.z,n=2**z,x=((coords.x%n)+n)%n,y=coords.y;
    let blob,scale=1,online=false;
    try{
      if(this.config.tile_url){let target;
        if(/\{[zxy]\}/.test(this.config.tile_url))target=new URL(this.config.tile_url.replaceAll('{z}',String(z)).replaceAll('{x}',String(x)).replaceAll('{y}',String(y)),location.href);
        else{const base=new URL(this.config.tile_url,location.href);target=new URL(`${z}/${x}/${y}.png`,base.href.endsWith('/')?base.href:base.href+'/');}
        if(!['http:','https:'].includes(target.protocol))throw Error('Invalid tile URL');
        const response=await fetch(target.href,{credentials:'omit',referrerPolicy:'strict-origin-when-cross-origin'});if(!response.ok)throw Error('Local tile missing');blob=await response.blob();
      }else{const response=await this._hass.fetchWithAuth(`/api/car_console/tiles/${z}/${x}/${y}.png`);if(!response.ok)throw Error('Local tile missing');blob=await response.blob();}
    }catch(error){
      if(this.config.online_fallback===false)throw error;
      online=true;scale=2**Math.max(0,z-16);blob=await onlineTile(`https://basemap.nationalmap.gov/arcgis/rest/services/USGSTopo/MapServer/tile/${Math.min(z,16)}/${Math.floor(y/scale)}/${Math.floor(x/scale)}`);
    }
    if(tile._cancelled)return;
    if(!blob.type.startsWith('image/')||blob.size>2097152)throw Error('Invalid map tile');
    const image=new Image();image.alt='';image.style.width=image.style.height=(256*scale)+'px';image.style.left=(-(x%scale)*256)+'px';image.style.top=(-(y%scale)*256)+'px';
    tile._blobURL=URL.createObjectURL(blob);
    await new Promise((resolve,reject)=>{image.onload=resolve;image.onerror=()=>reject(new Error('Invalid map image'));image.src=tile._blobURL;tile.append(image);});
    if(tile._cancelled)return;
    if(online){this._onlineTiles=true;this._leaflet?.attributionControl.addAttribution('Map: <a href="https://www.usgs.gov/programs/national-geospatial-program/national-map" target="_blank" rel="noopener">USGS The National Map</a>');}
  }
  leafletStatus(){
    const r=this.shadowRoot;if(!r.getElementById('mapModeText'))return;
    const valid=!!gpsPosition(this._hass,this.config.gps_entity||DEFAULTS.gps),shown=!!this._leafletLayer?.getContainer()?.querySelector('.leaflet-tile-loaded'),loading=this._leafletLayer?.isLoading();
    const label=gpsLabel(this._hass,this.config.gps_entity||DEFAULTS.gps);
    text(r,'mapModeText',shown?`${this._onlineTiles?'Online USA map':'Local map'} · GPS ${label}${loading?' · loading detail':''}`:`GPS ${label} · ${loading?'loading map':onlineProviderBlocked?'map provider refused access':'map tiles unavailable; planning available'}`);
    text(r,'mapBadge',valid?(shown?'GPS FIX':'GPS · NO MAP'):label==='waiting for fix'?'WAITING FOR FIX':'GPS DISCONNECTED');
    r.querySelector('.map-heading strong').textContent=this.config.title||(this._onlineTiles?'Navigation · USA map':'Navigation');
    const attribution=r.getElementById('mapAttribution');if(attribution)attribution.hidden=!shown;
  }
  drawLeafletFeatures(){
    if(!this._leafletFeatures||!this._navigation)return;
    const nav=this._navigation,replay=this._history?.[this._replayIndex||0];
    const key=JSON.stringify([nav.revision,nav.markers,nav.route.waypoints,nav.route.geometry.length,nav.route.geometry[0],nav.route.geometry.at(-1),this._history?.length,this._replayIndex,this._selectedPoint]);
    if(key!==this._featureKey){
      this._featureKey=key;this._leafletFeatures.clearLayers();this._trackLayers=[];
      const polyline=(points,color)=>LEAFLET.polyline(points.map(point=>this.leafletPoint(point)),{color,weight:color==='#ffbd59'?3:5}).addTo(this._leafletFeatures);
      if(this._history?.length){let group=[];const draw=()=>{if(group.length>1)this._trackLayers.push(polyline(group,'#ffbd59'));};for(const point of this._history){if(group.length&&(point.segment!==group.at(-1).segment||point.break_reason)){draw();group=[];}group.push(point);}draw();}
      if(nav.route.geometry.length)polyline(nav.route.geometry,'#42d5ff');
      const markers=[...(replay?[{lat:replay.lat,lon:replay.lon,name:'Replay'}]:[]),...nav.markers,...nav.route.waypoints.map((point,i)=>({...point,name:String(i+1)})),...(this._selectedPoint?[{...this._selectedPoint,name:'Selected'}]:[])];
      for(const point of markers){const label=document.createElement('button');label.textContent=point.name;label.setAttribute('aria-label',point.name);if(point.color&&CSS.supports('color',point.color))label.style.background=point.color;label.onclick=e=>{LEAFLET.DomEvent.stopPropagation(e);this._selectedPoint={lat:point.lat,lon:point.lon};this.drawNavigation();};LEAFLET.marker(this.leafletPoint(point),{icon:LEAFLET.divIcon({className:'drive-place-marker',html:label,iconSize:[0,0],iconAnchor:[0,0]})}).addTo(this._leafletFeatures);}
    }
    const gps=gpsPosition(this._hass,this.config.gps_entity||DEFAULTS.gps);
    if(gps){const point=this.leafletPoint({lat:gps.latitude,lon:gps.longitude});if(!this._gpsMarker)this._gpsMarker=LEAFLET.marker(point,{interactive:false,zIndexOffset:1000,icon:LEAFLET.divIcon({className:'drive-gps-marker',html:'➤',iconSize:[48,48],iconAnchor:[24,24]})}).addTo(this._leaflet);else this._gpsMarker.setLatLng(point);}
    else if(this._gpsMarker){this._gpsMarker.remove();this._gpsMarker=null;}
  }
  updateMap(){
    if(this._mapInteracting){this.leafletStatus();return;}
    const r=this.shadowRoot,h=this._hass,a=gpsPosition(h,this.config.gps_entity||DEFAULTS.gps),valid=!!a;
    this.loadNavigation();
    const settings=this._navigation?.settings||defaultNavigation().settings;
    let center=this._viewCenter||(valid&&this._followGPS?{lat:a.latitude,lon:a.longitude}:{lat:settings.lat,lon:settings.lon});
    if(settings.usaOnly)center={lat:Math.max(18,Math.min(72,center.lat)),lon:center.lon>=170?center.lon:Math.max(-180,Math.min(-60,center.lon))};
    const lat=center.lat,lon=center.lon;this._mapView={lat,lon};
    text(r,'gpsCoordinates',`${valid?'GPS: '+a.latitude.toFixed(5)+', '+a.longitude.toFixed(5)+' · ':''}View: ${lat.toFixed(5)}, ${lon.toFixed(5)}`);
    text(r,'gpsDetail',valid?'GPS connected · map available':'GPS '+gpsLabel(this._hass,this.config.gps_entity||DEFAULTS.gps)+' · map and trip planning available');
    r.getElementById('mapEmpty').hidden=true;
    if(!valid)r.getElementById('mapCenter').hidden=true;
    r.getElementById('mapCenter').hidden=true;
    this.ensureLeaflet();
    if(this._leaflet){
      this._settingMapView=true;
      try{this._leaflet.setMaxBounds(settings.usaOnly?LEAFLET.latLngBounds([18,-190],[72,-60]):null);const point=this.leafletPoint({lat,lon});if(!this._leaflet._loaded||this._leaflet.getZoom()!==this._zoom||this._leaflet.getCenter().distanceTo(point)>.1)this._leaflet.setView(point,this._zoom,{animate:false});this._leaflet.invalidateSize({pan:true,animate:false});}
      finally{this._settingMapView=false;}
    }
    this.leafletStatus();this.drawNavigation();
  }
  updateMedia(){
    if(this.config?.widget!=='media')return;const r=this.shadowRoot;
    text(r,'trackTitle',track>=0?tracks[track]?.name||this.config.empty_title||'Nothing playing':this.config.empty_title||'Nothing playing');text(r,'trackArtist',audio?.error?'Unable to play this audio file':tracks.length?'Local audio · this browser':this.config.artist_placeholder||'Choose audio files from your device');
    text(r,'elapsed',elapsed(audio?.currentTime||0));text(r,'duration',elapsed(audio?.duration));text(r,'playPause',audio && !audio.paused?'❚❚':'▶');
    const seek=r.getElementById('mediaSeek');if(seek)seek.value=audio && Number.isFinite(audio.duration)?audio.currentTime/audio.duration*100:0;
  }
}
const field=(name,selector)=>({name,selector});
const txt=name=>field(name,{text:{}}),flag=name=>field(name,{boolean:{}}),num=(name,min,max,step=1)=>field(name,{number:{min,max,step,mode:'box'}});
const choice=(name,options)=>field(name,{select:{options}}),entity=name=>field(name,{entity:{}});
const group=(name,title,schema,expanded=false)=>({name,title,type:'expandable',flatten:true,expanded,schema});
const PAGE_DEFAULTS={page_layout:'grid',columns:2,widget_gap:11,content_padding:16,show_header:true,show_rail:true,show_dock:true,show_tabs:true,show_clock:true,show_date:true,show_connections:true,show_theme_button:true,show_assistant_menu:true,show_volume:true,show_climate:true,show_home_button:true,show_back_button:true,compact_dock:true};
const WIDGET_DEFAULTS={clock:{show_date:true,show_footer:true,date_format:'full',hour24:false},media:{show_art:true,show_picker:true,show_seek:true,show_controls:true},map:{online_fallback:true,show_map_controls:true,show_position:true,show_empty:true,zoom:5},vehicleArt:{show_illustration:true},gauges:{show_age:true,show_freshness:true,speed_unit:'mph',speed_decimals:0,rpm_decimals:1,rpm_divisor:'1000',rpm_max:6500},metrics:{temperature_unit:'F',visible_metrics:['coolant','voltage','fuel','load']},link:{new_tab:true},page:PAGE_DEFAULTS};
function editorSchema(kind){
  const fields=[choice('widget',Object.entries(KINDS).map(([value,label])=>({value,label})))];
  if(kind==='page')return [...fields,
    group('page_layout_group','Page layout',[choice('page_layout',['cockpit','grid']),num('columns',1,6),num('widget_gap',0,60),num('content_padding',0,60),num('max_width',0,4096),num('left_width',.1,5,.05),num('map_width',.1,5,.05),num('right_width',.1,5,.05)],true),
    group('page_appearance','Page colors and typography',[choice('theme',['night','day','auto']),txt('background_color'),txt('surface_color'),txt('text_color'),txt('accent'),txt('border_color'),num('border_radius',0,60),num('font_size',10,36)]),
    group('page_header','Header',[flag('show_header'),txt('brand_text'),txt('subtitle'),txt('badge_text'),flag('show_clock'),flag('show_date'),flag('show_connections'),flag('show_theme_button'),flag('show_assistant_menu'),entity('obd_entity'),entity('gps_entity')]),
    group('page_navigation','Tabs and sidebar',[flag('show_tabs'),flag('show_rail'),txt('nav_brand'),field('navigation_items',{object:{}})]),
    group('page_dock','Bottom launcher and controls',[flag('show_dock'),flag('compact_dock'),field('dock_items',{object:{}}),flag('show_volume'),txt('volume_label'),flag('show_climate'),txt('climate_label'),txt('climate_status'),flag('show_home_button'),flag('show_back_button')]),
    group('page_css','Advanced page CSS',[field('custom_css',{text:{multiline:true}})])];
  fields.push(txt('title'));
  if(kind==='html')fields.push(group('html_source','Raw HTML, CSS and JavaScript',[field('html',{text:{multiline:true}}),field('css',{text:{multiline:true}}),field('javascript',{text:{multiline:true}})],true));
  if(kind==='clock')fields.push(group('clock_options','Clock',[flag('hour24'),choice('date_format',['full','long','short']),flag('show_date'),flag('show_footer'),txt('footer_text'),num('clock_size',24,120)],true));
  if(kind==='media')fields.push(group('media_options','Music player',[txt('empty_title'),txt('artist_placeholder'),txt('picker_label'),flag('show_art'),flag('show_picker'),flag('show_seek'),flag('show_controls')],true));
  if(kind==='gauges')fields.push(group('gauge_values','Gauge values and units',[entity('speed_entity'),entity('rpm_entity'),choice('speed_unit',['mph','km/h']),num('speed_max',1,1000),num('rpm_max',100,30000),num('speed_decimals',0,3),num('rpm_decimals',0,3),choice('rpm_divisor',[{value:'1',label:'Raw RPM'},{value:'1000',label:'RPM ×1000'}])],true),group('gauge_appearance','Gauge labels and colors',[txt('speed_label'),txt('rpm_label'),txt('speed_color'),txt('rpm_color'),flag('show_age'),flag('show_freshness')]));
  if(kind==='metrics'){
    fields.push(group('metric_values','Metrics and units',[...['coolant','voltage','fuel','load'].map(key=>entity(key+'_entity')),choice('temperature_unit',['F','C']),field('visible_metrics',{select:{options:['coolant','voltage','fuel','load'],multiple:true}})],true));
    fields.push(group('metric_labels','Metric labels and icons',['coolant','voltage','fuel','load'].flatMap(key=>[txt(key+'_label'),txt(key+'_icon')])));
  }
  if(kind==='map')fields.push(group('map_data','Map and GPS',[entity('gps_entity'),txt('tile_url'),num('zoom',2,18),flag('online_fallback'),flag('show_map_controls'),flag('show_position'),flag('show_empty')],true),group('map_labels','Map text',[txt('empty_title'),field('empty_message',{text:{multiline:true}}),txt('position_label'),txt('route_label'),txt('button_label')]));
  if(kind==='vehicleArt')fields.push(group('vehicle_image','Vehicle artwork',[txt('brand_text'),txt('status_text'),txt('vehicle_color'),txt('image_url'),flag('show_illustration')],true));
  if(kind==='status')fields.push(entity('obd_entity'),entity('gps_entity'));
  if(kind==='value')fields.push(entity('entity'),txt('unit'),num('decimals',0,6));
  if(kind==='text' || kind==='link')fields.push(field('body',{text:{multiline:true}}));
  if(kind==='link')fields.push(txt('url'),txt('button_label'),flag('new_tab'));
  fields.push(group('widget_appearance','Widget size, colors and position',[choice('placement',['left','map','right','extra']),num('column_span',1,6),num('min_height',60,1500),txt('accent'),txt('surface_color'),txt('text_color'),txt('border_color'),num('border_radius',0,60),num('font_size',10,36)]));
  fields.push(group('widget_css','Advanced widget CSS',[field('custom_css',{text:{multiline:true}})]));
  return fields;
}
class DriveConsoleEditor extends HTMLElement {
  setConfig(config){this.config={...config};this.render();}
  set hass(hass){this._hass=hass;this.render();}
  connectedCallback(){this.render();}
  render(){
    if(!this.config || !this._hass)return;
    if(!this.form){this.form=document.createElement('ha-form');this.append(this.form);this.form.addEventListener('value-changed',e=>{this.config={...this.config,...e.detail.value};event(this,'config-changed',{config:this.config});});}
    const labels={widget:'Widget',title:'Heading',page_layout:'Layout',columns:'Grid columns',widget_gap:'Space between widgets',content_padding:'Page padding',max_width:'Maximum content width (0 = full width)',compact_dock:'Compact bottom controls on short displays',left_width:'Cockpit left column ratio',map_width:'Cockpit map column ratio',right_width:'Cockpit right column ratio',accent:'Accent color',hour24:'24-hour clock',placement:'Position in the cockpit layout',tile_url:'Tile folder or XYZ template URL ({z}/{x}/{y})',speed_unit:'Speed display unit',temperature_unit:'Temperature display unit',rpm_divisor:'RPM display scale',custom_css:'Custom CSS',navigation_items:'Custom sidebar items',dock_items:'Custom launcher items',column_span:'Columns occupied in grid layout',image_url:'Vehicle image URL',entity:'Sensor entity'};
    this.form.hass=this._hass;this.form.data={...structuredClone(WIDGET_DEFAULTS[this.config.widget]||{}),...this.config};if(this.config.widget==='gauges')this.form.data.rpm_divisor=String(this.form.data.rpm_divisor);if(this._schemaKind!==this.config.widget){this._schemaKind=this.config.widget;this.form.schema=editorSchema(this.config.widget);}
    this.form.computeLabel=({name})=>labels[name]||name.replaceAll('_',' ').replace(/^./,s=>s.toUpperCase());
    this.form.computeHelper=({name})=>['navigation_items','dock_items'].includes(name)?'Optional list of {label, icon, page} or {label, icon, url, new_tab} items. Leave blank to follow tabs automatically; an empty list hides all items.':name==='custom_css'?'CSS applies only to this widget, or to this page’s header, sidebar and bottom controls.':'';
  }
}
const VIEW_CSS=`
:host{display:block;padding:0;border:0;margin:0;max-width:none;max-height:none;background:var(--bg);color:var(--text)}
:host(:popover-open){position:fixed;inset:0;width:100vw;height:100dvh;overflow:auto}
#shell,.workspace{min-height:100dvh}#shell{height:100dvh}.workspace{height:100dvh}.home-layout{height:100%}.map-holder{min-width:0;min-height:0}
#extra{padding:0 18px;display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:12px}#extra:empty{display:none}#badges{display:flex;flex-wrap:wrap;gap:8px;padding:6px 18px}#badges:empty{display:none}
.layout-tools{display:flex;gap:8px}.layout-tools button{white-space:nowrap}.view-info{padding:20px;color:var(--muted)}.view-info:empty{display:none}
#left>[data-drive-widget=clock]{flex:0 0 36%}#left>[data-drive-widget=media]{flex:1;min-height:0}
#right>[data-drive-widget=vehicleArt]{flex:0 0 35%}#right>[data-drive-widget=gauges]{flex:1;min-height:0}#right>[data-drive-widget=metrics]{flex:0 0 112px}
@media(max-width:1080px){#shell,.workspace{height:auto}.home-layout{height:auto;grid-template-columns:minmax(0,1fr) minmax(0,1.2fr)}#left{grid-column:1;grid-row:1;min-height:500px}#map{grid-column:2;grid-row:1;min-height:500px}#right{grid-column:1/-1;grid-row:2;display:grid;grid-template-columns:1fr 1fr;grid-template-rows:210px 112px}#right>[data-drive-widget=vehicleArt]{grid-column:1;grid-row:1/-1}#right>[data-drive-widget=gauges]{grid-column:2;grid-row:1}#right>[data-drive-widget=metrics]{grid-column:2;grid-row:2}}
@media(max-width:690px){#shell{height:auto;grid-template-columns:1fr}.home-layout{grid-template-columns:1fr}#left{grid-column:1;grid-row:2}#map{grid-column:1;grid-row:1;min-height:450px}#right{grid-column:1;grid-row:3;display:flex;min-height:540px}#right>[data-drive-widget=vehicleArt]{flex:0 0 200px}#right>[data-drive-widget=gauges]{flex:0 0 220px}.layout-tools{gap:4px}}

`;
const PAGE_CSS=`
#shell.without-rail{grid-template-columns:1fr}#navItems{display:flex;flex-direction:column;gap:5px;min-height:0;overflow:auto;flex:1}.rail-brand{flex-shrink:0}
#pageTabs{display:flex;gap:5px;padding:5px 16px;border-bottom:1px solid var(--line);overflow:auto;flex-shrink:0}.page-tab{display:flex;align-items:center;gap:7px;padding:6px 12px;border:1px solid transparent;border-radius:8px;background:transparent;color:var(--muted);white-space:nowrap}.page-tab.active{border-color:var(--cyan);background:#0a2d4b;color:var(--text)}.page-tab .ico{font-size:15px}
#view-home{min-height:540px}.page-grid{height:auto!important;min-height:0!important;align-items:stretch}.page-grid>hui-card{height:100%}.workspace{height:auto;min-height:100dvh}#shell{height:auto;min-height:100dvh}#shell[data-layout=grid] #view-home{flex:1;min-height:0}.rail{height:100dvh}
.floating-edit{position:fixed;right:16px;top:12px;z-index:20;background:var(--surface)}.app-icon ha-icon{--mdc-icon-size:28px}.rail .ico ha-icon{--mdc-icon-size:28px}
@media(min-width:1081px) and (max-height:860px){#shell[data-compact=true] .apps{min-height:72px;padding-block:4px}#shell[data-compact=true] .app-tile{height:62px}#shell[data-compact=true] .app-icon{width:40px;height:40px;border-radius:12px}#shell[data-compact=true] .controlbar{height:40px}}
@media(max-width:690px){#navItems{flex-direction:row;overflow:auto}.rail-brand{display:none}#pageTabs{padding-inline:8px}.page-tab{padding:6px 8px}.page-grid{grid-template-columns:1fr!important}#shell[data-layout=cockpit] .home-layout{grid-template-columns:1fr!important}#shell.without-rail .workspace{padding-bottom:0}}
`;
class DriveConsoleLayout extends HTMLElement {
  constructor(){super();this.attachShadow({mode:'open'});this._cards=[];this.initialRenderComplete=new Promise(resolve=>{this._ready=resolve;});}
  setConfig(config){this.config=config;this._theme=undefined;this.render();}
  set hass(value){this._hass=value;if(this._editor)this._editor.hass=value;this.updateHeader();}
  set lovelace(value){const prev=this._lovelace;this._lovelace=value;if(prev?.editMode!==value?.editMode || prev?.config!==value?.config)this.render();}
  set cards(value){this._cards=value||[];this.render();}
  set badges(value){this._badges=value||[];this.render();}
  get cards(){return this._cards;}
  set index(value){this._index=value;if(this._editor)this._editor.index=value;}
  set narrow(value){this._narrow=value;if(this._editor)this._editor.narrow=value;}
  connectedCallback(){this.render();this._timer=setInterval(()=>this.updateHeader(),1000);}
  disconnectedCallback(){clearInterval(this._timer);clearTimeout(this._notice);if(this.openPopover)this.hidePopover();}
  get openPopover(){return typeof this.showPopover==='function' && this.matches(':popover-open');}
  get settings(){return {...PAGE_DEFAULTS,page_layout:this.config?.path==='drive'?'cockpit':'grid',...currentPage(this.config)};}
  render(){
    if(!ORIGINAL || !this.config)return;
    if(this._lovelace?.editMode){
      if(this.openPopover)this.hidePopover();this.removeAttribute('popover');
      this.style.cssText='display:block;position:relative;min-height:100%;background:var(--lovelace-background,var(--primary-background-color))';
      if(!this._editor || !this.shadowRoot.contains(this._editor)){this.shadowRoot.replaceChildren();this._editor=document.createElement('hui-masonry-view');this.shadowRoot.append(this._editor);}
      for(const card of this._cards)card.style.cssText='';
      Object.assign(this._editor,{hass:this._hass,lovelace:this._lovelace,index:this._index,narrow:this._narrow,cards:this._cards,badges:this._badges||[],isStrategy:false});
      this._editor.setConfig(this.config);this._ready?.();return;
    }
    this._editor=null;this.style.cssText='';const p=this.settings;this._page=p;
    let saved;try{saved=localStorage.getItem('driveconsole-theme:'+this.config.path)||localStorage.getItem('driveconsole-theme');}catch(_){}
    const theme=this._theme||p.theme||saved||'night';this.dataset.theme=theme==='auto'?(matchMedia('(prefers-color-scheme:dark)').matches?'night':'day'):theme;
    this.shadowRoot.innerHTML=`<style>${ORIGINAL.css}\n${VIEW_CSS}\n${PAGE_CSS}</style><div id="shell"><nav class="rail" aria-label="Dashboard pages"><div class="rail-brand"><span class="brand-glyph">◆</span><span class="brand-caption"></span></div><div id="navItems"></div></nav><div class="workspace">${ORIGINAL.header}<div id="pageTabs" role="tablist" aria-label="Dashboard tabs"></div><div id="badges"></div><div id="view-home" class="view active"><div class="home-layout"><div class="left-stack" id="left"></div><div class="map-holder" id="map"></div><div class="right-stack" id="right"></div></div></div><div id="extra"></div>${ORIGINAL.footer}</div></div><div class="toast" id="toast" role="status" hidden></div>`;
    const r=this.shadowRoot,shell=r.getElementById('shell'),body=r.querySelector('.home-layout');
    for(const [name,key] of [['--bg','background_color'],['--text','text_color'],['--cyan','accent'],['--border','border_color']])if(p[key] && CSS.supports('color',p[key]))this.style.setProperty(name,p[key]);
    shell.classList.toggle('without-rail',p.show_rail===false);r.querySelector('.rail').hidden=p.show_rail===false;r.querySelector('.topbar').hidden=p.show_header===false;r.querySelector('.dock').hidden=p.show_dock===false;r.getElementById('pageTabs').hidden=p.show_tabs===false;
    r.getElementById('view-home').style.padding=bounded(p.content_padding,16,0,60)+'px';body.style.gap=bounded(p.widget_gap,11,0,60)+'px';
    for(const part of ['left','right'])r.getElementById(part).style.gap=bounded(p.widget_gap,11,0,60)+'px';
    if(p.max_width){r.getElementById('view-home').style.maxWidth=bounded(p.max_width,1200,320,4096)+'px';r.getElementById('view-home').style.width='100%';r.getElementById('view-home').style.margin='0 auto';}
    const cockpit=p.page_layout==='cockpit';shell.dataset.layout=cockpit?'cockpit':'grid';shell.dataset.compact=String(p.compact_dock!==false);
    if(cockpit){body.style.gridTemplateColumns=`minmax(0,${bounded(p.left_width,.82,.1,5)}fr) minmax(0,${bounded(p.map_width,1.53,.1,5)}fr) minmax(0,${bounded(p.right_width,1.25,.1,5)}fr)`;}
    else{body.replaceChildren();body.style.gridTemplateColumns=`repeat(${bounded(p.columns,2,1,6)},minmax(0,1fr))`;body.classList.add('page-grid');}
    const columns=bounded(p.columns,2,1,6);
    for(const [index,card] of this._cards.entries()){
      const config=this.config.cards?.[index]||{},kind=config.widget;if(config.type==='custom:drive-console-widget' && kind==='page')continue;
      const placement=config.placement||({clock:'left',media:'left',map:'map',vehicleArt:'right',gauges:'right',metrics:'right'}[kind])||'extra';
      const target=cockpit?r.getElementById(['left','map','right','extra'].includes(placement)?placement:'extra'):body;
      card.style.cssText='display:block;min-width:0'+(cockpit && placement==='map'?';height:100%':'');
      if(!cockpit)card.style.gridColumn='span '+bounded(config.column_span||config.view_layout?.column_span,1,1,columns);
      card.dataset.driveWidget=kind||'custom';target.append(card);
      const update=()=>{const widget=card.querySelector('drive-console-widget')||card.shadowRoot?.querySelector('drive-console-widget');if(widget){widget.dataset.theme=this.dataset.theme;widget.setPageStyle(p);}};
      update();requestAnimationFrame(update);
    }
    for(const badge of this._badges||[])r.getElementById('badges').append(badge);
    this.chrome();
    const testing=document.createElement('details');testing.style.cssText='padding:8px 16px;background:#142238;color:white';
    const summary=document.createElement('summary');summary.id='previewStatus';testing.append(summary);
    const instructions=document.createElement('p');instructions.textContent='GPS and vehicle measurements come from the configured devices. Map planning and saved trips remain available when GPS is disconnected.';testing.append(instructions);
    const original=document.createElement('button');original.textContent='Original console';original.onclick=()=>legacy('home');testing.append(original);
    r.getElementById('pageTabs').after(testing);

    const tools=document.createElement('span');tools.className='layout-tools';
    if(this._hass?.user?.is_admin){const edit=document.createElement('button');edit.className='top-icon';edit.textContent='✎';edit.title='Edit this tab';edit.setAttribute('aria-label','Edit dashboard');edit.onclick=()=>this._lovelace?.setEditMode(true);tools.append(edit);if(p.show_header===false){const floating=edit.cloneNode(true);floating.classList.add('floating-edit');floating.onclick=edit.onclick;r.append(floating);}}
    if(p.show_assistant_menu!==false){const menu=document.createElement('button');menu.className='top-icon';menu.textContent='☰';menu.setAttribute('aria-label','Assistant menu');menu.onclick=()=>this.assistantMenu();tools.append(menu);}
    r.querySelector('.system-right').append(tools);
    if(!this._listening){
      this._listening=true;
      r.addEventListener('click',e=>{const button=e.target.closest('button');if(!button)return;if(button.id==='quickTheme'){this._theme=this.dataset.theme==='night'?'day':'night';try{localStorage.setItem('driveconsole-theme:'+this.config.path,this._theme);}catch(_){}this.render();}if(button.id==='backButton')history.back();});
      r.addEventListener('drive-console-screen',e=>this.screen(e.detail.screen));
    }
    r.getElementById('audioVolume')?.addEventListener('input',e=>{player().volume=Number(e.target.value)/100;text(r,'volumeLabel',e.target.value+'%');});
    const custom=document.createElement('style');custom.textContent=String(p.custom_css||'');r.append(custom);
    this.updateHeader();
    if(this.isConnected && typeof this.showPopover==='function'){this.setAttribute('popover','manual');if(!this.openPopover)this.showPopover();}
    requestAnimationFrame(()=>this._ready?.());
  }
  chrome(){
    const r=this.shadowRoot,p=this.settings,views=(this._lovelace?.config.views||[]).filter(v=>!v.subview),current=this.config.path;
    const symbols={drive:'⌂',navigation:'➤',media:'♫',vehicle:'▰',health:'⚙',updates:'↻',settings:'☷'};
    const defaults=views.map((view,index)=>({label:view.title||'Page '+(index+1),icon:view.icon||symbols[view.path]||'◇',page:view.path??String(index)}));
    const items=Array.isArray(p.navigation_items)?p.navigation_items:defaults;
    const add=(holder,item,cls)=>{
      if(!item || typeof item!=='object')return;
      const button=document.createElement('button');button.className=cls;button.title=String(item.label||'Page');
      const glyph=document.createElement('span');glyph.className=cls==='app-tile'?'app-icon '+({drive:'phone',navigation:'maps',media:'music',vehicle:'vehicle',health:'diag',updates:'more'}[item.page]||'more'):'ico';icon(glyph,item.icon||'◇');
      const label=document.createElement('span');label.textContent=String(item.label||'Page');button.append(glyph,label);button.setAttribute('aria-label',String(item.label||'Page'));
      if(item.page===current)button.classList.add('active');
      button.onclick=()=>item.url?go(item.url,item.new_tab!==false):item.message?this.notice(item.message):this.screen(item.page);
      holder.append(button);return button;
    };
    text(r,'topClock','');r.querySelector('.brand-caption').textContent=p.nav_brand||'DRIVE';
    for(const item of items)add(r.getElementById('navItems'),item,'rail-item');
    for(const item of defaults){const tab=add(r.getElementById('pageTabs'),item,'page-tab');tab.setAttribute('role','tab');tab.setAttribute('aria-selected',String(item.page===current));}
    const dock=r.querySelector('.apps');dock.replaceChildren();for(const item of Array.isArray(p.dock_items)?p.dock_items:defaults)add(dock,item,'app-tile');
    r.querySelector('.small-brand').textContent=p.brand_text||'DRIVE CONSOLE';r.querySelector('.top-subtitle').textContent=p.subtitle||this.config.title||'HEAD UNIT';r.querySelector('.ford-pill').textContent=p.badge_text||'FORD • LOCAL';
    const hide=(selector,value)=>r.querySelectorAll(selector).forEach(el=>{el.hidden=value===false;});
    hide('#topClock',p.show_clock);hide('#topDate',p.show_date);hide('.dot-sep',p.show_clock!==false && p.show_date!==false);hide('.system-status',p.show_connections);hide('#quickTheme',p.show_theme_button);hide('.sound-controls',p.show_volume);hide('.climate-placeholder',p.show_climate);hide('.center-controls button[aria-label="Home"]',p.show_home_button);hide('#backButton',p.show_back_button);
    const home=r.querySelector('.center-controls button[data-view="home"]');if(home)home.onclick=()=>this.screen('home');
    r.querySelector('.sound-controls label').textContent=p.volume_label||'MEDIA VOLUME';r.querySelector('.climate-placeholder>span').textContent=p.climate_label||'CLIMATE';r.querySelector('.climate-placeholder strong').textContent=p.climate_status||'NOT CONNECTED';
  }
  assistantMenu(){if(this.openPopover)this.hidePopover();this.removeAttribute('popover');go('/config');}
  notice(message){const el=this.shadowRoot.getElementById('toast');el.textContent=message;el.hidden=false;clearTimeout(this._notice);this._notice=setTimeout(()=>{el.hidden=true;},4500);}
  screen(name){
    const views=this._lovelace?.config.views||[],view=targetView(views,name);
    if(!view){this.notice('That dashboard tab is unavailable. Add it or change the button target in the editor.');return;}
    const path=view.path??String(views.indexOf(view));go('/'+(this._lovelace.urlPath||'drive-console')+'/'+encodeURIComponent(path));
  }
  updateHeader(){
    const r=this.shadowRoot;if(!r.getElementById('topClock'))return;
    const now=new Date();text(r,'topClock',now.toLocaleTimeString(undefined,{hour:'numeric',minute:'2-digit'}));text(r,'topDate',now.toLocaleDateString(undefined,{weekday:'short',month:'short',day:'numeric'}));
    text(r,'previewStatus','System tools · original console');
    const h=this._hass,p=this.settings,obd=h?.connected && h.states?.[p.obd_entity||'binary_sensor.car_obd_connection']?.state==='on',gps=!!gpsPosition(h,p.gps_entity||DEFAULTS.gps);
    text(r,'telemetryState',obd?'OBD2 connected':'OBD2 unavailable');text(r,'gpsState','GPS '+gpsLabel(h,p.gps_entity||DEFAULTS.gps));r.getElementById('telemetryDot').className='led '+(obd?'good':'warning');r.getElementById('gpsDot').className='led '+(gps?'good':'');
  }
}
customElements.define('drive-console-widget',DriveConsoleWidget);
customElements.define('drive-console-widget-editor',DriveConsoleEditor);
customElements.define('drive-console-layout',DriveConsoleLayout);
window.customCards=window.customCards||[];
window.customCards.push({type:'drive-console-widget',name:'Drive Console widget',description:'Original car clock, local music, offline map, truck art and vehicle gauges',preview:true});
export {number,display,temperature,bounded,currentPage,targetView,editorSchema,WIDGET_DEFAULTS,htmlDocument,gpsPosition,gpsLabel,defaultNavigation};
