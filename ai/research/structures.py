"""Deterministic setup structure detection from technicals content (no I/O, no LLM)."""

from __future__ import annotations

from typing import Any

# Pullback: entry zone extends 1% above nearest support.
PULLBACK_ZONE_ABOVE_SUPPORT = 0.01
# Pullback ready when close is inside zone or within this % above zone high.
PULLBACK_READY_MAX_ABOVE_ZONE_PCT = 1.0
# Pullback forming when close is within this % above zone high.
PULLBACK_FORMING_MAX_ABOVE_ZONE_PCT = 5.0
# Pullback stop offset as fraction of ATR below support.
PULLBACK_STOP_ATR_MULT = 0.75
# Pullback stop fallback when ATR missing: support * this factor.
PULLBACK_STOP_FALLBACK_FACTOR = 0.985
# Breakout: close must be within this % below nearest resistance.
BREAKOUT_MAX_BELOW_RESISTANCE_PCT = 3.0
# Breakout entry zone starts this fraction above resistance.
BREAKOUT_ZONE_LOW_FACTOR = 1.001
# Breakout entry zone high as fraction above resistance.
BREAKOUT_ZONE_HIGH_FACTOR = 1.01
# Breakout stop from support when support is within this % of close.
BREAKOUT_SUPPORT_MAX_DIST_PCT = 8.0
# Breakout stop fallback: close minus this multiple of ATR.
BREAKOUT_STOP_ATR_MULT = 1.5
# Minimum reward/risk to emit a structure.
MIN_REWARD_RISK = 1.5

__all__ = ["detect_structures"]


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if parsed <= 0:
        return None
    return parsed


def _price_str(value: float) -> str:
    return f"{round(value, 2):.2f}"


def _levels(content: dict[str, Any]) -> dict[str, Any]:
    levels = content.get("levels")
    if isinstance(levels, dict):
        return levels
    return {}


def _support_prices(content: dict[str, Any]) -> list[float]:
    raw = _levels(content).get("support_prices")
    if not isinstance(raw, list):
        return []
    prices: list[float] = []
    for item in raw:
        parsed = _to_float(item)
        if parsed is not None:
            prices.append(parsed)
    return prices


def _resistance_prices(content: dict[str, Any]) -> list[float]:
    raw = _levels(content).get("resistance_prices")
    if not isinstance(raw, list):
        return []
    prices: list[float] = []
    for item in raw:
        parsed = _to_float(item)
        if parsed is not None:
            prices.append(parsed)
    return prices


def _reward_risk(
    zone_mid: float, stop: float, target: float
) -> float | None:
    risk = zone_mid - stop
    if risk <= 0:
        return None
    reward = target - zone_mid
    if reward <= 0:
        return None
    return reward / risk


def _structure_base(
    *,
    structure_type: str,
    entry_state: str,
    zone_low: float,
    zone_high: float,
    stop: float,
    targets: list[float],
    why: str,
    distance_to_zone_pct: float,
    rr_to_t1: float,
) -> dict[str, Any]:
    return {
        "type": structure_type,
        "entry_state": entry_state,
        "entry_zone": {"low": _price_str(zone_low), "high": _price_str(zone_high)},
        "stop": _price_str(stop),
        "targets": [_price_str(t) for t in targets],
        "why": why,
        "metrics": {
            "distance_to_zone_pct": round(distance_to_zone_pct, 2),
            "rr_to_t1": round(rr_to_t1, 2),
        },
    }


