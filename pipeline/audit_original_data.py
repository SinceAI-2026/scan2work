"""Read-only source-row audit of an original Wael data export against an imported twin.

No API calls, live-data edits or automatic merging. Coordinate/covariance checks
must establish row correspondence before any overlap metrics are produced.
"""
from __future__ import annotations
import argparse
from collections import Counter
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path
import struct
os.environ['VALMET_OFFLINE']='1'
import numpy as np
from PIL import Image
from .asset_export import GSPL,read_ply_header
from .ingest import FLOOR_Y_PLY,SH_C0,_compute_cov6_world


def file_hash(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda:f.read(4*1024*1024),b''):h.update(chunk)
    return h.hexdigest()


def validate_output_path(output,*input_roots):
    """Fail before reading inputs if an audit result could overwrite either dataset."""
    target=Path(output).resolve()
    for root in input_roots:
        root=Path(root).resolve()
        if target==root or root in target.parents:
            raise ValueError('Audit output must be outside both input data directories')
    return target


def read_buffers(data_dir):
    p=Path(data_dir);b=(p/'boiler_room_points.bin').read_bytes()
    if len(b)<16:raise ValueError('Short GSPL header')
    magic,version,n,stride=struct.unpack_from('<4sIII',b)
    if (magic,version,stride)!=(b'GSPL',3,40) or len(b)!=16+n*stride:raise ValueError('Unsupported/truncated GSPL')
    packed=np.frombuffer(b,dtype=GSPL,count=n,offset=16)
    q=(p/'splat_instance.bin').read_bytes()
    if len(q)<12:raise ValueError('Short GSID header')
    if struct.unpack_from('<4sII',q)!=(b'GSID',1,n) or len(q)!=12+2*n:raise ValueError('GSID does not align with GSPL')
    return packed,np.frombuffer(q,dtype='<u2',count=n,offset=12)


def read_source(path,expected_hash):
    h=file_hash(path)
    if h!=expected_hash:raise ValueError('Current original source PLY SHA256 mismatch')
    n,off,dt=read_ply_header(path)
    if Path(path).stat().st_size!=off+n*dt.itemsize:raise ValueError('Source PLY size mismatch')
    return np.memmap(path,dtype=dt,offset=off,mode='r',shape=(n,))


def source_arrays(raw):
    p=np.stack([raw[k] for k in ('x','y','z')],1)
    xyz=(p.astype(np.float64)*[1,-1,-1]+[0,FLOOR_Y_PLY,0]).astype('f4')
    logs=np.stack([raw[f'scale_{i}'] for i in range(3)],1)
    rots=np.stack([raw[f'rot_{i}'] for i in (1,2,3,0)],1)
    dc=np.stack([raw[f'f_dc_{i}'] for i in range(3)],1)
    valid=np.isfinite(p).all(1)&np.isfinite(logs).all(1)&np.isfinite(rots).all(1)&np.isfinite(dc).all(1)&np.isfinite(raw['opacity'])&(abs(p)<60).all(1)&(np.linalg.norm(p,axis=1)>1e-4)
    scales=np.exp(np.clip(logs,-9.5,-1.2).astype(np.float64)).astype('f4')
    opacity=(1/(1+np.exp(-np.clip(raw['opacity'],-20,20)))).astype('f4')
    return xyz,logs,rots,dc,valid,scales,opacity


