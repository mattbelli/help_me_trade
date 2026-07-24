"""Relevance-ranked memory retrieval — FTS5 lexical + model2vec embedding hybrid.

Phase 2 (2026-07-24): a Reciprocal-Rank-Fusion of the lexical token-overlap ranking
and a model2vec cosine ranking. Measured on the golden set, the *hybrid* beat pure
lexical (93% vs 80% hit@4) while embeddings ALONE did not — so the lexical path is
preserved exactly and the vector leg only re-ranks on top of it. Fully **fail-soft**:
no embedder / disabled / no stored embeddings -> the original lexical behaviour,
byte-for-byte unchanged. See memory-p2-embeddings-model2vec.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import numpy as np

from ai.memories.embedding import EMBED_MODEL_ID, decode, embed_query
from db.ai_tables import (
    expire_stale_memories,
    fetch_memory_embeddings,
    list_memories,
    search_memories,
)

logger = logging.getLogger(__name__)

_TOKEN_RE = re.compile(r"[a-z0-9]{3,}")
_RRF_K = 60  # standard Reciprocal-Rank-Fusion damping constant


def _tokenize(text: str) -> set[str]:
    return set(_TOKEN_RE.findall(text.lower()))


def score_memory_relevance(query: str, content: str) -> float:
    """Token overlap score in [0, 1+] — higher is more relevant."""
    q_tokens = _tokenize(query)
    if not q_tokens:
        return 0.0
    c_tokens = _tokenize(content)
    if not c_tokens:
        return 0.0
    overlap = len(q_tokens & c_tokens)
    score = overlap / len(q_tokens)
    lowered_query = query.strip().lower()
    lowered_content = content.lower()
    if lowered_query and lowered_query in lowered_content:
        score += 0.35
    return score


def _candidates(
    query: str, memory_type: str | None, lim: int
) -> list[dict[str, Any]]:
    """FTS5 candidates, topped up from recent rows — the universe both legs rank."""
    candidates = search_memories(query, memory_type=memory_type, limit=lim * 8)
    if len(candidates) < lim:
        seen = {item["id"] for item in candidates}
        for item in list_memories(memory_type=memory_type, limit=lim * 6):
            if item["id"] not in seen:
                candidates.append(item)
                seen.add(item["id"])
    return candidates


def _lexical_scored(
    query: str, candidates: list[dict[str, Any]]
) -> list[tuple[float, dict[str, Any]]]:
    """(score, item) for candidates with score > 0, sorted desc — the current behaviour."""
    scored: list[tuple[float, dict[str, Any]]] = []
    for item in candidates:
        rel = score_memory_relevance(query, str(item.get("content", "")))
        if rel > 0:
            scored.append((rel, item))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return scored


_EMBED_POOL_MAX = 200  # active rows the vector leg ranks over (bounded; tiny corpus today)


def _embedding_order(
    query: str, memory_type: str | None
) -> tuple[list[int], dict[int, dict[str, Any]]] | None:
    """(ids ranked by centered model2vec cosine, id->memory for those rows), or None
    if the vector leg is unavailable/insufficient. Ranks over the whole active POOL
    (not just FTS5 candidates) so a zero-keyword paraphrase can surface. Guarded —
    never raises."""
    try:
        qvec = embed_query(query)
        if qvec is None:
            return None
        pool = list_memories(memory_type=memory_type, limit=_EMBED_POOL_MAX)
        if not pool:
            return None
        pool_by_id = {int(m["id"]): m for m in pool}
        ids = list(pool_by_id)
        blobs = fetch_memory_embeddings(ids, model=EMBED_MODEL_ID)
        if not blobs:
            return None
        vecs: dict[int, np.ndarray] = {}
        for cid, blob in blobs.items():
            v = decode(blob)
            if v is not None and v.shape[0] == qvec.shape[0]:
                vecs[cid] = v
        if not vecs:
            return None
        emb_ids = [cid for cid in ids if cid in vecs]
        matrix = np.array([vecs[cid] for cid in emb_ids], dtype=np.float64)
        q = np.asarray(qvec, dtype=np.float64)
        # Center on the candidate mean (kills model2vec's mild anisotropy;
        # matches the validated bake-off config). RRF is robust to this choice.
        mu = matrix.mean(axis=0)
        m_c = matrix - mu
        q_c = q - mu
        m_n = m_c / (np.linalg.norm(m_c, axis=1, keepdims=True) + 1e-12)
        q_n = q_c / (np.linalg.norm(q_c) + 1e-12)
        sims = m_n @ q_n
        order = np.argsort(-sims, kind="stable")
        return [emb_ids[i] for i in order], pool_by_id
    except Exception:
        logger.debug("hybrid embedding leg failed; lexical fallback", exc_info=True)
        return None


def _rrf(*orders: list[int], k: int = _RRF_K) -> dict[int, float]:
    """Reciprocal-Rank-Fusion score per id across the given ranked-id lists."""
    score: dict[int, float] = {}
    for order in orders:
        for rank, cid in enumerate(order):
            score[cid] = score.get(cid, 0.0) + 1.0 / (k + rank)
    return score


def retrieve_relevant_memories(
    query: str,
    *,
    memory_type: str | None = None,
    limit: int = 5,
) -> list[dict[str, Any]]:
    """Top-k memories by relevance. Hybrid (FTS5 + model2vec RRF) when embeddings
    are available; the original lexical ranking otherwise. Empty query -> recent items."""
    lim = max(1, min(limit, 20))
    # Retire any memory whose valid_until has passed BEFORE reading — a stale thesis
    # must never rank into a propose-order-capable prompt. Flips status to 'expired'
    # once (every read below already filters status='active'); fail-soft, so a sweep
    # error degrades to "nothing expired this turn", never a broken retrieval.
    expire_stale_memories()
    q = query.strip()
    if not q:
        return list_memories(memory_type=memory_type, limit=lim)

    candidates = _candidates(q, memory_type, lim)
    lexical = _lexical_scored(q, candidates)

    emb = _embedding_order(q, memory_type)
    if emb is not None:
        # HYBRID: fuse the lexical rank and the embedding rank (RRF). A row matched
        # only by the vector leg (no keyword overlap — the paraphrase case) still
        # earns rank here; one matched only lexically keeps its lexical rank.
        emb_order, pool_by_id = emb
        lex_order = [int(item["id"]) for _, item in lexical]
        fused = _rrf(lex_order, emb_order)
        by_id = {**pool_by_id, **{int(c["id"]): c for c in candidates}}
        ranked = sorted(fused, key=lambda cid: fused[cid], reverse=True)
        out = [
            {**by_id[cid], "score": round(fused[cid], 4)}
            for cid in ranked
            if cid in by_id
        ]
        if out:
            return out[:lim]

    # --- fail-soft: the original lexical behaviour, unchanged ---
    if lexical:
        return [{**item, "score": round(rel, 3)} for rel, item in lexical][:lim]
    return candidates[:lim]
