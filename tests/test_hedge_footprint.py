"""Hermetic tests for tools/hedge_footprint.py (roadmap 3.2, spec 2026-08-01).

No network, no `data/` dependency: every fixture is synthetic or a tmp_path.

⚠️ Timestamp fixtures use pure INTEGER nanosecond arithmetic. float64 has 53
bits of mantissa and epoch-ns is ~1.8e18, so a float round-trip rounds by ~256ns
— the same trap the 3.1 fixture documents.
"""

from __future__ import annotations

import json
from datetime import date

import numpy as np
import pytest

import tools.hedge_footprint as hf
from tools.hedge_footprint import (
    D_MIN,
    G_NS,
    N_CONTROLS,
    PageCapError,
    _multileg,
    _paged_get,
    _profile,
    assert_window_volume,
    bs_price,
    build_condition_table,
    cluster_permutation_p,
    condition_masks,
    control_sessions,
    detectability,
    excess,
    expected_29s_volume,
    fetch_trades,
    fit_delta,
    group_side,
    hedge_aligned,
    hedge_shares,
    implied_vol,
    in_release_fence,
    in_trading_window,
    look_a,
    look_b,
    neighbour_premium,
    ols_two_term,
    option_tick,
    qualifying_sessions,
    session_midnight_ns,
    sign_midpoint,
    sign_tick_rule,
    single_leg_groups,
    statistic_s,
    theil_sen,
    verdict,
)

NS = 1_000_000_000
T0 = 1_800_000_000 * NS  # arbitrary epoch anchor, far past float64 ns precision


# ===========================================================================
# the falsifier's sign — the spec keeps these four as unit tests
# ===========================================================================

class TestHedgeSign:
    def test_customer_buys_call_dealer_buys_stock(self):
        # dealer is short the call -> negative delta -> BUYS stock
        assert hedge_shares(+1, +0.60, 500) == pytest.approx(30_000.0)

    def test_customer_buys_put_dealer_sells_stock(self):
        # dealer is short the put -> positive delta -> SELLS stock
        assert hedge_shares(+1, -0.40, 500) == pytest.approx(-20_000.0)

    def test_customer_sells_call_reverses(self):
        assert hedge_shares(-1, +0.60, 500) == pytest.approx(-30_000.0)

    def test_customer_sells_put_reverses(self):
        assert hedge_shares(-1, -0.40, 500) == pytest.approx(20_000.0)

    def test_size_is_delta_times_contracts_times_hundred(self):
        # the prediction is a SIZE, not a direction — that is what falsifies it
        assert abs(hedge_shares(+1, -0.25, 1_234)) == pytest.approx(0.25 * 1_234 * 100)


# ===========================================================================
# IV inversion from the quote midpoint, and Δ-stability
# ===========================================================================

class TestImpliedVol:
    def test_bisection_round_trip(self):
        px = bs_price(100.0, 100.0, 0.25, 0.30, False)
        assert implied_vol(px, 100.0, 100.0, 0.25, False) == pytest.approx(0.30, abs=1e-4)

    def test_bisection_round_trip_put(self):
        px = bs_price(100.0, 110.0, 0.5, 0.45, True)
        assert implied_vol(px, 100.0, 110.0, 0.5, True) == pytest.approx(0.45, abs=1e-4)

    def test_sub_intrinsic_quote_is_dropped_not_clamped(self):
        # deep ITM call: discounted intrinsic ~50.2, a 45.00 mid is unpriceable
        fit = fit_delta(45.0, 100.0, 50.0, 0.1, False)
        assert fit.ok is False and fit.reason == "sub_intrinsic"
        assert implied_vol(45.0, 100.0, 50.0, 0.1, False) is None

    def test_above_the_arbitrage_ceiling_is_no_iv(self):
        # 60s to expiry, ATM: no vol in [1e-4, 5] reaches a $0.50 mid
        fit = fit_delta(0.50, 100.0, 100.0, 60.0 / (365 * 86400), False)
        assert fit.ok is False and fit.reason == "no_iv"

    def test_delta_stability_drop(self):
        # 10 minutes to expiry, $0.02 mid, $0.01 tick: ±1 tick swings |Δ| 0.055
        fit = fit_delta(0.02, 100.0, 100.2, 600.0 / (365 * 86400), False)
        assert fit.ok is False and fit.reason == "unstable"
        assert fit.delta_swing > 0.05

    def test_delta_stability_keeps_a_well_conditioned_quote(self):
        px = bs_price(100.0, 100.0, 0.25, 0.30, False)
        fit = fit_delta(px, 100.0, 100.0, 0.25, False)
        assert fit.ok is True and fit.reason == ""
        assert fit.delta_swing < 0.05
        assert fit.delta == pytest.approx(0.556, abs=0.01)

    def test_put_delta_is_negative(self):
        px = bs_price(100.0, 100.0, 0.25, 0.30, True)
        assert fit_delta(px, 100.0, 100.0, 0.25, True).delta < 0

    def test_option_tick_penny_pilot_break(self):
        assert option_tick(2.99) == 0.01
        assert option_tick(3.00) == 0.05


