# Prepared demo packaging audit

Audited 7 November 2026. This describes the prepared **504-object baseline** and optional **1,933-region extraction** distribution. It does not require rerunning detection, training, or photo refinement. The release manifest and installer remain authoritative for archive hashes and versions.

## Runtime and install boundary

The prepared viewer uses local JavaScript modules, a local Python HTTP/API server, and a browser with WebGL. There is no npm build, remote renderer, model download, CUDA or H100 requirement for playback, navigation, candidate review, IFC export, or exact source-object export. Python packages must be installed before disconnected use; downloading the application/data archives from R2 and installing packages from PyPI are installation steps, not offline operations. A fully disconnected installation also needs compatible Python packages/wheels supplied for its operating system.

Keep `runtime.offline: true` and `runtime.external_models_enabled: false` in both prepared manifests. The server runtime policy reads the manifest before configuration loads credentials. The browser voice bootstrap checks these flags before creating the ElevenLabs widget or its remote script. Offline service answers use the prepared workbook information and local retrieval; they are not new model inference.

Current prepared server imports need NumPy, SciPy, scikit-learn and Pillow. `pandas`/`openpyxl` support workbook ingestion and import tooling; `torch`, `matplotlib` and optional `sentence-transformers` belong to other reconstruction, rendering, or online retrieval workflows. The installer owns the tested requirements set. Existing `field_service.py` uses `fcntl`; native Windows support requires a compatible locking implementation, otherwise use Linux/WSL or macOS.

## Application files

Ship `server.py`, the `pipeline/` Python modules and all application code under `web/`, preserving relative paths. In particular:

- `index.html`, `library.html`, their local CSS and `.js`/`.mjs` imports.
- `web/vendor/three/three.module.js` and `web/vendor/three/addons/controls/OrbitControls.js`. Both HTML import maps point here, not at a CDN.
- `web/vendor/NOTICE.md` and the complete license notices beneath `vendor/three/` and `vendor/hugeicons/`.

The splat sort worker is created from an in-memory Blob. It does not require a separate worker download. IFC generation also runs locally. System fonts are used (`-apple-system`, `BlinkMacSystemFont`, Segoe UI, sans-serif; ui-monospace/Menlo for monospace); there are no font binaries, web-font services or icon-font requests.

Use the Python server, not `file://` or an arbitrary static file server, for the full demo. The UI calls local `/api/library`, `/api/scene/edits`, `/api/field-jobs`, `/api/copilot/diagnose` and `/api/work-orders/draft` routes. AR availability currently uses a local `data/` directory listing; video playback/seek uses HTTP byte ranges. The bundled server provides these behaviours.

## Dataset files

The planned layout is `install/app/`, `install/data/baseline/`, and optionally `install/data/extraction/`. Each process serves its dataset as `/data/` through `VALMET_DATA_DIR`. Do not flatten or rename the internal paths.

| Dataset-relative path | Purpose / requirement |
| --- | --- |
| `digital_twin.json` | Required. Canonical objects/regions, geometry, navigation, synthetic service records, diagnostics, references and runtime policy. |
| `boiler_room_points.bin` + `splat_instance.bin` | Required matching GSPL/GSID pair. Baseline: 1,046,910 rendered rows and 504 IDs. Full extraction: 1,131,951 rows and 1,933 IDs. Do not mix files between datasets. |
| `source_scan.ply`, `splat_source_rows.npy`, `source_members.npz` | Required for exact **Save to library**/native PLY exports and identity verification. The source PLY is also checked against the manifest SHA256. They are not disposable caches. |
| `walk_poses.json` | Preserve the tracked pose file for baseline walkthrough passage navigation. With the archived baseline it produces 7,225 walkable cells; omission changes navigation to the original raw grid. Imported extraction intentionally does not apply these passage patches. |
| `rag_index.json` | Preserve for local document retrieval and grounded service answers. `knowledge_docs` in the main manifest does not replace all retrieval-index entries. |
| `photos/**` | Baseline source evidence. 471 distinct active photo URLs were checked; all exist. Keep the directory for preview and library-hover evidence. |
| `proposal-review/catalog.json`, `proposal-review/projected_instance.bin`, `proposal-review/evidence/**` | Baseline optional-review layer. All 1,933 proposals are separate from the 504 canonical objects; the projected GSID uses the baseline render order. All 3,716 active evidence paths exist. These files allow box/highlight/evidence review even without installing the full extraction dataset. |
| `object-rebuild/images/**`, `object-rebuild/crops/**`, `scene-extraction/crops/**` | Full extraction evidence. All 3,716 active `photo.url` and `evidence[]` image paths exist. Preserve paths as stored in the manifest. |
| `overlay_walk.mp4` | Optional AR demonstration, approximately 64.8 MB. Keep it in the full-feature baseline bundle to retain the existing AR action. The button is hidden when absent. Original MOV videos are not needed to play this prepared MP4. |
| `library/library.json` and referenced `library/<object-id>/**` | Optional pre-saved examples, but required together if shipped. Preserve complete entries/files, not just thumbnails. See below. |
| `scene_edits.json`, `field_log.json` | Mutable local state. Ship clean initial state or deliberately documented demo state. Never overwrite an existing user's state during an update. Do not distribute `.lock` files as meaningful data. |

