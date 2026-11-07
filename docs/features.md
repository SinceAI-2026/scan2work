# Features in detail

The README lists what Scan2Work does; this page has the detail for the larger features.

## Room controls

- **Camera** (round button, top left): **Follow robot**, **First person** (the robot's camera: drag to look,
  WASD/arrows to walk) and **Aerial view**. The robot is driven the same way in every view; the view only moves
  the camera.
- **Scan / Coloured / BIM**: the same room and camera with a different mask: the original splat, objects tinted by
  class, or BIM geometry.
- **Aerial tools** (left panel, Aerial view only): **Explode** slider, **Show in room** filters (walls, pipes and
  overhead objects, all equipment, relevant equipment) and the class legend.
- **Floor plan** opens the semantic plan and the IFC export. **Display** holds render quality, frame visibility
  (Nearby / All / Off), synthetic-fill tint and the ceiling cut.
- **Arduino panel** (chip icon): the fault panel's live state, see [arduino/README.md](../arduino/README.md).
- **Find equipment** and **To inspect** in the left sidebar. Selecting an item keeps the camera; **View location**,
  **Navigate here** and **Isolate item** are explicit actions. **Inspect faulty device** and **Tour the room** start
  the robot.

## Isolate an object and save it to the library

Select equipment and choose **Isolate item** in Equipment details, or press **T**. The object stays in place and the rest of the room fades. **R** toggles the turntable, **G** shows a faint ghost of the room, and **Esc** returns.

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

Select a pump or motor that stands inside one of the five split units and choose **Take apart** in Equipment details (or press **T**). For these objects the button takes the unit apart in the room. For every other object it keeps the take-apart described above.

| Unit (scan2service) | This viewer's objects inside it |
| --- | --- |
| Inline pump 2, `B019p+A069_r2` | M-005 and the pump casing below it (OBJ-034) |
| Inline pump 1, `B013+B013m` | M-004 |
| Red circulator, `B156+B156m_r2` | M-006 |
| Grundfos circulator, `B149+B149m` | P-001 |
| Red circulator, `A101+G071` | P-005 and OBJ-434 |

Away from the pump, the robot walks there first. The view glides into first person at the robot and turns to the unit, and the unit's parts ease apart where it stands. The room stays in view behind them, dimmed and blurred (3 px), and the objects' own scan splats are hidden under the model. Other frames, labels and the navigation panels are hidden until **Done** or **Esc**.

- Each part has a callout with its name and an unverified model size in mm. Only objects with a current workbook binding show a device tag and synthetic fault; unmatched regions show their scene label without borrowed telemetry.
- The dock on the right has **Explode**, **Reassemble**, **Reset** and **Done**, a slider, the fault list, a button per part and the selected part's detail.
- Click a part (or its callout or button) to select it. The eye eases about 30 % toward it.
- The fault part is tinted and outlined in red. Another object's fault in the same unit is shown in amber.

The models are AI-generated (Higgsfield) units, split into parts by geometry rules. scan2service fitted them in raw PLY coordinates of the same `Equipment Room.ply`. The viewer only levels them, `(x, 1.185 − y, −z)` as in `pipeline/ingest.py`, so each unit stands exactly where it was fitted. Objects are matched to a unit by **position**: an object belongs to the unit when its box centre lies inside the unit's box. Tags are never used for this, because the two repos tagged the Excel devices onto the scan independently. The faults come from the matched objects' own Excel records, on the part whose fault codes hold the issue, preferring the part the object stands in. For example, M-005's current imbalance is shown on the motor and P-005's fouling on the pump housing. An issue that no part of the model carries is still listed in the dock, if a current workbook binding has an issue that the split geometry cannot represent.

## AI 3D models in the room

