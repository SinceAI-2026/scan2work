"""Derived context must enrich display without claiming new membership or route safety."""
import json
import os
import tempfile
import unittest
from pathlib import Path
import numpy as np
os.environ['VALMET_OFFLINE']='1'
from pipeline import derived_context as D
from pipeline import config as C


class ContextUnitTests(unittest.TestCase):
    def test_only_existing_baseline_names_are_normalized(self):
        a={'label':'P-001 · Pump','class':'unclassified','provenance':{'preservedBaseline':True}}
        self.assertTrue(D.normalize_retained_label(a));self.assertEqual(a['class'],'circulation_pump')
        self.assertFalse(a['label_verified']);self.assertEqual(a['imported_catalog_class'],'unclassified')
        unknown={'label':'Pump','class':'unclassified','provenance':{'preservedBaseline':False}}
        self.assertFalse(D.normalize_retained_label(unknown));self.assertEqual(unknown['class'],'unclassified')

    def test_container_hint_does_not_invent_equipment_status(self):
        a={'label':'Bucket','class':'unclassified','condition_state':'unknown','mapped':False,'provenance':{'preservedBaseline':True}}
        D.normalize_retained_label(a)
        self.assertTrue(a['movable']);self.assertFalse(a['mapped']);self.assertEqual(a['condition_state'],'unknown')

    def test_wall_guides_explicitly_remain_assumptions(self):
        walls=D.wall_guides();self.assertEqual(len(walls),4)
        for w in walls:self.assertTrue(w['assumption']);self.assertFalse(w['measured'])

    def test_observed_floor_does_not_fill_unknown_gap(self):
        xyz=np.array([[0.,.02,0.]]*3+[[1.,.02,0.]]*3+[[.5,.4,0.]]*3)
        raw={'xyz':xyz,'opacity':np.ones(9),'scale':np.ones(9)*.02}
        floor=D.observed_floor(raw)
        self.assertEqual(floor['supported_cells'],2)
        self.assertFalse(floor['navigation_changed'])
        self.assertTrue(all(abs(p[0]-.5)>.2 for p in floor['cells_xz']))


DATA=C.OUT_DIR
@unittest.skipUnless((DATA/'derived_context.json').exists(),'run the imported scene context derivation')
class ContextArtifactTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.twin=json.loads((DATA/'digital_twin.json').read_text());cls.ctx=cls.twin['scene']['derived_context']

    def test_navigation_and_canonical_source_are_preserved(self):
        t=self.twin;r=self.ctx['recovery_report']
        self.assertEqual(r['navigation_sha256_before'],r['navigation_sha256_after'])
        self.assertEqual(D.digest_json(t['navigation']),r['navigation_sha256_before'])
        self.assertEqual(r['protected_file_sha256_before'],r['protected_file_sha256_after'])
        self.assertEqual(len(r['protected_file_sha256_before']),4)
        self.assertFalse(r['canonical_geometry_changed']);self.assertFalse(r['membership_changed'])
        self.assertEqual(len(t['assets']),1933)
        self.assertEqual(sum(a['n_gaussians'] for a in t['assets']),711568)
        self.assertEqual(sum(a['mapped'] for a in t['assets']),30)
        self.assertEqual(sum(bool(r['validated']) for r in t['navigation']['routes_by_asset'].values()),28)
        self.assertEqual(t['target_object_id'],'linards-BOX-0173')

    def test_all_fits_are_context_and_all_original_regions_have_proxies(self):
        ctx=self.ctx;ids={a['id'] for a in self.twin['assets']}
        self.assertGreater(len(ctx['pipe_segments']),0)
        self.assertEqual(len(ctx['walls']),4)
        for c in ctx['pipe_segments']:
            self.assertNotIn(c['id'],ids);self.assertGreater(c['radius'],0)
            self.assertGreaterEqual(c['arc_deg'],90);self.assertGreaterEqual(c['support'],30)
            self.assertFalse(c['semantic_verified']);self.assertTrue(set(c['region_ids']).issubset(ids))
        for a in self.twin['assets']:
            self.assertIn('geometry_proxy',a);self.assertFalse(a['geometry_proxy']['changes_membership'])
        self.assertTrue(any(a['geometry_proxy']['yaw']!=0 for a in self.twin['assets']))

    def test_normalization_preserves_unreviewed_semantics(self):
        normalized=[a for a in self.twin['assets'] if a.get('label_normalization')]
        self.assertEqual(len(normalized),415)
        for a in normalized:self.assertFalse(a['label_verified']);self.assertEqual(a['review_status'],'unreviewed')
        self.assertEqual(sum(a['class']=='unclassified' for a in self.twin['assets']),1514)

if __name__=='__main__':unittest.main()
