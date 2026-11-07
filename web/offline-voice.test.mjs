import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";

// Exercise the real bootstrap with an inert DOM: no widget, browser or network is launched.
const source=fs.readFileSync(new URL("./voice-assistant.mjs",import.meta.url),"utf8")
  .replace(/^import .* from "\.\/field-jobs\.mjs";\n/m,"")
  .replace(/if \(document\.readyState === "loading"\)[\s\S]*$/, "globalThis.mountDone=mount();");
async function boot(runtime) {
  const fetched=[],created=[],appended=[];let jobs=0;
  const context={
    fetch:async url=>{fetched.push(url);return {json:async()=>({runtime,assets:[{mapped:true,tag:"CV-003"}]})};},
    mountFieldJobs:()=>jobs++,showEquipment:()=>{},
    document:{getElementById:()=>null,createElement:tag=>{
      const element={tag,setAttribute(){}};created.push(element);return element;
    },body:{appendChild:el=>appended.push(el)},head:{appendChild:el=>appended.push(el)}},
    window:{addEventListener(){}},console,
  };
  vm.runInNewContext(source,context,{filename:"voice-assistant.mjs"});
  await context.mountDone;
  return {fetched,created,appended,jobs};
}
test("offline packages mount local field jobs without external widget requests",async()=>{
  for(const runtime of [{offline:true},{external_models_enabled:false},{public_demo:true}]) {
    const result=await boot(runtime);
    assert.deepEqual(result.fetched,["./data/digital_twin.json"]);
    assert.equal(result.jobs,1);
    assert.equal(result.created.length,0);
    assert.equal(result.appended.length,0);
  }
});
test("unmarked online scenes retain the optional voice widget",async()=>{
  const result=await boot(undefined);
  assert.equal(result.jobs,1);
  assert.deepEqual(result.created.map(el=>el.tag),["elevenlabs-convai","script"]);
  assert.equal(result.appended[1].src,"https://unpkg.com/@elevenlabs/convai-widget-embed@0.21.4/dist/index.js");
});
