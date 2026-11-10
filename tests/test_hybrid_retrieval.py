"""Hybrid retrieval: RRF fusion, reranker ordering, dense-only filtering, abstain gate, fallbacks."""
import pytest

from app.services import retriever, semantic, workflow


class FakeReranker:
    """Scores a section 0.95 if its doc_key is in `good`, else 0.05."""
    name = "fake"

    def __init__(self, good: set[str]):
        self.good = good

    def score(self, query, texts):
        return [0.95 if any(g in t for g in self.good) else 0.05 for t in texts]


@pytest.fixture
def hybrid(monkeypatch):
    monkeypatch.setenv("FAST_RETRIEVAL_MODE", "hybrid")
    yield monkeypatch
    semantic.reset()


def test_bm25_mode_rank_score_equals_score(monkeypatch):
    monkeypatch.setenv("FAST_RETRIEVAL_MODE", "bm25")
    hits = retriever.search("M10 bolt torque specification", ["quality"], top_k=4)
    assert hits and all(h["rank_score"] == h["score"] for h in hits)
    assert [h["score"] for h in hits] == sorted((h["score"] for h in hits), reverse=True)


def test_hybrid_fields_and_published_only(hybrid):
    hits = retriever.search("M10 bolt torque specification", ["quality"], top_k=10)
    assert hits and all(h["doc_id"] != "QC-TORQ-01-v2" for h in hits)
    for h in hits:
        assert {"score", "coverage", "dense", "rrf", "rerank", "rank_score"} <= h.keys()
    assert [h["rank_score"] for h in hits] == sorted((h["rank_score"] for h in hits), reverse=True)


def test_hybrid_category_scoped(hybrid):
    assert all(h["category"] == "quality" for h in retriever.search("spindle alarm", ["quality"], top_k=5))


def test_rrf_rewards_agreement(hybrid):
    """A section ranked well by both BM25 and dense beats one found by a single retriever."""
    hits = retriever.search("CNC-04 spindle overheating alarm", ["maintenance"], top_k=5)
    assert hits[0]["doc_key"] == "MM-CNC-04"
    assert hits[0]["rrf"] > 1 / 61           # contributions from both lists


def test_reranker_reorders_and_sets_rank_score(hybrid):
    hybrid.setattr(semantic, "get_reranker", lambda: FakeReranker({"Compressor"}))
    hits = retriever.search("air pressure dropping", ["maintenance"], top_k=4)
    assert hits[0]["doc_key"] == "MM-COMP-03" and hits[0]["rerank"] == 0.95
    assert all(h["rank_score"] == h["rerank"] for h in hits)


def test_dense_only_hits_need_reranker_confirmation(hybrid):
    hybrid.setattr(semantic, "get_reranker", lambda: FakeReranker(set()))
    hits = retriever.search("CNC-04 spindle overheating alarm", ["maintenance"], top_k=12)
    assert hits and all(h["score"] > 0 for h in hits)    # every rerank is 0.05 -> dense-only dropped


def test_category_scores_stay_lexical(hybrid):
    called = []
    hybrid.setattr(semantic, "get_embedder", lambda: called.append(1))
    assert retriever.category_scores("CNC-04 spindle alarm")["maintenance"] > 0
    assert not called


def test_abstain_gate_reranker_rescues_paraphrase():
    weak = {"score": 1.0, "coverage": 0.1, "rerank": 0.9, "rank_score": 0.9}
    assert workflow._abstain_reason([weak]) == ""
    assert workflow._abstain_reason([{**weak, "rerank": 0.1, "rank_score": 0.1}]) != ""
    assert workflow._abstain_reason([{"score": 1.0, "coverage": 0.1}]) != ""          # bm25 mode unchanged
    assert workflow._abstain_reason([{"score": 9.0, "coverage": 0.8}]) == ""


def test_embedder_falls_back_to_hash(monkeypatch):
    monkeypatch.setenv("FAST_EMBEDDER", "local")
    import builtins
    real_import = builtins.__import__

    def no_st(name, *a, **k):
        if name.startswith("sentence_transformers"):
            raise ImportError(name)
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", no_st)
    semantic.reset()
    try:
        assert isinstance(semantic.get_embedder(), semantic.HashEmbedder)
    finally:
        semantic.reset()


def test_hash_embedder_deterministic_and_normalised():
    e = semantic.HashEmbedder()
    a, b = e.embed_query("spindle overheating"), e.embed_query("spindle overheating")
    assert (a == b).all() and abs(float((a * a).sum()) - 1.0) < 1e-5
    assert float(a @ e.embed_query("spindle overheat")) > float(a @ e.embed_query("forklift battery"))


def test_end_to_end_ask_hybrid(hybrid):
    r = workflow.ask("What is the torque spec for M10 bolts?")
    assert r.status == "ok" and any(c.doc_key == "QC-TORQ-01" for c in r.citations)
