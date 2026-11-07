import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {sampleRoute, nearbyAssets, planFromPosition, reachableFloor, floorDestination, planRoomTour, assetDestination, advanceAlongRoute, segmentIsFree} from '../web/navigation-view.mjs';
import {mapFrame,mapPoint,mapWorldPoint} from '../web/game-map.mjs';

function openGrid(width=10,height=10) {
  return {grid_meta:{nx:width,nz:height,res:1,x_min:0,z_min:0},
    room_walkable_grid:Array.from({length:width},()=>Array(height).fill(1)),
    clearance_grid_m:Array.from({length:width},()=>Array(height).fill(1))};
}

function assertSweptFloor(navigation,a,b) {
  const m=navigation.grid_meta,free=navigation.room_walkable_grid || navigation.walkable_grid;
  const steps=Math.max(2,Math.ceil(Math.hypot(b.x-a.x,b.z-a.z)/Math.min(.002,m.res/10)));
  for(let i=0;i<=steps;i++) {
    const x=a.x+(b.x-a.x)*i/steps,z=a.z+(b.z-a.z)*i/steps;
    assert.equal(free[Math.floor((x-m.x_min)/m.res)]?.[Math.floor((z-m.z_min)/m.res)],1,
      `Movement must stay on free floor at (${x}, ${z})`);
  }
}

function assertPlayback(navigation,planned,step) {
  let actual={x:planned.waypoints[0].position[0],z:planned.waypoints[0].position[2]};
  for(let elapsed=0;elapsed<planned.duration_s;) {
    const next=Math.min(planned.duration_s,elapsed+step);
    const before=Object.freeze({...actual});
    actual=advanceAlongRoute(navigation,planned,before,elapsed,next);
    const crossed=planned.waypoints.filter(w=>w.t>elapsed&&w.t<next)
      .map(w=>({x:w.position[0],z:w.position[2]}));
    let previous=before;
    for(const point of [...crossed,actual]) {assertSweptFloor(navigation,previous,point);previous=point;}
    assert.ok(Math.hypot(actual.x-before.x,actual.z-before.z)<=.5*(next-elapsed)+.002,
      'The robot must not move farther than its walking speed permits.');
    elapsed=next;
  }
  const end=planned.waypoints.at(-1).position;
  assert.ok(Math.hypot(actual.x-end[0],actual.z-end[2])<.001,'Playback must reach the requested endpoint.');
  return actual;
}

const isolatedPoseGrid=openGrid();
const shortRoute={duration_s:2,waypoints:[
  {t:0,position:[.5,0,.5],distance_m:0,yaw:Math.PI/2},
  {t:2,position:[1.5,0,.5],distance_m:1,yaw:Math.PI/2},
]};
const unrelatedPose=Object.freeze({x:8.5,z:8.5});
assert.throws(()=>advanceAlongRoute(isolatedPoseGrid,shortRoute,unrelatedPose,0,.1),
  'Movement must reject an actual pose that differs from the route pose, rather than teleport to the route.');
assert.deepEqual(unrelatedPose,{x:8.5,z:8.5});
assert.throws(()=>advanceAlongRoute(isolatedPoseGrid,shortRoute,{x:.55,z:.5},.1,0));

const cornerGrid=openGrid(4,4);
cornerGrid.room_walkable_grid[1][0]=0;
assert.equal(segmentIsFree(cornerGrid,[.5,0,.5],[1.5,0,1.5]),false,
  'An exact diagonal touch against an obstacle corner must block movement.');
const cornerRoute=planFromPosition(cornerGrid,{x:.5,z:.5},{waypoints:[{position:[1.5,0,1.5]}]});
assert.ok(cornerRoute.total_distance_m>=2-1e-9,'A route must go around the blocked corner rather than take its diagonal.');
for(const step of [1/120,1/30,.08,.5])assertPlayback(cornerGrid,cornerRoute,step);

const stationary=planFromPosition(isolatedPoseGrid,{x:.5,z:.5},{waypoints:[{position:[.5,0,.5]}]});
assert.equal(stationary.duration_s,0);
assert.equal(stationary.total_distance_m,0);
for(const endTime of [0,.1,10]) {
  const sample=advanceAlongRoute(isolatedPoseGrid,stationary,{x:.5,z:.5},0,endTime);
  assert.equal(sample.x,.5);assert.equal(sample.z,.5);assert.equal(sample.distance,0);
}

