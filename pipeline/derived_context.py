"""Restore derived plan/BIM context around immutable imported Gaussian memberships.

Offline geometry only: retained-label normalization, Wael OBB/cylinder fitting,
observed floor samples and explicitly assumed legacy wall guides. These layers do
not change ownership, navigation, source positions or the synthetic device map.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
os.environ['VALMET_OFFLINE']='1'
import numpy as np
from scipy import ndimage
from scipy.spatial import cKDTree
from PIL import Image
from . import pipes
from .perception import _compute_obb_2d
from .asset_export import read_ply_header
from .ingest import FLOOR_Y_PLY, SH_C0

METHOD_VERSION='wael-context-v1'
LABEL_CLASSES={
    'valve':'control_valve', 'pump':'circulation_pump', 'motor':'electric_motor',
    'pressure transmitter':'pressure_sensor','vibration sensor':'vibration_sensor',
    'pressure vessel':'expansion_vessel','expansion vessel':'expansion_vessel','tank':'expansion_vessel',
    'electrical cabinet':'electrical_cabinet','control panel':'electrical_cabinet',
    'radiator':'heat_exchanger','washing machine':'appliance','door':'door',
    'bucket':'movable_container','plastic pail':'movable_container','spray bottle':'movable_container',
    'roll container':'movable_cart','sack truck':'movable_cart','hand truck':'movable_cart',
    'wire cage cart':'movable_cart','pallet jack':'movable_cart','cardboard box':'movable_crate',
    'plastic crate':'movable_crate','coil of cable':'movable_hose','cable reel':'movable_hose',
    'fire extinguisher':'fire_extinguisher',
}


def digest_file(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda:f.read(4*1024*1024),b''):h.update(chunk)
    return h.hexdigest()


def digest_json(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def normalize_retained_label(asset):
    """Normalize existing names only; never classify an unknown proposal by geometry or Excel tag."""
    if not asset.get('provenance',{}).get('preservedBaseline'):
        return False
    original=asset['label'].split('·')[-1].strip().lower()
    cls=LABEL_CLASSES.get(original)
    if cls is None:return False
    asset.setdefault('imported_catalog_class',asset['class'])
    asset['class']=cls
    asset['scene_class']=cls
    asset['movable']=cls.startswith('movable_')
    asset['label_source']='upstream_catalog_normalization'
    asset['label_verified']=False
    asset['label_normalization']={'input':asset['label'],'class':cls,'method':'Vocabulary normalization of retained Linards name; no new model inference', 'verified':False}
    return True


def wall_guides():
    segments=[('North Process Manifold Wall',[-4.55,3.48],[3.95,3.48]),
              ('South Expansion Vessel Wall',[-4.55,-3.35],[3.95,-3.35]),
              ('West Hydronic Pump Wall',[-4.55,-3.35],[-4.55,3.48]),
              ('East Utility Header Wall',[3.95,-3.35],[3.95,3.48])]
    return [{'id':f'wael-wall-guide-{i+1}','name':name,'from':a,'to':b,'assumption':True,'measured':False,
             'source':'Original Wael pipeline/perception.py configured room guide',
             'meaning':'Schematic context boundary; not wall detection or survey geometry', 'units':'unverified source units'}
            for i,(name,a,b) in enumerate(segments)]


def read_raw(path):
    n,offset,dtype=read_ply_header(path)
    raw=np.memmap(path,mode='r',dtype=dtype,offset=offset,shape=(n,))
    original=np.stack([raw[k] for k in ('x','y','z')],1)
    xyz=original*[1,-1,-1]+[0,FLOOR_Y_PLY,0]
    scales=np.exp(np.clip(np.stack([raw[f'scale_{i}'] for i in range(3)],1),-30,20))
    op=1/(1+np.exp(-np.clip(raw['opacity'],-20,20)))
    rots=np.stack([raw[f'rot_{i}'] for i in (1,2,3,0)],1)
    rgb=np.clip(.5+SH_C0*np.stack([raw[f'f_dc_{i}'] for i in range(3)],1),0,1)
    return {'xyz_raw':original,'xyz':xyz,'scales_3d':scales,'scale':scales.mean(1), 'opacity':op,'rots':rots,'rgb':rgb}


def observed_floor(raw):
    xyz=raw['xyz'];res=.05;x0,z0=-4.75,-3.55;nx,nz=178,146
    # This is a display of low observed samples, deliberately broader than the
    # independent navigation floor gate. No closing, hole filling or free-space inference.
    use=(raw['opacity']>.1)&(raw['scale']<.35)&(xyz[:,1]>-.06)&(xyz[:,1]<.10)
    ij=np.floor((xyz[use][:,[0,2]]-[x0,z0])/res).astype(int)
    valid=(ij[:,0]>=0)&(ij[:,0]<nx)&(ij[:,1]>=0)&(ij[:,1]<nz)
    counts=np.zeros((nx,nz),int);np.add.at(counts,tuple(ij[valid].T),1)
    cells=np.argwhere(counts>=3)
    return {'cells_xz':np.round([x0,z0]+(cells+.5)*res,4).tolist(),'res':res,'source_points':int(valid.sum()),
            'supported_cells':len(cells),'minimum_samples_per_cell':3,'source':'Original Gaussian samples at levelled heights -0.06 to 0.10; opacity>0.1, mean scale<0.35',
            'meaning':'Observed low-surface context only; holes remain unknown, not a walkability or ground-truth claim',
            'units':'unverified source units','navigation_changed':False}


def fit_pipe_context(raw, assets, memberships):
    """Reuse original Wael cylinder voting, without its membership-changing integrate()."""
    segs=pipes.fit_segments(raw)
    centres,normals=pipes.surface_normals(raw)
    s=np.sort(raw['scales_3d'],axis=1)
    use=(s[:,0]<pipes.FLAT*s[:,1])&(raw['opacity']>pipes.MIN_OPACITY)&(raw['xyz']>=pipes.ROI_LO).all(1)&(raw['xyz']<=pipes.ROI_HI).all(1)
    source_rows=np.flatnonzero(use)
    if len(source_rows)!=len(centres):raise ValueError('Pipe support source row alignment failed')
    owner=np.full(len(raw['xyz']),-1,np.int32)
    for i,a in enumerate(assets):
        row=memberships['source_rows'][memberships['offsets'][i]:memberships['offsets'][i+1]]
        owner[row]=i
    tree=cKDTree(centres)
    out=[]
    for seg in sorted(segs,key=lambda s:(s['axis'],*s['start'])):
        if seg['axis']!='y' and (seg['start'][1]+seg['end'][1])/2<seg['radius']+.05:continue
        start,end=seg['start'],seg['end'];direction=(end-start)/np.linalg.norm(end-start);mid=(start+end)/2
        ix=np.array(tree.query_ball_point(mid,seg['length']/2+seg['radius']+.03),int)
        delta=centres[ix]-start;along=delta@direction;radial=delta-along[:,None]*direction
        radius=np.linalg.norm(radial,axis=1);unit=radial/np.maximum(radius,1e-9)[:,None]
        supports=(along>=0)&(along<=seg['length'])&(abs(radius-seg['radius'])<=max(pipes.MIN_BAND,pipes.BAND*seg['radius']))&(np.abs((normals[ix]*unit).sum(1))>pipes.ALIGNED)
        support_rows=source_rows[ix[supports]];own=owner[support_rows];bound=own[own>=0]
        counts=np.bincount(bound,minlength=len(assets)) if len(bound) else np.zeros(len(assets),int)
        related=np.flatnonzero(counts>=max(5,.1*len(support_rows)))
        c=pipes._cyl(seg)
        c.update(id=f'wael-pipe-context-{len(out)+1:03d}',source='Wael pipeline/pipes.py surface-normal cylinder voting on original Gaussian attributes',
                 review_status='unreviewed',semantic_verified=False,scale_verified=False,
                 support_source_rows=len(support_rows),region_ids=[assets[i]['id'] for i in related],
                 meaning='Fitted geometry hypothesis; not a newly segmented asset or verified pipe identity')
        out.append(c)
    return out


def export_context_raster(raw, navigation, path):
    m=navigation['grid_meta'];scale=4;w,h=m['nx']*scale,m['nz']*scale;xyz=raw['xyz']
    ix=np.floor((xyz[:,0]-m['x_min'])/m['res']*scale).astype(int)
    iz=np.floor((xyz[:,2]-m['z_min'])/m['res']*scale).astype(int)
    keep=(ix>=0)&(ix<w)&(iz>=0)&(iz<h)&(xyz[:,1]>=-.06)&(xyz[:,1]<=1.72)&(raw['opacity']>.1)&(raw['scale']<.35)
    idx=np.flatnonzero(keep);idx=idx[np.argsort(xyz[idx,1])]
    raster=np.full((h,w,3),[223,225,216],np.uint8);hit=np.zeros((h,w),bool)
    yy,xx=h-1-iz[idx],ix[idx];raster[yy,xx]=(raw['rgb'][idx]*255).astype('u1');hit[yy,xx]=True
    # Match Wael's original <=1.5-pixel display interpolation. It never modifies
    # the measured-floor samples or robot occupancy map.
    distance,nearest=ndimage.distance_transform_edt(~hit,return_indices=True)
    fill=(~hit)&(distance<=1.5);raster[fill]=raster[nearest[0][fill],nearest[1][fill]]
    Image.fromarray(raster).save(path)
    return {'source_points':len(idx),'size':[w,h],'maximum_display_gap_fill_pixels':1.5,
            'meaning':'Orthographic native-colour scan context, not a free-space or wall estimate'}


def enrich(twin, data_dir, *, cached=None):
    data_dir=Path(data_dir);source=data_dir/twin['scene']['source_ply'];source_sha=twin['scene']['source_sha256']
    if digest_file(source)!=source_sha:raise ValueError('Source PLY SHA256 changed; refusing geometry-context derivation')
    before_nav=digest_json(twin['navigation'])
    protected_files=('boiler_room_points.bin','splat_instance.bin',twin['scene']['source_rows_file'],twin['scene']['members_file'])
    before_files={name:digest_file(data_dir/name) for name in protected_files}
    canonical=[{k:a[k] for k in ('id','asset_id','source_region_id','center','size','yaw','mapped','is_target','n_gaussians')} for a in twin['assets']]
    prior=twin['scene'].get('derived_context',{}).get('recovery_report',{})
    before={'regions':len(twin['assets']),'walls':len(twin['scene'].get('walls',[])),
            'pipe_cylinders':len(twin['scene'].get('derived_context',{}).get('pipe_segments',[]))+sum(bool(a.get('cylinder')) for a in twin['assets']),
            'nonzero_canonical_yaw':sum(bool(a['yaw']) for a in twin['assets']),
            'unclassified_regions':sum(a['class']=='unclassified' for a in twin['assets'])}
    raw=read_raw(source);memberships=dict(np.load(data_dir/twin['scene']['members_file'],allow_pickle=False))
    normalized=0;obb_rotated=0
    for i,a in enumerate(twin['assets']):
        normalized+=normalize_retained_label(a)
        rows=memberships['source_rows'][memberships['offsets'][i]:memberships['offsets'][i+1]]
        obb=_compute_obb_2d(raw['xyz'][rows]);obb_rotated+=bool(obb['yaw'])
        a['geometry_proxy']={'kind':'oriented_envelope','center':obb['center'],'size':obb['size'],'yaw':obb['yaw'],
                             'source':'Wael perception._compute_obb_2d on exact owned source rows (3rd–97th percentiles + padding)',
                             'meaning':'Approximate display envelope, not reconstructed solid geometry; exact membership remains authoritative',
                             'scale_verified':False,'changes_membership':False}
    if (cached and cached.get('method_version')==METHOD_VERSION and cached.get('source_sha256')==source_sha
            and cached.get('membership_sha256')==before_files[twin['scene']['members_file']]):
        cylinders=cached['pipe_segments']
    else:
        cylinders=fit_pipe_context(raw,twin['assets'],memberships)
    floor=observed_floor(raw);walls=wall_guides()
    raster=export_context_raster(raw,twin['navigation'],data_dir/'floorplan_scan.png')
    context={'version':1,'method_version':METHOD_VERSION,'source_sha256':source_sha,'units':'unverified source units',
             'membership_sha256':before_files[twin['scene']['members_file']],
             'walls':walls,'pipe_segments':cylinders,'observed_floor':floor,'floorplan_raster':raster,
             'membership_changed':False,'navigation_changed':False,
             'limits':['Cylinder fits and normalized catalog labels are unreviewed suggestions.',
                       'Configured wall guides are retained assumptions, not measured walls.',
                       'Observed floor context is distinct from the conservative navigation grid.']}
    twin['scene']['derived_context']=context;twin['scene']['walls']=walls
    twin['scene']['class_breakdown']={k:sum(a['class']==k for a in twin['assets']) for k in sorted({a['class'] for a in twin['assets']})}
    after_canonical=[{k:a[k] for k in canonical[0]} for a in twin['assets']]
    if canonical!=after_canonical or before_nav!=digest_json(twin['navigation']):raise ValueError('Context recovery changed canonical geometry or navigation')
    after_files={name:digest_file(data_dir/name) for name in protected_files}
    if before_files!=after_files:raise ValueError('Context recovery changed exact source membership or renderer files')
    report={'source_sha256':source_sha,'source_preserved':True,'before':before,
            'initial_import_before_recovery':prior.get('initial_import_before_recovery',prior.get('before',before)),
            'after':{'regions':len(twin['assets']),'normalized_catalog_labels':normalized,
                     'unclassified_regions':sum(a['class']=='unclassified' for a in twin['assets']),
                     'wall_guides':len(walls),'fitted_pipe_context_segments':len(cylinders),'oriented_geometry_proxies':len(twin['assets']),
                     'nonzero_proxy_yaw':obb_rotated,'observed_floor_cells':floor['supported_cells'],'observed_floor_points':floor['source_points']},
            'upstream_comparison':{'commit':'ab777d3c94182780cc3528fe1373e5b79e3fd110','published_objects':504,'published_classes':24,
                                   'source':'Upstream README historical report; original generated artifact unavailable locally',
                                   'exact_old_object_overlap':None},
            'navigation_sha256_before':before_nav,'navigation_sha256_after':digest_json(twin['navigation']),
            'protected_file_sha256_before':before_files,'protected_file_sha256_after':after_files,
            'membership_changed':False,'canonical_geometry_changed':False,'recognition_accuracy':None}
    context['recovery_report']=report
    (data_dir/'derived_context.json').write_text(json.dumps(context,separators=(',',':'),allow_nan=False))
    (data_dir/'context_recovery_report.json').write_text(json.dumps(report,indent=2))
    return report


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--data-dir',type=Path,default=Path(__file__).resolve().parents[1]/'web/data')
    args=ap.parse_args();p=args.data_dir/'digital_twin.json';twin=json.loads(p.read_text())
    cached_path=args.data_dir/'derived_context.json'
    cached=json.loads(cached_path.read_text()) if cached_path.exists() else None
    report=enrich(twin,args.data_dir,cached=cached)
    tmp=p.with_suffix('.json.tmp');tmp.write_text(json.dumps(twin,separators=(',',':'),allow_nan=False));tmp.replace(p)
    print(json.dumps(report,indent=2))

if __name__=='__main__':main()
