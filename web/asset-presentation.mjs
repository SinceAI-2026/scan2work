export function assetPresentation(asset) {
  const cls = String(asset.class || "").toLowerCase();
  const kind = asset.condition_state === "not_instrumented" ? "facility"
    : asset.movable || asset.condition_state === "movable" || cls.startsWith("movable_") ? "movable" : "instrumented";
  return {
    kind,
    category: asset.class_label || String(asset.class || "Equipment").replaceAll("_", " "),
    notice: kind === "facility" ? "Facility item (not instrumented)"
      : kind === "movable" ? "Movable object (not instrumented)" : "",
    frameColor: kind === "facility" ? 0x9098a1 : kind === "movable" ? 0x8b5cf6 : null,
    needsAttention: kind === "instrumented" && asset.condition_state !== "healthy",
  };
}
