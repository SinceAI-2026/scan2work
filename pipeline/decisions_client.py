"""Minimal OpenAI Decisions API client (POST /v1/decisions, model gpt-6-luna).

Zero extra dependencies (urllib). Every response is cached on disk keyed by the
request body, so the demo replays offline after one online build. With no
OPENAI_API_KEY (and no cache hit) `decide()` returns None and callers fall back
to the deterministic pipeline.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.error
import urllib.request

from . import config as C

URL = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/") + "/decisions"
CACHE_PATH = C.OUT_DIR / "decisions_cache.json"
_cache: dict | None = None


def _load_cache() -> dict:
    global _cache
    if _cache is None:
        try:
            _cache = json.loads(CACHE_PATH.read_text())
        except (OSError, ValueError):
            _cache = {}
    return _cache


def save_cache() -> None:
    if _cache:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        CACHE_PATH.write_text(json.dumps(_cache))


def enabled() -> bool:
    if C.OFFLINE:
        return False
    return bool(os.environ.get("OPENAI_API_KEY")) or bool(_load_cache())


def decide(input_, questions: list[dict], timeout: float = 60.0, cache_key: str | None = None) -> dict | None:
    """Return {question_name: answer_dict} or None if unavailable.

    cache_key: optional stable key (e.g. asset id + geometry) for requests whose bytes differ across
    machines, such as rendered images; defaults to a hash of the full request body.
    """
    if C.OFFLINE:
        return None
    body = {"model": C.DECISIONS_MODEL, "input": input_, "questions": questions}
    raw = json.dumps(body, sort_keys=True)
    key = cache_key or hashlib.sha256(raw.encode()).hexdigest()
    cache = _load_cache()
    if key in cache:
        return cache[key]

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return None
    req = urllib.request.Request(
        URL,
        data=raw.encode(),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    data = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read())
            break
        except (urllib.error.URLError, TimeoutError, ValueError) as e:
            transient = not isinstance(e, urllib.error.HTTPError) or e.code >= 500 or e.code == 429
            if transient and attempt < 2:
                time.sleep(2 * (attempt + 1))
                continue
            detail = e.read().decode()[:300] if isinstance(e, urllib.error.HTTPError) else str(e)
            print(f"   [decisions] request failed, using fallback: {detail}")
            return None

    answers = {a["name"]: a for a in data.get("answers", []) if a.get("type") != "refusal"}
    cache[key] = answers
    return answers


def choice_probs(answer: dict) -> dict[str, float]:
    """Normalise a choice answer's probabilities to {value: p} (API may return list or dict)."""
    probs = answer.get("probabilities") or {}
    if isinstance(probs, list):
        probs = {p.get("value") or p.get("choice"): float(p.get("probability", 0)) for p in probs}
    if not probs and answer.get("choice"):
        probs = {answer["choice"]: float(answer.get("confidence", 1.0))}
    return {k: float(v) for k, v in probs.items()}
