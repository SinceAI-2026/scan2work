// Surface samples use the same colour and covariance packing as the room renderer.
export function indexRoomScan(center, covA, covB, instances, count) {
  const step = .20, columns = new Map(), owned = new Map();
  const next = new Int32Array(count);
  const key = (x,z) => x * 10000 + z;
  const bin = x => Math.floor(x / step);
  for (let i=0;i<count;i++) {
    const k = key(bin(center[i*4]),bin(center[i*4+2]));
    next[i] = columns.get(k) ?? -1;
    columns.set(k,i);
    const owner = instances[i];
    if (owner) {
      if (!owned.has(owner)) owned.set(owner,[]);
      owned.get(owner).push(i);
    }
  }
  const query = (asset, pad, visit) => {
    const [x,,z] = asset.center;
    const reach = Math.hypot(asset.size[0],asset.size[2])/2 + pad;
    for(let i=bin(x-reach);i<=bin(x+reach);i++) for(let j=bin(z-reach);j<=bin(z+reach);j++) {
      for(let n=columns.get(key(i,j))??-1;n>=0;n=next[n]) visit(n,n*4);
    }
  };
  return {center,covA,covB,instances,count,query,owned};
}

// The walkthrough and isolated scan identify the rail, caster and cage outline.
// Match the reviewed geometry because IDs can be reused after a rebuild.
export function reviewedTrolleyParts(asset, assetsById) {
  if (asset.id !== 'OBJ-390' || asset.tag !== 'MC-252' ||
      asset.center.join() !== '-1.839,0.754,0.94' || asset.size.join() !== '0.876,1.251,0.994' || asset.yaw !== 0) return null;
  const rail = assetsById.get('OBJ-035');
  if (rail?.tag !== 'FR-029' || rail.mapped || rail.is_target ||
      rail.center.join() !== '-2.119,0.246,1.087' || rail.size.join() !== '0.179,0.166,0.485' || rail.yaw !== 0) return null;
  const caster = { min: [-1.71, .041, 1.30], max: [-1.58, .14, 1.42] };
  const footprint = [[-2.064,.645],[-1.49,.731],[-1.638,1.414],[-2.20,1.334]];
  return { rail, caster, footprint, baseY: caster.min[1] };
}

