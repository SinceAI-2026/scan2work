"""Stage 4 - Obstacle-aware 2.5D navigation & proximity engine.

Builds a 5cm floor occupancy grid from avatar-height obstacle points
(0.16m <= Y <= 1.62m) AND floor-intersecting 3D equipment bounding boxes,
inflates obstacles by the robot radius via scipy.ndimage.binary_dilation,
extracts the main connected walkable floor manifold via scipy.ndimage.label,
enforces robot clearance, plans distance-minimizing collision-free
8-connected A* paths with checked straight segments, and runs cKDTree proximity queries
along the trajectory.
"""
from __future__ import annotations

import heapq
import math
import numpy as np
from scipy import ndimage
from scipy.spatial import cKDTree

from . import config as C


def build_occupancy_grid(obstacle_pts: np.ndarray, assets: list[dict], res: float = C.GRID_RES, floor_pts=None) -> dict:
    x_min, x_max = -4.55, 3.95
    z_min, z_max = -3.35, 3.55
    nx = int(math.ceil((x_max - x_min) / res))
    nz = int(math.ceil((z_max - z_min) / res))

    occ_counts, _, _ = np.histogram2d(
        obstacle_pts[:, 0], obstacle_pts[:, 2],
        bins=[nx, nz],
        range=[[x_min, x_min + nx * res], [z_min, z_min + nz * res]],
    )
    raw_occ = occ_counts >= C.OBST_MIN_HITS

    neighbor_count = ndimage.convolve(raw_occ.astype(int), np.ones((3, 3), dtype=int), mode="constant", cval=0)
    cleaned_occ = raw_occ & (neighbor_count >= 2)

    # Rasterize core footprints of floor-mounted equipment boxes
    gx = x_min + (np.arange(nx)[:, None] + 0.5) * res
    gz = z_min + (np.arange(nz)[None, :] + 0.5) * res
    for a in assets:
        if a["tier"] != "equipment":
            continue
        cx, cy, cz = a["center"]
        sx, sy, sz = a["size"]
        bottom = cy - sy / 2
        top = cy + sy / 2
        if bottom > C.OBST_MAX_H or top < 0.10:
            continue
        yaw = a["yaw"]
        ca, sa = math.cos(yaw), math.sin(yaw)
        dx = gx - cx
        dz = gz - cz
        lx = dx * ca + dz * sa
        lz = -dx * sa + dz * ca
        in_box = (np.abs(lx) <= sx * 0.5) & (np.abs(lz) <= sz * 0.5)
        cleaned_occ |= in_box

    cleaned_occ[0, :] = True
    cleaned_occ[-1, :] = True
    cleaned_occ[:, 0] = True
    cleaned_occ[:, -1] = True

    # Include a physical safety margin and the half diagonal of occupied cells.
    safety_margin = 0.08
    required_clearance = C.ROBOT_RADIUS + safety_margin + res / math.sqrt(2)
    clearance_m = ndimage.distance_transform_edt(~cleaned_occ) * res
    dilated_occ = clearance_m <= required_clearance
    floor_supported = np.ones_like(dilated_occ, dtype=bool)
    if floor_pts is not None:
        floor_counts, _, _ = np.histogram2d(
            floor_pts[:, 0], floor_pts[:, 2], bins=[nx, nz],
            range=[[x_min, x_min + nx * res], [z_min, z_min + nz * res]],
        )
        # Unknown space is not automatically a floor. Bridge only small scan gaps.
        floor_supported = ndimage.distance_transform_edt(floor_counts == 0) * res <= 0.15
        dilated_occ |= ~floor_supported

    # Retain only the largest connected component of walkable space so no standoff
    # point ever lands in an unreachable pocket behind pipes or walls.
    room_walkable = ~dilated_occ.copy()
    labels, num_features = ndimage.label(room_walkable)
    if num_features > 0:
        counts = np.bincount(labels.ravel())
        counts[0] = 0
        main_label = int(np.argmax(counts))
        walkable = labels == main_label
        dilated_occ = ~walkable

    return {
        "x_min": x_min,
        "z_min": z_min,
        "res": res,
        "nx": nx,
        "nz": nz,
        "raw_occ": cleaned_occ,
        "dilated_occ": dilated_occ,
        "clearance_m": clearance_m,
        "safety_margin_m": safety_margin,
        "required_clearance_m": required_clearance,
        "floor_supported": floor_supported,
        "room_walkable": room_walkable,
        "component_labels": labels,
    }