Every Higgsfield unit that scan2service shows in its room is drawn where it was fitted, unshifted: 15 models from its own index (`equip_v1/assets_export/hf3d/index.json`), including the big vessel, cabinet and trolley, plus 3 newer ones from the MVP's `index_new.json`: the Grundfos twin pump unit (on P-002 here) and the valves A257 (on CV-007) and A352 (on M-002). The `_r2` redo is used where there is one. Units that scan2service hides, or whose silhouette fit against the photos is below 0.5, are skipped, and the import lists them with the reason. Each model sits on this viewer's objects whose box centres lie inside it. A small model with none sits on the smallest box that holds its centre. A model with no object of this viewer under it is skipped, so the scan never shows through a model (B165 and B169 here; `window.__ai3d.skipped()` lists them). The room models carry 1024 px copies of their textures, written by the import, while the split units for Take apart keep the full 2048 px. That is about 70 MB of video memory for the 13 room models instead of about 290 MB. Their scan splats are hidden under the model, and their room labels get an **AI 3D** badge. A pump and motor unit is one model, drawn once. The models load one by one after the splat. They follow the scene explode slider. Removing, rotating or moving any associated object falls back to the source scan, so the illustrative assembly cannot hide or resurrect edited objects. They are hidden in the BIM layer and while an object is taken apart with Wael's take-apart. `?ai3d=0` turns them off. The code is in `web/ai3d.mjs`.

The assets ship in the repo under `web/data/take_apart/`, so no import step is needed: `index.json`, the split units in `parts/` (2048 px textures) and the room models in `ai3d/` (1024 px copies), about 44 MB in 24 files. The rest of `web/data/` stays git-ignored. `scripts/import_take_apart.py` only regenerates them, from the MVP's s2s parts index, scan2service's hf3d index and the MVP's `index_new.json`:

```sh
.venv/bin/python -m scripts.import_take_apart    # regenerates web/data/take_apart/{index.json,parts/,ai3d/}
node tests/test_parts_data.mjs                    # matching by position, faults from the objects' own records
```

Take apart is ported from Mikael Lehtonen's scan2service (`web/parts.js`) via SinceAI-2026/mvp, with the UX after Rasmus Mäkinen's pump explode. The code is in `web/room-parts.mjs`, `web/parts-data.mjs`, `web/ai3d.mjs` and `web/room-parts.css`. `web/vendor/three/addons/loaders/GLTFLoader.js` is three.js r170's loader, matching the vendored three.

## Voice assistant (ElevenLabs agent Ilkka)

The [public MCP and widget setup](voice-mcp.md) describes the current
branch’s authenticated endpoint, independent voice opt-in, and the settings
Wael needs to apply in ElevenLabs. The immutable 0.1.1 installer remains offline.

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

ElevenLabs calls MCP servers from its cloud, so the agent needs a public HTTPS tunnel to port 8767. ngrok works; ElevenLabs could not reach a Cloudflare quick tunnel in testing. Requests through a tunnel must carry `Authorization: Bearer <FIELD_ATLAS_MCP_TOKEN>`. Store the token in ElevenLabs as a workspace secret, with no trailing newline. For this voice integration, point the tunnel at `mcp_server.py` only; the normal writable `server.py` routes have no authentication. The separate [public preview mode](public-demo.md) disables shared writes and uses an isolated prepared dataset. Voice is off by default; the [explicit voice configuration](voice-mcp.md) enables the widget separately after its agent and public MCP connection are verified.

## Another dataset

The original `web/data` setup remains the default. The [asset import guide](asset-import-guide.md) describes the modular importer, original-data preservation adapter, separate candidate-review overlay, and `VALMET_DATA_DIR` for serving another dataset. Generated scans and videos are not included in this PR. Set `VALMET_OFFLINE=1` to run without backend model calls; imported datasets also declare their offline runtime policy.

The evaluation results above describe the original generated twin. They are not accuracy results for imported candidates. Exact-source exports preserve native rows; the original dataset retains its photo-refinement workflow. The [compatibility notes](technical-notes.md) distinguish these modes.
