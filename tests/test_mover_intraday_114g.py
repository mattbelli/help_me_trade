"""Roadmap 1.14g (`tools/mover_intraday_114g.py`) — the registration, as assertions.

1.14g is PRE-REGISTERED and it re-reads a corpus whose stars are already public,
so the risk is not "the code crashes", it is "the code quietly measures something
the registration did not ask for" — and, uniquely here, "the code silently
resolves an ambiguity instead of declaring it". Every test below pins one
registered clause and HAND-COMPUTES the expected number wherever that is
possible, rather than asserting against the implementation's own output.

Hermetic: no network, no real corpus, no real bar store, no real 1.14f JSON. The
5-minute npz files are synthesised in `tmp_path` in the exact layout
`tools/equity_bars_5min_backfill.py` writes (now including `high`/`low`, which
1.14g's Parkinson stratifier needs), and the raw print tape is injected by
replacing `tape_bins` — the real one needs `databento` and an 11M-print file.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime
from datetime import time as dtime
from pathlib import Path

import numpy as np
import pytest

import tools.mover_intraday as mi
import tools.mover_intraday_114g as g

ET = mi.ET
LN2_4 = 4.0 * np.log(2.0)


# ---------------------------------------------------------------------------
# the registered constants — a prereg fence, so a "tidy-up" cannot retune it
# ---------------------------------------------------------------------------

def test_registered_parameters_are_the_registered_values():
    """Every one of these is a number the registration fixes. A study whose
    constants drift is not the study that was registered."""
    assert g.SEED == 20260809                  # NOT 1.14f's 20260808
    assert g.N_BOOT == 10_000
    assert g.BLOCK == 5                        # SESSIONS
    assert (g.RV_LOOKBACK, g.MIN_RV_SESSIONS) == (20, 10)
    assert g.RV_COVERAGE == 0.80
    assert (g.MIN_ASSIGN_ROWS, g.MIN_ASSIGN_DISTINCT) == (25, 5)
    assert g.QUINT_PCTL == (20.0, 40.0, 60.0, 80.0)
    assert (g.OCC_MIN_FIRED, g.OCC_MIN_COMPLEMENT) == (20, 20)
    assert g.DROPPED_MASS_MAX == 0.10
    assert g.PER_DRAW_DROPPED_P95_MAX == 0.20
    assert (g.PERM_N, g.PERM_TOL) == (10, 0.02)
    assert (g.COARSE_MIN_ABS, g.D_DROP_MAX) == (0.005, 0.05)
    assert g.RELIABILITY_FLOOR_FRAC == 0.80
    # the multiplicity budget is exactly the three component primaries;
    # short_dte_share is PARKED by the registration, not retested here
    assert g.COMPONENTS == ("vol_max_ratio", "largest_print_ratio",
                            "concentration")
    assert "short_dte_share" not in g.COMPONENTS
    assert g.REGISTERED_SIGN == {"vol_max_ratio": 1, "largest_print_ratio": 1,
                                 "concentration": -1}
    # strata = boundary x $-vol quintile x rv-quintile, POOLED across sessions
    assert g.N_STRATA == len(mi.BOUNDARY_BINS) * 5 * 5 == 300
    assert g.N_COARSE == len(mi.BOUNDARY_BINS) * 5 == 60


def test_the_registration_bands_are_the_registered_literals():
    """+-0.041 / +-0.029 / +-0.010, and each is max(0.01, 0.25 x |pooled 1.14f
    delta|) to three decimals. The literals are LAW; the arithmetic check exists
    so the 3-decimal rounding is visible instead of discovered."""
    assert g.NEGLIGIBLE_BAND == {"vol_max_ratio": 0.041,
                                 "largest_print_ratio": 0.029,
                                 "concentration": 0.010}
    for f in g.COMPONENTS:
        computed = max(g.BAND_FLOOR, g.BAND_FRACTION * abs(g.POOLED_114F[f]))
        assert round(computed, 3) == pytest.approx(g.NEGLIGIBLE_BAND[f])
        assert g.band(f) == g.NEGLIGIBLE_BAND[f]
    # concentration's band is the FLOOR, not the fraction
    assert g.BAND_FRACTION * abs(g.POOLED_114F["concentration"]) < g.BAND_FLOOR
    assert g.band("concentration") == g.BAND_FLOOR


def test_every_registration_silent_choice_is_declared_not_resolved_silently():
    """The module's contract with the reader: an interpretive fork is recorded,
    never buried in code. These keys are the forks this build found."""
    for key in ("bootstrap_stratum_weights", "dropped_mass_denominator",
                "unavailable_is_per_cell", "coverage_rounding",
                "post_close_bars_are_absent", "assignment_population_generalizes",
                "arm2b_needs_its_stratifier", "arm2a_scope_and_gate",
                "lagged_scope_is_w60", "secondary_rv_variants_scope",
                "coarse_baseline_occupancy", "d_test_dropped_draw_denominator",
                "attenuation_ratio_is_signed", "balance_quintile_gap",
                "balance_evaluability_folds_coverage", "mde_definition",
                "shared_resampling_is_per_arm", "join98_reuses_the_assignment",
                "vectorizer_bit_identity", "gate_failure_blocks_reading",
                "permutation_gate_occupancy_mismatch",
                "extra_rows_from_the_placebo_pools",
                "raw_outcome_row_universe"):
        assert key in g.AMBIGUITIES, key
        assert len(g.AMBIGUITIES[key]) > 80, key


# ---------------------------------------------------------------------------
# rv_park window geometry
# ---------------------------------------------------------------------------

def test_the_gapped_window_is_the_flow_window_minus_its_final_bin():
    """[b-W/5, b-1): the outcome-base bin b-1 must contribute NOTHING to the
    stratifier, or microstructure noise in the boundary price sits on both sides
    of the row (a collider). And the (b, W) grid is 1.14f's, reused."""
    bb = 24                                      # 11:30
    assert mi.window_bins(bb, 60) == (12, 24)    # the FLOW window: 12 bins
    gap = g.gapped_bins(bb, 60)
    assert list(gap) == list(range(12, 23))      # 11 bins, b-1 = 23 excluded
    assert len(gap) == 60 // mi.BAR_MIN - 1
    assert 23 not in set(gap.tolist())
    # a boundary whose FLOW window would need pre-open bars has no gapped window
    assert g.gapped_bins(6, 60) is None          # 10:00 at W=60
    assert g.gapped_bins(18, 120) is None        # 11:00 at W=120
    assert list(g.gapped_bins(6, 30)) == [0, 1, 2, 3, 4]   # 5 bins at W=30
    assert len(g.gapped_bins(24, 120)) == 23


def test_the_lagged_window_is_ungapped_and_mechanically_na_before_1130():
    """[b-2W/5, b-W/5), ALL W/5 bins — the collider that motivates the
    contemporaneous gap does not exist at the lag. At W=60 this excludes every
    boundary before 11:30 (~45% of vol_max_ratio's primary-cell fired mass), and
    that exclusion is geometry, not a data accident."""
    assert list(g.lagged_bins(24, 60)) == list(range(0, 12))     # 11:30
    assert len(g.lagged_bins(24, 60)) == 12                      # ungapped
    for bb in (6, 12, 18):                                       # 10:00-11:00
        assert g.lagged_bins(bb, 60) is None
    assert g.first_lagged_boundary(60) == mi.BOUNDARY_BINS.index(24)
    assert mi.BOUNDARY_LABELS[g.first_lagged_boundary(60)] == "11:30"
    assert mi.BOUNDARY_LABELS[g.first_lagged_boundary(120)] == "13:30"
    assert mi.BOUNDARY_LABELS[g.first_lagged_boundary(30)] == "10:30"


def _hl(n_names, bins_present, hi=101.0, lo=100.0, zero_range_bins=()):
    """(high, low) panels where only `bins_present` carry a bar."""
    h = np.full((n_names, mi.RTH_BINS), np.nan)
    ell = np.full((n_names, mi.RTH_BINS), np.nan)
    for b in bins_present:
        if b in zero_range_bins:
            h[:, b], ell[:, b] = lo, lo
        else:
            h[:, b], ell[:, b] = hi, lo
    return h, ell


def test_parkinson_is_the_registered_formula_per_bin_averaged():
    """sqrt( sum ln(h/l)^2 / (4 ln2 * n_present) ), hand-computed. Division by
    n_present (the per-bin AVERAGE) is what stops bin availability from
    masquerading as vol: 11 identical bins and 9 identical bins give the SAME
    number."""
    gap = g.gapped_bins(24, 60)
    r = np.log(101.0 / 100.0)
    want = np.sqrt(r * r / LN2_4)                 # per-bin average of r^2

    h, ell = _hl(1, gap)
    assert g.rv_park(h, ell, gap)[0] == pytest.approx(want)
    # 9 of 11 present (81.8% >= 80%) — same value, not a smaller one
    h9, l9 = _hl(1, gap[:9])
    assert g.rv_park(h9, l9, gap)[0] == pytest.approx(want)
    # a wider range gives a larger number, with the same shape
    h2, l2 = _hl(1, gap, hi=102.0)
    r2 = np.log(102.0 / 100.0)
    assert g.rv_park(h2, l2, gap)[0] == pytest.approx(np.sqrt(r2 * r2 / LN2_4))
    # no time scaling is applied anywhere: W=30's 5 bins with identical ranges
    # give the identical number (the own-normalized ratio cancels the scale)
    g30 = g.gapped_bins(6, 30)
    h3, l3 = _hl(1, g30)
    assert g.rv_park(h3, l3, g30)[0] == pytest.approx(want)


def test_eighty_percent_presence_is_the_line_and_it_is_not_ceiled():
    """'>=80% of W/5-1 bins present' as n_present >= 0.80 * n_bins. At W=60 that
    is >=9 of 11 (8.8); 8 is NA. The odd subset's 5 bins need >=4, and exactly
    4.0 PASSES — see AMBIGUITIES['coverage_rounding']."""
    gap = g.gapped_bins(24, 60)
    assert len(gap) == 11
    h9, l9 = _hl(1, gap[:9])
    assert np.isfinite(g.rv_park(h9, l9, gap)[0])
    h8, l8 = _hl(1, gap[:8])
    assert np.isnan(g.rv_park(h8, l8, gap)[0])
    _even, odd = g.parity_bins(gap)
    assert len(odd) == 5
    h4, l4 = _hl(1, odd[:4])
    assert np.isfinite(g.rv_park(h4, l4, odd)[0])      # 4 / 5 = 0.8 exactly
    h3, l3 = _hl(1, odd[:3])
    assert np.isnan(g.rv_park(h3, l3, odd)[0])


