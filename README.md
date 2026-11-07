# Field Atlas — original scene and candidate review

This fork includes [Wael’s upstream changes through 359530b](https://github.com/SinceAI-2026/Wael/tree/359530b305c67690b6ab8ef3b8e20098a09318ef), including the simplified sidebar, shared Scan/Coloured/BIM view controls and camera improvements. The main demo uses his supplied **504-object scene**, original photos, fitted equipment geometry, 30 procedural Excel bindings and walkthrough navigation. It preserves the original render buffers byte for byte.

The expanded extraction contains **1,933 candidate regions**, including all **415 Linards regions**. It is a separate review layer, not a replacement for the original catalog. Exact source-row correspondence connects proposals to original objects without changing their identities, device bindings or navigation. The full imported scene remains available separately for isolation and export.

Equipment records and fault scenarios come from Valmet’s **synthetic** Excel pack. The original archive also contains borrowed telemetry profiles for auxiliary objects; these are preserved for provenance but suppressed by the current viewer and service API. Only canonical Excel-linked equipment receives workbook readings and diagnoses. Model labels and candidate boundaries need review; more assigned points do not establish better recognition accuracy. Physical scale is unverified.

## Get the data

Obtain Wael’s `field-atlas-data.zip` and extract it into its own directory. Its `web/data` folder supplies the original generated scene. Do not unpack it over the imported dataset.

Download and extract `Field-Atlas-assets-2026-11-07.zip` from the [team’s shared asset folder](https://drive.google.com/drive/u/0/folders/14MMXTD37DJAzgkPp4029zU8tqayBF7OP). Keep the extracted directory structure intact. The bundle contains the room scan, exact object ownership, individual object files and available native video evidence. It does not contain the Excel workbook; use the supplied Valmet builder pack.

The same importer can read the original Field Atlas project directory, which contains `model1/demo/data/scene-extraction/scene.json`.

Generated scene data is excluded from Git. The importer creates this fork’s own `web/data/` outputs; the source package stays unchanged.

## Run locally

Python 3.10+ and a modern WebGL 2 browser are sufficient for these prepared-data paths. No CUDA, AI credentials or model downloads are required.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-import.txt

.venv/bin/python -m pipeline.import_field_atlas \
  --bundle-root /absolute/path/Field-Atlas-assets-2026-11-07 \
  --workbook /absolute/path/Valmet_Builder_Pack.xlsx \
  --target-region linards-BOX-0173

.venv/bin/python -m pipeline.build_baseline_demo \
  --original-data /absolute/path/original/web/data \
  --proposal-data web/data \
  --poses web/data/walk_poses.json \
  --output /absolute/path/wael-baseline-data \
  --full-view-url http://127.0.0.1:8768/

VALMET_OFFLINE=1 VALMET_DATA_DIR=/absolute/path/wael-baseline-data \
  .venv/bin/python server.py 8766
```

Open [the main demo](http://127.0.0.1:8766/). In another terminal, serve the complete imported scene for candidate review:

```sh
VALMET_OFFLINE=1 .venv/bin/python server.py 8768
```

`VALMET_DATA_DIR` selects the dataset for both static files and APIs, including edits, service logs and library saves. Without it, the server uses `web/data`, which contains the imported scene. The baseline builder refuses an existing output directory and never replaces either input. Keep the chosen data-directory setting in your launch command.

The importer validates the source PLY hash and the ownership array before generating the scene. Use `--source "/absolute/path/Equipment Room.ply"` when the original PLY is stored separately.

`VALMET_OFFLINE=1` skips credential loading and prevents backend AI calls. Service explanations use archived outputs and deterministic retrieval over the workbook. New baseline object exports preserve the original GSID ownership, including its historical box filling, without rerunning refinement. The optional upstream voice assistant and AI rebuilding workflow are separate integrations.

## What the separate import preserves

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
| `pipeline/build_baseline_demo.py` | Preserve the original scene, recover native source-row exports and project candidate correspondence into a separate review catalog |
| `pipeline/import_field_atlas.py` | Bundle adapter, source validation, coordinate conversion and exact ownership export |
| `pipeline/derived_context.py` | Offline pipe fitting, observed floor, assumed wall guides and display geometry, without changing object membership |
| `pipeline/knowledge.py` | Read equipment, telemetry, alarms, maintenance and documents from Excel |
| `pipeline/navigation.py` | Occupancy, clearance, path planning and proximity queries |
| `pipeline/diagnostics.py`, `pipeline/retrieval.py` | Workbook-grounded service explanations and retrieval |
| `pipeline/asset_export.py` | Export individual objects to the reusable asset library |
| `server.py` | Scene, service, library and editing endpoints |
| `web/imported-assets.mjs` | Imported-region presentation and evidence helpers |
| `web/proposal-review.mjs`, `web/proposal-review-data.mjs` | Optional candidate overlay, original-object correspondence, evidence and complete-review links |
| `web/scene-layers.mjs` | Shared floor-plan/BIM visibility and display-proxy rules for class-free candidates |
| `web/explode.mjs`, `web/take-apart.mjs` | Exact-membership separation and object inspection |

The viewer uses Wael’s levelled Y-up coordinates. Native PLY exports retain the original coordinate frame and Gaussian properties. Integration metadata records the conversion; do not align objects by filenames or array order alone.

In the separate imported view, the floor plan and geometry preview show large candidate regions even when their class is unknown. Visible/hidden counts and the small-region/overhead controls expose the full catalog. Preserved Linards names are normalized for display, with their unverified status retained. Oriented envelopes and fitted pipes are derived geometry, not new confirmed assets. The main baseline uses the original equipment geometry instead.

Observed floor is a separate context layer from the green navigation mask. The four wall guides retain Wael’s configured room-boundary assumptions. Adding these visual layers does not enlarge the walkable grid or change object ownership.

Wael’s original generated data contains 504 assets across 24 classes, 471 linked source photos and 83 asset-attached cylinder geometries. The imported scene is a different segmentation, not a superset: preserving the raw scan and Linards membership does not preserve Wael’s grouping or evidence associations. The baseline adapter therefore keeps both inventories separate, with review-only correspondence links. Object counts alone do not measure improvement.

See the [original-data audit](docs/original-data-audit.md) for source-row correspondence, grouping changes, synthetic mapping differences and the original browser's walkthrough-based floor expansion. To repeat the read-only comparison, extract the archive into a separate directory, then run:

```sh
.venv/bin/python -m pipeline.audit_original_data \
  --original-data /absolute/path/original/web/data \
  --current-data web/data \
  --output /absolute/path/comparison.json
node scripts/audit_runtime_floor.mjs \
  --original-data /absolute/path/original/web/data \
  --current-data web/data \
  --poses web/data/walk_poses.json \
  --output /absolute/path/runtime-floor.json
```

Both output files must be outside the input data directories. These commands do not merge or relabel objects.

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

To test a staged baseline instead of the default imported data, set `VALMET_DATA_DIR=/absolute/path/wael-baseline-data` on the Python test command. Artifact checks apply to their dataset; the preserved imported scene can still be tested independently. The browser walkthrough verifies the original CV-003 route, candidate-overlay isolation, reversible object removal, background native export and IFC validation; see [technical notes](docs/technical-notes.md).

Imported-scene validation is documented in [technical notes](docs/technical-notes.md). The upstream hand-labelled sample, 85% label score, 29/29 requirement score and refinement benchmarks were measured on a different generated scene. They must not be reported as results for these 1,933 imported regions.

The imported artifact retains all **711,568 owned source rows** in the rendered scene, including **103,378 baseline rows**. It provides grid-validated routes to **28 of 30** mapped devices, including CV-003; CV-011 and P-006 have no supported destination in the reachable floor component and remain unavailable. Distances are uncalibrated scan units.

Current limits:

- Candidate extraction is incomplete. Objects can be fragmented, merged or partially captured, and most additional regions have no native-video identity yet.
- Exact membership means reproducible extraction, not correct segmentation or complete geometry.
- The viewer is a scan-based demonstrator. Importing more regions does not reconstruct missing surfaces or produce a measured BIM.
- The main demo restores Wael’s walkthrough-expanded navigation policy; the separate import keeps its conservative observed-floor grid. Neither policy proves real-world clearance or robot safety, and their assumed robot dimensions differ.
- These prepared-data workflows run offline. The original `pipeline.run` and `scripts/check.sh` can invoke configured services and replace data; only the user runs them deliberately.

Wael’s original setup and optional voice/refinement workflows remain available in the [upstream README](https://github.com/SinceAI-2026/Wael/blob/359530b305c67690b6ab8ef3b8e20098a09318ef/README.md). Use the explicit dataset launch commands above for this integration.
