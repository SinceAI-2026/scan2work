"""Step 1: class-agnostic SAM 2.1 masks for the posed frames (automatic mask generator, point grid).

A grid of single-point prompts per frame, 3 masks per point; keep masks SAM is confident in and that
are stable under a threshold shift, drop tiny ones, NMS by box. Masks stored at half resolution, packed,
in data/pipeline/amg/<frame>.npz. Usage: amg.py [STEP] (every STEP-th frame).
"""
import sys
import time
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F
import torchvision
from PIL import Image
from transformers import Sam2Model, Sam2Processor

root = Path(__file__).resolve().parent.parent
frames_dir = root / "data" / "sfm" / "images"
out = root / "data" / "pipeline" / "amg"
out.mkdir(parents=True, exist_ok=True)

DEVICE = "cuda" if torch.cuda.is_available() else "mps" if torch.backends.mps.is_available() else "cpu"
SEGMENTER = "facebook/sam2.1-hiera-large"
GRID = (32, 18)  # points across x, y (960x540 frames: one every 30 px)
POINT_BATCH = 96
MIN_PRED_IOU = 0.7
MIN_STABILITY = 0.85  # area(logit > 1) / area(logit > -1)
MIN_AREA = 100  # half-res px (= 400 full-res px, about 20x20)
NMS_IOU = 0.7
H, W = 270, 480  # half resolution

step = int(sys.argv[1]) if len(sys.argv) > 1 else 1
processor = Sam2Processor.from_pretrained(SEGMENTER)
model = Sam2Model.from_pretrained(SEGMENTER).to(DEVICE).eval()

frames = sorted(frames_dir.glob("*.jpg"))[::step]
xs = (np.arange(GRID[0]) + 0.5) * 960 / GRID[0]
ys = (np.arange(GRID[1]) + 0.5) * 540 / GRID[1]
points = [[[float(x), float(y)]] for y in ys for x in xs]
t0 = time.time()
for n, frame in enumerate(frames):
    target = out / f"{frame.stem}.npz"
    if target.exists():
        continue
    image = Image.open(frame).convert("RGB")
    with torch.no_grad():
        base = processor(images=image, return_tensors="pt").to(DEVICE)
        embeddings = model.get_image_embeddings(base["pixel_values"])
        logits, ious = [], []
        for s in range(0, len(points), POINT_BATCH):
            chunk = points[s:s + POINT_BATCH]
            p = processor(images=image, input_points=[chunk], input_labels=[[[1]] * len(chunk)], return_tensors="pt")
            o = model(image_embeddings=embeddings, input_points=p["input_points"].to(DEVICE),
                      input_labels=p["input_labels"].to(DEVICE), multimask_output=True)
            lo = o.pred_masks[0].flatten(0, 1)  # (points*3, 256, 256)
            logits.append(F.interpolate(lo[:, None].float(), size=(H, W), mode="bilinear", align_corners=False)[:, 0])
            ious.append(o.iou_scores[0].flatten())
        logits, ious = torch.cat(logits), torch.cat(ious)
        hi, lo_ = (logits > 1).sum((1, 2)).float(), (logits > -1).sum((1, 2)).float()
        stability = hi / lo_.clamp(min=1)
        masks = logits > 0
        area = masks.sum((1, 2))
        keep = (ious >= MIN_PRED_IOU) & (stability >= MIN_STABILITY) & (area >= MIN_AREA)
        masks, ious = masks[keep], ious[keep]
        if len(masks):
            rows, cols = masks.any(2), masks.any(1)
            boxes = torch.stack([cols.float().argmax(1), rows.float().argmax(1),
                                 W - 1 - cols.flip(1).float().argmax(1), H - 1 - rows.flip(1).float().argmax(1)], 1).float()
            k = torchvision.ops.nms(boxes.cpu(), ious.cpu(), NMS_IOU)
            masks, ious, boxes = masks[k.to(DEVICE)], ious[k.to(DEVICE)], boxes[k.to(DEVICE)]
        masks = masks.cpu().numpy()
    np.savez_compressed(target, masks=np.packbits(masks, axis=-1), mask_shape=np.array(masks.shape),
                        scores=ious.cpu().numpy(), boxes=(boxes.cpu().numpy() * 2 if len(masks) else np.zeros((0, 4))))
    el = time.time() - t0
    print(f"{frame.name} {len(masks)} masks  {el / (n + 1):.1f}s/frame", flush=True)
print("done", len(frames), "frames in", round(time.time() - t0), "s")
