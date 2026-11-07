// AI 3D in the room (scan2service's room view): every Higgsfield model of a scan2service device is drawn where it was
// fitted (raw PLY of the same scan, so no shift), on this viewer's objects whose box centres lie inside it: their scan
// splats are hidden under it and their room labels get an "AI 3D" badge. Matched by POSITION, never by tag (the two
// repos tagged the Excel devices onto the scan independently). A pump + motor unit is one model, drawn once.
// Loaded one by one after the splat; ?ai3d=0 turns it off. The models use their own lit glTF materials under this
// scene's lights. Hidden while Wael's Take apart is open and in the BIM layer; they follow the explode slider and room
// moves of their main object, and leave with it.
// Take apart ported from Mikael Lehtonen's scan2service (web/parts.js) via SinceAI-2026/mvp; UX after Rasmus Mäkinen's
// pump explode. AI 3D after scan2service's room view and the MVP's src/s2s/room.ts.
import * as THREE from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { explodeOffset } from "./explode.mjs";
import { RAW_FLOOR_Y } from "./parts-data.mjs";

const DATA = "./data/take_apart/";

/** ctx: { scene, renderer, state, uniforms(), hudElements, roomAsset(a), removed(id), otherOpen(), ready (index promise) } */
export function createAi3d(ctx) {
  const drawn = new Map(); // model file -> { node, owners (our objects inside it), main (the one it follows) }
  const held = new Set();  // object ids whose model steps aside (the in-room Take apart stands there)
  const root = new THREE.Group();
  root.name = "ai3d";
  ctx.scene.add(root);
  const off = new URLSearchParams(location.search).get("ai3d") === "0";

  function cover(asset, on) { // the object's own scan splats hidden under its model (app.js uCovered)
    const u = ctx.uniforms(), i = ctx.state.twin.assets.findIndex(a => a.id === asset.id) + 1;
    if (!u?.uCovered || i < 1) return;
    u.uCovered.value.image.data[i] = on ? 1 : 0;
    u.uCovered.value.needsUpdate = true;
  }

  async function load(doc) {
    if (off || !doc?.ai3d) return;
    const loader = new GLTFLoader(), aniso = ctx.renderer.capabilities.getMaxAnisotropy(), box = new THREE.Box3(), c = new THREE.Vector3();
    for (const [name, e] of Object.entries(doc.ai3d)) {
      try {
        const model = (await loader.loadAsync(DATA + e.file)).scene;
        model.traverse(n => { if (n.material?.map) n.material.map.anisotropy = aniso; });
        const node = new THREE.Group(), flip = new THREE.Group(); // raw PLY -> this scene (pipeline/ingest.py)
        flip.scale.set(1, -1, -1);
        flip.position.y = RAW_FLOOR_Y;
        flip.add(model);
        node.add(flip);
        node.updateMatrixWorld(true);
        box.setFromObject(node);
        const owners = ctx.state.twin.assets.filter(a => box.containsPoint(c.fromArray(a.center)));
        const main = owners.find(a => a.mapped) ?? owners[0] ?? null;
        node.userData = { name, label: e.label, owners: owners.map(a => a.tag || a.id) };
        root.add(node);
        drawn.set(name, { node, owners, main });
        for (const a of owners) {
          cover(a, true);
          const el = ctx.hudElements.get(a.id);
          if (el) { el.classList.add("ai3d"); el._size = null; }
        }
      } catch (err) {
        console.warn(`AI 3D ${name} not loaded`, err);
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
    for (const { node, owners, main } of drawn.values()) {
      node.visible = !owners.some(a => held.has(a.id)) && !(main && ctx.removed(main.id));
      if (!main) continue;
      const live = ctx.roomAsset(main);
      node.position.set(live.center[0] - main.center[0], live.center[1] - main.center[1], live.center[2] - main.center[2]).add(explodeOffset(live));
    }
  }

  window.__ai3d = { drawn: () => [...drawn.values()].map(d => `${d.node.userData.name}: ${d.node.userData.owners.join(" + ") || "(no object)"}`) }; // checks
  return {
    frame,
    /** The in-room Take apart: the model of the object taken apart steps aside while its parts stand there. */
    hold(asset, on) { if (on) held.add(asset.id); else held.delete(asset.id); },
  };
}
