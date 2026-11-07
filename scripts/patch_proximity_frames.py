"""Idempotent UI patch: make proximity 3D frames clearly visible (demo-style coloured frames + tags)."""
from pathlib import Path

p = Path(__file__).resolve().parents[1] / "web" / "app.js"
s = p.read_text()
edits = [
    ("""    const lineMat = new THREE.LineBasicMaterial({
      color: hex,
      transparent: true,
      opacity: 0.95,
    });""",
     """    const lineMat = new THREE.LineBasicMaterial({
      color: hex,
      transparent: true,
      opacity: 0.95,
      depthTest: false, // frames stay visible inside dense splats
    });"""),
    ("""    const fillMat = new THREE.MeshBasicMaterial({
      color: hex,
      transparent: true,
      opacity: 0.04,
      depthWrite: false,
    });""",
     """    const fillMat = new THREE.MeshBasicMaterial({
      color: hex,
      transparent: true,
      opacity: 0.04,
      depthWrite: false,
      depthTest: false,
    });"""),
    ("""      entry.lineMat.color.setHex(getAssetBoxColor(a));
      entry.lineMat.opacity = a.mapped ? (isNear ? 0.78 : 0.42) : 0.28;
      entry.fillMat.opacity = a.mapped ? 0.02 : 0.005;""",
     """      const col = a.condition_state === "healthy" ? instanceColor(a.id) : getAssetBoxColor(a);
      entry.lineMat.color.setHex(isNear ? col : getAssetBoxColor(a));
      entry.fillMat.color.setHex(isNear ? col : getAssetBoxColor(a));
      entry.lineMat.opacity = isNear ? 0.95 : (a.mapped ? 0.42 : 0.28);
      entry.fillMat.opacity = isNear ? 0.10 : (a.mapped ? 0.02 : 0.005);"""),
    ("""function buildAssetBoundingBoxes() {""",
     """// "Every object, its own asset": stable distinct hue per extracted object
function instanceColor(id) {
  let h = 0;
  for (const ch of id) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  return new THREE.Color().setHSL(((h * 0.618033988749895) % 1), 0.75, 0.6).getHex();
}

function buildAssetBoundingBoxes() {"""),
    # The label title and budget edits that used to follow are built into app.js's updateHudLabels now
    # (no borrowed Excel profiles, budget 8 on the floor / 28 in Aerial view).
]
done = 0
for old, new in edits:
    if new in s:
        continue
    if s.count(old) != 1:
        print("anchor not found, skipped:", old.strip().splitlines()[0][:70]); continue
    s = s.replace(old, new); done += 1
p.write_text(s)
print(f"applied {done} edit(s)")
