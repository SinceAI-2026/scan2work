import test from 'node:test';
import assert from 'node:assert/strict';
import { arHazardContext } from './ar-hazards.mjs';

const asset={id:'OBJ-206',asset_id:'ASSET-003',tag:'CV-003',mapped:true,condition_state:'degraded',issue_summary:'Position deviation'};
const record={asset_id:'ASSET-003',snapshot_at:'2026-10-07 23:30',next_check:'verify mechanics',hazards:[{label:'Pressure',reading:{value:4.723,unit:'bar'}}]};
test('target context uses the matching workbook device and recorded energy',()=>{
 const result=arHazardContext(asset,{devices:{'OBJ-206':record}});
 assert.equal(result.title,'CV-003 · degraded');assert.equal(result.issue,'Position deviation');
 assert.equal(result.detail,'Pressure: 4.72 bar');assert.match(result.source,/Synthetic workbook snapshot/);
 assert.equal(result.next,'verify mechanics');
});
test('missing, unavailable, and mismatched records do not borrow another device',()=>{
 for(const context of [null,{unavailable:true},{devices:{'OBJ-206':{...record,asset_id:'ASSET-999'}}}])
  assert.equal(arHazardContext(asset,context).available,false);
});
test('missing readings and simulated conditions remain clearly identified',()=>{
 const result=arHazardContext(asset,{devices:{'OBJ-206':{...record,simulated_emergency:true,hazards:[{kind:'pressure'}]}}});
 assert.equal(result.detail,'No measured energy reading available');assert.match(result.source,/Scripted demo scenario/);
});
