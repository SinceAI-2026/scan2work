import test from 'node:test';
import assert from 'node:assert/strict';
import * as THREE from './vendor/three/three.module.js';
import {makeTechnician,advanceGait,technicianCameraOffset} from './technician-avatar.mjs';
import {summaryFor} from './device-summary.mjs';
import {visibleEnergy,energyDescription} from './hazard-layer.mjs';
import {createSafetyStory} from './safety-demo.mjs';
import {withTechnicianClearance,TECHNICIAN_RADIUS} from './technician-navigation.mjs';

test('technician faces +Z, rests on the floor and preserves recognizable uniform proportions',()=>{
 const avatar=makeTechnician(THREE,{height:1.4});
 avatar.root.updateMatrixWorld(true);const bounds=new THREE.Box3().setFromObject(avatar.root);
 assert.ok(Math.abs(bounds.min.y)<1e-5);assert.ok(Math.abs(bounds.max.y-1.4)<1e-5);
 for(let i=0;i<180;i++){
  avatar.animate(.016,.016);avatar.root.updateMatrixWorld(true);
  avatar.root.traverse(mesh=>{if(!mesh.geometry)return;const pos=mesh.geometry.getAttribute('position');for(let p=0;p<pos.count;p++){const v=new THREE.Vector3().fromBufferAttribute(pos,p).applyMatrix4(mesh.matrixWorld);assert.ok(Math.hypot(v.x,v.z)<=TECHNICIAN_RADIUS,`footprint ${v.x},${v.z}`);}});
 }
 const glasses=avatar.root.children.find(x=>x.position.z===.125);assert.ok(glasses,'safety glasses face +Z');
 const phase=avatar.gait.phase;for(let i=0;i<120;i++)avatar.animate(0,.016);
 assert.equal(avatar.gait.phase,phase);assert.equal(avatar.gait.amplitude,0);
});
test('gait follows actual displacement, never wall-clock motion or teleport distance',()=>{
 const initial={phase:0,amplitude:0};assert.deepEqual(advanceGait(initial,0,.1),initial);
 assert.equal(advanceGait(initial,4,.1).phase,0);assert.equal(advanceGait(initial,.1,.1).phase,advanceGait(initial,.1,.02).phase);
});
const asset={id:'OBJ-206',asset_id:'ASSET-003',tag:'CV-003',mapped:true,condition_state:'degraded',issue_summary:'Position tracking deviation',center:[2,1,-1],size:[.2,.3,.2]};
const context={devices:{'OBJ-206':{asset_id:'ASSET-003',condition_state:'healthy',snapshot_at:'2026-10-07 23:30',primary_metric:{label:'Actual position',latest:67.792,unit:'%',comparison:{value:58.66,label:'Setpoint'}},hazards:[{kind:'pressure'}]}}};
test('status summary keeps canonical condition, one sourced comparison and no unmapped readings',()=>{
 const d=summaryFor(asset,context);assert.equal(d.condition,'degraded');assert.equal(d.reading,'Actual position: 67.79 % · Setpoint: 58.66 %');
 assert.equal(summaryFor({...asset,mapped:false},context),null);assert.equal(summaryFor({...asset,asset_id:'OTHER'},context),null);
 const c=structuredClone(context);c.devices[asset.id].primary_metric.latest=0;c.devices[asset.id].simulated_emergency=true;
 assert.match(summaryFor(asset,c).reading,/0 %/);assert.equal(summaryFor(asset,c).scenario,true);
});
test('hazards use current host positions and membership, not stale server geometry',()=>{
 const before=JSON.stringify(context);context.devices[asset.id].center=[99,99,99];
 const moved={...asset,center:[.5,1,.5]};const out=visibleEnergy(context,[moved],moved,{x:0,z:0});assert.equal(out[0].asset,moved);
 assert.equal(visibleEnergy(context,[],moved,{x:0,z:0}).length,0);
 assert.equal(visibleEnergy(context,[{...moved,mapped:false}],moved,{x:0,z:0}).length,0);
 delete context.devices[asset.id].center;assert.equal(JSON.stringify(context),before);
});
test('guided demo waits for arrival, pauses, clears illustrations and never completes a job',()=>{
 const calls=[];let travel='moving';const host={capture:()=>calls.push('capture'),navigate:()=>calls.push('navigate'),travel:()=>travel,pause:()=>calls.push('pause'),resume:()=>calls.push('resume'),focus:()=>calls.push('focus'),hazard:on=>calls.push(['hazard',on]),evidence:()=>calls.push('evidence'),restore:o=>calls.push(['restore',o])};
 const story=createSafetyStory(host);story.start();story.tick(100);assert.equal(story.state.phase,'route');story.next();assert.equal(story.state.phase,'route');
 story.togglePause();travel='arrived';story.tick(100);assert.equal(story.state.phase,'route');story.togglePause();story.tick(.1);assert.equal(story.state.phase,'hazard');
 story.next();assert.equal(story.state.phase,'evidence');story.stop({takeover:true});story.tick(100);assert.deepEqual(story.state,{phase:'idle',paused:false});
 assert.deepEqual(calls.at(-1),['restore',{takeover:true}]);assert.deepEqual(calls.at(-2),['hazard',false]);assert.equal(calls.filter(x=>x==='navigate').length,1);
});
test('blocked demo route exits cleanly instead of teleporting or advancing the story',()=>{
 const events=[];const story=createSafetyStory({capture(){},navigate(){},travel:()=> 'blocked',pause(){},hazard(){},restore(){events.push('restore');},failed(){events.push('failed');}});
 story.start();story.tick(.1);assert.equal(story.state.phase,'idle');assert.deepEqual(events,['restore','failed']);
});

