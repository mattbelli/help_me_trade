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


def _grid_panel() -> list[Obs]:
    """5 symbols x 5 IV levels x 4 months, with VRP driven ONLY by the level axis.

    Level and 'between' (the symbol's trailing mean) are constructed independent: every
    symbol carries the same five IV values, and symbols differ only in trailing mean.
    """
    obs = []
    levels = [0.10, 0.20, 0.30, 0.40, 0.50]
    for s in range(5):
        tr_mean = 0.10 * (s + 1)
        for k, iv in enumerate(levels):
            for month in (1, 2, 3, 4):
                obs.append(Obs(f"S{s}", f"2024-{month:02d}-15", iv,
                               rv_fwd=iv - 0.01 * k, ivr_range=0.5, ivr_pct=0.5,
                               iv_tr_mean=tr_mean))
    return obs


def test_within_axis_is_the_relative_deviation_from_a_symbols_own_normal() -> None:
    from tools.iv_rank_baseline_study import axis_values

    obs = [
        Obs("A", "2024-01-02", 0.30, 0.2, 0.5, 0.5, iv_tr_mean=0.20),  # 50% above normal
        Obs("B", "2024-01-02", 0.30, 0.2, 0.5, 0.5, iv_tr_mean=None),  # unknowable
    ]
    within = axis_values(obs, "within")
    assert abs(within[0] - 0.5) < 1e-12
    assert within[1] is None
    # BETWEEN is the trailing mean itself; LEVEL is the raw IV.
    assert axis_values(obs, "between") == [0.20, None]
    assert axis_values(obs, "level") == [0.30, 0.30]


def test_double_sort_control_finds_a_real_inner_effect() -> None:
    """VRP depends only on level, so level must survive holding 'between' fixed."""
    from tools.iv_rank_baseline_study import double_sort_control

    res = double_sort_control(_grid_panel(), outer="between", inner="level")
    assert res["inner_orders_vrp_in_every_outer_quintile"] is True
    assert abs(res["pooled_within_outer_diff"] - 0.04) < 1e-9  # 4 steps of 0.01
    assert res["passes"] is True


def test_double_sort_control_rejects_an_effect_that_is_not_there() -> None:
    """The mirror: 'between' must NOT appear to order VRP, since nothing depends on it.

    A control that cannot return False would have rubber-stamped the rank gate.
    """
    from tools.iv_rank_baseline_study import double_sort_control

    res = double_sort_control(_grid_panel(), outer="level", inner="between")
    assert res["inner_orders_vrp_in_every_outer_quintile"] is False
    assert abs(res["pooled_within_outer_diff"]) < 1e-9
    assert res["passes"] is False


def test_top_decile_test_selects_the_top_tenth_of_the_axis() -> None:
    from tools.iv_rank_baseline_study import top_decile_test

    obs = [
        Obs("A", f"2024-{(i % 4) + 1:02d}-15", iv=0.01 * (i + 1), rv_fwd=0.01 * (i + 1) - 0.05,
            ivr_range=0.5, ivr_pct=0.5, iv_tr_mean=0.2)
        for i in range(200)
    ]
    res = top_decile_test(obs, "level", "smoke")
    assert res["n_rows"] == 20  # exactly the top decile by IV
    assert res["mean_iv"] > obs[-25].iv
    assert abs(res["mean_vrp"] - 0.05) < 1e-9


def test_diff_terms_isolate_the_top_and_bottom_rank_deciles() -> None:
    obs = [
        Obs("A", "2024-01-02", 0.3, 0.1, ivr_range=0.95, ivr_pct=None),  # top, vrp +0.2
        Obs("A", "2024-01-03", 0.3, 0.25, ivr_range=0.05, ivr_pct=None),  # bottom, vrp +0.05
        Obs("A", "2024-01-04", 0.3, 0.2, ivr_range=0.50, ivr_pct=None),  # middle, ignored
    ]
    terms = _diff_terms(obs, "ivr_range")
    totals = [sum(x) for x in zip(*terms.values(), strict=False)]
    assert abs(_diff_combine(totals) - (0.20 - 0.05)) < 1e-12


# --- Study C: breadth, chunked bar loading, and the guards on its two comparisons ---


def _bars(dates: list[str], closes: list[float]) -> list:
    from types import SimpleNamespace

    return [SimpleNamespace(time=f"{d}T00:00:00Z", close=c)
            for d, c in zip(dates, closes, strict=True)]


def _aligned(n: int = 60, sym: str = "TEST") -> tuple[list[tuple], list[str], list[float]]:
    """A corpus series and a bar series on the same weekday dates."""
    from datetime import date, timedelta

    dates, d = [], date.fromisoformat("2024-01-01")
    while len(dates) < n:
        if d.weekday() < 5:
            dates.append(d.isoformat())
        d += timedelta(days=1)
    rows = [(sym, dt, "0.30", "0.20", "0.60", "0.10") for dt in dates]
    # A deterministic wiggle so forward RV is well-defined and not constant.
    closes = [100.0 * (1.0 + 0.01 * ((i % 5) - 2)) for i in range(n)]
    return rows, dates, closes


