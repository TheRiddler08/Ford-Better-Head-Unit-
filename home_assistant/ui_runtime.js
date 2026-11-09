/* The loaded module identifies itself independently of Git and the HA image. */
const UI_BUILD='__DRIVE_CONSOLE_BUILD__';
const uiParticipants=window.__driveConsoleParticipants||(window.__driveConsoleParticipants=new Set());
let uiCheck;
function reloadDashboard(message){
  // A first upgrade can still be running older element classes which do not
  // register participants. Inspect existing HA shadow roots on explicit reload.
  const cards=new Set(uiParticipants),roots=[document];
  while(roots.length){const root=roots.pop();for(const element of root.querySelectorAll('*')){if(element.matches('drive-console-layout,drive-console-widget,drive-console-service'))cards.add(element);if(element.shadowRoot)roots.push(element.shadowRoot);}}
  const editing=[...cards].some(card=>card._draft||card._lovelace?.editMode||card.shadowRoot?.querySelector('video')?.paused===false||card.shadowRoot?.querySelector('#playPause')?.textContent==='❚❚')||window.__driveConsoleAudio?.paused===false;
  if(editing){message.textContent='Save your changes and stop playback before reloading.';return;}
  location.reload();
}
function updateNotice(){
  if(typeof document==='undefined'||document.getElementById('drive-console-update-notice'))return;
  const box=document.createElement('aside');box.id='drive-console-update-notice';box.setAttribute('role','status');
  box.style.cssText='position:fixed;inset:auto 16px 16px auto;margin:0;z-index:10000;padding:12px 16px;background:#20262c;color:#f4f5f6;border:1px solid #64717d;border-radius:8px;font:14px system-ui;max-width:min(360px,calc(100vw - 32px));box-shadow:0 4px 12px #0003';
  const text=document.createElement('span');text.textContent='Dashboard update ready. Save any changes, then reload.';
  const button=document.createElement('button');button.textContent='Reload dashboard';button.style.cssText='margin-left:12px;padding:8px 12px;border:1px solid #8996a2;border-radius:5px;background:transparent;color:inherit;font:inherit;cursor:pointer';button.onclick=()=>reloadDashboard(text);box.append(text,button);document.body.append(box);if(box.showPopover){box.setAttribute('popover','manual');box.showPopover();}
}
if(typeof document!=='undefined'){
  if(customElements.get?.('drive-console-layout')&&window.__driveConsoleBuild!==UI_BUILD)updateNotice();
  else window.__driveConsoleBuild=UI_BUILD;
}
async function dashboardVersion(hass){
  if(!hass?.connected||!hass.fetchWithAuth)return null;
  if(uiCheck&&Date.now()-uiCheck.at<30000)return uiCheck.promise;
  const controller=new AbortController();let timer;
  const timeout=new Promise(resolve=>{timer=setTimeout(()=>{controller.abort();resolve(null);},5000);});
  const read=(async()=>{try{const response=await hass.fetchWithAuth('/api/car_console/ui',{signal:controller.signal});if(!response.ok)return null;const data=await response.json();return data&&/^[0-9a-f]{12}$/.test(data.build)?data:null;}catch(_){return null;}})();
  const promise=Promise.race([read,timeout]).finally(()=>clearTimeout(timer));
  uiCheck={at:Date.now(),promise};return promise;
}
