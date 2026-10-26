"use strict";
const frame=document.getElementById('customFrame');
let revision=-1, telemetry=null;
function deliver(){if(telemetry)frame.contentWindow.postMessage({channel:'car-telemetry',live:telemetry},'*');}
async function refreshLayout(){
  try{
    const response=await fetch('/api/layout');if(!response.ok)throw Error();
    const data=await response.json();
    if(!data.enabled){location.replace('/cockpit.html');return;}
    if(data.revision!==revision){
      revision=data.revision;
      document.getElementById('layoutName').textContent=data.project.name;
      LayoutRuntime.mount(frame,data.project);
      document.getElementById('layoutStatus').textContent='Layout v'+revision;
    }
  }catch(_){document.getElementById('layoutError').hidden=false;}
}
async function poll(){
  try{const response=await fetch('/api/live');if(!response.ok)throw Error();telemetry=await response.json();}
  catch(_){telemetry={sensors:{},fresh:false};}
  deliver();
}
window.addEventListener('message',event=>{
  if(event.source!==frame.contentWindow || event.data?.channel!=='car-layout')return;
  if(event.data.type==='ready')deliver();
  if(event.data.type==='error')document.getElementById('layoutStatus').textContent='Custom script error — recovery is available';
});
refreshLayout();poll();setInterval(refreshLayout,15000);setInterval(poll,3000);
