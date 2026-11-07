"""Step 2: stitch the class-agnostic SAM masks into 3D instances by view consensus.

Follows MaskClustering (Yan et al., CVPR 2024, arXiv 2401.07745), with the splat's Gaussians as the
point cloud:
1. Lift: each mask claims the Gaussians that project into it and pass the depth test, keeping the
   biggest touching clump (drops the background that bleeds in at mask edges).
2. Drop under-segmenting masks: a mask whose Gaussians, seen from other frames, are split over
   several masks there (none contains them) in more than SPLIT_SHARE of those frames covers several
   things.
3. Cluster: nodes start as masks. For a pair of nodes an observer is a frame that sees enough of
   both and segmented what it sees (see COVERED: SAM's automatic masks, unlike the paper's
   CropFormer, leave gaps, and a frame with no mask on a thing cannot vote on it); a supporter is an observer with one mask that contains both. Pairs whose supporters are
   at least CONSENSUS of their observers merge. The minimum observer count starts at the top 5% of
   pairs and falls to the bottom 5%, so pairs backed by many views merge first; merged nodes are
   re-evaluated every round.
4. Each node's Gaussians: those its own views put inside its masks at least POINT_SHARE of the time
   they see them; split into touching clumps (OVIR-3D/MaskClustering's DBSCAN step); parts that
   sit inside a bigger instance go; room structure (walls, floor, ceiling, huge slabs) goes.

Writes data/pipeline/instances.json (box, frames by how many of its Gaussians they see, mask indices per frame, touching neighbours with
their view consensus), instances.npz (Gaussians as PLY rows) and evidence.npz (per instance
Gaussian: frames that see it, and how many of those put it inside the instance).
"""
import json
import time
from collections import Counter

import numpy as np
from scipy import ndimage, sparse

from geometry import FLIP, MASKS, MIN_OPACITY, fit_box, frame_masks, load_splat, posed_frames, project, repo, to_colmap

pipeline = repo / "data" / "pipeline"

MIN_CLAIM = 30  # Gaussians a mask must claim to count (fewer: the thing is not in the splat)
CLUMP_VOXEL = 0.05  # m; Gaussians in touching voxels of this size form one clump
VISIBLE = 0.3  # share of a node's Gaussians a frame must see to observe it (paper: 0.3)
CONTAINED = 0.8  # share of a node's visible Gaussians inside one mask for the mask to contain it (paper: 0.8)
CONSENSUS = 0.9  # supporters / observers needed to merge two nodes (paper: 0.9)
SPLIT_SHARE = 0.2  # a mask split in more than this share of the frames observing it is dropped (paper: 0.2)
COVERED = 0.8  # a frame observes a node only if its masks cover this share of the node's visible Gaussians
PERCENTILES = range(95, 0, -5)  # observer-count percentiles of the candidate pairs, one per round
MIN_OBSERVERS = 2  # one shared view is no consensus
MIN_FRAMES = 2  # an instance needs masks from this many frames
POINT_SHARE = 0.5  # a Gaussian stays if its instance's views that see it put it inside this often
MIN_GAUSSIANS = 40  # smaller clumps are specks, not objects
PART_OF = 0.8  # an instance with this share of its Gaussians inside a bigger one is a part of it
MAX_EXTENT = 2.5  # m; a longer box is room structure (the room is ~9 m x 7 m, equipment is smaller)
SLAB_RATIO = 0.05  # thinnest / longest box side below this, longer than SLAB_LENGTH: wall or floor patch
SLAB_LENGTH = 1.0  # m
PLANE_BAND = 0.12  # m; a flat thing this close to the floor or ceiling is floor or ceiling
TOUCH_VOXEL = 0.05  # m; instances with Gaussians in neighbouring voxels of this size touch

nav = json.loads((pipeline / "nav.json").read_text())
FLOOR, CEILING = nav["floor"], nav["ceiling"]


def clumps(gaussians, world):
    """Split Gaussians into touching clumps (26-connected voxels), biggest first."""
    cells = np.floor(world[gaussians] / CLUMP_VOXEL).astype(int)
    cells -= cells.min(0)
    grid = np.zeros(cells.max(0) + 1, bool)
    grid[tuple(cells.T)] = True
    labels, count = ndimage.label(grid, structure=np.ones((3, 3, 3)))
    of_point = labels[tuple(cells.T)]
    order = np.argsort(-np.bincount(of_point)[1:]) + 1
    return [gaussians[of_point == label] for label in order]


