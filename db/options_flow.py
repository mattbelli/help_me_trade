"""Options-flow events + daily baseline store (perception signal, Mode A).

Read-only perception substrate — no win_prob / ev / confidence columns exist.
See ``research/fable/2026-07-09_options-flow-screener-design.md`` §4 / §6.
"""

from __future__ import annotations

import json
import logging
import math
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from db.base import DEFAULT_DB_PATH
from db.base import connect as _connect
from db.base import now_iso as _now
from db.schema_helpers import _add_column_if_missing
from signals.options_flow.constants import DAILY_PRUNE_DAYS, EVENTS_PRUNE_DAYS

logger = logging.getLogger(__name__)

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
        # Corpus midpoint side beside DIR-1 (2026-08-07): buy/sell/unclassified/
        # wide_spread per fence_and_classify's scalar mirror. Annotation only —
        # detectors never gate on it. Historic rows stay NULL, never backfilled.
        _add_column_if_missing(conn, "options_flow_events", "mid_side", "TEXT")
        # Per-name empirical rarity (db/flow_rarity.py). NULL = unranked.
        _add_column_if_missing(conn, "options_flow_events", "rarity_p", "REAL")
        _add_column_if_missing(conn, "options_flow_events", "rarity_n", "INTEGER")
        _add_column_if_missing(conn, "options_flow_events", "rarity_cell", "TEXT")
        _add_column_if_missing(conn, "options_flow_events", "rarity_asof", "TEXT")
        conn.commit()
    from db.flow_rarity import init_tables as _init_rarity
    _init_rarity(resolved)
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
    if out.get("rarity_p") is not None:
        from db.flow_rarity import attach_sentence
        attach_sentence(out)
    from db.flow_conviction import attach as attach_conviction
    attach_conviction(out)
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
    from db.flow_rarity import stamp_event
    stamp_event(event, db_path=resolved)
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
                coverage, minutes_into_session, mid_side,
                rarity_p, rarity_n, rarity_cell, rarity_asof
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?
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
                detail = excluded.detail,
                mid_side = excluded.mid_side,
                rarity_p = COALESCE(excluded.rarity_p, options_flow_events.rarity_p),
                rarity_n = COALESCE(excluded.rarity_n, options_flow_events.rarity_n),
                rarity_cell = COALESCE(excluded.rarity_cell, options_flow_events.rarity_cell),
                rarity_asof = COALESCE(excluded.rarity_asof, options_flow_events.rarity_asof)
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
                event.get("mid_side"),
                event.get("rarity_p"),
                event.get("rarity_n"),
                event.get("rarity_cell"),
                event.get("rarity_asof"),
            ),
        )
        conn.commit()


def flag_events_spread(event_ids: list[str], db_path: Path | None = None) -> int:
    """Late-enrichment repair: set spread_flag=1 on already-written events.

    A MULTILEG condition code that lands on a print after its event was
    emitted can no longer change the event's composition — the honest repair
    is the flag, which the unusual-rank penalty and summarize's
    has_spread_dominated both read. Idempotent; returns rows changed.
    """
    if not event_ids:
        return 0
    changed = 0
    resolved = _db_path(db_path)
    with _connect(resolved) as conn:
        for i in range(0, len(event_ids), 500):
            chunk = [str(e) for e in event_ids[i:i + 500]]
            cur = conn.execute(
                "UPDATE options_flow_events SET spread_flag = 1 WHERE id IN "
                f"({','.join('?' * len(chunk))}) "
                "AND (spread_flag IS NULL OR spread_flag = 0)",
                chunk,
            )
            changed += cur.rowcount
        conn.commit()
    return changed


# Heuristic surfacing rank for order=unusual — SURFACING ONLY, unvalidated
# (never a signal/win%). Rewards what makes a print interesting per the academic
# prior (known aggressor, short-dated, swept broadly) and log-tames raw size so
# a routine $8M SPY print can't outrank a $400k out-of-character equity sweep;
# discounts index/ETF roots (hedging plumbing) via the registered root fence.
# spread_penalty is DORMANT today: live events have never carried spread_flag
# (pairing enrichment is metrics-only) — it activates if that ships.
_UNUSUAL_WEIGHTS = {
    "direction_known": 3.0,
    "dte_leq_7": 2.0,
    "dte_leq_30": 1.0,
    "prints_geq_20": 1.5,
    "prints_geq_5": 0.75,
    "exchanges_geq_10": 1.0,
    "aggressive_type": 1.0,  # sweep / directional_pressure over resting blocks
    "vol_oi_scale": 5.0,  # + min(vol_oi_ratio, 20) / this
    "zscore_gt_3": 0.5,
    "index_etf_penalty": -4.0,
    "spread_penalty": -2.0,
}
_UNUSUAL_SCAN_CAP = 200_000