def _patch_bars(monkeypatch, bars_by_symbol: dict) -> None:
    import tools.iv_rank_baseline_study as m

    monkeypatch.setattr(m, "_h4_fetch_bars",
                        lambda adapter, chunk: {s: bars_by_symbol[s] for s in chunk if s in bars_by_symbol})
    monkeypatch.setattr("brokers.alpaca.AlpacaAdapter",
                        type("_A", (), {"from_settings": staticmethod(lambda: object())}))


def test_h4_chunk_size_does_not_change_the_panel(monkeypatch) -> None:
    """The breadth refactor made bars chunk-local; chunking must be invisible to results."""
    import tools.iv_rank_baseline_study as m

    rows, dates, closes = _aligned()
    syms = [f"S{i}" for i in range(7)]
    corpus = [(s, dt, iv, hv, hi, lo) for s in syms for (_, dt, iv, hv, hi, lo) in rows]
    conn = _db(corpus)
    _patch_bars(monkeypatch, {s: _bars(dates, closes) for s in syms})

    monkeypatch.setattr(m, "H4_CHUNK", 1)
    one, meta_one = m.load_panel_h4(conn, syms)
    monkeypatch.setattr(m, "H4_CHUNK", 25)
    many, meta_many = m.load_panel_h4(conn, syms)

    assert one and len(one) == len(many)
    assert [(o.symbol, o.date, o.iv, o.rv_fwd) for o in one] == \
           [(o.symbol, o.date, o.iv, o.rv_fwd) for o in many]
    assert meta_one == meta_many


def test_h4_forward_rv_is_the_next_21_sessions_of_our_own_bars(monkeypatch) -> None:
    import statistics

    import tools.iv_rank_baseline_study as m

    rows, dates, closes = _aligned()
    conn = _db(rows)
    meta = {"symbols_with_bars": 0, "no_forward_window": 0}
    obs = m._h4_symbol_obs(conn, "TEST", _bars(dates, closes), meta)

    rets = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]
    first = obs[0]
    assert first.date == dates[0]
    expected = statistics.stdev(rets[0:m.H4_SESSIONS]) * math.sqrt(252)
    assert abs(first.rv_fwd - expected) < 1e-12
    # The tail has no room for a full forward window and is counted, not fabricated.
    assert meta["no_forward_window"] > 0
    assert all(o.date <= dates[-(m.H4_SESSIONS + 1)] for o in obs)


def test_level_deciles_are_ordered_by_iv_level() -> None:
    from tools.iv_rank_baseline_study import _level_deciles

    obs = [Obs("S", f"2024-01-{(i % 28) + 1:02d}", 0.10 + 0.01 * i, 0.15, 0.5, 0.5)
           for i in range(200)]
    rows = _level_deciles(obs)
    assert len(rows) == 10
    assert [r["decile"] for r in rows] == list(range(1, 11))
    means = [r["mean_iv"] for r in rows]
    assert means == sorted(means)


def test_c2_pairs_only_observations_present_under_both_horizons() -> None:
    """C2 is a like-for-like: a date only one panel has must not enter the comparison."""
    from tools.iv_rank_baseline_study import run_h4_breadth

    h4 = [Obs("A", f"2024-0{m_}-15", 0.30, 0.20, 0.5, 0.5) for m_ in range(1, 10)]
    vendor = list(h4) + [Obs("A", "2023-06-15", 0.90, 0.10, 0.5, 0.5)]  # unpaired outlier
    c = run_h4_breadth(h4, vendor, deep=set())
    assert c["C2"]["n_paired_rows"] == len(h4)
    # The unpaired 0.80-VRP row would have swamped the vendor mean had it leaked in.
    assert abs(c["C2"]["own_bars_21s"] - c["C2"]["vendor_30s"]) < 1e-12
    assert c["C2"]["passes"] is True


def test_c6_counts_symbols_the_bar_api_cannot_serve() -> None:
    """Delisted names vanish from a live bar API — C6 exists to measure that, not assume it."""
    from tools.iv_rank_baseline_study import run_h4_breadth

    h4 = [Obs("LIVE", f"2024-0{m_}-15", 0.30, 0.20, 0.5, 0.5) for m_ in range(1, 10)]
    vendor = list(h4) + [Obs("DEAD", f"2024-0{m_}-15", 0.30, 0.95, 0.5, 0.5) for m_ in range(1, 10)]
    c = run_h4_breadth(h4, vendor, deep=set())
    assert c["C6"]["n_symbols_with_bars"] == 1
    assert c["C6"]["n_symbols_without"] == 1
    # The vanished name realized far more vol than implied — a large negative VRP.
    assert c["C6"]["vendor_mean_vrp_without_bars"] < 0 < c["C6"]["vendor_mean_vrp_with_bars"]
    assert c["C6"]["flagged"] is True
