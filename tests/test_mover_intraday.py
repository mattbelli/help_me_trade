"""Roadmap 1.14f (`tools/mover_intraday.py`) — the registration, as assertions.

1.14f is PRE-REGISTERED, so the risk here is not "the code crashes", it is "the
code quietly measures something the registration did not ask for". Every test
below pins one registered parameter or one leakage rule, and hand-computes the
expected number rather than asserting against the implementation's own output.

Hermetic: no network, no real corpus, no real bar store. The 5-minute npz files
are synthesised in `tmp_path` in the exact layout
`tools/equity_bars_5min_backfill.py` writes, and the raw print tape is injected
by replacing `tape_bins` — the real one needs `databento` and an 11M-print file.
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
from tools.flow_corpus_rollup import PX, UNDEF, fence_and_classify

ET = mi.ET


# ---------------------------------------------------------------------------
# the registered constants — a prereg fence, so a "tidy-up" cannot retune the study
# ---------------------------------------------------------------------------

def test_registered_parameters_are_the_registered_values():
    """Every one of these is a number the registration fixes. A study whose
    constants drift is not the study that was registered, and the drift would be
    invisible in the output tables."""
    assert mi.SEED == 20260808
    assert mi.N_BOOT == 10_000
    assert mi.BLOCK == 5                      # SESSIONS, not windows
    assert mi.MIN_PRINTS == 20                # fenced classified prints/window
    assert mi.WINDOWS == (30, 60, 120)
    assert mi.HORIZONS == ("30m", "2h", "close")
    assert (mi.PRIMARY_W, mi.PRIMARY_H) == (60, "2h")
    assert mi.FEATS == ("short_dte_share", "concentration",
                        "largest_print_ratio", "vol_max_ratio")
    assert mi.RANK_PCT == 90.0
    assert (mi.MIN_PMAX_DAYS, mi.PMAX_LOOKBACK) == (20, 60)
    assert (mi.MIN_VOL_SESSIONS, mi.VOL_LOOKBACK) == (10, 20)
    assert (mi.MIN_OUT_SESSIONS, mi.OUT_LOOKBACK) == (10, 20)
    assert mi.BAR_COVERAGE == 0.80
    assert mi.TAIL_X == 3.0                   # secondary tail-rate threshold
    assert mi.K_GATE == 10                    # 1.14e earnings gate
    # 4 components at one primary cell => the registered 4 primary intervals
    assert len(mi.FEATS) == 4


def test_the_boundary_grid_is_ten_to_fifteen_thirty_every_half_hour():
    assert mi.BOUNDARY_LABELS == (
        "10:00", "10:30", "11:00", "11:30", "12:00", "12:30",
        "13:00", "13:30", "14:00", "14:30", "15:00", "15:30")
    assert len(mi.BOUNDARY_BINS) == 12


# ---------------------------------------------------------------------------
# grain geometry
# ---------------------------------------------------------------------------

def test_a_window_that_would_reach_before_the_open_is_not_a_window():
    """The registered rule: a (b, W) pair enters only if the window lies fully
    inside RTH — "the open bucket participates as window CONTENT, never as a
    truncated window". Silently truncating instead would compare a 30-minute
    window against 120-minute ones under the same label."""
    assert mi.window_bins(6, 30) == (0, 6)        # 10:00, 30m: 09:30-09:55
    assert mi.window_bins(6, 60) is None          # 10:00, 60m: needs 09:00
    assert mi.window_bins(12, 60) == (0, 12)      # 10:30, 60m: exactly the open
    assert mi.window_bins(18, 120) is None        # 11:00, 120m
    assert mi.window_bins(24, 120) == (0, 24)     # 11:30, 120m: exactly the open
    # so W=60 loses one boundary and W=120 loses three
    assert sum(mi.window_bins(b, 30) is not None for b in mi.BOUNDARY_BINS) == 12
    assert sum(mi.window_bins(b, 60) is not None for b in mi.BOUNDARY_BINS) == 11
    assert sum(mi.window_bins(b, 120) is not None for b in mi.BOUNDARY_BINS) == 9


def test_per_horizon_boundary_eligibility_is_the_registered_mechanical_rule():
    """"H=2h needs b <= 14:00, H=30m and H=close need b <= 15:30." 14:00's two
    hours end at exactly 16:00, so it is the last eligible 2h boundary."""
    assert mi.horizon_end_bin(54, "2h") == mi.RTH_BINS      # 14:00 -> 16:00
    assert mi.horizon_end_bin(60, "2h") is None             # 14:30 would overrun
    assert mi.horizon_end_bin(72, "30m") == mi.RTH_BINS     # 15:30 -> 16:00
    assert mi.horizon_end_bin(72, "close") == mi.RTH_BINS
    assert mi.horizon_end_bin(6, "close") == mi.RTH_BINS    # close spans to 16:00
    elig_2h = [b for b in mi.BOUNDARY_BINS if mi.horizon_end_bin(b, "2h")]
    assert len(elig_2h) == 9 and max(elig_2h) == 54


def test_rth_bins_are_derived_from_the_et_clock_across_dst():
    """Bin 0 is 09:30 ET and bin 77 is 15:55 ET on both sides of a DST change;
    16:00 and 09:25 are outside RTH. A UTC-fixed offset would drift by an hour
    for half the corpus."""
    for day in (date(2026, 1, 15), date(2026, 5, 15)):
        def ns(h, m, d=day):
            return np.array(
                [int(datetime.combine(d, dtime(h, m), ET).timestamp())
                 * 1_000_000_000])
        assert mi.bins_from_ns(ns(9, 30), day)[0] == 0
        assert mi.bins_from_ns(ns(15, 55), day)[0] == 77
        assert mi.bins_from_ns(ns(16, 0), day)[0] == -1
        assert mi.bins_from_ns(ns(9, 25), day)[0] == -1


# ---------------------------------------------------------------------------
# the fence — extracted from the corpus roller, previously untested anywhere
# ---------------------------------------------------------------------------

def _prints(rows):
    """(price, size, bid, ask) tuples -> a tcbbo-shaped structured array."""
    arr = np.zeros(len(rows), dtype=[
        ("price", "i8"), ("size", "u4"), ("bid_px_00", "i8"), ("ask_px_00", "i8")])
    for i, (p, s, b, a) in enumerate(rows):
        arr[i] = (p, s, b, a)
    return arr


def test_the_v7_fence_and_side_rule():
    """The whole 1.14f feature basis rests on this and it had no test in the
    repo: four hand-copied versions existed and two predate `wide_q`. Alphabet:
    0 buy, 1 sell, 2 unclassified, 3 wide_spread."""
    arr = _prints([
        (int(1.06 * PX), 10, int(1.00 * PX), int(1.10 * PX)),  # above mid -> buy
        (int(1.04 * PX), 10, int(1.00 * PX), int(1.10 * PX)),  # below mid -> sell
        (int(1.05 * PX), 10, int(1.00 * PX), int(1.10 * PX)),  # AT mid -> unclassified
        (int(1.50 * PX), 10, int(1.00 * PX), int(2.00 * PX)),  # spread>50% of mid
        (int(1.06 * PX), 10, UNDEF, int(1.10 * PX)),           # no bid -> no side
        (int(1.06 * PX), 10, int(1.10 * PX), int(1.00 * PX)),  # crossed -> no side
    ])
    fx = fence_and_classify(arr)
    assert list(fx.side) == [0, 1, 2, 3, 2, 2]
    # the wide print is quoted but fenced: it is NOT in good_q and NOT bull/bear
    assert list(fx.q_ok) == [True, True, True, True, False, False]
    assert list(fx.wide_q) == [False, False, False, True, False, False]
    assert list(fx.good_q) == [True, True, True, False, False, False]
    # premium = price * size * 100, with the DBN fixed-point scale undone
    assert fx.premium[0] == pytest.approx(1.06 * 10 * 100)


def test_the_spread_fence_boundary_is_fifty_percent_of_the_mid():
    """"the 1.25 clause-1 spread fence" names FLOW_ROADMAP section 1.25, not a
    1.25 threshold — the registered number is 0.50 of the mid, and reading it as
    a ratio of 1.25 would fence out a different population entirely."""
    from tools.flow_corpus_rollup import SPREAD_MAX_FRAC
    assert SPREAD_MAX_FRAC == 0.50
    # mid 1.00, spread exactly 0.50 -> not wide (strict >); 0.51 -> wide
    at = _prints([(int(1.0 * PX), 1, int(0.75 * PX), int(1.25 * PX))])
    over = _prints([(int(1.0 * PX), 1, int(0.745 * PX), int(1.255 * PX))])
    assert not fence_and_classify(at).wide_q[0]
    assert fence_and_classify(over).wide_q[0]


# ---------------------------------------------------------------------------
# the four components, hand-computed
# ---------------------------------------------------------------------------

def _bins(cls_by_bin, short_by_bin=None, bull_by_bin=None,
          cnt_by_bin=None, pmax_by_bin=None):
    """One name's per-5-minute-bin tape aggregates as (1, RTH_BINS) arrays."""
    def mk(d):
        a = np.zeros((1, mi.RTH_BINS))
        for b, v in (d or {}).items():
            a[0, b] = v
        return a
    return (mk(cls_by_bin), mk(short_by_bin), mk(bull_by_bin),
            mk(cnt_by_bin), mk(pmax_by_bin))


