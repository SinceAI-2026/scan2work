// Field Atlas asset library: the Equipment Room objects saved from the Take-apart view, each cut out of the scan
// and refined against the walkthrough frames (pipeline/asset_export.py) into web/data/library/<id>/. Layout after
// linards-project's library. #<object id> opens that object.
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { loadGspl, createSplatObject } from "./splat-object.mjs";

const LIB = "./data/library/";
let TARGET_DEMO = "OBJ-206"; // CV-003, the faulty control valve of the Equipment Room scenario
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
const plural = (n, w) => `${n.toLocaleString()} ${w}${n === 1 ? "" : "s"}`;
const words = s => String(s ?? "").replace(/_/g, " ");
const pretty = s => { const w = words(s).trim(); return w.charAt(0).toUpperCase() + w.slice(1); };
const className = e => e.class_label || pretty(e.class || "object");
const stateOf = e => (e.movable ? "movable" : e.condition_state || "not_instrumented");

function classColor(name) {
  let h = 0;
  for (const ch of name) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  return `hsl(${Math.round(((h * 0.618033988749895) % 1) * 360)} 55% 45%)`;
}

let saved = [], focusId = null, filterClass = null, showScan = false;

async function load() {
  try {
    const res = await fetch("./api/library");
    saved = res.ok ? (await res.json()).saved : [];
  } catch {
    saved = [];
  }
  saved.sort((a, b) => b.saved_at.localeCompare(a.saved_at));
}

async function main() {
  try { const twin = await fetch("./data/digital_twin.json").then(r => r.json()); TARGET_DEMO = twin.target_object_id || TARGET_DEMO; } catch {}
  await load();
  render();
  document.getElementById("q").addEventListener("input", filter);
  document.getElementById("classes").addEventListener("click", e => {
    const b = e.target.closest("button[data-class]");
    if (!b) return;
    filterClass = b.dataset.class || null;
    render();
  });
  window.addEventListener("hashchange", () => focus(decodeURIComponent(location.hash.slice(1))));
  focus(decodeURIComponent(location.hash.slice(1)));
}

function render() {
  const total = saved.reduce((s, e) => s + e.splats, 0);
  document.getElementById("stats").textContent = saved.length
    ? `${plural(saved.length, "object")} · ${total.toLocaleString()} Gaussians` : "Empty";
  document.getElementById("zip").hidden = !saved.length;
  const byClass = new Map();
  for (const e of saved) byClass.set(className(e), (byClass.get(className(e)) || 0) + 1);
  if (filterClass && !byClass.has(filterClass)) filterClass = null;
  document.getElementById("classes").innerHTML =
    `<button data-class="" class="${filterClass ? "" : "selected"}">All objects<span class="n">${saved.length}</span></button>` +
    [...byClass].sort((a, b) => b[1] - a[1]).map(([c, n]) =>
      `<button data-class="${esc(c)}" class="${filterClass === c ? "selected" : ""}"><span class="chip" style="background:${classColor(c)}"></span>${esc(c)}<span class="n">${n}</span></button>`).join("");
  const list = saved.filter(e => !filterClass || className(e) === filterClass);
  document.getElementById("stage").hidden = !saved.length;
  document.getElementById("instances").innerHTML = saved.length ? list.map(card).join("") : `
    <div id="empty">
      <h3>Your library is empty</h3>
      <p>Objects get here from the room: select equipment, choose <b>Take apart</b> (or press <b>T</b>), then
      <b>Save to library</b>. The server exports its source Gaussians. Imported regions retain exact membership; missing geometry is not generated.</p>
      <a class="btn primary" href="./index.html#apart=${TARGET_DEMO}">Take apart the faulty valve CV-003</a>
    </div>`;
  filter();
  if (!saved.some(e => e.id === focusId)) focusId = null;
  focus(focusId || list[0]?.id || saved[0]?.id);
}

function cardText(e) {
  return [e.id, e.tag, e.asset_id, e.label, e.class, className(e), stateOf(e), e.issue_code].filter(Boolean).join(" ").toLowerCase();
}

function filter() {
  const s = document.getElementById("q").value.trim().toLowerCase();
  for (const c of document.querySelectorAll("#instances .card")) c.style.display = !s || c.dataset.text.includes(s) ? "" : "none";
}

