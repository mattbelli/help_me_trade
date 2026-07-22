"""Deterministic actor-group scoring for Fusion Evidence Bus episodes."""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

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


# ============================================================================
# v2 decayed masses + conviction (Fusion Horizon Redesign 2026-07-21, §2.1/§2.2)
#
# ADDED ALONGSIDE score_dots — score_dots and the live shadow gate are unchanged.
# This layer replaces the disease measured 07-21: undecayed all-day sums and a
# binary "contested-if-ever" bit (§1). Here direction is a SIGNED, exponentially
# DECAYED sum, so afternoon evidence no longer carries morning noise and a
# contested symbol HEALS as its evidence ages below the noise floor.
#
# Pure functions only: inputs are dot rows + an explicit ``now`` + dial
# overrides. No DB, no datetime.now() inside the math (so a replay at any past
# instant is exact). Stamp/collapse filtering mirrors score_dots exactly.
# ============================================================================

ROLE_THESIS = "thesis"
ROLE_TRIGGER = "trigger"

_ET = ZoneInfo("America/New_York")
_SESSION_OPEN = (9, 30)
_SESSION_CLOSE = (16, 0)
# Regular RTH session length, 09:30–16:00 ET = 6.5h. Half-days (early closes)
# are ignored in v1 — they over-count ~3.25h of decay time a few days a year;
# a NYSE-calendar-aware version is a later dial (comment kept deliberately).
SESSION_SECONDS = 23_400

# --- FOUNDER DIALs (changes surfaced as diffs, never silent — CLAUDE.md) -----
# THESIS half-life, in TRADING DAYS, measured in session-seconds: overnight and
# weekend gaps contribute ZERO decay (a thesis doesn't age while the market is
# shut). 2.5 td ≈ the swing horizon the product arms on (§2.3).
THESIS_HALF_LIFE_TD = 2.5  # FOUNDER DIAL
# TRIGGER half-life, in WALL-CLOCK MINUTES: immediacy dies in real time,
# overnight included — a fired trigger is stale by the next session.
TRIGGER_HALF_LIFE_MIN = 60.0  # FOUNDER DIAL
# Options print at/below this DTE is TRIGGER fuel; above it is THESIS fuel. The
# 3–6 DTE band routes THESIS by default (DTE ≥ 3 → thesis) — bump this dial to 6
# to pull that band into the trigger window.
TRIGGER_DTE_MAX = 2  # FOUNDER DIAL (the 3–6 band lives just above it)
# Conviction below this, with gross mass at/above the floor, = contested.
CONVICTION_BAR = 0.4  # FOUNDER DIAL
# Minimum gross decayed mass for a role to count as "real" — the gate that lets
# contested HEAL: once the day's evidence decays below this, it is noise, not a
# live disagreement.
NOISE_FLOOR = 1.0  # FOUNDER DIAL

# Role by component MECHANISM, DTE-independent. Options-actor components are
# deliberately absent — they route by DTE in evidence_role (see the report note:
# live sweeps span DTE 0–879, so "sweeps are always trigger" would decay a
# 2-year sweep in 60 minutes; the spec's governing rule is "DTE routes a print's
# role", §2.2/§4, so options flow — sweeps included — is DTE-routed).
_TRIGGER_COMPONENTS = frozenset(
    {
        "session_level",  # intraday level touch = a moment
        "price_alert",  # founder level touch = a moment
        "rvol_ex_prints",  # RVOL burst
    }
)
_THESIS_COMPONENTS = frozenset(
    {
        "dark_pool",  # institutional accumulation
        "equity_block",  # institutional accumulation
        "equity_sweep",  # excluded from v1 scoring, mapped for completeness
        "earnings_proximity",  # catalyst proximity
        "reddit_buzz",  # attention builds over hours/days (non-directional)
        "stocktwits_buzz",
        "regime",  # context riders (non-directional)
        "index_pressure",
        "market_shock",
    }
)
_OPTIONS_COMPONENTS = frozenset(
    {"option_sweep", "option_block", "unusual_option_volume", "directional_pressure"}
)


def _dte(dot: dict) -> int | None:
    """A dot's days-to-expiry, if carried. Looked up top-level first (what the
    replay stamps from options_flow_events) then in ``detail``. Live
    evidence_dots do NOT persist DTE today — see the report; absent DTE routes an
    options dot to THESIS (positioning) by default."""
    for source in (dot, dot.get("detail")):
        if isinstance(source, dict) and source.get("dte") is not None:
            try:
                return int(float(source["dte"]))
            except (TypeError, ValueError):
                pass
    return None


def evidence_role(dot: dict, *, trigger_dte_max: int = TRIGGER_DTE_MAX) -> str:
    """Classify one dot as THESIS (slow) or TRIGGER (fast) fuel (§2.2).

    Mechanism-driven components resolve directly; options flow routes by DTE.
    Only directional actor dots ever enter a mass — state/context/attention dots
    are classified for completeness and never contribute (mirrors score_dots,
    which sums only ACTOR_GROUPS)."""
    component = str(dot.get("component") or "")
    if component in _TRIGGER_COMPONENTS:
        return ROLE_TRIGGER
    if component in _THESIS_COMPONENTS:
        return ROLE_THESIS
    group = str(dot.get("actor_group") or "")
    if component in _OPTIONS_COMPONENTS or group == "options_actor":
        dte = _dte(dot)
        if dte is not None and dte <= trigger_dte_max:
            return ROLE_TRIGGER
        return ROLE_THESIS  # DTE ≥ 3 (3–6 band is a dial) or unknown → thesis
    if group == "equity_actor":
        return ROLE_THESIS  # block/dark-pool accumulation
    # Unknown dot kind: default THESIS (slow) so nothing unseen masquerades as a
    # hot trigger. Any dot that lands here is flagged in the migration report.
    return ROLE_THESIS


