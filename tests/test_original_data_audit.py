"""Read-only comparison guards and exact ownership contingency semantics."""
import tempfile
import unittest
from pathlib import Path
import numpy as np
from pipeline.audit_original_data import validate_output_path,owner_vector,contingency


class OriginalDataAuditTests(unittest.TestCase):
    def test_output_cannot_overwrite_either_input_or_symlink_into_it(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);old=root/'original';cur=root/'current';old.mkdir();cur.mkdir()
            for p in (old/'digital_twin.json',cur/'nested/report.json'):
                with self.assertRaisesRegex(ValueError,'outside'):validate_output_path(p,old,cur)
            alias=root/'alias';alias.symlink_to(cur,target_is_directory=True)
            with self.assertRaisesRegex(ValueError,'outside'):validate_output_path(alias/'digital_twin.json',old,cur)
            self.assertEqual(validate_output_path(root/'audit.json',old,cur),(root/'audit.json').resolve())

    def test_gsid_context_remains_unassigned_without_uint16_wraparound(self):
        rows=np.array([0,2,4]);ids=np.array([0,65535,1],dtype='<u2')
        np.testing.assert_array_equal(owner_vector(rows,ids,2,6),[0,-1,-1,-1,1,-1])

    def test_duplicate_source_rows_and_invalid_ids_are_rejected(self):
        with self.assertRaisesRegex(ValueError,'Duplicate'):owner_vector(np.array([1,1]),np.array([0,1],dtype='u2'),2,3)
        with self.assertRaisesRegex(ValueError,'index'):owner_vector(np.array([1]),np.array([2],dtype='u2'),2,3)

    def test_overlap_separates_context_split_and_new_ownership(self):
        old=np.array([0,0,0,1,1,-1,-1]);new=np.array([0,1,-1,1,-1,2,-1])
        m=contingency(old,new,2,3)
        self.assertEqual(m.shape,(3,4))
        self.assertEqual(int(m.sum()),7)
        self.assertEqual(int(m[1:,1:].sum()),3)
        self.assertEqual(int(m[1:,0].sum()),2)
        self.assertEqual(int(m[0,1:].sum()),1)
        self.assertEqual(list(m[1,1:]),[1,1,0])

if __name__=='__main__':unittest.main()
