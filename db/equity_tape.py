"""Equity tape events store (blocks + dark-pool prints) — perception substrate.

Mirrors ``db/options_flow.py``: upsert on deterministic id, JSON detail column,
no win_prob / ev / confidence columns exist. Facts about the tape only.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from db.base import DEFAULT_DB_PATH
from db.base import connect as _connect
from db.base import now_iso as _now
from db.schema_helpers import _add_column_if_missing

EVENTS_PRUNE_DAYS = 30
_RECENT_EVENTS_CAP = 50


def _db_path(db_path: Path | None) -> Path:
    return db_path if db_path is not None else DEFAULT_DB_PATH


# Paths already initialized this process — same guard as db/options_flow.py:
# init took write transactions (ingest_seq backfill + DDL) on every accessor
# call, which serialized the tape writer against every reader at market open.
_INIT_DONE: set[str] = set()


def init_db(db_path: Path | None = None) -> None:
    resolved = _db_path(db_path)
    key = str(resolved)
    if key in _INIT_DONE:
        return
    with _connect(resolved) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS equity_tape_events (
                id TEXT PRIMARY KEY,
                ingest_seq INTEGER,
                ts TEXT NOT NULL,
                symbol TEXT NOT NULL,
                event_type TEXT NOT NULL,
                exchange TEXT,
                price REAL,
                size INTEGER,
                notional_usd REAL,
                direction TEXT,
                direction_basis TEXT,
                conditions TEXT,
                reason TEXT,
                caveat TEXT,
                detail TEXT,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_eq_tape_ts
                ON equity_tape_events (ts DESC);
            CREATE INDEX IF NOT EXISTS idx_eq_tape_symbol
                ON equity_tape_events (symbol);
            CREATE INDEX IF NOT EXISTS idx_eq_tape_type
                ON equity_tape_events (event_type);

            CREATE TABLE IF NOT EXISTS bus_ingest_sequences (
                store TEXT PRIMARY KEY,
                next_seq INTEGER NOT NULL
            );
            """
        )
        _add_column_if_missing(conn, "equity_tape_events", "ingest_seq", "INTEGER")
        missing = conn.execute(
            """
            SELECT id FROM equity_tape_events
            WHERE ingest_seq IS NULL
            ORDER BY created_at ASC, id ASC
            """
        ).fetchall()
        next_seq = int(
            conn.execute(
                "SELECT COALESCE(MAX(ingest_seq), 0) + 1 FROM equity_tape_events"
            ).fetchone()[0]
        )
        for row in missing:
            conn.execute(
                "UPDATE equity_tape_events SET ingest_seq = ? WHERE id = ?",
                (next_seq, row["id"]),
            )
            next_seq += 1
        conn.execute(
            """
            INSERT INTO bus_ingest_sequences (store, next_seq)
            VALUES ('equity_tape', ?)
            ON CONFLICT(store) DO UPDATE SET
                next_seq = MAX(bus_ingest_sequences.next_seq, excluded.next_seq)
            """,
            (next_seq,),
        )
        conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_eq_tape_ingest_seq
            ON equity_tape_events (ingest_seq)
            """
        )
        conn.commit()
    # Only after full success — a failed init must retry on the next call.
    _INIT_DONE.add(key)


def _row_to_event(row: Any) -> dict[str, Any]:
    out = dict(row)
    for key in ("conditions", "detail"):
        raw = out.get(key)
        if isinstance(raw, str) and raw:
            try:
                out[key] = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                out[key] = [] if key == "conditions" else None
        elif raw is None:
            out[key] = [] if key == "conditions" else None
    return out


def insert_event(event: dict, db_path: Path | None = None) -> None:
    """Upsert one tape event; ``ts`` / ``created_at`` keep first-detection values."""
    resolved = _db_path(db_path)
    init_db(resolved)
    conditions = event.get("conditions")
    conditions_json = (
        json.dumps(list(conditions)) if isinstance(conditions, (list, tuple)) else "[]"
    )
    detail = event.get("detail")
    detail_json = json.dumps(detail) if isinstance(detail, dict) else None
    created = event.get("created_at") or _now()
    with _connect(resolved) as conn:
        conn.execute("BEGIN IMMEDIATE")
        existing = conn.execute(
            "SELECT ingest_seq FROM equity_tape_events WHERE id = ?",
            (event["id"],),
        ).fetchone()
        if existing is None:
            seq_row = conn.execute(
                """
                UPDATE bus_ingest_sequences
                SET next_seq = next_seq + 1
                WHERE store = 'equity_tape'
                RETURNING next_seq - 1
                """
            ).fetchone()
            ingest_seq = int(seq_row[0])
        else:
            ingest_seq = int(existing["ingest_seq"])
        conn.execute(
            """
            INSERT INTO equity_tape_events (
                id, ingest_seq, ts, symbol, event_type, exchange, price, size,
                notional_usd, direction, direction_basis, conditions,
                reason, caveat, detail, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                symbol = excluded.symbol,
                event_type = excluded.event_type,
                exchange = excluded.exchange,
                price = excluded.price,
                size = excluded.size,
                notional_usd = excluded.notional_usd,
                direction = excluded.direction,
                direction_basis = excluded.direction_basis,
                conditions = excluded.conditions,
                reason = excluded.reason,
                caveat = excluded.caveat,
                detail = excluded.detail
            """,
            (
                event["id"],
                ingest_seq,
                event["ts"],
                str(event.get("symbol") or "").strip().upper(),
                event.get("event_type"),
                event.get("exchange"),
                event.get("price"),
                event.get("size"),
                event.get("notional_usd"),
                event.get("direction"),
                event.get("direction_basis"),
                conditions_json,
                event.get("reason"),
                event.get("caveat"),
                detail_json,
                created,
            ),
        )
        conn.commit()


def recent_events(
    *,
    symbol: str | None = None,
    event_type: str | None = None,
    min_notional: float | None = None,
    since: str | None = None,
    limit: int = 50,
    db_path: Path | None = None,
) -> list[dict]:
    resolved = _db_path(db_path)
    init_db(resolved)
    capped = max(1, min(int(limit), _RECENT_EVENTS_CAP))
    clauses: list[str] = []
    params: list[Any] = []
    if symbol:
        clauses.append("symbol = ?")
        params.append(symbol.strip().upper())
    if event_type:
        clauses.append("event_type = ?")
        params.append(event_type)
    if min_notional is not None:
        clauses.append("notional_usd >= ?")
        params.append(float(min_notional))
    if since:
        clauses.append("ts >= ?")
        params.append(since)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with _connect(resolved) as conn:
        rows = conn.execute(
            f"""
            SELECT * FROM equity_tape_events
            {where}
            ORDER BY ts DESC
            LIMIT ?
            """,
            (*params, capped),
        ).fetchall()
    return [_row_to_event(r) for r in rows]


def prune(db_path: Path | None = None) -> int:
    resolved = _db_path(db_path)
    init_db(resolved)
    cutoff = (datetime.now(UTC) - timedelta(days=EVENTS_PRUNE_DAYS)).isoformat()
    with _connect(resolved) as conn:
        cur = conn.execute("DELETE FROM equity_tape_events WHERE ts < ?", (cutoff,))
        conn.commit()
        return cur.rowcount
