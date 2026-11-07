// Exploded / segmentation view ("Every object, its own asset").
// Each extracted object gets a stable colour; a slider pushes every object outward from the room
// centre (splats in the shader, frames and tags in JS). Wired into app.js by scripts/patch_explode_view.py.
import * as THREE from "three";
import { assetPresentation } from "./asset-presentation.mjs";

export const explodeState = { amount: 0, segment: false };
const offsets = new Map();

export function objectColor(id) {
  let h = 0;
  for (const ch of id) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  return new THREE.Color().setHSL((h * 0.618033988749895) % 1, 0.75, 0.6);
}

function computeOffsets(assets) {
  let sx = 0, sz = 0;
  for (const a of assets) { sx += a.center[0]; sz += a.center[2]; }
  const cx = sx / assets.length, cz = sz / assets.length;
  for (const a of assets) {
    // radial expansion from the room centre + lift by height: the classic exploded-view layout
    offsets.set(a.id, new THREE.Vector3((a.center[0] - cx) * 0.55, 0.15 + 0.35 * a.center[1], (a.center[2] - cz) * 0.55));
  }
}

export function explodeOffset(asset) {
  const o = offsets.get(asset.id);
  return o ? o.clone().multiplyScalar(explodeState.amount) : new THREE.Vector3();
}

function floatTex(data, w, h, format) {
  const t = new THREE.DataTexture(data, w, h, format, THREE.FloatType);
  t.magFilter = t.minFilter = THREE.NearestFilter;
  t.generateMipmaps = false;
  t.needsUpdate = true;
  return t;
}

// Returns shader uniforms. Missing file -> harmless dummy textures (feature simply does nothing).
export async function loadInstanceUniforms(url, count, texW, texH, assets) {
  computeOffsets(assets);
  const instData = new Float32Array(texW * texH);
  const tableW = 256, tableH = Math.ceil((assets.length + 1) / tableW);
  const table = new Float32Array(tableW * tableH * 4);
  try {
    const res = await fetch(url);
    if (res.ok) {
      const buf = await res.arrayBuffer();
      const n = new DataView(buf).getUint32(8, true);
      const ids = new Uint16Array(buf, 12, Math.min(n, count));
      for (let i = 0; i < ids.length; i++) instData[i] = ids[i] === 65535 ? 0 : ids[i] + 1;
    }
  } catch (e) { console.warn("explode view: no splat_instance.bin", e); }
  assets.forEach((a, i) => {
    const o = offsets.get(a.id), c = objectColor(a.id), k = (i + 1) * 4;
    table[k] = o.x; table[k + 1] = o.y; table[k + 2] = o.z;
    table[k + 3] = Math.round(c.r * 255) + Math.round(c.g * 255) * 256 + Math.round(c.b * 255) * 65536;
  });
  return {
    uInstTex: { value: floatTex(instData, texW, texH, THREE.RedFormat) },
    uInstTable: { value: floatTex(table, tableW, tableH, THREE.RGBAFormat) },
    uInstTableSize: { value: new THREE.Vector2(tableW, tableH) },
    uExplode: { value: 0 },
    uSegment: { value: 0 },
  };
}

export const SHADER_DECL = `
      uniform sampler2D uInstTex;
      uniform sampler2D uInstTable;
      uniform vec2 uInstTableSize;
      uniform float uExplode;
      uniform float uSegment;
`;
export const SHADER_OFFSET = `
        float instId = texture2D(uInstTex, uv).x;
        vec4 instRow = vec4(0.0);
        if (instId > 0.5) {
          vec2 tuv = (vec2(mod(instId, uInstTableSize.x), floor(instId / uInstTableSize.x)) + 0.5) / uInstTableSize;
          instRow = texture2D(uInstTable, tuv);
          center += instRow.xyz * uExplode;
        }
`;
export const SHADER_COLOR = `
        if (instId > 0.5 && uSegment > 0.5) {
          float p = instRow.w;
          vec3 ic = vec3(mod(p, 256.0), mod(floor(p / 256.0), 256.0), floor(p / 65536.0)) / 255.0;
          vColor.rgb = mix(vColor.rgb, ic, 0.85);
        }
        if (instId < 0.5) vColor.a *= 1.0 - 0.85 * max(uExplode, uSegment * 0.6);
`;

// Called once per frame after app.js has styled the boxes.
export function applyExplode(boxMeshes, splatMaterial) {
  const active = explodeState.amount > 0.01 || explodeState.segment;
  if (splatMaterial && splatMaterial.uniforms.uExplode) {
    splatMaterial.uniforms.uExplode.value = explodeState.amount;
    splatMaterial.uniforms.uSegment.value = explodeState.segment ? 1 : 0;
  }
  for (const entry of boxMeshes.values()) {
    const a = entry.asset;
    entry.group.position.set(a.center[0], a.center[1], a.center[2]).add(explodeOffset(a));
    if (!active) continue;
    const passiveColor = assetPresentation(a).frameColor;
    const c = passiveColor !== null ? new THREE.Color(passiveColor) : a.is_target ? new THREE.Color(0xd94838) : objectColor(a.id);
    entry.group.visible = true;
    entry.lineMat.color.copy(c);
    entry.fillMat.color.copy(c);
    entry.lineMat.opacity = 0.9;
    entry.fillMat.opacity = explodeState.segment ? 0.06 : 0.12;
  }
}

export function mountExplodeControls() {
  const host = document.querySelector(".display-options-body");
  if (!host || document.getElementById("explode-range")) return;
  const wrap = document.createElement("div");
  wrap.className = "explode-controls";
  wrap.innerHTML = `
    <label for="explode-range">Explode objects</label>
    <input id="explode-range" type="range" min="0" max="1" step="0.01" value="0" aria-label="Explode objects">
    <label class="explode-toggle"><input id="segment-toggle" type="checkbox"> Colour each object</label>`;
  host.appendChild(wrap);
  wrap.querySelector("#explode-range").addEventListener("input", e => { explodeState.amount = parseFloat(e.target.value); });
  wrap.querySelector("#segment-toggle").addEventListener("change", e => { explodeState.segment = e.target.checked; });
  const style = document.createElement("style");
  style.textContent = `
    .explode-controls { display: grid; gap: 6px; margin-top: 8px; }
    .explode-controls input[type=range] { width: 100%; }
    .explode-toggle { display: flex; gap: 6px; align-items: center; }
    .hud-tag.movable { background: #6b4fbb; color: #fff; border-color: #8b6fe0; }`;
  document.head.appendChild(style);
}