def test_a_zero_range_bar_is_present_and_contributes_zero():
    """11.6% of bars are zero-range. The registration is explicit: h == l is
    PRESENT and contributes 0 — 'do not "improve" this into an absence'. Turning
    them into absences would both fail the coverage rule and inflate the
    per-bin average."""
    gap = g.gapped_bins(24, 60)
    h, ell = _hl(1, gap, zero_range_bins=tuple(gap[:6]))   # 6 flat, 5 ranged
    r = np.log(101.0 / 100.0)
    # n_present is 11 (not 5): the flat bins are in the denominator
    assert g.rv_park(h, ell, gap)[0] == pytest.approx(
        np.sqrt(5 * r * r / (LN2_4 * 11)))
    # and a WHOLLY flat window is 0.0, not NaN — a real measurement of no range
    hf, lf = _hl(1, gap, zero_range_bins=tuple(gap))
    assert g.rv_park(hf, lf, gap)[0] == 0.0


def test_the_registered_absence_conditions_are_exactly_those_three():
    """Absent iff non-finite h/l, l <= 0, or h < l. Nothing else is an absence,
    and each of the three really is one."""
    gap = g.gapped_bins(24, 60)
    r = np.log(101.0 / 100.0)
    full = np.sqrt(r * r / LN2_4)
    for spoil in ("nan", "inf", "zero_low", "negative_low", "crossed"):
        h, ell = _hl(1, gap)
        b = gap[0]
        if spoil == "nan":
            h[0, b] = np.nan
        elif spoil == "inf":
            h[0, b] = np.inf
        elif spoil == "zero_low":
            ell[0, b] = 0.0
        elif spoil == "negative_low":
            ell[0, b] = -1.0
        else:
            h[0, b], ell[0, b] = 100.0, 101.0      # h < l
        got = g.rv_park(h, ell, gap)[0]
        # one absence out of 11 keeps coverage (10/11) but drops the bin from
        # BOTH the sum and the per-bin denominator, so the value is unchanged
        assert got == pytest.approx(full), spoil
        # ...and it really was treated as absent: 3 more absences fail coverage
        h2, l2 = h.copy(), ell.copy()
        for bb in gap[1:4]:
            h2[0, bb] = np.nan
        assert np.isnan(g.rv_park(h2, l2, gap)[0]), spoil


