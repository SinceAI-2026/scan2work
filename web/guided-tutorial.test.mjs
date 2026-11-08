import test from 'node:test';
import assert from 'node:assert/strict';
import {TUTORIAL_KEY,TUTORIAL_STEPS,createTutorialPreference,tutorialSuppressed,visibleTutorialTarget} from './guided-tutorial.mjs';
const storage=()=>{const entries=new Map();return {entries,getItem:k=>entries.get(k)||null,setItem:(k,v)=>entries.set(k,v)};};
test('first visit is optional and dismissing is remembered without unrelated preference writes',()=>{
 const s=storage();s.setItem('other','untouched');const p=createTutorialPreference(s);assert.equal(p.shouldShow,true);p.dismiss();assert.equal(p.shouldShow,false);assert.equal(createTutorialPreference(s).shouldShow,false);assert.equal(s.getItem('other'),'untouched');assert.deepEqual([...s.entries.keys()].sort(),[TUTORIAL_KEY,'other'].sort());
});
test('dont-show-again persists; explicit Settings opt-in offers welcome once on a future visit',()=>{
 const s=storage(),p=createTutorialPreference(s);p.dismiss(true);assert.equal(JSON.parse(s.getItem(TUTORIAL_KEY)).autoShow,false);p.offerNextVisit(true);assert.equal(createTutorialPreference(s).shouldShow,true);p.dismiss();assert.equal(createTutorialPreference(s).shouldShow,false);p.offerNextVisit(false);assert.equal(p.shouldShow,false);
});
test('storage unavailable or corrupt is a safe session preference',()=>{
 const s={getItem(){throw Error('blocked')},setItem(){throw Error('blocked')}};const p=createTutorialPreference(s);assert.equal(p.shouldShow,true);assert.doesNotThrow(()=>p.dismiss(true));assert.equal(p.shouldShow,false);assert.equal(createTutorialPreference({getItem:()=>'{'}).shouldShow,true);
});
test('capture suppression and both deep links bypass welcome without writing seen',()=>{
 for(const url of ['/?tutorial=0','/?attract=0&tutorial=0','#clone=OBJ-008','#apart=OBJ-008'])assert.equal(tutorialSuppressed(url),true,url);
 for(const url of ['/?tutorial=1','/?attract=0','#something-else'])assert.equal(tutorialSuppressed(url),false,url);
 const s=storage();tutorialSuppressed('/?tutorial=0');assert.equal(s.entries.size,0);
});
test('tour explains actual control capabilities without actions, and includes the major workflows',()=>{
 assert.equal(new Set(TUTORIAL_STEPS.map(s=>s.id)).size,TUTORIAL_STEPS.length);
 for(const step of TUTORIAL_STEPS){assert.ok(step.target);assert.ok(step.title);assert.ok(step.text);assert.equal(Object.values(step).some(v=>typeof v==='function'),false);}
 for(const id of ['equipment','navigate','tour','camera','map','details','service','diagnostics','library','settings'])assert.ok(TUTORIAL_STEPS.some(s=>s.id===id));
 assert.match(TUTORIAL_STEPS.find(s=>s.id==='library').text,/Place or Cancel/);
});
test('spotlights skip hidden, collapsed and unavailable elements',()=>{
 const old=globalThis.getComputedStyle;globalThis.getComputedStyle=el=>({visibility:el.visibility||'visible'});
 try{
 const element=(width,hidden=false,visibility)=>({getBoundingClientRect:()=>({width,height:30}),closest:()=>hidden,visibility});
 const hidden=element(10,true),collapsed=element(0),invisible=element(20,false,'hidden'),visible=element(80);
 const doc={querySelectorAll:()=>[hidden,collapsed,invisible,visible]};assert.equal(visibleTutorialTarget(doc,{target:'button'}),visible);
 assert.equal(visibleTutorialTarget({querySelectorAll:()=>[hidden,collapsed,invisible]},{target:'button'}),null);
 }finally{globalThis.getComputedStyle=old;}
});
