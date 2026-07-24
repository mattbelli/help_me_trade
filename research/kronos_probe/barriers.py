"""The single shared barrier resolver.

Real forward bars, Kronos rollouts, and bootstrap paths all pass through THIS
function. Using different resolvers per arm — e.g. intrabar high/low touch for the
models but close-only for the bootstrap — would hand one arm more touch
opportunities and manufacture a win. Close-only understates real stop behaviour;
it is chosen because it is identical everywhere.
"""

from __future__ import annotations

import numpy as np

UP = 0
DOWN = 1
TIMEOUT = 2
N_OUTCOMES = 3


def intraday_log_returns(closes: np.ndarray, session_dates: list) -> np.ndarray:
    """Log returns with cross-session moves dropped.

    512 RTH 5-minute bars span ~6.6 sessions, so a naive diff() includes 7
    overnight gaps. EWMA weights recent observations heavily, so one recent gap
    dominated the estimate — measured inflation up to 2.46x, worst exactly on the
    high-volatility days the probe most needs to get right. Both the barriers and
    the bootstrap consume this function's output.
    """
    returns = np.diff(np.log(closes))
    same_session = np.array(
        [session_dates[i] == session_dates[i + 1] for i in range(len(session_dates) - 1)]
    )
    return returns[same_session]


def ewma_sigma(log_returns: np.ndarray, lam: float) -> float:
    """EWMA stdev of log returns, most recent observation weighted highest."""
    n = log_returns.size
    if n == 0:
        return float("nan")
    weights = lam ** np.arange(n - 1, -1, -1)
    weights /= weights.sum()
    mean = float(np.sum(weights * log_returns))
    var = float(np.sum(weights * (log_returns - mean) ** 2))
    return float(np.sqrt(var))


def resolve(closes: np.ndarray, upper: float, lower: float) -> np.ndarray:
    """First-touch outcome per path. ``closes`` is (n_paths, horizon).

    A single close cannot be both >= upper and <= lower (upper > lower), so the
    only ties are mutual non-touches, which resolve to TIMEOUT.
    """
    if closes.ndim == 1:
        closes = closes[np.newaxis, :]
    horizon = closes.shape[1]
    up_hit = closes >= upper
    down_hit = closes <= lower
    first_up = np.where(up_hit.any(axis=1), up_hit.argmax(axis=1), horizon)
    first_down = np.where(down_hit.any(axis=1), down_hit.argmax(axis=1), horizon)

    out = np.full(closes.shape[0], TIMEOUT, dtype=np.int8)
    out[first_up < first_down] = UP
    out[first_down < first_up] = DOWN
    return out


def distribution(outcomes: np.ndarray) -> np.ndarray:
    """Empirical 3-way probability vector from resolved paths."""
    counts = np.bincount(outcomes, minlength=N_OUTCOMES).astype(float)
    return counts / counts.sum()