export function traceRoomInstance(scan, asset, owner, assets, walls, { railOwner, caster, footprint } = {}) {
  const {center, covA, covB, instances} = scan;
  const step = .045, halo = .10;
  const [x,y,z] = asset.center, [sx,sy,sz] = asset.size;
  const c = Math.cos(asset.yaw), s = Math.sin(asset.yaw);
  const bins = new Map(), candidates = [], overlaps = [], casterPoints = [], retained = [];
  const background = new Set();
  const sampleOwner = i => {
    if (background.has(i)) return 0;
    return instances[i] === railOwner ? owner : instances[i];
  };
  // The fitted cage is slightly rotated inside the axis-aligned detection box.
  // Keep a narrow allowance for broad splats along its metal wires.
  const insideFootprint = (x,z) => !footprint || footprint.every(([ax,az],i)=>{
    const [bx,bz]=footprint[(i+1)%footprint.length],dx=bx-ax,dz=bz-az;
    return dx*(z-az)-dz*(x-ax) >= -.035*Math.hypot(dx,dz);
  });
  const cell = value => Math.floor(value / step);
  const key = (x,y,z) => x + ',' + y + ',' + z;
  const neighbours = assets.map((other,i)=>({other,id:i+1})).filter(({other,id}) => other.id !== asset.id && id !== railOwner &&
    other.aabb_min.every((v,i) => v <= asset.aabb_max[i] + halo) &&
    other.aabb_max.every((v,i) => v >= asset.aabb_min[i] - halo));
  const inside = (other, px, py, pz, margin) => {
    const [ox,oy,oz]=other.center, [a,b,d]=other.size;
    const cos=Math.cos(other.yaw), sin=Math.sin(other.yaw);
    return Math.abs(py-oy)<=b/2+margin && Math.abs((px-ox)*cos+(pz-oz)*sin)<=a/2+margin &&
      Math.abs(-(px-ox)*sin+(pz-oz)*cos)<=d/2+margin;
  };
  const smaller=neighbours.filter(({other})=>other.size[0]*other.size[1]*other.size[2]<sx*sy*sz*.6);
  scan.query(asset,halo + step,(i,k)=>{
    const px=center[k], py=center[k+1], pz=center[k+2];
    if (Math.abs(py-y) > sy/2 + halo + step) return;
    if (instances[i]) {
      if (instances[i]===owner && !insideFootprint(px,pz)) {
        background.add(i);
        retained.push([i,0]);
      }
      const bucket = key(cell(px),cell(py),cell(pz));
      if (!bins.has(bucket)) bins.set(bucket,[]);
      bins.get(bucket).push(i);
      if (sampleOwner(i)===owner && instances[i]===owner && py>.12) {
        const allowed=smaller.filter(({other})=>inside(other,px,py,pz,-.008)).map(match=>match.id);
        if (allowed.length) overlaps.push({i,allowed});
      }
      return;
    }
    if (!insideFootprint(px,pz)) return;
    if (caster && px>=caster.min[0] && px<=caster.max[0] && py>=caster.min[1] && py<=caster.max[1] &&
        pz>=caster.min[2] && pz<=caster.max[2] && covB[k+3]>=.18 && covA[k+3]>=.42*(covA[k]+covB[k+1])) {
      casterPoints.push(i);
      return;
    }
    const dx=px-x, dz=pz-z;
    if (py < .12 || covB[k+3] < .25 || Math.abs(py-y) > sy/2+halo ||
        Math.abs(dx*c+dz*s) > sx/2+halo || Math.abs(-dx*s+dz*c) > sz/2+halo) return;
    if (neighbours.some(({other})=>inside(other,px,py,pz,.02))) return;
    if (walls.some(({from,to})=>{
      const dx=to[0]-from[0], dz=to[1]-from[1];
      return Math.abs((px-from[0])*dz-(pz-from[1])*dx)/Math.hypot(dx,dz)<.10;
    })) return;
    candidates.push(i);
  });
  function nearby(i,visit) {
    const k=i*4, x=center[k], y=center[k+1], z=center[k+2], rgb=center[k+3];
    const r=rgb%256, g=Math.floor(rgb/256), b=covB[k+2]*255;
    const a=cell(x), h=cell(y), d=cell(z);
    for(let u=a-1;u<=a+1;u++) for(let v=h-1;v<=h+1;v++) for(let w=d-1;w<=d+1;w++) {
      for(const j of bins.get(key(u,v,w)) || []){
        const p=j*4, distance=(center[p]-x)**2+(center[p+1]-y)**2+(center[p+2]-z)**2;
        if (distance>step**2) continue;
        const colour=center[p+3];
        const difference=Math.abs(colour%256-r)+Math.abs(Math.floor(colour/256)-g)+Math.abs(covB[p+2]*255-b);
        visit(sampleOwner(j),distance,difference);
      }
    }
  }
  const recovered=candidates.filter(i=>{
    let matching=0, same=Infinity, other=Infinity;
    nearby(i,(id,distance,difference)=>{
      if(id!==owner) other=Math.min(other,distance);
      else if(difference<=70){matching++;same=Math.min(same,distance);}
    });
    return matching>=3 && same<=.027**2 && other>same*2;
  });
  // A smaller overlapping object can retain points its own scan supports nearby.
  for(const {i,allowed} of overlaps){
    const support=new Map();
    nearby(i,(id,distance,difference)=>{
      if(difference>45 || distance>.025**2 || !allowed.includes(id)) return;
      const match=support.get(id) || {count:0,distance:Infinity};
      match.count++;
      match.distance=Math.min(match.distance,distance);
      support.set(id,match);
    });
    const match=[...support].filter(([,v])=>v.count>=4 && v.distance<=.012**2)
      .sort((a,b)=>a[1].distance-b[1].distance)[0];
    if (match) retained.push([i,match[0]]);
  }
  return {recovered:[...recovered,...casterPoints],retained};
}

function median(values) {
  const sorted = values.slice().sort((a,b)=>a-b);
  return sorted[Math.floor(sorted.length/2)];
}

function surfaceSamples(scan, asset, removed) {
  const {center,covB,instances}=scan;
  const [cx,,cz]=asset.center;
  const samples=[];
  scan.query(asset,.65,(i,k)=>{
    if (removed.has(instances[i]) || covB[k+3]<.22) return;
    const y=center[k+1];
    if(y<-.09 || y>asset.center[1]+asset.size[1]/2+.45) return;
    const rgb=center[k+3];
    const brightness=(rgb%256 + Math.floor(rgb/256) + covB[k+2]*255)/3;
    samples.push({x:center[k]-cx,y,z:center[k+2]-cz,k,owner:instances[i],brightness});
  });
  return samples;
}

