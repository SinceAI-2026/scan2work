import assert from "node:assert/strict";
import { test } from "node:test";
import { decodeInstanceIds, firstNavigationPoint } from "./imported-assets.mjs";
import { assetPresentation } from "./asset-presentation.mjs";

function membership(ids) {
  const buffer = new ArrayBuffer(12 + ids.length * 2), view = new DataView(buffer);
  new Uint8Array(buffer,0,4).set([71,83,73,68]);
  view.setUint32(4,1,true); view.setUint32(8,ids.length,true);
  ids.forEach((v,i) => view.setUint16(12+2*i,v,true));
  return buffer;
}

test("membership preserves identities beyond the legacy object count and background", () => {
  assert.deepEqual([...decodeInstanceIds(membership([0,414,1518,1932,65535]),5,1933)], [1,415,1519,1933,0]);
});
test("a mismatched or corrupted membership cannot drive object edits", () => {
  assert.throws(() => decodeInstanceIds(membership([0]),2,1933), /match/);
  assert.throws(() => decodeInstanceIds(membership([1933]),1,1933), /unknown region/);
  const wrongMagic = membership([0]); new Uint8Array(wrongMagic)[0] = 0;
  assert.throws(() => decodeInstanceIds(wrongMagic,1,1933), /format/);
  const wrongVersion = membership([0]); new DataView(wrongVersion).setUint32(4,2,true);
  assert.throws(() => decodeInstanceIds(wrongVersion,1,1933), /format/);
  assert.throws(() => decodeInstanceIds(membership([0]).slice(0,-1),1,1933), /match/);
});
test("no target route still permits a supported start; no floor stays unavailable", () => {
  const nav = {primary_route:{waypoints:[]},grid_meta:{x_min:1,z_min:-2,res:.5},walkable_grid:[[0,1],[0,0]]};
  assert.deepEqual(firstNavigationPoint(nav).position,[1.25,0,-1.25]);
  assert.equal(firstNavigationPoint({...nav,walkable_grid:[[0,0],[0,0]]}),null);
  assert.equal(firstNavigationPoint({}),null);
});
test("unknown imported regions have no alarm or implied instrument reading", () => {
  const presentation = assetPresentation({source_region_id:"region-1",mapped:false,condition_state:"unknown"});
  assert.equal(presentation.kind,"candidate"); assert.equal(presentation.needsAttention,false);
  assert.match(presentation.notice,/no assigned device data/);
  assert.equal(assetPresentation({mapped:true,condition_state:"degraded"}).needsAttention,true);
});
