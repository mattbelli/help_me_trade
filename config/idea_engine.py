"""Idea Engine Phase 1 constants — law, not deployment toggles (spec §7).

Kept small and env-free on purpose: the tie-break, cooldown, and alarm thresholds
are calibration law. Only true deployment toggles ever belong in the environment.
"""

from __future__ import annotations

import math
from collections import defaultdict

# Per-origin dedup cooldown window in minutes (spec §1). A defaultdict so an
# unlisted origin falls back to 30 via IDEA_COOLDOWN_MIN[origin] with no lookup
# ceremony at the call site.
IDEA_COOLDOWN_MIN_DEFAULT = 30
IDEA_COOLDOWN_MIN: dict[str, int] = defaultdict(lambda: IDEA_COOLDOWN_MIN_DEFAULT)
# fusion_mech is one row per symbol-day BY CONSTRUCTION (the episode's
# registration_called guard is the primary wall); the day-wide cooldown is the
# dedup_key backstop for replay/restart races.
IDEA_COOLDOWN_MIN["fusion_mech"] = 1440

# Path A horizon templates (spec §3), literal trading-day horizons so the grader
# never needs call-time strategy-param resolution. earnings_drift enters at
# td=10 and exits at td=1 before the print → 9 trading days held.
STRATEGY_HORIZONS: dict[str, str] = {
    "earnings_drift": "9d",
    "momentum_sp500": "21d",
}

# Same daily bar touches stop AND target → stop wins (pessimistic). Codified so
# the grader never leaves the tie implicit (spec §4).
GRADING_TIEBREAK = "stop"

# Alarm thresholds (spec §5). Rate rules compare today's counter to the trailing
# median over this many trading days — never a boolean-at-zero.
ALARM_RATE_MEDIAN_DAYS = 10
ALARM_CONVICTION_PARSE_FAIL_RATE = 0.5
ALARM_CONVICTION_PARSE_FAIL_DAYS = 2
ALARM_SCAN_ZERO_REGISTERED_DAYS = 3
ALARM_TRACKER_ZERO_DAYS = 2
ALARM_MAX_OPEN_AGE_SLACK_DAYS = 3
ALARM_NEVER_WORKED_GRACE_DAYS = 3
ALARM_EXPECTED_LANES = frozenset(
    {"setup_scan", "conviction_pass", "idea_tracker", "path_a_register"}
)

# Canary + external dead-man times (ET). The tracker registers a synthetic idea,
# waits for the next sweep, and asserts a push receipt within the timeout.
CANARY_REGISTER_ET = "15:00"
CANARY_RECEIPT_TIMEOUT_MIN = 30
DEADMAN_ET = "16:45"

# Fusion Phase A1 remains observe-only until explicitly armed.  These values are
# deterministic policy constants so score/shock cohorts can be reproduced.
FUSION_CARDS_ENABLED = False
FUSION_POLL_SECONDS = 60.0
FUSION_STORM_BATCH_EVENTS = 500
FUSION_STORM_KEEP_EVENTS = 200
FUSION_SOCIAL_SPIKE_RATIO = 2.0
FUSION_SPY_SHOCK_MOVE_PCT = 2.0
FUSION_VIX_SHOCK_MOVE_PCT = 8.0
FUSION_SHOCK_WINDOW_MINUTES = 60
FUSION_CARD_DAILY_CAP = 50
FUSION_CARD_TRAILING_PERCENTILE = 0.75

# Fusion Phase C1 — SHADOW-MODE investigation gate (spec §2.6 LLM tier, §3 C).
# The deterministic "this evidence stack would be investigated by the LLM lane
# right now" decision — counted, never acted on. Zero LLM calls. REASON_SHADOW_ONLY
# is the C1↔C3 seam: the gate asserts it and does nothing but count; C3 flips it in
# this one place to arm real investigation. B=5/day total with a 25% opening reserve
# (ceil(0.25×5)=2 investigations during the first 30 min, so the open can't drain
# the day). The threshold is the (B×S)-th highest day-final score over the trailing
# S≤7 sessions, so ~B stacks clear on an average day; below INVESTIGATE_MIN_POOL the
# archetype pool widens, then reads no_baseline until sessions accrue.
REASON_DAILY_BUDGET = 5
REASON_OPENING_RESERVE_FRAC = 0.25
REASON_OPENING_WINDOW_MIN = 30
INVESTIGATE_TRAILING_SESSIONS = 7
INVESTIGATE_MIN_POOL = 20
REASON_SHADOW_ONLY = True


def validate_config() -> None:
    """Assert the invariants the rest of the engine relies on (config-rot defense)."""
    assert IDEA_COOLDOWN_MIN["__unlisted__"] == IDEA_COOLDOWN_MIN_DEFAULT
    assert IDEA_COOLDOWN_MIN_DEFAULT > 0
    assert GRADING_TIEBREAK == "stop"
    assert {"earnings_drift", "momentum_sp500"} <= set(STRATEGY_HORIZONS)
    assert all(v for v in STRATEGY_HORIZONS.values())
    assert 0.0 < ALARM_CONVICTION_PARSE_FAIL_RATE < 1.0
    assert ALARM_RATE_MEDIAN_DAYS >= 1
    assert ALARM_CONVICTION_PARSE_FAIL_DAYS >= 1
    assert ALARM_SCAN_ZERO_REGISTERED_DAYS >= 1
    assert ALARM_TRACKER_ZERO_DAYS >= 1
    assert ALARM_MAX_OPEN_AGE_SLACK_DAYS >= 0
    assert ALARM_NEVER_WORKED_GRACE_DAYS >= 1
    assert ALARM_EXPECTED_LANES == {
        "setup_scan",
        "conviction_pass",
        "idea_tracker",
        "path_a_register",
    }
    assert CANARY_RECEIPT_TIMEOUT_MIN > 0
    assert FUSION_CARDS_ENABLED is False
    assert 0 < FUSION_STORM_KEEP_EVENTS <= FUSION_STORM_BATCH_EVENTS
    assert 0 < FUSION_CARD_TRAILING_PERCENTILE < 1
    # Fusion C1 shadow-investigation invariants.
    assert REASON_SHADOW_ONLY is True
    assert REASON_DAILY_BUDGET >= 1
    assert 0.0 < REASON_OPENING_RESERVE_FRAC <= 1.0
    assert math.ceil(REASON_OPENING_RESERVE_FRAC * REASON_DAILY_BUDGET) <= REASON_DAILY_BUDGET
    assert REASON_OPENING_WINDOW_MIN > 0
    assert INVESTIGATE_TRAILING_SESSIONS >= 1
    assert INVESTIGATE_MIN_POOL >= 1
