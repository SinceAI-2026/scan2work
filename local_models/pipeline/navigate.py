"""Find where a person can walk in the splat and build a roadmap for shortest collision-free routes.

The floor is the densest horizontal layer of Gaussians and the ceiling the densest layer above head
height (measured per area, since parts of the ceiling are higher). Gaussians between ankle and head
height block a cell; blocked cells grow by a body radius, and what stays free on the floor is
walkable. The walkable area's medial axis (the line through the middle of every aisle) becomes a
graph: nodes where aisles meet or end, edges along the aisle centres. A route runs along the graph,
then cuts corners wherever a straight line stays walkable.

Writes data/pipeline/nav.json (grid, heights, graph and a route from the walk's start to every
object). As a CLI it answers one query: python pipeline/navigate.py --from=x,z --to=x,z|obj-012
"""
import argparse
import heapq
import json
from pathlib import Path

import numpy as np
from plyfile import PlyData
from scipy import ndimage
from skimage.measure import approximate_polygon
from skimage.morphology import disk, skeletonize

from geometry import SPLAT

repo = Path(__file__).resolve().parent.parent
nav_path = repo / "data" / "pipeline" / "nav.json"

FLIP = np.diag([1.0, -1.0, -1.0])  # splat -> viewer world, matches index.html
MIN_OPACITY = 0.3  # ignore faint floaters
CELL = 0.05  # world units per grid cell; finer than a foot, coarse enough that Gaussians fill it
FLOOR_TOLERANCE = 0.08  # Gaussians this close to the floor height are floor
STEP_HEIGHT = 0.10  # anything lower can be stepped over (and floor noise lives here)
HEAD_HEIGHT = 1.65  # a person walks under anything higher; the pipe layer's underside is at ~1.75
# and the walkthrough passed under it with the phone at ~1.4
HEAD_ROOM = 0.05  # keep the obstacle band this far under a low ceiling
CEILING_MIN = 1.5  # the ceiling is searched above this height over the floor
CEILING_CELL = 0.5  # ceiling heights are measured per area of this size
CEILING_MIN_GAUSSIANS = 20  # fewer above head height in an area: no ceiling seen there, use the room's
MIN_HITS = 2  # Gaussians a cell needs in the band to block it; a lone one is usually a floater
MIN_OBSTACLE = 0.01  # m²; blocked specks smaller than this in open floor are floaters, not things
ROOM_CLOSING = 0.3  # bridges gaps in the reconstructed floor so the room outline is one piece
WALL_MARGIN = 0.3  # keep Gaussians this far outside the floor (walls); beyond is outside the room
BODY_RADIUS = 0.2  # half a shoulder width; the walkthrough squeezed through aisles about 0.5 clear
SPUR_LENGTH = 0.4  # medial-axis branches shorter than this come from ragged edges, not aisles
SIMPLIFY = 0.03  # edge polylines may deviate this much from the medial axis
APPROACH_RANGE = 3.0  # an end walked out of a corner looks this far for the roadmap

OUTSIDE, BLOCKED, TOO_CLOSE, WALKABLE = range(4)  # cell codes in nav.json


def load_gaussians():
    ply = PlyData.read(SPLAT)["vertex"]
    opacity = 1 / (1 + np.exp(-np.asarray(ply["opacity"])))
    keep = opacity > MIN_OPACITY
    return np.stack([ply["x"], ply["y"], ply["z"]], 1)[keep].astype(np.float64) @ FLIP


def densest_height(heights, low, high):
    """Height of the thickest horizontal layer between low and high, refined to the layer's mean."""
    counts, edges = np.histogram(heights, bins=np.arange(low, high, 0.01))
    peak = edges[np.argmax(counts)] + 0.005
    near = heights[np.abs(heights - peak) < 0.03]
    return float(near.mean())


