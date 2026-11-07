"""Group each class's instances into types (look-alikes) and cut a 3D model of every type.

Instances of a class (unclassified ones too) cluster by look: DINOv2 view by view, two instances as
close as their best-matching views (SigLIP 2 puts every valve close to every other valve, so it can't
tell types apart; and a mean over views mixes in blurred and side-on ones), then split by the main colour
of the pixels inside their masks, since a red and a blue valve can still look alike in shape. A type is named after the descriptive
phrase in pipeline/classes.json ("brass ball valve") its members match best, when that phrase
clearly beats the others and no other type of the class claims it; otherwise it stays
"valve · type 3", or "unclassified · group 3". The type's model is its most typical instance with enough
Gaussians, cut out of the splat with every original PLY property (SH coefficients included) so it
renders like the scene.

Reads data/pipeline/objects.{json,npz}. Writes data/pipeline/types.json and
data/pipeline/models/<type-id>.ply.
"""
import json
import shutil
from pathlib import Path

import numpy as np
import torch
from skimage.color import rgb2hsv
from PIL import Image
from plyfile import PlyData, PlyElement
from scipy.cluster.hierarchy import fcluster, linkage
from scipy.ndimage import binary_dilation
from transformers import AutoModel, AutoProcessor

from geometry import FLIP, load_splat, object_mask

repo = Path(__file__).resolve().parent.parent
pipeline = repo / "data" / "pipeline"
models_dir = pipeline / "models"

DEVICE = "cuda" if torch.cuda.is_available() else "mps" if torch.backends.mps.is_available() else "cpu"
EMBEDDER = "google/siglip2-so400m-patch16-384"  # same model as embed.py, Apache 2.0
TYPE_DISTANCE = 0.5  # DINOv2 cosine distance (average linkage) below which instances share a type
BEST_PAIRS = 3  # instances are as close as the median of their this many closest view pairs
COLOURED = 0.15  # share of saturated mask pixels from which an object counts as coloured, not grey
# Colour family of each hue bin (30 degrees each, from red).
HUE_FAMILY = ["red", "orange", "orange", "green", "green", "green", "blue", "blue", "blue", "purple", "purple", "red"]
HUE_BINS = 12  # colour histogram: hues of the saturated pixels, plus dark/mid/light for the rest
SATURATED = 0.3
NAME_MARGIN = 0.02  # a type's best phrase must beat its second by this much cosine to name it
MODEL_VOXEL = 0.03  # world units; the cut-out takes every Gaussian in the instance's voxels
MODEL_GROW = 1  # voxels; grow the instance by this much so its faint edge Gaussians come along

UNCLASSIFIED = "unclassified"

classes = {c["name"]: c for c in json.loads((repo / "pipeline" / "classes.json").read_text())["classes"]}
objects = json.loads((pipeline / "objects.json").read_text())
stored = np.load(pipeline / "objects.npz")
rows = [stored["rows"][a:b] for a, b in zip(stored["offsets"][:-1], stored["offsets"][1:])]
embeddings = stored["embeddings"]
emb = np.load(pipeline / "embeddings.npz")
view_index = {inst["id"]: k for k, inst in enumerate(json.loads((pipeline / "instances.json").read_text()))}
dino_views = emb["dino_views"].astype(np.float32)
dino_views /= np.linalg.norm(dino_views, axis=1, keepdims=True)
looks_by_object = [  # every view of the object's merged instances (pipeline/embed.py)
    np.concatenate([dino_views[emb["view_offsets"][view_index[i]]:emb["view_offsets"][view_index[i] + 1]]
                    for i in o["merged_from"]])
    for o in objects]

processor = AutoProcessor.from_pretrained(EMBEDDER)
model = AutoModel.from_pretrained(EMBEDDER).to(DEVICE).eval()


@torch.no_grad()
def embed_texts(texts):
    inputs = processor(text=texts, padding="max_length", max_length=64, return_tensors="pt").to(DEVICE)
    return torch.nn.functional.normalize(model.get_text_features(**inputs).pooler_output, dim=-1).cpu().numpy()


def colour_histogram(views):
    """Share of mask pixels per hue (saturated ones) or per brightness (greys), over all views."""
    pixels = []
    for frame, _, index in views:
        image = np.asarray(Image.open(repo / "data" / "sfm" / "images" / f"{frame}.jpg").convert("RGB")) / 255
        pixels.append(image[object_mask(frame, index)])
    hue, saturation, value = rgb2hsv(np.concatenate(pixels)[None])[0].T
    bins = np.where(
        (saturation > SATURATED) & (value > 0.2),
        np.floor(hue * HUE_BINS).astype(int) % HUE_BINS,
        HUE_BINS + np.minimum((value * 3).astype(int), 2),
    )
    counts = np.bincount(bins, minlength=HUE_BINS + 3)
    return counts / counts.sum()