function nearestEight(ring,u,v) {
  const nearest = [];
  let worst = 0;
  for (const p of ring) {
    const d=(p.u-u)**2+(p.v-v)**2;
    if (nearest.length===8 && d>=nearest[worst].d) continue;
    if(nearest.length<8) nearest.push({p,d});
    else nearest[worst]={p,d};
    worst=0;
    for(let i=1;i<nearest.length;i++) if(nearest[i].d>nearest[worst].d) worst=i;
  }
  return nearest;
}

function tiledFill(scan, asset, surface, output) {
  const {ring,existing,u0,u1,v0,v1,area,position,axes,shadowFade}=surface;
  if(ring.length<8 || u1<=u0 || v1<=v0) return;
  const spacing=Math.max(.012,Math.min(.065,Math.sqrt(area/ring.length)));
  const cols=Math.ceil((u1-u0)/spacing),rows=Math.ceil((v1-v0)/spacing);
  const samples = new Map();
  const key=(u,v)=>u*10000+v;
  // Give each patch of the surrounding surface one vote, even where the scan is dense.
  const donorCells=new Map(), donorStep=Math.max(.025,spacing);
  for(const p of ring) {
    const k=key(Math.floor(p.u/donorStep),Math.floor(p.v/donorStep));
    if(!donorCells.has(k)) donorCells.set(k,[]);
    donorCells.get(k).push(p);
  }
  const donors=[...donorCells.values()].map(points=>
    points.sort((a,b)=>a.brightness-b.brightness)[Math.floor(points.length/2)]);
  if(donors.length<8) return;

  const [ux,uy,uz]=axes[0],[vx,vy,vz]=axes[1];
  const {center,covA,covB}=scan;
  const dotCov=(k,x,y,z,a,b,c)=> x*(covA[k]*a+covA[k+1]*b+covA[k+2]*c)+
    y*(covA[k+1]*a+covA[k+3]*b+covB[k]*c)+z*(covA[k+2]*a+covB[k]*b+covB[k+1]*c);
  for(const p of existing){
    const k=key(Math.floor(p.u/spacing),Math.floor(p.v/spacing));
    if(!samples.has(k)) samples.set(k,[]);
    const minVariance=(spacing*.24)**2, maxVariance=spacing**2;
    const uu=Math.max(minVariance,Math.min(maxVariance,dotCov(p.k,ux,uy,uz,ux,uy,uz)));
    const vv=Math.max(minVariance,Math.min(maxVariance,dotCov(p.k,vx,vy,vz,vx,vy,vz)));
    const uvLimit=.85*Math.sqrt(uu*vv);
    const uv=Math.max(-uvLimit,Math.min(uvLimit,dotCov(p.k,ux,uy,uz,vx,vy,vz)));
    const det=uu*vv-uv*uv;
    samples.get(k).push({...p,invUU:vv/det,invUV:-uv/det,invVV:uu/det});
  }
  let seed=0;
  for(const c of asset.id) seed=Math.imul(seed,31)+c.charCodeAt(0);
  const random=()=>((seed=(Math.imul(seed,1664525)+1013904223)|0)>>>0)/4294967296;
  for(let i=0;i<cols;i++) for(let j=0;j<rows;j++){
    const u=u0+(i+.5+(random()-.5)*.54)*(u1-u0)/cols;
    const v=v0+(j+.5+(random()-.5)*.54)*(v1-v0)/rows;
    const bi=Math.floor(u/spacing),bj=Math.floor(v/spacing);
    let transmission=1, scannedWeight=0, scannedBrightness=0, scannedY=0, protectedCoverage=0;
    for(let du=-2;du<=2;du++) for(let dv=-2;dv<=2;dv++){
      for(const p of samples.get(key(bi+du,bj+dv))||[]){
        const x=u-p.u,y=v-p.v;
        const distance=x*x*p.invUU+2*x*y*p.invUV+y*y*p.invVV;
        if(distance>4) continue;
        const alpha=covB[p.k+3]*Math.exp(-distance/2);
        transmission*=1-alpha;
        if(p.owner) protectedCoverage=Math.max(protectedCoverage,alpha);
        else {
          scannedWeight+=alpha;
          scannedBrightness+=alpha*p.brightness;
          scannedY+=alpha*p.y;
        }
      }
    }
    const feather=shadowFade?.(u,v) || 0;
    if(transmission<.08 && (!feather || scannedWeight<1 || protectedCoverage>.15)) continue;
    let total=0,r=0,g=0,b=0,a=0,uu=0,uv=0,vv=0;
    for(const {p,d} of nearestEight(donors,u,v)){
      const k=p.k,w=1/(d+.0025),rgb=center[k+3];
      total+=w;r+=w*(rgb%256);g+=w*Math.floor(rgb/256);b+=w*covB[k+2];a+=w*covB[k+3];
      uu+=w*dotCov(k,ux,uy,uz,ux,uy,uz);
      uv+=w*dotCov(k,ux,uy,uz,vx,vy,vz);
      vv+=w*dotCov(k,vx,vy,vz,vx,vy,vz);
    }
    const brightness=(r+g+b*255)/(3*total);
    const darkness=brightness-scannedBrightness/scannedWeight;
    // A strong, supported dark imprint inside a floor-standing object's footprint
    // can be softened. Keep the correction thin and feather it before the edge.
    const correction=feather && scannedWeight>=1 && protectedCoverage<=.15 && brightness>=65
      ? feather*Math.max(0,Math.min(.60,(darkness/brightness-.22)*2.2)) : 0;
    const opacity=a/total*Math.max(transmission,correction);
    if(opacity<.025) continue;
    const gain=(.97+.06*random())/total;
    const r8=Math.min(255,Math.round(r*gain)),g8=Math.min(255,Math.round(g*gain));
    const minVariance=(spacing*.52)**2,maxVariance=(spacing*1.8)**2;
    uu=Math.min(maxVariance,Math.max(minVariance,uu/total));
    vv=Math.min(maxVariance,Math.max(minVariance,vv/total));
    const limit=.8*Math.sqrt(uu*vv);
    uv=Math.max(-limit,Math.min(limit,uv/total));
    const c=(x,y,a,b)=>uu*x*a+uv*(x*b+y*a)+vv*y*b;
    const [x,y,z]=position(u,v,correction>transmission ? scannedY/scannedWeight+.012 : undefined);
    output.push(x,y,z,r8+g8*256,
      c(ux,vx,ux,vx)+.000002,c(ux,vx,uy,vy),c(ux,vx,uz,vz),c(uy,vy,uy,vy)+.000002,
      c(uy,vy,uz,vz),c(uz,vz,uz,vz)+.000002,Math.min(1,b*gain),opacity);
  }
}

