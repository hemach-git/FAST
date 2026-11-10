"""Section-level retrieval per category, over PUBLISHED documents only (pre-filter).

mode = bm25    lexical BM25 only (pure Python).
mode = hybrid  BM25 + dense embeddings, fused with Reciprocal Rank Fusion, then (optionally) a cross-encoder
               reranker over the fused top-N.  See app/services/semantic.py for the model backends.

Every hit keeps `score` = its BM25 score and `coverage` = share of query terms present, so the abstain
thresholds in config keep their meaning. Hybrid adds `dense`, `rrf`, `rerank` and `rank_score` (the value the
list is ordered by). Pure Python + numpy, no vector DB - fine for hundreds of sections.
"""
from __future__ import annotations

import math
import os
import re
import threading
from collections import Counter

import numpy as np

from app.config import get_config
from app.data import db
from app.services import semantic

HIT_KEYS = ("chunk_id", "doc_id", "section", "text", "doc_key", "title", "version", "category")

STOP = set("""a an the and or of to in on at for with by is are was be it this that how what when where why
which who do does did i we you my our your me can should must need from as if not no into per any all about
there their them they then than so up out its it's has have had will would could get got please tell""".split())

_lock = threading.Lock()
_index: dict | None = None


def _stem(t: str) -> str:
    for suf in ("ing", "ed", "es", "s"):
        if len(t) > 4 and t.endswith(suf):
            return t[: -len(suf)]
    return t


def tokenize(text: str) -> list[str]:
    return [_stem(t) for t in re.findall(r"[a-z0-9]+", text.lower()) if t not in STOP]


def invalidate() -> None:
    global _index
    with _lock:
        _index = None


def retrieval_mode() -> str:
    m = (os.getenv("FAST_RETRIEVAL_MODE") or get_config()["retrieval"].get("mode", "bm25")).strip().lower()
    return m if m in ("bm25", "hybrid") else "bm25"


def _build() -> dict:
    data = db.rows("""SELECT c.chunk_id, c.doc_id, c.section, c.text, d.doc_key, d.title, d.version,
                             d.category, d.equipment
                      FROM chunks c JOIN documents d ON d.doc_id = c.doc_id
                      WHERE d.status = 'published'""")
    by_cat: dict[str, list[dict]] = {}
    for r in data:
        # Title + section + equipment boost lexical match on headings.
        r["tokens"] = tokenize(f"{r['doc_key']} {r['title']} {r['section']} {r['section']} {r['equipment']} {r['text']}")
        r["tf"] = Counter(r["tokens"])
        by_cat.setdefault(r["category"], []).append(r)
    idx = {}
    for cat, docs in by_cat.items():
        n = len(docs)
        df = Counter(t for d in docs for t in set(d["tokens"]))
        idx[cat] = {"docs": docs, "n": n, "df": df,
                    "avgdl": sum(len(d["tokens"]) for d in docs) / max(n, 1)}
    # Global IDF across all categories so scores are comparable between categories.
    all_docs = [d for v in by_cat.values() for d in v]
    gdf = Counter(t for d in all_docs for t in set(d["tokens"]))
    return {"cats": idx, "gdf": gdf, "N": len(all_docs)}


def _get_index() -> dict:
    global _index
    with _lock:
        if _index is None:
            _index = _build()
        return _index


def _dense_vectors(idx: dict, cat: str) -> np.ndarray:
    """Lazily embed a category's sections (only when hybrid search is first used)."""
    ci = idx["cats"][cat]
    with _lock:
        if "vecs" not in ci:
            ci["vecs"] = semantic.embed_corpus(
                [f"{d['title']} - {d['section']}\n{d['text']}"[:2000] for d in ci["docs"]])
        return ci["vecs"]


def _bm25(idx: dict, ci: dict, q: list[str], k1: float, b: float) -> list[tuple[int, float]]:
    N, gdf, qs = idx["N"], idx["gdf"], set(q)
    out = []
    for i, d in enumerate(ci["docs"]):
        s = 0.0
        dl = len(d["tokens"])
        for t in qs:
            tf = d["tf"].get(t, 0)
            if tf:
                idf = math.log(1 + (N - gdf[t] + 0.5) / (gdf[t] + 0.5))
                s += idf * tf * (k1 + 1) / (tf + k1 * (1 - b + b * dl / ci["avgdl"]))
        if s > 0:
            out.append((i, s))
    out.sort(key=lambda x: x[1], reverse=True)
    return out


