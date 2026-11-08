# Local model pipeline (optional segmenter)

Semantic extraction from Valmet's boiler-room Gaussian splat. Everything runs locally on open models: SAM 2.1,
SigLIP 2, DINOv2 and OWLv2, with open weights downloaded from Hugging Face. No API key is needed.

The twin uses these objects when it is built with `VALMET_SEGMENTER=local` (see [Two segmenters](../docs/pipeline.md#two-segmenters)).
`pipeline/local_objects.py` reads `data/pipeline/objects.json` and `objects.npz`, which are committed here so
the twin can be built on them without re-running anything below.

## Setup

This is an optional authoring environment, separate from the prepared demo. Run the following from the
repository root; all later commands on this page assume the `local_models/` directory. Model weights require
initial downloads and their own licenses and hardware requirements apply.

```sh
cd local_models
uv venv --python 3.12 && uv pip install -r requirements.txt
```

Put the walkthrough video at `data/raw/boiler-room.mp4`. The splat is read from `data/equipment-room.ply`, else
from `../../Equipment Room.ply` (where the main pipeline reads it), or from `LOCAL_MODELS_PLY`
(Valmet's fixed splat, 1,676,481 Gaussians). The first PLY in the challenge zip was two-thirds corrupt.

## Run

```sh
python3 -m http.server 8765   # then open http://localhost:8765
```

- `index.html`: the splat. WASD/QE move, drag looks. V replays the video's camera path, P shows points,
  B toggles object boxes, N the walkable area and its roadmap. Click an object in the sidebar to fly to
  it and draw the route there.

## Pipeline

Each step writes files the next one reads. Steps 2–3 need the viewer running. The room is segmented first
and the pieces are named after (3D-first).

1. `ffmpeg -i data/raw/boiler-room.mp4 -vf "fps=3,scale=960:-2" -q:v 2 data/sfm/images/%04d.jpg`
2. `python tools/render_views.py`: renders the splat from known cameras.
3. `python tools/sfm.py`: COLMAP over the video frames and the renders.
4. `python tools/align.py`: fits COLMAP onto the splat, writes `walk.json` (the video's camera path).
5. `python pipeline/detect.py`: OWLv2 boxes for the detector prompts in `pipeline/classes.json`, SAM 2.1 masks,
   to `data/pipeline/detections/`. Here they are only a second opinion on names (step 10).
6. `python pipeline/amg.py`: SAM 2.1 automatic masks for every posed frame, class-agnostic (a point grid),
   to `data/pipeline/amg/`.
7. `python pipeline/navigate.py`: floor, ceiling, walkable area and its roadmap, and a route to every object
   once there are objects (run it again after step 11); writes `data/pipeline/nav.json`.
   `--from=x,z --to=x,z` (or `--to=obj-012`) prints one route.
8. `python pipeline/cluster.py`: lifts every mask onto the Gaussians and stitches masks into 3D instances
   by view consensus; drops room structure (floor and ceiling heights from `nav.json`). Writes
   `data/pipeline/instances.{json,npz}` and `evidence.npz` (per instance Gaussian: views that see it, and
   how many put it inside).
9. `python pipeline/embed.py`: the best views of every instance, cropped at three scales, embedded with
   SigLIP 2 and DINOv2. Writes `data/pipeline/embeddings.npz`, `views.json` and `crops/`.
10. `python pipeline/fuse.py`: names the instances from five signals (`appearance.py`, `plausibility.py`,
    `detector.py`, `consistency.py`, `similarity.py`) in a fixed cascade, merges look-alike pieces the
    views show as one. Writes `data/pipeline/objects.json` (with `confidence`, `separation`, `reasons` and
    `signals`), `objects.npz` and `rejected.json`.
10b. **Labelling and classifier.** `python tools/label_server.py`, then open http://127.0.0.1:8002/label.html.
    One object at a time, its views and size, the current guess pre-selected. Keys: 1 pump, 2 motor,
    3 valve, 4 pressure transmitter, 5 vibration sensor, 0 other, X bad piece (a mix or a fragment),
    Enter accepts the guess, S skips, Backspace undoes. Labels go to `data/pipeline/labels.json`
    (object id → `{label, source, ts}`; format in `pipeline/classifier.py`). Retrain (button, and every
    10 labels) runs `pipeline/classifier.py`, which rewrites `objects.json` with the classifier's classes
    and puts the most uncertain objects next. A stand-in's labels (`labels-claude.json`) are trained on
    and shown with their source until an expert's label replaces them. With no labels `objects.json`
    stays fuse.py's result. By hand: `python pipeline/classifier.py --labels data/pipeline/labels.json`;
    Then run steps 11–12.
11. `python pipeline/catalog.py`: groups each class (and the unclassified objects) into types by look
    and colour, names them from the phrases in `classes.json` where clear, cuts each type's model out of
    the splat with all PLY properties. Writes `data/pipeline/types.json` and `models/<type-id>.ply`.
12. `python pipeline/navigate.py` again: routes to the new objects.

The Excel mapping, navigation and the viewer are the main pipeline's: `VALMET_SEGMENTER=local bash scripts/check.sh`
from the repo root.

`tools/record_walk.py` renders the splat along the video path for a side-by-side recording.

`tools/evaluate.py` compares two object sets without labels (duplicates, membership consensus, coverage, name
agreement across views, counts, confidence).

## Method (3D-first)

Every object is cut out of the room before anything is named, so unnamed things still become assets.

- **Segment.** SAM 2.1 automatic masks (point grid, predicted-IoU and stability filters, box NMS) per frame,
  class-agnostic: the 2D proposal stage of OVIR-3D, Open3DIS and MaskClustering.
- **Stitch in 3D.** MaskClustering (Yan et al., CVPR 2024, [arXiv 2401.07745](https://arxiv.org/abs/2401.07745)):
  masks become nodes of a graph; two nodes merge when at least 90% of the frames that observe both (each
  ≥ 30% visible) have one mask containing ≥ 80% of both; masks split across several masks in > 20% of
  their other views are dropped as under-segmenting; merging runs in rounds with the minimum observer
  count falling from the 95th to the 5th percentile, so well-observed pairs merge first. Our one
  deviation: SAM's automatic masks leave gaps (the paper uses CropFormer, which labels every pixel), and
  in a quarter of the frames that saw a mask's Gaussians no mask covered them at all, so almost nothing
  could reach 90% consensus. A frame now observes a node only if its masks cover ≥ 80% of the node's
  visible Gaussians. Then per instance: Gaussians its views put inside it at least half the times they
  see them (MaskClustering's point filter), split into touching clumps (the DBSCAN step from OVIR-3D),
  parts inside a bigger instance dropped, room structure (> 2.5 m, thin slabs, floor and ceiling
  patches) dropped. Boxes from `geometry.fit_box`.
- **Appearance.** OpenMask3D (Takmaz et al., NeurIPS 2023, [arXiv 2306.13631](https://arxiv.org/abs/2306.13631)):
  the top 5 views by visible Gaussians, crops at 3 scales (the masked object, then two with surroundings).
  SigLIP 2 zero-shot with a prompt ensemble per class (every prompt in `classes.json` in every template,
  text embeddings averaged; CLIP, [Radford et al. 2021](https://arxiv.org/abs/2103.00020)), against the
  classes, the other things in the room and junk. The masked crop decides the class (over the classes and
  other things); the crops with surroundings decide junk (around a valve they mostly show pipe) and break
  ties: when the masked crop names nothing, a class the surroundings name clearly counts if the masked
  crop ranks it in its top two (a pump on grey reads as a bucket). Masked crops alone named 6 pumps where
  the averaged crops named 31.
- **Detector.** A second opinion from OWLv2 detections (step 5): a view supports a class
  when an OWLv2 detection of it and the instance's masks mostly overlap each other. An object nothing
  else named takes a class at least half its views support, if its size fits and SigLIP ranks it in
  its top two.
- **Similarity.** DINOv2 embeddings of the masked views. Look-alikes that touch merge only when the views
  show them as one (≥ 50% of shared views have one mask holding both), else they are twins and stay apart.
  Label propagation over a DINOv2 kNN graph (Zhou et al., NeurIPS 2003; ZLaP, Stojnić et al., CVPR 2024,
  [arXiv 2404.04072](https://arxiv.org/abs/2404.04072)) was tried and named no object in two runs, so it
  was removed.
- **Plausibility.** A longest-side range per class in `classes.json` (size priors as in OpenSight,
  [arXiv 2312.08876](https://arxiv.org/abs/2312.08876), and GLRD, [arXiv 2503.20682](https://arxiv.org/abs/2503.20682),
  which score a box against a class's typical size with a tolerance). No height rule: pumps hang on the
  circulator wall and valves sit on overhead pipes; a "big pumps stand on the floor" rule rejected wall pumps.
  Shape limits so pipes and rods aren't named: a pump's thinnest side ≥ 0.08 m, a valve's longest side
  ≤ 3× its middle one. A 3× limit for pumps was tried and removed: the splat mostly holds an object's
  front, so boxes come out elongated, and it withdrew real inline pumps along with the rods.
- **Consistency.** The share of an object's views (on the crops that named it) whose own top label agrees
  with its class. A class that fewer than half the views agree on and the detector doesn't back is held
  back: the object stays unclassified, with the reason.
- **Fusion.** A fixed cascade, documented in `pipeline/fuse.py`: junk → size gate → appearance → detector →
  hold back. `confidence` is how sure an object is of an asset class (for a named one, the mean of the
  signals that agree; for an unclassified one, how close its best class came, so low). `separation` is a
  different question: how surely it is one separate thing (the share of its Gaussians most of its views
  put inside it). Every object carries its reasons ("looks like pump (0.71)", "size fits", "5/5 views
  agree", "detector: valve in 4/6 views"). Thresholds are one stated value each, not tuned; no hand
  labels are used anywhere, evaluation included.
- **Classifier (optional, step 10b).** A linear probe on frozen features, as in CLIP's linear-probe
  evaluation ([Radford et al. 2021](https://arxiv.org/abs/2103.00020), sec. 3.2): L2-regularised multinomial
  logistic regression on DINOv2 ⊕ SigLIP 2 masked-crop embeddings ⊕ log box sides, trained on an expert's
  labels of the 3D objects. The expert labels the objects it is least sure of first: active learning by
  margin sampling (Settles, *Active Learning Literature Survey*, 2009), skipping look-alikes of objects
  already queued. Stratified k-fold accuracy goes to `data/pipeline/eval/classifier.json`.
