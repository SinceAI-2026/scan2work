// Take apart: one extracted object alone with the room faded away (after Scan2Service's explode.html). The
// per-splat object ids already on the GPU pick the object, and uApart / uApartRoom / uApartSelf in explode.mjs fade
// the rest. Save to library has the server cut the object out of the scan and refine it against the walkthrough
// frames (pipeline/asset_export.py, pipeline/object_refine.py). A saved object is drawn as that model in place of
// its scan splats. Keys while open: V model or scan, R turntable, G ghost room, Esc back.
// A save runs in the background on the server; the card polls GET /api/library until it is done, so a reload or a
// closed card does not lose it, and reopening the object picks the save up again.
import { loadGspl, createSplatObject } from "./splat-object.mjs";

const LIBRARY = "./data/library/";
const GHOST_ROOM = 0.07;

const POLL_MS = 2000;

async function loadLibrary() {
  try {
    const res = await fetch("./api/library");
    const doc = res.ok ? await res.json() : {};
    return { saved: new Map((doc.saved || []).map(s => [s.id, s])), saving: doc.saving || {} };
  } catch {
    return { saved: new Map(), saving: {} };
  }
}

const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
const plural = (n, word) => `${n.toLocaleString()} ${word}${n === 1 ? "" : "s"}`;

