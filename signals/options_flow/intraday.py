"""Intraday detection primitives (design §5 F-1/F-2/F-3, BLK-1, DIR-1) — pure, no I/O.

Transport-agnostic: consumes normalized prints from any source (REST poll now,
websocket later). Returns §6 event dicts or None. No network, no DB, no clock
reads except injected ``event_date``. ``reason`` / ``caveat`` are fixed templates.
"""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from signals.options_flow.constants import (
    BLOCK_MIN_CONTRACTS,
    BLOCK_MIN_PREMIUM_USD,
    CANCEL_CONDITIONS,
    EXT_HOURS_CONDITIONS,
    INDEX_MEGACAP_SET,
    LATE_OOS_CONDITIONS,
    MULTILEG_CONDITIONS,
    PRESSURE_MIN_CLASSIFIED,
    PRESSURE_MIN_USD,
    QUOTE_EPS,
    SPREAD_CLUSTER_MIN_STRIKES,
    SPREAD_CLUSTER_WINDOW_MS,
    ZERO_DTE_EXCLUDE,
)
from signals.options_flow.detect import (
    _expiry_month,
    _fmt_premium,
    _fmt_strike,
    _right_letter,
)

_CAVEAT_DIRECTION = (
    "direction inferred from quote rule (~83% class accuracy, Savickas-Wilson 2003); "
    "cannot distinguish new position vs close vs hedge vs spread leg"
)
_CAVEAT_UNDETERMINED = (
    "cannot distinguish new position vs close vs hedge vs spread leg; "
    "direction undetermined (no quote or midpoint print)"
)

_RFC3339_FRAC_RE = re.compile(r"^(.+?\.\d{6})\d*(Z|[+-]\d{2}:\d{2})$")


def _caveat_for_direction(direction: str) -> str:
    if str(direction) != "undetermined":
        return _CAVEAT_DIRECTION
    return _CAVEAT_UNDETERMINED


def _truncate_frac_rfc3339(ts: str) -> str:
    """Truncate fractional seconds beyond 6 digits (nanosecond-safe)."""
    s = str(ts).strip()
    m = _RFC3339_FRAC_RE.match(s)
    if m:
        return m.group(1) + m.group(2)
    return s


def _parse_ts_ms(ts: str) -> int | None:
    try:
        truncated = _truncate_frac_rfc3339(ts)
        dt = datetime.fromisoformat(truncated.replace("Z", "+00:00"))
        return int(dt.timestamp() * 1000)
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _ts_ms_to_iso(ts_ms: int) -> str:
    dt = datetime.fromtimestamp(int(ts_ms) / 1000.0, tz=timezone.utc)
    ms = int(ts_ms) % 1000
    return dt.strftime("%Y-%m-%dT%H:%M:%S") + f".{ms:03d}Z"


def _reason_block(
    *,
    underlying: str,
    expiry: str,
    strike: float,
    right: str,
    contracts: int,
    premium: float,
    direction: str,
    n_prints: int,
) -> str:
    month = _expiry_month(expiry)
    strike_s = _fmt_strike(strike)
    letter = _right_letter(right)
    return (
        f"{underlying} {month} {strike_s}{letter}: block {contracts:,} contracts, "
        f"{_fmt_premium(premium)} premium, {direction} ({n_prints} print)"
    )


def _fmt_signed_premium(net: float) -> str:
    sign = "+" if net >= 0 else "-"
    return f"{sign}{_fmt_premium(abs(net))}"


def _reason_pressure(
    *,
    underlying: str,
    net: float,
    classified_frac: float,
    call_net: float,
    put_net: float,
) -> str:
    side = "buy-side" if net > 0 else "sell-side"
    pct = int(round(classified_frac * 100))
    return (
        f"{underlying} net {_fmt_premium(abs(net))} {side} options premium today "
        f"({pct}% of premium classified; calls {_fmt_signed_premium(call_net)}, "
        f"puts {_fmt_signed_premium(put_net)})"
    )


