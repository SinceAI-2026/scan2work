// A schematic map: simplify scan contours for display, never for collision checks.
export function mapFrame(width,height) {
  return {x:width*.08,y:height*.08,width:width*.84,height:height*.84};
}

export function mapPoint(meta,width,height,x,z) {
  const f=mapFrame(width,height);
  return [f.x+(x-meta.x_min)/(meta.nx*meta.res)*f.width,
    f.y+f.height-(z-meta.z_min)/(meta.nz*meta.res)*f.height];
}

export function mapWorldPoint(meta,width,height,x,y) {
  const f=mapFrame(width,height);
  if(x<=f.x||x>=f.x+f.width||y<=f.y||y>=f.y+f.height)return null;
  return {x:meta.x_min+(x-f.x)/f.width*meta.nx*meta.res,
    z:meta.z_min+(1-(y-f.y)/f.height)*meta.nz*meta.res};
}

function closeMask(mask, radius=2) {
  const width=mask.length,height=mask[0].length;
  const dilated=mask.map(row=>row.slice()),closed=mask.map(row=>row.slice());
  for(let x=0;x<width;x++)for(let y=0;y<height;y++)if(mask[x][y]) {
    for(let dx=-radius;dx<=radius;dx++)for(let dy=-radius;dy<=radius;dy++)if(dilated[x+dx]&&y+dy>=0&&y+dy<height)dilated[x+dx][y+dy]=1;
  }
  for(let x=0;x<width;x++)for(let y=0;y<height;y++){
    let full=true;
    for(let dx=-radius;dx<=radius;dx++)for(let dy=-radius;dy<=radius;dy++)if(!dilated[x+dx]?.[y+dy])full=false;
    closed[x][y]=full?1:0;
  }
  return closed;
}

function simplify(points, tolerance) {
  if(points.length<4)return points;
  const a=points[0],b=points.at(-1),dx=b[0]-a[0],dy=b[1]-a[1],length=dx*dx+dy*dy;
  let farthest=0,index=0;
  for(let i=1;i<points.length-1;i++){
    const p=points[i],t=length?Math.max(0,Math.min(1,((p[0]-a[0])*dx+(p[1]-a[1])*dy)/length)):0;
    const distance=Math.hypot(p[0]-a[0]-t*dx,p[1]-a[1]-t*dy);
    if(distance>farthest){farthest=distance;index=i;}
  }
  if(farthest<=tolerance)return [a,b];
  return [...simplify(points.slice(0,index+1),tolerance).slice(0,-1),...simplify(points.slice(index),tolerance)];
}

function contourPath(mask, height, scale, minimumArea, tolerance) {
  const edges=new Map(),key=(x,y)=>`${x},${y}`;
  const add=(x,y,nx,ny)=>{const k=key(x,y);if(!edges.has(k))edges.set(k,[]);edges.get(k).push([nx,ny]);};
  for(let x=0;x<mask.length;x++)for(let z=0;z<height;z++)if(mask[x][z]){
    const y=height-1-z;
    if(!mask[x][z+1])add(x,y,x+1,y);
    if(!mask[x+1]?.[z])add(x+1,y,x+1,y+1);
    if(!mask[x][z-1])add(x+1,y+1,x,y+1);
    if(!mask[x-1]?.[z])add(x,y+1,x,y);
  }
  const path=new Path2D();
  while(edges.size){
    const origin=edges.keys().next().value,start=origin.split(',').map(Number),points=[start];
    let current=start;
    for(let i=0;i<100000;i++){
      const k=key(...current),next=edges.get(k);
      if(!next?.length)break;
      current=next.pop();if(!next.length)edges.delete(k);points.push(current);
      if(key(...current)===origin)break;
    }
    let area=0;
    for(let i=1;i<points.length;i++)area+=points[i-1][0]*points[i][1]-points[i][0]*points[i-1][1];
    if(Math.abs(area)/2<minimumArea)continue;
    const polygon=simplify(points,tolerance);
    path.moveTo(polygon[0][0]*scale,polygon[0][1]*scale);
    for(const p of polygon.slice(1))path.lineTo(p[0]*scale,p[1]*scale);
    path.closePath();
  }
  return path;
}

