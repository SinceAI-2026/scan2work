const STORAGE_KEY = "field-atlas:equipment-room:placements";

export function loadRoomPlacements(assets, canMove) {
  try {
    const saved = JSON.parse(localStorage.getItem(STORAGE_KEY)) || [];
    return new Map(saved.filter(([id, placement]) => {
      const asset = assets.get(id);
      return canMove(asset) && placement.center?.length === 3 &&
        [...placement.center, placement.yaw].every(Number.isFinite) &&
        [...asset.center, asset.yaw].every((value, i) => value === placement.source?.[i]);
    }));
  } catch {
    return new Map();
  }
}

export function saveRoomPlacements(placements) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify([...placements]));
}

export function roomTransform(asset, placement) {
  const angle = placement.yaw - asset.yaw;
  const c = Math.cos(angle), s = Math.sin(angle);
  const [cx, cy, cz] = asset.center, [x, y, z] = placement.center;
  return {
    angle, c, s,
    point(px, py, pz) {
      const dx = px - cx, dz = pz - cz;
      return [x + c * dx - s * dz, y + py - cy, z + s * dx + c * dz];
    },
  };
}

export function placedRoomAsset(asset, placement, sourceBaseY = asset.center[1] - asset.size[1] / 2) {
  const [x, y, z] = placement.center;
  const [sx, sy, sz] = asset.size;
  // Saved placements encode the requested base height using the original box.
  // Extend the effective bounds down to a reviewed contact point when present.
  const extension = Math.max(0, asset.center[1] - sy / 2 - sourceBaseY);
  const transform = roomTransform(asset, {...placement, center:[x, y + extension, z]});
  const c = Math.abs(Math.cos(placement.yaw)), s = Math.abs(Math.sin(placement.yaw));
  const hx = (sx * c + sz * s) / 2, hz = (sx * s + sz * c) / 2;
  const cylinder = asset.cylinder && {...asset.cylinder,
    start: transform.point(...asset.cylinder.start), end: transform.point(...asset.cylinder.end)};
  return {...asset, ...placement, center:[x,y+extension/2,z], size:[sx,sy+extension,sz], transform, cylinder,
    tier: y - sy / 2 <= 1.62 ? "equipment" : asset.tier,
    aabb_min: [x - hx, y - sy / 2, z - hz], aabb_max: [x + hx, y + sy / 2 + extension, z + hz]};
}

export function roomBoundsOverlap(a, b) {
  const dx = b.center[0] - a.center[0], dz = b.center[2] - a.center[2];
  const axes = asset => {
    const c = Math.cos(asset.yaw), s = Math.sin(asset.yaw);
    return [[c, s], [-s, c]];
  };
  const aAxes = axes(a), bAxes = axes(b);
  const dot = (p, q) => p[0] * q[0] + p[1] * q[1];
  const extent = (asset, basis, axis) =>
    (Math.abs(dot(basis[0], axis)) * asset.size[0] + Math.abs(dot(basis[1], axis)) * asset.size[2]) / 2;
  if (Math.abs(b.center[1] - a.center[1]) >= (a.size[1] + b.size[1]) / 2 - .015) return false;
  return [...aAxes, ...bAxes].every(axis =>
    Math.abs(dot([dx, dz], axis)) < extent(a, aAxes, axis) + extent(b, bAxes, axis) - .015);
}

export function roomPlacementError(asset, assets, scan, omitted, meta) {
  const [minX, minY, minZ] = asset.aabb_min, [maxX, maxY, maxZ] = asset.aabb_max;
  if (minX < meta.x_min || maxX > meta.x_min + meta.nx * meta.res ||
      minZ < meta.z_min || maxZ > meta.z_min + meta.nz * meta.res || minY < -.01 || maxY > 2.4) {
    return "Keep the object inside the room and above the floor.";
  }
  const neighbour = assets.find(other => other.id !== asset.id && roomBoundsOverlap(asset, other));
  if (neighbour) return `This position overlaps ${neighbour.tag} · ${neighbour.label}.`;

  // Test the interior, allowing contact with the floor or another supporting surface.
  const c = Math.cos(asset.yaw), s = Math.sin(asset.yaw);
  const [x, y, z] = asset.center, [sx, sy, sz] = asset.size;
  let hits = 0;
  scan.query(asset, 0, (i, k) => {
    if (hits >= 6 || omitted.has(scan.instances[i]) || scan.covB[k + 3] < .25) return;
    const dx = scan.center[k] - x, dz = scan.center[k + 2] - z;
    if (Math.abs(scan.center[k + 1] - y) < sy / 2 - .025 &&
        Math.abs(dx * c + dz * s) < sx / 2 - .02 &&
        Math.abs(-dx * s + dz * c) < sz / 2 - .02) hits++;
  });
  return hits >= 6 ? "Scan geometry occupies this position." : "";
}

export function copyPlacedSplats(scan, indices, asset, instance) {
  const {c, s, point} = asset.transform;
  const {center, covA, covB} = scan;
  const data = [];
  for (const i of indices) {
    const k = i * 4;
    const p = point(center[k], center[k + 1], center[k + 2]);
    const xx = covA[k], xy = covA[k + 1], xz = covA[k + 2], yy = covA[k + 3];
    const yz = covB[k], zz = covB[k + 1];
    data.push(...p, center[k + 3],
      c*c*xx - 2*c*s*xz + s*s*zz, c*xy - s*yz, c*s*(xx - zz) + (c*c - s*s)*xz, yy,
      s*xy + c*yz, s*s*xx + 2*c*s*xz + c*c*zz, covB[k + 2],
      // Negative alpha marks relocated scan data and retains its instance ID for Explode and Take apart.
      -(instance + covB[k + 3] / 2));
  }
  return data;
}
