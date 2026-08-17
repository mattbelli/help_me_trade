"""Nightly baseline artifact: own-norms, peer quintiles, base-state, outcome norms.

Layout: `data/abnormality/baselines-<session>.npz` + `.json` manifest (JSON last).
A `current.json` pointer names the latest. Collector refuses an artifact more
than STALE_SESSIONS old rather than fire against stale norms.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np

from signals.abnormality.base_state import classify_base_state
from signals.abnormality.constants import (
    BASE_HIGH,
    BASE_LOW,
    BASE_NONE,
    FORWARD_2H_BARS,
    LOOKBACK_SESSIONS,
    N_BUCKETS,
    STALE_SESSIONS,
    WARMUP_MIN_SESSIONS,
    WINDOW_BARS,
)
from signals.abnormality.math import assign_quintiles, nanmedian, parkinson_rv_rows, window_sum
from signals.abnormality.store import list_sessions, load_5min, load_daily, window_slice
from trading.market_calendar import trading_days_between

ET = ZoneInfo("America/New_York")
_STATE_CODE = {BASE_NONE: 0, BASE_LOW: 1, BASE_HIGH: 2}


@dataclass
class Baselines:
    session: date
    symbols: np.ndarray
    median_rv: np.ndarray
    median_window_vol: np.ndarray
    median_cum_vol: np.ndarray
    median_fwd_2h: np.ndarray
    median_daily_vol: np.ndarray
    median_dvol: np.ndarray
    quintile: np.ndarray
    base_state: np.ndarray
    base_weeks: np.ndarray
    vol_trend: np.ndarray
    baseline_close: np.ndarray
    n_sessions: np.ndarray

    def __post_init__(self) -> None:
        self.symbol_index = {str(s): i for i, s in enumerate(self.symbols)}

    def stale(self, today: date) -> bool:
        return trading_days_between(self.session, today) > STALE_SESSIONS

    def row(self, symbol: str) -> int | None:
        return self.symbol_index.get(symbol)


def default_dir() -> Path:
    return Path(__file__).resolve().parent.parent.parent / "data" / "abnormality"


def current_pointer(out_dir: Path) -> Path:
    return out_dir / "current.json"


def artifact_sessions(out_dir: Path | None = None) -> list[date]:
    """Sessions with a baseline artifact on disk, ascending."""
    d = out_dir or default_dir()
    return sorted(
        date.fromisoformat(p.stem[len("baselines-"):]) for p in d.glob("baselines-*.npz")
    )


def load_baselines_before(session: date, out_dir: Path | None = None) -> Baselines | None:
    """Newest artifact built STRICTLY before *session* — the registered rule that
    normalizers use sessions <= t-1 (never same-day leakage)."""
    prior = [a for a in artifact_sessions(out_dir) if a < session]
    return load_baselines(out_dir, session=prior[-1]) if prior else None


def load_baselines(out_dir: Path | None = None, *, session: date | None = None) -> Baselines | None:
    d = out_dir or default_dir()
    if session is None:
        ptr = current_pointer(d)
        if not ptr.exists():
            return None
        session = date.fromisoformat(json.loads(ptr.read_text())["session"])
    npz = d / f"baselines-{session.isoformat()}.npz"
    meta = d / f"baselines-{session.isoformat()}.json"
    if not npz.exists() or not meta.exists():
        return None
    with np.load(npz) as z:
        return Baselines(
            session=session,
            symbols=np.asarray(z["symbols"]),
            median_rv=np.asarray(z["median_rv"], np.float64),
            median_window_vol=np.asarray(z["median_window_vol"], np.float64),
            median_cum_vol=np.asarray(z["median_cum_vol"], np.float64),
            median_fwd_2h=np.asarray(z["median_fwd_2h"], np.float64),
            median_daily_vol=np.asarray(z["median_daily_vol"], np.float64),
            median_dvol=np.asarray(z["median_dvol"], np.float64),
            quintile=np.asarray(z["quintile"], np.int8),
            base_state=np.asarray(z["base_state"], np.int8),
            base_weeks=np.asarray(z["base_weeks"], np.float64),
            vol_trend=np.asarray(z["vol_trend"], np.float64),
            baseline_close=np.asarray(z["baseline_close"], np.float64),
            n_sessions=np.asarray(z["n_sessions"], np.int16),
        )


def build_baselines(
    *,
    bars_5min_dir: Path,
    daily_dir: Path,
    out_dir: Path,
    as_of: date | None = None,
) -> Baselines:
    """Rebuild the artifact from the bar stores. Idempotent (same inputs → same npz)."""
    sessions = list_sessions(bars_5min_dir)
    if not sessions:
        raise SystemExit("no completed 5-min sessions")
    as_of = as_of or sessions[-1]
    sessions = [s for s in sessions if s <= as_of]
    lookback = sessions[-LOOKBACK_SESSIONS:]
    if len(lookback) < WARMUP_MIN_SESSIONS:
        raise SystemExit(
            f"need ≥{WARMUP_MIN_SESSIONS} 5-min sessions, have {len(lookback)}"
        )

    syms: set[str] = set()
    grids = []
    for s in lookback:
        g = load_5min(bars_5min_dir / f"{s.isoformat()}.npz")
        grids.append(g)
        syms.update(str(x) for x in g.symbols)
    symbols = np.array(sorted(syms), dtype="U8")
    idx = {str(s): i for i, s in enumerate(symbols)}
    n, S = len(symbols), len(lookback)
    rv = np.full((n, N_BUCKETS, S), np.nan)
    wvol = np.full((n, N_BUCKETS, S), np.nan)
    cum = np.full((n, N_BUCKETS, S), np.nan)
    fwd = np.full((n, N_BUCKETS, S), np.nan)
    last_close = np.full(n, np.nan)

    for si, g in enumerate(grids):
        row = np.array([idx[str(s)] for s in g.symbols], dtype=np.int32)
        last_close[row] = _last_finite(g.close)
        for b in range(WINDOW_BARS, N_BUCKETS):
            win = window_slice(b, WINDOW_BARS)
            assert win is not None
            rv[row, b, si] = parkinson_rv_rows(g.high[:, win], g.low[:, win])
            wvol[row, b, si] = window_sum(g.volume[:, win])
            cum[row, b, si] = window_sum(g.volume[:, :b])
            fwd[row, b, si] = _fwd_max_move(g.close, g.high, g.low, b)

    n_sess = np.sum(np.isfinite(rv), axis=2).astype(np.int16)
    warm = n_sess >= WARMUP_MIN_SESSIONS
    median_rv = nanmedian(rv, axis=2)
    median_wvol = nanmedian(wvol, axis=2)
    median_cum = nanmedian(cum, axis=2)
    median_fwd = nanmedian(fwd, axis=2)
    median_rv = np.where(warm, median_rv, np.nan)
    median_wvol = np.where(warm, median_wvol, np.nan)
    median_cum = np.where(warm, median_cum, np.nan)
    median_fwd = np.where(warm, median_fwd, np.nan)  # the outcome norm needs warmup too

    daily_sessions = [s for s in list_sessions(daily_dir) if s <= as_of]
    median_dvol, median_dvol_sh, base_state, base_weeks, vol_trend, d_close = (
        _daily_panel(daily_dir, daily_sessions, symbols)
    )
    if np.isfinite(d_close).any():
        last_close = np.where(np.isfinite(d_close), d_close, last_close)
    quintile = assign_quintiles(median_dvol)

    out_dir.mkdir(parents=True, exist_ok=True)
    payload = dict(
        symbols=symbols,
        median_rv=median_rv.astype(np.float32),
        median_window_vol=median_wvol.astype(np.float32),
        median_cum_vol=median_cum.astype(np.float32),
        median_fwd_2h=median_fwd.astype(np.float32),
        median_daily_vol=median_dvol_sh.astype(np.float32),
        median_dvol=median_dvol.astype(np.float32),
        quintile=quintile,
        base_state=base_state,
        base_weeks=base_weeks.astype(np.float32),
        vol_trend=vol_trend.astype(np.float32),
        baseline_close=last_close.astype(np.float32),
        n_sessions=n_sess,
    )
    part = out_dir / f"baselines-{as_of.isoformat()}.npz.part"
    final = out_dir / f"baselines-{as_of.isoformat()}.npz"
    with open(part, "wb") as fh:
        np.savez_compressed(fh, **payload)
    part.replace(final)
    n_base = int((base_state > 0).sum())
    meta = {
        "session": as_of.isoformat(),
        "generated_at": datetime.now(ET).isoformat(timespec="seconds"),
        "n_symbols": n,
        "lookback_sessions": [s.isoformat() for s in lookback],
        "warmup_min": WARMUP_MIN_SESSIONS,
        "n_base_state": n_base,
        "bytes": final.stat().st_size,
    }
    (out_dir / f"baselines-{as_of.isoformat()}.json").write_text(json.dumps(meta, indent=1))
    current_pointer(out_dir).write_text(json.dumps({"session": as_of.isoformat()}, indent=1))
    return load_baselines(out_dir, session=as_of)  # type: ignore[return-value]


def _last_finite(a: np.ndarray) -> np.ndarray:
    """Last finite value along axis 1; NaN if a row is all-absent."""
    ok = np.isfinite(a)
    n = a.shape[1]
    idx = n - 1 - np.argmax(ok[:, ::-1], axis=1)
    out = a[np.arange(a.shape[0]), idx]
    return np.where(ok.any(axis=1), out, np.nan)


def _fwd_max_move(close: np.ndarray, high: np.ndarray, low: np.ndarray, bucket: int) -> np.ndarray:
    """Per-name max |move| over the next 2h. NaN when the horizon is incomplete."""
    end = bucket + FORWARD_2H_BARS
    if end > close.shape[1]:
        return np.full(close.shape[0], np.nan)
    entry = close[:, bucket - 1]
    h = high[:, bucket:end]
    lo = low[:, bucket:end]
    with np.errstate(invalid="ignore", divide="ignore"):
        up = np.nanmax(h / entry[:, None] - 1.0, axis=1)
        dn = np.nanmax(1.0 - lo / entry[:, None], axis=1)
        move = np.fmax(up, dn)
    ok = np.isfinite(entry) & (entry > 0) & np.isfinite(move)
    return np.where(ok, move, np.nan)


def _daily_panel(
    daily_dir: Path, sessions: list[date], symbols: np.ndarray
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    n, T = len(symbols), len(sessions)
    idx = {str(s): i for i, s in enumerate(symbols)}
    close = np.full((n, T), np.nan)
    high = np.full((n, T), np.nan)
    low = np.full((n, T), np.nan)
    volume = np.full((n, T), np.nan)
    for ti, s in enumerate(sessions):
        p = daily_dir / f"{s.isoformat()}.npz"
        if not p.exists():
            continue
        d = load_daily(p)
        for j, sym in enumerate(d.symbols):
            i = idx.get(str(sym))
            if i is None:
                continue
            close[i, ti] = d.close[j]
            high[i, ti] = d.high[j]
            low[i, ti] = d.low[j]
            volume[i, ti] = d.volume[j]
    median_dvol = np.full(n, np.nan)
    median_vol = np.full(n, np.nan)
    base_state = np.zeros(n, np.int8)
    base_weeks = np.full(n, np.nan)
    vol_trend = np.full(n, np.nan)
    last_close = np.full(n, np.nan)
    look = min(LOOKBACK_SESSIONS, T)
    for i in range(n):
        finite = np.isfinite(close[i])
        if finite.any():
            last_close[i] = close[i, np.flatnonzero(finite)[-1]]
        if look > 0:
            sl = slice(T - look, T)
            vol_sl = volume[i, sl]
            px_sl = close[i, sl]
            median_vol[i] = np.nanmedian(vol_sl)
            dvol = px_sl * vol_sl
            median_dvol[i] = np.nanmedian(dvol)
        state, strength = classify_base_state(close[i], high[i], low[i], volume[i])
        base_state[i] = _STATE_CODE[state]
        if state != BASE_NONE:
            base_weeks[i] = float(strength.get("weeks_in_base") or np.nan)
            vol_trend[i] = float(strength.get("vol_trend") or np.nan)
    return median_dvol, median_vol, base_state, base_weeks, vol_trend, last_close
