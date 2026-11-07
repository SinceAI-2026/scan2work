// One capability boundary for the shared demo; installed apps keep their existing write APIs.
export const isPublicDemo = runtime => runtime?.public_demo === true;
export const canWriteSharedData = runtime => !isPublicDemo(runtime) && runtime?.shared_writes_enabled !== false;
// The hosted demo can opt in to the voice assistant and its job log (VALMET_PUBLIC_ASSISTANT=1 on the server):
// inspection jobs and notes are then shared, while scene edits and library writes stay local.
export const canWriteFieldJobs = runtime => canWriteSharedData(runtime) || runtime?.field_jobs_enabled === true;
export const voiceAssistantEnabled = runtime => runtime?.voice_assistant === true
  || !(isPublicDemo(runtime) || runtime?.offline || runtime?.external_models_enabled === false);

const esc = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[c]);

export function installUrl(runtime, baseUrl = globalThis.location?.href) {
  if (typeof runtime?.install_url !== "string" || !runtime.install_url.trim()) return null;
  try {
    const url = new URL(runtime.install_url, baseUrl);
    return ["http:", "https:"].includes(url.protocol) ? url.href : null;
  } catch { return null; }
}

export function installAction(runtime, label = "Install Scan2Work") {
  const url = installUrl(runtime);
  return url ? `<a class="demo-install-link" href="${esc(url)}" target="_blank" rel="noopener">${esc(label)}</a>` : `<span>${esc(label)}</span>`;
}

export function mountDemoBanner(runtime) {
  if (!isPublicDemo(runtime) || document.getElementById("public-demo-banner")) return;
  const banner = document.createElement("div");
  banner.id = "public-demo-banner";
  banner.className = "public-demo-banner";
  banner.innerHTML = `<span>${runtime?.field_jobs_enabled === true ? "Live demo · jobs are shared; scene edits stay in this browser" : "Live demo · edits stay in this browser"}</span>${installAction(runtime, "Install")}`;
  document.querySelector("header")?.after(banner);
}

export async function sharedWrite(runtime, url, options, fetcher = globalThis.fetch) {
  if (!canWriteSharedData(runtime)) {
    const error = new Error("Install Scan2Work to save your own library and inspection jobs.");
    error.code = "public_demo_read_only";
    throw error;
  }
  return fetcher(url, options);
}
