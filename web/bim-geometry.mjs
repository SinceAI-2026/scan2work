const distance = (point, circle) => Math.abs(Math.hypot(point.x-circle.x,point.z-circle.z)-circle.radius);
const tolerance = radius => Math.max(.015,Math.min(.04,radius*.10));
const quantile = (values,q) => values.sort((a,b)=>a-b)[Math.floor((values.length-1)*q)];

function circleThrough(a,b,c) {
  const bx=b.x-a.x, bz=b.z-a.z, cx=c.x-a.x, cz=c.z-a.z;
  const cross=2*(bx*cz-bz*cx);
  if (Math.abs(cross)<.0001) return null;
  const bb=bx*bx+bz*bz, cc=cx*cx+cz*cz;
  const x=(bb*cz-cc*bz)/cross, z=(bx*cc-cx*bb)/cross;
  return {x:x+a.x,z:z+a.z,radius:Math.hypot(x,z)};
}

function boxSideDistance({x,z},asset) {
  const c=Math.cos(asset.yaw || 0), s=Math.sin(asset.yaw || 0);
  const dx=Math.abs(x*c+z*s)-asset.size[0]/2;
  const dz=Math.abs(-x*s+z*c)-asset.size[2]/2;
  return dx>0 || dz>0 ? Math.hypot(Math.max(0,dx),Math.max(0,dz)) : Math.abs(Math.max(dx,dz));
}

function fitVessel(asset, points) {
  const middle=points.filter(p=>Math.abs(p.y)<=asset.size[1]*.33);
  if (middle.length<75) return null;
  const held=middle.filter((_,i)=>i%5===0);
  const train=middle.filter((_,i)=>i%5!==0);
  const sample=train.filter((_,i)=>i%Math.ceil(train.length/550)===0);
  const width=Math.min(asset.size[0],asset.size[2]);
  let seed=16, winner, bestCount=0, bestError=Infinity;
  const next=()=>{
    seed=(Math.imul(seed,1664525)+1013904223)>>>0;
    return sample[Math.floor(seed/4294967296*sample.length)];
  };
  for(let i=0;i<640;i++){
    const candidate=circleThrough(next(),next(),next());
    if (!candidate || Math.hypot(candidate.x,candidate.z)>width*.33 ||
        candidate.radius<width*.32 || candidate.radius>width*.63) continue;
    let count=0,error=0;
    const limit=tolerance(candidate.radius);
    for(const point of sample){
      const residual=distance(point,candidate);
      if(residual<=limit){count++;error+=residual;}
    }
    if(count>bestCount || (count===bestCount && error<bestError)){
      winner=candidate;
      bestCount=count;
      bestError=error;
    }
  }
  if (!winner) return null;
  const {x,z,radius}=winner, limit=tolerance(radius);
  const inliers=held.filter(point=>distance(point,winner)<=limit);
  const sectors=Array(12).fill(0);
  for(const p of inliers){
    const angle=Math.atan2(p.z-z,p.x-x);
    sectors[Math.min(11,Math.floor((angle+Math.PI)*6/Math.PI))]++;
  }
  if(inliers.length<held.length*.45 || sectors.filter(n=>n>=2).length<5) return null;
  const circleError=quantile(held.map(point=>distance(point,winner)),.5);
  const boxError=quantile(held.map(point=>boxSideDistance(point,asset)),.5);
  if (circleError>=boxError*.8) return null;
  const heights=points.filter(point=>distance(point,winner)<=limit).map(point=>point.y);
  const bottom=quantile(heights,.02),top=quantile(heights,.98);
  if (top-bottom<asset.size[1]*.55) return null;
  const [cx,cy,cz]=asset.center;
  return {
    start:[cx+x,cy+bottom,cz+z],end:[cx+x,cy+top,cz+z],radius,
    source:"Fitted vessel body", circleError, boxError,
  };
}

export function fitBimVessels(assets, centers, colors, owners, count) {
  const candidates=new Map();
  assets.forEach((asset,i)=>{
    if(asset.class==="expansion_vessel" && !asset.cylinder) candidates.set(i+1,{asset,points:[]});
  });
  if (!candidates.size) return;
  for(let i=0;i<count;i++){
    const candidate=candidates.get(owners[i]);
    const k=i*4;
    if (!candidate || colors[k+3]<.25) continue;
    const [x,y,z]=candidate.asset.center;
    candidate.points.push({x:centers[k]-x,y:centers[k+1]-y,z:centers[k+2]-z});
  }
  for(const {asset,points} of candidates.values()){
    const fit=fitVessel(asset,points);
    if(fit) asset.bimCylinder=fit;
  }
}

export function bimCylinder(asset) {
  const fitted=asset.cylinder || asset.bimCylinder;
  if (!fitted || !(fitted.radius>0) || fitted.start?.length!==3 || fitted.end?.length!==3 ||
      ![fitted.radius,...fitted.start,...fitted.end].every(Number.isFinite) ||
      !fitted.start.some((value,i)=>value!==fitted.end[i])) return null;
  if(asset.cylinder) return {...fitted,source:"Fitted scan cylinder"};
  if(!asset.transform) return fitted;
  return {...fitted,start:asset.transform.point(...fitted.start),end:asset.transform.point(...fitted.end)};
}

export const mergedVesselEnvelope = (asset, cylinder) =>
  asset.class === "expansion_vessel" && !cylinder && asset.merged_fragments?.length > 1;
