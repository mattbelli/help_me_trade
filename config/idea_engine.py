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
# MONOTONIC registered_market_day latch is the primary wall, 07-28 — strictly
# newer days only, so lagging/replayed older days never register); the
# day-wide cooldown is the dedup_key backstop for replay/restart races.
IDEA_COOLDOWN_MIN["fusion_mech"] = 1440
# LLM paper-book lanes (References/LLM_BOOKS_PROGRAM_SPEC_2026-07-23.md §4):
# one nightly cycle per book, so the day-wide cooldown is the same replay/restart
# backstop. tests/test_llm_books.py fails if a book lane is missing here (the
# defaultdict would silently hand it 30 min).
for _book_origin in (
    "book:deepseek",
    "book:deepseek/considered",
    "book:fable",
    "book:fable/considered",
    "book:panel",
    "book:panel/bull",
    "book:panel/bear",
    "book:panel/macro",
    "book:panel/risk",
):
    IDEA_COOLDOWN_MIN[_book_origin] = 1440

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
# Fusion C5 reason-lane parse-fail ceiling: fraction of today's v1 attempts that
# may be parse_fail+refusal before pipeline_audit pages (mirrored module-local in
# signals/pipeline_audit as REASON_PARSE_FAIL_RATE_MAX, the house pattern). Only
# armed with the lane — the rule short-circuits to None while REASON_EMIT_ENABLED
# is False, so a dark lane never pages.
ALARM_REASON_PARSE_FAIL_RATE = 0.2
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

# Fusion Horizon Round 3 — episode & idea lifecycle dials (spec §2/§3). Every
# value here is a FOUNDER DIAL (changes surfaced as diffs, never silent). The
# sweep (signals/fusion/lifecycle.py) reads them at call time so a diff'd change
# takes effect on the next pass.
FUSION_EPISODE_INVAL_ATR = 1.5  # FOUNDER DIAL — price-invalidation K (× ATR14 from the latched anchor); wider than the idea's 1× stop on purpose
FUSION_EPISODE_DECAY_FLOOR = 0.5  # FOUNDER DIAL — thesis gross decayed mass below this = noise (one stale small print can't hold an episode open)
FUSION_EPISODE_DECAY_SESSIONS = 2  # FOUNDER DIAL — N_DECAY: consecutive first-of-session readings below the floor before decay_to_noise closes
FUSION_EPISODE_TTL_SESSIONS = 15  # FOUNDER DIAL — hard-TTL in trading days; matches the swing ceiling (§2.3)
FUSION_REHYDRATE_SESSIONS = 10  # FOUNDER DIAL — cold-storage rehydration window (trading days) for a returning symbol
FUSION_TRIGGER_TTL_IMPULSE_HOURS = 4  # FOUNDER DIAL — impulse-class trigger TTL (wall-clock hours)
FUSION_TRIGGER_TTL_THESIS_SESSIONS = 3  # FOUNDER DIAL — thesis-confirmation-class trigger TTL (trading days)
FUSION_HOT_LANE_DEMOTE_SESSIONS = 3  # FOUNDER DIAL — quiet trading days before a hot-lane symbol demotes (Round 4)

# Fusion context/catalyst minters (board fusion-unminted-dots, 2026-07-28) — the
# registered components start minting. regime/index_pressure are ANNOTATE-ONLY
# (never score); earnings_proximity is a scored catalyst state dot on the same
# [0, 1] closeness scale as structure. Every value is a FOUNDER DIAL.
FUSION_EARNINGS_AHEAD_TD = 5  # FOUNDER DIAL — upcoming earnings within N weekdays = catalyst-adjacent
FUSION_EARNINGS_BEHIND_TD = 2  # FOUNDER DIAL — reported earnings within N weekdays = drift window
FUSION_REGIME_STALE_DAYS = 5  # FOUNDER DIAL — macro series older than this mint nothing (stale context is worse than none)
FUSION_CONTEXT_REFRESH_S = 60.0  # FOUNDER DIAL — memo TTL for the market-wide context reads

