// The Arduino fault panel: a round icon next to Display that morphs into a card showing which fault the board
// shows and whether the board is online. Self-contained: it adds its own button to the room toolbar, polls
// /api/live/board once a second and hides itself if the server has no fault panel. Tells the rest of the viewer
// through a "fault-panel" window event; nothing depends on it.

const API = "/api/live/board";
const STATUS_TEXT = { fault: "Fault", in_progress: "Job open", done: "Done", clear: "All clear", waiting: "Waiting" };
const CHIP = '<svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" aria-hidden="true" focusable="false" viewBox="0 0 24 24"><g fill="none" stroke="currentColor" stroke-linejoin="round" stroke-width="1.5"><path d="M4 12c0-3.771 0-5.657 1.172-6.828S8.229 4 12 4s5.657 0 6.828 1.172S20 8.229 20 12s0 5.657-1.172 6.828S15.771 20 12 20s-5.657 0-6.828-1.172S4 15.771 4 12Z"/><path d="M7.732 16.268C8.464 17 9.643 17 12 17c.79 0 1.447 0 2-.028L16.972 14c.028-.553.028-1.21.028-2c0-2.357 0-3.536-.732-4.268S14.357 7 12 7s-3.536 0-4.268.732S7 9.643 7 12s0 3.536.732 4.268Z"/><path stroke-linecap="round" d="M8 2v2m8-2v2m-4-2v2M8 20v2m4-2v2m4-2v2m6-6h-2M4 8H2m2 8H2m2-4H2m20-4h-2m2 4h-2"/></g></svg>'; // hugeicons:chip

const host = document.createElement("div");
host.className = "fault-panel-switch";
host.hidden = true;
host.innerHTML = `
  <button type="button" class="fault-panel-fab" id="btn-fault-panel" aria-expanded="false" aria-controls="fault-panel-card"
    aria-label="Arduino panel" title="Arduino panel">${CHIP}<i class="fault-panel-dot" aria-hidden="true"></i></button>
  <section class="fault-panel" id="fault-panel-card" aria-label="Arduino fault panel" data-open="false" inert>
    <div class="fault-panel-head"><strong>Arduino panel</strong><span class="fault-panel-pill" id="fp-status">…</span>
      <button type="button" class="fault-panel-close" aria-label="Close Arduino panel">×</button></div>
    <p class="fault-panel-task" id="fp-task"></p>
    <div class="fault-panel-bars" id="fp-bars" aria-hidden="true">
      <div class="fault-panel-bar"><span>Normal</span><i><b id="fp-normal"></b></i><em id="fp-normal-val"></em></div>
      <div class="fault-panel-bar now"><span>Now</span><i><b id="fp-now"></b></i><em id="fp-now-val"></em></div>
    </div>
    <p class="fault-panel-meta" id="fp-meta" role="status"></p>
    <div class="fault-panel-actions">
      <button type="button" data-action="ack">Acknowledge</button>
      <button type="button" data-action="claim">Show newest</button>
    </div>
  </section>`;
(document.getElementById("display-switch") || document.querySelector(".room-toolbar"))?.after(host);

const fab = host.querySelector("#btn-fault-panel");
const card = host.querySelector("#fault-panel-card");
const $ = id => card.querySelector(`#${id}`);
const num = v => (v == null ? "–" : Math.abs(v) >= 100 ? v.toFixed(0) : v.toFixed(1));
let lastKey = "";

function setOpen(open, { focus = false } = {}) {
  card.dataset.open = String(open);
  card.inert = !open;
  fab.setAttribute("aria-expanded", String(open));
  if (open) card.querySelector(".fault-panel-close").focus({ preventScroll: true });
  else if (focus) fab.focus();
}
fab.addEventListener("click", () => setOpen(card.dataset.open !== "true", { focus: true }));
card.querySelector(".fault-panel-close").addEventListener("click", () => setOpen(false, { focus: true }));
document.addEventListener("pointerdown", e => { if (card.dataset.open === "true" && !host.contains(e.target)) setOpen(false); });
document.addEventListener("keydown", e => {
  if (card.dataset.open !== "true" || e.key !== "Escape") return;
  e.preventDefault(); e.stopPropagation(); setOpen(false, { focus: true });
}, true);

function storeText(name, s) {
  if (!s?.configured) return `${name} off`;
  return s.connected ? `${name} on` : `${name} offline`;
}

function render(st) {
  host.hidden = false;
  host.dataset.status = st.status;
  const t = st.task;
  fab.title = `Arduino panel · ${STATUS_TEXT[st.status] || st.status}${t ? ` · ${t.tag}` : ""}`;
  $("fp-status").textContent = STATUS_TEXT[st.status] || st.status;
  $("fp-task").textContent = t ? `${t.tag} · ${t.issue_code || t.issue || ""}${st.status === "in_progress" ? ` · ${t.checks_done}/${t.checks_total} checks` : ""}`
    : st.status === "waiting" ? `No board yet · first fault ${st.next_fault || "none"}` : "Nothing broken";
  const sig = t?.signal;
  $("fp-bars").hidden = !sig;
  if (sig) {
    $("fp-normal").style.width = `${sig.normal_pct}%`;
    $("fp-now").style.width = `${sig.now_pct}%`;
    $("fp-normal-val").textContent = num(sig.normal);
    $("fp-now-val").textContent = num(sig.now);
    $("fp-bars").title = `${sig.name}: ${sig.sigma} standard deviations from its 3-day mean`;
  }
  $("fp-meta").textContent = `${st.board_online ? "Board online" : "Board offline"}${sig ? ` · ${sig.name.replaceAll("_", " ")}` : ""} · ${storeText("Tiger", st.stores?.tiger)} · ${storeText("Snowflake", st.stores?.snowflake)}`;
  card.querySelector('[data-action="ack"]').disabled = st.status !== "fault";
  const key = `${st.status}:${t?.tag || ""}`;
  if (key !== lastKey) {
    lastKey = key;
    window.dispatchEvent(new CustomEvent("fault-panel", { detail: st }));
  }
}

async function poll() {
  try {
    const res = await fetch(API, { cache: "no-store" });
    if (!res.ok) throw new Error(res.status);
    render(await res.json());
    setTimeout(poll, 1000);
  } catch {
    host.hidden = true;
    setTimeout(poll, 10000); // server without the fault panel, or restarting
  }
}

card.addEventListener("click", async e => {
  const action = e.target.closest("button[data-action]")?.dataset.action;
  if (!action) return;
  const res = await fetch(`${API}/action`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ action, by: "viewer" }) });
  if (res.ok) render(await res.json());
});

poll();
