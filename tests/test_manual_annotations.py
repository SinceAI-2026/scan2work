"""Manual masks cannot erase unrelated owners or silently follow another scan."""
import copy
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock
from urllib.request import urlopen
from urllib.error import HTTPError

import numpy as np
from pipeline.manual_annotations import load_annotations, export_native, sha256, WASHER


class ManualAnnotationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        # Each float payload is distinct, including a non-position Gaussian property.
        self.records = np.arange(24, dtype='<f4').reshape(6, 4)
        self.header = b'ply\nformat binary_little_endian 1.0\nelement vertex 6\nproperty float x\nproperty float y\nproperty float z\nproperty float opacity\nend_header\n'
        (self.root/'source_scan.ply').write_bytes(self.header+self.records.tobytes())
        (self.root/'boiler_room_points.bin').write_bytes(b'fixture render bytes')
        np.save(self.root/'splat_source_rows.npy', np.array([5, 0, 3, 2], dtype='<u4'))
        self.spec = copy.deepcopy(WASHER)
        self.spec.update(source_sha256=sha256(self.root/'source_scan.ply'), rendered_rows=4,
                         source_rows=2, raw_source_rows=[0, 3])
        self.spec['annotation']['boundary_status']='unverified'
        self.spec['files'] = {name:sha256(self.root/name) for name in ('boiler_room_points.bin','splat_source_rows.npy')}
        self.twin = {'scene':{'dataset_kind':'wael-original-baseline','source_sha256':self.spec['source_sha256'],
                             'source_ply':'source_scan.ply'}, 'assets':[{'id':'unchanged','mapped':True}],
                     'navigation':{'primary_route':{'waypoints':[[0,0,0]]}}}

    def load(self):
        return load_annotations(self.root,self.twin,spec=self.spec)

    def test_resolves_exact_source_rows_without_mutating_canonical_twin(self):
        before=copy.deepcopy(self.twin); result=self.load(); item=result['annotations'][0]
        self.assertEqual(item['render_indices'],[1,2])
        self.assertEqual(item['source_row_count'],2)
        self.assertEqual(item['boundary_status'],'unverified')
        self.assertFalse(item['mapped']); self.assertNotIn('telemetry',item)
        self.assertFalse(result['canonical_assets_modified']); self.assertEqual(before,self.twin)
        # A selected mask leaves both other source rows untouched; no neighbouring owner expansion.
        self.assertEqual([i for i in range(4) if i not in item['render_indices']],[0,3])

    def test_native_export_preserves_original_bytes_and_all_properties(self):
        raw=export_native(self.root,self.twin,self.spec['id'],spec=self.spec)
        end=raw.index(b'end_header\n')+len(b'end_header\n')
        self.assertEqual(raw[:end],self.header.replace(b'vertex 6',b'vertex 2'))
        self.assertEqual(raw[end:],self.records[[0,3]].tobytes())
        self.assertEqual(sorted(p.name for p in self.root.iterdir()),['boiler_room_points.bin','source_scan.ply','splat_source_rows.npy'])

    def test_wrong_dataset_source_and_changed_render_fail_closed(self):
        for key,value in [('dataset_kind','expanded-extraction'),('source_sha256','different')]:
            with self.subTest(key=key):
                twin=copy.deepcopy(self.twin); twin['scene'][key]=value
                self.assertEqual(load_annotations(self.root,twin,spec=self.spec)['annotations'],[])
        self.assertTrue(self.load()['available'])
        (self.root/'boiler_room_points.bin').write_bytes(b'other render bytes')
        self.assertFalse(self.load()['available'])  # digest cache notices changed file identity

    def test_reordered_source_map_missing_and_duplicate_masks_refused(self):
        np.save(self.root/'splat_source_rows.npy',np.array([0,5,3,2],dtype='<u4'))
        self.assertFalse(self.load()['available'])
        self.spec['files']['splat_source_rows.npy']=sha256(self.root/'splat_source_rows.npy')
        for invalid in ([0,0],[0,4]):
            self.spec['raw_source_rows']=invalid
            self.assertFalse(self.load()['available'])

    def test_native_export_rejects_unknown_id_modified_source_and_out_of_range(self):
        with self.assertRaisesRegex(ValueError,'Unknown'):
            export_native(self.root,self.twin,'OTHER',spec=self.spec)
        (self.root/'source_scan.ply').write_bytes(self.header+self.records[::-1].tobytes())
        with self.assertRaisesRegex(ValueError,'checksum'):
            export_native(self.root,self.twin,self.spec['id'],spec=self.spec)
        self.spec['source_sha256']=sha256(self.root/'source_scan.ply');self.twin['scene']['source_sha256']=self.spec['source_sha256']
        np.save(self.root/'splat_source_rows.npy',np.array([5,0,7,2],dtype='<u4'))
        self.spec['files']['splat_source_rows.npy']=sha256(self.root/'splat_source_rows.npy');self.spec['raw_source_rows']=[0,7]
        with self.assertRaisesRegex(ValueError,'outside native'):
            export_native(self.root,self.twin,self.spec['id'],spec=self.spec)

    def test_native_export_rejects_source_path_escape(self):
        self.twin['scene']['source_ply']='../outside.ply'
        with self.assertRaisesRegex(ValueError,'outside'):
            export_native(self.root,self.twin,self.spec['id'],spec=self.spec)

    def test_public_http_manifest_and_native_download_are_read_only(self):
        os.environ['VALMET_OFFLINE']='1'
        import server
        projection=self.load(); native=export_native(self.root,self.twin,self.spec['id'],spec=self.spec)
        with mock.patch.object(server,'PUBLIC_DEMO',True), mock.patch.object(server,'load_annotations',return_value=projection), \
             mock.patch.object(server,'export_manual_annotation',return_value=native):
            http=server.ThreadingHTTPServer(('127.0.0.1',0),server.TwinRequestHandler)
            thread=threading.Thread(target=http.serve_forever,daemon=True);thread.start()
            try:
                base=f'http://127.0.0.1:{http.server_port}'
                with urlopen(base+'/api/manual-annotations') as response:
                    self.assertEqual(json.load(response)['annotations'][0]['render_indices'],[1,2])
                with urlopen(base+projection['annotations'][0]['export_url']) as response:
                    self.assertEqual(response.read(),native)
                    self.assertIn('MANUAL-WASHER-001.ply',response.headers['Content-Disposition'])
                with self.assertRaises(HTTPError) as err:
                    urlopen(base+'/api/manual-annotations',data=b'{}')
                self.assertEqual(err.exception.code,403)
            finally:
                http.shutdown();http.server_close();thread.join()