def incidence(sets, n_cols):
    """Sparse (len(sets) x n_cols) 0/1 matrix, row i set at sets[i]."""
    rows = np.repeat(np.arange(len(sets)), [len(s) for s in sets])
    cols = np.concatenate(sets) if len(sets) else np.zeros(0, int)
    return sparse.csr_matrix((np.ones(len(rows), np.float32), (rows, cols)), shape=(len(sets), n_cols))


def lift(world, points):
    """Every mask's claimed Gaussians, and per frame the Gaussians it sees."""
    posed = posed_frames()
    frames, masks, mask_frame, seen = [], [], [], []
    for path in sorted(MASKS.glob("*.npz")):
        image = posed.get(path.stem)
        if image is None:
            continue
        stack = frame_masks(path.stem)
        _, height, width = stack.shape
        u, v, visible = project(image, points, height, width)
        if not len(visible):
            continue
        for i, mask in enumerate(stack):
            claimed = visible[mask[v[visible], u[visible]]]
            if len(claimed) >= MIN_CLAIM:
                claimed = clumps(claimed, world)[0]
            if len(claimed) >= MIN_CLAIM:
                masks.append((path.stem, i, claimed))
                mask_frame.append(len(frames))
        seen.append(visible)
        frames.append(path.stem)
    return frames, masks, np.array(mask_frame), seen


def covering(in_mask, mask_frame, n_frames):
    """Gaussian x frame: the Gaussian lies in some mask of the frame."""
    to_frame = sparse.csr_matrix((np.ones(len(mask_frame)), (np.arange(len(mask_frame)), mask_frame)),
                                 shape=(len(mask_frame), n_frames))
    return (in_mask @ to_frame).sign().tocsr()


def containment(node_points, seen, in_any, in_mask, mask_frame, size):
    """Per node: frames observing it (bool node x frame), visible counts, and the masks containing it."""
    visible = (node_points @ seen).toarray()
    covered = (node_points @ in_any).toarray()
    observed = (visible >= VISIBLE * size[:, None]) & (covered >= COVERED * visible)
    inside = (node_points @ in_mask).tocoo()
    f = mask_frame[inside.col]
    ok = observed[inside.row, f] & (inside.data >= CONTAINED * visible[inside.row, f])
    contained = sparse.csr_matrix((np.ones(ok.sum(), np.float32), (inside.row[ok], inside.col[ok])),
                                  shape=(node_points.shape[0], in_mask.shape[1]))
    return observed, visible, contained


def pair_counts(observed, contained, mask_frame, n_frames):
    """Candidate pairs (nodes contained by a common mask), their observers and supporters (frames,
    not masks: SAM's masks overlap, so several masks of one frame can contain the same pair)."""
    by_frame = contained.tocsc()
    supporters = sparse.csr_matrix(contained.shape[:1] * 2, dtype=np.float32)
    for f in range(n_frames):
        cols = np.flatnonzero(mask_frame == f)
        if len(cols):
            block = by_frame[:, cols]
            supporters = supporters + sparse.triu(block @ block.T, k=1).sign()
    pairs = supporters.tocoo()
    bits = np.packbits(observed, axis=1)
    popcount = np.array([bin(b).count("1") for b in range(256)], np.int32)
    observers = np.concatenate([popcount[bits[pairs.row[s:s + 500000]] & bits[pairs.col[s:s + 500000]]].sum(1)
                                for s in range(0, max(len(pairs.row), 1), 500000)])
    return pairs.row, pairs.col, observers, pairs.data


def drop_undersegmenting(masks, mask_frame, seen, in_any, in_mask):
    """MaskClustering's filter: masks split over several masks (none contains them) in more than
    SPLIT_SHARE of the other frames observing them."""
    claims = incidence([m[2] for m in masks], seen.shape[0])
    size = np.asarray(claims.sum(1)).ravel()
    observed, _, contained = containment(claims, seen, in_any, in_mask, mask_frame, size)
    observed[np.arange(len(masks)), mask_frame] = False  # its own frame says nothing
    held = np.zeros_like(observed)
    c = contained.tocoo()
    held[c.row, mask_frame[c.col]] = True
    split = observed & ~held
    return split.sum(1) <= SPLIT_SHARE * observed.sum(1)


