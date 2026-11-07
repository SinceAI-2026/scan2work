/** Box-built service technician adapted from Rasmus's own makeTechnician model.
 * Source: SinceAI-2026/rasse, 5a3ca02, demo/web/src/meshes.ts.
 * +Y up, +Z forward, feet on y=0. Height is illustrative scene units, not a measurement.
 * Uniform illustrative proportions are independent of the navigation capsule; this does not certify human access.
 */
export function advanceGait(previous, distance, dt) {
  const elapsed=Math.max(0,Math.min(.1,Number.isFinite(dt)?dt:0));
  const travelled=Number.isFinite(distance)&&distance>=0&&distance<1?distance:0;
  const speed=elapsed?travelled/elapsed:0;
  const amplitude=previous.amplitude+(Math.min(.22,speed*.22)-previous.amplitude)*(1-Math.exp(-elapsed*10));
  return {phase:previous.phase+travelled/.75*Math.PI,amplitude:amplitude<1e-5?0:amplitude};
}
export function technicianDimensions(height=1.4) {
  if(!Number.isFinite(height)||height<=0)throw new Error('Technician height must be positive.');
  return {height,footprintRadius:height*.215};
}
export function makeTechnician(THREE,{height=1.4}={}) {
  const dimensions=technicianDimensions(height);
  const root=new THREE.Group(); root.name='Service technician';
  root.scale.setScalar(height/1.8);
  const colors={navy:0x23324a,vest:0xd4ee2a,stripe:0xe9eef2,skin:0xc98e68,hat:0xf2801f,glove:0x8a9099,boot:0x1b1d21};
  const add=(parent,w,h,d,x,y,z,color)=>{
    const shades=[.82,.7,1,.5,.9,.66];
    const mesh=new THREE.Mesh(new THREE.BoxGeometry(w,h,d),shades.map(shade=>new THREE.MeshBasicMaterial({color:new THREE.Color(color).multiplyScalar(shade)})));
    mesh.position.set(x,y,z); parent.add(mesh); return mesh;
  };
  // Convert the source's (x,-y,z) / +X-facing parts to (z,y,x) / +Z-facing.
  const part=(p,w,h,d,x,y,z,c)=>add(p,d,h,w,z,-y,x,c);
  const limb=(y,z)=>{const p=new THREE.Group();p.position.set(z,-y,0);root.add(p);return p;};
  const legs=[-1,1].map(side=>{const p=limb(-.86,side*.085);part(p,.16,.72,.15,0,.36,0,colors.navy);part(p,.24,.14,.16,.03,.79,0,colors.boot);return p;});
  part(root,.24,.62,.40,0,-1.16,0,colors.navy);part(root,.27,.46,.43,0,-1.22,0,colors.vest);
  for(const y of [-1.08,-1.30])part(root,.28,.04,.44,0,y,0,colors.stripe);
  const arms=[-1,1].map(side=>{const p=limb(-1.42,side*.27);part(p,.13,.46,.13,0,.23,0,colors.navy);part(p,.13,.10,.13,0,.51,0,colors.glove);return p;});
  part(root,.24,.24,.24,0,-1.59,0,colors.skin);part(root,.02,.05,.20,.125,-1.61,0,0x101820);
  part(root,.28,.10,.28,0,-1.75,0,colors.hat);part(root,.36,.02,.30,.03,-1.70,0,colors.hat);
  let gait={phase:0,amplitude:0};
  return {root,...dimensions,animate(distance,dt){gait=advanceGait(gait,distance,dt);const s=Math.sin(gait.phase)*gait.amplitude;legs[0].rotation.x=s;legs[1].rotation.x=-s;arms[0].rotation.x=-s*.8;arms[1].rotation.x=s*.8;},get gait(){return {...gait};}};
}

/** Select a clear trailing/side camera without pushing the camera into the avatar. */
export function technicianCameraOffset(nav,yaw,isFree) {
 const start=[nav.x,0,nav.z];
 for(const offset of [0,-Math.PI/4,Math.PI/4,-Math.PI/2,Math.PI/2,Math.PI]) {
  const angle=yaw+offset,dx=Math.sin(angle),dz=Math.cos(angle);
  for(let distance=1.8;distance>=1.19;distance-=.2) {
   const end=[nav.x-dx*distance,0,nav.z-dz*distance];
   if(isFree(start,end))return {x:end[0],y:1.8,z:end[2],targetY:.7};
  }
 }
 return {x:nav.x-Math.sin(yaw)*.25,y:2.1,z:nav.z-Math.cos(yaw)*.25,targetY:.25};
}
