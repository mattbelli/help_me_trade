"""Pinned tests for the o sign-validation prereg (2026-08-03) and its
Amendment 1 (2026-08-05) — these lock the sign conventions and mechanics
BEFORE any fetch, per the registrations."""

from datetime import date, timedelta

import numpy as np
import pytest

from tools.o_sign_validation import (
    CLAIMANT_R_NS,
    COHERENCE_MIN,
    SEED_A1,
    TIE_SUBSTREAM_OFF,
    _assert_table_keys,
    _bt_bounds,
    _fieller_ao,
    _step1p_verdict,
    bootstrap_t_ci,
    claimant_ambig,
    cluster_bounds,
    cluster_side_class,
    conjunction_power,
    conjunction_power_realized,
    match_event,
    match_event_signed,
    n_required,
    placebo_gate_a1,
    prevailing_quote_idx_strict,
    sigma_net,
    sign_stock_prints,
    skew_diagnostics,
    step1_sample_sessions,
    step1p_sample_sessions,
    stratified_cap_a1,
    taker_stock_side,
    terciles_seeded,
)

# --- the four worked sign cases (taker stock leg = NEGATION of dealer map) ---

def test_taker_buys_call_sells_stock():
    assert taker_stock_side(0, is_put=False) == -1


def test_taker_buys_put_buys_stock():
    assert taker_stock_side(0, is_put=True) == 1


def test_taker_sells_call_buys_stock():
    assert taker_stock_side(1, is_put=False) == 1


def test_taker_sells_put_sells_stock():
    assert taker_stock_side(1, is_put=True) == -1


# --- the 0/1-label encoding trap: side is a LABEL, never arithmetic ----------

def test_side_labels_2_and_3_raise():
    for label in (2, 3, -1):
        with pytest.raises(ValueError):
            taker_stock_side(label, is_put=False)


# --- σ_net: §3 convention + A/B-swap invariance away from o_diff = 0.5 -------

def test_sigma_net_convention_and_swap_invariance():
    # o near 1 ⇒ taker bought A / sold B; net Δ = Δ_A − Δ_B
    assert sigma_net(0.95, +0.40) == 1      # bought A, net long delta
    assert sigma_net(0.05, +0.40) == -1     # sold A ⇒ net short delta
    # swapping A/B: o → 1−o, netΔ → −netΔ ⇒ σ_net invariant
    assert sigma_net(0.95, +0.40) == sigma_net(1 - 0.95, -0.40)
    assert sigma_net(0.03, -0.25) == sigma_net(0.97, +0.25)
    with pytest.raises(ValueError):
        sigma_net(0.9, 0.0)


# --- strictly-before stock-quote rule (ts-equal quote is NOT used) -----------

def test_prevailing_quote_strictly_before():
    quote_t = np.array([100, 200, 300], dtype=np.int64)
    trade_t = np.array([200, 250, 100, 50], dtype=np.int64)
    idx = prevailing_quote_idx_strict(trade_t, quote_t)
    # trade at 200: quote at 200 excluded, prior is idx 0 (t=100)
    assert idx.tolist() == [0, 1, -1, -1]


def test_synthetic_drift_ts_equal_quote_excluded():
    """Synthetic drift: mid drifts up; a quote stamped AT the print's ts would
    flip the sign if used. The strict rule must sign against the earlier quote."""
    quote_t = np.array([1_000, 2_000], dtype=np.int64)
    bid = np.array([10.00, 10.20])
    ask = np.array([10.10, 10.30])          # mid 10.05 then 10.25
    trade_t = np.array([2_000], dtype=np.int64)
    trade_px = np.array([10.15])            # above earlier mid, below ts-equal mid
    s = sign_stock_prints(trade_t, trade_px, quote_t, bid, ask)
    assert s.tolist() == [1]                # strictly-before quote ⇒ BUY
    # at-or-before would have signed −1 — assert the trap is real
    later = sign_stock_prints(trade_t + 1, trade_px, quote_t, bid, ask)
    assert later.tolist() == [-1]


