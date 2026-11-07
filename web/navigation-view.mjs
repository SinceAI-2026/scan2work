// Shared by the animation, route map and HUD so all use the same trajectory.
export function sampleRoute(route, time) {
  const points = route.waypoints;
  const t = Math.max(0, Math.min(time, route.duration_s));
  let lo = 0, hi = points.length - 1;
  while (lo + 1 < hi) {
    const mid = (lo + hi) >> 1;
    if (points[mid].t <= t) lo = mid;
    else hi = mid;
  }
  const a = points[lo], b = points[hi];
  const u = Math.max(0, Math.min(1, (t - a.t) / Math.max(0.001, b.t - a.t)));
  const x = a.position[0] + (b.position[0] - a.position[0]) * u;
  const z = a.position[2] + (b.position[2] - a.position[2]) * u;
  const dx = b.position[0] - a.position[0], dz = b.position[2] - a.position[2];
  return { x, z, yaw: dx || dz ? Math.atan2(dx, dz) : a.yaw,
    distance: a.distance_m + (b.distance_m - a.distance_m) * u,
    clearance: Math.min(a.clearance_m ?? 0, b.clearance_m ?? 0) };
}

export function nearbyAssets(assets, x, z, radius) {
  return assets.map(asset => ({...asset, distance_m: Math.hypot(asset.center[0] - x, asset.center[2] - z)}))
    .filter(asset => asset.distance_m <= radius)
    .sort((a, b) => Number(b.is_target) - Number(a.is_target)
      || Number(b.condition_state !== "healthy") - Number(a.condition_state !== "healthy")
      || Number(b.mapped) - Number(a.mapped) || a.distance_m - b.distance_m);
}

export function segmentIsFree(navigation, start, end) {
  const m=navigation.grid_meta,free=navigation.room_walkable_grid || navigation.walkable_grid;
  const a=[(start[0]-m.x_min)/m.res,(start[2]-m.z_min)/m.res];
  const b=[(end[0]-m.x_min)/m.res,(end[2]-m.z_min)/m.res];
  if(![...a,...b].every(Number.isFinite)||a[0]<0||b[0]<0||a[1]<0||b[1]<0||a[0]>=m.nx||b[0]>=m.nx||a[1]>=m.nz||b[1]>=m.nz)return false;
  for(let x=Math.max(0,Math.floor(Math.min(a[0],b[0])-1e-9));x<=Math.min(m.nx-1,Math.floor(Math.max(a[0],b[0])+1e-9));x++) {
    for(let z=Math.max(0,Math.floor(Math.min(a[1],b[1])-1e-9));z<=Math.min(m.nz-1,Math.floor(Math.max(a[1],b[1])+1e-9));z++) {
      if(free[x][z])continue;
      let enter=0,leave=1;
      for(const [axis,cell] of [[0,x],[1,z]]) {
        const delta=b[axis]-a[axis];
        if(Math.abs(delta)<1e-12){if(a[axis]<cell||a[axis]>cell+1)leave=-1;}
        else {const t0=(cell-a[axis])/delta,t1=(cell+1-a[axis])/delta;enter=Math.max(enter,Math.min(t0,t1));leave=Math.min(leave,Math.max(t0,t1));}
      }
      if(enter<=leave+1e-12)return false;
    }
  }
  return true;
}

export function advanceAlongRoute(navigation,route,current,fromTime,toTime) {
  if(!Number.isFinite(fromTime)||!Number.isFinite(toTime)||fromTime<0)
    throw new Error("Movement needs valid route times.");
  if(toTime<fromTime)throw new Error("Movement cannot jump backwards along a route.");
  const expected=sampleRoute(route,fromTime);
  if(![current.x,current.z].every(Number.isFinite)||Math.hypot(current.x-expected.x,current.z-expected.z)>.001)
    throw new Error("Movement stopped: the current position does not match the route.");
  const points=route.waypoints.filter(w=>w.t>fromTime && w.t<=toTime).map(w=>w.position);
  const sample=sampleRoute(route,toTime);
  points.push([sample.x,0,sample.z]);
  let previous=[current.x,0,current.z];
  for(const point of points){
    if(!segmentIsFree(navigation,previous,point))throw new Error("Movement stopped: an object blocks the route. Choose another destination.");
    previous=point;
  }
  return sample;
}

