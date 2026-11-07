// Generated meshes are visual hypotheses, not new owners of the scan's Gaussians.
// A multi-object proxy cannot represent independent edits of its source objects.
export function canShowGeneratedModel(owners, { roomAsset = a => a, removed = () => false, held = new Set() } = {}) {
  return owners.length > 0 && owners.every(a => !removed(a.id) && !held.has(a.id) && !roomAsset(a)?.transform);
}

export function generatedCoverage(models, enabled = true) {
  return new Set(enabled ? models.filter(m => m.visible).flatMap(m => m.owners.map(a => a.id)) : []);
}
