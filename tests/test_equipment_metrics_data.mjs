/** Actual prepared workbook records: presentation must preserve supplied readings.
 * Optional datasets stay outside Git; override paths with VALMET_BASELINE_DATA_DIR
 * and VALMET_REVIEW_DATA_DIR. No API/model calls or dataset mutations.
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync,existsSync} from 'node:fs';
import {resolve,dirname} from 'node:path';
import {fileURLToPath} from 'node:url';
import {metricsFor} from '../web/equipment-metrics.mjs';

const root=resolve(dirname(fileURLToPath(import.meta.url)),'..');
const paths={baseline:process.env.VALMET_BASELINE_DATA_DIR||resolve(root,'../Wael-baseline-stage-2026-11-07'),
  review:process.env.VALMET_REVIEW_DATA_DIR||resolve(root,'web/data')};
const datasets=Object.fromEntries(Object.entries(paths).map(([name,path])=>{
  const file=resolve(path,'digital_twin.json');
  return [name,existsSync(file)?JSON.parse(readFileSync(file,'utf8')):null];
}));
const timestamp=value=>Date.parse(value.replace(' ','T')+'Z');

for(const [name,twin] of Object.entries(datasets)){
  const options={skip:!twin&&`${name} dataset not installed`};
  test(`${name}: all 30 mapped workbook bindings retain exact supplied signals and timestamps`,options,()=>{
    const assets=twin.assets.filter(a=>a.mapped),before=JSON.stringify(assets);
    assert.equal(assets.length,30);
    assert.equal(new Set(assets.map(a=>a.asset_id)).size,30);
    for(const asset of assets){
      const model=metricsFor(asset,undefined);
      assert.ok(model,asset.id);assert.equal(model.id,asset.id);assert.equal(model.assetId,asset.asset_id);
      assert.equal(model.tag,asset.tag);assert.equal(model.health,asset.health_score);
      const telemetry=asset.telemetry,series=telemetry.series;
      for(const [key,value] of Object.entries(telemetry.latest)){
        const metric=model.metrics.find(m=>m.key===key);assert.ok(metric,`${asset.tag}: ${key}`);
        assert.equal(metric.latest,Number.isFinite(value)?value:null,`${asset.tag}: ${key} value must not be rescaled`);
      }
      for(const [key,values] of Object.entries(series))if(key!=='timestamps'){
        const metric=model.metrics.find(m=>m.key===key);assert.ok(metric,`${asset.tag}: ${key} history`);
        assert.deepEqual(metric.values,values);assert.deepEqual(metric.timestamps,series.timestamps);
      }
      assert.equal(series.timestamps.length,24);
      assert.equal(series.timestamps[0],'2026-10-07 12:00');
      assert.equal(series.timestamps.at(-1),'2026-10-07 23:30');
      assert.equal(timestamp(series.timestamps.at(-1))-timestamp(series.timestamps[0]),11.5*60*60*1000);
      for(let i=1;i<series.timestamps.length;i++)assert.equal(timestamp(series.timestamps[i])-timestamp(series.timestamps[i-1]),30*60*1000);
      assert.equal(model.snapshot,series.timestamps.at(-1));
    }
    assert.equal(JSON.stringify(assets),before,'presentation must not rewrite readings or bindings');
  });
  test(`${name}: facility objects cannot inherit workbook metrics; latest-only error has no invented trend`,options,()=>{
    const unmapped=twin.assets.filter(a=>!a.mapped);assert.ok(unmapped.length>400);
    for(const asset of unmapped)assert.equal(metricsFor(asset,undefined),null,asset.id);
    const target=twin.assets.find(a=>a.tag==='CV-003'),model=metricsFor(target,undefined);
    const error=model.metrics.find(m=>m.key==='position_error_pct');
    assert.equal(error.latest,9.13);assert.deepEqual(error.values,[]);
    assert.equal(metricsFor(target,undefined,{removed:true}),null);
    assert.equal(metricsFor({...target,id:'MANUAL-WASHER-001',mapped:false},undefined),null);
  });
}

test('the baseline and proposal scene retain the same 30 workbook records with distinct scene bindings',
  {skip:(!datasets.baseline||!datasets.review)&&'Both prepared datasets are required'},()=>{
    const baseline=datasets.baseline.assets.filter(a=>a.mapped),review=new Map(datasets.review.assets.filter(a=>a.mapped).map(a=>[a.asset_id,a]));
    for(const asset of baseline){
      const candidate=review.get(asset.asset_id);assert.ok(candidate,asset.asset_id);
      assert.equal(candidate.tag,asset.tag);assert.equal(candidate.condition_state,asset.condition_state);
      assert.deepEqual(candidate.telemetry,asset.telemetry);
    }
    assert.equal(baseline.find(a=>a.tag==='CV-003').id,'OBJ-206');
    assert.equal(review.get('ASSET-003').id,'linards-BOX-0173');
  });
