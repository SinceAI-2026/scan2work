import { sampleRoute, planFromPosition } from "./navigation-view.mjs";

export function blockBox(navigation, obstacle) {
  const m = navigation.grid_meta;
  const free = (navigation.room_walkable_grid || navigation.walkable_grid).map(row => row.slice());
  const clearance = navigation.clearance_grid_m.map(row => row.slice());
  const half = obstacle.size / 2;
  const margin = m.required_clearance_m ?? (m.robot_radius_m + m.safety_margin_m);
  const obstacleCells = navigation.obstacle_cells_xz.slice();
  for (let x = 0; x < m.nx; x++) for (let z = 0; z < m.nz; z++) {
    const cx = m.x_min + (x + .5) * m.res, cz = m.z_min + (z + .5) * m.res;
    const dx = Math.max(0, Math.abs(cx-obstacle.x)-half);
    const dz = Math.max(0, Math.abs(cz-obstacle.z)-half);
    const distance = Math.hypot(dx, dz);
    clearance[x][z] = Math.min(clearance[x][z], distance);
    if (distance <= margin + m.res / Math.SQRT2) free[x][z] = 0;
    if (dx === 0 && dz === 0) obstacleCells.push([cx,cz]);
  }
  return {...navigation, room_walkable_grid:free, walkable_grid:free,
    clearance_grid_m:clearance, obstacle_cells_xz:obstacleCells};
}

export function planObstacleDrop(navigation, route, position, elapsed) {
  const current = sampleRoute(route, elapsed);
  const remaining = route.total_distance_m - current.distance;
  const goal = route.waypoints.at(-1).position;
  let fallback;
  for (let ahead = 1.1; ahead < remaining - .75; ahead += .25) {
    const distance = current.distance + ahead;
    const next = route.waypoints.findIndex(w => w.distance_m >= distance);
    if (next < 1) continue;
    const a = route.waypoints[next-1], b = route.waypoints[next];
    const t = (distance-a.distance_m) / (b.distance_m-a.distance_m);
    const point = sampleRoute(route, a.t+(b.t-a.t)*t);
    if (Math.hypot(point.x-position.x,point.z-position.z) < .9 || Math.hypot(point.x-goal[0],point.z-goal[2]) < .9) continue;
    const obstacle = {x:point.x,z:point.z,size:.6};
    const updated = blockBox(navigation,obstacle);
    try {
      return {obstacle,navigation:updated,route:planFromPosition(updated,position,{...route,stops:[]})};
    } catch (error) {
      fallback ||= {obstacle,navigation:updated,error:error.message};
    }
  }
  if (fallback) return fallback;
  throw new Error("Not enough clear route ahead. Start a longer walk before dropping an obstacle.");
}