def normalize_print(raw: dict, *, meta: dict) -> dict | None:
    """Map an Alpaca trade record + contract meta into the normalized print shape."""
    try:
        price = float(raw["p"])
        size = int(raw["s"])
    except (KeyError, TypeError, ValueError):
        return None
    if price <= 0 or size <= 0:
        return None

    ts_raw = raw.get("t")
    if not ts_raw:
        return None
    ts_ms = _parse_ts_ms(str(ts_raw))
    if ts_ms is None:
        return None

    conditions = raw.get("c") or []
    if not isinstance(conditions, list):
        conditions = list(conditions) if conditions else []

    return {
        "occ_symbol": str(meta["occ_symbol"]).strip().upper(),
        "underlying": str(meta["underlying"]).strip().upper(),
        "right": meta["right"],
        "strike": float(meta["strike"]),
        "expiry": str(meta["expiry"]),
        "dte": int(meta["dte"]),
        "ts_ms": ts_ms,
        "price": price,
        "size": size,
        "exchange": str(raw.get("x") or ""),
        "conditions": [str(c) for c in conditions],
    }


def is_hygiene_excluded(
    conditions: list[str],
    *,
    cancels: frozenset[str] = CANCEL_CONDITIONS,
    late: frozenset[str] = LATE_OOS_CONDITIONS,
    ext: frozenset[str] = EXT_HOURS_CONDITIONS,
) -> bool:
    """F-1: True if any condition code is in cancels, late/OOS, or extended-hours."""
    for code in conditions:
        c = str(code)
        if c in cancels or c in late or c in ext:
            return True
    return False


def is_multileg(
    conditions: list[str],
    *,
    multileg: frozenset[str] = MULTILEG_CONDITIONS,
) -> bool:
    """F-2: True if any condition code marks a multi-leg / spread leg."""
    return any(str(c) in multileg for c in conditions)


def tag_likely_spreads(prints: list[dict]) -> None:
    """F-2 clustering — in-place ``spread_leg`` / ``likely_spread`` tags."""
    for p in prints:
        conds = p.get("conditions") or []
        if is_multileg(conds):
            p["spread_leg"] = True

    by_underlying: dict[str, list[dict]] = defaultdict(list)
    for p in prints:
        by_underlying[str(p.get("underlying") or "")].append(p)

    for group in by_underlying.values():
        group.sort(key=lambda p: int(p.get("ts_ms") or 0))
        n = len(group)
        for i in range(n):
            anchor_ts = int(group[i].get("ts_ms") or 0)
            window: list[dict] = []
            for j in range(i, n):
                ts = int(group[j].get("ts_ms") or 0)
                if ts - anchor_ts > SPREAD_CLUSTER_WINDOW_MS:
                    break
                window.append(group[j])
            if len(window) < 2:
                continue
            by_size: dict[int, list[dict]] = defaultdict(list)
            for p in window:
                try:
                    sz = int(p.get("size") or 0)
                except (TypeError, ValueError):
                    continue
                if sz > 0:
                    by_size[sz].append(p)
            for size_group in by_size.values():
                strikes = {p.get("strike") for p in size_group if p.get("strike") is not None}
                if len(strikes) >= SPREAD_CLUSTER_MIN_STRIKES:
                    for p in size_group:
                        p["likely_spread"] = True


def classify_direction(
    *,
    price: float,
    bid: float | None,
    ask: float | None,
    eps: float = QUOTE_EPS,
) -> str:
    """DIR-1 quote rule — ``buy_side``, ``sell_side``, or ``undetermined`` (never guessed)."""
    if bid is None or ask is None:
        return "undetermined"
    b = float(bid)
    a = float(ask)
    if b <= 0 or a <= 0:
        return "undetermined"
    if b >= a:
        return "undetermined"
    p = float(price)
    if p >= a - eps:
        return "buy_side"
    if p <= b + eps:
        return "sell_side"
    return "undetermined"


