"""Abnormality events, live state snapshot, and outcome grades.

Windowed state is NOT an equity_tape print — own tables, own flush. Honesty
label + caveat columns are NOT NULL so they survive every transport.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from db.base import DEFAULT_DB_PATH
from db.base import connect as _connect
from db.base import now_iso as _now

_INIT_DONE: set[str] = set()

_DDL = """
CREATE TABLE IF NOT EXISTS abnormality_events (
    id TEXT PRIMARY KEY,
    ts TEXT NOT NULL,
    symbol TEXT NOT NULL,
    kind TEXT NOT NULL,
    session TEXT NOT NULL,
    bucket INTEGER NOT NULL,
    range_ratio REAL,
    vol_ratio REAL,
    rvol_session REAL,
    range_pctile REAL,
    vol_pctile REAL,
    composite REAL,
    base_state TEXT NOT NULL DEFAULT 'none',
    quintile INTEGER,
    label TEXT NOT NULL,
    caveat TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_abn_ev_session ON abnormality_events (session, bucket);
CREATE INDEX IF NOT EXISTS idx_abn_ev_symbol ON abnormality_events (symbol, session);

CREATE TABLE IF NOT EXISTS abnormality_state (
    symbol TEXT PRIMARY KEY,
    ts TEXT NOT NULL,
    session TEXT NOT NULL,
    bucket INTEGER,
    range_ratio REAL,
    vol_ratio REAL,
    rvol_session REAL,
    range_pctile REAL,
    vol_pctile REAL,
    composite REAL,
    base_state TEXT NOT NULL DEFAULT 'none',
    quintile INTEGER,
    status TEXT NOT NULL,
    label TEXT NOT NULL,
    caveat TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_abn_state_session ON abnormality_state (session, status);

CREATE TABLE IF NOT EXISTS abnormality_outcomes (
    event_id TEXT PRIMARY KEY,
    session TEXT NOT NULL,
    symbol TEXT NOT NULL,
    bucket INTEGER NOT NULL,
    kind TEXT NOT NULL,
    entry REAL,
    move_2h REAL,
    move_close REAL,
    excess_2h REAL,
    close_2h REAL,
    close_session REAL,
    direction INTEGER,
    horizon_complete INTEGER,
    graded_at TEXT NOT NULL,
    FOREIGN KEY (event_id) REFERENCES abnormality_events(id)
);
CREATE INDEX IF NOT EXISTS idx_abn_out_session ON abnormality_outcomes (session);
"""


def _db_path(db_path: Path | None) -> Path:
    return db_path if db_path is not None else DEFAULT_DB_PATH


def init_db(db_path: Path | None = None) -> None:
    resolved = _db_path(db_path)
    key = str(resolved)
    if key in _INIT_DONE:
        return
    with _connect(resolved) as conn:
        conn.executescript(_DDL)
    _INIT_DONE.add(key)


def insert_events(events: list[dict[str, Any]], db_path: Path | None = None) -> int:
    if not events:
        return 0
    init_db(db_path)
    now = _now()
    rows = [
        (
            e["id"], e["ts"], e["symbol"], e["kind"], e["session"], int(e["bucket"]),
            e.get("range_ratio"), e.get("vol_ratio"), e.get("rvol_session"),
            e.get("range_pctile"), e.get("vol_pctile"), e.get("composite"),
            e.get("base_state") or "none", e.get("quintile"),
            e["label"], e["caveat"], now,
        )
        for e in events
    ]
    with _connect(_db_path(db_path)) as conn:
        conn.executemany(
            """
            INSERT OR IGNORE INTO abnormality_events(
                id, ts, symbol, kind, session, bucket,
                range_ratio, vol_ratio, rvol_session, range_pctile, vol_pctile,
                composite, base_state, quintile, label, caveat, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        return conn.total_changes


def upsert_state(rows: list[dict[str, Any]], db_path: Path | None = None) -> int:
    if not rows:
        return 0
    init_db(db_path)
    now = _now()
    payload = [
        (
            r["symbol"], r["ts"], r["session"], r.get("bucket"),
            r.get("range_ratio"), r.get("vol_ratio"), r.get("rvol_session"),
            r.get("range_pctile"), r.get("vol_pctile"), r.get("composite"),
            r.get("base_state") or "none", r.get("quintile"),
            r["status"], r["label"], r["caveat"], now,
        )
        for r in rows
        if r.get("symbol") and r["symbol"] != "*"
    ]
    if not payload:
        return 0
    with _connect(_db_path(db_path)) as conn:
        conn.executemany(
            """
            INSERT INTO abnormality_state(
                symbol, ts, session, bucket, range_ratio, vol_ratio, rvol_session,
                range_pctile, vol_pctile, composite, base_state, quintile,
                status, label, caveat, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(symbol) DO UPDATE SET
                ts=excluded.ts, session=excluded.session, bucket=excluded.bucket,
                range_ratio=excluded.range_ratio, vol_ratio=excluded.vol_ratio,
                rvol_session=excluded.rvol_session, range_pctile=excluded.range_pctile,
                vol_pctile=excluded.vol_pctile, composite=excluded.composite,
                base_state=excluded.base_state, quintile=excluded.quintile,
                status=excluded.status, label=excluded.label, caveat=excluded.caveat,
                updated_at=excluded.updated_at
            """,
            payload,
        )
        return len(payload)


def get_state(symbol: str, db_path: Path | None = None) -> dict[str, Any] | None:
    init_db(db_path)
    with _connect(_db_path(db_path)) as conn:
        row = conn.execute(
            "SELECT * FROM abnormality_state WHERE symbol = ?", (symbol,)
        ).fetchone()
    return dict(row) if row else None


def get_state_many(symbols: list[str], db_path: Path | None = None) -> dict[str, dict[str, Any]]:
    if not symbols:
        return {}
    init_db(db_path)
    q = ",".join("?" * len(symbols))
    with _connect(_db_path(db_path)) as conn:
        rows = conn.execute(
            f"SELECT * FROM abnormality_state WHERE symbol IN ({q})", symbols
        ).fetchall()
    return {str(r["symbol"]): dict(r) for r in rows}


def list_state(
    *,
    session: str | None = None,
    fired: bool = False,
    paired: bool = False,
    base_low: bool = False,
    base_high: bool = False,
    limit: int = 50,
    db_path: Path | None = None,
) -> list[dict[str, Any]]:
    init_db(db_path)
    where = ["status != 'ca_suspect'"]
    args: list[Any] = []
    if session:
        where.append("session = ?")
        args.append(session)
    if base_low:
        where.append("base_state = 'base_low'")
    if base_high:
        where.append("base_state = 'base_high'")
    sql = f"SELECT * FROM abnormality_state WHERE {' AND '.join(where)}"
    with _connect(_db_path(db_path)) as conn:
        rows = [dict(r) for r in conn.execute(sql, args).fetchall()]
    if fired or paired:
        events = events_today(session, db_path=db_path)
        fired_syms = {e["symbol"] for e in events}
        paired_syms = {e["symbol"] for e in events if e["kind"] == "abnormality_paired"}
        if fired:
            rows = [r for r in rows if r["symbol"] in fired_syms]
        if paired:
            rows = [r for r in rows if r["symbol"] in paired_syms]
    rows.sort(key=lambda r: (r.get("composite") is None, -(r.get("composite") or 0)))
    return rows[: min(max(int(limit), 0), 200)]


def events_today(session: str | None = None, db_path: Path | None = None) -> list[dict[str, Any]]:
    """Fires for one ET session. Default is *today*, never MAX(session).

    MAX(session) would keep Friday's names on the Monday book pack until the
    first Monday fire landed — the pack line is labelled "today".
    """
    init_db(db_path)
    if session is None:
        from datetime import datetime
        from zoneinfo import ZoneInfo

        session = datetime.now(ZoneInfo("America/New_York")).date().isoformat()
    with _connect(_db_path(db_path)) as conn:
        rows = conn.execute(
            "SELECT * FROM abnormality_events WHERE session = ? ORDER BY ts",
            (session,),
        ).fetchall()
    return [dict(r) for r in rows]


def ungraded_events(session: str, db_path: Path | None = None) -> list[dict[str, Any]]:
    init_db(db_path)
    with _connect(_db_path(db_path)) as conn:
        rows = conn.execute(
            """
            SELECT e.* FROM abnormality_events e
            LEFT JOIN abnormality_outcomes o ON o.event_id = e.id
            WHERE e.session = ? AND o.event_id IS NULL
            """,
            (session,),
        ).fetchall()
    return [dict(r) for r in rows]


def insert_outcomes(rows: list[dict[str, Any]], db_path: Path | None = None) -> int:
    if not rows:
        return 0
    init_db(db_path)
    now = _now()
    payload = [
        (
            r.get("event_id") or r["id"], r["session"], r["symbol"], int(r["bucket"]),
            r.get("kind") or "abnormality",
            r.get("entry"), r.get("move_2h"), r.get("move_close"), r.get("excess_2h"),
            r.get("close_2h"), r.get("close_session"), r.get("direction"),
            int(bool(r.get("horizon_complete"))), now,
        )
        for r in rows
    ]
    with _connect(_db_path(db_path)) as conn:
        conn.executemany(
            """
            INSERT OR REPLACE INTO abnormality_outcomes(
                event_id, session, symbol, bucket, kind, entry, move_2h, move_close,
                excess_2h, close_2h, close_session, direction, horizon_complete, graded_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            payload,
        )
        return len(payload)


def outcomes_for_session(session: str, db_path: Path | None = None) -> list[dict[str, Any]]:
    init_db(db_path)
    with _connect(_db_path(db_path)) as conn:
        rows = conn.execute(
            "SELECT * FROM abnormality_outcomes WHERE session = ?", (session,)
        ).fetchall()
    return [dict(r) for r in rows]


def recent_outcomes(limit: int = 200, db_path: Path | None = None) -> list[dict[str, Any]]:
    init_db(db_path)
    with _connect(_db_path(db_path)) as conn:
        rows = conn.execute(
            """
            SELECT * FROM abnormality_outcomes
            ORDER BY session DESC, symbol LIMIT ?
            """,
            (max(1, min(limit, 1000)),),
        ).fetchall()
    return [dict(r) for r in rows]
