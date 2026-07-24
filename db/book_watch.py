"""Book watch ledger — the intraday wakeup record for the LLM paper books.

One row per (book, market_day, symbol, trigger). The table IS the dedup and the
budget counter — never a parallel tally (the fusion_investigations pattern). A
row is written for EVERY trigger that matched, including the ones the budget
refused, so "the watcher is quiet" and "the watcher is broken" are different
numbers on the page.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from db.base import DEFAULT_DB_PATH, now_iso
from db.base import connect as _connect

# fired         — the wakeup ran and produced an analyst note
# budget_dropped— the trigger matched but the book's daily wakeup budget was spent
# stale_data    — the trigger matched on a snapshot too old to act on
DECISIONS = ("fired", "budget_dropped", "stale_data")


def init_book_watch_tables(db_path: Path = DEFAULT_DB_PATH) -> None:
    with _connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS book_watch_events (
                id TEXT PRIMARY KEY,
                book_id TEXT NOT NULL,
                market_day TEXT NOT NULL,
                symbol TEXT NOT NULL,
                trigger TEXT NOT NULL,
                decision TEXT NOT NULL
                    CHECK (decision IN ('fired', 'budget_dropped', 'stale_data')),
                detail TEXT NOT NULL DEFAULT '',
                evidence_json TEXT NOT NULL DEFAULT '{}',
                report_id TEXT,
                verdict TEXT,
                decided_at TEXT NOT NULL
            );
            -- The dedup: one wakeup per name per trigger per day. A second match
            -- of the same trigger is the SAME event still developing, not news.
            CREATE UNIQUE INDEX IF NOT EXISTS uq_book_watch_day
                ON book_watch_events (book_id, market_day, symbol, trigger);
            CREATE INDEX IF NOT EXISTS idx_book_watch_day
                ON book_watch_events (book_id, market_day);
            """
        )
        conn.commit()


def claim_watch_event(
    *,
    book_id: str,
    market_day: str,
    symbol: str,
    trigger: str,
    decision: str,
    detail: str = "",
    evidence: dict[str, Any] | None = None,
    db_path: Path = DEFAULT_DB_PATH,
) -> str | None:
    """Claim today's (book, symbol, trigger) slot. Returns the new event id, or
    None when this trigger already fired for this name today (the unique index
    is the dedup — no read-then-write race)."""
    if decision not in DECISIONS:
        raise ValueError(f"unknown watch decision {decision!r}")
    init_book_watch_tables(db_path)
    event_id = f"bw_{uuid.uuid4().hex[:16]}"
    with _connect(db_path) as conn:
        cur = conn.execute(
            """
            INSERT INTO book_watch_events (
                id, book_id, market_day, symbol, trigger, decision,
                detail, evidence_json, report_id, verdict, decided_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?)
            ON CONFLICT (book_id, market_day, symbol, trigger) DO NOTHING
            """,
            (
                event_id,
                book_id,
                market_day,
                symbol.strip().upper(),
                trigger,
                decision,
                detail[:500],
                json.dumps(evidence or {}, default=str),
                now_iso(),
            ),
        )
        conn.commit()
        return event_id if cur.rowcount else None


def attach_outcome(
    event_id: str,
    *,
    report_id: str | None,
    verdict: str | None,
    db_path: Path = DEFAULT_DB_PATH,
) -> None:
    """Stamp the analyst's verdict + report onto a fired event."""
    with _connect(db_path) as conn:
        conn.execute(
            "UPDATE book_watch_events SET report_id = ?, verdict = ? WHERE id = ?",
            (report_id, verdict, event_id),
        )
        conn.commit()


def fired_today(book_id: str, market_day: str, db_path: Path = DEFAULT_DB_PATH) -> int:
    """Wakeups spent today = COUNT of fired rows (the table is the counter)."""
    init_book_watch_tables(db_path)
    with _connect(db_path) as conn:
        return int(
            conn.execute(
                "SELECT COUNT(*) FROM book_watch_events "
                "WHERE book_id = ? AND market_day = ? AND decision = 'fired'",
                (book_id, market_day),
            ).fetchone()[0]
        )


def events_for_day(
    book_id: str, market_day: str, db_path: Path = DEFAULT_DB_PATH
) -> list[dict[str, Any]]:
    """Every trigger that matched today, oldest first — the visible log."""
    init_book_watch_tables(db_path)
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM book_watch_events WHERE book_id = ? AND market_day = ? "
            "ORDER BY decided_at",
            (book_id, market_day),
        ).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        try:
            item["evidence"] = json.loads(item.pop("evidence_json") or "{}")
        except (json.JSONDecodeError, TypeError):
            item["evidence"] = {}
        out.append(item)
    return out
