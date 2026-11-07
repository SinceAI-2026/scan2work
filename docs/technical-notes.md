# Scan2Work technical notes

Scan2Work is a local industrial field-service demonstrator. It connects a Gaussian-splat room, framed object proposals, a robot route, synthetic operating records, and a cited service workflow.

For a stage-by-stage overview of the pipeline and how it got here, see [pipeline](pipeline.md); for the viewer features, [features in detail](features.md).

## Inputs and data meaning

The confirmed scene input is `Equipment Room.ply`. The binary PLY contains 1,676,481 Gaussian records in the current artifact. The viewer exports 1,046,910 visible splats after filtering. The source and export counts describe geometry density, not object-recognition accuracy.

The Valmet synthetic builder workbook supplies 30 registered assets, 4,320 rows in its recent telemetry sample, 1,000 events, 300 maintenance records, 50 service cases, eight knowledge documents, and 120 data-quality records. The 88-item retrieval corpus combines the eight knowledge documents, 50 cases, and 30 asset cards.

The mapping from workbook records to scene objects is procedural, as allowed by the brief. Thirty scene objects receive workbook-backed identity and condition records. Other proposals receive separately generated surrounding metadata. The system demonstrates diagnosis of a synthetic operating scenario. It does not establish that the real filmed equipment is faulty.

## Processing and rendering

`pipeline/ingest.py` decodes position, scale, rotation, colour, and opacity. It applies the configured room coordinate transform and floor offset. The current transform and room bounds are configured for this scan.

`pipeline/perception.py` proposes clusters using colour, geometry, DBSCAN, and subdivisions of large clusters. It creates object frames and class guesses. Optional image-based relabelling lives in `pipeline/semantic_labels.py`.

The extraction is a chain of independent stages, each with its own evidence. Every stage can be switched off or falls back without breaking the run.

