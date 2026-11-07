/** Actual prepared-room regressions: retain the robot's source capsule and observed passages.
 * Run with VALMET_BASELINE_DATA_DIR and VALMET_REVIEW_DATA_DIR to use other local copies.
 * The supplied data is intentionally not duplicated into source control.
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync,existsSync} from 'node:fs';
import {resolve,dirname} from 'node:path';
import {fileURLToPath} from 'node:url';
import {withWalkthroughPassages} from '../web/walkthrough-navigation.mjs';
import {withTechnicianProfile} from '../web/technician-navigation.mjs';
import {manualObstacles,manualStep} from '../web/manual-walk.mjs';
import {reachableFloor,planFromPosition,floorDestination,assetDestination,planRoomTour,segmentIsFree} from '../web/navigation-view.mjs';
import {blockBox} from '../web/temporary-obstacles.mjs';

const root=resolve(dirname(fileURLToPath(import.meta.url)),'..');
const baseline=process.env.VALMET_BASELINE_DATA_DIR||resolve(root,'../Wael-baseline-stage-2026-11-07');
const review=process.env.VALMET_REVIEW_DATA_DIR||resolve(root,'web/data');
const available=existsSync(baseline+'/digital_twin.json')&&existsSync(baseline+'/walk_poses.json');
const read=path=>JSON.parse(readFileSync(path,'utf8'));
let twin,source,current,start,routes;
if(available){
 twin=read(baseline+'/digital_twin.json');
 source=withWalkthroughPassages(twin.navigation,twin.assets,read(baseline+'/walk_poses.json'));
 current=withTechnicianProfile(source);
 start={x:source.start_xz[0],z:source.start_xz[1]};
 const routeTo=point=>planFromPosition(current,start,floorDestination(current,start,point));
 routes={central:routeTo({x:.025,z:-.025}),lower:routeTo({x:-3.275,z:-1.875}),
  target:planFromPosition(current,start,assetDestination(current,start,twin.assets.find(a=>a.tag==='CV-003'))),
  tour:planRoomTour(current,start)};
}
const options={skip:!available&&'Prepared original baseline not installed'};

function manualAudit(navigation,route,assets){
 const obstacles=manualObstacles(assets,navigation.grid_meta.robot_radius_m);
 let samples=0;
 for(let i=1;i<route.waypoints.length;i++){
  const a=route.waypoints[i-1].position,b=route.waypoints[i].position;
  assert.ok(segmentIsFree(navigation,a,b),'the full continuous route segment must remain in admitted cells');
  const steps=Math.max(1,Math.ceil(Math.hypot(b[0]-a[0],b[2]-a[2])/.01));
  for(let k=1;k<=steps;k++){
   const p={x:a[0]+(b[0]-a[0])*(k-1)/steps,z:a[2]+(b[2]-a[2])*(k-1)/steps};
   const q={x:a[0]+(b[0]-a[0])*k/steps,z:a[2]+(b[2]-a[2])*k/steps};
   const actual=manualStep(navigation,obstacles,p,q.x-p.x,q.z-p.z);
   assert.ok(Math.hypot(actual.x-q.x,actual.z-q.z)<1e-8,'manual walking must follow the same admitted route');samples++;
  }
 }
 return samples;
}

// Exact minimum distance between a segment and a closed occupied-cell square.
// Endpoints + rectangle corners cover disjoint convex boundaries; slab intersection handles crossing.
function segmentSquareGap(a,b,center,half){
 const delta=[b[0]-a[0],b[1]-a[1]],length2=delta[0]**2+delta[1]**2;
 let enter=0,leave=1;
 for(let k=0;k<2;k++){
  if(Math.abs(delta[k])<1e-12){if(a[k]<center[k]-half||a[k]>center[k]+half)leave=-1;}
  else{const t0=(center[k]-half-a[k])/delta[k],t1=(center[k]+half-a[k])/delta[k];enter=Math.max(enter,Math.min(t0,t1));leave=Math.min(leave,Math.max(t0,t1));}
 }
 if(enter<=leave)return 0;
 let gap=Math.min(...[a,b].map(p=>Math.hypot(Math.max(0,Math.abs(p[0]-center[0])-half),Math.max(0,Math.abs(p[1]-center[1])-half))));
 for(const dx of [-half,half])for(const dz of [-half,half]){
  const corner=[center[0]+dx,center[1]+dz];
  const t=Math.max(0,Math.min(1,((corner[0]-a[0])*delta[0]+(corner[1]-a[1])*delta[1])/Math.max(1e-20,length2)));
  gap=Math.min(gap,Math.hypot(corner[0]-a[0]-t*delta[0],corner[1]-a[1]-t*delta[1]));
 }
 return gap;
}

test('original 7225 reachable cells and source clearance profile survive the technician',options,()=>{
 assert.equal(twin.assets.length,504);
 assert.equal(twin.navigation.grid_meta.robot_radius_m,.15,'stored profile is distinct from runtime walkthrough profile');
 assert.equal(source.grid_meta.robot_radius_m,.10);assert.equal(source.grid_meta.safety_margin_m,.01);
 assert.equal(reachableFloor(source,start).length,7225);
 assert.equal(reachableFloor(current,start).length,7225);
 assert.strictEqual(current.walkable_grid,source.walkable_grid);
 assert.strictEqual(current.room_walkable_grid,source.room_walkable_grid);
 assert.strictEqual(current.grid_meta,source.grid_meta);
 assert.strictEqual(withTechnicianProfile(current).walkable_grid,current.walkable_grid,'reapplying profile must not erode cells');
});

test('central passage, disconnected lower room, target and tour support manual and automatic walking',options,()=>{
 let samples=0;
 for(const route of Object.values(routes))samples+=manualAudit(current,route,twin.assets);
 assert.ok(samples>=5800,'audit every route at intervals of at most0.01 scene units');
 assert.ok(routes.central.total_distance_m<3);
 assert.ok(routes.lower.total_distance_m>10,'route must follow the connected lower passage rather than cut across equipment');
});

test('complete route segments preserve the original capsule clearance from occupied-cell squares',options,()=>{
 const required=current.grid_meta.robot_radius_m+current.grid_meta.safety_margin_m;
 for(const [name,route] of Object.entries(routes))for(let i=1;i<route.waypoints.length;i++){
  const a=route.waypoints[i-1].position,b=route.waypoints[i].position;
  const gap=Math.min(...current.obstacle_cells_xz.map(o=>segmentSquareGap([a[0],a[2]],[b[0],b[2]],o,current.grid_meta.res/2)));
  assert.ok(gap>=required,`${name} segment${i} has ${gap} clearance, less than source profile${required}`);
 }
});

test('temporary/moved-object blocks stay blocked when the visual profile is reapplied',options,()=>{
 const blocked=blockBox(current,{x:.025,z:-.025,size:.3});
 const reapplied=withTechnicianProfile(blocked),m=reapplied.grid_meta;
 assert.strictEqual(reapplied.walkable_grid,blocked.walkable_grid);
 let changed=0;
 for(let i=0;i<m.nx;i++)for(let j=0;j<m.nz;j++){
  assert.ok(!reapplied.walkable_grid[i][j]||current.walkable_grid[i][j]);
  if(current.walkable_grid[i][j]&&!reapplied.walkable_grid[i][j])changed++;
 }
 assert.ok(changed>0);
 assert.throws(()=>floorDestination(reapplied,start,{x:.025,z:-.025}),/blocks/);
 const from={x:.025,z:.7},to={x:.025,z:-.025};
 assert.notDeepEqual(manualStep(reapplied,manualObstacles(twin.assets,m.robot_radius_m),from,to.x-from.x,to.z-from.z),to);
});

test('expanded extraction keeps its separate imported occupancy and source radius',
 {skip:!existsSync(review+'/digital_twin.json')&&'Expanded dataset not installed'},()=>{
 const twin=read(review+'/digital_twin.json'),nav=twin.navigation,result=withTechnicianProfile(nav);
 assert.strictEqual(result.walkable_grid,nav.walkable_grid);
 assert.strictEqual(result.grid_meta,nav.grid_meta);
 assert.equal(result.technician_profile.radius,nav.grid_meta.robot_radius_m);
});
