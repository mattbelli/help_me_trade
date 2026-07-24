"""Vol-matched circular block bootstrap — the baseline Kronos has to beat.

Deliberately a STRONG baseline: it resamples the context's own returns in blocks
(preserving short-horizon autocorrelation), keeps the context's drift, and is
rescaled to the same EWMA sigma the barriers use. Kronos therefore has to beat
"recent volatility + recent drift + recent autocorrelation" — not a coin flip.

What it structurally cannot do is condition on the *shape* of the recent path.
That gap is the only thing the probe is measuring.
"""

from __future__ import annotations

import numpy as np

BLOCK_BARS = 12  # one hour of 5-minute bars


def bootstrap_paths(
    log_returns: np.ndarray,
    last_close: float,
    *,
    horizon: int,
    n_paths: int,
    sigma: float,
    rng: np.random.Generator,
) -> np.ndarray:
    """Return (n_paths, horizon) simulated close prices starting from ``last_close``.

    ``log_returns`` must be intraday-only (see barriers.intraday_log_returns) —
    resampling overnight gaps would inject session-sized jumps into a 1-hour path.
    """
    if log_returns.size < BLOCK_BARS:
        raise ValueError("context too short for block bootstrap")

    # Rescale to the barrier sigma so both arms face barriers calibrated to the
    # same volatility estimate.
    sample_sd = float(log_returns.std())
    if sample_sd <= 0:
        raise ValueError("degenerate context: zero return variance")
    scaled = log_returns * (sigma / sample_sd)

    n_blocks = int(np.ceil(horizon / BLOCK_BARS))
    starts = rng.integers(0, scaled.size, size=(n_paths, n_blocks))
    offsets = np.arange(BLOCK_BARS)
    # Circular wrap keeps every start index usable without truncation bias.
    idx = (starts[:, :, np.newaxis] + offsets) % scaled.size
    drawn = scaled[idx].reshape(n_paths, n_blocks * BLOCK_BARS)[:, :horizon]
    return float(last_close) * np.exp(np.cumsum(drawn, axis=1))
