export function assetPresentation(asset) {
  const cls = String(asset.class || "").toLowerCase();
  const kind = asset.condition_state === "unknown" || (asset.source_region_id && !asset.mapped) ? "candidate"
    : asset.condition_state === "not_instrumented" ? "facility"
    : asset.movable || asset.condition_state === "movable" || cls.startsWith("movable_") ? "movable" : asset.mapped === false || asset.asset_class === "UNRECORDED_DEVICE" ? "detected" : "instrumented";
  return {
    kind,
    category: asset.class_label || String(asset.class || "Equipment").replaceAll("_", " "),
    notice: kind === "candidate" ? "Unreviewed region · no assigned device data"
      : kind === "detected" ? "Found in the scan · no record in the builder pack"
      : kind === "facility" ? "Facility item (not instrumented)"
      : kind === "movable" ? "Movable object (not instrumented)" : "",
    frameColor: kind === "candidate" ? 0x97a9b0 : kind === "detected" ? classHue(cls) : kind === "facility" ? 0x9098a1 : kind === "movable" ? 0x8b5cf6 : null,
    needsAttention: kind === "instrumented" && ["watch", "degraded", "attention_required"].includes(asset.condition_state),
  };
}

export function displayAssetTag(asset) {
  return asset.mapped === false ? asset.id : asset.tag || asset.id;
}

// One fixed colour per class, after linards-main-new's classColor.ts (same hues for the same kinds of object:
// valves green, pumps red-orange, motors violet, transmitters yellow, vibration sensors sky blue, pipes blue).
// Pipe kinds are shades of the pipe hue, as look-alike types are shades of their class there.
const CLASS_HSL = {
  control_valve: [150, 70, 50],
  circulation_pump: [10, 85, 60], process_pump: [10, 85, 60], pump: [10, 85, 60],
  electric_motor: [270, 75, 68],
  pressure_sensor: [45, 90, 58], instrument: [45, 90, 58],
  vibration_sensor: [200, 85, 60],
  vertical_pipe: [222, 85, 62], horizontal_pipe: [222, 85, 50], overhead_pipe: [214, 70, 72], pipe_fitting: [230, 80, 56],
  expansion_vessel: [0, 80, 42], tank_vessel: [345, 60, 45], heat_exchanger: [355, 75, 50],
  electrical_cabinet: [240, 45, 60],
  structural_column: [215, 15, 70], shelf_rack: [30, 25, 55], appliance: [180, 15, 82], sink: [180, 15, 70],
  movable_cart: [95, 55, 48], movable_container: [330, 70, 62], movable_crate: [32, 60, 45],
  movable_hose: [0, 0, 38], movable_ladder: [65, 55, 45], movable_cleaning: [175, 70, 48], movable_furniture: [290, 55, 60],
  clutter: [0, 0, 62], industrial_asset: [0, 0, 62],
};

function classHsl(cls) {
  if (CLASS_HSL[cls]) return CLASS_HSL[cls];
  let h = 0;   // any other class: a stable hue per name (linards-project/class-colors.js)
  for (const ch of cls) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  return [h % 360, 85, 60];
}

export function classCss(cls) {
  const [h, s, l] = classHsl(cls);
  return `hsl(${h}, ${s}%, ${l}%)`;
}

export function classHue(cls) {
  const [h, s, l] = classHsl(cls).map((v, i) => i ? v / 100 : v);
  const a = s * Math.min(l, 1 - l);
  const f = n => { const k = (n + h / 30) % 12; return l - a * Math.max(-1, Math.min(k - 3, 9 - k, 1)); };
  return (Math.round(f(0) * 255) << 16) | (Math.round(f(8) * 255) << 8) | Math.round(f(4) * 255);
}