def test_sign_at_mid_and_no_quote_unsignable():
    quote_t = np.array([1_000], dtype=np.int64)
    bid, ask = np.array([10.0]), np.array([10.2])
    assert sign_stock_prints(np.array([2_000]), np.array([10.1]),
                             quote_t, bid, ask).tolist() == [0]   # at mid
    assert sign_stock_prints(np.array([500]), np.array([10.1]),
                             quote_t, bid, ask).tolist() == [0]   # no prior


# --- MF-1: stock side aggregates, coherence gate, funnel rows ----------------

def _mk_trades(t, px, sz, cond, tape=None):
    n = len(t)
    return {"t_ns": np.array(t, dtype=np.int64),
            "price": np.array(px, dtype=np.float64),
            "size": np.array(sz, dtype=np.int64),
            "exchange": np.array(["D"] * n, dtype="U2"),
            "cond": np.array(cond, dtype="U8"),
            "tape": np.array(tape or ["A"] * n, dtype="U1")}


def _mk_quotes(t, bid, ask):
    return {"t_ns": np.array(t, dtype=np.int64),
            "bid": np.array(bid, dtype=np.float64),
            "ask": np.array(ask, dtype=np.float64),
            "tape": np.array(["A"] * len(t), dtype="U1")}


QUOTES = _mk_quotes([0], [10.0], [10.2])     # mid 10.1 prevails throughout


def test_match_aggregates_all_qualifying_prints():
    """Two '7V' prints, sizes 300+700 same side ⇒ matched, signable, S_net=1000."""
    tr = _mk_trades([1_000, 2_000], [10.15, 10.18], [300, 700], ["@7V", "@7V"])
    m = match_event(tr, QUOTES, 1_500, 100_000_000)
    assert m["matched"] and m["signable"]
    assert m["S_net"] == pytest.approx(1000.0)
    assert m["obs_side"] == 1


def test_coherence_gate_incoherent_is_unsignable():
    """Opposed sizes 600 buy / 500 sell: |S_net|/Σ = 100/1100 < 0.60 ⇒ not signable."""
    tr = _mk_trades([1_000, 2_000], [10.15, 10.05], [600, 500], ["@7V", "@7V"])
    m = match_event(tr, QUOTES, 1_500, 100_000_000)
    assert m["matched"] and not m["signable"]
    assert m["coherence"] < COHERENCE_MIN


def test_funnel_7only_Vonly_and_excluded():
    tr = _mk_trades([1_000, 1_100, 1_200, 1_300],
                    [10.15] * 4, [100] * 4,
                    ["@7", "@V", "@7VZ", "@7V"])   # Z = out-of-sequence (excluded)
    m = match_event(tr, QUOTES, 1_150, 100_000_000)
    assert m["n_7only"] == 1 and m["n_Vonly"] == 1
    assert m["n_excl_overlap"] == 1
    assert m["n_qual"] == 1                        # only the clean '7V'


def test_window_bounds_inclusive():
    tr = _mk_trades([1_000], [10.15], [100], ["@7V"])
    assert match_event(tr, QUOTES, 1_000 + 100_000_000, 100_000_000)["matched"]
    assert not match_event(tr, QUOTES, 1_000 + 100_000_001, 100_000_000)["matched"]


# --- power harness: stressed < unstressed (registered coded assertion) -------

def test_stressed_power_below_unstressed():
    rng = np.random.default_rng(7)
    blocks = [{"session": f"s{i % 12}", "symbol": f"N{i % 30}",
               "n_events": int(rng.integers(1, 5)),
               "premiums": rng.uniform(2e4, 2e5, size=4).tolist()}
              for i in range(150)]
    hi = conjunction_power(blocks, keep_rate=0.5, a_cal=0.80, a_o=0.75,
                           n_sim=60, seed=1)
    lo = conjunction_power(blocks, keep_rate=0.25, a_cal=0.70, a_o=0.75,
                           n_sim=60, seed=1)
    assert lo <= hi


