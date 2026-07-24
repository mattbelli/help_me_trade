"""Vol-matched circular block bootstrap — the baseline Kronos has to beat.

Deliberately a STRONG baseline: it resamples the context's own returns in blocks
(preserving short-horizon autocorrelation), keeps the context's drift, and is
rescaled to the same EWMA sigma the barriers use. Kronos therefore has to beat
"recent volatility + recent drift + recent autocorrelation" — not a coin flip.

What it structurally cannot do is condition on the *shape* of the recent path.
That gap is the only thing the probe is measuring.
"""

from __future__ import annotations

import logging
import warnings

import numpy as np

BLOCK_BARS = 12  # one hour of 5-minute bars

logger = logging.getLogger(__name__)


def garch_sigma_h(log_returns: np.ndarray, horizon: int) -> float | None:
    """GARCH(1,1) forecast of the horizon-aggregated sigma. None if the fit fails.

    The other half of the probe: Kronos's strongest published task is volatility,
    where the runner-up baseline is GARCH at 9% lower MAE. Fitting it here — on our
    bars, at our horizon — is what turns that claim into something we can check.

    Zero-mean, rescaled to percent because arch's optimizer is poorly conditioned on
    raw 5-minute log returns. Fail-soft: a non-converging fit drops this arm for this
    point only, never the whole evaluation.
    """
    from arch import arch_model

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            fitted = arch_model(
                log_returns * 100, mean="Zero", vol="Garch", p=1, q=1
            ).fit(disp="off", show_warning=False)
            variances = fitted.forecast(horizon=horizon, reindex=False).variance
        total = float(np.sum(variances.to_numpy()[0]))
    except Exception as exc:  # noqa: BLE001 - any optimizer failure drops this arm
        logger.debug("GARCH fit failed: %s", exc)
        return None
    if not np.isfinite(total) or total <= 0:
        return None
    return float(np.sqrt(total) / 100)


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
