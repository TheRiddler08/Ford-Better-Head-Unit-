/* Native Lovelace widgets using the original local Drive Console artwork/CSS. */
const ORIGINAL = null; // Filled from the original dashboard by the managed installer.
const KINDS = {clock:'Clock',media:'Local music',map:'Offline map',vehicleArt:'Vehicle illustration',gauges:'Driving gauges',metrics:'Vehicle metrics',status:'Connection status'};
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
section{height:100%}.metric-tiles{height:100%}.map-card{height:100%}.clock-card{min-height:180px}.music-card{min-height:300px}.vehicle-art{min-height:190px}.gauge-card{min-height:210px}
.status-card{padding:22px;display:grid;gap:12px;min-height:130px}.status-card p{color:var(--muted)}
`;
class DriveConsoleWidget extends HTMLElement {
  constructor(){super();this.attachShadow({mode:'open'});this._onMedia=()=>this.updateMedia();this._zoom=13;}
  setConfig(config){
    if(!KINDS[config.widget])throw new Error('Choose a Drive Console widget');
    this.config={...config};this.render();
  }
  getCardSize(){return this.config?.widget==='map'?8:this.config?.widget==='media'?5:3;}
  getGridOptions(){return {columns:12,rows:this.getCardSize()};}
  static getConfigElement(){return document.createElement('drive-console-widget-editor');}
  static getStubConfig(){return {type:'custom:drive-console-widget',widget:'gauges',speed_unit:'mph'};}
  set hass(hass){this._hass=hass;this.update();}
  get hass(){return this._hass;}
  connectedCallback(){this._timer=setInterval(()=>{this.clock();this.update();},1000);media.addEventListener('change',this._onMedia);this.clock();this.update();}
  disconnectedCallback(){clearInterval(this._timer);media.removeEventListener('change',this._onMedia);this._ticket=(this._ticket||0)+1;}
  render(){
    if(!this.config || !ORIGINAL)return;
    const kind=this.config.widget;
    this.shadowRoot.innerHTML=`<style>${ORIGINAL.css}\n${EXTRA_CSS}</style>`+(ORIGINAL.widgets[kind]||'<section class="glass status-card"><span class="eyebrow">CAR CONNECTION</span><strong id="connectionTitle"></strong><p id="connectionDetail"></p><button class="subtle-button" id="reports">Reports & GPS history →</button></section>');
    if(this.config.accent && CSS.supports('color',this.config.accent))this.style.setProperty('--cyan',this.config.accent);
    else this.style.removeProperty('--cyan');
    if(this.config.title){const label=this.shadowRoot.querySelector('.eyebrow,.card-caption span,.map-heading strong');if(label)label.textContent=this.config.title;}
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
  clock(){if(!this.config || this.config.widget!=='clock')return;const now=new Date();text(this.shadowRoot,'heroTime',now.toLocaleTimeString(undefined,{hour:'numeric',minute:'2-digit',hour12:this.config.hour24?false:undefined}));text(this.shadowRoot,'heroDate',now.toLocaleDateString(undefined,{weekday:'long',month:'long',day:'numeric',year:'numeric'}));}
  update(){
    if(!this.config || !this.shadowRoot.firstChild)return;
    const h=this._hass,c=this.config,r=this.shadowRoot;
    if(c.widget==='gauges'){
      let speed=number(h,c.speed_entity||DEFAULTS.speed);
      const source=h?.states?.[c.speed_entity||DEFAULTS.speed]?.attributes?.unit_of_measurement;
      const unit=c.speed_unit||'mph';
      if(speed!==null && unit==='mph' && source==='km/h')speed*=.621371192;
      if(speed!==null && unit==='km/h' && source==='mph')speed/= .621371192;
      const rpm=number(h,c.rpm_entity||DEFAULTS.rpm);
      text(r,'speedValue',display(speed));text(r,'speedUnit',unit.toUpperCase());text(r,'rpmValue',display(rpm===null?null:rpm/1000,1));
      for(const [id,value,max] of [['speedRing',speed,unit==='mph'?140:220],['rpmRing',rpm,6500]])r.getElementById(id)?.style.setProperty('--progress',`${value===null?0:Math.max(0,Math.min(1,value/max))*260}deg`);
      text(r,'gaugeFreshness',speed===null && rpm===null?'WAITING':'LIVE');
      const seen=h?.states?.[c.rpm_entity||DEFAULTS.rpm]?.attributes?.recorded_at;
      text(r,'gaugeAge',rpm===null?'—':seen?`${Math.max(0,Math.floor(Date.now()/1000-Number(seen)))}s ago`:'Live');
    }else if(c.widget==='metrics'){
      const coolant=number(h,c.coolant_entity||DEFAULTS.coolant),voltage=number(h,c.voltage_entity||DEFAULTS.voltage),fuel=number(h,c.fuel_entity||DEFAULTS.fuel),load=number(h,c.load_entity||DEFAULTS.load);
      text(r,'coolantValue',coolant===null?'—':display(temperature(coolant,h?.states?.[c.coolant_entity||DEFAULTS.coolant]?.attributes?.unit_of_measurement,c.temperature_unit))+(c.temperature_unit==='C'?'°C':'°F'));
      text(r,'voltageValue',voltage===null?'—':display(voltage,1)+' V');text(r,'fuelValue',fuel===null?'—':display(fuel)+'%');text(r,'loadValue',load===null?'—':display(load)+'%');
    }else if(c.widget==='map')this.updateMap();
    else if(c.widget==='status'){
      text(r,'connectionTitle',h?.connected?'Car Assistant connected':'Waiting for Car Assistant');
      const obd=h?.connected && h.states?.['binary_sensor.car_obd_connection']?.state==='on',gps=h?.connected && h.states?.['binary_sensor.car_gps_fix']?.state==='on';
      text(r,'connectionDetail',`OBD: ${obd?'connected':'unavailable'} · GPS: ${gps?'fix available':'unavailable'}. Measurements are read only.`);
    }
  }
  updateMap(){
    const r=this.shadowRoot,h=this._hass,obj=h?.states?.[this.config.gps_entity||DEFAULTS.gps],a=obj?.attributes||{},lat=a.latitude,lon=a.longitude;
    const valid=h?.connected && obj && !['unknown','unavailable'].includes(obj.state) && Number.isFinite(lat) && Number.isFinite(lon) && Math.abs(lat)<=90 && Math.abs(lon)<=180;
    text(r,'gpsCoordinates',valid?`${lat.toFixed(5)}, ${lon.toFixed(5)}`:'Waiting for GPS fix');
    if(!valid){r.getElementById('mapEmpty').hidden=false;r.getElementById('mapCenter').hidden=true;r.getElementById('mapTiles').replaceChildren();this._mapKey=null;text(r,'mapModeText','Waiting for GPS and local map tiles');text(r,'mapBadge','NO GPS');return;}
    const z=this._zoom,n=2**z,clamped=Math.max(-85.0511,Math.min(85.0511,lat)),phi=clamped*Math.PI/180;
    const x=(lon+180)/360*n,y=(1-Math.log(Math.tan(phi)+1/Math.cos(phi))/Math.PI)/2*n;
    const surface=r.getElementById('mapSurface'),w=surface.clientWidth||500,height=surface.clientHeight||500,key=[z,x.toFixed(5),y.toFixed(5),w,height].join(':');
    if(key===this._mapKey)return;this._mapKey=key;
    const ticket=this._ticket=(this._ticket||0)+1,holder=r.getElementById('mapTiles');holder.replaceChildren();let loaded=0;
    r.getElementById('mapEmpty').hidden=false;r.getElementById('mapCenter').hidden=true;text(r,'mapModeText','Looking for local map tiles');
    let base;
    try{base=new URL(this.config.tile_url||'http://127.0.0.1:8877/tiles/',location.href);if(!['http:','https:'].includes(base.protocol))return;}catch(_){return;}
    for(let dx=-Math.ceil(w/512);dx<=Math.ceil(w/512);dx++)for(let dy=-Math.ceil(height/512);dy<=Math.ceil(height/512);dy++){
      const tx=Math.floor(x)+dx,ty=Math.floor(y)+dy;if(ty<0 || ty>=n)continue;
      const image=new Image();image.alt='';image.style.left=`${w/2+(tx-x)*256}px`;image.style.top=`${height/2+(ty-y)*256}px`;
      image.onload=()=>{if(this._ticket!==ticket)return;loaded++;r.getElementById('mapEmpty').hidden=true;r.getElementById('mapCenter').hidden=false;text(r,'mapModeText','Local map tiles · GPS position');text(r,'mapBadge','GPS FIX');};
      image.onerror=()=>{image.hidden=true;if(this._ticket===ticket && !loaded){text(r,'mapModeText','Offline tiles unavailable');text(r,'mapBadge','NO TILES');}};
      holder.append(image);image.src=new URL(`${z}/${((tx%n)+n)%n}/${ty}.png`,base.href.endsWith('/')?base.href:base.href+'/').href;
    }
  }
  updateMedia(){
    if(this.config?.widget!=='media')return;const r=this.shadowRoot;
    text(r,'trackTitle',track>=0?tracks[track]?.name||'Nothing playing':'Nothing playing');text(r,'trackArtist',audio?.error?'Unable to play this audio file':tracks.length?'Local audio · this browser':'Choose audio files from your device');
    text(r,'elapsed',elapsed(audio?.currentTime||0));text(r,'duration',elapsed(audio?.duration));text(r,'playPause',audio && !audio.paused?'❚❚':'▶');
    const seek=r.getElementById('mediaSeek');if(seek)seek.value=audio && Number.isFinite(audio.duration)?audio.currentTime/audio.duration*100:0;
  }
}
class DriveConsoleEditor extends HTMLElement {
  setConfig(config){this.config={...config};this.render();}
  set hass(hass){this._hass=hass;this.render();}
  connectedCallback(){this.render();}
  render(){
    if(!this.config || !this._hass)return;
    if(!this.form){this.form=document.createElement('ha-form');this.append(this.form);this.form.addEventListener('value-changed',e=>{this.config={...this.config,...e.detail.value};event(this,'config-changed',{config:this.config});});}
    const fields=[{name:'widget',selector:{select:{options:Object.entries(KINDS).map(([value,label])=>({value,label}))}}},{name:'title',selector:{text:{}}},{name:'accent',selector:{text:{}}}];
    const kind=this.config.widget;
    if(kind==='clock')fields.push({name:'hour24',selector:{boolean:{}}});
    if(kind==='gauges')fields.push({name:'speed_entity',selector:{entity:{domain:'sensor'}}},{name:'rpm_entity',selector:{entity:{domain:'sensor'}}},{name:'speed_unit',selector:{select:{options:['mph','km/h']}}});
    if(kind==='metrics')for(const name of ['coolant','voltage','fuel','load'])fields.push({name:name+'_entity',selector:{entity:{domain:'sensor'}}});
    if(kind==='metrics')fields.push({name:'temperature_unit',selector:{select:{options:[{value:'F',label:'Fahrenheit'},{value:'C',label:'Celsius'}]}}});
    if(kind==='map')fields.push({name:'gps_entity',selector:{entity:{domain:'device_tracker'}}},{name:'tile_url',selector:{text:{}}});
    fields.push({name:'placement',selector:{select:{options:['left','map','right','extra']}}});
    const labels={widget:'Widget',title:'Heading',accent:'Accent color (CSS color)',hour24:'24-hour clock',placement:'Position in the driving layout',tile_url:'Local tile folder URL',speed_unit:'Speed display unit',temperature_unit:'Temperature display unit'};
    this.form.hass=this._hass;this.form.data=this.config;this.form.schema=fields;this.form.computeLabel=({name})=>labels[name]||name.replaceAll('_',' ');
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
class DriveConsoleLayout extends HTMLElement {
  constructor(){super();this.attachShadow({mode:'open'});this._cards=[];this._screen='home';this.initialRenderComplete=new Promise(resolve=>{this._ready=resolve;});}
  setConfig(config){this.config=config;this.render();}
  set hass(value){this._hass=value;if(this._editor)this._editor.hass=value;this.updateHeader();}
  set lovelace(value){const prev=this._lovelace;this._lovelace=value;if(prev?.editMode!==value?.editMode || prev?.config!==value?.config)this.render();}
  set cards(value){this._cards=value||[];this.render();}
  set badges(value){this._badges=value||[];this.render();}
  get cards(){return this._cards;}
  set index(value){this._index=value;if(this._editor)this._editor.index=value;}
  set narrow(value){this._narrow=value;if(this._editor)this._editor.narrow=value;}
  connectedCallback(){this.render();this._timer=setInterval(()=>this.updateHeader(),1000);}
  disconnectedCallback(){clearInterval(this._timer);if(this.matches(':popover-open'))this.hidePopover();}
  render(){
    if(!ORIGINAL || !this.config)return;
    const editing=this._lovelace?.editMode;
    if(editing){
      if(this.matches(':popover-open'))this.hidePopover();this.removeAttribute('popover');
      this.style.cssText='display:block;position:relative;min-height:100%;background:var(--lovelace-background,var(--primary-background-color))';
      if(!this._editor || !this.shadowRoot.contains(this._editor)){
        this.shadowRoot.replaceChildren();this._editor=document.createElement('hui-masonry-view');this.shadowRoot.append(this._editor);
      }
      for(const card of this._cards){card.style.cssText='';card.classList.remove('clock-card','music-card','vehicle-art','gauge-card','metric-tiles');}
      Object.assign(this._editor,{hass:this._hass,lovelace:this._lovelace,index:this._index,narrow:this._narrow,cards:this._cards,badges:[],isStrategy:false});
      this._editor.setConfig(this.config);this._ready?.();return;
    }
    this._editor=null;this.style.cssText='';
    let saved;try{saved=localStorage.getItem('driveconsole-theme');}catch(_){}
    const theme=this._theme||saved||'night';this.dataset.theme=theme;
    this.shadowRoot.innerHTML=`<style>${ORIGINAL.css}\n${VIEW_CSS}</style><div id="shell">${ORIGINAL.rail}<div class="workspace">${ORIGINAL.header}<div class="view-info" id="info"></div><div id="badges"></div><div id="view-home" class="view active"><div class="home-layout"><div class="left-stack" id="left"></div><div class="map-holder" id="map"></div><div class="right-stack" id="right"></div></div></div><div id="extra"></div>${ORIGINAL.footer}</div></div><div class="toast" id="toast" role="status" hidden></div>`;
    
    for(const [index,card] of this._cards.entries()){
      const config=this.config.cards?.[index]||{},widget=config.widget,placement=config.placement||({clock:'left',media:'left',map:'map',vehicleArt:'right',gauges:'right',metrics:'right'}[widget])||'extra';
      const target=this.shadowRoot.getElementById(['left','map','right','extra'].includes(placement)?placement:'extra');
      card.style.cssText='display:block;min-width:0'+(placement==='map'?';height:100%':'');card.dataset.driveWidget=widget||'custom';target.append(card);
      const inner=card.querySelector('drive-console-widget')||card.shadowRoot?.querySelector('drive-console-widget');if(inner)inner.dataset.theme=theme;
      // The native card wrapper may finish creating its custom element later.
      customElements.whenDefined('drive-console-widget').then(()=>requestAnimationFrame(()=>{const widget=card.querySelector('drive-console-widget')||card.shadowRoot?.querySelector('drive-console-widget');if(widget)widget.dataset.theme=this.dataset.theme;}));
    }
    for(const badge of this._badges||[])this.shadowRoot.getElementById('badges').append(badge);
    const tools=document.createElement('span');tools.className='layout-tools';
    if(this._hass?.user?.is_admin){const edit=document.createElement('button');edit.className='top-icon';edit.textContent='✎';edit.title='Edit dashboard';edit.setAttribute('aria-label','Edit dashboard');edit.onclick=()=>this._lovelace?.setEditMode(true);tools.append(edit);}
    const assistant=document.createElement('button');assistant.className='top-icon';assistant.textContent='☰';assistant.title='Assistant menu';assistant.setAttribute('aria-label','Assistant menu');assistant.onclick=()=>this.assistantMenu();tools.append(assistant);
    this.shadowRoot.querySelector('.system-right')?.append(tools);
    if(!this._listening){
    this._listening=true;
    this.shadowRoot.addEventListener('click',e=>{
      const button=e.target.closest('button');if(!button)return;
      if(button.dataset.view)this.screen(button.dataset.view);
      if(button.dataset.unavailable)this.notice(button.dataset.unavailable);
      if(button.id==='quickTheme'){this._theme=this.dataset.theme==='night'?'day':'night';try{localStorage.setItem('driveconsole-theme',this._theme);}catch(_){}this.render();}
      if(button.id==='backButton')this.screen('home');
    },{once:false});
    this.shadowRoot.addEventListener('drive-console-screen',e=>this.screen(e.detail.screen));
    }
    this.shadowRoot.getElementById('audioVolume')?.addEventListener('input',e=>{player().volume=Number(e.target.value)/100;text(this.shadowRoot,'volumeLabel',e.target.value+'%');});
    this.screen(this._screen);this.updateHeader();
    if(this.isConnected && typeof this.showPopover==='function'){
      this.setAttribute('popover','manual');if(!this.matches(':popover-open'))this.showPopover();
    }
    requestAnimationFrame(()=>this._ready?.());
  }
  assistantMenu(){
    if(this.matches(':popover-open'))this.hidePopover();this.removeAttribute('popover');
    history.pushState(null,'','/config');window.dispatchEvent(new CustomEvent('location-changed',{detail:{replace:false}}));
  }
  notice(message){const el=this.shadowRoot.getElementById('toast');el.textContent=message;el.hidden=false;clearTimeout(this._notice);this._notice=setTimeout(()=>{el.hidden=true;},4500);}
  screen(name){
    if(name==='diagnostics'){legacy('diagnostics');return;}if(name==='updates'){legacy('updates');return;}if(name==='settings'){if(this._hass?.user?.is_admin)this._lovelace?.setEditMode(true);else this.assistantMenu();return;}
    this._screen=name;
    for(const button of this.shadowRoot.querySelectorAll('.rail-item')){const active=button.dataset.view===name;button.classList.toggle('active',active);button.setAttribute('aria-current',active?'page':'false');}
    const left=this.shadowRoot.getElementById('left'),map=this.shadowRoot.getElementById('map'),right=this.shadowRoot.getElementById('right');
    if(!left || !map || !right)return;
    left.hidden=['navigation','vehicle'].includes(name);map.hidden=['media','vehicle'].includes(name);right.hidden=['navigation','media'].includes(name);
    const grid=this.shadowRoot.querySelector('.home-layout');grid.style.gridTemplateColumns=name==='home'?'':name==='vehicle'?'minmax(0,1fr)':name==='media'?'minmax(240px,500px)':'minmax(0,1fr)';
    const info=this.shadowRoot.getElementById('info');info.textContent=name==='navigation'?'Live GPS position and locally installed tiles. Turn-by-turn routing is not installed.':'';
  }
  updateHeader(){
    const r=this.shadowRoot;if(!r.getElementById('topClock'))return;
    const now=new Date();text(r,'topClock',now.toLocaleTimeString(undefined,{hour:'numeric',minute:'2-digit'}));text(r,'topDate',now.toLocaleDateString(undefined,{weekday:'short',month:'short',day:'numeric'}));
    const h=this._hass,obd=h?.connected && h.states?.['binary_sensor.car_obd_connection']?.state==='on',gps=h?.connected && h.states?.['binary_sensor.car_gps_fix']?.state==='on';
    text(r,'telemetryState',obd?'OBD2 connected':'OBD2 unavailable');text(r,'gpsState',gps?'GPS fix':'GPS unavailable');r.getElementById('telemetryDot').className='led '+(obd?'good':'warning');r.getElementById('gpsDot').className='led '+(gps?'good':'');
  }
}
customElements.define('drive-console-widget',DriveConsoleWidget);
customElements.define('drive-console-widget-editor',DriveConsoleEditor);
customElements.define('drive-console-layout',DriveConsoleLayout);
window.customCards=window.customCards||[];
window.customCards.push({type:'drive-console-widget',name:'Drive Console widget',description:'Original car clock, local music, offline map, truck art and vehicle gauges',preview:true});
export {number,display,temperature};
