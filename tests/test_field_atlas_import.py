"""Offline importer invariants and real-artifact integration checks (no AI or rendering calls)."""
import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock
import numpy as np
os.environ['VALMET_OFFLINE']='1'
from pipeline import import_field_atlas as I, asset_export as AX, decisions_client as DC
import server


class ImportValidationTests(unittest.TestCase):
    def fixture(self):
        scene={'coordinateFrame':'original-ply-y-down','sourceRows':4,'objects':[
            {'id':'first','sourceIds':[7],'count':2},{'id':'second','sourceIds':[3],'count':1}]}
        owners=np.array([7,0,3,7],dtype='<u2')
        members={'source_rows':np.array([0,3,2]),'offsets':np.array([0,2,3]),'object_ids':np.array(['first','second']), 'owner_ids':np.array([7,3])}
        return scene,owners,members

    def test_exact_ids_do_not_assume_owner_equals_array_index(self):
        s,o,m=self.fixture()
        np.testing.assert_array_equal(I.validate_membership(s,o,m,4),[0,65535,1,0])

    def test_reordered_manifest_is_rejected(self):
        s,o,m=self.fixture(); s['objects'].reverse()
        with self.assertRaisesRegex(ValueError,'order'): I.validate_membership(s,o,m,4)

    def test_foreign_row_or_overlapping_membership_is_rejected(self):
        s,o,m=self.fixture(); m['source_rows'][1]=2
        with self.assertRaisesRegex(ValueError,'overlap'): I.validate_membership(s,o,m,4)
        s,o,m=self.fixture(); o[0]=3
        with self.assertRaisesRegex(ValueError,'mismatch'): I.validate_membership(s,o,m,4)

    def test_source_hash_checked_before_reading_rows(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'source.ply'; p.write_bytes(b'wrong scene')
            with self.assertRaisesRegex(ValueError,'SHA256'): I.read_source(p,'0'*64,4)

    def test_video_evidence_hash_mismatch_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            demo=Path(d)/'demo';(demo/'data').mkdir(parents=True)
            (demo/'data/crop.jpg').write_bytes(b'unrelated image')
            asset={'evidence':[{'cropUrl':'data/crop.jpg','cropSha256':'0'*64}]}
            with self.assertRaisesRegex(ValueError,'Evidence SHA256'):
                I.copy_evidence(demo,Path(d)/'output',[asset])

    def test_coordinate_transform_is_rigid_and_preserves_anchor(self):
        p=np.array([[2.12,.125,1.6],[0,1.185,0],[1,2,3]])
        q=I.to_levelled(p)
        np.testing.assert_allclose(q[0],[2.12,1.06,-1.6])
        np.testing.assert_allclose(q[1],[0,0,0])
        self.assertAlmostEqual(float(np.linalg.norm(q[2]-q[0])),float(np.linalg.norm(p[2]-p[0])))

    def test_unmapped_copilot_never_borrows_target_telemetry(self):
        a={'id':'region','asset_id':'REGION-region','tag':'region','mapped':False,'diagnostics':I.unmapped_diagnostics('part')}
        with mock.patch.object(server,'ASSETS_BY_ID',{'REGION-region':a}), mock.patch.object(server,'ASSETS_BY_OBJ',{'region':a}):
            for intent in ('root_cause','telemetry','safety','other'):
                res=server._answer('REGION-region',intent)
                self.assertEqual(res['citations'],[]); self.assertFalse(res['mapped'])
                self.assertIn('unknown',res['answer'])
            self.assertIn('error',server._answer('missing','other'))

    def test_offline_guard_ignores_even_available_credentials(self):
        with mock.patch.dict(os.environ,{'OPENAI_API_KEY':'test-do-not-use'}), mock.patch('urllib.request.urlopen',side_effect=AssertionError('network forbidden')):
            self.assertIsNone(DC.decide('x',[])); self.assertFalse(DC.enabled())

    def test_failed_import_does_not_overwrite_active_manifest(self):
        with tempfile.TemporaryDirectory() as d:
            out=Path(d)/'data';out.mkdir();(out/'digital_twin.json').write_text('original')
            with self.assertRaises(ValueError): I.import_bundle(Path(d),None,Path(d)/'none.xlsx',out)
            self.assertEqual((out/'digital_twin.json').read_text(),'original')

    def test_mid_build_failure_preserves_existing_binary_and_manifest(self):
        with tempfile.TemporaryDirectory() as d:
            out=Path(d)/'data';out.mkdir()
            for name in ('digital_twin.json','boiler_room_points.bin'):
                (out/name).write_bytes(b'previous valid artifact')
            def failed_builder(bundle, source, workbook, stage, target):
                (stage/'boiler_room_points.bin').write_bytes(b'partially rebuilt scan')
                raise ValueError('Injected missing evidence after binary generation')
            with mock.patch.object(I,'_build_bundle',side_effect=failed_builder):
                with self.assertRaisesRegex(ValueError,'Injected'): I.import_bundle(Path(d),None,Path(d)/'pack.xlsx',out)
            for name in ('digital_twin.json','boiler_room_points.bin'):
                self.assertEqual((out/name).read_bytes(),b'previous valid artifact')


DATA=AX.DATA
@unittest.skipUnless((DATA/'import_report.json').exists(),'run the offline import to check real artifacts')
class ImportedArtifactTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.twin=json.loads((DATA/'digital_twin.json').read_text());cls.cutter=AX.SceneCutter()

    def test_unique_ids_and_no_invented_telemetry(self):
        assets=self.twin['assets'];self.assertEqual(len(assets),1933)
        self.assertEqual(len({a['asset_id'] for a in assets}),len(assets))
        self.assertEqual(sum(a['mapped'] for a in assets),30)
        for a in assets:
            if not a['mapped']:
                self.assertEqual(a['telemetry'],{'latest':{},'stats':{},'series':{}})
                self.assertIsNone(a['health_score']);self.assertEqual(a['condition_state'],'unknown')
                self.assertEqual(a['diagnostics']['hypotheses'],[])

    def test_native_library_export_is_exact_and_does_not_import_refinement(self):
        oid=self.twin['target_object_id'];c=self.cutter;i,idx,_,_=c._object(oid)
        with tempfile.TemporaryDirectory() as d:
            out=Path(d);entry=c.cut(oid,out)
            n,off,dt=AX.read_ply_header(c.source)
            source=np.memmap(c.source,dtype=dt,mode='r',offset=off,shape=(n,))
            nn,oo,dd=AX.read_ply_header(out/f'{oid}.ply')
            actual=np.fromfile(out/f'{oid}.ply',dtype=dd,offset=oo)
            self.assertEqual(nn,len(idx));self.assertEqual(actual.tobytes(),source[np.sort(c.rows[idx])].tobytes())
            self.assertIsNone(entry['files']['model_ply']);self.assertIsNone(entry['files']['glb'])
            self.assertEqual(entry['left_to_room'],{'floor':0,'floaters':0})

    def test_all_members_retained_in_render_ownership(self):
        from collections import Counter
        c=self.cutter
        counts=np.diff(c.bounds)
        np.testing.assert_array_equal(counts,[a['n_gaussians'] for a in self.twin['assets']])
        self.assertEqual(int(counts.sum()),711568)

    def test_all_available_routes_stay_in_saved_observed_grid(self):
        nav=self.twin['navigation'];meta=nav['grid_meta']
        grid={**meta,'dilated_occ':~np.array(nav['walkable_grid'],bool)}
        for route in nav['routes_by_asset'].values():
            if route['validated']:
                points=[[w['position'][0],w['position'][2]] for w in route['waypoints']]
                self.assertTrue(I.N.trajectory_is_walkable(points,grid),route['asset_id'])
            else:self.assertEqual(route['waypoints'],[])
        self.assertFalse(nav['scale_verified'])

if __name__=='__main__':unittest.main()
