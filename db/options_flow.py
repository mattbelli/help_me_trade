"""Options-flow events + daily baseline store (perception signal, Mode A).

Read-only perception substrate — no win_prob / ev / confidence columns exist.
See ``research/fable/2026-07-09_options-flow-screener-design.md`` §4 / §6.
"""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from db.base import DEFAULT_DB_PATH
from db.base import connect as _connect
from db.base import now_iso as _now
from db.schema_helpers import _add_column_if_missing
from signals.options_flow.constants import DAILY_PRUNE_DAYS, EVENTS_PRUNE_DAYS

_RECENT_EVENTS_CAP = 250


def _db_path(db_path: Path | None) -> Path:
    return db_path if db_path is not None else DEFAULT_DB_PATH


# Paths already initialized this process. Every accessor calls init_db() first,
# and the body takes WRITE transactions (ingest_seq backfill + bus_ingest_sequences
# upsert + DDL) — re-running it on every insert made two writer lanes serialize on
# init instead of their own tiny inserts, and at market open the loser threw
# "database is locked" and DROPPED its events (sweep cycles failed 121 times in
# 30 min on 2026-07-27). Init is idempotent, so a racing first-call is harmless.
_INIT_DONE: set[str] = set()


def init_db(db_path: Path | None = None) -> None:
    resolved = _db_path(db_path)
    key = str(resolved)
    if key in _INIT_DONE:
        return
    with _connect(resolved) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS options_flow_events (
                id TEXT PRIMARY KEY,
                ingest_seq INTEGER,
                ts TEXT NOT NULL,
                underlying TEXT NOT NULL,
                occ_symbol TEXT NOT NULL,
                right TEXT,
                strike REAL,
                expiry TEXT,
                dte INTEGER,
                otm_pct REAL,
                event_type TEXT NOT NULL,
                contracts INTEGER,
                premium_usd REAL,
                vol_oi_ratio REAL,
                vol_zscore REAL,
                direction TEXT,
                direction_basis TEXT,
                spread_flag INTEGER,
                n_prints INTEGER,
                exchanges TEXT,
                reason TEXT,
                caveat TEXT,
                ret_1d REAL,
                ret_5d REAL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_of_events_ts
                ON options_flow_events (ts DESC);
            CREATE INDEX IF NOT EXISTS idx_of_events_underlying
                ON options_flow_events (underlying);
            CREATE INDEX IF NOT EXISTS idx_of_events_type
                ON options_flow_events (event_type);
            CREATE TABLE IF NOT EXISTS bus_ingest_sequences (
                store TEXT PRIMARY KEY,
                next_seq INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS options_flow_daily (
                underlying TEXT NOT NULL,
                occ_symbol TEXT NOT NULL,
                date TEXT NOT NULL,
                right TEXT,
                dte INTEGER,
                dte_bucket TEXT,
                volume INTEGER,
                prints INTEGER,
                premium_usd REAL,
                close_oi INTEGER,
                PRIMARY KEY (occ_symbol, date)
            );
            CREATE INDEX IF NOT EXISTS idx_of_daily_underlying_bucket
                ON options_flow_daily (underlying, dte_bucket, date);
            """
        )
        _add_column_if_missing(conn, "options_flow_events", "detail", "TEXT")
        _add_column_if_missing(conn, "options_flow_events", "ingest_seq", "INTEGER")
        # Stable integer cursor migration.  Existing rows are numbered once in
        # first-seen order; the writer below never updates this column.
        missing = conn.execute(
            """
            SELECT id FROM options_flow_events
            WHERE ingest_seq IS NULL
            ORDER BY created_at ASC, id ASC
            """
        ).fetchall()
        next_seq = int(
            conn.execute(
                "SELECT COALESCE(MAX(ingest_seq), 0) + 1 FROM options_flow_events"
            ).fetchone()[0]
        )
        for row in missing:
            conn.execute(
                "UPDATE options_flow_events SET ingest_seq = ? WHERE id = ?",
                (next_seq, row["id"]),
            )
            next_seq += 1
        conn.execute(
            """
            INSERT INTO bus_ingest_sequences (store, next_seq)
            VALUES ('options_flow', ?)
            ON CONFLICT(store) DO UPDATE SET
                next_seq = MAX(bus_ingest_sequences.next_seq, excluded.next_seq)
            """,
            (next_seq,),
        )
        conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_of_events_ingest_seq
            ON options_flow_events (ingest_seq)
            """
        )
        # Intraday outcome horizons (signals/options_flow/outcomes.py) — underlying
        # forward returns from the print ts; ret_1d/ret_5d are original columns.
        _add_column_if_missing(conn, "options_flow_events", "ret_15m", "REAL")
        _add_column_if_missing(conn, "options_flow_events", "ret_1h", "REAL")
        _add_column_if_missing(conn, "options_flow_events", "ret_eod", "REAL")
        _add_column_if_missing(conn, "options_flow_events", "outcomes_done", "INTEGER")
        # Conditioning dimensions stamped at event time (FLOW_INTELLIGENCE_DESIGN §Q2):
        # coverage = what the detectors could SEE when this fired (the denominator
        # changes at the wildcard cutover and is unreconstructable later);
        # minutes_into_session = ET minutes since 09:30 (DST makes this non-derivable
        # in SQL). Weekday / size bucket / 0DTE derive from ts+columns at query time.
        _add_column_if_missing(conn, "options_flow_events", "coverage", "TEXT")
        _add_column_if_missing(conn, "options_flow_events", "minutes_into_session", "INTEGER")
        conn.commit()
    # Only after full success — a failed init must retry on the next call.
    _INIT_DONE.add(key)


def _row_to_event(row: Any) -> dict[str, Any]:
    out = dict(row)
    raw_ex = out.get("exchanges")
    if isinstance(raw_ex, str) and raw_ex:
        try:
            out["exchanges"] = json.loads(raw_ex)
        except (json.JSONDecodeError, TypeError):
            out["exchanges"] = []
    elif raw_ex is None:
        out["exchanges"] = []
    if "spread_flag" in out and out["spread_flag"] is not None:
        out["spread_flag"] = bool(out["spread_flag"])
    raw_detail = out.get("detail")
    if isinstance(raw_detail, str) and raw_detail:
        try:
            out["detail"] = json.loads(raw_detail)
        except (json.JSONDecodeError, TypeError):
            out["detail"] = None
    elif raw_detail is None:
        out["detail"] = None
    return out


def _minutes_into_session(ts: str) -> int | None:
    """ET minutes since 09:30 for a UTC ISO ts (stamped — DST breaks SQL derivation)."""
    try:
        from zoneinfo import ZoneInfo

        dt = datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(
            ZoneInfo("America/New_York")
        )
    except (ValueError, KeyError):
        return None
    return (dt.hour * 60 + dt.minute) - (9 * 60 + 30)


def insert_event(event: dict, db_path: Path | None = None) -> None:
    """Upsert a §6 event row; ``ts`` / ``created_at`` keep first-detection values."""
    resolved = _db_path(db_path)
    init_db(resolved)
    exchanges = event.get("exchanges")
    if isinstance(exchanges, (list, tuple)):
        exchanges_json = json.dumps(list(exchanges))
    elif isinstance(exchanges, str):
        exchanges_json = exchanges
    else:
        exchanges_json = "[]"
    spread = event.get("spread_flag")
    spread_int: int | None
    if spread is None:
        spread_int = None
    else:
        spread_int = 1 if spread else 0
    detail = event.get("detail")
    if isinstance(detail, dict):
        detail_json = json.dumps(detail)
    else:
        detail_json = None
    created = event.get("created_at") or _now()
    with _connect(resolved) as conn:
        conn.execute("BEGIN IMMEDIATE")
        existing = conn.execute(
            "SELECT ingest_seq FROM options_flow_events WHERE id = ?",
            (event["id"],),
        ).fetchone()
        if existing is None:
            seq_row = conn.execute(
                """
                UPDATE bus_ingest_sequences
                SET next_seq = next_seq + 1
                WHERE store = 'options_flow'
                RETURNING next_seq - 1
                """
            ).fetchone()
            ingest_seq = int(seq_row[0])
        else:
            ingest_seq = int(existing["ingest_seq"])
        conn.execute(
            """
            INSERT INTO options_flow_events (
                id, ingest_seq, ts, underlying, occ_symbol, right, strike, expiry, dte,
                otm_pct, event_type, contracts, premium_usd, vol_oi_ratio,
                vol_zscore, direction, direction_basis, spread_flag, n_prints,
                exchanges, reason, caveat, ret_1d, ret_5d, created_at, detail,
                coverage, minutes_into_session
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?
            )
            ON CONFLICT(id) DO UPDATE SET
                underlying = excluded.underlying,
                occ_symbol = excluded.occ_symbol,
                right = excluded.right,
                strike = excluded.strike,
                expiry = excluded.expiry,
                dte = excluded.dte,
                otm_pct = excluded.otm_pct,
                event_type = excluded.event_type,
                contracts = excluded.contracts,
                premium_usd = excluded.premium_usd,
                vol_oi_ratio = excluded.vol_oi_ratio,
                vol_zscore = excluded.vol_zscore,
                direction = excluded.direction,
                direction_basis = excluded.direction_basis,
                spread_flag = excluded.spread_flag,
                n_prints = excluded.n_prints,
                exchanges = excluded.exchanges,
                reason = excluded.reason,
                caveat = excluded.caveat,
                ret_1d = excluded.ret_1d,
                ret_5d = excluded.ret_5d,
                detail = excluded.detail
            """,
            (
                event["id"],
                ingest_seq,
                event["ts"],
                str(event.get("underlying") or "").strip().upper(),
                event.get("occ_symbol"),
                event.get("right"),
                event.get("strike"),
                event.get("expiry"),
                event.get("dte"),
                event.get("otm_pct"),
                event.get("event_type"),
                event.get("contracts"),
                event.get("premium_usd"),
                event.get("vol_oi_ratio"),
                event.get("vol_zscore"),
                event.get("direction"),
                event.get("direction_basis"),
                spread_int,
                event.get("n_prints"),
                exchanges_json,
                event.get("reason"),
                event.get("caveat"),
                event.get("ret_1d"),
                event.get("ret_5d"),
                created,
                detail_json,
                event.get("coverage") or "watchlist_capped",
                _minutes_into_session(str(event["ts"])),
            ),
        )
        conn.commit()


def recent_events(
    *,
    symbol: str | None = None,
    event_type: str | None = None,
    min_premium: float | None = None,
    since: str | None = None,
    limit: int = 50,
    db_path: Path | None = None,
) -> list[dict]:
    """Newest-first event rows; ``limit`` capped at ``_RECENT_EVENTS_CAP``.

    ``event_type`` accepts a single type or a comma-separated list (``IN``) so a
    caller can pull just the directional tape (``sweep,block,directional_pressure``)
    without the nightly ``unusual_volume`` batch burying it in the recency window.
    """
    resolved = _db_path(db_path)
    init_db(resolved)
    cap = max(1, min(int(limit), _RECENT_EVENTS_CAP))
    clauses = ["1=1"]
    params: list[Any] = []
    if symbol is not None:
        clauses.append("underlying = ?")
        params.append(str(symbol).strip().upper())
    if event_type is not None:
        types = [t.strip() for t in str(event_type).split(",") if t.strip()]
        if types:
            placeholders = ",".join("?" for _ in types)
            clauses.append(f"event_type IN ({placeholders})")
            params.extend(types)
    if min_premium is not None:
        clauses.append("premium_usd >= ?")
        params.append(float(min_premium))
    if since is not None:
        clauses.append("ts >= ?")
        params.append(since)
    where = " AND ".join(clauses)
    params.append(cap)
    with _connect(resolved) as conn:
        rows = conn.execute(
            f"""
            SELECT * FROM options_flow_events
            WHERE {where}
            ORDER BY ts DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
    return [_row_to_event(r) for r in rows]


def upsert_daily(rows: list[dict], db_path: Path | None = None) -> int:
    """Upsert daily baseline rows on PK (occ_symbol, date). Returns rows written."""
    resolved = _db_path(db_path)
    init_db(resolved)
    cleaned: list[tuple[Any, ...]] = []
    for row in rows:
        occ = str(row.get("occ_symbol") or "").strip().upper()
        day = str(row.get("date") or "").strip()
        if not occ or not day:
            continue
        cleaned.append(
            (
                str(row.get("underlying") or "").strip().upper(),
                occ,
                day,
                row.get("right"),
                row.get("dte"),
                row.get("dte_bucket"),
                row.get("volume"),
                row.get("prints"),
                row.get("premium_usd"),
                row.get("close_oi"),
            )
        )
    if not cleaned:
        return 0
    with _connect(resolved) as conn:
        conn.executemany(
            """
            INSERT INTO options_flow_daily (
                underlying, occ_symbol, date, right, dte, dte_bucket,
                volume, prints, premium_usd, close_oi
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(occ_symbol, date) DO UPDATE SET
                underlying = excluded.underlying,
                right = excluded.right,
                dte = excluded.dte,
                dte_bucket = excluded.dte_bucket,
                volume = excluded.volume,
                prints = excluded.prints,
                premium_usd = excluded.premium_usd,
                close_oi = excluded.close_oi
            """,
            cleaned,
        )
        conn.commit()
    return len(cleaned)


def daily_baselines(
    underlying: str,
    dte_bucket: str,
    lookback_days: int = 20,
    *,
    as_of: str | None = None,
    db_path: Path | None = None,
) -> dict:
    """Mean/std/n of daily bucket volume for UV-2 (summed across contracts per day)."""
    resolved = _db_path(db_path)
    init_db(resolved)
    sym = str(underlying).strip().upper()
    clauses = ["underlying = ?", "dte_bucket = ?"]
    params: list[Any] = [sym, dte_bucket]
    if as_of is not None:
        clauses.append("date <= ?")
        params.append(as_of)
    where = " AND ".join(clauses)
    with _connect(resolved) as conn:
        rows = conn.execute(
            f"""
            SELECT date, SUM(volume) AS day_vol
            FROM options_flow_daily
            WHERE {where}
            GROUP BY date
            ORDER BY date DESC
            LIMIT ?
            """,
            (*params, int(lookback_days)),
        ).fetchall()
    vols = [float(r["day_vol"] or 0) for r in rows]
    n = len(vols)
    if n == 0:
        return {"mean": 0.0, "std": 0.0, "n": 0}
    mean = sum(vols) / n
    if n < 2:
        return {"mean": mean, "std": 0.0, "n": n}
    var = sum((v - mean) ** 2 for v in vols) / (n - 1)
    return {"mean": mean, "std": math.sqrt(var), "n": n}


def stamp_returns(
    event_id: str,
    ret_1d: float | None,
    ret_5d: float | None,
    db_path: Path | None = None,
) -> None:
    resolved = _db_path(db_path)
    init_db(resolved)
    with _connect(resolved) as conn:
        conn.execute(
            """
            UPDATE options_flow_events
            SET ret_1d = ?, ret_5d = ?
            WHERE id = ?
            """,
            (ret_1d, ret_5d, event_id),
        )
        conn.commit()


def stamp_outcomes(
    event_id: str,
    *,
    ret_15m: float | None = None,
    ret_1h: float | None = None,
    ret_eod: float | None = None,
    ret_1d: float | None = None,
    ret_5d: float | None = None,
    done: bool = False,
    db_path: Path | None = None,
) -> None:
    """Write forward-return outcomes; only non-None horizons overwrite.

    ``done`` marks the event fully resolved (all horizons that will ever be
    computable have been) so the filler stops re-selecting it.
    """
    resolved = _db_path(db_path)
    init_db(resolved)
    sets, params = [], []
    for col, val in (
        ("ret_15m", ret_15m),
        ("ret_1h", ret_1h),
        ("ret_eod", ret_eod),
        ("ret_1d", ret_1d),
        ("ret_5d", ret_5d),
    ):
        if val is not None:
            sets.append(f"{col} = ?")
            params.append(float(val))
    if done:
        sets.append("outcomes_done = 1")
    if not sets:
        return
    params.append(event_id)
    with _connect(resolved) as conn:
        conn.execute(
            f"UPDATE options_flow_events SET {', '.join(sets)} WHERE id = ?",
            params,
        )
        conn.commit()


def events_missing_outcomes(*, limit: int = 2000, db_path: Path | None = None) -> list[dict]:
    """Events not yet outcome-resolved, oldest first (the filler's work queue)."""
    resolved = _db_path(db_path)
    init_db(resolved)
    with _connect(resolved) as conn:
        rows = conn.execute(
            """
            SELECT * FROM options_flow_events
            WHERE outcomes_done IS NULL OR outcomes_done = 0
            ORDER BY ts ASC
            LIMIT ?
            """,
            (int(limit),),
        ).fetchall()
    return [_row_to_event(r) for r in rows]


def prune(now_iso: str, db_path: Path | None = None) -> None:
    """Delete events older than EVENTS_PRUNE_DAYS and daily rows older than DAILY_PRUNE_DAYS."""
    resolved = _db_path(db_path)
    init_db(resolved)
    try:
        now = datetime.fromisoformat(str(now_iso).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        now = datetime.now(UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    events_cutoff = (now - timedelta(days=EVENTS_PRUNE_DAYS)).isoformat()
    daily_cutoff = (now - timedelta(days=DAILY_PRUNE_DAYS)).date().isoformat()
    with _connect(resolved) as conn:
        conn.execute(
            "DELETE FROM options_flow_events WHERE ts < ?",
            (events_cutoff,),
        )
        conn.execute(
            "DELETE FROM options_flow_daily WHERE date < ?",
            (daily_cutoff,),
        )
        conn.commit()
