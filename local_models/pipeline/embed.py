"""Step 3: look at every 3D instance in its best video views and embed what it looks like.

View selection follows OpenMask3D (Takmaz et al., NeurIPS 2023): the instance's top VIEWS frames by
how many of its Gaussians each one sees (cluster.py orders them), and in each a crop at several scales (MaskClustering does
the same). Scale 0 is the instance alone (its masks' pixels on grey), the others add surroundings.
Two embedders:
- SigLIP 2 for the zero-shot names in pipeline/appearance.py; one
  embedding per view (mean over its scales) so pipeline/consistency.py can ask each view on its own.
- DINOv2 for how alike two instances look (pipeline/similarity.py); self-supervised image features
  separate look-alikes better than text-aligned ones and don't care what a thing is called.

Reads data/pipeline/instances.{json,npz} and the SAM masks. Writes data/pipeline/embeddings.npz,
data/pipeline/views.json (per instance: frame, box, mask indices of each view) and the library's
crops in data/pipeline/crops/<instance>-<k>.jpg.
"""
import json
import shutil
import time

import numpy as np
import torch
from PIL import Image
from transformers import AutoImageProcessor, AutoModel, AutoProcessor

from geometry import MASKS, object_mask, repo

pipeline = repo / "data" / "pipeline"
frames_dir = repo / "data" / "sfm" / "images"
crops_dir = pipeline / "crops"

DEVICE = "cuda" if torch.cuda.is_available() else "mps" if torch.backends.mps.is_available() else "cpu"
NAMER = "google/siglip2-so400m-patch16-384"  # Apache 2.0, main's classifier
LOOKS = "facebook/dinov2-base"  # Apache 2.0
FRAME_WIDTH, FRAME_HEIGHT = 960, 540
VIEWS = 5  # OpenMask3D's k: views per instance
SCALES = [1.1, 1.6, 2.4]  # crop side / mask box side; 1.1 is the masked crop, the rest keep the background
BACKGROUND = (128, 128, 128)  # what replaces everything outside the mask in the masked crop
SAVED_CROPS = 4  # crops kept per instance for the library
BATCH = 16  # views per forward pass (x3 crops for SigLIP)
CROP_SIDE = 384  # px; crops are shrunk to SigLIP's input size (and the library's thumbnail size)


def box_of(mask):
    ys, xs = np.nonzero(mask)
    return [float(xs.min()), float(ys.min()), float(xs.max() + 1), float(ys.max() + 1)]


def crops(frame, indices, folder=MASKS):
    """The view's crops, one per scale (the first masked), and its box in frame pixels."""
    mask = object_mask(frame, indices, folder)
    image = Image.open(frames_dir / f"{frame}.jpg").convert("RGB")
    x0, y0, x1, y1 = box_of(mask)
    out = []
    for k, scale in enumerate(SCALES):
        side = max(x1 - x0, y1 - y0) * scale
        left, top = (x0 + x1 - side) / 2, (y0 + y1 - side) / 2
        source = image
        if k == 0:
            pixels = np.asarray(image).copy()
            pixels[~mask] = BACKGROUND
            source = Image.fromarray(pixels)
        canvas = Image.new("RGB", (round(side), round(side)), BACKGROUND)  # parts outside the frame stay grey
        canvas.paste(source, (round(-left), round(-top)))
        canvas.thumbnail((CROP_SIDE, CROP_SIDE))
        out.append(canvas)
    return out, [x0, y0, x1, y1]


@torch.no_grad()
def embed(model, processor, images, features):
    """Unit-length embeddings of a batch of images."""
    inputs = processor(images=images, return_tensors="pt").to(DEVICE)
    return torch.nn.functional.normalize(features(model, inputs), dim=-1).cpu().numpy()


def siglip_features(model, inputs):
    return model.get_image_features(**inputs).pooler_output


def dino_features(model, inputs):
    return model(**inputs).last_hidden_state[:, 0]  # the CLS token


def mean_direction(vectors):
    mean = vectors.mean(0)
    return mean / np.linalg.norm(mean)


def main():
    t0 = time.time()
    instances = json.loads((pipeline / "instances.json").read_text())
    namer, namer_input = AutoModel.from_pretrained(NAMER).to(DEVICE).eval(), AutoProcessor.from_pretrained(NAMER)
    looks, looks_input = AutoModel.from_pretrained(LOOKS).to(DEVICE).eval(), AutoImageProcessor.from_pretrained(LOOKS)

    if crops_dir.exists():
        shutil.rmtree(crops_dir)
    crops_dir.mkdir(parents=True)
    views = [[[frame, inst["views"][frame]] for frame in inst["frames"][:VIEWS]]  # cluster.py ranks them
             for inst in instances]
    jobs = [(k, n, frame, indices) for k, picked in enumerate(views) for n, (frame, indices) in enumerate(picked)]
    siglip, dino = [], []
    for start in range(0, len(jobs), BATCH):  # crops are made batch by batch: thousands don't fit in memory
        images = []
        for k, n, frame, indices in jobs[start:start + BATCH]:
            scaled, box = crops(frame, indices)
            images += scaled
            views[k][n] = [frame, box, indices]
            if n < SAVED_CROPS:
                scaled[1].save(crops_dir / f"{instances[k]['id']}-{n}.jpg", quality=88)  # the first scale with surroundings
        siglip.append(embed(namer, namer_input, images, siglip_features))
        dino.append(embed(looks, looks_input, images[:: len(SCALES)], dino_features))
        if start // BATCH % 20 == 0:
            print(f"{start + BATCH}/{len(jobs)} views ({time.time() - t0:.0f}s)", flush=True)
    siglip, dino = np.concatenate(siglip), np.concatenate(dino)

    per_view = siglip.reshape(-1, len(SCALES), siglip.shape[1]).mean(1)
    per_view /= np.linalg.norm(per_view, axis=1, keepdims=True)
    offsets = np.cumsum([0] + [len(v) for v in views])
    np.savez_compressed(
        pipeline / "embeddings.npz",
        view_offsets=offsets,
        siglip_views=per_view.astype(np.float32),  # one per view, mean over its scales
        siglip_scales=siglip.reshape(-1, len(SCALES), siglip.shape[1]).astype(np.float16),  # view x scale (0 = masked)
        siglip=np.stack([mean_direction(per_view[a:b]) for a, b in zip(offsets[:-1], offsets[1:])]).astype(np.float32),
        dino=np.stack([mean_direction(dino[a:b]) for a, b in zip(offsets[:-1], offsets[1:])]).astype(np.float32),
        dino_views=dino.astype(np.float16),  # one per view (masked crop); catalog.py matches types view by view
    )
    (pipeline / "views.json").write_text(json.dumps(views))
    print(f"{len(instances)} instances embedded ({time.time() - t0:.0f}s)")


if __name__ == "__main__":
    main()