| Stage | Module | Method | Models and libraries |
| --- | --- | --- | --- |
| Geometric proposals | `perception.py` | Colour and geometry rules, DBSCAN, oriented boxes fitted in each object's own axes (3rd–97th percentile, so floaters do not inflate frames) | NumPy, SciPy, scikit-learn |
| Camera alignment | `walk.py` | The challenge walkthrough video's 334 camera poses (COLMAP, aligned by Linards) mapped into our levelled frame | — |
| Real photo per object | `walk_photos.py` | Projects each object into every video frame and keeps the sharpest frame where its splats pass a z-buffer visibility test | NumPy, Pillow |
| AI labelling | `semantic_labels.py`, `gemini_labels.py` | Each object's real photo (or a splat render) with its 3D box drawn in, batched 45 per request, constrained to a 23-class vocabulary | Gemini 3.8 Flash (fallback: OpenAI Decisions API `gpt-6-luna`) |
| Multi-view detection lifted to 3D | `detect_lift.py` | Open-vocabulary detection on 42 sharp video keyframes. Each 2D box is lifted to the scan's front surface through the known camera, then fused across views. A single-body detection (appliance, cabinet, cart…) merges the clustering fragments inside it into one object with one box | Gemini 3.8 Flash |
| Pipe cylinders | `pipes.py` | Surface-normal voting for pipe axes and radii (port of Linards' `cylinders.py`, after Qiu et al., ECCV 2014), with a continuous-arc test that rejects walls and box faces | NumPy, SciPy |
| Clean-up | `semantic_labels.py` | Drops AI-confirmed clutter and merges touching pieces of the same pipe, tank or cabinet | — |

With `VALMET_SEGMENTER=local`, `pipeline/local_objects.py` replaces the proposals, AI labelling, detection and clean-up stages above with the objects from `local_models/`. These come from SAM 2.1 masks on the posed video frames, stitched into 3D objects by view consensus (MaskClustering), and are named by SigLIP 2, DINOv2, OWLv2 and a linear probe. Each object's box is refitted on the levelled splat with the same box fitter, then pipes, photos, mapping and navigation run as usual. No hosted API is called. The README's "Two pipelines" section compares both.

The prepared release room contains **504 objects**; a fresh rebuild from source gives 467 with the heuristic segmenter and 485 with the local one ([pipeline](pipeline.md#two-segmenters)). AI-verified label sources, movable objects and pipe cylinders are counted in `scene.semantic_labelling`.

### Measured extraction quality

The checks below are in `tests/` and run against hand-labelled ground truth, not against our own output.

- **Label accuracy** on a hand-labelled sample of 39 objects (`tests/gold_labels.json`), with the same objects for every method:

  | Method | Correct |
  | --- | --- |
  | Colour rules | 10% |
  | Decisions API on point crops | 31% |
  | Decisions API on splat renders | 56% |
  | Gemini on splat renders | 85% (33/39) |
  | Gemini on real video photos | **[refresh: `tests/ab_photos.py`]** |

  Movable objects are found in 9/10 cases.
- **2D→3D lifting** (`tests/validate_lifting.py`): every object is projected into the views and the synthetic boxes are lifted back. The fused centre falls inside the original box in 92% of cases.
- **Alignment with reality**: rendering the scan from video poses reproduces the real frames almost pixel for pixel. The camera sits 1.46 m above our floor, which is hand-held phone height.
- **The demo target is verified on video.** The physical object playing CV-003 is a motorised control valve (orange actuator) whose real frame is `web/data/photos/OBJ-206.jpg`. An earlier anchor chosen from splat renders turned out, on video, to be a cardboard box; the real-photo check exists to catch exactly that.

The browser uses locally vendored Three.js, a custom Gaussian-splat shader, and a worker that sorts splats by depth. The map is a simplified drawing of the room and equipment footprints. Navigation uses the collision grid, rather than the map's simplified contours.

High detail uses supersampling at 1.25 times the device pixel ratio, bounded between 2 and 2.5 before limits. Balanced uses a ratio of 1. Both profiles obey an eight-million-pixel budget and the GPU dimension limits. Startup, container resizing, and quality changes use the same policy in `web/render-quality.mjs`. Shader viewport and focal values use the actual drawing buffer. The screen-space splat limit scales with sampling so quality changes preserve apparent size.

This changes sampling of the existing scene. It does not recover missing PLY detail or use the upscaled video. The [render comparison](render-comparison/README.md) records the same-view check and its limits.

## Navigation and collision model

The navigation grid uses 5 cm cells. Obstacles combine selected scan points and equipment footprints. The configuration considers geometry between 0.16 m and 1.62 m above the floor. The robot has a 0.15 m radius. The occupancy grid adds a 0.08 m margin and a cell-size allowance. Floor support comes from observed low-height points with only small scan gaps bridged.

A* minimises distance on the free-cell graph. Diagonal moves cannot cut blocked corners. Collision-checked straight segments remove unnecessary grid turns. This is a shortest-grid-path objective with shortcuts, not a proof of the globally shortest continuous path.

The browser can plan to a map point, find a free inspection stop beside equipment, or join several stops into a room tour. A new route starts at the robot's current position. Swept segment checks validate each movement, including any waypoint turns crossed within a frame.

**Check against a real walk.** The challenge video's camera path is a route a person actually walked. Only 1.8% of its 334 poses fall on cells our map marks as solid, and those are the phone reaching over equipment. So the obstacle map does not invent walls where people walk. About 45% of the human path is inside the robot-safe area. The rest are corridors narrower than a 30 cm robot plus margin, which is why the margin is kept (`tests/scorecard.py`).

These are kinematic, grid-based collision checks. They do not simulate rigid-body forces, gravity, dynamic obstacles, or physical robot control. Their validity depends on the scan, coordinate transform, obstacle extraction, and chosen clearance. Disconnected floor areas remain visible but cannot be reached across a blocked passage.

## Arrival workflow and UI state

`web/journey.mjs` owns the idle, moving, paused, blocked, and arrived phases. The map and HUD read the same journey destination and progress. Inspector selection can change independently during travel. Completing an equipment route selects the destination, opens its existing diagnosis and evidence, reveals inspection steps, and focuses the service heading. Arrival has one effect per journey, so dismissing the report stays dismissed. A zero-distance equipment route opens immediately. Floor routes and tours complete without opening an equipment report.

Arrival reads the generated diagnostic records and does not issue a new inference request. Copilot responses are guarded against replacing a different selected asset or a newer request. Blocked movement preserves the actual robot position and requires a newly planned route.

## Diagnostics and optional AI

`pipeline/knowledge.py` joins asset records, condition labels, telemetry summaries, alarms, maintenance history, cases, and documentation. `pipeline/diagnostics.py` creates observations, hypotheses, inspection steps, and work-order draft data.

EmbeddingGemma provides semantic retrieval when its model is available. The fallback is TF-IDF. The Decisions API can provide image class decisions, diagnostic triage, and copilot intent routing. Cached decisions can support replay. If no suitable response is available, those callers use their fallback logic.

Installation or configuration alone does not prove a model ran. The generated metadata and populated diagnostic records identify the results available to the viewer. **Blind diagnosis evaluation** (`tests/eval_triage.py`). For each of the 13 non-healthy workbook assets, the model is never shown the answer: no condition, health score, issue code, issue summary, or asset card. It ranks root causes among that asset class's historical service cases.

| Evidence given to the model | Top-1 | Top-3 |
| --- | --- | --- |
| Telemetry only | 9/13 (69%, 95% CI 42–87%) | 11/13 |
| Telemetry plus alarms and work orders | 11/13 | 12/13 |
| Baseline: most common issue for the class | 2/13 | — |

Telemetry is compared with the class median using a robust z-score. The remaining telemetry-only misses are faults whose telemetry signature is not distinguishable (for example valve stiction vs. actuator faults on CV-007). In the current fallback logic, `attention_required` assets need review because they can receive a nominal-condition hypothesis.

## Generated artifact status

The artifact inspected on 7 November 2026 reports `decisions_api` for cluster labels and triage, and `embeddinggemma` for retrieval. It records 165 relabelled proposals out of 440 candidates and ten assets with AI triage. These are artifact facts, not an independent accuracy evaluation.

Read the current values before each recording, especially after a concurrent AI rebuild. From the repository root, run this read-only command.

```sh
python3 - <<'PY'
import json
from pathlib import Path

data = json.loads(Path('web/data/digital_twin.json').read_text())
print('Scene:', data['scene']['source_file'])
print('Proposals:', len(data['assets']))
print('Mapped assets:', sum(a['mapped'] for a in data['assets']))
print('AI backends:', data.get('ai_stack', 'not recorded'))
print('Relabelling:', data['scene'].get('semantic_labelling', 'not recorded'))
print('Assets with AI triage:', sum(bool(a['diagnostics'].get('ai_triage')) for a in data['assets']))
print('Floor coverage:', data['navigation'].get('coverage', 'not recorded'))
PY
```

## Credits

Parts of the pipeline adopt teammates' work in the SinceAI-2026 organisation:

- **Linards:** COLMAP alignment of the walkthrough video (`walk.json`) and the cylinder-voting pipe fit, ported in `pipeline/pipes.py`.
- **Rasse:** the briefing transcript and the idea of validating navigation against the real walk.
- **Scan2service:** the idea of choosing sharp frames by variance of the Laplacian.

## Local runtime and components

The pipeline uses Python with NumPy, SciPy, scikit-learn, pandas, openpyxl, Matplotlib, and Pillow. `requirements.txt` lists the core packages. Sentence Transformers is optional for EmbeddingGemma. The HTTP server uses Python's standard library. Browser navigation and UI logic are JavaScript modules.

The server starts with `.venv/bin/python server.py 8765`. It serves the viewer at `http://127.0.0.1:8765/`. The complete rebuild command is `.venv/bin/python -m pipeline.run`. A rebuild can invoke configured optional model services and replace generated artifacts. Preserve the verified recording artifact before a rebuild.

Work-order drafts remain in the server process's memory. They are demonstration records, not submitted maintenance orders or persistent enterprise integration. The current server also differs from the workbook API contract. It uses `/api/copilot/diagnose`, and does not implement the contract's document and scene-anchor endpoints.

## Semantic floor plan and IFC

The **Floor plan** in the viewer draws equipment footprints, fitted pipes, and the room outline from the live twin. Search by tag or name to select an asset, open its inspector, or plan a route. The drawing follows room edits and shows the current robot position and route. Overhead geometry is available as a separate layer. The BIM view, plan, and IFC export use the same fitted vessel bodies. The viewer fits a cylinder to each candidate vessel’s assigned splats and checks its support and residual against held-out splats before using it in place of the envelope.

**Download IFC4** exports the same live room inventory as IFC STEP in metres. The model contains a site, building, storey, room, walls, slab, and equipment with Valmet tags and source metadata. The scan's X, Y, Z coordinates are written as IFC X, −Z, Y. Fitted pipes use circular extrusions and other assets use oriented bounding volumes. Wall height and thickness and slab thickness are assumptions. The model is suitable for room orientation and asset lookup, with envelope geometry that needs field verification for dimensional work. The Python floor-plan generator also produces SVG, PDF, and PNG from the scan on a full rebuild.

## Verification

Run these checks from the repository root.

```sh
.venv/bin/python -m unittest discover -s tests -v
node tests/test_navigation_view.mjs
node tests/test_journey.mjs
node tests/test_render_quality.mjs
```

The Python checks cover blocked routes, equipment clearance, corner cutting, route shortening, and observed floor support. The JavaScript checks cover route timing, nearby assets, rerouting from the current position, room destinations, collision rejection, a complete room tour, and distance preference between safe passages.

The journey suite checks one-time arrival, immediate arrival, pause/resume, blocked recovery, destination changes, and floor/tour completion. On 7 November 2026, all 11 Python checks and both JavaScript suites passed. Navigation regressions include four real destinations at 120 Hz, 30 Hz, 80 ms, and 500 ms frame steps, plus 30 re-dispatch routes and an eight-stop tour.

Browser verification on the same date confirmed that selecting P-001 while travelling to CV-003 leaves the journey destination unchanged. Arrival restores CV-003, opens its evidence and inspection steps, and announces completion. Dismissing the report stays dismissed. Re-dispatching to the already-reached asset opens immediately. A complete browser room tour reaches ARRIVED with the inspector closed and no equipment-arrival announcement. The browser console reported no errors during these checks.

Repeat the browser check after changes. Complete a route to equipment and confirm that the matching diagnosis, evidence, and inspection steps open. Then select another object during a route and confirm that arrival still resolves to the route destination. A floor destination or room tour must not show an unrelated equipment diagnosis.

The challenge submission also requires a runnable demo, source and setup instructions, five to eight slides, and a demo video below five minutes. The [slide outline](slides-outline.md) and [recording sequence](demo-sequence.md) are preparation documents. They are not the final slide deck or recorded video.

## Expanded Field Atlas import (offline)

`pipeline/import_field_atlas.py` is a separate adapter, not another detection pipeline.
It accepts an extracted shared asset package or the original Field Atlas repository:

```sh
VALMET_OFFLINE=1 .venv/bin/python -m pipeline.import_field_atlas \
  --bundle-root /path/to/Field-Atlas-assets-2026-11-07 \
  --workbook /path/to/Valmet_SinceAI_2026_Synthetic_Industrial_Metaverse_Builder_Pack.xlsx
```

The package's `model1/demo/data/scene-extraction/room.ply` is the default scan;
`--source '/path/to/Equipment Room.ply'` supports the original checkout. `--output`
selects a different data directory. Stop the server before reimporting. Generation
happens in a temporary staging directory; validation failures do not overwrite the
active artifacts, and the new `digital_twin.json` manifest is published last.
Library saves, technician logs, edits and the upstream Decisions cache are preserved.

Integrity checks validate the full source PLY SHA256, payload size, declared row
count, raw ownership SHA256, object ordering, owner IDs, disjoint row indices and
per-object counts. A rigid transform maps source `[x,y,z]` to `[x,1.185-y,-z]`.
It changes neither scale nor object membership. The physical scale is unverified.

The imported scene contains 1,933 unreviewed regions, with exactly 711,568 owned
source Gaussians. All 415 baseline regions and their 103,378 rows remain. Regions
are proposals/partial surfaces, not a claim that 1,933 physical objects were
recognized correctly. 964,913 source rows remain unassigned. Recognition accuracy
is not measured by these coverage counts, and upstream gold labels/scorecards
must not be reused for these new region IDs.

`boiler_room_points.bin` (GSPL v3, stride40) and `splat_instance.bin` (GSID v1)
share exactly the same row ordering; GSID values are zero-based asset indices,
with 65535 denoting unassigned context. `splat_source_rows.npy` maps every rendered
row back to the original scan. The existing Wael renderer uses original anisotropic
covariance and DC colour; it does not render higher-order SH. `source_scan.ply`
retains every original attribute. `source_members.npz` preserves exact region rows.
The library export rechecks source SHA256, all source positions and every region's
membership before exporting native PLY, GSPL, a thumbnail and original row IDs.
No box filling, floor deletion, geometric expansion, training, or synthetic mesh is
performed on imported regions.

Only 30 procedural equipment bindings receive the provided Excel telemetry,
alarms and maintenance records. Other regions have unique `REGION-...` IDs,
`mapped=false`, unknown condition, no health score and empty telemetry. Their
copilot response states that service data is missing. Native video evidence is
copied where present; automatic regions retain rendered proposal evidence, which
is labelled separately. These photos are identification evidence, not calibrated
photogrammetric reconstruction.

The CV-003 demo preserves Wael's physical anchor: `linards-BOX-0173`, the orange
actuator visible in its native walkthrough crop. Its imported upstream region
boundary is still unreviewed. The prior Field Atlas CV-003 binding to
`linards-BOX-0262` is retained in source provenance and explicitly overridden for
this demonstration; BOX-0262 receives no invented replacement telemetry.

Navigation uses 37,687 observed floor samples and 188,536 obstacle samples from
the original scan. Region boxes are not treated as solid objects. Unknown floor
is blocked; robot clearance and lengths are simulation assumptions in source
units. All generated segments are collision-checked against this occupancy grid,
which does not prove real-world safety or completeness of the scan. The supplied
import produces 28 available routes, including the CV-003 target (4.619 source
units), and two unavailable destinations (CV-011, P-006) with explicit reasons.
The viewer calculates proximity across all assets live; saved routes carry a
compact 16-item summary per waypoint with the full nearby count preserved.

Both the importer and the default server disable external inference explicitly.
`VALMET_OFFLINE=1` bypasses keychain/secret loading, model downloads and Decisions
API calls, even if the shell already contains an API key. Copilot text is a
deterministic explanation of synthetic Excel records, with TF-IDF document
retrieval. Confidence values inherited from Excel are synthetic dataset values,
not newly measured recognition accuracy. Legacy training/AI pipeline entry points
remain separate; the offline import never invokes them.

Verification:

```sh
VALMET_OFFLINE=1 .venv/bin/python -m unittest discover -s tests -p 'test_*.py' -v
node tests/test_navigation_view.mjs
```

The new tests cover swapped owner IDs, reordered manifests, overlapping/foreign
rows, wrong source hashes, coordinate transforms, offline credentials, safe
unmapped diagnostics, failed-import isolation, all real ownership counts, exact
native PLY export bytes and route traversal against the saved occupancy grid.
The two legacy OBJ-206/refinement tests are skipped without their legacy scan
layout; the imported target has a separate exact-export integration test.

## Recovery of plan and BIM context after the expanded import

**Update:** The original archive was subsequently supplied. The actual object, source-row and runtime-floor comparison is in [Original data audit](original-data-audit.md). The missing-artifact statements below describe the earlier recovery stage, not current availability.

The first import preserved the scan and the new exact region memberships, but it
**did omit useful derived layers from Wael's pipeline**. More extraction regions
did not automatically make a richer BIM model. In particular, the initial import
had zero wall guides, zero fitted pipe cylinders, no oriented geometry proxies,
and no retained catalog-name normalization. 383 of the 415 baseline regions had
usable names but generic `unclassified` categories, so class-based plan filters
hid them. It also displayed a sparse monochrome plan raster.

At the time of this recovery, the original Wael generated `digital_twin.json`
was absent from the clone and locally searched directories. Its published 504
objects and 24 classes could not establish an exact object-by-object overlap.
The 415 preserved baseline regions refer to **Linards**, not Wael's 504 objects.
The user subsequently supplied the original archive; its actual labels, merges,
photos, fitted assets and exact source-row relationships are now documented in
[Original data audit](original-data-audit.md). They were audited read-only and
were not automatically merged into this recovery.

`pipeline/derived_context.py` now restores independently derived display context
using the existing Wael geometry methods, without changing the exact extraction:

| Layer | First expanded import | Context recovery |
| --- | ---: | ---: |
| Exact imported regions | 1,933 | 1,933, unchanged |
| Owned source Gaussian rows | 711,568 | 711,568, unchanged |
| Retained catalog names normalized into viewer classes | 0 | 415, still unverified |
| Regions with generic `unclassified` category | 1,897 | 1,514 |
| Configured Wael wall guides | 0 | 4, explicitly assumed |
| Fitted pipe geometry context | 0 | 120 unreviewed cylinder hypotheses |
| Approximate oriented display envelopes | 0 | 1,933 (427 with nonzero yaw) |
| Observed low-surface context | Not exported separately | 154,288 samples / 13,628 cells |
| Available navigation routes | 28 | 28, unchanged |

The auxiliary context is available in `scene.derived_context` and the standalone
`web/data/derived_context.json`; the audit is in `context_recovery_report.json`.
Each region gets a `geometry_proxy` separate from its canonical center, size,
yaw, source IDs and exact members. Pipe geometry is a separate layer, not 120 new
segmented objects. It uses `pipes.fit_segments` (normal/radius voting and a
continuous surface-arc check), never `pipes.integrate`, which would change source
ownership. Each cylinder records support, arc coverage and related existing
region IDs. Labels are normalized from existing Linards names only; no AI model
was rerun and the 1,514 unclassified regions remain unclassified.

The four wall lines reproduce the original configured Wael guides and explicitly
carry `assumption=true` and `measured=false`. They are not detected wall geometry.
Observed low-surface cells use a broader display gate than navigation and do not
fill unknown holes. The raster now includes native scan colours, thin obstacles
and equipment context, with at most 1.5-pixel display interpolation. These display
layers never open robot routes or assert floor safety. Navigation JSON SHA256 is
identical before and after context recovery:
`2976d8edcc4879401c607ef4e0d288c43e6269fe9f1e662261409db763d61443`.

The full original PLY SHA256 remains
`83d8c7d99316c08c9719cfc2c5a6b9d1903b89847780e2458adc662c79f32955`.
GSPL, GSID and source membership files are not rewritten by context recovery.
The import now runs this recovery automatically. To apply it to an existing
import without rebuilding extraction or navigation:

```sh
VALMET_OFFLINE=1 .venv/bin/python -m pipeline.derived_context
VALMET_OFFLINE=1 .venv/bin/python -m unittest discover -s tests -p test_derived_context.py -v
```

All context remains in unverified source units. These improvements restore useful
plan/BIM display layers; they do not establish survey accuracy, complete object
recognition, complete pipes, or a verified construction BIM.

The recovered IFC export includes all 1,933 region entities, 120 separately
identified unreviewed pipe-fit proxies, four assumed wall guides and an assumed
slab. Pipe context is exported as `IFCBUILDINGELEMENTPROXY`, without Gaussian
ownership claims. The observed-floor mask is recorded through its source sample
count, resolution and supported-cell count; it is not exported as a measured
floor mesh. The generated IFC4 file parsed with IfcOpenShell and returned zero
schema validation errors. Schema validity does not validate geometry, scale,
wall placement or the catalog labels. Moved assets use their placed canonical
envelopes so an original scan proxy cannot remain at a stale location; independent
scan-context pipe fits do not follow an arbitrary moved region.

## Original baseline with an optional proposal review layer

The active demonstration can now use the supplied Wael archive as its canonical
baseline. `pipeline/build_baseline_demo.py` stages a **new directory** and refuses
to replace an existing directory, input tree or parent of either input. It never
activates the staged data automatically. Every original field of all 504 objects,
their 30 canonical bindings, 471 photo references, 83 attached cylinders and the
complete stored navigation object is checked for equality. Original GSPL and GSID
files are copied byte-for-byte, preserving the original 1,046,910-row rendering.
The four archived asset-library entries remain available with their original
refinement provenance.

The staged dataset also contains the full source PLY, an audited original source-row
map and a compact exact ownership file for the 598,437 rows assigned by the archived
GSID. Original detector `n_gaussians` values are preserved. A separate
`exact_owned_gaussians` field controls new native PLY exports: CV-003 / OBJ-206 retains
its original detector count of 346, while native export contains its actual 1,231
GSID-owned source rows. No additional box filling, floor deletion or refinement runs.
A library containing both archived refinements and new exact exports explicitly
reports mixed provenance, with individual entry metadata authoritative.

The 1,933 expanded regions are kept **outside** the canonical asset list:

- `proposal-review/catalog.json` contains their original IDs, geometry, unreviewed
  labels, evidence, full/visible member counts and source-row links to original
  objects. It does not assign synthetic telemetry or duplicate canonical device IDs.
- `proposal-review/projected_instance.bin` uses the same GSID format and row ordering
  as the unchanged baseline renderer, but indexes the separate proposal catalog.
- 627,926 proposal-owned rows are visible in the original renderer; 83,642 additional
  owned rows remain available in the complete extraction review at port 8768.
- `proposal-review/full_source_members.npz` preserves all 711,568 proposal members.
  All 3,716 referenced proposal evidence files are copied locally for review.
- `digital_twin.json.proposal_review` advertises the catalog, projected ownership,
  full-view URL and counts. It defaults to hidden. Neither proposal visibility nor
  selection changes original identities, equipment bindings or navigation.

The original baseline has `scene.dataset_kind="wael-original-baseline"` and a
manifest-derived dataset ID. Its assets deliberately do not have `source_region_id`,
so original semantic rendering and walkthrough-passage behavior remain available.
Exact ownership is a separate capability (`scene.membership_mode="exact-source-rows"`)
and must not be confused with the expanded-import visual mode.

### Explicit data-directory selection

`VALMET_DATA_DIR` selects the served data **and** all dataset writes. It governs
`/data/...` static files, the API manifest, retrieval index, technician log, scene
edits and asset library. UI/code still come from the current checkout. This keeps
baseline operations separate from the complete extraction-review application's
files and browser origin. Running `server.py` without this variable still serves
this checkout's `web/data` directory; it does not silently switch datasets.

Current baseline launch:

```sh
VALMET_OFFLINE=1 \
VALMET_DATA_DIR=/Users/perttu/Downloads/SinceAI-2026/Wael-baseline-stage-2026-11-07 \
.venv/bin/python server.py 8766
```

The complete extraction application is preserved independently under
`/Users/perttu/Downloads/SinceAI-2026/Wael-extraction-review` at port 8768.

Baseline tests use the selected staged directory and assert unchanged original
fields, images, cylinders, bindings, navigation and renderer hashes; exact proposal
projection; byte-exact native target export; isolated static routing; and upstream's
asynchronous library-save HTTP 202 contract. Imported-region and derived-context
artifact checks remain strict but run only against their applicable dataset. They
can be run independently against the preserved extraction data:

```sh
VALMET_OFFLINE=1 \
VALMET_DATA_DIR=/Users/perttu/Downloads/SinceAI-2026/Wael-extraction-review/web/data \
.venv/bin/python -m unittest discover -s tests -p test_field_atlas_import.py -v
```

### Browser verification of the restored baseline

The baseline was activated on port 8766 using the staged data directory; the
checkout's expanded `web/data` and the complete review on port 8768 remain intact.
Browser verification confirmed:

- Navigation reached **ARRIVED** at CV-003 / OBJ-206 and displayed grounded service
  guidance.
- Saving the target continued in the background after its card was closed. The
  HTTP 202 job produced a native PLY containing exactly 1,231 owned source rows.
- Removing and restoring OBJ-004 returned the scene to zero removed objects.
- Selecting the 171-row proposal linked it to OBJ-206 through 170 shared rows;
  proposal review did not change the canonical catalog or equipment bindings.
- The actual browser IFC export contained 504 assets, 87 cylinder geometries
  (83 archived cylinders plus four legacy runtime vessel fits), and four walls.
  IfcOpenShell reported zero schema validation errors. This checks IFC structure,
  not measured scale, segmentation correctness or construction readiness.

### Compatibility with the upstream no-borrowed-profile policy

Upstream commit `ef96298` stops assigning healthy workbook profiles to unmapped
fixed objects in new builds. The supplied original archive still contains 321
such historical profiles. Its files and fields remain unchanged for provenance
and reproducibility; they are not treated as assigned device records. The viewer
gates operational presentation on `mapped`, and `pipeline/asset_records.py` applies
the same rule to API asset lists, details, telemetry, events, maintenance,
diagnostics and route-proximity records. The route's geometry is unchanged.
Unmapped records expose their object ID, scan geometry and evidence,
with no health score, borrowed readings or service history. An explicit provenance
field identifies any archived profile excluded from the response.

Unmapped copilot requests return a no-assigned-record explanation without model
routing or quoting archived diagnoses. Work orders and the technician queue remain
limited to mapped records. This compatibility layer does not rewrite the archive,
rebind devices, regenerate extraction or change the original 30 workbook records.

### Interactive feature parity checklist

A source review compared upstream `359530b` with merged `d0eae2b`. The upstream
main-view action IDs and functions remain present. This is a code inventory, not
a claim of full behavioral equivalence; the following interactions should be
checked in the browser after changes to the toolbar or panel layout.

| User action | Retained entry point / expected check |
| --- | --- |
| Browse freely | Aerial orbit, pan and zoom; First person and Follow robot; WASD/arrow movement and drag-to-look. |
| Choose a destination | Tour the room, Inspect faulty device, Navigate here, and clicking reachable map floor. |
| Control the mission | Start/resume/pause, arrival report, visible route, manual takeover and M to resume the mission. |
| Demonstrate replanning | Drop obstacle during an eligible route; retain the detour or blocked-route explanation and old-route trace. |
| Inspect equipment | Search/filter, queue selection, evidence photos, source geometry, View location and equipment details. |
| Change scene presentation | Scan, Coloured and BIM layers; nearby/all/off frames; walls, pipes, equipment, ceiling, render quality and explode controls. |
| Isolate and return | Isolate region / T, orbit, turntable / R, ghost room / G, and Esc back to the previous scene view. |
| Edit the room | Move, placement preview, cancel/place/reset, remove/restore, and the Room changes list; the designated target remains protected. |
| Inspect video evidence | AR walkthrough playback, seeking and target excerpt, using the locally preserved overlay clip. |
| Inspect/export BIM | Search/select on the 2D plan, navigate or view equipment, open the 3D model, and download IFC4. |
| Reuse an object | Background Save to library, save status after closing the card, library search/class filters, supported file downloads, ZIP, and return to the room. |
| Complete service work | Mapped-device telemetry and grounded guidance; draft work order; start checklist, safety-first checks, notes and completion. |
| Review extra extraction | Separate candidate review, source evidence and original-object overlaps; no implicit changes to canonical objects or bindings. |

Toolbar regression checks include retaining the current camera/layer when merely
selecting a list item, preserving every collapsed control's accessible entry
point, and offering service actions only for a mapped workbook device. An `OBJ-*`
identifier must not be mistaken for a registered device tag. Archived library
profiles also require the same no-borrowed-readings presentation as the main room.

The active baseline deliberately differs from the upstream runtime:

- External voice calls are disabled by its offline runtime flags. The voice
  integration remains in the repository; local inspection jobs remain available.
- New library saves preserve exact source rows, native PLY and scan GSPL. They do
  **not** currently generate a photo-refined model PLY or GLB mesh. This is an
  active capability difference, not an optional refinement button in the current
  UI. Four preserved archived library entries still provide their existing model
  or enhanced PLY/GSPL and GLB files; the viewer retains support for those files.
- Legacy photo refinement remains as local CPU PyTorch code, not an external AI
  service. It currently needs a missing `torch` dependency and dataset-aware
  source/frame paths before use with the staged baseline. The active dataset has
  334 matching frames and the original source PLY; its exact assets must remain
  separate from any future derived preview or reconstruction.
- Automatic idle demonstration is opt-in with `?attract=1`; normal browsing does
  not start an unsolicited mission.
- Exact-membership room edits retain the initial conservative floor grid. Moved
  objects can block additional space; removing an object does not automatically
  certify the floor beneath it or open a new shortcut. Dynamic obstacle replanning
  remains available independently.

There is no general add-new-instance action in the upstream main-room interface;
move, remove and restore operate on its existing objects. Keeping those actions
does not establish a new object-instantiation capability.

### Usability verification on the isolated baseline copy

Browser QA on port 8769 confirmed the compact toolbar has visible labels and the
Display panel starts closed; Esc closes it and returns focus to Display. Searching
for unmapped OBJ-004 retained the selected BIM view, and Show all exposed all 504
objects. Its move to X=0, Z=0, base height=0 and reset both worked; remove/restore
ended with zero removed objects. Isolation showed its 977 native Gaussians and a
background library save completed with exports.

The CV-003 route arrived after 5.5 source units; the explicit Inspect faulty device
action cleared the search and selected Follow robot. Aerial drag-to-orbit and a
map-click destination producing a new 4.4-unit route worked. The AR video loaded
at 1280×720 with `readyState=4`. The plan exposed 504 objects, 87 cylinder geometries
and four walls, and an actual IFC download completed. This round did not repeat
full IFC schema validation. No browser errors were observed; 21 viewer tests
passed and both patch scripts reported zero edits. The main dataset was unchanged.
These are observed interaction checks, not a user study or proof of complete
capability parity.

### Rasmus-derived scene context (2026-11-07)

`pipeline.scene_context.build_scene_context(twin)` supplies read-only
`GET /api/scene-context` from the active in-memory twin. It does not load Excel,
call models, generate assets, change navigation, or write any dataset. Its energy
class rules are adapted from `SinceAI-2026/rasse` commit `5a3ca02`; its geometry,
statuses, bindings and readings come exclusively from the active Scan2Work twin.

The baseline projection contains 504 objects, 30 mapped device summaries and
27 mapped devices with potential-energy annotations; 474 objects have no device
status. The extraction projection uses its own 1,933 IDs and bindings: 30 mapped,
1,903 unmapped, and 27 hazard annotations. No generated Rasse device profiles or
positions are imported. A relevant signal is compared with its own setpoint or
stored three-day mean; that mean is not a healthy baseline or a safe limit.

The response preserves actual archived observation IDs when present and supplies
explicit telemetry sheet/asset/field/time references otherwise. Current-state
row references remain separate from telemetry timestamps. Existing data-quality
issues are exposed. Non-finite readings become unknown; zero current does not
prove electrical isolation; hot bearings remain annotated. Visual radii use
unverified scene units. No inferred isolation points or safe-work decisions are
emitted. Existing in-memory Arduino scripted faults are explicitly marked
`runtime_scenario`, without incorrectly citing the recipient's original workbook
record. The pure projection is recomputed on request, so it does not conceal
those state changes behind a cache.

Validation: `python -m unittest tests.test_scene_context tests.test_public_demo`
passed 33 tests. Read-only checks against both prepared datasets confirmed all
30 mapped IDs, statuses, centers and asset bindings are preserved and the
response serializes with strict finite JSON. Browser QA uses an isolated local
data copy; original assets and shared production jobs are unchanged.