def world_to_grid(x: float, z: float, grid: dict) -> tuple[int, int]:
    ix = int(np.clip(round((x - grid["x_min"]) / grid["res"] - 0.5), 0, grid["nx"] - 1))
    iz = int(np.clip(round((z - grid["z_min"]) / grid["res"] - 0.5), 0, grid["nz"] - 1))
    return ix, iz


def grid_to_world(ix: int, iz: int, grid: dict) -> tuple[float, float]:
    x = grid["x_min"] + (ix + 0.5) * grid["res"]
    z = grid["z_min"] + (iz + 0.5) * grid["res"]
    return round(float(x), 3), round(float(z), 3)


def find_standoff_cell(target_x: float, target_z: float, grid: dict, min_r: float = 0.35, max_r: float = 1.45) -> tuple[int, int]:
    """Find best walkable standoff cell on the main floor manifold in front of an asset."""
    free = ~grid["dilated_occ"]
    clear = grid["clearance_m"]
    best_cell = None
    best_score = -1e9
    for ix in range(grid["nx"]):
        for iz in range(grid["nz"]):
            if not free[ix, iz]:
                continue
            wx, wz = grid_to_world(ix, iz, grid)
            d = math.hypot(wx - target_x, wz - target_z)
            if min_r <= d <= max_r:
                score = -abs(d - C.STANDOFF) * 4.0 + min(clear[ix, iz], 0.65) * 3.0
                if score > best_score:
                    best_score = score
                    best_cell = (ix, iz)
    if best_cell is not None:
        return best_cell
    free_idx = np.argwhere(free)
    if len(free_idx) == 0:
        raise ValueError("No walkable floor cells are available for a standoff.")
    wx = grid["x_min"] + (free_idx[:, 0] + 0.5) * grid["res"]
    wz = grid["z_min"] + (free_idx[:, 1] + 0.5) * grid["res"]
    d2 = (wx - target_x) ** 2 + (wz - target_z) ** 2
    k = int(np.argmin(d2))
    return int(free_idx[k, 0]), int(free_idx[k, 1])


def astar_grid(start: tuple[int, int], goal: tuple[int, int], grid: dict) -> list[tuple[int, int]]:
    """Distance-optimal 8-connected A* path planner on the connected floor manifold."""
    occ = grid["dilated_occ"]
    clear = grid["clearance_m"]
    nx, nz = grid["nx"], grid["nz"]

    if occ[start]:
        start = find_standoff_cell(*grid_to_world(*start, grid), grid, min_r=0.0, max_r=2.0)
    if occ[goal]:
        goal = find_standoff_cell(*grid_to_world(*goal, grid), grid, min_r=0.0, max_r=2.0)

    neighbors = [
        (-1, 0, 1.0), (1, 0, 1.0), (0, -1, 1.0), (0, 1, 1.0),
        (-1, -1, math.sqrt(2)), (-1, 1, math.sqrt(2)), (1, -1, math.sqrt(2)), (1, 1, math.sqrt(2)),
    ]

    def h(a, b):
        return math.hypot(a[0] - b[0], a[1] - b[1])

    open_heap = [(h(start, goal), 0.0, start)]
    came_from = {}
    g_score = {start: 0.0}
    closed = set()

    while open_heap:
        _, g, cur = heapq.heappop(open_heap)
        if cur in closed:
            continue
        if cur == goal:
            path = [cur]
            while cur in came_from:
                cur = came_from[cur]
                path.append(cur)
            path.reverse()
            return path
        closed.add(cur)
        cx, cz = cur
        for dx, dz, step_cost in neighbors:
            nx_i, nz_i = cx + dx, cz + dz
            if not (0 <= nx_i < nx and 0 <= nz_i < nz):
                continue
            if occ[nx_i, nz_i]:
                continue
            if dx != 0 and dz != 0 and (occ[cx + dx, cz] or occ[cx, cz + dz]):
                continue
            tentative_g = g + step_cost
            nxt = (nx_i, nz_i)
            if tentative_g < g_score.get(nxt, 1e18):
                came_from[nxt] = cur
                g_score[nxt] = tentative_g
                f = tentative_g + h(nxt, goal)
                heapq.heappush(open_heap, (f, tentative_g, nxt))

    raise ValueError(f"No collision-free route from {start} to {goal}.")


