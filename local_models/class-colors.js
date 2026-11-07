// One stable colour per detected class, shared by the viewer and the asset library.
// Fixed hues for the Excel classes so no two collide; anything else falls back to a hash.
const CLASS_COLORS = {
  pump: "hsl(10, 85%, 60%)",
  motor: "hsl(270, 75%, 68%)",
  valve: "hsl(150, 70%, 50%)",
  "pressure transmitter": "hsl(45, 90%, 58%)",
  "vibration sensor": "hsl(200, 85%, 60%)",
  unclassified: "hsl(0, 0%, 62%)",
};

export function classColor(name) {
  if (name in CLASS_COLORS) return CLASS_COLORS[name];
  let hash = 0;
  for (const char of name) hash = (hash * 31 + char.charCodeAt(0)) >>> 0;
  return `hsl(${hash % 360}, 85%, 60%)`; // comma syntax: THREE.Color doesn't parse the space form
}