# ===========================================================================
# the single-leg TRIPLE test, side unanimity, isolation
# ===========================================================================

class TestSingleLeg:
    def _case(self, cond, heur):
        gid = np.array([0, 0, 1, 1, 2, 2, 3, 3], dtype=np.int64)
        return single_leg_groups(gid, 4, np.array(cond, dtype="U1"),
                                 np.array(heur, dtype=bool))

    def test_clean_group_is_single_leg(self):
        ok, unjoined, pkg = self._case(["a", "a", "a", "a", "a", "a", "a", "a"],
                                       [False] * 8)
        assert ok.tolist() == [True] * 4
        assert unjoined.tolist() == [False] * 4

    def test_unjoined_print_certifies_nothing(self):
        # '' is not in PACKAGE_CODES, so a two-clause fence would PASS this
        ok, unjoined, _ = self._case(["a", "", "a", "a", "a", "a", "a", "a"],
                                     [False] * 8)
        assert ok[0] is np.False_ and unjoined[0] is np.True_
        assert ok[1] is np.True_

    def test_package_code_rejected(self):
        # 'f' is a multi-leg package code; 'k' is a stock-option combo
        ok, _, pkg = self._case(["a", "f", "a", "k", "a", "a", "a", "a"],
                                [False] * 8)
        assert ok.tolist() == [False, False, True, True]
        assert pkg.tolist() == [True, True, False, False]

    def test_ns_collision_heuristic_rejected(self):
        ok, _, pkg = self._case(["a"] * 8, [False, False, True, True, False, False,
                                            False, False])
        assert ok.tolist() == [True, False, True, True]
        assert pkg[1] is np.True_

    def test_heuristic_flags_different_instrument_at_same_ns(self):
        # the corpus rule: same ns + DIFFERENT instrument = package;
        # same ns + same instrument = one sweep across venues, not contamination
        ts = np.array([T0, T0, T0 + NS, T0 + NS], dtype=np.int64)
        iid = np.array([1, 2, 7, 7], dtype=np.int64)
        assert _multileg(ts, iid).tolist() == [True, True, False, False]


class TestGroupSide:
    def _side(self, side):
        gid = np.array([0, 0], dtype=np.int64)
        return group_side(gid, 1, np.array(side, dtype=np.int8))[0]

    def test_unanimous_buy(self):
        assert self._side([0, 0]) == 0

    def test_unanimous_sell(self):
        assert self._side([1, 1]) == 1

    def test_mixed_is_unusable(self):
        assert self._side([0, 1]) == -1

    def test_unclassified_leg_is_unusable(self):
        assert self._side([0, 2]) == -1

    def test_wide_spread_leg_is_unusable(self):
        assert self._side([0, 3]) == -1


class TestIsolation:
    def test_neighbour_premium_excludes_the_event_itself(self):
        ts = np.array([T0 - 60 * NS, T0, T0 + 60 * NS, T0 + 600 * NS], dtype=np.int64)
        prem = np.array([10_000.0, 100_000.0, 5_000.0, 999_999.0])
        out = neighbour_premium(ts, prem)
        # the event at T0 sees the ±120s neighbours only, never its own premium
        assert out[1] == pytest.approx(15_000.0)
        # the far print is outside every ±120s window
        assert out[3] == pytest.approx(0.0)

    def test_twenty_small_prints_break_isolation(self):
        ts = (T0 + np.arange(20, dtype=np.int64) * NS)
        prem = np.full(20, 10_000.0)
        ts = np.concatenate([[T0 - 30 * NS], ts])
        prem = np.concatenate([[25_000.0], prem])
        out = neighbour_premium(ts, prem)
        assert out[0] > 0.25 * 25_000.0


# ===========================================================================
# fences
# ===========================================================================