def segment_is_walkable(start, end, grid: dict) -> bool:
    """Check the complete segment against closed obstacle cells, including corners."""
    origin = np.array([grid["x_min"], grid["z_min"]], dtype=float)
    a = (np.asarray(start, dtype=float) - origin) / grid["res"]
    b = (np.asarray(end, dtype=float) - origin) / grid["res"]
    shape = np.array([grid["nx"], grid["nz"]])
    if not (np.isfinite(a).all() and np.isfinite(b).all()):
        return False
    if np.any(a < 0) or np.any(b < 0) or np.any(a >= shape) or np.any(b >= shape):
        return False

    low = np.maximum(0, np.floor(np.minimum(a, b) - 1e-9).astype(int))
    high = np.minimum(shape - 1, np.floor(np.maximum(a, b) + 1e-9).astype(int))
    blocked = np.argwhere(grid["dilated_occ"][low[0]:high[0] + 1, low[1]:high[1] + 1]) + low
    if not len(blocked):
        return True

    enter = np.zeros(len(blocked))
    leave = np.ones(len(blocked))
    for axis in range(2):
        delta = b[axis] - a[axis]
        if abs(delta) < 1e-12:
            intersects = (a[axis] >= blocked[:, axis]) & (a[axis] <= blocked[:, axis] + 1)
            leave[~intersects] = -1
        else:
            t0 = (blocked[:, axis] - a[axis]) / delta
            t1 = (blocked[:, axis] + 1 - a[axis]) / delta
            enter = np.maximum(enter, np.minimum(t0, t1))
            leave = np.minimum(leave, np.maximum(t0, t1))
    return not bool(np.any(enter <= leave + 1e-12))


def trajectory_is_walkable(points, grid: dict) -> bool:
    if len(points) == 0:
        return False
    return all(segment_is_walkable(a, b, grid) for a, b in zip(points, points[1:])) and segment_is_walkable(points[0], points[0], grid)


def _round_corners(corners: list, grid: dict, max_cut: float = 0.30) -> list:
    """Replace each sharp corner by a short quadratic arc; every arc is collision-checked and shrunk until clear."""
    if len(corners) < 3:
        return corners
    out = [corners[0]]
    for prev, cur, nxt in zip(corners, corners[1:], corners[2:]):
        a, b = prev - cur, nxt - cur
        la, lb = np.linalg.norm(a), np.linalg.norm(b)
        cut = min(max_cut, 0.45 * la, 0.45 * lb)
        arc = None
        while cut >= 0.04:
            p0, p2 = cur + a / la * cut, cur + b / lb * cut
            t = np.linspace(0, 1, 9)[:, None]
            cand = (1 - t) ** 2 * p0 + 2 * (1 - t) * t * cur + t ** 2 * p2
            if trajectory_is_walkable(np.vstack([out[-1], cand]), grid):
                arc = cand
                break
            cut *= 0.6
        out.extend(arc if arc is not None else [cur])
    out.append(corners[-1])
    return out


