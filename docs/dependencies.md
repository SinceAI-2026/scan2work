# Dependency and module map

## Prepared demo: installed automatically

`requirements-demo.lock.txt` pins the complete Python dependency set used by the release. The installer requests binary wheels and runs `pip check`. No frontend package manager is involved. Dependencies retain their own licenses; the code archive includes notices for the bundled viewer libraries.

| Dependency | Pinned version | Used for |
| --- | --- | --- |
| Python | 3.12.x | Installer, HTTP server, file formats and pipeline modules |
| NumPy | 2.5.4 | Gaussian arrays, native source rows, membership and exports |
| SciPy | 1.18.1 | Spatial lookup, geometry and sparse retrieval |
| scikit-learn | 1.9.1 | Offline TF-IDF retrieval, clustering utilities |
| pandas | 3.1.1 | Workbook records and import adapters |
| openpyxl / et-xmlfile | 3.1.5 / 2.0.0 | Excel input parsing |
| Pillow | 13.0.0 | Image evidence, thumbnails and exports |
| joblib / cloudpickle | 1.6.0 / 3.1.2 | scikit-learn's execution/serialization support |
| threadpoolctl / narwhals | 3.8.0 / 2.28.0 | scikit-learn transitive dependencies |
| python-dateutil / tzdata | 4.0.0 / 2026.6 | pandas date/time dependencies |
| Three.js + OrbitControls + TransformControls + GLTFLoader | r170, bundled locally | WebGL 2 scan/geometry rendering and camera controls |
| Hugeicons | inline SVG subset | Viewer controls; no icon CDN request |
| Browser | WebGL 2 | Room rendering, IFC text export and user interaction |

These versions were resolved and installed in a new Python 3.12 environment on macOS Apple Silicon. See the release verification note for the tested platform and actual checks. A fresh Linux installation and HTTP startup were also verified for 0.1.0; browser rendering was tested on macOS. This is not an end-to-end Linux graphics test.

## Modules teammates can reuse

| Module | Inputs → outputs | Additional needs |
| --- | --- | --- |
| `pipeline/ingest.py` | Gaussian PLY → filtered/levelled arrays | NumPy; correct source coordinate frame |
| `pipeline/import_field_atlas.py` | source membership bundle + workbook → prepared dataset | pandas/openpyxl; no model required |
| `pipeline/build_baseline_demo.py` | original Wael data + extraction + walk poses → preserved room and review overlay | Explicit new output directory |
| `pipeline/derived_context.py` | prepared scene/source rows → floor/context raster and pipe hypotheses | SciPy/Pillow; derived suggestions remain unverified |
| `pipeline/asset_export.py` | selected object + exact source membership → native PLY/GSPL/row IDs and thumbnail | All native source files must match; exact-source mode needs no torch |
| `pipeline/manual_annotations.py`, `pipeline/annotations/`, `web/manual-annotation*.mjs` | reviewed source-row annotation → searchable label, original-region export and browser-local visibility | NumPy and bundled Three.js; checks source/render identity; separate from canonical ownership and workbook bindings |
| `pipeline/navigation.py`, `web/navigation*.mjs` | observed grid, boxes and destination → simulated route | Scan units/clearance assumptions; no robot controller |
| `pipeline/retrieval.py` | `rag_index.json` → TF-IDF grounded evidence | scikit-learn; no downloaded embedding model in offline mode |
| `pipeline/asset_records.py`, `pipeline/field_service.py` | canonical workbook bindings → operational API, checklist and local job log | Synthetic workbook values; prevents borrowed health on unmapped objects |
| `pipeline/scene_context.py`, `web/device-summary.mjs` | active twin → read-only operator context and compact device summary | Standard library backend; no workbook rebuild, model calls or generated records |
| `web/technician-avatar.mjs`, `web/technician-navigation.mjs` | Three.js + existing room clearance profile → compact walking technician, preserving the prepared walkable grids | Procedural model adapted from Rasmus; independent height and footprint fit; scan scale remains unverified |
| `web/presentation-mode.mjs`, `web/presentation-mode.css` | browser preference + selected equipment → Simplified/Extended presentation | Browser APIs; no job, device or scene-write capability |
| `web/hazard-layer.mjs` | operator context + current object geometry → optional energy annotations | Three.js/DOM; generic demonstration assumptions, no isolation authorization |
| `web/safety-demo.mjs` | injected navigation/view callbacks → interruptible local walkthrough | No job, sensor, model or persistence API; reusable without the host app |
| `server.py`, `pipeline/runtime_policy.py` | selected data directory → local HTTP/API/media range service | `VALMET_DATA_DIR`, `VALMET_OFFLINE=1` |
| `scripts/serve_public_demo.py`, `web/runtime-access.mjs` | isolated prepared dataset → hosted read-only preview with browser-local edits | Existing HTTPS proxy; `VALMET_PUBLIC_DEMO=1`; no provisioning or credentials in the module |
| `web/proposal-review*.mjs`, `web/imported-assets.mjs` | separate proposal catalog/evidence → candidate review | Optional full extraction URL supplied at launch |
| `web/take-apart.mjs`, `web/explode.mjs` | rendered rows + object membership → isolate/explode | Membership must align exactly to the render |
| `web/room-parts.mjs`, `web/parts-data.mjs`, `web/ai3d.mjs` | prepared `take_apart/index.json` + 23 GLBs → fitted illustrative models / split assemblies | Bundled Three.js loaders; no model service needed at runtime; dimensions unverified |
| `web/generated-model-policy.mjs` | explicit visual review + edit state → allowed generated replacements and scan coverage | Pure JavaScript; rejected models cannot suppress native scan points |
| `live/board.py`, `live/security.py`, `live/stores.py`, `web/fault-panel.mjs` | optional Arduino input + shared inspection state → fault panel | Hosted use requires explicit Arduino opt-in, private device token and HTTPS; optional database connectors listed below |
| `web/bim-export*.mjs`, `web/semantic-plan.mjs` | scene boxes, fitted geometry and context → plan/IFC | Browser only; scale remains unverified |
| `distribution/build_release.py`, `scripts/install_scan2work.py` | prepared data/code → hashed archives → installed app | Python standard library; pip only during install |

