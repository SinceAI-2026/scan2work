// Portable helpers for imported region data. No rendering or API dependency.
export const isImportedScene = twin => Boolean(twin?.assets?.some(a => a.source_region_id));
export const escapeHtml = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));

export function decodeInstanceIds(buffer, count, assetCount) {
  if (!(buffer instanceof ArrayBuffer) || buffer.byteLength < 12) throw new Error("Object membership file is truncated.");
  const view = new DataView(buffer);
  if (String.fromCharCode(...new Uint8Array(buffer, 0, 4)) !== "GSID" || view.getUint32(4, true) !== 1)
    throw new Error("Unsupported object membership format.");
  if (view.getUint32(8, true) !== count || buffer.byteLength !== 12 + count * 2)
    throw new Error("Object membership does not match the displayed scan.");
  const result = new Float32Array(count);
  for (let i = 0; i < count; i++) {
    const id = view.getUint16(12 + i * 2, true);
    if (id !== 65535 && id >= assetCount) throw new Error("Object membership references an unknown region.");
    result[i] = id === 65535 ? 0 : id + 1;
  }
  return result;
}

export function firstNavigationPoint(navigation) {
  const routePoint = navigation?.primary_route?.waypoints?.[0];
  if (routePoint?.position?.every(Number.isFinite)) return routePoint;
  const m = navigation?.grid_meta;
  if (!m) return null;
  const grid = navigation.room_walkable_grid || navigation.walkable_grid || [];
  const start = navigation.start_xz;
  if (start?.length === 2 && start.every(Number.isFinite)) {
    const i = Math.floor((start[0]-m.x_min)/m.res), j = Math.floor((start[1]-m.z_min)/m.res);
    if (grid[i]?.[j] === 1) return {position:[start[0],0,start[1]],t:0,distance_m:0,yaw:0,clearance_m:navigation.clearance_grid_m?.[i]?.[j] ?? 0};
  }
  for (let i = 0; i < grid.length; i++) for (let j = 0; j < grid[i].length; j++) {
    if (grid[i][j] === 1) return {position:[m.x_min + (i+.5)*m.res,0,m.z_min + (j+.5)*m.res],t:0,distance_m:0,yaw:0,
      clearance_m:navigation.clearance_grid_m?.[i]?.[j] ?? 0};
  }
  return null;
}

export function regionSummary(asset) {
  const tier = String(asset.support_tier || "source region").replaceAll("-", " ").replaceAll("_", " ");
  return `${Number(asset.n_gaussians || 0).toLocaleString()} Gaussians · ${tier} · ${asset.review_status || "unreviewed"}`;
}