class TestFences:
    def test_trading_window_edges(self):
        assert not in_trading_window(9 * 3600 + 34 * 60 + 59)
        assert in_trading_window(9 * 3600 + 35 * 60)
        assert in_trading_window(15 * 3600 + 55 * 60)
        assert not in_trading_window(15 * 3600 + 55 * 60 + 1)

    def test_release_fence_both_windows(self):
        assert in_release_fence(9 * 3600 + 55 * 60)
        assert in_release_fence(10 * 3600 + 5 * 60)
        assert not in_release_fence(10 * 3600 + 5 * 60 + 1)
        assert in_release_fence(13 * 3600 + 55 * 60)
        assert in_release_fence(14 * 3600 + 5 * 60)
        assert not in_release_fence(12 * 3600)

    def test_release_fence_is_vectorized(self):
        sec = np.array([9 * 3600 + 56 * 60, 11 * 3600, 14 * 3600])
        assert in_release_fence(sec).tolist() == [True, False, True]

    def test_session_midnight_is_dst_honest(self):
        # 09:35 ET is 13:35 UTC in summer, 14:35 UTC in winter
        summer = session_midnight_ns(date(2025, 8, 1)) + (9 * 3600 + 35 * 60) * NS
        winter = session_midnight_ns(date(2025, 12, 1)) + (9 * 3600 + 35 * 60) * NS
        assert str(np.datetime64(summer, "ns")).endswith("T13:35:00.000000000")
        assert str(np.datetime64(winter, "ns")).endswith("T14:35:00.000000000")


class TestSessionPool:
    def _tree(self, tmp_path, sessions):
        tcbbo = tmp_path / "opra_tcbbo"
        labels = tmp_path / "alpaca_conditions"
        tcbbo.mkdir()
        labels.mkdir()
        ledger = {}
        for iso, size, capture, rate in sessions:
            (tcbbo / f"{iso}.dbn.zst").write_bytes(b"\0" * size)
            if capture:
                (labels / f"{iso}.json").write_text("{}")
                (labels / f"{iso}.npz").write_bytes(b"\0")
            if rate is not None:
                ledger[iso] = {"join_rate_dbn": rate}
        (labels / "join_ledger.json").write_text(json.dumps(ledger))
        return tcbbo, labels

    def test_half_day_and_join_floor_and_window(self, tmp_path):
        big = 2_000_000
        tcbbo, labels = self._tree(tmp_path, [
            ("2025-12-01", big, True, 0.99),        # qualifies
            ("2025-11-28", big, True, 0.99),        # half-day (post-Thanksgiving)
            ("2025-12-02", big, True, 0.50),        # below the 0.98 join floor
            ("2025-12-03", big, False, 0.99),       # no completed capture
            ("2025-12-04", 87, True, 0.99),         # holiday stub file
            ("2026-06-15", big, True, 0.99),        # outside the training window
        ])
        pool, stats = qualifying_sessions(tcbbo_dir=tcbbo, labels_dir=labels)
        assert pool == [date(2025, 12, 1)]
        assert stats["half_day"] == 1
        assert stats["below_join_floor"] == 1
        assert stats["no_capture"] == 1
        assert stats["qualified"] == 1


# ===========================================================================
# detectability
# ===========================================================================

class TestDetectability:
    def test_expected_is_median_minute_scaled_to_29s(self):
        vols = np.array([1_000.0, 2_000.0, 3_000.0])
        assert expected_29s_volume(vols) == pytest.approx(2_000.0 * 29.1 / 60.0)

    def test_d_is_hedge_over_expected(self):
        expected = expected_29s_volume(np.array([2_000.0]))
        assert detectability(3 * expected, expected) == pytest.approx(3.0)
        assert detectability(3 * expected, expected) >= D_MIN

    def test_median_not_mean_so_one_open_spike_cannot_carry_it(self):
        # the open runs 15-40x ADV/780; a median over same-clock-minute bars is
        # what makes the gate time-of-day honest
        vols = np.array([100.0, 100.0, 100.0, 100.0, 40_000.0])
        assert expected_29s_volume(vols) == pytest.approx(100.0 * 29.1 / 60.0)

    def test_zero_expected_is_nan_not_infinity(self):
        assert np.isnan(detectability(1_000.0, 0.0))
        assert np.isnan(expected_29s_volume(np.array([])))


# ===========================================================================
# signing
# ===========================================================================

