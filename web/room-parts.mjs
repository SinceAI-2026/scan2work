// In-room Take apart (Mikael's): a pump's unit model, split into named parts, eases apart where the device stands, seen
// from the technician's eyes (first person) with the room still there, dimmed and softly blurred. Callout labels give
// each part's name, size in mm and our device's tag and fault; the dock on the right has Explode / Reassemble / Reset /
// Done, the fault list, the parts and a part's detail. A click on a part selects it and the eye eases ~30 % toward it.
// Only at the device: away from it the robot walks there first. Other frames and labels are hidden until Done.
// Take apart ported from Mikael Lehtonen's scan2service (web/parts.js) via SinceAI-2026/mvp; UX after Rasmus Mäkinen's
// pump explode. Data: web/data/take_apart/ (scripts/import_take_apart.py); rules: parts-data.mjs.
// Drawn by this viewer's renderer and camera, in a second pass after the room (its own scene, so the card's lights
// shade only the parts and the room never covers them).
import * as THREE from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { unitFor, unitDevices, faultParts, deviceIn, floorDistance, easeTo, spreadLabels, sizeText, codeText, RAW_FLOOR_Y } from "./parts-data.mjs";

const DATA = "./data/take_apart/";
const SPREAD = 1.9;     // further apart than on the card, so nothing overlaps from the eyes
const ROOM_FADE = 0.75; // the rest of the room only mildly dimmed: the same view, not a stage
const BLUR_PX = 3;      // and softly blurred behind the parts (Gaussian sigma in CSS px, as the MVP's blur(3px))
const NEAR_M = 1.3;     // "at the device": the robot this close to its box, on the floor
const GLIDE_S = 1.1;    // into the technician's eyes before the parts come apart
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => `&#${c.charCodeAt(0)};`);

// The room blur: the frame just drawn (room, frames, AI 3D models) copied to a texture, blurred in two passes (across at
// half resolution, then down onto the screen), before the parts are drawn sharp on top. Three cheap full-screen passes.
let blur = null;
function blurRoom(r, sigmaCss) {
  const size = r.getDrawingBufferSize(new THREE.Vector2()), w = size.x, h = size.y;
  if (!blur) {
    const mat = new THREE.ShaderMaterial({
      uniforms: { tex: { value: null }, step: { value: new THREE.Vector2() } },
      vertexShader: "varying vec2 vUv; void main() { vUv = uv; gl_Position = vec4(position.xy, 0.0, 1.0); }",
      // 9 taps, 2 texels apart with linear filtering, weights of a Gaussian with sigma = 2 taps
      fragmentShader: `uniform sampler2D tex; uniform vec2 step; varying vec2 vUv;
        void main() {
          vec4 c = vec4(0.0); float t = 0.0;
          for (int i = -4; i <= 4; i++) { float k = exp(-float(i * i) / 8.0); c += k * texture2D(tex, vUv + float(i) * step); t += k; }
          gl_FragColor = c / t;
        }`,
      depthTest: false, depthWrite: false,
    });
    const quad = new THREE.Mesh(new THREE.PlaneGeometry(2, 2), mat);
    quad.frustumCulled = false;
    const scene = new THREE.Scene();
    scene.add(quad);
    blur = { mat, scene, cam: new THREE.Camera(), frame: null, half: new THREE.WebGLRenderTarget(1, 1, { depthBuffer: false }) };
  }
  if (!blur.frame || blur.frame.image.width !== w || blur.frame.image.height !== h) {
    blur.frame?.dispose();
    blur.frame = new THREE.FramebufferTexture(w, h);
    blur.frame.minFilter = blur.frame.magFilter = THREE.LinearFilter;
    blur.half.setSize(Math.ceil(w / 2), Math.ceil(h / 2));
  }
  // sigma (drawing-buffer px) = 2 taps of `step`: step = sigma / 2
  const s = (sigmaCss * r.getPixelRatio()) / 2;
  r.copyFramebufferToTexture(blur.frame);
  const auto = r.autoClear;
  r.autoClear = false;
  blur.mat.uniforms.tex.value = blur.frame;
  blur.mat.uniforms.step.value.set(s / w, 0);
  r.setRenderTarget(blur.half);
  r.render(blur.scene, blur.cam);
  blur.mat.uniforms.tex.value = blur.half.texture;
  blur.mat.uniforms.step.value.set(0, s / h);
  r.setRenderTarget(null);
  r.render(blur.scene, blur.cam);
  r.autoClear = auto;
}