# =============================================================================
# AMENDMENT 1 (2026-08-05) pinned tests — the 15 delta build obligations
# =============================================================================

PXI = 10 ** 9


# --- obligation 3: midpoint sign cases on the ACTUAL corpus fence ------------

def test_midpoint_sign_cases_fence_and_classify():
    """above-mid ⇒ buy (0), below-mid ⇒ sell (1), at-mid ⇒ unsignable (2),
    locked market ⇒ unsignable (2), wide fence ⇒ 3 — on fence_and_classify
    itself, the registered A1-1 sign source."""
    from tools.flow_corpus_rollup import fence_and_classify
    rows = [
        (10_060_000_000, 10, 10_000_000_000, 10_100_000_000),   # above mid
        (10_040_000_000, 10, 10_000_000_000, 10_100_000_000),   # below mid
        (10_050_000_000, 10, 10_000_000_000, 10_100_000_000),   # at mid
        (10_050_000_000, 10, 10_100_000_000, 10_100_000_000),   # locked
        (7_000_000_000, 10, 5_000_000_000, 10_000_000_000),     # wide fence
    ]
    arr = np.array(rows, dtype=[("price", "<i8"), ("size", "<u4"),
                                ("bid_px_00", "<i8"), ("ask_px_00", "<i8")])
    fx = fence_and_classify(arr)
    assert fx.side.tolist() == [0, 1, 2, 2, 3]


def test_cluster_side_class_rows_and_column_swap_trap():
    """Unanimity-or-drop + the three funnel rows (priority mixed > wide >
    at-mid), and the strict→side column-swap trap: feeding a different-label
    column MUST change the output — the loader consumes fx.side."""
    gid = np.array([0, 0, 1, 2, 3, 3, 4])
    side = np.array([0, 0, 1, 2, 0, 1, 3])
    uside, cls = cluster_side_class(gid, side, 5)
    assert uside.tolist() == [0, 1, -1, -1, -1]
    assert cls.tolist() == [0, 0, 1, 3, 2]
    strict = np.array([1, 1, 0, 0, 1, 1, 0])       # same 0/1 alphabet, swapped
    us2, _ = cluster_side_class(gid, strict, 5)
    assert not np.array_equal(uside, us2)


def test_table_key_match_assertion():
    table = [{"sym": "AAA", "ts_ns": 1}, {"sym": "BBB", "ts_ns": 2}]
    _assert_table_keys(table, {"sym": ["AAA", "BBB"], "ts_ns": [1, 2]})
    _assert_table_keys(table, {"sym": ["BBB"], "ts_ns": [2]})   # subset ok
    with pytest.raises(AssertionError):
        _assert_table_keys(table, {"sym": ["BBB", "AAA"], "ts_ns": [2, 1]})


# --- obligation 2: claimant radius decoupled from the match window -----------

def test_claimant_radius_decoupled_from_window(monkeypatch):
    import tools.o_sign_validation as m
    assert CLAIMANT_R_NS == 200_000_000
    g_ts = np.array([0, 150_000_000, 600_000_000], dtype=np.int64)
    g_und = np.array([1, 1, 1])
    assert claimant_ambig(g_ts, g_und).tolist() == [True, True, False]
    ts2 = np.array([0, 250_000_000], dtype=np.int64)
    assert claimant_ambig(ts2, np.array([1, 1])).tolist() == [False, False]
    # moving the match window must not move the claimant radius
    monkeypatch.setattr(m, "W_LO_NS", -500_000)
    monkeypatch.setattr(m, "W_HI_NS", 500_000)
    assert m.claimant_ambig(g_ts, g_und).tolist() == [True, True, False]


# --- obligation 1: signed-window matcher + full-window tripwire counters -----