export function planFromPosition(navigation, start, destination) {
  const m = navigation.grid_meta, free = navigation.room_walkable_grid || navigation.walkable_grid;
  const cell = (x, z) => [Math.floor((x - m.x_min) / m.res), Math.floor((z - m.z_min) / m.res)];
  const valid = (x, z) => x >= 0 && z >= 0 && x < m.nx && z < m.nz && free?.[x]?.[z] === 1;
  const from = cell(start.x, start.z), end = destination.waypoints.at(-1).position;
  const goal = cell(end[0], end[2]);
  if (!valid(...from) || !valid(...goal)) throw new Error("Start or destination is outside the validated floor.");
  const key = (x, z) => x * m.nz + z;
  const heuristic = (x, z) => Math.hypot(x - goal[0], z - goal[1]);
  const source = key(...from), target = key(...goal);
  const best = new Map([[source, 0]]), parent = new Map(), queue = [];
  const push = node => {
    queue.push(node);
    let i = queue.length - 1;
    while (i > 0) {
      const p = (i - 1) >> 1;
      if (queue[p].f <= node.f) break;
      queue[i] = queue[p]; i = p;
    }
    queue[i] = node;
  };
  const pop = () => {
    const result = queue[0], last = queue.pop();
    if (queue.length) {
      let i = 0;
      while (i * 2 + 1 < queue.length) {
        let child = i * 2 + 1;
        if (child + 1 < queue.length && queue[child + 1].f < queue[child].f) child++;
        if (last.f <= queue[child].f) break;
        queue[i] = queue[child]; i = child;
      }
      queue[i] = last;
    }
    return result;
  };
  push({x:from[0], z:from[1], g:0, f:heuristic(...from)});
  let found = false;
  while (queue.length) {
    const cur = pop(), id = key(cur.x, cur.z);
    if (cur.g !== best.get(id)) continue;
    if (id === target) { found = true; break; }
    for (let dx = -1; dx <= 1; dx++) for (let dz = -1; dz <= 1; dz++) {
      if (!dx && !dz) continue;
      const x = cur.x + dx, z = cur.z + dz;
      if (!valid(x, z) || (dx && dz && (!valid(cur.x + dx, cur.z) || !valid(cur.x, cur.z + dz)))) continue;
      // Clearance is enforced by the collision grid. Minimize travel distance.
      const g = cur.g + Math.hypot(dx, dz);
      const next = key(x, z);
      if (g >= (best.get(next) ?? Infinity)) continue;
      best.set(next, g); parent.set(next, id);
      push({x, z, g, f:g + heuristic(x, z)});
    }
  }
  if (!found) throw new Error("No collision-free route to this destination.");
  const cells = [target];
  while (cells.at(-1) !== source) cells.push(parent.get(cells.at(-1)));
  cells.reverse();
  const rawPositions = [[start.x, 0, start.z], ...cells.map(id => [m.x_min + (Math.floor(id / m.nz) + .5) * m.res, 0, m.z_min + (id % m.nz + .5) * m.res]), end];
  const positions=[rawPositions[0]];
  let cursor=0;
  while(cursor<rawPositions.length-1){
    let next=rawPositions.length-1;
    while(next>cursor+1&&!segmentIsFree(navigation,rawPositions[cursor],rawPositions[next]))next--;
    positions.push(rawPositions[next]);cursor=next;
  }
  let distance = 0;
  const waypoints = [];
  for (const position of positions) {
    const previous = waypoints.at(-1);
    if (previous) {
      const step = Math.hypot(position[0] - previous.position[0], position[2] - previous.position[2]);
      if (step < 1e-6) continue;
      distance += step;
    }
    const [x, z] = cell(position[0], position[2]);
    waypoints.push({position, distance_m:distance, t:distance / .5, yaw:0,
      clearance_m: navigation.clearance_grid_m[x][z] - m.res / Math.SQRT2});
  }
  return {...destination, waypoints, total_distance_m:distance, duration_s:distance / .5,
    planning_policy:"Shortest grid route by distance, with collision-checked shortcuts",
    min_clearance_m:Math.min(...waypoints.map(w => w.clearance_m)), validated:true};
}