_ROOT_FENCE: Any = None


def _root_fence() -> Any:
    """The one registered root-classification snapshot (frozen; load once)."""
    global _ROOT_FENCE
    if _ROOT_FENCE is None:
        from tools.root_classification_build import load

        _ROOT_FENCE = load(
            Path(__file__).resolve().parent.parent
            / "signals"
            / "options_flow"
            / "root_classification.json"
        )
    return _ROOT_FENCE


def _classify_events(events: list[dict]) -> None:
    """Stamp ``underlying_class`` on each event via the registered fence."""
    try:
        fence = _root_fence()
    except Exception:
        logger.warning("root fence unavailable — events left unclassified", exc_info=True)
        for ev in events:
            ev["underlying_class"] = "unclassified"
        return
    cache: dict[tuple[str, str], str] = {}
    for ev in events:
        root = str(ev.get("underlying") or "").strip().upper()
        day = str(ev.get("ts") or "")[:10]
        key = (root, day)
        cls = cache.get(key)
        if cls is None:
            cls = fence.classify(root, day or None)
            cache[key] = cls
        ev["underlying_class"] = cls


def _unusual_score(ev: dict) -> float:
    w = _UNUSUAL_WEIGHTS
    score = 0.0
    prem = ev.get("premium_usd")
    if prem is not None and float(prem) > 1_000:
        score += math.log(float(prem) / 1_000.0)
    if ev.get("direction") in ("buy_side", "sell_side"):
        score += w["direction_known"]
    dte = ev.get("dte")
    if dte is not None:
        if int(dte) <= 7:
            score += w["dte_leq_7"]
        elif int(dte) <= 30:
            score += w["dte_leq_30"]
    n_prints = ev.get("n_prints") or 0
    if n_prints >= 20:
        score += w["prints_geq_20"]
    elif n_prints >= 5:
        score += w["prints_geq_5"]
    if len(ev.get("exchanges") or []) >= 10:
        score += w["exchanges_geq_10"]
    if ev.get("event_type") in ("sweep", "directional_pressure"):
        score += w["aggressive_type"]
    vol_oi = ev.get("vol_oi_ratio")
    if vol_oi:
        score += min(float(vol_oi), 20.0) / w["vol_oi_scale"]
    zscore = ev.get("vol_zscore")
    if zscore is not None and float(zscore) > 3:
        score += w["zscore_gt_3"]
    if ev.get("underlying_class") in ("index", "etf"):
        score += w["index_etf_penalty"]
    if ev.get("spread_flag"):
        score += w["spread_penalty"]
    return round(score, 2)


def _event_clauses(
    *,
    symbol: str | None,
    event_type: str | None,
    min_premium: float | None,
    since: str | None,
    until: str | None,
) -> tuple[str, list[Any]]:
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
    if until is not None:
        clauses.append("ts < ?")
        params.append(until)
    return " AND ".join(clauses), params


