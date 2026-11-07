import { withWalkthroughPassages } from "./walkthrough-navigation.mjs";

function measureClearance(m, obstacles) {
  // Separable squared distances to the nearest remaining occupied cell.
  const columns = Array.from({length:m.nz},()=>[]);
  for(let i=0;i<m.nx;i++) for(let j=0;j<m.nz;j++) if(obstacles[i][j]) columns[j].push(i);
  const across = obstacles.map((row,i)=>Array.from(row,(_,j)=>{
    let min=Infinity;
    for(const x of columns[j]) min=Math.min(min,(i-x)**2);
    return min;
  }));
  return across.map(row=>row.map((_,j)=>{
    let min=Infinity;
    for(let z=0;z<m.nz;z++) min=Math.min(min,row[z]+(j-z)**2);
    return Math.sqrt(min)*m.res;
  }));
}

export function navigationAfterRoomEdits(original, assets, scan, removed, fills, poses, profile, placed = []) {
  const m=original.grid_meta;
  const mask=()=>Array.from({length:m.nx},()=>new Uint8Array(m.nz));
  const occupied=mask(), affected=mask(), remaining=mask(), floor=mask();
  const xCell=x=>Math.floor((x-m.x_min)/m.res),zCell=z=>Math.floor((z-m.z_min)/m.res);
  const xWorld=i=>m.x_min+(i+.5)*m.res,zWorld=j=>m.z_min+(j+.5)*m.res;
  for(const [x,z] of original.obstacle_cells_xz) {
    const i=xCell(x),j=zCell(z);
    if(occupied[i]?.[j]!==undefined) occupied[i][j]=1;
  }
  for(const [asset,fill] of fills) {
    const [cx,cy,cz]=asset.center,[sx,sy,sz]=asset.size;
    if(cy-sy/2>1.62 || cy+sy/2<.10) continue;
    const c=Math.cos(asset.yaw),s=Math.sin(asset.yaw),reach=Math.hypot(sx,sz)/2+.12;
    const halfCell=m.res*(Math.abs(c)+Math.abs(s))/2;
    for(let i=Math.max(1,xCell(cx-reach));i<=Math.min(m.nx-2,xCell(cx+reach));i++){
      for(let j=Math.max(1,zCell(cz-reach));j<=Math.min(m.nz-2,zCell(cz+reach));j++){
        const dx=xWorld(i)-cx,dz=zWorld(j)-cz;
        if(Math.abs(dx*c+dz*s)>sx/2+halfCell || Math.abs(-dx*s+dz*c)>sz/2+halfCell) continue;
        affected[i][j]=1;
        // Only ground-level removals with a supported floor can add floor support.
        if(fill.floor && cy-sy/2<.40) floor[i][j]=1;
      }
    }
    scan.query(asset,.12,(index,k)=>{
      if(removed.has(scan.instances[index])) return;
      const y=scan.center[k+1], i=xCell(scan.center[k]),j=zCell(scan.center[k+2]);
      if(affected[i]?.[j] && y>=.16 && y<=1.62 && scan.covB[k+3]>=.25 &&
         Math.max(scan.covA[k],scan.covA[k+3],scan.covB[k+1])<=.16**2) remaining[i][j]=1;
    });
  }
  // The fitted wall remains solid, even where its newly visible face needed a fill.
  for(const [,fill] of fills) for(let k=fill.floorCount*12;k<fill.data.length;k+=12) {
    const [x,y,z]=fill.data.slice(k,k+3),i=xCell(x),j=zCell(z);
    if(affected[i]?.[j] && y>=.16 && y<=1.62) remaining[i][j]=1;
  }
  const destinationHits=mask();
  for (const data of placed) for (let k=0;k<data.length;k+=12) {
    const x=data[k], y=data[k+1], z=data[k+2], i=xCell(x), j=zCell(z);
    const opacity=(-data[k+11])%1*2;
    if (destinationHits[i]?.[j] !== undefined && destinationHits[i][j]<6 && y>=.16 && y<=1.62 &&
        opacity>=.25 && Math.max(data[k+4],data[k+7],data[k+9])<=.16**2) destinationHits[i][j]++;
  }
  const obstacle_cells_xz=[];
  for(let i=0;i<m.nx;i++) for(let j=0;j<m.nz;j++) {
    if(affected[i][j]) occupied[i][j]=remaining[i][j];
    if (destinationHits[i][j]>=6) occupied[i][j]=1;
    if(occupied[i][j]) obstacle_cells_xz.push([xWorld(i),zWorld(j)]);
  }
  return withWalkthroughPassages({...original,obstacle_cells_xz,
    clearance_grid_m:measureClearance(m,occupied)},assets,poses,{floor,profile});
}
