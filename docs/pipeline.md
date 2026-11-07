# Pipeline

`python -m pipeline.run` (or `bash scripts/check.sh`, which also runs the scorecard) turns `Equipment Room.ply`
and the Valmet builder workbook into `web/data/digital_twin.json` plus the binary splat the viewer loads. The
detail behind each stage, and its measured quality, is in [technical notes](technical-notes.md).

## Stages

| # | Stage | Module | What it does |
|---|---|---|---|
| 1 | Ingest | `pipeline/ingest.py` | Decodes the PLY (position, scale, rotation, colour, opacity), drops corrupt rows, levels the room: `(x, 1.185 − y, −z)`, floor at y = 0 |
| 2 | Objects | `pipeline/perception.py` or `local_models/` | Proposes objects and fits oriented boxes. Two segmenters, see below |
| 3 | Labels | `semantic_labels.py`, `gemini_labels.py`, `detect_lift.py` | Names each object from its real walkthrough photo; multi-view 2D detection lifted to 3D finds objects clustering missed (heuristic segmenter only) |
| 4 | Pipes | `pipeline/pipes.py` | Fits pipe cylinders by surface-normal voting; gives pipes a true axis and adds missed runs |
| 5 | Photos | `pipeline/walk_photos.py` | Matches every object to its sharpest visible walkthrough frame (334 COLMAP-posed frames, `walk.py`) |
| 6 | Workbook | `pipeline/knowledge.py` | Binds the 30 Excel devices to distinct objects by class and position; CV-003 is the faulty target. Objects without a record carry no borrowed telemetry |
| 7 | Navigation | `pipeline/navigation.py` | 2.5D occupancy grid, robot-radius dilation, A* routes with stand-off to every mapped device, proximity lists |
| 8 | Diagnostics | `diagnostics.py`, `retrieval.py` | Grounded triage, root-cause hypotheses and LOTO-first verification steps, citing Excel evidence IDs (TF-IDF or EmbeddingGemma retrieval) |
| 9 | Export | `pipeline/run.py`, `floorplan.py` | Splat buffer with per-splat object ids, `digital_twin.json`, semantic floor plan (SVG/PDF/PNG) and IFC/GLB export |

Hosted AI is optional everywhere: cached answers in `web/data/decisions_cache.json` make a rebuild reproducible
without keys, and every model call has a deterministic fallback.

## Two segmenters

Choose with `VALMET_SEGMENTER`. Stages 4–9 are the same for both.

| | `heuristic` (default) | `local` |
|---|---|---|
| Segmentation | Colour and geometry rules, DBSCAN, oriented boxes | SAM 2.1 masks on the walkthrough video, stitched into 3D objects by view consensus (MaskClustering) |
| Naming | Gemini on real photos (OpenAI Decisions API fallback, or cached answers) | SigLIP 2, DINOv2, OWLv2 and a linear probe, open weights on this machine |
| API key / plant images leave the site | Yes, for a fresh labelling run | No |
| Objects in a fresh build | 467 | 485 (415 segmented + 70 pipe cylinders) |
| Label accuracy, 39-object sample | 85 % | 31 % (the sample was drawn from the heuristic clusters, so it favours them) |

The prepared release ships Wael's original **504-object** room unchanged; the numbers above are fresh rebuilds
from source. `VALMET_SEGMENTER=local bash scripts/check.sh` builds on the committed local results; re-running the
segmentation itself is described in [local_models/README.md](../local_models/README.md).

## How we got here

[![Pipeline approaches, from 2D-first to 3D-first](media/pipeline-approaches.gif)](media/pipeline-approaches.mp4)

*Sped up 3×. [Full video, 2 min](media/pipeline-approaches.mp4).*

The local segmenter is the result of these experiments on the same room, all on the walkthrough video aligned to
the splat (334 COLMAP-posed frames, so every pixel maps to the Gaussians it shows):

| Approach | Result | Kept? |
|---|---|---|
| Pipe cylinders as a helper signal (trim boxes, veto detections) | No detection gain | Dropped (cylinders kept as their own stage) |
| **2D-first:** OWLv2 boxes → SAM 2.1 masks → lift to Gaussians → SigLIP 2 names | 253 objects, 53 duplicate pairs, 28.7 % of the room covered; names well, misses what it has no word for | Replaced |
| **3D-first:** SAM 2.1 segments everything → view-consensus stitching in 3D → fused naming | 415 objects (+64 %), 17 duplicate pairs (−68 %), 42.5 % covered (+14 pts) | **Adopted** |
| Coarse-to-fine merge and split | Merging fixed a few areas, splitting mostly failed | Dropped |
| Linear probe on DINOv2 + SigLIP 2 + size, with active learning | Pumps 0.92/0.92 precision/recall; 80 labels beat random picks 0.92 vs 0.89 | Adopted |
| VLSplat-style refinement (the paper Valmet pointed to) | 3 better, 26 same, 11 worse on 40 objects: clutter raised the score | Not adopted |
| Local VLM naming (Qwen3-VL-4B) | 20–21/30 vs 23/30 for the zero-shot names | Not adopted |
| Look-alike types by best-view DINOv2 plus colour family | Pumps 1 → 5 types, valves 1 → 12, largest unclassified group 166 → 58 | Adopted |
| Label-free classes with propagation | 3 of 14 "pumps" real | Not adopted |

Ground rules for these experiments: open models only (Apache 2.0); SAM 3 excluded (its licence excludes nuclear
use, and Valmet supplies nuclear plants); YOLO excluded (AGPL).
