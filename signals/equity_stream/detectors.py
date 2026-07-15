"""Market-wide tape detectors fed by the wildcard stream.

Sweeps and velocity spikes are perception events — facts about the tape —
persisted to equity_tape_events beside the existing block/dark-pool types.
Direction comes from the live NBBO when fresh (the point of owning the quote
wildcard); anything staler is labeled undetermined rather than guessed
(2s-old quotes flip sign ~22% of the time, measured 2026-07-15).

SpreadStats collects baselines only: the liquidity-vacuum detector ships after
we've measured what normal widening looks like — no fabricated thresholds.

All window/cooldown math runs on caller-supplied epoch-ms so tests control the
clock completely.
"""

from __future__ import annotations

import collections
from datetime import datetime, timezone
from typing import Any

from signals.equity_stream.constants import (
    NBBO_FRESH_MS,
    SPREAD_BASELINE_SAMPLES,
    SPREAD_SAMPLE_EVERY,
    SWEEP_COOLDOWN_SECONDS,
    SWEEP_MIN_NOTIONAL_USD,
    SWEEP_MIN_VENUES,
    SWEEP_WINDOW_MS,
    VELOCITY_BUCKET_SECONDS,
    VELOCITY_COOLDOWN_SECONDS,
    VELOCITY_EMA_ALPHA,
    VELOCITY_EMA_SPIKE_CAP_RATIO,
    VELOCITY_MIN_PRINTS,
    VELOCITY_MIXED_NET_FRACTION,
    VELOCITY_SPIKE_RATIO,
    VELOCITY_WARMUP_BUCKETS,
)

# (bid, ask, ts_ms) — runner keeps one per symbol off the quote wildcard.
Nbbo = tuple[float, float, float]


def side_vs_nbbo(price: float, nbbo: Nbbo | None, now_ms: float) -> tuple[str, str]:
    """Classify an aggressor side against the live NBBO → (direction, basis).

    Strict at/through-the-quote rule (same posture as options_flow's
    classify_direction): inside-the-spread prints are undetermined, not leaned —
    midpoint leans were the weakest tier of the measured agreement data.
    """
    if (
        nbbo is not None
        and nbbo[0] > 0
        and nbbo[1] > nbbo[0]
        and now_ms - nbbo[2] <= NBBO_FRESH_MS
    ):
        bid, ask = nbbo[0], nbbo[1]
        if price >= ask:
            return "buy_side", "nbbo_at_print"
        if price <= bid:
            return "sell_side", "nbbo_at_print"
        return "undetermined", "nbbo_at_print"
    return "undetermined", "stale_quote"


def _iso_from_ms(ts_ms: float) -> str:
    return datetime.fromtimestamp(ts_ms / 1000.0, timezone.utc).isoformat()


