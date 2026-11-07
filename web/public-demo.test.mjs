import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";
import {roomEditStore} from "./room-edits.mjs";
import {canWriteSharedData, installUrl, installAction, sharedWrite} from "./runtime-access.mjs";

const runtime={public_demo:true,install_url:"https://example.test/install/"};

test("public removals survive reload and restore without reading or writing shared room state",async()=>{
  const oldFetch=globalThis.fetch,oldStorage=globalThis.localStorage,calls=[];
  const installedKey="field-atlas:equipment-room:edits:scene-1";
  const installed=JSON.stringify({removed:[{id:"original"}],pending:[{id:"original",action:"remove"}]});
  const storage=new Map([[installedKey,installed]]);
  globalThis.localStorage={getItem:key=>storage.get(key),setItem:(key,value)=>storage.set(key,value)};
  globalThis.fetch=async(...args)=>{calls.push(args);throw new Error("network must not be called");};
  try {
    const store=roomEditStore(()=>true,"scene-1",{localOnly:true});
    assert.deepEqual(await store.load(),{removed:[],offline:false});
    assert.equal((await store.edit("remove","OBJ-1")).removed[0].id,"OBJ-1");
    const reloaded=roomEditStore(()=>true,"scene-1",{localOnly:true});
    assert.equal((await reloaded.load()).removed[0].id,"OBJ-1");
    assert.deepEqual(await reloaded.edit("restore","OBJ-1"),{removed:[],offline:false});
    assert.equal(storage.get(installedKey),installed,"do not consume or modify installed pending writes");
    assert.deepEqual(JSON.parse(storage.get(`${installedKey}:public-demo`)).pending,[]);
    assert.deepEqual(calls,[]);
  } finally {globalThis.fetch=oldFetch;globalThis.localStorage=oldStorage;}
});

test("installed room edit store keeps its original server persistence",async()=>{
  const oldFetch=globalThis.fetch,oldStorage=globalThis.localStorage,calls=[];
  globalThis.localStorage={getItem:()=>null,setItem(){}};
  globalThis.fetch=async(url,options)=>{
    calls.push({url,options});
    return {ok:true,status:200,headers:{get:()=>"application/json"},json:async()=>({removed:options.method?[{id:"OBJ-1"}]:[]})};
  };
  try {
    const store=roomEditStore(()=>true,"installed");
    await store.load();await store.edit("remove","OBJ-1");
    assert.equal(calls.length,2);
    assert.equal(calls[1].options.method,"POST");
    assert.equal(JSON.parse(calls[1].options.body).action,"remove");
  } finally {globalThis.fetch=oldFetch;globalThis.localStorage=oldStorage;}
});

test("shared write boundary rejects both library methods before fetch but preserves installed capabilities",async()=>{
  const calls=[],fetcher=async(...args)=>{calls.push(args);return {ok:true};};
  for(const method of ["POST","DELETE"]) {
    await assert.rejects(sharedWrite(runtime,"./api/library",{method},fetcher),{code:"public_demo_read_only"});
  }
  assert.equal(calls.length,0);
  assert.equal(canWriteSharedData({offline:true}),true,"offline installations still save locally on their own server");
  assert.equal(canWriteSharedData(undefined),true,"unmarked upstream apps retain writes");
  await sharedWrite(undefined,"./api/library",{method:"POST"},fetcher);
  assert.equal(calls.length,1);
});

test("real field job API rejects public mutations while keeping the inspection queue readable",async()=>{
  const source=fs.readFileSync(new URL("./field-jobs.mjs",import.meta.url),"utf8")
    .replace(/^import .*;\n/m,"").replace(/export /g,"");
  const calls=[],context={canWriteSharedData,installAction,sharedWrite:(config,url,options)=>sharedWrite(config,url,options,context.fetch),
    fetch:async(url,options)=>{calls.push({url,options});return {ok:true,json:async()=>({jobs:{}})};}};
  vm.runInNewContext(`${source}\nruntime = ${JSON.stringify(runtime)}; globalThis.callApi=api;`,context);
  await assert.rejects(context.callApi("/api/field-jobs",{action:"start_job"}),{code:"public_demo_read_only"});
  await context.callApi("/api/field-jobs");
  assert.equal(calls.length,1);assert.equal(calls[0].options,undefined);
});

