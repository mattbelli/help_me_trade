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

CREATE TABLE IF NOT EXISTS attention_outcomes (
    symbol TEXT NOT NULL,
    session TEXT NOT NULL,
    angles INTEGER NOT NULL,
    quintile INTEGER,
    bucket INTEGER NOT NULL,
    anchor TEXT NOT NULL,
    first_lit_ts TEXT,
    entry REAL,
    move_2h REAL,
    move_close REAL,
    excess_2h REAL,
    direction INTEGER,
    horizon_complete INTEGER,
    graded_at TEXT NOT NULL,
    PRIMARY KEY (symbol, session)
);
CREATE INDEX IF NOT EXISTS idx_attention_out_session ON attention_outcomes (session, angles);

CREATE TABLE IF NOT EXISTS attention_queue (
    symbol TEXT NOT NULL,
    session TEXT NOT NULL,
    created_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    note TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (symbol, session)
);
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


# --- outcomes (Phase 3: every pool name-day, lit or not) -----------------------

_OUT_COLS = (
    "symbol", "session", "angles", "quintile", "bucket", "anchor", "first_lit_ts",
    "entry", "move_2h", "move_close", "excess_2h", "direction", "horizon_complete",
)


def insert_outcomes(rows: list[dict[str, Any]], db_path: Path | None = None) -> int:
    if not rows:
        return 0
    init_db(db_path)
    now = _now()
    payload = [
        tuple(r.get(c) for c in _OUT_COLS[:-1]) + (int(bool(r.get("horizon_complete"))), now)
        for r in rows
    ]
    with _connect(_db_path(db_path)) as conn:
        conn.executemany(
            f"""
            INSERT OR REPLACE INTO attention_outcomes ({", ".join(_OUT_COLS)}, graded_at)
            VALUES ({", ".join("?" for _ in _OUT_COLS)}, ?)
            """,
            payload,
        )
    return len(payload)


def outcomes_by_angles(
    *, sessions: int = 30, db_path: Path | None = None
) -> list[dict[str, Any]]:
    """The Phase-3 aggregate: median excess by angles lit over the trailing
    sessions, per angle count. Direction split reported so a caller can Wilson-
    bound it; medians only — never a win%."""
    init_db(db_path)
    with _connect(_db_path(db_path)) as conn:
        recent = [
            str(r[0])
            for r in conn.execute(
                "SELECT DISTINCT session FROM attention_outcomes ORDER BY session DESC LIMIT ?",
                (max(1, min(int(sessions), 250)),),
            ).fetchall()
        ]
        if not recent:
            return []
        q = ",".join("?" * len(recent))
        rows = conn.execute(
            f"""
            SELECT angles, COUNT(*) AS n,
                   SUM(direction = 1) AS up, SUM(direction = -1) AS down
            FROM attention_outcomes WHERE session IN ({q})
            GROUP BY angles ORDER BY angles
            """,
            recent,
        ).fetchall()
        out = []
        for r in rows:
            med = conn.execute(
                f"""
                SELECT excess_2h FROM attention_outcomes
                WHERE session IN ({q}) AND angles = ? AND excess_2h IS NOT NULL
                ORDER BY excess_2h
                LIMIT 1 OFFSET (
                    SELECT COUNT(*) / 2 FROM attention_outcomes
                    WHERE session IN ({q}) AND angles = ? AND excess_2h IS NOT NULL
                )
                """,
                [*recent, r["angles"], *recent, r["angles"]],
            ).fetchone()
            out.append(
                {
                    "angles": int(r["angles"]),
                    "n": int(r["n"]),
                    "median_excess": round(float(med[0]), 3) if med else None,
                    "up": int(r["up"] or 0),
                    "down": int(r["down"] or 0),
                    "sessions": len(recent),
                }
            )
    return out


# --- the bounded AI queue (Phase 3: angles=2 AND quintile >= floor) ------------


def enqueue_pending(
    session: str, *, min_quintile: int = 3, db_path: Path | None = None
) -> int:
    """One look_into item per qualifying name-day; idempotent by PK."""
    init_db(db_path)
    now = _now()
    with _connect(_db_path(db_path)) as conn:
        cur = conn.execute(
            """
            INSERT OR IGNORE INTO attention_queue (symbol, session, created_at, status, updated_at)
            SELECT symbol, session, ?, 'pending', ?
            FROM attention_daily
            WHERE session = ? AND angles >= 2 AND quintile IS NOT NULL AND quintile >= ?
            """,
            (now, now, session, int(min_quintile)),
        )
        return int(cur.rowcount or 0)


def next_pending(db_path: Path | None = None) -> dict[str, Any] | None:
    init_db(db_path)
    with _connect(_db_path(db_path)) as conn:
        row = conn.execute(
            """
            SELECT * FROM attention_queue WHERE status = 'pending' AND attempts < 3
            ORDER BY created_at LIMIT 1
            """
        ).fetchone()
    return dict(row) if row else None


def mark_queue_item(
    symbol: str,
    session: str,
    *,
    status: str,
    note: str | None = None,
    db_path: Path | None = None,
) -> None:
    init_db(db_path)
    with _connect(_db_path(db_path)) as conn:
        conn.execute(
            """
            UPDATE attention_queue
            SET status = ?, note = COALESCE(?, note),
                attempts = attempts + 1, updated_at = ?
            WHERE symbol = ? AND session = ?
            """,
            (status, note, _now(), symbol, session),
        )