def detect_block(
    print_: dict,
    *,
    event_date: str,
    direction: str = "undetermined",
    direction_basis: str = "none",
) -> dict | None:
    """BLK-1 block rule (design §5). Returns a §6 event dict or None."""
    sym = str(print_.get("underlying") or "").strip().upper()
    occ = str(print_.get("occ_symbol") or "").strip().upper()
    dte = int(print_.get("dte") or 0)

    if ZERO_DTE_EXCLUDE and sym in INDEX_MEGACAP_SET and dte <= 1:
        return None

    if print_.get("spread_leg") or print_.get("likely_spread"):
        return None

    conditions = print_.get("conditions") or []
    if is_hygiene_excluded(conditions):
        return None

    try:
        size = int(print_.get("size") or 0)
        price = float(print_.get("price") or 0)
    except (TypeError, ValueError):
        return None
    if size < BLOCK_MIN_CONTRACTS or price <= 0:
        return None

    premium = float(size) * price * 100.0
    if premium < BLOCK_MIN_PREMIUM_USD:
        return None

    ts_ms = int(print_.get("ts_ms") or 0)
    exchange = str(print_.get("exchange") or "")

    event: dict[str, Any] = {
        "id": f"of_{event_date}_{sym}_{occ}_blk_{ts_ms}",
        "ts": _ts_ms_to_iso(ts_ms),
        "underlying": sym,
        "occ_symbol": occ,
        "right": print_.get("right"),
        "strike": float(print_.get("strike") or 0),
        "expiry": print_.get("expiry"),
        "dte": dte,
        "otm_pct": None,
        "event_type": "block",
        "contracts": size,
        "premium_usd": premium,
        "vol_oi_ratio": None,
        "vol_zscore": None,
        "direction": direction,
        "direction_basis": direction_basis,
        "spread_flag": False,
        "n_prints": 1,
        "exchanges": [exchange] if exchange else [],
        "reason": "",
        "caveat": _caveat_for_direction(direction),
    }
    event["reason"] = _reason_block(
        underlying=sym,
        expiry=str(print_.get("expiry") or ""),
        strike=float(print_.get("strike") or 0),
        right=str(print_.get("right") or ""),
        contracts=size,
        premium=premium,
        direction=direction,
        n_prints=1,
    )
    return event


def aggregate_directional_pressure(
    *,
    underlying: str,
    prints: list[dict],
    event_date: str,
    direction_basis: str,
) -> dict | None:
    """DIR-1 underlying-day directional pressure (design §5).

    The event id ``of_{event_date}_{sym}_pressure`` is stable so INSERT OR REPLACE
    in the events store updates the same row as the day progresses.
    """
    sym = str(underlying).strip().upper()

    total_non_spread_premium = 0.0
    classified_premium = 0.0
    buy_premium = 0.0
    sell_premium = 0.0
    call_net = 0.0
    put_net = 0.0
    classified_contracts = 0
    classified_prints = 0

    for p in prints:
        if p.get("spread_leg") or p.get("likely_spread"):
            continue
        conditions = p.get("conditions") or []
        if is_hygiene_excluded(conditions):
            continue

        try:
            size = int(p.get("size") or 0)
            price = float(p.get("price") or 0)
        except (TypeError, ValueError):
            continue
        if size <= 0 or price <= 0:
            continue

        prem = float(size) * price * 100.0
        total_non_spread_premium += prem

        direction = str(p.get("direction") or "undetermined")
        if direction == "undetermined":
            continue

        classified_premium += prem
        classified_contracts += size
        classified_prints += 1

        right = str(p.get("right") or "").strip().lower()
        if direction == "buy_side":
            buy_premium += prem
            if right.startswith("c"):
                call_net += prem
            elif right.startswith("p"):
                put_net += prem
        elif direction == "sell_side":
            sell_premium += prem
            if right.startswith("c"):
                call_net -= prem
            elif right.startswith("p"):
                put_net -= prem

    if total_non_spread_premium <= 0:
        return None

    classified_frac = classified_premium / total_non_spread_premium
    if classified_frac < PRESSURE_MIN_CLASSIFIED:
        return None

    net = buy_premium - sell_premium
    if abs(net) < PRESSURE_MIN_USD:
        return None

    direction = "buy_side" if net > 0 else "sell_side"
    return {
        "id": f"of_{event_date}_{sym}_pressure",
        "ts": f"{event_date}T21:00:00Z",
        "underlying": sym,
        "occ_symbol": "",
        "right": None,
        "strike": None,
        "expiry": None,
        "dte": None,
        "otm_pct": None,
        "event_type": "directional_pressure",
        "contracts": classified_contracts,
        "premium_usd": abs(net),
        "vol_oi_ratio": None,
        "vol_zscore": None,
        "direction": direction,
        "direction_basis": direction_basis,
        "spread_flag": False,
        "n_prints": classified_prints,
        "exchanges": [],
        "reason": _reason_pressure(
            underlying=sym,
            net=net,
            classified_frac=classified_frac,
            call_net=call_net,
            put_net=put_net,
        ),
        "caveat": _CAVEAT_DIRECTION,
    }