class TestSigning:
    def _quotes(self):
        return (np.array([T0, T0 + 10 * NS], dtype=np.int64),
                np.array([9.90, 10.90]), np.array([10.10, 11.10]))

    def test_midpoint_rule_signs_and_at_mid_is_unsigned(self):
        qt, bid, ask = self._quotes()
        t = np.array([T0 - NS, T0 + NS, T0 + 2 * NS, T0 + 3 * NS, T0 + 11 * NS],
                     dtype=np.int64)
        px = np.array([10.00, 10.15, 10.00, 9.95, 11.05])
        s = sign_midpoint(t, px, qt, bid, ask)
        # no quote yet -> unsigned; then above mid, AT mid (unsigned), below mid;
        # the last trade signs off the SECOND quote (prevailing, not the first)
        assert s.tolist() == [0, 1, 0, -1, 1]

    def test_crossed_or_empty_quote_is_unsigned(self):
        qt = np.array([T0], dtype=np.int64)
        s = sign_midpoint(np.array([T0 + NS], dtype=np.int64), np.array([10.0]),
                          qt, np.array([11.0]), np.array([10.0]))
        assert s.tolist() == [0]
        s2 = sign_midpoint(np.array([T0], dtype=np.int64), np.array([10.0]),
                           np.array([], dtype=np.int64), np.array([]), np.array([]))
        assert s2.tolist() == [0]

    def test_tick_rule_is_a_secondary_and_carries_the_last_change(self):
        px = np.array([10.0, 10.0, 10.1, 10.05, 10.05])
        assert sign_tick_rule(px).tolist() == [0, 0, 1, -1, -1]


# ===========================================================================
# S / E / Z
# ===========================================================================

class TestStatistics:
    def test_s_is_post_minus_pre_on_integer_ns(self):
        t = np.array([T0 - NS, T0, T0 + NS], dtype=np.int64)
        signed = np.array([-50.0, 100.0, 200.0])
        # 16g = 29.12s, so all three prints are inside; post = [T, T+h)
        assert statistic_s(t, signed, T0, 16 * G_NS) == pytest.approx(350.0)

    def test_window_edges_are_half_open_so_nothing_double_counts(self):
        h = 16 * G_NS
        t = np.array([T0 - h, T0 - 1, T0, T0 + h - 1, T0 + h], dtype=np.int64)
        signed = np.array([1.0, 2.0, 4.0, 8.0, 16.0])
        # pre = [T-h, T) -> 1+2 = 3; post = [T, T+h) -> 4+8 = 12; the T+h print is out
        assert statistic_s(t, signed, T0, h) == pytest.approx(9.0)

    def test_excess_uses_median_and_mad_of_the_controls(self):
        ctrl = np.array([-2.0, -1.0, 0.0, 1.0, 2.0])
        ex = excess(10.0, ctrl)
        assert ex.median == pytest.approx(0.0)
        assert ex.mad == pytest.approx(1.0)
        assert ex.e == pytest.approx(10.0)
        assert ex.z == pytest.approx(10.0)

    def test_degenerate_control_set_yields_nan_not_infinity(self):
        assert np.isnan(excess(10.0, np.zeros(20)).z)
        assert np.isnan(excess(10.0, np.array([])).z)


# ===========================================================================
# Look A — the size test, and why the direction-only alternative fails it
# ===========================================================================

def _cut(abs_h, z, h_sign, clusters=None):
    n = len(abs_h)
    cl = clusters if clusters is not None else np.array([f"C{i}" for i in range(n)])
    return {"abs_h": np.asarray(abs_h, float), "value": np.asarray(z, float),
            "h_sign": np.asarray(h_sign, float), "underlying": cl, "cluster": cl,
            "abs_delta": np.full(n, 0.5), "shares": np.asarray(abs_h, float) * 2.0,
            "records": []}


