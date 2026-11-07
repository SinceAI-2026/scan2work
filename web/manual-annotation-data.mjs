/** Exact source-row overlays remain separate from the canonical room catalog. */
export function validateManualAnnotations(payload,{count,sourceSha}) {
  if(!payload?.available)return [];
  if(payload.source_sha256!==sourceSha || payload.rendered_rows!==count)throw Error('Manual annotation belongs to a different scan');
  const ids=new Set(),owned=new Set();
  return (payload.annotations||[]).map(a=>{
    if(!a.id||ids.has(a.id)||a.kind!=='manual_annotation'||a.mapped!==false)throw Error('Invalid manual annotation identity');
    ids.add(a.id);
    if(a.source_sha256!==sourceSha||a.rendered_rows!==count||a.render_indices?.length!==a.source_row_count)throw Error('Manual annotation membership is incomplete');
    if(!a.center?.every(Number.isFinite)||a.center.length!==3||!a.size?.every(n=>Number.isFinite(n)&&n>0)||a.size.length!==3)throw Error('Invalid manual annotation geometry');
    for(const row of a.render_indices){if(!Number.isInteger(row)||row<0||row>=count||owned.has(row))throw Error('Invalid or overlapping manual source rows');owned.add(row);}
    return a;
  });
}
export function createManualMask(annotations,{count,datasetId,sourceSha,storage}) {
  const key=`scan2work:manual-annotations:v1:${datasetId}:${sourceSha}`;
  const allowed=new Set(annotations.map(a=>a.id));
  let removed=new Set();
  try{removed=new Set((JSON.parse(storage?.getItem(key)||'[]')).filter(id=>allowed.has(id)));}catch{/* private browser/corrupt preference */}
  const membership=new Float32Array(count),owners=new Uint16Array(count);
  annotations.forEach((a,i)=>a.render_indices.forEach(row=>owners[row]=i+1));
  const refresh=()=>annotations.forEach((a,i)=>a.render_indices.forEach(row=>membership[row]=(removed.has(a.id)?-1:1)*(i+1)));
  refresh();
  return {membership,ownsRow:row=>owners[row]>0,owner:row=>owners[row],isRemoved:id=>removed.has(id),
    setRemoved(id,value){if(!allowed.has(id))throw Error('Unknown manual annotation');value?removed.add(id):removed.delete(id);refresh();try{storage?.setItem(key,JSON.stringify([...removed]));}catch{}},
    withoutOwned:rows=>rows.filter(row=>!owners[row])};
}
export function manualRowVisible(value,isolated=0){return value>=0 && (!isolated || value===isolated);}

export function matchingManualAnnotations(annotations,query='',filter='all') {
  const q=query.toLowerCase().trim();
  return annotations.filter(a=>(filter==='all'||q) && [a.label,a.name,a.id,...(a.search_terms||[])].some(value=>String(value||'').toLowerCase().includes(q)));
}
