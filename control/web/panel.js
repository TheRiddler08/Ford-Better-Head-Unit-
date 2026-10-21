"use strict";
const $=id=>document.getElementById(id);
let secret="",current=null,edits={};
const pretty={clock:"Clock & date",media:"Music player",map:"Map & GPS",
  vehicleArt:"Vehicle illustration",gauges:"Speed & RPM",metrics:"OBD quick tiles",
  home:"Home",navigation:"Navigation",vehicle:"Vehicle",diagnostics:"Diagnostics",settings:"Settings"};
function node(tag,cls,text){
  const el=document.createElement(tag);
  if(cls)el.className=cls;
  if(text!==undefined)el.textContent=String(text);
  return el;
}
function input(kind,val,onChange){
  const x=document.createElement("input");
  x.type=kind;
  if(kind==="checkbox")x.checked=!!val;else x.value=String(val);
  x.addEventListener(kind==="checkbox"?"change":"input",()=>onChange(kind==="checkbox"?x.checked:kind==="number"?Number(x.value):x.value));
  return x;
}
function change(path,value){
  edits[path]=value;
  $("pendingEdits").textContent=Object.keys(edits).length+" unsaved field change(s)";
}
async function call(path,payload){
  const options={headers:{"Authorization":"Bearer "+secret,"Accept":"application/json"}};
  if(payload!==undefined){options.method="POST";options.headers["Content-Type"]="application/json";options.body=JSON.stringify(payload);}
  const response=await fetch(path,options);
  const result=await response.json();
  if(!response.ok)throw new Error(result.error || "HTTP "+response.status);
  return result;
}
function renderModules(){
  const container=$("moduleCards");container.replaceChildren();
  const captions={clock:"Time/date header",media:"Local music & playlists",map:"GNSS and offline tiles",
    vehicleArt:"Ford-themed artwork",gauges:"Read-only speed and engine RPM",metrics:"Coolant, voltage, fuel and load"};
  for(const [id,cfg] of Object.entries(current.home)){
    const card=node("div","panel module");
    const head=node("div","module-top");
    head.append(node("h3",null,pretty[id]),input("checkbox",cfg.visible,v=>change("home."+id+".visible",v)));
    card.append(head,node("p",null,captions[id]));
    const controls=node("div","module-bottom");
    controls.append(node("label",null,"Display order"),input("number",cfg.order,v=>change("home."+id+".order",v)));
    card.append(controls);container.append(card);
  }
}
function renderPrefs(){
  const prefs=$("preferences");prefs.replaceChildren();
  for(const [path,title,val,type,options] of [
    ["theme","Appearance",current.theme,"select",["night","day"]],
    ["units","Vehicle units",current.units,"select",["us","metric"]],
    ["labels.vehicle","Vehicle card heading",current.labels.vehicle,"text"],
    ["labels.dashboard","Dashboard brand",current.labels.dashboard,"text"],
    ["telemetry.pollSeconds","Telemetry refresh (seconds)",current.telemetry.pollSeconds,"number"]
  ]){
    const row=node("label","setting");
    row.append(node("strong",null,title));
    let control;
    if(type==="select"){
      control=node("select");
      for(const option of options){const item=node("option",null,option);item.value=option;item.selected=option===val;control.append(item);}
      control.addEventListener("change",()=>change(path,control.value));
    }else control=input(type,val,value=>change(path,value));
    row.append(control);prefs.append(row);
  }
  const nav=$("navPreferences");nav.replaceChildren();
  for(const [key,val] of Object.entries(current.nav)){
    const row=node("label","nav-setting");
    const cb=input("checkbox",val,v=>change("nav."+key,v));
    if(["home","settings"].includes(key)){cb.disabled=true;}
    row.append(cb,node("span",null,pretty[key]||key));
    nav.append(row);
  }
}
async function refresh(){
  const device=$("device").value.trim() || "car-1";
  const qs="?device="+encodeURIComponent(device);
  const [conf,queue]=await Promise.all([call("/api/config"+qs),call("/api/pending"+qs)]);
  current=conf;edits={};
  $("configRevision").textContent="v"+conf.revision;
  $("pendingEdits").textContent="No unsaved changes";
  $("pendingCount").textContent=String(queue.items.length);
  renderModules();renderPrefs();
  const box=$("queueDetails");box.replaceChildren();
  if(!queue.items.length){box.append(node("div",null,"No pending changes. The car is up to date as far as acknowledgments are known."));return;}
  for(const item of queue.items){
    const row=node("div","queue-item");
    row.append(node("span",null,"Revision "+item.revision),node("span",null,item.queued+"/"+item.fragments+" frames queued to network server"),
      node("span",null,item.acked?"ACKNOWLEDGED":"Awaiting car acknowledgment"));
    box.append(row);
  }
}
async function save(){
  if(!current || !Object.keys(edits).length)return;
  try{
    const result=await call("/api/change",{device:$("device").value.trim()||"car-1",baseRevision:current.revision,changes:edits});
    $("pendingEdits").textContent="Revision "+result.revision+" queued in "+result.frames+" radio frame(s)";
    await refresh();
  }catch(error){$("pendingEdits").textContent="Error: "+error.message;}
}
$("connect").addEventListener("click",async()=>{
  secret=$("adminToken").value.trim();
  if(!secret)return;
  try{await refresh();$("loginArea").hidden=true;$("editor").hidden=false;$("adminToken").value="";}
  catch(error){$("authMessage").textContent=error.message;secret="";}
});
$("save").addEventListener("click",save);
$("refresh").addEventListener("click",()=>refresh().catch(err=>$("pendingEdits").textContent=err.message));
$("reset").addEventListener("click",()=>refresh().catch(err=>$("pendingEdits").textContent=err.message));
$("device").addEventListener("change",()=>refresh().catch(err=>$("pendingEdits").textContent=err.message));
