# Field Atlas

Field Atlas is a local industrial inspection demonstrator built from `Equipment Room.ply` and the Valmet synthetic builder workbook. It shows framed equipment proposals, collision-aware robot navigation, operating evidence, and a service report for the destination asset.

The workbook supplies a synthetic fault scenario. The demonstration does not establish that the real filmed equipment is faulty.

## Run the viewer

From the `valmet-twin` directory, use the existing environment and generated data.

```sh
.venv/bin/python server.py 8765
```

Open [the local viewer](http://127.0.0.1:8765/). Wait for the scene and map to load, then select **Inspect faulty device** or **Tour the room**. You can also click clear floor on the map or select equipment and choose **Navigate here**.

A successful equipment arrival opens the destination's service report, evidence, and inspection steps. Floor destinations and room tours complete without opening an unrelated equipment report. Selecting equipment during a journey changes the inspector, not the journey destination.

If you need a new Python environment, create it and install the core dependencies first.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

Three.js is included locally. Optional AI services have separate setup and model requirements. After a generated-data rebuild, restart the server and reload the browser so both use the same artifact.

## Verify changes

```sh
.venv/bin/python -m unittest discover -s tests -v
node tests/test_navigation_view.mjs
node tests/test_journey.mjs
node tests/test_render_quality.mjs
```

The checks cover blocked paths, corner contact, movement continuity, re-dispatch, journey transitions, and map coordinate alignment. They validate against the derived collision map. They do not prove complete physical obstacle coverage or object-recognition accuracy.

Use the browser to verify that arrival reveals the correct report and that closing it does not make it reopen on the next frame.

## Check the results

```sh
.venv/bin/python -m tests.scorecard          # every requirement of the brief, with measured numbers
.venv/bin/python -m tests.eval_triage        # blind AI-diagnosis evaluation (answers hidden)
.venv/bin/python -m tests.ab_photos          # label accuracy: real video photos vs splat renders
.venv/bin/python -m scripts.render_overlay   # AR clip: the twin drawn over the real walkthrough video
```

## Prepare the submission

- [Demo sequence](docs/demo-sequence.md) gives a recording plan below five minutes.
- [Seven-slide outline](docs/slides-outline.md) covers the extraction, navigation, metadata, and service workflow.
- [Technical notes](docs/technical-notes.md) describe the components, generated model status, verification commands, and current limits.

The slide outline and recording sequence are preparation documents. A final deck and a recorded demo still need to be produced.

## Rebuild scene data

Generated data is not in git. A fresh clone builds it in about 5 minutes. The cached AI answers in `web/data/decisions_cache.json` make the rebuild reproducible and free of API calls.

1. Place the challenge files next to this repository (paths in `pipeline/config.py`):

   ```text
   ../Equipment Room.ply                                    (the valid scan, 1,676,481 Gaussians)
   ../Valmet_SinceAI2026 _1_/Industrial Boiler Room (1).mp4 (walkthrough video)
   ../Valmet_SinceAI2026 _1_/Valmet_SinceAI_2026_..._Builder_Pack (1) (2) (1).xlsx
   ```

2. Extract the walkthrough frames. Their camera poses are in `web/data/walk_poses.json` (COLMAP alignment by Linards).

   ```sh
   mkdir -p web/data/walk_frames
   ffmpeg -i "../Valmet_SinceAI2026 _1_/Industrial Boiler Room (1).mp4" -vf "fps=3,scale=1280:-2" -q:v 3 web/data/walk_frames/f_%04d.jpg
   ```

3. Install and build. API keys are optional when the cache is present. If you have them, put them in `secrets.env`, which is git-ignored and never committed: `OPENAI_API_KEY`, `GEMINI_API_KEY`, `HF_TOKEN`.

   ```sh
   python3 -m venv .venv && .venv/bin/python -m pip install -r requirements.txt
   bash scripts/check.sh                       # build + scorecard (expect 0 hard failures)
   .venv/bin/python -m scripts.render_overlay  # optional AR clip
   ```

   The splat-render crops used for labelling come from `python -m scripts.render_crops` (needs Node + Playwright). Without them the cached labels still apply.

A rebuild replaces generated viewer data and can invoke configured AI services. Coordinate it with detection and AI work. The viewer can display cached results, while model callers retain their documented fallbacks.

Check generated metadata before claiming that a model ran. The inspection command and the distinction between model results and accuracy are in [Technical notes](docs/technical-notes.md#generated-artifact-status).
