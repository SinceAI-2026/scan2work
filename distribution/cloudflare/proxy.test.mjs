import test from 'node:test';
import assert from 'node:assert/strict';
import worker from './proxy.mjs';
const env = {ORIGIN:'http://192.0.2.10:20229'};
test('streams ranges to a fixed origin and drops visitor credentials',async()=>{
 const original=globalThis.fetch;
 let got;
 globalThis.fetch=async(url,init)=>{got={url:String(url),init};return new Response('xy',{status:206,headers:{'Content-Range':'bytes 0-1/20'}});};
 try {
  const out=await worker.fetch(new Request('https://scan2work.app/data/clip.mp4?x=1',{headers:{Range:'bytes=0-1',Cookie:'private',Authorization:'private'}}),env);
  assert.equal(out.status,206);assert.equal(await out.text(),'xy');
  assert.equal(got.url,'http://192.0.2.10:20229/data/clip.mp4?x=1');
  assert.equal(got.init.headers.get('range'),'bytes=0-1');assert.equal(got.init.headers.get('cookie'),null);assert.equal(got.init.headers.get('authorization'),null);
 }finally{globalThis.fetch=original;}
});
test('rejects shared writes and oversized copilot requests before the origin',async()=>{
 assert.equal((await worker.fetch(new Request('https://scan2work.app/api/library',{method:'POST'}),env)).status,403);
 assert.equal((await worker.fetch(new Request('https://scan2work.app/api/copilot/diagnose',{method:'POST',body:'x'.repeat(16385)}),env)).status,413);
});
test('routes /mcp to the MCP origin with its bearer token, only when configured',async()=>{
 const original=globalThis.fetch;
 let got;
 globalThis.fetch=async(url,init)=>{got={url:String(url),init};return Response.json({jsonrpc:'2.0',id:1,result:{}});};
 try {
  const call=e=>worker.fetch(new Request('https://scan2work.app/mcp',{method:'POST',headers:{Authorization:'Bearer t',
   'Content-Type':'application/json','Mcp-Session-Id':'s1',Cookie:'private'},body:'{"jsonrpc":"2.0","id":1,"method":"ping"}'}),e);
  assert.equal((await call(env)).status,503);   // MCP requires an explicitly configured origin
  const out=await call({...env,MCP_ORIGIN:'https://mcp.example'});
  assert.equal(out.status,200);
  assert.equal(got.url,'https://mcp.example/mcp');
  assert.equal(got.init.headers.get('authorization'),'Bearer t');
  assert.equal(got.init.headers.get('mcp-session-id'),'s1');
  assert.equal(got.init.headers.get('cookie'),null);
  assert.ok(got.init.headers.get('x-forwarded-for'));   // so mcp_server.py demands the token
 }finally{globalThis.fetch=original;}
});
test('field-job writes pass only with ASSISTANT=1, and other writes stay blocked',async()=>{
 const original=globalThis.fetch;
 globalThis.fetch=async()=>Response.json({ok:true});
 try {
  const post=(path,e)=>worker.fetch(new Request('https://scan2work.app'+path,{method:'POST',body:'{}'}),e);
  assert.equal((await post('/api/field-jobs',env)).status,403);
  assert.equal((await post('/api/field-jobs',{...env,ASSISTANT:'1'})).status,200);
  assert.equal((await post('/api/library',{...env,ASSISTANT:'1'})).status,403);
  assert.equal((await post('/api/scene/edits',{...env,ASSISTANT:'1'})).status,403);
 }finally{globalThis.fetch=original;}
});

