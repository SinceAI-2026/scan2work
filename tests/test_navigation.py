"""Run from valmet-twin: .venv/bin/python -m unittest discover -s tests -v."""
import unittest

import numpy as np

from pipeline import navigation as nav


def grid(size=8):
    return {
        "x_min": 0.0, "z_min": 0.0, "res": 1.0,
        "nx": size, "nz": size,
        "dilated_occ": np.zeros((size, size), dtype=bool),
        "clearance_m": np.ones((size, size)),
    }


class NavigationTests(unittest.TestCase):
    def test_full_equipment_footprint_includes_robot_safety_margin(self):
        asset = dict(tier="equipment", center=[0, 0.5, 0], size=[1, 0.8, 1], yaw=0)
        g = nav.build_occupancy_grid(np.empty((0, 3)), [asset])
        self.assertTrue(g["raw_occ"][nav.world_to_grid(.475, 0, g)])
        self.assertTrue(g["dilated_occ"][nav.world_to_grid(.675, 0, g)])
        self.assertFalse(g["dilated_occ"][nav.world_to_grid(.925, 0, g)])
        self.assertGreater(g["required_clearance_m"], nav.C.ROBOT_RADIUS + .08)

    def test_unreachable_goal_does_not_return_a_straight_line(self):
        g = grid()
        g["dilated_occ"][3, :] = True
        with self.assertRaisesRegex(ValueError, "No collision-free route"):
            nav.astar_grid((1, 1), (5, 1), g)

    def test_segment_with_clear_endpoints_can_cross_an_obstacle(self):
        g = grid()
        g["dilated_occ"][3, 2] = True
        self.assertFalse(nav.segment_is_walkable((1.5, 2.5), (5.5, 2.5), g))
        self.assertTrue(nav.segment_is_walkable((1.5, 1.5), (5.5, 1.5), g))

    def test_corner_touching_and_grid_boundary_are_not_walkable(self):
        g = grid()
        g["dilated_occ"][2, 1] = True
        self.assertFalse(nav.segment_is_walkable((1.5, 1.5), (2.5, 2.5), g))
        self.assertFalse(nav.segment_is_walkable((1.5, 1.5), (-0.1, 1.5), g))
        self.assertFalse(nav.segment_is_walkable((1.5, 1.5), (float("nan"), 1.5), g))

    def test_shortened_route_still_goes_around_obstacle(self):
        g = grid()
        g["dilated_occ"][3, 2] = True
        cells = [(1, 2), (1, 3), (2, 3), (3, 3), (4, 3), (5, 3), (5, 2)]
        waypoints = nav.smooth_trajectory(cells, g, num_samples=2)
        points = [[w["position"][0], w["position"][2]] for w in waypoints]
        self.assertTrue(nav.trajectory_is_walkable(points, g))
        self.assertEqual(points[0], [1.5, 2.5])
        self.assertEqual(points[-1], [5.5, 2.5])

    def test_open_floor_route_removes_unnecessary_detour(self):
        g = grid()
        cells = [(1, 1), (1, 2), (1, 3), (2, 3), (3, 3)]
        route = nav.smooth_trajectory(cells, g)
        self.assertAlmostEqual(route[-1]["distance_m"], np.sqrt(8), places=2)

    def test_unobserved_floor_is_not_assumed_walkable(self):
        g = nav.build_occupancy_grid(np.empty((0, 3)), [], floor_pts=np.array([[0, 0, 0]]))
        self.assertFalse(g["dilated_occ"][nav.world_to_grid(0, 0, g)])
        self.assertTrue(g["dilated_occ"][nav.world_to_grid(1, 1, g)])

    def test_disconnected_floor_is_preserved_for_map_feedback(self):
        divider = dict(tier="equipment", center=[-.3, .5, .1], size=[.1, 1, 7], yaw=0)
        g = nav.build_occupancy_grid(np.empty((0, 3)), [divider])
        self.assertGreater(g["room_walkable"].sum(), (~g["dilated_occ"]).sum())
        self.assertGreaterEqual(len(np.unique(g["component_labels"])), 3)

    def test_astar_avoids_diagonal_corner_cutting(self):
        g = grid()
        g["dilated_occ"][2, 1] = True
        cells = nav.astar_grid((1, 1), (3, 3), g)
        points = [nav.grid_to_world(*cell, g) for cell in cells]
        self.assertTrue(nav.trajectory_is_walkable(points, g))

    def test_empty_floor_and_empty_route_report_errors(self):
        g = grid()
        g["dilated_occ"][:] = True
        with self.assertRaisesRegex(ValueError, "No walkable floor"):
            nav.find_standoff_cell(1, 1, g)
        with self.assertRaisesRegex(ValueError, "empty route"):
            nav.smooth_trajectory([], g)

    def test_proximity_includes_every_asset_in_range(self):
        assets = [dict(id=f"OBJ-{i}", asset_id=f"AUX-{i}", tag=f"P-{i}",
                       center=[0.01 * i, 1.0, 0.0], condition_state="healthy",
                       health_score=95, is_target=False, mapped=False) for i in range(60)]
        assets.append({**assets[0], "id": "far", "center": [10, 1, 10]})
        wp = nav.attach_proximity_timeline([{"position": [0, 0, 0]}], assets, radius=1.0)[0]
        self.assertEqual(wp["nearby_count"], 60)
        self.assertEqual(len(wp["nearby_assets"]), 60)
        self.assertNotIn("far", [a["id"] for a in wp["nearby_assets"]])


if __name__ == "__main__":
    unittest.main()
