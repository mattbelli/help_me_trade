"""Confluence importance score — honest unusualness, never a win%.

Additive surprise over measures the components already carry: how far past its
floor each component is, breadth, cross-source direction agreement, and the
founder's revealed interest. Observe-only first: the score rides along on every
confluence notification so the push threshold can be set empirically.
"""

from __future__ import annotations

import math
from typing import Any

from signals.options_flow.constants import CONV_RVOL_MIN

# Per-component point caps keep one screaming component from drowning the rest.
_SIZE_CAP = 4.0
_RVOL_CAP = 3.0
FLOW_PREMIUM_BASE_USD = 250_000.0
TAPE_FLOOR_FALLBACK_USD = 1_000_000.0

# Revealed interest (founder: watchlist names = high priority). Max, not sum.
_INTEREST_HELD = 1.0
_INTEREST_ALERTED = 0.75
_INTEREST_ACTIVE_LIST = 0.5


def _log2_past(value: float, base: float, cap: float) -> float:
    """Points for how many doublings *value* sits past *base* (0 if at/below)."""
    if value <= 0 or base <= 0:
        return 0.0
    return max(0.0, min(cap, math.log2(value / base)))


def score_confluence(
    *,
    flow_event: dict | None,
    tape_event: dict | None,
    rvol: float | None,
    level: dict | None,
    interest: dict | None = None,
    extended_hours: bool = False,
) -> dict[str, Any]:
    """Score one confluence. Returns {"score": float, "drivers": [str, ...]}."""
    score = 0.0
    drivers: list[str] = []
    n_components = 0

    if flow_event is not None:
        n_components += 1
        premium = float(flow_event.get("premium_usd") or 0)
        pts = _log2_past(premium, FLOW_PREMIUM_BASE_USD, _SIZE_CAP)
        score += pts
        if pts > 0:
            drivers.append(f"{premium / FLOW_PREMIUM_BASE_USD:.1f}x flow base")
        exchanges = flow_event.get("exchanges")
        n_ex = len(exchanges) if isinstance(exchanges, (list, tuple)) else 0
        if n_ex > 3:
            bonus = min(1.0, (n_ex - 3) * 0.25)
            score += bonus
            drivers.append(f"{n_ex}-exchange sweep")

    if tape_event is not None:
        n_components += 1
        notional = float(tape_event.get("notional_usd") or 0)
        detail = tape_event.get("detail")
        floor = TAPE_FLOOR_FALLBACK_USD
        if isinstance(detail, dict) and detail.get("floor_usd"):
            floor = max(float(detail["floor_usd"]), 1.0)
        pts = _log2_past(notional, floor, _SIZE_CAP)
        score += pts
        if pts > 0:
            drivers.append(f"{notional / floor:.1f}x tape floor")

    if rvol is not None and rvol >= CONV_RVOL_MIN:
        n_components += 1
        pts = _log2_past(float(rvol), CONV_RVOL_MIN, _RVOL_CAP)
        score += pts
        if pts > 0:
            drivers.append(f"RVOL {rvol}x")

    if level is not None:
        n_components += 1
        dist_atr = level.get("dist_atr")
        if dist_atr is not None:
            # 0 ATR away = 1 point, at the 0.5 ATR edge = 0.
            closeness = max(0.0, 1.0 - float(dist_atr) / 0.5)
            score += closeness
            drivers.append(f"{level.get('name', 'level')} {float(dist_atr):.2f} ATR")
        if str(level.get("name") or "").lower() == "alert":
            score += 0.5  # the founder drew this line himself

    if n_components > 2:
        breadth = (n_components - 2) * 0.75
        score += breadth
        drivers.append(f"{n_components} components")

    flow_dir = str((flow_event or {}).get("direction") or "undetermined")
    tape_dir = str((tape_event or {}).get("direction") or "undetermined")
    if flow_dir != "undetermined" and flow_dir == tape_dir:
        score += 1.0
        drivers.append("sides agree")

    if interest:
        boost = 0.0
        tag = None
        if interest.get("held"):
            boost, tag = _INTEREST_HELD, "held"
        elif interest.get("alerted"):
            boost, tag = _INTEREST_ALERTED, "alerted"
        elif interest.get("active_watchlist"):
            boost, tag = _INTEREST_ACTIVE_LIST, "active list"
        if tag:
            score += boost
            drivers.append(tag)

    if extended_hours:
        drivers.append("ext-hours")

    return {"score": round(score, 1), "drivers": drivers}
