# Field Atlas

Field Atlas is a local industrial inspection demonstrator built from `Equipment Room.ply` and the Valmet synthetic builder workbook. It shows framed equipment proposals, collision-aware robot navigation, operating evidence, and a service report for the destination asset.

The workbook supplies a synthetic fault scenario. The demonstration does not establish that the real filmed equipment is faulty.

## Strengths

- **The scan becomes a labelled twin.** 504 framed objects in 24 classes come out of the Gaussian-splat scan. Every one of the 30 workbook assets is bound to its own 3D object, and the AI labels are checked against a hand-labelled sample.
- **Labels come from real photos, not only renders.** Each object is matched to its sharpest walkthrough video frame, and 96 of 147 devices are independently confirmed by detection in those frames.
- **The robot route is collision-checked.** It keeps a clearance of at least 0.23 m, never crosses an equipment box, and ends 0.57 m from the faulty valve CV-003. The obstacle map agrees with the path a person actually walked in the video.
- **Every number is grounded.** Status, telemetry and citations come from the Excel pack. The scorecard checks all 2,621 cited evidence IDs, and none is unknown.
- **The diagnosis is tested blind.** The model never sees the answer, and it still finds the right root cause from telemetry alone in 9 of 13 faulty assets, against 2 of 13 for the obvious baseline.
- **Objects can be taken apart and kept.** Any object can be shown on its own, cut out of the scan, refined against the video frames and saved to an asset library as `.ply`, 3DGS and `.glb` files.
- **A voice assistant works the job with the technician.** Ilkka, an ElevenLabs voice agent inside the viewer, does these things:
  - shows equipment on screen when asked;
  - diagnoses from live data;
  - walks the technician through the procedure with lockout/tagout enforced;
  - records steps and notes that appear on screen as it writes them;
  - closes jobs, which removes them from the inspection queue.

## Evaluation results

Measured on 7 November 2026 on the generated twin. Each number comes from a command in this repository. The workbook data is synthetic.

| Area | Measure | Result | Command |
| --- | --- | --- | --- |
| Challenge brief | Requirement checks passing | 29/29, 0 hard failures | `tests.scorecard` |
| Extraction | Label accuracy on 39 hand-labelled objects | 85% (33/39); colour-rule baseline 13% | `tests.scorecard` |
| Extraction | Movable objects found in the sample | 10/10 | `tests.scorecard` |
| Extraction | Duplicate boxes (IoU > 0.5) | 5 pairs among 504 objects | `tests.scorecard` |
| Extraction | 2D→3D lifting: fused centre inside the true box | 95% (box recovered at IoU > 0.25: 71%, median IoU 0.34) | `tests.validate_lifting` |
| Mapping | Workbook assets bound to distinct objects | 30/30, with 0 telemetry values mismatched against Excel | `tests.scorecard` |
| Navigation | Path samples off walkable floor | 0/454 | `tests.scorecard` |
| Navigation | Minimum clearance (robot radius 0.15 m) | 0.234 m | `tests.scorecard` |
| Navigation | Video camera poses on cells marked solid | 1.8% of 334 | `tests.scorecard` |
| Navigation | Journey, swept-movement and tour suites | all pass | `node tests/*.mjs` |
| AI diagnosis | Blind root cause, telemetry only (top-1 / top-3) | 9/13 (69%, 95% CI 42–87%) / 11/13 | `tests.eval_triage` |
| AI diagnosis | Blind root cause with alarms and work orders | 11/13 / 12/13 | `tests.eval_triage` |
| AI diagnosis | Baseline: most common issue for the class | 2/13 | `tests.eval_triage` |
| AI grounding | Cited evidence IDs found in the Excel pack | 2,621 of 2,621 | `tests.scorecard` |
| Object refinement | Colour error / SSIM against a held-out photo, 6 objects | 0.162 → 0.123 / 0.340 → 0.399 | `tests.eval_refine` |
| Job log | Safety-first, fixed-only-when-complete and queue rules | 18/18 unit tests pass | `unittest discover -s tests` |
| Voice assistant | Calls `show_equipment` with the right tag when asked "where is it?" | 9/9 runs | ElevenLabs agent tests |
| Voice assistant | Full CV-003 job by voice: steps, notes, close as fixed | 5/5 runs on `gemini-3.5-flash` | ElevenLabs simulation tests |

Limits:
- The navigation checks are kinematic and grid-based. They do not simulate robot dynamics or moving obstacles.
- With alarms included, the diagnosis is close to trivial, because in this synthetic pack alarm descriptions name the issue code. The telemetry-only row is the honest test.
- Refinement gains least on objects the video saw from few angles. Pump P-001 got slightly worse on SSIM (see below).
- The voice-agent runs are ElevenLabs text simulations, not recorded phone calls.

[Technical notes](docs/technical-notes.md) describe the methods behind each number.

## Prerequisites

