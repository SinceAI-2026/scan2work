import { canWriteSharedData, canWriteFieldJobs } from "./runtime-access.mjs";

export const arduinoEnabled = runtime => runtime?.arduino_enabled === true
  || (runtime?.arduino_enabled !== false && canWriteSharedData(runtime));
export const canControlArduino = runtime => arduinoEnabled(runtime) && canWriteFieldJobs(runtime)
  && (runtime?.arduino_actions_enabled === true
    || (runtime?.arduino_actions_enabled !== false && canWriteSharedData(runtime)));

// Only the existing panel gestures are exposed to browsers. Board claims and
// heartbeats use the device's separate authenticated transport.
export async function panelAction(runtime, action, fetcher = globalThis.fetch) {
  if (!canControlArduino(runtime)) throw new Error("Arduino actions are disabled for this view.");
  if (!["ack", "claim"].includes(action)) throw new Error("Unknown Arduino panel action.");
  const response = await fetcher("/api/live/board/action", { method: "POST",
    headers: { "content-type": "application/json" }, body: JSON.stringify({ action, by: "viewer" }) });
  const state = await response.json();
  if (!response.ok) throw new Error(state.error || `Panel request failed (${response.status}).`);
  return state;
}

export function heartbeatText(state) {
  const age = state.heartbeat_age_s;
  const freshness = typeof age === "number" && Number.isFinite(age)
    ? `last heartbeat ${Math.max(0, Math.floor(age))} s ago`
    : state.board_online ? "heartbeat current" : "no heartbeat received";
  return `Board ${state.board_online ? "online" : "offline"} · ${freshness}`;
}

export function touchText(state) {
  const touch = state.touch;
  if (!state.board_online || !touch || !Number.isFinite(touch.swing) || !Number.isFinite(touch.base)) return "";
  return `Live A0 input · swing ${touch.swing} ADC · baseline ${touch.base} ADC · ${touch.touched ? "touched" : "released"}`;
}
