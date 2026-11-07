import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import {canWriteSharedData, sharedWrite} from './runtime-access.mjs';

const source=fs.readFileSync(new URL('./fault-panel.mjs',import.meta.url),'utf8')
  .replace(/^import .*;\n/m,'').replace(/mount\(\);\s*$/,'globalThis.done=mount();');
async function boot(runtime,{manifestOK=true}={}) {
  const requests=[],created=[],nodes=new Map(),listeners=new Map();
  function node(key) {
    if(!nodes.has(key)) nodes.set(key,{dataset:{},style:{},setAttribute(){},focus(){},after(){},contains(){return false;},
      querySelector:selector=>node(selector),addEventListener:(event,fn)=>listeners.set(`${key}:${event}`,fn)});
    return nodes.get(key);
  }
  const fetcher=async(url,options)=>{
    requests.push({url,method:options?.method??'GET'});
    return {ok:url==='./data/digital_twin.json'?manifestOK:true,json:async()=>url==='./data/digital_twin.json'?{runtime}:{status:'waiting'}};
  };
  const context={fetch:fetcher,canWriteSharedData,sharedWrite:(r,u,o)=>sharedWrite(r,u,o,fetcher),
    setTimeout(){},CustomEvent:class{},window:{dispatchEvent(){}},
    document:{createElement:tag=>{created.push(tag);return node('host');},getElementById:id=>node(id),querySelector:node,addEventListener(){}},console};
  vm.runInNewContext(source,context);await context.done;
  return {requests,created,click:listeners.get('#fault-panel-card:click')};
}
test('public demo never mounts or polls the hardware panel',async()=>{
  for(const runtime of [{public_demo:true},{shared_writes_enabled:false}]) {
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
    await actual.click({target:{closest:()=>({dataset:{action:'ack'}})}});
    assert.deepEqual(actual.requests[2],{url:'/api/live/board/action',method:'POST'});
  }
});