The baseline archive was observed at 945,624,430 bytes before release cleanup; full extraction at 747,995,103 bytes. These are uncompressed snapshots, not promised download sizes. The 395,651,019-byte `source_scan.ply` is identical in both datasets; any release deduplication must preserve the expected dataset-relative path and read access.

### Asset library completeness

The library index has a `saved` array and per-entry `files` paths relative to `data/library/`. Preserve every non-null referenced file, including archived files not directly linked by the current UI:

- `gspl`: required for the library's 3D object viewer and isolated saved-model display.
- `scan_gspl`: required for the **Scan only** toggle on refined entries.
- `thumb`: card image; the original object `photo` remains a dataset-relative image URL.
- `ply`, optional `model_ply`/`enhanced_ply`, and optional `glb`: downloadable original/refined geometry; retain archived provenance.
- `source_rows`: exact source-row IDs for newer exact-membership exports.

The inspected baseline had five saved entries, including four archived objects and exact `OBJ-206`. The optional dataset had exact `linards-BOX-0173`. Every non-null `files` reference existed. These counts can change with demo use; validate the index at release creation rather than hardcoding an allowlist of objects. New exact exports do not require Torch or the original MOV files. Legacy unmarked scenes retain their separate photo-refinement workflow.

## Optional outputs, provenance and rebuilding inputs

`baseline_report.json`, `import_report.json`, `context_recovery_report.json`, standalone `derived_context.json`, `navigation_map_preview.png`, `floorplan_scan.png` and `proposal-review/full_source_members.npz` are useful provenance/debug outputs. Current viewer operation reads its geometry/navigation/context from the main manifest and its proposal projection from `projected_instance.bin`; these standalone audit files are not core rendering dependencies. Keep them if the distribution promises reproducibility or team investigation.

`rag_embeddings.npy` is optional in the forced-offline TF-IDF configuration. `decisions_cache.json` is a rebuilding cache, not a requirement for the prepared offline service responses. Never clear a working team's cache during installation. `walk_frames/**`, original MOV files, the Excel workbook, detector caches, training checkpoints and source extraction outputs are inputs to reprocessing/refinement; the prepared exact-export demo does not need to reconstruct them at launch.

Two existing reference limitations are distinct from missing required assets:

- Six baseline `camera_view.image` paths (`./data/views/cam1.jpg` through `cam6.jpg`) are absent from the archived source bundle. They affect the technical detection canvas, whose error handler reports an unavailable image. Native photos and 3D rendering still work. Do not claim those six images are included.
- The full extraction manifest retains old `originalEvidenceUrls`, `evidenceUrls` and guided-pump audit links inside provenance. Some are absent. The viewer uses the present `photo.url` / `evidence[].cropUrl` / `imageUrl` instead. Do not treat old provenance URLs as runtime fetch dependencies or regenerate missing evidence automatically.

## Optional full-view link

The baseline manifest should ship `proposal_review.full_view_url: null`. Missing, empty or null configuration suppresses **Inspect complete candidate** and explains that complete inspection needs the optional dataset. There is no hardcoded fallback to port 8768.

When the optional dataset is installed, the launcher sets `VALMET_PROPOSAL_VIEW_URL` for the baseline process. The server replaces the served value in memory; it should not rewrite archived source data. The viewer accepts the configured HTTP(S) URL or a relative URL and appends the selected region as an encoded `#apart=...` fragment. The installer may choose ports other than 8766/8768.

## External services and notices

The only active remote browser-script URL in application code is the optional online ElevenLabs widget at `https://unpkg.com/@elevenlabs/convai-widget-embed@0.21.4/dist/index.js`. Explicit offline flags prevent its creation. SVG namespace URLs and hyperlinks in vendor comments are not network requests. Current prepared photo/evidence references resolve to local dataset files. R2 is a distribution download host, not a renderer or inference dependency after installation.

Three.js r170 and OrbitControls are MIT-licensed; the [original release notice](https://github.com/mrdoob/three.js/blob/r170/LICENSE) is included in `web/vendor/three/LICENSE`. Inline Hugeicons free Stroke Rounded icons are MIT-licensed according to the [publisher's license documentation](https://hugeicons.com/docs/faqs/license-questions); the full publisher notice and source reference are included under `web/vendor/hugeicons/` and `web/vendor/NOTICE.md`. These dependency licenses do not define the challenge dataset's redistribution permissions. Keep the dataset in the team's approved sharing scope.

## Verification performed

- Validated active photo/evidence paths in both manifests and the proposal catalog; all present.
- Validated every non-null library file reference; all present.
- Added regression tests for null/empty full-view URLs, custom ports, relative URLs and encoded region IDs.
- Exercised the actual voice bootstrap in an inert DOM: offline manifests fetch only the local twin manifest, mount field jobs and create no widget/script. An unmarked online manifest still takes the existing optional-widget branch. This is a bootstrap regression test, not a claim that an online voice call was made.
- Existing membership, candidate isolation, navigation, render-quality, metadata-display and IFC tests remain applicable. The installer should additionally verify archive checksums, expected binary row counts, per-entry paths and clean state during staging.
