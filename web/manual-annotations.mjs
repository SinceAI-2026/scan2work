import * as THREE from 'three';
import {validateManualAnnotations,createManualMask,matchingManualAnnotations} from './manual-annotation-data.mjs';
import {escapeHtml as esc} from './imported-assets.mjs';
import {renderAssetPhoto} from './asset-photo.mjs';

/** Manual identity + exact-row visibility. Never appends canonical assets or edits their ownership/navigation. */
export function mountManualAnnotations({payload,state,count,uniforms,container,scene,camera,controls,host}) {
  const empty={close(){},renderSearch(){},frame(){},ownsRow:()=>false,withoutOwned:rows=>rows,get isOpen(){return false;},get isolated(){return false;}};
  let annotations;
  try{annotations=validateManualAnnotations(payload,{count,sourceSha:state.twin.scene.source_sha256});}catch(error){console.warn(error.message);return empty;}
  if(!annotations.length)return empty;
  let storage;try{storage=localStorage;}catch{}
  const model=createManualMask(annotations,{count,datasetId:state.datasetId,sourceSha:payload.source_sha256,storage});
  const u=uniforms(),size=u.uTexSize.value,pixels=new Float32Array(size.x*size.y);pixels.set(model.membership);
  const texture=new THREE.DataTexture(pixels,size.x,size.y,THREE.RedFormat,THREE.FloatType);
  texture.magFilter=texture.minFilter=THREE.NearestFilter;texture.generateMipmaps=false;texture.needsUpdate=true;
  u.uManualMask.value.dispose();u.uManualMask.value=texture;u.uManualActive.value=1;
  const card=document.createElement('section');card.className='manual-annotation-panel';card.hidden=true;card.setAttribute('aria-label','Manually identified item');container.append(card);
  const list=document.createElement('section');list.className='manual-annotation-list';list.setAttribute('aria-label','Manually identified items');
  let selected=null,open=false,isolated=false,outline=null,saved=null,lastQuery='',lastFilter='';
  const removed=()=>selected&&model.isRemoved(selected.id);
  function maskChanged(){pixels.set(model.membership);texture.needsUpdate=true;}
  function renderSearch(query='',filter='all'){
    lastQuery=query;lastFilter=filter;
    const matches=matchingManualAnnotations(annotations,query,filter);
    list.hidden=!matches.length;
    list.innerHTML='<strong>Manually identified</strong>'+matches.map(a=>`<button type="button" data-manual="${esc(a.id)}" class="proposal-card"><span class="prop-class">${esc(a.label)}</span><small>Manual label · ${model.isRemoved(a.id)?'hidden in this browser':'no workbook status'}</small></button>`).join('');
    const assetList=document.getElementById('asset-list');assetList.before(list);
    const empty=assetList.querySelector('.list-empty');
    if(empty?.textContent.startsWith('No matching '))empty.hidden=matches.length>0;
    list.querySelectorAll('[data-manual]').forEach(button=>button.addEventListener('click',()=>show(annotations.find(a=>a.id===button.dataset.manual))));
  }
  function render(){
    card.innerHTML=`<div class="manual-heading"><small>MANUAL LABEL</small><button type="button" data-manual-action="close" aria-label="Close manual item">×</button></div><h2>${esc(selected.label)}</h2>
      <p>Manual label · ${selected.boundary_status==='manually_reviewed_approximate'?'approximate boundary':'boundary needs review'}<br>No workbook status linked.</p>
      <div class="manual-actions"><button type="button" data-manual-action="focus">Focus</button><button type="button" data-manual-action="isolate" ${removed()?'disabled':''}>${isolated?'Show room':'Isolate item'}</button></div>
      <button type="button" data-manual-action="remove">${removed()?'Restore region':'Remove selected region'}</button>
      <p class="manual-state" role="status">${removed()?'Region hidden in this browser · navigation unchanged.':'Original scan · selected region'} <span data-extended>· ${selected.source_row_count.toLocaleString()} Gaussians</span></p>
      <figure class="asset-photo" hidden><img alt="" hidden><figcaption></figcaption></figure>
      <details><summary>Source and boundary details</summary><p>${esc(selected.boundary_notice)}</p><p>${esc(selected.source_regions?.join(', '))}. This is a separate manual annotation; existing room records and navigation are unchanged. Removing it hides only these recorded points. Unassigned fragments may remain.</p><a href="${esc(selected.export_url)}" download>Download original region (.ply)</a></details>`;
    renderAssetPhoto({...selected,source_region_id:selected.source_regions?.[0]},card.querySelector('figure'),location.href);
  }
  function focus(){
    host.focus(selected);
    const direction=camera.position.clone().sub(controls.target).normalize();
    if(direction.lengthSq()<.1)direction.set(.8,.4,1).normalize();
    controls.target.fromArray(selected.center);camera.position.copy(controls.target).addScaledVector(direction,Math.max(1.4,Math.hypot(...selected.size)*1.6));controls.update();
  }
  function show(a){
    if(!open){saved=host.open(a);open=true;document.body.classList.add('manual-annotation-active');}
    selected=a;isolated=false;u.uManualOnly.value=0;card.hidden=false;
    if(outline){scene.remove(outline);outline.geometry.dispose();outline.material.dispose();}
    outline=new THREE.LineSegments(new THREE.EdgesGeometry(new THREE.BoxGeometry(...a.size)),new THREE.LineBasicMaterial({color:0xe7ae52,depthTest:false}));
    outline.position.fromArray(a.center);outline.rotation.y=-(a.yaw||0);outline.renderOrder=10;scene.add(outline);
    host.context(a);focus();render();card.querySelector('[data-manual-action=close]').focus({preventScroll:true});
  }
  function close(){if(!open)return;open=false;isolated=false;u.uManualOnly.value=0;card.hidden=true;outline.visible=false;document.body.classList.remove('manual-annotation-active');host.close(saved);saved=null;list.querySelector('[data-manual]')?.focus({preventScroll:true});}
  card.addEventListener('click',event=>{
    const action=event.target.closest('[data-manual-action]')?.dataset.manualAction;
    if(action==='close')close();
    if(action==='focus')focus();
    if(action==='isolate'){isolated=!isolated;u.uManualOnly.value=isolated?annotations.indexOf(selected)+1:0;render();}
    if(action==='remove'){model.setRemoved(selected.id,!removed());isolated=false;u.uManualOnly.value=0;maskChanged();render();renderSearch(lastQuery,lastFilter);}
    if(action && action!=='close')card.querySelector(`[data-manual-action=${action}]`)?.focus({preventScroll:true});
  });
  return {close,renderSearch,ownsRow:model.ownsRow,withoutOwned:model.withoutOwned,
    frame(){if(outline)outline.visible=open&&!removed();},get isOpen(){return open;},get isolated(){return isolated;}};
}
