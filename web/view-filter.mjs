// The Aerial view's "Show in room" checkboxes, shared by every layer: which objects get frames and labels
// (Scan), frames, labels and class tint (Coloured objects) or BIM geometry (BIM model), and whether the
// room's walls are drawn. The target and the selected object always show.
import { overhead, major } from "./semantic-plan.mjs";

const checked = id => !!document.getElementById(id)?.checked;

export const isPipe = asset => !!asset.cylinder || /pipe/.test(asset.class || "") || overhead(asset);

export function filterShows(asset) {
  if (isPipe(asset)) return checked("layer-pipes");
  if (checked("layer-all")) return true;
  return checked("layer-relevant") && major(asset);   // registered (Excel) assets and the larger machines, vessels, fixtures
}

export const wallsShown = () => checked("layer-walls");

export function onFilterChange(fn) {
  for (const id of ["layer-walls", "layer-pipes", "layer-all", "layer-relevant"])
    document.getElementById(id)?.addEventListener("change", fn);
}