def build():
    points = load_gaussians()
    y = points[:, 1]
    floor = densest_height(y, np.percentile(y, 0.5), np.median(y))
    ceiling = densest_height(y, floor + CEILING_MIN, np.percentile(y, 99.9))

    origin = np.floor(points[:, [0, 2]].min(0) / CELL) * CELL - WALL_MARGIN
    size = np.ceil((points[:, [0, 2]].max(0) - origin) / CELL).astype(int) + int(WALL_MARGIN / CELL) + 1
    cells = np.floor((points[:, [0, 2]] - origin) / CELL).astype(int)
    width, height = size

    def raster(mask):
        grid = np.zeros((height, width), int)
        np.add.at(grid, (cells[mask, 1], cells[mask, 0]), 1)
        return grid

    # The room is the floor the splat reconstructed, with gaps and the ground under equipment filled in.
    on_floor = raster(np.abs(y - floor) < FLOOR_TOLERANCE) > 0
    room = ndimage.binary_closing(on_floor, disk(round(ROOM_CLOSING / CELL)), border_value=0)
    labels, count = ndimage.label(room)
    room = labels == 1 + np.argmax(ndimage.sum(room, labels, range(1, count + 1)))
    room = ndimage.binary_fill_holes(room)
    inside = ndimage.binary_dilation(room, disk(round(WALL_MARGIN / CELL)))

    # Ceiling per area: the densest layer above head height, the room's where too little is seen.
    ratio = round(CEILING_CELL / CELL)
    coarse = (cells // ratio)[:, ::-1]
    ceilings = np.full((-(-height // ratio), -(-width // ratio)), ceiling)
    above = y > floor + CEILING_MIN
    for index in np.unique(coarse[above], axis=0):
        here = above & (coarse == index).all(1)
        if here.sum() >= CEILING_MIN_GAUSSIANS:
            ceilings[tuple(index)] = densest_height(y[here], floor + CEILING_MIN, y[here].max() + 0.02)
    # One noisy area must not punch a hole up or down; take the median of its neighbourhood.
    ceilings = ndimage.median_filter(ceilings, size=3, mode="nearest")

    band_top = np.minimum(floor + HEAD_HEIGHT, ceilings[cells[:, 1] // ratio, cells[:, 0] // ratio] - HEAD_ROOM)
    in_band = (y > floor + STEP_HEIGHT) & (y < band_top)
    blocked = ndimage.binary_closing(raster(in_band) >= MIN_HITS, disk(1))  # join neighbouring Gaussians
    labels, count = ndimage.label(blocked)
    sizes = ndimage.sum(blocked, labels, range(1, count + 1)) * CELL**2
    blocked = np.isin(labels, 1 + np.flatnonzero(sizes >= MIN_OBSTACLE)) | ~room
    clearance = ndimage.distance_transform_edt(~blocked) * CELL
    free = clearance > BODY_RADIUS

    # Keep the walkable piece the walk went through; other free islands are boxed in by equipment.
    walk = json.loads((repo / "walk.json").read_text())
    poses = np.array([frame["position"] for frame in walk])[:, [0, 2]]
    pose_cells = np.clip(np.floor((poses - origin) / CELL).astype(int), 0, [width - 1, height - 1])
    labels, count = ndimage.label(free)
    hits = np.bincount(labels[pose_cells[:, 1], pose_cells[:, 0]], minlength=count + 1)
    hits[0] = 0
    walkable = labels == np.argmax(hits)

    grid = np.full((height, width), OUTSIDE)
    grid[inside] = BLOCKED
    grid[inside & ~blocked] = TOO_CLOSE
    grid[walkable] = WALKABLE

    nodes, edges = roadmap(walkable, origin)

    # How well the walk (a hand-held phone, so a real walker's path) agrees with the walkable area.
    distance_to_walkable = ndimage.distance_transform_edt(~walkable) * CELL
    pose_distance = distance_to_walkable[pose_cells[:, 1], pose_cells[:, 0]]
    nav = {
        "floor": round(floor, 3),
        "ceiling": round(ceiling, 3),
        "step_height": STEP_HEIGHT,
        "head_height": HEAD_HEIGHT,
        "head_room": HEAD_ROOM,
        "body_radius": BODY_RADIUS,
        "grid": {
            "origin": origin.round(3).tolist(),  # world x, z of cell [0][0]'s corner
            "cell": CELL,
            "codes": ["outside", "blocked", "too_close", "walkable"],
            "rows": ["".join(map(str, row)) for row in grid],  # row = z, character = x
        },
        "ceilings": {"cell": CEILING_CELL, "rows": ceilings.round(3).tolist()},
        "nodes": nodes,
        "edges": edges,
        "stats": {
            "walkable_m2": round(float(walkable.sum()) * CELL**2, 2),
            "room_m2": round(float(room.sum()) * CELL**2, 2),
            "walk_poses": len(poses),
            "walk_poses_walkable": int((pose_distance == 0).sum()),
            "walk_pose_max_distance": round(float(pose_distance.max()), 3),
        },
    }
    nav["start"] = snap(nav, poses[0])
    nav["routes"] = object_routes(nav)
    return nav


def roadmap(walkable, origin):
    """Medial axis of the walkable area as a graph: nodes at junctions and dead ends, edges between."""
    pixels, edges = trace(skeletonize(walkable))
    edges = [e for e in edges if e["a"] != e["b"] or e["length"] > 3]  # specks from tiny pixel loops
    while True:
        # Short dead-end branches come from ragged edges of the walkable area, not from aisles.
        degree = np.bincount([n for e in edges for n in (e["a"], e["b"])], minlength=len(pixels))
        spurs = [e for e in edges if e["a"] != e["b"] and e["length"] * CELL < SPUR_LENGTH
                 and min(degree[e["a"]], degree[e["b"]]) == 1 and max(degree[e["a"]], degree[e["b"]]) > 2]
        if spurs:
            edges = [e for e in edges if not any(e is spur for spur in spurs)]
            degree = np.bincount([n for e in edges for n in (e["a"], e["b"])], minlength=len(pixels))
        # A node left with two edges is just a bend; join the edges through it.
        bend = next((n for n in range(len(pixels)) if degree[n] == 2
                     and len([e for e in edges if n in (e["a"], e["b"]) and e["a"] != e["b"]]) == 2), None)
        if bend is None and not spurs:
            break
        if bend is not None:
            first, second = [e for e in edges if bend in (e["a"], e["b"])]
            one = first["chain"] if first["b"] == bend else first["chain"][::-1]
            two = second["chain"] if second["a"] == bend else second["chain"][::-1]
            joined = {"a": first["a"] if first["b"] == bend else first["b"],
                      "b": second["b"] if second["a"] == bend else second["a"],
                      "length": first["length"] + second["length"], "chain": one + two[1:]}
            edges = [e for e in edges if e is not first and e is not second] + [joined]

    used = sorted({n for e in edges for n in (e["a"], e["b"])})
    renumber = {old: new for new, old in enumerate(used)}
    degree = np.bincount([renumber[n] for e in edges for n in (e["a"], e["b"])], minlength=len(used))

    def world(cells):
        cells = np.asarray(cells, float)
        return (origin + (cells[:, ::-1] + 0.5) * CELL).round(3)

    nodes = [{"id": i, "x": float(p[0]), "z": float(p[1]), "degree": int(d)}
             for i, (p, d) in enumerate(zip(world([pixels[n] for n in used]), degree))]
    out = [{"a": renumber[e["a"]], "b": renumber[e["b"]], "length": round(e["length"] * CELL, 3),
            "points": world(approximate_polygon(np.asarray(e["chain"], float), SIMPLIFY / CELL)).tolist()}
           for e in edges]
    return nodes, out


NEIGHBOURS = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]


def trace(skeleton):
    """Split a one-pixel skeleton into nodes and the pixel chains between them.

    Pixels with other than two neighbours are junctions or dead ends; touching ones are one node.
    What is left falls apart into simple chains, each running from one node to another.
    """
    def around(p, among):
        return [(p[0] + dr, p[1] + dc) for dr, dc in NEIGHBOURS if (p[0] + dr, p[1] + dc) in among]

    pixels = set(zip(*np.nonzero(skeleton)))
    special = {p for p in pixels if len(around(p, pixels)) != 2}
    node_of, nodes = {}, []
    for p in sorted(special):
        if p in node_of:
            continue
        stack, members = [p], []
        node_of[p] = len(nodes)
        while stack:
            members.append(stack.pop())
            for n in around(members[-1], special):
                if n not in node_of:
                    node_of[n] = len(nodes)
                    stack.append(n)
        centre = np.mean(members, 0)
        nodes.append(min(members, key=lambda m: np.hypot(*np.subtract(m, centre))))

    edges = []
    plain = pixels - special
    while plain:
        # Walk to one end of this chain, then along it to the other.
        seed = plain.pop()
        chain = [seed]
        for _ in range(2):
            chain.reverse()
            while True:
                step = [n for n in around(chain[-1], plain) if n not in chain[-2:]]
                if not step:
                    break
                plain.discard(step[0])
                chain.append(step[0])
        ends = [around(chain[0], special), around(chain[-1], special)]
        if len(chain) == 1:
            ends = [ends[0][:1], ends[0][1:]]
        if not ends[0] or not ends[1]:  # a closed loop with no junction: its first pixel becomes one
            nodes.append(chain[0])
            node_of[chain[0]] = len(nodes) - 1
            chain.append(chain[0])
            a = b = node_of[chain[0]]
        else:
            a, b = node_of[ends[0][0]], node_of[ends[1][0]]
            chain = [nodes[a]] + chain + [nodes[b]]
        length = sum(np.hypot(*np.subtract(p, q)) for p, q in zip(chain, chain[1:]))
        edges.append({"a": a, "b": b, "length": length, "chain": chain})
    return nodes, edges


class Grid:
    """The walkable grid of a nav.json, for snapping points and line-of-sight tests."""

    def __init__(self, nav):
        self.origin = np.array(nav["grid"]["origin"])
        self.cell = nav["grid"]["cell"]
        self.walkable = np.array([[ch == str(WALKABLE) for ch in row] for row in nav["grid"]["rows"]])

    def index(self, point):
        c, r = np.floor((np.asarray(point) - self.origin) / self.cell).astype(int)
        return r, c

    def is_walkable(self, point):
        r, c = self.index(point)
        return 0 <= r < self.walkable.shape[0] and 0 <= c < self.walkable.shape[1] and self.walkable[r, c]

    def sees(self, a, b):
        steps = int(np.ceil(np.hypot(*np.subtract(b, a)) / (self.cell / 2))) + 1
        return all(self.is_walkable(p) for p in np.linspace(a, b, steps))

    def centre(self, r, c):
        return self.origin + (np.array([c, r]) + 0.5) * self.cell

    def approach(self, point, targets):
        """Walkable cells from point to the nearest cell that sees one of targets (breadth first)."""
        start = self.index(point)
        came_from = {start: None}
        frontier = [start]
        for cell in frontier:
            here = self.centre(*cell)
            if any(self.sees(here, t) for t in targets if np.hypot(*(t - here)) < APPROACH_RANGE):
                chain = []
                while cell is not None:
                    chain.append(self.centre(*cell))
                    cell = came_from[cell]
                return chain[::-1]
            for dr, dc in NEIGHBOURS:
                n = (cell[0] + dr, cell[1] + dc)
                if n not in came_from and self.is_walkable(self.centre(*n)):
                    came_from[n] = cell
                    frontier.append(n)
        return None

    def nearest_walkable(self, point):
        rows, cols = np.nonzero(self.walkable)
        centres = self.origin + (np.stack([cols, rows], 1) + 0.5) * self.cell
        return centres[np.argmin(((centres - point) ** 2).sum(1))]


def snap(nav, point, grid=None):
    grid = grid or Grid(nav)
    point = np.asarray(point, float)
    return (point if grid.is_walkable(point) else grid.nearest_walkable(point)).round(3).tolist()


def route(nav, start, goal, grid=None):
    """Shortest walkable route from start to goal (world x, z) as a list of [x, z] points.

    Both ends are moved to the nearest walkable spot (an object's centre is inside the object).
    The roadmap's polyline points are the graph; the ends join every point they can see in a
    straight line, Dijkstra finds the route, and string pulling drops corners that can be seen past.
    """
    grid = grid or Grid(nav)
    start, goal = (np.asarray(snap(nav, p, grid)) for p in (start, goal))
    if grid.sees(start, goal):
        return [start.tolist(), goal.tolist()]
    points, index, neighbours = [], {}, {}

    def add(point):
        key = tuple(np.round(point, 3))
        if key not in index:
            index[key] = len(points)
            points.append(np.asarray(point, float))
            neighbours[index[key]] = []
        return index[key]

    def link(a, b):
        neighbours[a].append(b)
        neighbours[b].append(a)

    for edge in nav["edges"]:
        ids = [add(p) for p in edge["points"]]
        for a, b in zip(ids, ids[1:]):
            link(a, b)
    roadmap_points = np.array(points)
    s, g = add(start), add(goal)
    for end in (s, g):
        # An end tucked in a corner may see none of the roadmap; walk it out cell by cell until it does.
        chain = [points[end]]
        if not any(grid.sees(points[end], p) for p in roadmap_points):
            chain = grid.approach(points[end], roadmap_points) or chain
        ids = [end] + [add(p) for p in chain[1:]]
        for a, b in zip(ids, ids[1:]):
            link(a, b)
        for i in range(len(roadmap_points)):
            if grid.sees(points[ids[-1]], roadmap_points[i]):
                link(ids[-1], i)
    points = np.array(points)

    distance = {s: 0.0}
    previous = {}
    queue = [(0.0, s)]
    while queue:
        d, i = heapq.heappop(queue)
        if i == g:
            break
        if d > distance[i]:
            continue
        for j in neighbours[i]:
            nd = d + float(np.hypot(*(points[j] - points[i])))
            if nd < distance.get(j, np.inf):
                distance[j], previous[j] = nd, i
                heapq.heappush(queue, (nd, j))
    if g not in previous:
        return None
    path = [g]
    while path[-1] != s:
        path.append(previous[path[-1]])
    path = points[path[::-1]]

    pulled = [path[0]]
    i = 0
    while i < len(path) - 1:
        j = len(path) - 1
        while j > i + 1 and not grid.sees(path[i], path[j]):
            j -= 1
        pulled.append(path[j])
        i = j
    return np.round(pulled, 3).tolist()


def length(points):
    return float(np.sum(np.hypot(*np.diff(np.asarray(points), axis=0).T))) if points else float("nan")


def object_routes(nav):
    """A route from the walk's start to every detected object, for the viewer."""
    objects_path = repo / "data" / "pipeline" / "objects.json"
    if not objects_path.exists():
        return {}
    grid = Grid(nav)
    routes = {}
    for obj in json.loads(objects_path.read_text()):
        path = route(nav, nav["start"], [obj["center"][0], obj["center"][2]], grid)
        if path:
            routes[obj["id"]] = {"length": round(length(path), 2), "points": path}
    return routes


def parse_point(text):
    if text.startswith("obj-"):
        obj = next(o for o in json.loads((repo / "data" / "pipeline" / "objects.json").read_text()) if o["id"] == text)
        return [obj["center"][0], obj["center"][2]]
    return [float(v) for v in text.split(",")]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--from", dest="start", help="world x,z (default: the walk's start)")
    parser.add_argument("--to", dest="goal", help="world x,z or an object id; without it, rebuild nav.json")
    args = parser.parse_args()
    if args.goal is None:
        nav = build()
        nav_path.write_text(json.dumps(nav, separators=(",", ":")))
        degrees = np.bincount([n["degree"] for n in nav["nodes"]])
        print(f"floor {nav['floor']}, ceiling {nav['ceiling']} ({nav['ceiling'] - nav['floor']:.2f} above)")
        print(f"{nav['stats']}")
        print(f"{len(nav['nodes'])} nodes (by degree {degrees.tolist()}), {len(nav['edges'])} edges, "
              f"{len(nav['routes'])} object routes -> {nav_path.relative_to(repo)}")
    else:
        nav = json.loads(nav_path.read_text())
        path = route(nav, parse_point(args.start) if args.start else nav["start"], parse_point(args.goal))
        print(json.dumps({"length": round(length(path), 2), "points": path}) if path else "no route")
