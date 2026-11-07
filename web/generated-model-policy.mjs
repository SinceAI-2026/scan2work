// Generated meshes are visual hypotheses, not new owners of the scan's Gaussians.
// A multi-object proxy cannot represent independent edits of its source objects.
export const GENERATED_MODEL_REVIEWS = Object.freeze({
  'A207.glb': Object.freeze({decision:'prefer_original_scan',reviewed_on:'2026-11-07',
    reason:'Manual visual review: the generated washing-machine replacement obscures the intact front-loader in the original scan.',
    evidence:'Same aerial camera with and without AI 3D replacement; original video frames at 10.67 s and 82.67 s.',
    scope:'Appearance only. The model archive and original object memberships are retained.'}),
});
export function generatedModelAllowed(name) { return GENERATED_MODEL_REVIEWS[name]?.decision !== 'prefer_original_scan'; }
export function canShowGeneratedModel(owners, { roomAsset = a => a, removed = () => false, held = new Set(), modelName } = {}) {
  return generatedModelAllowed(modelName) && owners.length > 0 && owners.every(a => !removed(a.id) && !held.has(a.id) && !roomAsset(a)?.transform);
}

export function generatedCoverage(models, enabled = true) {
  return new Set(enabled ? models.filter(m => m.visible && generatedModelAllowed(m.name)).flatMap(m => m.owners.map(a => a.id)) : []);
}
