"""Offline, lossless-membership adapter from Field Atlas packages to the Wael viewer.

No detection, synthetic geometry, model downloads or paid inference is performed.
Run ``python -m pipeline.import_field_atlas --help`` for relocatable inputs.
"""
from __future__ import annotations
import argparse
import copy
import gzip
import hashlib
import json
import os
import re
import shutil
import struct
import tempfile
from collections import Counter
from pathlib import Path

# Must precede configuration imports: do not consult the keychain or load secrets.
os.environ['VALMET_OFFLINE'] = '1'
import numpy as np
from PIL import Image
from . import config as C, diagnostics, knowledge, navigation as N, retrieval
from .asset_export import GSPL, read_ply_header, write_gspl
from .ingest import FLOOR_Y_PLY, SH_C0, _compute_cov6_world

DEFAULT_TARGET = 'linards-BOX-0173'
CONTEXT = 65535


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(4 * 1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def to_levelled(xyz):
    """Rigid rotation plus translation; scale is deliberately unchanged/unverified."""
    return np.asarray(xyz) * np.array([1., -1., -1.]) + np.array([0., FLOOR_Y_PLY, 0.])


def resolve_bundle(root: Path) -> Path:
    for p in (root / 'model1/demo', root / 'demo', root):
        if (p / 'data/scene-extraction/scene.json').is_file():
            return p
    raise ValueError('Bundle must contain model1/demo/data/scene-extraction/scene.json or data/scene-extraction/scene.json')


def validate_membership(scene, owners, members, source_rows):
    """Reject stale/reordered/overlapping memberships rather than nearest-point guessing."""
    objects = scene['objects']
    if scene.get('coordinateFrame') != 'original-ply-y-down':
        raise ValueError('Unsupported source coordinate frame')
    if source_rows != scene['sourceRows'] or len(owners) != source_rows:
        raise ValueError('Source row count / owner row count does not match manifest')
    if len(objects) >= CONTEXT or len({o['id'] for o in objects}) != len(objects):
        raise ValueError('Object IDs must be unique and fit the GSID uint16 table')
    if any(not re.fullmatch(r'[A-Za-z0-9_-]+', o['id']) for o in objects):
        raise ValueError('Object IDs must be safe filename components')
    if members['object_ids'].tolist() != [o['id'] for o in objects]:
        raise ValueError('Membership object order differs from scene manifest')
    offsets, rows = members['offsets'], members['source_rows']
    if offsets.shape != (len(objects) + 1,) or offsets[0] != 0 or offsets[-1] != len(rows) or np.any(np.diff(offsets) < 0):
        raise ValueError('Invalid membership offsets')
    if len(rows) and (rows.min() < 0 or rows.max() >= source_rows or len(np.unique(rows)) != len(rows)):
        raise ValueError('Membership rows overlap or are outside source')
    lookup = np.full(65536, CONTEXT, '<u2')
    used = set()
    for i, o in enumerate(objects):
        ids = o['sourceIds']
        if len(ids) != 1 or not 0 < ids[0] < CONTEXT or ids[0] in used:
            raise ValueError('Source owner IDs must be unique positive uint16 values')
        used.add(ids[0]); lookup[ids[0]] = i
        row = rows[offsets[i]:offsets[i+1]]
        if int(members['owner_ids'][i]) != ids[0] or len(row) != o['count'] or not np.all(owners[row] == ids[0]):
            raise ValueError(f"Exact membership mismatch for {o['id']}")
    if np.count_nonzero(owners) != len(rows) or not set(np.unique(owners)).issubset(used | {0}):
        raise ValueError('Ownership contains rows absent from compact memberships')
    return lookup[owners]


def read_source(source, expected_sha, expected_rows):
    if sha256(source) != expected_sha:
        raise ValueError('Source PLY SHA256 mismatch: refusing to apply memberships to different source rows')
    n, off, dtype = read_ply_header(source)
    if n != expected_rows or source.stat().st_size != off + n * dtype.itemsize:
        raise ValueError('Source PLY row count, stride or payload size differs from manifest')
    required = {'x','y','z','opacity', *[f'scale_{i}' for i in range(3)], *[f'rot_{i}' for i in range(4)], *[f'f_dc_{i}' for i in range(3)]}
    if not required.issubset(dtype.names):
        raise ValueError('Source does not contain the required 3D Gaussian attributes')
    return np.memmap(source, mode='r', dtype=dtype, offset=off, shape=(n,))


def pack_source(raw, instance):
    xyz = to_levelled(np.stack([raw[k] for k in ('x','y','z')], 1)).astype('<f4')
    logs = np.stack([raw[f'scale_{i}'] for i in range(3)], 1)
    rots = np.stack([raw[f'rot_{i}'] for i in (1,2,3,0)], 1)
    dc = np.stack([raw[f'f_dc_{i}'] for i in range(3)], 1)
    finite = np.isfinite(xyz).all(1) & np.isfinite(logs).all(1) & np.isfinite(rots).all(1) & np.isfinite(dc).all(1) & np.isfinite(raw['opacity'])
    if not np.all(finite[instance != CONTEXT]):
        raise ValueError('Owned source rows contain invalid values; refusing silent membership loss')
    op = 1 / (1 + np.exp(-np.clip(raw['opacity'], -20, 20)))
    # Original scales, not Wael's old scale clamp. Original SH is kept in source_scan.ply;
    # the existing 40-byte viewer uses SH DC only, an explicit rendering limitation.
    scales = np.exp(np.clip(logs, -30, 20))
    roi = (xyz[:,0] >= -4.75) & (xyz[:,0] <= 4.15) & (xyz[:,1] >= -.12) & (xyz[:,1] <= 2.25) & (xyz[:,2] >= -3.55) & (xyz[:,2] <= 3.75)
    keep = finite & (((op > .065) & (scales.mean(1) < .45) & roi) | (instance != CONTEXT))
    rows = np.flatnonzero(keep).astype('<u4')
    packed = np.empty(len(rows), dtype=GSPL)
    packed['xyz'] = xyz[rows]
    packed['rgba'][:,:3] = np.clip((.5 + SH_C0 * dc[rows]) * 255, 0, 255).astype('u1')
    packed['rgba'][:,3] = np.clip(op[rows] * 255, 0, 255).astype('u1')
    packed['cov6'] = _compute_cov6_world(scales[rows], rots[rows])
    return packed, rows, xyz, op, scales.mean(1)


def unmapped_diagnostics(label):
    return {'ai_triage': None, 'retrieved_evidence': [], 'headline': f'{label} — not linked to service data',
            'executive_summary': 'This is an unreviewed scan region. No synthetic equipment record is assigned; operating condition and equipment identity are unknown.',
            'observations': [], 'hypotheses': [], 'verification_steps': [], 'work_order_draft': None}


def convert_assets(scene, xyz, members, excel, target_region, evidence):
    by_tag = {r['tag']:r for r in excel['records'].values()}
    assets = []
    target_source = next((o['id'] for o in scene['objects'] if o.get('tag') == 'CV-003'), None)
    for i,o in enumerate(scene['objects']):
        rows = members['source_rows'][members['offsets'][i]:members['offsets'][i+1]]
        p = xyz[rows]; lo,hi = p.min(0),p.max(0)
        tag = 'CV-003' if o['id'] == target_region else o.get('tag')
        if o['id'] != target_region and tag == 'CV-003': tag = None
        rec = copy.deepcopy(by_tag.get(tag, {}))
        native = evidence.get(o['id'], {}).get('evidence', [])
        rendered = [{'cropUrl':url,'kind':'rendered-proposal', 'alignmentMeaning':'Synthetic source-splat view; not a native video identity confirmation.'} for url in o.get('cropUrls', [])]
        ev = copy.deepcopy(native or rendered)
        for e in ev: e.setdefault('kind', 'native-video')
        a = {**rec, 'id':o['id'], 'source_region_id':o['id'], 'asset_id':rec.get('asset_id', 'REGION-' + o['id']),
             'tag':rec.get('tag', o['id']), 'label':o['name'], 'class':o['catalog'].get('class','unclassified'),
             'class_label':o['name'], 'asset_class':rec.get('asset_class', 'UNCLASSIFIED'),
             'center':((lo+hi)/2).tolist(), 'size':(hi-lo).tolist(), 'bbox_min':lo.tolist(), 'bbox_max':hi.tolist(), 'yaw':0.,
             'tier':'structure' if o.get('structureRole') else 'equipment', 'n_gaussians':len(rows),
             'mapped':bool(rec), 'instrumented':bool(rec), 'is_target':o['id']==target_region, 'movable':False,
             'condition_state':rec.get('condition_state', 'unknown'), 'health_score':rec.get('health_score'),
             'effective_confidence':rec.get('effective_confidence'), 'mapping_confidence':None,
             'telemetry':rec.get('telemetry',{'latest':{},'stats':{},'series':{}}),
             'alarms':rec.get('alarms',[]), 'maintenance_history':rec.get('maintenance_history',[]),
             'issue_code':rec.get('issue_code'), 'issue_summary':rec.get('issue_summary','No service record assigned'),
             'area_name':rec.get('area_name','Equipment Room scan'), 'data_quality_issues':rec.get('data_quality_issues',[]),
             'review_status':o.get('reviewStatus','unreviewed'), 'support_tier':o.get('supportTier'), 'structure_role':o.get('structureRole'),
             'label_source':'Preserved upstream suggestion; unverified', 'label_verified':False,
             'source_binding':copy.deepcopy(o.get('service')), 'provenance':copy.deepcopy(o['provenance']),
             'membership_mode':'exact-source-rows', 'evidence':ev,
             'photo':{'url': ev[0]['cropUrl'], 'kind':ev[0]['kind'], 'source':'Native walkthrough crop' if native else 'Rendered proposal evidence'} if ev else None,
             'metadata_mapping':'Synthetic Excel record; procedural demonstration binding' if rec else 'No synthetic record assigned'}
        if a['is_target']:
            a['provenance']['demo_binding_override'] = {'tag':'CV-003','previous_source_region':target_source,'reason':'Preserve Wael physical target anchor; native video crop visually reviewed. Exact object boundary remains unverified.', 'wael_anchor':list(C.TARGET_ANCHOR)}
        assets.append(a)
    if len({a['asset_id'] for a in assets}) != len(assets): raise ValueError('Duplicate canonical asset bindings')
    diagnostics.set_class_baselines(assets)
    for a in assets:
        a['diagnostics'] = diagnostics.generate_diagnostic_report(a,excel['docs'],allow_ai=False) if a['mapped'] else unmapped_diagnostics(a['label'])
        if a['mapped']: a['diagnostics']['source'] = 'Deterministic explanation of synthetic Excel data; external models disabled'
    return assets


def build_navigation(xyz, opacity, scales, assets):
    geom = (opacity >= .3) & (scales < .16)
    floor = xyz[geom & (np.abs(xyz[:,1]) <= .08)]
    obstacle = xyz[geom & (xyz[:,1] >= C.OBST_MIN_H) & (xyz[:,1] <= C.OBST_MAX_H)]
    # Unreviewed region boxes are not physical solid volumes. Occupancy uses original scan samples.
    grid = N.build_occupancy_grid(obstacle, [], floor_pts=floor)
    free = np.argwhere(~grid['dilated_occ'])
    if len(free):
        world = np.array([N.grid_to_world(*c,grid) for c in free])
        start = tuple(free[np.argmin(np.linalg.norm(world - [-.25,2.35],axis=1))])
        start_xz = list(N.grid_to_world(*start,grid))
    else: start=None; start_xz=[-.25,2.35]
    routes={}
    for a in assets:
        if not a['mapped']: continue
        route={'asset_id':a['asset_id'],'object_id':a['id'],'tag':a['tag'],'validated':False,'status':'unavailable','waypoints':[], 'total_distance_m':0,'duration_s':0,'min_clearance_m':None,
               'scale_verified':False,'planning_policy':'Observed-floor occupancy simulation; source units are unverified. No physical safety or clearance guarantee.'}
        try:
            if start is None: raise ValueError('No connected floor-supported route space in the source scan')
            ax,_,az=a['center']; d=np.linalg.norm(world-[ax,az],axis=1)
            eligible=np.flatnonzero((d>=.35)&(d<=1.4))
            if not len(eligible): raise ValueError('No reachable floor-supported stand-off within 1.4 source units of this region')
            idx=eligible[np.argmin(abs(d[eligible]-.52))]; goal=tuple(free[idx])
            cells=N.astar_grid(start,goal,grid); wps=N.smooth_trajectory(cells,grid)
            N.attach_proximity_timeline(wps,assets)
            # The viewer computes proximity against all 1,933 centers live. A compact
            # precomputed summary avoids serializing the same hundreds of regions at
            # every waypoint on every route (hundreds of megabytes of duplicate JSON).
            for wp in wps:
                wp['nearby_assets'] = wp['nearby_assets'][:16]
                wp['nearby_summary_limit'] = 16
            route.update(status='available',validated=True,validation_scope='Collision checked against sampled occupancy grid only; scan omissions remain unknown',
                         waypoints=wps,total_distance_m=wps[-1]['distance_m'],duration_s=wps[-1]['t'],raw_astar_steps=len(cells),
                         min_clearance_m=min(w['clearance_m'] for w in wps),standoff_xz=list(N.grid_to_world(*goal,grid)))
        except ValueError as e: route['reason']=str(e)
        routes[a['asset_id']]=route
    target=next(a for a in assets if a['is_target']); primary=routes[target['asset_id']]
    obst=[]; walk=[]
    for ix,iz in np.argwhere(grid['raw_occ']): obst.append(list(N.grid_to_world(ix,iz,grid)))
    for ix,iz in free[::4]: walk.append([*N.grid_to_world(ix,iz,grid),round(float(grid['clearance_m'][ix,iz]),3)])
    return {'grid_meta':{k:grid[k] for k in ('x_min','z_min','res','nx','nz')} | {'robot_radius_m':C.ROBOT_RADIUS,'safety_margin_m':grid['safety_margin_m'],'required_clearance_m':grid['required_clearance_m'],'proximity_radius_m':C.PROXIMITY_RADIUS,'walkable_cells':len(free),'obstacle_cells':len(obst),'units':'source units; physical scale unverified'},
            'start_xz':start_xz,'target_asset_id':target['asset_id'],'target_object_id':target['id'],'target_tag':target['tag'],
            'target_standoff_xz':primary.get('standoff_xz',start_xz),'primary_route':primary,'routes_by_asset':routes,
            'obstacle_cells_xz':obst,'walkable_sample_xz':walk,'walkable_grid':(~grid['dilated_occ']).astype(int).tolist(),
            'room_walkable_grid':grid['room_walkable'].astype(int).tolist(),'component_grid':grid['component_labels'].tolist(),
            'clearance_grid_m':np.round(grid['clearance_m'],3).tolist(),
            'coverage':{'reachable_area_m2':round(len(free)*grid['res']**2,2),'total_free_area_m2':round(float(grid['room_walkable'].sum())*grid['res']**2,2),'units':'square source units; not measured square metres'},
            'scale_verified':False,'floor_source_points':len(floor),'obstacle_source_points':len(obstacle),
            'limitations':['Unobserved floor is blocked.', 'Observed samples cannot prove walls and hazards are complete.', 'Robot radius and distances are simulation assumptions in uncalibrated source units.']}


def copy_evidence(demo, output, assets):
    copied=set()
    for a in assets:
        for e in a['evidence']:
            for key in ('imageUrl','cropUrl'):
                if not e.get(key): continue
                rel=Path(e[key])
                if rel.is_absolute() or '..' in rel.parts or rel.parts[0]!='data': raise ValueError('Unsafe evidence path')
                src=demo/rel; dst=output/Path(*rel.parts[1:])
                if not src.is_file(): raise ValueError(f'Missing evidence: {src}')
                if e[key] in copied: continue
                expected = e.get('imageSha256' if key == 'imageUrl' else 'cropSha256')
                if expected and sha256(src) != expected:
                    raise ValueError(f'Evidence SHA256 mismatch: {rel}')
                dst.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(src,dst); copied.add(e[key])
    return len(copied)


def floorplan(xyz, opacity, scales, nav, out):
    m=nav['grid_meta']; size=4
    w,h=m['nx']*size,m['nz']*size
    xx=np.floor((xyz[:,0]-m['x_min'])/m['res']*size).astype(int)
    zz=np.floor((xyz[:,2]-m['z_min'])/m['res']*size).astype(int)
    use=(xx>=0)&(xx<w)&(zz>=0)&(zz<h)&(xyz[:,1]>=-.08)&(xyz[:,1]<=1.62)&(opacity>=.3)&(scales<.16)
    raster=np.full((h,w,3),[25,34,36],dtype='u1'); raster[h-1-zz[use],xx[use]]=[167,181,174]
    Image.fromarray(raster).save(out)


def _build_bundle(bundle_root, source, workbook, output, target_region=DEFAULT_TARGET):
    demo=resolve_bundle(Path(bundle_root)); output=Path(output); output.mkdir(parents=True,exist_ok=True)
    scene_path=demo/'data/scene-extraction/scene.json'; scene=json.loads(scene_path.read_text())
    if target_region not in {o['id'] for o in scene['objects']}: raise ValueError('Requested target region absent')
    source=Path(source) if source else demo/'data/scene-extraction/room.ply'
    raw=read_source(source,scene['sourceSha256'],scene['sourceRows'])
    owner_bytes=gzip.decompress((scene_path.parent/'owners.gz').read_bytes())
    if hashlib.sha256(owner_bytes).hexdigest()!=scene['ownersSha256']: raise ValueError('Ownership SHA256 mismatch')
    owners=np.frombuffer(owner_bytes,'<u2'); members=dict(np.load(scene_path.parent/'members.npz',allow_pickle=False))
    instance=validate_membership(scene,owners,members,len(raw))
    print(f'Validated {len(raw):,} original rows and {len(scene["objects"]):,} exact regions',flush=True)
    packed,rows,xyz,opacity,scales=pack_source(raw,instance)
    write_gspl(packed,output/'boiler_room_points.bin')
    (output/'splat_instance.bin').write_bytes(struct.pack('<4sII',b'GSID',1,len(rows))+instance[rows].tobytes())
    np.save(output/'splat_source_rows.npy',rows,allow_pickle=False)
    np.savez_compressed(output/'source_members.npz',**members)
    shutil.copy2(source,output/'source_scan.ply')
    evidence_path=demo/'data/object-rebuild/evidence.json'
    evidence_doc=json.loads(evidence_path.read_text()) if evidence_path.exists() else {'objects':[]}
    if evidence_doc.get('sourceSceneSha256',scene['sourceSha256'])!=scene['sourceSha256']: raise ValueError('Video evidence is for a different source scan')
    evidence={o['id']:o for o in evidence_doc['objects']}
    excel=knowledge.load_excel_pack(Path(workbook))
    assets=convert_assets(scene,xyz,members,excel,target_region,evidence)
    n_evidence=copy_evidence(demo,output,assets)
    print(f'Bound {sum(a["mapped"] for a in assets)} synthetic records; copied {n_evidence} evidence files',flush=True)
    nav=build_navigation(xyz,opacity,scales,assets)
    floorplan(xyz,opacity,scales,nav,output/'floorplan_scan.png'); nav['floorplan_image']='./data/floorplan_scan.png'
    corpus=retrieval.build_corpus(excel)
    (output/'rag_index.json').write_text(json.dumps({'backend':'tfidf','model':None,'items':corpus}))
    frame={'rotation_matrix':[[1,0,0],[0,-1,0],[0,0,-1]],'translation':[0,FLOOR_Y_PLY,0], 'floor_y_ply':FLOOR_Y_PLY,'up_axis':'Y','units':'unverified source units','scale_verified':False,'source_frame':'original-ply-y-down'}
    report={'scene_id':scene['sceneId'],'source_sha256':scene['sourceSha256'],'owners_sha256':scene['ownersSha256'],'manifest_sha256':sha256(scene_path),
            'source_rows':len(raw),'regions':len(assets),'owned_rows':int(np.count_nonzero(owners)), 'rendered_rows':len(rows),
            'owned_rows_rendered':int(np.count_nonzero(instance[rows]!=CONTEXT)), 'baseline_objects_preserved':scene['stats']['preservedBaselineObjects'],
            'baseline_rows_preserved':scene['stats']['preservedBaselineRows'],'unassigned_rows':int(np.count_nonzero(owners==0)),
            'mapped_assets':sum(a['mapped'] for a in assets),'unreviewed_regions':len(assets),'scale_verified':False,'recognition_accuracy':None,
            'available_routes':sum(r['validated'] for r in nav['routes_by_asset'].values()), 'offline':True,
            'membership_policy':'Exact source rows; no bounding-box filling, floor removal, nearest-point remapping or refinement',
            'rendering_limits':'Viewer GSPL uses original anisotropic scales/covariance and DC colour only; source_scan.ply preserves every SH attribute.'}
    target=next(a for a in assets if a['is_target'])
    twin={'project':'Field Atlas · Wael viewer with expanded extraction','generated_in_sec':0,
          'scene':{'source_file':source.name,'source_sha256':scene['sourceSha256'],'source_scene_id':scene['sceneId'],
                   'ingestion_stats':{'declared_vertices':len(raw),'valid_gaussians':len(raw)},'coordinate_frame':frame,
                   'perception_stats':scene['stats'],'walls':[], 'splat_binary':{'file':'boiler_room_points.bin','exported_splats':len(rows),'stride_bytes':40,'binary_size_bytes':16+len(rows)*40},
                   'class_breakdown':dict(Counter(a['class'] for a in assets)), 'canonical_state_breakdown':dict(Counter(a['condition_state'] for a in assets if a['mapped'])),
                   'semantic_labelling':{'source':'Preserved Linards metadata and class-free proposals','verified':False},
                   'membership_mode':'exact-source-rows','source_ply':'source_scan.ply','source_rows_file':'splat_source_rows.npy','members_file':'source_members.npz','import_report':report},
          'runtime':{'offline':True,'external_models_enabled':False},'ai_stack':{'cluster_labels':'Preserved suggestions; unreviewed','retrieval_backend':'tfidf','triage':'Deterministic synthetic Excel explanation; no external inference'},
          'target_asset_id':target['asset_id'],'target_object_id':target['id'],'target_tag':target['tag'],
          'assets':assets,'navigation':nav,'knowledge_docs':excel['docs'],'builder_questions':excel['builder_questions'],'api_contract':excel['api_contract']}
    # Preserve the richer plan/BIM layers as auxiliary geometry. This does not
    # expand source membership or replace the conservative navigation grid.
    from .derived_context import enrich as enrich_context
    recovery = enrich_context(twin, output)
    report['derived_context'] = recovery['after']
    (output/'digital_twin.json').write_text(json.dumps(twin,separators=(',',':'),allow_nan=False))
    (output/'import_report.json').write_text(json.dumps(report,indent=2))
    print(json.dumps(report,indent=2),flush=True)
    return twin


def import_bundle(bundle_root, source, workbook, output, target_region=DEFAULT_TARGET):
    """Build and validate everything in isolation; failed imports leave the active data untouched.

    Stop the viewer server during publication. The manifest is published last.
    Existing library, edits, decisions cache and technician log are not overwritten.
    """
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.field-atlas-import-', dir=output.parent) as temporary:
        stage = Path(temporary)
        twin = _build_bundle(bundle_root, source, workbook, stage, target_region)
        output.mkdir(parents=True, exist_ok=True)
        for p in sorted(stage.iterdir(), key=lambda p:p.name == 'digital_twin.json'):
            dst = output / p.name
            if p.is_dir() and dst.exists():
                # Evidence is immutable within a SHA-validated input scene.
                shutil.copytree(p, dst, dirs_exist_ok=True)
            else:
                p.replace(dst)
    return twin


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--bundle-root',type=Path,required=True,help='Extracted Field Atlas package or source repository')
    ap.add_argument('--source',type=Path,help='Original full PLY (default: package scene-extraction/room.ply)')
    ap.add_argument('--workbook',type=Path,required=True,help='Original Valmet synthetic builder pack XLSX')
    ap.add_argument('--output',type=Path,default=C.OUT_DIR)
    ap.add_argument('--target-region',default=DEFAULT_TARGET,help='CV-003 demonstration binding; default preserves reviewed Wael physical anchor')
    args=ap.parse_args(); import_bundle(args.bundle_root,args.source,args.workbook,args.output,args.target_region)

if __name__=='__main__': main()
