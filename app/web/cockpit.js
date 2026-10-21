/* Ford Better Head Unit — self-contained UI; data remains local. */
"use strict";
const $ = id => document.getElementById(id);
const state = {
  view: "home", priorView: "home", units: "us", theme: "night",
  live: {}, freshness: null, sensors: [], summary: null, gps: null,
  tilesZoom: 13, currentTrack: -1, tracks: [], currentUrl: null,
  build: null, mapTicket: 0, toastTimer: null
};
const nf = (v, digits=0) => v === null || v === undefined || !Number.isFinite(Number(v)) ?
  "—" : Number(v).toLocaleString(undefined,{maximumFractionDigits:digits});
const value = x => x && x.number !== null && x.number !== undefined &&
  Number.isFinite(Number(x.number)) ? Number(x.number) : null;
const esc = value => String(value ?? "").replace(/[&<>"']/g,
  c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const spd = v => v === null ? null : state.units === "us" ? v * .621371 : v;
const degrees = v => v === null ? null : state.units === "us" ? v * 9/5 + 32 : v;
const datetime = secs => Number.isFinite(Number(secs)) && secs != null ?
  new Date(Number(secs)*1000).toLocaleString(undefined,{month:"short",day:"numeric",hour:"numeric",minute:"2-digit"}) : "—";
const elapsed = secs => {
  if(secs == null || !Number.isFinite(Number(secs)))return "—";
  const diff=Math.max(0,Math.floor(Date.now()/1000 - Number(secs)));
  return diff<5?"just now":diff<60?diff+"s ago":diff<3600?Math.floor(diff/60)+"m ago":diff<86400?Math.floor(diff/3600)+"h ago":Math.floor(diff/86400)+"d ago";
};
const formatClock = secs => Number.isFinite(secs) ? Math.floor(secs/60)+":"+String(Math.floor(secs%60)).padStart(2,"0") : "0:00";
async function api(path) {
  const controller=new AbortController(); const timeout=setTimeout(()=>controller.abort(),3500);
  try{
    const response=await fetch(path,{cache:"no-store",signal:controller.signal,headers:{"Accept":"application/json"}});
    const payload=await response.json();
    if(!response.ok)throw new Error(payload.error || "HTTP "+response.status);
    return payload;
  }finally{clearTimeout(timeout);}
}
function notice(message){
  const box=$("toast");box.textContent=message;box.hidden=false;
  clearTimeout(state.toastTimer);state.toastTimer=setTimeout(()=>{box.hidden=true;},4500);
}
function clock(){
  const now=new Date(),short=now.toLocaleTimeString(undefined,{hour:"numeric",minute:"2-digit"});
  $("topClock").textContent=short;
  $("heroTime").textContent=short;
  $("topDate").textContent=now.toLocaleDateString(undefined,{weekday:"short",month:"short",day:"numeric"});
  $("heroDate").textContent=now.toLocaleDateString(undefined,{weekday:"long",month:"long",day:"numeric",year:"numeric"});
}
function show(view){
  if(!$("view-"+view))return;
  if(state.view!==view)state.priorView=state.view;
  state.view=view;
  document.querySelectorAll(".view").forEach(element=>element.classList.toggle("active",element.id==="view-"+view));
  document.querySelectorAll(".rail-item").forEach(button=>{
    const selected=button.dataset.view===view;button.classList.toggle("active",selected);
    if(selected)button.setAttribute("aria-current","page");else button.removeAttribute("aria-current");
  });
  if(view==="vehicle")refreshVehicle();
  if(view==="diagnostics")refreshDiagnostics();
  if(view==="navigation")renderGPS();
}
function setTheme(theme){
  state.theme=theme==="day"?"day":"night";
  document.documentElement.dataset.theme=state.theme;
  $("quickTheme").textContent=state.theme==="day"?"☀":"☾";
  ["Night","Day"].forEach(label=>$("set"+label).classList.toggle("chosen",state.theme===label.toLowerCase()));
  try{localStorage.setItem("driveconsole-theme",state.theme);}catch(_){}
}
function setUnits(units){
  state.units=units==="metric"?"metric":"us";
  $("setUS").classList.toggle("chosen",state.units==="us");
  $("setMetric").classList.toggle("chosen",state.units==="metric");
  try{localStorage.setItem("driveconsole-units",state.units);}catch(_){}
  renderGauges();
}
function paintRing(id,actual,max){
  const element=$(id);
  const fraction=actual===null?0:Math.max(0,Math.min(1,actual/max));
  element.style.setProperty("--progress",(fraction*260).toFixed(1)+"deg");
}
function renderGauges(){
  const latest=state.live.sensors || {}, isFresh=!!state.live.fresh;
  const speed=spd(value(latest.SPEED)), rpm=value(latest.RPM), coolant=degrees(value(latest.COOLANT_TEMP));
  const voltage=value(latest.CONTROL_MODULE_VOLTAGE),fuel=value(latest.FUEL_LEVEL),load=value(latest.ENGINE_LOAD);
  $("speedValue").textContent=nf(speed);$("rpmValue").textContent=nf(rpm===null?null:rpm/1000,1);
  $("speedUnit").textContent=state.units==="us"?"MPH":"KM/H";
  $("coolantValue").textContent=coolant===null?"—":nf(coolant)+"°"+(state.units==="us"?"F":"C");
  $("voltageValue").textContent=voltage===null?"—":nf(voltage,1)+" V";
  $("fuelValue").textContent=fuel===null?"—":nf(fuel)+"%";
  $("loadValue").textContent=load===null?"—":nf(load)+"%";
  $("vehicleSpeed").textContent=speed===null?"—":nf(speed)+" "+(state.units==="us"?"mph":"km/h");
  $("vehicleRpm").textContent=nf(rpm);
  paintRing("speedRing",speed,state.units==="us"?120:200);
  paintRing("rpmRing",rpm,7000);
  $("gaugeAge").textContent=elapsed(state.live.lastReading);
  $("gaugeFreshness").textContent=isFresh?"RECENT DATA":state.live.lastReading?"STALE DATA":"NO DATA";
  $("telemetryState").textContent=isFresh?"OBD2 readings recent":state.live.lastReading?"OBD2 data stale":"OBD2 unavailable";
  $("telemetryDot").className="led "+(isFresh?"good":state.live.lastReading?"warning":"bad");
}
async function pollOBD(){
  try{state.live=await api("/api/live");renderGauges();}
  catch(error){
    state.live={sensors:{},fresh:false,lastReading:null};
    renderGauges();
  }
}
async function refreshVehicle(){
  try{
    const [sensors,summary]=await Promise.all([api("/api/sensors"),api("/api/summary")]);
    state.sensors=sensors;state.summary=summary;renderVehicleRows();
    $("readingCount").textContent=nf(summary.totalReadings);
    $("sensorCount").textContent=nf(summary.sensorCount);
  }catch(error){$("sensorRows").innerHTML='<tr><td colspan="4">Vehicle records unavailable</td></tr>';}
}
function renderVehicleRows(){
  const filter=$("sensorSearch").value.trim().toLowerCase(),live=state.live.sensors||{};
  const selected=state.sensors.filter(s=>String(s.name).toLowerCase().includes(filter));
  $("sensorRows").innerHTML=selected.slice(0,200).map(s=>{
    const current=live[s.name],reading=current?(current.value || (current.number==null?"—":String(current.number))):"—";
    return "<tr><td>"+esc(s.name)+"</td><td>"+esc(elapsed(s.lastSeen))+"</td><td>"+esc(reading)+"</td><td>"+nf(s.samples)+"</td></tr>";
  }).join("") || '<tr><td colspan="4">No matching sensors</td></tr>';
}
async function refreshDiagnostics(){
  try{
    const [snap,events,alerts]=await Promise.all([api("/api/snapshots"),api("/api/events"),api("/api/alerts")]);
    $("snapshotRows").innerHTML=snap.slice(0,40).map(x=>'<div class="log-row"><div><strong>'+
      esc(x.stage||"Diagnostic scan")+'</strong><span>'+nf(x.successes)+' / '+nf(x.total)+' responses</span></div><span>'+esc(datetime(x.started))+'</span></div>').join("") || "No snapshots recorded";
    $("eventRows").innerHTML=events.slice(0,45).map(x=>'<div class="log-row"><div><strong>'+esc(x.name)+
      '</strong><span>'+esc(x.detail||"")+'</span></div><span>'+esc(datetime(x.ts))+'</span></div>').join("") || "No recorder events";
    $("sensorNotice").textContent=alerts.length?
      alerts.map(x=>x.message).join(" · "):"No sensor-data anomalies detected by the available rules. Not a mechanical diagnosis.";
  }catch(error){$("snapshotRows").textContent="Records unavailable";$("eventRows").textContent="Records unavailable";$("sensorNotice").textContent=error.message;}
}
/* Optional gpsd endpoint; never fabricate map positions or route guidance. */
function validFix(gps){
  return gps && gps.fix===true && Number.isFinite(Number(gps.lat)) &&
    Number.isFinite(Number(gps.lon)) && Math.abs(Number(gps.lat))<=90 && Math.abs(Number(gps.lon))<=180;
}
function renderGPS(){
  const fix=validFix(state.gps);
  $("gpsDot").className="led "+(fix?"good":"warning");
  $("gpsState").textContent=fix?"GPS fix":"GPS unavailable";
  $("navFixTitle").textContent=fix?"GPS position received":"Waiting for GPS";
  const coords=fix?Number(state.gps.lat).toFixed(5)+", "+Number(state.gps.lon).toFixed(5):"No coordinates received";
  $("gpsCoordinates").textContent=coords;
  $("navCoords").textContent=fix?coords+" · "+(state.gps.mode===3?"3D":"2D")+" fix":coords;
  $("gpsDetail").textContent=fix?"Live GNSS coordinates · no route selected":"No navigation route active";
  $("navFixDescription").textContent=fix?
    "Position from your connected GPS receiver. Route guidance is not yet installed.":
    "Connect your USB GNSS receiver and start gpsd to receive a position fix.";
  $("mapCenter").hidden=!fix;
  if(!fix){$("mapEmpty").hidden=false;$("mapModeText").textContent="GPS/map not connected";$("mapBadge").textContent="NO ROUTE";$("mapTiles").replaceChildren();return;}
  renderTileMap();
}
const clamp=(v,min,max)=>Math.max(min,Math.min(max,v));
function mapCoords(lat,lon,z){
  const zoom=2**z;
  const latitude=clamp(lat,-85.05112878,85.05112878)*Math.PI/180;
  return {x:(lon+180)/360*zoom,y:(1-Math.asinh(Math.tan(latitude))/Math.PI)/2*zoom};
}
let mapImageCache=new Map();
function renderTileMap(){
  if(!validFix(state.gps))return;
  const z=state.tilesZoom,xy=mapCoords(Number(state.gps.lat),Number(state.gps.lon),z);
  const wrap=2**z,ele=$("mapTiles"),ticket=++state.mapTicket;
  const width=ele.clientWidth||570,height=ele.clientHeight||480;
  const centerX=xy.x*256,centerY=xy.y*256;
  const startX=Math.floor((centerX-width/2)/256),stopX=Math.floor((centerX+width/2)/256);
  const startY=Math.floor((centerY-height/2)/256),stopY=Math.floor((centerY+height/2)/256);
  ele.replaceChildren();
  let loaded=0,attempted=0;const nodes=[];
  for(let ty=startY;ty<=stopY;ty++){
    if(ty<0||ty>=wrap)continue;
    for(let tx=startX;tx<=stopX;tx++){
      const tileX=((tx%wrap)+wrap)%wrap;attempted++;
      const image=document.createElement("img");
      image.alt="";image.decoding="async";
      image.style.left=(tx*256-centerX+width/2)+"px";
      image.style.top=(ty*256-centerY+height/2)+"px";
      image.onload=()=>{if(state.mapTicket!==ticket)return;loaded++;$("mapEmpty").hidden=true;$("mapModeText").textContent="Local map tiles · GPS position";$("mapBadge").textContent="GPS FIX";};
      image.onerror=()=>{if(state.mapTicket!==ticket)return;if(loaded===0){$("mapEmpty").hidden=false;$("mapModeText").textContent="Offline tiles unavailable";$("mapBadge").textContent="NO ROUTE";}};
      image.src="/tiles/"+z+"/"+tileX+"/"+ty+".png";
      nodes.push(image);
    }
  }
  ele.replaceChildren(...nodes);
  $("mapEmpty").hidden=false;
  $("mapModeText").textContent="Looking for local map tiles";
  $("mapBadge").textContent="GPS FIX";
}
async function pollGPS(){
  try{state.gps=await api("/api/gps");}
  catch(_){state.gps=null;}
  renderGPS();
}
/* Local file playback through the browser; no streaming services or upload. */
const audio=$("audioPlayer");
function trackName(file){return file.name.replace(/\.[^.]+$/,"").replace(/_/g," ");}
function loadFiles(files){
  const audioFiles=Array.from(files).filter(f=>f.type.startsWith("audio/") ||
    /\.(mp3|m4a|ogg|oga|wav|flac|aac|opus)$/i.test(f.name));
  if(!audioFiles.length){notice("Choose supported audio files.");return;}
  for(const file of audioFiles){state.tracks.push({file,name:trackName(file)});}
  $("libraryTitle").textContent=state.tracks.length+" local track"+(state.tracks.length===1?"":"s");
  renderPlaylist();
  if(state.currentTrack<0)selectTrack(0,true);
  else notice(audioFiles.length+" track(s) added");
}
function renderPlaylist(){
  $("playlist").innerHTML=state.tracks.map((t,i)=>'<li class="'+(i===state.currentTrack?"current":"")+'"><button type="button" data-track="'+i+'">'+esc(t.name)+'</button></li>').join("")||"<li>No tracks loaded</li>";
}
function selectTrack(index,autoPlay=false){
  if(!state.tracks.length)return;
  index=(index+state.tracks.length)%state.tracks.length;
  if(state.currentUrl)URL.revokeObjectURL(state.currentUrl);
  state.currentTrack=index;
  const item=state.tracks[index];
  state.currentUrl=URL.createObjectURL(item.file);
  audio.src=state.currentUrl;
  audio.load();
  $("trackTitle").textContent=item.name;
  $("trackArtist").textContent="Local file · "+item.file.name.split(".").at(-1).toUpperCase();
  $("playPause").textContent="▶";renderPlaylist();
  if(autoPlay)audio.play().catch(()=>notice("Tap play to start your audio."));
}
function toggleAudio(){
  if(state.currentTrack<0){notice("Choose local music files first.");return;}
  if(audio.paused)audio.play().catch(()=>notice("Unable to play this audio file."));
  else audio.pause();
}
function syncAudio(){
  $("playPause").textContent=audio.paused?"▶":"Ⅱ";
  $("elapsed").textContent=formatClock(audio.currentTime);
  $("duration").textContent=formatClock(audio.duration);
  const fraction=Number.isFinite(audio.duration)&&audio.duration>0?(audio.currentTime/audio.duration*100):0;
  $("mediaSeek").value=String(fraction);
}
function attach(){
  document.querySelectorAll("[data-view]").forEach(button=>button.addEventListener("click",()=>show(button.dataset.view)));
  document.querySelectorAll("[data-unavailable]").forEach(button=>button.addEventListener("click",()=>notice(button.dataset.unavailable)));
  $("backButton").addEventListener("click",()=>show(state.priorView||"home"));
  $("quickTheme").addEventListener("click",()=>setTheme(state.theme==="night"?"day":"night"));
  $("setNight").addEventListener("click",()=>setTheme("night"));
  $("setDay").addEventListener("click",()=>setTheme("day"));
  $("setUS").addEventListener("click",()=>setUnits("us"));
  $("setMetric").addEventListener("click",()=>setUnits("metric"));
  $("fullscreenButton").addEventListener("click",async()=>{
    try{if(document.fullscreenElement)await document.exitFullscreen();
    else await document.documentElement.requestFullscreen();}catch(_){notice("Fullscreen is controlled by the kiosk browser.");}
  });
  $("sensorSearch").addEventListener("input",renderVehicleRows);
  $("mediaInput").addEventListener("change",event=>{loadFiles(event.target.files);event.target.value="";});
  $("playPause").addEventListener("click",toggleAudio);
  $("prevTrack").addEventListener("click",()=>selectTrack(state.currentTrack-1,true));
  $("nextTrack").addEventListener("click",()=>selectTrack(state.currentTrack+1,true));
  $("audioVolume").addEventListener("input",event=>{audio.volume=Number(event.target.value)/100;$("volumeLabel").textContent=event.target.value+"%";});
  $("mediaSeek").addEventListener("change",event=>{if(Number.isFinite(audio.duration))audio.currentTime=Number(event.target.value)/100*audio.duration;});
  $("playlist").addEventListener("click",event=>{
    const button=event.target.closest("[data-track]");
    if(button)selectTrack(Number(button.dataset.track),true);
  });
  audio.addEventListener("play",syncAudio);audio.addEventListener("pause",syncAudio);
  audio.addEventListener("timeupdate",syncAudio);audio.addEventListener("loadedmetadata",syncAudio);
  audio.addEventListener("ended",()=>selectTrack(state.currentTrack+1,true));
  audio.addEventListener("error",()=>{if(state.currentTrack>=0)notice("Could not decode this audio file.");});
  audio.volume=.65;
  $("mapLocate").addEventListener("click",()=>{if(validFix(state.gps))renderTileMap();else notice("No GPS fix yet. Check gpsd and the receiver.");});
  $("mapZoomIn").addEventListener("click",()=>{state.tilesZoom=clamp(state.tilesZoom+1,2,18);renderGPS();});
  $("mapZoomOut").addEventListener("click",()=>{state.tilesZoom=clamp(state.tilesZoom-1,2,18);renderGPS();});
  window.addEventListener("resize",()=>{if(validFix(state.gps))renderTileMap();});
  window.addEventListener("beforeunload",()=>{if(state.currentUrl)URL.revokeObjectURL(state.currentUrl);});
}
async function watchBuild(){
  try{
    const next=(await api("/api/build")).revision;
    if(!next || next==="not-a-git-checkout")return;
    if(state.build!==null && state.build!==next){location.reload();return;}
    state.build=next;
  }catch(_){}
}
function main(){
  attach();
  try{setTheme(localStorage.getItem("driveconsole-theme")||"night");setUnits(localStorage.getItem("driveconsole-units")||"us");}
  catch(_){setTheme("night");setUnits("us");}
  clock();pollOBD();pollGPS();watchBuild();
  setInterval(clock,15000);
  setInterval(pollOBD,3000);
  setInterval(pollGPS,12000);
  setInterval(watchBuild,30000);
  setInterval(()=>{if(state.view==="vehicle")refreshVehicle();else if(state.view==="diagnostics")refreshDiagnostics();},60000);
}
main();
