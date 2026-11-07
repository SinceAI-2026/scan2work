"""How close a saved object looks to the real camera image: the scan splats against the refined model.

    .venv/bin/python -m tests.eval_refine [OBJ-008 OBJ-204 ...] [--out DIR]

For each object, the best walkthrough frame that sees it is the evaluation frame. The model is refined with that
frame and its neighbours (+-4 frames) held out, then the scan and the model are rendered from the evaluation
frame's camera with the fit's own rasteriser and compared with the photo inside the scan's footprint:
  err   mean absolute RGB error (0..1) after a per-image exposure gain, lower is better
  ssim  structural similarity of the grey images, higher is better
After Scan2Service's scripts/eval_isolate.py. Writes photo | scan | refined tiles to --out.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
from PIL import Image
from scipy.ndimage import gaussian_filter

from pipeline import object_refine as RF
from pipeline import walk as WK
from pipeline.asset_export import SceneCutter

W, H = 640, 360
HOLD_OUT = 4
OBJECTS = ["OBJ-008", "OBJ-204", "OBJ-231", "OBJ-001", "OBJ-100", "OBJ-206"]


def ssim(a, b, mask):
    g = lambda x: gaussian_filter(x, 1.5)
    ma, mb = g(a), g(b)
    va, vb, cab = g(a * a) - ma ** 2, g(b * b) - mb ** 2, g(a * b) - ma * mb
    s = ((2 * ma * mb + 1e-4) * (2 * cab + 9e-4)) / ((ma ** 2 + mb ** 2 + 1e-4) * (va + vb + 9e-4))
    return float(s[mask].mean())


def score(img, cover, photo, mask):
    out = img / np.maximum(cover[..., None], 1e-6)
    out = np.clip(out * np.median(photo[mask], 0) / np.maximum(np.median(out[mask], 0), 1e-3), 0, 1)
    grey = lambda x: x @ [0.299, 0.587, 0.114]
    return float(np.abs(out - photo)[mask].mean()), ssim(grey(out), grey(photo), mask), out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ids", nargs="*", default=OBJECTS)
    ap.add_argument("--out", default="/tmp/field-atlas-eval")
    args = ap.parse_args()
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    cut = SceneCutter()
    walk = RF.walk_frames()
    rows = []
    for oid in args.ids:
        _, idx, _, _ = cut._object(oid)
        scan = cut.packed[idx]
        frames = RF.pick_frames(scan["xyz"].astype(np.float64), cut.packed["xyz"], k=1)
        if not frames:
            print(f"{oid}: no frame sees it")
            continue
        fi = frames[0]
        V = WK.camera(walk, fi)["_V"]
        photo = np.asarray(Image.open(walk["files"][fi]).convert("RGB").resize((W, H), Image.LANCZOS), np.float64) / 255
        img, cover = RF.render_gspl(scan, V, W, H)
        mask = cover > 0.5
        model, _ = RF.refine(cut, oid, exclude_frames=range(fi - HOLD_OUT, fi + HOLD_OUT + 1))
        if model is None:
            print(f"{oid}: refinement found no training frame once {walk['files'][fi].name} was held out")
            continue
        s_err, s_ssim, s_img = score(img, cover, photo, mask)
        r_err, r_ssim, r_img = score(*RF.render_gspl(model, V, W, H), photo, mask)
        rows.append((s_err, s_ssim, r_err, r_ssim))
        print(f"{oid}  frame {walk['files'][fi].name}  scan err {s_err:.4f} ssim {s_ssim:.4f}  "
              f"refined err {r_err:.4f} ssim {r_ssim:.4f}")
        ys, xs = np.nonzero(mask)
        y0, y1, x0, x1 = max(ys.min() - 10, 0), min(ys.max() + 10, H), max(xs.min() - 10, 0), min(xs.max() + 10, W)
        tiles = [photo[y0:y1, x0:x1]] + [np.where(mask[y0:y1, x0:x1, None], t[y0:y1, x0:x1], 0.07) for t in (s_img, r_img)]
        Image.fromarray((np.concatenate(tiles, 1) * 255).astype(np.uint8)).save(out_dir / f"{oid}.png")
    if rows:
        m = np.mean(rows, 0)
        print(f"mean over {len(rows)} objects  scan err {m[0]:.4f} ssim {m[1]:.4f}  refined err {m[2]:.4f} ssim {m[3]:.4f}")
    print(f"tiles (photo | scan | refined) -> {out_dir}")


if __name__ == "__main__":
    main()
