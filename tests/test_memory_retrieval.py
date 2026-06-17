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
