"""Live 5-minute abnormality detector — hangs off EquityTapeRunner.

Hot path is O(1) per print (update the symbol's current 1-minute bar). Boundary
work runs on a 5-min timer, never the recv loop. Stale baselines → stand down.
"""

from __future__ import annotations

import logging
import threading
from datetime import date, datetime
from datetime import time as dtime
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np

from signals.abnormality.baselines import Baselines
from signals.abnormality.constants import (
    CA_GAP_PCT,
    CA_VOL_GAP_PCT,
    CA_VOL_RATIO,
    CAVEAT,
    KIND_PAIRED,
    LABEL,
    N_BUCKETS,
    N_BUCKETS_EARLY,
    PAIRED_CAVEAT,
    WINDOW_BARS,
)
from signals.abnormality.engine import BoundaryResult, FireThresholds, PriorFire, evaluate_boundary
from trading.market_calendar import is_early_close, is_trading_day

logger = logging.getLogger(__name__)
ET = ZoneInfo("America/New_York")
OPEN = dtime(9, 30)


class _Sym:
    __slots__ = (
        "m1_minute", "m1_h", "m1_l", "m1_c", "m1_v",
        "m5_h", "m5_l", "m5_c", "m5_v",
        "primed",
    )

    def __init__(self) -> None:
        self.m1_minute = -1
        self.m1_h = 0.0
        self.m1_l = 0.0
        self.m1_c = 0.0
        self.m1_v = 0.0
        n = N_BUCKETS
        self.m5_h = np.full(n, np.nan)
        self.m5_l = np.full(n, np.nan)
        self.m5_c = np.full(n, np.nan)
        self.m5_v = np.full(n, np.nan)
        self.primed = False