class TestLookAAlignment:
    def _h(self, n=60):
        abs_h = np.linspace(1_000.0, 20_000.0, n)
        h_sign = np.where(np.arange(n) % 2 == 0, 1.0, -1.0)
        return abs_h, h_sign

    def test_direction_only_synthetic_is_flat_with_a_positive_intercept(self):
        # news/momentum reaction: a constant hedge-aligned excess, no size scaling
        abs_h, h_sign = self._h()
        z = 2.0 * h_sign                       # E = c*sign(H), MAD = 1
        slope, intercept = theil_sen(abs_h, hedge_aligned(z, h_sign))
        assert slope == pytest.approx(0.0, abs=1e-12)
        assert intercept == pytest.approx(2.0)
        res = look_a(_cut(abs_h, z, h_sign), n_perm=200, n_boot=200)
        assert res["pass"] is False           # AMBIGUOUS territory, never CONFIRMED

    def test_true_hedge_synthetic_recovers_the_slope(self):
        abs_h, h_sign = self._h()
        beta = 0.5
        z = beta * abs_h * h_sign              # E = beta*H, MAD = 1
        slope, intercept = theil_sen(abs_h, hedge_aligned(z, h_sign))
        assert slope == pytest.approx(beta, rel=1e-9)
        assert intercept == pytest.approx(0.0, abs=1e-6)
        res = look_a(_cut(abs_h, z, h_sign), n_perm=200, n_boot=200)
        assert res["pass"] is True
        assert res["slope"] == pytest.approx(beta, rel=1e-6)
        assert res["ci95"][0] > 0

    def test_true_hedge_with_scatter_clears_the_cluster_permutation(self):
        # a NOISELESS Ẽ = β|H| is degenerate for the permutation null: every
        # pairwise Theil–Sen slope then has |slope| ≥ β, so the null is bimodal
        # at ±β and p ≈ 0.5 by construction. Real scatter is what makes the test
        # informative, so the significance leg is asserted on a scattered one.
        abs_h, h_sign = self._h()
        beta = 0.5
        z = (beta * abs_h + np.random.default_rng(11).normal(0, 3_000, len(abs_h))) * h_sign
        res = look_a(_cut(abs_h, z, h_sign), n_perm=2_000, n_boot=400)
        assert res["slope"] == pytest.approx(beta, rel=0.2)
        assert res["pass"] is True
        assert res["permutation_p"] < 0.01

    def test_signed_h_regression_would_have_passed_on_direction_alone(self):
        # why the registered form is Ẽ vs |H| and not Ẽ vs signed H
        abs_h, h_sign = self._h()
        z = 2.0 * h_sign
        naive_slope, _ = theil_sen(abs_h * h_sign, z)
        assert naive_slope > 0                 # the draft's form passes on nothing


class TestLookAPrimeDiscriminator:
    """Common information scales with SIZE traded; only a delta hedge scales
    with Δ holding contracts fixed — so b (the |H| coefficient) is the test."""

    def _panel(self, n=80, seed=3):
        rng = np.random.default_rng(seed)
        contracts = rng.uniform(100, 2_000, n)
        delta = rng.uniform(0.15, 0.85, n)
        return contracts * 100.0, contracts * 100.0 * delta

    def test_size_only_effect_leaves_b_near_zero(self):
        shares, abs_h = self._panel()
        aligned = 0.001 * shares
        _, a_coef, b = ols_two_term(aligned, shares, abs_h)
        assert a_coef == pytest.approx(0.001, abs=1e-6)
        assert b == pytest.approx(0.0, abs=1e-6)

    def test_delta_scaled_effect_makes_b_positive(self):
        shares, abs_h = self._panel()
        aligned = 0.002 * abs_h
        _, a_coef, b = ols_two_term(aligned, shares, abs_h)
        assert b == pytest.approx(0.002, abs=1e-6)
        assert a_coef == pytest.approx(0.0, abs=1e-6)


# ===========================================================================
# Look B — the stacked g-bin profile, as a test
# ===========================================================================

class TestLookBProfile:
    def test_bin_k_is_the_first_post_bin(self):
        k = 2
        t = np.array([T0 - 1, T0, T0 + G_NS, T0 + 2 * G_NS - 1], dtype=np.int64)
        signed = np.array([1.0, 2.0, 4.0, 8.0])
        prof = _profile(t, signed, T0, k)
        assert len(prof) == 2 * k
        assert prof.tolist() == [0.0, 1.0, 2.0, 12.0]

    def test_prints_outside_the_profile_are_dropped(self):
        k = 2
        t = np.array([T0 - 3 * G_NS, T0 + 2 * G_NS], dtype=np.int64)
        prof = _profile(t, np.array([5.0, 5.0]), T0, k)
        assert prof.sum() == 0.0

    def test_look_b_aligns_by_sign_h_and_uses_the_cluster_permutation(self):
        recs = []
        for i in range(60):
            sign = 1.0 if i % 2 == 0 else -1.0
            recs.append({
                "symbol": f"S{i}", "session": "2025-09-02", "band": "primary",
                "h_sign": sign,
                # a real hedge leaves the excess pointing the way H does, so the
                # RAW b_z carries sign(H) and only the alignment makes it positive
                "horizons": {"16": {"quiet": True, "b_z": sign * (2.0 + 0.01 * i),
                                    "b_shares": sign * 1_000.0}},
            })
        res = look_b(recs, 16, "quiet", "primary", n_perm=2_000)
        assert res["n"] == 60
        assert res["mean_aligned_b_z"] > 0
        assert res["pass"] is True

    def test_look_b_ignores_the_active_tier_when_the_quiet_tier_is_asked_for(self):
        recs = [{"symbol": "S", "session": "d", "band": "primary", "h_sign": 1.0,
                 "horizons": {"16": {"quiet": False, "b_z": 5.0, "b_shares": 1.0}}}]
        assert look_b(recs, 16, "quiet", "primary", n_perm=10)["n"] == 0


