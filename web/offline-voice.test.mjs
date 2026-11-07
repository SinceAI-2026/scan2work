import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";
import { voiceConfig } from "./voice-config.mjs";
import { canWriteSharedData, canWriteFieldJobs } from "./runtime-access.mjs";

// Exercise the real bootstrap with an inert DOM: no widget, browser or network is launched.
const source=fs.readFileSync(new URL("./voice-assistant.mjs",import.meta.url),"utf8")
  .replace(/^import .*;\n/gm,"")
  .replace(/if \(document\.readyState === "loading"\)[\s\S]*$/, "globalThis.mountDone=mount();");
const enabled={enabled:true,provider:"elevenlabs",agent_id:"agent_verified_demo",agent_name:"Ilkka",service_mode:"read_only"};
async function boot(runtime, { title="control valve · CV-003", hidden=false }={}) {
  const fetched=[],created=[],appended=[],timers=[],events=new Map(),shown=[];
  const titleNode={textContent:title};let jobs=0,jobRuntime;
  const context={
    voiceConfig,
    fetch:async url=>{fetched.push(url);return {json:async()=>({runtime,assets:[{mapped:true,tag:"CV-003"},{id:"OBJ-004",mapped:false,tag:"OLD-001"}]})};},
    mountFieldJobs:(_tag,value)=>{jobs++;jobRuntime=value;},
    showEquipment:async equipment=>{shown.push(equipment);return {tag:equipment,label:"Valve",area:"Boiler room"};},
    MutationObserver:class { observe(){} },
    setTimeout:callback=>{timers.push(callback);return timers.length;},clearTimeout(){},
    document:{getElementById:id=>id==="detail-title"?titleNode:id==="detail-health-pill"?{hidden}:null,createElement:tag=>{
      const element={tag,attributes:{},setAttribute(key,value){this.attributes[key]=value;},remove(){this.removed=true;}};
      created.push(element);return element;
    },body:{appendChild:el=>appended.push(el)},head:{appendChild:el=>appended.push(el)}},
    window:{addEventListener:(name,callback)=>events.set(name,callback)},console,
  };
  vm.runInNewContext(source,context,{filename:"voice-assistant.mjs"});
  await context.mountDone;
  return {fetched,created,appended,jobs,jobRuntime,timers,events,shown,titleNode,context};
}
test("offline/public packages mount local field jobs without external widget requests by default",async()=>{
  for(const runtime of [{offline:true},{external_models_enabled:false},{public_demo:true},{voice:{enabled:false}},
    {public_demo:true,offline:true,external_models_enabled:false,shared_writes_enabled:false,voice:{...enabled,service_mode:"write"}},
    {public_demo:true,voice:{...enabled,agent_id:"https://wrong"}},
    {public_demo:true,voice:{...enabled,agent_name:""}},
    {public_demo:true,voice:{...enabled,provider:"other"}},
    {public_demo:true,voice:{...enabled,enabled:"true"}}]) {
    const result=await boot(runtime);
    assert.deepEqual(result.fetched,["./data/digital_twin.json"]);
    assert.equal(result.jobs,1);
    assert.equal(result.created.length,0);
    assert.equal(result.appended.length,0);
  }
});
test("explicit validated read-only voice can load without enabling backend inference or shared writes",async()=>{
  const runtime={public_demo:true,offline:true,external_models_enabled:false,shared_writes_enabled:false,voice:enabled};
  const original=JSON.stringify(runtime),result=await boot(runtime);
  assert.deepEqual(result.created.map(el=>el.tag),["elevenlabs-convai","script"]);
  assert.equal(result.created[0].attributes['agent-id'],enabled.agent_id);
  assert.equal(result.created[0].attributes['action-text'],"Call Ilkka for support");
  assert.equal(result.created[0].attributes['dynamic-variables'],'{"selected_equipment":"CV-003"}');
  assert.equal(JSON.stringify(runtime),original);
  assert.equal(result.jobRuntime,runtime);
  assert.equal(canWriteSharedData(result.jobRuntime),false);
  assert.equal(result.jobs,1);
});
test("unmarked online scenes retain the optional legacy voice widget",async()=>{
  const result=await boot(undefined);
  assert.equal(result.jobs,1);
  assert.deepEqual(result.created.map(el=>el.tag),["elevenlabs-convai","script"]);
  assert.equal(result.created[0].attributes['agent-id'],"agent_5501m6v02k69e2r8fwc0hznmfda5");
  assert.equal(result.appended[1].src,"https://unpkg.com/@elevenlabs/convai-widget-embed@0.21.4/dist/index.js");
});
test("Wael's assistant opt-in preserves its original widget and independent shared job mode",async()=>{
  for (const field_jobs_enabled of [false, true]) {
    const runtime={public_demo:true,offline:true,external_models_enabled:false,voice_assistant:true,field_jobs_enabled};
    const result=await boot(runtime);
    assert.equal(result.created[0].tag,"elevenlabs-convai");
    assert.equal(result.created[0].attributes['agent-id'],"agent_5501m6v02k69e2r8fwc0hznmfda5");
    assert.equal(canWriteFieldJobs(result.jobRuntime),field_jobs_enabled);
    assert.equal(canWriteSharedData(result.jobRuntime),false);
    assert.equal(voiceConfig(runtime).serviceMode,field_jobs_enabled ? "shared_jobs" : "read_only");
  }
  const off=await boot({public_demo:true,offline:true,external_models_enabled:false,voice_assistant:false});
  assert.equal(off.created.length,0);
});
test("explicit voice configuration overrides legacy opt-in without granting job or scene access",async()=>{
  const runtime={public_demo:true,voice_assistant:true,field_jobs_enabled:true,voice:{...enabled,service_mode:"shared_jobs"}};
  const result=await boot(runtime);
  assert.equal(result.created[0].attributes['agent-id'],enabled.agent_id);
  assert.equal(voiceConfig(runtime).serviceMode,"shared_jobs");
  assert.equal(canWriteFieldJobs(result.jobRuntime),true);
  assert.equal(canWriteSharedData(result.jobRuntime),false);
  for(const override of [{voice:{enabled:false}},{voice:{...enabled,agent_id:"invalid"}},
    {field_jobs_enabled:false},{voice:{...enabled,service_mode:"write"}}]) {
    const off=await boot({...runtime,...override});
    assert.equal(off.created.length,0);
  }
});
test("async widget failure or registration timeout shows a visible local fallback",async()=>{
  for(const mode of ["error","timeout"]) {
    const result=await boot({public_demo:true,voice:enabled});
    if(mode==="error")result.created[1].onerror();else result.timers[0]();
    assert.equal(result.created[0].removed,true);
    const status=result.appended.at(-1);
    assert.equal(status.attributes.role,"status");
    assert.match(status.textContent,/Voice unavailable.*workbook guidance/);
    result.created[1].onerror();
    assert.equal(result.appended.length,3,"failure notice is not duplicated");
  }
});
test("selected context excludes unmapped records and client tool only delegates local equipment display",async()=>{
  for(const options of [{title:"Cart · OLD-001"},{title:"Valve · CV-003",hidden:true}]) {
    const result=await boot({public_demo:true,voice:enabled},options);
    assert.equal(result.created[0].attributes['dynamic-variables'],'{"selected_equipment":"none"}');
  }
  const result=await boot({public_demo:true,voice:enabled});
  const event={detail:{config:{}}};result.events.get('elevenlabs-convai:call')(event);
  assert.deepEqual(Object.keys(event.detail.config.clientTools),['show_equipment']);
  assert.match(await event.detail.config.clientTools.show_equipment({equipment:'CV-003'}),/Now showing CV-003/);
  assert.deepEqual(result.shown,['CV-003']);
  assert.deepEqual(result.fetched,['./data/digital_twin.json']);
});
