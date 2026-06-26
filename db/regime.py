"""Macro regime series (yield curve, VIX) stored from FRED."""

from __future__ import annotations

from pathlib import Path

from db.base import DEFAULT_DB_PATH
from db.base import connect as _connect


def init_regime_table(db_path: Path = DEFAULT_DB_PATH) -> None:
    with _connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS regime_macro (
                series TEXT NOT NULL,
                date TEXT NOT NULL,
                value REAL NOT NULL,
                PRIMARY KEY (series, date)
            )
            """
        )
        conn.commit()


def upsert_regime(
    rows: list[tuple[str, str, float]],
    *,
    db_path: Path = DEFAULT_DB_PATH,
) -> int:
    """Insert/refresh (series, date, value) rows. Returns rows written."""
    init_regime_table(db_path)
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
            INSERT INTO regime_macro (series, date, value)
            VALUES (?, ?, ?)
            ON CONFLICT(series, date) DO UPDATE SET
                value = excluded.value
            """,
            cleaned,
        )
        conn.commit()
    return len(cleaned)


def latest_observation(
    series: str,
    *,
    as_of: str | None = None,
    db_path: Path = DEFAULT_DB_PATH,
) -> tuple[str, float] | None:
    """Most recent (date, value) for series with date <= as_of (or overall latest)."""
    init_regime_table(db_path)
    with _connect(db_path) as conn:
        if as_of is None:
            row = conn.execute(
                """
                SELECT date, value FROM regime_macro
                WHERE series = ?
                ORDER BY date DESC
                LIMIT 1
                """,
                (series,),
            ).fetchone()
        else:
            row = conn.execute(
                """
                SELECT date, value FROM regime_macro
                WHERE series = ? AND date <= ?
                ORDER BY date DESC
                LIMIT 1
                """,
                (series, as_of),
            ).fetchone()
    if row is None:
        return None
    return str(row[0]), float(row[1])
