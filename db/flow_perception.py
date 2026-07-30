"""Daily unusual-vs-peers flow perception rows — one per (session, symbol, window).

Written by the post-close scan in ``signals/flow_perception.py``; read by the
``flow_perception`` perception resource. Narrow time-series store (index_pressure
pattern), not shoehorned into options_flow_events: these are derived per-name-day
window statistics, not prints.

Only name-days where at least one component fired are persisted — a symbol absent
from a session means "not unusual versus its peers that session", which is the
common case and needs no row. ``disclaimer`` rides on every row because honesty
flags have been stripped at transports before (07-08 red-team): a consumer that
reads a row without reading this module still gets the caveat.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from db.base import DEFAULT_DB_PATH, now_iso
from db.base import connect as _connect

# The four registered fingerprint components (FLOW_ROADMAP 1.13 / 1.13b), in the
# column order below. Callers build rows keyed by these names.
COMPONENT_COLUMNS = (
    "short_dte_share",
    "concentration",
    "largest_print_ratio",
    "vol_max_ratio",
)

_VALUE_COLUMNS = tuple(
    f"{c}{suffix}"
    for c in COMPONENT_COLUMNS
    for suffix in ("", "_rank", "_fired")
)

_ROW_COLUMNS = (
    "session",
    "symbol",
    "window",
    "quintile",
    *_VALUE_COLUMNS,
    "n_fired",
    "fired_components",
    "days_to_earnings",
    "earnings_coverage",
    "earnings_estimated",
    "earnings_state",
    "regime",
    "session_age_days",
    "disclaimer",
    "scanned_at",
)


def init_flow_perception_table(db_path: Path = DEFAULT_DB_PATH) -> None:
    with _connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS flow_perception_daily (
                -- the corpus session the trailing window ENDS on (the as-of date)
                session TEXT NOT NULL,
                symbol TEXT NOT NULL,
                window INTEGER NOT NULL,          -- trailing sessions: 5 | 10 | 20
                quintile INTEGER NOT NULL,        -- peer cell: trailing-20d $-volume quintile 0-4
                -- component value, its percentile rank INSIDE the peer cell, and
                -- whether it cleared the cell's p90 (1.14d's firing rule)
                short_dte_share REAL,
                short_dte_share_rank REAL,
                short_dte_share_fired INTEGER NOT NULL DEFAULT 0,
                concentration REAL,
                concentration_rank REAL,
                concentration_fired INTEGER NOT NULL DEFAULT 0,
                largest_print_ratio REAL,
                largest_print_ratio_rank REAL,
                largest_print_ratio_fired INTEGER NOT NULL DEFAULT 0,
                vol_max_ratio REAL,
                vol_max_ratio_rank REAL,
                vol_max_ratio_fired INTEGER NOT NULL DEFAULT 0,
                n_fired INTEGER NOT NULL DEFAULT 0,
                fired_components TEXT,            -- csv, display order = COMPONENT_COLUMNS
                days_to_earnings INTEGER,         -- calendar days; NULL = no known date
                earnings_coverage INTEGER NOT NULL DEFAULT 0,
                -- 1 = the date is PROJECTED from the name's own 8-K filing cadence,
                -- not confirmed by anybody. Rides on the row because honesty flags
                -- have been stripped at transports before (07-08 red-team).
                earnings_estimated INTEGER NOT NULL DEFAULT 0,
                -- 'dated' | 'non_filer' (an ETF: no earnings is CORRECT) | 'unknown'
                earnings_state TEXT,
                regime TEXT NOT NULL,
                -- weekday sessions between `session` and the scan: a stale corpus
                -- must read as stale, never as "nothing unusual today"
                session_age_days INTEGER NOT NULL DEFAULT 0,
                disclaimer TEXT NOT NULL,
                scanned_at TEXT NOT NULL,
                PRIMARY KEY (session, symbol, window)
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_flow_perception_symbol "
            "ON flow_perception_daily (symbol, session DESC)"
        )
        # Additive columns for tables created before earnings estimates existed.
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(flow_perception_daily)")}
        for col, decl in (
            ("earnings_estimated", "INTEGER NOT NULL DEFAULT 0"),
            ("earnings_state", "TEXT"),
        ):
            if col not in cols:
                conn.execute(f"ALTER TABLE flow_perception_daily ADD COLUMN {col} {decl}")
        conn.commit()


def upsert_rows(rows: list[dict[str, Any]], *, db_path: Path = DEFAULT_DB_PATH) -> int:
    """Upsert scan rows on (session, symbol, window). Re-running a session replaces
    its rows — the scan recomputes the whole session, so last write wins."""
    if not rows:
        return 0
    init_flow_perception_table(db_path)
    now = now_iso()
    cols = ", ".join(_ROW_COLUMNS)
    marks = ", ".join(f":{c}" for c in _ROW_COLUMNS)
    updates = ", ".join(
        f"{c} = excluded.{c}"
        for c in _ROW_COLUMNS
        if c not in ("session", "symbol", "window")
    )
    payload = []
    for row in rows:
        r = {c: row.get(c) for c in _ROW_COLUMNS}
        r["scanned_at"] = row.get("scanned_at") or now
        # NOT NULL counters: a missing flag means "did not fire", and an explicit
        # NULL bind would defeat the column default.
        for col in (*(f"{c}_fired" for c in COMPONENT_COLUMNS),
                    "n_fired", "earnings_coverage", "earnings_estimated",
                    "session_age_days"):
            r[col] = int(r[col] or 0)
        payload.append(r)
    with _connect(db_path) as conn:
        conn.executemany(
            f"INSERT INTO flow_perception_daily ({cols}) VALUES ({marks}) "
            f"ON CONFLICT(session, symbol, window) DO UPDATE SET {updates}",
            payload,
        )
        conn.commit()
    return len(payload)


def latest_session(db_path: Path = DEFAULT_DB_PATH) -> str | None:
    init_flow_perception_table(db_path)
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT MAX(session) AS s FROM flow_perception_daily"
        ).fetchone()
    return row["s"] if row and row["s"] else None


def rows_for_session(
    session: str | None = None,
    *,
    window: int | None = None,
    symbol: str | None = None,
    min_fired: int = 1,
    limit: int = 50,
    db_path: Path = DEFAULT_DB_PATH,
) -> list[dict[str, Any]]:
    """Scan rows for one session (default: the latest scanned), most unusual first."""
    init_flow_perception_table(db_path)
    target = session or latest_session(db_path=db_path)
    if not target:
        return []
    sql = (
        "SELECT * FROM flow_perception_daily WHERE session = ? AND n_fired >= ?"
    )
    params: list[Any] = [target, int(min_fired)]
    if window is not None:
        sql += " AND window = ?"
        params.append(int(window))
    if symbol:
        sql += " AND symbol = ?"
        params.append(symbol.strip().upper())
    sql += " ORDER BY n_fired DESC, symbol ASC, window ASC LIMIT ?"
    params.append(max(1, int(limit)))
    with _connect(db_path) as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