class RealManualAnnotationTests(unittest.TestCase):
    @unittest.skipUnless(Path('/Users/perttu/Downloads/SinceAI-2026/Wael-baseline-stage-2026-11-07/digital_twin.json').exists(),'Local original baseline not installed')
    def test_reviewed_mask_preserves_source_identity_and_excludes_neighbour_patch(self):
        root=Path('/Users/perttu/Downloads/SinceAI-2026/Wael-baseline-stage-2026-11-07')
        twin=json.loads((root/'digital_twin.json').read_text());before=copy.deepcopy(twin)
        result=load_annotations(root,twin);self.assertTrue(result['available'],result.get('reason'))
        item=result['annotations'][0];self.assertEqual(len(item['render_indices']),10000)
        self.assertEqual(item['boundary_status'],'manually_reviewed_approximate')
        catalog=json.loads((root/'proposal-review/catalog.json').read_text())
        index=next(i for i,r in enumerate(catalog['regions']) if r['id']=='linards-BOX-0001')
        owners=np.fromfile(root/'proposal-review/projected_instance.bin',dtype='<u2',offset=12)
        chosen=np.zeros(len(owners),dtype=bool);chosen[item['render_indices']]=True
        self.assertEqual(int((chosen&(owners==index)).sum()),1821)
        self.assertEqual(int((~chosen&(owners==index)).sum()),190)
        second=next(i for i,r in enumerate(catalog['regions']) if r['id']=='linards-BOX-0151')
        self.assertEqual(int((chosen&(owners==second)).sum()),198)
        self.assertEqual(item['provenance']['kind'],'manual_geometry_refinement')
        packed=np.memmap(root/'boiler_room_points.bin',dtype=np.dtype([('xyz','<f4',(3,)),('rest','V28')]),offset=16,mode='r')
        xyz=packed['xyz'][chosen]
        self.assertFalse(np.any((xyz[:,0]<-1.11)&(xyz[:,1]<.57)&(xyz[:,2]>.60)))
        np.testing.assert_allclose(xyz.min(0),item['bbox_min'],atol=1e-6)
        np.testing.assert_allclose(xyz.max(0),item['bbox_max'],atol=1e-6)
        # No complete neighbour owner deletion: explicitly preserve rows outside the mask.
        canonical=np.fromfile(root/'splat_instance.bin',dtype='<u2',offset=12)
        for oid in ['OBJ-081','OBJ-141','OBJ-641']:
            owner=next(i for i,a in enumerate(twin['assets']) if a['id']==oid)
            self.assertGreater(int(((canonical==owner)&~chosen).sum()),0)
        for a in twin['assets']:
            if a.get('tag') in {'CV-001','CV-006','VS-003'}:
                self.assertFalse(np.any(((xyz>=np.array(a['aabb_min'])-.03)&(xyz<=np.array(a['aabb_max'])+.03)).all(1)))
        self.assertEqual(twin,before);self.assertEqual(len(twin['assets']),504)
        self.assertEqual(sum(bool(a.get('mapped')) for a in twin['assets']),30)


if __name__=='__main__':unittest.main()
