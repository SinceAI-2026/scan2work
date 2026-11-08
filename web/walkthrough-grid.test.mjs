import test from 'node:test';
import assert from 'node:assert/strict';
import {sourceSquareClearance} from './walkthrough-grid.mjs';
import {withWalkthroughPassages} from './walkthrough-navigation.mjs';
import {mapObstacleMask,mapPoint,mapWorldPoint} from './game-map.mjs';
import {blockBox} from './temporary-obstacles.mjs';

function fixture(){
 const m={res:.05,x_min:0,z_min:0,nx:40,nz:30,robot_radius_m:.15,safety_margin_m:.08};
 const free=Array.from({length:m.nx},()=>Array(m.nz).fill(1));free[24][16]=0;
 return {grid_meta:m,walkable_grid:free,room_walkable_grid:free,
   clearance_grid_m:free.map(r=>r.map(()=>1)),obstacle_cells_xz:[[.725,.625]],start_xz:[.175,.175]};
}
const poses=[{position:[.175,1.1,.175]}];

test('refined distances retain original occupied square size, including explicit temporary-cell sizes',()=>{
 const m={res:.025,x_min:0,z_min:0,nx:36,nz:26},obstacles=[[.425,.325],[.6125,.5125,.025]];
 const distances=sourceSquareClearance(m,obstacles,.05);
 for(let i=0;i<m.nx;i++)for(let j=0;j<m.nz;j++){
  const x=(i+.5)*m.res,z=(j+.5)*m.res;
  const expected=Math.min(...obstacles.map(([a,b,size=.05])=>Math.hypot(Math.max(0,Math.abs(x-a)-size/2),Math.max(0,Math.abs(z-b)-size/2))));
  assert.ok(Math.abs(distances[i][j]-expected)<1e-12);
 }
});

test('refinement is source-immutable, whole-cell conservative, and does not shrink the technician',()=>{
 const source=fixture(),before=JSON.stringify(source),fine=withWalkthroughPassages(source,[],poses);
 assert.equal(fine.grid_meta.res,.025);assert.equal(fine.grid_meta.nx,80);assert.equal(fine.obstacle_cell_size,.05);
 assert.equal(fine.grid_meta.robot_radius_m,.10);assert.equal(fine.grid_meta.safety_margin_m,.01);
 assert.equal(fine.obstacle_cells_xz,source.obstacle_cells_xz);
 assert.equal(fine.clearance_grid_m.length,80);assert.equal(fine.clearance_grid_m[0].length,60);
 for(let i=0;i<80;i++)for(let j=0;j<60;j++)if(fine.walkable_grid[i][j]){
  const x=(i+.5)*.025,z=(j+.5)*.025,halfSum=(.05+.025)/2;
  assert.ok(Math.hypot(Math.max(0,Math.abs(x-.725)-halfSum),Math.max(0,Math.abs(z-.625)-halfSum))>=.11-1e-12);
 }
 assert.equal(JSON.stringify(source),before);
 assert.equal(withWalkthroughPassages(fine,[],poses),fine);
 const blocked=blockBox(fine,{x:.3,z:.3,size:.15});
 assert.equal(withWalkthroughPassages(blocked,[],poses),blocked);
 assert.equal(blocked.walkable_grid[12][12],0);
 assert.ok(blocked.obstacle_cells_xz.slice(source.obstacle_cells_xz.length).every(p=>p[2]===.025));
});

test('source-grid edit floor is resampled without shifting its world position',()=>{
 const source=fixture(),floor=source.walkable_grid.map(r=>r.map(()=>0));floor[24][16]=1;
 const fine=withWalkthroughPassages(source,[],poses,{floor,profile:{robot_radius_m:.10,safety_margin_m:.01}});
 for(let di=0;di<2;di++)for(let dj=0;dj<2;dj++)assert.equal(fine.walkable_grid[48+di][32+dj],1);
 assert.equal(source.walkable_grid[24][16],0);
 const coarse=withWalkthroughPassages(source,[],poses,null,{planningRes:.05});
 assert.equal(coarse.grid_meta.res,.05);assert.equal(coarse.walkthrough_refinement,undefined);
});

test('map retains four fine cells per source obstacle and precise floor hit round trips',()=>{
 const nav={grid_meta:{res:.025,x_min:0,z_min:0,nx:8,nz:8},obstacle_cell_size:.05,
   obstacle_cells_xz:[[.075,.075],[.1625,.1625,.025]]};
 const mask=mapObstacleMask(nav);
 assert.equal(mask.flatMap(r=>Array.from(r)).reduce((a,b)=>a+b,0),5);
 for(const [i,j] of [[2,2],[2,3],[3,2],[3,3],[6,6]])assert.equal(mask[i][j],1);
 for(const p of [{x:.0125,z:.0125},{x:.0875,z:.1375},{x:.1875,z:.1875}]){
  const pixel=mapPoint(nav.grid_meta,678,550,p.x,p.z),result=mapWorldPoint(nav.grid_meta,678,550,...pixel);
  assert.ok(Math.hypot(result.x-p.x,result.z-p.z)<1e-12);
 }
});