def _hit(d: dict, q: list[str], bm25: float) -> dict:
    qs = set(q)
    return {**{k: d[k] for k in HIT_KEYS}, "score": round(bm25, 3),
            "coverage": round(len(qs & set(d["tf"])) / len(qs), 3)}


def search(query: str, categories: list[str], top_k: int = 4, k1: float = 1.4, b: float = 0.75,
           mode: str | None = None) -> list[dict]:
    idx = _get_index()
    q = tokenize(query)
    if not q:
        return []
    mode = mode or retrieval_mode()
    rc = get_config()["retrieval"]
    reranker = semantic.get_reranker() if mode == "hybrid" else None
    qv = semantic.get_embedder().embed_query(query) if mode == "hybrid" else None
    cats = [c for c in categories if c in idx["cats"]]
    if mode == "bm25":
        results = []
        for cat in cats:
            ci = idx["cats"][cat]
            results.extend({**_hit(ci["docs"][i], q, s), "rank_score": round(s, 3)}
                           for i, s in _bm25(idx, ci, q, k1, b)[:top_k])
        results.sort(key=lambda x: x["rank_score"], reverse=True)
        return results

    # ---- hybrid: ONE candidate pool across the requested categories, so ranks (and RRF) are comparable.
    #      BM25 list + dense list -> RRF -> optional cross-encoder rerank -> cap top_k per category.
    n_cand, k_rrf = int(rc.get("candidates", 20)), int(rc.get("rrf_k", 60))
    lex, dense, bm, sim = [], [], {}, {}
    for cat in cats:
        ci = idx["cats"][cat]
        for i, s in _bm25(idx, ci, q, k1, b):
            lex.append(((cat, i), s))
            bm[(cat, i)] = s
        for i, s in enumerate(_dense_vectors(idx, cat) @ qv):
            dense.append(((cat, i), float(s)))
            sim[(cat, i)] = float(s)
    lex.sort(key=lambda x: x[1], reverse=True)
    dense.sort(key=lambda x: x[1], reverse=True)
    fused: dict[tuple, float] = {}
    for ranked in (lex[:n_cand], dense[:n_cand]):
        for r, (key, _) in enumerate(ranked):
            fused[key] = fused.get(key, 0.0) + 1.0 / (k_rrf + r + 1)
    n_top = max(int(rc.get("rerank_top_n", 12)), top_k)
    hits = []
    for key in sorted(fused, key=lambda k: fused[k], reverse=True)[:n_top]:
        h = _hit(idx["cats"][key[0]]["docs"][key[1]], q, bm.get(key, 0.0))
        h.update(dense=round(sim[key], 4), rrf=round(fused[key], 5), rerank=None, rank_score=round(fused[key], 5))
        hits.append(h)
    if reranker:
        texts = [f"{h['title']} - {h['section']}\n{h['text']}" for h in hits]
        for h, p in zip(hits, reranker.score(query, texts)):
            h["rerank"] = h["rank_score"] = round(p, 4)
    # Dense-only candidates (no BM25 match) must be confirmed semantically, else they are just noise.
    hits = [h for h in hits if h["score"] > 0 or (
        h["rerank"] >= rc.get("rerank_min", 0.5) if reranker else h["dense"] >= rc.get("dense_min", 0.6))]
    hits.sort(key=lambda h: h["rank_score"], reverse=True)
    per_cat: Counter = Counter()
    results = []
    for h in hits:
        if per_cat[h["category"]] < top_k:
            per_cat[h["category"]] += 1
            results.append(h)
    return results


def category_scores(query: str) -> dict[str, float]:
    """Best BM25 score per category - used by the (mock) classifier."""
    hits = search(query, ["safety", "maintenance", "quality"], top_k=1, mode="bm25")
    out = {c: 0.0 for c in ("safety", "maintenance", "quality")}
    for h in hits:
        out[h["category"]] = max(out[h["category"]], h["score"])
    return out
