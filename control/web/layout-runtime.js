/* Shared renderer. Every authored document runs in a unique-origin sandbox. */
"use strict";
window.LayoutRuntime=(()=>{
  const escape=value=>String(value).replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  function visual(project){
    return '<main id="layout-stage">'+project.pages.map((page,index)=>'<section class="layout-page" data-page="'+escape(page.id)+'"'+(index?' hidden':'')+'>'+page.blocks.map(block=>{
      const style='left:'+block.x+'px;top:'+block.y+'px;width:'+block.w+'px;height:'+block.h+'px;font-size:'+block.fontSize+'px;color:'+block.color+';background:'+block.background;
      let inside=escape(block.text);
      if(block.type==='sensor')inside='<small>'+escape(block.text||block.sensor)+'</small><strong data-sensor="'+escape(block.sensor)+'">—</strong><small data-sensor-status="'+escape(block.sensor)+'">No telemetry</small>';
      if(block.type==='clock')inside='<strong data-clock>--:--</strong>';
      if(block.type==='image')inside='<img alt="'+escape(block.text)+'" src="'+escape(block.src)+'">';
      if(block.type==='html')inside=block.text;
      if(block.type==='button')inside='<button data-page-target="'+escape(block.target)+'">'+escape(block.text)+'</button>';
      return '<div class="layout-block type-'+escape(block.type)+'" data-block="'+escape(block.id)+'" style="'+escape(style)+'">'+inside+'</div>';
    }).join('')+'</section>').join('')+(project.pages.length>1?'<nav class="layout-nav">'+project.pages.map(page=>'<button data-page-target="'+escape(page.id)+'">'+escape(page.name)+'</button>').join('')+'</nav>':'')+'</main>';
  }
  function documentFor(project, pageId){
    const css=String(project.css).replace(/</g,'\\3c ');
    const setup=`(${function(width,height,visualMode,pageId){
      const send=(type,value)=>parent.postMessage({channel:'car-layout',type,value},'*');
      window.addEventListener('error',e=>send('error',e.message));
      window.addEventListener('unhandledrejection',()=>send('error','Unhandled promise rejection'));
      const showPage=id=>{
        const pages=[...document.querySelectorAll('.layout-page')];
        if(pages.some(p=>p.dataset.page===id))pages.forEach(p=>p.hidden=p.dataset.page!==id);
      };
      window.addEventListener('DOMContentLoaded',()=>{
        if(pageId)showPage(pageId);
        document.addEventListener('click',e=>{
          const button=e.target.closest('[data-page-target]');
          if(button)showPage(button.dataset.pageTarget);
        });
        const resize=()=>{
          const stage=document.getElementById('layout-stage');
          if(visualMode && stage){const scale=Math.min(innerWidth/width,innerHeight/height);stage.style.transform='scale('+scale+')';}
        };
        addEventListener('resize',resize);resize();
        setInterval(()=>document.querySelectorAll('[data-clock]').forEach(el=>el.textContent=new Date().toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'})),1000);
        send('ready',true);
      });
      window.addEventListener('message',event=>{
        if(event.source!==parent || event.data?.channel!=='car-telemetry')return;
        const live=event.data.live;
        if(!live || typeof live!=='object')return;
        window.carTelemetry=live;
        document.querySelectorAll('[data-sensor]').forEach(el=>{
          const sensor=live.sensors?.[el.dataset.sensor];
          el.textContent=sensor?(sensor.number??sensor.value)+' '+(sensor.unit||''):'—';
        });
        document.querySelectorAll('[data-sensor-status]').forEach(el=>{
          const sensor=live.sensors?.[el.dataset.sensorStatus];
          const age=sensor?Date.now()/1000-Number(sensor.ts):Infinity;
          el.textContent=event.data.demo?'PREVIEW SAMPLE':Number.isFinite(age)&&age>=-20&&age<=45?'RECENT DATA':'STALE / NO DATA';
        });
        window.dispatchEvent(new CustomEvent('cartelemetry',{detail:live}));
      });
    }.toString()})(${Number(project.width)},${Number(project.height)},${project.mode==='visual'},${JSON.stringify(pageId||'')});`;
    const scripts=setup+'\n'+String(project.js);
    // Escape closing tags without altering JavaScript string/template semantics.
    const safeScript=scripts.replace(/<\/script/gi,'<\\/script');
    const policy="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data: blob:; font-src data:; connect-src 'none'; frame-src 'none'; object-src 'none'; form-action 'none'; base-uri 'none'";
    return '<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Content-Security-Policy" content="'+policy+'"><style>html,body{margin:0;width:100%;height:100%;font-family:system-ui;background:'+escape(project.background)+';color:white}*{box-sizing:border-box}[hidden]{display:none!important}#layout-stage{position:relative;width:'+project.width+'px;height:'+project.height+'px;transform-origin:top left;overflow:hidden}.layout-page{position:absolute;inset:0}.layout-block{position:absolute;padding:16px;border-radius:16px;overflow:hidden;display:flex;flex-direction:column;justify-content:center;gap:8px}.layout-block img{width:100%;height:100%;object-fit:contain}.layout-block button,.layout-nav button{font:inherit;background:#145a91;color:inherit;border:1px solid #66bfff;border-radius:12px;padding:12px;cursor:pointer}.layout-block strong{font-size:1.2em}.layout-block small{font-size:.48em;opacity:.75}.layout-nav{position:absolute;bottom:8px;left:8px;right:8px;display:flex;gap:8px;z-index:100}.layout-nav button{font-size:16px}'+css+'</style><script>'+safeScript+'</script></head><body>'+(project.mode==='visual'?visual(project):project.html)+'</body></html>';
  }
  const pending=new Map();
  window.addEventListener('message',event=>{
    if(event.data?.channel!=='car-layout'||event.data.type!=='host-ready')return;
    const html=pending.get(event.source);
    if(html!==undefined){event.source.postMessage({channel:'car-layout-host',html},'*');pending.delete(event.source);}
  });
  function mount(frame,project,pageId){
    pending.set(frame.contentWindow,documentFor(project,pageId));
    frame.removeAttribute('srcdoc');frame.src='/layout-host';
  }
  return {documentFor,visual,mount};
})();
