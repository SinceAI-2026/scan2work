"""Appearance signal: what an instance looks like to SigLIP 2, zero-shot.

Every label gets a prompt ensemble (CLIP, Radford et al. 2021, sec. 3.1.4): each of its phrases in
each template of pipeline/classes.json, the normalised text embeddings averaged. The labels are the
asset classes, the other things the room holds (tank, cabinet, door...; so a tank isn't forced into
"pump") and junk (pipe, wall, sticker...: one label, scored by its closest phrase). Each view's
image embedding (pipeline/embed.py: mean over its crop scales) gets a softmax over the labels; an
instance's scores are the mean over its views.
"""
import json

import numpy as np
import torch
from transformers import AutoModel, AutoProcessor

from geometry import repo
from verdict import Signal

NAMER = "google/siglip2-so400m-patch16-384"  # pipeline/embed.py's image side
JUNK = "junk"
# What SAM cuts out that is not equipment. No "a blurry photo": it is about the view, not the thing, and
# wins half of all views (small objects upscaled, masked crops). "pipe" and "pipe elbow" are real labels
# now (classes.json other_prompts: the BIM export draws pipes), so they are not junk phrases.
JUNK_PROMPTS = [
    "insulated pipe", "pipe flange", "pipe with a coloured label",
    "a sticker", "a wall", "the floor", "the ceiling", "a bundle of cables", "a ladder", "a shelf",
    "a light fixture",
]

config = json.loads((repo / "pipeline" / "classes.json").read_text())
CLASSES = [c["name"] for c in config["classes"]]
OTHERS = [d["prompt"] for d in config["detector_prompts"] if not d.get("counts_as")] + config["other_prompts"]
LABELS = CLASSES + OTHERS + [JUNK]


@torch.no_grad()
def label_texts():
    """One text embedding per label but junk (its prompt ensemble), and one per junk phrase; the logit scale."""
    model = AutoModel.from_pretrained(NAMER).to("cpu").eval()
    processor = AutoProcessor.from_pretrained(NAMER)

    def embed(phrases):
        texts = [t.format(p) for p in phrases for t in config["templates"]]
        inputs = processor(text=texts, padding="max_length", max_length=64, return_tensors="pt")
        vectors = torch.nn.functional.normalize(model.get_text_features(**inputs).pooler_output, dim=-1).numpy()
        return vectors.reshape(len(phrases), len(config["templates"]), -1)

    ensemble = [embed(c["prompts"]) for c in config["classes"]]
    ensemble = [v.reshape(-1, v.shape[-1]) for v in ensemble]
    ensemble += list(embed(OTHERS))
    labels = np.stack([v.mean(0) / np.linalg.norm(v.mean(0)) for v in ensemble])
    junk = embed(JUNK_PROMPTS).mean(1)
    return labels, junk / np.linalg.norm(junk, axis=1, keepdims=True), model.logit_scale.exp().item()


def view_probabilities(view_embeddings, texts, with_junk=True):
    """Softmax over the labels (junk: its closest phrase) per view; without junk, over the rest
    (junk's probability is then 0: the question is only what the thing is, if it is a thing)."""
    labels, junk, scale = texts
    junk_logit = (view_embeddings @ junk.T).max(1, keepdims=True) if with_junk else np.full((len(view_embeddings), 1), -np.inf)
    logits = np.concatenate([view_embeddings @ labels.T, junk_logit], 1) * scale
    p = np.exp(logits - logits.max(1, keepdims=True))
    return p / p.sum(1, keepdims=True)


def judge(probabilities):
    """The label an instance looks most like, over all labels."""
    best = int(np.argmax(probabilities))
    return Signal(LABELS[best], probabilities[best], f"looks like {LABELS[best]} ({probabilities[best]:.2f})")
