"use strict";
const $=id=>document.getElementById(id), ns='http://www.w3.org/2000/svg';
let cursor=0, totalRows=0, recordRequest=0, routeCursor=0, points=[], routeMore=false, loadingAll=false, routeGeneration=0, extent=null, view=null, drag=null;
const timestamp=value=>Number.isFinite(Number(value))?new Date(Number(value)*1000).toLocaleString():'—';
function params(){
  const p=new URLSearchParams();
  for(const [id,key] of [['fromTime','from'],['toTime','to']])if($(id).value)p.set(key,new Date($(id).value).getTime()/1000);
  if($('sensorName').value.trim())p.set('name',$('sensorName').value.trim());
  if($('scanId').value)p.set('snapshot',$('scanId').value);
  if($('outcome').value)p.set('status',$('outcome').value);
  return p;
}
async function api(url){const response=await fetch(url);const data=await response.json();if(!response.ok)throw Error(data.error||response.status);return data;}
function exportLink(){const q=params();q.set('kind',$('recordKind').value);$('exportCsv').href='/api/report/export?'+q;}
async function summary(){
  const data=await api('/api/report/summary');$('archiveSummary').replaceChildren();
  for(const [key,label] of [['values','Sensor values'],['commands','OBD requests'],['snapshots','Scans'],['gps','GPS fixes'],['stops','Visited stops']]){
    const box=document.createElement('div');box.className='stat';const n=document.createElement('strong');n.textContent=data[key].count.toLocaleString();const text=document.createElement('span');text.textContent=label;box.append(n,text);$('archiveSummary').append(box);
  }
}
function showRecord(record){$('recordJson').textContent=JSON.stringify(record,null,2);$('recordDetail').showModal();}
async function records(reset=false){
  const request=++recordRequest;
  if(reset){cursor=0;totalRows=0;$('recordRows').replaceChildren();}
  const p=params();p.set('after',cursor);p.set('limit',500);const kind=$('recordKind').value;exportLink();
  try{
    const data=await api('/api/report/'+kind+'?'+p);if(request!==recordRequest)return;
    if(reset){const tr=document.createElement('tr');for(const field of data.columns){const th=document.createElement('th');th.textContent=field;tr.append(th);}$('recordColumns').replaceChildren(tr);}
    for(const row of data.items){const tr=document.createElement('tr');tr.tabIndex=0;tr.onclick=()=>showRecord(row);tr.onkeydown=e=>{if(e.key==='Enter')showRecord(row);};for(const field of data.columns){const td=document.createElement('td');const value=row[field];td.textContent=value==null?'—':['ts','received','completed','started','ended'].includes(field)?timestamp(value):String(value);tr.append(td);}$('recordRows').append(tr);}
    cursor=data.next;totalRows+=data.items.length;$('moreRecords').disabled=!data.hasMore;
    $('recordStatus').textContent=data.available?totalRows.toLocaleString()+' matching records loaded'+(data.hasMore?' · more available':' · end of results'):'This record type has not been collected yet. Existing readings are still available under Every sensor value.';
  }catch(error){$('recordStatus').textContent=error.message;}
}
function project(point){const lat=Math.max(-85.05112878,Math.min(85.05112878,point.lat))*Math.PI/180;return {x:(point.lon+180)/360,y:(1-Math.log(Math.tan(lat)+1/Math.cos(lat))/Math.PI)/2};}
function svg(tag,attrs){const element=document.createElementNS(ns,tag);for(const [k,v] of Object.entries(attrs))element.setAttribute(k,v);return element;}
function setView(next){view=next;$('routeMap').setAttribute('viewBox',[next.x,next.y,next.w,next.h].join(' '));}
function fit(){if(!extent)return;const ratio=$('routeMap').clientWidth/$('routeMap').clientHeight;let w=Math.max(extent.maxX-extent.minX,.00002),h=Math.max(extent.maxY-extent.minY,.00002);if(w/h<ratio)w=h*ratio;else h=w/ratio;setView({x:(extent.maxX+extent.minX)/2-w*.56,y:(extent.maxY+extent.minY)/2-h*.56,w:w*1.12,h:h*1.12});tiles();}
function tiles(){
  $('routeTiles').replaceChildren();if(!view||!$('showTiles').checked)return;
  const z=Math.max(0,Math.min(16,Math.floor(Math.log2($('routeMap').clientWidth/(256*view.w))))),n=2**z;
  const x1=Math.max(0,Math.floor(view.x*n)),x2=Math.min(n-1,Math.floor((view.x+view.w)*n)),y1=Math.max(0,Math.floor(view.y*n)),y2=Math.min(n-1,Math.floor((view.y+view.h)*n));
  if((x2-x1+1)*(y2-y1+1)>120)return;
  for(let x=x1;x<=x2;x++)for(let y=y1;y<=y2;y++){
    const tile=svg('image',{x:x/n,y:y/n,width:1/n,height:1/n,href:'/tiles/'+z+'/'+x+'/'+y+'.png'});tile.addEventListener('error',()=>tile.remove());$('routeTiles').append(tile);
  }
}
function draw(){
  $('routePaths').replaceChildren();if(!points.length){extent=null;view=null;$('routeTiles').replaceChildren();$('selectedFix').setAttribute('r',0);return;}
  let minX=Infinity,maxX=-Infinity,minY=Infinity,maxY=-Infinity,path='',lastSegment=null;
  for(const point of points){const p=project(point);minX=Math.min(minX,p.x);maxX=Math.max(maxX,p.x);minY=Math.min(minY,p.y);maxY=Math.max(maxY,p.y);path+=(point.segment!==lastSegment?'M':'L')+p.x+' '+p.y+' ';lastSegment=point.segment;}
  $('routePaths').append(svg('path',{d:path,fill:'none',stroke:'#58e8be','stroke-width':3,'vector-effect':'non-scaling-stroke'}));
  const first=project(points[0]),last=project(points.at(-1));
  for(const p of [first,last])$('routePaths').append(svg('circle',{cx:p.x,cy:p.y,r:.000002,fill:'#f9c463'}));
  extent={minX,maxX,minY,maxY};fit();
}
async function loadRoute(reset=false){
  if(reset){routeGeneration++;routeCursor=0;points=[];$('selectedFix').setAttribute('r',0);}
  const generation=routeGeneration,p=params();p.set('after',routeCursor);p.set('limit',2000);
  try{
    const data=await api('/api/report/gps?'+p);if(generation!==routeGeneration)return;
    points.push(...data.items);routeCursor=data.next;routeMore=data.hasMore;
    $('routeStatus').textContent=points.length.toLocaleString()+' actual GPS fixes loaded'+(routeMore?' · partial history: more fixes available':' · all matching recorded fixes loaded')+(data.available?'':' · GPS recorder has not created a history file yet');
    $('moreRoute').disabled=!routeMore;if(!loadingAll)draw();
  }catch(error){$('routeStatus').textContent=error.message;routeMore=false;}
}
function zoom(factor){if(!view)return;const w=view.w*factor,h=view.h*factor;setView({x:view.x+(view.w-w)/2,y:view.y+(view.h-h)/2,w,h});tiles();}
function mapPoint(event){const p=$('routeMap').createSVGPoint();p.x=event.clientX;p.y=event.clientY;return p.matrixTransform($('routeMap').getScreenCTM().inverse());}
$('routeMap').addEventListener('wheel',e=>{e.preventDefault();zoom(e.deltaY>0?1.3:1/1.3);},{passive:false});
$('routeMap').addEventListener('pointerdown',e=>{if(!view)return;drag={p:mapPoint(e),view:{...view},x:e.clientX,y:e.clientY};$('routeMap').setPointerCapture(e.pointerId);});
$('routeMap').addEventListener('pointermove',e=>{if(!drag)return;const p=mapPoint(e);setView({...view,x:view.x+drag.p.x-p.x,y:view.y+drag.p.y-p.y});});
$('routeMap').addEventListener('pointerup',e=>{if(!drag)return;const clicked=Math.hypot(e.clientX-drag.x,e.clientY-drag.y)<5;drag=null;tiles();if(!clicked||!points.length)return;const p=mapPoint(e);let nearest=points[0],best=Infinity;for(const point of points){const q=project(point),d=(q.x-p.x)**2+(q.y-p.y)**2;if(d<best){best=d;nearest=point;}}const q=project(nearest);$('selectedFix').setAttribute('cx',q.x);$('selectedFix').setAttribute('cy',q.y);$('selectedFix').setAttribute('r',view.w/180);$('fixDetail').textContent=timestamp(nearest.ts)+'\n'+JSON.stringify(nearest,null,2);});
$('routeMap').addEventListener('pointercancel',()=>{drag=null;});
$('zoomIn').onclick=()=>zoom(.6);$('zoomOut').onclick=()=>zoom(1/.6);$('fitRoute').onclick=fit;$('showTiles').onchange=tiles;
$('loadRoute').onclick=()=>{loadingAll=false;loadRoute(true);};$('moreRoute').onclick=()=>loadRoute(false);
$('allRoute').onclick=async()=>{if(loadingAll)return;loadingAll=true;$('stopRoute').disabled=false;await loadRoute(true);while(routeMore&&loadingAll)await loadRoute(false);loadingAll=false;$('stopRoute').disabled=true;draw();};
$('stopRoute').onclick=()=>{loadingAll=false;$('stopRoute').disabled=true;};
$('applyFilters').onclick=()=>{loadingAll=false;routeGeneration++;points=[];draw();$('routeStatus').textContent='Filters changed. Reload route to apply them.';records(true);};
$('allTime').onclick=()=>{$('fromTime').value='';$('toTime').value='';$('applyFilters').click();};
$('recordKind').onchange=()=>records(true);$('moreRecords').onclick=()=>records(false);$('closeDetail').onclick=()=>$('recordDetail').close();
summary().catch(error=>$('recordStatus').textContent=error.message);records(true);
