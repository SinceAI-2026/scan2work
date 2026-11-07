"""Stage the supplied Wael baseline with a separate exact proposal-review catalog.

Preserves original objects, identities, bindings, render bytes and navigation.
Does not activate or overwrite a live data directory. No inference or secret use.
"""
from __future__ import annotations
import argparse
import copy
import json
import os
from pathlib import Path
import shutil
import struct
import tempfile
os.environ['VALMET_OFFLINE']='1'
import numpy as np
from .audit_original_data import file_hash,read_buffers,read_source,align_original,owner_vector,contingency


def checked_output(output,*inputs):
    output=Path(output).resolve()
    for root in inputs:
        root=Path(root).resolve()
        if output==root or root in output.parents or output in root.parents:
            raise ValueError('Staged output must be separate from both input data directories')
    if output.exists():raise ValueError('Staged output already exists; use a new directory rather than replacing data')
    return output


def membership_archive(asset_ids, source_rows, owners):
    groups=[np.sort(source_rows[owners==i]).astype('i8') for i in range(len(asset_ids))]
    sizes=np.array([len(g) for g in groups],dtype='i8')
    return {'source_rows':np.concatenate(groups),'offsets':np.r_[0,np.cumsum(sizes)],
            'object_ids':np.array(asset_ids),'owner_ids':np.arange(1,len(asset_ids)+1,dtype='u2')},sizes


def copy_evidence_url(url, proposal_dir, output, copied):
    if not url:return url
    rel=Path(str(url).removeprefix('./'))
    if rel.is_absolute() or '..' in rel.parts or not rel.parts or rel.parts[0]!='data':raise ValueError('Unsafe proposal evidence path')
    relative=Path(*rel.parts[1:]);src=proposal_dir/relative
    dest=Path('proposal-review/evidence')/relative
    if str(dest) not in copied:
        if not src.is_file():raise ValueError(f'Missing proposal evidence: {src}')
        (output/dest).parent.mkdir(parents=True,exist_ok=True);shutil.copy2(src,output/dest);copied.add(str(dest))
    return 'data/'+dest.as_posix()


