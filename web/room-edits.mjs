const STORAGE_KEY = "field-atlas:equipment-room:edits";
const ENDPOINT = "./api/scene/edits";

async function serverEdits(action) {
  const res = await fetch(ENDPOINT, {
    cache:"no-store",
    signal:AbortSignal.timeout(5000),
    ...(action && {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(action)}),
  });
  const json = res.headers.get("content-type")?.includes("application/json");
  if (!json || res.status>=500) throw new Error("Room edits API unavailable");
  const result = await res.json();
  if (!res.ok) {
    const error = new Error(result.error || "Room edit was rejected.");
    error.rejected = true;
    throw error;
  }
  if (!Array.isArray(result.removed)) throw new Error("Room edits response unavailable");
  return result.removed;
}

export function roomEditStore(canEdit) {
  let cache = {};
  try { cache = JSON.parse(localStorage.getItem(STORAGE_KEY)) || {}; } catch {}
  const valid = list => Array.isArray(list) ? list.filter(edit=>canEdit(edit?.id)) : [];
  let removed = valid(cache.removed);
  let pending = valid(cache.pending);
  let offline = false;
  const snapshot = () => ({removed,offline});
  const save = () => {
    try { localStorage.setItem(STORAGE_KEY,JSON.stringify({removed,pending})); } catch {}
  };
  async function sync() {
    let server = await serverEdits();
    for (const edit of pending) {
      try { server = await serverEdits(edit); } catch (error) {
        if (!error.rejected) throw error;
      }
    }
    pending = [];
    offline = false;
    return valid(server);
  }
  return {
    async load() {
      try { removed = await sync(); } catch { offline = true; }
      save();
      return snapshot();
    },
    async edit(action, id, note="") {
      if (!canEdit(id)) throw new Error("This object cannot be removed.");
      const change = {action,id,note};
      try {
        if (pending.length) removed = await sync();
        removed = valid(await serverEdits(change));
        offline = false;
      } catch(error) {
        if (error.rejected) throw error;
        offline = true;
        pending = [...pending.filter(edit=>edit.id!==id),change];
        removed = removed.filter(edit=>edit.id!==id);
        if(action==="remove") removed.push({id,at:new Date().toISOString(),by:"viewer",note});
      }
      save();
      return snapshot();
    },
  };
}