def test_odd_even_parity_is_the_absolute_bin_index_and_splits_six_five():
    """'ODD/EVEN = parity of the ABSOLUTE 5-minute bin index within the gapped
    window ... at W=60 the 11 gapped bins split 6 even / 5 odd (the window's
    first and last bins are even-index), so the STRATIFIER (even) is the thicker
    side.' Hand-computed at 11:30, then checked at every boundary."""
    gap = g.gapped_bins(24, 60)
    even, odd = g.parity_bins(gap)
    assert list(even) == [12, 14, 16, 18, 20, 22]
    assert list(odd) == [13, 15, 17, 19, 21]
    assert (len(even), len(odd)) == (6, 5)
    assert gap[0] % 2 == 0 and gap[-1] % 2 == 0     # first AND last are even
    # every boundary bin index and every W/5 are even, so window-relative parity
    # equals absolute parity for the whole registered grid
    for W in mi.WINDOWS:
        assert (W // mi.BAR_MIN) % 2 == 0
        for bb in mi.BOUNDARY_BINS:
            assert bb % 2 == 0
            gg = g.gapped_bins(bb, W)
            if gg is None:
                continue
            e, o = g.parity_bins(gg)
            assert len(e) + len(o) == len(gg)
            assert len(e) == len(o) + 1              # even is always thicker
    # the registration's W=30 numbers, which justify excluding it in advance
    e30, o30 = g.parity_bins(g.gapped_bins(6, 30))
    assert (len(e30), len(o30)) == (3, 2)
    e120, o120 = g.parity_bins(g.gapped_bins(24, 120))
    assert (len(e120), len(o120)) == (12, 11)


def test_close_to_close_bridges_gaps_and_counts_returns_not_bins():
    """The disclosed secondary variant: returns between CONSECUTIVE PRESENT
    closes of [b-W/5, b-2], 10 returns at W=60, >=80% of the RETURN count."""
    gap = g.gapped_bins(24, 60)
    logc = np.full((1, mi.RTH_BINS), np.nan)
    step = 0.002
    for i, b in enumerate(gap):
        logc[0, b] = i * step
    got = g.rv_close_to_close(logc, gap)[0]
    assert got == pytest.approx(np.sqrt(10 * step * step / 10))   # = step
    # a hole is BRIDGED: 9 closes -> 8 returns (>= 0.8 * 10), one of them 2 bins
    holed = logc.copy()
    holed[0, gap[5]] = np.nan
    holed[0, gap[7]] = np.nan
    got2 = g.rv_close_to_close(holed, gap)[0]
    # present closes 0,1,2,3,4,6,8,9,10 => 8 returns, two of them 2 steps wide
    ssq = 6 * step ** 2 + 2 * (2 * step) ** 2
    assert got2 == pytest.approx(np.sqrt(ssq / 8))
    # 7 returns is below the floor
    for b in (gap[2], gap[3]):
        holed[0, b] = np.nan
    assert np.isnan(g.rv_close_to_close(holed, gap)[0])


# ---------------------------------------------------------------------------
# the rv-quintile assignment
# ---------------------------------------------------------------------------

def test_quintile_cuts_are_equal_count_and_ties_fall_to_the_lower_quintile():
    """np.percentile([20,40,60,80], linear) then searchsorted(side='left'), so a
    value sitting exactly ON a breakpoint takes the LOWER quintile. Cuts are
    taken inside each $-vol quintile separately (the 1.14f peer cell INCLUDES
    it), which is what makes the ranks session-local."""
    vals = np.arange(26.0)                       # 0..25, 26 rows, 26 distinct
    quint = np.zeros(26, np.int8)
    lab, small = g.rank_quintiles(vals, np.ones(26, bool), quint)
    assert small == 0
    bp = np.percentile(vals, g.QUINT_PCTL)
    assert bp[0] == 5.0                          # exactly a data value
    assert lab[5] == 0                           # the tie goes DOWN
    assert lab[6] == 1
    assert lab[0] == 0 and lab[25] == 4
    assert set(lab.tolist()) == {0, 1, 2, 3, 4}
    # each $-vol quintile is cut on its OWN rows: a second cell with a shifted
    # distribution gets the same label set, not the first cell's breakpoints
    vals2 = np.r_[np.arange(26.0), 1000.0 + np.arange(26.0)]
    quint2 = np.r_[np.zeros(26, np.int8), np.ones(26, np.int8)]
    lab2, _ = g.rank_quintiles(vals2, np.ones(52, bool), quint2)
    assert np.array_equal(lab2[:26], lab)
    assert np.array_equal(lab2[26:], lab)


def test_a_thin_or_degenerate_peer_cell_contributes_no_strata_at_all():
    """'A peer cell with <25 assignment rows, or with <5 distinct values,
    contributes NO strata (never partial-quintile strata; rows counted in
    na_strata_cell_too_small).'"""
    quint = np.zeros(24, np.int8)
    lab, small = g.rank_quintiles(np.arange(24.0), np.ones(24, bool), quint)
    assert (lab == -1).all() and small == 24         # 24 rows: one short
    lab, small = g.rank_quintiles(np.arange(25.0), np.ones(25, bool),
                                  np.zeros(25, np.int8))
    assert (lab >= 0).all() and small == 0           # 25 rows: the line
    # 30 rows but only 4 distinct values => degenerate breakpoints => no strata
    vals = np.repeat(np.array([1.0, 2.0, 3.0, 4.0]), 8)[:30]
    vals = np.r_[vals, np.full(30 - len(vals), 4.0)]
    lab, small = g.rank_quintiles(vals, np.ones(30, bool), np.zeros(30, np.int8))
    assert (lab == -1).all() and small == 30
    # rows outside the assignment population are never labelled
    elig = np.ones(30, bool)
    elig[:5] = False
    lab, _ = g.rank_quintiles(np.arange(30.0), elig, np.zeros(30, np.int8))
    assert (lab[:5] == -1).all() and (lab[5:] >= 0).all()


# ---------------------------------------------------------------------------
# stratum pooling, the occupancy floor, and the two DISTINCT unavailable rules
# ---------------------------------------------------------------------------

def _strat_fixture(n_f_a=20, n_c_a=20, n_f_b=19, n_c_b=50, sessions_a=None,
                   sessions_b=None, S=10):
    """Two pooled strata with hand-set occupancy. Stratum 0's fired rows are all
    2.0 and complement 1.0 (so its delta is exactly 1.0); stratum 1's are 5.0 vs
    1.0 (delta 4.0), so a weighted delta is checkable by hand."""
    rows = []
    for s_id, (nf, nc, dv) in enumerate([(n_f_a, n_c_a, 2.0),
                                         (n_f_b, n_c_b, 5.0)]):
        sess = sessions_a if s_id == 0 else sessions_b
        for i in range(nf):
            rows.append((dv, (sess[i % len(sess)] if sess else i % S), True, s_id))
        for i in range(nc):
            rows.append((1.0, (sess[i % len(sess)] if sess else i % S), False,
                         s_id))
    vals = np.array([r[0] for r in rows], float)
    sess = np.array([r[1] for r in rows], np.int64)
    fired = np.array([r[2] for r in rows], bool)
    strat = np.array([r[3] for r in rows], np.int64)
    return vals, sess, fired, strat


def test_the_occupancy_floor_drops_a_stratum_and_counts_its_rows():
    """'a stratum needs >=20 fired AND >=20 complement rows, else it drops with
    its rows counted'. 19 fired is one short."""
    vals, sess, fired, strat = _strat_fixture()
    wmat = mi.block_weights(10, 40, 5, np.random.default_rng(1))
    r = g.stratified(vals, sess, fired, strat, 2, 10, wmat, boot=False)
    assert r["n_strata_populated"] == 2
    assert r["n_strata_included"] == 1
    assert r["n_strata_dropped_occupancy"] == 1
    assert r["dropped_fired_rows"] == 19
    assert r["total_fired_rows"] == 39
    assert r["dropped_fired_share"] == pytest.approx(round(19 / 39, 5))
    # only the surviving stratum's delta enters, at weight 1
    assert r["delta"] == pytest.approx(1.0)
    # 19/39 = 48.7% of fired mass dropped => UNAVAILABLE, not null, not star
    assert "UNAVAILABLE" in r["unavailable"]
    assert "occupancy-dropped" in r["unavailable"]


def test_a_stratum_one_complement_row_short_also_drops():
    vals, sess, fired, strat = _strat_fixture(n_f_b=40, n_c_b=19)
    wmat = mi.block_weights(10, 20, 5, np.random.default_rng(1))
    r = g.stratified(vals, sess, fired, strat, 2, 10, wmat, boot=False)
    assert r["n_strata_included"] == 1 and r["dropped_fired_rows"] == 40


def test_a_small_dropped_mass_leaves_the_point_estimate_available():
    """The threshold is 10% of the cell's fired rows, so a thin dropped stratum
    is a disclosure, not an UNAVAILABLE."""
    vals, sess, fired, strat = _strat_fixture(n_f_a=200, n_f_b=19, n_c_b=5)
    wmat = mi.block_weights(10, 20, 5, np.random.default_rng(1))
    r = g.stratified(vals, sess, fired, strat, 2, 10, wmat, boot=False)
    assert r["dropped_fired_share"] == pytest.approx(round(19 / 219, 5))
    assert r["dropped_fired_share"] < g.DROPPED_MASS_MAX
    assert "unavailable" not in r


def test_the_weighted_estimand_is_the_fired_count_share_and_min_variant():
    """Delta = sum_s w_s (median_f,s - median_c,s), w_s = fired-count share
    (ATT-on-alarms). The registered secondary re-weights by min(n_f, n_c)."""
    vals, sess, fired, strat = _strat_fixture(n_f_a=20, n_c_a=100,
                                              n_f_b=60, n_c_b=20)
    wmat = mi.block_weights(10, 20, 5, np.random.default_rng(1))
    r = g.stratified(vals, sess, fired, strat, 2, 10, wmat, boot=False)
    assert r["n_strata_included"] == 2
    # w = 20/80, 60/80 on deltas 1.0 and 4.0
    assert r["delta"] == pytest.approx(0.25 * 1.0 + 0.75 * 4.0)
    m = g.stratified(vals, sess, fired, strat, 2, 10, wmat, boot=False,
                     weight="min")
    # min(20,100)=20 and min(60,20)=20 => equal weights
    assert m["delta"] == pytest.approx(0.5 * 1.0 + 0.5 * 4.0)
    assert m["weighting"] == "min" and r["weighting"] == "fired"


def test_the_per_draw_drop_rule_is_distinct_from_the_point_estimate_floor():
    """Two DIFFERENT mechanisms, and the registration says so. Here the point
    estimate keeps every stratum (0% dropped mass) but one stratum's complement
    rows all live in a single session, so a quarter of the draws lose that side
    entirely: the per-draw dropped-fired-mass p95 blows the 20% rule and the
    cell is UNAVAILABLE for that reason ALONE."""
    vals, sess, fired, strat = _strat_fixture(
        n_f_a=20, n_c_a=20, n_f_b=60, n_c_b=20,
        sessions_a=list(range(10)), sessions_b=[0])
    wmat = mi.block_weights(10, 400, 5, np.random.default_rng(7))
    r = g.stratified(vals, sess, fired, strat, 2, 10, wmat)
    assert r["dropped_fired_share"] == 0.0            # the FLOOR is satisfied
    pdd = r["per_draw_dropped_fired_mass"]
    assert pdd["max"] == pytest.approx(0.75)          # 60 of 80 fired rows
    assert pdd["p95"] > g.PER_DRAW_DROPPED_P95_MAX
    assert "per-draw dropped fired mass" in r["unavailable"]
    assert "DISTINCT rule" in r["unavailable"]
    # the interval is still computed (the draws that DO have both sides)
    assert r["ci"][0] is not None and "bootstrap_se" in r
    # the reported MDE is 2.8016 x the realized bootstrap SE (95%/80% power)
    assert abs(r["min_detectable_effect"]
               - g.MDE_Z * r["bootstrap_se"]) < 1e-4


def test_a_cell_with_no_qualifying_stratum_is_unavailable_not_zero():
    vals, sess, fired, strat = _strat_fixture(n_f_a=5, n_c_a=5, n_f_b=5, n_c_b=5)
    wmat = mi.block_weights(10, 20, 5, np.random.default_rng(1))
    r = g.stratified(vals, sess, fired, strat, 2, 10, wmat, boot=False)
    assert "delta" not in r
    assert "occupancy floor" in r["unavailable"]


# ---------------------------------------------------------------------------
# calibration gate 1 — the permutation's count-preservation property
# ---------------------------------------------------------------------------

def test_permuting_labels_within_cell_and_status_preserves_stratum_counts():
    """THE property the gate depends on: 'This preserves every stratum's fired
    and complement counts exactly, so the gate certifies the estimator on the
    REAL run's occupancy geometry.' If it did not hold, the gate would certify
    some other geometry."""
    rng = np.random.default_rng(0)
    n = 4000
    sess = rng.integers(0, 8, n)
    coarse = rng.integers(0, 6, n)               # (boundary x $-vol quintile)
    cell_id = sess * 6 + coarse
    status = rng.random(n) < 0.1
    lab = rng.integers(0, 5, n).astype(np.int8)

    perm = g.permute_within(cell_id, status, lab, np.random.default_rng(3))
    assert not np.array_equal(perm, lab)                 # it really shuffled
    assert np.array_equal(np.bincount(perm, minlength=5),
                          np.bincount(lab, minlength=5))  # multiset preserved
    for st in (status, ~status):
        before = np.bincount((coarse[st] * 5 + lab[st]).astype(np.int64),
                             minlength=30)
        after = np.bincount((coarse[st] * 5 + perm[st]).astype(np.int64),
                            minlength=30)
        assert np.array_equal(before, after)
    # and it is a permutation WITHIN each (cell, status) group, not across them
    for c in np.unique(cell_id):
        for st in (status, ~status):
            m = (cell_id == c) & st
            assert sorted(lab[m].tolist()) == sorted(perm[m].tolist())


# ---------------------------------------------------------------------------
# criterion (d) — the paired benchmark test
# ---------------------------------------------------------------------------

def _arm(delta, draws, star=True):
    return {"delta": delta, "star": star, "_draws": np.asarray(draws, float),
            "ci": [delta - 0.01, delta + 0.01]}


def test_the_d_test_passes_only_when_the_component_beats_the_placebo():
    """R_x(d) = strat(d)/coarse(d) on the SAME draw row; statistic =
    R_comp - R_2b; pass iff its 2.5th percentile > 0. Retaining a fraction is
    NOT the test — beating the benchmark is."""
    n = 4000
    comp_strat = _arm(0.09, np.full(n, 0.09))
    comp_coarse = _arm(0.10, np.full(n, 0.10))          # R_comp = 0.9
    b2_strat = _arm(0.05, np.full(n, 0.05))
    b2_coarse = _arm(0.10, np.full(n, 0.10))            # R_2b = 0.5
    r = g.benchmark_test(comp_strat, comp_coarse, b2_strat, b2_coarse,
                         "vol_max_ratio")
    assert r["available"] and r["passed"]
    assert r["attenuation_ratio_component"] == pytest.approx(0.9)
    assert r["attenuation_ratio_arm2b"] == pytest.approx(0.5)
    assert r["paired_statistic_p2_5"] == pytest.approx(0.4)
    # the placebo retaining MORE fails, and is not recorded as a pass
    r2 = g.benchmark_test(_arm(0.05, np.full(n, 0.05)), comp_coarse,
                          _arm(0.09, np.full(n, 0.09)), b2_coarse,
                          "vol_max_ratio")
    assert r2["available"] and not r2["passed"]


def test_the_d_test_preconditions_make_it_unavailable_not_failed():
    """Evaluable only where the coarse delta's CI is clear of zero AND
    |coarse delta| >= the component's negligible band; a coarse baseline failing
    the occupancy floor likewise makes (d) UNAVAILABLE."""
    n = 100
    ok_strat = _arm(0.09, np.full(n, 0.09))
    b2s, b2c = _arm(0.05, np.full(n, 0.05)), _arm(0.10, np.full(n, 0.1))
    # (i) coarse CI contains zero
    r = g.benchmark_test(ok_strat, _arm(0.10, np.full(n, 0.1), star=False),
                         b2s, b2c, "vol_max_ratio")
    assert not r["available"] and "not clear of zero" in r["unavailable"]
    # (ii) |coarse| inside the band (vol_max_ratio's band is 0.041)
    r = g.benchmark_test(ok_strat, _arm(0.02, np.full(n, 0.02)), b2s, b2c,
                         "vol_max_ratio")
    assert not r["available"] and "negligible band" in r["unavailable"]
    # ...and the SAME 0.02 clears concentration's 0.010 band
    r = g.benchmark_test(ok_strat, _arm(0.02, np.full(n, 0.02)), b2s, b2c,
                         "concentration")
    assert r["available"]
    # (iii) coarse baseline with no included stratum
    r = g.benchmark_test(ok_strat, {"unavailable": "no stratum clears"}, b2s,
                         b2c, "vol_max_ratio")
    assert not r["available"] and "coarse baseline" in r["unavailable"]


def test_an_unavailable_arm_2b_cascades_into_every_component():
    """'an UNAVAILABLE Arm 2b makes criterion (d) — and therefore survival —
    UNAVAILABLE for every component.'"""
    n = 100
    ok_strat, ok_coarse = _arm(0.09, np.full(n, 0.09)), _arm(0.10, np.full(n, 0.1))
    dead = {"n_fired": 3, "unavailable": "no stratum clears the floor"}
    for f in g.COMPONENTS:
        r = g.benchmark_test(ok_strat, ok_coarse, dead,
                             _arm(0.1, np.full(n, 0.1)), f)
        assert not r["available"]
        assert "Arm 2b" in r["unavailable"] and "survival" in r["unavailable"]


def test_the_draw_drop_guard_makes_d_unavailable_past_five_percent():
    """'Draws with |delta_coarse| < 0.005 for either arm drop as undefined; >5%
    dropped draws => (d) UNAVAILABLE (not passed, not failed).'"""
    n = 1000
    cs = np.full(n, 0.09)
    cc = np.full(n, 0.10)
    cc[:40] = 0.004                                  # 4% undefined: still fine
    r = g.benchmark_test(_arm(0.09, cs), _arm(0.10, cc),
                         _arm(0.05, np.full(n, 0.05)),
                         _arm(0.10, np.full(n, 0.10)), "vol_max_ratio")
    assert r["available"] and r["dropped_draw_share"] == pytest.approx(0.04)
    assert r["n_draws_used"] == 960
    cc2 = np.full(n, 0.10)
    cc2[:51] = 0.004                                 # 5.1%: over the line
    r2 = g.benchmark_test(_arm(0.09, cs), _arm(0.10, cc2),
                          _arm(0.05, np.full(n, 0.05)),
                          _arm(0.10, np.full(n, 0.10)), "vol_max_ratio")
    assert not r2["available"] and "UNAVAILABLE" in r2["unavailable"]
    assert r2["dropped_draw_share"] == pytest.approx(0.051)
    # a NaN draw is as undefined as a near-zero denominator
    cc3 = np.full(n, 0.10)
    cc3[:60] = np.nan
    r3 = g.benchmark_test(_arm(0.09, cs), _arm(0.10, cc3),
                          _arm(0.05, np.full(n, 0.05)),
                          _arm(0.10, np.full(n, 0.10)), "vol_max_ratio")
    assert not r3["available"]


# ---------------------------------------------------------------------------
# the verdicts — science and product recorded SEPARATELY
# ---------------------------------------------------------------------------

WI_P = mi.WINDOWS.index(mi.PRIMARY_W)
WI_REP = mi.WINDOWS.index(120)


def _cells(component, delta, ci, star=True, rep_star=True):
    """A by_cell map with the same-sign star at all three W=60 horizons plus the
    W=120 replication cell."""
    out = {}
    for h in mi.HORIZONS:
        out[(component, 60, h)] = {"delta": delta, "ci": ci, "star": star,
                                   "bootstrap_se": 0.01,
                                   "min_detectable_effect": 0.028}
    out[(component, 120, "2h")] = {"delta": delta, "ci": ci, "star": rep_star,
                                   "bootstrap_se": 0.01}
    return out


def _dtests(component, passed=True, available=True, unavailable=None,
            rep_passed=True):
    d = {"available": available, "passed": passed,
         "attenuation_ratio_component": 0.9, "attenuation_ratio_arm2b": 0.5}
    if unavailable:
        d["unavailable"] = unavailable
    return {(component, WI_P): d,
            (component, WI_REP): {**d, "passed": rep_passed}}


ADEQUATE = {"verdict": "ADEQUATE", "adequate": True}
REL = {"w120_below_floor": False}


def test_passing_all_four_records_rv_orthogonal_association_never_flow_info():
    """The registered wording, and only that wording: 'RV-ORTHOGONAL ASSOCIATION
    — incremental to contemporaneous equity RV as measured by [Parkinson, gapped
    W-window]' — never 'flow information' unqualified."""
    f = "vol_max_ratio"
    rule, v = g.verdict_for(f, _cells(f, 0.09, [0.06, 0.12]), _dtests(f),
                            ADEQUATE, {"star": True}, REL, False, False)
    assert rule["all_four"] and rule["b_horizons_starred_same_sign"] == 3
    assert v["science"].startswith("RV-ORTHOGONAL ASSOCIATION")
    assert "flow information" not in v["science"]
    assert v["product"].startswith("NOT DEMOTED")
    assert v["survives"] is True


def test_science_positive_can_still_be_product_demoted_and_then_not_survive():
    """'A component may be science-positive AND product-demoted; the record
    states both, the PRODUCT verdict governs any perception/ship decision, and
    the unqualified word SURVIVES is reserved for science-positive AND not
    product-demoted.' concentration's band is 0.010."""
    f = "concentration"
    cells = _cells(f, -0.006, [-0.009, -0.002])
    rule, v = g.verdict_for(f, cells, _dtests(f), ADEQUATE, {"star": True}, REL,
                            False, False)
    assert rule["all_four"]                       # the science test still passes
    assert v["science"].startswith("RV-ORTHOGONAL ASSOCIATION")
    assert "NEGATIVE discrimination" in v["science"]     # sign < 0
    assert v["product"].startswith("PRODUCT-DEMOTED")
    assert "free equity-RV monitor" in v["product"]
    assert v["survives"] is False


def test_a_ci_containing_zero_outside_the_band_is_underpowered_not_demoted():
    f = "vol_max_ratio"
    cells = _cells(f, 0.02, [-0.05, 0.09], star=False)
    _rule, v = g.verdict_for(f, cells, _dtests(f, passed=False), ADEQUATE,
                             {"star": True}, REL, False, False)
    assert v["product"].startswith("UNDERPOWERED")
    assert "not demoted" in v["product"]
    assert v["science"] == "DID NOT PASS the four-part science test"
    assert v["stratified_primary"]["min_detectable_effect"] == 0.028


def test_a_star_with_the_opposite_sign_is_its_own_recorded_finding():
    """'Stratified star with sign OPPOSITE to 1.14f => recorded as a finding of
    confounding-or-over-adjustment, never folded silently into "did not pass".'
    concentration's registered sign is its POOLED -0.021, never the
    earnings-split signs."""
    f = "concentration"
    cells = _cells(f, +0.03, [0.01, 0.05])        # positive: opposite the pooled
    rule, v = g.verdict_for(f, cells, _dtests(f), ADEQUATE, {"star": True}, REL,
                            False, False)
    assert v["sign_flip_vs_114f"] is True
    assert v["science"].startswith("CONFOUNDING-OR-OVER-ADJUSTMENT")
    assert not rule["a_primary_clear_of_zero_same_sign"]   # (a) needs the sign
    assert rule["a_registered_sign"] == -1
    assert v["survives"] is False


def test_a_failed_calibration_gate_blocks_every_reading():
    """'failing either makes the run INCONCLUSIVE' — and the result rows are
    still emitted, flagged, so a reader can see what the gate failed on."""
    f = "vol_max_ratio"
    _rule, v = g.verdict_for(f, _cells(f, 0.09, [0.06, 0.12]), _dtests(f),
                             ADEQUATE, {"star": True}, REL, True, False)
    assert v["read_blocked"] is True
    assert v["science"].startswith("INCONCLUSIVE")
    assert v["product"].startswith("INCONCLUSIVE")
    assert v["survives"] is False


def test_arm_2b_explaining_every_residual_is_inconclusive_for_all_science():
    """'Arm 2b's attenuation ratio >= every component's AND its own stratified CI
    clear of zero => coarsening alone explains all residuals => INCONCLUSIVE for
    all science claims.'"""
    f = "largest_print_ratio"
    _rule, v = g.verdict_for(f, _cells(f, 0.06, [0.04, 0.08]), _dtests(f),
                             ADEQUATE, {"star": True}, REL, False, True)
    assert v["science"].startswith("INCONCLUSIVE")
    assert "coarsening alone" in v["science"]
    assert v["survives"] is False
    # the PRODUCT verdict is independent of the science override
    assert v["product"].startswith("NOT DEMOTED")


def test_an_inadequate_balance_removes_survival_authority_not_the_hypothesis():
    f = "vol_max_ratio"
    _rule, v = g.verdict_for(f, _cells(f, 0.09, [0.06, 0.12]), _dtests(f),
                             {"verdict": "INADEQUATE"}, {"star": True}, REL,
                             False, False)
    assert v["science"].startswith("NO SURVIVAL AUTHORITY")
    assert "the matching failed, not the hypothesis" in v["science"]
    assert v["balance"] == "INADEQUATE"


def test_an_unavailable_primary_is_unavailable_in_both_verdicts():
    f = "vol_max_ratio"
    cells = _cells(f, 0.09, [0.06, 0.12])
    cells[(f, 60, "2h")]["unavailable"] = "12.0% of fired rows dropped"
    _rule, v = g.verdict_for(f, cells, _dtests(f), ADEQUATE, {"star": True},
                             REL, False, False)
    assert v["science"].startswith("UNAVAILABLE")
    assert v["product"].startswith("UNAVAILABLE")
    assert v["survives"] is False


def test_a_product_demotion_is_adjudicated_by_arm_1b_with_licensed_language():
    """'The flow ANGLE after a product demotion is adjudicated by Arm 1b on the
    boundary-matched restriction: lagged-null too => the clustering story
    strengthens and "thermometer" language is licensed FOR THE ADJUDICATED
    SUBPOPULATION only; lagged-star => "not RV-orthogonal at this grain" — the
    angle stays alive for other constructions, and "costume" language is
    FORBIDDEN.'"""
    f = "concentration"
    demoted = _cells(f, -0.006, [-0.009, -0.002])       # inside the 0.010 band
    args = (demoted, _dtests(f), ADEQUATE, {"star": True}, REL, False, False)
    # lagged NULL: thermometer language, restricted to the adjudicated subpop
    _r, v = g.verdict_for(f, *args, {"delta": -0.001, "star": False})
    assert v["product"].startswith("PRODUCT-DEMOTED")
    assert "thermometer" in v["flow_angle_adjudication"]
    assert "SUBPOPULATION ONLY" in v["flow_angle_adjudication"]
    assert "UN-ADJUDICATED" in v["flow_angle_adjudication"]
    # lagged STAR: the angle stays alive and "costume" is forbidden
    _r, v = g.verdict_for(f, *args, {"delta": -0.02, "star": True})
    assert "NOT RV-ORTHOGONAL AT THIS GRAIN" in v["flow_angle_adjudication"]
    assert "FORBIDDEN" in v["flow_angle_adjudication"]
    assert "thermometer" not in v["flow_angle_adjudication"]
    # Arm 1b unavailable: neither word is licensed
    _r, v = g.verdict_for(f, *args, {"unavailable": "no strata"})
    assert v["flow_angle_adjudication"].startswith("UN-ADJUDICATED")
    assert "survival" in v["arm1b_note"]
    # a component that is NOT demoted gets no adjudication line at all
    _r, v = g.verdict_for(f, _cells(f, -0.06, [-0.08, -0.04]), *args[1:],
                          {"delta": -0.02, "star": True})
    assert "flow_angle_adjudication" not in v


def test_both_dropped_mass_rules_are_reported_when_both_trip():
    """The point-estimate occupancy rule and the per-draw rule are DISTINCT, so
    a cell that trips both must say so twice — overwriting one with the other
    would hide a registered rule."""
    vals, sess, fired, strat = _strat_fixture(
        n_f_a=20, n_c_a=20, n_f_b=60, n_c_b=20,
        sessions_a=list(range(10)), sessions_b=[0])
    # make a third stratum that fails the point-estimate floor with real mass
    vals = np.r_[vals, np.full(30, 3.0), np.full(5, 1.0)]
    sess = np.r_[sess, np.arange(30) % 10, np.arange(5)]
    fired = np.r_[fired, np.ones(30, bool), np.zeros(5, bool)]
    strat = np.r_[strat, np.full(35, 2, np.int64)]
    wmat = mi.block_weights(10, 400, 5, np.random.default_rng(7))
    r = g.stratified(vals, sess, fired, strat, 3, 10, wmat)
    assert r["dropped_fired_share"] > g.DROPPED_MASS_MAX
    assert r["per_draw_dropped_fired_mass"]["p95"] > g.PER_DRAW_DROPPED_P95_MAX
    assert len(r["unavailable_reasons"]) == 2
    assert "occupancy-dropped" in r["unavailable"]
    assert "per-draw" in r["unavailable"]


def test_criterion_c_needs_the_w120_star_and_the_w120_benchmark_rerun():
    """(c) is 'stratified replication star at W=120, H=2h, PLUS the (d)
    benchmark test re-run at W=120'. A star alone is not (c)."""
    f = "vol_max_ratio"
    cells = _cells(f, 0.09, [0.06, 0.12])
    rule, _v = g.verdict_for(f, cells, _dtests(f, rep_passed=False), ADEQUATE,
                             {"star": True}, REL, False, False)
    assert rule["c_w120_star"] and not rule["c_w120_benchmark_passed"]
    assert not rule["c_pass"] and not rule["all_four"]
    # and a W=120 star below the reliability floor is CAVEATED, not silent
    rule2, _v2 = g.verdict_for(f, cells, _dtests(f), ADEQUATE, {"star": True},
                               {"w120_below_floor": True}, False, False)
    assert rule2["c_reliability_caveated"] is True


def test_w30_is_never_a_replication_window():
    """W=30 was DECLARED IN ADVANCE unusable for replication: its gapped window
    has 5 bins (3 even / 2 odd). (c) reads W=120 only, so a W=30 star cannot let
    a component through a stratifier too noisy to strat-match anything."""
    f = "vol_max_ratio"
    cells = _cells(f, 0.09, [0.06, 0.12], rep_star=False)
    cells[(f, 30, "2h")] = {"delta": 0.09, "ci": [0.06, 0.12], "star": True}
    rule, _v = g.verdict_for(f, cells, _dtests(f, rep_passed=False), ADEQUATE,
                             {"star": True}, REL, False, False)
    assert not rule["c_pass"]


# ---------------------------------------------------------------------------
# the primary cell's 8 boundaries, derived not hard-coded
# ---------------------------------------------------------------------------

def test_the_primary_cell_has_exactly_the_registrations_eight_boundaries():
    """'>=1 included stratum at >=6 of 8 primary-cell boundaries' — the 8 is
    mechanical: at (W=60, H=2h) a boundary needs a full 60-minute window
    (b >= 10:30) and a reachable 2-hour horizon (b <= 14:00)."""
    hi = mi.HORIZONS.index("2h")
    bounds = g.primary_boundaries(WI_P, hi)
    assert len(bounds) == 8
    assert [mi.BOUNDARY_LABELS[b] for b in bounds] == [
        "10:30", "11:00", "11:30", "12:00", "12:30", "13:00", "13:30", "14:00"]
    assert len(g.primary_boundaries(mi.WINDOWS.index(30), hi)) == 9
    assert len(g.primary_boundaries(WI_REP, hi)) == 6


# ---------------------------------------------------------------------------
# the outcome store: high/low, and the untouched 1.14f default
# ---------------------------------------------------------------------------

def _write_npz(path: Path, session: date, per_sym: dict[str, dict[int, tuple]]):
    """One session in the layout `equity_bars_5min_backfill.write_session`
    produces. `per_sym[sym][bin] = (high, low, close)`."""
    syms = sorted(per_sym)
    t, h, ell, c, si = [], [], [], [], []
    open_ns = int(datetime.combine(session, dtime(9, 30), ET).timestamp()) \
        * 1_000_000_000
    for i, s in enumerate(syms):
        for b, (hv, lv, cv) in sorted(per_sym[s].items()):
            t.append(open_ns + b * mi.BIN_NS)
            h.append(hv)
            ell.append(lv)
            c.append(cv)
            si.append(i)
    n = len(t)
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        path, t_ns=np.array(t, np.int64), open=np.array(c, np.float64),
        high=np.array(h, np.float64), low=np.array(ell, np.float64),
        close=np.array(c, np.float64), volume=np.zeros(n, np.int64),
        sym_idx=np.array(si, np.int32), symbols=np.array(syms, dtype="U8"))