def align_original(raw,packed):
    """Reproduce the frozen upstream ingest/export row gate; validate every record."""
    xyz,logs,rots,dc,valid,scales,op=source_arrays(raw)
    keep=valid&(op>.065)&(scales.mean(1)<.45)&(xyz[:,0]>=-4.75)&(xyz[:,0]<=4.15)&(xyz[:,1]>=-.12)&(xyz[:,1]<=2.22)&(xyz[:,2]>=-3.55)&(xyz[:,2]<=3.75)
    rows=np.flatnonzero(keep)
    if len(rows)!=len(packed):raise ValueError(f'Original render filter yields {len(rows)} rows but original buffer has {len(packed)}; no guessed matches used')
    poserr=np.max(abs(xyz[rows]-packed['xyz']),axis=1)
    cov=_compute_cov6_world(scales[rows],rots[rows]);coverr=np.max(abs(cov-packed['cov6']),axis=1)
    rgb=(np.clip(.5+SH_C0*dc[rows],0,1).astype('f4')*255).astype('u1')
    alpha=np.clip(op[rows]*255,4,255).astype('u1')
    rgb_err=np.max(abs(rgb.astype('i2')-packed['rgba'][:,:3].astype('i2')),axis=1)
    alpha_err=abs(alpha.astype('i2')-packed['rgba'][:,3].astype('i2'))
    report={'method':'Frozen ab777d3 ingest and export filters, full ordered coordinate/covariance/DC-colour/opacity verification against source PLY',
            'records_checked':len(rows),'position_max_abs_error':float(poserr.max()),'position_tolerance':1e-5,
            'covariance_max_abs_error':float(coverr.max()),'covariance_tolerance':1e-6,
            'rgb_max_byte_error':int(rgb_err.max()),'alpha_max_byte_error':int(alpha_err.max()),
            'all_coordinates_pass':bool(np.all(poserr<=1e-5)),'all_covariances_pass':bool(np.all(coverr<=1e-6)),
            'all_colours_pass':bool(np.all(rgb_err<=1)),'all_opacities_pass':bool(np.all(alpha_err<=1)),
            'nearest_neighbor_matching_used':False}
    report['verified']=all(report[k] for k in ('all_coordinates_pass','all_covariances_pass','all_colours_pass','all_opacities_pass'))
    if not report['verified']:raise ValueError('Source row verification failed: '+json.dumps(report))
    return rows,report,xyz


def owner_vector(rows,instances,asset_count,source_count):
    if len(rows)!=len(instances) or len(np.unique(rows))!=len(rows):raise ValueError('Duplicate/mismatched source rows')
    if len(rows) and (rows.min()<0 or rows.max()>=source_count):raise ValueError('Out-of-range source row')
    if np.any((instances!=65535)&(instances>=asset_count)):raise ValueError('GSID object index out of range')
    out=np.full(source_count,-1,np.int32);out[rows]=np.where(instances==65535,-1,instances.astype(np.int32))
    return out


def contingency(old_owner,new_owner,old_count,new_count):
    if len(old_owner)!=len(new_owner):raise ValueError('Different source sizes')
    return np.bincount((old_owner.astype('i8')+1)*(new_count+1)+new_owner+1,minlength=(old_count+1)*(new_count+1)).reshape(old_count+1,new_count+1)


def image_path(data_dir,url):
    rel=Path(str(url).removeprefix('./'))
    if rel.is_absolute() or '..' in rel.parts:return None
    if rel.parts and rel.parts[0]=='data':rel=Path(*rel.parts[1:])
    return Path(data_dir)/rel


