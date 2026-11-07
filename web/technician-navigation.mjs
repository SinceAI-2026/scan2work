/** Runtime navigation footprint for the illustrative technician, never scan/BIM geometry.
 * Raw clearance is distance to occupied-cell centres. The diagonal allowance covers
 * both occupied-cell square extents and movement anywhere inside an admitted cell.
 * The small guard accounts for the source clearance values rounded to 0.001 units.
 */
import {manualObstacles} from './manual-walk.mjs';
import {technicianDimensions} from './technician-avatar.mjs';
export const TECHNICIAN_HEIGHT=1.4;
export const TECHNICIAN_RADIUS=technicianDimensions(TECHNICIAN_HEIGHT).footprintRadius;
export function withTechnicianClearance(navigation,{height=TECHNICIAN_HEIGHT,assets=[]}={}) {
  if(!navigation?.grid_meta)return navigation;
  const {footprintRadius:radius}=technicianDimensions(height);
  const m=navigation.grid_meta,source=navigation.room_walkable_grid||navigation.walkable_grid;
  const threshold=radius+.01+Math.SQRT2*m.res+.0005;
  const free=source.map((row,i)=>row.map((cell,j)=>Number(Boolean(cell)&&Number.isFinite(navigation.clearance_grid_m?.[i]?.[j])&&navigation.clearance_grid_m[i][j]>threshold)));
  // Use the same equipment envelopes as manual walking, covering every point in each admitted cell.
  for(const box of manualObstacles(assets,radius+.01)) {
    const halfCell=m.res*(Math.abs(box.c)+Math.abs(box.s))/2;
    const halfX=box.halfX+halfCell,halfZ=box.halfZ+halfCell,reach=Math.hypot(halfX,halfZ);
    const ix=x=>Math.floor((x-m.x_min)/m.res),iz=z=>Math.floor((z-m.z_min)/m.res);
    for(let i=Math.max(0,ix(box.x-reach));i<=Math.min(m.nx-1,ix(box.x+reach));i++)for(let j=Math.max(0,iz(box.z-reach));j<=Math.min(m.nz-1,iz(box.z+reach));j++) {
      const dx=m.x_min+(i+.5)*m.res-box.x,dz=m.z_min+(j+.5)*m.res-box.z;
      if(Math.abs(dx*box.c+dz*box.s)<=halfX&&Math.abs(-dx*box.s+dz*box.c)<=halfZ)free[i][j]=0;
    }
  }
  return {...navigation,source_floor_grid:navigation.source_floor_grid||source,
    walkable_grid:free,room_walkable_grid:free,
    technician_profile:{height,radius,units:'scene_units',scale_verified:false,clearance_basis:'occupied-cell squares; full admitted-cell extent'},
    grid_meta:{...m,robot_radius_m:radius,safety_margin_m:.01,required_clearance_m:threshold,walkable_cells:free.reduce((n,row)=>n+row.reduce((a,b)=>a+b,0),0)}};
}