export function createGameMap(navigation, reachable, assets=[]) {
  const m=navigation.grid_meta,scale=6;
  const canvas=document.createElement('canvas');canvas.width=m.nx*scale;canvas.height=m.nz*scale;
  const ctx=canvas.getContext('2d');
  const obstacles=Array.from({length:m.nx},()=>new Uint8Array(m.nz));
  for(const [x,z] of navigation.obstacle_cells_xz){
    const ix=Math.round((x-m.x_min)/m.res-.5),iz=Math.round((z-m.z_min)/m.res-.5);
    if(obstacles[ix]&&iz>=0&&iz<m.nz)obstacles[ix][iz]=1;
  }
  const floor=Array.from({length:m.nx},()=>new Uint8Array(m.nz));
  for(const p of reachable){
    const x=Math.floor((p.x-m.x_min)/m.res),z=Math.floor((p.z-m.z_min)/m.res);
    if(floor[x]&&z>=0&&z<m.nz)floor[x][z]=1;
  }
  // Broad surfaces and straight vector contours replace individual scan cells.
  const accessible=contourPath(floor,m.nz,scale,4,.6);
  const closed = closeMask(obstacles);
  const structures = contourPath(closed.map((row, x) => row.map((blocked, z) => blocked && !floor[x][z])), m.nz, scale, 12, .6);
  const passageMask = navigation.walkthrough_passages;
  const frame=mapFrame(canvas.width,canvas.height);
  ctx.fillStyle='#172023';ctx.fillRect(0,0,canvas.width,canvas.height);
  ctx.save();ctx.translate(frame.x,frame.y);ctx.scale(.84,.84);
  ctx.beginPath();ctx.rect(0,0,canvas.width,canvas.height);ctx.clip();
  ctx.fillStyle='#353f40';ctx.fillRect(0,0,canvas.width,canvas.height);
  ctx.fillStyle='#516060';ctx.fill(accessible,'evenodd');
  if (passageMask) {
    const passage = contourPath(floor.map((row, x) => row.map((free, z) => free && passageMask[x][z])), m.nz, scale, 2, .6);
    ctx.fillStyle='#448883';ctx.fill(passage,'evenodd');
  }
  ctx.strokeStyle='#72828250';ctx.lineWidth=2;ctx.stroke(accessible);
  // A quiet floor texture, clipped to accessible space, echoes a level floor plan.
  ctx.save();ctx.clip(accessible,'evenodd');ctx.strokeStyle='#bccbc70a';ctx.lineWidth=1;
  const tile=.5/m.res*scale;
  for(let x=0;x<canvas.width;x+=tile){ctx.beginPath();ctx.moveTo(x,0);ctx.lineTo(x,canvas.height);ctx.stroke();}
  for(let y=0;y<canvas.height;y+=tile){ctx.beginPath();ctx.moveTo(0,y);ctx.lineTo(canvas.width,y);ctx.stroke();}
  ctx.restore();
  ctx.fillStyle='#777f7e';ctx.fill(structures,'evenodd');
  ctx.save();ctx.clip(structures,'evenodd');ctx.strokeStyle='#e5ece516';ctx.lineWidth=2;
  for(let x=-canvas.height;x<canvas.width;x+=18){ctx.beginPath();ctx.moveTo(x,0);ctx.lineTo(x+canvas.height,canvas.height);ctx.stroke();}ctx.restore();
  ctx.lineJoin='miter';ctx.miterLimit=3;ctx.strokeStyle='#111a1cb0';ctx.lineWidth=5;ctx.stroke(structures);
  ctx.strokeStyle='#d8e0db';ctx.lineWidth=2;ctx.stroke(structures);
  // Show individual equipment footprints used by occupancy construction. Small
  // objects remain visible even when their scan contour was simplified away.
  for(const asset of assets) {
    const [cx,cy,cz]=asset.center,[sx,sy,sz]=asset.size;
    if(asset.tier!=="equipment"||cy-sy/2>1.62||cy+sy/2<.10)continue;
    // Imported boxes are candidate envelopes, not an occupancy mask. Keep only
    // mapped envelopes as dashed outlines; never paint them as blocked floor.
    if(asset.source_region_id && !asset.mapped) continue;
    const x=(cx-m.x_min)/m.res*scale,y=canvas.height-(cz-m.z_min)/m.res*scale;
    const w=sx/m.res*scale,h=sz/m.res*scale;
    ctx.save();ctx.translate(x,y);ctx.rotate(-asset.yaw);
    const prominent=asset.mapped || sx*sz>=.08;
    ctx.fillStyle=asset.mapped?'#253b3be6':prominent?'#263333a8':'#26333322';
    ctx.strokeStyle=asset.mapped?'#bccdc5':'#a0b1a58c';
    ctx.lineWidth=asset.mapped?3:1.5;
    if(asset.source_region_id)ctx.setLineDash([5,4]);
    else ctx.fillRect(-w/2,-h/2,w,h);
    if(prominent)ctx.strokeRect(-w/2,-h/2,w,h);
    ctx.restore();
  }
  ctx.restore();
  // Four continuous walls remain distinct from every interior equipment contour.
  ctx.lineJoin='miter';ctx.strokeStyle='#0c1417';ctx.lineWidth=26;
  ctx.strokeRect(frame.x,frame.y,frame.width,frame.height);
  ctx.strokeStyle='#e0e5dc';ctx.lineWidth=15;
  ctx.strokeRect(frame.x,frame.y,frame.width,frame.height);
  ctx.strokeStyle='#929e98';ctx.lineWidth=2;
  ctx.strokeRect(frame.x+10,frame.y+10,frame.width-20,frame.height-20);
  return canvas;
}