def recent_events(
    *,
    symbol: str | None = None,
    event_type: str | None = None,
    min_premium: float | None = None,
    since: str | None = None,
    until: str | None = None,
    order: str = "recent",
    universe: str | None = None,
    limit: int = 50,
    db_path: Path | None = None,
) -> list[dict]:
    """Event rows; ``limit`` capped at ``_RECENT_EVENTS_CAP``.

    ``order`` is ``recent`` (newest-first, the default), ``premium``
    (largest-first), ``unusual`` — the heuristic surfacing rank
    (``_UNUSUAL_WEIGHTS``) that answers "did anything unusual happen?" without
    letting routine index/ETF size win by sheer dollars — or ``conviction``
    (rarity × tenor × OTM × type; perception, not a forecast). ``universe``
    filters by the registered root class (``equity`` | ``etf`` | ``index``).

    ``order=unusual``, ``order=conviction``, and ``universe`` score/filter in
    Python over the full matching range (scan capped at ``_UNUSUAL_SCAN_CAP``
    newest rows). Unusual rows carry ``underlying_class`` + ``unusual_score``.
    Every returned row already carries ``conviction`` (attached at read).

    ``event_type`` accepts a single type or a comma-separated list (``IN``) so a
    caller can pull just the directional tape (``sweep,block,directional_pressure``)
    without the nightly ``unusual_volume`` batch burying it in the recency window.
    """
    resolved = _db_path(db_path)
    init_db(resolved)
    cap = max(1, min(int(limit), _RECENT_EVENTS_CAP))
    where, params = _event_clauses(
        symbol=symbol,
        event_type=event_type,
        min_premium=min_premium,
        since=since,
        until=until,
    )
    needs_python_pass = order in ("unusual", "conviction") or universe is not None
    if needs_python_pass:
        sql_cap = _UNUSUAL_SCAN_CAP
    else:
        sql_cap = cap
    if order in ("unusual", "conviction") and event_type is None:
        # The EOD unusual_volume batch is WATCHLIST-capped day-aggregates —
        # mixing it into a market-wide unusualness rank re-creates the coverage
        # artifact this order exists to kill. Ask for it explicitly to see it.
        where += " AND event_type != 'unusual_volume'"
    # SQLite sorts NULL smallest, so DESC pushes premium-less rows to the end.
    order_by = "premium_usd DESC" if order == "premium" else "ts DESC"
    params.append(sql_cap)
    with _connect(resolved) as conn:
        rows = conn.execute(
            f"""
            SELECT * FROM options_flow_events
            WHERE {where}
            ORDER BY {order_by}
            LIMIT ?
            """,
            params,
        ).fetchall()
    events = [_row_to_event(r) for r in rows]
    if not needs_python_pass:
        return events
    if universe is not None or order in ("unusual", "conviction"):
        _classify_events(events)
    if universe is not None:
        events = [e for e in events if e["underlying_class"] == universe]
    elif order == "conviction":
        # The registered root fence applies here too (08-17 audit: 10 of the
        # top-100 were ETF/index plumbing — the XLV case). Equity by default;
        # ask for universe=etf/index explicitly to rank those.
        events = [e for e in events if e["underlying_class"] not in ("index", "etf")]
    if order == "unusual":
        for ev in events:
            ev["unusual_score"] = _unusual_score(ev)
        events.sort(key=lambda e: e["unusual_score"], reverse=True)
    elif order == "conviction":
        events.sort(key=lambda e: float(e.get("conviction") or 0.0), reverse=True)
    return events[:cap]


def summarize_events(
    *,
    symbol: str | None = None,
    event_type: str | None = None,
    min_premium: float | None = None,
    since: str | None = None,
    until: str | None = None,
    top: int = 30,
    db_path: Path | None = None,
) -> dict[str, Any]:
    """Full-range aggregate by underlying — SQL over EVERY matching row.

    Exists because grouping a row-capped ``recent_events`` slice silently
    summarized the newest 50 events as if they were the range. Totals here cover
    the whole filter range; only the per-underlying breakdown is trimmed to the
    ``top`` groups by premium, and ``distinct_underlyings`` says what was left.
    """
    resolved = _db_path(db_path)
    init_db(resolved)
    where, params = _event_clauses(
        symbol=symbol,
        event_type=event_type,
        min_premium=min_premium,
        since=since,
        until=until,
    )
    top_n = max(1, min(int(top), 100))
    with _connect(resolved) as conn:
        total_row = conn.execute(
            f"""
            SELECT COUNT(*) AS n, COUNT(DISTINCT underlying) AS names,
                   COALESCE(SUM(premium_usd), 0) AS premium
            FROM options_flow_events WHERE {where}
            """,
            params,
        ).fetchone()
        group_rows = conn.execute(
            f"""
            SELECT underlying,
                   COUNT(*) AS n_events,
                   COALESCE(SUM(premium_usd), 0) AS total_premium_usd,
                   MAX(premium_usd) AS max_single_premium_usd,
                   GROUP_CONCAT(DISTINCT event_type) AS event_types,
                   MAX(spread_flag) AS has_spread_dominated
            FROM options_flow_events WHERE {where}
            GROUP BY underlying
            ORDER BY total_premium_usd DESC
            LIMIT ?
            """,
            [*params, top_n],
        ).fetchall()
    by_underlying = {
        str(r["underlying"]): {
            "count": int(r["n_events"]),
            "total_premium_usd": round(float(r["total_premium_usd"]), 2),
            "max_single_premium_usd": (
                round(float(r["max_single_premium_usd"]), 2)
                if r["max_single_premium_usd"] is not None
                else None
            ),
            "event_types": sorted((r["event_types"] or "").split(",")),
            "has_spread_dominated": bool(r["has_spread_dominated"]),
        }
        for r in group_rows
    }
    return {
        "total_events": int(total_row["n"]),
        "n_underlyings": int(total_row["names"]),
        "total_premium_usd": round(float(total_row["premium"]), 2),
        "by_underlying": by_underlying,
        "underlyings_shown": len(by_underlying),
    }


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
