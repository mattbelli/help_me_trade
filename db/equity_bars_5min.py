"""Read side of the Rust collector's 5-minute bar store (`data/equity_bars_5min.db`).

`collectors/equity_rs` OWNS this file and its schema (`src/db.rs::BARS_DDL`):
`equity_bars_5min(session, symbol, bucket, high, low, close, volume)` for the
whole tape universe, plus `equity_bars_5min_flush(session, through_bucket, ts)`
— the marker the collector advances after each 5-min boundary write ("every
bucket < through_bucket is complete"). Python only reads: the abnormality
sidecar waits on the marker, then loads the session grid; agents/tools can read
intraday bars for any symbol without touching trading.db.
"""

from __future__ import annotations

import os
import sqlite3
from datetime import date
from pathlib import Path

import numpy as np

from signals.abnormality.constants import N_BUCKETS
from signals.abnormality.store import SessionGrid

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PATH = Path(os.environ.get("EQUITY_BARS_DB_PATH", str(ROOT / "data" / "equity_bars_5min.db")))


def _connect(path: Path | None) -> sqlite3.Connection | None:
    p = path or DEFAULT_PATH
    if not p.exists():
        return None
    conn = sqlite3.connect(f"file:{p}?mode=ro", uri=True, timeout=5.0)
    conn.row_factory = sqlite3.Row
    return conn


def through_bucket(session: date, path: Path | None = None) -> int | None:
    """Buckets < the returned value are complete on disk; None = nothing yet."""
    conn = _connect(path)
    if conn is None:
        return None
    try:
        row = conn.execute(
            "SELECT through_bucket FROM equity_bars_5min_flush WHERE session = ?",
            (session.isoformat(),),
        ).fetchone()
    except sqlite3.OperationalError:
        return None
    finally:
        conn.close()
    return int(row[0]) if row else None


def load_session_grid(session: date, path: Path | None = None) -> SessionGrid | None:
    """Dense [n_sym, N_BUCKETS] grid of what the collector has written for *session*."""
    conn = _connect(path)
    if conn is None:
        return None
    try:
        rows = conn.execute(
            "SELECT symbol, bucket, high, low, close, volume FROM equity_bars_5min "
            "WHERE session = ? ORDER BY symbol",
            (session.isoformat(),),
        ).fetchall()
    except sqlite3.OperationalError:
        return None
    finally:
        conn.close()
    if not rows:
        return None
    symbols = sorted({r["symbol"] for r in rows})
    idx = {s: i for i, s in enumerate(symbols)}
    n = len(symbols)
    high = np.full((n, N_BUCKETS), np.nan)
    low = np.full((n, N_BUCKETS), np.nan)
    close = np.full((n, N_BUCKETS), np.nan)
    vol = np.full((n, N_BUCKETS), np.nan)
    for r in rows:
        b = int(r["bucket"])
        if not 0 <= b < N_BUCKETS:
            continue
        i = idx[r["symbol"]]
        high[i, b] = r["high"]
        low[i, b] = r["low"]
        close[i, b] = r["close"]
        vol[i, b] = r["volume"]
    return SessionGrid(
        session=session,
        symbols=np.array(symbols, dtype="U8"),
        high=high, low=low, close=close, volume=vol,
    )
