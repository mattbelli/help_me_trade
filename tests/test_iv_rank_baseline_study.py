"""IV-rank study — the drop rules, the no-lookahead guarantee, and the block bootstrap.

This is research code that feeds a real-money decision (`options-income-lane`), so the
parts that could silently corrupt a verdict get tests: a trailing window that peeks at
the future would manufacture an edge, and a bootstrap that ignores overlap would
manufacture significance.
"""

from __future__ import annotations

import math
import sqlite3

from tools.iv_rank_baseline_study import (
    MIN_TRAILING_OBS,
    Obs,
    _diff_combine,
    _diff_terms,
    _f,
    _mean_combine,
    _mean_terms,
    decile_table,
    effective_n,
    load_panel,
    month_block_bootstrap,
    spearman,
)


def _db(rows: list[tuple]) -> sqlite3.Connection:
    """In-memory corpus: (symbol, date, iv, hv, iv_year_high, iv_year_low)."""
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE volatility_history (act_symbol TEXT, date TEXT, iv_current TEXT,"
        " hv_current TEXT, iv_year_high TEXT, iv_year_low TEXT)"
    )
    conn.executemany("INSERT INTO volatility_history VALUES (?,?,?,?,?,?)", rows)
    return conn


def _series(n: int, *, start: str = "2024-01-01", iv: float = 0.20, hv: float = 0.18) -> list[tuple]:
    """n consecutive weekdays of a single symbol."""
    from datetime import date, timedelta

    out, d = [], date.fromisoformat(start)
    while len(out) < n:
        if d.weekday() < 5:
            out.append(("TEST", d.isoformat(), f"{iv}", f"{hv}", "0.60", "0.10"))
        d += timedelta(days=1)
    return out


def test_empty_and_nonpositive_values_are_absent_not_zero() -> None:
    assert _f("") is None and _f(None) is None
    assert _f("0") is None and _f("-1") is None  # a zero vol is missing data, not a vol
    assert _f("0.25") == 0.25


def test_forward_row_must_land_on_a_real_30_session_horizon() -> None:
    """A sparse corpus makes '30 rows ahead' a ~70-day horizon — those are refused."""
    dense = _series(200)
    panel, drop = load_panel(_db(dense))
    assert panel, "a dense series must produce observations"
    assert drop.no_forward_row == 30  # only the last 30 rows lack a forward row

    # Same row count, but every row a week apart: 30 rows ahead is ~210 days.
    from datetime import date, timedelta

    sparse, d = [], date.fromisoformat("2024-01-01")
    for _ in range(200):
        sparse.append(("TEST", d.isoformat(), "0.20", "0.18", "0.60", "0.10"))
        d += timedelta(days=7)
    panel_sparse, drop_sparse = load_panel(_db(sparse))
    assert panel_sparse == []
    assert drop_sparse.no_forward_row == 200


def test_trailing_percentile_never_sees_the_future() -> None:
    """The guarantee that matters: a later IV spike must not change an earlier rank."""
    n = MIN_TRAILING_OBS + 80
    base = _series(n)
    panel_a, _ = load_panel(_db(base))

    spiked = list(base)
    for i in range(n - 20, n):  # blow up IV only at the very end of the series
        s, d, _iv, hv, hi, lo = spiked[i]
        spiked[i] = (s, d, "3.00", hv, hi, lo)
    panel_b, _ = load_panel(_db(spiked))

    early_a = {o.date: o.ivr_pct for o in panel_a if o.date < base[n - 40][1]}
    early_b = {o.date: o.ivr_pct for o in panel_b if o.date < base[n - 40][1]}
    assert early_a and early_a == early_b


def test_thin_trailing_window_yields_no_percentile() -> None:
    panel, drop = load_panel(_db(_series(MIN_TRAILING_OBS - 20)))
    assert all(o.ivr_pct is None for o in panel)
    assert drop.thin_trailing_window > 0


def test_vrp_sign_convention_is_sold_rich_is_positive() -> None:
    o = Obs("X", "2024-01-01", iv=0.30, rv_fwd=0.20, ivr_range=0.5, ivr_pct=0.5)
    assert abs(o.vrp - 0.10) < 1e-12  # sold rich => positive


def test_effective_n_counts_symbol_months_not_rows() -> None:
    obs = [
        Obs("A", "2024-01-02", 0.2, 0.1, 0.5, 0.5),
        Obs("A", "2024-01-03", 0.2, 0.1, 0.5, 0.5),  # same symbol-month
        Obs("A", "2024-02-02", 0.2, 0.1, 0.5, 0.5),
        Obs("B", "2024-01-02", 0.2, 0.1, 0.5, 0.5),
    ]
    assert len(obs) == 4
    assert effective_n(obs) == 3


def test_spearman_is_one_on_a_perfectly_monotone_series() -> None:
    assert spearman([1, 2, 3, 4], [10.0, 20.0, 30.0, 40.0]) == 1.0
    assert spearman([1, 2, 3, 4], [40.0, 30.0, 20.0, 10.0]) == -1.0


def test_bootstrap_ci_brackets_the_point_estimate() -> None:
    obs = [
        Obs("A", f"2024-{m:02d}-{d:02d}", 0.3, 0.2, 0.5, 0.5)
        for m in range(1, 13) for d in (1, 2, 3, 4, 5)
    ]
    lo, hi = month_block_bootstrap(_mean_terms(obs), _mean_combine)
    # Every VRP is identical here, so the CI must collapse onto the value itself.
    assert abs(lo - 0.1) < 1e-9 and abs(hi - 0.1) < 1e-9


def test_bootstrap_needs_at_least_three_months() -> None:
    obs = [Obs("A", "2024-01-02", 0.3, 0.2, 0.5, 0.5)]
    lo, hi = month_block_bootstrap(_mean_terms(obs), _mean_combine)
    assert math.isnan(lo) and math.isnan(hi)


def test_decile_table_reports_the_mandatory_tail_columns() -> None:
    obs = [
        Obs("A", f"2024-01-{(i % 28) + 1:02d}", 0.3, 0.3 - (i % 10) * 0.01,
            ivr_range=(i % 10) / 10 + 0.05, ivr_pct=0.5)
        for i in range(200)
    ]
    table = decile_table(obs, "ivr_range")
    assert len(table) == 10
    for row in table:
        for col in ("mean_vrp", "p05_vrp", "worst_symbol_month", "share_negative"):
            assert col in row, f"{col} is mandatory per the prereg"
        assert row["p05_vrp"] <= row["mean_vrp"] or row["n_rows"] < 20


def test_diff_terms_isolate_the_top_and_bottom_rank_deciles() -> None:
    obs = [
        Obs("A", "2024-01-02", 0.3, 0.1, ivr_range=0.95, ivr_pct=None),  # top, vrp +0.2
        Obs("A", "2024-01-03", 0.3, 0.25, ivr_range=0.05, ivr_pct=None),  # bottom, vrp +0.05
        Obs("A", "2024-01-04", 0.3, 0.2, ivr_range=0.50, ivr_pct=None),  # middle, ignored
    ]
    terms = _diff_terms(obs, "ivr_range")
    totals = [sum(x) for x in zip(*terms.values(), strict=False)]
    assert abs(_diff_combine(totals) - (0.20 - 0.05)) < 1e-12
