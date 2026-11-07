"""Original Wael defaults and offline imported-scene isolation, without services."""
import json
import os
from pathlib import Path
import struct
import tempfile
import types
import unittest
from unittest import mock

os.environ['VALMET_OFFLINE'] = '1'
import numpy as np
from pipeline.runtime_policy import configure_server_runtime
from pipeline import asset_export as AX, config as C
import server


class RuntimeCompatibilityTests(unittest.TestCase):
    def policy(self, twin, env=None):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data = root / 'web' / 'data'
            data.mkdir(parents=True)
            if twin is not None:
                (data / 'digital_twin.json').write_text(json.dumps(twin))
            environment = dict(env or {})
            result = configure_server_runtime(root, environment)
            return result, environment

    def test_original_scene_keeps_legacy_default_and_explicit_online_setting(self):
        for twin in (None, {'scene': {}, 'assets': []}, {'assets': [{'id': 'OBJ-206', 'mapped': True}]}):
            offline, env = self.policy(twin)
            self.assertFalse(offline)
            self.assertNotIn('VALMET_OFFLINE', env)
            offline, env = self.policy(twin, {'VALMET_OFFLINE': '0'})
            self.assertFalse(offline)
            self.assertEqual(env['VALMET_OFFLINE'], '0')

    def test_imported_and_offline_manifest_cannot_enable_external_models(self):
        for twin in (
            {'runtime': {'offline': True}},
            {'runtime': {'external_models_enabled': False}},
            {'scene': {'membership_mode': 'exact-source-rows'}},
            {'scene': {'dataset_kind': 'wael-original-baseline'}},
            {'assets': [{'source_region_id': 'region-1'}]},
        ):
            for env in ({}, {'VALMET_OFFLINE': '0'}):
                offline, actual = self.policy(twin, env)
                self.assertTrue(offline)
                self.assertEqual(actual['VALMET_OFFLINE'], '1')

    def test_explicit_offline_never_reads_manifest(self):
        with mock.patch.object(Path, 'read_text', side_effect=AssertionError('no manifest access needed')):
            self.assertTrue(configure_server_runtime(Path('/unused'), {'VALMET_OFFLINE': '1'}))

    def test_selected_data_directory_is_used_instead_of_default_scene(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); selected = root / 'selected'; selected.mkdir()
            (selected / 'digital_twin.json').write_text('{"runtime":{"offline":true}}')
            env = {'VALMET_DATA_DIR': str(selected)}
            self.assertTrue(configure_server_runtime(root, env))
            self.assertEqual(env['VALMET_OFFLINE'], '1')

    def test_malformed_policy_fails_before_configuration_import(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); data = root / 'web' / 'data'; data.mkdir(parents=True)
            (data / 'digital_twin.json').write_text('{broken')
            with self.assertRaisesRegex(ValueError, 'runtime policy'):
                configure_server_runtime(root, {})

    def test_server_preserves_optional_gemma_online_and_disables_it_offline(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest = Path(tmp) / 'digital_twin.json'
            manifest.write_text('{"assets":[]}')
            for offline in (False, True):
                with mock.patch.object(C, 'OFFLINE', offline), \
                     mock.patch.object(server, 'DATA_PATH', manifest), \
                     mock.patch.object(server.retrieval, 'load_saved', return_value=None) as load, \
                     mock.patch.object(server.field_service, 'FieldService'), \
                     mock.patch.object(server, 'TWIN_DATA', {}), \
                     mock.patch.object(server, 'ASSETS_BY_ID', {}), \
                     mock.patch.object(server, 'ASSETS_BY_OBJ', {}), \
                     mock.patch.object(server, 'RETRIEVER', None), \
                     mock.patch.object(server, 'FIELD', None, create=True):
                    server.load_data()
                    load.assert_called_once_with(try_gemma=not offline)

    def test_unmarked_scene_keeps_legacy_source_and_refinement_export_branch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); out = root / 'saved'
            asset = {'id': 'OBJ-206', 'n_gaussians': 25}
            (root / 'digital_twin.json').write_text(json.dumps({'assets': [asset]}))
            packed = np.zeros(25, AX.GSPL)
            packed['xyz'] = np.random.default_rng(0).uniform(.1, .3, (25, 3))
            AX.write_gspl(packed, root / 'boiler_room_points.bin')
            (root / 'splat_instance.bin').write_bytes(struct.pack('<4sII', b'GSID', 1, 25) + np.zeros(25, '<u2').tobytes())
            legacy_source = root / 'legacy-source.ply'
            fake_refine = types.ModuleType('pipeline.object_refine')
            fake_refine.refine = mock.Mock(return_value=(packed, {'splats': 25}))
            fake_refine.write_3dgs_ply = mock.Mock()
            with mock.patch.object(AX, 'DATA', root), mock.patch.object(C, 'PLY_PATH', legacy_source), \
                 mock.patch.object(AX, 'source_rows', return_value=np.arange(25)) as rows, \
                 mock.patch.object(AX, 'own_splats', return_value=(np.arange(25), 0, 0)), \
                 mock.patch.dict('sys.modules', {'pipeline.object_refine': fake_refine}), \
                 mock.patch.object(AX, 'write_ply_rows') as write_native, \
                 mock.patch.object(AX, 'thumbnail'), \
                 mock.patch.object(AX, 'alpha_mesh', return_value=np.empty((0, 3), dtype=int)):
                cutter = AX.SceneCutter()
                self.assertFalse(cutter.exact)
                self.assertEqual(cutter.source, legacy_source)
                self.assertEqual(rows.call_args.args[0], legacy_source)
                entry = cutter.cut('OBJ-206', out)
                fake_refine.refine.assert_called_once_with(cutter, 'OBJ-206')
                fake_refine.write_3dgs_ply.assert_called_once()
                self.assertEqual(write_native.call_args.args[0], legacy_source)
                self.assertEqual(entry['files']['model_ply'], 'OBJ-206/OBJ-206_model.ply')
                self.assertNotIn('membership_mode', entry)


if __name__ == '__main__':
    unittest.main()
