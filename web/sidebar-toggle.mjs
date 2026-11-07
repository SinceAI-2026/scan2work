// Show or hide the left sidebar (the "To inspect" / "All equipment" menu) from a button at the left of the top bar.
// Hiding gives the 3D view the full width; app.js's ResizeObserver resizes the renderer. The sidebar's elements stay
// in the page, so the voice assistant's show_equipment keeps working while it is hidden. The choice is remembered.

const KEY = "field-atlas-sidebar-collapsed";

function mount() {
  const bar = document.querySelector(".topbar-left");
  const sidebar = document.querySelector(".left-sidebar");
  if (!bar || !sidebar) return;
  sidebar.id ||= "left-sidebar";
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "sidebar-toggle";
  btn.setAttribute("aria-controls", sidebar.id);
  btn.innerHTML = `<svg viewBox="0 0 20 20" width="18" height="18" aria-hidden="true"><rect x="2.5" y="3.5" width="15" height="13" rx="2" fill="none" stroke="currentColor" stroke-width="1.6"/><path d="M7.5 3.5v13" stroke="currentColor" stroke-width="1.6"/></svg>`;
  bar.prepend(btn);

  const set = (collapsed) => {
    document.body.classList.toggle("sidebar-collapsed", collapsed);
    btn.setAttribute("aria-expanded", String(!collapsed));
    btn.setAttribute("aria-label", collapsed ? "Show menu" : "Hide menu");
    btn.title = collapsed ? "Show menu" : "Hide menu";
    try { localStorage.setItem(KEY, collapsed ? "1" : "0"); } catch { /* private window */ }
  };
  btn.addEventListener("click", () => set(!document.body.classList.contains("sidebar-collapsed")));
  let saved = false;
  try { saved = localStorage.getItem(KEY) === "1"; } catch { /* private window */ }
  set(saved);
}

if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", mount);
else mount();
