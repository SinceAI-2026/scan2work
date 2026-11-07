"""Recover the walkthrough video's camera path with COLMAP (data/sfm/).

The splat renders from tools/render_views.py go into the same reconstruction, so their
known poses tie COLMAP's frame to the splat's (tools/align.py).
"""
import math
from pathlib import Path

import pycolmap

FOV = 43  # tools/render_views.py renders at this vertical field of view, 960x540

root = Path(__file__).resolve().parent.parent / "data" / "sfm"
images = root / "images"
database = root / "database.db"
database.unlink(missing_ok=True)

frames = sorted(p.name for p in images.glob("*.jpg"))
renders = sorted(f"renders/{p.name}" for p in (images / "renders").glob("*.jpg"))
focal = 270 / math.tan(math.radians(FOV / 2))

pycolmap.extract_features(
    database, images, image_names=frames,
    camera_mode=pycolmap.CameraMode.SINGLE,
    reader_options=pycolmap.ImageReaderOptions(camera_model="OPENCV"),
)
pycolmap.extract_features(
    database, images, image_names=renders,
    camera_mode=pycolmap.CameraMode.SINGLE,
    reader_options=pycolmap.ImageReaderOptions(camera_model="PINHOLE", camera_params=f"{focal},{focal},480,270"),
)
pycolmap.match_sequential(database)
pairs = root / "pairs.txt"
pairs.write_text("".join(f"{r} {f}\n" for r in renders for f in frames[::2]))
pycolmap.match_image_pairs(database, pairing_options=pycolmap.ImportedPairingOptions(match_list_path=pairs))

maps = pycolmap.incremental_mapping(database, images, root / "sparse")
for i, rec in maps.items():
    print(i, rec.num_reg_images(), "images,", sum(im.name.startswith("renders/") for im in rec.images.values()), "renders")