export function reachableFloor(navigation, start) {
  const m = navigation.grid_meta, free = navigation.room_walkable_grid || navigation.walkable_grid;
  const x = Math.floor((start.x - m.x_min) / m.res), z = Math.floor((start.z - m.z_min) / m.res);
  if (!free?.[x]?.[z]) throw new Error("Robot is outside the walkable floor.");
  const seen = new Set([x * m.nz + z]), queue = [[x,z]];
  for (let i=0;i<queue.length;i++) {
    const [cx,cz]=queue[i];
    for (const [dx,dz] of [[1,0],[-1,0],[0,1],[0,-1]]) {
      const nx=cx+dx,nz=cz+dz,key=nx*m.nz+nz;
      if (!free[nx]?.[nz] || seen.has(key)) continue;
      seen.add(key);queue.push([nx,nz]);
    }
  }
  return queue.map(([x,z])=>({x:m.x_min+(x+.5)*m.res,z:m.z_min+(z+.5)*m.res,clearance:navigation.clearance_grid_m[x][z]}));
}

export function floorDestination(navigation, start, point, maxSnap=.30) {
  const m=navigation.grid_meta,free=navigation.room_walkable_grid || navigation.walkable_grid;
  const x=Math.floor((point.x-m.x_min)/m.res),z=Math.floor((point.z-m.z_min)/m.res);
  if(!free[x]?.[z])throw new Error("An object or wall blocks that point. Choose clear floor or select equipment to approach it.");
  const floor=reachableFloor(navigation,start);
  let best, distance=Infinity;
  for (const cell of floor) {
    const d=Math.hypot(cell.x-point.x,cell.z-point.z);
    if (d<distance) {best=cell;distance=d;}
  }
  if (distance>maxSnap) throw new Error("That point is blocked or disconnected. Choose a highlighted floor area.");
  return {asset_id:null,tag:"Map destination",kind:"floor",waypoints:[{position:[best.x,0,best.z]}]};
}

// The nearest clear spot 0.35 to 2.5 m from the equipment, preferring about 0.65 m. Equipment with no clear spot
// close by is inspected from further out (it was 1.5 m, which left VS-002 and M-006 without an inspection stop).
export const MAX_INSPECTION_DISTANCE_M = 2.5;
export function assetDestination(navigation,start,asset) {
  const floor=reachableFloor(navigation,start);
  let best,score=Infinity;
  for (const cell of floor) {
    const d=Math.hypot(cell.x-asset.center[0],cell.z-asset.center[2]);
    if (d<.35 || d>MAX_INSPECTION_DISTANCE_M) continue;
    const value=Math.abs(d-.65)+.1/Math.max(.05,cell.clearance);
    if (value<score) {best=cell;score=value;}
  }
  if (!best) throw new Error("This equipment has no reachable inspection stop from here.");
  return {asset_id:asset.asset_id,tag:asset.tag,kind:"asset",waypoints:[{position:[best.x,0,best.z]}]};
}

export function planRoomTour(navigation,start) {
  const floor=reachableFloor(navigation,start);
  // Sample the room's extent, with clear stopping points rather than wall edges.
  const candidates=floor.filter(p=>p.clearance>=navigation.grid_meta.required_clearance_m+.08);
  const pool=candidates.length?candidates:floor;
  const stops=[], available=pool.slice();
  for (let i=0;i<8 && available.length;i++) {
    let bestIndex=0,bestDistance=-1;
    for (let j=0;j<available.length;j++) {
      const p=available[j];
      const d=Math.min(...[start,...stops].map(q=>Math.hypot(p.x-q.x,p.z-q.z)));
      if(d>bestDistance){bestDistance=d;bestIndex=j;}
    }
    if(bestDistance<.65)break;
    stops.push(available.splice(bestIndex,1)[0]);
  }
  const waypoints=[], orderedStops=[];
  let position=start,distance=0,time=0;
  while(stops.length) {
    // Compare actual route lengths; straight-line distance can cross equipment.
    const options=stops.map((stop,index)=>({index,stop,route:planFromPosition(navigation,position,{asset_id:null,tag:"Room tour",waypoints:[{position:[stop.x,0,stop.z]}]})}));
    options.sort((a,b)=>a.route.total_distance_m-b.route.total_distance_m);
    const next=options[0];
    for(const w of next.route.waypoints.slice(waypoints.length?1:0))waypoints.push({...w,t:w.t+time,distance_m:w.distance_m+distance});
    distance+=next.route.total_distance_m;time+=next.route.duration_s;
    position=next.stop;orderedStops.push(next.stop);stops.splice(next.index,1);
  }
  if (!waypoints.length) throw new Error("Not enough reachable floor for a room tour.");
  return {asset_id:null,tag:"Room tour",kind:"tour",waypoints,total_distance_m:distance,duration_s:time,validated:true,stops:orderedStops};
}
