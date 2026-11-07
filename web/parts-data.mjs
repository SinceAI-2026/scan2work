// In-room Take apart, the pure part (room-parts.mjs draws it): which unit model a device gets, where our devices'
// synthetic Excel issues live on its parts, where it stands in this scene, label layout. node tests/test_parts_data.mjs
// Take apart ported from Mikael Lehtonen's scan2service (web/parts.js) via SinceAI-2026/mvp; UX after Rasmus Mäkinen's
// pump explode. Data: web/data/take_apart/index.json (scripts/import_take_apart.py).

// pipeline/ingest.py level_scene: this scene = (x, 1.185 - y, -z) of the raw PLY, which the unit models use
export const RAW_FLOOR_Y = 1.185;
export const toRaw = p => [p[0], RAW_FLOOR_Y - p[1], -p[2]];

/** The unit model a device gets, by Excel tag: { tag, model, card, how ("tag" or "standin"), entry }, or null. */
export function unitFor(doc, asset) {
  const d = doc?.devices?.[asset?.tag];
  const entry = d && doc.models?.[d.model];
  return entry ? { ...d, tag: asset.tag, entry } : null;
}

/** How far the model moves (raw PLY) so the assembled unit's centre sits on our device's box centre. (The models are
 *  floor-standing units; this viewer's pumps mostly hang on wall pipes, so the unit's height follows the device.) */
export function shiftFor(asset, unitCenterRaw) {
  const c = unitCenterRaw, d = toRaw(asset.center);
  return [d[0] - c[0], d[1] - c[1], d[2] - c[2]];
}

/**
 * Our devices on the unit, with their issues: the Excel devices whose tags the unit's scan2service objects carry. A
 * stand-in (P-001 shown as the P-005 unit) carries only the focus device's own issue and lends it to no one.
 */
export function unitDevices(doc, unit, focus, assetByTag) {
  if (unit.how === "standin") return [{ s2sId: unit.card, tag: focus.tag, issue: focus.issue_code, own: true }];
  const out = [];
  for (const id of unit.entry.ids) {
    const tag = doc.cards[id]?.tag, a = tag && assetByTag(tag);
    if (a) out.push({ s2sId: id, tag, issue: a.issue_code, own: tag === focus.tag });
  }
  return out;
}

/** Where each device's issue lives: the part whose fault_codes hold it, preferring the part serving that device. */
export function faultParts(entry, devices) {
  const out = [];
  for (const d of devices) {
    const code = String(d.issue ?? "").toUpperCase();
    if (!code || code === "NORMAL") continue;
    const fits = entry.parts.filter(p => !p.context && (p.fault_codes ?? []).includes(code));
    const p = fits.find(x => x.serves === d.s2sId) ?? fits[0];
    if (p) out.push({ node: p.node, tag: d.tag, code, own: d.own, note: entry.fault_notes?.[code] ?? null });
  }
  return out;
}

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