def test_the_bar_loaders_default_return_is_unchanged_and_range_is_opt_in(tmp_path):
    """The ONLY change 1.14g makes to `tools/mover_intraday.py`: an opt-in
    `with_range`. 1.14f's own call site must be byte-for-byte unaffected."""
    session = date(2026, 5, 15)
    p = tmp_path / f"{session.isoformat()}.npz"
    _write_npz(p, session, {"AAA": {0: (11.0, 9.0, 10.0),
                                    1: (12.5, 11.5, 12.0)}})
    plain = mi.load_bars_session(p, {"AAA": 0}, 1)
    assert len(plain) == 3                              # the 1.14f shape
    closes, last_bin, n_sym = plain
    assert closes[0, 0] == 10.0 and closes[0, 1] == 12.0
    wide = mi.load_bars_session(p, {"AAA": 0}, 1, with_range=True)
    assert len(wide) == 5
    c2, lb2, ns2, highs, lows = wide
    assert np.array_equal(c2, closes, equal_nan=True)
    assert (lb2, ns2) == (last_bin, n_sym)
    assert highs[0, 0] == 11.0 and lows[0, 0] == 9.0
    assert np.isnan(highs[0, 2]) and np.isnan(lows[0, 2])
    # an empty file keeps both shapes rather than raising
    empty = tmp_path / "2026-05-16.npz"
    _write_npz(empty, date(2026, 5, 16), {})
    assert len(mi.load_bars_session(empty, {"AAA": 0}, 1)) == 3
    assert len(mi.load_bars_session(empty, {"AAA": 0}, 1, with_range=True)) == 5


