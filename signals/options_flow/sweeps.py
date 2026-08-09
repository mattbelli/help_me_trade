"""SWP-1 sweep/split clustering (design §5, Phase 3b) — pure, no I/O.

Consumes normalized prints (with ``direction``/``direction_basis`` already
classified at-print from the stream quote cache) and emits §6 ``sweep`` events.
A cluster is same-contract prints within ``SWEEP_WINDOW_MS`` of its first print;
it closes once no future print can join (window + grace). Intermarket sweep =
≥``SWEEP_MIN_EXCHANGES`` distinct exchanges; split variant = one exchange,
≥``SPLIT_MIN_PRINTS`` prints. Condition ``S`` (ISO) corroborates, never gates.
Restart residual: partially-buffered clusters are lost with the process —
acceptable for an attention router; the poll path stays authoritative.
"""

from __future__ import annotations

from typing import Any

from signals.options_flow.constants import (
    INDEX_MEGACAP_SET,
    MIN_PREMIUM_INDEX_USD,
    MIN_PREMIUM_USD,
    PRESSURE_MIN_CLASSIFIED,
    SPLIT_MIN_PRINTS,
    SWEEP_CLOSE_GRACE_MS,
    SWEEP_MIN_EXCHANGES,
    SWEEP_WINDOW_MS,
    ZERO_DTE_EXCLUDE,
)
from signals.options_flow.detect import (
    _expiry_month,
    _fmt_premium,
    _fmt_strike,
    _right_letter,
)
from signals.options_flow.intraday import (
    _caveat_for_direction,
    _ts_ms_to_iso,
    is_hygiene_excluded,
    is_multileg,
)

ISO_SWEEP_CONDITION = "S"  # OPRA intermarket-sweep condition (probe 2026-07-09)


def _print_eligible(p: dict) -> bool:
    """F-1/F-2/F-3 per-print gates, mirroring ``detect_block``.

    Reads the condition codes directly as well as the drain-time tags: on the
    paired databento lane a code can land on the print AFTER
    ``tag_likely_spreads`` ran, and this gate is re-applied at cluster close
    (2026-08-03 late-enrichment round).
    """
    if p.get("spread_leg") or p.get("likely_spread"):
        return False
    conditions = p.get("conditions") or []
    if is_multileg(conditions):
        return False
    if is_hygiene_excluded(conditions):
        return False
    sym = str(p.get("underlying") or "").strip().upper()
    dte = int(p.get("dte") or 0)
    if ZERO_DTE_EXCLUDE and sym in INDEX_MEGACAP_SET and dte <= 1:
        return False
    try:
        return int(p.get("size") or 0) > 0 and float(p.get("price") or 0) > 0
    except (TypeError, ValueError):
        return False


def _cluster_direction(prints: list[dict], premium: float) -> tuple[str, str]:
    """DIR-1 over the cluster: majority classified premium side, never guessed."""
    buy = 0.0
    sell = 0.0
    basis = "none"
    for p in prints:
        prem = float(p["size"]) * float(p["price"]) * 100.0
        direction = str(p.get("direction") or "undetermined")
        if direction == "buy_side":
            buy += prem
        elif direction == "sell_side":
            sell += prem
        else:
            continue
        if basis == "none":
            # The transport's own per-print stamp (dbn_nbbo / stream_quote),
            # never a hardcoded lane literal: matching "stream_quote" alone
            # left every sided sweep claiming basis "none" for the whole
            # databento era (07-27 provider flip; standing-NBBO audit 08-09).
            basis = str(p.get("direction_basis") or "none")
    classified = buy + sell
    if premium <= 0 or classified / premium < PRESSURE_MIN_CLASSIFIED:
        return "undetermined", "none"
    direction = "buy_side" if buy >= sell else "sell_side"
    return direction, basis


def _reason_sweep(
    *,
    underlying: str,
    expiry: str,
    strike: float,
    right: str,
    contracts: int,
    premium: float,
    direction: str,
    n_prints: int,
    n_exchanges: int,
    iso_flagged: bool,
) -> str:
    month = _expiry_month(expiry)
    strike_s = _fmt_strike(strike)
    letter = _right_letter(right)
    if n_exchanges >= SWEEP_MIN_EXCHANGES:
        shape = f"sweep across {n_exchanges} exchanges"
    else:
        shape = "split order on one exchange"
    iso = ", ISO-flagged" if iso_flagged else ""
    return (
        f"{underlying} {month} {strike_s}{letter}: {shape} — {contracts:,} contracts, "
        f"{_fmt_premium(premium)} premium, {direction} ({n_prints} prints{iso})"
    )