def test_window_stats_sums_the_window_and_separates_bin_top_from_print_top():
    """`concentration`'s granule is the top 5-minute BIN; `largest_print_ratio`'s
    numerator is the top single PRINT. Conflating them is the easy mistake: one
    descends the granule with the grain, the other only the numerator."""
    cls, short, bull, cnt, pmax = _bins(
        cls_by_bin={0: 100.0, 1: 400.0, 2: 500.0, 6: 9_999.0},
        short_by_bin={0: 100.0, 2: 200.0},
        bull_by_bin={1: 400.0},
        cnt_by_bin={0: 5, 1: 8, 2: 10, 6: 99},
        pmax_by_bin={0: 60.0, 1: 300.0, 2: 250.0, 6: 9_999.0})
    ws = mi.window_stats(cls, short, bull, cnt, pmax, 0, 6)   # 10:00, W=30
    assert ws["cls"][0] == 1000.0        # bin 6 is OUTSIDE the trailing window
    assert ws["short"][0] == 300.0
    assert ws["bull"][0] == 400.0
    assert ws["cnt"][0] == 23
    assert ws["bin_top"][0] == 500.0     # the top BIN
    assert ws["print_top"][0] == 300.0   # the top PRINT, in a different bin


def test_each_component_is_the_registered_ratio():
    ws = {"cls": np.array([1000.0]), "short": np.array([300.0]),
          "bull": np.array([400.0]), "cnt": np.array([23.0]),
          "bin_top": np.array([500.0]), "print_top": np.array([300.0])}
    c = mi.components(ws, med_pmax=np.array([150.0]), cnt_pmax=np.array([25.0]),
                      vol_med=np.array([250.0]), vol_cnt=np.array([12.0]))
    assert c["short_dte_share"][0] == pytest.approx(0.30)    # 300/1000
    assert c["concentration"][0] == pytest.approx(0.50)      # 500/1000
    assert c["largest_print_ratio"][0] == pytest.approx(2.0)  # 300/150
    assert c["vol_max_ratio"][0] == pytest.approx(4.0)       # 1000/250