def build(original_data,proposal_data,poses,output,full_view_url='http://127.0.0.1:8768/'):
    olddir=Path(original_data).resolve();newdir=Path(proposal_data).resolve();poses=Path(poses).resolve()
    output=checked_output(output,olddir,newdir)
    old=json.loads((olddir/'digital_twin.json').read_text());proposals=json.loads((newdir/'digital_twin.json').read_text())
    baseline_manifest_hash=file_hash(olddir/'digital_twin.json')
    source=newdir/proposals['scene']['source_ply'];source_hash=proposals['scene']['source_sha256']
    raw=read_source(source,source_hash)
    oldpacked,oldinst=read_buffers(olddir);newpacked,newinst=read_buffers(newdir)
    oldrows,alignment,xyz=align_original(raw,oldpacked)
    newrows=np.load(newdir/proposals['scene']['source_rows_file'],allow_pickle=False)
    if len(newrows)!=len(newpacked) or not np.allclose(xyz[newrows],newpacked['xyz'],atol=1e-5,rtol=0):raise ValueError('Proposal source row order mismatch')
    oldowner=owner_vector(oldrows,oldinst,len(old['assets']),len(raw));newowner=owner_vector(newrows,newinst,len(proposals['assets']),len(raw))
    projection=np.where(newowner[oldrows]<0,65535,newowner[oldrows]).astype('<u2')
    projected_counts=np.bincount(projection[projection!=65535],minlength=len(proposals['assets']))
    overlap=contingency(oldowner,newowner,len(old['assets']),len(proposals['assets']))
    members,owned_counts=membership_archive([a['id'] for a in old['assets']],oldrows,oldinst)
    dataset_id='wael-original-'+baseline_manifest_hash[:16]
    baseline=copy.deepcopy(old)
    for i,a in enumerate(baseline['assets']):
        a['exact_owned_gaussians']=int(owned_counts[i])
        a['membership_mode']='exact-source-rows'
        a['membership_provenance']={'source':'Supplied original Wael GSID; includes original box-filled ownership',
                                    'original_reported_detector_gaussians':a.get('n_gaussians'),'native_export_preserves':'All original GSID-owned source rows; no additional filling/refinement'}
        a['metadata_mapping']='Original procedural canonical Excel binding' if a.get('mapped') else (
            f"Archived demonstration telemetry profile borrowed from {a.get('excel_profile_source')}; not a measured reading of this scene object" if a.get('excel_profile_source') else 'No canonical Excel binding')
    baseline['scene'].update(dataset_kind='wael-original-baseline',dataset_id=dataset_id,source_sha256=source_hash,
                             source_ply='source_scan.ply',source_rows_file='splat_source_rows.npy',members_file='source_members.npz',
                             membership_mode='exact-source-rows',scale_verified=False,
                             baseline_provenance={'manifest_sha256':baseline_manifest_hash,'source':'User-supplied field-atlas-data.zip',
                                                  'canonical_objects_preserved':True,'navigation_preserved':True,'render_buffers_preserved':True,
                                                  'manifest_reported_owned_rows':old['scene']['splat_binary'].get('object_splats'),
                                                  'actual_gsid_owned_rows':int(owned_counts.sum()),'row_alignment':alignment})
    baseline['runtime']={'offline':True,'external_models_enabled':False,'diagnostics':'Archived original model outputs; no new inference',
                         'dataset_id':dataset_id,'writes':'Scene edits, technician log and asset library belong to this dataset directory'}
    copied=set();regions=[]
    output.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.wael-baseline-stage-',dir=output.parent) as tmp:
        stage=Path(tmp)
        # Explicit artifact allowlist: archive instructions and arbitrary files are not executed or copied.
        for name in ('boiler_room_points.bin','splat_instance.bin','rag_index.json','rag_embeddings.npy','floorplan_scan.png','navigation_map_preview.png','overlay_walk.mp4'):
            if (olddir/name).is_file():shutil.copy2(olddir/name,stage/name)
        for name in ('photos','walk_frames','library'):
            if (olddir/name).is_dir():shutil.copytree(olddir/name,stage/name)
        shutil.copy2(source,stage/'source_scan.ply');shutil.copy2(poses,stage/'walk_poses.json')
        np.save(stage/'splat_source_rows.npy',oldrows.astype('<u4'),allow_pickle=False)
        np.savez_compressed(stage/'source_members.npz',**members)
        (stage/'scene_edits.json').write_text(json.dumps({'removed':[],'dataset_id':dataset_id}))
        (stage/'field_log.json').write_text(json.dumps({'jobs':{},'dataset_id':dataset_id}))
        proposal_folder=stage/'proposal-review';proposal_folder.mkdir()
        for j,a in enumerate(proposals['assets']):
            indices=np.flatnonzero(overlap[1:,j+1]);indices=indices[np.argsort(-overlap[indices+1,j+1])]
            links=[{'id':old['assets'][i]['id'],'asset_id':old['assets'][i]['asset_id'],'tag':old['assets'][i]['tag'],
                    'label':old['assets'][i]['label'],'class':old['assets'][i]['class'],'shared_rows':int(overlap[i+1,j+1]),
                    'fraction_of_region':round(float(overlap[i+1,j+1]/max(a['n_gaussians'],1)),5),
                    'fraction_of_original':round(float(overlap[i+1,j+1]/max(owned_counts[i],1)),5)} for i in indices]
            region={k:copy.deepcopy(a.get(k)) for k in ('id','label','class','review_status','support_tier','center','size','yaw','bbox_min','bbox_max','provenance')}
            region.update(total_source_rows=a['n_gaussians'],visible_source_rows=int(projected_counts[j]),original_matches=links,
                          label_verified=False,full_view_url=full_view_url.rstrip('/')+'/#apart='+a['id'])
            region['evidence']=copy.deepcopy(a.get('evidence',[]))
            for e in region['evidence']:
                for key in ('imageUrl','cropUrl'):
                    if e.get(key):e[key]=copy_evidence_url(e[key],newdir,stage,copied)
            photo=copy.deepcopy(a.get('photo'))
            if isinstance(photo,dict) and photo.get('url'):photo['url']=copy_evidence_url(photo['url'],newdir,stage,copied)
            elif isinstance(photo,str):photo=copy_evidence_url(photo,newdir,stage,copied)
            region['photo']=photo;regions.append(region)
        projected_owned=int((projection!=65535).sum())
        catalog={'version':1,'source_scene_id':proposals['scene'].get('source_scene_id'),'source_sha256':source_hash,
                 'rendered_rows':len(oldrows),'coordinate_frame':proposals['scene']['coordinate_frame'],
                 'projection':{'rendered_rows':len(oldrows),'total_owned_rows':int((newowner>=0).sum()),'visible_owned_rows':projected_owned,
                               'method':'Exact original PLY row IDs; projected GSID aligns with unchanged baseline GSPL order',
                               'full_proposal_render_rows':len(newrows),'full_view_url':full_view_url},
                 'regions':regions,'limits':['Review proposals are not canonical original equipment or verified physical objects.',
                                            'Rows outside the original render remain available in the separate complete review view.',
                                            'Original-match row overlap is correspondence evidence, not a recognition-accuracy score.']}
        (proposal_folder/'projected_instance.bin').write_bytes(struct.pack('<4sII',b'GSID',1,len(projection))+projection.tobytes())
        (proposal_folder/'catalog.json').write_text(json.dumps(catalog,separators=(',',':'),allow_nan=False))
        # Preserve full proposal membership separately; it never replaces the baseline map.
        shutil.copy2(newdir/proposals['scene']['members_file'],proposal_folder/'full_source_members.npz')
        baseline['proposal_review']={'catalog_url':'data/proposal-review/catalog.json','projected_instance_url':'data/proposal-review/projected_instance.bin',
                                     'full_view_url':full_view_url,'count':len(regions),'visible_owned_rows':projected_owned,'total_owned_rows':int((newowner>=0).sum()),
                                     'rendered_rows':len(oldrows),'default_visible':False}
        report={'dataset_id':dataset_id,'original_objects':len(old['assets']),'canonical_bindings':sum(bool(a['mapped']) for a in old['assets']),
                'original_manifest_sha256':baseline_manifest_hash,'source_sha256':source_hash,'original_gspl_sha256':file_hash(olddir/'boiler_room_points.bin'),
                'staged_gspl_sha256':file_hash(stage/'boiler_room_points.bin'),'original_gsid_sha256':file_hash(olddir/'splat_instance.bin'),
                'staged_gsid_sha256':file_hash(stage/'splat_instance.bin'),'original_owned_rows':int(owned_counts.sum()),
                'proposal_regions':len(regions),'proposal_total_owned_rows':int((newowner>=0).sum()),'proposal_visible_owned_rows':projected_owned,
                'proposal_rows_outside_original_render':int((newowner>=0).sum())-projected_owned,
                'copied_proposal_evidence_files':len(copied),'full_proposal_view_url':full_view_url,'activation':'Staged only; active data directory was not replaced'}
        # Verify every original field, device location, cylinder, photo and navigation record stayed identical.
        for original,adapted in zip(old['assets'],baseline['assets']):
            if any(adapted[k]!=v for k,v in original.items()):raise ValueError('Baseline adapter changed an original asset field')
        if old['navigation']!=baseline['navigation']:raise ValueError('Baseline navigation changed')
        if report['original_gspl_sha256']!=report['staged_gspl_sha256'] or report['original_gsid_sha256']!=report['staged_gsid_sha256']:raise ValueError('Baseline renderer bytes changed')
        (stage/'baseline_report.json').write_text(json.dumps(report,indent=2))
        (stage/'digital_twin.json').write_text(json.dumps(baseline,separators=(',',':'),allow_nan=False))
        stage.replace(output)
    return report


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--original-data',type=Path,required=True);ap.add_argument('--proposal-data',type=Path,required=True)
    ap.add_argument('--poses',type=Path,required=True);ap.add_argument('--output',type=Path,required=True)
    ap.add_argument('--full-view-url',default='http://127.0.0.1:8768/')
    a=ap.parse_args();print(json.dumps(build(a.original_data,a.proposal_data,a.poses,a.output,a.full_view_url),indent=2))

if __name__=='__main__':main()
