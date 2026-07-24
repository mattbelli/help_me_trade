"""Local, in-process memory embeddings — model2vec (static), fail-soft.

Phase 2 of the memory overhaul. Decided by measurement (07-24, see
memory-p2-embeddings-model2vec): a model2vec + FTS5 *hybrid* beat pure lexical
(93% vs 80% hit@4 on the golden set) while pure vectors did NOT — the value is
the fusion, not the embeddings alone. model2vec was picked over bge-small because
it tied on quality at a fraction of the cost (no onnxruntime, no torch; it rides
the numpy/scikit-learn the app already ships).

Everything here is **fail-soft**: if model2vec or the model weights are
unavailable (not installed, no network on first download, any load/encode error),
every function returns None and the caller falls back to pure lexical retrieval —
embeddings are never a hard dependency of memory create or recall.
"""

from __future__ import annotations

import logging
import os
import threading

import numpy as np

logger = logging.getLogger(__name__)

# Short id stored per row in memories.embedding_model, so a future model swap
# knows which rows to re-embed. Bump this (and backfill) if the model changes.
EMBED_MODEL_ID = "potion-base-8M"
_MODEL_REPO = "minishlab/potion-base-8M"
_DTYPE = np.float32

_model: object | None = None
_load_failed = False
# Serializes the one-time load so concurrent first-calls (e.g. two chat turns at
# once) can't race into a double from_pretrained. NOTE: the load is still
# synchronous on the first caller — deploy pre-caches the model (run the backfill
# tool before/at restart) so the first request reads a warm on-disk cache, never a
# cold network download. On any failure it latches off -> lexical.
_load_lock = threading.Lock()


def _enabled() -> bool:
    """Kill switch. Default ON in prod; the test suite sets this to '0' in
    conftest so the suite never downloads/loads the model (stays hermetic) and
    retrieval exercises the lexical fallback. Also lets prod disable embeddings
    without a redeploy."""
    return os.environ.get("MEMORY_EMBEDDINGS_ENABLED", "1") != "0"


def reset_cache() -> None:
    """Drop the cached model + failure latch. For the eval tool, which toggles the
    enable flag to measure lexical-vs-hybrid in one process. Not used at runtime."""
    global _model, _load_failed
    _model = None
    _load_failed = False


def _get_model() -> object | None:
    """Lazily load the static model once. On any failure, latch off and never
    retry (so a missing model can't cost a load attempt on every call). The
    enable flag is checked FIRST so it is always authoritative, even once cached."""
    global _model, _load_failed
    if not _enabled():
        return None
    if _model is not None:
        return _model
    if _load_failed:
        return None
    with _load_lock:
        # Re-check under the lock: a racing caller may have loaded (or latched) it.
        if _model is not None:
            return _model
        if _load_failed:
            return None
        try:
            from model2vec import StaticModel

            _model = StaticModel.from_pretrained(_MODEL_REPO)
            logger.info("memory embeddings: loaded %s", _MODEL_REPO)
            return _model
        except Exception:
            _load_failed = True
            logger.warning(
                "memory embeddings unavailable (model2vec/model load failed) — "
                "retrieval falls back to lexical",
                exc_info=True,
            )
            return None


def is_available() -> bool:
    return _get_model() is not None


def _encode(texts: list[str]) -> np.ndarray | None:
    model = _get_model()
    if model is None or not texts:
        return None
    try:
        vecs = np.asarray(model.encode(texts), dtype=_DTYPE)  # type: ignore[attr-defined]
        if vecs.ndim != 2 or vecs.shape[0] != len(texts):
            return None
        return vecs
    except Exception:
        logger.debug("memory embedding encode failed", exc_info=True)
        return None


def embed_documents(texts: list[str]) -> list[bytes] | None:
    """Batch-embed for the write path / backfill. Returns one float32 blob per
    text (same order), or None if embeddings are unavailable."""
    vecs = _encode(texts)
    if vecs is None:
        return None
    return [v.tobytes() for v in vecs]


def embed_document(text: str) -> bytes | None:
    """One document → its float32 blob, or None (fail-soft)."""
    blobs = embed_documents([text])
    return blobs[0] if blobs else None


def embed_query(text: str) -> np.ndarray | None:
    """Query text → its float32 vector, or None (fail-soft). model2vec is
    symmetric (no separate query prefix), so this mirrors document encoding."""
    vecs = _encode([text])
    return vecs[0] if vecs is not None else None


def decode(blob: bytes | None) -> np.ndarray | None:
    """Stored blob → vector. None/empty/corrupt → None (skip that row)."""
    if not blob:
        return None
    try:
        vec = np.frombuffer(blob, dtype=_DTYPE)
        return vec if vec.size else None
    except Exception:
        return None