def test_signed_window_bounds_and_full_counters():
    t0 = 10_000_000_000
    tr = _mk_trades(
        [t0 - 1_000_000, t0 + 3_000_000, t0 - 1_000_001, t0 + 3_000_001,
         t0 + 200_000_000],
        [10.15] * 5, [100] * 5, ["@7V", "@7V", "@7V", "@7V", "@7"])
    m = match_event_signed(tr, QUOTES, t0, -1_000_000, 3_000_000)
    assert m["n_qual"] == 2                    # both edges inclusive, outside out
    assert m["matched"] and m["signable"]
    assert m["dt_ns"] == -1_000_000            # nearest candidate, signed
    assert m["n_7only"] == 0                   # in-window funnel
    assert m["n_7only_full"] == 1              # full ±300 s tripwire counter
    assert m["n_window"] == 2


# --- obligation 5: pool[3::6] sampler + disjointness -------------------------

def test_step1p_sampler_disjoint():
    pool = [date(2026, 1, 1) + timedelta(days=i) for i in range(105)]
    s1 = step1_sample_sessions(pool)
    s1p = step1p_sample_sessions(pool)
    assert s1p == pool[3::6]
    assert not set(s1) & set(s1p)
    assert len(s1) == 18 and len(s1p) == 17


# --- obligation 6: seeded terciles + substream separation --------------------

def test_terciles_seeded_zero_drift_populated():
    t = terciles_seeded(np.zeros(300))
    assert [(t == k).sum() for k in (0, 1, 2)] == [100, 100, 100]


def test_terciles_seeded_tied_session_composition():
    """The stable-index date-clump trap: 18 sessions × 50 all-tied events —
    each tercile's session composition must match the population's, never a
    time-ordered prefix."""
    sess = np.repeat(np.arange(18), 50)
    t = terciles_seeded(np.zeros(900))
    for k in (0, 1, 2):
        assert len(set(sess[t == k])) >= 16


def test_tie_break_substream_separation():
    n = 1000
    u = np.random.default_rng(SEED_A1 + TIE_SUBSTREAM_OFF).random(n)
    boot = np.random.default_rng(SEED_A1).random(n)
    assert not np.allclose(u, boot)
    # n_required derives data-dependent seed+k offsets: k = 5 would collide
    # with the reserved tie-break substream and must abort (A1-7)
    blocks = [{"session": "s", "symbol": "N", "n_events": 1,
               "premiums": [1e4]}] * 20      # 20 × 0.25 scale ⇒ k = 5
    with pytest.raises(AssertionError):
        n_required(blocks, keep_rate=0.5, a_cal=0.8)


# --- obligation 3/A1-1: the 6-cell stratified cap ----------------------------

def test_stratified_cap_a1_redistribution_and_determinism():
    rng = np.random.default_rng(3)
    n = 600
    prem = rng.uniform(1e4, 1e6, size=n)
    is_strict = np.zeros(n, dtype=bool)
    is_strict[:60] = True                      # scarce strict class
    sel, info = stratified_cap_a1(prem, is_strict, cap=300)
    assert len(sel) == 300
    fills = np.array(info["fill"])
    eligs = np.array(info["eligible"])
    assert (fills <= eligs).all()
    for t in range(3):                         # shortfall redistributed WITHIN tercile
        assert fills[t].sum() == 100
    sel2, _ = stratified_cap_a1(prem, is_strict, cap=300)
    assert np.array_equal(sel, sel2)           # selection, not a draw
    sel3, info3 = stratified_cap_a1(prem[:100], is_strict[:100], cap=300)
    assert len(sel3) == 100                    # under cap: take everything


# --- obligation 10: bootstrap-t crossing pin + 17-cluster coverage -----------

def test_bootstrap_t_crossed_quantiles_pin():
    """LB must use the HIGH t* quantile (LB = â − t*_{0.975}·SE) — the classic
    crossing error inflates the LB toward LIVE′."""
    t_star = np.array([-1.0] * 50 + [5.0] * 50)     # right-skewed t*
    lb, ub = _bt_bounds(0.8, 0.1, t_star)
    assert lb == pytest.approx(0.8 - float(np.quantile(t_star, 0.975)) * 0.1)
    assert ub == pytest.approx(0.8 - float(np.quantile(t_star, 0.025)) * 0.1)
    assert lb < ub


