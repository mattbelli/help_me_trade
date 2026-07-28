"""Macro regime substrate store — daily macro series in their own database.

Its own database (``data/macro.db``) per the board card macro-regime-substrate:
research-scale history (decades × ~16 series) kept off the single-writer
``trading.db``, and the substrate must outlive any one consumer. The small
``regime_macro`` table in trading.db stays where it is — its consumers
(regime sleeves, the fusion regime dot) are untouched; this store is consumed
FIRST for prereg regime-composition reporting (FLOW_BASELINE_PREREG commits
"regime composition reported with every result"), only later as a perception
input.

Same (series, date, value) shape as db/regime.py — deliberately, so a future
consolidation is a copy, not a translation.
"""

from __future__ import annotations

from pathlib import Path

from db.base import connect as _connect

DEFAULT_MACRO_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "macro.db"


def init_macro_table(db_path: Path = DEFAULT_MACRO_DB_PATH) -> None:
    with _connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS macro_series (
                series TEXT NOT NULL,
                date TEXT NOT NULL,
                value REAL NOT NULL,
                PRIMARY KEY (series, date)
            )
            """
        )
        conn.commit()


def upsert_macro(
    rows: list[tuple[str, str, float]],
    *,
    db_path: Path = DEFAULT_MACRO_DB_PATH,
) -> int:
    """Insert/refresh (series, date, value) rows. Returns rows written."""
    init_macro_table(db_path)
    cleaned: list[tuple[str, str, float]] = []
    for series, date, value in rows:
        s = str(series).strip()
        d = str(date).strip()
        if not s or not d:
            continue
        cleaned.append((s, d, float(value)))
    if not cleaned:
        return 0
    with _connect(db_path) as conn:
        conn.executemany(
            """
            INSERT INTO macro_series (series, date, value)
            VALUES (?, ?, ?)
            ON CONFLICT(series, date) DO UPDATE SET
                value = excluded.value
            """,
            cleaned,
        )
        conn.commit()
    return len(cleaned)


def latest_value(
    series: str,
    *,
    as_of: str | None = None,
    db_path: Path = DEFAULT_MACRO_DB_PATH,
) -> tuple[str, float] | None:
    """Most recent (date, value) for series with date <= as_of (or overall latest)."""
    init_macro_table(db_path)
    with _connect(db_path) as conn:
        if as_of is None:
            row = conn.execute(
                "SELECT date, value FROM macro_series WHERE series = ? "
                "ORDER BY date DESC LIMIT 1",
                (series,),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT date, value FROM macro_series WHERE series = ? AND date <= ? "
                "ORDER BY date DESC LIMIT 1",
                (series, as_of),
            ).fetchone()
    if row is None:
        return None
    return str(row[0]), float(row[1])


def series_history(
    series: str,
    *,
    db_path: Path = DEFAULT_MACRO_DB_PATH,
) -> list[tuple[str, float]]:
    """Full (date, value) history for series, date ASCENDING."""
    init_macro_table(db_path)
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT date, value FROM macro_series WHERE series = ? ORDER BY date ASC",
            (series,),
        ).fetchall()
    return [(str(r[0]), float(r[1])) for r in rows]


def coverage(*, db_path: Path = DEFAULT_MACRO_DB_PATH) -> list[dict]:
    """Per-series row count + date range — the backfill's verification read."""
    init_macro_table(db_path)
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT series, COUNT(*) AS n, MIN(date) AS first, MAX(date) AS last
            FROM macro_series GROUP BY series ORDER BY series
            """
        ).fetchall()
    return [
        {
            "series": str(r["series"]),
            "rows": int(r["n"]),
            "first": str(r["first"]),
            "last": str(r["last"]),
        }
        for r in rows
    ]
