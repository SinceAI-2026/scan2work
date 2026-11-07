#!/bin/bash
# Re-apply UI patches (safe if already applied), rebuild the twin, and score it against the brief.
# AI answers are cached after the first run, so re-runs are fast and free.
cd "$(dirname "$0")/.."
./.venv/bin/python scripts/patch_proximity_frames.py
./.venv/bin/python scripts/patch_explode_view.py
./.venv/bin/python -u -m pipeline.run | grep --line-buffered -E "gemini|labels\]|decisions|cleanup|retrieval|photos|detect|pipes|target|Done|Extracted|path"
./.venv/bin/python -m tests.scorecard
