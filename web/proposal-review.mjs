import * as THREE from "three";
import {renderAssetPhoto} from "./asset-photo.mjs";
import {decodeInstanceIds,escapeHtml as esc} from "./imported-assets.mjs";
import {validateProposalCatalog,findProposals,proposalVisibilityText,proposalFullViewUrl} from "./proposal-review-data.mjs";

/** Optional read-only overlay. Canonical catalog, ownership and navigation are never changed. */
export function mountProposalReview({config,container,scene,camera,controls,button,uniforms,count,texSize,onOpen,onOriginal,originalLabel=id=>id}) {
  const empty={close(){},get isOpen(){return false;}};
  if(!config?.catalog_url)return empty;
  button.hidden=false;
  const panel=document.createElement("section");panel.className="proposal-review-panel";panel.hidden=true;
  panel.setAttribute("aria-label","Candidate review");
  panel.innerHTML=`<div class="proposal-review-heading"><strong>Detection candidates</strong><button type="button" data-close aria-label="Close candidate review">×</button></div>
    <p class="proposal-review-note">A separate review layer. These regions may overlap or contain parts of existing room objects.</p>
    <label>Find a candidate<input type="search" placeholder="Name, source ID, or matching room object"></label>
    <p class="proposal-review-status" role="status"></p><div class="proposal-review-list"></div>
    <div class="proposal-review-detail" hidden></div>`;
  container.appendChild(panel);
  const search=panel.querySelector("input"),list=panel.querySelector(".proposal-review-list"),status=panel.querySelector("[role=status]"),detail=panel.querySelector(".proposal-review-detail");
  let opened=false,regions=[],selected=null,limit=40,loadPromise=null,loaded=false,outline=null,restore=null;
  let projectionReady=false;
  const group=new THREE.Group();group.visible=false;scene.add(group);
  function clearOutline(){if(outline){group.remove(outline);outline.geometry.dispose();outline.material.dispose();outline=null;}}

  async function load() {
    const response=await fetch(config.catalog_url);
    if(!response.ok)throw new Error("Candidate catalog could not be loaded.");
    const catalog=await response.json();regions=validateProposalCatalog(catalog,config.count);
    // A missing projection does not hide the catalog or claim a complete object.
    try {
      const response=await fetch(config.projected_instance_url);
      if(!response.ok)throw new Error("Projection unavailable");
      const ids=decodeInstanceIds(await response.arrayBuffer(),count,regions.length);
      const actual=new Uint32Array(regions.length);
      for(const id of ids)if(id>0)actual[id-1]++;
      if(regions.some((r,i)=>actual[i]!==r.visible_source_rows))throw new Error("Projection counts differ from the candidate catalog");
      const pixels=new Float32Array(texSize.x*texSize.y);pixels.set(ids);
      const texture=new THREE.DataTexture(pixels,texSize.x,texSize.y,THREE.RedFormat,THREE.FloatType);
      texture.magFilter=texture.minFilter=THREE.NearestFilter;texture.generateMipmaps=false;texture.needsUpdate=true;
      const u=uniforms();u.uProposalTex.value.dispose();u.uProposalTex.value=texture;projectionReady=true;
    } catch(error) { panel.dataset.projectionError=error.message; }
    loaded=true;renderList();
  }

  function renderList(){
    const matches=findProposals(regions,search.value),shown=matches.slice(0,limit);
    status.textContent=`${matches.length.toLocaleString()} candidates · not added to the room catalog`;
    list.innerHTML=shown.map(r=>`<button type="button" data-proposal="${esc(r.id)}" aria-pressed="${selected?.id===r.id}"><strong>${esc(r.label || r.id)}</strong><small>${esc(r.id)} · ${(r.original_matches || []).length} room overlaps</small></button>`).join("") || '<p>No matching candidates.</p>';
    if(shown.length<matches.length)list.insertAdjacentHTML("beforeend",'<button type="button" data-more>Show next 40</button>');
    for(const node of list.querySelectorAll("[data-proposal]"))node.addEventListener("click",()=>select(regions.find(r=>r.id===node.dataset.proposal)));
    list.querySelector("[data-more]")?.addEventListener("click",()=>{limit+=40;renderList();});
  }

  function select(region){
    if(!region)return;selected=region;clearOutline();
    outline=new THREE.LineSegments(new THREE.EdgesGeometry(new THREE.BoxGeometry(...region.size)),new THREE.LineBasicMaterial({color:0xff8554,depthTest:false,transparent:true,opacity:1}));
    outline.position.fromArray(region.center);outline.rotation.y=-(region.yaw || 0);outline.renderOrder=10;group.add(outline);
    uniforms().uProposalId.value=projectionReady ? regions.indexOf(region)+1 : 0;
    const direction=camera.position.clone().sub(controls.target).normalize();
    if(direction.lengthSq()<.1)direction.set(.8,.35,1).normalize();
    const distance=Math.max(1.0,Math.min(8,Math.hypot(...region.size)*2.2));
    controls.target.fromArray(region.center);camera.position.copy(controls.target).addScaledVector(direction,distance);controls.update();
    const full=proposalFullViewUrl(config.full_view_url,region.id,location.href);
    detail.hidden=false;
    detail.innerHTML=`<h3>${esc(region.label || region.id)}</h3><p class="proposal-review-note">${esc(region.id)} · ${esc(region.review_status || "unreviewed")}</p>
      <p>${esc(proposalVisibilityText(region))}</p>
      ${!projectionReady ? `<p class="proposal-review-note">Box/evidence preview only: ${esc(panel.dataset.projectionError || "projection unavailable")}</p>` : '<p class="proposal-review-note">Orange highlights show candidate source points. Room ownership is unchanged.</p>'}
      <figure class="asset-photo" hidden><img alt="" hidden><figcaption></figcaption></figure>
      <strong>Overlapping room objects</strong><div class="proposal-overlaps"></div>
      <p class="proposal-review-note">Shared geometry is an association for review, not a new label or device binding.</p>
      ${full ? `<a class="btn-outline-full" target="_blank" rel="noopener" href="${esc(full.href)}">Inspect complete candidate ↗</a>` : '<p class="proposal-review-note">Complete candidate inspection is available with the optional extraction dataset.</p>'}`;
    renderAssetPhoto({...region,source_region_id:region.id},detail.querySelector("figure"),location.href);
    const matches=detail.querySelector(".proposal-overlaps");
    const originals=region.original_matches || [];
    if(!originals.length)matches.textContent="No source-point overlap with a room object.";
    for(const match of originals.slice(0,8)){
      const node=document.createElement("button");node.type="button";
      node.textContent=`${originalLabel(match.id)} · ${Number(match.shared_rows || 0).toLocaleString()} shared rows`;
      node.addEventListener("click",()=>{close();onOriginal(match.id);});matches.appendChild(node);
    }
    if(originals.length>8){const note=document.createElement("small");note.textContent=`${originals.length-8} further overlaps in the candidate catalog.`;matches.appendChild(note);}
    renderList();
  }

  async function open(){
    restore={position:camera.position.clone(),target:controls.target.clone()};
    restore.view=onOpen();opened=true;panel.hidden=false;group.visible=true;
    button.setAttribute("aria-expanded","true");container.classList.add("proposal-review-open");
    if(!loaded){status.textContent="Loading candidate review…";try{await(loadPromise ||= load());}catch(error){loadPromise=null;status.textContent=error.message;}}
    if(opened && selected)select(selected);
    if(opened)search.focus();
  }
  function close(){
    if(!opened)return;
    opened=false;panel.hidden=true;group.visible=false;uniforms().uProposalId.value=0;
    container.classList.remove("proposal-review-open");button.setAttribute("aria-expanded","false");
    if(restore){
      if(typeof restore.view==="function")restore.view();
      else {camera.position.copy(restore.position);controls.target.copy(restore.target);controls.update();}
      restore=null;
    }
  }
  button.addEventListener("click",()=>opened ? close() : open());
  panel.querySelector("[data-close]").addEventListener("click",close);
  search.addEventListener("input",()=>{limit=40;if(loaded)renderList();});
  panel.addEventListener("keydown",event=>{if(event.key==="Escape"){event.preventDefault();close();button.focus();}});
  return {close,get isOpen(){return opened;}};
}
