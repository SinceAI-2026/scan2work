// Library links request a draft; they never carry a copied device record or persisted placement.
export function parseLibraryCloneHash(hash) {
  const match = /^#clone=(.+)$/.exec(hash);
  if (!match) return null;
  try { return decodeURIComponent(match[1]); } catch { return null; }
}

export function libraryCloneIssue(source, hasMembership = true) {
  if (!source || source.copy_of) return 'This saved object has no matching source in this room.';
  if (!hasMembership) return 'Scan membership is unavailable for this object.';
  if (![source.center, source.size].every(v => Array.isArray(v) && v.length === 3 && v.every(Number.isFinite)) ||
      source.size.some(v => v <= 0) || !Number.isFinite(source.yaw)) return 'This object has no usable room geometry.';
  return '';
}
