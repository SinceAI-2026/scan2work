import assert from "node:assert/strict";
import {createJourney,transitionJourney,journeyView} from "../web/journey.mjs";

const position={x:1,z:2};
const route=(asset_id="ASSET-A",duration_s=10)=>({asset_id,tag:asset_id||"Room tour",duration_s,total_distance_m:duration_s,
  waypoints:[{position:[1,0,2]},{position:[1+duration_s,0,2]}]});
const start=(r=route())=>transitionJourney(createJourney(r),{type:"start",route:r,position}).journey;
const send=(j,type,extra={})=>transitionJourney(j,{type,...extra});

const idle=createJourney(route());
assert.equal(journeyView(idle).status,"READY TO EXPLORE");
assert.equal(send(idle,"progress",{elapsed:10}).arrivedAssetId,null);
let journey=start();
assert.equal(journey.phase,"moving");
journey=send(journey,"progress",{elapsed:4}).journey;
let inspectedAsset="ASSET-B";
assert.equal(journey.route.asset_id,"ASSET-A","browsing another asset does not change destination");
const arrival=send(journey,"progress",{elapsed:10});
if(arrival.arrivedAssetId)inspectedAsset=arrival.arrivedAssetId;
assert.equal(inspectedAsset,"ASSET-A","arrival opens the routed asset");
assert.equal(arrival.journey.phase,"arrived");
assert.equal(journeyView(arrival.journey).remainingSeconds,0);
for(const type of ["progress","pause","resume"])
  assert.equal(send(arrival.journey,type,{elapsed:11}).arrivedAssetId,null,"arrival opens only once");

journey=send(start(),"progress",{elapsed:3}).journey;
journey=send(journey,"pause").journey;
assert.equal(journey.phase,"paused");
assert.equal(send(journey,"progress",{elapsed:7}).journey.elapsed,3);
journey=send(journey,"resume").journey;
assert.equal(journey.phase,"moving");
journey=send(journey,"block",{message:"Path obstructed"}).journey;
assert.equal(journey.elapsed,3,"failure keeps the last valid progress");
assert.equal(journey.route.asset_id,"ASSET-A");
assert.equal(journeyView(journey).error,"Path obstructed");
assert.equal(journeyView(journey).status,"BLOCKED");
assert.equal(send(journey,"resume").journey.phase,"blocked","resume cannot bypass a blocked path");

const replacement={...route("ASSET-C"),waypoints:[{position:[4,0,2]},{position:[14,0,2]}]};
assert.throws(()=>send(journey,"start",{route:replacement,position}),/current position/);
journey=send(journey,"start",{route:replacement,position:{x:4,z:2}}).journey;
assert.equal(journey.elapsed,0);
assert.equal(journey.error,"");
assert.equal(journeyView(journey).destination,"ASSET-C");
assert.equal(journey.phase,"moving");
assert.throws(()=>send(journey,"progress",{elapsed:NaN}),/forward/);
journey=send(journey,"progress",{elapsed:5}).journey;
assert.throws(()=>send(journey,"progress",{elapsed:4}),/forward/);

for(const kind of ["floor","tour"]) {
  const r={...route(null),kind};
  const done=send(start(r),"progress",{elapsed:10});
  assert.equal(done.journey.phase,"arrived");
  assert.equal(done.arrivedAssetId,null,`${kind} completion opens no unrelated equipment`);
}
const alreadyHere=route("ASSET-A",0);
const immediate=send(arrival.journey,"start",{route:alreadyHere,position});
assert.equal(immediate.journey.phase,"arrived");
assert.equal(immediate.arrivedAssetId,"ASSET-A");
assert.equal(immediate.journey.elapsed,0);
assert.equal(send(immediate.journey,"progress",{elapsed:1}).arrivedAssetId,null);
assert.equal(send(immediate.journey,"start",{route:route("ASSET-D"),position}).journey.phase,"moving");
assert.throws(()=>start({...alreadyHere,waypoints:[{position:[1,0,2]},{position:[2,0,2]}]}),/zero-duration/);
assert.throws(()=>start({...alreadyHere,duration_s:-1}),/duration/);

console.log("Journey behavior passed: one-time destination arrival, pause/resume, blocked recovery, re-dispatch, floor/tour and immediate arrival.");
