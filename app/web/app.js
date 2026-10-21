/* Read-only local UI: no external libraries, trackers or network calls. */
'use strict';
const $ = (id) => document.getElementById(id);
const state = { units: 'us', latest: {}, sensors: [], page: 'overview', chart: null, fresh: false, lastReading: null, theme: 'night' };
const ICONS = { fuel_zero: '⚠' };
const n = (value, d=0) => value === null || value === undefined || !Number.isFinite(Number(value)) ? '—' : Number(value).toLocaleString(undefined, {maximumFractionDigits:d,minimumFractionDigits:d});
const esc = s => String(s ?? '').replace(/[&<>"']/g, x=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[x]));
function date(ts, short=false){ if(!ts && ts!==0)return '—';const d=new Date(Number(ts)*1000);return Number.isNaN(d.valueOf())?'—':d.toLocaleString(undefined,short?{month:'short',day:'numeric',hour:'numeric',minute:'2-digit'}:{dateStyle:'medium',timeStyle:'short'});}
function ago(ts){if(!ts)return '—';const delta=Math.max(0, Math.floor(Date.now()/1000-Number(ts)));if(delta<10)return 'just now';if(delta<60)return `${delta}s ago`;if(delta<3600)return `${Math.floor(delta/60)}m ago`;if(delta<86400)return `${Math.floor(delta/3600)}h ago`;return `${Math.floor(delta/86400)}d ago`;}
async function get(path){const res=await fetch(path,{cache:'no-store',headers:{'Accept':'application/json'}});let json;try{json=await res.json();}catch{throw new Error(`Server returned HTTP ${res.status}`);}if(!res.ok)throw new Error(json.error||`HTTP ${res.status}`);return json;}
function value(r){return r && r.number!==null && r.number!==undefined ? Number(r.number):null;}
function speed(v){return v===null?null:state.units==='us'?v*0.621371:v;}
function temp(v){return v===null?null:state.units==='us'?(v*9/5+32):v;}
function unitSpeed(){return state.units==='us'?'MPH':'KM/H';}
function ageFor(name){return ago(state.latest[name]?.ts);}
function metric(id, val, digits=0){$(id).textContent=n(val,digits);}
function meter(id,val,max){$(id).style.width=(val===null?'0':Math.min(100,Math.max(0,(val/max)*100))).toFixed(2)+'%';}
function arc(id,val,max){
 const el=$(id);if(!el)return;
 const fraction = val===null || !Number.isFinite(val) ? 0 : Math.max(0, Math.min(1, val/max));
 const arcLength = (575*fraction).toFixed(2);
 el.setAttribute('stroke-dasharray',arcLength+' 767');
}
function renderGauges(){
 const l=state.latest;
 const sp=speed(value(l.SPEED)), rpm=value(l.RPM), ct=temp(value(l.COOLANT_TEMP));
 const volts=value(l.CONTROL_MODULE_VOLTAGE);
 const fuel=value(l.FUEL_LEVEL), engineLoad=value(l.ENGINE_LOAD);
 const intake=temp(value(l.INTAKE_TEMP));
 metric('speedValue',sp,0);metric('rpmValue',rpm,0);
 metric('coolantValue',ct,0);metric('voltageValue',volts,2);
 // Fuel sensor has shown contradictory 0% records. Display raw values, never estimated range.
 metric('fuelValue',fuel,0);
 $('speedUnit').textContent=unitSpeed();
 $('tempUnit').textContent=state.units==='us'?'°F':'°C';
 const speedMax=state.units==='us'?120:200;
 meter('speedBar',sp,speedMax);meter('rpmBar',rpm,7000);
 meter('coolantBar',value(l.COOLANT_TEMP),125);meter('voltageBar',volts,16);
 meter('fuelBar',fuel,100);
 arc('speedArc',sp,speedMax);arc('rpmArc',rpm,7000);
 $('engineLoadValue').textContent=engineLoad===null?'—':n(engineLoad,0)+'%';
 $('intakeTempValue').textContent=intake===null?'—':n(intake,0)+(state.units==='us'?'°F':'°C');
 for(const [name,id] of [['SPEED','speedAge'],['RPM','rpmAge'],['COOLANT_TEMP','coolantAge'],
                         ['CONTROL_MODULE_VOLTAGE','voltageAge'],['FUEL_LEVEL','fuelAge']]){
   $(id).textContent=ageFor(name);
 }
}
function setOnline(data){
 const pill=$('statusPill');const online=!!data.fresh;
 state.fresh=online;state.lastReading=data.lastReading;
 pill.classList.toggle('offline',!online);
 pill.innerHTML='<i></i> '+(online?'RECENT DATA':'DATA STALE');
 $('lastSync').textContent=data.lastReading?'Updated '+ago(data.lastReading):'No readings yet';
 $('heroState').textContent=online?'Recent OBD readings':data.lastReading?'Recorded data · '+ago(data.lastReading):'No vehicle data yet';
 document.body.classList.toggle('data-stale',!online);
 const led=document.querySelector('.band-led');
 if(led)led.classList.toggle('stale',!online);
}
async function pollLive(){
 try{
   const data=await get('/api/live');
   state.latest=data.sensors||{};
   renderGauges();setOnline(data);
   $('connectionError').hidden=true;
 }catch(err){
   state.fresh=false;
   $('connectionError').hidden=false;
   $('connectionError').textContent='Recorder data unavailable: '+err.message+'. Check the database path and permissions.';
   $('statusPill').innerHTML='<i></i> UNAVAILABLE';
   $('statusPill').classList.add('offline');
   $('heroState').textContent='Vehicle data unavailable';
   document.body.classList.add('data-stale');
   const led=document.querySelector('.band-led');
   if(led)led.classList.add('stale');
 }
}
async function refreshSummary(){
 try{
   const sum=await get('/api/summary');
   metric('totalReadings',sum.totalReadings);
   metric('uniqueSensors',sum.sensorCount);
   metric('totalSnapshots',sum.snapshotCount);
   $('lastRecorded').textContent=sum.lastReading?ago(sum.lastReading):'—';
 }catch(err){console.error('Summary unavailable',err);}
}
async function refreshAlerts(){try{const a=await get('/api/alerts');const el=$('alertsContainer');if(!a.length){el.innerHTML='<div class="notice safe"><span>✓</span><span>No suspicious fuel-zero readings identified by the current rule. This does not establish that every sensor is accurate.</span></div>';return;}el.innerHTML=a.map(item=>`<div class="notice"><span>${ICONS[item.code]||'⚠'}</span><span><strong>${esc(item.message)}</strong><br><small>${esc(date(item.ts))}</small></span></div>`).join('');}catch(err){$('alertsContainer').textContent=`Unable to check notices: ${err.message}`;}}
function chartNumbers(sensor,points){return points.map(p=>({ts:Number(p.ts),y:(sensor==='SPEED'?speed(Number(p.number)):sensor==='COOLANT_TEMP'||sensor==='INTAKE_TEMP'||sensor==='AMBIANT_AIR_TEMP'?temp(Number(p.number)):Number(p.number))})).filter(p=>Number.isFinite(p.y)&&Number.isFinite(p.ts));}
function renderChart(){const sensor=$('chartSensor').value,payload=state.chart;const svg=$('historyChart'),msg=$('chartMessage');
 if(!payload||!payload.points||!payload.points.length){svg.innerHTML='';msg.hidden=false;msg.textContent='No recorded readings for this sensor in the selected period.';$('chartLeft').textContent='—';$('chartRight').textContent='—';return;}
 const points=chartNumbers(sensor,payload.points);if(!points.length){svg.innerHTML='';msg.hidden=false;msg.textContent='No numeric data in this period.';return;}msg.hidden=true;
 const xmin=Math.min(...points.map(x=>x.ts)),xmax=Math.max(...points.map(x=>x.ts)),ymin=Math.min(...points.map(x=>x.y)),ymax=Math.max(...points.map(x=>x.y));
 const lo=ymin===ymax?ymin-1:ymin-(ymax-ymin)*.18,hi=ymin===ymax?ymax+1:ymax+(ymax-ymin)*.17;
 const x=(v)=>52+(v-xmin)/(xmax-xmin||1)*584; const y=(v)=>231-(v-lo)/(hi-lo)*185;
 const path=points.map((p,i)=>`${i===0?'M':'L'} ${x(p.ts).toFixed(1)} ${y(p.y).toFixed(1)}`).join(' ');
 const area=path+` L ${x(points[points.length-1].ts).toFixed(1)} 231 L ${x(points[0].ts).toFixed(1)} 231 Z`;
 let lines='';for(let i=0;i<5;i++){const value=hi-(hi-lo)*i/4,cy=46+(185*i/4);lines+=`<line x1="52" x2="640" y1="${cy}" y2="${cy}" stroke="#263b48" stroke-dasharray="3 6"/><text x="43" y="${cy+4}" text-anchor="end" fill="#728d9b" font-size="10">${n(value, value>100?0:1)}</text>`;}
 svg.innerHTML=`<defs><linearGradient id="areaFill" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stop-color="#55e6c1" stop-opacity=".27"/><stop offset="1" stop-color="#55e6c1" stop-opacity="0"/></linearGradient></defs>${lines}<path d="${area}" fill="url(#areaFill)"/><path d="${path}" stroke="#55e6c1" stroke-width="2.6" fill="none" stroke-linecap="round" stroke-linejoin="round"/><circle cx="${x(points[points.length-1].ts)}" cy="${y(points[points.length-1].y)}" r="4" fill="#55e6c1" stroke="#10242a" stroke-width="2"/>`;
 $('chartLeft').textContent=date(xmin,true);$('chartRight').textContent=date(xmax,true);
}
async function refreshChart(){const sensor=$('chartSensor').value,period=$('chartPeriod').value;try{const data=await get(`/api/history?name=${encodeURIComponent(sensor)}&period=${encodeURIComponent(period)}`);if($('chartSensor').value===sensor&&$('chartPeriod').value===period){state.chart=data;renderChart();}}catch(err){$('chartMessage').hidden=false;$('chartMessage').textContent=err.message;}}
async function refreshSensors(){try{state.sensors=await get('/api/sensors');renderSensors();const chartSel=$('chartSensor');const options=new Set(Array.from(chartSel.options).map(o=>o.value));for(const s of state.sensors){if(s.samples>0&&s.average!==null&&!options.has(s.name)){const option=document.createElement('option');option.value=s.name;option.textContent=s.name.replaceAll('_',' ').toLowerCase();chartSel.append(option);}}}catch(err){$('sensorTable').innerHTML=`<tr><td colspan="6">${esc(err.message)}</td></tr>`;}}
function renderSensors(){const q=$('sensorSearch').value.trim().toLowerCase();const filtered=state.sensors.filter(x=>x.name.toLowerCase().includes(q));$('sensorTable').innerHTML=filtered.map(s=>`<tr><td>${esc(s.name)}</td><td>${n(s.samples)}</td><td>${n(s.min,2)}</td><td>${n(s.max,2)}</td><td>${n(s.average,2)}</td><td>${esc(ago(s.lastSeen))}</td></tr>`).join('')||'<tr><td colspan="6">No matching sensors</td></tr>';}
async function refreshTrips(){try{const sessions=await get('/api/trips');$('tripsList').innerHTML=sessions.map((s,i)=>`<article class="session-card"><div class="session-title"><span>SESSION ${String(sessions.length-i).padStart(2,'0')}</span><span class="session-tag">INFERRED</span></div><p class="session-time">${esc(date(s.start))} — ${esc(date(s.end,true))}</p><div class="session-stats"><div><strong>${n(Math.round(s.durationSeconds/60))}<small>MINUTES</small></strong></div><div><strong>${n(speed(s.maxSpeed),0)}<small>MAX ${unitSpeed()}</small></strong></div><div><strong>${n(s.maxRpm,0)}<small>MAX RPM</small></strong></div></div><p class="session-time" style="margin:22px 0 0">Approx. ${n(state.units==='us'?s.distanceKmEstimate*.621371:s.distanceKmEstimate,1)} ${state.units==='us'?'mi':'km'} from sampled speed · ${n(s.speedSamples)} speed readings</p></article>`).join('')||'<div class="panel loading-panel">No speed/RPM sessions saved yet.</div>';}catch(err){$('tripsList').textContent=err.message;}}
async function refreshDiagnostics(){try{const [snap,ev]=await Promise.all([get('/api/snapshots'),get('/api/events')]);$('snapshotsList').innerHTML=snap.map(s=>`<div class="event-row"><div><strong>Scan #${n(s.id)} · ${esc((s.stage||'unknown').replaceAll('_',' '))}</strong><p>${n(s.successes)} / ${n(s.total)} successful responses</p></div><span class="time">${esc(date(s.started,true))}</span></div>`).join('')||'<div class="empty-text">No snapshots yet</div>';$('eventsList').innerHTML=ev.map(e=>`<div class="event-row"><div><strong>${esc((e.name||'unknown').replaceAll('_',' '))}</strong><p>${esc(e.detail)}</p></div><span class="time">${esc(date(e.ts,true))}</span></div>`).join('')||'<div class="empty-text">No events yet</div>';}catch(err){$('snapshotsList').textContent=err.message;$('eventsList').textContent=err.message;}}
function show(page){
 state.page=page;
 document.querySelectorAll('.page').forEach(e=>e.classList.toggle('active',e.id==='page-'+page));
 document.querySelectorAll('.nav-btn').forEach(e=>{
   const selected=e.dataset.page===page;
   e.classList.toggle('selected',selected);
   if(selected)e.setAttribute('aria-current','page');else e.removeAttribute('aria-current');
 });
 $('pageBreadcrumb').textContent=page.toUpperCase();
 if(page==='sensors')refreshSensors();
 else if(page==='trips')refreshTrips();
 else if(page==='diagnostics')refreshDiagnostics();
 else{refreshChart();refreshSummary();refreshAlerts();}
}
function updateClock(){
 const d=new Date();
 $('clockTime').textContent=d.toLocaleTimeString(undefined,{hour:'numeric',minute:'2-digit'});
 $('clockDate').textContent=d.toLocaleDateString(undefined,{weekday:'short',month:'short',day:'numeric'});
}
function applyTheme(theme){
 state.theme=theme==='day'?'day':'night';
 document.documentElement.dataset.theme=state.theme;
 $('themeToggle').textContent=state.theme==='day'?'☾':'☼';
 $('themeToggle').setAttribute('aria-label','Switch to '+(state.theme==='day'?'night':'day')+' theme');
 try{localStorage.setItem('car-dashboard-theme',state.theme);}catch(_err){}
}
function init(){
 document.querySelectorAll('.nav-btn').forEach(x=>x.addEventListener('click',()=>show(x.dataset.page)));
 $('chartSensor').addEventListener('change',refreshChart);
 $('chartPeriod').addEventListener('change',refreshChart);
 $('sensorSearch').addEventListener('input',renderSensors);
 $('unitSelect').addEventListener('change',e=>{
   state.units=e.target.value;
   renderGauges();renderChart();
   if(state.page==='trips')refreshTrips();
 });
 $('refreshBtn').addEventListener('click',()=>{pollLive();refreshSummary();refreshChart();refreshAlerts();});
 $('themeToggle').addEventListener('click',()=>applyTheme(state.theme==='day'?'night':'day'));
 $('fullscreenBtn').addEventListener('click',async()=>{
   try{
     if(document.fullscreenElement)await document.exitFullscreen();
     else if(document.documentElement.requestFullscreen)await document.documentElement.requestFullscreen();
   }catch(err){console.info('Fullscreen unavailable',err);}
 });
 try{applyTheme(localStorage.getItem('car-dashboard-theme')||'night');}
 catch(_err){applyTheme('night');}
 updateClock();setInterval(updateClock,15000);
 pollLive();refreshSummary();refreshChart();refreshAlerts();
 setInterval(pollLive,3000);
 setInterval(()=>{if(state.page==='overview'){refreshSummary();refreshAlerts();refreshChart();}},60000);
}
let initialKioskBuild = null;
async function checkForKioskUpdate(){
  try {
    const build = await get('/api/build');
    if(!build.revision || build.revision === 'not-a-git-checkout')return;
    if(initialKioskBuild !== null && build.revision !== initialKioskBuild){
      // The updater has installed a new revision; refresh the full-screen view.
      window.location.reload();
      return;
    }
    initialKioskBuild = build.revision;
  }catch(err){
    // Dashboard service may be restarting during updates or the car booting.
    console.info('Version check unavailable:',err.message);
  }
}
init();
checkForKioskUpdate();
setInterval(checkForKioskUpdate,30000);
