"""Breadth and realized correlation, computed from OUR OWN bars into the macro store.

Not vendor series — derived daily statistics. They land in ``macro.db``'s
``macro_series`` deliberately, so they inherit the whole regime container rather
than reimplementing it: a percentile against their own history, the house 33/80
buckets, the thin-history floor that withholds a rank it cannot support, and
every agent surface. Computing them ad hoc inside a perception read would get
none of that, and would have no history to rank against at all.

What they answer, which no vendor series does:
  - breadth — is the market rising on everything, or on five names? For a
    $10B-$500B universe this is the direct read on whether THAT kind of stock is
    participating, which the index level cannot tell you.
  - correlation — are stocks moving as one macro blob, or on their own news?
    High correlation means single-name analysis stops paying and everything is
    leveraged beta; low means selection is what pays. This is the closest thing
    we have to "is my whole process worth running this week".

⚠️ Source is ``momentum_daily_bars`` (S&P 500, maintained by the scheduler's
``momentum_bars_refresh`` job, NOT by the momentum strategy's enabled flag).
Backfilled to 2021-08-13 on 2026-08-11, so ~5 years / ~1,230 sessions.

⚠️⚠️ **SURVIVORSHIP.** The panel is TODAY's S&P 500 members, priced backwards.
Names dropped from the index over the window are absent, and recent additions
carry their pre-addition history. So historical breadth is "how many of today's
members were above their 200-day average then", not "how many members at the
time" — the two differ, and the first flatters the past because dropped names
were usually falling. Free point-in-time constituents do not exist; the Norgate
corpus that has them is Windows-PC-only. Read the percentiles with that in mind.

The depth matters more than it sounds: on the 1.4-year window this store had
before the backfill, breadth read 99.4th percentile ("high"). On five years
including 2022 the SAME value reads 77.7th ("mid"). The thin window was not
merely imprecise, it was misleading.
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np

from db.base import DEFAULT_DB_PATH
from db.macro import DEFAULT_MACRO_DB_PATH, upsert_macro
from db.momentum_bars import load_all_closes

logger = logging.getLogger(__name__)

# Stored under lowercase names so a reader can tell a derived statistic from a
# vendor series id at a glance (FRED ids are uppercase).
BREADTH_SERIES = "breadth_above_200dma"
CORR_SERIES = "corr_realized_21d"
DERIVED_SERIES: dict[str, str] = {
    BREADTH_SERIES: "breadth",
    CORR_SERIES: "correlation",
}

_MA_WINDOW = 200  # the line everyone watches; also why the series starts 200 sessions late
_CORR_WINDOW = 21  # ~1 trading month of returns
_MIN_NAMES = 50  # below this a cross-sectional statistic is describing noise


def _panel(db_path: Path) -> tuple[list[str], np.ndarray]:
    """(dates, T×N close matrix) over names with a COMPLETE series.

    Dropping incomplete names (IPOs, index adds mid-window) keeps both statistics
    on a fixed cross-section, so a change in the number means a change in the
    MARKET rather than a change in the panel.
    """
    rows = load_all_closes(db_path=db_path)
    dates = sorted({r[1] for r in rows})
    syms = sorted({r[0] for r in rows})
    di = {d: i for i, d in enumerate(dates)}
    si = {s: i for i, s in enumerate(syms)}
    m = np.full((len(dates), len(syms)), np.nan)
    for sym, day, close in rows:
        m[di[day], si[sym]] = close
    complete = ~np.isnan(m).any(axis=0)
    return dates, m[:, complete]


def _mean_pairwise_corr(window: np.ndarray) -> float:
    """Average off-diagonal correlation of a T×N return window.

    Computed WITHOUT forming the N×N matrix: standardise each column, then the
    sum of every entry of ZᵀZ/(T-1) equals ‖row-sums of Z‖²/(T-1). O(T·N) rather
    than O(T·N²), and verified EXACTLY equal to the np.corrcoef answer (0.0e+00
    difference at N=497). Measured 10× faster over the full panel — less than the
    operation count suggests, because np.corrcoef is heavily optimised.
    """
    z = window - window.mean(axis=0)
    sd = z.std(axis=0, ddof=1)
    keep = sd > 0
    z, n = z[:, keep] / sd[keep], int(keep.sum())
    if n < _MIN_NAMES:
        return float("nan")
    total = float((z.sum(axis=1) ** 2).sum()) / (len(z) - 1)
    return (total - n) / (n * (n - 1))


def compute_internals(
    *,
    db_path: Path = DEFAULT_DB_PATH,
    macro_db_path: Path = DEFAULT_MACRO_DB_PATH,
) -> dict[str, int]:
    """Recompute both series over all available history and upsert. Idempotent."""
    dates, closes = _panel(db_path)
    if closes.shape[1] < _MIN_NAMES or len(dates) <= _MA_WINDOW:
        logger.warning(
            "market internals: panel too small (%d names, %d sessions) — skipped",
            closes.shape[1], len(dates),
        )
        return {}

    # Breadth: share of the cross-section above its own 200-session average.
    csum = np.cumsum(np.vstack([np.zeros(closes.shape[1]), closes]), axis=0)
    ma = (csum[_MA_WINDOW:] - csum[:-_MA_WINDOW]) / _MA_WINDOW
    above = (closes[_MA_WINDOW - 1:] > ma).mean(axis=1) * 100.0
    breadth = [
        (BREADTH_SERIES, d, round(float(v), 2))
        for d, v in zip(dates[_MA_WINDOW - 1:], above)
        if np.isfinite(v)
    ]

    rets = np.diff(np.log(closes), axis=0)
    corr = [
        (CORR_SERIES, dates[i + 1], round(c, 4))
        for i in range(_CORR_WINDOW, len(rets))
        if np.isfinite(c := _mean_pairwise_corr(rets[i - _CORR_WINDOW : i]))
    ]

    written = {
        BREADTH_SERIES: upsert_macro(breadth, db_path=macro_db_path),
        CORR_SERIES: upsert_macro(corr, db_path=macro_db_path),
    }
    logger.info(
        "market internals: %d names, breadth=%d corr=%d rows",
        closes.shape[1], written[BREADTH_SERIES], written[CORR_SERIES],
    )
    return written
