// AI 3D in the room (scan2service's room view): every Excel device whose scan2service card shows a Higgsfield model gets
// that model drawn in place, moved onto this viewer's object for its tag, its own scan splats hidden under it, and an
// "AI 3D" badge on its label. A pump + motor unit is drawn once, at the pump; P-001 has a stand-in copy of the P-005
// unit (scripts/import_take_apart.py decides both). Loaded one by one after the splat; ?ai3d=0 turns it off.
// The models use their own lit glTF materials under this scene's lights. Hidden while Wael's Take apart is open and in
// the BIM layer; they follow the explode slider and room moves, and leave with a removed object.
// Take apart ported from Mikael Lehtonen's scan2service (web/parts.js) via SinceAI-2026/mvp; UX after Rasmus Mäkinen's
// pump explode. AI 3D after scan2service's room view and the MVP's src/s2s/room.ts.
import * as THREE from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { explodeOffset } from "./explode.mjs";
import { shiftFor, RAW_FLOOR_Y } from "./parts-data.mjs";

const DATA = "./data/take_apart/";

/** ctx: { scene, renderer, state, uniforms(), hudElements, roomAsset(a), removed(id), otherOpen(), ready (index promise) } */
export function createAi3d(ctx) {
  const drawn = new Map(); // asset id -> { asset, node }
  const held = new Set();  // asset ids whose model steps aside (the in-room Take apart stands there)
  const root = new THREE.Group();
  root.name = "ai3d";
  ctx.scene.add(root);
  const off = new URLSearchParams(location.search).get("ai3d") === "0";

  function cover(asset, on) { // the device's own scan splats hidden under its model (app.js uCovered)
    const u = ctx.uniforms(), i = ctx.state.twin.assets.findIndex(a => a.id === asset.id) + 1;
    if (!u?.uCovered || i < 1) return;
    u.uCovered.value.image.data[i] = on ? 1 : 0;
    u.uCovered.value.needsUpdate = true;
  }

  async function load(doc) {
    if (off || !doc?.ai3d) return;
    const loader = new GLTFLoader(), aniso = ctx.renderer.capabilities.getMaxAnisotropy();
    for (const [tag, e] of Object.entries(doc.ai3d)) {
      const asset = ctx.state.twin.assets.find(a => a.tag === tag);
      if (!asset) continue;
      try {
        const model = (await loader.loadAsync(DATA + e.file)).scene;
        model.traverse(n => { if (n.material?.map) n.material.map.anisotropy = aniso; });
        // raw PLY -> this scene (pipeline/ingest.py), the model's centre on our box centre
        const node = new THREE.Group(), flip = new THREE.Group(), shift = new THREE.Group();
        flip.scale.set(1, -1, -1);
        flip.position.y = RAW_FLOOR_Y;
        model.updateMatrixWorld(true);
        shift.position.fromArray(shiftFor(asset, new THREE.Box3().setFromObject(model).getCenter(new THREE.Vector3()).toArray()));
        shift.add(model);
        flip.add(shift);
        node.add(flip);
        node.userData = { tag, how: e.how, label: e.label };
        root.add(node);
        drawn.set(asset.id, { asset, node });
        cover(asset, true);
        const el = ctx.hudElements.get(asset.id);
        if (el) { el.classList.add("ai3d"); el._size = null; }
      } catch (err) {
        console.warn(`AI 3D for ${tag} not loaded`, err);
      }
      await new Promise(r => setTimeout(r, 250)); // one at a time, the room keeps drawing in between
    }
  }
  ctx.ready.then(load);

  /** Every frame before the room is drawn. */
  function frame() {
    const show = !ctx.otherOpen() && ctx.state.viewLayer !== "bim";
    root.visible = show;
    const u = ctx.uniforms();
    if (u?.uHasCovered) u.uHasCovered.value = show && drawn.size ? 1 : 0;
    for (const { asset, node } of drawn.values()) {
      const live = ctx.roomAsset(asset), gone = ctx.removed(asset.id);
      node.visible = !gone && !held.has(asset.id);
      node.position.set(live.center[0] - asset.center[0], live.center[1] - asset.center[1], live.center[2] - asset.center[2]).add(explodeOffset(live));
    }
  }

  window.__ai3d = { // checks: what is drawn, and how far each model's centre is from its object's (m)
    drawn: () => [...drawn.values()].map(({ asset, node }) => `${node.userData.tag}${node.userData.how === "standin" ? "*" : ""}:${asset.id}`),
    offsets: () => [...drawn.values()].map(({ asset, node }) => [node.userData.tag,
      +new THREE.Box3().setFromObject(node).getCenter(new THREE.Vector3()).distanceTo(new THREE.Vector3(...ctx.roomAsset(asset).center)).toFixed(3)]),
  };
  return {
    frame,
    /** The in-room Take apart: the device's model steps aside while its parts stand there. */
    hold(asset, on) { if (on) held.add(asset.id); else held.delete(asset.id); },
    has: id => drawn.has(id),
  };
}
