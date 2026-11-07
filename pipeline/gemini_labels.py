"""Batched object labelling with Gemini (generateContent REST API, zero extra dependencies).

Sends C.GEMINI_BATCH photo-like renders per request and asks for one JSON label per image, constrained
to the same class vocabulary as the Decisions labeller. Every answer is stored in the shared
decisions cache under its own key, in the same shape as a Decisions `choice` answer, so the rest of
semantic_labels.py treats both backends identically. Objects Gemini doesn't reach (daily quota, errors)
fall back to their cached Decisions answer.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

from . import config as C
from . import decisions_client as D

URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


def enabled() -> bool:
    """Gemini labels are used if there is a key OR cached Gemini answers (a fresh clone rebuilds without keys)."""
    if C.LABELLER == "decisions":
        return False
    return (bool(os.environ.get("GEMINI_API_KEY")) or C.LABELLER == "gemini"
            or any(k.startswith("gemini") for k in D._load_cache()))


def cache_key(label_key: str, prefix: str = "gemini-v1") -> str:
    return f"{prefix}:{C.GEMINI_MODEL}:{label_key}"


def _request(parts: list, schema: dict) -> list | None:
    body = {
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {"temperature": 0, "responseMimeType": "application/json", "responseSchema": schema},
    }
    req = urllib.request.Request(
        URL.format(model=C.GEMINI_MODEL), data=json.dumps(body).encode(), method="POST",
        headers={"x-goog-api-key": os.environ.get("GEMINI_API_KEY", ""), "Content-Type": "application/json"},
    )
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=180) as resp:
                data = json.loads(resp.read())
            text = data["candidates"][0]["content"]["parts"][0]["text"]
            return json.loads(text)
        except urllib.error.HTTPError as e:
            detail = e.read().decode()[:300]
            if e.code == 429 and "PerDay" in detail:
                print("   [gemini] daily free-tier quota reached; remaining objects keep their Decisions labels")
                return "quota"
            if (e.code == 429 or e.code >= 500) and attempt < 2:
                time.sleep(30 * (attempt + 1))
                continue
            print(f"   [gemini] request failed ({e.code}): {detail}")
            return None
        except (urllib.error.URLError, TimeoutError, KeyError, IndexError, ValueError) as e:
            if attempt < 2:
                time.sleep(10)
                continue
            print(f"   [gemini] request failed: {e}")
            return None
    return None


PHOTO_PREFIX = "gemini-photo-v1"
BOTH_PREFIX = "gemini-both-v1"
COLLAGE_PREFIX = "gemini-collage-v1"
GLOSSARY = (
    "Industrial glossary for this room:\n"
    "- control_valve: valve body INLINE in a pipe with a handwheel, lever, or an actuator/positioner box on top "
    "(e.g. orange or blue box actuators). An actuator or positioner sitting on a valve is control_valve, not electric_motor.\n"
    "- instrument: small device OFF the pipe run: round pressure gauge, thermometer, small transmitter box or cylinder "
    "with a cable gland.\n"
    "- pump: inline pump with a motor head (e.g. red/black Grundfos head with a display) or a base-mounted pump set.\n"
    "- electric_motor: finned cylindrical motor without a display, driving a pump by a shaft.\n"
    "- pipe_vertical / pipe_horizontal: straight runs, often white, grey or aluminium insulation cladding; green arrow "
    "tape is a flow marker on a pipe, still a pipe. pipe_fitting: elbow, tee, flange, reducer, strainer.\n"
    "- tank_vessel: large closed red, blue or grey vessel (e.g. 'reflex' expansion vessel).\n"
    "- electrical_cabinet: flat wall enclosure with a door. appliance: washing machine (round door), dryer, fridge.\n"
    "- cleaning_equipment: broom, mop, wet/dry vacuum (yellow-black body with hose), NOT cart_trolley. "
    "cart_trolley: wheeled frame for carrying (roll cage, hand truck, pallet jack).\n"
    "- clutter: only if no view shows a coherent object inside the box.\n"
    "Common mistakes to avoid: orange box on a white valve body -> control_valve (not electric_motor); red round "
    "vessel -> tank_vessel (not clutter); cladded pipe with green tape -> pipe (not instrument or pump).\n")
RENDER_PREFIX = "gemini-v1"

INTRO = {
    "render": ("Each image below is a rendered view of part of a 3D Gaussian-splat scan of an industrial boiler / "
               "equipment room. In each image, classify ONLY the object inside the yellow 3D box (everything else is "
               "context). Splat renders can be blurry; judge shape, size, height and context. "),
    "collage": ("Each image below is a 2x2 collage of ONE object from an industrial boiler / equipment room: "
                "A = render of the 3D scan around the object (blurry, but only the scan near the box), "
                "B = best real photo from a walkthrough video, C = real photo from another angle, D = wider real photo "
                "showing what the object is mounted on. The object is the one inside the yellow 3D box in every tile "
                "(in photos the box is projected from the scan, can be a few cm off and is drawn over things in front "
                "of the object). Combine all four views. " + GLOSSARY),
    "both": ("Each object below is shown TWICE: first a render of a 3D Gaussian-splat scan of an industrial boiler / "
             "equipment room (only the scan near the object, blurry), then a real frame from a walkthrough video of the "
             "same room. In both, the object is the one inside the yellow 3D box; in the photo the box is projected from "
             "the scan, can be a few cm off and is drawn over things in front of or behind the object. Use the render for "
             "WHAT is inside the box and the photo for what it really looks like (material, colour, nameplates). "),
    "photo": ("Each image below is a real frame from a hand-held walkthrough video of an industrial boiler / equipment "
              "room. The yellow 3D box is one object from the aligned 3D scan, projected into the photo (it can be a "
              "few cm off). In each image, classify ONLY the object inside the yellow box (everything else is context); "
              "read labels or nameplates if visible. "),
}


def label_batches(jobs: list, classes: dict, label_key, prefix: str = RENDER_PREFIX, kind: str = "render",
                  batch: int | None = None) -> int:
    """jobs = [(asset, jpeg_data_url)]. Fills the cache for objects not yet labelled; returns #labelled now."""
    cache = D._load_cache()
    if not os.environ.get("GEMINI_API_KEY"):
        return 0                                       # cache-only mode: never call the API without a key
    def _ok(u):
        return all(x.startswith("data:image/jpeg") for x in (u if isinstance(u, (list, tuple)) else [u]))
    todo = [(a, url) for a, url in jobs if _ok(url) and cache_key(label_key(a), prefix) not in cache]
    if not todo:
        return 0
    choices = list(classes)
    guide = "\n".join(f"- {k}: {v[2]}" for k, v in classes.items())
    schema = {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
        "id": {"type": "STRING"},
        "label": {"type": "STRING", "enum": choices},
        "confidence": {"type": "NUMBER"},
    }, "required": ["id", "label", "confidence"]}}
    bs = batch or C.GEMINI_BATCH
    n_req = -(-len(todo) // bs)
    print(f"   [gemini] labelling {len(todo)} objects from {kind}s in {n_req} batched requests with {C.GEMINI_MODEL}")
    header = {"text": (
        INTRO[kind] + "Use 'clutter' only if "
        "there is no real object inside the box, and 'other' for a real object that fits no class.\n"
        f"Classes:\n{guide}\n\nReturn one entry per image with its id, the label, and your confidence 0-1.")}

    def ask(chunk):
        parts = [header]
        for a, url in chunk:
            sx, sy, sz = a["size"]
            parts.append({"text": f"Image id {a['id']}: box {sx:.2f} x {sy:.2f} x {sz:.2f} m (W x H x D), "
                                  f"centre {a['center'][1]:.2f} m above floor."})
            for u in (url if isinstance(url, (list, tuple)) else [url]):
                parts.append({"inline_data": {"mime_type": "image/jpeg", "data": u.split(",", 1)[1]}})
        return chunk, _request(parts, schema)

    chunks = [todo[b:b + bs] for b in range(0, len(todo), bs)]
    workers = max(1, C.GEMINI_WORKERS)
    done = finished = 0
    from concurrent.futures import ThreadPoolExecutor, as_completed
    with ThreadPoolExecutor(workers) as pool:          # paid tier: several requests in flight at once
        futs = []
        for k, ch in enumerate(chunks):
            futs.append(pool.submit(ask, ch))
            if workers == 1 or k < workers:
                time.sleep(C.GEMINI_MIN_INTERVAL_S if workers == 1 else 0.3)
        for fut in as_completed(futs):
            chunk, out = fut.result()
            finished += 1
            if out == "quota" or not isinstance(out, list):
                continue
            by_id = {str(r.get("id")): r for r in out if isinstance(r, dict)}
            for a, _ in chunk:
                r = by_id.get(a["id"])
                if not r or r.get("label") not in classes:
                    continue
                conf = max(0.0, min(1.0, float(r.get("confidence", 0.5))))
                cache[cache_key(label_key(a), prefix)] = {"equipment_class": {
                    "type": "choice", "name": "equipment_class", "choice": r["label"], "confidence": conf,
                    "probabilities": [{"value": r["label"], "probability": conf}], "source": C.GEMINI_MODEL,
                    "image": kind}}
                done += 1
            D.save_cache()
            print(f"   [gemini] batch {finished}/{n_req}: {done} labelled so far")
    return done


def cached_answer(asset: dict, label_key, prefixes=(COLLAGE_PREFIX, RENDER_PREFIX)) -> dict | None:
    """Photo answers win over render answers when both exist."""
    cache = D._load_cache()
    for p in prefixes:
        hit = cache.get(cache_key(label_key(asset), p))
        if hit:
            return hit
    return None
