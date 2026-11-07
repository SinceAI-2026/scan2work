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
