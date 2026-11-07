// Call the field-service voice assistant (ElevenLabs agent Ilkka) from the viewer. The ElevenLabs widget runs the
// call in the browser; the agent reaches this project through mcp_server.py. The equipment open in Equipment
// details is passed as the dynamic variable selected_equipment, so "what's wrong with this one?" needs no tag.
// It reads the tag from #detail-title ("control valve · CV-003"), which app.js fills when equipment is selected.
// The agent has one client tool, show_equipment: it selects the equipment and moves the camera to it on this screen.
// field-jobs.mjs shows the job log the agent writes (inspection queue, checklist, technician notes).

import { mountFieldJobs, showEquipment } from "./field-jobs.mjs";
import { voiceConfig } from "./voice-config.mjs";

const WIDGET_SRC = "https://unpkg.com/@elevenlabs/convai-widget-embed@0.21.4/dist/index.js";
let registeredTags=new Set();

function selectedTag() {
  const title = document.getElementById("detail-title")?.textContent || "";
  const tag = title.split("·").pop().trim();
  return registeredTags.has(tag) && !document.getElementById("detail-health-pill")?.hidden ? tag : "none";
}

async function mount() {
  // Offline imports stay local unless the server explicitly configures a voice service.
  let runtime;
  try {
    const twin=await fetch("./data/digital_twin.json").then(res=>res.json());
    runtime=twin.runtime;
    registeredTags=new Set((twin.assets || []).filter(asset=>asset.mapped).map(asset=>asset.tag));
  }
  catch { return; }
  mountFieldJobs(selectedTag, runtime);
  const voice = voiceConfig(runtime);
  if (!voice) return;
  const widget = document.createElement("elevenlabs-convai");
  widget.setAttribute("agent-id", voice.agentId);
  widget.setAttribute("action-text", `Call ${voice.agentName} for support`);
  widget.setAttribute("start-call-text", "Start call");
  widget.setAttribute("end-call-text", "End call");
  widget.setAttribute("placement", "bottom-right"); // field-jobs.css moves it clear of Equipment details when that is open
  const sync = () => widget.setAttribute("dynamic-variables", JSON.stringify({ selected_equipment: selectedTag() }));
  sync();
  const title = document.getElementById("detail-title");
  if (title) new MutationObserver(sync).observe(title, { childList: true, characterData: true, subtree: true });
  document.body.appendChild(widget);
  const script = document.createElement("script");
  script.src = WIDGET_SRC;
  script.async = true;
  let failed = false;
  const unavailable = () => {
    if (failed) return;
    failed = true;
    clearTimeout(loadingTimeout);
    widget.remove();
    const status = document.createElement("div");
    status.className = "voice-load-status";
    status.setAttribute("role", "status");
    status.textContent = "Voice unavailable. Use workbook guidance in Equipment details.";
    document.body.appendChild(status);
  };
  const loadingTimeout = setTimeout(unavailable, 20000);
  script.onerror = unavailable;
  // Loading the widget does not start a call or request microphone access.
  if (globalThis.customElements) customElements.whenDefined("elevenlabs-convai").then(() => clearTimeout(loadingTimeout));
  document.head.appendChild(script);
  // The widget asks for client tools when a call starts; their return value is what the agent hears back.
  window.addEventListener("elevenlabs-convai:call", (event) => {
    if (!event.detail?.config) return;
    sync();
    event.detail.config.clientTools = {
      show_equipment: async ({ equipment }) => {
        try {
          const { tag, label, area } = await showEquipment(String(equipment || ""));
          return `Now showing ${tag} (${label}) on the technician's screen. Registered area: ${area || "not recorded"}.`;
        } catch (err) {
          return `Could not show it: ${err.message}`;
        }
      },
    };
  });
  window.__voiceAssistant = { selectedTag, showEquipment };
}

if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", mount);
else mount();
