"""Render the splat from known cameras through the viewer, for tools/align.py.

Needs the viewer served on localhost:8765 (python3 -m http.server 8765). Writes
data/sfm/images/renders/*.jpg and data/sfm/renders.json (world-frame camera poses).

Cameras: a grid at eye height in the middle of the room, plus, once walk.json exists, every
WALK_STEP-th pose of the video path (known free space, and views the video frames match well).
"""
import json
import shutil
from pathlib import Path

from playwright.sync_api import sync_playwright
from scipy.spatial.transform import Rotation

repo = Path(__file__).resolve().parent.parent
out = repo / "data" / "sfm" / "images" / "renders"
shutil.rmtree(out, ignore_errors=True)
out.mkdir(parents=True)

FOV = 43  # match the walkthrough video, see VIDEO_FOV in index.html
EYE_HEIGHT = 0.4  # world y; the floor is at about -1.07
XS = [-3.5, -2, -0.5, 1, 2.5]
ZS = [1.6, 2.15, 2.7]
YAWS = range(0, 360, 45)
WALK_STEP = 4

cameras = [
    ([x, EYE_HEIGHT, z], Rotation.from_euler("YXZ", [yaw, 0, 0], degrees=True).as_quat().tolist())
    for x in XS for z in ZS for yaw in YAWS
]
walk = repo / "walk.json"
if walk.exists():
    cameras += [(frame["position"], frame["quaternion"]) for frame in json.loads(walk.read_text())[::WALK_STEP]]

poses = []
with sync_playwright() as p:
    browser = p.chromium.launch(channel="chrome", headless=False, args=["--use-angle=metal"])
    page = browser.new_page(viewport={"width": 960, "height": 540}, device_scale_factor=1)
    page.goto("http://localhost:8765/")
    page.wait_for_function("document.getElementById('minimap').height > 150", timeout=90000)
    page.evaluate("""for (const id of ['minimap', 'sidebar']) document.getElementById(id).style.display = 'none';
        window.viewer.boxes.visible = false;""")
    for position, quaternion in cameras:
        name = f"r{len(poses):03d}.jpg"
        page.evaluate(
            """([p, q, fov]) => {
                const { camera } = window.viewer;
                camera.fov = fov;
                camera.updateProjectionMatrix();
                camera.position.fromArray(p);
                camera.quaternion.fromArray(q);
            }""",
            [position, quaternion, FOV],
        )
        page.wait_for_timeout(600)
        page.locator("canvas").first.screenshot(path=out / name, type="jpeg", quality=92)
        poses.append({"name": f"renders/{name}", "position": position, "quaternion": quaternion})
    browser.close()
(repo / "data" / "sfm" / "renders.json").write_text(json.dumps(poses))
print(len(poses), "renders")
