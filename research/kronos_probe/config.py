"""Pre-registered probe parameters. Changing these after a run invalidates the result.

See README.md -> PRE-REGISTRATION. These are constants, not knobs.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

# Derived locally rather than imported from db.base: the probe must stay importable
# under .venv-kronos, which does not have the app's dependency tree.
ROOT = Path(__file__).resolve().parents[2]

SYMBOLS = (
    "SPY",
    "QQQ",
    "AAPL",
    "MSFT",
    "NVDA",
    "AMD",
    "TSLA",
    "META",
    "AMZN",
    "GOOGL",
)

# Post-cutoff: Kronos's corpus ends ~mid-2024 (paper tests 2024-07..2025-06, published
# 2025-08), so 2025-09 onward is clean under any reading. The leakage window is
# deliberately inside the corpus — the gap between the two is the memorization estimate.
WINDOWS = {
    "primary": (date(2025, 9, 1), date(2026, 7, 1)),
    "leakage": (date(2023, 1, 1), date(2023, 12, 31)),
    # Nightly variant — same dates, 1-hour bars at a one-session horizon. See
    # NIGHTLY_README.md for its own pre-registration.
    "nightly_primary": (date(2025, 9, 1), date(2026, 7, 1)),
    "nightly_leakage": (date(2023, 1, 1), date(2023, 12, 31)),
}

TIMEFRAME = "5min"
CONTEXT_BARS = 512
# Upstream's rolling-window limit, deliberately separate from how many bars we feed.
# At CONTEXT_BARS == MAX_CONTEXT its generation loop torch.rolls a fixed window for
# every generated bar; when the context is shorter it appends to a growing one, which
# is what upstream's own demo does. Changing this changes the conditioning, not just
# the speed, so it is pre-registered per profile rather than tuned.
MAX_CONTEXT = 512
HORIZON_BARS = 12  # 1 hour at 5-minute bars
# Bootstrap block length: 1 hour at 5-minute bars.
BLOCK_BARS = 12
# Whether sigma and the bootstrap pool keep cross-session (overnight) returns. False
# for the 5-min probe, whose horizon never crosses a gap. See barriers.context_log_returns.
KEEP_GAP_RETURNS = False
BARRIER_SIGMA_MULT = 1.0
EWMA_LAMBDA = 0.94
N_PATHS = 200
# Draws per GPU batch — a memory knob, not a statistical parameter. Measured on the
# RTX 2060 SUPER: 50/100/200 all cost the same per path (3.32/6.65/13.43s), i.e. the
# card is already saturated at 50, so raising it buys nothing. The real cost driver is
# upstream having no KV cache — it re-encodes all 512 context bars for every generated
# token, so a 12-bar rollout is 24 full-context forward passes.
PATH_BATCH = 50

# ET clock times for evaluation points. 24 bars apart, so at H=12 no two forward
# windows overlap; 14:00 + 1h = 15:00 stays inside the RTH session.
EVAL_TIMES_ET = ((10, 0), (12, 0), (14, 0))
# When True the evaluation point is instead the last RTH bar of each session, one per
# session, and the forward window is the whole next session (the nightly profile).
EVAL_AT_SESSION_CLOSE = False

# ET minute-of-day of the first RTH bar. Alpaca stamps a bar with its START, so at
# 5-minute bars the first RTH bar is 09:30; at 1-hour bars it is the 09:00 bar, which
# spans 09:00-10:00 and therefore carries 30 minutes of pre-market in its OHLV. Its
# CLOSE is a 10:00 RTH print, so close-only resolution is unaffected.
RTH_OPEN_MIN = 9 * 60 + 30
# Calendar slack before a window start so the first evaluation point already has
# CONTEXT_BARS of RTH history behind it. 512 5-minute bars ~ 6.6 sessions.
CONTEXT_SLACK_DAYS = 20

MODEL_NAME = "NeoQuasar/Kronos-small"
TOKENIZER_NAME = "NeoQuasar/Kronos-Tokenizer-base"
SAMPLE_TEMPERATURE = 1.0
# top_p = 1.0 (no nucleus truncation). Truncation shrinks per-step variance, which
# compounds over a 12-step rollout; measured dispersion match vs realized sigma was
# 0.91x at p=0.99 and 0.97x at p=1.0. See calibrate.py — chosen on scale match
# alone, before any outcome was scored.
SAMPLE_TOP_P = 1.0

OUTPUT_DIR = ROOT / "data" / "research" / "kronos_probe"

# --- Nightly variant -------------------------------------------------------------
# Same harness, 1-hour bars, one-session horizon. A config variant rather than a
# second package, so both frequencies share one resolver, one bootstrap and one
# scorer — the thing that makes the two runs comparable at all.
#
# The gap convention is the one parameter that genuinely INVERTS: a 5-minute H=12
# path evaluated at 10:00/12:00/14:00 never crosses an overnight gap, so gaps are
# contamination; a 1-hour H=7 path evaluated at the session close crosses exactly
# one, so gaps are signal. Estimate sigma on the return process the horizon actually
# traverses. See NIGHTLY_README.md.
_NIGHTLY = {
    "TIMEFRAME": "1hour",
    # 360 x 1h ~ 51 sessions, and below MAX_CONTEXT so upstream's generation loop
    # appends to a growing context instead of torch.roll-ing a fixed window.
    "CONTEXT_BARS": 360,
    "HORIZON_BARS": 7,  # exactly one RTH session: the 09:00..15:00 bars
    "BLOCK_BARS": 7,  # one session per block, so each block spans ~one gap
    "KEEP_GAP_RETURNS": True,
    "EVAL_AT_SESSION_CLOSE": True,
    "RTH_OPEN_MIN": 9 * 60,
    # Set by calibrate.py at THIS frequency, on dispersion match alone, before any
    # outcome was scored. T=1.0 (the 5-minute value) samples 1.38x too wide over a
    # 7-bar gap-crossing horizon; T=0.7 matches at 1.00x. Scale is corrected with
    # temperature rather than top_p because the scored outcome is a barrier touch —
    # a tail event — and nucleus truncation removes the support that produces it.
    "SAMPLE_TEMPERATURE": 0.7,
    # 360 1-hour bars ~ 51 sessions ~ 72 calendar days; 120 leaves room for holidays.
    "CONTEXT_SLACK_DAYS": 120,
}

# An early-close session (Black Friday, Christmas Eve) still yields a full 7-bar RTH
# grid on the SIP tape: thin after-hours prints fill the 14:00 and 15:00 slots. On
# 2025-11-28 SPY's 15:00 bar carried 89k shares against a 9.5M session median, so its
# close is not a price anyone could trade. A session whose FINAL bar is that thin is
# dropped both as an evaluation point and as a forward window. Volume-based rather
# than a hardcoded holiday list so the rule carries to any window unchanged; the
# observed margin is ~180x (0.9% vs 163% of median), so the threshold is not doing
# subtle work.
EARLY_CLOSE_VOLUME_RATIO = 0.10


def activate(window: str) -> None:
    """Switch this module to the profile ``window`` belongs to.

    Call once, before anything reads a parameter. Mutating module globals keeps
    every read site a plain ``config.X`` lookup, so the 5-minute profile stays the
    literal default that a reader sees at the top of this file.
    """
    if window.startswith("nightly"):
        globals().update(_NIGHTLY)
