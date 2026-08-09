"""Rolling-window flow detectors: D-1 directional burst + D-2 small-print cluster.

Design: ``References/FLOW_INTELLIGENCE_DESIGN_2026-07-11.md`` (Q1). Pure module —
no I/O, no clocks: callers feed normalized prints (``intraday.normalize_print``
shape) and ask for events at a given ``now_ms``. Transport-agnostic by
construction: identical over the capped active set or the wildcard tape.

- D-1 ``flow_burst``: net signed premium per underlying over a rolling 30-min
  window (1-min buckets). Fires the moment the trailing window crosses the floor
  — replaces the whole-day ``directional_pressure`` net as the intraday primitive.
- D-2 ``flow_cluster``: many small prints (each < $100k), one (underlying, right,
  direction-or-unsigned) key, floored on the AGGREGATE — the founder's
  footprint-minimizing-slicer hypothesis. Stamps footprint metrics (modal-size
  share, inter-arrival CV, top-contract share) in ``detail`` so TWAP/iceberg
  becomes a query on accrued data, not a speculative detector (D-3 deferred).

Memory: O(names × 30 buckets) counters — no per-print storage (branch-W load
lane). Direction never guessed: unsigned aggregates emit ``undetermined`` with
an honest caveat; the quotes-follow-heat escalation may upgrade later episodes.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime

from signals.options_flow.constants import INDEX_MEGACAP_SET, ZERO_DTE_EXCLUDE
from signals.options_flow.intraday import is_hygiene_excluded

# --- window geometry --------------------------------------------------------------
BUCKET_MS = 60_000
WINDOW_BUCKETS = 30  # rolling 30 min of 1-min buckets

# --- D-1 burst floors (v1 guesses — re-justify from week-1 live distribution) -----
BURST_SIDED_MIN_USD = 1_000_000  # watchlist sided net / 30 min
BURST_SIDED_MIN_CLASSIFIED = 0.5  # fraction of window premium with a DIR-1 side
BURST_UNSIGNED_MIN_USD = 3_000_000  # market-wide gross when sides unknown
BURST_INDEX_MIN_USD = 5_000_000  # index/megacap: sided-only, higher bar
BURST_REARM_FACTOR = 1.5  # after firing, net must grow 1.5x to re-fire same day

# --- D-2 cluster floors ------------------------------------------------------------
CLUSTER_PRINT_MAX_USD = 100_000  # a "small" print
CLUSTER_PRINT_MIN_USD = 2_000  # dust floor (INGEST_MIN_PREMIUM)
CLUSTER_MIN_AGG_USD = 500_000  # aggregate floor, single name
CLUSTER_INDEX_MIN_AGG_USD = 2_000_000  # index/megacap aggregate floor
CLUSTER_MIN_PRINTS = 25
CLUSTER_REARM_FACTOR = 2.0  # re-fire same key only if aggregate doubles


def _fmt_usd(v: float) -> str:
    if v >= 1_000_000:
        return f"${v / 1_000_000:.1f}M"
    return f"${v / 1_000:.0f}k"


def _iso_from_ms(ms: int) -> str:
    dt = datetime.fromtimestamp(ms / 1000, tz=UTC)
    return dt.strftime("%Y-%m-%dT%H:%M:%S") + f".{dt.microsecond // 1000:03d}Z"


@dataclass
class _Bucket:
    """One (underlying, minute) counter set — O(1) per print, no print storage."""

    gross: float = 0.0
    buy: float = 0.0  # side-classified buy premium
    sell: float = 0.0
    call_net: float = 0.0
    put_net: float = 0.0
    n_prints: int = 0
    # small prints only (D-2), keyed by right
    small: dict[str, "_SmallSide"] = field(default_factory=dict)


@dataclass
class _SmallSide:
    prem: float = 0.0
    n: int = 0
    buy_prem: float = 0.0
    sell_prem: float = 0.0
    ts_ms: list[int] = field(default_factory=list)  # capped, for interarrival CV
    sizes: dict[int, int] = field(default_factory=dict)  # clip size -> count
    contracts: dict[str, float] = field(default_factory=dict)  # occ -> prem

    _TS_CAP = 400  # plenty for CV over 30 min; bounds memory on flood keys


class RollingFlow:
    """Feed prints; poll for burst/cluster events. One instance per session day."""

    def __init__(self) -> None:
        self._buckets: dict[str, dict[int, _Bucket]] = defaultdict(dict)
        self._burst_fired: dict[str, float] = {}  # underlying -> |net| at last fire
        self._cluster_fired: dict[tuple[str, str], float] = {}  # (sym, right) -> agg
        # The transport's per-print stamp on classified prints (dbn_nbbo /
        # stream_quote) — one transport per process, so last-seen is exact.
        # Events must not claim a lane the prints didn't come from: hardcoding
        # "stream_quote" made every sided burst/cluster a false era claim after
        # the 07-27 provider flip (standing-NBBO audit 08-09).
        self._classified_basis = "none"

    # --- ingest ---------------------------------------------------------------

    def add_print(self, p: dict) -> None:
        if is_hygiene_excluded(p.get("conditions") or []):
            return
        try:
            price = float(p["price"])
            size = int(p["size"])
            ts_ms = int(p["ts_ms"])
        except (KeyError, TypeError, ValueError):
            return
        if price <= 0 or size <= 0:
            return
        prem = price * size * 100.0
        if prem < CLUSTER_PRINT_MIN_USD:
            return  # dust — skips even the counters (INGEST_MIN_PREMIUM)
        if p.get("spread_leg") or p.get("likely_spread"):
            return

        sym = str(p.get("underlying") or "").strip().upper()
        if not sym:
            return
        # Pin pre-gauge-lane behavior: index/megacap 0-1 DTE never reached these
        # detectors (unsubscribed before the gauge lane existed; excluded by policy
        # F-3 everywhere else). The gauge lane subscribes them FOR THE GAUGE ONLY —
        # without this guard, bursts/clusters would silently change character the
        # day the lane ships. (audit 2026-07-16 F1)
        if ZERO_DTE_EXCLUDE and sym in INDEX_MEGACAP_SET:
            try:
                dte = int(p["dte"]) if p.get("dte") is not None else None
            except (TypeError, ValueError):
                dte = None
            if dte is not None and dte <= 1:
                return
        minute = ts_ms // BUCKET_MS
        buckets = self._buckets[sym]
        b = buckets.get(minute)
        if b is None:
            b = buckets[minute] = _Bucket()
            self._evict(buckets, minute)

        right = str(p.get("right") or "").strip().lower()[:1] or "?"
        direction = str(p.get("direction") or "undetermined")
        if direction != "undetermined":
            self._classified_basis = str(p.get("direction_basis") or "none")

        b.gross += prem
        b.n_prints += 1
        if direction == "buy_side":
            b.buy += prem
            if right == "c":
                b.call_net += prem
            elif right == "p":
                b.put_net += prem
        elif direction == "sell_side":
            b.sell += prem
            if right == "c":
                b.call_net -= prem
            elif right == "p":
                b.put_net -= prem

        if prem < CLUSTER_PRINT_MAX_USD:
            s = b.small.get(right)
            if s is None:
                s = b.small[right] = _SmallSide()
            s.prem += prem
            s.n += 1
            if direction == "buy_side":
                s.buy_prem += prem
            elif direction == "sell_side":
                s.sell_prem += prem
            if len(s.ts_ms) < _SmallSide._TS_CAP:
                s.ts_ms.append(ts_ms)
            s.sizes[size] = s.sizes.get(size, 0) + 1
            occ = str(p.get("occ_symbol") or "")
            if occ:
                s.contracts[occ] = s.contracts.get(occ, 0.0) + prem

    @staticmethod
    def _evict(buckets: dict[int, _Bucket], now_minute: int) -> None:
        stale = [m for m in buckets if m < now_minute - WINDOW_BUCKETS - 1]
        for m in stale:
            del buckets[m]

    # --- window math ------------------------------------------------------------

    def _window(self, sym: str, now_ms: int) -> list[_Bucket]:
        now_minute = now_ms // BUCKET_MS
        buckets = self._buckets.get(sym) or {}
        return [
            b
            for m, b in buckets.items()
            if now_minute - WINDOW_BUCKETS < m <= now_minute
        ]

    # --- D-1 burst ---------------------------------------------------------------

    def check_bursts(self, now_ms: int) -> list[dict]:
        """Events for every underlying whose trailing window crosses its floor."""
        out: list[dict] = []
        event_date = datetime.fromtimestamp(now_ms / 1000, tz=UTC).date().isoformat()
        for sym in list(self._buckets):
            win = self._window(sym, now_ms)
            if not win:
                continue
            gross = sum(b.gross for b in win)
            if gross <= 0:
                continue
            buy = sum(b.buy for b in win)
            sell = sum(b.sell for b in win)
            call_net = sum(b.call_net for b in win)
            put_net = sum(b.put_net for b in win)
            n_prints = sum(b.n_prints for b in win)
            classified = buy + sell
            classified_frac = classified / gross
            net = buy - sell
            is_index = sym in INDEX_MEGACAP_SET

            sided_floor = BURST_INDEX_MIN_USD if is_index else BURST_SIDED_MIN_USD
            sided = (
                classified_frac >= BURST_SIDED_MIN_CLASSIFIED
                and abs(net) >= sided_floor
            )
            unsigned = (
                not is_index  # v1: no unsigned index bursts (gross baseline needed)
                and classified_frac < BURST_SIDED_MIN_CLASSIFIED
                and gross >= BURST_UNSIGNED_MIN_USD
            )
            if not (sided or unsigned):
                continue

            magnitude = abs(net) if sided else gross
            last = self._burst_fired.get(sym)
            if last is not None and magnitude < last * BURST_REARM_FACTOR:
                continue
            self._burst_fired[sym] = magnitude

            if sided:
                side = "buy_side" if net > 0 else "sell_side"
                word = "buy-side" if net > 0 else "sell-side"
                reason = (
                    f"{sym}: {_fmt_usd(abs(net))} net {word} options premium in 30 min "
                    f"({n_prints} prints, {int(classified_frac * 100)}% classified; "
                    f"calls {_fmt_usd(abs(call_net))} {'+' if call_net >= 0 else '-'}, "
                    f"puts {_fmt_usd(abs(put_net))} {'+' if put_net >= 0 else '-'})"
                )
                caveat = (
                    "30-min rolling net of side-classified premium; "
                    f"{int((1 - classified_frac) * 100)}% of premium had no quote to "
                    "classify against. Facts about the tape, not a signal."
                )
            else:
                side = "undetermined"
                reason = (
                    f"{sym}: {_fmt_usd(gross)} gross options premium in 30 min "
                    f"({n_prints} prints) with only {int(classified_frac * 100)}% "
                    "side-classified"
                )
                caveat = (
                    "UNSIGNED burst — premium is flowing but aggressor side is "
                    "unknown (no quote coverage yet); side may resolve if quote "
                    "escalation catches later prints. Facts, not a signal."
                )

            out.append(
                {
                    "id": f"of_{event_date}_{sym}_burst_{now_ms // BUCKET_MS}",
                    "ts": _iso_from_ms(now_ms),
                    "underlying": sym,
                    "occ_symbol": "",
                    "right": None,
                    "strike": None,
                    "expiry": None,
                    "dte": None,
                    "otm_pct": None,
                    "event_type": "flow_burst",
                    "contracts": None,
                    "premium_usd": round(magnitude, 2),
                    "vol_oi_ratio": None,
                    "vol_zscore": None,
                    "direction": side,
                    "direction_basis": self._classified_basis if sided else "none",
                    "spread_flag": False,
                    "n_prints": n_prints,
                    "exchanges": [],
                    "reason": reason,
                    "caveat": caveat,
                    "detail": {
                        "window_min": 30,
                        "gross_premium": round(gross, 2),
                        "net_premium": round(net, 2),
                        "classified_frac": round(classified_frac, 4),
                        "call_net": round(call_net, 2),
                        "put_net": round(put_net, 2),
                    },
                }
            )
        return out

    # --- D-2 cluster ---------------------------------------------------------------

    def check_clusters(self, now_ms: int) -> list[dict]:
        out: list[dict] = []
        event_date = datetime.fromtimestamp(now_ms / 1000, tz=UTC).date().isoformat()
        for sym in list(self._buckets):
            win = self._window(sym, now_ms)
            if not win:
                continue
            is_index = sym in INDEX_MEGACAP_SET
            floor = CLUSTER_INDEX_MIN_AGG_USD if is_index else CLUSTER_MIN_AGG_USD
            for right in ("c", "p"):
                agg = 0.0
                n = 0
                buy_prem = 0.0
                sell_prem = 0.0
                ts_all: list[int] = []
                sizes: dict[int, int] = {}
                contracts: dict[str, float] = {}
                for b in win:
                    s = b.small.get(right)
                    if s is None:
                        continue
                    agg += s.prem
                    n += s.n
                    buy_prem += s.buy_prem
                    sell_prem += s.sell_prem
                    ts_all.extend(s.ts_ms)
                    for k, v in s.sizes.items():
                        sizes[k] = sizes.get(k, 0) + v
                    for k, p_ in s.contracts.items():
                        contracts[k] = contracts.get(k, 0.0) + p_
                if agg < floor or n < CLUSTER_MIN_PRINTS:
                    continue

                key = (sym, right)
                last = self._cluster_fired.get(key)
                if last is not None and agg < last * CLUSTER_REARM_FACTOR:
                    continue
                self._cluster_fired[key] = agg

                # footprint metrics (D-3 instrumentation — slicing becomes a query)
                modal_share = (max(sizes.values()) / n) if sizes and n else 0.0
                top_contract_share = (
                    max(contracts.values()) / agg if contracts and agg > 0 else 0.0
                )
                cv = None
                if len(ts_all) >= 3:
                    ts_all.sort()
                    gaps = [b_ - a_ for a_, b_ in zip(ts_all, ts_all[1:]) if b_ > a_]
                    if len(gaps) >= 2:
                        mean = sum(gaps) / len(gaps)
                        if mean > 0:
                            var = sum((g - mean) ** 2 for g in gaps) / len(gaps)
                            cv = math.sqrt(var) / mean

                classified = buy_prem + sell_prem
                classified_frac = classified / agg if agg > 0 else 0.0
                net = buy_prem - sell_prem
                if classified_frac >= 0.5 and net != 0:
                    side = "buy_side" if net > 0 else "sell_side"
                    basis = self._classified_basis
                else:
                    side = "undetermined"
                    basis = "none"

                word = {"c": "calls", "p": "puts"}[right]
                span_min = (
                    max(1, round((max(ts_all) - min(ts_all)) / 60_000)) if ts_all else 30
                )
                reason = (
                    f"{sym}: {_fmt_usd(agg)} of {word} in {n} prints < "
                    f"{_fmt_usd(CLUSTER_PRINT_MAX_USD)} over {span_min} min"
                    + (
                        f", {int(modal_share * 100)}% at one clip size"
                        if modal_share >= 0.5
                        else ""
                    )
                )
                caveat = (
                    "aggregate of many small prints; cannot distinguish coordinated "
                    "slicing from a coincident retail crowd"
                    + (
                        f"; side classified on {int(classified_frac * 100)}% of premium"
                        if side != "undetermined"
                        else "; aggressor side unknown"
                    )
                    + ". Facts, not a signal."
                )

                out.append(
                    {
                        "id": f"of_{event_date}_{sym}_{right}_cluster_{now_ms // BUCKET_MS}",
                        "ts": _iso_from_ms(now_ms),
                        "underlying": sym,
                        "occ_symbol": "",
                        "right": "call" if right == "c" else "put",
                        "strike": None,
                        "expiry": None,
                        "dte": None,
                        "otm_pct": None,
                        "event_type": "flow_cluster",
                        "contracts": None,
                        "premium_usd": round(agg, 2),
                        "vol_oi_ratio": None,
                        "vol_zscore": None,
                        "direction": side,
                        "direction_basis": basis,
                        "spread_flag": False,
                        "n_prints": n,
                        "exchanges": [],
                        "reason": reason,
                        "caveat": caveat,
                        "detail": {
                            "window_min": 30,
                            "classified_frac": round(classified_frac, 4),
                            "net_premium": round(net, 2),
                            "modal_size_share": round(modal_share, 4),
                            "n_unique_sizes": len(sizes),
                            "interarrival_cv": round(cv, 4) if cv is not None else None,
                            "top_contract_share": round(top_contract_share, 4),
                            "n_contracts": len(contracts),
                        },
                    }
                )
        return out