def cluster(masks, mask_frame, seen, in_any, in_mask, n_points, n_frames):
    """Iterative view-consensus merging; returns the member masks of every final node."""
    nodes = [[k] for k in range(len(masks))]
    points = [m[2] for m in masks]
    thresholds = None
    for round_ in range(len(PERCENTILES)):
        node_points = incidence(points, n_points)
        size = np.asarray(node_points.sum(1)).ravel()
        observed, _, contained = containment(node_points, seen, in_any, in_mask, mask_frame, size)
        a, b, observers, supporters = pair_counts(observed, contained, mask_frame, n_frames)
        if thresholds is None:  # MaskClustering: from the observer counts of the first graph
            thresholds = [max(MIN_OBSERVERS, int(np.percentile(observers, p))) for p in PERCENTILES]
        merge = (observers >= thresholds[round_]) & (supporters >= CONSENSUS * observers)
        graph = sparse.csr_matrix((np.ones(merge.sum()), (a[merge], b[merge])), shape=(len(points),) * 2)
        _, label = sparse.csgraph.connected_components(graph, directed=False)
        groups = [np.flatnonzero(label == g) for g in range(label.max() + 1)]
        nodes = [[m for i in g for m in nodes[i]] for g in groups]
        points = [np.unique(np.concatenate([points[i] for i in g])) for g in groups]
        print(f"  round {round_ + 1}: observers >= {thresholds[round_]}, {len(a)} pairs, "
              f"{merge.sum()} merge -> {len(nodes)} nodes", flush=True)
    return nodes


def structure(box):
    """Why a box is room structure, or None."""
    size, y = np.array(box["size"]), box["center"][1]
    if size.max() > MAX_EXTENT:
        return "longer than the room's equipment"
    if size.min() < SLAB_RATIO * size.max() and size.max() > SLAB_LENGTH:
        return "wall or floor slab"
    flat = size[1] < PLANE_BAND
    if flat and (y - FLOOR < PLANE_BAND or CEILING - y < PLANE_BAND):
        return "floor or ceiling patch"
    return None


def evidence(gaussians, members, masks, mask_frame, seen, in_mask):
    """Per Gaussian of an instance: its frames that see it, and how many of those put it inside."""
    member_frames = np.unique(mask_frame[members])
    looks = np.asarray(seen[gaussians][:, member_frames].sum(1)).ravel()
    to_frame = sparse.csr_matrix((np.ones(len(members)), (np.arange(len(members)), np.searchsorted(member_frames, mask_frame[members]))),
                                 shape=(len(members), len(member_frames)))
    inside = np.asarray((in_mask[gaussians][:, members] @ to_frame).sign().sum(1)).ravel()
    return looks, inside


def touching(rows_world):
    """Pairs of instances with Gaussians in neighbouring voxels: (a, b, share of the smaller's voxels)."""
    voxels = [set(map(tuple, np.floor(w / TOUCH_VOXEL).astype(int))) for w in rows_world]
    offsets = [(x, y, z) for x in (-1, 0, 1) for y in (-1, 0, 1) for z in (-1, 0, 1)]
    owner = {}
    for k, cells in enumerate(voxels):
        for cell in cells:
            owner.setdefault(cell, set()).add(k)
    shared = Counter()
    for k, cells in enumerate(voxels):
        near = Counter()
        for x, y, z in cells:
            hit = set()
            for dx, dy, dz in offsets:
                hit |= owner.get((x + dx, y + dy, z + dz), set())
            near.update(o for o in hit if o > k)
        for o, n in near.items():
            shared[k, o] = n / min(len(voxels[k]), len(voxels[o]))
    return shared


