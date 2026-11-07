import {test} from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import {exportRoomIfc} from "./bim-export.mjs";

const meta={x_min:0,z_min:0,nx:4,nz:4,res:.5,units:"unverified source units"};
const asset={id:"region-1",source_region_id:"region-1",center:[1,.5,1],size:[.3,1,.3],yaw:0,class:"unclassified"};
const stamp=new Date("2026-11-07T12:00:00Z");
const context={pipe_segments:[{id:"fit-1",start:[0,1,0],end:[1,1,0],radius:.04,support:55,region_ids:["region-1"]}],observed_floor:{cells_xz:[[0,0]],res:.05,source_points:100}};
const count=(text,pattern)=>(text.match(pattern)||[]).length;

test("legacy timestamp argument remains valid; optional context preserves source asset entities",()=>{
  const legacy=exportRoomIfc([asset],[],meta,stamp);
  const withContext=exportRoomIfc([asset],[],meta,stamp,context);
  assert.match(legacy,/2026-11-07T12:00:00/);
  assert.equal(count(legacy,/'FieldAtlas_Asset'/g),1);
  assert.equal(count(withContext,/'FieldAtlas_Asset'/g),1);
  assert.equal(count(withContext,/'DerivedContextPipeFit'/g),1);
  assert.equal(count(withContext,/'FieldAtlas_DerivedContext'/g),1);
  assert.match(withContext,/Context only; no new asset or Gaussian ownership/);
  assert.match(withContext,/Pipe hypothesis; unverified/);
  assert.match(withContext,/FieldAtlas_ObservedFloorEvidence/);
  assert.match(withContext,/Assumed room slab; not measured floor geometry/);
});
test("invalid context geometry is rejected rather than silently losing an exported segment",()=>{
  assert.throws(()=>exportRoomIfc([asset],[],meta,stamp,{pipe_segments:[{...context.pipe_segments[0],radius:NaN}]}),/Invalid context pipe/);
  assert.throws(()=>exportRoomIfc([asset],[],meta,stamp,{pipe_segments:[context.pipe_segments[0],context.pipe_segments[0]]}),/Duplicate context/);
});

const path=new URL("./data/digital_twin.json",import.meta.url);
test("generated scene exports every extracted region and every context pipe fit",{skip:!fs.existsSync(path)},()=>{
  const twin=JSON.parse(fs.readFileSync(path,"utf8"));
  const text=exportRoomIfc(twin.assets,twin.scene.walls,twin.navigation.grid_meta,stamp,twin.scene.derived_context);
  assert.equal(count(text,/'FieldAtlas_Asset'/g),twin.assets.length);
  assert.equal(count(text,/'DerivedContextPipeFit'/g),twin.scene.derived_context.pipe_segments.length);
  assert.equal(count(text,/=IFCWALL\(/g),twin.scene.walls.length);
  assert.equal(count(text,/'FieldAtlas_DerivedContext'/g),twin.scene.derived_context.pipe_segments.length);
  assert.doesNotMatch(text,/NaN|Infinity/);
});
