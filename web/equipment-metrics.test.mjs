import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {metricsFor,metricChartSvg,mountEquipmentMetrics} from './equipment-metrics.mjs';

const asset={id:'OBJ-206',asset_id:'ASSET-003',tag:'CV-003',label:'Control valve',mapped:true,condition_state:'degraded',health_score:52,
  telemetry:{latest:{position_actual_pct:67.792,position_setpoint_pct:58.66,process_pressure_bar:4.723,process_flow_pct:51.121,sensor_bias:0},
    stats:{process_pressure_bar:{min:4.62,max:5.76,mean:5.2}},
    series:{timestamps:['2026-10-07 12:00','2026-10-07 12:30','2026-10-07 13:00','2026-10-07 13:30'],position_actual_pct:[35,null,41,67.792],position_setpoint_pct:[29,30,40,58.66],process_pressure_bar:[4.8,4.9,4.7,4.723],process_flow_pct:[60,57,50,51.121],sensor_bias:[0,0,0,0]}},
  data_quality_issues:[{issue_id:'DQ-001',source_sheet:'Telemetry_Sample_3d',issue_type:'example'}]};

test('metrics preserve every supplied value, timestamp and workbook aggregate without inferring safety limits',()=>{
  const before=JSON.stringify(asset),m=metricsFor(asset,null);
  assert.equal(m.metrics.length,5);assert.equal(m.metrics.find(x=>x.key==='process_flow_pct').latest,51.121);
  assert.deepEqual(m.metrics.find(x=>x.key==='process_pressure_bar').range,{min:4.62,max:5.76});
  assert.equal(m.snapshot,'2026-10-07 13:30');assert.equal(m.health,52);assert.equal(m.quality[0].issue_id,'DQ-001');
  assert.equal(m.contextUnavailable,true);assert.equal(JSON.stringify(asset),before);
  assert.equal(m.metrics.find(x=>x.key==='sensor_bias').unit,'unit unspecified');
});
test('missing latest stays missing despite a historical last sample; zero and constant ranges remain valid',()=>{
  const a=structuredClone(asset);a.telemetry.latest.process_pressure_bar=null;
  const m=metricsFor(a,null);assert.equal(m.metrics.find(x=>x.key==='process_pressure_bar').latest,null);
  assert.deepEqual(m.metrics.find(x=>x.key==='sensor_bias').range,{min:0,max:0});
  assert.equal(m.metrics.find(x=>x.key==='sensor_bias').latest,0);
});
test('unmapped, movable, removed, mismatched and manual selections cannot expose borrowed telemetry',()=>{
  for(const changes of [{mapped:false},{mapped:undefined},{movable:true},{condition_state:'not_instrumented'}])assert.equal(metricsFor({...asset,...changes},null),null);
  assert.equal(metricsFor(asset,null,{removed:true}),null);
  assert.equal(metricsFor(asset,{devices:{'OBJ-206':{asset_id:'ASSET-999'}}}),null);
  assert.equal(metricsFor({id:'MANUAL-WASHER-001',label:'Washing machine'},null),null);
});
test('scenario provenance cannot expose copied history as the recipient workbook data',()=>{
  const context={devices:{'OBJ-206':{asset_id:'ASSET-003',simulated_emergency:true,primary_metric:{signal:'process_pressure_bar',latest:9.1,unit:'bar'}}}};
  const m=metricsFor(asset,context);assert.equal(m.scenario,true);assert.equal(m.snapshot,null);assert.equal(m.metrics[0].latest,9.1);assert.deepEqual(m.metrics[0].values,[]);assert.equal(m.metrics[0].range,null);
  assert.equal(m.health,null);
  assert.equal(metricsFor(asset,{unavailable:true},{knownScenario:true}).unavailable,true);
  assert.equal(metricsFor({...asset,simulated_emergency:true},null).scenario,true);
  assert.equal(metricsFor({...asset,simulated_emergency:true},{unavailable:true}).unavailable,true);
});
test('chart preserves missing-sample gaps and only overlays signals with matching physical units',()=>{
  const m=metricsFor(asset,null),position=metricChartSvg(m,'position_actual_pct');
  assert.match(position,/with position setpoint/);assert.match(position,/M54\.00.*M/s);assert.match(position,/2026-10-07 12:00/);assert.doesNotMatch(position,/3.day/i);
  assert.match(metricChartSvg(m,'position_actual_pct',{width:340}),/viewBox="0 0 340 205"/);
  const pressure=metricChartSvg(m,'process_pressure_bar');assert.doesNotMatch(pressure,/Position setpoint|#84d9df/);
  const noTrend=metricsFor({...asset,telemetry:{latest:{position_error_pct:9.132}}},null);
  assert.match(metricChartSvg(noTrend,'position_error_pct'),/No recorded trend/);
});

function fakeDocument(){
  const doc={activeElement:null};
  class Element{
    constructor(){this.listeners={};this.attrs={};this.hidden=false;this.open=false;this.innerHTML='';this.elements=new Map();}
    setAttribute(k,v){this.attrs[k]=v;}
    addEventListener(k,fn){this.listeners[k]=fn;}
    append(e){this.child=e;}
    focus(){doc.activeElement=this;}
    showModal(){this.open=true;}
    close(){this.open=false;this.listeners.close?.();}
    querySelector(s){if(!this.elements.has(s))this.elements.set(s,new Element());return this.elements.get(s);}
  }
  doc.createElement=()=>new Element();doc.body=new Element();doc.defaultView=new Element();return {doc,button:new Element()};
}
test('dialog opens from both-mode action, Escape restores focus, and changing selection clears stale metrics without writes',()=>{
  const {doc,button}=fakeDocument(),controller=mountEquipmentMetrics({button,document:doc});
  assert.equal(button.hidden,true);controller.update(asset,null);assert.equal(button.hidden,false);
  button.listeners.click();const dialog=doc.body.child;assert.equal(dialog.open,true);assert.match(dialog.innerHTML,/CV-003/);assert.match(dialog.innerHTML,/51.121/);
  let prevented=false;doc.defaultView.listeners.keydown({key:'Escape',preventDefault(){prevented=true;},stopImmediatePropagation(){}});
  assert.equal(prevented,true);assert.equal(dialog.open,false);assert.equal(doc.activeElement,button);
  button.listeners.click();controller.update({...asset,id:'OBJ-008',tag:'P-001',telemetry:{latest:{vibration_mm_s:5.548}}},null);
  assert.match(dialog.innerHTML,/P-001/);assert.doesNotMatch(dialog.innerHTML,/CV-003|51.121/);
  controller.update({...asset,mapped:false},null);assert.equal(button.hidden,true);assert.equal(dialog.open,false);
  const source=fs.readFileSync(new URL('./equipment-metrics.mjs',import.meta.url),'utf8');assert.doesNotMatch(source,/fetch\(|localStorage|sessionStorage/);
});
test('known runtime scenario stays withheld when refresh fails and metrics entry remains available in both modes',()=>{
  const {doc,button}=fakeDocument(),controller=mountEquipmentMetrics({button,document:doc});
  controller.update(asset,{devices:{'OBJ-206':{asset_id:'ASSET-003',simulated_emergency:true}}});button.listeners.click();
  assert.doesNotMatch(doc.body.child.innerHTML,/Workbook health/);
  controller.update(asset,{unavailable:true});assert.match(doc.body.child.innerHTML,/readings withheld/);assert.doesNotMatch(doc.body.child.innerHTML,/51.121|Workbook health/);
  const html=fs.readFileSync(new URL('./index.html',import.meta.url),'utf8'),css=fs.readFileSync(new URL('./presentation-mode.css',import.meta.url),'utf8');
  assert.match(html,/<button id="btn-equipment-metrics"[^>]*>Metrics &amp; trends<\/button>/);assert.doesNotMatch(css,/btn-equipment-metrics/);
});