def test_component_na_rules_are_the_registered_minimum_histories():
    """>=20 valid days for the daily premium_max median, >=10 valid sessions for
    the time-of-day-matched window-premium median, and a zero denominator is NA
    rather than an infinity that would top every peer cell it appeared in."""
    ws = {"cls": np.array([1000.0] * 4), "short": np.array([300.0] * 4),
          "bull": np.array([0.0] * 4), "cnt": np.array([23.0] * 4),
          "bin_top": np.array([500.0] * 4), "print_top": np.array([300.0] * 4)}
    c = mi.components(
        ws,
        med_pmax=np.array([150.0, 150.0, 0.0, 150.0]),
        cnt_pmax=np.array([25.0, 19.0, 25.0, 25.0]),      # 19 < 20 -> NA
        vol_med=np.array([250.0, 250.0, 250.0, 0.0]),
        vol_cnt=np.array([12.0, 12.0, 12.0, 12.0]))
    assert np.isfinite(c["largest_print_ratio"][0])
    assert np.isnan(c["largest_print_ratio"][1])            # too few valid days
    assert np.isnan(c["largest_print_ratio"][2])            # zero denominator
    assert np.isnan(c["vol_max_ratio"][3])                  # zero denominator

    thin = mi.components(ws, np.array([150.0] * 4), np.array([25.0] * 4),
                         np.array([250.0] * 4), np.array([9.0] * 4))
    assert np.isnan(thin["vol_max_ratio"]).all()            # 9 < 10 sessions


def test_a_silent_window_has_no_shares_rather_than_a_zero_divide():
    ws = {k: np.array([0.0]) for k in
          ("cls", "short", "bull", "cnt", "bin_top", "print_top")}
    c = mi.components(ws, np.array([150.0]), np.array([25.0]),
                      np.array([250.0]), np.array([12.0]))
    assert np.isnan(c["short_dte_share"][0])
    assert np.isnan(c["concentration"][0])


def test_eligibility_is_twenty_fenced_classified_prints_in_the_window():
    """The registered threshold, set from the 07-24 geometry probe. Off by one
    here changes the population of every table in the study."""
    cls, short, bull, cnt, pmax = _bins(
        cls_by_bin={0: 100.0}, cnt_by_bin={0: 19, 1: 1})
    assert mi.window_stats(cls, short, bull, cnt, pmax, 0, 1)["cnt"][0] == 19
    assert mi.window_stats(cls, short, bull, cnt, pmax, 0, 6)["cnt"][0] == 20
    # the tool's own gate, so the constant and the comparison stay together
    assert (np.array([19.0]) >= mi.MIN_PRINTS)[0] == np.False_
    assert (np.array([20.0]) >= mi.MIN_PRINTS)[0] == np.True_


def test_group_max_is_the_per_cell_maximum_and_order_independent():
    key = np.array([2, 0, 2, 1, 0])
    val = np.array([5.0, 9.0, 7.0, 1.0, 3.0])
    out = mi.group_max(key, val, 4)
    assert list(out) == [9.0, 1.0, 7.0, 0.0]
    perm = np.array([3, 1, 4, 0, 2])
    assert list(mi.group_max(key[perm], val[perm], 4)) == [9.0, 1.0, 7.0, 0.0]
    assert list(mi.group_max(np.array([], int), np.array([]), 2)) == [0.0, 0.0]


# ---------------------------------------------------------------------------
# firing
# ---------------------------------------------------------------------------

def test_firing_is_p90_within_the_quintile_and_ties_fire():
    """1.14d's rule at the new grain: `np.percentile(..., 90)` per quintile, and
    `>=` so a tie at the cut fires. Ranking across quintiles instead would let
    the liquid tier's scale decide every firing."""
    vals = np.arange(1.0, 11.0)              # 1..10, p90 = 9.1
    q = np.zeros(10, np.int8)
    pool = np.ones(10, bool)
    assert list(np.flatnonzero(mi.fire_p90(vals, q, pool))) == [9]
    # two cells rank independently: each contributes its own top name
    q2 = np.array([0] * 5 + [1] * 5, np.int8)
    assert list(np.flatnonzero(mi.fire_p90(vals, q2, pool))) == [4, 9]
    # a name outside the pool never fires even when its value is the largest
    pool2 = pool.copy()
    pool2[9] = False
    assert not mi.fire_p90(vals, q, pool2)[9]


def test_names_without_a_quintile_are_not_peer_celled():
    """`_tier_quintiles` returns -1 with too little history. Those names must not
    form a pseudo-cell of their own."""
    vals = np.array([1.0, 2.0, 3.0])
    q = np.array([-1, -1, -1], np.int8)
    assert not mi.fire_p90(vals, q, np.ones(3, bool)).any()


# ---------------------------------------------------------------------------
# outcomes
# ---------------------------------------------------------------------------

