"""Opening index-pressure per-minute rows — the gauge's calibration history.

Narrow time-series store (regime_macro pattern), deliberately NOT shoehorned into
the wide options_flow_events table: baselines need per-minute rows queryable across
days. Written by the flow collector's sweep loop during [09:30, 10:00) ET; read by
/api/index-pressure and the ``index_pressure`` perception resource. Rows are
premium sums (floats are fine — this is perception data, not money movement).
"""

from __future__ import annotations

from pathlib import Path

from db.base import DEFAULT_DB_PATH, now_iso
from db.base import connect as _connect


def init_index_pressure_table(db_path: Path = DEFAULT_DB_PATH) -> None:
    with _connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS index_pressure_minutes (
                underlying TEXT NOT NULL,
                date TEXT NOT NULL,
                window TEXT NOT NULL DEFAULT 'open',
                minute INTEGER NOT NULL,
                bull_usd REAL NOT NULL DEFAULT 0,
                bear_usd REAL NOT NULL DEFAULT 0,
                unclassified_usd REAL NOT NULL DEFAULT 0,
                n_prints INTEGER NOT NULL DEFAULT 0,
                coverage TEXT,
                updated_at TEXT,
                PRIMARY KEY (underlying, date, window, minute)
            )
            """
        )
        # Per-day completeness sidecar (audit F5): makes every absence interpretable —
        # "quiet minute" vs "restart-truncated" vs "collector down" vs "half day".
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS index_pressure_days (
                date TEXT NOT NULL,
                window TEXT NOT NULL,
                session_type TEXT NOT NULL DEFAULT 'full',
                minutes_observed INTEGER NOT NULL DEFAULT 0,
                syms INTEGER NOT NULL DEFAULT 0,
                coverage_modes TEXT,
                writer_pid INTEGER,
                writers INTEGER NOT NULL DEFAULT 1,
                finalized_at TEXT,
                PRIMARY KEY (date, window)
            )
            """
        )
        conn.commit()


def upsert_minutes(
    rows: list[tuple[str, str, int, float, float, float, int, str | None]],
    *,
    window: str = "open",
    db_path: Path = DEFAULT_DB_PATH,
) -> int:
    """Upsert (underlying, date, minute, bull, bear, uncls, n_prints, coverage) rows.

    Re-upserting the same minute REPLACES its totals (the aggregator holds the
    whole window in memory and rewrites running totals each flush — last write
    wins, so a mid-window restart only loses prints, never double-counts them).
    Coverage is provenance, stamped at accrual time: a stored value is never
    replaced by a DIFFERENT one — a genuine lane change mid-minute lands as
    'mixed' from the aggregator itself, and a post-restart flush that lost the
    original mode cannot silently relabel history (audit F2).
    """
    if not rows:
        return 0
    init_index_pressure_table(db_path)
    now = now_iso()
    with _connect(db_path) as conn:
        conn.executemany(
            """
            INSERT INTO index_pressure_minutes (
                underlying, date, window, minute, bull_usd, bear_usd,
                unclassified_usd, n_prints, coverage, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(underlying, date, window, minute) DO UPDATE SET
                bull_usd = excluded.bull_usd,
                bear_usd = excluded.bear_usd,
                unclassified_usd = excluded.unclassified_usd,
                n_prints = excluded.n_prints,
                coverage = CASE
                    WHEN index_pressure_minutes.coverage IS NULL
                        THEN excluded.coverage
                    WHEN excluded.coverage IS NULL
                        OR excluded.coverage = index_pressure_minutes.coverage
                        THEN index_pressure_minutes.coverage
                    ELSE 'mixed'
                END,
                updated_at = excluded.updated_at
            """,
            [(r[0], r[1], window, *r[2:], now) for r in rows],
        )
        conn.commit()
    return len(rows)


