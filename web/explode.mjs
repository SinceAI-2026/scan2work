// Exploded / segmentation view ("Every object, its own asset").
// Each extracted object gets a stable colour; a slider pushes every object outward from the room
// centre (splats in the shader, frames and tags in JS). Wired into app.js by scripts/patch_explode_view.py.
import * as THREE from "three";
import { assetPresentation, classHue } from "./asset-presentation.mjs";
import { decodeInstanceIds } from "./imported-assets.mjs";

export const explodeState = { amount: 0, segment: false };
const offsets = new Map();
let tintTable = null;   // {data, texture, assets, colours}: per-object class colour, -1 = not tinted
let alertTable = null;  // {data, texture, assets}: 1 for objects whose splats flash red (broken equipment)
let alertPredicate = null;

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

// One colour per class (the "Detected objects" layer): splats and frames of all valves share a hue, etc.
export function classColour(asset) {
  if(asset.source_region_id && asset.label_verified!==true)return new THREE.Color(0x97a9b0);
  return new THREE.Color(classHue(String(asset.class || "").toLowerCase()));
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

// Fail closed: a different scan's ownership must never select or edit an object.
export async function loadInstanceUniforms(url, count, texW, texH, assets) {
  computeOffsets(assets);
  const instData = new Float32Array(texW * texH);
  let complete = false;
  const tableW = 256, tableH = Math.ceil((assets.length + 1) / tableW);
  const table = new Float32Array(tableW * tableH * 4);
  const res = await fetch(url);
  if (!res.ok) throw new Error("Object membership is unavailable. Re-import the scene before selecting objects.");
  instData.set(decodeInstanceIds(await res.arrayBuffer(), count, assets.length));
  complete = true;
  assets.forEach((a, i) => {
    const o = offsets.get(a.id), c = classColour(a), k = (i + 1) * 4;
    table[k] = o.x; table[k + 1] = o.y; table[k + 2] = o.z;
    table[k + 3] = Math.round(c.r * 255) + Math.round(c.g * 255) * 256 + Math.round(c.b * 255) * 65536;
  });
  const tableTexture = floatTex(table, tableW, tableH, THREE.RGBAFormat);
  tintTable = { data: table, texture: tableTexture, assets, colours: assets.map((a, i) => table[(i + 1) * 4 + 3]) };
  const alertData = new Float32Array(tableW * tableH);
  alertTable = { data: alertData, texture: floatTex(alertData, tableW, tableH, THREE.RedFormat), assets };
  if (alertPredicate) setAlertMask(alertPredicate);
  const instanceTexture = floatTex(instData, texW, texH, THREE.RedFormat);
  instanceTexture.userData.complete = complete;
  return {
    uInstTex: { value: instanceTexture },
    uInstTable: { value: tableTexture },
    uInstTableSize: { value: new THREE.Vector2(tableW, tableH) },
    uExplode: { value: 0 },
    uCeilCut: { value: 0 },    // > 0: hide every scan splat above this height (bird's-eye "Hide ceiling")
    uSegment: { value: 0 },
    uApart: { value: 0 },      // take-apart view (take-apart.mjs): instance id + 1 of the isolated object, 0 = off
    uApartRoom: { value: 0 },  // opacity left to every other splat while one object is taken apart
    uApartSelf: { value: 1 },  // opacity of the isolated object's scan splats (0 while its saved model stands in)
    uProposalTex: { value: floatTex(new Float32Array(1),1,1,THREE.RedFormat) },
    uProposalId: { value: 0 }, // optional review overlay, independent of canonical object ownership
    uAlertTex: { value: alertTable.texture },  // per object: 1 = flash red (setAlertMask)
    uAlertPulse: { value: 0 },  // 0 = off; 0..1 = how red the flagged objects are this frame (setAlertPulse)
  };
}

export const SHADER_DECL = `
      uniform sampler2D uInstTex;
      uniform sampler2D uInstTable;
      uniform vec2 uInstTableSize;
      uniform float uExplode;
      uniform float uCeilCut;
      uniform float uSegment;
      uniform float uApart;
      uniform float uApartRoom;
      uniform float uApartSelf;
      uniform sampler2D uProposalTex;
      uniform float uProposalId;
      uniform sampler2D uAlertTex;
      uniform float uAlertPulse;
`;
export const SHADER_OFFSET = `
        float instId = synthetic ? floor(max(0.0, -fillCovB.w)) : texture2D(uInstTex, uv).x;
        vec4 instRow = vec4(0.0);
        if (instId > 0.5) {
          vec2 tuv = (vec2(mod(instId, uInstTableSize.x), floor(instId / uInstTableSize.x)) + 0.5) / uInstTableSize;
          if (!synthetic && uHasRemoved > 0.5 && texture2D(uRemoved, tuv).x > 0.5) {
            gl_Position = vec4(0.0, 0.0, 2.0, 1.0);
            return;
          }
          instRow = texture2D(uInstTable, tuv);
          center += instRow.xyz * uExplode * step(uApart, 0.5);
        }
`;
export const SHADER_COLOR = `
        if (instId > 0.5 && uSegment > 0.5 && uApart < 0.5 && instRow.w >= 0.0) {
          float p = instRow.w;
          vec3 ic = vec3(mod(p, 256.0), mod(floor(p / 256.0), 256.0), floor(p / 65536.0)) / 255.0;
          vColor.rgb = mix(vColor.rgb, ic, 0.85);
        }
        if (instId > 0.5 && uAlertPulse > 0.0 && uApart < 0.5) {
          vec2 auv = (vec2(mod(instId, uInstTableSize.x), floor(instId / uInstTableSize.x)) + 0.5) / uInstTableSize;
          // Toward a bright red and a little lighter at the peak, so even red-painted equipment visibly blinks.
          if (texture2D(uAlertTex, auv).x > 0.5) vColor.rgb = min(mix(vColor.rgb, vec3(1.0, 0.1, 0.06), uAlertPulse) + 0.22 * uAlertPulse, 1.0);
        }
        if (uApart > 0.5) vColor.a *= abs(instId - uApart) > 0.5 ? uApartRoom : uApartSelf;
        else if (instId < 0.5) vColor.a *= 1.0 - 0.85 * max(uExplode, uSegment * 0.6);
        if (uProposalId > 0.5 && !synthetic && abs(texture2D(uProposalTex, uv).x - uProposalId) < 0.5) {
          vColor.rgb = mix(vColor.rgb, vec3(1.0, 0.38, 0.13), 0.75);
        }
`;

// Coloured objects: only objects the predicate accepts take their class colour (the "Show in room" boxes).
export function setTintMask(predicate) {
  if (!tintTable) return;
  const { data, texture, assets, colours } = tintTable;
  assets.forEach((a, i) => { data[(i + 1) * 4 + 3] = predicate(a) ? colours[i] : -1; });
  texture.needsUpdate = true;
}

// Broken equipment flashes red when frames are off: which objects (once), and how red this frame (every frame).
export function setAlertMask(predicate) {
  alertPredicate = predicate;   // kept, so a call before the splats load still applies
  if (!alertTable) return;
  const { data, texture, assets } = alertTable;
  assets.forEach((a, i) => { data[i + 1] = predicate(a) ? 1 : 0; });
  texture.needsUpdate = true;
}

export function setAlertPulse(splatMaterial, value) {
  const u = splatMaterial?.uniforms?.uAlertPulse;
  if (u) u.value = value;
}

// Called once per frame after app.js has styled the boxes.
export function applyExplode(boxMeshes, splatMaterial) {
  const active = explodeState.amount > 0.01 || explodeState.segment;
  if (splatMaterial && splatMaterial.uniforms.uExplode) {
    splatMaterial.uniforms.uExplode.value = explodeState.amount;
    splatMaterial.uniforms.uSegment.value = explodeState.segment ? 1 : 0;
  }
  for (const entry of boxMeshes.values()) {
    const a = entry.asset;
    if (entry.removed) continue;
    entry.group.position.set(a.center[0], a.center[1], a.center[2]).add(explodeOffset(a));
    if (!active) continue;
    const passiveColor = assetPresentation(a).frameColor;
    const c = a.is_target ? new THREE.Color(0xd94838) : explodeState.segment ? classColour(a)
      : passiveColor !== null ? new THREE.Color(passiveColor) : objectColor(a.id);
    // The display and proximity controls remain authoritative while exploding.
    if (!entry.group.visible) continue;
    entry.lineMat.color.copy(c);
    entry.fillMat.color.copy(c);
    entry.lineMat.opacity = 0.9;
    entry.fillMat.opacity = explodeState.segment ? 0.06 : 0.12;
  }
}

export function mountExplodeControls() {
  const range = document.getElementById("explode-range");
  if (range && !range.dataset.bound) {
    range.dataset.bound = "1";
    range.addEventListener("input", e => { explodeState.amount = parseFloat(e.target.value); });
  }
  const style = document.createElement("style");
  style.textContent = `.hud-tag.movable { background: #6b4fbb; color: #fff; border-color: #8b6fe0; }`;
  document.head.appendChild(style);
}