# ===========================================================================
# control sessions — symmetric, and skipped on PRE-D activity
# ===========================================================================

class TestControlSessions:
    def _pool(self):
        # 30 weekday sessions around the event
        days = [date(2025, 9, 1) + __import__("datetime").timedelta(days=i)
                for i in range(45)]
        return [d for d in days if d.weekday() < 5]

    def test_symmetric_ten_before_ten_after(self):
        pool = self._pool()
        event = pool[20]
        ts = session_midnight_ns(event) + (11 * 3600) * NS
        got = control_sessions(event, "AAPL", ts, pool, lambda s: (np.array([]),
                                                                   np.array([])))
        assert len(got) == N_CONTROLS
        assert sum(1 for d in got if d < event) == 10
        assert sum(1 for d in got if d > event) == 10
        assert event not in got

    def test_one_sided_fill_when_the_pool_runs_out(self):
        pool = self._pool()
        event = pool[2]
        ts = session_midnight_ns(event) + (11 * 3600) * NS
        got = control_sessions(event, "AAPL", ts, pool, lambda s: (np.array([]),
                                                                   np.array([])))
        assert len(got) == N_CONTROLS
        assert sum(1 for d in got if d < event) == 2

    def test_sessions_with_nearby_25k_activity_are_skipped(self):
        pool = self._pool()
        event = pool[20]
        ts = session_midnight_ns(event) + (11 * 3600) * NS
        dirty = {pool[19], pool[21]}

        def _activity(sess):
            if sess in dirty:
                # the name printed ≥$25k single-leg activity 100s away
                t = session_midnight_ns(sess) + (11 * 3600) * NS + 100 * NS
                return np.array(["AAPL"]), np.array([t], dtype=np.int64)
            return np.array(["MSFT"]), np.array([0], dtype=np.int64)

        got = control_sessions(event, "AAPL", ts, pool, _activity)
        assert dirty.isdisjoint(got)
        assert len(got) == N_CONTROLS

    def test_a_release_fence_clock_time_yields_no_controls(self):
        pool = self._pool()
        event = pool[20]
        ts = session_midnight_ns(event) + (10 * 3600) * NS  # 10:00 ET
        assert control_sessions(event, "AAPL", ts, pool,
                                lambda s: (np.array([]), np.array([]))) == []


# ===========================================================================
# cluster-permutation inference — the 50/50 binomial is BANNED
# ===========================================================================

class TestClusterPermutation:
    def _data(self, n=100, n_clusters=5):
        z = np.full(n, 3.0)
        h_sign = np.ones(n)
        cl = np.array([f"C{i % n_clusters}" for i in range(n)])
        return z, h_sign, cl

    def test_clustered_same_sign_data_does_not_reach_p001(self):
        z, h_sign, cl = self._data(n=100, n_clusters=5)
        _, p = cluster_permutation_p(lambda s: float(np.mean(z * s)), h_sign, cl,
                                     n_perm=2000, seed=7)
        # a 50/50 binomial on 100 same-sign events would read p ~ 1e-30;
        # 5 clusters give at best 1/32
        assert p > 0.01

    def test_independent_clusters_do_reach_significance(self):
        z, h_sign, cl = self._data(n=100, n_clusters=100)
        _, p = cluster_permutation_p(lambda s: float(np.mean(z * s)), h_sign, cl,
                                     n_perm=2000, seed=7)
        assert p < 0.01

    def test_flip_is_per_cluster_not_per_event(self):
        seen: list[np.ndarray] = []
        z, h_sign, cl = self._data(n=10, n_clusters=2)

        def _stat(s):
            seen.append(np.asarray(s).copy())
            return float(np.mean(z * s))

        cluster_permutation_p(_stat, h_sign, cl, n_perm=25, seed=3)
        for s in seen[1:]:
            # every event of a cluster carries the SAME flipped sign
            assert len(set(s[::2].tolist())) == 1
            assert len(set(s[1::2].tolist())) == 1