def _parse_ts(ts: Any) -> datetime:
    """UTC-aware datetime from an ISO string (or passthrough datetime)."""
    if isinstance(ts, datetime):
        return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return datetime(1970, 1, 1, tzinfo=timezone.utc)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def session_seconds_between(start: datetime, end: datetime) -> float:
    """Regular-session seconds elapsed between two instants (§2.2 thesis clock).

    Counts only RTH overlap (09:30–16:00 ET, Mon–Fri); overnight and weekend
    gaps contribute zero. Half-days are treated as full sessions in v1 (a small
    over-count a few days a year — noted at SESSION_SECONDS). O(days) — dot ages
    here are hours to a handful of sessions."""
    if end <= start:
        return 0.0
    start_et = start.astimezone(_ET)
    end_et = end.astimezone(_ET)
    total = 0.0
    day = start_et.date()
    last = end_et.date()
    while day <= last:
        if day.weekday() < 5:  # Mon–Fri
            open_dt = datetime(
                day.year, day.month, day.day, _SESSION_OPEN[0], _SESSION_OPEN[1], tzinfo=_ET
            )
            close_dt = datetime(
                day.year, day.month, day.day, _SESSION_CLOSE[0], _SESSION_CLOSE[1], tzinfo=_ET
            )
            lo = max(start_et, open_dt)
            hi = min(end_et, close_dt)
            if hi > lo:
                total += (hi - lo).total_seconds()
        day += timedelta(days=1)
    return total


def _decay_factor(
    role: str,
    dot_ts: datetime,
    now: datetime,
    thesis_half_life_td: float,
    trigger_half_life_min: float,
) -> float:
    """Exponential decay in [0, 1] for a dot of ``role`` observed at ``dot_ts``."""
    if role == ROLE_TRIGGER:
        elapsed_min = max(0.0, (now - dot_ts).total_seconds() / 60.0)
        half_life = max(1e-9, trigger_half_life_min)
        return 0.5 ** (elapsed_min / half_life)
    elapsed = session_seconds_between(dot_ts, now)
    half_life = max(1e-9, thesis_half_life_td * SESSION_SECONDS)
    return 0.5 ** (elapsed / half_life)


def _conviction(bull: float, bear: float) -> float:
    gross = bull + bear
    return abs(bull - bear) / gross if gross > 0 else 0.0


def score_decayed(
    dots: list[dict],
    now: Any,
    *,
    thesis_half_life_td: float = THESIS_HALF_LIFE_TD,
    trigger_half_life_min: float = TRIGGER_HALF_LIFE_MIN,
    conviction_bar: float = CONVICTION_BAR,
    noise_floor: float = NOISE_FLOOR,
    trigger_dte_max: int = TRIGGER_DTE_MAX,
) -> dict[str, Any]:
    """Signed, decayed thesis/trigger masses for one symbol's dots at ``now``.

    Per (role, direction) the decayed sum of per-dot points (each dot's own
    capped log-magnitude, so options premium and equity notional are normalised
    to their own bases before summing). ``conviction = |bull − bear| / gross``;
    ``contested`` = gross at/above the floor AND conviction below the bar,
    evaluated CONTEMPORANEOUSLY at ``now``. Maturity = thesis_gross ×
    thesis_conviction — trigger masses are computed and returned but NEVER mixed
    in (fast evidence times an entry, never carries a thesis; §2.2/§5, no
    cross-role score mixing).

    Filtering mirrors score_dots exactly: only stamped, directional
    (bull/bear) actor-group dots that survive the 5-minute collapse contribute.
    ``now`` and each dot ``ts`` are UTC ISO strings or datetimes.
    """
    now_dt = _parse_ts(now)
    collapsed = _collapse_ids(dots)
    masses: dict[str, dict[str, float]] = {
        ROLE_THESIS: {"bull": 0.0, "bear": 0.0},
        ROLE_TRIGGER: {"bull": 0.0, "bear": 0.0},
    }
    for dot in dots:
        if dot.get("actor_group") not in ACTOR_GROUPS:
            continue
        direction = dot.get("direction")
        if direction not in ("bull", "bear"):
            continue
        if not bool(dot.get("stamped", 1)):
            continue
        if str(dot.get("id")) in collapsed:
            continue
        point = _log_score(_value(dot), _base(dot))
        if point <= 0:
            continue
        role = evidence_role(dot, trigger_dte_max=trigger_dte_max)
        decay = _decay_factor(
            role, _parse_ts(dot["ts"]), now_dt, thesis_half_life_td, trigger_half_life_min
        )
        masses[role][direction] += point * decay

    roles_out: dict[str, Any] = {}
    for role, sides in masses.items():
        bull = sides["bull"]
        bear = sides["bear"]
        gross = bull + bear
        conviction = _conviction(bull, bear)
        roles_out[role] = {
            "bull_mass": round(bull, 4),
            "bear_mass": round(bear, 4),
            "gross": round(gross, 4),
            "net": round(bull - bear, 4),
            "conviction": round(conviction, 4),
            "contested": gross >= noise_floor and conviction < conviction_bar,
        }

    thesis = roles_out[ROLE_THESIS]
    maturity = thesis["gross"] * thesis["conviction"]
    return {
        "now": now_dt.isoformat(),
        "thesis": thesis,
        "trigger": roles_out[ROLE_TRIGGER],
        "maturity": round(maturity, 4),
    }