def _detect_pullback(content: dict[str, Any]) -> dict[str, Any] | None:
    close = _to_float(content.get("latest_close"))
    sma50 = _to_float(content.get("sma50"))
    atr14 = _to_float(content.get("atr14"))
    supports = _support_prices(content)
    resistances = _resistance_prices(content)

    if close is None or sma50 is None or not supports:
        return None

    s = supports[0]
    if close <= sma50 or close < s:
        return None

    zone_low = s
    zone_high = s * (1.0 + PULLBACK_ZONE_ABOVE_SUPPORT)
    distance_pct = (close - zone_high) / close * 100.0

    if distance_pct > PULLBACK_FORMING_MAX_ABOVE_ZONE_PCT:
        return None

    if distance_pct <= PULLBACK_READY_MAX_ABOVE_ZONE_PCT:
        entry_state = "ready"
    else:
        entry_state = "forming"

    if atr14 is not None:
        stop = s - PULLBACK_STOP_ATR_MULT * atr14
    else:
        stop = s * PULLBACK_STOP_FALLBACK_FACTOR

    if stop <= 0 or stop >= zone_low:
        return None

    targets = [r for r in resistances if r > zone_high][:2]
    if not targets:
        return None

    zone_mid = (zone_low + zone_high) / 2.0
    rr = _reward_risk(zone_mid, stop, targets[0])
    if rr is None or rr < MIN_REWARD_RISK:
        return None

    dist_to_support_pct = (close - s) / close * 100.0
    why = (
        f"price {dist_to_support_pct:.1f}% above nearest support {_price_str(s)}; "
        f"close > SMA50"
    )
    return _structure_base(
        structure_type="pullback_to_support",
        entry_state=entry_state,
        zone_low=zone_low,
        zone_high=zone_high,
        stop=stop,
        targets=targets,
        why=why,
        distance_to_zone_pct=distance_pct,
        rr_to_t1=rr,
    )


def _detect_breakout(content: dict[str, Any]) -> dict[str, Any] | None:
    close = _to_float(content.get("latest_close"))
    sma50 = _to_float(content.get("sma50"))
    atr14 = _to_float(content.get("atr14"))
    supports = _support_prices(content)
    resistances = _resistance_prices(content)

    if close is None or sma50 is None or not resistances:
        return None

    r = resistances[0]
    if close <= sma50:
        return None

    dist_below_pct = (r - close) / close * 100.0
    if dist_below_pct <= 0 or dist_below_pct > BREAKOUT_MAX_BELOW_RESISTANCE_PCT:
        return None

    zone_low = r * BREAKOUT_ZONE_LOW_FACTOR
    zone_high = r * BREAKOUT_ZONE_HIGH_FACTOR
    if zone_low <= close:
        return None

    stop: float | None = None
    if supports:
        nearest_support = supports[0]
        if nearest_support < close:
            support_dist_pct = (close - nearest_support) / close * 100.0
            if support_dist_pct <= BREAKOUT_SUPPORT_MAX_DIST_PCT:
                stop = nearest_support

    if stop is None:
        if atr14 is None:
            return None
        stop = close - BREAKOUT_STOP_ATR_MULT * atr14

    if stop <= 0 or stop >= zone_low:
        return None

    targets = [price for price in resistances[1:] if price > zone_high][:2]
    if not targets:
        return None

    zone_mid = (zone_low + zone_high) / 2.0
    rr = _reward_risk(zone_mid, stop, targets[0])
    if rr is None or rr < MIN_REWARD_RISK:
        return None

    why = (
        f"price {dist_below_pct:.1f}% below nearest resistance {_price_str(r)}; "
        f"close > SMA50"
    )
    return _structure_base(
        structure_type="breakout_above_resistance",
        entry_state="forming",
        zone_low=zone_low,
        zone_high=zone_high,
        stop=stop,
        targets=targets,
        why=why,
        distance_to_zone_pct=dist_below_pct,
        rr_to_t1=rr,
    )


def detect_structures(technicals_content: dict[str, Any]) -> list[dict[str, Any]]:
    """Detect pullback/breakout structures from technicals source content."""
    if not isinstance(technicals_content, dict) or not technicals_content:
        return []

    structures: list[dict[str, Any]] = []
    pullback = _detect_pullback(technicals_content)
    if pullback is not None:
        structures.append(pullback)
    breakout = _detect_breakout(technicals_content)
    if breakout is not None:
        structures.append(breakout)
    return structures