# ===========================================================================
# the verdict table
# ===========================================================================

_PASS = {"pass": True}
_FAIL = {"pass": False}
_UNEVAL = {"pass": None, "evaluable": False}


class TestVerdict:
    def test_confirmed(self):
        v = verdict(_PASS, _PASS, _PASS, _PASS, 0.001, 150, 30, 0.2, False)
        assert v["verdict"] == "FOOTPRINT-CONFIRMED"
        assert v["floors_met"] is True

    def test_absent_requires_every_look_to_fail_at_every_horizon(self):
        v = verdict(_FAIL, _FAIL, _FAIL, _FAIL, 0.9, 150, 30, 0.2, True)
        assert v["verdict"] == "FOOTPRINT-ABSENT"

    def test_mixed_outcome_is_ambiguous_not_absent(self):
        v = verdict(_PASS, _UNEVAL, _FAIL, _FAIL, 0.9, 150, 30, 0.2, False)
        assert v["verdict"] == "FOOTPRINT-AMBIGUOUS"

    def test_look_c_only_excess_is_ambiguous(self):
        v = verdict(_FAIL, _UNEVAL, _FAIL, _PASS, 0.5, 150, 30, 0.2, False)
        assert v["verdict"] == "FOOTPRINT-AMBIGUOUS"

    def test_direction_test_alone_cannot_confirm(self):
        v = verdict(_PASS, _PASS, _FAIL, _PASS, 0.001, 150, 30, 0.2, False)
        assert v["verdict"] == "FOOTPRINT-AMBIGUOUS"

    def test_underpowered_gates_absent(self):
        v = verdict(_FAIL, _FAIL, _FAIL, _FAIL, 0.9, 150, 30, 0.5, True)
        assert v["verdict"] == "NO-VERDICT-UNDERPOWERED"

    def test_missing_mde_is_underpowered(self):
        v = verdict(_FAIL, _FAIL, _FAIL, _FAIL, 0.9, 150, 30, None, True)
        assert v["verdict"] == "NO-VERDICT-UNDERPOWERED"

    def test_event_floor_unmet(self):
        v = verdict(_PASS, _PASS, _PASS, _PASS, 0.001, 50, 30, 0.2, False)
        assert v["verdict"] == "NO-VERDICT"
        assert v["floors"]["n_events_ok"] is False

    def test_underlying_floor_unmet(self):
        v = verdict(_PASS, _PASS, _PASS, _PASS, 0.001, 150, 12, 0.2, False)
        assert v["verdict"] == "NO-VERDICT"
        assert v["floors"]["n_underlyings_ok"] is False

    def test_direction_pass_blocks_all_horizons_failed(self):
        # The registered table enumerates direction-without-size as AMBIGUOUS:
        # a passing direction test at ANY horizon must veto the ABSENT route.
        def _looks(direction_p):
            return {
                str(k): {"look_a": _FAIL, "look_b": _FAIL, "look_c": _FAIL,
                         "look_a_prime": _UNEVAL, "direction_permutation_p": direction_p}
                for k in hf.HORIZONS_G
            }
        assert hf.all_horizons_failed(_looks(0.9)) is True
        assert hf.all_horizons_failed(_looks(0.001)) is False
        assert hf.all_horizons_failed(_looks(float("nan"))) is True


# ===========================================================================
# per-tape condition table
# ===========================================================================

