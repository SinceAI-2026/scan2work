// Call the field-service voice assistant (ElevenLabs agent Ilkka) from the viewer. The ElevenLabs widget runs the
// call in the browser; the agent reaches this project through mcp_server.py. The equipment open in Equipment
// details is passed as the dynamic variable selected_equipment, so "what's wrong with this one?" needs no tag.
// It reads the tag from #detail-title ("control valve · CV-003"), which app.js fills when equipment is selected.
// The agent has one client tool, show_equipment: it selects the equipment and moves the camera to it on this screen.
// field-jobs.mjs shows the job log the agent writes (inspection queue, checklist, technician notes).

import { mountFieldJobs, showEquipment } from "./field-jobs.mjs";

const AGENT_ID = "agent_5501m6v02k69e2r8fwc0hznmfda5";
const WIDGET_SRC = "https://unpkg.com/@elevenlabs/convai-widget-embed@0.21.4/dist/index.js";

function selectedTag() {
  const title = document.getElementById("detail-title")?.textContent || "";
  const tag = title.split("·").pop().trim();
  return /^[A-Z]{1,4}-\d{2,4}$/.test(tag) ? tag : "none";
}

async function mount() {
  // Offline imports must not load an external widget or start an external model session.
  let runtime;
  try { runtime = (await fetch("./data/digital_twin.json").then(res => res.json())).runtime; }
  catch { return; }
  if (runtime?.offline || runtime?.external_models_enabled === false) {
    mountFieldJobs(selectedTag);
    return;
  }
  const widget = document.createElement("elevenlabs-convai");
  widget.setAttribute("agent-id", AGENT_ID);
  widget.setAttribute("action-text", "Call Ilkka for support");
  widget.setAttribute("start-call-text", "Start call");
  widget.setAttribute("end-call-text", "End call");
  widget.setAttribute("placement", "bottom"); // bottom right would cover the Equipment details panel
  const sync = () => widget.setAttribute("dynamic-variables", JSON.stringify({ selected_equipment: selectedTag() }));
  sync();
  const title = document.getElementById("detail-title");
  if (title) new MutationObserver(sync).observe(title, { childList: true, characterData: true, subtree: true });
  document.body.appendChild(widget);
  const script = document.createElement("script");
  script.src = WIDGET_SRC;
  script.async = true;
  script.onerror = () => console.warn("voice assistant: the ElevenLabs widget did not load (offline?)");
  document.head.appendChild(script);
  // The widget asks for client tools when a call starts; their return value is what the agent hears back.
  window.addEventListener("elevenlabs-convai:call", (event) => {
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
  mountFieldJobs(selectedTag);
  window.__voiceAssistant = { selectedTag, showEquipment };
}

if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", mount);
else mount();