def test_the_outcome_is_max_abs_cumulative_log_return_from_the_price_at_b():
    """Hand-computed. The base is the close of the last bar STRICTLY BEFORE b, so
    the outcome shares no bar with the feature window; max|x| must pick up an
    adverse excursion as readily as a favourable one."""
    closes = np.full((1, mi.RTH_BINS), np.nan)
    closes[0, 5] = 100.0                                  # the price at 10:00
    for b, px in zip(range(6, 12), (101, 99, 105, 100, 98, 100)):
        closes[0, b] = px
    meas, signed, ok = mi.outcome_measures(closes, expected_bars=7)
    bi, hi = 0, mi.HORIZONS.index("30m")                  # b=10:00, H=30m
    assert ok[0]
    assert meas[0, bi, hi] == pytest.approx(abs(np.log(105 / 100)))
    assert signed[0, bi, hi] == pytest.approx(np.log(100 / 100))

    # a purely adverse path: the magnitude measure still sees it
    down = np.full((1, mi.RTH_BINS), np.nan)
    down[0, 5] = 100.0
    for b, px in zip(range(6, 12), (99, 98, 97, 96, 95, 94)):
        down[0, b] = px
    m2, s2, _ = mi.outcome_measures(down, expected_bars=7)
    assert m2[0, bi, hi] == pytest.approx(abs(np.log(94 / 100)))
    assert s2[0, bi, hi] == pytest.approx(np.log(94 / 100))    # signed is negative
    assert s2[0, bi, hi] < 0


def test_a_horizon_the_boundary_cannot_reach_is_na_not_truncated():
    closes = np.full((1, mi.RTH_BINS), 100.0)
    closes[0, 60:] = np.arange(100.0, 100.0 + mi.RTH_BINS - 60)
    meas, _s, _ok = mi.outcome_measures(closes, expected_bars=mi.RTH_BINS)
    b_1430 = mi.BOUNDARY_BINS.index(60)
    assert np.isnan(meas[0, b_1430, mi.HORIZONS.index("2h")])
    assert np.isfinite(meas[0, b_1430, mi.HORIZONS.index("30m")])
    assert np.isfinite(meas[0, b_1430, mi.HORIZONS.index("close")])


def test_a_name_day_below_eighty_percent_of_the_session_grid_is_na_and_counted():
    """The registered halt/LULD rule. 78 expected bars needs >= 62.4, so 62 bars
    is NA and 63 is kept — and an early-close session is measured against its own
    grid rather than NA-ing every name in it."""
    thin = np.full((1, mi.RTH_BINS), np.nan)
    thin[0, :62] = 100.0
    assert not mi.outcome_measures(thin, mi.RTH_BINS)[2][0]
    thin[0, 62] = 100.0
    assert mi.outcome_measures(thin, mi.RTH_BINS)[2][0]
    # 42-bar early close: 40 bars is 95% of ITS grid, so the name-day survives
    half = np.full((1, mi.RTH_BINS), np.nan)
    half[0, :40] = 100.0
    assert mi.outcome_measures(half, expected_bars=42)[2][0]
    assert not mi.outcome_measures(half, expected_bars=78)[2][0]


def test_a_name_that_stops_trading_at_the_boundary_has_no_signed_return():
    """The direction test must not read "no bars after b" as a zero return —
    that would park a fabricated 0 in the middle of the sign distribution."""
    closes = np.full((1, mi.RTH_BINS), np.nan)
    closes[0, :6] = 100.0
    meas, signed, _ = mi.outcome_measures(closes, expected_bars=6)
    assert np.isnan(signed[0, 0, mi.HORIZONS.index("30m")])
    assert np.isnan(meas[0, 0, mi.HORIZONS.index("30m")])


# ---------------------------------------------------------------------------
# the t-1 rule
# ---------------------------------------------------------------------------

def test_the_ring_read_before_write_cannot_see_the_current_session():
    """The structural form of "all denominators use sessions <= t-1". If the
    order ever flips, a name's own spike enters its own normalizer and the
    component collapses toward 1.0 exactly when it should be extreme."""
    r = mi.Ring((1,), 20)
    for v in range(1, 11):
        r.push(np.array([float(v)], np.float32))
    med, cnt = r.median_count()             # read for session 11
    assert med[0] == pytest.approx(5.5) and cnt[0] == 10
    r.push(np.array([1000.0], np.float32))  # session 11's own value
    med2, cnt2 = r.median_count()           # read for session 12
    assert med2[0] == pytest.approx(6.0) and cnt2[0] == 11


def test_the_ring_forgets_beyond_the_lookback():
    r = mi.Ring((1,), 3)
    for v in (1.0, 2.0, 3.0, 100.0):
        r.push(np.array([v], np.float32))
    med, cnt = r.median_count()
    assert cnt[0] == 3 and med[0] == pytest.approx(3.0)   # the 1.0 aged out


def test_absent_sessions_are_nan_slots_not_skipped_slots():
    """A name with no prints must still consume a ring slot, or its "trailing 20
    sessions" silently stretches across months of absence."""
    r = mi.Ring((1,), 3)
    r.push(np.array([5.0], np.float32))
    r.push(np.array([np.nan], np.float32))
    r.push(np.array([7.0], np.float32))
    med, cnt = r.median_count()
    assert cnt[0] == 2 and med[0] == pytest.approx(6.0)


def test_the_daily_premium_max_normalizer_is_shifted_off_the_current_session():
    """`_roll_med60` windows end at t inclusive (the day-grain convention). 1.14f
    says t-1, so the panel is shifted once, here."""
    med = np.array([[1.0, 2.0, 3.0]])
    cnt = np.array([[10.0, 20.0, 30.0]])
    m, c = mi.shift_one_session(med, cnt)
    assert np.isnan(m[0, 0]) and c[0, 0] == 0
    assert list(m[0, 1:]) == [1.0, 2.0]
    assert list(c[0, 1:]) == [10.0, 20.0]


