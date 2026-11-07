"""Semantic retrieval over the Builder Pack corpus (EmbeddingGemma, TF-IDF fallback).

Corpus = Knowledge_Docs + Historical_Service_Cases + one asset card per canonical
asset. Embeddings come from EmbeddingGemma via sentence-transformers when it is
installed (and the model is downloadable / cached); otherwise a TF-IDF index is
used so the pipeline and server always work offline.
"""
from __future__ import annotations

import json

import numpy as np

from . import config as C

INDEX_JSON = C.OUT_DIR / "rag_index.json"
INDEX_NPY = C.OUT_DIR / "rag_embeddings.npy"


def build_corpus(excel_pack: dict) -> list[dict]:
    items = [
        {"id": d["doc_id"], "kind": "doc", "scope": d["scope"], "title": d["title"], "text": d["content"]}
        for d in excel_pack["docs"].values()
    ]
    for c in excel_pack["cases"]:
        items.append({
            "id": c["case_id"], "kind": "case", "scope": c["asset_class"],
            "title": f"{c['asset_class']} {c['issue_code']}: {c['interpretation']}",
            "text": (f"{c['interpretation']}. Severity {c['severity']}. Next check: {c['recommended_next_check']}. "
                     f"Workflow: {c['safe_evidence_workflow']}"),
        })
    for r in excel_pack["records"].values():
        items.append({
            "id": f"DOC-{r['asset_id']}", "kind": "asset_card", "scope": r["asset_class"],
            "title": f"{r['tag']} {r['asset_class']} asset card",
            "text": (f"{r['tag']} ({r['asset_id']}) {r['asset_class']} in {r['area_name']}, {r['process_context']}, "
                     f"criticality {r['criticality']}. State {r['condition_state']}, issue {r['issue_code']}: "
                     f"{r['issue_summary']}. Recommended next check: {r['recommended_next_check']}."),
        })
    return items


def _texts(items):
    return [f"title: {it['title']} | text: {it['text']}" for it in items]


class Retriever:
    def __init__(self, items: list[dict], embeddings: np.ndarray | None = None, try_gemma: bool = True):
        self.items = items
        self.backend = "tfidf"
        self._model = None
        if try_gemma:
            try:
                from sentence_transformers import SentenceTransformer
                self._model = SentenceTransformer(C.EMBED_MODEL)
                self.backend = "embeddinggemma"
            except Exception as e:  # not installed, gated model, offline...
                print(f"   [retrieval] EmbeddingGemma unavailable ({type(e).__name__}); using TF-IDF")
        if self._model is not None:
            self.emb = embeddings if embeddings is not None else self._encode(_texts(items), doc=True)
        else:
            from sklearn.feature_extraction.text import TfidfVectorizer
            self._tfidf = TfidfVectorizer(ngram_range=(1, 2), sublinear_tf=True, token_pattern=r"[A-Za-z0-9_\-]+")
            self.emb = self._tfidf.fit_transform(_texts(items))

    def _encode(self, texts, doc: bool):
        m = self._model
        fn = getattr(m, "encode_document" if doc else "encode_query", None)
        vec = fn(texts) if fn else m.encode(texts, prompt_name="document" if doc else "query")
        vec = np.asarray(vec, dtype=np.float32)
        return vec / (np.linalg.norm(vec, axis=1, keepdims=True) + 1e-9)

    def search(self, query: str, k: int = C.RAG_TOP_K, scope: str | None = None) -> list[dict]:
        if self._model is not None:
            scores = self.emb @ self._encode([query], doc=False)[0]
        else:
            scores = (self.emb @ self._tfidf.transform([query]).T).toarray().ravel()
        if scope:  # soft preference for same asset class / global docs
            boost = np.array([0.08 if it["scope"] in (scope, "GLOBAL", "ALL") else 0.0 for it in self.items])
            scores = scores + boost
        top = np.argsort(-scores)[:k]
        return [{**self.items[i], "score": round(float(scores[i]), 3)} for i in top]

    def save(self):
        INDEX_JSON.write_text(json.dumps({"backend": self.backend, "model": C.EMBED_MODEL, "items": self.items}))
        if self.backend == "embeddinggemma":
            np.save(INDEX_NPY, self.emb)


def load_saved(try_gemma: bool = True) -> Retriever | None:
    try:
        meta = json.loads(INDEX_JSON.read_text())
    except (OSError, ValueError):
        return None
    emb = np.load(INDEX_NPY) if meta["backend"] == "embeddinggemma" and INDEX_NPY.exists() else None
    return Retriever(meta["items"], emb, try_gemma=try_gemma and meta["backend"] == "embeddinggemma")
