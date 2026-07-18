"""Deterministic actor-group scoring for Fusion Evidence Bus episodes."""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any

from signals.confluence_score import (
    FLOW_PREMIUM_BASE_USD,
    TAPE_FLOOR_FALLBACK_USD,
)

ACTOR_GROUPS = frozenset({"options_actor", "equity_actor"})
ANNOTATE_ONLY_GROUPS = frozenset({"background_crowd", "context"})
GROUP_COMPONENTS: dict[str, frozenset[str]] = {
    "options_actor": frozenset(
        {"option_sweep", "option_block", "unusual_option_volume", "directional_pressure"}
    ),
    "equity_actor": frozenset({"equity_block", "dark_pool"}),
    "background_crowd": frozenset({"rvol_ex_prints"}),
    "structure": frozenset({"session_level", "price_alert"}),
    "attention": frozenset({"reddit_buzz", "stocktwits_buzz"}),
    "catalyst": frozenset({"earnings_proximity"}),
    "context": frozenset({"regime", "index_pressure", "market_shock"}),
}
SIZE_CAP = 4.0
AGREEMENT_BONUS = 1.0
BREADTH_PER_GROUP = 0.5
BREADTH_CAP = 1.5
COLLAPSE_WINDOW_SECONDS = 5 * 60


def options_direction(right: Any, execution_side: Any) -> str:
    """Four-way options convention; unknown side/right is non-directional."""
    opt = str(right or "").strip().lower()[:1]
    side = str(execution_side or "").strip().lower()
    if side in {"buy_side", "ask", "at_ask", "ask_side"}:
        at_ask = True
    elif side in {"sell_side", "bid", "at_bid", "bid_side"}:
        at_ask = False
    else:
        return "none"
    if opt == "c":
        return "bull" if at_ask else "bear"
    if opt == "p":
        return "bear" if at_ask else "bull"
    return "none"


def tape_direction(value: Any) -> str:
    raw = str(value or "").strip().lower()
    if raw in {"buy_side", "buy", "ask", "bull", "bullish"}:
        return "bull"
    if raw in {"sell_side", "sell", "bid", "bear", "bearish"}:
        return "bear"
    return "none"


def _seconds(ts: str) -> float:
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return 0.0


def _value(dot: dict) -> float:
    try:
        return max(0.0, float(dot.get("raw_value") or 0))
    except (TypeError, ValueError):
        return 0.0


def _base(dot: dict) -> float:
    if dot.get("actor_group") == "options_actor":
        return FLOW_PREMIUM_BASE_USD
    detail = dot.get("detail")
    if isinstance(detail, dict):
        try:
            return max(1.0, float(detail.get("floor_usd") or TAPE_FLOOR_FALLBACK_USD))
        except (TypeError, ValueError):
            pass
    return TAPE_FLOOR_FALLBACK_USD


def _log_score(value: float, base: float) -> float:
    if value <= 0 or base <= 0:
        return 0.0
    return max(0.0, min(SIZE_CAP, math.log2(value / base)))


def _collapse_ids(dots: list[dict]) -> set[str]:
    """Return dots excluded by same-direction cross-actor 5-minute collapse."""
    actor = [
        dot
        for dot in dots
        if dot.get("actor_group") in ACTOR_GROUPS
        and dot.get("direction") in {"bull", "bear"}
        and bool(dot.get("stamped", 1))
    ]
    totals: dict[tuple[str, str], tuple[float, float]] = {}
    for group in ACTOR_GROUPS:
        for direction in ("bull", "bear"):
            selected = [
                dot
                for dot in actor
                if dot.get("actor_group") == group and dot.get("direction") == direction
            ]
            totals[(group, direction)] = (
                sum(_value(dot) for dot in selected),
                _base(selected[0]) if selected else 1.0,
            )

    collapsed: set[str] = set()
    for direction in ("bull", "bear"):
        options = [
            dot
            for dot in actor
            if dot.get("actor_group") == "options_actor" and dot.get("direction") == direction
        ]
        equities = [
            dot
            for dot in actor
            if dot.get("actor_group") == "equity_actor" and dot.get("direction") == direction
        ]
        option_score = _log_score(*totals[("options_actor", direction)])
        equity_score = _log_score(*totals[("equity_actor", direction)])
        loser_group = "equity_actor" if option_score >= equity_score else "options_actor"
        for option_dot in options:
            for equity_dot in equities:
                if (
                    abs(_seconds(option_dot["ts"]) - _seconds(equity_dot["ts"]))
                    <= COLLAPSE_WINDOW_SECONDS
                ):
                    loser = equity_dot if loser_group == "equity_actor" else option_dot
                    collapsed.add(str(loser["id"]))
    return collapsed


def score_dots(dots: list[dict]) -> dict[str, Any]:
    """Score an episode from all persisted dots, recomputing correlation exactly."""
    collapsed = _collapse_ids(dots)
    group_scores: dict[str, float] = {}
    group_directions: dict[str, str] = {}
    contested_groups: list[str] = []

    for group in ACTOR_GROUPS:
        directional_scores: dict[str, float] = {}
        for direction in ("bull", "bear"):
            selected = [
                dot
                for dot in dots
                if dot.get("actor_group") == group
                and dot.get("direction") == direction
                and bool(dot.get("stamped", 1))
                and str(dot.get("id")) not in collapsed
            ]
            if selected:
                directional_scores[direction] = _log_score(
                    sum(_value(dot) for dot in selected), _base(selected[0])
                )
        positive = {key: value for key, value in directional_scores.items() if value > 0}
        if positive:
            group_scores[group] = max(positive.values())
            group_directions[group] = max(positive, key=positive.get)
        present_directions = {
            str(dot.get("direction"))
            for dot in dots
            if dot.get("actor_group") == group
            and dot.get("direction") in {"bull", "bear"}
            and bool(dot.get("stamped", 1))
            and str(dot.get("id")) not in collapsed
        }
        if present_directions == {"bull", "bear"}:
            contested_groups.append(group)

    # State groups use their latest/best reading, never a sum.
    for group in GROUP_COMPONENTS:
        if group in ACTOR_GROUPS or group in ANNOTATE_ONLY_GROUPS:
            continue
        selected = [dot for dot in dots if dot.get("actor_group") == group]
        if not selected:
            continue
        best = max(float(dot.get("points") or 0) for dot in selected)
        if best > 0:
            group_scores[group] = best
        directional = [dot for dot in selected if dot.get("direction") in {"bull", "bear"}]
        if directional:
            latest = max(
                directional, key=lambda dot: (_seconds(dot["ts"]), float(dot.get("points") or 0))
            )
            group_directions[group] = str(latest["direction"])

    contested = bool(contested_groups)
    directional_states = set(group_directions.values())
    if contested:
        direction_state = "contested"
    elif len(directional_states) == 1:
        direction_state = f"aligned({next(iter(directional_states))})"
    else:
        direction_state = "mixed"

    agreement = 0.0
    if not contested and len(group_directions) >= 2 and len(directional_states) == 1:
        agreement = AGREEMENT_BONUS
    breadth = min(
        BREADTH_CAP,
        max(0, len(group_scores) - 2) * BREADTH_PER_GROUP,
    )
    total = sum(group_scores.values()) + agreement + breadth
    return {
        "score": round(total, 3),
        "group_scores": {key: round(value, 3) for key, value in group_scores.items()},
        "group_directions": group_directions,
        "agreement_bonus": agreement,
        "breadth_bonus": breadth,
        "collapsed_dot_ids": sorted(collapsed),
        "contested": contested,
        "contested_groups": sorted(contested_groups),
        "direction_state": direction_state,
    }
