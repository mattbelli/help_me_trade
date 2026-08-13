"""Breadth + realized correlation computed from our own bars.

No network: the panel is seeded directly into a temp bar store.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pytest

from db.macro import series_history
from db.momentum_bars import upsert_daily_bars
from signals.market_internals import (
    BREADTH_SERIES,
    CORR_SERIES,
    _mean_pairwise_corr,
    compute_internals,
)


def test_fast_mean_pairwise_corr_equals_the_correlation_matrix() -> None:
    """The O(T·N) identity is the only non-obvious maths in this module: the sum of
    every entry of the correlation matrix equals ‖row-sums of the standardised
    returns‖²/(T-1). If that is wrong, the axis is silently wrong and nothing else
    would notice."""
    rng = np.random.default_rng(0)
    for n in (60, 200, 497):
        # A common factor, so the correlation is real rather than ~0 noise.
        x = rng.normal(size=(21, n)) + rng.normal(size=(21, 1)) * 0.7
        c = np.corrcoef(x.T)
        naive = (c.sum() - n) / (n * (n - 1))
        assert _mean_pairwise_corr(x) == pytest.approx(naive, abs=1e-12)


def test_a_panel_moving_in_lockstep_is_perfectly_correlated() -> None:
    """Sanity anchor with a known answer — every name the same series => 1.0."""
    rng = np.random.default_rng(1)
    one = rng.normal(size=(21, 1))
    assert _mean_pairwise_corr(np.repeat(one, 80, axis=1)) == pytest.approx(1.0)


def test_a_flat_name_cannot_poison_the_average(tmp_path: Path) -> None:
    """A name that never moves has zero variance and NO correlation with anything.
    Left in, it makes every pair involving it NaN and the whole axis NaN."""
    rng = np.random.default_rng(2)
    x = rng.normal(size=(21, 80)) + rng.normal(size=(21, 1))
    x[:, 0] = 5.0  # dead series
    out = _mean_pairwise_corr(x)
    assert np.isfinite(out) and 0.0 < out < 1.0


def test_compute_internals_writes_both_series(tmp_path: Path) -> None:
    bars_db, macro_db = tmp_path / "trading.db", tmp_path / "macro.db"
    start = date(2025, 1, 1)
    rng = np.random.default_rng(3)
    sessions = 260
    rows: list[tuple[str, str, float, float]] = []
    for s in range(60):  # _MIN_NAMES
        px = 100.0
        for i in range(sessions):
            px *= float(np.exp(rng.normal(0.0006, 0.012)))
            d = (start + timedelta(days=i)).isoformat()
            rows.append((f"SYM{s:02d}", d, px, px))
    upsert_daily_bars(rows, db_path=bars_db)

    written = compute_internals(db_path=bars_db, macro_db_path=macro_db)
    assert written[BREADTH_SERIES] > 0 and written[CORR_SERIES] > 0
    # Breadth starts 200 sessions late by construction — the 200-day average has
    # to exist before "above it" means anything.
    assert written[BREADTH_SERIES] == sessions - 199
    breadth = series_history(BREADTH_SERIES, db_path=macro_db)
    assert all(0.0 <= v <= 100.0 for _, v in breadth)
    corr = series_history(CORR_SERIES, db_path=macro_db)
    assert all(-1.0 <= v <= 1.0 for _, v in corr)


def test_too_small_a_panel_writes_nothing(tmp_path: Path) -> None:
    """A cross-sectional statistic over a handful of names describes noise. Better
    to emit nothing than a confident number the agent will rank."""
    bars_db, macro_db = tmp_path / "trading.db", tmp_path / "macro.db"
    start = date(2025, 1, 1)
    upsert_daily_bars(
        [(f"SYM{s}", (start + timedelta(days=i)).isoformat(), 100.0, 100.0 + i)
         for s in range(5) for i in range(260)],
        db_path=bars_db,
    )
    assert compute_internals(db_path=bars_db, macro_db_path=macro_db) == {}
    assert series_history(CORR_SERIES, db_path=macro_db) == []
