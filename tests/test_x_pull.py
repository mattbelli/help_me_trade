"""Tests for research.x_pull — synthetic, in-memory, no network."""

from __future__ import annotations

import json
from pathlib import Path

from research.x_pull import merge_tweets, save_tweets


def test_merge_tweets_fetched_wins_and_sorts_by_created_at():
    existing = [
        {"id": "A", "created_at": "2025-06-01T10:00:00+00:00", "text": "old A"},
        {"id": "B", "created_at": "2025-06-02T10:00:00+00:00", "text": "old B"},
    ]
    fetched = [
        {"id": "B", "created_at": "2025-06-02T11:00:00+00:00", "text": "fresh B"},
        {"id": "C", "created_at": "2025-06-03T10:00:00+00:00", "text": "new C"},
    ]
    merged = merge_tweets(existing, fetched)
    assert [tweet["id"] for tweet in merged] == ["A", "B", "C"]
    assert merged[1]["text"] == "fresh B"


def test_merge_tweets_empty_existing_returns_fetched():
    fetched = [{"id": "1", "created_at": "2025-06-01T10:00:00+00:00", "text": "only"}]
    assert merge_tweets([], fetched) == fetched


def test_save_tweets_merges_existing_file(tmp_path: Path):
    user_dir = tmp_path / "adaamset"
    user_dir.mkdir()
    existing = [{"id": "old", "created_at": "2025-06-01T10:00:00+00:00", "text": "keep me"}]
    (user_dir / "tweets.json").write_text(json.dumps(existing), encoding="utf-8")

    fetched = [
        {
            "id": "new",
            "created_at": "2025-06-02T10:00:00+00:00",
            "text": "hello",
            "_media": [],
        }
    ]
    merged = save_tweets(user_dir, fetched, {})
    assert len(merged) == 2
    on_disk = json.loads((user_dir / "tweets.json").read_text(encoding="utf-8"))
    assert {tweet["id"] for tweet in on_disk} == {"old", "new"}
    assert (user_dir / "tweets.md").exists()
