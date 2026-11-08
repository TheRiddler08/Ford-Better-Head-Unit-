"use strict";
(()=>{
 const notice=document.getElementById('messageNotice');
 async function request(data){const response=await fetch('/api/messages',data?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)}:{});const result=await response.json();if(!response.ok)throw new Error(result.error||'Messages unavailable');return result;}
 async function refresh(){const result=await request();const list=document.getElementById('messageList');list.replaceChildren();for(const row of result.items){const article=document.createElement('article');const heading=document.createElement('h3');heading.textContent=`${row.source} → ${row.destination} · ${row.phase}`;const text=document.createElement('p');text.textContent=row.text;const stamp=document.createElement('small');stamp.textContent=new Date(row.created*1000).toLocaleString();article.append(heading,text,stamp);list.append(article);}}
 document.getElementById('messageForm').addEventListener('submit',async event=>{event.preventDefault();const text=document.getElementById('messageText');try{await request({text:text.value,destination:'home'});text.value='';notice.textContent='Queued locally; waiting for an authenticated delivery receipt.';await refresh();}catch(error){notice.textContent=error.message;}});
 document.getElementById('requestStatus').onclick=async()=>{try{await request({destination:'home',action:'status-request'});notice.textContent='Read-only home health request queued.';await refresh();}catch(error){notice.textContent=error.message;}};
 document.getElementById('refreshMessages').onclick=()=>refresh().catch(error=>notice.textContent=error.message);
 refresh().catch(error=>notice.textContent=error.message);setInterval(()=>refresh().catch(()=>{}),15000);
})();
