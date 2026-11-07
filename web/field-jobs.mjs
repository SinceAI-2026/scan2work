// The technician's jobs on screen: the left sidebar gets two tabs, "To inspect" (the inspection queue: what still
// needs inspection, worst first, and what is done) and "All equipment" (the viewer's own search, filter and list,
// moved in unchanged, so app.js and showEquipment keep working on the same elements); and a job card and a job card in Equipment details (checklist, notes, a note field and Mark done). The voice assistant writes
// the same log through mcp_server.py, so this polls /api/field-jobs and shows its notes and ticks as they land.
// showEquipment(tag) selects the equipment and moves the camera to it; the assistant calls it as a client tool.
// It drives the viewer through its own controls (the list cards and View location), so app.js needs no changes.

import { canWriteFieldJobs, installAction } from "./runtime-access.mjs";

const POLL_MS = 3000;
const STATE_TEXT = { attention_required: "Urgent", degraded: "Degraded", watch: "Watch" };
const STATE_HELP = {
  attention_required: "Needs attention now",
  degraded: "Not working as it should; inspect soon",
  watch: "Readings drifting; check when nearby",
};
const QUEUE_PREVIEW = 6; // rows shown before "Show all"
const TAB_KEY = "field-atlas-sidebar-tab";
let showAll = false;
let data = { needs_inspection: [], done: [], jobs: {} };
let lastJson = "";
let currentTag = () => "none";
let runtime;

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

async function api(path, body) {
  if (body && !canWriteFieldJobs(runtime)) {
    const error = new Error("Install Scan2Work to save your own inspection jobs.");
    error.code = "public_demo_read_only";
    throw error;
  }
  const res = body
    ? await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) })
    : await fetch(path);
  const out = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(out.error || `HTTP ${res.status}`);
  return out;
}

// ------------------------------------------------------------------ show equipment on screen
export async function showEquipment(equipment) {
  const { tag, object_id: obj, label, area } = await api(`/api/field-jobs/find?equipment=${encodeURIComponent(equipment)}`);
  const search = document.getElementById("asset-search");
  const filter = document.getElementById("select-visual-review");
  const card = () => document.querySelector(`#asset-list .proposal-card[data-obj="${CSS.escape(obj)}"]`);
  const keep = { q: search.value, f: filter.value };
  if (!card()) { search.value = ""; search.dispatchEvent(new Event("input")); }
  if (!card()) { filter.value = "all"; filter.dispatchEvent(new Event("change")); }
  if (!card()) { search.value = obj; search.dispatchEvent(new Event("input")); }
  if (!card()) throw new Error(`${tag} is not in the room list (removed from the room?)`);
  card().click();
  document.getElementById("btn-room-location").click();
  if (search.value !== keep.q) { search.value = keep.q; search.dispatchEvent(new Event("input")); }
  if (filter.value !== keep.f) { filter.value = keep.f; filter.dispatchEvent(new Event("change")); }
  flash(`Showing ${tag}`);
  return { tag, label, area };
}

function flash(text) {
  let el = document.getElementById("fj-flash");
  if (!el) {
    el = Object.assign(document.createElement("div"), { id: "fj-flash", className: "fj-flash", role: "status" });
    document.querySelector(".viewport-col")?.appendChild(el);
  }
  el.textContent = text;
  el.classList.add("on");
  clearTimeout(flash.t);
  flash.t = setTimeout(() => el.classList.remove("on"), 2600);
}

// ------------------------------------------------------------------ the inspection queue (left sidebar)
function progress(job) {
  if (!job) return "Not started";
  const done = job.checks.filter((c) => c.done).length;
  if (job.status === "in_progress") return `In progress · ${done} of ${job.checks.length} steps done`;
  return { fixed: "Fixed", not_fixed: "Closed, not fixed", escalated: "Escalated" }[job.status] || job.status;
}