# ---------------------------------------------------------------------------
# bootstrap
# ---------------------------------------------------------------------------

def test_the_block_bootstrap_is_reproducible_under_the_registered_seed():
    a = mi.block_weights(40, 50, mi.BLOCK, np.random.default_rng(mi.SEED))
    b = mi.block_weights(40, 50, mi.BLOCK, np.random.default_rng(mi.SEED))
    assert np.array_equal(a, b)
    assert not np.array_equal(a, mi.block_weights(
        40, 50, mi.BLOCK, np.random.default_rng(mi.SEED + 1)))


def test_every_draw_resamples_whole_blocks_of_sessions():
    """Circular moving blocks of 5 SESSIONS: each draw's total weight is
    n_blocks*5, and the wrap-around is what keeps every session equally likely
    to be drawn (a non-circular version under-samples the tail)."""
    w = mi.block_weights(40, 200, 5, np.random.default_rng(mi.SEED))
    assert w.shape == (200, 40)
    assert (w.sum(axis=1) == 40).all()          # ceil(40/5) blocks x 5
    assert w.sum(axis=0).min() > 0              # no session is unreachable


def test_the_bootstrap_median_is_exact_not_a_histogram_approximation():
    """The chunked search must return EXACTLY the weighted median of the
    resampled multiset, with `np.median` semantics on both odd and even totals.
    Compared against the brute-force `np.repeat` construction it replaces."""
    rng = np.random.default_rng(7)
    for n in (1, 2, 9, 40, 501):
        vals = rng.normal(size=n)
        sess = rng.integers(0, 6, size=n)
        wmat = rng.integers(0, 4, size=(25, 6)).astype(float)
        got = mi.boot_medians(vals, sess, 6, wmat, chunk=4)
        for d in range(len(wmat)):
            multiset = np.repeat(vals, wmat[d][sess].astype(int))
            want = np.median(multiset) if len(multiset) else np.nan
            assert (np.isnan(got[d]) and np.isnan(want)) or \
                got[d] == pytest.approx(want), f"n={n} draw={d}"


def test_the_bootstrap_median_with_unit_weights_is_the_plain_median():
    vals = np.array([3.0, 1.0, 2.0, 10.0])
    got = mi.boot_medians(vals, np.zeros(4, int), 1, np.ones((1, 1)))
    assert got[0] == pytest.approx(np.median(vals))


def test_an_empty_pool_yields_no_interval_rather_than_a_zero():
    got = mi.boot_medians(np.array([]), np.array([], int), 3, np.ones((5, 3)))
    assert np.isnan(got).all()
    assert mi.ci(np.array([np.nan, np.nan])) == (None, None)


def test_a_star_means_the_interval_excludes_zero_on_either_side():
    assert mi.starred(0.1, 0.2) and mi.starred(-0.2, -0.1)
    assert not mi.starred(-0.1, 0.2)
    assert not mi.starred(None, None)


# ---------------------------------------------------------------------------
# the outcome store: read the real layout
# ---------------------------------------------------------------------------

def _write_npz(path: Path, session: date, per_sym: dict[str, dict[int, float]]):
    """One session in the exact layout `equity_bars_5min_backfill.write_session`
    produces: sorted `symbols` index table + parallel arrays keyed by `sym_idx`."""
    syms = sorted(per_sym)
    t, c, si = [], [], []
    open_ns = int(datetime.combine(session, dtime(9, 30), ET).timestamp()) \
        * 1_000_000_000
    for i, s in enumerate(syms):
        for b, px in sorted(per_sym[s].items()):
            t.append(open_ns + b * mi.BIN_NS)
            c.append(px)
            si.append(i)
    n = len(t)
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        path, t_ns=np.array(t, np.int64), open=np.array(c, np.float64),
        high=np.array(c, np.float64), low=np.array(c, np.float64),
        close=np.array(c, np.float64), volume=np.zeros(n, np.int64),
        sym_idx=np.array(si, np.int32), symbols=np.array(syms, dtype="U8"))


def test_the_bar_store_is_read_in_the_layout_the_backfill_writes(tmp_path):
    session = date(2026, 5, 15)
    _write_npz(tmp_path / f"{session.isoformat()}.npz", session,
               {"AAA": {0: 10.0, 1: 11.0, 77: 12.0}, "ZZZ": {0: 99.0}})
    closes, expected, n_sym = mi.load_bars_session(
        tmp_path / f"{session.isoformat()}.npz", {"AAA": 0}, 1)
    assert closes.shape == (1, mi.RTH_BINS)
    assert closes[0, 0] == 10.0 and closes[0, 1] == 11.0 and closes[0, 77] == 12.0
    assert np.isnan(closes[0, 2])
    assert n_sym == 1                       # ZZZ is outside the universe
    assert expected == 3                    # the session's own observed grid


def test_the_expected_bar_grid_never_exceeds_a_full_session(tmp_path):
    session = date(2026, 5, 15)
    _write_npz(tmp_path / f"{session.isoformat()}.npz", session,
               {"AAA": dict.fromkeys(range(mi.RTH_BINS), 10.0)})
    _c, expected, _n = mi.load_bars_session(
        tmp_path / f"{session.isoformat()}.npz", {"AAA": 0}, 1)
    assert expected == mi.RTH_BINS


# ---------------------------------------------------------------------------
# the session pass: first-fire, normalization, NA accounting
# ---------------------------------------------------------------------------

N_NAMES = 10
BI_1000, BI_1030, BI_1100 = 0, 1, 2


