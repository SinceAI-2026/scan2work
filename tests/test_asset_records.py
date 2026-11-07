"""Archived borrowed profiles cannot become operational device readings."""
import copy
import json
import os
import threading
import unittest
from unittest import mock
from urllib.request import urlopen

os.environ['VALMET_OFFLINE'] = '1'
from pipeline.asset_records import asset_for_api, route_for_api, UNMAPPED_NOTICE
from pipeline.knowledge import bind_assets_to_scene
import server


class AssetRecordTests(unittest.TestCase):
    def setUp(self):
        self.asset = {
            'id': 'OBJ-999', 'asset_id': 'AUX-999', 'tag': 'AV-012', 'mapped': False,
            'class': 'control_valve', 'label': 'Valve', 'asset_class': 'CONTROL_VALVE',
            'center': [1, 2, 3], 'size': [.2, .3, .4], 'photo': 'data/photos/OBJ-999.jpg',
            'condition_state': 'healthy', 'health_score': 97,
            'excel_profile_source': 'VALVE-001', 'excel_profile_tag': 'CV-001',
            'telemetry': {'latest': {'pressure': 12}, 'stats': {}, 'series': {}},
            'alarms': [{'id': 'AL-1'}], 'maintenance_history': [{'id': 'MH-1'}],
            'diagnostics': {'executive_summary': 'Borrowed healthy profile', 'work_order_draft': {'id': 'WO-1'}},
        }

    def test_projection_preserves_source_geometry_but_hides_unmapped_operational_data(self):
        before = copy.deepcopy(self.asset)
        result = asset_for_api(self.asset)
        self.assertEqual(self.asset, before)
        for key in ('id', 'asset_id', 'center', 'size', 'photo', 'label', 'class'):
            self.assertEqual(result[key], before[key])
        self.assertEqual(result['tag'], 'OBJ-999')
        self.assertIsNone(result['health_score'])
        self.assertEqual(result['condition_state'], 'not_instrumented')
        self.assertEqual(result['telemetry']['latest'], {})
        self.assertEqual(result['alarms'], [])
        self.assertEqual(result['maintenance_history'], [])
        self.assertEqual(result['diagnostics']['executive_summary'], UNMAPPED_NOTICE)
        self.assertEqual(result['archived_profile_provenance']['source_asset_id'], 'VALVE-001')
        candidate = {**self.asset, 'source_region_id': 'region-1', 'condition_state': 'unknown'}
        self.assertEqual(asset_for_api(candidate)['condition_state'], 'unknown')
        self.assertEqual(asset_for_api({**self.asset, 'movable': True})['condition_state'], 'movable')

    def test_mapped_records_are_unchanged(self):
        self.asset['mapped'] = True
        self.assertEqual(asset_for_api(self.asset), self.asset)

    def test_route_projection_preserves_path_and_source_but_hides_borrowed_proximity_status(self):
        nearby = {key: self.asset[key] for key in ('id', 'asset_id', 'tag', 'mapped', 'condition_state', 'health_score')}
        nearby['distance_m'] = 1.2
        route = {'validated': True, 'waypoints': [
            {'position': [1, 2, 3], 'yaw': .5, 'nearby_assets': [nearby], 'nearby_count': 1}]}
        before = copy.deepcopy(route)
        out = route_for_api(route)
        self.assertEqual(route, before)
        self.assertEqual(out['waypoints'][0]['position'], [1, 2, 3])
        self.assertEqual(out['waypoints'][0]['yaw'], .5)
        self.assertEqual(out['waypoints'][0]['nearby_count'], 1)
        actual = out['waypoints'][0]['nearby_assets'][0]
        self.assertEqual(set(actual), set(nearby))
        self.assertEqual(actual['distance_m'], 1.2)
        self.assertEqual(actual['tag'], 'OBJ-999')
        self.assertIsNone(actual['health_score'])

    def test_new_unmapped_geometry_gets_no_borrowed_workbook_record(self):
        obj = {k: self.asset[k] for k in ('id', 'class', 'label', 'center', 'size')}
        record = bind_assets_to_scene([obj], {'records': {}, 'docs': {}})[0]
        self.assertFalse(record['mapped'])
        self.assertEqual(record['asset_class'], 'UNRECORDED_DEVICE')
        self.assertEqual(record['tag'], obj['id'])
        self.assertEqual(record['telemetry']['latest'], {})
        self.assertIsNone(record['health_score'])
        self.assertNotIn('excel_profile_source', record)

    def test_unmapped_copilot_does_not_repeat_archive_diagnosis_or_call_models(self):
        with mock.patch.object(server, 'ASSETS_BY_ID', {'AUX-999': self.asset}), \
             mock.patch.object(server, 'ASSETS_BY_OBJ', {'OBJ-999': self.asset}), \
             mock.patch.object(server, 'classify_intent', side_effect=AssertionError('must not route unbound records')):
            answer = server.answer_copilot_question('AUX-999', 'What is its pressure?')
        self.assertEqual(answer['answer'], UNMAPPED_NOTICE)
        self.assertEqual(answer['citations'], [])
        self.assertEqual(answer['tag'], 'OBJ-999')

    def test_http_list_detail_and_subresources_apply_nonmutating_projection(self):
        before = copy.deepcopy(self.asset)
        with mock.patch.object(server, 'TWIN_DATA', {'assets': [self.asset]}), \
             mock.patch.object(server, 'ASSETS_BY_ID', {'AUX-999': self.asset}), \
             mock.patch.object(server, 'ASSETS_BY_OBJ', {'OBJ-999': self.asset}):
            http = server.ThreadingHTTPServer(('127.0.0.1', 0), server.TwinRequestHandler)
            thread = threading.Thread(target=http.serve_forever, daemon=True)
            thread.start()
            try:
                def get(path):
                    with urlopen(f'http://127.0.0.1:{http.server_port}{path}') as response:
                        return json.load(response)
                self.assertIsNone(get('/api/assets')['assets'][0]['health_score'])
                self.assertEqual(get('/api/assets?mapped=true')['count'], 0)
                self.assertEqual(get('/api/assets/OBJ-999')['tag'], 'OBJ-999')
                self.assertEqual(get('/api/assets/AUX-999/telemetry')['telemetry']['latest'], {})
                self.assertEqual(get('/api/assets/AUX-999/events')['alarms'], [])
                self.assertEqual(get('/api/assets/AUX-999/maintenance')['maintenance_history'], [])
                self.assertEqual(get('/api/assets/AUX-999/diagnostics')['diagnostics']['executive_summary'], UNMAPPED_NOTICE)
            finally:
                http.shutdown(); http.server_close(); thread.join()
        self.assertEqual(self.asset, before)


if __name__ == '__main__':
    unittest.main()
