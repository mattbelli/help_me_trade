"""db.news store — normalizer, seq-stable upsert, and query semantics."""

from __future__ import annotations

from pathlib import Path

from db.news import init_db, normalize_news_item, recent_news, upsert_news_item


def _db(tmp_path: Path) -> Path:
    p = tmp_path / "news.db"
    init_db(p)
    return p


def test_normalize_maps_alpaca_payload() -> None:
    item = normalize_news_item(
        {
            "id": 60622342,
            "headline": "Chips retreat",
            "summary": "sum",
            "author": "A. Writer",
            "source": "benzinga",
            "url": "https://bz/1",
            "created_at": "2026-07-22T19:52:19Z",
            "updated_at": "2026-07-22T19:53:44Z",
            "symbols": ["tsla", "AAPL", "AAPL", " qqq "],
            "images": [
                {"size": "large", "url": "https://img/large"},
                {"size": "small", "url": "https://img/small"},
                {"size": "thumb", "url": "https://img/thumb"},
            ],
        }
    )
    assert item is not None
    assert item["id"] == "60622342"
    assert item["symbols"] == ["AAPL", "QQQ", "TSLA"]
    assert item["image_url"] == "https://img/small"
    assert item["published_at"] == "2026-07-22T19:52:19Z"


def test_normalize_rejects_headline_less_items() -> None:
    assert normalize_news_item({"id": 1, "headline": "  "}) is None
    assert normalize_news_item({"headline": "no id"}) is None
    assert normalize_news_item("not a dict") is None


def test_upsert_preserves_ingest_seq_and_rebuilds_symbols(tmp_path: Path) -> None:
    db = _db(tmp_path)
    upsert_news_item(
        {"id": "n1", "headline": "v1", "symbols": ["AAPL"], "published_at": "2026-07-22T10:00:00Z"},
        db_path=db,
    )
    upsert_news_item(
        {"id": "n2", "headline": "other", "symbols": ["MSFT"], "published_at": "2026-07-22T11:00:00Z"},
        db_path=db,
    )
    # Update n1: headline revised, symbol set changed.
    upsert_news_item(
        {"id": "n1", "headline": "v2", "symbols": ["TSLA"], "published_at": "2026-07-22T10:00:00Z"},
        db_path=db,
    )

    rows = recent_news(limit=10, db_path=db)
    by_id = {r["id"]: r for r in rows}
    assert by_id["n1"]["headline"] == "v2"
    assert by_id["n1"]["symbols"] == ["TSLA"]
    # The old symbol no longer matches n1...
    assert [r["id"] for r in recent_news(symbol="AAPL", db_path=db)] == []
    # ...and the new one does.
    assert [r["id"] for r in recent_news(symbol="TSLA", db_path=db)] == ["n1"]

    # Seq is allocated once and survives the update (bus cursor contract).
    import sqlite3

    conn = sqlite3.connect(db)
    seqs = dict(conn.execute("SELECT id, ingest_seq FROM news_items").fetchall())
    conn.close()
    assert seqs["n1"] == 1
    assert seqs["n2"] == 2


def test_recent_news_filters_and_orders(tmp_path: Path) -> None:
    db = _db(tmp_path)
    for i, (ts, syms) in enumerate(
        [
            ("2026-07-20T10:00:00Z", ["AAPL"]),
            ("2026-07-21T10:00:00Z", ["AAPL", "MSFT"]),
            ("2026-07-22T10:00:00Z", ["MSFT"]),
        ]
    ):
        upsert_news_item(
            {"id": f"s{i}", "headline": f"h{i}", "symbols": syms, "published_at": ts},
            db_path=db,
        )

    # Multi-symbol query returns each story ONCE, newest first.
    rows = recent_news(symbols=["AAPL", "MSFT"], db_path=db)
    assert [r["id"] for r in rows] == ["s2", "s1", "s0"]
    # since filter.
    rows = recent_news(symbols=["AAPL", "MSFT"], since="2026-07-21T00:00:00Z", db_path=db)
    assert [r["id"] for r in rows] == ["s2", "s1"]
    # limit.
    assert len(recent_news(symbols=["AAPL", "MSFT"], limit=1, db_path=db)) == 1
    # unfiltered = whole feed.
    assert len(recent_news(db_path=db)) == 3
