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
    monkeypatch.setattr("ai.memories.retrieval.expire_stale_memories", lambda **k: 0)

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
    monkeypatch.setattr(retrieval, "expire_stale_memories", lambda **k: 0)


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


# --- P3 lifecycle: valid_until enforcement --------------------------------------


def test_parse_valid_until_handles_mixed_formats() -> None:
    """Free-form model-emitted valid_until parses defensively; garbage -> None."""
    from datetime import UTC, datetime

    from db.ai_tables import _parse_valid_until

    # date-only is 'valid through end of that day' -> expiry is next midnight UTC
    assert _parse_valid_until("2026-08-01") == datetime(2026, 8, 2, tzinfo=UTC)
    # full ISO datetime, naive -> assumed UTC
    assert _parse_valid_until("2026-08-01T12:30:00") == datetime(
        2026, 8, 1, 12, 30, tzinfo=UTC
    )
    # 'Z' suffix is honored
    assert _parse_valid_until("2026-08-01T00:00:00Z") == datetime(
        2026, 8, 1, tzinfo=UTC
    )
    # explicit offset is normalized to UTC
    assert _parse_valid_until("2026-08-01T00:00:00-04:00") == datetime(
        2026, 8, 1, 4, 0, tzinfo=UTC
    )
    # unparseable / empty -> None (never expire on garbage)
    assert _parse_valid_until("someday") is None
    assert _parse_valid_until("") is None
    assert _parse_valid_until("   ") is None


def test_is_memory_expired_only_when_past() -> None:
    from datetime import UTC, datetime

    from db.ai_tables import is_memory_expired

    now = datetime(2026, 7, 24, tzinfo=UTC)
    assert is_memory_expired({"valid_until": "2020-01-01"}, now=now) is True
    assert is_memory_expired({"valid_until": "2999-01-01"}, now=now) is False
    # no valid_until, or unparseable -> never expired
    assert is_memory_expired({}, now=now) is False
    assert is_memory_expired({"valid_until": "whenever"}, now=now) is False


def test_expire_stale_memories_flips_only_past_rows(monkeypatch, tmp_path) -> None:
    """The sweep flips ACTIVE rows past valid_until to 'expired' and leaves future +
    unparseable + no-valid_until rows active. Idempotent (a 2nd run flips 0)."""
    import db.ai_tables as ai_tables
    import db.base as db_base

    db = tmp_path / "expire.db"
    monkeypatch.setattr(db_base, "DEFAULT_DB_PATH", db)
    monkeypatch.setattr(ai_tables, "_compute_embedding", lambda text: (None, None))

    past = ai_tables.create_memory(
        memory_type="thesis", content="SLB oversold", valid_until="2020-01-01", db_path=db
    )
    future = ai_tables.create_memory(
        memory_type="thesis", content="oil rally into winter", valid_until="2999-01-01",
        db_path=db,
    )
    junk = ai_tables.create_memory(
        memory_type="thesis", content="watch rates", valid_until="someday", db_path=db
    )
    forever = ai_tables.create_memory(
        memory_type="directive", content="cap any single name at 20%", db_path=db
    )

    flipped = ai_tables.expire_stale_memories(db_path=db)
    assert flipped == 1
    assert ai_tables.get_memory(past["id"], db_path=db)["status"] == "expired"
    assert ai_tables.get_memory(future["id"], db_path=db)["status"] == "active"
    assert ai_tables.get_memory(junk["id"], db_path=db)["status"] == "active"
    assert ai_tables.get_memory(forever["id"], db_path=db)["status"] == "active"
    # the expired row drops out of the active-only read surfaces
    active_ids = {m["id"] for m in ai_tables.list_memories(db_path=db)}
    assert past["id"] not in active_ids
    # idempotent
    assert ai_tables.expire_stale_memories(db_path=db) == 0


def test_retrieval_sweeps_expired_before_reading(monkeypatch, tmp_path) -> None:
    """End-to-end: a stale thesis in the DB is gone from retrieve_relevant_memories AND
    flipped to 'expired' in the DB — the sweep runs on the read path (embeddings off)."""
    import db.ai_tables as ai_tables
    import db.base as db_base

    db = tmp_path / "sweep.db"
    monkeypatch.setattr(db_base, "DEFAULT_DB_PATH", db)
    monkeypatch.setattr(ai_tables, "_compute_embedding", lambda text: (None, None))

    stale = ai_tables.create_memory(
        memory_type="thesis",
        content="SLB oversold on oil capex fears",
        valid_until="2020-01-01",
        db_path=db,
    )
    from ai.memories.retrieval import retrieve_relevant_memories

    out = retrieve_relevant_memories("SLB oil capex")
    assert all(m["id"] != stale["id"] for m in out)
    assert ai_tables.get_memory(stale["id"], db_path=db)["status"] == "expired"


def test_injection_fence_drops_expired_even_if_still_active(monkeypatch) -> None:
    """Belt-and-suspenders: if a past-valid_until row reaches the fence still marked
    active (sweep hasn't landed), turn_memory_context must still refuse to inject it."""
    from ai.chat import context

    rows = [
        {
            "id": 1,
            "type": "directive",
            "content": "cap any single name at 20%",
            "authority": "user_authored",
            "status": "active",
        },
        {
            "id": 2,
            "type": "thesis",
            "content": "SLB oversold on oil capex fears",
            "authority": "user_confirmed",
            "status": "active",
            "valid_until": "2020-01-01",
        },
    ]
    monkeypatch.setattr(context, "retrieve_relevant_memories", lambda *a, **k: rows)

    out = context.turn_memory_context("what do we think about SLB?", limit=5)
    ids = {m["id"] for m in out}
    assert 1 in ids and 2 not in ids