class SweepAggregator:
    """Cluster ISO-flagged prints per symbol into one sweep event.

    A parent sweep order lands as many child prints across venues within
    milliseconds; per-print events would spam and undercount significance.
    Buckets close when a later print (or the periodic flush) finds them older
    than SWEEP_WINDOW_MS.
    """

    def __init__(self) -> None:
        self._active: dict[str, dict[str, Any]] = {}
        self._cooldown_until_ms: dict[str, float] = {}
        self.emitted = 0
        self.suppressed = 0

    def on_iso_print(
        self,
        *,
        symbol: str,
        price: float,
        size: int,
        exchange: str,
        ts_ms: float,
        nbbo: Nbbo | None,
    ) -> dict[str, Any] | None:
        """Feed one ISO print; returns a finalized event if this print closed
        out the symbol's previous window."""
        closed: dict[str, Any] | None = None
        bucket = self._active.get(symbol)
        if bucket is not None and ts_ms - bucket["start_ms"] > SWEEP_WINDOW_MS:
            closed = self._finalize(symbol, bucket)
            bucket = None
        if bucket is None:
            bucket = {
                "start_ms": ts_ms,
                "end_ms": ts_ms,
                "buy_notional": 0.0,
                "sell_notional": 0.0,
                "gross": 0.0,
                "shares": 0,
                "prints": 0,
                "venues": set(),
                "px_min": price,
                "px_max": price,
                "last_price": price,
                "stale_quote": False,
            }
            self._active[symbol] = bucket
        notional = price * size
        direction, basis = side_vs_nbbo(price, nbbo, ts_ms)
        if direction == "buy_side":
            bucket["buy_notional"] += notional
        elif direction == "sell_side":
            bucket["sell_notional"] += notional
        if basis == "stale_quote":
            bucket["stale_quote"] = True
        bucket["gross"] += notional
        bucket["shares"] += size
        bucket["prints"] += 1
        bucket["venues"].add(exchange)
        bucket["px_min"] = min(bucket["px_min"], price)
        bucket["px_max"] = max(bucket["px_max"], price)
        bucket["last_price"] = price
        bucket["end_ms"] = ts_ms
        return closed

    def flush(self, now_ms: float) -> list[dict[str, Any]]:
        """Finalize every bucket whose window has expired."""
        out: list[dict[str, Any]] = []
        for symbol in list(self._active):
            bucket = self._active[symbol]
            if now_ms - bucket["start_ms"] > SWEEP_WINDOW_MS:
                ev = self._finalize(symbol, bucket)
                if ev is not None:
                    out.append(ev)
        return out

    def _finalize(self, symbol: str, bucket: dict[str, Any]) -> dict[str, Any] | None:
        self._active.pop(symbol, None)
        gross = bucket["gross"]
        venues = bucket["venues"]
        if gross < SWEEP_MIN_NOTIONAL_USD or len(venues) < SWEEP_MIN_VENUES:
            return None
        until = self._cooldown_until_ms.get(symbol, 0.0)
        if bucket["start_ms"] < until:
            self.suppressed += 1
            return None
        self._cooldown_until_ms[symbol] = (
            bucket["start_ms"] + SWEEP_COOLDOWN_SECONDS * 1000.0
        )

        buy, sell = bucket["buy_notional"], bucket["sell_notional"]
        classified = buy + sell
        if classified > 0 and abs(buy - sell) >= classified * 0.5:
            direction = "buy_side" if buy > sell else "sell_side"
        else:
            direction = "undetermined"
        basis = "nbbo_at_print" if not bucket["stale_quote"] else "nbbo_partial"

        start_iso = _iso_from_ms(bucket["start_ms"])
        span_ms = int(bucket["end_ms"] - bucket["start_ms"])
        self.emitted += 1
        return {
            "id": f"eqsw_{start_iso[:10]}_{symbol}_{int(bucket['start_ms'])}",
            "ts": start_iso,
            "symbol": symbol,
            "event_type": "equity_sweep",
            "exchange": "multi",
            "price": round(bucket["last_price"], 4),
            "size": bucket["shares"],
            "notional_usd": round(gross, 2),
            "direction": direction,
            "direction_basis": basis,
            "conditions": ["F"],
            "reason": (
                f"${gross / 1e6:,.1f}M ISO sweep · {bucket['prints']} prints "
                f"across {len(venues)} venues in {max(span_ms, 1)}ms"
            ),
            "caveat": (
                "Intermarket-sweep prints clustered across venues; side judged "
                "against the NBBO at each print. A fact about the tape, not a "
                "buy/sell signal."
            ),
            "detail": {
                "venues": sorted(venues),
                "prints": bucket["prints"],
                "px_min": round(bucket["px_min"], 4),
                "px_max": round(bucket["px_max"], 4),
                "window_ms": span_ms,
                "buy_notional": round(buy, 2),
                "sell_notional": round(sell, 2),
            },
        }


