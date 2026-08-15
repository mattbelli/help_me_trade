"""Daily-grain base-state detector — both sides, draft params (design §2b).

`base_low` is an accumulation candidate: close near the 26-week low, sitting in
the bottom of the 52-week range for most of the last 30 sessions (a base, not a
falling knife), with volume escalating. `base_high` is the exact mirror.
Insufficient history → `none` (absent), never a fabricated state.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from signals.abnormality.constants import (
    BASE_HIGH,
    BASE_LOW,
    BASE_NEAR_PCT,
    BASE_NONE,
    BASE_PERSIST_K,
    BASE_PERSIST_N,
    BASE_RANGE_FRAC,
    BASE_VOL_PRIOR,
    BASE_VOL_RATIO,
    BASE_VOL_RECENT,
    BASE_WEEKS_LOW,
    BASE_WEEKS_RANGE,
    SESSIONS_PER_WEEK,
)


def classify_base_state(
    close: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    volume: np.ndarray,
    *,
    near_pct: float = BASE_NEAR_PCT,
    range_frac: float = BASE_RANGE_FRAC,
    persist_k: int = BASE_PERSIST_K,
    persist_n: int = BASE_PERSIST_N,
    vol_recent: int = BASE_VOL_RECENT,
    vol_prior: int = BASE_VOL_PRIOR,
    vol_ratio_min: float = BASE_VOL_RATIO,
) -> tuple[str, dict[str, Any]]:
    """Return (`none`|`base_low`|`base_high`, strength fields).

    Arrays are oldest→newest daily bars. Strength is empty on `none`.
    """
    c = np.asarray(close, np.float64)
    h = np.asarray(high, np.float64)
    lo = np.asarray(low, np.float64)
    v = np.asarray(volume, np.float64)
    n = int(c.size)
    need = max(
        persist_n,
        vol_recent + vol_prior,
        BASE_WEEKS_LOW * SESSIONS_PER_WEEK,
        BASE_WEEKS_RANGE * SESSIONS_PER_WEEK,
    )
    if n < need:
        return BASE_NONE, {"reason": "warmup", "n_sessions": n, "need": need}

    n_26 = BASE_WEEKS_LOW * SESSIONS_PER_WEEK
    n_52 = BASE_WEEKS_RANGE * SESSIONS_PER_WEEK
    last = float(c[-1])
    if not np.isfinite(last) or last <= 0:
        return BASE_NONE, {"reason": "no_close"}

    low_26 = float(np.nanmin(lo[-n_26:]))
    high_26 = float(np.nanmax(h[-n_26:]))
    lo_52 = float(np.nanmin(lo[-n_52:]))
    hi_52 = float(np.nanmax(h[-n_52:]))
    rng = hi_52 - lo_52
    if not np.isfinite(rng) or rng <= 0:
        return BASE_NONE, {"reason": "no_range"}

    pos = (c[-persist_n:] - lo_52) / rng
    in_bottom = int(np.nansum(pos <= range_frac))
    in_top = int(np.nansum(pos >= (1.0 - range_frac)))
    near_low = last <= low_26 * (1.0 + near_pct)
    near_high = last >= high_26 * (1.0 - near_pct)

    recent = v[-vol_recent:]
    prior = v[-(vol_recent + vol_prior) : -vol_recent]
    med_recent = float(np.nanmedian(recent))
    med_prior = float(np.nanmedian(prior))
    if not np.isfinite(med_recent) or not np.isfinite(med_prior) or med_prior <= 0:
        return BASE_NONE, {"reason": "no_volume"}
    vol_trend = med_recent / med_prior
    vol_ok = vol_trend >= vol_ratio_min

    weeks = _weeks_in_base(pos, range_frac)

    low_ok = near_low and in_bottom >= persist_k and vol_ok
    high_ok = near_high and in_top >= persist_k and vol_ok
    strength = {
        "weeks_in_base": weeks,
        "vol_trend": round(vol_trend, 3),
        "in_bottom": in_bottom,
        "in_top": in_top,
        "near_low": near_low,
        "near_high": near_high,
    }
    if low_ok and not high_ok:
        return BASE_LOW, strength
    if high_ok and not low_ok:
        return BASE_HIGH, strength
    return BASE_NONE, strength


def _weeks_in_base(pos: np.ndarray, range_frac: float) -> float:
    """Trailing consecutive sessions inside the base band, in weeks."""
    bottom = pos <= range_frac
    top = pos >= (1.0 - range_frac)
    # Count from the end: consecutive days in EITHER band (the side is decided
    # by the classifier). Mixed last-30 still counts the trailing run.
    run = 0
    for flag in (bottom | top)[::-1]:
        if flag:
            run += 1
        else:
            break
    return round(run / SESSIONS_PER_WEEK, 1)
