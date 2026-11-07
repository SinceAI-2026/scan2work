"""Check real-scene navigation without modifying shared demo artifacts.

Run from valmet-twin: PYTHONPATH=. .venv/bin/python tests/verify_navigation_scene.py
"""
import json
from pipeline import config, ingest, navigation, perception


def main():
    twin_path = config.OUT_DIR / "digital_twin.json"
    twin = json.loads(twin_path.read_text())
    if twin["scene"]["source_file"] != config.PLY_PATH.name:
        raise ValueError("Generated assets and configured scan do not match.")
    splat = ingest.level_scene(ingest.load_splat())
    extracted = perception.extract_assets(splat)
    assets = twin["assets"]
    bundle = navigation.build_navigation_bundle(extracted, assets)
    grid = navigation.build_occupancy_grid(extracted["obstacle_points"], assets, floor_pts=extracted.get("floor_points"))
    routes = {"primary": bundle["primary_route"], **bundle["routes_by_asset"]}
    expected_ids = {a["asset_id"] for a in assets if a["mapped"]}
    if set(bundle["routes_by_asset"]) != expected_ids:
        raise ValueError("Missing routes to mapped registry assets.")
    waypoint_count = 0
    maximum_nearby = 0
    for name, route in routes.items():
        waypoints = route["waypoints"]
        points = [[w["position"][0], w["position"][2]] for w in waypoints]
        if not navigation.trajectory_is_walkable(points, grid):
            raise ValueError(f"Route {name} crosses a blocked cell.")
        for wp in waypoints:
            if len(wp["nearby_assets"]) != wp["nearby_count"]:
                raise ValueError(f"Route {name} truncates nearby assets.")
            maximum_nearby = max(maximum_nearby, wp["nearby_count"])
        waypoint_count += len(waypoints)
    print(json.dumps({
        "passed": True,
        "source_scan": str(config.PLY_PATH),
        "source_workbook": str(config.XLSX_PATH),
        "routes_verified": len(routes),
        "waypoints_verified": waypoint_count,
        "maximum_nearby_assets": maximum_nearby,
        "primary_distance_m": bundle["primary_route"]["total_distance_m"],
        "collision_check": "Complete line segments against inflated occupancy cells",
        "shared_artifacts_modified": False,
    }, indent=2))


if __name__ == "__main__":
    main()
