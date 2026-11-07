import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {validateManualAnnotations,createManualMask,manualRowVisible,matchingManualAnnotations} from './manual-annotation-data.mjs';

const annotation={id:'MANUAL-WASHER-001',kind:'manual_annotation',mapped:false,source_sha256:'scan',rendered_rows:10,render_indices:[1,3,8],source_row_count:3,center:[0,1,0],size:[1,1,1]};
const payload={available:true,source_sha256:'scan',rendered_rows:10,annotations:[annotation]};
const options={count:10,sourceSha:'scan'};
test('manual membership fails closed for a different scan or invalid/overlapping row IDs',()=>{
  assert.equal(validateManualAnnotations(payload,options).length,1);
  assert.deepEqual(validateManualAnnotations(null,options),[]);
  for(const change of [{source_sha256:'other'},{rendered_rows:11},{render_indices:[1,1,8]},{render_indices:[1,3,10]},{mapped:true}]) {
    assert.throws(()=>validateManualAnnotations({...payload,annotations:[{...annotation,...change}]},options));
  }
  assert.throws(()=>validateManualAnnotations({...payload,annotations:[annotation,{...annotation,id:'SECOND'}]},options));
});
test('remove/restore is an independent exact-row browser preference, not an owner edit',()=>{
  const saved=new Map([['canonical-room-edits','["CV-001","CV-006","VS-003"]']]);
  const storage={getItem:key=>saved.get(key),setItem:(key,value)=>saved.set(key,value)};
  const args={...options,datasetId:'baseline',storage};
  const mask=createManualMask([annotation],args);
  mask.setRemoved(annotation.id,true);
  assert.deepEqual([...mask.membership],[0,-1,0,-1,0,0,0,0,-1,0]);
  assert.equal(saved.get('canonical-room-edits'),'["CV-001","CV-006","VS-003"]');
  assert.equal(createManualMask([annotation],args).isRemoved(annotation.id),true);
  assert.equal(createManualMask([annotation],{...args,datasetId:'other'}).isRemoved(annotation.id),false);
  mask.setRemoved(annotation.id,false);
  assert.deepEqual([...mask.membership],[0,1,0,1,0,0,0,0,1,0]);
});
test('manual-owned rows are excluded from moved fragment copies, without removing their neighbours',()=>{
  const mask=createManualMask([annotation],{...options,datasetId:'baseline'});
  const mixedOwner=Uint32Array.from([0,1,2,3,4]);
  assert.deepEqual([...mask.withoutOwned(mixedOwner)],[0,2,4]);
  mask.setRemoved(annotation.id,true);
  assert.deepEqual([...mask.withoutOwned(mixedOwner)],[0,2,4]);
  assert.equal(mask.ownsRow(8),true);
  assert.equal(mask.ownsRow(4),false);
});
test('isolation admits only retained manual rows and has no effect when inactive',()=>{
  assert.equal(manualRowVisible(1,1),true);
  for(const v of [-1,0,2])assert.equal(manualRowVisible(v,1),false);
  for(const v of [0,1,2])assert.equal(manualRowVisible(v),true);
  assert.equal(manualRowVisible(-1),false);
});
test('renderer protects manual rows before canonical cuts and movement; manual selection clears service context',()=>{
  const app=readFileSync(new URL('./app.js',import.meta.url),'utf8');
  const shader=readFileSync(new URL('./explode.mjs',import.meta.url),'utf8');
  assert.match(shader,/float instId = manualOwned \? 0\.0/);
  assert.match(app,/!synthetic && !manualOwned && instId < 0\.5 && uCutCount/);
  assert.match(app,/manualAnnotations\?\.withoutOwned\(originalRows\)/);
  const context=app.slice(app.indexOf('context(asset){'),app.indexOf('focus:asset=>viewRoomLocation'));
  assert.match(context,/state.selectedAsset=asset/);
  assert.ok(context.indexOf("'detail-health-pill').hidden=true")<context.indexOf("'detail-title').textContent"));
  assert.match(context,/No workbook status linked/);
  const ui=readFileSync(new URL('./manual-annotations.mjs',import.meta.url),'utf8');
  assert.doesNotMatch(ui,/fetch\(|sharedWrite|\/api\/field-jobs/);
});

test('manual identity is discoverable through the default registered filter without inventing a mapped record',()=>{
  const assets=[{...annotation,label:'Washing machine'}];
  for(const filter of ['mapped','alerts','all'])assert.equal(matchingManualAnnotations(assets,'washing',filter).length,1);
  assert.equal(matchingManualAnnotations(assets,'MANUAL-WASHER','mapped').length,1);
  assert.equal(matchingManualAnnotations(assets,'valve','mapped').length,0);
  assert.equal(matchingManualAnnotations(assets,'','mapped').length,0);
  const ui=readFileSync(new URL('./manual-annotations.mjs',import.meta.url),'utf8');
  assert.match(ui,/startsWith\('No matching '\)\)empty.hidden=matches.length>0/);
});
