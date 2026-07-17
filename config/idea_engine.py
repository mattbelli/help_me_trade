"""Idea Engine Phase 1 constants — law, not deployment toggles (spec §7).

Kept small and env-free on purpose: the tie-break, cooldown, and alarm thresholds
are calibration law. Only true deployment toggles ever belong in the environment.
"""

from __future__ import annotations

from collections import defaultdict

# Per-origin dedup cooldown window in minutes (spec §1). A defaultdict so an
# unlisted origin falls back to 30 via IDEA_COOLDOWN_MIN[origin] with no lookup
# ceremony at the call site.
IDEA_COOLDOWN_MIN_DEFAULT = 30
IDEA_COOLDOWN_MIN: dict[str, int] = defaultdict(lambda: IDEA_COOLDOWN_MIN_DEFAULT)

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

# Canary + external dead-man times (ET). The tracker registers a synthetic idea,
# waits for the next sweep, and asserts a push receipt within the timeout.
CANARY_REGISTER_ET = "15:00"
CANARY_RECEIPT_TIMEOUT_MIN = 30
DEADMAN_ET = "16:45"


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
    assert CANARY_RECEIPT_TIMEOUT_MIN > 0