# rvol_ex_prints → background_crowd STATE dots (board fusion-unminted-dots,
# 2026-07-28) — the last unminted scorer component. ANNOTATE-ONLY by spec §2.2:
# background_crowd sits in scorer.ANNOTATE_ONLY_GROUPS, so these dots never
# reach group_scores/breadth/direction. There is deliberately NO points dial —
# the dot always mints at 0.0 points; its payload is raw_value (the multiple)
# for lifecycle's rvol_confirm trigger and the dossier. Every value is a
# FOUNDER DIAL.
FUSION_RVOL_REFRESH_BUCKET_MIN = 5  # FOUNDER DIAL — write throttle in minutes-since-open buckets; the volume-profile baseline's own resolution is 5 min, so refreshing more often adds no information
FUSION_RVOL_DAY_VOLUME_MAX_AGE_S = 600.0  # FOUNDER DIAL — a fusion_day_volume row further than this from the event reads as MISSING (state 'stale_volume'), never as a reading
FUSION_RVOL_WARMUP_MIN = 15  # FOUNDER DIAL — minutes after the open before a reading is published (mirrors volume_profile.WARMUP_MIN: the first buckets are too noisy to divide by)
FUSION_DAY_VOLUME_REFRESH_S = 300.0  # FOUNDER DIAL — producer cadence for the fusion_day_volume read-cache (market-hours only)
FUSION_DAY_VOLUME_LATCH_MAX_MIN = 12  # FOUNDER DIAL — the premarket offset may only be latched within N minutes of the open (>= 2 refresh intervals + slack). A symbol first sampled LATER (a mid-session episode on an off-universe name) has no trustworthy offset today: its offset is stored NULL and the reading reads UNAVAILABLE until tomorrow's open latches a real one — latching mid-session would swallow the whole day's volume and publish a fabricated ~0.0 RVOL on exactly the fresh-burst names the lane exists for

# News → catalyst EVENT dots (board news-catalyst-dots, 2026-07-28). Headlines are
# FACTS, never sentiment: direction is always 'none', so news adds catalyst-group
# points (and breadth) without ever entering a directional mass. Every value is a
# FOUNDER DIAL.
FUSION_NEWS_WINDOW_MIN = 120  # FOUNDER DIAL — session-minutes a headline stays fresh (measured on the SESSION clock via scorer.session_seconds_between, so a pre-market headline is fresh at the open)
FUSION_NEWS_MAX_SYMBOLS = 12  # FOUNDER DIAL — a headline tagging more symbols than this is a roundup: the dot still mints (annotate-only) but at points 0.0
FUSION_NEWS_BURST_POINTS = ((4, 1.0), (2, 0.75), (1, 0.5))  # FOUNDER DIAL — headline-count-in-window → points; first threshold ≤ n wins

# Fusion mechanical registration (2026-07-28 measurement-integrity round).
# FUSION_MIN_ATR_PCT: refuse fusion_mech registration when ATR(14) is under this
# % of price — measured 07-28: the 253 sub-2% rows won 3.2% and every sub-1% row
# is a bond/cash ETF whose 1×ATR stop sits inside the bid/ask (86 rows under
# 0.5%, zero wins ever). 1.0 keeps SPY/QQQ/XLE (1–2% band) registering. Every
# registration stamps atr_pct into source_ref regardless, so the floor's effect
# stays measurable. COHORT NOTE: the floor gates from 2026-07-28 — pre/post
# comparisons must split on that date.
FUSION_MIN_ATR_PCT = 1.0  # FOUNDER DIAL
# fusion_atr_cache rows older than this many calendar days are refused by
# get_atr (5 = weekend + holiday headroom over the daily refresh). A plan built
# on a weeks-old ATR measures nothing; the dot stays unstamped and is counted
# by the existing unstampable dispositions instead.
FUSION_ATR_MAX_AGE_DAYS = 5
# Whole-market price fallback (fusion_spot_cache, fed by the equity collector's
# wildcard tape): a spot older than this is refused — overnight/halted/collector-
# down must read as missing, never as a price. 15 min is generous for any
# optionable name during RTH at the 30s flush cadence.
FUSION_SPOT_MAX_AGE_S = 900.0