class TestConditionTable:
    def test_average_price_code_differs_per_tape(self):
        table = build_condition_table()
        cond = np.array(["B", "B", "W", "W", " ", "@"], dtype="U8")
        tape = np.array(["A", "C", "A", "C", "A", "C"], dtype="U1")
        excluded, _ = condition_masks(cond, tape, table)
        # CTA 'B' = average price (excluded); UTP 'B' = bunched trade (kept)
        # UTP 'W' = average price (excluded); CTA 'W' is not in the CTA set
        assert excluded.tolist() == [True, False, False, True, False, False]

    def test_prior_reference_and_derivatively_priced_excluded_on_both(self):
        table = build_condition_table()
        cond = np.array(["P", "P", "4", "4", "Z", "Z"], dtype="U8")
        tape = np.array(["A", "C", "A", "C", "B", "C"], dtype="U1")
        excluded, _ = condition_masks(cond, tape, table)
        assert excluded.tolist() == [True] * 6

    def test_qct_is_reported_separately_not_pooled(self):
        table = build_condition_table()
        cond = np.array(["7", "@7"], dtype="U8")
        tape = np.array(["A", "C"], dtype="U1")
        excluded, qct = condition_masks(cond, tape, table)
        assert qct.tolist() == [True, True]
        assert excluded.tolist() == [False, False]

    def test_unknown_tape_takes_the_conservative_union(self):
        table = build_condition_table()
        cond = np.array(["B", "W", "@"], dtype="U8")
        tape = np.array(["", "", ""], dtype="U1")
        excluded, _ = condition_masks(cond, tape, table)
        assert excluded.tolist() == [True, True, False]

    def test_multi_code_string_matches_any_member(self):
        table = build_condition_table()
        cond = np.array(["@I", "@IW"], dtype="U8")
        tape = np.array(["C", "C"], dtype="U1")
        excluded, _ = condition_masks(cond, tape, table)
        assert excluded.tolist() == [False, True]


# ===========================================================================
# the fetcher — pagination, the page cap, the SIP feed assertion
# ===========================================================================

class _FakeResp:
    def __init__(self, payload):
        self.status_code = 200
        self._payload = payload
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


class _FakeClient:
    """Two pages behind next_page_token; records requested params."""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    def get(self, url, params=None):
        self.calls.append((url, dict(params or {})))
        if params.get("page_token"):
            return _FakeResp({"trades": [{
                "t": "2026-01-05T15:00:00.000000200Z", "p": 2.0, "s": 7, "x": "D",
                "c": ["@", "W"], "z": "C"}], "next_page_token": None})
        return _FakeResp({"trades": [{
            "t": "2026-01-05T14:59:59.000000100Z", "p": 1.0, "s": 5, "x": "V",
            "c": [" "], "z": "A"}], "next_page_token": "tok"})


class _EndlessClient:
    def get(self, url, params=None):
        return _FakeResp({"trades": [], "next_page_token": "always"})


class TestFetch:
    def test_pagination_stitches_and_keeps_conditions_and_tape(self):
        client = _FakeClient()
        out = fetch_trades(client, "http://x", "AAPL", T0, T0 + 600 * NS)
        assert len(client.calls) == 2
        assert client.calls[1][1]["page_token"] == "tok"
        assert out["size"].tolist() == [5, 7]
        assert out["exchange"].tolist() == ["V", "D"]
        assert out["cond"].tolist() == [" ", "@W"]
        assert out["tape"].tolist() == ["A", "C"]
        assert out["t_ns"][0] % NS == 100

    def test_feed_sip_is_passed_literally(self):
        client = _FakeClient()
        fetch_trades(client, "http://x", "AAPL", T0, T0 + 600 * NS)
        assert client.calls[0][1]["feed"] == "sip"

    def test_window_is_rfc3339_nanosecond_utc(self):
        client = _FakeClient()
        fetch_trades(client, "http://x", "AAPL", T0, T0 + 600 * NS)
        params = client.calls[0][1]
        assert params["start"].endswith("Z") and "T" in params["start"]
        assert params["start"] == "2027-01-15T08:00:00.000000000Z"

    def test_page_cap_raises_it_never_warns(self):
        # 3.1's fetcher warned; a truncated window stales every prevailing quote
        # after it, so 3.2 raises
        with pytest.raises(PageCapError):
            _paged_get(_EndlessClient(), "http://x", {}, "trades", max_pages=3)


class TestFeedAssertion:
    def test_sip_window_passes(self):
        # a 600s window carrying ~2.5% of a 10M-share day is pro-rata normal
        ratio = assert_window_volume(250_000.0, 10_000_000.0, 600.0, "AAPL")
        assert ratio > 0.9

    def test_iex_style_understatement_raises(self):
        # ~20x understated: what would manufacture FOOTPRINT-ABSENT
        with pytest.raises(Exception, match="feed downgrade"):
            assert_window_volume(12_500.0, 10_000_000.0, 600.0, "AAPL")

    def test_missing_daily_bar_raises(self):
        with pytest.raises(Exception, match="daily bar"):
            assert_window_volume(250_000.0, 0.0, 600.0, "AAPL")