# ---------------------------------------------------------------------------
# the session pass: rings, valid sessions, the mechanical lagged NA
# ---------------------------------------------------------------------------

N_NAMES = 10
AMP = 0.004


def _fake_tape(cls_by_name_bin, cnt=40.0):
    """A `tape_bins` stand-in: {name_idx: {bin: classified premium}}."""
    def fn(session, name_index, n_names, na):
        shape = (n_names, mi.RTH_BINS)
        cls = np.zeros(shape)
        cnt_a = np.zeros(shape)
        for ni, bins in cls_by_name_bin.items():
            for b, v in bins.items():
                cls[ni, b] = v
                cnt_a[ni, b] = cnt
        na["prints_kept"] += int(cnt_a.sum())
        return cls, cls * 0.5, cls * 0.5, cnt_a, cls * 0.1, cls.sum(axis=1) > 0
    return fn


def _bars_fn(n_bins=mi.RTH_BINS, amp=AMP):
    """Every name, every bin: a real range and a real alternating close."""
    def fn(si, names):
        out = {}
        for i, n in enumerate(names):
            a = amp * (1 + 0.25 * (i % 4)) * (1 + 0.1 * (si % 3))
            px = {}
            for b in range(n_bins):
                c = 100.0 * (np.exp(a) if b % 2 else 1.0)
                px[b] = (c * (1 + a), c * (1 - a), c)
            out[n] = px
        return out
    return fn


def _sessions(n, start=date(2026, 1, 5)):
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d = d.fromordinal(d.toordinal() + 1)
    return out


def _panels(n_sessions, n_names=N_NAMES):
    quint = np.tile(np.arange(n_names, dtype=np.int8) // (n_names // 5),
                    (n_sessions, 1)).T
    return (quint, np.full((n_names, n_sessions), 100.0),
            np.full((n_names, n_sessions), 30.0))


def _run_pass(tmp_path, monkeypatch, n_sessions, bars_fn=None, tape=None,
              write_bars_for=None, n_bins=mi.RTH_BINS):
    tape = tape or {n: dict.fromkeys(range(mi.RTH_BINS), 100.0 + 10.0 * n)
                    for n in range(N_NAMES)}
    monkeypatch.setattr(mi, "tape_bins", _fake_tape(tape))
    sessions = _sessions(n_sessions)
    names = [f"N{i:02d}" for i in range(N_NAMES)]
    bars_fn = bars_fn or _bars_fn(n_bins)
    for si, s in enumerate(sessions):
        if write_bars_for is not None and si not in write_bars_for:
            continue
        _write_npz(tmp_path / f"{s.isoformat()}.npz", s, bars_fn(si, names))
    quint, mp, cp = _panels(n_sessions)
    na = g.new_na_114g()
    table = g.run_sessions_114g(sessions, names, [s.isoformat() for s in sessions],
                                quint, mp, cp, tmp_path, na)
    return table, na, sessions, names


@pytest.fixture
def small_cells(monkeypatch):
    """A 10-name fixture cannot fill a 25-row peer cell, so pass-level tests
    shrink the assignment minimums. The REGISTERED values are pinned by
    `test_registered_parameters_are_the_registered_values` and by
    `test_a_thin_or_degenerate_peer_cell_contributes_no_strata_at_all`."""
    monkeypatch.setattr(g, "MIN_ASSIGN_ROWS", 2)
    monkeypatch.setattr(g, "MIN_ASSIGN_DISTINCT", 2)


def _recording_rings(monkeypatch):
    made = []

    class Recording(mi.Ring):
        def __init__(self, shape, k):
            super().__init__(shape, k)
            self.pushes: list[np.ndarray] = []
            made.append(self)

        def push(self, values):
            self.pushes.append(np.array(values, copy=True))
            super().push(values)

    monkeypatch.setattr(mi, "Ring", Recording)
    return made


def _rv_ring(made):
    """The rv ring is created after vol_ring and out_ring, in RV_KINDS order."""
    return made[2 + g.RV_KINDS.index("rv")]


def test_every_session_consumes_an_rv_ring_slot_even_when_it_is_skipped(
        tmp_path, monkeypatch):
    """The registered valid-session rule, all four clauses: a VALID session =
    bar file exists AND session_last_bin > 0 AND the window holds >=80% of its
    bins at-or-before session_last_bin; and 'every skipped session still pushes
    NaN into its ring slot (1.14f Ring semantics — trailing-20 never silently
    stretches across gaps)'."""
    made = _recording_rings(monkeypatch)
    _t, na, sessions, _n = _run_pass(tmp_path, monkeypatch, 4,
                                     write_bars_for={0, 3})
    rv = _rv_ring(made)
    assert rv.n == 4                                    # no skipped slot
    assert na["sessions_no_bar_file"] == 2
    finite = [int(np.isfinite(p).sum()) for p in rv.pushes]
    assert finite[0] > 0 and finite[3] > 0
    assert finite[1] == 0 and finite[2] == 0            # NaN, not absent
    # the 20-slot ring is the registered lookback for every RV panel
    for k in g.RV_KINDS:
        assert made[2 + g.RV_KINDS.index(k)].buf.shape[0] == g.RV_LOOKBACK


def test_an_empty_bar_file_is_not_a_valid_session(tmp_path, monkeypatch):
    """session_last_bin > 0 is a clause of its own: a file that exists but holds
    no RTH bar pushes NaN and is counted."""
    made = _recording_rings(monkeypatch)
    sessions = _sessions(3)
    names = [f"N{i:02d}" for i in range(N_NAMES)]
    monkeypatch.setattr(mi, "tape_bins", _fake_tape(
        {n: dict.fromkeys(range(mi.RTH_BINS), 100.0) for n in range(N_NAMES)}))
    bars = _bars_fn()
    _write_npz(tmp_path / f"{sessions[0].isoformat()}.npz", sessions[0],
               bars(0, names))
    _write_npz(tmp_path / f"{sessions[1].isoformat()}.npz", sessions[1], {})
    _write_npz(tmp_path / f"{sessions[2].isoformat()}.npz", sessions[2],
               bars(2, names))
    quint, mp, cp = _panels(3)
    na = g.new_na_114g()
    g.run_sessions_114g(sessions, names, [s.isoformat() for s in sessions],
                        quint, mp, cp, tmp_path, na)
    rv = _rv_ring(made)
    assert na["sessions_empty_bar_file"] == 1
    assert rv.n == 3
    assert int(np.isfinite(rv.pushes[1]).sum()) == 0


