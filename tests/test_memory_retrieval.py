"""Semantic-style memory retrieval scoring."""

from __future__ import annotations

from ai.memories.retrieval import retrieve_relevant_memories, score_memory_relevance


def test_score_memory_relevance_token_overlap() -> None:
    score = score_memory_relevance(
        "NVDA concentration risk limit",
        "I cap single stock exposure at 20% and avoid NVDA above 15%.",
    )
    assert score > 0.2


def test_retrieve_relevant_memories_ranks_best_match(
    monkeypatch,
) -> None:
    memories = [
        {
            "id": 1,
            "type": "preference",
            "content": "Keep cash above 10% for dry powder.",
            "created_at": "2026-01-01T00:00:00+00:00",
        },
        {
            "id": 2,
            "type": "lesson",
            "content": "Never add to NVDA above 25% portfolio weight.",
            "created_at": "2026-01-02T00:00:00+00:00",
        },
    ]

    def fake_search(query: str, *, memory_type=None, limit=10, db_path=None):
        return memories

    def fake_list(*, memory_type=None, limit=50, db_path=None):
        return memories

    monkeypatch.setattr("ai.memories.retrieval.search_memories", fake_search)
    monkeypatch.setattr("ai.memories.retrieval.list_memories", fake_list)

    results = retrieve_relevant_memories("NVDA weight limit", limit=2)
    assert results
    assert results[0]["id"] == 2
    assert results[0].get("score", 0) > 0


_MEMS = [
    {
        "id": 1,
        "type": "directive",
        "content": "Cap any single name at 20% of the portfolio.",
        "created_at": "2026-01-01T00:00:00+00:00",
    },
    {
        "id": 2,
        "type": "thesis",
        "content": "SLB oversold on oil capex fears.",
        "created_at": "2026-01-02T00:00:00+00:00",
    },
]


def _patch_candidates(monkeypatch) -> None:
    from ai.memories import retrieval

    monkeypatch.setattr(
        retrieval,
        "search_memories",
        lambda q, *, memory_type=None, limit=10, db_path=None: list(_MEMS),
    )
    monkeypatch.setattr(
        retrieval,
        "list_memories",
        lambda *, memory_type=None, limit=50, db_path=None: list(_MEMS),
    )


def test_hybrid_surfaces_paraphrase_via_embeddings(monkeypatch) -> None:
    """The embedding leg rescues a memory with ZERO keyword overlap — the whole
    point of the hybrid. 'keep positions small' shares no tokens with either row,
    so lexical is blind; the injected vectors put #1 (the 20% cap) nearest."""
    import numpy as np

    from ai.memories import retrieval

    _patch_candidates(monkeypatch)
    monkeypatch.setattr(retrieval, "embed_query", lambda text: np.array([0.9, 0.1]))
    monkeypatch.setattr(
        retrieval,
        "fetch_memory_embeddings",
        lambda ids, *, model=None, db_path=None: {1: b"a", 2: b"b"},
    )
    vecs = {b"a": np.array([1.0, 0.0]), b"b": np.array([0.0, 1.0])}
    monkeypatch.setattr(retrieval, "decode", lambda blob: vecs[blob])

    out = retrieval.retrieve_relevant_memories("keep positions small", limit=2)
    assert out and out[0]["id"] == 1


def test_embedding_leg_failure_falls_back_to_lexical(monkeypatch) -> None:
    """A broken embedder must never break retrieval — it degrades to lexical."""
    from ai.memories import retrieval

    _patch_candidates(monkeypatch)

    def boom(_text):
        raise RuntimeError("embedder down")

    monkeypatch.setattr(retrieval, "embed_query", boom)

    # 'single name cap' lexically matches #1; the crash in the vector leg must not
    # prevent lexical from ranking it first.
    out = retrieval.retrieve_relevant_memories("single name cap", limit=2)
    assert out and out[0]["id"] == 1


def test_create_memory_stores_and_filters_embedding(monkeypatch, tmp_path) -> None:
    """create_memory persists the embedding blob + model; fetch filters by model."""
    import db.ai_tables as ai_tables

    db = tmp_path / "mem.db"
    monkeypatch.setattr(
        ai_tables, "_compute_embedding", lambda text: (b"\x00\x00\x80?", "potion-base-8M")
    )
    row = ai_tables.create_memory(memory_type="fact", content="anything", db_path=db)

    got = ai_tables.fetch_memory_embeddings(
        [row["id"]], model="potion-base-8M", db_path=db
    )
    assert got.get(row["id"]) == b"\x00\x00\x80?"
    # a different model id must not return the row (never mix vector spaces)
    assert ai_tables.fetch_memory_embeddings(
        [row["id"]], model="other-model", db_path=db
    ) == {}


def test_hybrid_end_to_end_through_real_db(monkeypatch, tmp_path) -> None:
    """The full seam: create_memory stores blobs, and retrieve_relevant_memories
    reads them back via the REAL fetch_memory_embeddings + decode (only embed_query
    is faked). A zero-keyword paraphrase must surface through the DB round-trip."""
    import numpy as np

    import db.ai_tables as ai_tables
    import db.base as db_base
    from ai.memories import retrieval

    db = tmp_path / "e2e.db"
    monkeypatch.setattr(db_base, "DEFAULT_DB_PATH", db)

    def fake_compute(text: str):
        # key only on '20%' (note 'cap' would also match 'capex' — a fixture trap)
        vec = np.array(
            [1.0, 0.0] if "20%" in text.lower() else [0.0, 1.0], dtype=np.float32
        )
        return vec.tobytes(), "potion-base-8M"

    monkeypatch.setattr(ai_tables, "_compute_embedding", fake_compute)
    ai_tables.create_memory(
        memory_type="directive", content="Cap any single name at 20%.", db_path=db
    )
    ai_tables.create_memory(
        memory_type="thesis", content="SLB oversold on oil capex.", db_path=db
    )

    # 'keep positions small' shares no tokens with the 20% cap; only the vector leg
    # (through the real DB fetch + np.frombuffer decode) can surface it.
    monkeypatch.setattr(retrieval, "embed_query", lambda _t: np.array([0.9, 0.1]))
    out = retrieval.retrieve_relevant_memories("keep positions small", limit=2)
    assert out and "20%" in out[0]["content"]


def test_fallback_is_lexical_when_embeddings_off(monkeypatch) -> None:
    """embed_query None (embeddings disabled) -> pure lexical ranking + lexical score."""
    from ai.memories import retrieval

    _patch_candidates(monkeypatch)
    monkeypatch.setattr(retrieval, "embed_query", lambda _t: None)

    out = retrieval.retrieve_relevant_memories("single name cap portfolio", limit=2)
    assert out and out[0]["id"] == 1
    assert isinstance(out[0].get("score"), float) and out[0]["score"] > 0


def test_search_memories_respects_dynamic_default_db(monkeypatch, tmp_path) -> None:
    """Regression for the eval-gate live-DB leak (review B, finding 1): search_memories
    must resolve DEFAULT_DB_PATH at CALL time, not freeze it at import — otherwise a
    rebind (memory_eval._fresh_db) leaks reads to the live DB."""
    import db.ai_tables as ai_tables
    import db.base as db_base

    db = tmp_path / "dyn.db"
    monkeypatch.setattr(db_base, "DEFAULT_DB_PATH", db)
    ai_tables.create_memory(
        memory_type="fact", content="unique canary token zebra", db_path=db
    )
    # no db_path passed -> must read the rebound default (temp), find the canary
    hits = ai_tables.search_memories("zebra")
    assert any("zebra" in h["content"] for h in hits)