/** ctx: { scene, camera, renderer, container, state, uniforms(), look (first-person yaw/pitch), eye (m), setPreset(name),
 *  robot() -> {x, z}, walkTo(asset), closeOther(), boxMeshes, setInspector(open), onStage(asset, on) } */
export function createRoomParts(ctx) {
  let doc = null, live = null, pending = null, opening = false;
  const ready = fetch(`${DATA}index.json`).then(r => (r.ok ? r.json() : null)).then(d => (doc = d)).catch(() => null);
  const arrivedAt = a => ctx.state.journey.phase === "arrived" && ctx.state.journey.route?.asset_id === a.asset_id;
  const near = a => { const r = ctx.robot(); return floorDistance(a, r.x, r.z) <= NEAR_M || arrivedAt(a); };

  // Equipment details: Wael's own "Take apart" button (and T) run this one for a device with a split unit model
  // (walking there first if needed); for every other device they keep Wael's Take apart as it is.
  const btn = document.getElementById("btn-take-apart"), ownTitle = btn?.title ?? "";
  const mine = () => !!unitFor(doc, ctx.state.selectedAsset);
  document.addEventListener("click", e => {
    if (!e.target.closest?.("#btn-take-apart") || !mine()) return;
    e.stopPropagation(); // before Wael's listener on the button
    open(ctx.state.selectedAsset);
  }, { capture: true });
  function syncButton() {
    const a = ctx.state.selectedAsset;
    const title = !mine() ? ownTitle : pending === a ? "Walking there to take it apart in the room…"
      : near(a) ? "Take the unit apart here, in the room, from the technician's eyes (T; Esc or Done puts it back)"
      : "Walk there and take the unit apart in the room (T)";
    if (btn && btn.title !== title) btn.title = title;
    if (btn && mine() && btn.textContent !== "Take apart") btn.textContent = "Take apart"; // Wael's label returns on the next selection
  }

  function open(asset) {
    if (!unitFor(doc, asset) || live || opening) return;
    if (near(asset)) { pending = null; start(asset); return; }
    pending = asset; // walk there first; frame() starts it on arrival
    ctx.walkTo(asset);
  }

  async function start(asset) {
    opening = true;
    try {
      ctx.closeOther();
      const unit = unitFor(doc, asset);
      const gltf = await new GLTFLoader().loadAsync(DATA + unit.entry.file);
      live = build(asset, unit, gltf.scene);
    } catch (err) {
      console.error("in-room Take apart:", err);
    } finally {
      opening = false;
    }
  }

  function build(asset, unit, root) {
    const { entry } = unit;
    const devices = unitDevices(unit, asset, ctx.state.roomAssets); // our objects inside the unit, by position
    const faults = faultParts(entry, devices);
    // raw PLY → this scene (pipeline/ingest.py); the unit stands where it was fitted, on the same scan
    const world = new THREE.Group();
    world.scale.set(1, -1, -1);
    world.position.y = RAW_FLOOR_Y;
    const holder = new THREE.Group();
    world.add(holder);
    holder.add(root);
    const aniso = ctx.renderer.capabilities.getMaxAnisotropy();
    const parts = [];
    for (const p of entry.parts) {
      const obj = root.getObjectByName(p.node);
      if (!obj) continue;
      const fault = faults.find(f => f.node === p.node) ?? null;
      const box = new THREE.Box3();
      obj.traverse(n => {
        if (!n.isMesh) return;
        n.userData.part = p.node;
        const m = (n.material = n.material.clone()); // per part: its tint and glow, as the card
        if (p.context) { m.transparent = true; m.opacity = 0.5; }
        if (m.map) { // the photo texture: sharpest filtering the GPU has, mipmaps, sRGB
          m.map.anisotropy = aniso;
          m.map.colorSpace = THREE.SRGBColorSpace;
          m.map.generateMipmaps = true;
          m.map.minFilter = THREE.LinearMipmapLinearFilter;
          m.map.needsUpdate = true;
        } else { // the cut caps (no photo): cast-metal grey
          m.color?.set(0x7a8087);
          if ("roughness" in m) { m.roughness = 0.6; m.metalness = 0.35; }
        }
        n.geometry.computeBoundingBox();
        box.union(n.geometry.boundingBox);
        if (fault && !p.context) { // the fault part: an even tint and a rim shell for its outline
          m.color?.lerp(new THREE.Color(fault.own ? 0xff5a4f : 0xffb347), 0.35);
          const c = box.getCenter(new THREE.Vector3()), g = n.geometry.clone().translate(-c.x, -c.y, -c.z);
          const rim = new THREE.Mesh(g, new THREE.MeshBasicMaterial({ color: fault.own ? 0xff453a : 0xffb020, side: THREE.BackSide, transparent: true, opacity: 0.9 }));
          rim.scale.setScalar(1.018);
          rim.position.copy(c);
          rim.userData.rim = true;
          n.parent.add(rim);
        }
      });
      parts.push({ ...p, dist: p.dist * SPREAD, obj, anchor: box.getCenter(new THREE.Vector3()), t: 0, fault, label: null, line: null });
    }
    const pickable = parts.filter(p => !p.context);
    const tagOf = p => (p.fault ? `${p.fault.tag} ${codeText(p.fault.code)}` : deviceIn(p, devices)?.tag ?? "");

    // the card's light rig: hemisphere sky from above, a fixed key, key / fill / rim placed from the camera every frame
    const scene = new THREE.Scene();
    const key0 = new THREE.DirectionalLight(0xffffff, 0.9), key = new THREE.DirectionalLight(0xffffff, 2.4),
      fill = new THREE.DirectionalLight(0xdbe6ff, 1.0), rimL = new THREE.DirectionalLight(0xcfe2ff, 2.0);
    scene.add(new THREE.HemisphereLight(0xffffff, 0x404850, 2.4), world);
    for (const l of [key0, key, fill, rimL]) scene.add(l, l.target);

    // DOM: labels + leaders over the room, the dock at the right edge
    const ui = document.createElement("div");
    ui.className = "room-parts";
    ui.innerHTML = `<svg width="100%" height="100%"></svg><div class="rp-labels"></div>
      <div class="rp-dock">
        <div class="rp-box rp-bar"><b>Take apart · ${esc(asset.tag)} <span class="rp-muted">in the room</span></b>
          <input type="range" min="0" max="100" value="0" aria-label="How far apart"><output>0 %</output>
          <div class="rp-row"><button type="button" data-a="explode">Explode</button><button type="button" data-a="assemble">Reassemble</button>
            <button type="button" data-a="reset" title="All apart again, nothing selected">Reset</button><button type="button" class="rp-done" data-a="done" title="Put it back together (Esc)">Done</button></div></div>
        <div class="rp-box"><div class="rp-muted">The unit as it stands here: ${esc(entry.label)} · sizes ${esc(entry.size_label ?? "fitted to the scan")}</div>
          ${faults.map(f => `<div class="rp-fault${f.own ? "" : " mate"}"><b>${esc(f.tag)} ${esc(codeText(f.code))}</b> (synthetic) → ${esc(parts.find(x => x.node === f.node)?.name)}</div>`).join("")}</div>
        <div class="rp-box"><div class="rp-list"></div></div>
        <div class="rp-box rp-panel" hidden></div>
      </div>`;
    ctx.container.append(ui);
    const q = s => ui.querySelector(s);
    const svg = q("svg"), labels = q(".rp-labels"), list = q(".rp-list"), panel = q(".rp-panel"), slider = q("input"), out = q("output");
    for (const p of pickable) {
      p.label = Object.assign(document.createElement("div"), { className: "rp-label" });
      p.label.innerHTML = `${esc(p.name)}<small>${esc(sizeText(p.size_mm))}${tagOf(p) ? ` · ${esc(tagOf(p))}` : ""}</small>`;
      p.label.classList.toggle("fault", !!p.fault?.own);
      p.label.classList.toggle("fault2", !!p.fault && !p.fault.own);
      p.label.onclick = () => select(p.node);
      labels.append(p.label);
      const b = Object.assign(document.createElement("button"), { type: "button", textContent: p.name });
      b.dataset.node = p.node;
      b.onclick = () => select(p.node);
      list.append(b);
      p.line = document.createElementNS("http://www.w3.org/2000/svg", "line");
      p.line.setAttribute("stroke", "rgb(255 255 255 / 0.75)");
      p.line.setAttribute("stroke-width", "1");
      svg.append(p.line);
    }

    // the room: the device's own splats hidden (the parts stand there), the rest dimmed and blurred; frames, labels,
    // the navigation panels and Equipment details out of the way until Done
    const u = ctx.uniforms();
    const instance = ctx.state.twin.assets.findIndex(a => a.id === asset.id) + 1;
    const inspectorWas = document.body.classList.contains("inspector-open");
    ctx.setInspector(false);
    ctx.onStage?.(asset, true);
    document.body.classList.add("parts-mode");
    const setRoom = on => {
      if (!u?.uApart) return;
      u.uApart.value = on ? instance : 0;
      u.uApartRoom.value = on ? ROOM_FADE : 0;
      u.uApartSelf.value = on ? 0 : 1;
    };
    setRoom(true);

    // into the technician's eyes: first person at the robot, turned to the device, gliding from where the camera was
    const cam = ctx.camera, from = { p: cam.position.clone(), q: cam.quaternion.clone() };
    if (ctx.state.cameraPreset !== "first") ctx.setPreset("first");
    // looking at the middle of the unit as it will stand apart (the parts spread upward from the casing)
    for (const p of parts) p.obj.position.fromArray(p.dir).multiplyScalar(p.dist);
    world.updateMatrixWorld(true);
    const apart = new THREE.Box3();
    for (const p of pickable) apart.union(new THREE.Box3().setFromObject(p.obj));
    for (const p of parts) p.obj.position.set(0, 0, 0);
    const aim = apart.getCenter(new THREE.Vector3());
    const r0 = ctx.robot(), dx = aim.x - r0.x, dz = aim.z - r0.z;
    const yaw = Math.atan2(dx, dz);
    Object.assign(ctx.look, { body: yaw, yaw: 0, at: performance.now(),
      pitch: THREE.MathUtils.clamp(Math.atan2(aim.y - ctx.eye, Math.hypot(dx, dz)), -1.1, 1.1) });
    if (ctx.state.manualWalk) ctx.state.manualWalk.yaw = yaw;
    let glide = 0;

    let target = 0, explode = 0, selected = null, focused = null, focusK = 0, focusR = 0.2, hovered = null, closing = false, closed = false, pulledBack = 0;
    const focusAt = new THREE.Vector3(), v = new THREE.Vector3(), all = new THREE.Box3(), tmp = new THREE.Box3();
    function select(node, focus = true) { // a click (label, button, 3D) also eases the eye toward it
      selected = node && node !== selected ? node : null;
      focused = focus ? selected : null;
      for (const p of pickable) p.label.classList.toggle("sel", p.node === selected);
      for (const b of list.querySelectorAll("button")) b.setAttribute("aria-pressed", String(b.dataset.node === selected));
      const p = parts.find(x => x.node === selected);
      panel.hidden = !p;
      if (p) panel.innerHTML = `<h4>${esc(p.name)}${tagOf(p) ? ` <span class="rp-muted">· ${esc(tagOf(p))}</span>` : ""}</h4>
        ${p.fault ? `<p class="rp-fault"><b>${esc(p.fault.tag)} ${esc(codeText(p.fault.code))}</b>: ${esc(p.fault.note ?? "status synthetic (Valmet pack)")}</p>` : ""}
        <div class="rp-muted">${esc(sizeText(p.size_mm))} (height × along the pipe × across) · fitted to the scan</div>
        <p>${esc(p.what)} <span class="rp-muted">(general engineering knowledge)</span></p>`;
    }
    slider.addEventListener("input", () => (target = Number(slider.value) / 100));
    q(".rp-bar").addEventListener("click", e => {
      const a = e.target.closest("button")?.dataset.a;
      if (a === "explode") target = 1;
      else if (a === "assemble") { target = 0; select(null); }
      else if (a === "reset") { target = 1; select(null); }
      else if (a === "done") close();
    });
    const own = faults.find(f => f.own) ?? faults[0];
    if (own) select(own.node, false); // opens on the fault part, the eye where it is

    // a part in 3D: hover lights it, a click (not a drag) selects it as its label or button does
    const ray = new THREE.Raycaster(), ndc = new THREE.Vector2(), canvas = ctx.renderer.domElement;
    let down = null;
    const pickPart = (x, y) => {
      const rc = canvas.getBoundingClientRect();
      ndc.set(((x - rc.left) / rc.width) * 2 - 1, -((y - rc.top) / rc.height) * 2 + 1);
      ray.setFromCamera(ndc, cam);
      const hit = ray.intersectObjects(pickable.map(p => p.obj), true).find(h => h.object.userData.part && !h.object.userData.rim);
      return hit ? hit.object.userData.part : null;
    };
    const onMove = e => { if (!e.buttons) { hovered = pickPart(e.clientX, e.clientY); canvas.style.cursor = hovered ? "pointer" : ""; } };
    const onDown = e => (down = [e.clientX, e.clientY]);
    const onUp = e => {
      if (!down || Math.hypot(e.clientX - down[0], e.clientY - down[1]) > 5) return (down = null);
      down = null;
      const node = pickPart(e.clientX, e.clientY);
      if (node) select(node);
    };
    canvas.addEventListener("pointermove", onMove);
    canvas.addEventListener("pointerdown", onDown);
    canvas.addEventListener("pointerup", onUp);

    const lightFrom = (l, c, back, up, side, len) => {
      const d = new THREE.Vector3().subVectors(cam.position, c).normalize(), rr = len * 2, right = new THREE.Vector3().crossVectors(d, cam.up).normalize();
      l.position.copy(c).addScaledVector(d, back * rr).addScaledVector(cam.up, up * rr).addScaledVector(right, side * rr);
      l.target.position.copy(c);
    };
    const worldBox = p => tmp.setFromObject(p.obj);

    /** Every frame, after app.js placed the first-person camera: ease the parts, step the eye back if needed. */
    function frame(dt) {
      for (const e of ctx.boxMeshes.values()) e.group.visible = false;
      world.updateMatrixWorld(true);
      if (glide < GLIDE_S) { // glide from where the camera was into the robot's eyes, then apart
        glide += dt;
        const k = THREE.MathUtils.smoothstep(Math.min(1, glide / GLIDE_S), 0, 1);
        const p = cam.position.clone(), qq = cam.quaternion.clone();
        cam.position.copy(from.p).lerp(p, k);
        cam.quaternion.copy(from.q).slerp(qq, k);
        cam.updateMatrixWorld(true);
        if (glide >= GLIDE_S && !closing) target = 1;
        if (glide < GLIDE_S) return;
      }
      explode = easeTo(explode, target, dt);
      if (Math.abs(explode - target) < 0.002) explode = target;
      if (closing && explode === 0) return finish();
      slider.value = String(Math.round(target * 100));
      out.textContent = `${Math.round(explode * 100)} %`;
      all.makeEmpty();
      for (const p of parts) {
        const goal = p.context ? 0 : p.node === selected ? Math.max(explode, 0.35) : explode;
        p.t = p.node === selected ? easeTo(p.t, goal, dt) : goal;
        p.obj.position.set(p.dir[0] * p.dist * p.t, p.dir[1] * p.dist * p.t, p.dir[2] * p.dist * p.t);
        p.obj.updateMatrixWorld(true);
        if (!p.context) all.union(worldBox(p));
        const k = p.node === selected ? 0.26 : p.node === hovered ? 0.12 : 0; // selection / hover: a light cyan glow
        p.obj.traverse(n => {
          if (!n.isMesh || n.userData.rim || !n.material.emissive) return;
          n.material.emissive.set(k ? 0x3ad6ff : 0x000000);
          n.material.emissiveIntensity = k;
        });
      }
      // step the eye back just enough that the whole unit fits (app.js places the camera again every frame, so this
      // goes away by itself on Done; it eases with the explosion)
      const c = all.getCenter(new THREE.Vector3()), R = all.getSize(new THREE.Vector3()).length() / 2;
      const away = new THREE.Vector3().subVectors(cam.position, c), dist = away.length() || 1;
      const need = ((R * 1.2) / Math.tan(THREE.MathUtils.degToRad(cam.fov) / 2)) * Math.max(1, 1 / Math.max(0.5, cam.aspect));
      const back = Math.max(0, need - dist) * explode;
      if (back > 0) cam.position.addScaledVector(away.normalize(), back);
      pulledBack = +back.toFixed(3);
      // a selected part: the eye eases ~30 % of the way toward it and turns to it (never closer than 2.5 part radii)
      const fp = parts.find(x => x.node === focused);
      focusK = easeTo(focusK, fp ? 1 : 0, dt);
      if (fp) { const b = worldBox(fp); b.getCenter(focusAt); focusR = b.getSize(v).length() / 2; }
      if (focusK > 0.002) {
        const C = cam.position, d = C.distanceTo(focusAt) || 1, u3 = new THREE.Vector3().subVectors(focusAt, C).divideScalar(d);
        const step = Math.min(0.3 * d, Math.max(0, d - 2.5 * focusR)) * focusK, fwd = cam.getWorldDirection(new THREE.Vector3());
        C.addScaledVector(u3, step);
        cam.lookAt(fwd.lerp(u3, 0.8 * focusK).add(C));
      }
      cam.updateMatrixWorld(true);
      key0.position.copy(c).add(new THREE.Vector3(1.5, 3, 2));
      key0.target.position.copy(c);
      lightFrom(key, c, 0.7, 0.9, -0.8, R);
      lightFrom(fill, c, 0.6, 0.1, 1.0, R);
      lightFrom(rimL, c, -1.0, 0.7, 0.2, R);
    }

    /** After the room is drawn: the parts on top (depth cleared: splats write none), then labels and leaders. */
    function draw() {
      ui.hidden = glide < GLIDE_S;
      const k = closing ? explode : Math.min(1, glide / GLIDE_S); // in with the glide, out with the reassembly
      if (k > 0.02) blurRoom(ctx.renderer, BLUR_PX * k);
      const r = ctx.renderer, auto = r.autoClear;
      r.autoClear = false;
      r.clearDepth();
      r.render(scene, cam);
      r.autoClear = auto;
      const W = ctx.container.clientWidth, H = ctx.container.clientHeight;
      let sx0 = Infinity, sx1 = -Infinity;
      for (const p of pickable) {
        const b = worldBox(p);
        for (let i = 0; i < 8; i++) {
          v.set(i & 1 ? b.max.x : b.min.x, i & 2 ? b.max.y : b.min.y, i & 4 ? b.max.z : b.min.z).project(cam);
          if (v.z < 1) { const x = ((v.x + 1) / 2) * W; sx0 = Math.min(sx0, x); sx1 = Math.max(sx1, x); }
        }
      }
      const rows = [];
      for (const p of pickable) {
        v.copy(p.anchor).applyMatrix4(p.obj.matrixWorld).project(cam);
        const on = v.z < 1 && Math.abs(v.x) < 1.2 && Math.abs(v.y) < 1.2 && (explode >= 0.5 || p.node === selected || !!p.fault);
        p.label.hidden = !on;
        p.line.style.display = on ? "" : "none";
        if (on) rows.push({ p, x: ((v.x + 1) / 2) * W, y: ((1 - v.y) / 2) * H });
      }
      const cx = sx0 < Infinity ? (sx0 + sx1) / 2 : W / 2;
      const dockLeft = q(".rp-dock").offsetLeft - 16;
      for (const side of ["left", "right"]) {
        const colm = rows.filter(rw => (side === "left" ? rw.x < cx : rw.x >= cx)).sort((a, b) => a.y - b.y);
        const ys = spreadLabels(colm.map(rw => rw.y), 48, 80, H - 80);
        colm.forEach((rw, i) => {
          const l = rw.p.label, lw = l.offsetWidth || 170;
          const lx = side === "left" ? Math.max(8, Math.min(sx0, rw.x) - 36 - lw) : Math.min(dockLeft - lw, Math.max(sx1, rw.x) + 36);
          l.style.left = `${Math.round(lx)}px`;
          l.style.top = `${Math.round(ys[i])}px`;
          const ax = side === "left" ? lx + lw : lx;
          rw.p.line.setAttribute("x1", ax); rw.p.line.setAttribute("y1", ys[i] + 10);
          rw.p.line.setAttribute("x2", rw.x); rw.p.line.setAttribute("y2", rw.y);
        });
      }
    }

    function close() { // Done: reassemble in place, then give the room back
      target = 0;
      select(null);
      closing = true;
      if (matchMedia("(prefers-reduced-motion: reduce)").matches || document.hidden) finish();
    }
    function finish() {
      if (closed) return;
      closed = true;
      canvas.removeEventListener("pointermove", onMove);
      canvas.removeEventListener("pointerdown", onDown);
      canvas.removeEventListener("pointerup", onUp);
      canvas.style.cursor = "";
      ui.remove();
      setRoom(false);
      ctx.onStage?.(asset, false);
      document.body.classList.remove("parts-mode");
      root.traverse(n => {
        n.geometry?.dispose();
        for (const m of [].concat(n.material ?? [])) { m.map?.dispose(); m.dispose(); }
      });
      live = null;
      ctx.look.pitch = THREE.MathUtils.clamp(Math.atan2(asset.center[1] - ctx.eye, Math.hypot(dx, dz)), -1.1, 1.1); // back on the device
      ctx.setInspector(inspectorWas);
    }
    const state = () => ({ tag: asset.tag, model: entry.file, devices: devices.map(d => d.tag), explode: +explode.toFixed(2), target, selected, glide: +Math.min(glide, GLIDE_S).toFixed(2),
      pulledBack, focus: +focusK.toFixed(2), parts: pickable.length, faults: faults.map(f => `${f.tag}:${f.code}->${f.node}`),
      labels: pickable.filter(p => !p.label.hidden).map(p => p.label.innerText.replace(/\n/g, " / ")) });
    /** For checks: a part's centre on screen now (client px). */
    const partPoint = node => {
      const p = parts.find(x => x.node === node), rc = canvas.getBoundingClientRect();
      if (!p) return null;
      v.copy(p.anchor).applyMatrix4(p.obj.matrixWorld).project(cam);
      return [rc.left + ((v.x + 1) / 2) * rc.width, rc.top + ((1 - v.y) / 2) * rc.height];
    };
    return { frame, draw, close, state, partPoint, select };
  }

  window.addEventListener("keydown", e => {
    const t = e.key.toLowerCase() === "t" && !e.repeat && !e.altKey && !e.ctrlKey && !e.metaKey && !e.target.closest?.("input, textarea, select");
    if (live && e.key === "Escape") live.close();
    else if (t && (live || mine())) { // T: this Take apart for a unit device, never Wael's on top of it
      e.stopImmediatePropagation();
      if (!live) open(ctx.state.selectedAsset);
    }
  }, { capture: true });

  const api = {
    open, ready,
    get isOpen() { return !!live || opening; },
    /** Every frame before the room is drawn. */
    frame(dt) {
      const p = pending;
      if (p && !live) {
        const route = ctx.state.journey.route;
        if (route?.asset_id !== p.asset_id || ctx.state.journey.phase === "blocked") pending = null; // went elsewhere
        else if (arrivedAt(p)) { pending = null; start(p); }
      }
      syncButton();
      live?.frame(dt);
    },
    /** Every frame after the room is drawn. */
    draw() { live?.draw(); },
    close() { live?.close(); },
    debug: () => (live ? live.state() : { open: false, pending: pending?.tag ?? null, opening }),
    partPoint: node => live?.partPoint(node) ?? null,
  };
  // for checks in the browser (as window.__voiceAssistant): state, open a device by tag, a part's screen point
  window.__roomParts = { state: api.debug, partPoint: api.partPoint, select: node => live?.select(node),
    open: tag => open(ctx.state.roomAssets.find(a => a.tag === tag)), close: api.close };
  return api;
}