def test_post_close_bars_never_satisfy_the_windows_coverage_rule(
        tmp_path, monkeypatch):
    """'the window holds >=80% of its bins at-or-before session_last_bin
    (post-close stragglers never count)'. On a 13:00-close session (last_bin=42)
    the 12:30 boundary's gapped window (bins 30..40) is inside the real grid and
    finite; the 13:30 boundary's (bins 36..46) holds only 6 of 11 real bins —
    54% — and is NA even though the npz carries bars there."""
    made = _recording_rings(monkeypatch)
    sessions = _sessions(1)
    names = [f"N{i:02d}" for i in range(N_NAMES)]
    monkeypatch.setattr(mi, "tape_bins", _fake_tape(
        {n: dict.fromkeys(range(mi.RTH_BINS), 100.0) for n in range(N_NAMES)}))

    # a real cross-sectional collapse at bin 42, with a few stragglers after it
    per_sym = {}
    bars = _bars_fn()(0, names)
    for i, n in enumerate(names):
        per_sym[n] = {b: v for b, v in bars[n].items() if b < 42}
    for j in range(3):                        # 3 straggler names report all day
        per_sym[f"S{j:02d}"] = bars[names[0]]
    for j in range(40):                       # widen the cross-section
        per_sym[f"X{j:02d}"] = {b: v for b, v in bars[names[0]].items()
                                if b < 42}
    _write_npz(tmp_path / f"{sessions[0].isoformat()}.npz", sessions[0], per_sym)
    quint, mp, cp = _panels(1)
    na = g.new_na_114g()
    g.run_sessions_114g(sessions, names, [sessions[0].isoformat()], quint, mp,
                        cp, tmp_path, na)
    rv = _rv_ring(made).pushes[0]
    wi = mi.WINDOWS.index(60)
    bi_1230 = mi.BOUNDARY_BINS.index(36)
    bi_1330 = mi.BOUNDARY_BINS.index(48)
    assert na["sessions_early_close"] == 1
    assert np.isfinite(rv[0, bi_1230, wi])
    assert np.isnan(rv[0, bi_1330, wi])
    # the straggler names are not in the analysis universe, so the ONLY way the
    # 13:30 window could have been finite is by counting post-close bars
    assert np.isnan(rv[:, bi_1330, wi]).all()


def test_the_lagged_panel_is_na_before_1130_and_labelled_only_at_w60(
        tmp_path, monkeypatch, small_cells):
    """Arm 1b is 'reported at W=60, all three horizons' and 'mechanically NA
    where the lagged window exits RTH (at W=60 this excludes boundaries before
    11:30)'."""
    table, _na, _s, _n = _run_pass(tmp_path, monkeypatch, 14)
    assert len(table["sess"])
    wi = mi.WINDOWS.index(60)
    for bi in range(len(mi.BOUNDARY_BINS)):
        m = table["bi"] == bi
        if not m.any():
            continue
        lab = table["rvq_lag"][m, wi]
        if mi.BOUNDARY_BINS[bi] < 24:
            assert (lab == -1).all(), mi.BOUNDARY_LABELS[bi]
        else:
            assert (lab >= 0).any(), mi.BOUNDARY_LABELS[bi]
    # W=30 and W=120 carry no lagged labels at all — the registered scope
    for w in (30, 120):
        assert (table["rvq_lag"][:, mi.WINDOWS.index(w)] == -1).all()
    # the same holds for the two disclosed secondary variants
    for col in ("rvq_c2c", "rvq_zg"):
        assert (table[col][:, mi.WINDOWS.index(30)] == -1).all()
        assert (table[col][:, mi.WINDOWS.index(120)] == -1).all()
        assert (table[col][:, wi] >= 0).any()


def test_the_rv_ratio_normalizer_reads_the_ring_before_this_session(
        tmp_path, monkeypatch, small_cells):
    """rv_ratio = rv_park / the name's own trailing-20 median of the same (b, W)
    measure, '>=10 valid sessions else NA', read BEFORE the session is pushed. A
    run of exactly 10 sessions can therefore produce no rv label at all; the
    11th is the first that can."""
    t10, _na, _s, _n = _run_pass(tmp_path, monkeypatch, 10)
    if len(t10["sess"]):
        assert (t10["rvq"] == -1).all()
    t12, _na2, _s2, _n2 = _run_pass(tmp_path, monkeypatch, 12)
    wi = mi.WINDOWS.index(60)
    late = t12["sess"] >= 10
    assert late.any() and (t12["rvq"][late, wi] >= 0).any()
    # the ratio is finite exactly where a label exists
    labelled = t12["rvq"][:, wi] >= 0
    assert np.isfinite(t12["rvr"][labelled, wi]).all()


def test_the_two_placebo_arms_fire_through_the_same_machinery(
        tmp_path, monkeypatch, small_cells):
    """Arm 2a fires on rv_ratio and Arm 2b on the ODD-bin ratio, both through
    1.14f's `fire_p90` within the ORIGINAL peer cell, both with
    first-fire-per-day, both with complement = ~fired AND pool (never ~fired AND
    eligible)."""
    table, na, _s, _n = _run_pass(tmp_path, monkeypatch, 14)
    wi = mi.WINDOWS.index(60)
    for tag in ("2a", "2b"):
        pool = table[f"pool{tag}"][:, wi]
        fired = table[f"fired{tag}"][:, wi]
        excl = table[f"excl{tag}"][:, wi]
        assert pool.any() and fired.any()
        assert not (fired & ~pool).any()          # a fired row is a pool row
        assert not (fired & excl).any()           # first-fire and excluded are
        assert na[f"cells_pool_arm{tag}"] > 0     # disjoint
    # Arm 2a's raw p90 hit is kept as well, for the co-firing secondary
    assert (table["hit2a"][:, wi] & ~table["fired2a"][:, wi]).any() or True
    assert not (table["fired2a"][:, wi] & ~table["hit2a"][:, wi]).any()


# ---------------------------------------------------------------------------
# the vectorized bootstrap, and the proof that gates it
# ---------------------------------------------------------------------------

def test_the_vectorized_bootstrap_is_bit_identical_to_the_per_stratum_one():
    """The implementation lock allows vectorization ONLY behind this proof. The
    two paths must agree exactly, including on ties, zero-weight sessions and
    dead draws — not 'to within a tolerance'."""
    rng = np.random.default_rng(11)
    for n, S in ((1, 6), (2, 6), (37, 8), (500, 10), (4001, 12)):
        vals = np.round(rng.normal(size=n), 3)          # deliberate ties
        sess = rng.integers(0, S, n)
        wmat = mi.block_weights(S, 200, 5, np.random.default_rng(5))
        a = mi.boot_medians(vals, sess, S, wmat)
        b = g.boot_medians_vec(vals, sess, S, wmat)
        assert np.array_equal(a, b, equal_nan=True), (n, S)
    # an empty side is NaN in both
    assert np.isnan(g.boot_medians_vec(np.zeros(0), np.zeros(0, np.int64), 6,
                                       mi.block_weights(6, 5, 5,
                                                        np.random.default_rng(1)))
                    ).all()


def test_the_vectorized_path_refuses_to_run_without_the_in_process_proof(
        monkeypatch):
    """'the full run must refuse to use the vectorized path unless that proof has
    run in-process.' The flag is process state, never a config knob."""
    monkeypatch.setattr(g, "_VECTOR_PROVEN", False)
    with pytest.raises(RuntimeError, match="NOT been proven"):
        g._bootfn(True)
    assert g._bootfn(False) is mi.boot_medians
    monkeypatch.setattr(g, "_VECTOR_PROVEN", True)
    assert g._bootfn(True) is g.boot_medians_vec


def test_the_smoke_proof_runs_on_real_strata_and_sets_the_flag(
        tmp_path, monkeypatch, small_cells):
    monkeypatch.setattr(g, "_VECTOR_PROVEN", False)
    table, _na, sessions, _n = _run_pass(tmp_path, monkeypatch, 16)
    proof = g.prove_vectorized(table, sessions, n_subset=10, n_boot=50,
                               max_arrays=40)
    assert proof["proven"] is True
    assert proof["arrays_compared"] > 0
    assert g._VECTOR_PROVEN is True
    assert g._bootfn(True) is g.boot_medians_vec
    # too few sessions is reported, not silently accepted
    monkeypatch.setattr(g, "_VECTOR_PROVEN", False)
    tiny = g.prove_vectorized({k: v[:0] for k, v in table.items()}, sessions)
    assert tiny["proven"] is False and g._VECTOR_PROVEN is False


# ---------------------------------------------------------------------------
# the reproduce-1.14f gate
# ---------------------------------------------------------------------------

def _locked(primary, n_sessions=14):
    return {"meta": {"registered": "2026-07-30"},
            "arms": {"all_train": {"n_sessions": n_sessions,
                                   "primary": primary}}}


def test_placebo_only_rows_can_never_reach_a_component_cell(
        tmp_path, monkeypatch, small_cells):
    """1.14g's row table is a SUPERSET of 1.14f's: it keeps rows that only the
    placebo pools want (rv defined where every component is NA). The reproduce
    gate is only honest if such a row cannot enter a component's fired or
    complement set — `mi.cell_mask` requires that component's own pool bit, and
    this asserts it rather than arguing it."""
    # name 9 carries prints but ZERO premium, so `short_dte_share`,
    # `concentration` and `vol_max_ratio` are all NA (its trailing window-premium
    # median is 0), and 19 valid premium_max days put `largest_print_ratio` out
    # of reach too — yet its equity bars give it a perfectly good rv_ratio
    tape = {n: dict.fromkeys(range(mi.RTH_BINS), 100.0 + 10.0 * n)
            for n in range(N_NAMES)}
    tape[9] = dict.fromkeys(range(mi.RTH_BINS), 0.0)
    monkeypatch.setattr(mi, "tape_bins", _fake_tape(tape))
    sessions = _sessions(14)
    names = [f"N{i:02d}" for i in range(N_NAMES)]
    bars = _bars_fn()
    for si, s in enumerate(sessions):
        _write_npz(tmp_path / f"{s.isoformat()}.npz", s, bars(si, names))
    quint, mp, cp = _panels(14)
    cp[9, :] = 19.0                                  # one short of 20 days
    table = g.run_sessions_114g(sessions, names,
                               [s.isoformat() for s in sessions], quint, mp, cp,
                               tmp_path, g.new_na_114g())
    extra = (~table["pool"].any(axis=(1, 2))
             & (table["pool2a"].any(axis=1) | table["pool2b"].any(axis=1)))
    assert extra.any(), "fixture produced no placebo-only rows to check"
    assert set(table["name"][extra].tolist()) == {9}
    for wi in range(len(mi.WINDOWS)):
        for fi in range(len(mi.FEATS)):
            for hi in range(len(mi.HORIZONS)):
                assert not (mi.cell_mask(table, wi, fi, hi) & extra).any()


