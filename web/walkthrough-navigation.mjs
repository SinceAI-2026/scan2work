import { manualObstacles } from "./manual-walk.mjs";
import { reachableFloor } from "./navigation-view.mjs";

// Aligns with the walkthrough export. Its levelling transform only changes Y.
export function withWalkthroughPassages(navigation, assets, poses, edit = null) {
  const m = navigation.grid_meta;
  const editFloor = edit?.floor;
  const radius = edit?.profile.robot_radius_m ?? .10;
  const margin = edit?.profile.safety_margin_m ?? .01;
  const clearance = radius + margin;
  const evidenceRadius = .40;
  const worldX = i => m.x_min + (i + .5) * m.res;
  const worldZ = j => m.z_min + (j + .5) * m.res;
  const indexX = x => Math.floor((x - m.x_min) / m.res);
  const indexZ = z => Math.floor((z - m.z_min) / m.res);
  const mask = () => Array.from({length:m.nx}, () => new Uint8Array(m.nz));
  const observed = mask();
  for (const {position} of poses) {
    const x = position?.[0], z = position?.[2];
    if (!Number.isFinite(x) || !Number.isFinite(z)) continue;
    for (let i = Math.max(0, indexX(x - evidenceRadius)); i <= Math.min(m.nx - 1, indexX(x + evidenceRadius)); i++) {
      for (let j = Math.max(0, indexZ(z - evidenceRadius)); j <= Math.min(m.nz - 1, indexZ(z + evidenceRadius)); j++) {
        if (Math.hypot(worldX(i) - x, worldZ(j) - z) <= evidenceRadius) observed[i][j] = 1;
      }
    }
  }

  const blocked = mask();
  // Check the gap between whole cell squares, so a centre can travel anywhere in an admitted cell.
  const span = Math.ceil(clearance / m.res) + 1;
  const offsets = [];
  for (let i = -span; i <= span; i++) for (let j = -span; j <= span; j++) {
    const gap = Math.hypot(Math.max(0, Math.abs(i) - 1), Math.max(0, Math.abs(j) - 1)) * m.res;
    if (gap < clearance) offsets.push([i,j]);
  }
  for (const [x,z] of navigation.obstacle_cells_xz) {
    const i = indexX(x), j = indexZ(z);
    for (const [di,dj] of offsets) {
      if (blocked[i + di]?.[j + dj] !== undefined) blocked[i + di][j + dj] = 1;
    }
  }
  for (const box of manualObstacles(assets, clearance)) {
    const halfCell = m.res * (Math.abs(box.c) + Math.abs(box.s)) / 2;
    const halfX = box.halfX + halfCell, halfZ = box.halfZ + halfCell;
    const reach = Math.hypot(halfX, halfZ);
    for (let i = Math.max(0, indexX(box.x - reach)); i <= Math.min(m.nx - 1, indexX(box.x + reach)); i++) {
      for (let j = Math.max(0, indexZ(box.z - reach)); j <= Math.min(m.nz - 1, indexZ(box.z + reach)); j++) {
        const dx = worldX(i) - box.x, dz = worldZ(j) - box.z;
        if (Math.abs(dx * box.c + dz * box.s) <= halfX && Math.abs(-dx * box.s + dz * box.c) <= halfZ) blocked[i][j] = 1;
      }
    }
  }

  const baseline = navigation.room_walkable_grid || navigation.walkable_grid;
  const room = baseline.map((row, i) => row.map((free, j) => Number(!blocked[i][j] && (free || observed[i][j] || editFloor?.[i]?.[j]))));
  const [x,z] = navigation.start_xz;
  if (!room[indexX(x)]?.[indexZ(z)]) return editFloor ? null : navigation;
  const reachable = reachableFloor({...navigation, room_walkable_grid:room}, {x,z});
  const walkable = baseline.map(row => row.map(() => 0));
  const passages = mask();
  let opened = 0;
  for (const point of reachable) {
    const i = indexX(point.x), j = indexZ(point.z);
    walkable[i][j] = 1;
    if (!navigation.walkable_grid[i][j]) {
      if (!editFloor?.[i]?.[j]) passages[i][j] = 1;
      opened++;
    }
  }
  if (!opened && !editFloor) return navigation;
  return {...navigation, walkable_grid:walkable, room_walkable_grid:walkable, walkthrough_passages:passages,
    grid_meta:{...m, robot_radius_m:radius, safety_margin_m:margin,
      required_clearance_m:clearance + m.res / Math.SQRT2, walkable_cells:reachable.length}};
}