def test_bootstrap_t_coverage_17_clusters():
    rng = np.random.default_rng(42)
    true_p, n_ds, cover = 0.75, 120, 0
    for _ in range(n_ds):
        p_sess = np.clip(true_p + rng.normal(0, 0.08, size=17), 0.05, 0.95)
        counts = rng.integers(20, 60, size=17)
        hit = np.concatenate([(rng.random(c) < p).astype(float)
                              for p, c in zip(p_sess, counts)])
        sess = np.repeat(np.arange(17), counts)
        lb, ub, meta = bootstrap_t_ci(hit, sess, n_boot=299,
                                      seed=int(rng.integers(10 ** 9)))
        assert meta["n_clusters"] == 17
        if lb <= true_p <= ub:
            cover += 1
    assert cover / n_ds >= 0.90


# --- obligation 7: reweighted LB responds to composition ---------------------

def test_reweighted_lb_responds_to_composition():
    rng = np.random.default_rng(0)
    n = 2000
    sess = np.array([f"s{i % 17}" for i in range(n)])
    cells = rng.integers(0, 2, size=n)
    hit = np.where(cells == 0, rng.random(n) < 0.90,
                   rng.random(n) < 0.55).astype(float)
    lb0, _, _ = bootstrap_t_ci(hit, sess, n_boot=400, cells=cells,
                               target={0: 0.9, 1: 0.1})
    lb1, _, _ = bootstrap_t_ci(hit, sess, n_boot=400, cells=cells,
                               target={0: 0.1, 1: 0.9})
    assert lb0 > lb1 + 0.10


# --- obligation 8: row (viii) CALIBRATED-NO-RUN′ over LIVE′ ------------------

def test_step1p_verdict_truth_table():
    gates = {"stop_mechanics": False, "match_ge_20": True,
             "signable_ge_50": True, "placebo_c_lt_01": True,
             "positive_control": True}
    ci = (0.80, 0.90)
    assert _step1p_verdict(gates, ci, 0.001, 0.85, 0.60, True) \
        == "CALIBRATED-NO-RUN'"                # row (viii) over LIVE′
    assert _step1p_verdict(gates, ci, 0.001, 0.85, 0.60, False) == "LIVE'"
    assert _step1p_verdict(gates, (0.55, 0.70), 0.02, 0.85, 0.60, False) \
        == "INSTRUMENT-WEAK'"                  # row (vii): CI above 0.5, p ≥ 0.01
    assert _step1p_verdict(gates, ci, 0.001, 0.55, 0.60, True) \
        == "INSTRUMENT-WEAK'"                  # row (viii) still needs LB ≥ θ*
    assert _step1p_verdict(gates, ci, 0.001) == "PENDING-GATE-REWEIGHTED-LB"
    assert _step1p_verdict(gates, (0.45, 0.55), 0.001, 0.9, 0.6, False) \
        == "INSTRUMENT-DEAD'-NO-DIRECTION"     # rows (ii)/(iii) FIRST, mixture
    assert _step1p_verdict(gates, (0.30, 0.45), 0.001, 0.9, 0.6, False) \
        == "INSTRUMENT-DEAD'-INVERTED-TERMINAL"
    bad = dict(gates, match_ge_20=False)
    assert _step1p_verdict(bad, ci, 0.001, skew_tripped=True) \
        == "INVESTIGATE'-SKEW"                 # A1-2: not DEAD′-MECHANICS
    assert _step1p_verdict(bad, ci, 0.001) == "INSTRUMENT-DEAD'-MECHANICS"
    assert _step1p_verdict(dict(gates, positive_control=False), ci, 0.001) \
        == "INVESTIGATE'-PLACEBO-CONTROL"


