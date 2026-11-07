"""Record the splat along the video's camera path (walk.json) into data/walk-frames/.

Needs the viewer served on localhost:8765. Frames are rendered one pose at a time, so the
recording keeps the video's timing however slow the renders are.
"""
import json
import shutil
from pathlib import Path

import numpy as np
from playwright.sync_api import sync_playwright
from scipy.spatial.transform import Rotation, Slerp

FPS = 15
FOV = 43  # VIDEO_FOV in index.html

repo = Path(__file__).resolve().parent.parent
out = repo / "data" / "walk-frames"
shutil.rmtree(out, ignore_errors=True)
out.mkdir(parents=True)

walk = json.loads((repo / "walk.json").read_text())
times = np.array([frame["t"] for frame in walk])
positions = np.array([frame["position"] for frame in walk])
rotations = Slerp(times, Rotation.from_quat([frame["quaternion"] for frame in walk]))

with sync_playwright() as p:
    browser = p.chromium.launch(channel="chrome", headless=False, args=["--use-angle=metal"])
    page = browser.new_page(viewport={"width": 960, "height": 540}, device_scale_factor=1)
    page.goto("http://localhost:8765/")
    page.wait_for_function("document.getElementById('minimap').height > 150", timeout=60000)
    page.evaluate("document.getElementById('sidebar').style.display = 'none'; window.viewer.boxes.visible = false")
    for i, t in enumerate(np.arange(0, times[-1], 1 / FPS)):
        position = [np.interp(t, times, positions[:, k]) for k in range(3)]
        page.evaluate(
            """([p, q, fov]) => {
                const { camera } = window.viewer;
                camera.fov = fov;
                camera.updateProjectionMatrix();
                camera.position.fromArray(p);
                camera.quaternion.fromArray(q);
            }""",
            [position, rotations(t).as_quat().tolist(), FOV],
        )
        page.wait_for_timeout(150 if i else 2000)
        page.screenshot(path=out / f"{i:05d}.jpg", type="jpeg", quality=90)
    browser.close()
print(i + 1, "frames at", FPS, "fps")