/** ctx: { camera, controls, state, uniforms() -> splat material uniforms, setPreset(name), container, scene, renderer } */
export function createTakeApart(ctx) {
  const card = document.createElement("section");
  card.className = "apart-card";
  card.hidden = true;
  card.setAttribute("aria-label", "Object taken apart");
  ctx.container.appendChild(card);
  const api = { active: null, ghost: false, showScan: false, saved: new Map(), saving: {}, error: "" };
  let restore = null;
  let model = null; // { id, data, obj }: the saved model of the object taken apart
  let token = 0;
  let watching = null; // the object whose save is being polled

  function dropModel() {
    if (model?.obj) {
      ctx.scene.remove(model.obj);
      model.obj.userData.dispose();
      model.obj = null;
    }
  }

  function placeModel() {
    dropModel();
    if (model && api.active?.asset.id === model.id && !api.showScan) {
      const obj = createSplatObject(model.data);
      const transform = api.active.asset.transform; // set when the object was moved in the room
      obj.position.fromArray(transform ? transform.point(...obj.userData.mid) : obj.userData.mid);
      if (transform) obj.rotation.y = -transform.angle;
      ctx.scene.add(obj);
      model.obj = obj;
    }
    setUniforms();
  }

  async function loadModel(asset) {
    const my = ++token;
    dropModel();
    model = null;
    setUniforms();
    ({ saved: api.saved, saving: api.saving } = await loadLibrary());
    if (savingNow(asset)) watchSave(asset);
    const entry = api.saved.get(asset.id);
    if (entry) {
      try {
        const data = await loadGspl(`${LIBRARY}${entry.files.gspl}`);
        if (my !== token || api.active?.asset !== asset) return;
        model = { id: asset.id, data, obj: null };
        placeModel();
      } catch (err) {
        api.error = `Saved model unavailable: ${err.message}`;
      }
    }
    if (my === token && api.active?.asset === asset) render();
  }

  function setUniforms() {
    const u = ctx.uniforms();
    if (!u?.uApart) return;
    u.uApart.value = api.active ? api.active.instance : 0;
    u.uApartRoom.value = api.ghost ? GHOST_ROOM : 0;
    u.uApartSelf.value = model?.obj ? 0 : 1;
  }

  function describe(entry) {
    const m = entry.model;
    if (entry.membership_mode === "exact-source-rows" || api.active?.asset.source_region_id) return `<p class="apart-facts">Exact region export: ${plural(entry.splats, "source Gaussian")}. Original membership retained; no new geometry or photo refinement. Scale and boundaries remain unverified.</p>`;
    if (!m?.splats) return `<p class="apart-facts">In your library: the scan's ${plural(entry.splats, "Gaussian")} (no walkthrough frame saw it clearly enough to refine).</p>`;
    return `<p class="apart-facts">${api.showScan ? "Showing the scan as it is." : "Refined from photos:"} ` +
      `${m.scan_splats.toLocaleString()} scan and ${m.faint_added.toLocaleString()} faint splats fitted to ` +
      `${plural(m.frames.length, "walkthrough frame")} (${m.frames.map(f => `${f.video_t_s} s`).join(", ")}), giving ` +
      `${plural(m.splats, "Gaussian")}. Camera poses corrected by up to ${m.pose_correction_max_mm} mm. No synthetic splats.</p>`;
  }

  function render() {
    const { asset } = api.active;
    const entry = api.saved.get(asset.id);
    const job = savingNow(asset);
    const busy = Boolean(job);
    const files = entry?.files || {};
    const tag = asset.mapped && asset.tag ? `${esc(asset.tag)} · ${esc(asset.asset_id)} · ${esc(asset.condition_state)}` : "No device record in the Excel pack";
    const exact = Boolean(ctx.state.exactMembership || asset.source_region_id || asset.membership_mode === "exact-source-rows");
    const facts = entry ? describe(entry)
      : exact ? `<p class="apart-facts">${busy ? `Exporting ${esc(asset.id)}${job?.seconds ? ` (${job.seconds} s so far)` : ""}. You can close this card; the save carries on.` : `${plural(asset.exact_owned_gaussians ?? asset.n_gaussians ?? 0, "source Gaussian")} · ${esc(asset.review_status || (ctx.state.baseline ? "original room membership" : "unreviewed"))}. Save exports this exact region for your team.`} Original membership is retained; missing geometry is not rebuilt.</p>`
      : busy ? `<p class="apart-facts">Cutting ${esc(asset.id)} out of the scan and fitting it to the walkthrough photos. This takes 1 to 3 minutes${job?.seconds ? ` (${job.seconds} s so far)` : ""}. You can close this card; the save carries on.</p>`
      : `<p class="apart-facts">Showing the scan. Save to library cuts this object out and refines it against the walkthrough photos (1 to 3 minutes).</p>`;
    card.innerHTML = `
      <div class="apart-head">
        <span class="apart-kicker">Isolated region</span>
        <button class="apart-x" data-act="close" aria-label="Back to the room">×</button>
      </div>
      <h3>${esc(asset.label || asset.class_label || asset.id)}</h3>
      <p class="apart-sub">${esc(asset.class_label || String(asset.class || "").replace(/_/g, " "))} · ${esc(asset.id)}</p>
      <p class="apart-sub">${tag}</p>
      ${facts}
      ${api.error ? `<p class="apart-facts warn">${esc(api.error)}</p>` : ""}
      <div class="apart-actions">
        ${entry ? `<span class="apart-save saved">Saved to library ✓</span>`
          : `<button class="apart-save" data-act="save" ${busy ? "disabled" : ""}>${busy ? "Saving…" : "Save to library"}</button>`}
        ${files.ply ? `<a class="apart-btn" href="${LIBRARY}${files.ply}" download>Scan .ply</a>` : ""}
        ${files.source_rows ? `<a class="apart-btn" href="${LIBRARY}${files.source_rows}" download>Source row IDs</a>` : ""}
        ${files.model_ply ? `<a class="apart-btn" href="${LIBRARY}${files.model_ply}" download>Model .ply</a>` : ""}
        ${files.glb ? `<a class="apart-btn" href="${LIBRARY}${files.glb}" download>.glb</a>` : ""}
        ${entry ? `<a class="apart-btn" href="./library.html#${encodeURIComponent(asset.id)}">Open in library</a>` : ""}
      </div>
      <div class="apart-actions">
        ${entry?.model?.splats ? `<button class="apart-btn${api.showScan ? " on" : ""}" data-act="view">Scan only (V)</button>` : ""}
        <button class="apart-btn${ctx.controls.autoRotate ? " on" : ""}" data-act="spin">Turntable (R)</button>
        <button class="apart-btn${api.ghost ? " on" : ""}" data-act="ghost">Ghost room (G)</button>
        <button class="apart-btn" data-act="close">Back (Esc)</button>
      </div>`;
  }

  function savingNow(asset) {
    const job = api.saving[asset.id];
    return job && !job.error ? job : null;
  }

  async function saveActive() {
    const asset = api.active.asset;
    if (savingNow(asset) || api.saved.has(asset.id)) return;
    api.error = "";
    api.saving = { ...api.saving, [asset.id]: { seconds: 0 } };
    render();
    try {
      const res = await fetch("./api/library", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ id: asset.id }),
      });
      const doc = await res.json();
      if (!res.ok) throw new Error(doc.error || `HTTP ${res.status}`);
      api.saving = doc.saving || api.saving;
    } catch (err) {
      api.error = err.message;
      delete api.saving[asset.id];
      if (api.active?.asset === asset) render();
      return;
    }
    watchSave(asset);
  }

  // Poll until the server finishes the save (or reports why it failed), then show the saved model.
  function watchSave(asset) {
    if (watching === asset.id) return;
    watching = asset.id;
    const tick = async () => {
      const lib = await loadLibrary();
      api.saved = lib.saved;
      api.saving = lib.saving;
      const job = lib.saving[asset.id];
      if (job && !job.error) {
        if (api.active?.asset === asset) render();
        setTimeout(tick, POLL_MS);
        return;
      }
      watching = null;
      if (job?.error) api.error = `Save failed: ${job.error}`;
      if (api.active?.asset === asset) await loadModel(asset);
    };
    setTimeout(tick, POLL_MS);
  }

  const toggle = act => {
    if (act === "view") { api.showScan = !api.showScan; placeModel(); }
    else if (act === "spin") ctx.controls.autoRotate = !ctx.controls.autoRotate;
    else if (act === "ghost") { api.ghost = !api.ghost; setUniforms(); }
    else return false;
    render();
    return true;
  };

  card.addEventListener("click", e => {
    const act = e.target.closest("[data-act]")?.dataset.act;
    if (act === "close") close();
    else if (act === "save") saveActive();
    else toggle(act);
  });

  window.addEventListener("keydown", e => {
    if (!api.active || e.altKey || e.ctrlKey || e.metaKey || e.target.closest?.("input, textarea, select")) return;
    const k = e.key.toLowerCase();
    if (k === "escape") close();
    else if (!toggle({ v: "view", r: "spin", g: "ghost" }[k])) return;
    e.preventDefault();
  });

  function frameCamera(asset) {
    const c = asset.center;
    const r = Math.max(0.12, Math.hypot(...asset.size) / 2);
    const cam = ctx.camera;
    const fit = Math.min(cam.fov / 2, (Math.atan(Math.tan((cam.fov / 2) * Math.PI / 180) * cam.aspect) * 180) / Math.PI);
    const dist = (r / Math.sin((fit * Math.PI) / 180)) * 1.1;
    let dx = cam.position.x - c[0], dz = cam.position.z - c[2];
    const h = Math.hypot(dx, dz) || 1;
    dx /= h; dz /= h;
    const pitch = 0.42;
    cam.position.set(c[0] + dx * dist * Math.cos(pitch), c[1] + dist * Math.sin(pitch), c[2] + dz * dist * Math.cos(pitch));
    ctx.controls.target.set(c[0], c[1], c[2]);
    ctx.controls.update();
  }

  function open(asset) {
    if (!asset) return;
    const index = ctx.state.twin.assets.findIndex(original => original.id === asset.id);
    if (index < 0) return;
    ctx.beforeOpen?.();
    if (!api.active) {
      const c = ctx.controls;
      restore = {
        preset: ctx.state.cameraPreset, position: ctx.camera.position.clone(), target: c.target.clone(),
        layer:ctx.state.viewLayer,ceilingCut:ctx.state.ceilingCut,
        minDistance: c.minDistance, maxDistance: c.maxDistance, minPolar: c.minPolarAngle, maxPolar: c.maxPolarAngle,
      };
      ctx.setPreset("apart");
      Object.assign(c, { enabled: true, minDistance: 0.05, maxDistance: 8, minPolarAngle: 0.05,
        maxPolarAngle: Math.PI * 0.95, autoRotate: false, autoRotateSpeed: 1.6 });
      document.body.classList.add("apart-mode");
    }
    const changed = api.active?.asset !== asset;
    api.active = { asset, instance: index + 1 };
    api.error = "";
    frameCamera(asset);
    setUniforms();
    card.hidden = false;
    render();
    if (changed) loadModel(asset);
  }

  function close() {
    if (!api.active) return;
    api.active = null;
    token++;
    dropModel();
    model = null;
    setUniforms();
    card.hidden = true;
    document.body.classList.remove("apart-mode");
    const c = ctx.controls;
    c.autoRotate = false;
    Object.assign(c, { minDistance: restore.minDistance, maxDistance: restore.maxDistance,
      minPolarAngle: restore.minPolar, maxPolarAngle: restore.maxPolar });
    ctx.setPreset(restore.preset === "apart" ? "aerial" : restore.preset);
    if(restore.layer)ctx.setLayer?.(restore.layer);
    ctx.setCeilingCut?.(restore.ceilingCut);
    ctx.camera.position.copy(restore.position);
    c.target.copy(restore.target);
    c.update();
  }

  /** Every frame, after app.js styles the frames: none while an object is taken apart. */
  function frame(boxMeshes) {
    if (!api.active) return;
    for (const entry of boxMeshes.values()) entry.group.visible = false;
    model?.obj?.userData.sort(ctx.camera, ctx.renderer);
  }

  Object.defineProperty(api, "isOpen", { get: () => !!api.active }); // a live getter: Object.assign would copy the value
  return Object.assign(api, { open, close, frame });
}