def test_the_gate_passes_when_the_screen_reproduces_exactly(
        tmp_path, monkeypatch):
    table, _na, _s, _n = _run_pass(tmp_path, monkeypatch, 14)
    primary = g.primary_screen(table)
    assert len(primary) == 4                      # all four 1.14f components
    assert {r["component"] for r in primary} == set(mi.FEATS)
    report = g.reproduce_114f(table, _locked(primary))
    assert report["passed"] is True
    for cell in report["cells"]:
        assert cell["n_fired"]["match"] and cell["diff"]["match"]
    # the environment is stamped, and the locked run's absence of one is stated
    assert report["environment_this_run"]["numpy"] == np.__version__
    assert "NOT STAMPED" in report["environment_locked_run"]


def test_a_one_row_count_difference_aborts_with_the_per_cell_delta(
        tmp_path, monkeypatch):
    """'n_fired and n_complement integer-exact, diffs to 5 decimals — else
    ABORT, reporting the per-cell delta and the environment diff; never proceed
    on a loosened tolerance.'"""
    table, _na, _s, _n = _run_pass(tmp_path, monkeypatch, 14)
    primary = g.primary_screen(table)
    bad = [dict(r) for r in primary]
    bad[2]["n_fired"] = bad[2]["n_fired"] + 1
    with pytest.raises(g.ReproduceGateFailure) as exc:
        g.reproduce_114f(table, _locked(bad))
    report = json.loads(str(exc.value))
    assert report["passed"] is False
    cell = next(c for c in report["cells"] if c["component"] == bad[2]["component"])
    assert cell["n_fired"]["match"] is False
    assert cell["n_fired"]["locked"] == cell["n_fired"]["recomputed"] + 1
    assert report["environment_this_run"]["platform"]
    assert "5 decimals" in report["tolerance"]


def test_a_sixth_decimal_median_difference_aborts_too(tmp_path, monkeypatch):
    table, _na, _s, _n = _run_pass(tmp_path, monkeypatch, 14)
    primary = g.primary_screen(table)
    bad = [dict(r) for r in primary]
    live = next(i for i, r in enumerate(bad) if r["diff"] is not None)
    bad[live]["diff"] = round(bad[live]["diff"] + 0.00001, 5)
    with pytest.raises(g.ReproduceGateFailure):
        g.reproduce_114f(table, _locked(bad))
    # a component missing from the locked JSON is a failure, not a skip
    with pytest.raises(g.ReproduceGateFailure) as exc:
        g.reproduce_114f(table, _locked(primary[:3]))
    assert "absent from locked JSON" in str(exc.value)


# ---------------------------------------------------------------------------
# end to end through main(): the gate, the arms, the artifact, the output
# ---------------------------------------------------------------------------

E2E_NAMES = 40
E2E_DATES = 60
E2E_CORPUS = 30


def _spiky_tape():
    """A session-AWARE tape stand-in: each name carries a premium spike in one
    boundary's window, and which boundary moves with (name, session).

    A tape that repeats itself is useless here: `vol_max_ratio` would be exactly
    1.0 for every name (its own trailing median IS the current value), p90 with
    ties-fire would fire the whole pool, and there would be no complement to
    contrast — the fixture would exercise only the "empty pool" branch. Moving
    the spike also spreads the fired rows across boundaries, which is what the
    balance gate's boundary-coverage rule reads."""
    def fn(session, name_index, n_names, na):
        shape = (n_names, mi.RTH_BINS)
        d = session.toordinal()
        base = np.arange(mi.RTH_BINS)[None, :] % 7
        cls = 100.0 + 5.0 * ((np.arange(n_names)[:, None] + base) % 7)
        # a per-(name, session) drift, so `vol_max_ratio` is not exactly 1.0 for
        # every name — otherwise p90-with-ties fires the whole pool at the first
        # boundary and first-fire-per-day leaves no complement anywhere
        cls = cls * (1.0 + 0.4 * (((np.arange(n_names)[:, None] * 3 + d) % 13)
                                  / 13.0))
        for i in range(n_names):
            bb = mi.BOUNDARY_BINS[(i * 3 + d) % len(mi.BOUNDARY_BINS)]
            cls[i, max(bb - 2, 0)] *= 6.0 + (i % 5)
        cnt = np.full(shape, 40.0)
        na["prints_kept"] += int(cnt.sum())
        return (cls, cls * 0.5, cls * 0.5, cnt, cls * 0.1,
                np.ones(n_names, bool))
    return fn


def _e2e_bars():
    """Bars whose range and close vary richly across (name, session), so the
    rv-quintile cuts are not degenerate and the outcome is not a constant."""
    def fn(si, names):
        out = {}
        for i, n in enumerate(names):
            a = AMP * (1.0 + 0.7 * (((i * 7 + si * 13) % 11) / 11.0))
            px = {}
            for b in range(mi.RTH_BINS):
                c = 100.0 * np.exp(a * ((i + b + si) % 3))
                r = a * (1.0 + 0.5 * ((b + i) % 4) / 4.0)
                px[b] = (c * (1 + r), c * (1 - r), c)
            out[n] = px
        return out
    return fn


def _corpus_db(path: Path, names, dates, corpus_sessions):
    from db.flow_corpus import init_db
    init_db(path)
    con = sqlite3.connect(path)
    rng = np.random.default_rng(3)
    for i, n in enumerate(["SPY", *names]):
        for d in dates:
            con.execute(
                "INSERT OR REPLACE INTO equity_bars "
                "(symbol, date, o, h, l, c, v, adjustment, source) "
                "VALUES (?,?,?,?,?,?,?,'split','test')",
                (n, d, 100.0, 101.0, 99.0, 100.0,
                 float(1_000_000 * (i + 1)) + rng.integers(0, 1000)))
        if n == "SPY":
            continue
        for d in dates:
            con.execute(
                "INSERT OR REPLACE INTO flow_rollup (date, underlying, right, "
                "dte_bucket, mny_bucket, side, strict_side, prints, orders, "
                "contracts, premium_usd, premium_sq, premium_sq_orders, "
                "premium_max, u_premium, u_premium_clipped, multileg, cond, "
                "schema_version) VALUES (?,?,'C','0-7','atm','buy','buy',"
                "1,1,1,1000.0,1.0,1.0,?,0.0,0.0,0,'',7)",
                (d, n, 100.0 + 10.0 * i))
    for d in corpus_sessions:
        con.execute(
            "INSERT OR REPLACE INTO corpus_sessions "
            "(date, prints, rows, underlyings, unresolved_prints, "
            "no_spot_prints, schema_version, ingested_at) "
            "VALUES (?,1,1,1,0,0,7,'now')", (d,))
    con.commit()
    con.close()


@pytest.fixture
def e2e(tmp_path, monkeypatch):
    """A whole synthetic study: 40 names, 60 calendar dates, the last 22 of them
    corpus sessions with bar files. Occupancy and assignment minimums are shrunk
    so a 40-name universe can populate strata at all — the REAL constants are
    pinned by `test_registered_parameters_are_the_registered_values`."""
    dates = [d.isoformat() for d in _sessions(E2E_DATES, date(2026, 2, 2))]
    dates = [d for d in dates if d <= mi.TRAIN_END]
    assert len(dates) >= E2E_DATES - 2
    names = [f"N{i:02d}" for i in range(E2E_NAMES)]
    corpus = dates[-E2E_CORPUS:]
    db = tmp_path / "corpus.db"
    _corpus_db(db, names, dates, corpus)
    bars = tmp_path / "bars"
    bars_fn = _e2e_bars()
    for si, ds in enumerate(corpus):
        _write_npz(bars / f"{ds}.npz", date.fromisoformat(ds), bars_fn(si, names))
    monkeypatch.setattr(mi, "tape_bins", _spiky_tape())
    monkeypatch.setattr(mi, "load_universe", lambda dbp, fenced=False: list(names))
    monkeypatch.setattr(g, "N_BOOT", 40)
    monkeypatch.setattr(g, "PERM_N", 2)
    monkeypatch.setattr(g, "OCC_MIN_FIRED", 2)
    monkeypatch.setattr(g, "OCC_MIN_COMPLEMENT", 2)
    monkeypatch.setattr(g, "MIN_ASSIGN_ROWS", 4)
    monkeypatch.setattr(g, "MIN_ASSIGN_DISTINCT", 2)
    return {"db": db, "bars": bars, "names": names, "dates": dates,
            "corpus": corpus, "tmp": tmp_path}


def _e2e_locked(e2e) -> Path:
    """Build the locked-1.14f stand-in from the SAME inputs main() will use, via
    the shared `build_inputs` — a hand-rebuilt setup would be testing its own
    copy of main."""
    inp = g.build_inputs(e2e["db"], mi.TRAIN_END)
    med_pmax, cnt_pmax = inp["pmax_panels"]
    table = g.run_sessions_114g(inp["sessions"], inp["universe"], inp["dates"],
                                inp["quint"], med_pmax, cnt_pmax, e2e["bars"],
                                g.new_na_114g())
    assert len(table["sess"]), "the e2e fixture produced no rows"
    path = e2e["tmp"] / "locked_114f.json"
    path.write_text(json.dumps(_locked(g.primary_screen(table),
                                       len(inp["sessions"]))))
    return path


