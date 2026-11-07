// Review data has its own identities. Nothing here rewrites a canonical room asset or route.
export function validateProposalCatalog(catalog, expectedCount) {
  if (!Array.isArray(catalog?.regions)) throw new Error("Proposal catalog is unavailable.");
  if (Number.isInteger(expectedCount) && catalog.regions.length !== expectedCount) throw new Error("Proposal catalog count does not match this scene.");
  const seen=new Set();
  for(const region of catalog.regions) {
    if(typeof region.id!=="string" || !region.id || seen.has(region.id)) throw new Error("Proposal IDs must be unique.");
    seen.add(region.id);
    if(![region.center,region.size].every(v=>Array.isArray(v)&&v.length===3&&v.every(Number.isFinite)) ||
      !region.size.every(v=>v>=0) || !Number.isFinite(region.yaw || 0)) throw new Error(`Invalid proposal geometry: ${region.id}`);
    if(!Number.isInteger(region.total_source_rows) || !Number.isInteger(region.visible_source_rows) || region.visible_source_rows<0 ||
      region.total_source_rows<region.visible_source_rows) throw new Error(`Invalid proposal visibility count: ${region.id}`);
  }
  return catalog.regions;
}
export function findProposals(regions,query) {
  const q=String(query || "").trim().toLowerCase();
  return regions.filter(r=>!q || [r.id,r.label,r.class,...(r.original_matches || []).flatMap(m=>[m.id,m.tag,m.label])]
    .some(value=>String(value || "").toLowerCase().includes(q)));
}
export function proposalVisibilityText(region) {
  return `${region.visible_source_rows.toLocaleString()} / ${region.total_source_rows.toLocaleString()} source Gaussians visible in this room render`;
}

// The optional full dataset is installed separately; never guess a localhost port.
export function proposalFullViewUrl(configuredUrl,regionId,baseUrl) {
  if(typeof configuredUrl!=="string" || !configuredUrl.trim())return null;
  try {
    const url=new URL(configuredUrl,baseUrl);
    if(!["http:","https:"].includes(url.protocol))return null;
    url.hash=`apart=${encodeURIComponent(regionId)}`;
    return url;
  }catch{return null;}
}