# Fusion Phase C1 — SHADOW-MODE investigation gate (spec §2.6 LLM tier, §3 C).
# The deterministic "this evidence stack would be investigated by the LLM lane
# right now" decision — counted, never acted on. Zero LLM calls. REASON_SHADOW_ONLY
# is the C1↔ARM seam: the gate asserts it and does nothing but count. C3 did NOT flip
# it (C3 shipped the lane dark); the future ARM commit flips it — see the ARMING
# CHECKLIST beside REASON_EMIT_ENABLED below. B=5/day total with a 25% opening reserve
# (ceil(0.25×5)=2 investigations during the first 30 min, so the open can't drain
# the day). The cut-line is the z dual gate (FUSION_Z_GATE_T + CONVICTION_BAR in
# signals/fusion/scorer.py) — the pre-z trailing-percentile baseline machinery
# and its INVESTIGATE_* dials were removed with the founder's T pick (07-22).
REASON_DAILY_BUDGET = 5
REASON_OPENING_RESERVE_FRAC = 0.25
REASON_OPENING_WINDOW_MIN = 30
REASON_SHADOW_ONLY = True

# Fusion Phase C2 — DOSSIER BUILDER (deterministic claim matrix; zero LLM/network).
# The plain-text matrix rendering (signals/dossier.build_dossier) is hard-capped so
# the T1/T2 prompt that consumes it stays bounded; truncation drops WHOLE trailing
# claims (never mid-line) and appends a "(+N claims truncated)" marker. This is a
# law constant, not a deployment toggle — a bigger budget is a NEW estimand for the
# investigator, never an env override.
DOSSIER_SUMMARY_MAX_CHARS = 4000

# Fusion Phase C3 — the LLM INVESTIGATION lane (Unified §4 + "LLM invocation design"
# + Phase 3). Ships DARK: REASON_EMIT_ENABLED is the arming seam — the runner
# (research/reason.py) returns a counted no-op while it is False. C5 wired the lane
# into production (the fusion_reason job, delivery, crash recovery, alarm rules) but
# left it DARK; the flag is still False. NOTE: never assert its value here — the ARM
# commit must be able to set it True without tripping config validation.
#
# ═══ ARMING CHECKLIST — the future ARM commit is exactly these four edits ═══
#   1. REASON_SHADOW_ONLY = True → False        (the C1 intake gate stops shadowing)
#   2. REASON_EMIT_ENABLED = False → True       (the reason lane + alarms go live)
#   3. drop `assert REASON_SHADOW_ONLY is True` from validate_config below
#   4. add "fusion_reason" to ALARM_EXPECTED_LANES (and its validate_config assert)
#      so the never-worked rule guards the now-live lane. Its counters-map entry in
#      signals/pipeline_audit._EXPECTED_LANE_COUNTERS is ALREADY pre-staged by C5 —
#      without it, edit 4 alone would KeyError and silently kill never-worked
#      coverage for every lane (adversarial review 07-21).
# Nothing else — then DEPLOY (a backend restart is required: these flags are read
# at import time). Everything C5 shipped keys off REASON_EMIT_ENABLED, so flipping
# (2) arms delivery + both new alarm rules in one move. Do NOT edit any of this now.
# PLUS one runtime step at ARM (settings, not code): alerts.max_push_per_day is 0 —
# founder killed ALL flow-lane phone pushes 07-21 ("first-5-of-hundreds has no
# value; send none"), superseding the no-blackout rule. Restore it to 5 via
# PUT /api/settings when arming, or the new idea lane pushes nothing; convergence
# pushes retire in the same round so the pool serves ONLY investigated ideas.
REASON_EMIT_ENABLED = False
# Lane ceilings — defense in depth OVER C1's 5/day investigation gate (a runaway
# poll can never exceed these). T1 triages ≤8 candidates/call; v1 is one call per
# investigate=True candidate. REASON_V1_DAILY_BUDGET must not undercut the C1 gate
# (asserted below), else a valid investigation would be budget-denied at the door.
T1_DAILY_BUDGET = 150
REASON_V1_DAILY_BUDGET = 40
REASON_T1_BATCH_MAX = 8
# One structured completion per call, wrapped in asyncio.wait_for(timeout) → status
# `timeout`. Token caps are generous on purpose (DeepSeek lesson: reasoning tokens
# eat max_tokens — a length-truncated completion must persist as parse_fail, never
# be silently dropped; zero and dead are different numbers).
REASON_CALL_TIMEOUT_S = 90
REASON_V1_MAX_TOKENS = 2048
REASON_T1_MAX_TOKENS = 1024


