// In-room Take apart, the pure part (room-parts.mjs draws it): which split unit stands at an object, where the objects'
// synthetic Excel issues live on its parts, label layout. node tests/test_parts_data.mjs
// Take apart ported from Mikael Lehtonen's scan2service (web/parts.js) via SinceAI-2026/mvp; UX after Rasmus Mäkinen's
// pump explode. Data: web/data/take_apart/index.json (scripts/import_take_apart.py).
// The models were fitted in raw PLY coordinates of the same scan this viewer levels, so they stand where their objects
// are: everything here matches by POSITION (an object's box centre inside a model's or part's raw box), never by tag.

// pipeline/ingest.py level_scene: this scene = (x, 1.185 - y, -z) of the raw PLY (the same formula both ways)
export const RAW_FLOOR_Y = 1.185;
export const toRaw = p => [p[0], RAW_FLOOR_Y - p[1], -p[2]];
export const inside = (box, p) => box && [0, 1, 2].every(k => p[k] >= box[0][k] && p[k] <= box[1][k]);

/** The split unit standing at this object (its centre inside the unit's box): { name, entry }, or null. */
export function unitFor(doc, asset) {
  if (!asset?.center) return null;
  const at = toRaw(asset.center);
  for (const [name, entry] of Object.entries(doc?.models ?? {})) if (inside(entry.box, at)) return { name, entry };
  return null;
}

/** This viewer's objects inside the unit, with their own Excel issues (focus = the object taken apart). */
export function unitDevices(unit, focus, assets) {
  return assets.filter(a => a.center && inside(unit.entry.box, toRaw(a.center)))
    .map(a => ({ id: a.id, tag: a.tag || a.id, issue: a.issue_code, own: a.id === focus.id, at: toRaw(a.center) }));
}

/** Where each object's issue lives: a part whose fault_codes hold it, preferring the part the object stands in. */
export function faultParts(entry, devices) {
  const out = [];
  for (const d of devices) {
    const code = String(d.issue ?? "").toUpperCase();
    const fits = entry.parts.filter(p => !p.context && (p.fault_codes ?? []).includes(code));
    const p = fits.find(x => inside(x.box, d.at)) ?? fits[0];
    if (p) out.push({ node: p.node, tag: d.tag, code, own: d.own, note: entry.fault_notes?.[code] ?? null });
  }
  return out;
}

/** Our objects' real issues that no part of the model carries (shown in the dock, so none is silently dropped). */
const NO_ISSUE = new Set(["", "NORMAL", "NO_PACK_RECORD", "MOVABLE", "NOT_INSTRUMENTED"]);
export const unplacedFaults = (devices, faults) =>
  devices.filter(d => !NO_ISSUE.has(String(d.issue ?? "").toUpperCase()) && !faults.some(f => f.tag === d.tag));

/** The object a part holds (its centre inside the part's box), for the part's callout. */
export const deviceIn = (part, devices) => devices.find(d => inside(part.box, d.at)) ?? null;

/** Floor distance (m) from a point to the device's box: "at the device" when small. */
export function floorDistance(asset, x, z) {
  const lo = asset.aabb_min, hi = asset.aabb_max;
  return Math.hypot(Math.max(lo[0] - x, 0, x - hi[0]), Math.max(lo[2] - z, 0, z - hi[2]));
}

/** One step of the exponential ease towards target (dt in seconds). */
export const easeTo = (cur, target, dt) => cur + (target - cur) * (1 - Math.exp(-7 * dt));

/** Label positions in one column: the anchors' heights in order, at least gap apart, kept within [lo, hi]. */
export function spreadLabels(ys, gap, lo, hi) {
  const idx = ys.map((y, i) => [y, i]).sort((a, b) => a[0] - b[0]);
  const out = new Array(ys.length);
  let prev = -Infinity;
  for (const [y, i] of idx) out[i] = prev = Math.max(y, prev + gap);
  const over = Math.max(0, Math.max(...out, lo) - hi);
  if (over > 0) {
    let next = Infinity;
    for (const [, i] of [...idx].reverse()) out[i] = next = Math.min(out[i] - over, next - gap);
  }
  return out.map(y => Math.max(lo, y));
}

export const sizeText = s => (s?.length === 3 ? `${s[0]} × ${s[1]} × ${s[2]} mm` : "");
export const codeText = code => String(code).replace(/_/g, " ").toLowerCase();
