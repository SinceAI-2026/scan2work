// Voice is an independently configured service. Enabling it never changes the
// backend inference policy or the permission to persist shared jobs/scene edits.
import { voiceAssistantEnabled } from "./runtime-access.mjs";

export function voiceConfig(runtime) {
  if (runtime && Object.hasOwn(runtime, "voice")) {
    const value = runtime.voice;
    if (!value || value.enabled !== true || value.provider !== "elevenlabs" ||
        !["read_only", "shared_jobs"].includes(value.service_mode) ||
        (value.service_mode === "shared_jobs" && runtime.field_jobs_enabled !== true) ||
        typeof value.agent_id !== "string" || !/^agent_[A-Za-z0-9_-]{1,100}$/.test(value.agent_id) ||
        typeof value.agent_name !== "string" || !value.agent_name.trim() || value.agent_name.length > 80) return null;
    return { agentId: value.agent_id, agentName: value.agent_name.trim(), serviceMode: value.service_mode };
  }
  if (!voiceAssistantEnabled(runtime)) return null;
  // Wael's --assistant mode intentionally enables the original agent and may
  // separately enable its shared job log. Do not treat it as an offline default.
  const serviceMode = runtime?.field_jobs_enabled === true ? "shared_jobs"
    : runtime?.voice_assistant === true ? "read_only" : "legacy";
  return { agentId: "agent_5501m6v02k69e2r8fwc0hznmfda5", agentName: "Ilkka", serviceMode };
}
