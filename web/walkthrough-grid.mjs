import {manualObstacles} from './manual-walk.mjs';
import {reachableFloor} from './navigation-view.mjs';

/** Exact centre-to-source-square distances, using separability of squared gaps.
 * Source rows keep their physical cell size when the planning grid is refined.
 */
export function sourceSquareClearance(meta,obstacles,cellSize) {
  const rows=new Map();
  for(const [x,z,size=cellSize] of obstacles) {
    const key=`${z}:${size}`;
    if(!rows.has(key))rows.set(key,{z,half:size/2,xs:[]});
    rows.get(key).xs.push(x);
  }
  const occupiedRows=[...rows.values()].map(row=>({...row,xs:row.xs.sort((a,b)=>a-b)}));
  const xGaps=occupiedRows.map(({xs,half})=>{
    let nearest=0;
    return Float64Array.from({length:meta.nx},(_,i)=>{
      const x=meta.x_min+(i+.5)*meta.res;
      while(nearest+1<xs.length&&Math.abs(xs[nearest+1]-x)<=Math.abs(xs[nearest]-x))nearest++;
      return Math.max(0,Math.abs(xs[nearest]-x)-half)**2;
    });
  });
  return Array.from({length:meta.nx},(_,i)=>Array.from({length:meta.nz},(_,j)=>{
    const z=meta.z_min+(j+.5)*meta.res;let squared=Infinity;
    for(let r=0;r<occupiedRows.length;r++)squared=Math.min(squared,xGaps[r][i]+Math.max(0,Math.abs(z-occupiedRows[r].z)-occupiedRows[r].half)**2);
    return Math.sqrt(squared);
  }));
}

/** Runtime-only refinement of already-supported floor. No scan cells are erased. */
export function refineWalkthroughGrid(source,coarse,assets,poses,edit,planningRes) {
  const s=source.grid_meta,factor=Math.ceil(s.res/planningRes);
  if(factor<=1)return coarse;
  const radius=edit?.profile?.robot_radius_m??.10,margin=edit?.profile?.safety_margin_m??.01,clearance=radius+margin;
  const obstacleCellSize=source.obstacle_cell_size??s.res;
  const m={...s,res:s.res/factor,nx:s.nx*factor,nz:s.nz*factor,
    robot_radius_m:radius,safety_margin_m:margin,required_clearance_m:clearance,
    source_resolution:s.res,clearance_reference:'source_occupied_cell_surface'};
  const mask=()=>Array.from({length:m.nx},()=>new Uint8Array(m.nz));
  const worldX=i=>m.x_min+(i+.5)*m.res,worldZ=j=>m.z_min+(j+.5)*m.res;
  const ix=x=>Math.floor((x-m.x_min)/m.res),iz=z=>Math.floor((z-m.z_min)/m.res);
  const sourceAt=(grid,i,j)=>grid?.[Math.floor(i/factor)]?.[Math.floor(j/factor)];
  const observed=mask(),blocked=mask();
  const visit=(x,z,reach,fn)=>{
    for(let i=Math.max(0,ix(x-reach));i<=Math.min(m.nx-1,ix(x+reach));i++)
      for(let j=Math.max(0,iz(z-reach));j<=Math.min(m.nz-1,iz(z+reach));j++)fn(i,j,worldX(i)-x,worldZ(j)-z);
  };
  for(const {position} of poses){
    const x=position?.[0],z=position?.[2];if(!Number.isFinite(x)||!Number.isFinite(z))continue;
    visit(x,z,.40,(i,j,dx,dz)=>{if(Math.hypot(dx,dz)<=.40)observed[i][j]=1;});
  }
  // Distance between the complete source obstacle square and complete planning
  // cell. This is equivalent to keeping every subcell of each occupied square.
  for(const [x,z,size=obstacleCellSize] of source.obstacle_cells_xz){
    const halfSum=(size+m.res)/2;
    visit(x,z,halfSum+clearance,(i,j,dx,dz)=>{
      if(Math.hypot(Math.max(0,Math.abs(dx)-halfSum),Math.max(0,Math.abs(dz)-halfSum))<clearance-1e-12)blocked[i][j]=1;
    });
  }
  for(const box of manualObstacles(assets,clearance)){
    const halfCell=m.res*(Math.abs(box.c)+Math.abs(box.s))/2;
    const hx=box.halfX+halfCell,hz=box.halfZ+halfCell;
    visit(box.x,box.z,Math.hypot(hx,hz),(i,j,dx,dz)=>{
      if(Math.abs(dx*box.c+dz*box.s)<=hx&&Math.abs(-dx*box.s+dz*box.c)<=hz)blocked[i][j]=1;
    });
  }
  const baseline=source.room_walkable_grid||source.walkable_grid;
  // Coarse certified cells remain supported even near the edge of the camera
  // evidence tube; refinement must not drop their previously admitted floor.
  const room=mask().map((row,i)=>row.map((_,j)=>Number(!blocked[i][j]&&
    (sourceAt(coarse.walkable_grid,i,j)||sourceAt(baseline,i,j)||observed[i][j]||sourceAt(edit?.floor,i,j)))));
  const [x,z]=source.start_xz;
  if(!room[ix(x)]?.[iz(z)])return edit?.floor?null:coarse;
  const distances=sourceSquareClearance(m,source.obstacle_cells_xz,obstacleCellSize);
  const reachable=reachableFloor({...source,grid_meta:m,room_walkable_grid:room,clearance_grid_m:distances},{x,z});
  const walkable=mask(),passages=mask();
  for(const p of reachable){
    const i=ix(p.x),j=iz(p.z);walkable[i][j]=1;
    if(!sourceAt(source.walkable_grid,i,j)&&!sourceAt(edit?.floor,i,j))passages[i][j]=1;
  }
  return {...source,grid_meta:{...m,walkable_cells:reachable.length},walkable_grid:walkable,room_walkable_grid:walkable,
    walkthrough_passages:passages,clearance_grid_m:distances,obstacle_cell_size:obstacleCellSize,
    walkthrough_refinement:{source_resolution:s.res,planning_resolution:m.res,factor,
      policy:'Whole planning cells avoid original occupied squares and equipment envelopes at unchanged clearance; floor support comes from the source or walkthrough.'}};
}
