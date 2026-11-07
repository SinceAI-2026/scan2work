import test from "node:test";
import assert from "node:assert/strict";
import {assetPresentation,displayAssetTag} from "./asset-presentation.mjs";
import {exportRoomIfc} from "./bim-export.mjs";

test("archived unmapped profiles cannot imply readings or device tags",()=>{
  const asset={id:"OBJ-226",tag:"EV-153",asset_id:"AUX-226",mapped:false,class:"expansion_vessel",condition_state:"healthy",health_score:95};
  assert.equal(assetPresentation(asset).kind,"detected");
  assert.equal(assetPresentation(asset).needsAttention,false);
  assert.equal(displayAssetTag(asset),"OBJ-226");
  assert.equal(assetPresentation({...asset,condition_state:"degraded"}).needsAttention,false);
  assert.equal(assetPresentation({id:"OBJ-226",mapped:false,health_score:95,condition_state:"healthy"}).kind,"detected");
});

test("actual mapped alarms, movable objects and candidates keep distinct meanings",()=>{
  assert.equal(assetPresentation({mapped:true,condition_state:"watch"}).needsAttention,true);
  assert.equal(displayAssetTag({id:"OBJ-206",tag:"CV-003",mapped:true}),"CV-003");
  assert.equal(assetPresentation({mapped:false,movable:true,condition_state:"movable"}).kind,"movable");
  assert.equal(assetPresentation({mapped:false,condition_state:"not_instrumented"}).kind,"facility");
  assert.equal(assetPresentation({source_region_id:"asset-1",mapped:false,condition_state:"unknown"}).kind,"candidate");
});

test("IFC preserves unmapped object identity without borrowed Excel tags",()=>{
  const asset={id:"OBJ-226",asset_id:"AUX-226",tag:"EV-153",mapped:false,label:"Vessel",class:"expansion_vessel",center:[1,1,1],size:[.4,1,.4],yaw:0};
  const meta={x_min:0,z_min:0,nx:10,nz:10,res:.1,units:"unverified source units"};
  const text=exportRoomIfc([asset],[],meta,new Date("2026-11-07T00:00:00Z"));
  assert.ok(text.includes("OBJ-226"));
  assert.ok(!text.includes("EV-153"));
  assert.equal(asset.tag,"EV-153"); // saved metadata remains intact
});
