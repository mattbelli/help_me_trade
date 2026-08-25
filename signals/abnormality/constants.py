"""Fire rule, honesty labels, and grid constants for the abnormality scanner.

Attention Phase 1 (2026-08-25): the fire rule is the REGISTERED 1.14f/g study
rule — range ratio >= p90 within the (boundary x $-vol quintile) peer cell,
single component, cells need >= 25 occupants (1.14g's own floor), fires only on
the half-hour boundary grid the study measured, universe = the options-eligible
pool. The old draft rule (p99 ^ p95 ^ floors ^ top-N) is retired from firing
and PRE-REGISTERED as a subset test on the fire ledger instead
(ATTENTION_JOIN_DESIGN_2026-08-17.md §4.6). State stays 5-minute.
"""

from __future__ import annotations

import math

N_BUCKETS = 78  # 09:30–16:00 ET, 5-minute bars (last bar 15:55)
N_BUCKETS_EARLY = 42  # 09:30–13:00 ET early close (last bar 12:55)
WINDOW_BARS = 12  # trailing 60 min at 5-min grain
WINDOW_MINUTES = 60
FORWARD_2H_BARS = 24
RV_COVERAGE = 0.80  # ≥80% of window bins present, else absent (1.14g)
LN2_4 = 4.0 * math.log(2.0)
WARMUP_MIN_SESSIONS = 10  # of the trailing 20
LOOKBACK_SESSIONS = 20
N_QUINTILES = 5
STALE_SESSIONS = 3  # artifact older than this → detector stands down

# Study fire rule (1.14f/g — the licensed object, not a tunable draft).
RANGE_PCTL = 90.0  # single component: range ratio >= p90 within the peer cell
CELL_MIN_OCCUPANCY = 25  # 1.14g: a peer cell with <25 rows contributes nothing
# Half-hour boundary grid, W=60 fully inside RTH and H=2h reachable:
# 10:30..14:00 ET = 5-min buckets 12,18,..,54 (the study's 8 boundaries).
FIRE_BUCKETS = frozenset(range(12, 55, 6))

# Corporate-action guard (design §3).
CA_GAP_PCT = 0.40
CA_VOL_RATIO = 50.0
CA_VOL_GAP_PCT = 0.15  # "riding a large gap" companion to the 50× volume test

# Base-state draft params (design §2b). Founder-eyeball lock is Inc 3.
BASE_NEAR_PCT = 0.15
BASE_RANGE_FRAC = 0.20
BASE_PERSIST_K = 20
BASE_PERSIST_N = 30
BASE_VOL_RECENT = 20
BASE_VOL_PRIOR = 60
BASE_VOL_RATIO = 1.3
BASE_WEEKS_LOW = 26
BASE_WEEKS_RANGE = 52
SESSIONS_PER_WEEK = 5

LABEL = "abnormality perception — magnitude tell."
CAVEAT = (
    "Selects unusual range/volume vs this name's own time-of-day norm and its "
    "peers. Measured in-sample (1.14f/g, options-eligible names, first fire of "
    "the day): fired names' next-2h max move ran ~1.3x their own time-of-day norm "
    "vs ~0.94x for non-fired peers; direction was a coin flip. Own-norm units "
    "only — not a forecast of a large absolute move. Not a validated edge; no "
    "direction implied."
)
PAIRED_CAVEAT = (
    "Base-state pairing encodes a founder thesis (accumulation/distribution) — "
    "labelled thesis, not measurement."
)

KIND_ABNORMALITY = "abnormality"
KIND_PAIRED = "abnormality_paired"
BASE_NONE = "none"
BASE_LOW = "base_low"
BASE_HIGH = "base_high"

MINUTE_RING = 60