def validate_config() -> None:
    """Assert the invariants the rest of the engine relies on (config-rot defense)."""
    assert IDEA_COOLDOWN_MIN["__unlisted__"] == IDEA_COOLDOWN_MIN_DEFAULT
    assert IDEA_COOLDOWN_MIN_DEFAULT > 0
    assert GRADING_TIEBREAK == "stop"
    assert {"earnings_drift", "momentum_sp500"} <= set(STRATEGY_HORIZONS)
    assert all(v for v in STRATEGY_HORIZONS.values())
    assert 0.0 < ALARM_CONVICTION_PARSE_FAIL_RATE < 1.0
    assert 0.0 < ALARM_REASON_PARSE_FAIL_RATE < 1.0
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
    # Fusion Horizon Round 3 lifecycle dials.
    assert FUSION_EPISODE_INVAL_ATR > 0
    assert FUSION_EPISODE_DECAY_FLOOR >= 0
    assert FUSION_EPISODE_DECAY_SESSIONS >= 1
    assert FUSION_EPISODE_TTL_SESSIONS >= 1
    assert FUSION_REHYDRATE_SESSIONS >= 1
    assert FUSION_TRIGGER_TTL_IMPULSE_HOURS > 0
    assert FUSION_TRIGGER_TTL_THESIS_SESSIONS >= 1
    assert FUSION_HOT_LANE_DEMOTE_SESSIONS >= 1
    # Context/catalyst minter dials.
    assert FUSION_EARNINGS_AHEAD_TD >= 1
    assert FUSION_EARNINGS_BEHIND_TD >= 0
    assert FUSION_REGIME_STALE_DAYS >= 1
    assert FUSION_CONTEXT_REFRESH_S >= 0
    # rvol_ex_prints dials.
    assert FUSION_RVOL_REFRESH_BUCKET_MIN >= 1
    assert FUSION_RVOL_DAY_VOLUME_MAX_AGE_S > 0
    assert FUSION_RVOL_WARMUP_MIN >= 0
    assert FUSION_DAY_VOLUME_REFRESH_S > 0
    # The latch window must span at least two producer passes, or a restart
    # inside the window strands every symbol on a NULL offset for the day.
    assert FUSION_DAY_VOLUME_LATCH_MAX_MIN * 60 >= 2 * FUSION_DAY_VOLUME_REFRESH_S
    # News catalyst dials.
    assert FUSION_NEWS_WINDOW_MIN > 0
    assert FUSION_NEWS_MAX_SYMBOLS >= 1
    assert FUSION_NEWS_BURST_POINTS
    assert all(n >= 1 and 0.0 <= p <= 1.0 for n, p in FUSION_NEWS_BURST_POINTS)
    assert [n for n, _ in FUSION_NEWS_BURST_POINTS] == sorted(
        (n for n, _ in FUSION_NEWS_BURST_POINTS), reverse=True
    )
    assert [p for _, p in FUSION_NEWS_BURST_POINTS] == sorted(
        (p for _, p in FUSION_NEWS_BURST_POINTS), reverse=True
    )
    # Fusion C1 shadow-investigation invariants.
    assert REASON_SHADOW_ONLY is True
    assert REASON_DAILY_BUDGET >= 1
    assert 0.0 < REASON_OPENING_RESERVE_FRAC <= 1.0
    assert math.ceil(REASON_OPENING_RESERVE_FRAC * REASON_DAILY_BUDGET) <= REASON_DAILY_BUDGET
    assert REASON_OPENING_WINDOW_MIN > 0
    # Mechanical-registration dials (2026-07-28 round).
    assert FUSION_MIN_ATR_PCT >= 0
    assert FUSION_ATR_MAX_AGE_DAYS >= 1
    assert FUSION_SPOT_MAX_AGE_S > 0
    # Fusion C2 dossier invariant.
    assert DOSSIER_SUMMARY_MAX_CHARS > 0
    # Fusion C3 LLM-lane invariants. REASON_EMIT_ENABLED is deliberately NOT asserted
    # (C5 flips it True). The lane v1 ceiling must not undercut C1's 5/day gate.
    assert T1_DAILY_BUDGET > 0
    assert REASON_V1_DAILY_BUDGET > 0
    assert 1 <= REASON_T1_BATCH_MAX <= 16
    assert REASON_CALL_TIMEOUT_S > 0
    assert REASON_V1_MAX_TOKENS > 0
    assert REASON_T1_MAX_TOKENS > 0
    assert REASON_V1_DAILY_BUDGET >= REASON_DAILY_BUDGET