def inventory(twin,data_dir,instances):
    a=twin['assets'];photo_types=Counter();existing=0;native_refs=0;native_regions=0;rendered_regions=0
    for x in a:
        photo=x.get('photo');url=photo.get('url') if isinstance(photo,dict) else photo
        if url:
            photo_types[photo.get('kind','unspecified') if isinstance(photo,dict) else 'original-photo-string']+=1
            p=image_path(data_dir,url);existing+=bool(p and p.is_file())
        ev=x.get('evidence',[]);native=[e for e in ev if e.get('kind')=='native-video'];native_refs+=len(native)
        native_regions+=bool(native);rendered_regions+=bool(ev) and not native
    nav=twin['navigation'];context=twin['scene'].get('derived_context',{});routes=nav.get('routes_by_asset',{})
    floor=Path(data_dir)/'floorplan_scan.png'
    sizes=Image.open(floor).size if floor.exists() else None
    return {'objects':len(a),'classes':dict(Counter(x['class'] for x in a)),'distinct_classes':len({x['class'] for x in a}),
            'mapped_assets':sum(bool(x.get('mapped')) for x in a),'movable':sum(bool(x.get('movable')) for x in a),
            'asset_attached_cylinders':sum(bool(x.get('cylinder')) for x in a),'separate_pipe_context':len(context.get('pipe_segments',[])),
            'nonzero_canonical_yaw':sum(bool(x.get('yaw')) for x in a),'nonzero_proxy_yaw':sum(bool(x.get('geometry_proxy',{}).get('yaw')) for x in a),
            'walls':len(twin['scene'].get('walls',[])),'label_sources':dict(Counter(x.get('label_source','unspecified') for x in a)),
            'objects_with_photo_reference':sum(photo_types.values()),'existing_referenced_photo_files':existing,'photo_reference_types':dict(photo_types),
            'objects_with_native_video_evidence':native_regions,'native_video_evidence_references':native_refs,'objects_with_rendered_only_evidence':rendered_regions,
            'legacy_video_photo_associations':sum(bool(x.get('photo')) and bool(x.get('photo_source')) for x in a),
            'objects_with_detection_views':sum(bool(x.get('detection_views')) for x in a),'objects_with_merged_from':sum(bool(x.get('merged_from')) for x in a),
            'objects_with_merged_fragments':sum(bool(x.get('merged_fragments')) for x in a),'rendered_gaussians':len(instances),'owned_rendered_rows':int(np.count_nonzero(instances!=65535)),
            'unmapped_with_nonempty_telemetry':sum(not x.get('mapped') and bool(x.get('telemetry',{}).get('latest')) for x in a),
            'manifest_reported_object_splats':twin['scene'].get('splat_binary',{}).get('object_splats'),
            'navigation':{'grid_meta':nav['grid_meta'],'walkable_cells':int(np.asarray(nav['walkable_grid']).sum()),'room_walkable_cells':int(np.asarray(nav.get('room_walkable_grid',nav['walkable_grid'])).sum()),
                          'coverage_record':nav.get('coverage'),'route_count':len(routes),'available_routes':sum(bool(r.get('waypoints')) and r.get('validated',False) for r in routes.values()),
                          'target':{k:v for k,v in nav['primary_route'].items() if k!='waypoints'},
                          'primary_waypoints':len(nav['primary_route']['waypoints'])},
            'floorplan_raster':{'exists':floor.exists(),'dimensions':sizes,'sha256':file_hash(floor) if floor.exists() else None},
            'observed_floor_context':{k:v for k,v in context.get('observed_floor',{}).items() if k!='cells_xz'}}


def compare_navigation(old,new):
    keys=('x_min','z_min','res','nx','nz');m0=old['grid_meta'];m1=new['grid_meta']
    if any(m0[k]!=m1[k] for k in keys):return {'same_grid':False,'cellwise_comparison':None}
    a=np.asarray(old['walkable_grid'],bool);b=np.asarray(new['walkable_grid'],bool)
    return {'same_grid':True,'both_walkable_cells':int((a&b).sum()),'old_walkable_now_blocked':int((a&~b).sum()),'new_walkable_old_blocked':int((~a&b).sum()),
            'both_blocked_cells':int((~a&~b).sum()),'units':'Source units; neither dataset establishes physical scale',
            'interpretation':'Different derived navigation policies and obstacles; more walkable cells is not proof of safer or more correct floor detection.'}