class AbnormalityDetector:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._syms: dict[str, _Sym] = {}
        self._baselines: Baselines | None = None
        self._session: date | None = None
        self._n_buckets = N_BUCKETS
        self._ca_suspect: set[str] = set()
        self._prior: dict[str, PriorFire] = {}
        self._last_boundary = -1
        self._thresholds = FireThresholds()
        self.pending_events: list[dict[str, Any]] = []
        self.pending_state: list[dict[str, Any]] = []
        self.stood_down = False
        self.primed = False

    def set_baselines(self, baselines: Baselines | None, today: date) -> None:
        self._baselines = baselines
        self.stood_down = baselines is None or baselines.stale(today)
        if self.stood_down:
            logger.warning(
                "abnormality detector standing down (baselines %s)",
                "missing" if baselines is None else f"stale session={baselines.session}",
            )

    def reset_session(self, session: date) -> None:
        with self._lock:
            self._reset_session_unlocked(session)

    def _reset_session_unlocked(self, session: date) -> None:
        self._session = session
        self._n_buckets = N_BUCKETS_EARLY if is_early_close(session) else N_BUCKETS
        self._syms.clear()
        self._ca_suspect.clear()
        self._prior.clear()
        self._last_boundary = -1
        self.primed = False
        self.pending_events.clear()
        self.pending_state.clear()

    def prime_from_grid(
        self, symbols: np.ndarray, high: np.ndarray, low: np.ndarray,
        close: np.ndarray, volume: np.ndarray,
    ) -> None:
        """Load today's completed 5-min bars. Ratios stay absent until primed."""
        with self._lock:
            for j, sym in enumerate(symbols):
                st = self._syms.setdefault(str(sym), _Sym())
                st.m5_h = np.asarray(high[j], np.float64).copy()
                st.m5_l = np.asarray(low[j], np.float64).copy()
                st.m5_c = np.asarray(close[j], np.float64).copy()
                st.m5_v = np.asarray(volume[j], np.float64).copy()
                st.primed = True
            self.primed = True

    def seed_prior(self, session: date, fires: dict[str, PriorFire]) -> None:
        """Rehydrate the per-symbol cooldown from fires already persisted for
        *session*. The cooldown lived only in process memory (2026-08-17: seven
        collector restarts → 139 of 276 fires were within-60-min repeats, the
        exact population 1.14f excluded as pseudo-replicates). Keeps the LATER
        of DB vs in-memory per symbol."""
        with self._lock:
            if self._session is None:
                # Adopt the session WITHOUT the reset (which would wipe the
                # primed bars); mirrors what the first print would set.
                self._session = session
                self._n_buckets = N_BUCKETS_EARLY if is_early_close(session) else N_BUCKETS
            elif self._session != session:
                return
            for name, fire in fires.items():
                cur = self._prior.get(name)
                if cur is None or fire.bucket > cur.bucket:
                    self._prior[name] = fire

    def on_print(self, symbol: str, price: float, size: int, ts_et: datetime) -> None:
        """O(1) hot path — update the current 1-minute bar. Recv-loop safe."""
        if not is_trading_day(ts_et.date()):
            return
        minutes = (ts_et.hour * 60 + ts_et.minute) - (OPEN.hour * 60 + OPEN.minute)
        if minutes < 0 or minutes >= 6 * 60 + 30:
            return
        if price <= 0 or size <= 0:
            return
        with self._lock:
            if self._session is None or ts_et.date() != self._session:
                self._reset_session_unlocked(ts_et.date())
            st = self._syms.setdefault(symbol, _Sym())
            if st.m1_minute != minutes:
                if st.m1_minute >= 0:
                    self._fold_minute(st)
                st.m1_minute = minutes
                st.m1_h = price
                st.m1_l = price
                st.m1_c = price
                st.m1_v = float(size)
            else:
                if price > st.m1_h:
                    st.m1_h = price
                if price < st.m1_l:
                    st.m1_l = price
                st.m1_c = price
                st.m1_v += float(size)

    def _fold_minute(self, st: _Sym) -> None:
        """Fold a completed 1-minute bar into its 5-minute slot (lock held)."""
        bucket = st.m1_minute // 5
        if bucket < 0 or bucket >= self._n_buckets:
            return
        h, lo, v = st.m1_h, st.m1_l, st.m1_v
        if not np.isfinite(st.m5_h[bucket]):
            st.m5_h[bucket] = h
            st.m5_l[bucket] = lo
            st.m5_v[bucket] = v
        else:
            if h > st.m5_h[bucket]:
                st.m5_h[bucket] = h
            if lo < st.m5_l[bucket]:
                st.m5_l[bucket] = lo
            st.m5_v[bucket] += v
        st.m5_c[bucket] = st.m1_c  # last print in this minute is the running close

    def maybe_boundary(self, now_et: datetime) -> BoundaryResult | None:
        """Timer path. Returns the result when a new 5-min boundary is crossed."""
        if self._session is None or now_et.date() != self._session:
            if is_trading_day(now_et.date()) and (
                now_et.hour > 9 or (now_et.hour == 9 and now_et.minute >= 30)
            ):
                self.reset_session(now_et.date())
            else:
                return None
        minutes = (now_et.hour * 60 + now_et.minute) - (OPEN.hour * 60 + OPEN.minute)
        if minutes < WINDOW_BARS * 5:
            return None
        bucket = minutes // 5
        if now_et.second < 2:
            return None  # let straggling prints land
        if bucket == self._last_boundary or bucket < WINDOW_BARS:
            return None
        if bucket >= self._n_buckets:
            return None
        self._last_boundary = bucket
        return self._run_boundary(bucket, now_et)

    def run_boundary_for_test(self, bucket: int, now_et: datetime) -> BoundaryResult | None:
        """Test hook — skip the wall-clock gate."""
        self._last_boundary = bucket
        return self._run_boundary(bucket, now_et)

    def _run_boundary(self, bucket: int, now_et: datetime) -> BoundaryResult | None:
        bl = self._baselines
        if bl is None or self.stood_down:
            self._publish_unavailable(now_et, "baselines_stale")
            return None
        with self._lock:
            names = list(self._syms.keys())
            n = len(names)
            high = np.full((n, N_BUCKETS), np.nan)
            low = np.full((n, N_BUCKETS), np.nan)
            close = np.full((n, N_BUCKETS), np.nan)
            vol = np.full((n, N_BUCKETS), np.nan)
            for i, name in enumerate(names):
                st = self._syms[name]
                if st.m1_minute >= 0:
                    self._fold_minute(st)
                high[i] = st.m5_h
                low[i] = st.m5_l
                close[i] = st.m5_c
                vol[i] = st.m5_v

        # Align live rows onto the baseline universe; extras are ignored (no norm).
        live_idx = np.array([bl.row(s) for s in names], dtype=object)
        keep = np.array([i is not None for i in live_idx])
        if not keep.any():
            return None
        names_k = [s for s, k in zip(names, keep) if k]
        rows = np.array([int(i) for i in live_idx[keep]], dtype=np.int32)
        high_k = high[keep]
        low_k = low[keep]
        vol_k = vol[keep]
        close_k = close[keep]

        self._apply_ca_guard(names_k, close_k, vol_k, bl, rows, bucket)

        prior_idx: dict[int, PriorFire] = {}
        for i, name in enumerate(names_k):
            p = self._prior.get(name)
            if p is not None:
                prior_idx[i] = p

        result = evaluate_boundary(
            high=high_k,
            low=low_k,
            volume=vol_k,
            bucket=bucket,
            median_rv=bl.median_rv[rows, bucket],
            median_window_vol=bl.median_window_vol[rows, bucket],
            median_cum_vol=bl.median_cum_vol[rows, bucket],
            quintile=bl.quintile[rows],
            base_state=bl.base_state[rows],
            prior=prior_idx,
            thresholds=self._thresholds,
            n_buckets=self._n_buckets,
        )
        # Drop CA-suspect fires.
        if self._ca_suspect:
            hold = [j for j, i in enumerate(result.fire_idx) if names_k[int(i)] not in self._ca_suspect]
            result.fire_idx = result.fire_idx[hold] if hold else np.array([], np.int32)
            result.fire_kind = [result.fire_kind[j] for j in hold]

        ts = now_et.isoformat(timespec="seconds")
        session = now_et.date().isoformat()
        for j, i in enumerate(result.fire_idx):
            i = int(i)
            name = names_k[i]
            kind = result.fire_kind[j]
            comp = float(result.composite[i])
            self._prior[name] = PriorFire(bucket=bucket, composite=comp)
            caveat = CAVEAT if kind != KIND_PAIRED else f"{CAVEAT} {PAIRED_CAVEAT}"
            bs = int(bl.base_state[rows[i]])
            self.pending_events.append(
                {
                    "id": f"{name}:{session}:{bucket}:{kind}",
                    "ts": ts,
                    "symbol": name,
                    "kind": kind,
                    "session": session,
                    "bucket": bucket,
                    "range_ratio": _f(result.range_ratio[i]),
                    "vol_ratio": _f(result.vol_ratio[i]),
                    "rvol_session": _f(result.rvol_session[i]),
                    "range_pctile": _f(result.range_pctile[i]),
                    "vol_pctile": _f(result.vol_pctile[i]),
                    "composite": _f(comp),
                    "base_state": ("none", "base_low", "base_high")[bs],
                    "quintile": int(bl.quintile[rows[i]]),
                    "label": LABEL,
                    "caveat": caveat,
                }
            )
        state_rows = []
        for i, name in enumerate(names_k):
            if name in self._ca_suspect:
                status = "ca_suspect"
            elif not np.isfinite(result.range_ratio[i]) and not np.isfinite(result.vol_ratio[i]):
                status = "absent"
            else:
                status = "live"
            bs = int(bl.base_state[rows[i]])
            state_rows.append(
                {
                    "symbol": name,
                    "ts": ts,
                    "session": session,
                    "bucket": bucket,
                    "range_ratio": _f(result.range_ratio[i]),
                    "vol_ratio": _f(result.vol_ratio[i]),
                    "rvol_session": _f(result.rvol_session[i]),
                    "range_pctile": _f(result.range_pctile[i]),
                    "vol_pctile": _f(result.vol_pctile[i]),
                    "composite": _f(result.composite[i]),
                    "base_state": ("none", "base_low", "base_high")[bs],
                    "quintile": int(bl.quintile[rows[i]]),
                    "status": status,
                    "label": LABEL,
                    "caveat": CAVEAT,
                }
            )
        self.pending_state = state_rows
        return result

    def _apply_ca_guard(
        self,
        names: list[str],
        close: np.ndarray,
        vol: np.ndarray,
        bl: Baselines,
        rows: np.ndarray,
        bucket: int,
    ) -> None:
        if bucket != WINDOW_BARS:
            return
        first = close[:, 0]
        for i, name in enumerate(names):
            base_px = float(bl.baseline_close[int(rows[i])])
            px = float(first[i]) if np.isfinite(first[i]) else float("nan")
            if not np.isfinite(base_px) or base_px <= 0 or not np.isfinite(px) or px <= 0:
                continue
            gap = abs(px / base_px - 1.0)
            if gap > CA_GAP_PCT:
                self._ca_suspect.add(name)
                continue
            med = float(bl.median_window_vol[int(rows[i]), bucket])
            wvol = float(np.nansum(vol[i, bucket - WINDOW_BARS : bucket]))
            if med > 0 and wvol / med > CA_VOL_RATIO and gap > CA_VOL_GAP_PCT:
                self._ca_suspect.add(name)

    def _publish_unavailable(self, now_et: datetime, status: str) -> None:
        self.pending_state = [
            {
                "symbol": "*",
                "ts": now_et.isoformat(timespec="seconds"),
                "session": now_et.date().isoformat(),
                "bucket": None,
                "range_ratio": None,
                "vol_ratio": None,
                "rvol_session": None,
                "range_pctile": None,
                "vol_pctile": None,
                "composite": None,
                "base_state": "none",
                "quintile": None,
                "status": status,
                "label": LABEL,
                "caveat": CAVEAT,
            }
        ]

    def take_pending(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        events, self.pending_events = self.pending_events, []
        state, self.pending_state = self.pending_state, []
        return events, state


def _f(v: Any) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if np.isfinite(x) else None