class VelocityDetector:
    """Print-rate spike vs the symbol's own trailing baseline (micro-RVOL).

    Counts prints per VELOCITY_BUCKET_SECONDS bucket; on bucket close, the
    completed count is judged against the EMA of prior buckets. Direction is
    the net NBBO-classified aggressor notional inside the bucket — labeled
    weak, because velocity measures attention, not conviction.
    """

    def __init__(self) -> None:
        self._sym: dict[str, dict[str, Any]] = {}
        self.emitted = 0

    def on_print(
        self,
        *,
        symbol: str,
        price: float,
        size: int,
        ts_ms: float,
        signed_notional: float,
    ) -> dict[str, Any] | None:
        bucket_ms = VELOCITY_BUCKET_SECONDS * 1000.0
        idx = int(ts_ms // bucket_ms)
        st = self._sym.get(symbol)
        if st is None:
            self._sym[symbol] = {
                "idx": idx,
                "count": 1,
                "gross": price * size,
                "net": signed_notional,
                "shares": size,
                "last_price": price,
                "baseline": 0.0,
                "buckets_seen": 0,
                "cooldown_until_ms": 0.0,
            }
            return None
        if idx == st["idx"]:
            st["count"] += 1
            st["gross"] += price * size
            st["net"] += signed_notional
            st["shares"] += size
            st["last_price"] = price
            return None

        event = self._close_bucket(symbol, st)
        self._decay_gap(st, idx)
        st["idx"] = idx
        st["count"] = 1
        st["gross"] = price * size
        st["net"] = signed_notional
        st["shares"] = size
        st["last_price"] = price
        return event

    def flush(self, now_ms: float) -> list[dict[str, Any]]:
        """Close every bucket whose window has ended — without this, a spike in
        the final bucket before a symbol goes silent would only be judged when
        (if ever) the next print arrived."""
        current_idx = int(now_ms // (VELOCITY_BUCKET_SECONDS * 1000.0))
        out: list[dict[str, Any]] = []
        for symbol, st in self._sym.items():
            if current_idx <= st["idx"] or st["count"] == 0:
                continue
            ev = self._close_bucket(symbol, st)
            if ev is not None:
                out.append(ev)
            self._decay_gap(st, current_idx)
            st["idx"] = current_idx
            st["count"] = 0
            st["gross"] = 0.0
            st["net"] = 0.0
            st["shares"] = 0
        return out

    @staticmethod
    def _decay_gap(st: dict[str, Any], new_idx: int) -> None:
        # Empty buckets between prints decay the baseline toward zero — a name
        # that went quiet must not keep its hot baseline. 60 zero-updates at
        # alpha=0.05 ≈ ×0.05, so deeper gaps are indistinguishable from reset.
        gap = min(max(new_idx - st["idx"] - 1, 0), 60)
        for _ in range(gap):
            st["baseline"] *= 1.0 - VELOCITY_EMA_ALPHA
            st["buckets_seen"] += 1

    def _close_bucket(self, symbol: str, st: dict[str, Any]) -> dict[str, Any] | None:
        count = st["count"]
        baseline = st["baseline"]
        eligible = (
            st["buckets_seen"] >= VELOCITY_WARMUP_BUCKETS
            and baseline > 0
            and count >= VELOCITY_MIN_PRINTS
            and count >= baseline * VELOCITY_SPIKE_RATIO
        )
        bucket_start_ms = st["idx"] * VELOCITY_BUCKET_SECONDS * 1000.0
        event: dict[str, Any] | None = None
        if eligible and bucket_start_ms >= st["cooldown_until_ms"]:
            st["cooldown_until_ms"] = (
                bucket_start_ms + VELOCITY_COOLDOWN_SECONDS * 1000.0
            )
            event = self._build_event(symbol, st, bucket_start_ms)
            self.emitted += 1
        # EMA update AFTER judging; spike buckets contribute capped so one
        # burst doesn't raise the bar for the next real one.
        contribution = float(count)
        if baseline > 0:
            contribution = min(
                contribution, baseline * VELOCITY_EMA_SPIKE_CAP_RATIO
            )
        st["baseline"] = (
            baseline * (1.0 - VELOCITY_EMA_ALPHA)
            + contribution * VELOCITY_EMA_ALPHA
        )
        st["buckets_seen"] += 1
        return event

    def _build_event(
        self, symbol: str, st: dict[str, Any], bucket_start_ms: float
    ) -> dict[str, Any]:
        count = st["count"]
        gross = st["gross"]
        net = st["net"]
        baseline = st["baseline"]
        ratio = count / baseline if baseline > 0 else float("inf")
        if gross > 0 and abs(net) >= gross * VELOCITY_MIXED_NET_FRACTION:
            direction = "buy_side" if net > 0 else "sell_side"
        else:
            direction = "mixed"
        start_iso = _iso_from_ms(bucket_start_ms)
        return {
            "id": f"eqvel_{start_iso[:10]}_{symbol}_{int(bucket_start_ms)}",
            "ts": start_iso,
            "symbol": symbol,
            "event_type": "tape_velocity",
            "exchange": None,
            "price": round(st["last_price"], 4),
            "size": st["shares"],
            "notional_usd": round(gross, 2),
            "direction": direction,
            "direction_basis": "nbbo_net_aggressor",
            "conditions": [],
            "reason": (
                f"{count} prints in {VELOCITY_BUCKET_SECONDS}s vs ~{baseline:.1f} "
                f"typical ({ratio:.0f}x) · ${gross / 1e6:,.1f}M traded"
            ),
            "caveat": (
                "Print-rate spike vs this name's own trailing baseline — "
                "attention, not direction; net-aggressor side is weak evidence. "
                "A fact about the tape, not a buy/sell signal."
            ),
            "detail": {
                "prints": count,
                "baseline_prints": round(baseline, 2),
                "ratio": round(ratio, 1),
                "bucket_seconds": VELOCITY_BUCKET_SECONDS,
                "net_aggressor_usd": round(net, 2),
            },
        }


class SpreadStats:
    """Rolling per-symbol spread baselines — calibration for the future
    liquidity-vacuum detector. Samples 1-in-N quotes, keeps a short window of
    spread_bps, and tracks the max widening ratio since the last report."""

    def __init__(self) -> None:
        self._seen = 0
        self._samples: dict[str, collections.deque[float]] = {}
        self._max_ratio: dict[str, tuple[float, float]] = {}  # ratio, bps

    def on_quote(self, symbol: str, bid: float, ask: float) -> None:
        self._seen += 1
        if self._seen % SPREAD_SAMPLE_EVERY:
            return
        if bid <= 0 or ask <= bid:
            return
        bps = (ask - bid) / ((ask + bid) / 2.0) * 1e4
        dq = self._samples.get(symbol)
        if dq is None:
            dq = collections.deque(maxlen=SPREAD_BASELINE_SAMPLES)
            self._samples[symbol] = dq
        if len(dq) >= 8:
            median = sorted(dq)[len(dq) // 2]
            if median > 0:
                ratio = bps / median
                best = self._max_ratio.get(symbol)
                if best is None or ratio > best[0]:
                    self._max_ratio[symbol] = (ratio, bps)
        dq.append(bps)

    def top_wideners(self, n: int = 8) -> list[tuple[str, float, float]]:
        """→ [(symbol, ratio, bps)] worst first; resets the window's maxima."""
        items = sorted(
            ((sym, r, bps) for sym, (r, bps) in self._max_ratio.items()),
            key=lambda t: -t[1],
        )[:n]
        self._max_ratio.clear()
        return items