# --- obligation 11: placebo law — empty ⇒ FAIL + nan control fails (ONE test)

def test_placebo_law_empty_fails_and_nan_control_fails():
    nan = float("nan")
    contam = {"t60_primary": {"c": nan, "rate": nan},
              "alt_primary": {"c": nan, "rate": nan},
              "t60_100000000": {"rate": nan}, "alt_100000000": {"rate": nan}}
    g = placebo_gate_a1(contam)
    assert g["placebo_c_lt_01"] is False       # non-finite c = FAIL, not skip
    assert g["positive_control_100ms_gt_0"] is False   # nan fails by assertion
    healthy = {"t60_primary": {"c": 0.002}, "alt_primary": {"c": 0.001},
               "t60_100000000": {"rate": 0.010}, "alt_100000000": {"rate": 0.002}}
    g2 = placebo_gate_a1(healthy)
    assert g2["placebo_c_lt_01"] and g2["positive_control_100ms_gt_0"]


# --- obligation 12: t0 = min ts_event + pinned skew estimator ----------------

def test_cluster_t0_is_min_ts():
    gid = np.array([0, 0, 0, 1, 1])
    ts = np.array([50, 10, 30, 100, 90], dtype=np.int64)   # unsorted in-cluster
    t0, span = cluster_bounds(gid, ts)
    assert t0.tolist() == [10, 90]
    assert span.tolist() == [40, 10]


def test_skew_diagnostics_pinned():
    dt = np.concatenate([np.full(50, 1_500_000), np.full(30, -900_000)])
    d = skew_diagnostics(dt, capture_num=80, capture_den=100)
    assert d["sub_modes"]["pos"]["ok"] and not d["sub_modes"]["neg"]["ok"]
    assert d["tripped"]                        # neg mode 0.1 ms from the −1 edge
    d2 = skew_diagnostics(np.full(50, 1_500_000), 20, 100)
    assert not d2["capture_ok"] and d2["tripped"]      # capture 0.20 < 0.30
    d3 = skew_diagnostics(np.full(50, 1_500_000), 50, 100)
    assert not d3["tripped"]
    assert d3["sub_modes"]["neg"]["ok"]        # empty sub-population passes


# --- obligation 15: Step-2 inter-leg span precondition -----------------------

def test_step2_span_precondition(monkeypatch):
    import tools.o_sign_validation as m
    monkeypatch.setattr(m, "step2_span_stats",
                        lambda: {"n": 10, "p90_ns": 2_000_000.0})
    with pytest.raises(AssertionError):
        m.assert_step2_window_ok()
    monkeypatch.setattr(m, "step2_span_stats",
                        lambda: {"n": 10, "p90_ns": 5_400.0})
    assert m.assert_step2_window_ok()["p90_ns"] == 5_400.0


# --- obligation 9: Fieller fallback + Gate-2 realized structure --------------

def test_fieller_ao_brackets_point_estimate():
    rng = np.random.default_rng(1)
    s1_sess = np.repeat([f"s{i}" for i in range(20)], 100)
    s1_hit = (rng.random(2000) < 0.85).astype(float)
    u_sess = np.repeat([f"s{i}" for i in range(20)], 10)
    u_hit = (rng.random(200) < 0.75).astype(float)
    f = _fieller_ao(s1_hit, s1_sess, u_hit, u_sess)
    assert f["ok"]
    lo, hi = f["a_o_ci"]
    point = 0.5 + (u_hit.mean() - 0.5) / (2 * s1_hit.mean() - 1)
    assert lo < point < hi


def test_conjunction_power_realized_monotone():
    units = [{"session": f"s{i % 15}", "sym": f"N{i % 40}",
              "tercile": int(i % 3)} for i in range(300)]
    hi = conjunction_power_realized(units, a_cal=0.85, a_o=0.75,
                                    n_sim=60, seed=2)
    lo = conjunction_power_realized(units, a_cal=0.55, a_o=0.60,
                                    n_sim=60, seed=2)
    assert lo <= hi