function status(e) {
  if (e.movable) return "movable object · no telemetry";
  if (e.source_region_id || e.condition_state === "unknown") return "unreviewed region · scale unverified";
  if (!e.tag) return "facility item · no device record";
  return `health ${e.health_score ?? "–"} · ${words(e.condition_state)}${e.issue_code && e.issue_code !== "NORMAL" ? ` · ${words(e.issue_code)}` : ""}`;
}

function card(e) {
  const photo = typeof e.photo === "string" ? e.photo : e.photo?.url;
  return `<div class="card" data-id="${esc(e.id)}" data-text="${esc(cardText(e))}">
    <img src="${LIB}${e.files.thumb}" alt="" loading="lazy" ${photo ? `data-photo="./${esc(photo)}"` : ""}>
    <div class="body">
      <div class="name"><span class="dot ${stateOf(e)}"></span>${esc(e.tag || e.label || className(e))}${e.tag ? ' <span class="badge">SYNTHETIC</span>' : ""}</div>
      <div class="sub">${esc(e.label || className(e))}</div>
      <div class="sub mono">${esc(e.id)} · ${e.splats.toLocaleString()} G</div>
      <div class="status">${esc(status(e))}</div>
    </div></div>`;
}

function focus(id) {
  const e = saved.find(x => x.id === id);
  document.querySelector(".card.focus")?.classList.remove("focus");
  if (!e) {
    if (!saved.length) model.clear();
    return;
  }
  const el = document.querySelector(`.card[data-id="${CSS.escape(e.id)}"]`);
  el?.classList.add("focus");
  if (id === decodeURIComponent(location.hash.slice(1))) el?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  if (e.id !== focusId || !model.showing(e.id)) model.show(e);
  focusId = e.id;
  const size = e.bounds.max.map((v, k) => (v - e.bounds.min[k]).toFixed(2)).join(" × ");
  document.getElementById("focus").innerHTML = `
    <h3>${esc(e.label || className(e))}</h3>
    <div class="sub">${esc(className(e))} · ${esc(e.id)}${e.is_target ? " · faulty device" : ""}</div>
    <dl>
      ${e.tag ? `<dt>Tag</dt><dd>${esc(e.tag)} · ${esc(e.asset_id)}</dd>` : ""}
      <dt>Status</dt><dd>${esc(status(e))}</dd>
      <dt>Gaussians</dt><dd>${e.splats.toLocaleString()}</dd>
      <dt>Size</dt><dd>${size} ${e.membership_mode === "exact-source-rows" || e.source_region_id ? "scan units (unverified)" : "m"}</dd>
      <dt>Position</dt><dd>${e.centroid.map(v => v.toFixed(2)).join(", ")}</dd>
      ${e.mesh_faces ? `<dt>Mesh</dt><dd>${e.mesh_faces.toLocaleString()} faces</dd>` : ""}
      ${e.model?.splats ? `<dt>Refined</dt><dd>${plural(e.model.splats, "Gaussian")} fitted to ${plural(e.model.frames.length, "frame")} (${e.model.frames.map(f => `${f.video_t_s} s`).join(", ")})</dd>` : ""}
      <dt>Cut from</dt><dd>${esc(e.source || "Equipment Room.ply")}</dd>
      <dt>Saved</dt><dd>${esc(e.saved_at.replace("T", " "))}</dd>
    </dl>
    <div class="row">
      ${e.model?.splats ? `<button class="btn ${showScan ? "on" : ""}" data-scan>Scan only</button>` : ""}
      <a class="btn" href="${LIB}${e.files.ply}" download>Scan .ply</a>
      ${e.files.source_rows ? `<a class="btn" href="${LIB}${e.files.source_rows}" download>Source row IDs</a>` : ""}
      ${e.files.model_ply ? `<a class="btn" href="${LIB}${e.files.model_ply}" download>Model .ply</a>` : ""}
      ${e.files.glb ? `<a class="btn" href="${LIB}${e.files.glb}" download>.glb</a>` : ""}
      <a class="btn" href="./index.html#apart=${encodeURIComponent(e.id)}">In the room</a>
      <button class="btn danger" data-remove="${esc(e.id)}">Remove</button>
    </div>`;
}

