// Shared plan/geometry visibility. Classification is never required to browse an imported region.
export const GROUPS = {
  control_valve:"valve", circulation_pump:"machine", electric_motor:"machine",
  heat_exchanger:"machine", expansion_vessel:"vessel", pressure_sensor:"instrument",
  vibration_sensor:"instrument", electrical_cabinet:"fixture", shelf_rack:"fixture",
  appliance:"fixture", sink:"fixture", structural_column:"fixture",
};
export function visualGeometry(asset) {
  const proxy = asset.geometry_proxy;
  if (asset.transform || !proxy || ![proxy.center,proxy.size].every(v => Array.isArray(v) && v.length===3 && v.every(Number.isFinite)) ||
      !proxy.size.every(v => v>0) || !Number.isFinite(proxy.yaw ?? 0)) return asset;
  return {...asset,center:proxy.center,size:proxy.size,yaw:proxy.yaw || 0};
}
export const floorRegion = a => a.structure_role === "floor";
export const overhead = a => { const v=visualGeometry(a); return !floorRegion(a) && (a.class==="overhead_pipe" || v.center[1]-v.size[1]/2>1.70); };
export const major = a => {
  const v=visualGeometry(a);
  return Boolean(a.mapped || a.is_target || floorRegion(a) || (a.source_region_id
    ? v.size[0]*v.size[2]>.07
    : ["fixture","machine","vessel"].includes(GROUPS[a.class]) && v.size[0]*v.size[2]>.07));
};
export const groupOf = a => a.source_region_id && a.label_verified !== true ? "other" : a.movable ? "movable" : GROUPS[a.class] || "other";
export function regionVisible(asset,{all=false,raised=false,floors=true,selectedId}={}) {
  if (asset.id===selectedId || asset.is_target) return true;
  if (floorRegion(asset)) return floors;
  return overhead(asset) ? raised : all || major(asset);
}
export function layerSummary(assets,options) {
  const visible=assets.filter(a=>regionVisible(a,options));
  const hidden=assets.filter(a=>!regionVisible(a,options));
  return {visible:visible.length,total:assets.length,hiddenSmall:hidden.filter(a=>!floorRegion(a)&&!overhead(a)).length,
    hiddenOverhead:hidden.filter(overhead).length,hiddenFloor:hidden.filter(floorRegion).length};
}
export function layerSummaryText(summary,noun="regions",hiddenName="small") {
  const parts=[`${summary.visible.toLocaleString()} / ${summary.total.toLocaleString()} ${noun} shown`];
  if(summary.hiddenSmall) parts.push(`${summary.hiddenSmall.toLocaleString()} ${hiddenName} hidden`);
  if(summary.hiddenOverhead) parts.push(`${summary.hiddenOverhead.toLocaleString()} overhead hidden`);
  if(summary.hiddenFloor) parts.push(`${summary.hiddenFloor} floor patches hidden`);
  return parts.join(" · ");
}
