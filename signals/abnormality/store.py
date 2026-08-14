"""Load 5-minute and daily equity-bar npz sessions into dense grids."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from datetime import time as dtime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np

from signals.abnormality.constants import N_BUCKETS

ET = ZoneInfo("America/New_York")
FIVE_MIN_NS = 5 * 60 * 1_000_000_000


@dataclass(frozen=True)
class SessionGrid:
    """Dense [n_sym, N_BUCKETS] RTH 5-minute bars. Missing bins are NaN / 0."""

    session: date
    symbols: np.ndarray  # U
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    volume: np.ndarray  # float64, NaN where the bar is absent


def session_open_ns(session: date) -> int:
    return int(datetime.combine(session, dtime(9, 30), ET).timestamp() * 1e9)


def timestamps_to_buckets(t_ns: np.ndarray, session: date) -> np.ndarray:
    """5-minute bucket index 0–77 from unix-ns timestamps. Out of range stays."""
    open_ns = session_open_ns(session)
    return ((t_ns.astype(np.int64) - open_ns) // FIVE_MIN_NS).astype(np.int32)


def load_5min(path: Path) -> SessionGrid:
    """Read one `<session>.npz` into a dense grid. Path stem is the session date."""
    session = date.fromisoformat(path.stem)
    with np.load(path) as z:
        symbols = np.asarray(z["symbols"])
        t_ns = np.asarray(z["t_ns"], np.int64)
        high = np.asarray(z["high"], np.float64)
        low = np.asarray(z["low"], np.float64)
        close = np.asarray(z["close"], np.float64)
        volume = np.asarray(z["volume"], np.float64)
        sym_idx = np.asarray(z["sym_idx"], np.int32)
    n_sym = int(len(symbols))
    h = np.full((n_sym, N_BUCKETS), np.nan)
    lo = np.full((n_sym, N_BUCKETS), np.nan)
    c = np.full((n_sym, N_BUCKETS), np.nan)
    v = np.full((n_sym, N_BUCKETS), np.nan)
    buckets = timestamps_to_buckets(t_ns, session)
    ok = (buckets >= 0) & (buckets < N_BUCKETS) & (sym_idx >= 0) & (sym_idx < n_sym)
    h[sym_idx[ok], buckets[ok]] = high[ok]
    lo[sym_idx[ok], buckets[ok]] = low[ok]
    c[sym_idx[ok], buckets[ok]] = close[ok]
    v[sym_idx[ok], buckets[ok]] = volume[ok]
    return SessionGrid(session=session, symbols=symbols, high=h, low=lo, close=c, volume=v)


@dataclass(frozen=True)
class DailySession:
    session: date
    symbols: np.ndarray
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    volume: np.ndarray


def load_daily(path: Path) -> DailySession:
    session = date.fromisoformat(path.stem)
    with np.load(path) as z:
        return DailySession(
            session=session,
            symbols=np.asarray(z["symbols"]),
            open=np.asarray(z["open"], np.float64),
            high=np.asarray(z["high"], np.float64),
            low=np.asarray(z["low"], np.float64),
            close=np.asarray(z["close"], np.float64),
            volume=np.asarray(z["volume"], np.float64),
        )


def list_sessions(store_dir: Path) -> list[date]:
    """Sessions that have a stats JSON (the completion marker), sorted."""
    out: list[date] = []
    for p in store_dir.glob("*.json"):
        if p.stem == "manifest":
            continue
        try:
            out.append(date.fromisoformat(p.stem))
        except ValueError:
            continue
    return sorted(out)


def window_slice(bucket: int, n_bars: int = 12) -> slice | None:
    """Trailing window [bucket − n_bars, bucket). None if the window isn't full."""
    start = bucket - n_bars
    if start < 0:
        return None
    return slice(start, bucket)