function row(r) {
  const job = r.job;
  const total = job?.checks.length || 0;
  const done = job ? job.checks.filter((c) => c.done).length : 0;
  const notes = job?.notes?.length || 0;
  const fixed = job?.status === "fixed";
  const selected = r.tag === currentTag();
  return `<li><button type="button" class="iq-row${selected ? " selected" : ""}" data-tag="${esc(r.tag)}"
      title="${esc(fixed ? "Fixed" : STATE_HELP[r.state] || r.state)}. Click to see it in the room." ${selected ? 'aria-current="true"' : ""}>
    <span class="iq-name"><span class="iq-tag">${esc(r.tag)}</span><span class="iq-label">${esc(r.label || r.type)}</span></span>
    <span class="iq-meta"><span class="iq-state ${fixed ? "fixed" : esc(r.state)}">${fixed ? "✓ Fixed" : esc(STATE_TEXT[r.state] || r.state)}</span>
      ${job ? `<span class="iq-progress">${esc(progress(job))}${notes ? ` · ${notes} note${notes > 1 ? "s" : ""}` : ""}</span>` : ""}</span>
    ${job?.status === "in_progress" && total ? `<span class="iq-bar" aria-hidden="true"><i style="width:${Math.round((100 * done) / total)}%"></i></span>` : ""}
  </button></li>`;
}

function renderQueue(root) {
  const todo = data.needs_inspection;
  const shown = showAll ? todo : todo.slice(0, QUEUE_PREVIEW);
  document.querySelectorAll(".iq-count").forEach((el) => { el.textContent = todo.length; });
  root.querySelector(".iq-list").innerHTML = shown.map(row).join("") || `<li class="iq-empty">Nothing left to inspect. Well done.</li>`;
  const more = root.querySelector(".iq-more");
  more.hidden = todo.length <= QUEUE_PREVIEW;
  more.textContent = showAll ? "Show fewer" : `Show all ${todo.length}`;
  root.querySelector(".iq-done-count").textContent = data.done.length;
  root.querySelector(".iq-done-list").innerHTML = data.done.map(row).join("") || `<li class="iq-empty">Jobs closed as fixed appear here.</li>`;
}

// ------------------------------------------------------------------ the job card (Equipment details)
function renderJob(root) {
  const tag = currentTag();
  if (root.dataset.equipment !== tag) {
    root.querySelector("#fj-text").value = "";
    root.querySelector(".fj-error").textContent = "";
    root.dataset.equipment = tag;
  }
  root.hidden = tag === "none";
  if (root.hidden) return;
  const readOnly = !canWriteFieldJobs(runtime);
  root.querySelector(".fj-install").hidden = !readOnly;
  if (readOnly) {
    root.querySelector(".fj-status").textContent = "";
    for (const el of root.querySelectorAll(".fj-start,.fj-checks,.fj-notes,.fj-sub,.fj-form")) el.hidden = true;
    return;
  }
  const job = data.jobs[tag];
  const status = root.querySelector(".fj-status");
  status.textContent = job ? progress(job) : "No job yet";
  status.className = `fj-status ${job?.status || "none"}`;
  root.querySelector(".fj-start").hidden = Boolean(job);
  root.querySelector(".fj-checks").innerHTML = (job?.checks || []).map((c) => `
    <li class="${c.done ? "done" : ""}"><label>
      <input type="checkbox" data-check="${esc(c.id)}" ${c.done ? "checked disabled" : ""} ${job.status !== "in_progress" ? "disabled" : ""}>
      <span><b>${esc(c.id)}. ${esc(c.title)}</b>${c.safety ? ' <em class="fj-safety">safety</em>' : ""}
      ${c.result ? `<small>${esc(c.result)}${c.by ? ` (${esc(c.by)})` : ""}</small>` : ""}</span>
    </label></li>`).join("");
  root.querySelector(".fj-notes").innerHTML = (job?.notes || []).slice().reverse().map((n) => `
    <li class="${esc(n.kind)}"><div class="fj-note-meta">${esc(n.kind)} · ${esc(n.by || "technician")} · ${esc(n.at)}</div>${esc(n.text)}</li>`).join("")
    || `<li class="fj-empty">No notes yet.</li>`;
  const open = job?.checks.filter((c) => !c.done).length ?? 1;
  const done = root.querySelector(".fj-mark-done");
  done.hidden = job?.status === "fixed";
  done.disabled = !job || job.status !== "in_progress" || open > 0;
  done.title = !job ? "Start the job first" : open > 0 ? `Confirm the ${open} remaining step${open > 1 ? "s" : ""} first` : "Close the job as fixed";
}