def compare(original_data,current_data,output):
    olddir=Path(original_data);newdir=Path(current_data)
    output=validate_output_path(output,olddir,newdir)
    protected=['digital_twin.json','boiler_room_points.bin','splat_instance.bin','splat_source_rows.npy','source_members.npz']
    hashes_before={n:file_hash(newdir/n) for n in protected}
    old=json.loads((olddir/'digital_twin.json').read_text());new=json.loads((newdir/'digital_twin.json').read_text())
    original_packed,original_inst=read_buffers(olddir);current_packed,current_inst=read_buffers(newdir)
    raw=read_source(newdir/new['scene']['source_ply'],new['scene']['source_sha256'])
    oldrows,alignment,xyz=align_original(raw,original_packed)
    newrows=np.load(newdir / new['scene']['source_rows_file'], allow_pickle=False)
    if len(newrows)!=len(current_packed) or not np.allclose(xyz[newrows],current_packed['xyz'],atol=1e-5,rtol=0):raise ValueError('Current source-row ordering failed verification')
    oo=owner_vector(oldrows,original_inst,len(old['assets']),len(raw));no=owner_vector(newrows,current_inst,len(new['assets']),len(raw))
    matrix=contingency(oo,no,len(old['assets']),len(new['assets']));oldsize=matrix[1:,:].sum(1);newsize=matrix[:,1:].sum(0)
    overlaps=matrix[1:,1:];oldlinks=[];newlinks=[]
    for i,a in enumerate(old['assets']):
        ranking=np.argsort(-overlaps[i]);ranking=ranking[overlaps[i,ranking]>0]
        major=ranking[(overlaps[i,ranking]>=10)&(overlaps[i,ranking]>=.1*oldsize[i])]
        links=[]
        for j in ranking[:12]:
            inter=int(overlaps[i,j]);links.append({'current_id':new['assets'][j]['id'],'current_label':new['assets'][j]['label'],'current_class':new['assets'][j]['class'],
                'shared_source_rows':inter,'fraction_of_original_rows':round(inter/max(1,int(oldsize[i])),5),'fraction_of_current_rows':round(inter/max(1,int(newsize[j])),5),
                'source_row_jaccard':round(inter/max(1,int(oldsize[i]+newsize[j]-inter)),5)})
        oldlinks.append({'original_id':a['id'],'label':a['label'],'class':a['class'],'mapped':a['mapped'],'asset_id':a['asset_id'],'tag':a['tag'],
                         'reported_detector_gaussians':a.get('n_gaussians'),
                         'original_owned_rows':int(oldsize[i]),'rows_owned_by_current_regions':int(overlaps[i].sum()),'rows_currently_unassigned':int(matrix[i+1,0]),
                         'current_region_count_any_overlap':len(ranking),'current_region_count_10pct_overlap':len(major),'links':links,'original_photo':a.get('photo'),'original_cylinder':a.get('cylinder')})
    for j,a in enumerate(new['assets']):
        idx=np.flatnonzero((overlaps[:,j]>=10)&(overlaps[:,j]>=.1*newsize[j]))
        if len(idx)>1:newlinks.append({'current_id':a['id'],'current_label':a['label'],'current_owned_rows':int(newsize[j]),
                                     'original_contributors':[{'original_id':old['assets'][i]['id'],'class':old['assets'][i]['class'],'shared_source_rows':int(overlaps[i,j])} for i in idx]})
    oldmapped={a['asset_id']:(i,a) for i,a in enumerate(old['assets']) if a['mapped']};newmapped={a['asset_id']:(i,a) for i,a in enumerate(new['assets']) if a['mapped']}
    mappings=[]
    for aid,(i,a) in oldmapped.items():
        if aid not in newmapped:continue
        j,b=newmapped[aid];inter=int(overlaps[i,j]);mappings.append({'asset_id':aid,'tag':a['tag'],'original_object':a['id'],'current_object':b['id'],
            'original_center':a['center'],'current_center':b['center'],'center_distance_source_units':round(float(np.linalg.norm(np.array(a['center'])-b['center'])),4),
            'shared_owned_source_rows':inter,'original_owned_rows':int(oldsize[i]),'current_owned_rows':int(newsize[j]),
            'original_reported_detector_gaussians':a.get('n_gaussians'),'current_reported_membership_gaussians':b.get('n_gaussians'),
            'fraction_of_original_rows':round(inter/max(1,int(oldsize[i])),5),'fraction_of_current_rows':round(inter/max(1,int(newsize[j])),5)})
    oldrender=np.zeros(len(raw),bool);oldrender[oldrows]=True;newrender=np.zeros(len(raw),bool);newrender[newrows]=True
    summary={'source_rows':len(raw),'source_sha256':new['scene']['source_sha256'],'original_rendered_rows':len(oldrows),'current_rendered_rows':len(newrows),
             'old_rendered_rows_retained_in_current_render':int((oldrender&newrender).sum()),'old_rendered_rows_missing_from_current_render':int((oldrender&~newrender).sum()),
             'newly_rendered_rows':int((~oldrender&newrender).sum()),'original_owned_rows':int(oldsize.sum()),'current_owned_rows':int(newsize.sum()),
             'owned_by_both_schemes':int(overlaps.sum()),'original_owned_now_unassigned':int(matrix[1:,0].sum()),'newly_owned_not_owned_in_original':int(matrix[0,1:].sum()),
             'original_objects_no_current_owned_overlap':sum(not x['links'] for x in oldlinks),
             'original_objects_with_no_original_GSID_rows':int(np.count_nonzero(oldsize==0)),
             'original_objects_overlap_multiple_current_regions_at_10pct':sum(x['current_region_count_10pct_overlap']>1 for x in oldlinks),
             'current_regions_overlap_multiple_original_objects_at_10pct':len(newlinks),
             'original_objects_with_any_current_owned_overlap':sum(bool(x['links']) for x in oldlinks),
             'original_objects_with_at_least_half_rows_in_single_current_region':sum(bool(x['links']) and x['links'][0]['fraction_of_original_rows']>=.5 for x in oldlinks),
             'original_objects_with_less_than_10pct_rows_owned_in_current':sum(x['rows_owned_by_current_regions']<.1*x['original_owned_rows'] for x in oldlinks),
             'mapped_pairs_with_zero_shared_membership':sum(x['shared_owned_source_rows']==0 for x in mappings)}
    report={'version':1,'generated_at':datetime.now(timezone.utc).isoformat(),'read_only':True,
            'inputs':{'original_data':str(olddir.resolve()),'current_data':str(newdir.resolve()),'original_manifest_sha256':file_hash(olddir/'digital_twin.json'),
                      'original_gspl_sha256':file_hash(olddir/'boiler_room_points.bin'),'original_gsid_sha256':file_hash(olddir/'splat_instance.bin')},
            'alignment':alignment,'original_inventory':inventory(old,olddir,original_inst),'current_inventory':inventory(new,newdir,current_inst),
            'source_row_summary':summary,'navigation_comparison':compare_navigation(old['navigation'],new['navigation']),
            'original_objects':oldlinks,'current_regions_with_multiple_original_contributors':newlinks,'synthetic_mapping_comparison':mappings,
            'limitations':['Original GSID includes bounding-box-filled ownership (pipeline.run.fill_object_membership); it is not pure detector segmentation.',
                           'Current GSID preserves Linards/class-free source-row membership. Neither segmentation is independent ground truth.',
                           'Row overlap, Jaccard and multi-contributor thresholds measure different groupings, not recognition correctness or semantic equivalence.',
                           'Original labels, merged-group identities, photo associations and attached cylinder links were not retained by replacing its asset list with the current one.',
                           'Native-video references must not be compared with rendered proposal crops as equivalent evidence quality.',
                           'Manifest-reported object-splat counts can differ from supplied GSID membership; the separately reported binary counts are authoritative for this audit.',
                           'Synthetic Excel mappings are procedural; equal tags do not imply the same physical object.']}
    hashes_after={n:file_hash(newdir/n) for n in protected}
    if hashes_before!=hashes_after:raise ValueError('Live data changed while auditing; refusing inconsistent comparison')
    report['live_data_unchanged']={'before':hashes_before,'after':hashes_after,'verified':True}
    output=Path(output);output.parent.mkdir(parents=True,exist_ok=True);output.write_text(json.dumps(report,indent=2,allow_nan=False))
    return report


def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--original-data',type=Path,required=True);ap.add_argument('--current-data',type=Path,required=True);ap.add_argument('--output',type=Path,required=True)
    args=ap.parse_args();r=compare(args.original_data,args.current_data,args.output)
    print(json.dumps({'alignment':r['alignment'],'source_row_summary':r['source_row_summary'],'navigation_comparison':r['navigation_comparison']},indent=2))

if __name__=='__main__':main()
