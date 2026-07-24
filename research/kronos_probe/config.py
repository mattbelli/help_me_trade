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
}

TIMEFRAME = "5min"
CONTEXT_BARS = 512
HORIZON_BARS = 12  # 1 hour at 5-minute bars
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

MODEL_NAME = "NeoQuasar/Kronos-small"
TOKENIZER_NAME = "NeoQuasar/Kronos-Tokenizer-base"
SAMPLE_TEMPERATURE = 1.0
# top_p = 1.0 (no nucleus truncation). Truncation shrinks per-step variance, which
# compounds over a 12-step rollout; measured dispersion match vs realized sigma was
# 0.91x at p=0.99 and 0.97x at p=1.0. See calibrate.py — chosen on scale match
# alone, before any outcome was scored.
SAMPLE_TOP_P = 1.0

OUTPUT_DIR = ROOT / "data" / "research" / "kronos_probe"
