"""Idempotent UI patch: exploded / per-object segmentation view + movable-object styling.

Run from valmet-twin/:  ./.venv/bin/python scripts/patch_explode_view.py   (safe to re-run)
Needs web/explode.mjs and web/data/splat_instance.bin (written by python -m pipeline.run).
"""
import re
from pathlib import Path

p = Path(__file__).resolve().parents[1] / "web" / "app.js"
s = p.read_text()

EDITS = [
    # 1. import the module
    ('import { OrbitControls } from "three/addons/controls/OrbitControls.js";',
     'import { OrbitControls } from "three/addons/controls/OrbitControls.js";\n'
     'import { explodeState, explodeOffset, loadInstanceUniforms, applyExplode, mountExplodeControls, '
     'SHADER_DECL, SHADER_OFFSET, SHADER_COLOR } from "./explode.mjs";'),
    # 2. load per-splat object ids before the splat material is created
    ("  splatMaterial = new THREE.ShaderMaterial({",
     '  const explodeUniforms = await loadInstanceUniforms("./data/splat_instance.bin", count, TEX_W, TEX_H, state.twin.assets);\n'
     "  splatMaterial = new THREE.ShaderMaterial({"),
    ("      uFocal: { value: new THREE.Vector2(900, 900) },",
     "      uFocal: { value: new THREE.Vector2(900, 900) },\n      ...explodeUniforms,"),
    # 3. shader: declarations, per-object offset, per-object colour
    ("      uniform vec2 uFocal;\n",
     "      uniform vec2 uFocal;\n${SHADER_DECL}"),
    ("        vec3 center = centerRgba.xyz;\n",
     "        vec3 center = centerRgba.xyz;\n${SHADER_OFFSET}"),
    ("        vColor = vec4(r, g, b, a);\n",
     "        vColor = vec4(r, g, b, a);\n${SHADER_COLOR}"),
    # 4. frames + tags follow the explosion; controls in the Display panel
    ("  renderJourneyControls(nav);",
     "  applyExplode(boxMeshes, splatMaterial);\n  renderJourneyControls(nav);"),
    # (labels following the explosion is built into app.js's updateHudLabels now)
    ("  buildAssetBoundingBoxes();\n",
     "  buildAssetBoundingBoxes();\n  mountExplodeControls();\n"),
    # 5. movable objects: violet frames
    ("function getAssetBoxColor(asset) {\n",
     "function getAssetBoxColor(asset) {\n  if (asset.movable) return 0x8b5cf6; // movable object (changeable obstacle)\n"),
]

applied = 0
for old, new in EDITS:
    if new in s:
        continue
    if s.count(old) != 1:
        print(f"  ! anchor not found ({s.count(old)} matches): {old.strip()[:70]}")
        continue
    s = s.replace(old, new)
    applied += 1

# 6. health text for non-instrumented (movable) objects
n_health = 0
for var in (() if '"Movable object"' in s else ("a", "asset")):
    pat = "Health ${" + var + ".health_score}%"
    rep = "${" + var + ".health_score == null ? \"Movable object\" : `Health ${" + var + ".health_score}%`}"
    if pat in s:
        n_health += s.count(pat)
        s = s.replace(pat, rep)

p.write_text(s)
print(f"applied {applied} edit(s), {n_health} health label(s)")
