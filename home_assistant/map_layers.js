/* Optional navigation layers. All viewport and proximity queries stay local. */
const mapLayerDefaults=()=>({cameras:false,coverage:false,estimates:false,alerts:false,manufacturer:'flock',gateway:'all',alertMeters:300,opacity:.75});
Object.assign(DriveConsoleWidget.prototype,{
  layerOptions(){return {...mapLayerDefaults(),...(this._navigation?.settings?.layers||{})};},
  installLayerTools(surface){
    const panel=document.createElement('details');panel.className='map-layers';panel.innerHTML=`<summary>Layers</summary><div class="layer-tools">
      <label><input type="checkbox" id="layerCameras"> Reported camera locations</label>
      <label>Camera filter<select id="layerManufacturer"><option value="flock">Flock Safety only</option><option value="all">All reported ALPR cameras</option></select></label>
      <label><input type="checkbox" id="layerCoverage"> Recorded LoRaWAN receptions</label>
      <label><input type="checkbox" id="layerEstimates"> Estimated gateway range</label>
      <label>Gateway<select id="layerGateway"><option value="all">All gateways</option></select></label>
      <label>Layer opacity<input id="layerOpacity" type="range" min="0.15" max="1" step="0.05"></label>
      <label><input type="checkbox" id="layerAlerts"> Nearby-camera alerts</label>
      <label>Alert distance (meters)<input id="layerAlertMeters" type="number" min="50" max="5000" step="50"></label>
      <p id="layerAlert" role="status"></p><p id="layerStatus" role="status">Reading local layer cache…</p>
      <p class="layer-legend"><span class="camera-key">●</span> Reported camera · <span class="rf-key">●</span> Reception · ◌ User estimate</p>
      <button id="layerRefresh">Refresh cached data</button><label>Import camera JSON / GeoJSON<input id="layerImport" type="file" accept=".json,.geojson,application/json"></label>
      <details><summary>Gateway locations & estimates</summary><label>Edit gateway<select id="layerSite"><option value="">New gateway</option></select></label>
      <label>Gateway EUI<input id="layerSiteID" maxlength="16" placeholder="16 hexadecimal digits"></label><label>Name<input id="layerSiteName" maxlength="160"></label>
      <label>Role<select id="layerSiteRole"><option value="fixed">Fixed site</option><option value="mobile">Mobile gateway</option></select></label>
      <label>Latitude<input id="layerSiteLat" type="number" step="any" min="-90" max="90"></label><label>Longitude<input id="layerSiteLon" type="number" step="any" min="-180" max="180"></label>
      <label>Estimated radius (km; 0 = no estimate)<input id="layerSiteRadius" type="number" min="0" max="200" step="0.1" value="0"></label>
      <button id="layerSitePoint">Use selected map point</button><button id="layerSiteSave">Save gateway</button><button id="layerSiteHide">Hide gateway</button></details>
      <p class="layer-help">Receptions show where packets were received, not guaranteed coverage. Dashed rings are user estimates. Camera reports may be incomplete or outdated.</p>
      <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener noreferrer">Camera data © OpenStreetMap contributors · ODbL</a><small>USA camera export: DeFlock / FlockHopper</small></div>`;
    surface.append(panel);
    const style=document.createElement('style');style.textContent=`.map-layers{position:absolute;z-index:10;right:88px;top:16px;max-width:min(300px,72%);max-height:70%;overflow:auto;background:var(--panel,#1b2229);color:var(--text,#ecf0f3);border:1px solid var(--border,#34404b);border-radius:7px;padding:7px 10px;font:13px/1.45 system-ui;touch-action:pan-y}.map-layers summary{cursor:pointer;padding:5px}.layer-tools{width:260px;max-width:100%;padding-top:4px}.layer-tools label,.layer-tools small{display:block;margin:8px 0}.layer-tools label:has(input[type=checkbox]){display:flex;align-items:center;gap:8px}.layer-tools input:not([type=checkbox]),.layer-tools select{display:block;width:100%;box-sizing:border-box;padding:7px;margin-top:4px;background:var(--surface,#121a23);color:inherit;border:1px solid var(--border,#34404b);border-radius:4px}.layer-tools input[type=checkbox]{width:18px;height:18px}.layer-tools button{min-height:36px;margin:3px;padding:6px 9px;border:1px solid var(--border,#34404b);border-radius:4px;background:var(--surface,#121a23);color:inherit;cursor:pointer}.layer-tools a{color:var(--cyan,#8bbcf0)}.layer-help,.layer-legend,.layer-tools small{font-size:11px;color:var(--muted,#a8b3be)}.camera-key{color:#f2b45c}.rf-key{color:#4ab4a5}.layer-cluster{background:transparent!important;border:0!important;box-shadow:none!important;color:#111!important;font:600 11px system-ui!important}.map-layer-popup{color:#17202a;font:13px/1.5 system-ui;max-width:250px}.map-layer-popup button{display:block;margin-top:7px;min-height:32px}#layerAlert:not(:empty){border-left:3px solid #e6ab51;padding-left:8px}.leaflet-popup{z-index:450}@media(max-width:600px){.map-layers{right:76px;top:12px;max-width:65%}.layer-tools{width:235px}}`;
    style.textContent+='.map-layers>summary{width:max-content;margin-left:auto;max-width:100%}';
    this.shadowRoot.append(style);
    LEAFLET?.DomEvent.disableClickPropagation(panel);LEAFLET?.DomEvent.disableScrollPropagation(panel);
    const r=this.shadowRoot;
    for(const [id,key,kind] of [['layerCameras','cameras','bool'],['layerCoverage','coverage','bool'],['layerEstimates','estimates','bool'],['layerAlerts','alerts','bool'],['layerManufacturer','manufacturer','text'],['layerGateway','gateway','text'],['layerOpacity','opacity','number'],['layerAlertMeters','alertMeters','number']]){
      r.getElementById(id).onchange=()=>{const input=r.getElementById(id),options=this.layerOptions(),value=kind==='bool'?input.checked:kind==='number'?Number(input.value):input.value;
        if(kind==='number'&&(!Number.isFinite(value)||(key==='alertMeters'?(value<50||value>5000):(value<.15||value>1)))){this.layerNotice('Choose an opacity from 0.15–1 and an alert distance from 50–5000 meters.');return;}
        options[key]=value;this._navigation.settings.layers=options;this._layerRenderKey=null;this.drawMapLayers();this.refreshMapLayers(true);this.saveNavigation();};
    }
    r.getElementById('layerRefresh').onclick=()=>this.layerAction({action:'refresh'});
    r.getElementById('layerImport').onchange=async event=>{const file=event.target.files[0];if(!file)return;try{if(file.size>16*1024**2)throw Error('Choose a camera JSON file under 16 MB');await this.layerAction({action:'import-cameras',data:JSON.parse(await file.text())});}catch(error){this.layerNotice(error.message);}};
    r.getElementById('layerSite').onchange=()=>{const site=this._layerData?.gateways?.find(item=>item.id===r.getElementById('layerSite').value);for(const [id,key] of [['layerSiteID','id'],['layerSiteName','label'],['layerSiteLat','lat'],['layerSiteLon','lon'],['layerSiteRole','role'],['layerSiteRadius','radius']])r.getElementById(id).value=site?.[key]??(key==='role'?'fixed':key==='radius'?0:'');};
    r.getElementById('layerSitePoint').onclick=()=>{if(!validPoint(this._selectedPoint)){this.layerNotice('Select a point on the map first.');return;}r.getElementById('layerSiteLat').value=this._selectedPoint.lat;r.getElementById('layerSiteLon').value=this._selectedPoint.lon;};
    r.getElementById('layerSiteSave').onclick=()=>this.layerAction({action:'gateway',id:r.getElementById('layerSiteID').value,name:r.getElementById('layerSiteName').value,role:r.getElementById('layerSiteRole').value,lat:r.getElementById('layerSiteLat').value===''?null:Number(r.getElementById('layerSiteLat').value),lon:r.getElementById('layerSiteLon').value===''?null:Number(r.getElementById('layerSiteLon').value),radius:Number(r.getElementById('layerSiteRadius').value)});
    r.getElementById('layerSiteHide').onclick=()=>this.layerAction({action:'hide-gateway',id:r.getElementById('layerSiteID').value});
    this.syncLayerControls();
  },
  layerNotice(message){const node=this.shadowRoot.getElementById('layerStatus');if(node)node.textContent=message;},
  syncLayerControls(){
    const r=this.shadowRoot,options=this.layerOptions();if(!r.getElementById('layerCameras'))return;
    for(const [id,key] of [['layerCameras','cameras'],['layerCoverage','coverage'],['layerEstimates','estimates'],['layerAlerts','alerts']])r.getElementById(id).checked=options[key];
    for(const [id,key] of [['layerManufacturer','manufacturer'],['layerGateway','gateway'],['layerOpacity','opacity'],['layerAlertMeters','alertMeters']])if(r.activeElement!==r.getElementById(id))r.getElementById(id).value=options[key];
    const admin=this._hass?.user?.is_admin!==false;
    for(const id of ['layerRefresh','layerImport','layerSiteSave','layerSiteHide'])r.getElementById(id).disabled=!admin;
  },
  async layerAction(data){
    if(this._layerWriting)return;this._layerWriting=true;this.layerNotice('Saving layer request…');
    const controller=new AbortController();this._layerWriteRequest=controller;const timeout=setTimeout(()=>controller.abort(),30000);
    try{const response=await this._hass.fetchWithAuth('/api/car_console/service/map-layers',{method:'POST',signal:controller.signal,headers:{'Content-Type':'application/json'},body:JSON.stringify(data)}),result=await response.json();if(!response.ok)throw Error(result.error||'Layer action failed');this.layerNotice(result.phase==='queued'?'Refresh queued. The last saved data stays available.':'Saved locally');this._layerQueryKey=null;this.refreshMapLayers(true);}
    catch(error){this.layerNotice(controller.signal.aborted?'Layer request interrupted; reload the saved data before retrying.':error.message);}finally{clearTimeout(timeout);this._layerWriting=false;}
  },
  refreshMapLayers(force=false){
    if(!this._leaflet?._loaded||!this._hass||!this.isConnected)return;
    this.syncLayerControls();this.updateLayerAlert();
    const map=this._leaflet,bounds=map.getBounds(),options=this.layerOptions(),wrap=n=>((n+180)%360+360)%360-180;
    const optionKey=JSON.stringify(options);if(this._layerOptionsKey!==optionKey){this._layerOptionsKey=optionKey;this._layerRenderKey=null;this.drawMapLayers();}
    const world=bounds.getEast()-bounds.getWest()>=360;
    const params={west:world?-180:wrap(bounds.getWest()),east:world?180:wrap(bounds.getEast()),south:Math.max(-90,bounds.getSouth()),north:Math.min(90,bounds.getNorth()),zoom:map.getZoom(),layers:['cameras','coverage','estimates'].filter(key=>options[key]).join(','),manufacturer:options.manufacturer,gateway:options.gateway};
    const gps=gpsPosition(this._hass,this.config.gps_entity||DEFAULTS.gps);
    if(options.alerts&&gps)Object.assign(params,{alertLat:gps.latitude,alertLon:gps.longitude,alertMeters:options.alertMeters});
    const key=JSON.stringify([params,Math.floor(Date.now()/15000)]);
    if(!force&&key===this._layerQueryKey)return;this._layerQueryKey=key;
    this._layerRequest?.abort();clearTimeout(this._layerTimer);
    const controller=new AbortController();this._layerRequest=controller;
    this._layerTimer=setTimeout(async()=>{
      const timeout=setTimeout(()=>controller.abort(),8000);
      try{const response=await this._hass.fetchWithAuth('/api/car_console/service/map-layers?'+new URLSearchParams(params),{signal:controller.signal});const data=await response.json();if(!response.ok)throw Error(data.error||'Layer cache unavailable');if(controller!==this._layerRequest||controller.signal.aborted||!this.isConnected)return;
        this._layerData=data;this._layerAlertPoint=gps?{lat:gps.latitude,lon:gps.longitude,at:Date.now()}:null;this._layerRenderKey=null;this.drawMapLayers();this.layerStatus();}
      catch(error){if(controller===this._layerRequest&&this.isConnected)this.layerNotice('Layer service unavailable; last displayed data retained.');}finally{clearTimeout(timeout);}
    },force?0:250);
  },
  layerPopup(title,lines,point,osm){
    const box=document.createElement('div');box.className='map-layer-popup';const heading=document.createElement('strong');heading.textContent=title;box.append(heading);
    for(const value of lines){const line=document.createElement('p');line.textContent=value;box.append(line);}
    if(osm&&/^(node|way|relation)$/.test(osm.osm_type)&&/^\d+$/.test(osm.osm_id)){const link=document.createElement('a');link.textContent='Open source report';link.href=`https://www.openstreetmap.org/${osm.osm_type}/${osm.osm_id}`;link.target='_blank';link.rel='noopener noreferrer';box.append(link);}
    if(point){const button=document.createElement('button');button.textContent='Use as route waypoint';button.onclick=()=>{this._selectedPoint={lat:point.lat,lon:point.lon};this.addNavPoint('waypoint');};box.append(button);}
    LEAFLET.DomEvent.disableClickPropagation(box);return box;
  },
  drawMapLayers(){
    if(!this._leaflet||!this._layerData)return;
    const map=this._leaflet,options=this.layerOptions(),data=this._layerData;
    const key=JSON.stringify([data,options]);if(this._layerRenderKey===key)return;this._layerRenderKey=key;
    if(!this._mapLayerGroups){this._mapLayerGroups={};for(const key of ['cameras','coverage','estimates'])this._mapLayerGroups[key]=LEAFLET.layerGroup().addTo(map);this._layerCanvas=LEAFLET.canvas({padding:.3});}
    for(const group of Object.values(this._mapLayerGroups))group.clearLayers();
    const circle=(row,style,group)=>LEAFLET.circleMarker(this.leafletPoint(row),{renderer:this._layerCanvas,bubblingMouseEvents:false,weight:1.5,opacity:options.opacity,fillOpacity:options.opacity,...style}).addTo(this._mapLayerGroups[group]);
    if(options.cameras){
      for(const row of data.cameras||[]){
        if(row.cluster){const marker=circle(row,{radius:Math.min(19,9+Math.log2(row.count||1)),color:'#6b4820',fillColor:'#efb45f'},'cameras');marker.bindTooltip(String(row.count),{permanent:true,direction:'center',className:'layer-cluster'});marker.on('click',()=>map.setView(this.leafletPoint(row),Math.min(18,map.getZoom()+2)));}
        else circle(row,{radius:6,color:'#684720',fillColor:'#efb45f'},'cameras').bindPopup(this.layerPopup(row.label||'Reported camera',[row.brand||'Manufacturer not reported',row.operator||'Operator not reported',row.direction==null?'Facing direction not reported':`Reported facing: ${Math.round(row.direction)}°`,'Community report; current presence and activity unverified.'],row,row));
      }
    }
    if(options.coverage)for(const row of data.receptions||[]){const color=row.rssi==null?'#8995a1':row.rssi>=-90?'#4ab4a5':row.rssi>=-110?'#e5b462':'#df7d73';circle(row,{radius:5,color,fillColor:color},'coverage').bindPopup(this.layerPopup('Recorded reception',[`Gateway: ${row.gateway}`,`RSSI: ${row.rssi??'unknown'} dBm · SNR: ${row.snr??'unknown'} dB`,new Date(row.received*1000).toLocaleString(),'Reception at this point only; surrounding areas remain untested.'],row));}
    if(options.coverage||options.estimates)for(const site of data.gateways||[]){if(site.hidden||site.lat==null||site.lon==null||(options.gateway!=='all'&&options.gateway!==site.id))continue;
      const content=this.layerPopup(site.label||site.id,[`Gateway EUI: ${site.id}`,site.role==='fixed'?'Fixed site':site.role==='mobile'?'Mobile gateway; excluded from measured fixed-site coverage':'Gateway role unknown',site.radius>0?`User estimate: ${site.radius} km radius; terrain and buildings are not modeled.`:'No estimated radius configured.'],site);
      circle(site,{radius:9,color:'#cad8ed',fillColor:'#497db4'},options.coverage?'coverage':'estimates').bindPopup(content);
      if(options.estimates&&site.role==='fixed'&&site.radius>0)LEAFLET.circle(this.leafletPoint(site),{radius:site.radius*1000,color:'#86a8d4',fillColor:'#86a8d4',opacity:options.opacity,fillOpacity:.07*options.opacity,dashArray:'7 7',weight:2,bubblingMouseEvents:false}).addTo(this._mapLayerGroups.estimates).bindPopup(this.layerPopup('Estimated gateway range',[`${site.label||site.id}: ${site.radius} km`,'User-supplied estimate, not measured coverage or a connectivity guarantee.'],site));
    }
  },
  layerStatus(){
    const r=this.shadowRoot,data=this._layerData;if(!data)return;
    const camera=data.cameraStatus||{},coverage=data.coverageStatus||{},when=stamp=>stamp?new Date(stamp*1000).toLocaleString():'not yet';
    this.layerNotice(`Camera cache: ${camera.count||0} reports${camera.sourceDate?' · source snapshot '+camera.sourceDate:''} · downloaded ${when(camera.fetchedAt)}. Recorded fixed-site receptions: ${coverage.count||0} · synced ${when(coverage.syncedAt)}.${data.cameraTruncated||data.coverageTruncated?' Zoom in or select a gateway to see more.':''}${camera.phase==='pending'?' Waiting for the first USA camera download.':''}${data.worker?.phase==='download-unavailable-cache-retained'?' Download unavailable; using saved reports.':''}${data.homeStatus?.phase==='home-unavailable-cache-retained'?' Home link unavailable; using saved receptions.':''}`);
    const key=JSON.stringify(data.gateways||[]);if(this._layerSitesKey!==key){this._layerSitesKey=key;for(const id of ['layerGateway','layerSite']){const select=r.getElementById(id),old=select.value;select.replaceChildren();const first=new Option(id==='layerGateway'?'All gateways':'New gateway',id==='layerGateway'?'all':'');select.add(first);for(const site of data.gateways||[])select.add(new Option((site.label||site.id)+(site.hidden?' (hidden)':''),site.id));if([...select.options].some(option=>option.value===old))select.value=old;}}
    this.updateLayerAlert();
    this.syncLayerControls();
    if(this._leaflet)this._leaflet.attributionControl.addAttribution('Camera data © <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap contributors</a> · DeFlock/FlockHopper');
  },
  updateLayerAlert(){
    const node=this.shadowRoot.getElementById('layerAlert');if(!node)return;
    const options=this.layerOptions(),gps=gpsPosition(this._hass,this.config.gps_entity||DEFAULTS.gps),near=this._layerData?.nearest?.[0],point=this._layerAlertPoint;
    const fresh=point&&gps&&Date.now()-point.at<20000&&LEAFLET.latLng(point.lat,point.lon).distanceTo(LEAFLET.latLng(gps.latitude,gps.longitude))<30;
    node.textContent=!options.alerts?'':!gps?'Nearby alerts need a current GPS fix.':!fresh?'Updating nearby camera reports…':near?`Reported camera nearby: ${near.label||near.brand||'ALPR'} · ${near.meters} m`:'No reported camera within the selected alert distance.';
  },
  clearMapLayers(){
    clearTimeout(this._layerTimer);this._layerRequest?.abort();this._layerWriteRequest?.abort();this._layerRequest=null;this._layerQueryKey=null;this._layerRenderKey=null;this._mapLayerGroups=null;this._layerCanvas=null;this._layerSitesKey=null;this._layerOptionsKey=null;this._layerAlertPoint=null;
  }
});