The [asset import guide](asset-import-guide.md) describes coordinate conventions, dataset contracts and source-row provenance. [Packaging audit](packaging-audit.md) distinguishes active evidence from optional historical/debug URLs.

## Optional authoring and external services

These are **not** installed or activated by the demo installer. Install them in a separate development environment when deliberately rebuilding or enabling that workflow.

| Capability | Extra dependencies / data | Where configured |
| --- | --- | --- |
| Rebuild original extraction | Original PLY, source video, Excel workbook; core `requirements.txt`, matplotlib; ffmpeg for video | `pipeline/config.py`, `pipeline/run.py`; legacy raw-input paths must be adapted |
| Local open-model segmentation | Full source checkout, `local_models/requirements.txt` (PyTorch/torchvision, transformers, accelerate, pycolmap and image tools), source data and model weights | `local_models/README.md`, `VALMET_SEGMENTER=local`; initial Hugging Face downloads need internet, no segmentation API key |
| Video-frame matching/lifting | Original video/frames, camera poses; ffmpeg and image/geometry libraries | `pipeline/walk*.py`, `pipeline/detect_lift.py` |
| Legacy photo-refined saved objects | PyTorch >=2.4 (upstream tested 2.8), original frames/poses and source PLY | `pipeline/object_refine.py`; original unmarked dataset mode only, CPU supported |
| Semantic embeddings | `sentence-transformers>=5.0`, model access/cache, possibly Hugging Face credentials | `pipeline/retrieval.py`; TF-IDF works without it |
| New vision labels / generated diagnostics | Configured provider credentials and network access; may incur cost | `pipeline/gemini_labels.py`, `pipeline/decisions_client.py`, `pipeline/diagnostics.py` |
| ElevenLabs voice assistant | ElevenLabs agent, configured MCP token and public HTTPS tunnel | `mcp_server.py`, `web/voice-assistant.mjs`; see [voice and MCP](voice-mcp.md) |
| Arduino fault panel | Arduino UNO Q; optional `live/requirements.txt` database clients, configured board token / service credentials | `arduino/README.md`; core installed board state uses the standard library; no live databases activated by installer |
| IFC schema validation | IfcOpenShell in a validation environment | Not required for browser IFC generation |
| JavaScript regression tests | Node.js (upstream tested 24) | `tests/*.mjs`, `web/*.test.mjs` |
| GPU reconstruction research / VLSplat | Separate research repositories, CUDA-compatible PyTorch/compiler/model weights and training inputs | Not a dependency of this packaged Scan2Work demo; no turnkey training claim |
| R2 publishing | AWS CLI, bucket-scoped R2 access profile, Cloudflare account/bucket/domain | `distribution/publish_r2.py`; downloaders do not need these credentials |

Only the user runs `pipeline.run` or `scripts/check.sh`: they may call paid services and replace prepared data. Installing the demo never invokes either command. The source PLY alone does not provide missing camera calibration, measured scale or ground-truth labels.

The prepared code archive excludes `local_models/` and downloaded weights. Borrow
the optional local segmenter from a full repository checkout, in its own
environment. Selecting it does not automatically make the surrounding diagnostics
and retrieval rebuild offline; those modules retain their separate configuration.
