"use strict";
(()=>{
const byId=id=>document.getElementById(id);
let project=null, server=null, selection=null, pageIndex=0, undo=[], redo=[], timer=null, dirty=false, loadedDevice=null;
const clone=x=>JSON.parse(JSON.stringify(x));
const device=()=>byId('device').value.trim()||'car-1';
const page=()=>project.pages[pageIndex];
const selected=()=>page().blocks.find(block=>block.id===selection);
const uid=()=>('n'+crypto.getRandomValues(new Uint32Array(2)).join(''));
function el(tag,text,cls){const item=document.createElement(tag);if(text!==undefined)item.textContent=text;if(cls)item.className=cls;return item;}
function status(text){byId('builderStatus').textContent=text;}
function remember(){undo.push(clone(project));if(undo.length>60)undo.shift();redo=[];}
function changed(){dirty=true;status('Unsaved project changes');clearTimeout(timer);timer=setTimeout(preview,220);}
function error(err){status('Error: '+err.message);}
function guard(){return !dirty || confirm('Discard unsaved builder changes? Save a draft first to keep them.');}
function sample(){return {sensors:{SPEED:{number:42,unit:'km/h',ts:Date.now()/1000},RPM:{number:1450,unit:'rpm',ts:Date.now()/1000},COOLANT_TEMP:{number:89,unit:'°C',ts:Date.now()/1000}},fresh:true};}
function sendDemo(){byId('builderPreview').contentWindow.postMessage({channel:'car-telemetry',live:sample(),demo:true},'*');}
function preview(){
  if(!project)return;
  const frame=byId('builderPreview');frame.width=project.width;frame.height=project.height;
  byId('builderCanvas').style.width=project.width+'px';byId('builderCanvas').style.height=project.height+'px';
  LayoutRuntime.mount(frame,project,page().id);
  renderOverlay();zoom();
}
function renderOverlay(){
  const overlay=byId('builderOverlay');overlay.replaceChildren();
  overlay.hidden=project.mode!=='visual'||byId('interactPreview').checked;
  for(const block of page().blocks){
    const box=el('div',undefined,'builder-hit'+(block.id===selection?' selected':''));
    Object.assign(box.style,{left:block.x+'px',top:block.y+'px',width:block.w+'px',height:block.h+'px'});
    box.append(el('span',block.type+' · '+(block.text||block.sensor||'component').slice(0,28)));
    const resize=el('i',undefined,'builder-resize');resize.title='Drag to resize';box.append(resize);
    box.setAttribute('role','button');box.tabIndex=0;box.setAttribute('aria-label','Select '+block.type+' '+block.text);
    box.addEventListener('keydown',e=>{if(e.key==='Enter'){selection=block.id;renderInspector();renderOverlay();}});
    box.addEventListener('pointerdown',e=>{
      e.preventDefault();selection=block.id;renderInspector();renderLayers();
      const start={x:e.clientX,y:e.clientY,bx:block.x,by:block.y,bw:block.w,bh:block.h};const resizing=e.target===resize;let moved=false;
      box.classList.add('selected');box.setPointerCapture(e.pointerId);
      const move=event=>{
        if(!moved){remember();moved=true;}
        const zoom=Number(byId('canvasZoom').value)/100;
        if(resizing){
          block.w=Math.max(24,Math.min(7680,Math.round((start.bw+(event.clientX-start.x)/zoom)/8)*8));
          block.h=Math.max(24,Math.min(7680,Math.round((start.bh+(event.clientY-start.y)/zoom)/8)*8));
          box.style.width=block.w+'px';box.style.height=block.h+'px';return;
        }
        block.x=Math.max(0,Math.min(project.width-20,Math.round((start.bx+(event.clientX-start.x)/zoom)/8)*8));
        block.y=Math.max(0,Math.min(project.height-20,Math.round((start.by+(event.clientY-start.y)/zoom)/8)*8));
        box.style.left=block.x+'px';box.style.top=block.y+'px';
      };
      const end=()=>{box.removeEventListener('pointermove',move);box.removeEventListener('pointerup',end);box.removeEventListener('pointercancel',end);if(moved)changed();renderInspector();renderOverlay();};
      box.addEventListener('pointermove',move);box.addEventListener('pointerup',end);box.addEventListener('pointercancel',end);
    });
    overlay.append(box);
  }
}
function renderLayers(){
  const list=byId('builderLayers');list.replaceChildren();
  [...page().blocks].reverse().forEach(block=>{const button=el('button',block.type+' · '+(block.text||block.sensor||'Component').slice(0,25),block.id===selection?'active':'secondary');button.onclick=()=>{selection=block.id;renderInspector();renderOverlay();renderLayers();};list.append(button);});
}
function renderInspector(){
  const root=byId('builderInspector');root.replaceChildren();const block=selected();
  if(!block){root.append(el('p','Select a component on the canvas or in Layers.'));return;}
  for(const [field,label,type] of [['text',block.type==='html'?'Component HTML':'Text / label','textarea'],['x','Left','number'],['y','Top','number'],['w','Width','number'],['h','Height','number'],['fontSize','Font size','number'],['color','Text color','text'],['background','Background','text'],['sensor','Sensor key (e.g. SPEED)','text'],['src','Image data URL','textarea'],['target','Button target page ID','text']]){
    if(field==='sensor'&&block.type!=='sensor'||field==='src'&&block.type!=='image'||field==='target'&&block.type!=='button')continue;
    const wrap=el('label',label);const input=el(type==='textarea'?'textarea':'input');
    if(type!=='textarea')input.type=type;
    input.value=block[field];input.setAttribute('aria-label',label);
    input.onchange=()=>{remember();block[field]=type==='number'?Number(input.value):input.value;changed();renderLayers();};
    wrap.append(input);root.append(wrap);
  }
  if(block.type==='image'){
    const input=el('input');input.type='file';input.accept='image/png,image/jpeg,image/webp,image/gif';
    input.setAttribute('aria-label','Embed image');input.onchange=async()=>{
      const file=input.files[0];if(!file)return;if(file.size>180000){status('Choose an image smaller than 180 KB.');return;}
      const reader=new FileReader();reader.onload=()=>{remember();block.src=reader.result;changed();renderInspector();};reader.readAsDataURL(file);
    };root.append(el('small','Embed a local PNG/JPEG/WebP/GIF (up to 180 KB).'),input);
  }
}
function renderPages(){
  const list=byId('builderPages');list.replaceChildren();
  project.pages.forEach((p,index)=>{const option=el('option',p.name+' ('+p.id+')');option.value=index;option.selected=index===pageIndex;list.append(option);});
}
function render(){
  pageIndex=Math.min(pageIndex,project.pages.length-1);
  byId('projectName').value=project.name;byId('canvasWidth').value=project.width;byId('canvasHeight').value=project.height;byId('canvasBackground').value=project.background;
  byId('builderMode').value=project.mode;
  for(const field of ['html','css','js'])byId('source'+field).value=project[field];
  byId('sourcehtml').disabled=project.mode==='visual';
  byId('visualHelp').hidden=project.mode!=='visual';
  renderPages();renderLayers();renderInspector();preview();
}
function renderHistory(){
  const select=byId('layoutVersions');select.replaceChildren();
  for(const item of server.history){const option=el('option','v'+item.revision+' · '+item.project.name+(item.enabled?'':' · standard dashboard'));option.value=item.revision;select.append(option);}
  const p=server.published;byId('publishedStatus').textContent='Published v'+p.revision+' · '+(p.enabled?'custom UI':'standard dashboard')+' · '+(server.acknowledgedRevision===p.revision?'saved on car':'awaiting car acknowledgment');
}
async function load(force=false){
  if(!force&&!guard())return;
  const next=await call('/api/layout?device='+encodeURIComponent(device()));
  server=next;project=clone(next.draft);loadedDevice=device();dirty=false;selection=null;pageIndex=0;undo=[];redo=[];
  render();renderHistory();status('Draft loaded. Preview uses sample telemetry.');
}
async function persist(action,version){
  if(!project)return;
  if(loadedDevice!==device()){status('Device changed. Reload its project first.');return;}
  const payload={device:loadedDevice,generation:server.generation,action};
  if(action==='save'||action==='publish')payload.project=project;
  if(version!==undefined)payload.version=version;
  byId('editor').inert=true;
  try{
    const result=await call('/api/layout',payload);server=result;project=clone(result.draft);dirty=false;render();renderHistory();
    status(action==='save'?'Draft saved on home server.':action==='disable'?'Standard dashboard queued for car.':'Layout v'+result.published.revision+' queued for car.');
  }catch(err){error(err);}finally{byId('editor').inert=false;}
}
function add(type){
  if(project.mode!=='visual'){status('Switch to visual mode to add components. Your HTML source is retained.');return;}
  if(page().blocks.length>=80){status('Maximum 80 components per page.');return;}
  remember();const n=page().blocks.length;
  const block={id:uid(),type,text:({heading:'DRIVE CONSOLE',text:'Your text',button:'Go to page',sensor:'SPEED',html:'<b>Custom HTML</b>'})[type]||'',x:32+(n%4)*48,y:32+(n%4)*56,w:type==='heading'?480:280,h:type==='sensor'?180:120,fontSize:type==='heading'?40:28,color:'#edf7ff',background:'#14283f',sensor:'SPEED',src:'',target:project.pages[0].id};
  page().blocks.push(block);selection=block.id;changed();render();
}
function zoom(){const scale=Number(byId('canvasZoom').value)/100;byId('builderCanvas').style.transform='scale('+scale+')';byId('canvasSizer').style.width=project.width*scale+'px';byId('canvasSizer').style.height=project.height*scale+'px';}
byId('loadStarter').onclick=()=>{
  if(!project||!guard())return;
  remember();
  project={name:'My cockpit',mode:'visual',width:1280,height:720,background:'#081321',pages:[{id:'home',name:'Home',blocks:[]}],html:'<h1>My cockpit</h1>',css:'',js:''};
  const add=(type,text,x,y,w,h,fontSize=28,sensor='SPEED')=>project.pages[0].blocks.push({id:uid(),type,text,x,y,w,h,fontSize,color:'#edf7ff',background:'#132a40',sensor,src:'',target:'home'});
  const starter=byId('starterLayout').value;
  if(starter!=='blank'){
    add('heading','DRIVE CONSOLE',32,32,780,96,42);
    add('clock','',860,32,380,96,40);
    if(starter==='telemetry'){
      add('sensor','SPEED',32,164,580,248,54,'SPEED');
      add('sensor','ENGINE RPM',640,164,600,248,54,'RPM');
      add('sensor','COOLANT',32,444,380,200,38,'COOLANT_TEMP');
      add('text','Your road. Your cockpit.',440,444,800,200,32);
    }else add('text','Ready for the next journey',32,200,1208,340,52);
  }
  pageIndex=0;selection=null;changed();render();
};
byId('builderOpen').onclick=()=>load().catch(error);
byId('saveDraft').onclick=()=>persist('save');
byId('publishLayout').onclick=()=>{if(confirm('Publish this layout to the car? Test interactions in Preview first.'))persist('publish');};
byId('disableLayout').onclick=()=>{if(guard())persist('disable');};
byId('restoreLayout').onclick=()=>{const version=Number(byId('layoutVersions').value);if(version&&guard())persist('restore',version);};
byId('reloadLayout').onclick=()=>load().catch(error);
byId('builderPages').onchange=()=>{pageIndex=Number(byId('builderPages').value);selection=null;render();};
byId('addPage').onclick=()=>{if(project.pages.length>=12)return;const name=prompt('Page name');if(!name)return;remember();project.pages.push({id:uid(),name,blocks:[]});pageIndex=project.pages.length-1;selection=null;changed();render();};
byId('renamePage').onclick=()=>{const name=prompt('Page name',page().name);if(!name)return;remember();page().name=name;changed();renderPages();};
byId('deletePage').onclick=()=>{if(project.pages.length===1){status('Keep at least one page.');return;}if(!confirm('Delete this page and its components?'))return;remember();project.pages.splice(pageIndex,1);pageIndex=0;selection=null;changed();render();};
for(const button of document.querySelectorAll('[data-add]'))button.onclick=()=>add(button.dataset.add);
for(const [id,field] of [['projectName','name'],['canvasWidth','width'],['canvasHeight','height'],['canvasBackground','background']])byId(id).onchange=()=>{if(!project)return;remember();project[field]=['width','height'].includes(field)?Number(byId(id).value):byId(id).value;changed();render();zoom();};
byId('builderMode').onchange=()=>{remember();project.mode=byId('builderMode').value;changed();render();};
byId('exportVisual').onclick=()=>{remember();project.html=LayoutRuntime.visual(project);project.mode='code';changed();render();};
for(const field of ['html','css','js'])byId('source'+field).addEventListener('input',()=>{if(!project)return;remember();project[field]=byId('source'+field).value;changed();});
byId('runPreview').onclick=()=>{preview();zoom();};
byId('interactPreview').onchange=renderOverlay;
byId('canvasZoom').oninput=zoom;
byId('deleteBlock').onclick=()=>{if(!selected())return;remember();page().blocks=page().blocks.filter(b=>b.id!==selection);selection=null;changed();render();};
byId('duplicateBlock').onclick=()=>{const block=selected();if(!block||page().blocks.length>=80)return;remember();const next={...block,id:uid(),x:block.x+24,y:block.y+24};page().blocks.push(next);selection=next.id;changed();render();};
for(const [id,direction] of [['layerUp',1],['layerDown',-1]])byId(id).onclick=()=>{const i=page().blocks.findIndex(b=>b.id===selection),j=i+direction;if(i<0||j<0||j>=page().blocks.length)return;remember();[page().blocks[i],page().blocks[j]]=[page().blocks[j],page().blocks[i]];changed();render();};
function travel(from,to){if(!from.length)return;to.push(clone(project));project=from.pop();selection=null;changed();render();}
byId('undoLayout').onclick=()=>travel(undo,redo);byId('redoLayout').onclick=()=>travel(redo,undo);
byId('exportLayout').onclick=()=>{if(!project)return;const url=URL.createObjectURL(new Blob([JSON.stringify(project,null,2)],{type:'application/json'}));const a=el('a');a.href=url;a.download='car-ui-project.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
byId('importLayout').onchange=async()=>{const file=byId('importLayout').files[0];if(!file||!guard())return;try{if(file.size>400000)throw Error('Project is larger than 400 KB');const imported=JSON.parse(await file.text());if(!imported.pages?.length||!['visual','code'].includes(imported.mode)||!['html','css','js'].every(k=>typeof imported[k]==='string')||!Number.isInteger(imported.width)||!Number.isInteger(imported.height))throw Error('Invalid project format');const checked=await call('/api/layout',{action:'validate',project:imported});remember();project=checked.project;pageIndex=0;selection=null;changed();render();}catch(err){error(err);}};
window.addEventListener('beforeunload',e=>{if(dirty){e.preventDefault();e.returnValue='';}});
window.addEventListener('message',event=>{if(event.source!==byId('builderPreview').contentWindow||event.data?.channel!=='car-layout')return;if(event.data.type==='ready')sendDemo();if(event.data.type==='error')status('Preview script error: '+String(event.data.value).slice(0,250));});
document.addEventListener('panel-connected',()=>load(true).catch(error));
document.addEventListener('panel-device',()=>{if(loadedDevice!==device())status('Device changed. Reload the project before editing or publishing.');});
setInterval(()=>{if(project)sendDemo();},3000);
setInterval(async()=>{
  if(!project||loadedDevice!==device())return;
  try{
    const result=await call('/api/layout-status?device='+encodeURIComponent(loadedDevice));
    if(result.generation!==server.generation){status('Project changed in another tab. Export unsaved work, then reload.');return;}
    server.acknowledgedRevision=result.acknowledgedRevision;renderHistory();
  }catch(_){/* Retain draft during disconnection. */}
},10000);
})();
