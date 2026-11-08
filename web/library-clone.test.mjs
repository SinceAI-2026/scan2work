import test from 'node:test';
import assert from 'node:assert/strict';
import {parseLibraryCloneHash, libraryCloneIssue} from './library-clone.mjs';
import {roomCopyAsset, nextCopyId, saveRoomCopies, loadRoomCopies, saveRoomPlacements, loadRoomPlacements} from './room-placement.mjs';

const source={id:'OBJ-008',label:'Pump',center:[0,1,0],size:[.3,.5,.3],yaw:0,
  aabb_min:[-.15,.75,-.15],aabb_max:[.15,1.25,.15],mapped:true,is_target:true,
  tag:'P-001',asset_id:'ASSET-013',health_score:69,telemetry:{vibration:5.55},
  diagnostics:{hypotheses:['MISALIGNMENT']},alarms:[1],maintenance_history:[1],
  issue_code:'MISALIGNMENT',issue_summary:'Alignment',recommended_next_check:'Inspect alignment',
  model_confidence:.85,data_quality_issues:[1],knowledge_doc:{},matched_cases:['CASE-032']};

test('clone links decode one source id, reject unrelated and malformed fragments',()=>{
  assert.equal(parseLibraryCloneHash('#clone=OBJ%2D008'),'OBJ-008');
  for(const hash of ['','#apart=OBJ-008','#clone=','#clone=%E0%A4%A']) assert.equal(parseLibraryCloneHash(hash),null);
});
test('only source geometry and available membership permit a library copy, including removed or target sources',()=>{
  assert.equal(libraryCloneIssue(source,true),'');
  assert.equal(libraryCloneIssue({...source,removed:true},true),'');
  assert.ok(libraryCloneIssue(source,false));
  assert.ok(libraryCloneIssue(null));
  assert.ok(libraryCloneIssue({...source,copy_of:'OTHER'}));
  assert.ok(libraryCloneIssue({...source,size:[0,.5,.3]}));
  assert.ok(libraryCloneIssue({...source,center:[Infinity,1,0]}));
});
test('copies preserve geometry but never inherit workbook readings, diagnosis, target or device identity',()=>{
  const copy=roomCopyAsset(source,'OBJ-008-copy2');
  assert.deepEqual(copy.center,source.center);
  assert.equal(copy.copy_of,source.id);
  assert.equal(copy.id,'OBJ-008-copy2');
  assert.equal(copy.mapped,false);assert.equal(copy.is_target,false);
  assert.equal(copy.condition_state,'not_instrumented');
  for(const key of ['tag','asset_id','health_score','telemetry','diagnostics','alarms','maintenance_history',
    'issue_code','issue_summary','recommended_next_check','model_confidence','data_quality_issues','knowledge_doc','matched_cases']) assert.equal(key in copy,false,key);
  assert.equal(source.health_score,69);
});
test('copy identity and placements remain isolated by dataset and survive reload',()=>{
  const store=new Map();globalThis.localStorage={getItem:k=>store.get(k)??null,setItem:(k,v)=>store.set(k,v)};
  const id=nextCopyId(source.id,new Set([source.id,source.id+'-copy1']));
  assert.equal(id,'OBJ-008-copy2');
  const copy=roomCopyAsset(source,id); const copies=new Map([[id,copy]]);
  saveRoomCopies(copies,'room-a');assert.deepEqual(loadRoomCopies('room-a'),[[id,source.id]]);
  assert.deepEqual(loadRoomCopies('room-b'),[]);
  const placement={center:[2,1,3],yaw:.3,source:[...source.center,source.yaw]};
  saveRoomPlacements(new Map([[id,placement]]),'room-a');
  assert.deepEqual(loadRoomPlacements(new Map([[id,copy]]),()=>true,'room-a').get(id),placement);
  saveRoomCopies(new Map(),'room-a');saveRoomPlacements(new Map(),'room-a');
  assert.deepEqual(loadRoomCopies('room-a'),[]);
  assert.equal(loadRoomPlacements(new Map([[id,copy]]),()=>true,'room-a').size,0);
  delete globalThis.localStorage;
});