const mapMeta={x_min:-4.55,z_min:-3.35,nx:170,nz:138,res:.05};
for(const [width,height] of [[680,552],[1020,828],[225,183]]) {
  const f=mapFrame(width,height);
  const near=mapPoint(mapMeta,width,height,-4.55,3.55);
  assert.ok(Math.hypot(near[0]-f.x,near[1]-f.y)<1e-9);
  const opposite=mapPoint(mapMeta,width,height,3.95,-3.35);
  assert.ok(Math.abs(opposite[0]-(f.x+f.width))<1e-9&&Math.abs(opposite[1]-(f.y+f.height))<1e-9);
  for(const [x,z] of [[0,0],[-4,3],[3,-3]]) {
    const pixel=mapPoint(mapMeta,width,height,x,z),world=mapWorldPoint(mapMeta,width,height,...pixel);
    assert.ok(Math.hypot(world.x-x,world.z-z)<1e-9,'Map clicks must resolve to the same room location as map markers.');
  }
  for(const pixel of [[0,0],[f.x-1,f.y+10],[f.x+f.width+1,f.y+10],[f.x+10,f.y-1],[f.x+10,f.y+f.height+1]])
    assert.equal(mapWorldPoint(mapMeta,width,height,...pixel),null,'Clicks outside a room wall must not create destinations.');
}
console.log('Passed pose continuity, corner clearance, stationary movement, backwards-time rejection, and map-to-room coordinates.');

const route = {duration_s:10,waypoints:[
  {t:0,position:[0,0,0],distance_m:0,yaw:0,clearance_m:.5},
  {t:1,position:[1,0,0],distance_m:1,yaw:0,clearance_m:.4},
  {t:10,position:[1,0,9],distance_m:10,yaw:0,clearance_m:.3},
]};
assert.equal(sampleRoute(route,.5).x,.5);
assert.equal(sampleRoute(route,1).z,0);
assert.equal(sampleRoute(route,5).z,4);
assert.equal(sampleRoute(route,5).distance,5);
assert.equal(sampleRoute(route,100).z,9);
assert.equal(sampleRoute(route,-1).x,0);
const assets = Array.from({length:60},(_,i)=>({id:i,center:[i/100,0,0],condition_state:'healthy',mapped:false}));
assert.equal(nearbyAssets(assets,0,0,1).length,60);
assert.equal(nearbyAssets([...assets,{id:'target',center:[.9,0,0],condition_state:'degraded',is_target:true}],0,0,1)[0].id,'target');

const twin = JSON.parse(readFileSync(new URL('../web/data/digital_twin.json',import.meta.url)));
const nav = twin.navigation, m = nav.grid_meta;
let checked = 0;
// Re-dispatch from the middle of each real route; check every interpolated segment.
for (const destination of Object.values(nav.routes_by_asset)) {
  const start = sampleRoute(destination, destination.duration_s / 2);
  const planned = planFromPosition(nav,start,destination);
  assert.equal(planned.waypoints[0].position[0],start.x);
  assert.equal(planned.waypoints[0].position[2],start.z);
  for (let i=1;i<planned.waypoints.length;i++) {
    const a=planned.waypoints[i-1].position,b=planned.waypoints[i].position;
    const steps=Math.max(2,Math.ceil(Math.hypot(b[0]-a[0],b[2]-a[2])/.002));
    for (let j=0;j<=steps;j++) {
      const x=a[0]+(b[0]-a[0])*j/steps,z=a[2]+(b[2]-a[2])*j/steps;
      assert.equal(nav.walkable_grid[Math.floor((x-m.x_min)/m.res)][Math.floor((z-m.z_min)/m.res)],1);
    }
  }
  checked++;
}
const roomStart=sampleRoute(nav.primary_route,0);
const destinations=Object.values(nav.routes_by_asset);
const playbackDestinations=[0,Math.floor(destinations.length/3),Math.floor(destinations.length*2/3),destinations.length-1]
  .map(index=>planFromPosition(nav,roomStart,destinations[index]))
  .filter(planned=>planned.duration_s>.2);
assert.ok(playbackDestinations.length>=3,'Exercise distinct nonzero destinations across the real room.');
for(const planned of playbackDestinations)for(const step of [1/120,1/30,.08,.5])assertPlayback(nav,planned,step);
for(let i=0;i<playbackDestinations.length;i++) {
  const first=playbackDestinations[i],initial={x:first.waypoints[0].position[0],z:first.waypoints[0].position[2]};
  const midTime=first.duration_s*.37;
  const moving=advanceAlongRoute(nav,first,initial,0,midTime);
  const target=destinations[(i*7+5)%destinations.length];
  const redirected=planFromPosition(nav,moving,target);
  assert.deepEqual(redirected.waypoints[0].position,[moving.x,0,moving.z],
    'Changing destination must start from the actual in-flight position.');
  const next=advanceAlongRoute(nav,redirected,moving,0,Math.min(1/30,redirected.duration_s));
  assert.ok(Math.hypot(next.x-moving.x,next.z-moving.z)<=.5/30+.001,
    'The first update after re-dispatch must not jump back to the entrance.');
  assertPlayback(nav,redirected,.08);
}
console.log(`Passed ${playbackDestinations.length} real destinations at 120Hz, 30Hz, 80ms, and 500ms steps, plus mid-route destination changes.`);

const blocked = {...nav,room_walkable_grid:nav.walkable_grid.map(row=>row.map(()=>0)),walkable_grid:nav.walkable_grid.map(row=>row.map(()=>0))};
assert.throws(()=>planFromPosition(blocked,{x:0,z:0},nav.primary_route),/outside the validated floor/);
console.log(`Passed animation timing, complete proximity, blocked-route rejection, and ${checked} real-scene re-dispatch routes.`);

