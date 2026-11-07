import test from 'node:test';
import assert from 'node:assert/strict';
import {canShowGeneratedModel, generatedCoverage} from './generated-model-policy.mjs';
import {unitDevices, faultParts, floorDistance, sizeText} from './parts-data.mjs';

const owners=[{id:'A'},{id:'B'}];
test('removing, moving, or taking apart any member restores its assembly scan',()=>{
  assert.equal(canShowGeneratedModel(owners),true);
  assert.equal(canShowGeneratedModel(owners,{removed:id=>id==='B'}),false);
  assert.equal(canShowGeneratedModel(owners,{roomAsset:a=>a.id==='B'?{...a,transform:{yaw:1}}:a}),false);
  assert.equal(canShowGeneratedModel(owners,{held:new Set(['A'])}),false);
  assert.deepEqual([...generatedCoverage([{owners,visible:false}])],[]);
  assert.deepEqual([...generatedCoverage([{owners,visible:true}],false)],[]);
  assert.deepEqual([...generatedCoverage([{owners,visible:true},{owners:[owners[1]],visible:true}])],['A','B']);
});
test('unmapped archive profiles and proposals cannot become generated-part fault evidence',()=>{
  const unit={entry:{box:[[-10,-10,-10],[10,10,10]],parts:[{node:'motor',fault_codes:['CURRENT_IMBALANCE']}]}};
  const assets=[
    {id:'old',center:[0,0,0],mapped:false,tag:'FAKE-123',issue_code:'CURRENT_IMBALANCE'},
    {id:'proposal',center:[0,0,0],source_region_id:'candidate',tag:'FAKE-124',issue_code:'CURRENT_IMBALANCE'},
    {id:'real',center:[0,0,0],mapped:true,tag:'M-005',issue_code:'CURRENT_IMBALANCE'},
  ];
  const devices=unitDevices(unit,assets[0],assets);
  assert.deepEqual(devices.map(d=>d.tag),['old','proposal','M-005']);
  assert.deepEqual(faultParts(unit.entry,devices).map(f=>f.tag),['M-005']);
});
test('candidate bounding boxes are supported and dimensions identify model data',()=>{
  assert.equal(floorDistance({bbox_min:[0,0,0],bbox_max:[1,1,1]},2,.5),1);
  assert.equal(floorDistance({},0,0),Infinity);
  assert.match(sizeText([100,200,300]),/mm \(model\)/);
});
