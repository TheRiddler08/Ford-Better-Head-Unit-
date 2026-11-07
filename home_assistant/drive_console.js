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
function legacy(view){
  const url=new URL(view==='reports'?'/records':'/cockpit.html', 'http://127.0.0.1:8877');
  if(view!=='reports')url.hash=view;
  window.open(url.href,'_blank','noopener');
}
const EXTRA_CSS=`
:host{display:block;height:100%;min-width:0;color:var(--text);font:14px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
:host([data-theme=day]){color-scheme:light}
section{height:100%}.glass{border-radius:var(--drive-radius,17px);background:var(--drive-card-background,var(--panel-glass));border-color:var(--drive-border,var(--border));color:var(--drive-text,var(--text))}:host{font-size:var(--drive-font-size,14px)}.metric-tiles{height:100%}.map-card{height:100%}.clock-card{min-height:180px}.music-card{min-height:300px}.vehicle-art{min-height:190px}.gauge-card{min-height:210px}
.status-card{padding:22px;display:grid;gap:12px;min-height:130px}.status-card p{color:var(--muted)}
`;
class DriveConsoleWidget extends HTMLElement {
  constructor(){super();this.attachShadow({mode:'open'});this._onMedia=()=>this.updateMedia();this._zoom=13;this._htmlMessage=e=>{if(e.source===this.shadowRoot.getElementById('htmlFrame')?.contentWindow && e.data?.channel==='drive-html-ready')this.update();};}
  setConfig(config){
    if(!KINDS[config.widget])throw new Error('Choose a Drive Console widget');
    this.config={...config};this.dataset.widget=config.widget;this._zoom=bounded(config.zoom,13,2,18);this.render();
  }
  getCardSize(){return this.config?.widget==='page'?2:this.config?.widget==='map'?8:this.config?.widget==='media'?5:3;}
  setPageStyle(value){this.pageStyle=value;applyWidgetStyle(this);}
  getGridOptions(){return {columns:12,rows:this.getCardSize()};}
  static getConfigElement(){return document.createElement('drive-console-widget-editor');}
  static getStubConfig(){return {type:'custom:drive-console-widget',widget:'gauges',speed_unit:'mph'};}
  set hass(hass){this._hass=hass;this.update();}
  get hass(){return this._hass;}
  connectedCallback(){window.addEventListener('message',this._htmlMessage);this._timer=setInterval(()=>{this.clock();this.update();},1000);media.addEventListener('change',this._onMedia);this.clock();this.update();}
  disconnectedCallback(){window.removeEventListener('message',this._htmlMessage);clearInterval(this._timer);media.removeEventListener('change',this._onMedia);this._ticket=(this._ticket||0)+1;}
  render(){
    if(!this.config || !ORIGINAL)return;
    const kind=this.config.widget;
    this._mapKey=null;this._ticket=(this._ticket||0)+1;
    this.shadowRoot.innerHTML=`<style>${ORIGINAL.css}\n${EXTRA_CSS}</style>`+markup(kind);
    applyWidgetStyle(this);
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
    on('mapLocate',()=>{this._mapKey=null;this.update();});
    this.shadowRoot.querySelector('[data-view="navigation"]')?.addEventListener('click',()=>event(this,'drive-console-screen',{screen:'navigation'}));
    on('reports',()=>legacy('reports'));
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
      text(r,'connectionDetail',`OBD: ${obd?'connected':'unavailable'} · GPS: ${gps?'fix available':'unavailable'}. Measurements are read only.`);
    }
  }
  updateMap(){
    const r=this.shadowRoot,h=this._hass,a=gpsPosition(h,this.config.gps_entity||DEFAULTS.gps),lat=a?.latitude,lon=a?.longitude;
    const valid=!!a;
    text(r,'gpsCoordinates',valid?`${lat.toFixed(5)}, ${lon.toFixed(5)}`:'Waiting for GPS fix');
    if(!valid){this._ticket=(this._ticket||0)+1;r.getElementById('mapEmpty').hidden=this.config.show_empty===false;r.querySelector('.map-empty strong').textContent='Waiting for GPS position';r.querySelector('.map-empty p').textContent='The GPS source has not supplied a current position.';r.getElementById('mapCenter').hidden=true;r.getElementById('mapTiles').replaceChildren();this._mapKey=null;text(r,'mapModeText','Waiting for GPS');text(r,'mapBadge','NO GPS');return;}
    r.querySelector('.map-empty strong').textContent=this.config.empty_title||'GPS connected · map tiles unavailable';r.querySelector('.map-empty p').textContent=this.config.empty_message||'Position is available. Install local tiles or enable online map fallback in this card’s editor.';
    const z=this._zoom,n=2**z,clamped=Math.max(-85.0511,Math.min(85.0511,lat)),phi=clamped*Math.PI/180;
    const x=(lon+180)/360*n,y=(1-Math.log(Math.tan(phi)+1/Math.cos(phi))/Math.PI)/2*n;
    const surface=r.getElementById('mapSurface'),w=surface.clientWidth||500,height=surface.clientHeight||500,key=[z,x.toFixed(5),y.toFixed(5),w,height].join(':');
    if(key===this._mapKey && (!this._mapRetryAt || Date.now()<this._mapRetryAt))return;this._mapKey=key;this._mapRetryAt=0;
    const ticket=this._ticket=(this._ticket||0)+1,holder=r.getElementById('mapTiles');holder.replaceChildren();let loaded=0,onlineUsed=false;
    r.getElementById('mapEmpty').hidden=this.config.show_empty===false;r.getElementById('mapCenter').hidden=true;text(r,'mapModeText','Looking for local map tiles');
    let base;
    try{if(this.config.tile_url){base=new URL(this.config.tile_url,location.href);if(!['http:','https:'].includes(base.protocol))return;}}catch(_){return;}
    let attribution=r.getElementById('mapAttribution');if(!attribution){attribution=document.createElement('a');attribution.id='mapAttribution';attribution.href='https://www.openstreetmap.org/copyright';attribution.target='_blank';attribution.rel='noopener';attribution.textContent='© OpenStreetMap contributors';attribution.style.cssText='position:absolute;bottom:2px;right:2px;z-index:5;background:#071321dd;color:white;padding:2px 6px;font-size:11px';surface.append(attribution);}attribution.hidden=true;
    for(let tx=Math.floor(x-w/512);tx<=Math.floor(x+w/512);tx++)for(let ty=Math.floor(y-height/512);ty<=Math.floor(y+height/512);ty++){
      if(ty<0 || ty>=n)continue;
      const image=new Image();image.alt='';image.style.left=`${w/2+(tx-x)*256}px`;image.style.top=`${height/2+(ty-y)*256}px`;
      image.onload=()=>{if(this._ticket!==ticket)return;loaded++;this._mapRetryAt=0;onlineUsed=onlineUsed||!!image.dataset.online;r.getElementById('mapEmpty').hidden=true;r.getElementById('mapCenter').hidden=false;text(r,'mapModeText',onlineUsed?'Online map · GPS connected':'Offline map · GPS connected');text(r,'mapBadge','GPS FIX');if(image.dataset.online)attribution.hidden=false;r.querySelector('.map-heading strong').textContent=this.config.title||(onlineUsed?'Navigation · online map':'Offline navigation');};
      let objectURL;
      const missing=()=>{if(this._ticket!==ticket)return;image.hidden=true;if(!loaded){this._mapRetryAt=Date.now()+15000;text(r,'mapModeText',onlineProviderBlocked?'GPS connected · map provider refused access':'GPS connected · map tiles unavailable');text(r,'mapBadge','GPS · NO MAP');if(onlineProviderBlocked){r.querySelector('.map-empty strong').textContent='GPS connected · online map blocked';r.querySelector('.map-empty p').textContent='The map provider refused access. No further online requests will be made this session. Install local tiles or configure an authorized tile source.';}}};
      const fallback=()=>{if(this._ticket!==ticket)return;if(this.config.online_fallback===false){missing();return;}image.hidden=false;image.dataset.online='true';image.onerror=missing;onlineTile(`https://tile.openstreetmap.org/${z}/${((tx%n)+n)%n}/${ty}.png`).then(blob=>{if(this._ticket!==ticket)return;objectURL=URL.createObjectURL(blob);image.src=objectURL;}).catch(missing);};
      const onload=image.onload;image.onload=()=>{if(objectURL){URL.revokeObjectURL(objectURL);objectURL=null;}onload();};
      image.onerror=()=>{if(objectURL){URL.revokeObjectURL(objectURL);objectURL=null;}fallback();};
      holder.append(image);
      if(base)image.src=new URL(`${z}/${((tx%n)+n)%n}/${ty}.png`,base.href.endsWith('/')?base.href:base.href+'/').href;
      else if(h?.fetchWithAuth){
        h.fetchWithAuth(`/api/car_console/tiles/${z}/${((tx%n)+n)%n}/${ty}.png`).then(async response=>{
          if(!response.ok)throw Error('Local tile unavailable');const blob=await response.blob();if(this._ticket!==ticket)return;objectURL=URL.createObjectURL(blob);image.src=objectURL;
        }).catch(fallback);
      }else fallback();
    }
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
const WIDGET_DEFAULTS={clock:{show_date:true,show_footer:true,date_format:'full',hour24:false},media:{show_art:true,show_picker:true,show_seek:true,show_controls:true},map:{online_fallback:true,show_map_controls:true,show_position:true,show_empty:true,zoom:13},vehicleArt:{show_illustration:true},gauges:{show_age:true,show_freshness:true,speed_unit:'mph',speed_decimals:0,rpm_decimals:1,rpm_divisor:'1000',rpm_max:6500},metrics:{temperature_unit:'F',visible_metrics:['coolant','voltage','fuel','load']},link:{new_tab:true},page:PAGE_DEFAULTS};
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
    const labels={widget:'Widget',title:'Heading',page_layout:'Layout',columns:'Grid columns',widget_gap:'Space between widgets',content_padding:'Page padding',max_width:'Maximum content width (0 = full width)',compact_dock:'Compact bottom controls on short displays',left_width:'Cockpit left column ratio',map_width:'Cockpit map column ratio',right_width:'Cockpit right column ratio',accent:'Accent color',hour24:'24-hour clock',placement:'Position in the cockpit layout',tile_url:'Local tile folder URL',speed_unit:'Speed display unit',temperature_unit:'Temperature display unit',rpm_divisor:'RPM display scale',custom_css:'Custom CSS',navigation_items:'Custom sidebar items',dock_items:'Custom launcher items',column_span:'Columns occupied in grid layout',image_url:'Vehicle image URL',entity:'Sensor entity'};
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
    const instructions=document.createElement('p');instructions.textContent='GPS comes from the normal gpsd service. For a virtual serial GPS, run the external virtual_gps.py source tool on the Tiny. Stop that tool to restore the physical receiver. Test positions use normal recording and map rendering.';testing.append(instructions);
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
    text(r,'previewStatus','GPS source: gpsd · source tools & original console');
    const h=this._hass,p=this.settings,obd=h?.connected && h.states?.[p.obd_entity||'binary_sensor.car_obd_connection']?.state==='on',gps=!!gpsPosition(h,p.gps_entity||DEFAULTS.gps);
    text(r,'telemetryState',obd?'OBD2 connected':'OBD2 unavailable');text(r,'gpsState',gps?'GPS fix':'GPS unavailable');r.getElementById('telemetryDot').className='led '+(obd?'good':'warning');r.getElementById('gpsDot').className='led '+(gps?'good':'');
  }
}
customElements.define('drive-console-widget',DriveConsoleWidget);
customElements.define('drive-console-widget-editor',DriveConsoleEditor);
customElements.define('drive-console-layout',DriveConsoleLayout);
window.customCards=window.customCards||[];
window.customCards.push({type:'drive-console-widget',name:'Drive Console widget',description:'Original car clock, local music, offline map, truck art and vehicle gauges',preview:true});
export {number,display,temperature,bounded,currentPage,targetView,editorSchema,WIDGET_DEFAULTS,htmlDocument,gpsPosition};