def colour_family(histogram):
    """The main colour of a colour_histogram: its biggest hue family, or grey when few pixels are saturated."""
    hues = histogram[:HUE_BINS]
    if hues.sum() < COLOURED:
        return "grey"
    families = {}
    for family, share in zip(HUE_FAMILY, hues):
        families[family] = families.get(family, 0) + share
    return max(families, key=families.get)


def look_distances(members):
    """Condensed (pdist-order) distances between objects: the median of their BEST_PAIRS closest view pairs,
    so two of a kind seen from different sides still match on the views that show the same face."""
    return np.array([
        np.median(np.sort(1 - looks_by_object[a] @ looks_by_object[b].T, axis=None)[:BEST_PAIRS])
        for k, a in enumerate(members) for b in members[k + 1:]])


def slug(text):
    return "-".join(text.lower().split())


ply, centres, _ = load_splat()
world = centres @ FLIP.T


def cut(members_rows):
    """PLY rows of every Gaussian (any opacity) in the voxels the instance occupies, grown a little."""
    cells = np.floor(world[members_rows] / MODEL_VOXEL).astype(int)
    low = cells.min(0) - MODEL_GROW
    grid = np.zeros(cells.max(0) - low + 1 + MODEL_GROW, bool)
    grid[tuple((cells - low).T)] = True
    grid = binary_dilation(grid, np.ones((3, 3, 3)), iterations=MODEL_GROW)
    near = np.all((world >= low * MODEL_VOXEL) & (world < (low + grid.shape) * MODEL_VOXEL), axis=1)
    candidates = np.flatnonzero(near)
    inside = grid[tuple((np.floor(world[candidates] / MODEL_VOXEL).astype(int) - low).T)]
    return candidates[inside]


if models_dir.exists():
    shutil.rmtree(models_dir)
models_dir.mkdir(parents=True)
types = []
families = [colour_family(colour_histogram(o["views"])) for o in objects]
for name in [*classes, UNCLASSIFIED]:
    members = [i for i, o in enumerate(objects) if o["class"] == name]
    if not members:
        continue
    if len(members) == 1:
        clusters = np.array([1])
    else:
        looks = fcluster(linkage(look_distances(members), "average"), TYPE_DISTANCE, "distance")
        clusters = np.unique([(look, families[i]) for look, i in zip(looks, members)], axis=0, return_inverse=True)[1].ravel() + 1
    groups = sorted(
        ([members[k] for k in np.flatnonzero(clusters == c)] for c in np.unique(clusters)),
        key=lambda g: (-len(g), -max(objects[i]["gaussians"] for i in g)),
    )

    # Name types: each phrase goes to the type it suits best, if it clearly suits that type.
    phrases = classes[name]["types"] if name in classes else []
    similarity = np.stack([embeddings[g].mean(0) for g in groups]) @ embed_texts([f"a photo of a {p}." for p in phrases]).T if phrases else None
    names = [None] * len(groups)
    if similarity is not None:
        for t in range(len(groups)):
            order = np.argsort(-similarity[t])
            best = order[0]
            clear = len(order) == 1 or similarity[t, best] - similarity[t, order[1]] >= NAME_MARGIN
            if clear and similarity[:, best].argmax() == t:
                names[t] = phrases[best]

    for t, group in enumerate(groups):
        type_id = f"{slug(name)}-{t + 1}"
        centroid = embeddings[group].mean(0)
        # The most typical instance among the better-reconstructed half.
        enough = np.median([objects[i]["gaussians"] for i in group])
        representative = max(
            (i for i in group if objects[i]["gaussians"] >= enough), key=lambda i: embeddings[i] @ centroid
        )
        model_rows = cut(rows[representative])
        PlyData([PlyElement.describe(ply.data[model_rows], "vertex")], byte_order="<").write(models_dir / f"{type_id}.ply")
        points = world[model_rows]
        low, high = np.percentile(points, 2, axis=0), np.percentile(points, 98, axis=0)
        types.append({
            "id": type_id,
            "class": name,
            "name": names[t] or f"{name} · {'group' if name == UNCLASSIFIED else 'type'} {t + 1}",
            "named": names[t] is not None,
            "instances": [objects[i]["id"] for i in sorted(group, key=lambda i: -objects[i]["gaussians"])],
            "representative": objects[representative]["id"],
            "model": f"data/pipeline/models/{type_id}.ply",
            "model_gaussians": len(model_rows),
            "model_center": [round(float(v), 3) for v in (low + high) / 2],
            "model_radius": round(float(np.linalg.norm(high - low) / 2), 3),
        })
    print(name, len(members), "instances ->", len(groups), "types:", [types[-len(groups) + t]["name"] for t in range(len(groups))])

(pipeline / "types.json").write_text(json.dumps(types, indent=1))
print(len(types), "types,", sum(t["model_gaussians"] for t in types), "Gaussians in models")