def record_day(
    *,
    date: str,
    window: str,
    session_type: str,
    minutes_observed: int,
    syms: int,
    coverage_modes: str | None,
    writer_pid: int,
    finalized: bool,
    db_path: Path = DEFAULT_DB_PATH,
) -> None:
    """Upsert the per-day completeness row; bumps `writers` on a pid change
    (a restart marker readable forever, audit F5)."""
    init_index_pressure_table(db_path)
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO index_pressure_days (
                date, window, session_type, minutes_observed, syms,
                coverage_modes, writer_pid, writers, finalized_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)
            ON CONFLICT(date, window) DO UPDATE SET
                session_type = excluded.session_type,
                -- Completeness is MONOTONE (review H2): a post-restart process that
                -- observed less (or nothing) of the window must never shrink or
                -- clobber what an earlier writer recorded.
                minutes_observed = MAX(
                    index_pressure_days.minutes_observed, excluded.minutes_observed
                ),
                syms = MAX(index_pressure_days.syms, excluded.syms),
                coverage_modes = COALESCE(
                    excluded.coverage_modes, index_pressure_days.coverage_modes
                ),
                writers = index_pressure_days.writers
                    + (COALESCE(index_pressure_days.writer_pid, -1)
                       != COALESCE(excluded.writer_pid, -1)),
                writer_pid = excluded.writer_pid,
                finalized_at = COALESCE(
                    index_pressure_days.finalized_at, excluded.finalized_at
                )
            """,
            (
                date,
                window,
                session_type,
                minutes_observed,
                syms,
                coverage_modes,
                writer_pid,
                now_iso() if finalized else None,
            ),
        )
        conn.commit()


def day_record(
    date: str, window: str, *, db_path: Path = DEFAULT_DB_PATH
) -> dict | None:
    init_index_pressure_table(db_path)
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM index_pressure_days WHERE date = ? AND window = ?",
            (date, window),
        ).fetchone()
    return dict(row) if row else None


def day_summary(
    date: str, *, window: str = "open", db_path: Path = DEFAULT_DB_PATH
) -> list[dict]:
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
                   MAX(minute) AS last_minute,
                   GROUP_CONCAT(DISTINCT coverage) AS coverage
            FROM index_pressure_minutes
            WHERE date = ? AND window = ?
            GROUP BY underlying
            """,
            (date, window),
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
                "coverage": r["coverage"],
            }
        )
    out.sort(key=lambda d: abs(d["net_usd"]), reverse=True)
    return out


def minute_series(
    date: str,
    underlying: str,
    *,
    window: str = "open",
    db_path: Path = DEFAULT_DB_PATH,
) -> list[dict]:
    """Per-minute rows for one (date, underlying), minute ascending (sparkline feed)."""
    init_index_pressure_table(db_path)
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT minute, bull_usd, bear_usd, unclassified_usd, n_prints, coverage
            FROM index_pressure_minutes
            WHERE date = ? AND underlying = ? AND window = ?
            ORDER BY minute
            """,
            (date, underlying.strip().upper(), window),
        ).fetchall()
    return [
        {
            "minute": int(r["minute"]),
            "bull_usd": float(r["bull_usd"]),
            "bear_usd": float(r["bear_usd"]),
            "unclassified_usd": float(r["unclassified_usd"]),
            "n_prints": int(r["n_prints"]),
            "coverage": r["coverage"],
        }
        for r in rows
    ]


def day_completeness(date: str, *, db_path: Path = DEFAULT_DB_PATH) -> list[dict]:
    """The completeness-sidecar rows for one date (one per finalized window)."""
    init_index_pressure_table(db_path)
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM index_pressure_days WHERE date = ?", (date,)
        ).fetchall()
    return [dict(r) for r in rows]


def baseline_days(*, window: str = "open", db_path: Path = DEFAULT_DB_PATH) -> int:
    """How many distinct dates have accrued — the calibration warm-up gate."""
    init_index_pressure_table(db_path)
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT COUNT(DISTINCT date) FROM index_pressure_minutes WHERE window = ?",
            (window,),
        ).fetchone()
    return int(row[0]) if row else 0