function fillFloor(scan,asset,samples,out) {
  const [cx,cy,cz]=asset.center,[sx,sy,sz]=asset.size;
  if (cy-sy/2>.40) return false;
  const c=Math.cos(asset.yaw),s=Math.sin(asset.yaw),hx=sx/2,hz=sz/2;
  const plane=samples.filter(p=>Math.abs(p.y)<=.095 && scan.covA[p.k+3]<.55*(scan.covA[p.k]+scan.covB[p.k+1]))
    .map(p=>({...p,u:p.x*c+p.z*s,v:-p.x*s+p.z*c}));
  const edge=p=>Math.max(Math.abs(p.u)-hx,Math.abs(p.v)-hz);
  const donors=plane.filter(p=>!p.owner&&edge(p)>=.10&&edge(p)<=.40);
  if(donors.length<8) return false;
  const y=median(donors.map(p=>p.y));
  const ring=donors.filter(p=>Math.abs(p.y-y)<=.035);
  if(ring.length<8) return false;
  const area=(sx+.8)*(sz+.8)-(sx+.2)*(sz+.2);
  tiledFill(scan,asset,{ring,area,existing:plane.filter(p=>edge(p)<.10&&Math.abs(p.y-y)<=.035),
    u0:-hx-.05,u1:hx+.05,v0:-hz-.05,v1:hz+.05,axes:[[c,0,s],[-s,0,c]],
    shadowFade:cy-sy/2<=.15 ? (u,v)=>Math.max(0,Math.min(1,(hx-Math.abs(u))/.14,(hz-Math.abs(v))/.14)) : undefined,
    position:(u,v,height=y)=>[cx+c*u-s*v,height,cz+s*u+c*v]},out);
  return true;
}

