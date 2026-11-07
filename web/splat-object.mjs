// One object as Gaussian splats (EWA, the room viewer's projection), for the asset library and the take-apart view.
// Reads a GSPL file from web/data/library/<id>/: a 16-byte header, then per splat float32 xyz, uint8 rgba and
// float32 cov6 in the levelled frame. Objects are small enough to depth-sort on the main thread.
import * as THREE from "three";

export async function loadGspl(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${url}: HTTP ${res.status}`);
  const buf = await res.arrayBuffer();
  const head = new DataView(buf, 0, 16);
  if (String.fromCharCode(...new Uint8Array(buf, 0, 4)) !== "GSPL") throw new Error(`${url}: not a GSPL file`);
  const count = head.getUint32(8, true), stride = head.getUint32(12, true);
  const dv = new DataView(buf, 16), u8 = new Uint8Array(buf, 16);
  const center = new Float32Array(count * 3), color = new Float32Array(count * 4);
  const covA = new Float32Array(count * 3), covB = new Float32Array(count * 3);
  for (let i = 0; i < count; i++) {
    const o = i * stride;
    for (let k = 0; k < 3; k++) center[i * 3 + k] = dv.getFloat32(o + 4 * k, true);
    for (let k = 0; k < 4; k++) color[i * 4 + k] = u8[o + 12 + k] / 255;
    for (let k = 0; k < 3; k++) covA[i * 3 + k] = dv.getFloat32(o + 16 + 4 * k, true);
    for (let k = 0; k < 3; k++) covB[i * 3 + k] = dv.getFloat32(o + 28 + 4 * k, true);
  }
  return { count, center, color, covA, covB };
}

const VERT = `
  attribute vec3 iCenter;
  attribute vec4 iColor;
  attribute vec3 iCovA;
  attribute vec3 iCovB;
  uniform vec2 uViewport;
  uniform vec2 uFocal;
  varying vec4 vColor;
  varying vec2 vQuad;
  void main() {
    vec4 cam = modelViewMatrix * vec4(iCenter, 1.0);
    if (cam.z > -0.02) { gl_Position = vec4(0.0, 0.0, 2.0, 1.0); return; }
    vec4 pos2d = projectionMatrix * cam;
    mat3 V = mat3(iCovA.x, iCovA.y, iCovA.z, iCovA.y, iCovB.x, iCovB.y, iCovA.z, iCovB.y, iCovB.z);
    float tz = -cam.z, tz2 = tz * tz;
    float cx = clamp(cam.x, -1.3 * tz, 1.3 * tz), cy = clamp(cam.y, -1.3 * tz, 1.3 * tz);
    vec3 ju = vec3(uFocal.x / tz, 0.0, uFocal.x * cx / tz2);
    vec3 jv = vec3(0.0, uFocal.y / tz, uFocal.y * cy / tz2);
    mat3 R = mat3(modelViewMatrix);
    vec3 wu = ju * R, wv = jv * R;
    float a = dot(wu, V * wu) + 0.3, b = dot(wu, V * wv), c = dot(wv, V * wv) + 0.3;
    float mid = 0.5 * (a + c), r = sqrt(max(0.0001, (a - c) * (a - c) * 0.25 + b * b));
    float l1 = mid + r, l2 = max(0.3, mid - r);
    vec2 d = a >= c ? normalize(vec2(max(l1 - c, 1e-6), b)) : normalize(vec2(b, max(l1 - a, 1e-6)));
    vec2 major = min(2.0 * sqrt(l1), 512.0) * d, minor = min(2.0 * sqrt(l2), 512.0) * vec2(-d.y, d.x);
    vColor = iColor;
    vQuad = position.xy;
    gl_Position = vec4(pos2d.xy + (position.x * major + position.y * minor) / uViewport * pos2d.w, pos2d.z, pos2d.w);
  }`;
const FRAG = `
  varying vec4 vColor;
  varying vec2 vQuad;
  void main() {
    float p = -0.5 * dot(vQuad, vQuad);
    if (p < -4.5) discard;
    float alpha = min(0.99, vColor.a * exp(p));
    if (alpha < 0.004) discard;
    gl_FragColor = vec4(vColor.rgb * alpha, alpha);
  }`;

/** A THREE.Group of the splats, centred on the object's middle (userData.mid: that middle in the data's frame);
 * call userData.sort(camera, renderer) before each render. */
export function createSplatObject(data) {
  const { count } = data;
  const lo = [Infinity, Infinity, Infinity], hi = [-Infinity, -Infinity, -Infinity];
  for (let i = 0; i < count; i++) for (let k = 0; k < 3; k++) {
    const v = data.center[i * 3 + k];
    if (v < lo[k]) lo[k] = v;
    if (v > hi[k]) hi[k] = v;
  }
  const mid = lo.map((v, k) => (v + hi[k]) / 2);
  const radius = Math.hypot(hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2]) / 2 || 0.1;
  const quad = new THREE.PlaneGeometry(6, 6);
  const geom = new THREE.InstancedBufferGeometry();
  geom.index = quad.index;
  geom.setAttribute("position", quad.attributes.position);
  const attrs = { iCenter: [data.center, 3], iColor: [data.color, 4], iCovA: [data.covA, 3], iCovB: [data.covB, 3] };
  const sorted = {};
  for (const [name, [src, size]] of Object.entries(attrs)) {
    sorted[name] = { src, size, attr: new THREE.InstancedBufferAttribute(new Float32Array(src), size) };
    sorted[name].attr.setUsage(THREE.DynamicDrawUsage);
    geom.setAttribute(name, sorted[name].attr);
  }
  geom.instanceCount = count;
  const material = new THREE.ShaderMaterial({
    uniforms: { uViewport: { value: new THREE.Vector2(800, 600) }, uFocal: { value: new THREE.Vector2(700, 700) } },
    vertexShader: VERT, fragmentShader: FRAG, transparent: true, depthWrite: false, depthTest: true,
    blending: THREE.CustomBlending, blendSrc: THREE.OneFactor, blendDst: THREE.OneMinusSrcAlphaFactor,
    side: THREE.DoubleSide,
  });
  const mesh = new THREE.Mesh(geom, material);
  mesh.frustumCulled = false;
  mesh.renderOrder = 2;
  mesh.position.set(-mid[0], -mid[1], -mid[2]);
  const holder = new THREE.Group();
  holder.add(mesh);
  const depth = new Float32Array(count), order = new Uint32Array(count);
  let lastKey = "";
  const m = new THREE.Matrix4();
  holder.userData = {
    radius, mid, count,
    /** Back-to-front order for this camera, and the projection uniforms for this renderer. */
    sort(camera, renderer) {
      const size = renderer.getDrawingBufferSize(material.uniforms.uViewport.value);
      const p = camera.projectionMatrix.elements;
      material.uniforms.uFocal.value.set(0.5 * size.x * p[0], 0.5 * size.y * p[5]);
      mesh.updateWorldMatrix(true, false);
      m.multiplyMatrices(camera.matrixWorldInverse, mesh.matrixWorld);
      const e = m.elements;
      const key = `${e[2].toFixed(3)},${e[6].toFixed(3)},${e[10].toFixed(3)},${e[14].toFixed(3)}`;
      if (key === lastKey) return;
      lastKey = key;
      for (let i = 0; i < count; i++) {
        const c = data.center;
        depth[i] = c[i * 3] * e[2] + c[i * 3 + 1] * e[6] + c[i * 3 + 2] * e[10];
        order[i] = i;
      }
      order.sort((a, b) => depth[a] - depth[b]); // more negative view z = farther: drawn first
      for (const s of Object.values(sorted)) {
        const out = s.attr.array;
        for (let j = 0; j < count; j++) {
          const i = order[j];
          for (let k = 0; k < s.size; k++) out[j * s.size + k] = s.src[i * s.size + k];
        }
        s.attr.needsUpdate = true;
      }
    },
    dispose() {
      geom.dispose();
      quad.dispose();
      material.dispose();
    },
  };
  return holder;
}
