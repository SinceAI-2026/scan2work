# Field Atlas — expanded asset integration

This fork combines Wael’s room viewer, navigation and service workflow with the expanded Field Atlas extraction bundle. It starts from [SinceAI-2026/Wael at ab777d3](https://github.com/SinceAI-2026/Wael/tree/ab777d3c94182780cc3528fe1373e5b79e3fd110).

The imported scene contains **1,933 candidate regions**, including all **415 Linards regions**. Regions retain exact membership in the original Gaussian-splat file. This makes selecting, isolating and exporting an object consistent across the viewer and the asset library.

The 30 equipment records and fault scenarios come from Valmet’s **synthetic** Excel pack. Region boundaries and inherited labels remain unreviewed; more assigned points do not establish better recognition accuracy. The source scan’s physical scale is unverified.

## Get the data

Download and extract `Field-Atlas-assets-2026-11-07.zip` from the [team’s shared asset folder](https://drive.google.com/drive/u/0/folders/14MMXTD37DJAzgkPp4029zU8tqayBF7OP). Keep the extracted directory structure intact. The bundle contains the room scan, exact object ownership, individual object files and available native video evidence. It does not contain the Excel workbook; use the supplied Valmet builder pack.

The same importer can read the original Field Atlas project directory, which contains `model1/demo/data/scene-extraction/scene.json`.

Generated scene data is excluded from Git. The importer creates this fork’s own `web/data/` outputs; the source package stays unchanged.

## Run locally

Python 3.10+ and a modern WebGL 2 browser are sufficient for the imported scene. No CUDA, AI credentials or model downloads are required for this path.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-import.txt

.venv/bin/python -m pipeline.import_field_atlas \
  --bundle-root /absolute/path/Field-Atlas-assets-2026-11-07 \
  --workbook /absolute/path/Valmet_Builder_Pack.xlsx \
  --target-region linards-BOX-0173

VALMET_OFFLINE=1 .venv/bin/python server.py 8766
```

Open [the local demo](http://127.0.0.1:8766/).

The importer validates the source PLY hash and the ownership array before generating the scene. Use `--source "/absolute/path/Equipment Room.ply"` when the original PLY is stored separately.

`VALMET_OFFLINE=1` skips credential loading and prevents AI calls. In this mode, service explanations use deterministic logic and retrieval over the workbook. The optional upstream voice assistant and AI rebuilding workflow are separate integrations.

## What the import preserves

| Data | Imported behavior |
| --- | --- |
| 1,933 regions | Stable source IDs, source labels, evidence and review status |
| 415 Linards regions | Retained identities and baseline membership |
| Exact ownership | Each displayed Gaussian maps to its original PLY row and region; no bounding-box expansion |
| 30 workbook records | Distinct procedural scene bindings with original telemetry, alarms and maintenance evidence |
| Other candidates | Unknown condition; no invented sensor readings or service diagnosis |
| Video evidence | Native crops and frame references where the bundle supplies them; no claim of exhaustive tracking |
| Asset library | Original per-object PLY properties retained for reuse |
| Navigation | Rebuilt from scan evidence; inaccessible destinations are reported instead of replaced by straight paths |

CV-003 keeps Wael’s physical demo location. Native video evidence shows the orange actuator beneath the green valve at `linards-BOX-0173`, near Wael’s existing anchor. The import explicitly moves the **synthetic record binding** from the package’s previous `linards-BOX-0262` assignment and retains that provenance. This does not identify the actual device tag or establish that the filmed equipment is faulty.

## Reusable modules

| Module | Responsibility |
| --- | --- |
| `pipeline/import_field_atlas.py` | Bundle adapter, source validation, coordinate conversion and exact ownership export |
| `pipeline/derived_context.py` | Offline pipe fitting, observed floor, assumed wall guides and display geometry, without changing object membership |
| `pipeline/knowledge.py` | Read equipment, telemetry, alarms, maintenance and documents from Excel |
| `pipeline/navigation.py` | Occupancy, clearance, path planning and proximity queries |
| `pipeline/diagnostics.py`, `pipeline/retrieval.py` | Workbook-grounded service explanations and retrieval |
| `pipeline/asset_export.py` | Export individual objects to the reusable asset library |
| `server.py` | Scene, service, library and editing endpoints |
| `web/imported-assets.mjs` | Imported-region presentation and evidence helpers |
| `web/scene-layers.mjs` | Shared floor-plan/BIM visibility and display-proxy rules for class-free candidates |
| `web/explode.mjs`, `web/take-apart.mjs` | Exact-membership separation and object inspection |

The viewer uses Wael’s levelled Y-up coordinates. Native PLY exports retain the original coordinate frame and Gaussian properties. Integration metadata records the conversion; do not align objects by filenames or array order alone.

The floor plan and geometry preview show large candidate regions even when their class is unknown. Visible/hidden counts and the small-region/overhead controls expose the full catalog. Preserved Linards names are normalized for display, with their unverified status retained. Oriented envelopes and fitted pipes are derived geometry, not new confirmed assets.

Observed floor is a separate context layer from the green navigation mask. The four wall guides retain Wael’s configured room-boundary assumptions. Adding these visual layers does not enlarge the walkable grid or change object ownership. Wael’s original generated 504-object artifact is not present in this checkout, so the integration cannot establish a one-to-one preservation claim for that separate extraction.

Imports derive this context automatically. To recover it in an existing imported scene, run `.venv/bin/python -m pipeline.derived_context` and restart the server. This offline command preserves source membership and navigation, and writes `web/data/context_recovery_report.json` with integrity hashes. The current recovery adds 120 pipe-fit hypotheses; IFC export includes them separately from the 1,933 regions and marks the wall/slab assumptions and unverified scale.

## Validation and limitations

Run the offline checks from the repository root:

```sh
VALMET_OFFLINE=1 .venv/bin/python -m unittest discover -s tests -v
node --test web/imported-assets.test.mjs
node --test web/scene-layers.test.mjs web/bim-export-context.test.mjs
node tests/test_navigation_view.mjs
node tests/test_journey.mjs
node tests/test_render_quality.mjs
```

Imported-scene validation is documented in [technical notes](docs/technical-notes.md). The upstream hand-labelled sample, 85% label score, 29/29 requirement score and refinement benchmarks were measured on a different generated scene. They must not be reported as results for these 1,933 imported regions.

The imported artifact retains all **711,568 owned source rows** in the rendered scene, including **103,378 baseline rows**. It provides grid-validated routes to **28 of 30** mapped devices, including CV-003; CV-011 and P-006 have no supported destination in the reachable floor component and remain unavailable. Distances are uncalibrated scan units.

Current limits:

- Candidate extraction is incomplete. Objects can be fragmented, merged or partially captured, and most additional regions have no native-video identity yet.
- Exact membership means reproducible extraction, not correct segmentation or complete geometry.
- The viewer is a scan-based demonstrator. Importing more regions does not reconstruct missing surfaces or produce a measured BIM.
- Navigation checks the derived occupancy grid. Unknown floor remains unavailable; the model does not prove real-world clearance or autonomous robot safety.
- Only the imported mode is guaranteed to avoid paid AI calls. The original `pipeline.run` and `scripts/check.sh` can invoke configured services and replace data; run those only deliberately.

Wael’s original setup and optional voice/refinement workflows remain available in the [upstream README](https://github.com/SinceAI-2026/Wael/blob/ab777d3c94182780cc3528fe1373e5b79e3fd110/README.md). Use the import instructions above for this expanded dataset.
