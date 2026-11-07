"""Original canonical baseline preservation, proposal projection and isolated writes."""
import copy
import json
import os
from pathlib import Path
import struct
import tempfile
import threading
import unittest
from unittest import mock
from urllib.request import Request,urlopen
import numpy as np
os.environ['VALMET_OFFLINE']='1'
from pipeline import build_baseline_demo as B,asset_export as AX,config as C
import server


class BaselineAdapterTests(unittest.TestCase):
    def test_staging_refuses_input_roots_existing_dirs_and_ancestors(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);a=p/'a';b=p/'b';a.mkdir();b.mkdir()
            for bad in (a,a/'child',p):
                with self.assertRaises(ValueError):B.checked_output(bad,a,b)
            self.assertEqual(B.checked_output(p/'stage',a,b),(p/'stage').resolve())

    def test_membership_archive_preserves_detector_order_and_exact_rows(self):
        rows=np.array([20,30,40,50],dtype='u4');ids=np.array([1,65535,0,1],dtype='u2')
        data,counts=B.membership_archive(['OBJ-A','OBJ-B'],rows,ids)
        np.testing.assert_array_equal(counts,[1,2]);np.testing.assert_array_equal(data['source_rows'],[40,20,50])
        np.testing.assert_array_equal(data['offsets'],[0,1,3])

    def test_static_data_routing_uses_active_dataset_only(self):
        handler=server.TwinRequestHandler.__new__(server.TwinRequestHandler);handler.directory=str(server.WEB_DIR)
        with tempfile.TemporaryDirectory() as d,mock.patch.object(C,'OUT_DIR',Path(d)):
            self.assertEqual(handler.translate_path('/data/digital_twin.json'),str(Path(d)/'digital_twin.json'))
            self.assertEqual(handler.translate_path('/app.js'),str(server.WEB_DIR/'app.js'))
            self.assertNotEqual(handler.translate_path('/data/../app.js'),str(Path(d)/'app.js'))

    def test_library_post_preserves_upstream_202_background_contract(self):
        gate=threading.Event();calls=[]
        def save(oid):calls.append(oid);gate.wait(3)
        patches=[mock.patch.object(server,'ASSETS_BY_OBJ',{'OBJ-X':{'id':'OBJ-X'}}),mock.patch.object(server,'SAVING',{}),
                 mock.patch.object(AX,'save',side_effect=save),mock.patch.object(AX,'read_library',return_value=[])]
        with patches[0],patches[1],patches[2],patches[3]:
            http=server.ThreadingHTTPServer(('127.0.0.1',0),server.TwinRequestHandler)
            thread=threading.Thread(target=http.serve_forever,daemon=True);thread.start()
            try:
                url=f'http://127.0.0.1:{http.server_port}/api/library'
                for _ in range(2):
                    req=Request(url,data=b'{"id":"OBJ-X"}',headers={'Content-Type':'application/json'},method='POST')
                    with urlopen(req) as r:self.assertEqual(r.status,202);self.assertIn('saving',json.load(r))
                with urlopen(url) as r:self.assertIn('OBJ-X',json.load(r)['saving'])
                self.assertEqual(calls,['OBJ-X'])
            finally:gate.set();http.shutdown();http.server_close();thread.join()


READY=(C.OUT_DIR/'baseline_report.json').is_file()
ORIGINAL=Path(os.environ.get('VALMET_ORIGINAL_DATA_DIR','/Users/perttu/Downloads/SinceAI-2026/Wael-original-data-2026-11-07/web/data'))
@unittest.skipUnless(READY and (ORIGINAL/'digital_twin.json').is_file(),'point VALMET_DATA_DIR to the staged baseline and VALMET_ORIGINAL_DATA_DIR to the supplied archive')
class BaselineArtifactTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.old=json.loads((ORIGINAL/'digital_twin.json').read_text());cls.new=json.loads((C.OUT_DIR/'digital_twin.json').read_text());cls.cutter=AX.SceneCutter()

    def test_every_original_asset_field_photo_cylinder_binding_and_route_is_preserved(self):
        self.assertEqual(len(self.new['assets']),504)
        for old,new in zip(self.old['assets'],self.new['assets']):
            for k,v in old.items():self.assertEqual(new[k],v,(old['id'],k))
            self.assertNotIn('source_region_id',new)
        self.assertEqual(self.old['navigation'],self.new['navigation'])
        self.assertEqual(sum(a['mapped'] for a in self.new['assets']),30)
        self.assertEqual(sum(bool(a.get('cylinder')) for a in self.new['assets']),83)
        self.assertEqual(sum(bool(a.get('photo')) for a in self.new['assets']),471)
        for name in ('boiler_room_points.bin','splat_instance.bin'):
            self.assertEqual(B.file_hash(ORIGINAL/name),B.file_hash(C.OUT_DIR/name))

    def test_proposals_are_separate_and_projection_uses_exact_source_rows(self):
        side=self.new['proposal_review'];cat=json.loads((C.OUT_DIR/'proposal-review/catalog.json').read_text())
        self.assertEqual(len(cat['regions']),1933)
        self.assertTrue(set(a['id'] for a in self.new['assets']).isdisjoint(a['id'] for a in cat['regions']))
        data=(C.OUT_DIR/'proposal-review/projected_instance.bin').read_bytes();n=side['rendered_rows']
        self.assertEqual(struct.unpack_from('<4sII',data),(b'GSID',1,n));ids=np.frombuffer(data,'<u2',offset=12)
        members=np.load(C.OUT_DIR/'proposal-review/full_source_members.npz');rows=np.load(C.OUT_DIR/'splat_source_rows.npy')
        owners=np.full(1676481,65535,dtype='u2')
        for i in range(1933):owners[members['source_rows'][members['offsets'][i]:members['offsets'][i+1]]]=i
        np.testing.assert_array_equal(ids,owners[rows])
        self.assertEqual(int((ids!=65535).sum()),627926)
        self.assertEqual(sum(r['total_source_rows'] for r in cat['regions']),711568)
        self.assertTrue(all('asset_id' not in r and 'telemetry' not in r for r in cat['regions']))

    def test_original_target_exports_owned_rows_not_detector_count_without_refinement(self):
        c=self.cutter;i,idx,_,_=c._object('OBJ-206');a=c.assets[i]
        self.assertEqual(a['n_gaussians'],346);self.assertEqual(a['exact_owned_gaussians'],1231);self.assertEqual(len(idx),1231)
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);entry=c.cut('OBJ-206',p)
            n,off,dt=AX.read_ply_header(c.source);raw=np.memmap(c.source,dtype=dt,offset=off,shape=(n,),mode='r')
            nn,oo,dd=AX.read_ply_header(p/'OBJ-206.ply');actual=np.fromfile(p/'OBJ-206.ply',dtype=dd,offset=oo)
            self.assertEqual(actual.tobytes(),raw[np.sort(c.rows[idx])].tobytes());self.assertEqual(nn,1231)
            self.assertFalse(entry['model']['refined'])

if __name__=='__main__':unittest.main()
