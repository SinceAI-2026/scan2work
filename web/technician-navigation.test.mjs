import test from 'node:test';
import assert from 'node:assert/strict';
import {withTechnicianClearance,TECHNICIAN_RADIUS} from './technician-navigation.mjs';
import {planFromPosition,advanceAlongRoute,sampleRoute,planRoomTour} from './navigation-view.mjs';
import {manualObstacles,manualStep} from './manual-walk.mjs';
import {blockBox} from './temporary-obstacles.mjs';
function fixture(){
 const n=31,res=.1,m={nx:n,nz:n,res,x_min:0,z_min:0,robot_radius_m:.1};
 const obstacles=[];
 for(let i=0;i<n;i++)for(let j=0;j<n;j++)if(!i||!j||i===n-1||j===n-1||(i===15&&j<=20))obstacles.push([(i+.5)*res,(j+.5)*res]);
 const clearance=Array.from({length:n},(_,i)=>Array.from({length:n},(_,j)=>Math.min(...obstacles.map(([x,z])=>Math.hypot((i+.5)*res-x,(j+.5)*res-z)))));
 return {grid_meta:m,walkable_grid:clearance.map(row=>row.map(d=>Number(d>.15))),clearance_grid_m:clearance,obstacle_cells_xz:obstacles};
}
const gapToSquare=(p,o,res)=>Math.hypot(Math.max(0,Math.abs(p.x-o[0])-res/2),Math.max(0,Math.abs(p.z-o[1])-res/2));
test('full route segments and manual walking honor the wider capsule around a thin wall',()=>{
 const nav=withTechnicianClearance(fixture()),start={x:.55,z:.55};
 const route=planFromPosition(nav,start,{waypoints:[{position:[2.55,0,.55]}]});
 assert.ok(route.total_distance_m>4,'must go around the wall');
 let previous=start;
 for(let t=.01;t<=route.duration_s+.01;t+=.01){
  const time=Math.min(t,route.duration_s),p=sampleRoute(route,time);
  assert.ok(Math.min(...nav.obstacle_cells_xz.map(o=>gapToSquare(p,o,nav.grid_meta.res)))>TECHNICIAN_RADIUS);
  assert.deepEqual(manualStep(nav,[],previous,p.x-previous.x,p.z-previous.z),{x:p.x,z:p.z});
  previous={x:p.x,z:p.z};
 }
 const last=advanceAlongRoute(nav,route,start,0,route.duration_s);assert.equal(last.x,2.55);
 const tour=planRoomTour(nav,start);assert.ok(tour.total_distance_m>0);advanceAlongRoute(nav,tour,start,0,tour.duration_s);
});
test('equipment envelope cells and moved-object blocks cannot disagree with manual walking',()=>{
 const asset={tier:'equipment',center:[.75,.5,2.5],size:[.2,1,.3],yaw:.4};
 const nav=withTechnicianClearance(fixture(),{assets:[asset]}),m=nav.grid_meta,boxes=manualObstacles([asset],TECHNICIAN_RADIUS);
 for(let i=0;i<m.nx;i++)for(let j=0;j<m.nz;j++)if(nav.walkable_grid[i][j]){
  const start={x:(i+.5)*m.res,z:(j+.5)*m.res},end=manualStep(nav,boxes,start,.001,.001);
  assert.notEqual(end,start,'admitted cells must not be inside an equipment collider');
 }
 const blocked=blockBox(nav,{x:.55,z:.55,size:.2});assert.equal(blocked.walkable_grid[5][5],0);assert.equal(blocked.source_floor_grid,nav.source_floor_grid);
});
