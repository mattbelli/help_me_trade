"""The attention ledger — one row per (symbol, session), both angles joined.

ATTENTION_JOIN_DESIGN_2026-08-17.md §4.2. Angles are booleans plus their own
magnitude facts — never a composite score. `abn_vol_pctile` is carried beyond
the design sketch because the pre-registered §4.6 subset test needs it on the
ledger. Honesty label + caveat are NOT NULL so they survive every transport.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from db.base import DEFAULT_DB_PATH
from db.base import connect as _connect
from db.base import now_iso as _now

_INIT_DONE: set[str] = set()

_DDL = """
CREATE TABLE IF NOT EXISTS attention_daily (
    symbol TEXT NOT NULL,
    session TEXT NOT NULL,
    quintile INTEGER,
    abn_lit INTEGER NOT NULL DEFAULT 0,
    abn_first_ts TEXT,
    abn_bucket INTEGER,
    abn_range_ratio REAL,
    abn_range_pctile REAL,
    abn_vol_ratio REAL,
    abn_vol_pctile REAL,
    abn_rvol_session REAL,
    abn_repeats INTEGER NOT NULL DEFAULT 0,
    base_state TEXT NOT NULL DEFAULT 'none',
    flow_lit INTEGER NOT NULL DEFAULT 0,
    flow_first_ts TEXT,
    flow_cell_days INTEGER NOT NULL DEFAULT 0,
    flow_min_p REAL,
    flow_max_print_usd REAL,
    flow_call_prem REAL,
    flow_put_prem REAL,
    flow_short_dated_share REAL,
    flow_events INTEGER NOT NULL DEFAULT 0,
    angles INTEGER NOT NULL DEFAULT 0,
    rank_pctile REAL,
    first_lit_ts TEXT,
    label TEXT NOT NULL,
    caveat TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (symbol, session)
);
CREATE INDEX IF NOT EXISTS idx_attention_session ON attention_daily (session, angles);
"""

_COLS = (
    "symbol", "session", "quintile",
    "abn_lit", "abn_first_ts", "abn_bucket", "abn_range_ratio", "abn_range_pctile",
    "abn_vol_ratio", "abn_vol_pctile", "abn_rvol_session", "abn_repeats", "base_state",
    "flow_lit", "flow_first_ts", "flow_cell_days", "flow_min_p", "flow_max_print_usd",
    "flow_call_prem", "flow_put_prem", "flow_short_dated_share", "flow_events",
    "angles", "rank_pctile", "first_lit_ts", "label", "caveat",
)


def _db_path(db_path: Path | None) -> Path:
    return db_path if db_path is not None else DEFAULT_DB_PATH


def init_db(db_path: Path | None = None) -> None:
    resolved = _db_path(db_path)
    key = str(resolved)
    if key in _INIT_DONE:
        return
    with _connect(resolved) as conn:
        conn.executescript(_DDL)
        conn.commit()
    _INIT_DONE.add(key)


def upsert_rows(rows: list[dict[str, Any]], db_path: Path | None = None) -> int:
    """Upsert the tick's full picture. The job recomputes every lit name each
    pass, so a plain overwrite of all fields is the correct merge."""
    if not rows:
        return 0
    init_db(db_path)
    now = _now()
    placeholders = ", ".join("?" for _ in _COLS) + ", ?"
    updates = ", ".join(f"{c}=excluded.{c}" for c in _COLS[2:])
    payload = [tuple(r.get(c) for c in _COLS) + (now,) for r in rows]
    with _connect(_db_path(db_path)) as conn:
        conn.executemany(
            f"""
            INSERT INTO attention_daily ({", ".join(_COLS)}, updated_at)
            VALUES ({placeholders})
            ON CONFLICT(symbol, session) DO UPDATE SET
                {updates}, updated_at=excluded.updated_at
            """,
            payload,
        )
    return len(payload)


def list_rows(
    session: str,
    *,
    min_quintile: int = 0,
    min_angles: int = 1,
    limit: int = 100,
    db_path: Path | None = None,
) -> list[dict[str, Any]]:
    """Today's list, ranked (angles desc, within-angle percentile desc, first
    lit first). A quintile floor > 0 also drops unknown-quintile rows —
    deny-by-default on the display floor; the row itself stays in the ledger."""
    init_db(db_path)
    where = ["session = ?", "angles >= ?"]
    args: list[Any] = [session, int(min_angles)]
    if min_quintile > 0:
        where.append("quintile IS NOT NULL AND quintile >= ?")
        args.append(int(min_quintile))
    args.append(min(max(int(limit), 1), 500))
    with _connect(_db_path(db_path)) as conn:
        rows = conn.execute(
            f"""
            SELECT * FROM attention_daily WHERE {" AND ".join(where)}
            ORDER BY angles DESC, rank_pctile IS NULL, rank_pctile DESC, first_lit_ts
            LIMIT ?
            """,
            args,
        ).fetchall()
    return [dict(r) for r in rows]


def get_row(symbol: str, session: str, db_path: Path | None = None) -> dict[str, Any] | None:
    init_db(db_path)
    with _connect(_db_path(db_path)) as conn:
        row = conn.execute(
            "SELECT * FROM attention_daily WHERE symbol = ? AND session = ?",
            (str(symbol).strip().upper(), session),
        ).fetchone()
    return dict(row) if row else None
