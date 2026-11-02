"use strict";
(()=>{
  let current=null;
  setInterval(async()=>{
    try{
      const response=await fetch('/api/update-status',{cache:'no-store'});
      if(!response.ok)return;
      const state=await response.json(),version=state.revision+':'+(state.overlayRevision || '');
      if(current===null){current=version;return;}
      const editing=document.activeElement?.matches('input,textarea,select,[contenteditable="true"]');
      if(version!==current && !editing)location.reload();
    }catch{}
  },30000);
})();
