// The technician's jobs on screen: an inspection queue in the left sidebar (what still needs inspection, and what is
// done) and a job card in Equipment details (checklist, notes, a note field and Mark done). The voice assistant writes
// the same log through mcp_server.py, so this polls /api/field-jobs and shows its notes and ticks as they land.
// showEquipment(tag) selects the equipment and moves the camera to it; the assistant calls it as a client tool.
// It drives the viewer through its own controls (the list cards and View location), so app.js needs no changes.

const POLL_MS = 3000;
const STATE_TEXT = { attention_required: "Attention", degraded: "Degraded", watch: "Watch" };
let data = { needs_inspection: [], done: [], jobs: {} };
let lastJson = "";
let currentTag = () => "none";

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

async function api(path, body) {
  const res = await fetch(path, body ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : {});
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
  return job.status === "in_progress" ? `${done}/${job.checks.length} steps` : job.status.replace("_", " ");
}

function row(r) {
  return `<li><button type="button" class="iq-row" data-tag="${esc(r.tag)}" title="Show ${esc(r.tag)} in the room">
    <span class="iq-tag">${esc(r.tag)}</span><span class="iq-label">${esc(r.label || r.type)}</span>
    <span class="iq-state ${esc(r.state)}">${esc(STATE_TEXT[r.state] || r.state)}</span>
    <span class="iq-progress">${esc(progress(r.job))}${r.job?.notes?.length ? ` · ${r.job.notes.length} note${r.job.notes.length > 1 ? "s" : ""}` : ""}</span>
  </button></li>`;
}

function renderQueue(root) {
  root.querySelector(".iq-count").textContent = data.needs_inspection.length;
  root.querySelector(".iq-list").innerHTML = data.needs_inspection.map(row).join("") || `<li class="iq-empty">Nothing left to inspect.</li>`;
  root.querySelector(".iq-done-count").textContent = data.done.length;
  root.querySelector(".iq-done-list").innerHTML = data.done.map(row).join("") || `<li class="iq-empty">No jobs closed as fixed yet.</li>`;
}

// ------------------------------------------------------------------ the job card (Equipment details)
function renderJob(root) {
  const tag = currentTag();
  root.hidden = tag === "none";
  if (root.hidden) return;
  const job = data.jobs[tag];
  const status = root.querySelector(".fj-status");
  status.textContent = job ? progress(job) : "No job yet";
  status.className = `fj-status ${job?.status || "none"}`;
  root.querySelector(".fj-checks").innerHTML = (job?.checks || []).map((c) => `
    <li class="${c.done ? "done" : ""}"><label>
      <input type="checkbox" data-check="${esc(c.id)}" ${c.done ? "checked disabled" : ""} ${job.status !== "in_progress" ? "disabled" : ""}>
      <span><b>${esc(c.id)}. ${esc(c.title)}</b>${c.safety ? ' <em class="fj-safety">safety</em>' : ""}
      ${c.result ? `<small>${esc(c.result)}${c.by ? ` (${esc(c.by)})` : ""}</small>` : ""}</span>
    </label></li>`).join("");
  root.querySelector(".fj-notes").innerHTML = (job?.notes || []).slice().reverse().map((n) => `
    <li class="${esc(n.kind)}"><div class="fj-note-meta">${esc(n.kind)} · ${esc(n.by || "technician")} · ${esc(n.at)}</div>${esc(n.text)}</li>`).join("")
    || `<li class="fj-empty">No notes yet. Notes from the call with Ilkka appear here too.</li>`;
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
  const root = document.createElement("section");
  root.id = "inspection-queue";
  root.className = "inspection-queue";
  root.setAttribute("aria-label", "Inspection queue");
  root.innerHTML = `<h2 class="field-label">Needs inspection <span class="iq-count"></span></h2><ol class="iq-list"></ol>
    <details class="iq-done"><summary>Done <span class="iq-done-count"></span></summary><ol class="iq-done-list"></ol></details>`;
  root.addEventListener("click", (e) => {
    const b = e.target.closest(".iq-row");
    if (b) showEquipment(b.dataset.tag).catch((err) => flash(err.message));
  });
  const anchor = document.querySelector(".left-sidebar .mission-box");
  anchor ? anchor.after(root) : document.querySelector(".left-sidebar")?.appendChild(root);
}

function mountJob() {
  const root = document.createElement("div");
  root.id = "field-job";
  root.className = "section-block field-job";
  root.hidden = true;
  root.innerHTML = `<div class="sec-title-row"><h3 class="sec-title">Technician job</h3><span class="fj-status"></span></div>
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
  if (title) new MutationObserver(() => renderJob(root)).observe(title, { childList: true, characterData: true, subtree: true });
}

async function poll() {
  try {
    const next = await api("/api/field-jobs");
    if (JSON.stringify(next) !== lastJson) apply(next);
  } catch (e) {
    /* server restarting: keep the last view */
  }
}

export function mountFieldJobs(selectedTag) {
  currentTag = selectedTag;
  mountQueue();
  mountJob();
  poll();
  setInterval(poll, POLL_MS);
}
