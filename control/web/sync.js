"use strict";
(()=>{
  const role=document.getElementById('syncRole'),notice=document.getElementById('syncNotice');
  if(!role||!notice)return;
  const el=id=>document.getElementById(id);
  let revision=null,loaded=null,busy=false;
  const text=(tag,value)=>{const node=document.createElement(tag);node.textContent=value;return node;};
  function source(){
    const value=el('syncSource').value;
    if(loaded&&value===loaded.displaySource)return loaded.source;
    return loaded?.source.includes('\r\n')&&!loaded.source.replaceAll('\r\n','').includes('\n')?value.replace(/\r?\n/g,'\r\n'):value;
  }
  function bytes(){el('syncBytes').textContent=new TextEncoder().encode(source()).length+' / 16384 UTF-8 bytes';}
  function dirty(){return loaded&&source()!==loaded.source;}
  async function run(action){
    if(busy)return;busy=true;role.disabled=true;el('syncSource').readOnly=true;el('syncSelection').disabled=true;
    try{await action();}catch(error){notice.textContent=error.message;}
    finally{busy=false;role.disabled=false;el('syncSource').readOnly=false;el('syncSelection').disabled=false;}
  }
  async function refresh(){
    const result=await call('/api/sync?role='+encodeURIComponent(role.value));revision=result.revision;
    el('syncSelection').value=result.files.join(', ');
    const peer=result.peer;
    el('syncPeer').textContent=peer?'Last car selection report: '+new Date(peer.reportedAt*1000).toLocaleString()+' · '+(peer.files.join(', ')||'No files selected')+'. Home-only names: '+(result.files.filter(name=>!peer.files.includes(name)).join(', ')||'none')+'.':'Car selection has not been reported over the home IP link yet.';
    const rows=result.items.map(item=>{
      const row=text('p',item.name+' · '+item.phase+(item.error?' · '+item.error:''));
      const button=text('button','Open '+item.name);button.type='button';button.className='secondary';button.addEventListener('click',()=>run(()=>open(item.name)));row.append(' ',button);return row;
    });
    el('syncFiles').replaceChildren(...rows);
    if(!rows.length)el('syncFiles').append(text('p','No home data files selected.'));
  }
  function conflict(){
    const item=loaded?.conflicts.find(row=>row.id===el('syncConflictId').value);
    el('syncIncoming').value=item?.incoming??'A verified common baseline is unavailable. Compare the originals manually.';
    for(const id of ['syncUseCar','syncKeepHome','syncMerge'])el(id).disabled=!item||item.incoming===null;
  }
  function render(result){
    loaded={...result,role:role.value};el('syncEditor').hidden=false;
    el('syncFileTitle').textContent=result.name;el('syncFileState').textContent=result.phase+' · '+result.sha256.slice(0,16)+(result.pending?' · transfer pending':'')+(result.error?' · '+result.error:'');
    el('syncSource').value=result.source;loaded.displaySource=el('syncSource').value;bytes();
    el('syncSaveFile').disabled=!!result.pending||result.phase==='conflict'||!!result.recovery;
    el('syncRecovery').hidden=!result.recovery;el('syncRecoverySource').value=result.recovery?.saved||'';
    el('syncConflict').hidden=!result.conflicts.length;
    el('syncConflictId').replaceChildren(...result.conflicts.map(item=>{const option=text('option',new Date(item.reportedAt*1000).toLocaleString()+' · '+item.target.slice(0,16));option.value=item.id;return option;}));
    el('syncIncoming').value='No incoming conflict to review.';if(result.conflicts.length)conflict();
  }
  async function open(name){
    if(dirty())throw new Error('Save or discard the unsaved edit before opening another copy.');
    render(await call('/api/sync/file?role='+encodeURIComponent(role.value)+'&name='+encodeURIComponent(name)));notice.textContent='Loaded the home copy. Newer local changes will require reloading before saving.';
  }
  async function resolve(choice){
    if(!loaded||loaded.role!==role.value)return;
    const result=await call('/api/sync/file',{action:'resolve',role:role.value,name:loaded.name,id:el('syncConflictId').value,sha256:loaded.sha256,choice,source:choice==='merge'?source():undefined});
    render(result);await refresh();notice.textContent=result.recovery?'Choice saved. Resolution is pending recovery; review the file status above.':result.pending?'Choice saved with backups. Return transfer queued; waiting for the car applied receipt.':'Reported car copy accepted with a backup of the replaced home copy.';
  }
  el('syncSource').addEventListener('input',bytes);el('syncConflictId').addEventListener('change',conflict);
  role.addEventListener('change',()=>{
    if(dirty()){role.value=loaded.role;notice.textContent='Save or discard the unsaved edit before changing roles.';return;}
    loaded=null;revision=null;el('syncEditor').hidden=true;run(refresh);
  });
  el('syncRefresh').addEventListener('click',()=>run(refresh));
  el('syncSaveSelection').addEventListener('click',()=>run(async()=>{
    if(dirty())throw new Error('Save or discard the unsaved edit before changing the selection.');
    if(!revision)throw new Error('Load the current home selection first.');
    const files=el('syncSelection').value.split(/[,\n]/).map(value=>value.trim()).filter(Boolean);
    await call('/api/sync',{role:role.value,files,revision});await refresh();loaded=null;el('syncEditor').hidden=true;
    notice.textContent='Home selection saved. Existing copies are retained; the car allowlist is unchanged.';
  }));
  el('syncSaveFile').addEventListener('click',()=>run(async()=>{
    if(!loaded||loaded.role!==role.value)throw new Error('Open a selected file first.');
    const result=await call('/api/sync/file',{action:'save',role:role.value,name:loaded.name,sha256:loaded.sha256,source:source()});
    render(result);await refresh();notice.textContent=result.pending?'Home edit saved with a backup and queued. Waiting for remote application.':'Home copy saved; no changed bytes need sending.';
  }));
  el('syncUseCar').addEventListener('click',()=>run(()=>resolve('car')));
  el('syncKeepHome').addEventListener('click',()=>run(()=>resolve('home')));
  el('syncMerge').addEventListener('click',()=>run(()=>resolve('merge')));
  el('syncDownload').addEventListener('click',()=>{
    if(!loaded)return;const url=URL.createObjectURL(new Blob([source()],{type:'text/plain;charset=utf-8'}));
    const link=document.createElement('a');link.href=url;link.download='home-'+loaded.name;link.click();URL.revokeObjectURL(url);
  });
  el('syncDiscard').addEventListener('click',()=>{
    if(!loaded)return;el('syncSource').value=loaded.source;loaded.displaySource=el('syncSource').value;bytes();notice.textContent='Unsaved edit discarded. Reopen the file to load its newest saved copy.';
  });
  document.addEventListener('panel-connected',()=>run(refresh));
})();