def smooth_trajectory(grid_path: list[tuple[int, int]], grid: dict, num_samples: int = 130) -> list[dict]:
    """Shorten discrete A* routes using collision-checked straight segments."""
    if not grid_path:
        raise ValueError("Cannot generate a trajectory from an empty route.")
    pts = np.array([grid_to_world(ix, iz, grid) for ix, iz in grid_path], dtype=float)
    if not trajectory_is_walkable(pts, grid):
        raise ValueError("Input route intersects an obstacle or leaves the navigation grid.")
    # Remove unnecessary grid turns using exact line-of-sight checks. Preserve
    # the remaining corners rather than fitting a decorative sweeping spline.
    corners = [pts[0]]
    index = 0
    while index < len(pts) - 1:
        end = len(pts) - 1
        while end > index + 1 and not segment_is_walkable(pts[index], pts[end], grid):
            end -= 1
        corners.append(pts[end])
        index = end
    corners = _round_corners(corners, grid)
    dense = [corners[0]]
    for start, end in zip(corners, corners[1:]):
        count = max(1, int(math.ceil(np.linalg.norm(end - start) / (grid["res"] * .5))))
        dense.extend(start + (end - start) * t / count for t in range(1, count + 1))
    candidate = np.round(dense, 3)
    if trajectory_is_walkable(candidate, grid):
        pts = candidate

    diffs = np.diff(pts, axis=0, prepend=pts[:1])
    seg_d = np.hypot(diffs[:, 0], diffs[:, 1])
    cum_d = np.cumsum(seg_d)

    waypoints = []
    for i in range(len(pts)):
        if i + 1 < len(pts):
            dx = pts[i + 1, 0] - pts[i, 0]
            dz = pts[i + 1, 1] - pts[i, 1]
        elif i > 0:
            dx = pts[i, 0] - pts[i - 1, 0]
            dz = pts[i, 1] - pts[i - 1, 1]
        else:
            dx, dz = 1.0, 0.0
        yaw = float(math.atan2(dx, dz))
        waypoints.append({
            "step": i,
            "t": round(float(cum_d[i] / C.WALK_SPEED), 2),
            "distance_m": round(float(cum_d[i]), 3),
            "position": [round(float(pts[i, 0]), 3), 0.0, round(float(pts[i, 1]), 3)],
            "yaw": round(yaw, 4),
            "clearance_m": round(float(grid["clearance_m"][world_to_grid(*pts[i], grid)]) - grid["res"] / math.sqrt(2), 3),
        })
    return waypoints


def attach_proximity_timeline(waypoints: list[dict], assets: list[dict], radius: float = C.PROXIMITY_RADIUS) -> list[dict]:
    """Attach nearby 3D assets (within radius in 2.5D) to every trajectory waypoint."""
    centers_xz = np.array([[a["center"][0], a["center"][2]] for a in assets], dtype=float)
    tree = cKDTree(centers_xz)

    for wp in waypoints:
        px, _, pz = wp["position"]
        idxs = tree.query_ball_point([px, pz], r=radius)
        nearby = []
        for i in idxs:
            a = assets[i]
            d = math.hypot(a["center"][0] - px, a["center"][2] - pz)
            nearby.append({
                "id": a["id"],
                "asset_id": a["asset_id"],
                "tag": a["tag"],
                "condition_state": a["condition_state"],
                "health_score": a["health_score"],
                "is_target": a["is_target"],
                "mapped": a["mapped"],
                "distance_m": round(d, 2),
            })
        nearby.sort(key=lambda n: (0 if n["is_target"] else (1 if n["condition_state"] != "healthy" else (2 if n["mapped"] else 3)), n["distance_m"]))
        wp["nearby_assets"] = nearby
        wp["nearby_count"] = len(idxs)
    return waypoints