function wallPlane(scan,asset,samples,walls) {
  const [sx,sy,sz]=asset.size,top=asset.center[1]+sy/2;
  const bottom=Math.max(0,asset.center[1]-sy/2-.025);
  if(top<.18) return null;
  const c=Math.cos(asset.yaw),s=Math.sin(asset.yaw);
  let best;
  for(const {from,to} of walls){
    const [cx,,cz]=asset.center;
    const length=Math.hypot(to[0]-from[0],to[1]-from[1]);
    if (!length) continue;
    let nx=(to[1]-from[1])/length,nz=(from[0]-to[0])/length;
    const towardWall=(from[0]-cx)*nx+(from[1]-cz)*nz;
    if(towardWall<0){nx=-nx;nz=-nz;}
    const tx=-nz,tz=nx;
    const halfN=(Math.abs(nx*c+nz*s)*sx+Math.abs(-nx*s+nz*c)*sz)/2;
    const halfU=(Math.abs(tx*c+tz*s)*sx+Math.abs(-tx*s+tz*c)*sz)/2;
    const wallDistance=Math.abs(towardWall);
    if(wallDistance-halfN>.46) continue;
    const donors=[];
    for(const p of samples){
      const u=p.x*tx+p.z*tz,n=p.x*nx+p.z*nz;
      if(p.owner || n<halfN-.04 || n>halfN+.25 || Math.abs(u)>halfU+.40 ||
         p.y<Math.max(-.025,bottom-.40) || p.y>top+.40) continue;
      const edge=Math.max(Math.abs(u)-halfU,p.y-top,bottom-p.y);
      if(edge<.08)continue;
      const k=p.k,xx=scan.covA[k],xz=scan.covA[k+2],zz=scan.covB[k+1];
      const normal=nx*nx*xx+2*nx*nz*xz+nz*nz*zz;
      if(normal>.42*(xx+zz-normal+scan.covA[k+3]))continue;
      donors.push({...p,u,v:p.y,n});
    }
    if(donors.length<32) continue;
    const bins=new Map();
    for(const p of donors){
      const k=Math.round(p.n/.025);
      bins.set(k,(bins.get(k)||0)+1);
    }
    let peak=0,max=0;
    for(const [k,value] of bins)if(value>max){peak=k;max=value;}
    let ring=donors.filter(p=>Math.abs(p.n-peak*.025)<=.036);
    if(ring.length<32)continue;
    const avgU=ring.reduce((sum,p)=>sum+p.u,0)/ring.length;
    const avgN=ring.reduce((sum,p)=>sum+p.n,0)/ring.length;
    let slope=0,variance=0;
    for(const p of ring){slope+=(p.u-avgU)*(p.n-avgN);variance+=(p.u-avgU)**2;}
    slope/=Math.max(.001,variance);
    const offset=avgN-slope*avgU;
    if(Math.abs(slope)>.22 || offset<halfN-.04 || offset>halfN+.25 || Math.abs(offset-wallDistance)>.22)continue;
    ring=ring.filter(p=>Math.abs(p.n-offset-slope*p.u)<=.027);
    if(ring.length<32 || !ring.some(p=>p.u<-halfU-.07) || !ring.some(p=>p.u>halfU+.07) ||
       !ring.some(p=>p.v<bottom+(top-bottom)*.35) || !ring.some(p=>p.v>bottom+(top-bottom)*.70))continue;
    const support=new Set(ring.map(p=>Math.floor(p.u/.08)+','+Math.floor(p.v/.08))).size;
    if(support<10 || (best && best.support>=support))continue;
    best={ring,support,halfU,bottom,top,nx,nz,tx,tz,slope,offset};
  }
  return best;
}

export function fillRoomObject(scan,asset,removed,walls=[]) {
  const samples=surfaceSamples(scan,asset,removed), data=[];
  const floor=fillFloor(scan,asset,samples,data);
  const floorCount=data.length/12;
  const wall=wallPlane(scan,asset,samples,walls);
  if(wall){
    const {ring,halfU,bottom,top,nx,nz,tx,tz,slope,offset}=wall;
    const [cx,,cz]=asset.center;
    const length=Math.hypot(1,slope);
    const existing=samples.map(p=>({...p,u:p.x*tx+p.z*tz,v:p.y,n:p.x*nx+p.z*nz}))
      .filter(p=>Math.abs(p.n-offset-slope*p.u)<.026&&Math.abs(p.u)<=halfU+.07);
    const low=Math.min(.4,bottom), span=top-bottom;
    const area=(2*halfU+.8)*(span+.4+low)-(2*halfU+.16)*(span+.08+Math.min(.08,low));
    tiledFill(scan,asset,{ring,existing,area,u0:-halfU-.035,u1:halfU+.035,v0:bottom,v1:top+.025,
      axes:[[(tx+nx*slope)/length,0,(tz+nz*slope)/length],[0,1,0]],
      position:(u,v)=>[cx+tx*u+nx*(offset+slope*u),v,cz+tz*u+nz*(offset+slope*u)]},data);
  }
  return {data,floor,floorCount,wallCount:data.length/12-floorCount};
}
