# AGENTS.md: working on Scan2Work

Instructions for coding agents (and humans) working in this repository. Read this before editing.

## What this is

A Valmet × Since AI 2026 challenge entry. It covers:
- semantic 3D extraction from a Gaussian-splat scan (`../Equipment Room.ply`);
- binding of the Excel builder pack to the scene;
- collision-free robot navigation to a faulty device;
- a grounded AI diagnosis.

The pipeline writes `web/data/digital_twin.json`, which the web viewer and `server.py` read. Scoring weights are extraction 30%, navigation and proximity 25%, AI diagnosis 20%, backend 15%, demo 10%. Every change should serve one of these.

## Ownership (two agents work in parallel)

| Area | Owner | Others may |
| --- | --- | --- |
| `pipeline/`, `server.py`, `tests/`, `scripts/` | pipeline agent | read only |
| `web/` (viewer, `*.mjs`, `index.html`, `styles.css`) | viewer agent | read only |
| `docs/technical-notes.md`, `docs/demo-sequence.md`, `docs/slides-outline.md` | pipeline agent | suggest changes |
| `docs/screenshots/`, `docs/render-comparison/` | viewer agent | read only |

`scripts/patch_proximity_frames.py` and `scripts/patch_explode_view.py` patch `web/app.js` idempotently via anchor lines. After editing `app.js`, run both and confirm they print `0 edit(s)` with no "anchor not found".

Only the user runs `pipeline.run` / `scripts/check.sh`. They call paid AI APIs and rewrite `web/data/`.

## Commands (run from this directory)

```sh
bash scripts/check.sh                          # rebuild the twin + requirements scorecard (must end with 0 hard failures)
.venv/bin/python -m tests.scorecard            # scorecard only
.venv/bin/python -m tests.ab_photos            # label-image A/B on the hand-labelled sample
.venv/bin/python -m tests.eval_triage          # blind AI-diagnosis evaluation
.venv/bin/python -m tests.validate_lifting     # 2D->3D lifting geometry check
.venv/bin/python -m scripts.render_overlay     # AR clip: twin over the real walkthrough video
.venv/bin/python server.py 8765                # viewer at http://127.0.0.1:8765/ (restart after a rebuild)
```

Environment knobs: `VALMET_LABEL_IMAGES` (`collage` by default; also `render`, `photo`, `both`), `VALMET_DETECT_TILES` (1), `VALMET_GEMINI_WORKERS` (4), `VALMET_GEMINI_BATCH`. Keys are read by `pipeline/vault.py` from the environment, the macOS Keychain or `secrets.env`. **Never print, commit or copy `secrets.env`.**

## Data contract: fields the viewer reads (`assets[]` in `digital_twin.json`)

| Field | Meaning |
| --- | --- |
| `id`, `center`, `size`, `yaw`, `aabb_min/max` | oriented 3D box in the levelled frame (y up, floor at 0, metres) |
| `class`, `label`, `class_label` | internal class; display name (may be specific, e.g. "Washing machine"); category |
| `label_source` | `gemini`, `gemini_detection`, `cylinder_fit`, `decisions_api` or `geometric_rules` |
| `movable` | true for carts, chairs, crates, ladders, cleaning equipment… (violet, no telemetry) |
| `condition_state` | `healthy`, `watch`, `degraded`, `attention_required`, `movable` or `not_instrumented` (grey facility items) |
| `mapped`, `is_target`, `tag`, `asset_id` | Excel binding; exactly one target (CV-003 = OBJ-206) |
| `scene_class` | what the AI says a mapped object is, before the Excel class override |
| `photo`, `photo_source.video_t_s` | best real walkthrough frame (`data/photos/<id>.jpg`) and its video time |
| `cylinder` | `{start, end, radius}` for fitted pipes: draw as a tube |
| `detection_views`, `merged_fragments` | evidence: real frames that detected it; fragments merged into it |
| `telemetry`, `alarms`, `maintenance_history`, `diagnostics` | from the Excel pack; `diagnostics.ai_triage` cites evidence IDs |

`navigation` holds the grid, `primary_route.waypoints` (with `nearby_assets` per waypoint) and `routes_by_asset`.

## Rules that keep the demo honest

- Every status, number and citation shown must come from the Excel pack. The scorecard checks this.
- Do not claim accuracy without a test. Accuracy numbers come from `tests/` output only.
- The target is anchored by `C.TARGET_ANCHOR` / `C.TARGET_ZONE` in `pipeline/config.py`. It was verified on a real video frame. Do not move it without re-checking `web/data/photos/<id>.jpg`.
- AI answers are cached in `web/data/decisions_cache.json` (keyed by object geometry). Re-runs are free; deleting the cache costs API calls.
