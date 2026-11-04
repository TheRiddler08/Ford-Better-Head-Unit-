import assert from 'node:assert/strict';
import test from 'node:test';
// Registration needs DOM constructors; value handling itself needs no DOM.
globalThis.HTMLElement=class {};
globalThis.customElements={define(){}};
globalThis.window={};
const {number,display,temperature}=await import('../home_assistant/drive_console.js');
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
