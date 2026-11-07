"""Detect objects in the posed video frames: OWLv2 boxes from the class prompts, SAM 2.1 masks.

Reads data/sfm/images/*.jpg and the detector prompts in pipeline/classes.json. Writes
data/pipeline/detections/<frame>.npz (boxes, labels, scores and packed half-resolution masks) and
data/pipeline/detections.json.
"""
import json
import sys
from pathlib import Path

import numpy as np
import torch
import torchvision
from PIL import Image
from transformers import Owlv2ForObjectDetection, Owlv2Processor, Sam2Model, Sam2Processor

repo = Path(__file__).resolve().parent.parent
frames_dir = repo / "data" / "sfm" / "images"
out = repo / "data" / "pipeline" / "detections"
out.mkdir(parents=True, exist_ok=True)

DEVICE = "cuda" if torch.cuda.is_available() else "mps" if torch.backends.mps.is_available() else "cpu"
DETECTOR = "google/owlv2-base-patch16-ensemble"
SEGMENTER = "facebook/sam2.1-hiera-large"
THRESHOLD = 0.2  # OWLv2 score; lower finds more and noisier objects
NMS_IOU = 0.5
MASK_SCALE = 2  # masks are stored at 1/2 the frame size

classes = json.loads((repo / "pipeline" / "classes.json").read_text())["detector_prompts"]
queries = [[f"a photo of a {c['prompt']}" for c in classes]]

detector_processor = Owlv2Processor.from_pretrained(DETECTOR)
detector = Owlv2ForObjectDetection.from_pretrained(DETECTOR).to(DEVICE).eval()
segmenter_processor = Sam2Processor.from_pretrained(SEGMENTER)
segmenter = Sam2Model.from_pretrained(SEGMENTER).to(DEVICE).eval()

frames = sorted(frames_dir.glob("*.jpg"))
if len(sys.argv) > 1:
    frames = [frames_dir / name for name in sys.argv[1:]]

summary = {}
for frame in frames:
    image = Image.open(frame).convert("RGB")
    with torch.no_grad():
        inputs = detector_processor(text=queries, images=image, return_tensors="pt").to(DEVICE)
        result = detector_processor.post_process_grounded_object_detection(
            detector(**inputs), threshold=THRESHOLD, target_sizes=torch.tensor([[max(image.size)] * 2]),
        )[0]
    boxes, scores, labels = (result[k].cpu() for k in ("boxes", "scores", "labels"))
    keep = torchvision.ops.batched_nms(boxes, scores, labels, NMS_IOU)
    boxes, scores, labels = boxes[keep], scores[keep], labels[keep]
    boxes[:, 0::2] = boxes[:, 0::2].clamp(0, image.width)
    boxes[:, 1::2] = boxes[:, 1::2].clamp(0, image.height)

    masks = np.zeros((0, image.height // MASK_SCALE, image.width // MASK_SCALE), bool)
    if len(boxes):
        with torch.no_grad():
            inputs = segmenter_processor(images=image, input_boxes=[boxes.tolist()], return_tensors="pt").to(DEVICE)
            outputs = segmenter(**inputs, multimask_output=False)
        full = segmenter_processor.post_process_masks(outputs.pred_masks.cpu(), inputs["original_sizes"].cpu())[0]
        masks = full[:, 0, ::MASK_SCALE, ::MASK_SCALE].numpy().astype(bool)

    np.savez_compressed(
        out / f"{frame.stem}.npz",
        boxes=boxes.numpy(), scores=scores.numpy(), labels=labels.numpy(),
        masks=np.packbits(masks, axis=-1), mask_shape=np.array(masks.shape),
    )
    summary[frame.name] = [classes[int(l)]["name"] for l in labels]
    print(frame.name, len(boxes), flush=True)

(repo / "data" / "pipeline" / "detections.json").write_text(json.dumps(summary))