async function remove(id) {
  const e = saved.find(x => x.id === id);
  if (!confirm(`Remove ${e?.tag || e?.label || id} from the library? Its saved files are deleted; you can save it again from the room.`)) return;
  const res = await fetch(`./api/library/${encodeURIComponent(id)}`, { method: "DELETE" });
  if (!res.ok) return alert(`Could not remove ${id}: HTTP ${res.status}`);
  saved = (await res.json()).saved.sort((a, b) => b.saved_at.localeCompare(a.saved_at));
  if (focusId === id) focusId = null;
  render();
}

document.addEventListener("click", e => {
  if (e.target.closest("[data-scan]")) {
    showScan = !showScan;
    const f = saved.find(x => x.id === focusId);
    if (f) { model.show(f); focus(f.id); }
    return;
  }
  const r = e.target.closest("[data-remove]");
  if (r) return remove(r.dataset.remove);
  const c = e.target.closest(".card");
  if (c) focus(c.dataset.id);
});
// Hovering a card shows the real walkthrough frame of the object, where there is one.
document.addEventListener("mouseover", e => {
  const img = e.target.closest?.(".card")?.querySelector("img[data-photo]");
  if (img && !img.dataset.thumb) { img.dataset.thumb = img.src; img.src = img.dataset.photo; }
});
document.addEventListener("mouseout", e => {
  const card = e.target.closest?.(".card");
  if (!card || card.contains(e.relatedTarget)) return;
  const img = card.querySelector("img[data-thumb]");
  if (img) { img.src = img.dataset.thumb; delete img.dataset.thumb; }
});

/** The focused object's model (or its scan splats) on a turntable. Drag to orbit. */
const model = (() => {
  const host = document.getElementById("model");
  const statusEl = document.getElementById("model-status");
  const renderer = new THREE.WebGLRenderer({ antialias: false });
  renderer.setPixelRatio(Math.min(2, devicePixelRatio));
  renderer.setClearColor(0x121615, 1);
  host.prepend(renderer.domElement);
  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(40, 1, 0.01, 50);
  const controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true;
  controls.autoRotate = true;
  controls.autoRotateSpeed = 2.2;
  let obj = null, token = 0, shownId = null;
  const resize = () => {
    const w = host.clientWidth, h = host.clientHeight;
    if (!w || !h) return;
    renderer.setSize(w, h, false);
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
  };
  new ResizeObserver(resize).observe(host);
  resize();
  renderer.setAnimationLoop(() => {
    controls.update();
    if (obj) obj.userData.sort(camera, renderer);
    renderer.render(scene, camera);
  });
  const clear = () => {
    token++;
    shownId = null;
    if (obj) { scene.remove(obj); obj.userData.dispose(); obj = null; }
    statusEl.textContent = "";
  };
  return {
    clear,
    showing: id => shownId === `${id}:${showScan}`,
    async show(e) {
      const my = ++token;
      shownId = `${e.id}:${showScan}`;
      statusEl.textContent = `Loading ${e.id}…`;
      try {
        const data = await loadGspl(`${LIB}${showScan ? e.files.scan_gspl : e.files.gspl}`);
        if (my !== token) return;
        if (obj) { scene.remove(obj); obj.userData.dispose(); }
        obj = createSplatObject(data);
        scene.add(obj);
        const r = Math.max(0.08, obj.userData.radius);
        const d = (r / Math.sin((camera.fov / 2) * Math.PI / 180)) * 1.05;
        camera.position.set(d * 0.7, d * 0.45, d * 0.7);
        controls.target.set(0, 0, 0);
        controls.update();
        statusEl.textContent = `${e.id} · ${showScan || e.membership_mode === "exact-source-rows" ? "source region" : "refined model"} · ${plural(data.count, "Gaussian")} · drag to orbit`;
      } catch (err) {
        if (my === token) statusEl.textContent = `Could not load ${e.id}: ${err.message}`;
      }
    },
  };
})();

main().catch(err => {
  document.getElementById("instances").innerHTML = `<div id="empty">Error: ${esc(err.message)}</div>`;
});
