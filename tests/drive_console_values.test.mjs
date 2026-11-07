import assert from 'node:assert/strict';
import test from 'node:test';
// Registration needs DOM constructors; value handling itself needs no DOM.
globalThis.HTMLElement=class {};
globalThis.customElements={define(){}};
globalThis.window={};
const {number,display,temperature,targetView,currentPage,editorSchema,WIDGET_DEFAULTS}=await import('../home_assistant/drive_console.js');
test('missing, stale and disconnected values never become a fabricated zero',()=>{
  for(const value of ['unavailable','unknown','',undefined,null,'NaN','Infinity']){
    assert.equal(number({connected:true,states:{speed:{state:value}}},'speed'),null);
  }
  assert.equal(number({connected:false,states:{speed:{state:'55'}}},'speed'),null);
  assert.equal(display(null),'—');
});
test('real zero and fractional readings survive',()=>{
  assert.equal(number({connected:true,states:{speed:{state:'0'}}},'speed'),0);
  assert.equal(number({connected:true,states:{speed:{state:'55.5'}}},'speed'),55.5);
});

test('HA temperature conversion is respected without converting Fahrenheit twice',()=>{
  assert.equal(temperature(100,'°C','F'),212);
  assert.equal(temperature(212,'°F','F'),212);
  assert.equal(temperature(212,'°F','C'),100);
  assert.equal(temperature(273.15,'K','C'),0);
  assert.equal(temperature(null,'°C','F'),null);
});

test('native tabs are selected by their actual saved path without restoring removed pages',()=>{
  const views=[{title:'My home',path:'my-home'},{title:'My map',path:'gps'}];
  assert.equal(targetView(views,'gps'),views[1]);
  assert.equal(targetView(views,'home'),views[0]);
  assert.equal(targetView(views,'health'),undefined);
});
test('page appearance and navigation controls live in the page card',()=>{
  const one={widget:'page',type:'custom:drive-console-widget',background_color:'red'};
  assert.equal(currentPage({cards:[one]}),one);
  assert.deepEqual(currentPage({cards:[]}),{});
  const names=schema=>schema.flatMap(field=>[field.name,...(field.schema?names(field.schema):[])]);
  for(const key of ['show_tabs','show_header','show_rail','show_dock','navigation_items','dock_items','page_layout','custom_css'])assert.ok(names(editorSchema('page')).includes(key));
  assert.equal(WIDGET_DEFAULTS.page.show_tabs,true);
  for(const kind of ['clock','media','map','vehicleArt','gauges','metrics','value','text','link'])assert.ok(names(editorSchema(kind)).includes('custom_css'));
});

test('raw code editor exposes all sources and keeps network blocked in custom frame',async()=>{
  const {htmlDocument}=await import('../home_assistant/drive_console.js');
  const fields=editorSchema('html').flatMap(f=>f.schema||[f]).map(f=>f.name);
  for(const field of ['html','css','javascript'])assert.ok(fields.includes(field));
  const doc=htmlDocument({html:'<h1>Custom car</h1>',javascript:'window.x="</script>";'});
  assert.ok(doc.includes("connect-src 'none'"));
  assert.ok(doc.includes('window.driveData'));
  assert.ok(doc.includes('window.x="<\\/script>"'));
});

test('map and GPS status share the tracker position, regardless of missing or differently named binary sensor',async()=>{
  const {gpsPosition}=await import('../home_assistant/drive_console.js');
  const states={'device_tracker.car_location':{state:'not_home',attributes:{latitude:38.36,longitude:-81.62}},'binary_sensor.car_gps_fix':{state:'off'}};
  const h={connected:true,states};
  assert.equal(gpsPosition(h).latitude,38.36);
  assert.equal(gpsPosition({...h,connected:false}),null);
  states['device_tracker.car_location'].state='unavailable';
  assert.equal(gpsPosition(h),null);
  assert.equal(gpsPosition({connected:true,states:{}}),null);
});