def _fake_tape(cls_by_name_bin, cnt=40.0):
    """A `tape_bins` stand-in: {name_idx: {bin: classified premium}}.

    Every populated bin also carries `cnt` prints so eligibility is satisfied,
    which keeps each test about the one rule it names.
    """
    def fn(session, name_index, n_names, na):
        shape = (n_names, mi.RTH_BINS)
        cls = np.zeros(shape)
        cnt_a = np.zeros(shape)
        for ni, bins in cls_by_name_bin.items():
            for b, v in bins.items():
                cls[ni, b] = v
                cnt_a[ni, b] = cnt
        short = cls * 0.5
        bull = cls * 0.5
        pmax = cls * 0.1
        active = cls.sum(axis=1) > 0
        na["prints_kept"] += int(cnt_a.sum())
        return cls, short, bull, cnt_a, pmax, active
    return fn


def _daily_panels(n_sessions):
    """Quintiles and a premium_max normalizer for `n_sessions` sessions, wide
    enough that every name is peer-celled and `largest_print_ratio` is defined."""
    quint = np.tile(np.arange(N_NAMES, dtype=np.int8) // 2, (n_sessions, 1)).T
    med_pmax = np.full((N_NAMES, n_sessions), 100.0)
    cnt_pmax = np.full((N_NAMES, n_sessions), 30.0)
    return quint, med_pmax, cnt_pmax


def _run(tmp_path, n_sessions, tape_fn, bars_fn, monkeypatch,
         start=date(2026, 1, 5)):
    """Drive `run_sessions` over synthetic sessions and return (table, na)."""
    monkeypatch.setattr(mi, "tape_bins", tape_fn)
    sessions, d = [], start
    while len(sessions) < n_sessions:
        if d.weekday() < 5:
            sessions.append(d)
        d = d.fromordinal(d.toordinal() + 1)
    names = [f"N{i:02d}" for i in range(N_NAMES)]
    dates = [s.isoformat() for s in sessions]
    for si, s in enumerate(sessions):
        _write_npz(tmp_path / f"{s.isoformat()}.npz", s, bars_fn(si, names))
    quint, med_pmax, cnt_pmax = _daily_panels(n_sessions)
    na = mi.new_na_counters()
    table = mi.run_sessions(sessions, names, dates, quint, med_pmax, cnt_pmax,
                            tmp_path, na)
    return table, na, sessions, names


def _zigzag_bars(k=1.0, px=100.0):
    """Every name, every bin, a small alternating move of `k` units.

    Deliberately NOT a flat tape: a flat name-day has a max |log return| of
    exactly 0, so its trailing median normalizer is 0 and every row is correctly
    NA. Fixtures need real motion or they test the NA path by accident.
    """
    def fn(si, names):
        return {n: {b: (px * np.exp(k * 0.001) if b % 2 else px)
                    for b in range(mi.RTH_BINS)} for n in names}
    return fn


def test_a_name_that_fires_contributes_one_row_and_leaves_the_complement(
        tmp_path, monkeypatch):
    """The registered first-fire rule and the ambiguity resolution recorded in
    AMBIGUITIES['complement_boundaries']: name 9 is engineered to fire at 10:30
    and 11:00 but not 10:00. It must appear ONCE as fired (10:30) and must not
    appear in the complement at 11:00 *or at the earlier 10:00* — "never fired
    that session" is a session-wide property."""
    # `_daily_panels` puts names (8, 9) in one quintile, so p90 of two distinct
    # values fires exactly the higher one. Shares like concentration are
    # scale-invariant and would tie every identical name into firing together, so
    # this drives `largest_print_ratio`, whose numerator carries magnitude.
    # W=30 windows: 10:00 = bins 0-5, 10:30 = bins 6-11, 11:00 = bins 12-17.
    tape = {n: dict.fromkeys(range(mi.RTH_BINS), 100.0 + 10.0 * n)
            for n in range(N_NAMES)}
    tape[8] = dict.fromkeys(range(mi.RTH_BINS), 180.0)      # ratio 0.18
    tape[9] = {b: (500.0 if b >= 6 else 100.0)              # 0.10 then 0.50
               for b in range(mi.RTH_BINS)}
    table, na, sessions, _names = _run(
        tmp_path, 14, _fake_tape(tape), _zigzag_bars(), monkeypatch)

    wi, fi = mi.WINDOWS.index(30), mi.FEATS.index("largest_print_ratio")
    last = table["sess"] == table["sess"].max()
    def row(name, bi):
        m = last & (table["name"] == name) & (table["bi"] == bi)
        assert m.sum() == 1
        return table["fired"][m, wi, fi][0], table["excl"][m, wi, fi][0]

    assert row(9, BI_1030) == (True, False)     # the FIRST firing boundary
    assert row(9, BI_1100) == (False, True)     # later boundary: neither pool
    assert row(9, BI_1000) == (False, True)     # earlier boundary: not complement
    # a quiet peer stays in the complement at every boundary
    for bi in (BI_1000, BI_1030, BI_1100):
        assert row(0, bi) == (False, False)
    assert na["rows_excluded_later_boundary"] > 0


def test_the_outcome_normalizer_excludes_the_session_being_measured(
        tmp_path, monkeypatch):
    """End-to-end t-1 proof on the outcome side. Ten prior sessions carry moves
    whose median is 5.5 units; session 11 moves 100. The normalized value must be
    100/5.5. If session 11 leaked into its own normalizer the median would be 6.0
    and the answer 100/6 — the even-count construction is what makes the two
    distinguishable at all."""
    scale = {}

    def bars(si, names):
        # a name-day whose max |log return| from every boundary is k * unit
        k = (si + 1) if si < 10 else 100
        unit = 0.001
        out = {}
        for n in names:
            px = {b: 100.0 for b in range(mi.RTH_BINS)}
            for b in range(mi.RTH_BINS):
                if b % 2:
                    px[b] = 100.0 * np.exp(k * unit)
            out[n] = px
        scale[si] = k * unit
        return out

    tape = {n: {b: 100.0 for b in range(mi.RTH_BINS)} for n in range(N_NAMES)}
    table, na, sessions, _n = _run(
        tmp_path, 11, _fake_tape(tape), bars, monkeypatch)

    hi = mi.HORIZONS.index("2h")
    last = table["sess"] == 10
    vals = table["norm"][last, hi]
    vals = vals[np.isfinite(vals)]
    assert len(vals)
    expected = scale[10] / float(np.median([scale[i] for i in range(10)]))
    assert vals[0] == pytest.approx(expected, rel=1e-4)
    leaked = scale[10] / float(np.median([scale[i] for i in range(11)]))
    assert not np.isclose(vals[0], leaked, rtol=1e-3)


def test_a_name_without_ten_prior_sessions_has_no_normalized_outcome(
        tmp_path, monkeypatch):
    """<10 valid trailing sessions is NA, counted — never a raw move smuggled in
    as if it were normalized."""
    tape = {n: {b: 100.0 for b in range(mi.RTH_BINS)} for n in range(N_NAMES)}
    table, na, _s, _n = _run(
        tmp_path, 9, _fake_tape(tape), _zigzag_bars(), monkeypatch)
    assert len(table["sess"]) == 0            # nothing is normalizable yet
    assert na["rows_na_outcome_normalizer"] > 0
    assert na["sessions_analysed"] == 9


def test_a_name_with_no_bars_is_na_counted_and_never_dropped_in_silence(
        tmp_path, monkeypatch):
    """The registration's rule for the store: "names with missing/insufficient
    bars are NA-counted, disclosed, never silently dropped"."""
    def bars(si, names):
        return {n: dict.fromkeys(range(mi.RTH_BINS), 100.0)
                for n in names if n != "N03"}
    tape = {n: {b: 100.0 for b in range(mi.RTH_BINS)} for n in range(N_NAMES)}
    table, na, _s, names = _run(
        tmp_path, 12, _fake_tape(tape), bars, monkeypatch)
    assert 3 not in set(table["name"].tolist())
    assert na["namedays_with_bars"] == 12 * (N_NAMES - 1)
    assert na["rows_na_outcome_measure"] + na["rows_na_outcome_normalizer"] > 0


def test_a_session_with_no_bar_file_is_counted_and_does_not_shift_the_rings(
        tmp_path, monkeypatch):
    """A missing session must still consume a ring slot (see the NaN-slot test),
    so it is skipped for rows but never skipped for trailing bookkeeping."""
    tape = {n: {b: 100.0 for b in range(mi.RTH_BINS)} for n in range(N_NAMES)}
    monkeypatch.setattr(mi, "tape_bins", _fake_tape(tape))
    sessions = [date(2026, 1, 5), date(2026, 1, 6), date(2026, 1, 7)]
    names = [f"N{i:02d}" for i in range(N_NAMES)]
    _write_npz(tmp_path / "2026-01-05.npz", sessions[0],
               {n: {0: 100.0} for n in names})
    quint, mp, cp = _daily_panels(3)
    na = mi.new_na_counters()
    mi.run_sessions(sessions, names, [s.isoformat() for s in sessions],
                    quint, mp, cp, tmp_path, na)
    assert na["sessions_no_bars"] == 2


def test_na_accounting_reaches_the_output_and_names_every_stage(
        tmp_path, monkeypatch):
    tape = {n: {b: 100.0 for b in range(mi.RTH_BINS)} for n in range(N_NAMES)}
    _table, na, _s, _n = _run(
        tmp_path, 12, _fake_tape(tape), _zigzag_bars(), monkeypatch)
    for key in ("sessions_analysed", "namedays_bars_below_coverage",
                "cells_ineligible_prints", "cells_na_vol_max_ratio",
                "rows_na_outcome_normalizer", "rows_emitted",
                "rows_excluded_later_boundary"):
        assert key in na, key
    assert na["rows_emitted"] > 0


# ---------------------------------------------------------------------------
# the analysis mask, end to end through main()
# ---------------------------------------------------------------------------

def _corpus_db(path: Path, names, dates, corpus_sessions):
    from db.flow_corpus import init_db
    init_db(path)
    con = sqlite3.connect(path)
    rng = np.random.default_rng(3)
    for i, n in enumerate(["SPY", *names]):
        for t, d in enumerate(dates):
            con.execute(
                "INSERT OR REPLACE INTO equity_bars "
                "(symbol, date, o, h, l, c, v, adjustment, source) "
                "VALUES (?,?,?,?,?,?,?,'split','test')",
                (n, d, 100.0, 101.0, 99.0, 100.0,
                 float(1_000_000 * (i + 1)) + rng.integers(0, 1000)))
    for d in corpus_sessions:
        con.execute(
            "INSERT OR REPLACE INTO corpus_sessions "
            "(date, prints, rows, underlyings, unresolved_prints, "
            "no_spot_prints, schema_version, ingested_at) "
            "VALUES (?,1,1,1,0,0,7,'now')", (d,))
    con.commit()
    con.close()


def test_a_session_past_the_train_end_is_never_read_into_the_analysis(
        tmp_path, monkeypatch):
    """The hardest rule to verify by reading code and the one with no second
    chance: the test split (2026-06-01 -> 2026-07-25) lands on disk, and the
    discovery run must not touch it. A post-train session is placed in
    `corpus_sessions` WITH a bar file, and `tape_bins` records what it is asked
    for."""
    dates, d = [], date(2026, 3, 2)
    while len(dates) < 90:
        if d.weekday() < 5:
            dates.append(d.isoformat())
        d = d.fromordinal(d.toordinal() + 1)
    names = [f"N{i:02d}" for i in range(N_NAMES)]
    train = [x for x in dates if x <= mi.TRAIN_END][-16:]
    after = next(x for x in dates if x > mi.TRAIN_END)
    corpus = [*train, after]

    db = tmp_path / "corpus.db"
    _corpus_db(db, names, dates, corpus)
    bars = tmp_path / "bars"
    moving = _zigzag_bars()
    for ds in corpus:                       # the test tail IS on disk
        _write_npz(bars / f"{ds}.npz", date.fromisoformat(ds),
                   moving(0, names))

    asked: list[str] = []
    # A per-name SHAPE gradient, not a magnitude one: shares are scale-invariant,
    # so uniform names would tie at p90 and all fire, leaving no complement. An
    # alternating profile makes `concentration` rise with the name index inside
    # every window length, so each 2-name quintile splits into one fired + one
    # complement and the statistics path runs for real.
    tape = {n: {b: (100.0 + 30.0 * n if b % 2 else 100.0)
                for b in range(mi.RTH_BINS)} for n in range(N_NAMES)}
    inner = _fake_tape(tape)

    def spy(session, name_index, n_names, na):
        asked.append(session.isoformat())
        return inner(session, name_index, n_names, na)

    monkeypatch.setattr(mi, "tape_bins", spy)
    monkeypatch.setattr(mi, "N_BOOT", 120)
    monkeypatch.setattr(mi, "load_universe", lambda db, fenced: list(names))
    out = tmp_path / "out.json"
    monkeypatch.setattr("sys.argv", [
        "mover_intraday", "--corpus-db", str(db), "--bars-dir", str(bars),
        "--earnings-db", str(tmp_path / "missing-earnings.db"),
        "--join-ledger", str(tmp_path / "missing-ledger.json"),
        "--out", str(out)])
    assert mi.main() == 0

    assert after not in asked, "a post-train-end session entered the analysis"
    assert asked and set(asked) <= set(train)
    payload = json.loads(out.read_text())
    assert payload["meta"]["train_end"] == mi.TRAIN_END
    assert payload["na_accounting"]["sessions_after_train_end"] == 1
    assert payload["na_accounting"]["sessions_on_disk"] == len(corpus)
    assert payload["na_accounting"]["sessions_train"] == len(train)
    # the output carries the NA ledger and the recorded ambiguities
    assert payload["na_accounting"]["rows_emitted"] > 0
    assert "complement_boundaries" in payload["meta"]["ambiguities"]
    assert payload["meta"]["not_a_backtest"]
    # "THIS PHASE IS NOT A BACKTEST": no entry/exit/P&L/hit-rate may appear in
    # the RESULTS. (The meta block is prose about the study and says the words in
    # order to forbid them, so it is deliberately not scanned.)
    blob = json.dumps({k: v for k, v in payload.items() if k != "meta"}).lower()
    for banned in ("pnl", "p&l", "slippage", "hit_rate", "hit_share",
                   "win_rate", "entry_price", "position_size", "return_pct"):
        assert banned not in blob, banned
    # what the results DO carry: medians, intervals, and NA counts
    arm = payload["arms"]["all_train"]
    assert arm["n_sessions"] == len(train)
    assert any(c.get("star") is not None for c in arm["cells"])
    assert {"median_fired", "median_complement", "diff", "ci"} <= set(
        next(c for c in arm["cells"] if "median_fired" in c))


def test_a_missing_join_ledger_disables_the_robustness_arm_rather_than_faking_it(
        tmp_path):
    assert mi.load_join_subset(tmp_path / "nope.json") is None
    led = tmp_path / "join_ledger.json"
    led.write_text(json.dumps({
        "2026-01-05": {"join_rate_dbn": 0.991, "floor_pass": True},
        "2026-01-06": {"join_rate_dbn": 0.770, "floor_pass": False}}))
    assert mi.load_join_subset(led) == {"2026-01-05"}


def test_the_earnings_gate_is_the_1_14e_ten_session_window(tmp_path):
    """Reused semantics, not a new gate: a filing 10 sessions ahead is inside,
    11 is outside, and a name with no EDGAR history is UNKNOWN rather than
    "not gated" (it is excluded from the split, with the count disclosed)."""
    db = tmp_path / "earnings.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE earnings (symbol TEXT, cik TEXT, date TEXT)")
    con.execute("INSERT INTO earnings VALUES ('AAA','1','2026-01-20')")
    con.commit()
    con.close()
    dates = [f"2026-01-{d:02d}" for d in range(5, 26)]   # index 0..20
    gate, known = mi.load_gate(db, ["AAA", "BBB"], dates)
    fi = dates.index("2026-01-20")
    assert known[0] and not known[1]
    assert gate[0, fi - mi.K_GATE]            # exactly 10 sessions ahead
    assert not gate[0, fi - mi.K_GATE - 1]    # 11 ahead is outside
    assert not gate[0, fi]                    # the filing session itself
    assert not gate[1].any()                  # unknown names are never gated
    assert mi.load_gate(tmp_path / "absent.db", ["AAA"], dates)[1].sum() == 0