| Need | Version used | For |
| --- | --- | --- |
| Python | 3.9 or newer (tested on 3.9.6) | pipeline, `server.py`, tests |
| Python packages | `requirements.txt` | the core packages, plus PyTorch (2.8 tested) for **Save to library** refinement |
| Node.js | tested on 24 | the JavaScript test suites; Playwright only for `scripts.render_crops` |
| ffmpeg | tested on 8.0 | extracting walkthrough frames for a rebuild |
| A modern browser with WebGL 2 | — | the viewer (Three.js is bundled, no build step) |
| Challenge files | — | `Equipment Room.ply`, the walkthrough video and the builder workbook, for a rebuild only |
| API keys (optional) | — | `OPENAI_API_KEY`, `GEMINI_API_KEY`, `HF_TOKEN` in `secrets.env`. They are not needed while `web/data/decisions_cache.json` covers the answers. |
| ElevenLabs account and a public HTTPS tunnel (optional) | ngrok 3 tested | the voice assistant |

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

## Run the viewer

From the `valmet-twin` directory, with generated data in `web/data/` (see [Rebuild scene data](#rebuild-scene-data) on a fresh clone):

```sh
.venv/bin/python server.py 8765
```

Open [the local viewer](http://127.0.0.1:8765/). Wait for the scene and map to load, then select **Inspect faulty device** or **Tour the room**. You can also click clear floor on the map or select equipment and choose **Navigate here**.

A successful equipment arrival opens the destination's service report, evidence, and inspection steps. Floor destinations and room tours complete without opening an unrelated equipment report. Selecting equipment during a journey changes the inspector, not the journey destination.

After a generated-data rebuild, restart the server and reload the browser so both use the same artifact.

## Take objects apart and keep them

Select equipment and choose **Take apart** in Equipment details, or press **T**. The object stays in place and the rest of the room fades. **R** toggles the turntable, **G** shows a faint ghost of the room, and **Esc** returns.

**Save to library** cuts the object out of the scan and refines it against the walkthrough frames that see it. This takes 1 to 3 minutes on the CPU and calls no AI model. The separation follows Scan2Service's asset export, on this twin's per-splat object ids. The refinement (`pipeline/object_refine.py`) is 3DGS training restricted to one object, in three steps:

1. It starts from the object's scan splats and the faint splats of the same surface that the viewer drops.
2. It splits them into children of about 5 mm.
3. It fits them to up to 16 cropped frames, with a per-frame camera and exposure correction.

Once saved, the object is drawn as the refined model, and **V** switches back to the scan. Each saved object gets `web/data/library/<id>/`:

- `<id>.ply` holds the object's rows of `Equipment Room.ply` with every property.
- `<id>_model.ply` is the refined model as a standard 3DGS file.
- `<id>.glb` is an alpha-shape mesh, and `thumb.jpg` is a thumbnail.

The [asset library](http://127.0.0.1:8765/library.html) lists the saved objects by class, with each model on a turntable. It offers the downloads, **Remove**, and **Download saved (.zip)**. Its routes are `GET/POST /api/library`, `DELETE /api/library/<id>` and `GET /api/library/export.zip`.

```sh
.venv/bin/python -m pipeline.asset_export OBJ-206            # save the faulty valve CV-003 from the shell
.venv/bin/python -m pipeline.asset_export --remove OBJ-206
.venv/bin/python -m tests.eval_refine                       # scan vs refined, scored on a held-out frame
```

On 6 objects (7 Nov 2026), `tests.eval_refine` held out each object's best frame and its neighbours. Against that photo, mean colour error fell from 0.162 to 0.123, and SSIM rose from 0.340 to 0.399. Objects the walkthrough saw from few angles gain least: pump P-001 (OBJ-008) went from 0.128 to 0.124 error, and its SSIM fell from 0.275 to 0.262.

## Take a pump apart in the room

Select M-005, the motor of the right-hand inline pump on the plinth, and choose **Take apart** in Equipment details (or press **T**). The pump casing below it (OBJ-034) does the same. For these objects the button takes the pump unit apart in the room. For every other object it keeps the take-apart described above.

Away from the pump, the robot walks there first. The view glides into first person at the robot and turns to the unit, and the unit's parts ease apart where it stands. The room stays in view behind them, dimmed and blurred (3 px), and the objects' own scan splats are hidden under the model. Other frames, labels and the navigation panels are hidden until **Done** or **Esc**.

- Each part has a callout with its name and size in mm. It also shows the tag of the object that the part holds, with that object's synthetic fault.
- The dock on the right has **Explode**, **Reassemble**, **Reset** and **Done**, a slider, the fault list, a button per part and the selected part's detail.
- Click a part (or its callout or button) to select it. The eye eases about 30 % toward it.
- The fault part is tinted and outlined in red. Another object's fault in the same unit is shown in amber.

The models are AI-generated (Higgsfield) units, split into parts by geometry rules. scan2service fitted them in raw PLY coordinates of the same `Equipment Room.ply`. The viewer only levels them, `(x, 1.185 − y, −z)` as in `pipeline/ingest.py`, so each unit stands exactly where it was fitted. Objects are matched to a unit by **position**: an object belongs to the unit when its box centre lies inside the unit's box. Tags are never used for this, because the two repos tagged the Excel devices onto the scan independently. The faults come from the matched objects' own Excel records: M-005's current imbalance is shown on the motor, and the casing has no record.

### AI 3D models in the room

Every Higgsfield model of a scan2service device is drawn where it was fitted, unshifted. It sits on this viewer's objects whose box centres lie inside it. Their scan splats are hidden under the model, and their room labels get an **AI 3D** badge. A pump and motor unit is one model, drawn once. The models load one by one after the splat. They follow the explode slider and room moves of their main object, and leave with it. They are hidden in the BIM layer and while an object is taken apart with Wael's take-apart. `?ai3d=0` turns them off. The code is in `web/ai3d.mjs`.

The data comes from the MVP and is git-ignored like the rest of `web/data/`:

```sh
.venv/bin/python -m scripts.import_take_apart    # reads ~/sinceai-mvp/demo/web/public/data/s2s, writes web/data/take_apart/{index.json,parts/,ai3d/}
node tests/test_parts_data.mjs                    # matching by position, faults from the objects' own records
```

Take apart is ported from Mikael Lehtonen's scan2service (`web/parts.js`) via SinceAI-2026/mvp, with the UX after Rasmus Mäkinen's pump explode. The code is in `web/room-parts.mjs`, `web/parts-data.mjs`, `web/ai3d.mjs` and `web/room-parts.css`. `web/vendor/three/addons/loaders/GLTFLoader.js` is three.js r170's loader, matching the vendored three.

## Voice assistant (ElevenLabs agent Ilkka)

Select **Call Ilkka for support** at the bottom of the viewer. Ilkka runs on `gemini-3.5-flash`, answers in short radio-style replies, and is told which equipment is selected in the viewer, so "what's wrong with this one?" needs no tag.

**On screen** (`web/voice-assistant.mjs`, `web/field-jobs.mjs`):
- **Show me where it is.** Ilkka's client tool `show_equipment` selects the equipment and moves the camera to it. It then names the equipment's registered area.
- **Inspection queue.** The left sidebar lists equipment that still needs inspection, worst first, with step progress and note counts. Clicking a row shows that equipment. Jobs closed as fixed move to **Done**.
- **Technician job.** Equipment details shows the checklist, the technician's notes (including the ones Ilkka records), a note field and **Mark done**. The panel follows the same rules as the assistant and refreshes every 3 seconds, so Ilkka's work appears as it happens.

**MCP tools.** `mcp_server.py` gives the agent ten tools (`pipeline/field_service.py`):
- **Reading the plant:** `find_equipment`, `get_equipment_status`, `list_open_problems`, `get_procedure` and `search_service_knowledge` read the twin's Excel-sourced data. Tags work as spoken: CV-003, cv 3 or ASSET-003.
- **The job log:** `start_job`, `complete_check`, `add_note`, `close_job` and `get_job` keep the technician's job in `web/data/field_log.json`. Both servers share it under a file lock.
- **Job rules:**
  - A job's checklist is the asset's verification procedure.
  - The safety step (permit, lockout/tagout) must be confirmed before any step after it.
  - A job closes as fixed only when every step is confirmed, and fixed equipment leaves the inspection queue.

The viewer's routes are `GET /api/field-jobs` (queue, jobs and notes), `POST /api/field-jobs` (`start_job`, `complete_check`, `add_note` or `close_job` from the panel) and `GET /api/field-jobs/find?equipment=cv 3`.

```sh
FIELD_ATLAS_MCP_TOKEN=<secret> .venv/bin/python mcp_server.py 8767   # serves only POST /mcp
ngrok http 8767                                                      # public HTTPS URL for ElevenLabs
.venv/bin/python -m scripts.build_vale_kb                            # the agent's knowledge base, web/data/vale_kb/*.md
```

ElevenLabs calls MCP servers from its cloud, so the agent needs a public HTTPS tunnel to port 8767. ngrok works; ElevenLabs could not reach a Cloudflare quick tunnel in testing. Requests through a tunnel must carry `Authorization: Bearer <FIELD_ATLAS_MCP_TOKEN>`. Store the token in ElevenLabs as a workspace secret, with no trailing newline. Point the tunnel at `mcp_server.py` only, never at `server.py`, whose routes have no authentication.

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
.venv/bin/python -m tests.validate_lifting   # 2D->3D lifting geometry, no AI model
.venv/bin/python -m tests.eval_triage        # blind AI-diagnosis evaluation (answers hidden)
.venv/bin/python -m tests.ab_photos          # label accuracy: real video photos vs splat renders
.venv/bin/python -m tests.eval_refine        # object refinement vs a held-out photo
.venv/bin/python -m scripts.render_overlay   # AR clip: the twin drawn over the real walkthrough video
```

`tests.scorecard`, `tests.validate_lifting` and `tests.eval_refine` call no AI service. `tests.eval_triage` and `tests.ab_photos` read cached answers and call the API only for answers that are missing.

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
