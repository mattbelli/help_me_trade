"""Store for the whole-market OPRA directional-flow corpus.

Its own database (``data/flow_corpus.db``) — research-scale capture, kept out of
the 1.6GB ``trading.db`` for the same reason as the options-chain sampler.

Grain is the Tier-3 rollup from `References/FLOW_BASELINE_PREREG_2026-07-26.md`:
per session, per underlying, per right, per DTE bucket, per moneyness bucket,
per side. Both classification arms ride on every row —
``side`` is the midpoint rule (PRIMARY, per Addendum A) and ``strict_side`` is
the at-the-quote rule (SECONDARY) — so a finding can be shown under both without
a second pass over 11M prints.

Two rules this schema exists to enforce, both learned the hard way:
  * **Direction is never aggregated away.** ``options_flow_daily`` was rolled up
    from option bars, has no side column, and is therefore useless for exactly
    the question this corpus was bought to answer.
  * **Dispersion is kept, not just totals.** A z-score needs a variance, so the
    sum of squares travels with the sum.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "flow_corpus.db"

# Bump when a bucket boundary or a classification rule changes. Rows carry it so
# incomparable vintages can never be silently pooled — the same failure mode as
# retuning a detector threshold and invalidating every historical baseline.
SCHEMA_VERSION = 1

DTE_BUCKETS = ("0-7", "8-30", "31-90", "90+")
MNY_BUCKETS = ("itm", "atm", "otm2_10", "otm10p", "unknown")
SIDES = ("buy", "sell", "unclassified")


def connect(db_path: Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def init_db(db_path: Path = DEFAULT_DB_PATH) -> None:
    with connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS flow_rollup (
                date TEXT NOT NULL,
                underlying TEXT NOT NULL,
                right TEXT NOT NULL,
                dte_bucket TEXT NOT NULL,
                mny_bucket TEXT NOT NULL,
                side TEXT NOT NULL,
                strict_side TEXT NOT NULL,
                prints INTEGER NOT NULL,
                contracts INTEGER NOT NULL,
                premium_usd REAL NOT NULL,
                premium_sq REAL NOT NULL,
                premium_max REAL NOT NULL,
                u_premium REAL NOT NULL,
                schema_version INTEGER NOT NULL,
                PRIMARY KEY (date, underlying, right, dte_bucket, mny_bucket,
                             side, strict_side)
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_rollup_und_date "
            "ON flow_rollup (underlying, date)"
        )
        # The spot each session's moneyness was computed against. Stored so a
        # bucket can be audited or recomputed without guessing which close was
        # used, and so a later intraday-spot upgrade is a diff, not a mystery.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS corpus_spot (
                date TEXT NOT NULL,
                underlying TEXT NOT NULL,
                spot REAL NOT NULL,
                source TEXT NOT NULL,
                PRIMARY KEY (date, underlying)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS equity_bars (
                symbol TEXT NOT NULL,
                date TEXT NOT NULL,
                o REAL, h REAL, l REAL, c REAL, v INTEGER,
                adjustment TEXT NOT NULL,
                source TEXT NOT NULL,
                PRIMARY KEY (symbol, date, adjustment)
            )
            """
        )
        # Ingest ledger. A session absent here was never processed; a session
        # present with prints=0 is a recorded holiday, not a silent hole.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS corpus_sessions (
                date TEXT PRIMARY KEY,
                prints INTEGER NOT NULL,
                rows INTEGER NOT NULL,
                underlyings INTEGER NOT NULL,
                unresolved_prints INTEGER NOT NULL,
                no_spot_prints INTEGER NOT NULL,
                schema_version INTEGER NOT NULL,
                ingested_at TEXT NOT NULL
            )
            """
        )


def upsert_rollup(rows: list[tuple], db_path: Path = DEFAULT_DB_PATH) -> int:
    """Replace-on-conflict so a re-run of a session is idempotent."""
    if not rows:
        return 0
    with connect(db_path) as conn:
        conn.executemany(
            "INSERT OR REPLACE INTO flow_rollup (date, underlying, right, "
            "dte_bucket, mny_bucket, side, strict_side, prints, contracts, "
            "premium_usd, premium_sq, premium_max, u_premium, schema_version) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            rows,
        )
    return len(rows)


def upsert_spot(rows: list[tuple], db_path: Path = DEFAULT_DB_PATH) -> int:
    if not rows:
        return 0
    with connect(db_path) as conn:
        conn.executemany(
            "INSERT OR REPLACE INTO corpus_spot (date, underlying, spot, source) "
            "VALUES (?,?,?,?)",
            rows,
        )
    return len(rows)


def upsert_bars(rows: list[tuple], db_path: Path = DEFAULT_DB_PATH) -> int:
    if not rows:
        return 0
    with connect(db_path) as conn:
        conn.executemany(
            "INSERT OR REPLACE INTO equity_bars "
            "(symbol, date, o, h, l, c, v, adjustment, source) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            rows,
        )
    return len(rows)


def bars_for_date(date: str, db_path: Path = DEFAULT_DB_PATH) -> dict[str, float]:
    with connect(db_path) as conn:
        return {
            r["symbol"]: r["c"]
            for r in conn.execute(
                "SELECT symbol, c FROM equity_bars "
                "WHERE date = ? AND adjustment = 'split' AND c IS NOT NULL",
                (date,),
            )
        }


def record_session(
    date: str,
    *,
    prints: int,
    rows: int,
    underlyings: int,
    unresolved_prints: int,
    no_spot_prints: int,
    ingested_at: str,
    db_path: Path = DEFAULT_DB_PATH,
) -> None:
    with connect(db_path) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO corpus_sessions (date, prints, rows, "
            "underlyings, unresolved_prints, no_spot_prints, schema_version, "
            "ingested_at) VALUES (?,?,?,?,?,?,?,?)",
            (date, prints, rows, underlyings, unresolved_prints,
             no_spot_prints, SCHEMA_VERSION, ingested_at),
        )


def ingested_dates(db_path: Path = DEFAULT_DB_PATH) -> set[str]:
    with connect(db_path) as conn:
        return {
            r["date"]
            for r in conn.execute(
                "SELECT date FROM corpus_sessions WHERE schema_version = ?",
                (SCHEMA_VERSION,),
            )
        }
