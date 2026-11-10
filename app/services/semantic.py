"""Semantic retrieval helpers: text embedder (dense search) and cross-encoder reranker.

Backends (config retrieval.embedder / retrieval.reranker, env FAST_EMBEDDER / FAST_RERANKER override):
  embedder  local   - sentence-transformers bi-encoder on CPU (default: BAAI/bge-small-en-v1.5)
            openai  - OpenAI embeddings API (uses OPENAI_API_KEY; one network call per query)
            hash    - offline hashed word + char-trigram vectors; deterministic, used by tests and as fallback
  reranker  cross_encoder - sentence-transformers cross-encoder (default: ms-marco-MiniLM-L-6-v2)
            none          - no second stage

If sentence-transformers (or the model download) is unavailable, the code falls back to hash / none and logs
it once, so the app never fails because of an optional model. Document embeddings are cached on disk next to
the DB, keyed by model + text hash, so restarts do not re-embed the corpus.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
import threading

import numpy as np

from app.config import db_path, get_config

log = logging.getLogger(__name__)
_lock = threading.Lock()
_embedder = None
_reranker = None
_HASH_DIM = 512
BGE_QUERY_PREFIX = "Represent this sentence for searching relevant passages: "


def _rcfg() -> dict:
    return get_config()["retrieval"]


def embedder_name() -> str:
    return (os.getenv("FAST_EMBEDDER") or _rcfg().get("embedder", "hash")).strip().lower()


def reranker_name() -> str:
    return (os.getenv("FAST_RERANKER") or _rcfg().get("reranker", "none")).strip().lower()


def reset() -> None:
    """Drop loaded models (tests / config changes)."""
    global _embedder, _reranker
    with _lock:
        _embedder = _reranker = None


# ------------------------------------------------------------------ embedders
class HashEmbedder:
    """Deterministic, dependency-free embedding: hashed words + character trigrams, L2-normalised."""
    name = "hash-v1"

    def _vec(self, text: str) -> np.ndarray:
        v = np.zeros(_HASH_DIM, dtype=np.float32)
        words = re.findall(r"[a-z0-9]+", text.lower())
        feats = words + [w[i:i + 3] for w in words if len(w) > 3 for i in range(len(w) - 2)]
        for f in feats:
            h = int.from_bytes(hashlib.md5(f.encode()).digest()[:4], "little")
            v[h % _HASH_DIM] += 1.0 if (h >> 31) & 1 else -1.0
        n = np.linalg.norm(v)
        return v / n if n else v

    def embed_docs(self, texts: list[str]) -> np.ndarray:
        return np.stack([self._vec(t) for t in texts]) if texts else np.zeros((0, _HASH_DIM), np.float32)

    def embed_query(self, text: str) -> np.ndarray:
        return self._vec(text)


class LocalEmbedder:
    def __init__(self, model: str):
        from sentence_transformers import SentenceTransformer  # optional dependency
        self.model = SentenceTransformer(model, device="cpu")
        self.name = model
        self.prefix = BGE_QUERY_PREFIX if "bge" in model.lower() else ""

    def embed_docs(self, texts: list[str]) -> np.ndarray:
        return np.asarray(self.model.encode(texts, batch_size=32, normalize_embeddings=True), dtype=np.float32)

    def embed_query(self, text: str) -> np.ndarray:
        return np.asarray(self.model.encode([self.prefix + text], normalize_embeddings=True)[0], dtype=np.float32)


class OpenAIEmbedder:
    def __init__(self, model: str):
        from openai import OpenAI
        self.client = OpenAI(timeout=get_config()["llm"]["timeout_s"])
        self.name = f"openai:{model}"
        self.model = model

    def _embed(self, texts: list[str]) -> np.ndarray:
        out = self.client.embeddings.create(model=self.model, input=texts)
        m = np.asarray([d.embedding for d in out.data], dtype=np.float32)
        return m / np.linalg.norm(m, axis=1, keepdims=True)

    def embed_docs(self, texts: list[str]) -> np.ndarray:
        return np.vstack([self._embed(texts[i:i + 100]) for i in range(0, len(texts), 100)])

    def embed_query(self, text: str) -> np.ndarray:
        return self._embed([text])[0]


def get_embedder():
    global _embedder
    with _lock:
        if _embedder is None:
            kind, rc = embedder_name(), _rcfg()
            try:
                if kind == "local":
                    _embedder = LocalEmbedder(rc.get("embed_model", "BAAI/bge-small-en-v1.5"))
                elif kind == "openai":
                    _embedder = OpenAIEmbedder(rc.get("openai_embed_model", "text-embedding-3-small"))
            except Exception as e:  # missing package, no network, bad key -> keep working offline
                log.warning("embedder %s unavailable (%s); falling back to hash", kind, type(e).__name__)
            _embedder = _embedder or HashEmbedder()
        return _embedder


# ------------------------------------------------------------------ doc-embedding cache
def _cache_file(model_name: str):
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", model_name)
    return db_path().parent / f"emb_cache_{safe}.json"


def embed_corpus(texts: list[str]) -> np.ndarray:
    """Embed document texts, reusing vectors cached on disk for unchanged text."""
    emb = get_embedder()
    if isinstance(emb, HashEmbedder):
        return emb.embed_docs(texts)
    f = _cache_file(emb.name)
    try:
        cache = json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
    except (OSError, ValueError):
        cache = {}
    keys = [hashlib.sha256(t.encode()).hexdigest()[:24] for t in texts]
    missing = [i for i, k in enumerate(keys) if k not in cache]
    if missing:
        new = emb.embed_docs([texts[i] for i in missing])
        for i, v in zip(missing, new):
            cache[keys[i]] = [round(float(x), 6) for x in v]
        live = set(keys)
        cache = {k: v for k, v in cache.items() if k in live}     # drop vectors of removed/edited sections
        try:
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(json.dumps(cache), encoding="utf-8")
        except OSError:
            pass
    return np.asarray([cache[k] for k in keys], dtype=np.float32)


# ------------------------------------------------------------------ reranker
class CrossEncoderReranker:
    def __init__(self, model: str):
        from sentence_transformers import CrossEncoder
        self.model = CrossEncoder(model, device="cpu", max_length=512)
        self.name = model

    def score(self, query: str, texts: list[str]) -> list[float]:
        if not texts:
            return []
        logits = self.model.predict([(query, t) for t in texts], batch_size=16, show_progress_bar=False)
        return [1.0 / (1.0 + math.exp(-float(x))) for x in np.ravel(logits)]   # probability of relevance


def get_reranker():
    """Returns a reranker or None (disabled / unavailable)."""
    global _reranker
    with _lock:
        if _reranker is None:
            _reranker = False
            if reranker_name() == "cross_encoder":
                try:
                    _reranker = CrossEncoderReranker(_rcfg().get("rerank_model", "cross-encoder/ms-marco-MiniLM-L-6-v2"))
                except Exception as e:
                    log.warning("reranker unavailable (%s); continuing without it", type(e).__name__)
        return _reranker or None
