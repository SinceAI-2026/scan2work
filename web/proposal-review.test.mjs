import {test} from "node:test";
import assert from "node:assert/strict";
import {scenePolicy} from "./imported-assets.mjs";
import {validateProposalCatalog,findProposals,proposalVisibilityText} from "./proposal-review-data.mjs";
import {loadRoomPlacements,saveRoomPlacements} from "./room-placement.mjs";

const region={id:"candidate-1",label:"Pipe fragment",class:"unclassified",center:[1,1,1],size:[.1,1,.1],yaw:0,
  visible_source_rows:25,total_source_rows:40,original_matches:[{id:"OBJ-206",tag:"CV-003",shared_rows:12}]};

test("baseline semantics and exact source membership are independent",()=>{
  const baseline={scene:{dataset_kind:"wael-original-baseline",membership_mode:"exact-source-rows"},assets:[{id:"OBJ-206",class:"control_valve"}]};
  assert.deepEqual(scenePolicy(baseline),{baseline:true,imported:false,exactMembership:true,scaleUnverified:true});
  assert.equal(scenePolicy({scene:{membership_mode:"exact-source-rows"},assets:[{source_region_id:"candidate-1"}]}).imported,true);
});
test("candidate validation/search cannot append candidates or rewrite canonical bindings/navigation",()=>{
  const baseline={assets:[Object.freeze({id:"OBJ-206",asset_id:"VALVE-003",tag:"CV-003"})],navigation:{walkable_grid:[[1,0],[0,1]],target_asset_id:"VALVE-003"}};
  const before=JSON.stringify(baseline);
  const catalog=Object.freeze({regions:Object.freeze([Object.freeze(region)])});
  const found=findProposals(validateProposalCatalog(catalog,1),"CV-003");
  assert.equal(found[0].id,"candidate-1");
  assert.match(proposalVisibilityText(found[0]),/^25 \/ 40/);
  assert.equal(JSON.stringify(baseline),before);
  assert.notEqual(found,baseline.assets);
  assert.equal(Object.hasOwn(found[0],"asset_id"),false);
});
test("inconsistent candidate counts and IDs cannot be reviewed as a valid projection",()=>{
  assert.throws(()=>validateProposalCatalog({regions:[region]},2),/count/);
  assert.throws(()=>validateProposalCatalog({regions:[region,region]},2),/unique/);
  assert.throws(()=>validateProposalCatalog({regions:[{...region,visible_source_rows:100}]},1),/visibility/);
});
test("baseline placement storage leaves previous candidate scene placements untouched",()=>{
  const legacyKey="field-atlas:equipment-room:placements",previous='[["candidate-1",{"center":[1,2,3]}]]';
  const storage=new Map([[legacyKey,previous]]),prior=globalThis.localStorage;
  globalThis.localStorage={getItem:key=>storage.get(key),setItem:(key,value)=>storage.set(key,value)};
  try {
    const asset={id:"OBJ-1",center:[0,0,0],yaw:0},placement={center:[1,0,0],yaw:0,source:[0,0,0,0]};
    saveRoomPlacements(new Map([[asset.id,placement]]),"wael-original-test");
    assert.equal(storage.get(legacyKey),previous);
    assert.equal(loadRoomPlacements(new Map([[asset.id,asset]]),()=>true,"wael-original-test").get(asset.id).center[0],1);
    assert.equal(loadRoomPlacements(new Map([[asset.id,asset]]),()=>true,"another-dataset").size,0);
  }finally{globalThis.localStorage=prior;}
});
