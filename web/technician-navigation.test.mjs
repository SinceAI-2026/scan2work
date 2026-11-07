import test from 'node:test';
import assert from 'node:assert/strict';
import {withTechnicianProfile} from './technician-navigation.mjs';
import {planFromPosition,advanceAlongRoute,sampleRoute,planRoomTour,segmentIsFree} from './navigation-view.mjs';
import {manualStep} from './manual-walk.mjs';
import {blockBox} from './temporary-obstacles.mjs';
function fixture(){
 const n=31,res=.1,m={nx:n,nz:n,res,x_min:0,z_min:0,robot_radius_m:.1,safety_margin_m:.01,required_clearance_m:.18};
 const obstacles=[];
 for(let i=0;i<n;i++)for(let j=0;j<n;j++)if(!i||!j||i===n-1||j===n-1||(i===15&&j<=20))obstacles.push([(i+.5)*res,(j+.5)*res]);
 const clearance=Array.from({length:n},(_,i)=>Array.from({length:n},(_,j)=>Math.min(...obstacles.map(([x,z])=>Math.hypot((i+.5)*res-x,(j+.5)*res-z)))));
 return {grid_meta:m,walkable_grid:clearance.map(row=>row.map(d=>Number(d>.18))),clearance_grid_m:clearance,obstacle_cells_xz:obstacles};
}
const gapToSquare=(p,o,res)=>Math.hypot(Math.max(0,Math.abs(p.x-o[0])-res/2),Math.max(0,Math.abs(p.z-o[1])-res/2));
test('fitted technician keeps source-admitted corridors while manual and automatic routes avoid a thin wall',()=>{
 const source=fixture(),nav=withTechnicianProfile(source),start={x:.55,z:.55};
 assert.strictEqual(nav.walkable_grid,source.walkable_grid);
 const route=planFromPosition(nav,start,{waypoints:[{position:[2.55,0,.55]}]});
 assert.ok(route.total_distance_m>4,'must go around the wall');
 assert.equal(segmentIsFree(nav,[.55,0,.55],[2.55,0,.55]),false);
 let previous=start;
 for(let t=.01;t<=route.duration_s+.01;t+=.01){
  const time=Math.min(t,route.duration_s),p=sampleRoute(route,time);
  assert.ok(Math.min(...nav.obstacle_cells_xz.map(o=>gapToSquare(p,o,nav.grid_meta.res)))>nav.technician_profile.footprintRadius);
  assert.deepEqual(manualStep(nav,[],previous,p.x-previous.x,p.z-previous.z),{x:p.x,z:p.z});
  previous={x:p.x,z:p.z};
 }
 const last=advanceAlongRoute(nav,route,start,0,route.duration_s);assert.equal(last.x,2.55);
 const tour=planRoomTour(nav,start);assert.ok(tour.total_distance_m>0);advanceAlongRoute(nav,tour,start,0,tour.duration_s);
});
test('visual height changes neither reopen dynamic obstacles nor add another equipment-envelope erosion',()=>{
 const source=fixture(),asset={tier:'equipment',center:[.75,.5,2.5],size:[.2,1,.3],yaw:.4};
 const nav=withTechnicianProfile(source,{height:1.1,assets:[asset]});
 assert.strictEqual(nav.walkable_grid,source.walkable_grid,'source grid is already the collision mask; do not add coarse envelopes twice');
 const blocked=blockBox(nav,{x:.55,z:.55,size:.2}),again=withTechnicianProfile(blocked,{height:1.3});
 assert.equal(again.walkable_grid[5][5],0);assert.equal(again.source_floor_grid,nav.source_floor_grid);
 assert.strictEqual(again.walkable_grid,blocked.walkable_grid);
 assert.equal(again.grid_meta.robot_radius_m,source.grid_meta.robot_radius_m);
 assert.equal(again.technician_profile.radius,.1);
});