test('MCP requires a configured HTTPS origin and a bearer token before forwarding',async()=>{
 const original=globalThis.fetch;let calls=0;
 globalThis.fetch=async()=>{calls++;return new Response('unexpected');};
 try {
  const make=()=>new Request('https://scan2work.app/mcp',{method:'POST',headers:{Authorization:'Bearer private'},body:'{}'});
  assert.equal((await worker.fetch(make(),env)).status,503);
  assert.equal((await worker.fetch(make(),{...env,MCP_ORIGIN:'http://insecure.example'})).status,503);
  assert.equal((await worker.fetch(new Request('https://scan2work.app/mcp',{method:'POST',body:'{}'}),{...env,MCP_ORIGIN:'https://mcp.example'})).status,401);
  assert.equal(calls,0);
 }finally{globalThis.fetch=original;}
});
test('MCP credentials go only to the fixed HTTPS MCP origin; redirects are never followed',async()=>{
 const original=globalThis.fetch;let got;
 globalThis.fetch=async(url,init)=>{got={url:String(url),init};return Response.json({jsonrpc:'2.0',id:1,result:{}});};
 try {
  const out=await worker.fetch(new Request('https://scan2work.app/mcp?ignored=1',{method:'POST',headers:{Authorization:'Bearer private',Cookie:'visitor','MCP-Protocol-Version':'2025-03-26'},body:'{"id":1}'}),{...env,MCP_ORIGIN:'https://mcp.example'});
  assert.equal(out.status,200);assert.equal(got.url,'https://mcp.example/mcp');
  assert.equal(got.init.headers.get('authorization'),'Bearer private');assert.equal(got.init.headers.get('cookie'),null);
  assert.equal(got.init.headers.get('mcp-protocol-version'),'2025-03-26');assert.equal(got.init.redirect,'manual');
  assert.equal((await worker.fetch(new Request('https://scan2work.app/mcp',{method:'POST',headers:{Authorization:'Bearer private'},body:'x'.repeat(16385)}),{...env,MCP_ORIGIN:'https://mcp.example'})).status,413);
 }finally{globalThis.fetch=original;}
});

test('Arduino routes require opt-in, an encrypted origin and an exact endpoint',async()=>{
 const original=globalThis.fetch;let calls=0;
 globalThis.fetch=async()=>{calls++;return Response.json({});};
 try {
  const get=(path,e)=>worker.fetch(new Request('https://scan2work.app'+path),e);
  assert.equal((await get('/api/live/board',env)).status,403);
  assert.equal((await get('/api/live/board',{...env,ARDUINO:'1'})).status,503);
  assert.equal((await get('/api/live/board',{...env,ARDUINO:'1',ARDUINO_ORIGIN:'http://board.example'})).status,503);
  assert.equal((await get('/api/live/unknown',{...env,ARDUINO:'1',ARDUINO_ORIGIN:'https://board.example'})).status,404);
  assert.equal(calls,0);
 }finally{globalThis.fetch=original;}
});

test('board credentials go only to the dedicated HTTPS claim and heartbeat routes',async()=>{
 const original=globalThis.fetch;let got;
 globalThis.fetch=async(url,init)=>{got={url:String(url),init};return Response.json({ok:true});};
 const enabled={...env,ARDUINO:'1',ARDUINO_ORIGIN:'https://board.example'};
 const post=(path,headers={})=>worker.fetch(new Request('https://scan2work.app'+path,{method:'POST',headers,body:'{}'}),enabled);
 try {
  assert.equal((await post('/api/live/board/heartbeat')).status,401);
  for(const path of ['/api/live/board/claim','/api/live/board/heartbeat']) {
   assert.equal((await post(path+'?token=never-forward',{'X-Live-Token':'board-secret',Authorization:'Bearer mcp-secret',Cookie:'visitor'})).status,200);
   assert.equal(got.url,'https://board.example'+path);
   assert.equal(got.init.headers.get('x-live-token'),'board-secret');
   assert.equal(got.init.headers.get('authorization'),null);
   assert.equal(got.init.headers.get('cookie'),null);
   assert.equal(got.init.redirect,'manual');
  }
  assert.equal((await post('/api/live/board/action',{'X-Live-Token':'board-secret'})).status,200);
  assert.equal(got.init.headers.get('x-live-token'),null);
  assert.equal((await post('/api/library')).status,403);
  const out=await worker.fetch(new Request('https://scan2work.app/data/photo.jpg',{headers:{'X-Live-Token':'board-secret'}}),enabled);
  assert.equal(out.status,200);assert.equal(got.init.headers.get('x-live-token'),null);
 }finally{globalThis.fetch=original;}
});

test('Arduino body limits apply before contacting the board service',async()=>{
 const original=globalThis.fetch;let calls=0;
 globalThis.fetch=async()=>{calls++;return new Response('unexpected');};
 try {
  const out=await worker.fetch(new Request('https://scan2work.app/api/live/board/heartbeat',{method:'POST',headers:{'X-Live-Token':'secret'},body:'x'.repeat(16385)}),{...env,ARDUINO:'1',ARDUINO_ORIGIN:'https://board.example'});
  assert.equal(out.status,413);assert.equal(calls,0);
 }finally{globalThis.fetch=original;}
});
