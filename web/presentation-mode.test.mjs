import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import {createInformationPreference, INFORMATION_MODE_KEY, inspectionAction, simpleFrameVisible, mountInformationSettings} from './presentation-mode.mjs';

test('Simple is first-visit default; switching preserves unrelated browser scene and job records',()=>{
  const saved=new Map([['room-edits','existing edits'],['job-note','unfinished note']]);
  const writes=[];
  const storage={getItem:k=>saved.get(k),setItem:(k,v)=>{writes.push(k);saved.set(k,v);}};
  const pref=createInformationPreference(storage);
  assert.equal(pref.mode,'simple'); assert.equal(writes.length,0);
  pref.set('extended'); assert.equal(createInformationPreference(storage).mode,'extended');
  pref.set('simple'); assert.equal(createInformationPreference(storage).mode,'simple');
  assert.deepEqual(writes,[INFORMATION_MODE_KEY,INFORMATION_MODE_KEY]);
  assert.equal(saved.get('room-edits'),'existing edits'); assert.equal(saved.get('job-note'),'unfinished note');
});
test('Corrupt or blocked preference storage safely defaults and remains usable',()=>{
  assert.equal(createInformationPreference({getItem:()=>'{broken'}).mode,'simple');
  const pref=createInformationPreference({getItem(){throw Error();},setItem(){throw Error();}});
  assert.equal(pref.set('extended'),'extended'); assert.equal(pref.set(null),'simple');
});
test('Simple inspection follows the selected object, while Extended keeps the labeled faulty-device action',()=>{
  const target={id:'OBJ-206',tag:'CV-003',label:'Control valve',mapped:true};
  const selected={id:'OBJ-411',tag:'M-005',label:'Motor',mapped:true};
  const before=JSON.stringify([selected,target]);
  assert.equal(inspectionAction(selected,target,true).asset,selected);
  assert.equal(inspectionAction(selected,target,true).destination,'M-005 · Motor');
  assert.equal(inspectionAction(selected,target,true).label,'Start inspection');
  assert.equal(inspectionAction(selected,target,false).asset,target);
  assert.equal(inspectionAction(null,target,true).asset,target);
  assert.equal(inspectionAction({label:'Crate',mapped:false},target,true).label,'Go to item');
  assert.equal(JSON.stringify([selected,target]),before);
});
test('Simple frames focus the selection; optional nearby context never reveals every candidate',()=>{
  assert.equal(simpleFrameVisible({selected:true}),true);
  assert.equal(simpleFrameVisible({near:true,mapped:true}),false);
  assert.equal(simpleFrameVisible({near:true,mapped:true,showContext:true}),true);
  assert.equal(simpleFrameVisible({near:true,mapped:false,showContext:true}),false);
  assert.equal(simpleFrameVisible({near:false,mapped:true,showContext:true}),false);
});

test('Settings interaction changes only preference/presentation and restores focus, without network actions',()=>{
  class Element {
    constructor(){this.listeners={};this.attrs={};this.classList={toggle(){}};this.lastChild={};this.dataset={};}
    addEventListener(type,fn){this.listeners[type]=fn;}
    setAttribute(k,v){this.attrs[k]=v;}
    focus(){doc.activeElement=this;}
    showModal(){this.open=true;}
    close(){this.open=false;this.listeners.close?.();}
  }
  const elements=new Map(),get=id=>elements.get(id)||elements.set(id,new Element()).get(id);
  const radios=['simple','extended'].map(value=>Object.assign(new Element(),{value,name:'information-mode'}));
  const dialog=get('information-settings');dialog.querySelectorAll=()=>radios;dialog.querySelector=()=>radios.find(r=>r.checked);
  const win=new Element(),doc={getElementById:get,documentElement:{dataset:{}},defaultView:win};
  const changes=[],writes=[];
  const app={selected:'M-005',phase:'moving',route:[1,2,3],note:'Draft observation'};
  const before=JSON.stringify(app);
  const settings=mountInformationSettings({document:doc,storage:{getItem:()=>null,setItem:(...args)=>writes.push(args)},onChange:change=>changes.push(change)});
  get('btn-settings').listeners.click();
  assert.equal(dialog.open,true);assert.equal(doc.activeElement,radios[0]);
  dialog.listeners.change({target:radios[1]});assert.equal(settings.simple,false);
  let prevented=false,stopped=false;
  win.listeners.keydown({key:'Escape',preventDefault(){prevented=true;},stopImmediatePropagation(){stopped=true;}});
  assert.equal(dialog.open,false);assert.equal(doc.activeElement,get('btn-settings'));
  assert.equal(prevented&&stopped,true);assert.equal(JSON.stringify(app),before);
  assert.deepEqual(writes,[[INFORMATION_MODE_KEY,'extended']]);
  assert.equal(changes.length,2);
});

test('Removed-device lookup restores the existing search and points to reachable controls in both modes',async()=>{
  const source=fs.readFileSync(new URL('./field-jobs.mjs',import.meta.url),'utf8').replace(/^import .*;\n/m,'').replace(/export /g,'');
  for(const mode of ['simple','extended']) {
    const search={value:'pump',dispatchEvent(){}},filter={value:'mapped',dispatchEvent(){}};
    const context={CSS:{escape:x=>x},Event:class{},
      document:{documentElement:{dataset:{informationMode:mode}},getElementById:id=>id==='asset-search'?search:filter,querySelector:()=>null},
      fetch:async()=>({ok:true,json:async()=>({tag:'P-001',object_id:'OBJ-008',label:'Pump'})})};
    vm.runInNewContext(source+'\nglobalThis.showRemoved=showEquipment;',context);
    await assert.rejects(context.showRemoved('P-001'),mode==='simple'?/Settings → Extended, then Room changes/:/Restore it from Room changes/);
    assert.equal(search.value,'pump');assert.equal(filter.value,'mapped');
  }
});
