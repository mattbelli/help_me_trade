"""Symbol-tagged news store (Alpaca/Benzinga) — perception substrate, Mode A.

Fed by the realtime news websocket (``signals/news_stream.py``) with REST
backfill; read by the ticker/watchlist news endpoints and the ``news``
perception resource. Facts about published headlines — never a signal.

Bus-ready by construction: rows carry a monotonic ``ingest_seq`` per the
evidence-bus cursor contract (FUSION_EVIDENCE_BUS_SPEC §2.1 — TEXT ids +
upsert make neither ids nor rowids stable cursors; the seq is allocated once
at first insert and PRESERVED on update). The catalyst-dot wiring itself is a
separate, founder-visible round (spec §2.9, one component at a time).
"""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any

from db.base import DEFAULT_DB_PATH
from db.base import connect as _connect
from db.base import now_iso as _now

_RECENT_CAP = 250


def _db_path(db_path: Path | None) -> Path:
    return db_path if db_path is not None else DEFAULT_DB_PATH


def init_db(db_path: Path | None = None) -> None:
    resolved = _db_path(db_path)
    with _connect(resolved) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS news_items (
                id TEXT PRIMARY KEY,
                ingest_seq INTEGER,
                headline TEXT NOT NULL,
                summary TEXT,
                url TEXT,
                source TEXT,
                author TEXT,
                symbols TEXT NOT NULL DEFAULT '[]',
                image_url TEXT,
                published_at TEXT NOT NULL,
                updated_at TEXT,
                received_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_news_published
                ON news_items (published_at DESC);
            CREATE TABLE IF NOT EXISTS news_symbols (
                news_id TEXT NOT NULL,
                symbol TEXT NOT NULL,
                PRIMARY KEY (news_id, symbol)
            );
            CREATE INDEX IF NOT EXISTS idx_news_symbols_symbol
                ON news_symbols (symbol, news_id);
            CREATE TABLE IF NOT EXISTS bus_ingest_sequences (
                store TEXT PRIMARY KEY,
                next_seq INTEGER NOT NULL
            );
            """
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO bus_ingest_sequences (store, next_seq)
            VALUES ('news', 1)
            """
        )


def normalize_news_item(raw: dict[str, Any]) -> dict[str, Any] | None:
    """Alpaca news payload (REST item or websocket ``T:"n"`` message) → row dict.

    Returns None for items with no id or headline — nothing worth storing.
    """
    if not isinstance(raw, dict):
        return None
    raw_id = raw.get("id")
    # Benzinga ships headlines HTML-escaped (``&#39;`` / ``&amp;``) — ~12% of
    # stored rows measured; decode once at the write side so every consumer
    # (UI, agents, fusion) sees plain text.
    headline = html.unescape(str(raw.get("headline") or "")).strip()
    if raw_id is None or not headline:
        return None
    symbols = sorted(
        {
            str(s).strip().upper()
            for s in (raw.get("symbols") or [])
            if isinstance(s, str) and s.strip()
        }
    )
    image_url = None
    for img in raw.get("images") or []:
        if isinstance(img, dict) and img.get("url"):
            image_url = str(img["url"])
            if img.get("size") == "small":
                break
    return {
        "id": str(raw_id),
        "headline": headline,
        "summary": html.unescape(str(raw.get("summary") or "")).strip(),
        "url": str(raw.get("url") or "").strip(),
        "source": str(raw.get("source") or "").strip(),
        "author": str(raw.get("author") or "").strip(),
        "symbols": symbols,
        "image_url": image_url,
        "published_at": str(raw.get("created_at") or "").strip() or _now(),
        "updated_at": str(raw.get("updated_at") or "").strip() or None,
    }


def upsert_news_item(item: dict[str, Any], db_path: Path | None = None) -> None:
    """Insert-or-update one normalized item; ``ingest_seq`` sticks to first insert."""
    resolved = _db_path(db_path)
    with _connect(resolved) as conn:
        conn.execute("BEGIN IMMEDIATE")
        existing = conn.execute(
            "SELECT ingest_seq FROM news_items WHERE id = ?", (item["id"],)
        ).fetchone()
        if existing is None:
            seq_row = conn.execute(
                """
                UPDATE bus_ingest_sequences
                SET next_seq = next_seq + 1
                WHERE store = 'news'
                RETURNING next_seq - 1
                """
            ).fetchone()
            ingest_seq = int(seq_row[0])
        else:
            ingest_seq = int(existing["ingest_seq"])
        conn.execute(
            """
            INSERT INTO news_items (
                id, ingest_seq, headline, summary, url, source, author,
                symbols, image_url, published_at, updated_at, received_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                headline = excluded.headline,
                summary = excluded.summary,
                url = excluded.url,
                source = excluded.source,
                author = excluded.author,
                symbols = excluded.symbols,
                image_url = excluded.image_url,
                published_at = excluded.published_at,
                updated_at = excluded.updated_at
            """,
            (
                item["id"],
                ingest_seq,
                item["headline"],
                item.get("summary"),
                item.get("url"),
                item.get("source"),
                item.get("author"),
                json.dumps(item.get("symbols") or []),
                item.get("image_url"),
                item["published_at"],
                item.get("updated_at"),
                _now(),
            ),
        )
        conn.execute(
            "DELETE FROM news_symbols WHERE news_id = ?", (item["id"],)
        )
        conn.executemany(
            "INSERT OR IGNORE INTO news_symbols (news_id, symbol) VALUES (?, ?)",
            [(item["id"], s) for s in item.get("symbols") or []],
        )


def _row_to_item(row: Any) -> dict[str, Any]:
    return {
        "id": row["id"],
        "headline": row["headline"],
        "summary": row["summary"] or "",
        "url": row["url"] or "",
        "source": row["source"] or "",
        "author": row["author"] or "",
        "symbols": json.loads(row["symbols"] or "[]"),
        "image_url": row["image_url"],
        "published_at": row["published_at"],
        "updated_at": row["updated_at"],
    }


def recent_news(
    *,
    symbol: str | None = None,
    symbols: list[str] | None = None,
    since: str | None = None,
    limit: int = 50,
    db_path: Path | None = None,
) -> list[dict[str, Any]]:
    """Newest-first news rows, optionally filtered to one symbol or a set."""
    resolved = _db_path(db_path)
    capped = max(1, min(int(limit), _RECENT_CAP))
    wanted: list[str] = []
    if symbol:
        wanted = [symbol.strip().upper()]
    elif symbols:
        wanted = sorted({s.strip().upper() for s in symbols if s and s.strip()})

    query = "SELECT DISTINCT n.* FROM news_items n"
    params: list[Any] = []
    where: list[str] = []
    if wanted:
        placeholders = ",".join("?" for _ in wanted)
        query += " JOIN news_symbols ns ON ns.news_id = n.id"
        where.append(f"ns.symbol IN ({placeholders})")
        params.extend(wanted)
    if since:
        where.append("n.published_at >= ?")
        params.append(since)
    if where:
        query += " WHERE " + " AND ".join(where)
    query += " ORDER BY n.published_at DESC LIMIT ?"
    params.append(capped)

    with _connect(resolved) as conn:
        rows = conn.execute(query, params).fetchall()
    return [_row_to_item(r) for r in rows]