test('hazard descriptions distinguish recorded zero, missing and runtime scenario readings',()=>{
 const record={snapshot_at:'2026-10-07',hazards:[{kind:'current',reading:{value:0,unit:'A'}}]};
 assert.match(energyDescription(record).reading,/0 A/);assert.match(energyDescription(record).source,/Synthetic workbook/);
 assert.match(energyDescription({hazards:[]}).reading,/No measured/);
 assert.match(energyDescription({...record,provenance:{kind:'runtime_scenario'}}).source,/Scripted demo/);
});

test('follow camera tries a clear side view before using a compact overhead fallback',()=>{
 const nav={x:0,z:0};const back=technicianCameraOffset(nav,0,()=>true);assert.ok(Math.hypot(back.x,back.z)>=1.2);
 const side=technicianCameraOffset(nav,0,(a,b)=>b[0]>1);assert.ok(side.x>1);assert.ok(Math.hypot(side.x,side.z)>=1.2);
 const overhead=technicianCameraOffset(nav,0,()=>false);assert.equal(overhead.y,2.1);
});

test('technician navigation only restricts cells and preserves original floor evidence and source metadata',()=>{
 const input={grid_meta:{nx:3,nz:2,res:.05,robot_radius_m:.1},walkable_grid:[[1,1],[1,0],[1,1]],clearance_grid_m:[[.1,.5],[.2,1],[null,.6]]};
 const before=JSON.stringify(input),out=withTechnicianClearance(input);
 assert.equal(JSON.stringify(input),before);assert.deepEqual(out.walkable_grid,[[0,1],[0,0],[0,1]]);
 assert.equal(out.source_floor_grid,input.walkable_grid);assert.equal(out.grid_meta.robot_radius_m,TECHNICIAN_RADIUS);
 assert.deepEqual(withTechnicianClearance(out).walkable_grid,out.walkable_grid);
});

test('reported technician footprint contains the animated model across height variants',()=>{
 for(const height of [.7,1.4,1.65]){
  const avatar=makeTechnician(THREE,{height});
  for(let i=0;i<180;i++){
   avatar.animate(.03,.016);avatar.root.updateMatrixWorld(true);
   avatar.root.traverse(mesh=>{if(!mesh.geometry)return;const pos=mesh.geometry.getAttribute('position');for(let p=0;p<pos.count;p++){const v=new THREE.Vector3().fromBufferAttribute(pos,p).applyMatrix4(mesh.matrixWorld);assert.ok(Math.hypot(v.x,v.z)<=avatar.footprintRadius);}});
  }
  assert.equal(withTechnicianClearance({grid_meta:{res:.05},walkable_grid:[[1]],clearance_grid_m:[[1]]},{height}).grid_meta.robot_radius_m,avatar.footprintRadius);
 }
});

test('context refresh rejects out-of-order responses and clears readings on a failed update',async()=>{
 const {createContextRefresh,applyContextStatus}=await import('./scene-context-refresh.mjs');
 const pending=[],applied=[];const refresh=createContextRefresh({load:()=>new Promise((resolve,reject)=>pending.push({resolve,reject})),apply:c=>applied.push(c)});
 const first=refresh(),second=refresh();pending[1].resolve({devices:{latest:{}}});await second;pending[0].resolve({devices:{old:{}}});await first;assert.ok(applied.at(-1).devices.latest);
 const third=refresh();pending[2].reject(new Error('offline'));await third;assert.equal(applied.at(-1).unavailable,true);assert.deepEqual(applied.at(-1).devices,{});
 assert.equal(summaryFor(asset,applied.at(-1)).reading,null);assert.equal(summaryFor(asset,applied.at(-1)).condition,'unknown');
 const changed=structuredClone(asset),center=changed.center;applyContextStatus([changed],{devices:{[asset.id]:{asset_id:asset.asset_id,condition_state:'attention_required',health_score:10}}});assert.equal(changed.condition_state,'attention_required');assert.equal(changed.center,center);
 const unknown={...changed,mapped:false};applyContextStatus([unknown],{devices:{[asset.id]:{asset_id:asset.asset_id,health_score:90}}});assert.equal(unknown.health_score,10);
});
