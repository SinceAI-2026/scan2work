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
let twin,source,current,start,routes,coarseRoutes,sourceBefore;
if(available){
 twin=read(baseline+'/digital_twin.json');
 sourceBefore=JSON.stringify(twin.navigation);
 source=withWalkthroughPassages(twin.navigation,twin.assets,read(baseline+'/walk_poses.json'),null,{planningRes:twin.navigation.grid_meta.res});
 current=withTechnicianProfile(withWalkthroughPassages(twin.navigation,twin.assets,read(baseline+'/walk_poses.json')));
 start={x:source.start_xz[0],z:source.start_xz[1]};
 const buildRoutes=n=>{const routeTo=point=>planFromPosition(n,start,floorDestination(n,start,point));return {central:routeTo({x:.025,z:-.025}),lower:routeTo({x:-3.275,z:-1.875}),
  target:planFromPosition(n,start,assetDestination(n,start,twin.assets.find(a=>a.tag==='CV-003'))),tour:planRoomTour(n,start)};};
 coarseRoutes=buildRoutes(source);routes=buildRoutes(current);
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
 const h=Array.isArray(half)?half:[half,half];
 const delta=[b[0]-a[0],b[1]-a[1]],length2=delta[0]**2+delta[1]**2;
 let enter=0,leave=1;
 for(let k=0;k<2;k++){
  if(Math.abs(delta[k])<1e-12){if(a[k]<center[k]-h[k]||a[k]>center[k]+h[k])leave=-1;}
  else{const t0=(center[k]-h[k]-a[k])/delta[k],t1=(center[k]+h[k]-a[k])/delta[k];enter=Math.max(enter,Math.min(t0,t1));leave=Math.min(leave,Math.max(t0,t1));}
 }
 if(enter<=leave)return 0;
 let gap=Math.min(...[a,b].map(p=>Math.hypot(Math.max(0,Math.abs(p[0]-center[0])-h[0]),Math.max(0,Math.abs(p[1]-center[1])-h[1]))));
 for(const dx of [-h[0],h[0]])for(const dz of [-h[1],h[1]]){
  const corner=[center[0]+dx,center[1]+dz];
  const t=Math.max(0,Math.min(1,((corner[0]-a[0])*delta[0]+(corner[1]-a[1])*delta[1])/Math.max(1e-20,length2)));
  gap=Math.min(gap,Math.hypot(corner[0]-a[0]-t*delta[0],corner[1]-a[1]-t*delta[1]));
 }
 return gap;
}

test('all original 7225 reachable cells retain their complete world coverage after refinement',options,()=>{
 assert.equal(twin.assets.length,504);
 assert.equal(twin.navigation.grid_meta.robot_radius_m,.15,'stored profile remains distinct from runtime profile');
 assert.equal(source.grid_meta.robot_radius_m,.10);assert.equal(source.grid_meta.safety_margin_m,.01);
 const floor=reachableFloor(source,start);assert.equal(floor.length,7225);
 assert.equal(current.grid_meta.res,.025);assert.equal(current.grid_meta.robot_radius_m,.10);
 assert.equal(current.grid_meta.safety_margin_m,.01);
 const m=current.grid_meta,index=(x,z)=>[Math.floor((x-m.x_min)/m.res),Math.floor((z-m.z_min)/m.res)];
 for(const p of floor)for(const dx of [-.0125,.0125])for(const dz of [-.0125,.0125]){
  const [i,j]=index(p.x+dx,p.z+dz);
  assert.equal(current.walkable_grid[i][j],1,`previously admitted source cell ${p.x},${p.z} lost child ${i},${j}`);
 }
 assert.ok(reachableFloor(current,start).length>=floor.length*4);
 assert.equal(JSON.stringify(twin.navigation),sourceBefore,'refinement cannot rewrite source navigation');
 assert.strictEqual(withTechnicianProfile(current).walkable_grid,current.walkable_grid);
 assert.strictEqual(withWalkthroughPassages(current,twin.assets,read(baseline+'/walk_poses.json')).walkable_grid,current.walkable_grid,'reentry must not subdivide or reopen cells');
});

test('central passage, disconnected lower room, target and tour support manual and automatic walking',options,()=>{
 let legacySamples=0,samples=0;
 for(const route of Object.values(coarseRoutes))legacySamples+=manualAudit(source,route,twin.assets);
 for(const route of Object.values(routes))samples+=manualAudit(current,route,twin.assets);
 assert.ok(legacySamples>=5800,'retain the previous full route coverage');
 assert.ok(samples>0,'audit every new route at intervals of at most0.01 scene units');
 assert.ok(routes.central.total_distance_m<3);
 assert.ok(coarseRoutes.lower.total_distance_m>10,'legacy grid still requires its earlier detour');
 assert.ok(routes.lower.total_distance_m<=coarseRoutes.lower.total_distance_m,'new shortcuts must still pass continuous collision audits');
});

