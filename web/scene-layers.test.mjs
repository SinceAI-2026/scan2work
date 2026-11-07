import {test} from "node:test";
import assert from "node:assert/strict";
import {major,overhead,groupOf,regionVisible,layerSummary,visualGeometry} from "./scene-layers.mjs";
import {manualObstacles,manualStep} from "./manual-walk.mjs";

const region = (id,extra={}) => ({id,source_region_id:id,class:"unclassified",center:[1,1,1],size:[.5,1,.5],yaw:0,tier:"equipment",...extra});
test("unclassified imported geometry appears without a semantic class",()=>{
  assert.equal(major(region("main")),true);
  assert.equal(regionVisible(region("main")),true);
  assert.equal(groupOf(region("named",{class:"circulation_pump",label_verified:false})),"other");
});
test("visible counts explain hidden small and overhead regions, all toggles reveal every region",()=>{
  const items=[region("main"),region("small",{size:[.1,.2,.1]}),region("raised",{center:[1,2.5,1]}),region("floor",{structure_role:"floor",center:[1,0,1],size:[1,.02,1]})];
  assert.deepEqual(layerSummary(items,{}),{visible:2,total:4,hiddenSmall:1,hiddenOverhead:1,hiddenFloor:0});
  assert.equal(layerSummary(items,{all:true,raised:true,floors:true}).visible,4);
  assert.equal(regionVisible(items[1],{selectedId:"small"}),true);
  assert.equal(regionVisible(items[2],{selectedId:"raised"}),true);
  assert.equal(layerSummary(items,{floors:false}).hiddenFloor,1);
});
test("proxy envelopes affect only visualization and do not mutate canonical geometry",()=>{
  const source=region("proxy",{geometry_proxy:{center:[2,2.5,3],size:[.4,.4,.4],yaw:.5}});
  const snapshot=JSON.stringify(source),proxy=visualGeometry(source);
  assert.deepEqual(proxy.center,[2,2.5,3]);assert.equal(overhead(source),true);
  assert.equal(JSON.stringify(source),snapshot);
  assert.equal(visualGeometry({...source,transform:{}}).center,source.center);
});
test("a broad unreviewed envelope cannot block occupancy-supported manual walking",()=>{
  const nav={grid_meta:{x_min:0,z_min:0,res:1,nx:4,nz:3},walkable_grid:[[1,1,1],[1,1,1],[0,0,0],[1,1,1]]};
  const broad=region("broad",{center:[1,1,1],size:[20,2,20]});
  const obstacles=manualObstacles([broad],.15);
  assert.equal(obstacles.length,0);
  assert.deepEqual(manualStep(nav,obstacles,{x:.5,z:1.5},.5,0),{x:1,z:1.5});
  assert.deepEqual(manualStep(nav,obstacles,{x:1.5,z:1.5},1,0),{x:1.5,z:1.5});
  assert.equal(manualObstacles([{...broad,source_region_id:undefined}],.15).length,1);
});
