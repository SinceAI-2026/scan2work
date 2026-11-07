// Read-only reproduction of the original browser's walkthrough-passage expansion.
import { readFileSync, writeFileSync, realpathSync, existsSync } from 'node:fs';
import { resolve, dirname, basename, sep } from 'node:path';
import { createHash } from 'node:crypto';
import { withWalkthroughPassages } from '../web/walkthrough-navigation.mjs';
const args=Object.fromEntries(process.argv.slice(2).reduce((a,v,i,all)=>i%2?a:[...a,[v,all[i+1]]],[]));
for(const k of ['--original-data','--current-data','--poses','--output'])if(!args[k])throw new Error(`Missing ${k}`);
const output=existsSync(args['--output']) ? realpathSync(args['--output'])
  : resolve(realpathSync(dirname(resolve(args['--output']))),basename(args['--output']));
for(const dir of [args['--original-data'],args['--current-data']]){
  const root=realpathSync(dir);
  if(output===root || output.startsWith(root+sep))throw new Error('Audit output must be outside both input data directories');
}
const read=p=>JSON.parse(readFileSync(p,'utf8'));
const old=read(`${args['--original-data']}/digital_twin.json`),cur=read(`${args['--current-data']}/digital_twin.json`);
for(const key of ['x_min','z_min','res','nx','nz']){
  if(old.navigation.grid_meta[key]!==cur.navigation.grid_meta[key])throw new Error(`Navigation grids differ at ${key}; cellwise comparison refused`);
}
const poses=read(args['--poses']);
const nav=withWalkthroughPassages(old.navigation,old.assets,poses);
const count=grid=>grid.reduce((n,row)=>n+row.reduce((s,x)=>s+Number(Boolean(x)),0),0);
const base=old.navigation.walkable_grid,runtime=nav.walkable_grid,current=cur.navigation.walkable_grid;
let opened=0,closed=0,oldRuntimeOnly=0,currentOnly=0,both=0;
for(let i=0;i<base.length;i++)for(let j=0;j<base[i].length;j++){
  if(runtime[i][j]&&!base[i][j])opened++;
  if(base[i][j]&&!runtime[i][j])closed++;
  if(runtime[i][j]&&!current[i][j])oldRuntimeOnly++;
  if(!runtime[i][j]&&current[i][j])currentOnly++;
  if(runtime[i][j]&&current[i][j])both++;
}
const res=nav.grid_meta.res;
const report={
  read_only:true,method:'Actual original withWalkthroughPassages function, original archived assets/navigation, tracked upstream walk_poses.json; no live scene changes',
  module_sha256:Object.fromEntries(['walkthrough-navigation.mjs','manual-walk.mjs','navigation-view.mjs'].map(name=>[
    name,createHash('sha256').update(readFileSync(new URL(`../web/${name}`,import.meta.url))).digest('hex')
  ])),
  original_assets_with_imported_source_region_id:old.assets.filter(a=>a.source_region_id).length,
  poses:poses.length,poses_sha256:createHash('sha256').update(readFileSync(args['--poses'])).digest('hex'),
  original_stored_walkable_cells:count(base),original_runtime_walkable_cells:count(runtime),
  original_stored_area_source_units2:count(base)*res**2,original_runtime_area_source_units2:count(runtime)*res**2,
  current_imported_walkable_cells:count(current),current_imported_area_source_units2:count(current)*res**2,
  original_runtime_opened_cells:opened,original_runtime_closed_cells:closed,
  original_runtime_vs_current:{both_walkable:both,original_only_walkable:oldRuntimeOnly,current_only_walkable:currentOnly},
  original_stored_profile:old.navigation.grid_meta,original_runtime_profile:nav.grid_meta,
  original_stale_coverage_record:nav.coverage,
  limitations:['The original browser expands its stored grid near walkthrough camera positions and changes robot radius/margin assumptions.',
    'Stored coverage fields are not recomputed by that frontend function; they understate its displayed grid.',
    'The imported viewer intentionally does not apply these passage edits. More visible floor is not a verified accuracy or safety improvement.',
    'Physical scale and camera-to-floor assumptions are unverified.']
};
writeFileSync(output,JSON.stringify(report,null,2));console.log(JSON.stringify(report,null,2));