test('complete route segments preserve the original capsule clearance from occupied-cell squares',options,()=>{
 const required=current.grid_meta.robot_radius_m+current.grid_meta.safety_margin_m;
 for(const [name,route] of Object.entries(routes))for(let i=1;i<route.waypoints.length;i++){
  const a=route.waypoints[i-1].position,b=route.waypoints[i].position;
  const gap=Math.min(...current.obstacle_cells_xz.map(o=>segmentSquareGap([a[0],a[2]],[b[0],b[2]],o,twin.navigation.grid_meta.res/2)));
  assert.ok(gap>=required,`${name} segment${i} has ${gap} clearance, less than source profile${required}`);
 }
 for(const [name,route] of Object.entries(routes))assert.ok(exactPhysicalGaps(route).boxGap>=required,`${name} route must also clear the exact oriented equipment geometry`);
});

test('temporary/moved-object blocks stay blocked when the visual profile is reapplied',options,()=>{
 const blocked=blockBox(current,{x:.025,z:-.025,size:.3});
 const reapplied=withTechnicianProfile(withWalkthroughPassages(blocked,twin.assets,read(baseline+'/walk_poses.json'))),m=reapplied.grid_meta;
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

function exactPhysicalGaps(route){
 let rawGap=Infinity,boxGap=Infinity;
 for(let i=1;i<route.waypoints.length;i++){
  const a=route.waypoints[i-1].position,b=route.waypoints[i].position;
  // The planning grid is finer; physical source squares remain 0.05 wide.
  for(const o of twin.navigation.obstacle_cells_xz)rawGap=Math.min(rawGap,
   segmentSquareGap([a[0],a[2]],[b[0],b[2]],o,twin.navigation.grid_meta.res/2));
  for(const asset of twin.assets){
   if(asset.tier!=='equipment'||asset.center[1]-asset.size[1]/2>1.62||asset.center[1]+asset.size[1]/2<.10)continue;
   const c=Math.cos(asset.yaw),s=Math.sin(asset.yaw),local=p=>{
    const x=p[0]-asset.center[0],z=p[2]-asset.center[2];return[x*c+z*s,-x*s+z*c];
   };
   boxGap=Math.min(boxGap,segmentSquareGap(local(a),local(b),[0,0],[asset.size[0]/2,asset.size[2]/2]));
  }
 }
 return {rawGap,boxGap};
}

test('left passage works in both directions without reducing source-square or equipment clearance',options,()=>{
 const lower={x:-3.075,z:.125},upper={x:-2.725,z:1.675};
 const legacy=planFromPosition(source,lower,floorDestination(source,lower,upper));
 assert.ok(legacy.total_distance_m>15,'retain evidence of the coarse-grid detour');
 for(const [from,to] of [[lower,upper],[upper,lower]]){
  const route=planFromPosition(current,from,floorDestination(current,from,to));
  assert.ok(route.total_distance_m<2,'passage should connect locally instead of circling the room');
  assert.ok(manualAudit(current,route,twin.assets)>150);
  const gaps=exactPhysicalGaps(route),required=.10+.01;
  assert.ok(gaps.rawGap>=required,`source 5cm occupied-square gap ${gaps.rawGap} < ${required}`);
  assert.ok(gaps.boxGap>=required,`equipment boundary gap ${gaps.boxGap} < ${required}`);
 }
});

test('a newly placed obstacle closes the recovered neck and survives profile reentry',options,()=>{
 const lower={x:-3.075,z:-.125},upper={x:-2.725,z:1.675};
 const blocked=blockBox(current,{x:-2.8875,z:.3125,size:.35});
 const replay=withWalkthroughPassages(withTechnicianProfile(blocked),twin.assets,read(baseline+'/walk_poses.json'));
 assert.strictEqual(replay.walkable_grid,blocked.walkable_grid);
 const route=planFromPosition(replay,lower,floorDestination(replay,lower,upper));
 assert.ok(route.total_distance_m>10,'obstacle must force a detour, not be overwritten by camera evidence');
 for(let i=1;i<route.waypoints.length;i++)assert.ok(segmentIsFree(replay,route.waypoints[i-1].position,route.waypoints[i].position));
});