def main():
    t0 = time.time()
    _, centres, opacity = load_splat()
    kept = np.flatnonzero(opacity > MIN_OPACITY)  # indices below are into this subset; kept[i] is the PLY row
    world = centres[kept] @ FLIP.T
    frames, masks, mask_frame, seen_lists = lift(world, to_colmap(centres[kept]))
    n = len(kept)
    seen = incidence(seen_lists, n).T.tocsr()  # Gaussian x frame: the frame sees it
    in_mask = incidence([m[2] for m in masks], n).T.tocsr()  # Gaussian x mask
    print(f"lift: {len(frames)} frames, {len(masks)} masks on the splat ({time.time() - t0:.0f}s)", flush=True)

    keep = drop_undersegmenting(masks, mask_frame, seen, covering(in_mask, mask_frame, len(frames)), in_mask)
    print(f"under-segmenting masks dropped: {(~keep).sum()} of {len(masks)} ({time.time() - t0:.0f}s)", flush=True)
    masks = [m for m, k in zip(masks, keep) if k]
    mask_frame = mask_frame[keep]
    in_mask = in_mask[:, np.flatnonzero(keep)]
    in_any = covering(in_mask, mask_frame, len(frames))  # the dropped masks no longer cover anything

    nodes = cluster(masks, mask_frame, seen, in_any, in_mask, n, len(frames))
    print(f"cluster: {len(nodes)} nodes ({time.time() - t0:.0f}s)", flush=True)

    candidates, dropped = [], Counter()
    for members in nodes:
        members = np.array(members)
        if len(np.unique(mask_frame[members])) < MIN_FRAMES:
            dropped["seen in one frame"] += 1
            continue
        gaussians = np.unique(np.concatenate([masks[m][2] for m in members]))
        looks, inside = evidence(gaussians, members, masks, mask_frame, seen, in_mask)
        gaussians = gaussians[inside >= POINT_SHARE * np.maximum(looks, 1)]
        if len(gaussians) < MIN_GAUSSIANS:
            dropped["too few Gaussians"] += 1
            continue
        for part in clumps(gaussians, world):
            if len(part) < MIN_GAUSSIANS:
                dropped["speck split off"] += 1
                continue
            # the masks of this clump: those that claim mostly Gaussians in it
            own = [m for m in members if np.isin(masks[m][2], part).mean() >= 0.5]
            if len(np.unique(mask_frame[own])) < MIN_FRAMES:
                dropped["seen in one frame"] += 1
                continue
            box = fit_box(world[part])
            why = structure(box)
            if why:
                dropped[why] += 1
                continue
            candidates.append((part, np.array(own), box))

    # Parts inside bigger instances go (SAM gives both a whole and its parts; where they did not merge
    # the whole one stays). Gaussians two instances still share go to the bigger one.
    candidates.sort(key=lambda c: -len(c[0]))
    owner = np.full(n, -1)
    final = []
    for part, own, box in candidates:
        taken = owner[part] >= 0
        if taken.mean() >= PART_OF:
            dropped["part of a bigger instance"] += 1
            continue
        part = part[~taken]
        if len(part) < MIN_GAUSSIANS:
            dropped["part of a bigger instance"] += 1
            continue
        owner[part] = len(final)
        final.append((part, own, fit_box(world[part])))
    print("dropped:", dict(dropped))

    # View consensus between touching instances, for pipeline/fuse.py's merge check.
    node_points = incidence([p for p, _, _ in final], n)
    size = np.asarray(node_points.sum(1)).ravel()
    observed, _, contained = containment(node_points, seen, in_any, in_mask, mask_frame, size)
    a, b, observers, supporters = pair_counts(observed, contained, mask_frame, len(frames))
    consensus = {(i, j): (int(o), int(s)) for i, j, o, s in zip(a, b, observers, supporters)}
    touch = touching([world[p] for p, _, _ in final])

    instances, rows, looks_all, inside_all = [], [], [], []
    for k, (part, own, box) in enumerate(final):
        views = {}
        for m in own:
            views.setdefault(masks[m][0], []).append(int(masks[m][1]))
        frame_index = {f: i for i, f in enumerate(frames)}
        sees = np.asarray(seen[part][:, [frame_index[f] for f in views]].sum(0)).ravel()
        by_visibility = [f for _, f in sorted(zip(-sees, views))]  # OpenMask3D ranks views by visible points
        looks, inside = evidence(part, own, masks, mask_frame, seen, in_mask)
        neighbours = []
        for (i, j), share in touch.items():
            if k in (i, j):
                other = j if i == k else i
                observers_, supporters_ = consensus.get((min(i, j), max(i, j)), (int((observed[i] & observed[j]).sum()), 0))
                neighbours.append([f"inst-{other + 1:04d}", round(share, 3), observers_, supporters_])
        instances.append({
            "id": f"inst-{k + 1:04d}", "gaussians": int(len(part)), "masks": int(len(own)),
            "frames": by_visibility, "views": views, "observed_frames": int(observed[k].sum()),
            "touching": neighbours,  # [instance, share of voxels touching, observers, supporters]
            **box,
        })
        rows.append(kept[part])
        looks_all.append(looks)
        inside_all.append(inside)

    offsets = np.cumsum([0] + [len(r) for r in rows])
    (pipeline / "instances.json").write_text(json.dumps(instances))
    np.savez_compressed(pipeline / "instances.npz", rows=np.concatenate(rows).astype(np.int32), offsets=offsets)
    np.savez_compressed(pipeline / "evidence.npz", rows=np.concatenate(rows).astype(np.int32), offsets=offsets,
                        looks=np.concatenate(looks_all).astype(np.int16), inside=np.concatenate(inside_all).astype(np.int16))
    print(f"{len(instances)} instances ({time.time() - t0:.0f}s)")


if __name__ == "__main__":
    main()