async function takeApartHarness(config, saved=[]) {
  const source=fs.readFileSync(new URL("./take-apart.mjs",import.meta.url),"utf8")
    .replace(/^import .*;\n/gm,"").replace("export function createTakeApart","function createTakeApart");
  const calls=[],listeners={},card={hidden:true,innerHTML:"",setAttribute(){},addEventListener:(event,fn)=>listeners[event]=fn};
  const vector=()=>({x:1,y:1,z:1,clone(){return vector();},set(){},copy(){}});
  const asset={id:"OBJ-1",label:"Pump",center:[0,0,0],size:[1,1,1],n_gaussians:30};
  const context={canWriteSharedData,installAction,sharedWrite,console,
    document:{createElement:()=>card,body:{classList:{add(){},remove(){}}}},window:{addEventListener(){}},
    fetch:async(url,options)=>{calls.push({url,options});return {ok:true,json:async()=>({saved,saving:{}})};},
    loadGspl:async()=>{throw new Error("inert model renderer");},setTimeout(){},
    ctx:{state:{twin:{runtime:config,assets:[asset]},exactMembership:true},container:{appendChild(){}},
      camera:{position:vector(),fov:60,aspect:1},controls:{target:vector(),update(){}},uniforms:()=>null,
      setPreset(){},scene:{remove(){}}},asset};
  context.sharedWrite=(config,url,options)=>sharedWrite(config,url,options,context.fetch);
  vm.runInNewContext(`${source}\nglobalThis.apart=createTakeApart(ctx); apart.open(asset);`,context);
  await new Promise(resolve=>setImmediate(resolve));
  return {calls,card,clickSave:async()=>{listeners.click({target:{closest:()=>({dataset:{act:"save"}})}});await new Promise(resolve=>setImmediate(resolve));}};
}

test("public take-apart offers install instead of export and even an injected Save event cannot post",async()=>{
  const h=await takeApartHarness(runtime);
  assert.match(h.card.innerHTML,/Install Scan2Work/);
  assert.doesNotMatch(h.card.innerHTML,/data-act="save"/);
  await h.clickSave();
  assert.equal(h.calls.filter(call=>call.options?.method).length,0);
});

test("public take-apart retains available source downloads and installed take-apart still exports",async()=>{
  const saved=[{id:"OBJ-1",splats:30,membership_mode:"exact-source-rows",files:{gspl:"OBJ-1/model.gspl",ply:"OBJ-1/source.ply",source_rows:"OBJ-1/rows.npy"}}];
  const publicView=await takeApartHarness(runtime,saved);
  assert.match(publicView.card.innerHTML,/href="\.\/data\/library\/OBJ-1\/source.ply" download/);
  assert.match(publicView.card.innerHTML,/Source row IDs/);
  const installed=await takeApartHarness(undefined);
  assert.match(installed.card.innerHTML,/data-act="save"/);
  await installed.clickSave();
  assert.equal(installed.calls.filter(call=>call.options?.method==="POST").length,1);
});

test("install links use configured safe URLs without guessing a download endpoint",()=>{
  assert.equal(installUrl(runtime),"https://example.test/install/");
  assert.equal(installUrl({install_url:"/install"},"https://example.test/demo/"),"https://example.test/install");
  for(const value of [undefined,null,"","javascript:alert(1)","file:///tmp/install"])assert.equal(installUrl({install_url:value}),null);
  assert.doesNotMatch(installAction({}),/<a /);
});

test("AR discovery works without directory listings and only probes optional video headers",async()=>{
  const app=fs.readFileSync(new URL("./app.js",import.meta.url),"utf8");
  const source=app.slice(app.indexOf("async function bindArView()"),app.indexOf("\ninit().catch"));
  for(const available of [true,false]) {
    const calls=[],button={hidden:true,addEventListener(){}},video={addEventListener(){}};
    const dialog={querySelector:()=>video,addEventListener(){}},control={addEventListener(){}};
    const context={state:{assetsById:new Map(),twin:{}},
      document:{getElementById:id=>id==="btn-ar-view"?button:id==="ar-view"?dialog:control},
      fetch:async(url,options)=>{calls.push({url,options});return {ok:available};}};
    vm.runInNewContext(`${source}\nglobalThis.ready=bindArView();`,context);
    await context.ready;
    assert.equal(button.hidden,!available);
    assert.equal(calls.length,1);
    assert.equal(calls[0].url,"./data/overlay_walk.mp4");
    assert.equal(calls[0].options.method,"HEAD");
  }
});