const start=sampleRoute(nav.primary_route,0);
const floor=reachableFloor(nav,start);
// Navigate to destinations across the complete reachable floor, beyond registry stops.
for(const point of floor.filter((_,i)=>i%197===0)) {
  const destination=floorDestination(nav,start,point,.01);
  const route=planFromPosition(nav,start,destination);
  assert.equal(route.kind,'floor');
  assert.ok(Math.hypot(route.waypoints.at(-1).position[0]-point.x,route.waypoints.at(-1).position[2]-point.z)<.001);
}
assert.throws(()=>floorDestination(nav,start,{x:-100,z:-100}),/blocks|blocked/);
const split={grid_meta:{nx:5,nz:2,res:1,x_min:0,z_min:0},
  room_walkable_grid:[[1,1],[1,1],[0,0],[1,1],[1,1]],clearance_grid_m:Array.from({length:5},()=>[1,1])};
assert.equal(reachableFloor(split,{x:.5,z:.5}).length,4);
assert.throws(()=>floorDestination(split,{x:.5,z:.5},{x:4.5,z:.5}),/blocked or disconnected/);
assert.throws(()=>planFromPosition(split,{x:.5,z:.5},{waypoints:[{position:[4.5,0,.5]}]}),/No collision-free route/);
const discovered=twin.assets.find(a=>!a.mapped&&Math.hypot(a.center[0]-start.x,a.center[2]-start.z)<1.5);
assert.ok(discovered);
assert.equal(assetDestination(nav,start,discovered).asset_id,discovered.asset_id);
const tour=planRoomTour(nav,start);
assert.ok(tour.stops.length>=6);
const close=floor.filter(p=>tour.waypoints.some(w=>Math.hypot(p.x-w.position[0],p.z-w.position[2])<1.5));
assert.ok(close.length/floor.length>.95, 'Tour should pass within 1.5m of at least 95% of reachable floor');
for(let i=1;i<tour.waypoints.length;i++) {
  const a=tour.waypoints[i-1].position,b=tour.waypoints[i].position;
  const steps=Math.max(2,Math.ceil(Math.hypot(b[0]-a[0],b[2]-a[2])/.002));
  for(let j=0;j<=steps;j++){
    const x=a[0]+(b[0]-a[0])*j/steps,z=a[2]+(b[2]-a[2])*j/steps;
    assert.equal(nav.room_walkable_grid[Math.floor((x-m.x_min)/m.res)][Math.floor((z-m.z_min)/m.res)],1);
  }
}
console.log(`Passed room-wide destinations, discovered-object access, disconnected-floor rejection, and ${tour.stops.length}-stop tour (${tour.total_distance_m.toFixed(1)}m; ${(close.length/floor.length*100).toFixed(1)}% floor within 1.5m).`);

const bypass={grid_meta:{nx:6,nz:6,res:1,x_min:0,z_min:0},
 room_walkable_grid:Array.from({length:6},(_,x)=>Array.from({length:6},(_,z)=>x===2&&z<4?0:1)),
 clearance_grid_m:Array.from({length:6},()=>Array(6).fill(1))};
const unsafe={duration_s:8,waypoints:[{t:0,position:[.5,0,.5],distance_m:0},{t:8,position:[4.5,0,.5],distance_m:4}]};
assert.throws(()=>advanceAlongRoute(bypass,unsafe,{x:.5,z:.5},0,8),/object blocks/);
const detour=planFromPosition(bypass,{x:.5,z:.5},{waypoints:[{position:[4.5,0,.5]}]});
assert.ok(detour.total_distance_m>8);
assert.deepEqual(detour.waypoints[0].position,[.5,0,.5]);
let position={x:detour.waypoints[0].position[0],z:detour.waypoints[0].position[2]};
for(let t=0;t<detour.duration_s;t+=1/30)position=advanceAlongRoute(bypass,detour,position,t,Math.min(detour.duration_s,t+1/30));
assert.ok(Math.hypot(position.x-4.5,position.z-.5)<.001);
let actual=sampleRoute(tour,0);
for(let t=0;t<tour.duration_s;t+=1/30)actual=advanceAlongRoute(nav,tour,actual,t,Math.min(tour.duration_s,t+1/30));
console.log('Passed swept movement: object tunneling rejected, detour followed, and full real-scene tour simulated at 30Hz.');
const alternatives={grid_meta:{nx:7,nz:7,res:1,x_min:0,z_min:0},
 room_walkable_grid:Array.from({length:7},(_,x)=>Array.from({length:7},(_,z)=>x===3&&z>0&&z<6?0:1)),
 clearance_grid_m:Array.from({length:7},()=>Array.from({length:7},(_,z)=>z<3?.27:10))};
const shortest=planFromPosition(alternatives,{x:.5,z:2.5},{waypoints:[{position:[6.5,0,2.5]}]});
assert.ok(Math.min(...shortest.waypoints.map(w=>w.position[2]))<1,'Choose the shorter lower passage despite extra clearance on the longer upper passage.');
console.log('Passed shortest-distance preference with competing safe passages.');