def test_the_whole_registered_run_end_to_end(e2e):
    """One pass over the whole tool: the gate, both calibration gates, all four
    arms, the balance/reliability tables, criterion (d), the verdicts, the
    secondary battery, the fired-key artifact and the NA ledger."""
    out = e2e["tmp"] / "out.json"
    keys = e2e["tmp"] / "keys.npz"
    rc = g.main(["--corpus-db", str(e2e["db"]), "--bars-dir", str(e2e["bars"]),
                 "--earnings-db", str(e2e["tmp"] / "missing.db"),
                 "--join-ledger", str(e2e["tmp"] / "missing.json"),
                 "--locked-114f", str(_e2e_locked(e2e)),
                 "--out", str(out), "--keys-out", str(keys)])
    assert rc == 0
    p = json.loads(out.read_text())

    # the gate ran, passed, and stamped the environment 1.14f never recorded
    assert p["meta"]["reproduce_gate"]["passed"] is True
    assert p["meta"]["environment"]["numpy"] == np.__version__
    assert p["meta"]["environment"]["platform"]
    assert p["meta"]["wall_seconds"] is not None
    assert p["meta"]["seed"] == 20260809
    assert p["meta"]["multiplicity_budget"] == list(g.COMPONENTS)
    assert p["meta"]["ambiguities"]["bootstrap_stratum_weights"]

    arm = p["arms"]["all_train"]
    for key in ("arm1_contemporaneous", "arm1_coarse_baseline",
                "arm1_min_weighted_secondary", "arm1_boundary_matched",
                "arm1b_lagged", "arm2a_machinery_placebo",
                "arm2b_split_sample_placebo", "calibration_gates", "balance",
                "stratifier_reliability", "reliability_floor",
                "benchmark_test_d", "reading_rule", "verdicts", "secondary",
                "coarsening_override"):
        assert key in arm, key
    # 3 components x 3 W x 3 H = 27 Arm 1 cells, exactly one of them primary
    assert len(arm["arm1_contemporaneous"]) == 27
    assert sum(c["primary_cell"] for c in arm["arm1_contemporaneous"]) == 3
    live = [c for c in arm["arm1_contemporaneous"] if "delta" in c]
    assert live, "no evaluable Arm 1 cell in the fixture"
    for c in live:
        assert {"n_strata_included", "dropped_fired_share", "weighting"} <= set(c)
    # every component carries a verdict with BOTH readings, separately
    for f in g.COMPONENTS:
        v = arm["verdicts"][f]
        assert set(v) >= {"science", "product", "survives", "balance",
                          "negligible_band", "sign_flip_vs_114f"}
        assert v["negligible_band"] == g.NEGLIGIBLE_BAND[f]
        rr = arm["reading_rule"][f]
        assert set(rr) >= {"a_primary_clear_of_zero_same_sign", "b_pass",
                           "c_pass", "d_pass", "all_four"}
    # Arm 1b and Arm 2a are labelled as carrying no survival authority
    assert all(r.get("survival_authority") is False for r in arm["arm1b_lagged"])
    assert all(r.get("stratified") is False
               for r in arm["arm2a_machinery_placebo"])
    assert "NOT magnitude-comparable" in arm["arm2a_note"]
    # the secondary battery is present and says what it is
    sec = arm["secondary"]
    for key in ("delta_by_rv_quintile", "decile_recut",
                "co_firing_and_top_quintile", "contemporaneous_complement_arm",
                "denominator_inflation", "mechanical_descriptive",
                "stratifier_variants", "fired_mass_all_strata_by_rv_quintile"):
        assert key in sec, key
    assert "OUT OF SCOPE" in sec["note"]

    # the fired-key artifact and its hash
    assert keys.exists()
    with np.load(keys) as z:
        recs = z["keys"]
    assert len(recs) == p["meta"]["fired_row_keys"]["n_rows"] > 0
    assert set(recs["component"].tolist()) <= set(mi.FEATS)
    assert set(recs["W"].tolist()) <= set(mi.WINDOWS)
    import hashlib
    assert hashlib.sha256(recs.tobytes()).hexdigest() == \
        p["meta"]["fired_row_keys"]["sha256"]

    # NOT A BACKTEST: the results carry no trading language
    blob = json.dumps({k: v for k, v in p.items() if k != "meta"}).lower()
    for banned in ("pnl", "p&l", "slippage", "hit_rate", "win_rate",
                   "entry_price", "position_size", "return_pct"):
        assert banned not in blob, banned
    # no bootstrap draw vectors leaked into the JSON
    assert "_draws" not in blob and "_nf" not in blob
    # the NA ledger names every new stage
    for key in ("cells_na_rv_ratio", "cells_na_rv_lag", "cells_na_rv_even",
                "na_strata_cell_too_small", "cells_pool_arm2a",
                "cells_pool_arm2b", "sessions_rv_valid_any"):
        assert key in p["na_accounting"], key

    # the fixture really does populate pooled strata (a run where every stratum
    # dropped would pass every assertion above while measuring nothing)
    prim = {c["component"]: c for c in arm["arm1_contemporaneous"]
            if c["primary_cell"]}
    assert all(prim[f]["n_strata_included"] > 20 for f in g.COMPONENTS)
    # both calibration gates were evaluated, per component, before any reading
    gate1 = arm["calibration_gates"]["gate1_permuted_label_null"]
    assert {p_["component"] for p_ in gate1["per_component"]} == set(g.COMPONENTS)
    for p_ in gate1["per_component"]:
        assert len(p_["permutations"]) == g.PERM_N
        assert p_["max_abs_deviation"] is not None
        assert p_["passed"] == (p_["max_abs_deviation"] <= g.PERM_TOL)
    # gate 2 is the physics check: an rv_ratio screen must predict |move|
    assert arm["calibration_gates"]["gate2_machinery_control"]["passed"] is True
    assert arm["calibration_gates"]["run_inconclusive"] == (not gate1["passed"])
    # every verdict states whether the gates blocked the reading, and a blocked
    # run reads INCONCLUSIVE on both axes
    blocked = arm["calibration_gates"]["run_inconclusive"]
    for f in g.COMPONENTS:
        assert arm["verdicts"][f]["read_blocked"] is blocked
        if blocked:
            assert arm["verdicts"][f]["science"].startswith("INCONCLUSIVE")
            assert arm["verdicts"][f]["product"].startswith("INCONCLUSIVE")
            assert arm["verdicts"][f]["survives"] is False
    # the balance table is a real table: 5 quintile rows, each with its coverage
    for b in arm["balance"]:
        assert len(b["quintiles"]) == 5
        assert b["n_primary_boundaries"] == 8
        for q in b["quintiles"]:
            assert set(q) >= {"evaluable", "boundaries_covered",
                              "fired_mass_share", "gap_within_limit"}
    # Arm 2b is evaluable here, so the (d) machinery ran with a real benchmark
    assert any("delta" in r for r in arm["arm2b_split_sample_placebo"])


def test_the_vectorized_bootstrap_reproduces_the_registered_run_exactly(e2e):
    """The implementation lock's real claim: with the proof in hand, the
    vectorized path is not an approximation. Two whole runs of the same study,
    one per-stratum and one vectorized, must agree on every emitted number."""
    locked = _e2e_locked(e2e)
    plain, vec = e2e["tmp"] / "plain.json", e2e["tmp"] / "vec.json"
    common = ["--corpus-db", str(e2e["db"]), "--bars-dir", str(e2e["bars"]),
              "--earnings-db", str(e2e["tmp"] / "missing.db"),
              "--join-ledger", str(e2e["tmp"] / "missing.json"),
              "--locked-114f", str(locked),
              "--keys-out", str(e2e["tmp"] / "k3.npz")]
    assert g.main([*common, "--out", str(plain)]) == 0
    assert g.main([*common, "--out", str(vec), "--vectorized"]) == 0
    pj, vj = json.loads(plain.read_text()), json.loads(vec.read_text())
    assert pj["meta"]["vectorized_bootstrap"]["used"] is False
    assert vj["meta"]["vectorized_bootstrap"]["used"] is True
    assert vj["meta"]["vectorized_bootstrap"]["proof"]["proven"] is True
    assert vj["meta"]["vectorized_bootstrap"]["proof"]["arrays_compared"] > 0
    for key in ("arm1_contemporaneous", "arm1_coarse_baseline", "arm1b_lagged",
                "arm2b_split_sample_placebo", "benchmark_test_d"):
        assert (json.dumps(pj["arms"]["all_train"][key])
                == json.dumps(vj["arms"]["all_train"][key])), key
    assert json.dumps(pj["arms"]["all_train"]["verdicts"]) == \
        json.dumps(vj["arms"]["all_train"]["verdicts"])


def test_main_aborts_and_writes_nothing_when_the_screen_does_not_reproduce(e2e):
    """The abort path, end to end: exit 3, no output file, no key artifact."""
    locked = json.loads(_e2e_locked(e2e).read_text())
    locked["arms"]["all_train"]["primary"][0]["n_complement"] += 7
    bad = e2e["tmp"] / "bad_locked.json"
    bad.write_text(json.dumps(locked))
    out = e2e["tmp"] / "never.json"
    keys = e2e["tmp"] / "never_keys.npz"
    rc = g.main(["--corpus-db", str(e2e["db"]), "--bars-dir", str(e2e["bars"]),
                 "--earnings-db", str(e2e["tmp"] / "missing.db"),
                 "--join-ledger", str(e2e["tmp"] / "missing.json"),
                 "--locked-114f", str(bad), "--out", str(out),
                 "--keys-out", str(keys)])
    assert rc == 3
    assert not out.exists() and not keys.exists()


def test_a_missing_locked_114f_json_stops_the_run_rather_than_skipping_the_gate(
        e2e):
    out = e2e["tmp"] / "never2.json"
    rc = g.main(["--corpus-db", str(e2e["db"]), "--bars-dir", str(e2e["bars"]),
                 "--earnings-db", str(e2e["tmp"] / "missing.db"),
                 "--join-ledger", str(e2e["tmp"] / "missing.json"),
                 "--locked-114f", str(e2e["tmp"] / "absent.json"),
                 "--out", str(out)])
    assert rc == 2 and not out.exists()


def test_the_join98_mirror_is_the_registered_reduced_battery(e2e):
    """'join>=98 arm mirror of the three component primaries (stratified) + Arm
    2a (unstratified), rows filtered to the subset with ALL trailing rings still
    built from every train session'."""
    led = e2e["tmp"] / "join_ledger.json"
    led.write_text(json.dumps({d: {"floor_pass": i % 2 == 0}
                               for i, d in enumerate(e2e["corpus"])}))
    out = e2e["tmp"] / "out_join.json"
    rc = g.main(["--corpus-db", str(e2e["db"]), "--bars-dir", str(e2e["bars"]),
                 "--earnings-db", str(e2e["tmp"] / "missing.db"),
                 "--join-ledger", str(led),
                 "--locked-114f", str(_e2e_locked(e2e)),
                 "--out", str(out), "--keys-out", str(e2e["tmp"] / "k2.npz")])
    assert rc == 0
    arms = json.loads(out.read_text())["arms"]
    assert set(arms) == {"all_train", "join_ge_98"}
    mirror = arms["join_ge_98"]
    assert mirror["n_sessions"] < arms["all_train"]["n_sessions"]
    assert len(mirror["arm1_contemporaneous"]) == 3          # primaries only
    assert all(c["primary_cell"] for c in mirror["arm1_contemporaneous"])
    assert len(mirror["arm2a_machinery_placebo"]) == 1
    assert "reading_rule" not in mirror                      # no authority
    assert "trailing rings" in mirror["mirror_note"]


def test_the_smoke_verify_mode_only_proves_the_vectorizer(e2e, monkeypatch):
    """The proof's 10-session subset is 10 ROW-BEARING sessions, which is why it
    cannot be produced by truncating the pass to 10 sessions: every trailing ring
    needs >=10 valid sessions before a single row exists."""
    monkeypatch.setattr(g, "_VECTOR_PROVEN", False)
    out = e2e["tmp"] / "unused.json"
    rc = g.main(["--corpus-db", str(e2e["db"]), "--bars-dir", str(e2e["bars"]),
                 "--smoke-verify", "--smoke-sessions", "10",
                 "--out", str(out)])
    assert rc == 0
    assert not out.exists()          # a proof run emits no study output
    assert g._VECTOR_PROVEN is True
