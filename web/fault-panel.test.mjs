import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import {arduinoEnabled, canControlArduino, panelAction, heartbeatText, touchText} from './arduino-access.mjs';
import {canWriteSharedData, mountDemoBanner} from './runtime-access.mjs';

const source=fs.readFileSync(new URL('./fault-panel.mjs',import.meta.url),'utf8')
  .replace(/^import .*;\n/m,'').replace(/mount\(\);\s*$/,'globalThis.done=mount();');
async function boot(runtime,{manifestOK=true,boardOK=true,state={status:'waiting',board_online:false,heartbeat_age_s:null}}={}) {
  const requests=[],created=[],nodes=new Map(),listeners=new Map();
  function node(key) {
    if(!nodes.has(key)) nodes.set(key,{dataset:{},style:{},setAttribute(){},focus(){},after(){},contains(){return false;},
      querySelector:selector=>node(selector),addEventListener:(event,fn)=>listeners.set(`${key}:${event}`,fn)});
    return nodes.get(key);
  }
  const fetcher=async(url,options)=>{
    requests.push({url,method:options?.method??'GET',...(options?.body?{body:JSON.parse(options.body)}:{})});
    return {ok:url==='./data/digital_twin.json'?manifestOK:boardOK,status:boardOK?200:503,
      json:async()=>url==='./data/digital_twin.json'?{runtime}:state};
  };
  const context={fetch:fetcher,arduinoEnabled,canControlArduino,panelAction:(r,a)=>panelAction(r,a,fetcher),heartbeatText,touchText,
    setTimeout(){},CustomEvent:class{},window:{dispatchEvent(){}},
    document:{createElement:tag=>{created.push(tag);return node('host');},getElementById:id=>node(id),querySelector:node,addEventListener(){}},console};
  vm.runInNewContext(source,context);await context.done;
  return {requests,created,node,click:listeners.get('#fault-panel-card:click')};
}
const clickAction=(view,action)=>view.click({target:{closest:()=>({dataset:{action}})}});

test('public/review mode does not mount or poll Arduino without its own explicit opt-in',async()=>{
  for(const runtime of [{public_demo:true},{public_demo:true,field_jobs_enabled:true},{public_demo:true,arduino_enabled:false},{shared_writes_enabled:false}]) {
    const actual=await boot(runtime);
    assert.deepEqual(actual.requests,[{url:'./data/digital_twin.json',method:'GET'}]);
    assert.deepEqual(actual.created,[]);
  }
  assert.deepEqual((await boot(undefined,{manifestOK:false})).created,[]);
});
test('installed offline and unmarked viewers retain board polling and actions',async()=>{
  for(const runtime of [{offline:true},undefined]) {
    const actual=await boot(runtime);
    assert.equal(actual.requests[1].url,'/api/live/board');
    await clickAction(actual,'ack');
    assert.deepEqual(actual.requests[2],{url:'/api/live/board/action',method:'POST',body:{action:'ack',by:'viewer'}});
  }
});
test('public Arduino polling and actions are separate from scene/library permissions',async()=>{
  const base={public_demo:true,shared_writes_enabled:false,arduino_enabled:true};
  const readOnly=await boot({...base,field_jobs_enabled:true});
  assert.equal(readOnly.requests[1].url,'/api/live/board');
  await clickAction(readOnly,'ack');
  assert.equal(readOnly.requests.length,2);
  assert.match(readOnly.node('#fp-error').textContent,/disabled/);
  const runtime={...base,field_jobs_enabled:true,arduino_actions_enabled:true};
  const active=await boot(runtime);
  await clickAction(active,'claim');
  assert.deepEqual(active.requests[2].body,{action:'claim',by:'viewer'});
  assert.equal(canWriteSharedData(runtime),false);
  assert.equal(canControlArduino({...runtime,field_jobs_enabled:false}),false);
  const before=active.requests.length;
  await clickAction(active,'heartbeat');
  assert.equal(active.requests.length,before,'browser cannot spoof a heartbeat');
});
test('connection failure remains visible and does not display stale signals as live',async()=>{
  const actual=await boot({public_demo:true,arduino_enabled:true},{boardOK:false});
  assert.equal(actual.node('host').hidden,false);
  assert.equal(actual.node('#fp-status').textContent,'Unavailable');
  assert.match(actual.node('#fp-meta').textContent,/Connection unavailable/);
  assert.equal(actual.node('#fp-touch').hidden,true);
  assert.equal(actual.node('[data-action="claim"]').disabled,true);
});
test('heartbeat freshness and real touch input are distinct from synthetic equipment values',async()=>{
  const state={status:'fault',board_online:true,heartbeat_age_s:1.2,touch:{swing:123,base:12,touched:true},
    task:{tag:'P-005',signal:{name:'temperature',now:72,normal:60,now_pct:70,normal_pct:40,sigma:2}}};
  const actual=await boot({public_demo:true,arduino_enabled:true},{state});
  assert.equal(actual.node('#fp-source').hidden,false);
  assert.match(actual.node('#fp-meta').textContent,/Board online · last heartbeat 1 s ago/);
  assert.equal(actual.node('#fp-touch').textContent,'Live A0 input · swing 123 ADC · baseline 12 ADC · touched');
  assert.equal(touchText({...state,board_online:false,heartbeat_age_s:9}), '');
  assert.equal(heartbeatText({board_online:false,heartbeat_age_s:null}),'Board offline · no heartbeat received');
});
test('public banner explains shared job persistence without changing scene-edit scope',()=>{
  let banner;
  const previous=globalThis.document;
  globalThis.document={getElementById:()=>null,createElement:()=>({}),querySelector:()=>({after:el=>{banner=el;}})};
  try {
    mountDemoBanner({public_demo:true,field_jobs_enabled:true});
    assert.match(banner.innerHTML,/jobs are shared; scene edits stay in this browser/);
  } finally { globalThis.document=previous; }
});