async function act(root, body) {
  const err = root.querySelector(".fj-error");
  err.textContent = "";
  try {
    apply(await api("/api/field-jobs", body));
    return true;
  } catch (e) {
    err.textContent = e.message;
    return false;
  }
}

function apply(next) {
  data = next;
  lastJson = JSON.stringify(next);
  renderQueue(document.getElementById("inspection-queue"));
  renderJob(document.getElementById("field-job"));
}

function mountQueue() {
  const sidebar = document.querySelector(".left-sidebar");
  if (!sidebar) return;
  const tabs = document.createElement("div");
  tabs.className = "side-tabs";
  tabs.setAttribute("role", "tablist");
  tabs.setAttribute("aria-label", "Equipment lists");
  tabs.innerHTML = `<button type="button" role="tab" id="tab-inspect" aria-controls="inspection-queue" data-tab="inspect">To inspect <span class="iq-count tab-count"></span></button>
    <button type="button" role="tab" id="tab-equipment" aria-controls="equipment-panel" data-tab="equipment">All equipment</button>`;

  const root = document.createElement("section");
  root.id = "inspection-queue";
  root.className = "inspection-queue side-panel";
  root.setAttribute("role", "tabpanel");
  root.setAttribute("aria-labelledby", "tab-inspect");
  root.innerHTML = `<ol class="iq-list"></ol>
    <button type="button" class="iq-more" hidden></button>
    <details class="iq-done"><summary>Done <span class="iq-done-count"></span></summary><ol class="iq-done-list"></ol></details>`;
  root.addEventListener("click", (e) => {
    if (e.target.closest(".iq-more")) { showAll = !showAll; renderQueue(root); return; }
    const b = e.target.closest(".iq-row");
    if (b) showEquipment(b.dataset.tag).catch((err) => flash(err.message));
  });

  // Search stays visible above both tabs; the original filter and list keep their handlers.
  const panel = document.createElement("section");
  panel.id = "equipment-panel";
  panel.className = "equipment-panel side-panel";
  panel.setAttribute("role", "tabpanel");
  panel.setAttribute("aria-labelledby", "tab-equipment");
  for (const el of [document.getElementById("select-visual-review"),
    document.getElementById("asset-result-count"), document.getElementById("asset-list")]) if (el) panel.appendChild(el);

  const anchor = sidebar.querySelector(".filter-group") || sidebar.querySelector(".mission-box");
  anchor ? anchor.after(tabs, root, panel) : sidebar.append(tabs, root, panel);

  const select = (name) => {
    for (const b of tabs.querySelectorAll("[role=tab]")) {
      const on = b.dataset.tab === name;
      b.setAttribute("aria-selected", String(on));
      b.tabIndex = on ? 0 : -1;
    }
    root.hidden = name !== "inspect";
    panel.hidden = name !== "equipment";
    try { localStorage.setItem(TAB_KEY, name); } catch { /* private window */ }
  };
  tabs.addEventListener("click", (e) => { const b = e.target.closest("[role=tab]"); if (b) select(b.dataset.tab); });
  tabs.addEventListener("keydown", (e) => {
    if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
    const next = tabs.querySelector('[role=tab][aria-selected="false"]');
    select(next.dataset.tab);
    next.focus();
  });
  let saved = "inspect";
  try { saved = localStorage.getItem(TAB_KEY) || saved; } catch { /* private window */ }
  select(saved === "equipment" ? "equipment" : "inspect");
  const search=document.getElementById("asset-search"),filter=document.getElementById("select-visual-review");
  let previousFilter=null;
  search.addEventListener("input",()=>{
    if(search.value.trim()) {
      if(previousFilter===null){previousFilter=filter.value;filter.value="all";filter.dispatchEvent(new Event("change"));}
      select("equipment");
    } else if(previousFilter!==null) {
      filter.value=previousFilter;previousFilter=null;filter.dispatchEvent(new Event("change"));
    }
  },true);
}

