"""Retrieval-only eval: compares BM25 vs hybrid vs hybrid + reranker on the golden set (no LLM, free).

    python -m evals.retrieval_eval            # all configs, prints a table, writes evals/results/retrieval.json
    python -m evals.retrieval_eval --k 3

Uses every golden case that has `expect_cited_doc`. Search runs over ALL categories (no routing) so it measures
the retriever alone. A hit is relevant if its doc_key equals the expected document.
Metrics: Hit@1, Hit@k, MRR@10 (first relevant document), mean and p95 search latency.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

CONFIGS = {  # name -> (mode, embedder, reranker)
    "bm25": ("bm25", "hash", "none"),
    "hybrid_hash": ("hybrid", "hash", "none"),
    "hybrid_local": ("hybrid", "local", "none"),
    "hybrid_local_rerank": ("hybrid", "local", "cross_encoder"),
}
CATS = ["safety", "maintenance", "quality"]


def p95(xs: list[float]) -> float:
    return sorted(xs)[max(0, int(round(0.95 * len(xs))) - 1)] if xs else 0.0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--k", type=int, default=3)
    ap.add_argument("--configs", default=",".join(CONFIGS))
    args = ap.parse_args()

    tmp = Path(tempfile.mkdtemp(prefix="fast-reval-"))
    os.environ.update(FAST_DB_PATH=str(tmp / "eval.db"), FAST_LOG_DIR=str(tmp / "logs"), LLM_PROVIDER="mock")
    from app.config import get_config
    get_config.cache_clear()
    from app.services import retriever, semantic, workflow
    workflow.ensure_ready()

    cases = [json.loads(ln) for ln in (ROOT / "evals" / "golden.jsonl").read_text(encoding="utf-8").splitlines() if ln.strip()]
    cases = [c for c in cases if c.get("expect_cited_doc")]
    report = {"k": args.k, "n_cases": len(cases), "configs": {}}
    for name in args.configs.split(","):
        mode, emb, rr = CONFIGS[name]
        os.environ.update(FAST_RETRIEVAL_MODE=mode, FAST_EMBEDDER=emb, FAST_RERANKER=rr)
        semantic.reset()
        retriever.invalidate()
        retriever.search("warm up", CATS, top_k=1)           # load models + embed corpus outside the timing
        hit1 = hitk = rr_sum = 0.0
        lat, misses, by_type = [], [], {}
        for c in cases:
            t = time.perf_counter()
            hits = retriever.search(c["question"], CATS, top_k=10)
            lat.append((time.perf_counter() - t) * 1000)
            docs = list(dict.fromkeys(h["doc_key"] for h in hits))      # rank of documents, not sections
            rank = docs.index(c["expect_cited_doc"]) + 1 if c["expect_cited_doc"] in docs else None
            hit1 += rank == 1
            hitk += bool(rank and rank <= args.k)
            rr_sum += 1 / rank if rank and rank <= 10 else 0
            t_ = by_type.setdefault(c["type"], [0, 0])
            t_[0] += rank == 1
            t_[1] += 1
            if rank != 1:
                misses.append({"id": c["id"], "expected": c["expect_cited_doc"], "rank": rank, "top": docs[:3]})
        n = len(cases)
        report["configs"][name] = {
            "embedder": semantic.get_embedder().name if mode == "hybrid" else None,
            "reranker": getattr(semantic.get_reranker(), "name", None) if mode == "hybrid" else None,
            "hit@1": round(hit1 / n, 3), f"hit@{args.k}": round(hitk / n, 3), "mrr@10": round(rr_sum / n, 3),
            "hit@1_by_type": {k: f"{v[0]}/{v[1]}" for k, v in sorted(by_type.items())},
            "latency_ms_mean": round(statistics.mean(lat), 1), "latency_ms_p95": round(p95(lat), 1),
            "misses": misses}

    print(f"\nRetrieval eval - {len(cases)} cases, search over all categories\n")
    print(f"{'config':22} {'hit@1':>6} {'hit@' + str(args.k):>6} {'mrr@10':>7} {'mean ms':>8} {'p95 ms':>7}  models")
    for name, r in report["configs"].items():
        print(f"{name:22} {r['hit@1']:6.3f} {r[f'hit@{args.k}']:6.3f} {r['mrr@10']:7.3f} "
              f"{r['latency_ms_mean']:8.1f} {r['latency_ms_p95']:7.1f}  {r['embedder'] or '-'} / {r['reranker'] or '-'}")
    print("\nhit@1 by case type:")
    for name, r in report["configs"].items():
        print(f"  {name:22} " + "  ".join(f"{k}={v}" for k, v in r["hit@1_by_type"].items()))
    out = ROOT / "evals" / "results" / "retrieval.json"
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nwrote {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