def _cluster_event(cluster: list[dict], event_date: str) -> dict | None:
    """Score one closed cluster against SWP-1; §6 event dict or None."""
    exchanges = sorted({str(p.get("exchange") or "") for p in cluster if p.get("exchange")})
    n_prints = len(cluster)
    is_sweep = len(exchanges) >= SWEEP_MIN_EXCHANGES
    is_split = len(exchanges) <= 1 and n_prints >= SPLIT_MIN_PRINTS
    if not (is_sweep or is_split):
        return None

    first = cluster[0]
    sym = str(first.get("underlying") or "").strip().upper()
    occ = str(first.get("occ_symbol") or "").strip().upper()

    contracts = sum(int(p["size"]) for p in cluster)
    premium = sum(float(p["size"]) * float(p["price"]) * 100.0 for p in cluster)
    floor = MIN_PREMIUM_INDEX_USD if sym in INDEX_MEGACAP_SET else MIN_PREMIUM_USD
    if premium < floor:
        return None

    iso_flagged = any(
        ISO_SWEEP_CONDITION in (p.get("conditions") or []) for p in cluster
    )
    direction, basis = _cluster_direction(cluster, premium)
    anchor_ts = int(first.get("ts_ms") or 0)

    event: dict[str, Any] = {
        "id": f"of_{event_date}_{sym}_{occ}_swp_{anchor_ts}",
        "ts": _ts_ms_to_iso(anchor_ts),
        "underlying": sym,
        "occ_symbol": occ,
        "right": first.get("right"),
        "strike": float(first.get("strike") or 0),
        "expiry": first.get("expiry"),
        "dte": int(first.get("dte") or 0),
        "otm_pct": None,
        "event_type": "sweep",
        "contracts": contracts,
        "premium_usd": premium,
        "vol_oi_ratio": None,
        "vol_zscore": None,
        "direction": direction,
        "direction_basis": basis,
        "spread_flag": False,
        "n_prints": n_prints,
        "exchanges": exchanges,
        "reason": "",
        "caveat": _caveat_for_direction(direction),
    }
    event["reason"] = _reason_sweep(
        underlying=sym,
        expiry=str(first.get("expiry") or ""),
        strike=float(first.get("strike") or 0),
        right=str(first.get("right") or ""),
        contracts=contracts,
        premium=premium,
        direction=direction,
        n_prints=n_prints,
        n_exchanges=len(exchanges),
        iso_flagged=iso_flagged,
    )
    return event


class SweepBuffer:
    """Per-contract print buffer; clusters close once no print can still join."""

    _PER_OCC_CAP = 500  # defensive bound; a real cluster is tens of prints

    def __init__(self) -> None:
        self._by_occ: dict[str, list[dict]] = {}

    def add(self, prints: list[dict]) -> None:
        for p in prints:
            if not _print_eligible(p):
                continue
            occ = str(p.get("occ_symbol") or "").strip().upper()
            if not occ or not p.get("ts_ms"):
                continue
            bucket = self._by_occ.setdefault(occ, [])
            if len(bucket) < self._PER_OCC_CAP:
                bucket.append(p)

    def close_ready(self, now_ms: int, event_date: str) -> list[dict]:
        """Close every cluster whose window (+grace) has passed; return events."""
        events: list[dict] = []
        for occ in list(self._by_occ):
            bucket = self._by_occ[occ]
            bucket.sort(key=lambda p: int(p["ts_ms"]))
            remaining: list[dict] = []
            i = 0
            n = len(bucket)
            while i < n:
                anchor = int(bucket[i]["ts_ms"])
                j = i
                while j < n and int(bucket[j]["ts_ms"]) - anchor <= SWEEP_WINDOW_MS:
                    j += 1
                # Re-filter at close: a condition code that arrived after
                # add()'s gate (late pairing enrichment) excludes the print
                # here, while the cluster is still open.
                cluster = [p for p in bucket[i:j] if _print_eligible(p)]
                if now_ms - anchor > SWEEP_WINDOW_MS + SWEEP_CLOSE_GRACE_MS:
                    ev = _cluster_event(cluster, event_date) if cluster else None
                    if ev is not None:
                        events.append(ev)
                        # Backref for the late-enrichment repair: a code that
                        # lands on one of these prints after THIS point can
                        # only be applied by flagging the stored event row.
                        for p in cluster:
                            p["_event_id"] = ev["id"]
                else:
                    remaining.extend(bucket[i:])
                    break
                i = j
            if remaining:
                self._by_occ[occ] = remaining
            else:
                del self._by_occ[occ]
        return events
