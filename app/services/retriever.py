"""Section-level BM25 retrieval per category, over PUBLISHED documents only (pre-filter).

Pure Python (no vector DB) - fine for ~100 sections; swap for hybrid search in v1.
"""
from __future__ import annotations

import math
import re
import threading
from collections import Counter

from app.data import db

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


def search(query: str, categories: list[str], top_k: int = 4, k1: float = 1.4, b: float = 0.75) -> list[dict]:
    idx = _get_index()
    q = tokenize(query)
    if not q:
        return []
    N, gdf = idx["N"], idx["gdf"]
    results = []
    for cat in categories:
        ci = idx["cats"].get(cat)
        if not ci:
            continue
        scored = []
        for d in ci["docs"]:
            s = 0.0
            dl = len(d["tokens"])
            for t in set(q):
                tf = d["tf"].get(t, 0)
                if not tf:
                    continue
                idf = math.log(1 + (N - gdf[t] + 0.5) / (gdf[t] + 0.5))
                s += idf * tf * (k1 + 1) / (tf + k1 * (1 - b + b * dl / ci["avgdl"]))
            if s > 0:
                cover = len(set(q) & set(d["tf"])) / len(set(q))
                scored.append({**{k: d[k] for k in ("chunk_id", "doc_id", "section", "text", "doc_key",
                                                    "title", "version", "category")},
                               "score": round(s, 3), "coverage": round(cover, 3)})
        scored.sort(key=lambda x: x["score"], reverse=True)
        results.extend(scored[:top_k])
    results.sort(key=lambda x: x["score"], reverse=True)
    return results


def category_scores(query: str) -> dict[str, float]:
    """Best BM25 score per category - used by the (mock) classifier."""
    hits = search(query, ["safety", "maintenance", "quality"], top_k=1)
    out = {c: 0.0 for c in ("safety", "maintenance", "quality")}
    for h in hits:
        out[h["category"]] = max(out[h["category"]], h["score"])
    return out
