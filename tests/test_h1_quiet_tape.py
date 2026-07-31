"""Registered-semantics tests for tools/h1_quiet_tape.py (H1 prereg v5).

Every test here pins a REGISTERED convention against a hand-computed value on
synthetic fixtures — no real data store is touched. The pins map to the
prereg: References/H1_QUIET_TAPE_PREREG_2026-07-31.md and the module's P1-P11.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date, timedelta

import numpy as np
import pytest

from tools import h1_quiet_tape as h1
from tools import open_close_split as ocs
from tools.flow_obligation3 import circular_blocks
from tools.h1_quiet_tape import (
    BlockingHole,
    Graded,
    Panel,
    _gate,
    _nw_batch,
    _ordinal_pct,
    atr14,
    calibrate,
    grade_name_day,
    h1_blocks,
    nw_stats,
    placebo,
    rv_grid,
    session_population,
)

H = h1.HORIZON


# ------------------------------ percentile convention (P2) ------------------------------

def test_ordinal_pct_ties_get_distinct_consecutive_ranks():
    vals = np.array([5.0, 5.0, 1.0, 9.0])
    mask = np.ones(4, bool)
    pct = _ordinal_pct(vals, mask)
    # stable double-argsort: ranks 1, 2, 0, 3 -> pct * 100/(n-1)
    assert pct.tolist() == [100 / 3, 200 / 3, 0.0, 100.0]


def test_ordinal_pct_single_member_guard():
    pct = _ordinal_pct(np.array([7.0, 1.0]), np.array([True, False]))
    assert pct[0] == 0.0 and np.isnan(pct[1])          # max(n-1, 1) guard


def test_ordinal_pct_off_population_nan():
    pct = _ordinal_pct(np.array([1.0, 2.0, 3.0]), np.array([True, False, True]))
    assert np.isnan(pct[1]) and pct[0] == 0.0 and pct[2] == 100.0


def test_event_cut_boundary_inclusive():
    # cuts are >= 80.0 and <= 33.0 exactly; a 5-member population puts one
    # name exactly at 100*3/4 = 75 (fails) and one at 100 (passes)
    vals = np.arange(5, dtype=float)
    pct = _ordinal_pct(vals, np.ones(5, bool))
    assert ((pct >= h1.F_OPEN_CUT).sum(), (pct <= h1.RV_CUT).sum()) == (1, 2)


# ------------------------------ rv grid (P4) ------------------------------

def test_rv_grid_early_close_truncation():
    assert len(rv_grid(date(2025, 11, 28))) == 42
    assert len(rv_grid(date(2025, 12, 24))) == 42
    assert len(rv_grid(date(2026, 7, 17))) == 78


# ------------------------------ ATR14 (G.2 pin) ------------------------------

def test_atr14_hand_computed_inclusive_of_d():
    n = 20
    h = np.full(n, 101.0)
    lo = np.full(n, 99.0)
    c = np.full(n, 100.0)
    assert atr14(h, lo, c, 19) == pytest.approx(2.0)
    # gap day inside the window: TR = |h - c_prev| = 6 on that day
    h2, c2 = h.copy(), c.copy()
    c2[10] = 95.0                      # prior close for day 11
    h2[11] = 101.0                     # TR_11 = max(2, 101-95, |99-95|) = 6
    got = atr14(h2, lo, c2, 19)
    # window is days 6..19; day 10's own TR also changes: max(2, |101-100|, |99-100|)=2
    assert got == pytest.approx((13 * 2.0 + 6.0) / 14)


def test_atr14_requires_15_bars_through_d():
    h = np.full(14, 101.0)
    assert atr14(h, h - 2, h - 1, 13) is None          # 14 bars: case (iii)


# ------------------------------ terminal-value rule ------------------------------

def _calendar(n=70):
    dates = [f"d{i:03d}" for i in range(n)]
    cal_pos = {d: i for i, d in enumerate(dates)}
    spy_o = np.full(n, 500.0)
    spy_c = np.full(n, 500.0)
    return dates, cal_pos, spy_o, spy_c


def _bars(cal, i0, i1, close=None):
    """Flat 100-bars covering calendar indices [i0, i1]."""
    ds = cal[i0:i1 + 1]
    n = len(ds)
    o = np.full(n, 100.0)
    h = np.full(n, 101.0)
    lo = np.full(n, 99.0)
    c = np.full(n, 100.0) if close is None else close
    return (ds, o, h, lo, c)


def test_case_ii_zero_bars():
    _, cal_pos, spy_o, spy_c = _calendar()
    for bars in (None, ([], np.array([]), np.array([]), np.array([]), np.array([]))):
        g = grade_name_day(bars, cal_pos, spy_o, spy_c, 20, 69, True)
        assert g.case == "ii"


def test_case_iii_no_bar_at_d_and_short_history_and_zero_atr():
    cal, cal_pos, spy_o, spy_c = _calendar()
    # series ends before d -> (iii), pinned so it never splits with (iv)
    g = grade_name_day(_bars(cal, 0, 19), cal_pos, spy_o, spy_c, 20, 69, True)
    assert g.case == "iii"
    # only 10 bars through d -> (iii)
    g = grade_name_day(_bars(cal, 11, 30), cal_pos, spy_o, spy_c, 20, 69, True)
    assert g.case == "iii"
    # zero-range series -> ATR14 == 0 -> (iii)
    ds = cal[0:31]
    flat = np.full(31, 100.0)
    g = grade_name_day((ds, flat, flat, flat, flat), cal_pos, spy_o, spy_c,
                       20, 69, True)
    assert g.case == "iii"


def test_case_iv_no_bar_at_entry_after_termination():
    cal, cal_pos, spy_o, spy_c = _calendar()
    g = grade_name_day(_bars(cal, 0, 20), cal_pos, spy_o, spy_c, 20, 69, True)
    assert g.case == "iv" and g.atr is not None


def test_case_i_truncation_grades_at_last_close():
    cal, cal_pos, spy_o, spy_c = _calendar()
    close = np.full(26, 100.0)
    close[-1] = 104.0                                  # last close, idx k0+5
    g = grade_name_day(_bars(cal, 0, 25, close), cal_pos, spy_o, spy_c,
                       20, 69, True)
    assert g.case == "truncated" and g.trunc_at == 5
    assert g.covered[3] == "full" and g.covered[H] == "truncated"
    # alpha(10td) graded at the truncated window: (104/100-1 - 0)*100/2 = 2.0
    assert g.alpha[H] == pytest.approx(2.0)


def test_full_alpha_hand_computed_and_spy_benchmark():
    cal, cal_pos, spy_o, spy_c = _calendar()
    spy_c = spy_c.copy()
    spy_c[23] = 505.0                                  # +1% SPY over 3td window
    close = np.full(70, 100.0)
    close[30] = 102.0                                  # +2% name at 10td
    g = grade_name_day(_bars(cal, 0, 69, close), cal_pos, spy_o, spy_c,
                       20, 69, True)
    assert g.case == "full"
    assert g.alpha[3] == pytest.approx((0.0 - 0.01) * 100 / 2)   # -0.5
    assert g.alpha[H] == pytest.approx((0.02 - 0.0) * 100 / 2)   # +1.0
    assert g.open_over_atr == pytest.approx(50.0)
    assert g.atr_over_price == pytest.approx(0.02)               # P6: ATR/close_d


def test_not_elapsed_columns_p9():
    cal, cal_pos, spy_o, spy_c = _calendar(40)
    g = grade_name_day(_bars(cal, 0, 39), cal_pos, spy_o, spy_c, 20, 39, True)
    assert g.covered[3] == "full" and g.covered[10] == "full"
    assert g.covered[21] == "not_elapsed" and g.covered[45] == "not_elapsed"
    assert g.alpha[21] is None


def test_interior_hole_blocks_p8():
    cal, cal_pos, spy_o, spy_c = _calendar()
    # missing entry bar at d+1 with later bars present
    ds = cal[0:21] + cal[22:41]
    n = len(ds)
    bars = (ds, np.full(n, 100.0), np.full(n, 101.0),
            np.full(n, 99.0), np.full(n, 100.0))
    with pytest.raises(BlockingHole):
        grade_name_day(bars, cal_pos, spy_o, spy_c, 20, 69, True)
    # missing bar at d+10 with later bars present
    ds = cal[0:30] + cal[31:41]
    n = len(ds)
    bars = (ds, np.full(n, 100.0), np.full(n, 101.0),
            np.full(n, 99.0), np.full(n, 100.0))
    with pytest.raises(BlockingHole):
        grade_name_day(bars, cal_pos, spy_o, spy_c, 20, 69, True)


def test_cases_mode_computes_no_alpha_p10():
    cal, cal_pos, spy_o, spy_c = _calendar()
    g = grade_name_day(_bars(cal, 0, 69), cal_pos, spy_o, spy_c, 20, 69, False)
    assert g.case == "full" and all(v is None for v in g.alpha.values())
    assert g.covered[H] == "full"                      # coverage still known


# ------------------------------ Newey-West (G.4 formula) ------------------------------

def test_nw_hand_computed():
    t, mean, se, fb = nw_stats(np.array([1.0, 2.0, 3.0]), np.array([0, 1, 2]))
    # gamma0 = 2/3; gamma1 = 0; gamma2 = -1/3
    lrv = 2 / 3 + 2 * (1 - 2 / 10) * (-1 / 3)
    assert not fb
    assert se == pytest.approx(np.sqrt(lrv / 3))
    assert mean == pytest.approx(2.0)
    assert t == pytest.approx(2.0 / np.sqrt(lrv / 3))


def test_nw_zero_pair_gaps_contribute_zero():
    # gaps 0, 11, 22: no pair within 9 lags -> LRV = gamma0 (iid)
    x = np.array([1.0, 2.0, 6.0])
    t, mean, se, fb = nw_stats(x, np.array([0, 11, 22]))
    g0 = np.var(x)                                     # ddof=0 = gamma0
    assert not fb and se == pytest.approx(np.sqrt(g0 / 3))


def test_nw_fallback_uses_own_series_ddof1():
    # injected pair set drives the Bartlett sum negative deterministically:
    # x = [1,-1], every lag h=1..9 sees the single (t=1,s=0) pair
    X = np.array([[1.0, -1.0]])
    pairs = {hh: (np.array([1]), np.array([0])) for hh in range(1, 10)}
    ts, means, ses, fb = _nw_batch(X, np.array([0, 1]), pairs)
    assert fb[0]                                       # LRV = 1 - 4.5 < 0
    assert ses[0] == pytest.approx(np.sqrt(np.var(X[0], ddof=1) / 2))
    assert ts[0] == pytest.approx(0.0)


def test_nw_constant_series_hits_fallback_branch():
    _, _, _, fb = nw_stats(np.array([2.0, 2.0, 2.0]), np.array([0, 1, 2]))
    assert fb


# ------------------------------ block assembly (H1.4 / G.4) ------------------------------

def test_h1_blocks_matches_circular_blocks_when_lengths_equal():
    a = h1_blocks(np.random.default_rng(7), 37, 37, 50)
    b = circular_blocks(np.random.default_rng(7), 37, 50)
    assert np.array_equal(a, b)


def test_h1_blocks_wraps_and_truncates():
    idx = h1_blocks(np.random.default_rng(1), g_src=15, g_out=37, reps=200)
    assert idx.shape == (200, 37)
    assert idx.min() >= 0 and idx.max() < 15           # wraps modulo g_src


# ------------------------------ calibration + MDE ------------------------------

def test_calibrate_cutoff_and_mde_on_synthetic_series():
    rng = np.random.default_rng(0)
    train = rng.normal(0.0, 0.2, size=100)
    gaps = np.arange(37) + np.array([0] * 20 + [2] * 17)   # a gappy calendar
    out = calibrate(train, gaps, reps=2000)
    assert out["seed"] == 20260818 and out["block"] == 10
    assert np.isfinite(out["cutoff_t99"]) and out["cutoff_t99"] > 0
    assert out["mde_atr"] is not None and 0 < out["mde_atr"] <= 1.0
    assert not out["power_limited"]
    assert out["judgment_G"] == 37


def test_calibrate_power_limited_on_huge_variance():
    rng = np.random.default_rng(0)
    train = rng.normal(0.0, 50.0, size=60)             # per-date sd of 50 ATR
    out = calibrate(train, np.arange(37), reps=500)
    assert out["power_limited"]


# ------------------------------ placebo ------------------------------

def test_placebo_preserves_sign_multiset_and_p_floor():
    signs = [np.array([1.0, -1.0, 1.0]), np.array([1.0, 1.0])]
    mags = [np.array([0.5, 0.2, 0.1]), np.array([0.3, 0.4])]
    base = np.array([0.0, 0.1])
    out = placebo(signs, mags, base, np.array([0, 1]), t_real=0.5, reps=200)
    assert out["seed"] == 20260819
    assert 1 / 201 <= out["p_upper"] <= 1.0
    # determinism under the registered seed
    out2 = placebo(signs, mags, base, np.array([0, 1]), t_real=0.5, reps=200)
    assert out2["p_upper"] == out["p_upper"]


def test_placebo_single_sign_dates_are_permutation_invariant():
    # every event on both dates shares one sign, so every permuted series
    # equals the real series and every permuted t equals t_real -> p = 1
    signs = [np.array([1.0, 1.0]), np.array([-1.0, -1.0])]
    mags = [np.array([0.4, 0.2]), np.array([0.3, 0.5])]
    base = np.array([0.0, 0.1])
    diffs = np.array([float((s * m).mean()) - b
                      for s, m, b in zip(signs, mags, base)])
    t_real, _, _, _ = nw_stats(diffs, np.array([0, 1]))
    out = placebo(signs, mags, base, np.array([0, 1]), t_real=t_real, reps=50)
    assert out["p_upper"] == pytest.approx(1.0)        # all 50 tie t_real
    assert out["perm_t_sd"] == pytest.approx(0.0)


# ------------------------------ population gating (P1-P3) ------------------------------

def _tiny_panel():
    n_days = 30
    sessions = [date(2026, 1, 1 + i) if i < 26 else date(2026, 2, i - 25)
                for i in range(n_days)]
    names = np.array(["AAA", "BBB", "CCC", "DDD"])
    prem = np.full((4, n_days), 300_000.0)
    prem[1, :] = 100_000.0                             # BBB never clears the floor
    present = prem > 0
    tilt = np.full((4, n_days), 0.5)
    tilt[3, :] = 0.0                                   # DDD zero tilt
    ck = np.cumsum(np.c_[np.zeros(4), present.astype(float)], 1)
    presence_ok = np.zeros(present.shape, bool)
    for d in range(h1.LOOKBACK, n_days):
        presence_ok[:, d] = (ck[:, d] - ck[:, d - h1.LOOKBACK]) >= h1.MIN_PRESENT
    return Panel(sessions, names, {n: i for i, n in enumerate(names)},
                 prem, tilt, present, presence_ok)


def test_session_population_gates_and_censuses():
    panel = _tiny_panel()
    j = 25
    s = panel.sessions[j].isoformat()
    fo = {(s, "AAA"): (5000.0, 0.7), (s, "DDD"): (2000.0, 0.6)}   # CCC: no OI
    rv_j = np.array([0.1, 0.1, 0.1, np.nan])           # DDD: no rv
    pop = session_population(panel, rv_j, fo, j)
    assert pop.base.tolist() == [True, False, True, True]
    assert pop.oi_ok.tolist() == [True, False, False, True]
    assert pop.ranked.tolist() == [True, False, False, False]
    assert pop.n_no_oi == 1 and pop.n_no_rv == 1
    assert pop.coverage == pytest.approx(2 / 3)        # P3: oi_ok / base
    assert pop.f_pct[0] == 0.0                         # single-member population


def test_presence_needs_20_day_window():
    panel = _tiny_panel()
    assert not panel.presence_ok[:, h1.LOOKBACK - 1].any()   # undefined early
    assert panel.presence_ok[:, h1.LOOKBACK].all()


# ------------------------------ f_open refactor seam ------------------------------

def test_f_open_by_identity_rekey_sum_before_floor(tmp_path, monkeypatch):
    db = tmp_path / "oi.db"
    con = sqlite3.connect(db)
    con.execute("""CREATE TABLE oi_reconcile_rollup (
        session TEXT, underlying TEXT, right TEXT, dte_bucket TEXT,
        contracts INTEGER, volume INTEGER, doi_net INTEGER,
        vol_weighted_f_open REAL, flag_doi_gt_v INTEGER, flag_expired INTEGER)""")
    rows = [
        # OLD + NEW both print on the seam session; each is below the floor
        # alone, together they clear it — sum-before-floor is the pin
        ("2026-01-05", "OLD", "C", "0", 1, 600, 0, 0.8, 0, 0),
        ("2026-01-05", "NEW", "P", "0", 1, 600, 0, 0.4, 0, 0),
        # a name below the floor even after aggregation
        ("2026-01-05", "TINY", "C", "0", 1, 100, 0, 0.9, 0, 0),
        # NULL f_open rows are excluded by the WHERE clause
        ("2026-01-05", "NUL", "C", "0", 1, 5000, 0, None, 0, 0),
    ]
    con.executemany("INSERT INTO oi_reconcile_rollup VALUES (?,?,?,?,?,?,?,?,?,?)",
                    rows)
    con.commit()
    con.close()
    monkeypatch.setattr(ocs, "OI_DB", db)

    def resolve(root, session):
        return "NEW" if root in ("OLD", "NEW") else root

    fo = ocs.f_open_by_identity(resolve=resolve)
    assert set(fo) == {("2026-01-05", "NEW")}
    v, f = fo[("2026-01-05", "NEW")]
    assert v == 1200 and f == pytest.approx((600 * 0.8 + 600 * 0.4) / 1200)
    # and the ranking wrapper still returns a percentile per surviving name
    pct = ocs._f_open_pct(resolve=resolve)
    assert pct[("2026-01-05", "NEW")] == 0.0


# ------------------------------ calendar gates (P7) ------------------------------

def test_gates_refuse_before_close_of_2026_08_07():
    for stage in ("record", "calibrate", "score"):
        with pytest.raises(SystemExit):
            _gate(stage, today=date(2026, 8, 7))
        _gate(stage, today=date(2026, 8, 8))           # first permitted day
    _gate("eligibility", today=date(2026, 7, 31))      # never gated


# ------------------------------ Graded invariants ------------------------------

def test_graded_case_never_both_truncated_and_ungradeable():
    cal, cal_pos, spy_o, spy_c = _calendar()
    g = grade_name_day(_bars(cal, 0, 25), cal_pos, spy_o, spy_c, 20, 69, True)
    assert g.case == "truncated"
    assert isinstance(g, Graded)
    assert g.covered[45] == "truncated"                # stops mid-window later too


def test_atr_window_hole_before_d_blocks_p8():
    cal, cal_pos, spy_o, spy_c = _calendar()
    ds = cal[0:10] + cal[11:26]                        # hole at calendar 10
    n = len(ds)
    bars = (ds, np.full(n, 100.0), np.full(n, 101.0),
            np.full(n, 99.0), np.full(n, 100.0))
    with pytest.raises(BlockingHole, match="before d"):
        grade_name_day(bars, cal_pos, spy_o, spy_c, 20, 69, True)


# ------------------------------ NW guards ------------------------------

def test_nw_rejects_length_mismatch_and_short_series():
    with pytest.raises(ValueError, match="gap vector length"):
        nw_stats(np.array([1.0, 2.0, 3.0]), np.array([0, 1]))
    with pytest.raises(ValueError, match="< 2"):
        nw_stats(np.array([1.0]), np.array([0]))


def test_calibrate_guards_nonfinite_and_short_train():
    with pytest.raises(SystemExit, match="cannot calibrate"):
        calibrate(np.array([1.0]), np.arange(37), reps=10)
    with pytest.raises(SystemExit, match="non-finite"):
        calibrate(np.array([1.0, np.nan, 2.0]), np.arange(37), reps=10)


# ------------------------------ build_window (synthetic end-to-end) ------------------------------

def _fixture_panel():
    """11 identities, 30 sessions in the training window; live date = index 25.

    Ranked pcts at n=11: k*100/10 -> cond1 (>=80): ranks 8,9,10; cond2
    (<=33): ranks 0..3. Design: ZZZ.B = event (f rank 10, rv rank 0, tilt +,
    bars stored under OCC form ZZZB — exercises P5); GGG = zero-tilt event
    (f rank 9, rv rank 1); JJJ = opening-heavy but NOT quiet (f rank 8, rv
    rank 10) — the ≥p80 member outside the event set that discriminates the
    realized-cut computation; AAA (tilt −) and BBB (tilt +) = baseline.
    """
    n_days = 30
    sessions = [date(2026, 1, 5) + timedelta(days=i) for i in range(n_days)]
    names = np.array(["AAA", "BBB", "CCC", "DDD", "EEE", "FFF", "GGG",
                      "HHH", "III", "JJJ", "ZZZ.B"])
    n = len(names)
    prem = np.full((n, n_days), 300_000.0)
    present = prem > 0
    tilt = np.full((n, n_days), 0.3)
    tilt[0, :] = -0.5                                  # AAA baseline, short
    tilt[6, :] = 0.0                                   # GGG zero tilt
    tilt[10, :] = 0.5                                  # ZZZ.B event, long
    ck = np.cumsum(np.c_[np.zeros(n), present.astype(float)], 1)
    presence_ok = np.zeros(present.shape, bool)
    for d in range(h1.LOOKBACK, n_days):
        presence_ok[:, d] = (ck[:, d] - ck[:, d - h1.LOOKBACK]) >= h1.MIN_PRESENT
    return Panel(sessions, names, {nm: i for i, nm in enumerate(names)},
                 prem, tilt, present, presence_ok)


@pytest.fixture
def _fixture_world(monkeypatch):
    panel = _fixture_panel()
    j_live = 25
    s_live = panel.sessions[j_live].isoformat()
    # calendar = the 30 sessions + 15 future dates; k0(live) = 25, last = 44
    cal = [s.isoformat() for s in panel.sessions] + [
        f"2026-03-{i:02d}" for i in range(1, 16)]
    spy_o = np.full(len(cal), 500.0)
    spy_c = np.full(len(cal), 500.0)
    #        AAA  BBB  CCC  DDD  EEE  FFF  GGG  HHH  III   JJJ  ZZZ.B
    f_arr = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.9, 0.7, 0.75, 0.85, 0.99]
    rv_live = np.array(
        [0.2, 0.25, 0.4, 0.45, 0.5, 0.55, 0.15, 0.6, 0.65, 0.9, 0.1])
    fo = {(s_live, nm): (2000.0, f_arr[i])
          for i, nm in enumerate(panel.names.tolist())}

    def fake_rv(p, idxs):
        return {j: (rv_live if j == j_live else np.full(len(panel.names), np.nan))
                for j in idxs}

    def fake_bars(symbols):
        out = {}
        for nm in ("AAA", "BBB", "CCC", "DDD", "EEE", "FFF", "GGG", "HHH",
                   "III", "JJJ", "ZZZB"):              # ZZZ.B under OCC form
            c10 = {"AAA": 101.0, "ZZZB": 102.0}.get(nm, 100.0)
            close = np.full(len(cal), 100.0)
            close[35] = c10                            # k0+10 for the live date
            out[nm] = (cal, np.full(len(cal), 100.0), np.full(len(cal), 101.0),
                       np.full(len(cal), 99.0), close)
        return out

    monkeypatch.setattr(h1, "rv_for_sessions", fake_rv)
    monkeypatch.setattr(h1, "spy_calendar",
                        lambda: (cal, spy_o, spy_c))
    monkeypatch.setattr(h1, "daily_bars", fake_bars)
    return panel, fo, s_live


def test_build_window_outcomes_end_to_end(_fixture_world):
    panel, fo, s_live = _fixture_world
    res = h1.build_window(panel, fo, "train", mode="outcomes")
    # one live date; the rest are empty-population or rv-NaN
    assert res.dates == [s_live]
    assert res.gaps.tolist() == [25]
    # event = ZZZ.B (+1 x alpha 1.0); baseline = AAA (-1 x 0.5), BBB (+1 x 0)
    assert res.diffs[0] == pytest.approx(1.0 - (-0.5 + 0.0) / 2)
    c = res.census
    assert c["events_gradeable"] == 1                  # ZZZ.B only (P11)
    assert c["event_dates_gradeable"] == 1
    assert c["event_zero_tilt"] == 1                   # GGG counted, excluded
    assert c["series_dates"] == 1
    row = next(r for r in c["per_date"] if r["date"] == s_live)
    assert row["events"] == 2 and row["baseline"] == 2
    # realized p80 cut = min f over the RANKED >=p80 set {JJJ, GGG, ZZZ.B}
    # = 0.85 — the event-set intersection would wrongly read 0.9
    assert row["f_open_cut_value"] == pytest.approx(0.85)
    assert row["rv_cut_value"] == pytest.approx(0.25)  # max rv among quiet
    assert res.arms["net_signed_share"][s_live] == pytest.approx(1.0)
    # magnitude secondary includes zero-tilt: mean(|1|,|0|) - mean(|0.5|,|0|)
    assert res.arms["magnitude"][0][2] == pytest.approx(0.25)
    # joint secondary: events vs fails-1-or-2 (AAA..GGG signed by own tilt)
    assert len(res.arms["joint"]) == 1
    # grid: 21/45 not elapsed at last_idx=44, 3/10 observed
    assert c["grid_not_elapsed"]["21"] > 0 and c["grid_not_elapsed"]["45"] > 0
    assert len(res.arms["grid_diffs"]["10"]) == 1
    # per-arm scale: ATR=2, entry=100, close_d=100
    assert res.arms["per_arm_scale"]["event"]["open_over_atr"] == pytest.approx(50.0)
    assert res.arms["per_arm_scale"]["event"]["atr_over_price"] == pytest.approx(0.02)


def test_build_window_census_reads_no_daily_bars(_fixture_world, monkeypatch):
    panel, fo, s_live = _fixture_world

    def boom(_):
        raise AssertionError("census mode must not read daily bars")
    monkeypatch.setattr(h1, "daily_bars", boom)
    res = h1.build_window(panel, fo, "train", mode="census")
    assert res.census["events_condition_total"] == 2
    assert res.dates == []                             # no G in census mode


def test_bars_for_sibling_fallback_p5(_fixture_world):
    panel, fo, s_live = _fixture_world
    res = h1.build_window(panel, fo, "train", mode="outcomes")
    # ZZZ.B's bars live only under ZZZB; without the P5 fallback it would be
    # case (ii) and the series would grade baseline-only (no event date)
    assert res.census["ungradeable"]["ii"] == 0
    assert res.dates == [s_live]


def test_window_indices_pins_37_judgment_dates():
    panel = _fixture_panel()                           # 30 January sessions
    with pytest.raises(SystemExit, match="registration pins 37"):
        h1._window_indices(panel, "judgment")


def test_rv_for_sessions_missing_file_blocks(tmp_path, monkeypatch):
    monkeypatch.setattr(h1, "BARS5_DIR", tmp_path)     # empty store
    panel = _fixture_panel()
    with pytest.raises(SystemExit, match="5-minute store missing"):
        h1.rv_for_sessions(panel, [25])


# ------------------------------ stage guards ------------------------------

def test_stage_ordering_and_first_run_governs(tmp_path, monkeypatch):
    monkeypatch.setattr(h1, "RECORD_PATH", tmp_path / "rec.json")
    monkeypatch.setattr(h1, "EARLIEST_RUN", date(2000, 1, 1))
    # calibrate before record refuses
    with pytest.raises(SystemExit, match="run --stage record first"):
        h1.stage_calibrate()
    # score without the G verdict artifact refuses
    with pytest.raises(SystemExit, match="verdict artifact not found"):
        h1.stage_score(str(tmp_path / "missing.json"))
    # an already-recorded stage refuses to re-run (first run governs)
    (tmp_path / "rec.json").write_text(json.dumps(
        {"prereg": "x", "stages": {"record": {}, "calibrate": {}}}))
    with pytest.raises(SystemExit, match="already recorded"):
        h1.stage_record()
    with pytest.raises(SystemExit, match="already recorded"):
        h1.stage_calibrate()
    # score with all stages present but a prior score refuses
    (tmp_path / "rec.json").write_text(json.dumps(
        {"prereg": "x",
         "stages": {"record": {}, "calibrate": {}, "score": {}}}))
    gv = tmp_path / "g.json"
    gv.write_text("{}")
    with pytest.raises(SystemExit, match="already ran"):
        h1.stage_score(str(gv))


def test_load_record_tolerates_missing_stages_key(tmp_path, monkeypatch):
    p = tmp_path / "rec.json"
    p.write_text(json.dumps({"prereg": "x"}))
    monkeypatch.setattr(h1, "RECORD_PATH", p)
    assert h1._load_record()["stages"] == {}
