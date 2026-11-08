"use strict";
(()=>{
  const el=id=>document.getElementById(id),list=el('transferList');if(!list)return;
  const notice=el('transferNotice');let cursor=null,busy=false,loaded=false,paginated=false,again=false;
  const node=(tag,value)=>{const item=document.createElement(tag);if(value!==undefined)item.textContent=value;return item;};
  const date=value=>value?new Date(value*1000).toLocaleString():'none recorded';
  const selection=()=>({device:el('device').value,role:el('transferRole').value,state:el('transferFilter').value});
  async function run(work){
    if(busy)return;busy=true;el('transferRole').disabled=true;el('transferFilter').disabled=true;
    try{await work();}catch(error){notice.textContent=error.message;}
    finally{busy=false;el('transferRole').disabled=false;el('transferFilter').disabled=false;if(again){again=false;run(()=>refresh());}}
  }
  async function action(item,kind,value){
    if(item.device!==el('device').value)throw new Error('The selected car changed. Refresh transfers before changing delivery.');
    await call('/api/software/transfer',{device:el('device').value,id:item.id,action:kind,value,revision:item.revision});
    await refresh();notice.textContent=kind==='retry'?'Retry scheduled for missing fragments or the final applied receipt. Radio limits and pause settings still apply.':'Sender controls saved. Data already transmitted may still finish on the receiver.';
  }
  function card(item){
    const box=node('article');box.className='transfer-card';box.dataset.transferId=item.id;
    box.append(node('h3',item.role+' · '+item.path),node('p',item.displayPhase+' · '+item.acknowledged+'/'+item.total+' received fragments · '+item.progress+'%'));
    const progress=node('progress');progress.max=item.total||1;progress.value=item.acknowledged;progress.setAttribute('aria-label','Fragments confirmed by device');box.append(progress);
    const decisions={'finished':'Transfer finished','paused':'Sender is paused','ip-only':'Waiting for IP delivery; radio is disabled for this update','ip-preferred':'Recent authenticated IP exchange; radio is held for this role','radio-fallback':'No recent IP exchange; radio fallback is eligible within the shared quota'};
    box.append(node('p',decisions[item.decision]),node('p','Priority: '+item.priority+' · Last receipt: '+date(item.lastReceiptAt)+' · Last transport: '+(item.lastTransport||'none recorded')));
    if(item.error)box.append(node('p','Receiver error: '+item.error));
    const details=node('details'),summary=node('summary','Transfer details');details.append(summary);
    const copy=node('pre','ID: '+item.id+'\nBase: '+item.base+'\nTarget: '+item.target+'\nSigned delta: '+item.deltaBytes+' bytes\nManifest receipts: '+item.manifest.acknowledged+'/'+item.manifest.total+'\nData receipts: '+item.data.acknowledged+'/'+item.data.total+'\nLast send queued: '+date(item.lastSentAt)+'\nIP last seen: '+date(item.ipSeenAt)+'\nRadio frames for one pass of unconfirmed chunks: '+(item.radioFramesPerPass??'unavailable')+' (estimate excludes partial encrypted packets, lost receipts and retries)');details.append(copy);box.append(details);
    if(!['applied','failed'].includes(item.phase)){
      const controls=node('div');controls.className='inline';
      for(const [label,kind,value] of [[item.paused?'Resume sending':'Pause sending',item.paused?'resume':'pause',undefined],['Retry missing fragments','retry',undefined],[item.policy==='ip-only'?'Use automatic fallback':'Use IP only','policy',item.policy==='ip-only'?'auto':'ip-only'],[item.priority==='high'?'Normal priority':'High priority','priority',item.priority==='high'?'normal':'high']]){
        const button=node('button',label);button.className='secondary';button.addEventListener('click',()=>run(()=>action(item,kind,value)));controls.append(button);
      }
      box.append(controls);
    }
    return box;
  }
  async function refresh(older=false){
    const selected=selection(),query=new URLSearchParams(selected);if(older&&cursor)query.set('before',String(cursor));
    const result=await call('/api/software/transfers?'+query);
    if(JSON.stringify(selected)!==JSON.stringify(selection())){again=true;return;}
    cursor=result.nextCursor;loaded=true;paginated=older;
    const cards=result.items.map(card);if(older)list.append(...cards);else list.replaceChildren(...cards);
    if(!older&&!cards.length)list.append(node('p','No matching signed update transfers.'));
    el('transferMore').hidden=!cursor;
    const budget=result.budget;
    el('transferBudget').textContent=`Shared downlink budget: ${budget.reserved}/${budget.limit} reservations used, ${budget.remaining} left. Quota resets ${date(budget.resetsAt)}. Minimum spacing ${budget.spacingSeconds}s. Next eligible reservation: ${date(budget.nextEligibleAt)}. Reservations and broker queueing are separate from device receipts.`;
    notice.textContent='Showing '+selected.state+' transfers. 100% reception still requires an applied receipt.';
  }
  el('transferRefresh').addEventListener('click',()=>run(()=>refresh()));
  el('transferMore').addEventListener('click',()=>run(()=>refresh(true)));
  for(const id of ['transferRole','transferFilter'])el(id).addEventListener('change',()=>run(()=>refresh()));
  for(const event of ['panel-connected','panel-device','software-published'])document.addEventListener(event,()=>{if(busy)again=true;else run(()=>refresh());});
  setInterval(()=>{if(loaded&&!paginated&&!el('editor').hidden&&!busy&&!list.contains(document.activeElement)&&!list.querySelector('details[open]'))run(()=>refresh());},15000);
})();
