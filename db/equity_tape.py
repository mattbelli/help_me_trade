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

EVENTS_PRUNE_DAYS = 30
_RECENT_EVENTS_CAP = 50


def _db_path(db_path: Path | None) -> Path:
    return db_path if db_path is not None else DEFAULT_DB_PATH


def init_db(db_path: Path | None = None) -> None:
    resolved = _db_path(db_path)
    with _connect(resolved) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS equity_tape_events (
                id TEXT PRIMARY KEY,
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
            """
        )
        conn.commit()


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
        json.dumps(list(conditions))
        if isinstance(conditions, (list, tuple))
        else "[]"
    )
    detail = event.get("detail")
    detail_json = json.dumps(detail) if isinstance(detail, dict) else None
    created = event.get("created_at") or _now()
    with _connect(resolved) as conn:
        conn.execute(
            """
            INSERT INTO equity_tape_events (
                id, ts, symbol, event_type, exchange, price, size,
                notional_usd, direction, direction_basis, conditions,
                reason, caveat, detail, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
        cur = conn.execute(
            "DELETE FROM equity_tape_events WHERE ts < ?", (cutoff,)
        )
        conn.commit()
        return cur.rowcount