function mountJob() {
  const root = document.createElement("div");
  root.id = "field-job";
  root.className = "section-block field-job";
  root.hidden = true;
  root.innerHTML = `<div class="sec-title-row"><h3 class="sec-title">Technician job</h3><span class="fj-status"></span></div>
    <p class="fj-install demo-install-note" hidden>${installAction(runtime)} to record your own inspection checklist and notes.</p>
    <button type="button" class="fj-btn primary fj-start">Start inspection checklist</button>
    <ol class="fj-checks"></ol>
    <h4 class="fj-sub">Technician notes</h4><ul class="fj-notes"></ul>
    <form class="fj-form">
      <label class="sr-only" for="fj-text">Note</label>
      <textarea id="fj-text" rows="2" placeholder="Add a note: what you saw, measured or fixed…"></textarea>
      <div class="fj-row">
        <select class="fj-kind" aria-label="Note type"><option>observation</option><option>measurement</option><option>fix</option><option>escalation</option></select>
        <button type="submit" class="fj-btn">Add note</button>
        <button type="button" class="fj-btn primary fj-mark-done">Mark done</button>
      </div>
      <p class="fj-error" role="status"></p>
    </form>`;
  root.querySelector(".fj-start").addEventListener("click", async e => {
    const button = e.currentTarget;
    button.disabled = true;
    try { await act(root, { action: "start_job", equipment: currentTag() }); }
    finally { button.disabled = false; }
  });
  root.addEventListener("change", async (e) => {
    const id = e.target.dataset?.check;
    if (!id) return;
    e.target.disabled = true;
    const ok = await act(root, { action: "complete_check", equipment: currentTag(), check_id: id, result: "Confirmed in the viewer" });
    if (!ok) { e.target.checked = false; e.target.disabled = false; }
  });
  root.querySelector(".fj-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const text = root.querySelector("#fj-text");
    if (!text.value.trim()) return;
    const tag = currentTag();
    if (!data.jobs[tag] && !(await act(root, { action: "start_job", equipment: tag }))) return;
    if (await act(root, { action: "add_note", equipment: tag, text: text.value, kind: root.querySelector(".fj-kind").value })) text.value = "";
  });
  root.querySelector(".fj-mark-done").addEventListener("click", async () => {
    const tag = currentTag();
    const text = root.querySelector("#fj-text");
    const lastFix = data.jobs[tag]?.notes.filter((n) => n.kind === "fix").pop()?.text;
    if (await act(root, { action: "close_job", equipment: tag, outcome: "fixed", summary: text.value.trim() || lastFix || "Marked done in the viewer." })) {
      text.value = "";
      flash(`${tag} marked done`);
    }
  });
  const anchor = document.querySelector("#active-asset-details .action-bar-inline");
  anchor ? anchor.after(root) : document.getElementById("active-asset-details")?.appendChild(root);
  const title = document.getElementById("detail-title");
  if (title) new MutationObserver(() => {
    renderJob(root);
    const queue = document.getElementById("inspection-queue");
    if (queue) renderQueue(queue); // highlight the selected equipment
  }).observe(title, { childList: true, characterData: true, subtree: true });
}

async function poll() {
  try {
    const next = await api("/api/field-jobs");
    if (JSON.stringify(next) !== lastJson) apply(next);
  } catch (e) {
    /* server restarting: keep the last view */
  }
}

export function mountFieldJobs(selectedTag, runtimeConfig) {
  currentTag = selectedTag;
  runtime = runtimeConfig;
  mountQueue();
  mountJob();
  poll();
  setInterval(poll, POLL_MS);
}
