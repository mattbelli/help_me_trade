"""Persistent daily bars for the momentum strategy universe."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from db.base import DEFAULT_DB_PATH
from db.base import connect as _connect


def init_momentum_bars_table(db_path: Path = DEFAULT_DB_PATH) -> None:
    with _connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS momentum_daily_bars (
                symbol TEXT NOT NULL,
                date TEXT NOT NULL,
                open REAL NOT NULL,
                close REAL NOT NULL,
                PRIMARY KEY (symbol, date)
            );
            CREATE INDEX IF NOT EXISTS idx_momentum_daily_bars_symbol_date
                ON momentum_daily_bars (symbol, date);
            """
        )
        conn.commit()


def upsert_daily_bars(
    rows: list[tuple[str, str, float, float]],
    db_path: Path = DEFAULT_DB_PATH,
) -> int:
    """Insert/refresh (symbol, date, open, close) rows. Returns rows written."""
    init_momentum_bars_table(db_path)
    cleaned: list[tuple[str, str, float, float]] = []
    for symbol, date, open_, close in rows:
        sym = str(symbol).strip().upper()
        dt = str(date).strip()[:10]
        if not sym or not dt:
            continue
        cleaned.append((sym, dt, float(open_), float(close)))
    if not cleaned:
        return 0
    with _connect(db_path) as conn:
        conn.executemany(
            """
            INSERT INTO momentum_daily_bars (symbol, date, open, close)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(symbol, date) DO UPDATE SET
                open = excluded.open,
                close = excluded.close
            """,
            cleaned,
        )
        conn.commit()
    return len(cleaned)


def latest_universe_bar_date(db_path: Path = DEFAULT_DB_PATH) -> str | None:
    """Newest bar date across the whole table (ISO string), or None when empty."""
    init_momentum_bars_table(db_path)
    with _connect(db_path) as conn:
        row = conn.execute("SELECT MAX(date) AS d FROM momentum_daily_bars").fetchone()
    return str(row["d"]) if row and row["d"] else None


def load_daily_bars(
    symbols: list[str],
    db_path: Path = DEFAULT_DB_PATH,
) -> dict[str, tuple[list[str], list[float], list[float]]]:
    """Load bars in the shape expected by compute_target_set()."""
    init_momentum_bars_table(db_path)
    cleaned = sorted({s.strip().upper() for s in symbols if s and s.strip()})
    if not cleaned:
        return {}

    placeholders = ",".join("?" * len(cleaned))
    with _connect(db_path) as conn:
        db_rows = conn.execute(
            f"""
            SELECT symbol, date, open, close
            FROM momentum_daily_bars
            WHERE symbol IN ({placeholders})
            ORDER BY symbol, date
            """,
            cleaned,
        ).fetchall()

    raw: dict[str, list[tuple[str, float, float]]] = defaultdict(list)
    for row in db_rows:
        sym = str(row["symbol"])
        raw[sym].append((str(row["date"]), float(row["open"]), float(row["close"])))

    result: dict[str, tuple[list[str], list[float], list[float]]] = {}
    for sym, bar_list in raw.items():
        bar_list.sort(key=lambda x: x[0])
        result[sym] = (
            [b[0] for b in bar_list],
            [b[2] for b in bar_list],
            [b[1] for b in bar_list],
        )
    return result


def latest_bar_date(symbol: str, db_path: Path = DEFAULT_DB_PATH) -> str | None:
    init_momentum_bars_table(db_path)
    sym = symbol.strip().upper()
    if not sym:
        return None
    with _connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT date FROM momentum_daily_bars
            WHERE symbol = ?
            ORDER BY date DESC
            LIMIT 1
            """,
            (sym,),
        ).fetchone()
    return str(row["date"]) if row else None
