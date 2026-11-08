"use strict";
(()=>{
  let baseline=null;
  const paths={tiny:["app/web/cockpit.html","app/web/cockpit.css","app/web/cockpit.js","app/web/index.html","app/web/styles.css","app/web/app.js","app/web/update-watch.js","recorder/obd_full_recorder.py","recorder/gps_recorder.py"],duet:["duet/proxy.py","duet/launch.sh","duet/launcher_lock.py"]};
  const role=document.getElementById("softwareRole"),file=document.getElementById("softwarePath"),source=document.getElementById("softwareSource"),notice=document.getElementById("softwareNotice");
  function choices(){file.replaceChildren(...paths[role.value].map(path=>{const option=document.createElement("option");option.value=path;option.textContent=path;return option;}));baseline=null;source.value="";}
  async function status(){
    const result=await call("/api/software/status?device="+encodeURIComponent($("device").value));
    $("softwareQueue").textContent=result.updates.length?result.updates.map(item=>`${item.role || "receiving"} · ${item.path || item.id} · ${item.phase} · ${item.acknowledged}/${item.total} confirmed fragments · ${item.lastTransport || "waiting"}`).join("\n"):"No software updates queued.";
    $("softwareRadio").textContent=result.radioConfigured?"ChirpStack configured; actual radio delivery is shown by device receipts.":"Radio credentials/modem are not configured. Wi-Fi transfer can operate independently.";
    if(result.radioDiagnostic)$("softwareRadio").textContent+=" Radio test: "+result.radioDiagnostic.phase+" — "+result.radioDiagnostic.message;
    const homeHa=document.getElementById('homeHaStatus');
    if(homeHa){
      const phase=result.homeAssistant?.phase||'unknown';
      const hints={'unconfigured':'Enter the existing broker settings in the private home control.env file.','status-stale':'The bridge heartbeat is stale. Check car-home-ha on the home server.','mqtt-rejected':'The broker rejected the connection. Check its credentials and access rules.','mqtt-retrying':'The bridge is retrying discovery and state publication.','mqtt-disconnected-retrying':'The broker connection is down; reconnection is automatic.'};
      homeHa.textContent='Home Home Assistant: '+phase+'. '+(hints[phase]||'Entities and current state are republished when Home Assistant restarts.');
    }
    const devices=document.getElementById('homeHaDevices');
    if(devices){
      const rows=Object.entries(result.carLinks||{}).map(([name,item])=>{
        const row=document.createElement('p'),report=item.report,updates=item.updates,sync=item.sync;
        const age=report.age===null?'No sensor report yet':`${report.fresh?'Fresh':'Stale'} observations · ${report.age<60?report.age+' seconds':Math.floor(report.age/60)+' minutes'} old · ${report.transport}`;
        row.textContent=`${name.toUpperCase()}: ${age}. Updates: ${updates.phase} · ${updates.progress}% fragments received · ${updates.pending} pending. Data sync: ${sync.phase} · ${sync.pending} pending · ${sync.conflicts} conflicts · ${sync.blocked} blocked.`;
        for(const file of sync.files.filter(file=>file.error)){const detail=document.createElement('small');detail.textContent=` ${file.name}: ${file.error}`;row.append(document.createElement('br'),detail);}
        return row;
      });
      devices.replaceChildren(...rows);
    }
  }
  role.addEventListener("change",choices);file.addEventListener("change",()=>{baseline=null;});
  $("loadSoftware").addEventListener("click",async()=>{
    try{
      baseline=await call("/api/software/source?device="+encodeURIComponent($("device").value)+"&role="+encodeURIComponent(role.value)+"&path="+encodeURIComponent(file.value));
      source.value=baseline.source;notice.textContent="Loaded device copy · "+baseline.base.slice(0,16)+" · "+new Date(baseline.reportedAt*1000).toLocaleString();
    }catch(error){notice.textContent=error.message;}
  });
  $("publishSoftware").addEventListener("click",async()=>{
    if(!baseline){notice.textContent="Load the current device copy first.";return;}
    try{
      const result=await call("/api/software/publish",{device:$("device").value,role:role.value,path:baseline.path,base:baseline.base,source:source.value});
      if(result.phase==="already-current"){notice.textContent="No changes to send; this copy is already current.";return;}
      notice.textContent=`Signed ${result.bytes}-byte delta queued. Reception can continue across Wi-Fi/radio interruptions; installation is tracked separately.`;
      document.dispatchEvent(new Event('software-published'));
      baseline=null;await status();
    }catch(error){notice.textContent=error.message;}
  });
  $("refreshSoftware").addEventListener("click",()=>status().catch(error=>notice.textContent=error.message));
  document.getElementById('refreshHomeHa')?.addEventListener('click',()=>status().catch(error=>document.getElementById('homeHaStatus').textContent='Unable to refresh integration: '+error.message));
  document.addEventListener("panel-connected",()=>status().catch(error=>notice.textContent=error.message));
  document.addEventListener("panel-device",()=>{baseline=null;status().catch(error=>notice.textContent=error.message);});
  setInterval(()=>{if(!$("editor").hidden)status().catch(()=>{});},15000);
  choices();
})();