def build_navigation_bundle(perception_out: dict, enriched_assets: list[dict]) -> dict:
    grid = build_occupancy_grid(perception_out["obstacle_points"], enriched_assets, floor_pts=perception_out.get("floor_points"))

    # Start at the main North entrance doorway (-0.25, 2.35) of the Equipment Room
    default_start_xz = (-0.25, 2.35)
    start_cell = find_standoff_cell(default_start_xz[0], default_start_xz[1], grid, min_r=0.0, max_r=1.10)
    start_world = grid_to_world(*start_cell, grid)

    target_obj = next((a for a in enriched_assets if a["is_target"]), enriched_assets[0])
    tx, _, tz = target_obj["center"]
    goal_cell = find_standoff_cell(tx, tz, grid, min_r=0.38, max_r=1.25)
    goal_world = grid_to_world(*goal_cell, grid)

    raw_cells = astar_grid(start_cell, goal_cell, grid)
    waypoints = smooth_trajectory(raw_cells, grid, num_samples=140)
    waypoints = attach_proximity_timeline(waypoints, enriched_assets)

    routes_by_asset = {}
    for a in enriched_assets:
        if not a["mapped"]:
            continue
        ax, _, az = a["center"]
        g_cell = find_standoff_cell(ax, az, grid, min_r=0.35, max_r=1.40)
        cells = astar_grid(start_cell, g_cell, grid)
        wps = smooth_trajectory(cells, grid, num_samples=100)
        wps = attach_proximity_timeline(wps, enriched_assets)
        routes_by_asset[a["asset_id"]] = {
            "asset_id": a["asset_id"],
            "object_id": a["id"],
            "tag": a["tag"],
            "standoff_xz": list(grid_to_world(*g_cell, grid)),
            "raw_astar_steps": len(cells),
            "total_distance_m": wps[-1]["distance_m"] if wps else 0.0,
            "duration_s": wps[-1]["t"] if wps else 0.0,
            "waypoints": wps,
            "validated": True,
            "min_clearance_m": min(w["clearance_m"] for w in wps),
            "planning_policy": "Shortest grid route by distance with fixed clearance and collision-checked shortcuts",
        }

    free_cells = []
    obst_cells = []
    for ix in range(grid["nx"]):
        for iz in range(grid["nz"]):
            wx, wz = grid_to_world(ix, iz, grid)
            if grid["raw_occ"][ix, iz]:
                obst_cells.append([wx, wz])
            elif not grid["dilated_occ"][ix, iz] and (ix % 2 == 0 and iz % 2 == 0):
                free_cells.append([wx, wz, round(float(grid["clearance_m"][ix, iz]), 2)])

    return {
        "grid_meta": {
            "x_min": grid["x_min"],
            "z_min": grid["z_min"],
            "res": grid["res"],
            "nx": grid["nx"],
            "nz": grid["nz"],
            "robot_radius_m": C.ROBOT_RADIUS,
            "safety_margin_m": grid["safety_margin_m"],
            "required_clearance_m": round(grid["required_clearance_m"], 3),
            "proximity_radius_m": C.PROXIMITY_RADIUS,
            "walkable_cells": int((~grid["dilated_occ"]).sum()),
            "obstacle_cells": int(grid["raw_occ"].sum()),
        },
        "start_xz": list(start_world),
        "target_asset_id": target_obj["asset_id"],
        "target_object_id": target_obj["id"],
        "target_tag": target_obj["tag"],
        "target_standoff_xz": list(goal_world),
        "primary_route": {
            "asset_id": target_obj["asset_id"],
            "object_id": target_obj["id"],
            "tag": target_obj["tag"],
            "validated": True,
            "min_clearance_m": min(w["clearance_m"] for w in waypoints),
            "planning_policy": "Shortest grid route by distance with fixed clearance and collision-checked shortcuts",
            "total_distance_m": waypoints[-1]["distance_m"],
            "duration_s": waypoints[-1]["t"],
            "raw_astar_steps": len(raw_cells),
            "waypoints": waypoints,
        },
        "routes_by_asset": routes_by_asset,
        "obstacle_cells_xz": obst_cells,
        "walkable_sample_xz": free_cells,
        "walkable_grid": (~grid["dilated_occ"]).astype(int).tolist(),
        "room_walkable_grid": grid["room_walkable"].astype(int).tolist(),
        "component_grid": grid["component_labels"].tolist(),
        "coverage": {
            "reachable_area_m2": round(float((~grid["dilated_occ"]).sum()) * grid["res"] ** 2, 2),
            "total_free_area_m2": round(float(grid["room_walkable"].sum()) * grid["res"] ** 2, 2),
        },
        "clearance_grid_m": np.round(grid["clearance_m"], 3).tolist(),
    }
