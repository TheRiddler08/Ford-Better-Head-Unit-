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

test('simulated and laptop fixes never change backend state and expire',async()=>{
  const {preview,previewHass}=await import('../home_assistant/drive_console.js');
  const real={connected:false,states:{'sensor.car_speed':{state:'unavailable'}}};
  preview.mode='demo';preview.started=0;
  const first=previewHass(real,10000),next=previewHass(real,20000);
  assert.equal(real.states['sensor.car_speed'].state,'unavailable');
  assert.equal(real.connected,false);
  assert.notEqual(first.states['device_tracker.car_location'].attributes.latitude,next.states['device_tracker.car_location'].attributes.latitude);
  assert.equal(first.states['sensor.car_speed'].attributes.preview,true);
  await assert.rejects(first.callService(),/disabled/);
  preview.mode='location';preview.fix={latitude:40,longitude:-76,accuracy:20,time:10000};
  assert.equal(previewHass(real,11000).states['device_tracker.car_location'].attributes.gps_accuracy,20);
  assert.equal(previewHass(real,11000).states['sensor.car_speed'].state,'unavailable');
  assert.equal(previewHass(real,41000).states['binary_sensor.car_gps_fix'].state,'off');
  preview.mode='live';preview.fix=null;
  assert.equal(previewHass(real),real);
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
