import { segmentIsFree } from "./navigation-view.mjs";

export function manualObstacles(assets, radius) {
  // Use the same height band as the room grid's equipment footprints.
  return assets.filter(({tier, center, size}) =>
    tier === "equipment" && center[1] - size[1] / 2 <= 1.62 && center[1] + size[1] / 2 >= .10
  ).map(({center, size, yaw}) => ({
    x: center[0], z: center[2], c: Math.cos(yaw), s: Math.sin(yaw),
    halfX: size[0] / 2 + radius, halfZ: size[2] / 2 + radius,
  }));
}

function intersectsBox(start, end, box) {
  const local = ({x, z}) => {
    const dx = x - box.x, dz = z - box.z;
    return [dx * box.c + dz * box.s, -dx * box.s + dz * box.c];
  };
  const a = local(start), b = local(end);
  const half = [box.halfX, box.halfZ];
  let enter = 0, leave = 1;
  for (let axis = 0; axis < 2; axis++) {
    const delta = b[axis] - a[axis];
    if (Math.abs(delta) < 1e-12) {
      if (Math.abs(a[axis]) > half[axis]) return false;
      continue;
    }
    const t0 = (-half[axis] - a[axis]) / delta, t1 = (half[axis] - a[axis]) / delta;
    enter = Math.max(enter, Math.min(t0, t1));
    leave = Math.min(leave, Math.max(t0, t1));
    if (enter > leave) return false;
  }
  return true;
}

// Wall sliding adapted from SinceAI-2026/rasse demo/web, Rasmus.
export function manualStep(navigation, obstacles, start, dx, dz) {
  if (!dx && !dz) return start;
  const grid = {...navigation, room_walkable_grid: navigation.walkable_grid};
  const from = [start.x, 0, start.z];
  for (const [x, z] of [[dx, dz], [dx, 0], [0, dz]]) {
    if (!x && !z) continue;
    const end = {x: start.x + x, z: start.z + z};
    if (segmentIsFree(grid, from, [end.x, 0, end.z]) && !obstacles.some(box => intersectsBox(start, end, box))) {
      return end;
    }
  }
  return start;
}
