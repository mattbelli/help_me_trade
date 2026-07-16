"""Opening index-pressure per-minute rows — the gauge's calibration history.

Narrow time-series store (regime_macro pattern), deliberately NOT shoehorned into
the wide options_flow_events table: baselines need per-minute rows queryable across
days. Written by the flow collector's sweep loop during [09:30, 10:00) ET; read by
/api/index-pressure and the ``index_pressure`` perception resource. Rows are
premium sums (floats are fine — this is perception data, not money movement).
"""

from __future__ import annotations

from pathlib import Path

from db.base import DEFAULT_DB_PATH
from db.base import connect as _connect


def init_index_pressure_table(db_path: Path = DEFAULT_DB_PATH) -> None:
    with _connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS index_pressure_minutes (
                underlying TEXT NOT NULL,
                date TEXT NOT NULL,
                minute INTEGER NOT NULL,
                bull_usd REAL NOT NULL DEFAULT 0,
                bear_usd REAL NOT NULL DEFAULT 0,
                unclassified_usd REAL NOT NULL DEFAULT 0,
                n_prints INTEGER NOT NULL DEFAULT 0,
                coverage TEXT,
                PRIMARY KEY (underlying, date, minute)
            )
            """
        )
        conn.commit()


def upsert_minutes(
    rows: list[tuple[str, str, int, float, float, float, int]],
    *,
    coverage: str | None = None,
    db_path: Path = DEFAULT_DB_PATH,
) -> int:
    """Upsert (underlying, date, minute, bull, bear, unclassified, n_prints) rows.

    Re-upserting the same minute REPLACES its values (the aggregator holds the
    whole window in memory and rewrites running totals each flush — last write
    wins, so a mid-window restart only loses prints, never double-counts them).
    """
    if not rows:
        return 0
    init_index_pressure_table(db_path)
    with _connect(db_path) as conn:
        conn.executemany(
            """
            INSERT INTO index_pressure_minutes (
                underlying, date, minute, bull_usd, bear_usd, unclassified_usd,
                n_prints, coverage
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(underlying, date, minute) DO UPDATE SET
                bull_usd = excluded.bull_usd,
                bear_usd = excluded.bear_usd,
                unclassified_usd = excluded.unclassified_usd,
                n_prints = excluded.n_prints,
                coverage = excluded.coverage
            """,
            [(*r, coverage) for r in rows],
        )
        conn.commit()
    return len(rows)


def day_summary(date: str, *, db_path: Path = DEFAULT_DB_PATH) -> list[dict]:
    """Per-underlying window totals for one date, biggest |net| first."""
    init_index_pressure_table(db_path)
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT underlying,
                   SUM(bull_usd) AS bull_usd,
                   SUM(bear_usd) AS bear_usd,
                   SUM(unclassified_usd) AS unclassified_usd,
                   SUM(n_prints) AS n_prints,
                   COUNT(*) AS minutes_with_data,
                   MAX(minute) AS last_minute
            FROM index_pressure_minutes
            WHERE date = ?
            GROUP BY underlying
            """,
            (date,),
        ).fetchall()
    out = []
    for r in rows:
        bull, bear, uncls = float(r["bull_usd"]), float(r["bear_usd"]), float(r["unclassified_usd"])
        gross = bull + bear + uncls
        out.append(
            {
                "underlying": str(r["underlying"]),
                "bull_usd": bull,
                "bear_usd": bear,
                "unclassified_usd": uncls,
                "net_usd": bull - bear,
                "classified_frac": ((bull + bear) / gross) if gross > 0 else 0.0,
                "n_prints": int(r["n_prints"]),
                "minutes_with_data": int(r["minutes_with_data"]),
                "last_minute": int(r["last_minute"]),
            }
        )
    out.sort(key=lambda d: abs(d["net_usd"]), reverse=True)
    return out


def minute_series(
    date: str, underlying: str, *, db_path: Path = DEFAULT_DB_PATH
) -> list[dict]:
    """Per-minute rows for one (date, underlying), minute ascending (sparkline feed)."""
    init_index_pressure_table(db_path)
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT minute, bull_usd, bear_usd, unclassified_usd, n_prints
            FROM index_pressure_minutes
            WHERE date = ? AND underlying = ?
            ORDER BY minute
            """,
            (date, underlying.strip().upper()),
        ).fetchall()
    return [
        {
            "minute": int(r["minute"]),
            "bull_usd": float(r["bull_usd"]),
            "bear_usd": float(r["bear_usd"]),
            "unclassified_usd": float(r["unclassified_usd"]),
            "n_prints": int(r["n_prints"]),
        }
        for r in rows
    ]


def baseline_days(*, db_path: Path = DEFAULT_DB_PATH) -> int:
    """How many distinct dates have accrued — the calibration warm-up gate."""
    init_index_pressure_table(db_path)
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT COUNT(DISTINCT date) FROM index_pressure_minutes"
        ).fetchone()
    return int(row[0]) if row else 0
