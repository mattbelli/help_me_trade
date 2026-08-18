"""Mid-session prime: today's completed 5-min bars for the baseline universe,
from Alpaca REST, as a dense grid — so a collector that (re)starts at 11:20
isn't blind to the morning (window coverage + session cumulative volume both
need it). Shared by the in-process runner and the Rust-side sidecar."""

from __future__ import annotations

from datetime import date

import numpy as np

from signals.abnormality.constants import N_BUCKETS
from signals.abnormality.store import SessionGrid, timestamps_to_buckets


def fetch_today_grid(today: date, symbols: list[str], key: str, secret: str) -> SessionGrid:
    """Bounded bulk fetch (`tools/equity_bars_5min_backfill.fetch_session`)."""
    from tools.equity_bars_5min_backfill import fetch_session

    headers = {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}
    per_sym, _n = fetch_session(today, symbols, headers)
    n = len(symbols)
    idx = {s: i for i, s in enumerate(symbols)}
    high = np.full((n, N_BUCKETS), np.nan)
    low = np.full((n, N_BUCKETS), np.nan)
    close = np.full((n, N_BUCKETS), np.nan)
    vol = np.full((n, N_BUCKETS), np.nan)
    for sym, parts in per_sym.items():
        i = idx.get(sym)
        if i is None:
            continue
        t = np.concatenate([p[0] for p in parts])
        h = np.concatenate([p[2] for p in parts])
        lo = np.concatenate([p[3] for p in parts])
        c = np.concatenate([p[4] for p in parts])
        v = np.concatenate([p[5] for p in parts]).astype(np.float64)
        buckets = timestamps_to_buckets(t, today)
        ok = (buckets >= 0) & (buckets < N_BUCKETS)
        high[i, buckets[ok]] = h[ok]
        low[i, buckets[ok]] = lo[ok]
        close[i, buckets[ok]] = c[ok]
        vol[i, buckets[ok]] = v[ok]
    return SessionGrid(
        session=today, symbols=np.array(symbols, dtype="U8"),
        high=high, low=low, close=close, volume=vol,
    )
