"""Synthetic-fixture proof of the classification-lab scorer's registered math.

Every assertion here traces to a clause of References/CLASSIFICATION_LAB_PREREG_2026-07-16.md.
No real capture is read: the fixtures are hand-built so each metric's arithmetic is
verifiable by inspection.
"""

from __future__ import annotations

import json
import statistics
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from tools.classification_lab_scorer import (
    VEGA_PER_VOL_SCALE,
    Acc,
    Core,
    Print,
    QuoteNode,
    Sample,
    _fold,
    _net_sign,
    _report,
    _viable,
    _window_of,
    backpair_labels,
    bound_timeline,
    delta_adjusted_labels,
    downsample_chain,
    is_opex,
    load_chain,
    method_measurable,
    method_variants,
    net_fidelity,
    pool_net_observations,
    score,
    spike_minutes,
    summarize,
    tick_labels,
)

_ET = ZoneInfo("America/New_York")
OCC = "SPY260730C00600000"
OCC_PUT = "SPY260730P00600000"
BIG = 200  # contracts; keeps synthetic prints above the gauge's $2,000 dust floor


def ms(day: str, hh: int, mm: int, ss: int = 0, milli: int = 0) -> int:
    d = date.fromisoformat(day)
    dt = datetime(d.year, d.month, d.day, hh, mm, ss, milli * 1000, tzinfo=_ET)
    return int(dt.timestamp() * 1000)


def mk_print(
    ts_ms: int,
    price: float,
    *,
    occ: str = OCC,
    size: int = 1,
    truth: str | None = None,
    undetermined: bool = False,
    window: str = "open",
    minute: int = 0,
    in_scope: bool = True,
    gauge_eligible: bool = True,
    bid: float | None = None,
    ask: float | None = None,
    q_ts_ms: int | None = None,
) -> Print:
    return Print(
        ts_ms=ts_ms,
        occ=occ,
        name="SPY",
        price=price,
        size=size,
        right=occ[-9],
        window=window,
        minute=minute,
        in_scope=in_scope,
        premium=price * size * 100.0,
        truth=truth,
        truth_undetermined=undetermined,
        gauge_eligible=gauge_eligible,
        q_bid=bid,
        q_ask=ask,
        q_ts_ms=q_ts_ms,
    )


# --- scope: the gauge windows, shoulders read but never scored --------------------


def test_window_of_covers_both_gauge_windows_and_marks_shoulders_out_of_scope():
    assert _window_of(ms("2026-07-20", 9, 30)) == ("open", 0, True)
    assert _window_of(ms("2026-07-20", 9, 34, 59)) == ("open", 4, True)
    assert _window_of(ms("2026-07-20", 9, 59, 59)) == ("open", 29, True)
    assert _window_of(ms("2026-07-20", 15, 30)) == ("close", 0, True)
    # shoulders are captured (they seed tick chains + the 5s grid) but never scored
    assert _window_of(ms("2026-07-20", 9, 27)) == ("open", -3, False)
    assert _window_of(ms("2026-07-20", 10, 2)) == ("open", 32, False)
    assert _window_of(ms("2026-07-20", 12, 0)) is None


# --- method 1: naive tick test ----------------------------------------------------


def test_tick_test_is_the_sign_of_the_price_change_on_the_same_contract():
    prints = [
        mk_print(ms("2026-07-20", 9, 31), 1.00),
        mk_print(ms("2026-07-20", 9, 32), 1.20),  # up
        mk_print(ms("2026-07-20", 9, 33), 1.20),  # zero tick -> no claim
        mk_print(ms("2026-07-20", 9, 34), 0.90),  # down
        mk_print(ms("2026-07-20", 9, 35), 5.00, occ=OCC_PUT),  # other contract, first
    ]
    assert tick_labels(prints) == [None, "buy_side", None, "sell_side", None]


def test_tick_chain_resets_between_the_open_and_close_windows():
    prints = [
        mk_print(ms("2026-07-20", 9, 31), 1.00, window="open"),
        mk_print(ms("2026-07-20", 15, 31), 2.00, window="close"),
    ]
    assert tick_labels(prints) == [None, None]


def test_shoulder_prints_seed_the_tick_chain_for_the_windows_first_print():
    prints = [
        mk_print(ms("2026-07-20", 9, 27), 1.00, minute=-3, in_scope=False),
        mk_print(ms("2026-07-20", 9, 30), 1.20, minute=0),
    ]
    # without the shoulder the window's first print would be structurally unclassifiable
    assert tick_labels(prints) == [None, "buy_side"]


# --- method 2: delta-adjusted tick test -------------------------------------------


def _delta_fixture(second_price: float, *, iv_now: float = 0.22):
    t0 = ms("2026-07-20", 9, 31)
    t1 = ms("2026-07-20", 9, 32)
    prints = [mk_print(t0, 1.00), mk_print(t1, second_price)]
    chain = {
        OCC: [
            Sample(t0 - 1000, 0.99, 1.01, 0.20, 0.5, 0.01, 0.10),
            Sample(t1 - 1000, 0.99, 1.01, iv_now, 0.5, 0.01, 0.10),
        ]
    }
    tape = {"SPY": ([t0 - 500, t1 - 500], [600.0, 600.4])}
    return prints, chain, tape


def test_delta_adjusted_residual_matches_the_registered_formula():
    # Vendor vega is per 1% of vol while iv is a decimal, so the vol term is
    # vega * 100 * d(iv) = 0.10 * 100 * 0.02 = 0.20 — measured convention, see
    # tools/option_spread_probe.py:121 and its vega_convention_check() at :209.
    # resid = dP - [d*dS + 0.5*g*dS^2 + V*100*dSigma]
    #       = 0.50 - [0.5*0.4 + 0.5*0.01*0.16 + 0.20] = 0.2992 > 0 -> buy
    prints, chain, tape = _delta_fixture(1.50)
    assert delta_adjusted_labels(prints, chain, tape) == [None, "buy_side"]


def test_vol_term_is_vega_times_one_hundred_times_delta_iv():
    # Same price change, same underlying move; only iv differs. delta+gamma account for
    # 0.2008 of the 0.30 move, leaving +0.0992 — so the label flips to sell only if the
    # vol term exceeds 0.0992. vega*100*d(iv) = 0.10*100*0.02 = 0.20 flips it; the
    # unscaled vega*d(iv) = 0.002 would NOT, which is exactly the 100x bug this pins.
    assert VEGA_PER_VOL_SCALE == 100.0
    flat, flat_chain, flat_tape = _delta_fixture(1.30, iv_now=0.20)
    assert delta_adjusted_labels(flat, flat_chain, flat_tape) == [None, "buy_side"]
    rising, chain, tape = _delta_fixture(1.30, iv_now=0.22)
    assert delta_adjusted_labels(rising, chain, tape) == [None, "sell_side"]


def test_delta_adjustment_can_flip_the_naive_tick_verdict():
    # price ROSE (naive tick says buy) but by less than the underlying+vol move:
    # resid = 0.15 - 0.4008 < 0 -> sell. This is the whole point of method 2.
    prints, chain, tape = _delta_fixture(1.15)
    assert tick_labels(prints) == [None, "buy_side"]
    assert delta_adjusted_labels(prints, chain, tape) == [None, "sell_side"]


def test_delta_adjusted_claims_nothing_without_greeks_or_underlying_tape():
    prints, chain, tape = _delta_fixture(1.50)
    no_greeks = {OCC: [Sample(s.ts_ms, s.bid, s.ask, s.iv, None, None, None) for s in chain[OCC]]}
    assert delta_adjusted_labels(prints, no_greeks, tape) == [None, None]
    assert delta_adjusted_labels(prints, chain, {}) == [None, None]


def test_spx_uses_the_registered_spy_times_ten_proxy():
    t0 = ms("2026-07-20", 9, 31)
    t1 = ms("2026-07-20", 9, 32)
    occ = "SPXW260730C06000000"
    prints = [mk_print(t0, 1.00, occ=occ), mk_print(t1, 1.30, occ=occ)]
    for p in prints:
        p.name = "SPXW"
    chain = {
        occ: [
            Sample(t0 - 1000, None, None, 0.20, 0.05, 0.0, 0.10),
            Sample(t1 - 1000, None, None, 0.20, 0.05, 0.0, 0.10),
        ]
    }
    # SPY 600.00 -> 600.40 proxies SPX 6000.0 -> 6004.0, dS = 4.0; iv flat
    # resid = 0.30 - 0.05*4.0 = 0.10 > 0 -> buy
    tape = {"SPY": ([t0 - 500, t1 - 500], [600.0, 600.4])}
    assert delta_adjusted_labels(prints, chain, tape) == [None, "buy_side"]
    # no SPY tape -> no proxy -> no claim (never falls back to something else)
    assert delta_adjusted_labels(prints, chain, {"SPXW": ([t0], [6000.0])}) == [None, None]


# --- method 3: sampled-quote back-pairing -----------------------------------------


def test_downsampling_keeps_the_first_sample_in_each_interval_bucket():
    base = ms("2026-07-20", 9, 31)
    chain = {OCC: [Sample(base + i * 15_000, 1.0, 1.2, None, None, None, None) for i in range(5)]}
    assert [n.ts_ms for n in downsample_chain(chain, 15)[OCC]] == [
        base + i * 15_000 for i in range(5)
    ]
    assert [n.ts_ms - base for n in downsample_chain(chain, 30)[OCC]] == [0, 30_000, 60_000]
    assert [n.ts_ms - base for n in downsample_chain(chain, 60)[OCC]] == [0, 60_000]


def _quote_timeline(base: int) -> dict[str, list[QuoteNode]]:
    return {
        OCC: [
            QuoteNode(base, 1.00, 1.20, -1),
            QuoteNode(base + 30_000, 1.20, 1.40, -1),
        ]
    }


def test_strict_backward_never_looks_forward_and_interpolation_does():
    base = ms("2026-07-20", 9, 31)
    timeline = _quote_timeline(base)
    p = mk_print(base + 15_000, 1.15)
    # strict: mid of the ts<=t sample is 1.10 -> 1.15 above mid -> buy
    strict, _ = backpair_labels([p], timeline, "strict", [None])
    # interp: halfway between the two samples the mid is 1.20 -> 1.15 below -> sell
    interp, _ = backpair_labels([p], timeline, "interp", [None])
    assert strict == ["buy_side"]
    assert interp == ["sell_side"]


def test_a_print_before_the_first_sample_claims_nothing():
    base = ms("2026-07-20", 9, 31)
    labels, _ = backpair_labels([mk_print(base - 1, 1.15)], _quote_timeline(base), "strict", [None])
    assert labels == [None]


def test_interpolation_claims_nothing_past_the_last_sample():
    base = ms("2026-07-20", 9, 31)
    p = mk_print(base + 45_000, 1.15)
    labels, _ = backpair_labels([p], _quote_timeline(base), "interp", [None])
    assert labels == [None]


def test_lee_ready_falls_back_to_the_tick_test_at_the_midpoint():
    base = ms("2026-07-20", 9, 31)
    timeline = _quote_timeline(base)
    at_mid = mk_print(base + 1_000, 1.10)
    for tick, expected in (("buy_side", "buy_side"), ("sell_side", "sell_side"), (None, None)):
        labels, _ = backpair_labels([at_mid], timeline, "strict", [tick])
        assert labels == [expected]


def test_crossed_or_missing_quotes_claim_nothing():
    base = ms("2026-07-20", 9, 31)
    crossed = {OCC: [QuoteNode(base, 1.30, 1.20, -1), QuoteNode(base + 30_000, 1.3, 1.2, -1)]}
    labels, _ = backpair_labels([mk_print(base + 1_000, 1.15)], crossed, "strict", [None])
    assert labels == [None]
    empty = {OCC: [QuoteNode(base, None, None, -1)]}
    labels, _ = backpair_labels([mk_print(base + 1_000, 1.15)], empty, "strict", [None])
    assert labels == [None]


# T4 — the chain timeline must not let a trade read a quote that did not exist yet.
def test_chain_node_timestamp_is_the_later_of_the_pass_stamp_and_the_quote_stamp(tmp_path):
    pass_ts = "2026-07-20T09:31:00-04:00"
    late_quote = "2026-07-20T09:31:10-04:00"
    rows = [
        {"ts": pass_ts, "u": "SPY", "occ": OCC, "bid": 1.20, "ask": 1.40, "q_ts": late_quote},
        {"ts": pass_ts, "u": "SPY", "occ": OCC_PUT, "bid": 1.0, "ask": 1.1, "q_ts": pass_ts},
    ]
    (tmp_path / "2026-07-20.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    chain, stats = load_chain(tmp_path, "2026-07-20")
    # the quote fetched 10s into the pass is stamped at 09:31:10, not the pass start
    assert chain[OCC][0].ts_ms == ms("2026-07-20", 9, 31, 10)
    assert chain[OCC_PUT][0].ts_ms == ms("2026-07-20", 9, 31)
    assert stats["coverage_by_root"]["SPY"]["samples"] == 2
    # a trade at 09:31:05 must NOT see the 09:31:10 quote
    early = mk_print(ms("2026-07-20", 9, 31, 5), 1.35)
    timeline = downsample_chain(chain, 15)
    labels, _ = backpair_labels([early], timeline, "strict", [None])
    assert labels == [None]  # no node at or before the trade
    # back-dating it to the pass stamp would have said buy (1.35 > mid 1.30)
    backdated = {OCC: [QuoteNode(ms("2026-07-20", 9, 31), 1.20, 1.40, -1)]}
    leaked, _ = backpair_labels([early], backdated, "strict", [None])
    assert leaked == ["buy_side"]


# T3 — the 5s bound's self-pairing, on both lookup branches.
def test_five_second_bound_grid_keeps_one_quote_per_bucket_and_flags_self_pairs():
    base = ms("2026-07-20", 9, 31)
    prints = [
        mk_print(base, 1.15, bid=1.00, ask=1.20, q_ts_ms=base),
        mk_print(base + 2_000, 1.15, bid=1.10, ask=1.30, q_ts_ms=base + 2_000),
        mk_print(base + 7_000, 1.20, bid=1.05, ask=1.25, q_ts_ms=base + 7_000),
    ]
    timeline = bound_timeline(prints, 5)
    # two 5s buckets -> the second print's quote is dropped by the grid
    assert [n.ts_ms - base for n in timeline[OCC]] == [0, 7_000]
    labels, self_flags = backpair_labels(prints, timeline, "strict", [None, None, None])
    assert labels == ["buy_side", "buy_side", "buy_side"]
    # prints 0 and 2 contributed the grid quotes they paired with; print 1 did not
    assert self_flags == [True, False, True]


def test_interpolated_bound_flags_self_pairing_on_the_right_hand_node():
    base = ms("2026-07-20", 9, 31)
    prints = [
        # its own quote is the LEFT node
        mk_print(base, 1.15, bid=1.00, ask=1.20, q_ts_ms=base),
        # DIR-1 quote stamped just after the trade, so its own node lands to the RIGHT
        mk_print(base + 2_000, 1.15, bid=1.00, ask=1.20, q_ts_ms=base + 7_000),
        # contributes no node at all (no quote captured)
        mk_print(base + 3_000, 1.15, bid=None, ask=None, q_ts_ms=None),
    ]
    timeline = bound_timeline(prints, 5)
    assert [(n.ts_ms - base, n.src) for n in timeline[OCC]] == [(0, 0), (7_000, 1)]
    _, self_flags = backpair_labels(prints, timeline, "interp", [None, None, None])
    assert self_flags == [True, True, False]


def test_bound_agreement_is_reported_and_gated_on_the_self_excluded_population():
    acc = Acc()
    truth_print = mk_print(ms("2026-07-20", 9, 31), 1.0, size=BIG, truth="buy_side")
    # a self-paired print that agrees, and a non-self-paired print that does not
    _fold(acc, truth_print, "buy_side", "2026-07-20", True)
    _fold(acc, truth_print, "sell_side", "2026-07-20", False)
    out = summarize(acc, is_bound=True)
    assert out["agreement"] == pytest.approx(0.5)  # inflated by the truth-vs-truth print
    assert out["self_paired"] == 1
    assert out["self_paired_share"] == pytest.approx(0.5)
    assert out["excl_self_paired"]["agreement"] == pytest.approx(0.0)
    assert out["excl_self_paired"]["n_truth"] == 1


# --- metrics ----------------------------------------------------------------------


def test_agreement_counts_no_claims_against_the_truth_denominator():
    core = Core()
    core.n_truth, core.n_agree, core.n_disagree, core.n_noclaim = 10, 6, 3, 1
    core.prem_truth, core.prem_agree = 1000.0, 900.0
    out = summarize(Acc(full=core), is_bound=False)
    assert out["agreement"] == pytest.approx(0.6)
    assert out["agreement_premium_weighted"] == pytest.approx(0.9)
    # N1: labelled unregistered diagnostics, never a gate
    diagnostics = out["unregistered_diagnostics"]
    assert diagnostics["claim_rate"] == pytest.approx(0.9)
    assert diagnostics["agreement_when_claimed"] == pytest.approx(6 / 9)


def test_directional_bias_is_the_premium_weighted_asymmetry():
    core = Core()
    core.prem_method_sell, core.prem_method_sell_truth_buy = 1000.0, 300.0
    core.prem_method_buy, core.prem_method_buy_truth_sell = 2000.0, 100.0
    out = summarize(Acc(full=core), is_bound=False)
    assert out["p_truth_buy_given_method_sell_pw"] == pytest.approx(0.30)
    assert out["p_truth_sell_given_method_buy_pw"] == pytest.approx(0.05)
    assert out["directional_bias_asymmetry_pp"] == pytest.approx(25.0)


def test_overreach_is_scored_on_the_prints_truth_refused_to_guess():
    acc = Acc()
    acc.n_undetermined, acc.n_overreach = 10, 4
    acc.prem_undetermined, acc.prem_overreach = 5000.0, 2000.0
    out = summarize(acc, is_bound=False)
    assert out["overreach_rate"] == pytest.approx(0.4)
    assert out["overreach_rate_premium_weighted"] == pytest.approx(0.4)


def test_net_fidelity_reports_correlation_sign_agreement_and_resolution():
    truth = [100.0, -50.0, 200.0]
    method = [110.0, -40.0, 150.0]
    net = net_fidelity(list(zip(truth, method, strict=True)))
    assert net["n_observations"] == 3
    assert net["sign_agreement"] == pytest.approx(1.0)
    assert net["sign_agreement_resolution"] == pytest.approx(1 / 3)
    assert net["max_magnitude_error"] == pytest.approx(0.25)  # |150-200|/200
    assert net["correlation"] == pytest.approx(statistics.correlation(truth, method))


def test_net_sign_agreement_catches_a_flipped_window():
    net = net_fidelity([(100.0, 100.0), (-50.0, 50.0)])
    assert net["sign_agreement"] == pytest.approx(0.5)
    assert net["sign_agreement_resolution"] == pytest.approx(0.5)
    assert net["correlation"] is None  # fewer than 3 observations


def test_net_premium_follows_the_gauge_bull_bear_convention():
    assert _net_sign("C", "buy_side") == 1.0
    assert _net_sign("P", "sell_side") == 1.0
    assert _net_sign("P", "buy_side") == -1.0
    assert _net_sign("C", "sell_side") == -1.0


# T2 — the registered net rides the matched population and the gauge's own filters.
def test_registered_net_uses_only_truth_labelled_prints():
    acc = Acc()
    day = "2026-07-20"
    call = mk_print(ms(day, 9, 31), 1.0, size=BIG, truth="buy_side")
    undet = mk_print(ms(day, 9, 32), 1.0, size=BIG, undetermined=True)
    _fold(acc, call, "buy_side", day, False)
    _fold(acc, undet, "buy_side", day, False)  # method claims where truth refused
    out = summarize(acc, is_bound=False)
    # registered: matched population only, so both nets are one call buy
    assert acc.full.truth_net[day] == pytest.approx(20_000.0)
    assert acc.full.method_net[day] == pytest.approx(20_000.0)
    assert out["net_gauge"]["max_magnitude_error"] == pytest.approx(0.0)
    # unregistered diagnostic: the method's net over everything it claimed is 2x
    diag = out["unregistered_diagnostics"]["full_population_net"]
    assert acc.full_pop_method_net[day] == pytest.approx(40_000.0)
    assert diag["max_magnitude_error"] == pytest.approx(1.0)


def test_gauge_filters_keep_spread_legs_hygiene_and_dust_out_of_the_nets():
    acc = Acc()
    day = "2026-07-20"
    kept = mk_print(ms(day, 9, 31), 1.0, size=BIG, truth="buy_side")
    filtered = mk_print(ms(day, 9, 32), 1.0, size=BIG, truth="buy_side", gauge_eligible=False)
    _fold(acc, kept, "buy_side", day, False)
    _fold(acc, filtered, "sell_side", day, False)
    # both prints count for agreement (the prereg scores every classified print) ...
    assert acc.full.n_truth == 2
    assert acc.full.n_agree == 1
    # ... but only the gauge-eligible one reaches either net
    assert acc.full.truth_net[day] == pytest.approx(20_000.0)
    assert acc.full.method_net[day] == pytest.approx(20_000.0)


def test_dust_and_flags_decide_gauge_eligibility_at_load(tmp_path):
    day = "2026-07-20"
    base = ms(day, 9, 31)
    rows = [
        {
            "ts_ms": base,
            "occ": OCC,
            "u": "SPY",
            "price": 1.0,
            "size": 200,
            "dir": "buy_side",
            "basis": "stream_quote",
        },
        {
            "ts_ms": base,
            "occ": OCC,
            "u": "SPY",
            "price": 1.0,
            "size": 200,
            "dir": "buy_side",
            "basis": "stream_quote",
            "spread": True,
        },
        {
            "ts_ms": base,
            "occ": OCC,
            "u": "SPY",
            "price": 1.0,
            "size": 200,
            "dir": "buy_side",
            "basis": "stream_quote",
            "hyg": True,
        },
        {
            "ts_ms": base,
            "occ": OCC,
            "u": "SPY",
            "price": 0.01,
            "size": 1,
            "dir": "buy_side",
            "basis": "stream_quote",
        },
    ]
    (tmp_path / f"{day}.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    from tools.classification_lab_scorer import load_prints

    prints = load_prints(tmp_path, day, frozenset({"SPY"}))
    assert [p.gauge_eligible for p in prints] == [True, False, False, False]


# --- thresholds -------------------------------------------------------------------


def _spike(agreement=0.90, asymmetry=2.0, mag=0.05):
    return {
        "agreement_premium_weighted": agreement,
        "directional_bias_asymmetry_pp": asymmetry,
        "net_gauge": {"max_magnitude_error": mag},
    }


def test_registered_thresholds_pass_exactly_at_the_boundary():
    assert _viable(
        _spike(agreement=0.85, asymmetry=5.0, mag=0.15), is_bound=False, pooled_sign=0.95
    )


@pytest.mark.parametrize(
    ("kwargs", "pooled"),
    [
        ({"agreement": 0.8499}, 0.95),
        ({"asymmetry": 5.01}, 0.95),
        ({"mag": 0.1501}, 0.95),
        ({"agreement": None}, 0.95),
        ({}, 0.9499),
        ({}, None),
    ],
)
def test_missing_or_failing_any_registered_threshold_is_not_viable(kwargs, pooled):
    assert not _viable(_spike(**kwargs), is_bound=False, pooled_sign=pooled)


def test_a_bound_variant_is_gated_on_its_self_excluded_numbers():
    metrics = _spike(agreement=0.99)
    metrics["excl_self_paired"] = _spike(agreement=0.10)
    assert not _viable(metrics, is_bound=True, pooled_sign=1.0)
    assert _viable(metrics, is_bound=False, pooled_sign=1.0)


# --- session classes + conditioning -----------------------------------------------


def test_monthly_opex_is_the_third_friday():
    assert is_opex(date(2026, 7, 17))
    assert not is_opex(date(2026, 7, 24))
    assert not is_opex(date(2026, 7, 20))


def test_spike_conditioning_takes_the_top_decile_of_volume_minutes():
    minutes = {("2026-07-20", m): m for m in range(20)}
    picked = spike_minutes({("ordinary", "SPY", "open"): minutes})
    # ceil(20 * 0.10) = 2 minutes, the two heaviest
    assert picked[("ordinary", "SPY", "open")] == {("2026-07-20", 19), ("2026-07-20", 18)}


# T5 — the spike decile is ranked inside a session class, never across classes.
def test_spike_conditioning_never_pools_ordinary_and_opex_minutes():
    ordinary = {("2026-07-20", m): 10 for m in range(10)}
    opex = {("2026-07-17", m): 1_000 for m in range(10)}
    picked = spike_minutes({("ordinary", "SPY", "open"): ordinary, ("opex", "SPY", "open"): opex})
    # the heavy OpEx minutes do not crowd the ordinary class out of its own decile
    assert len(picked[("ordinary", "SPY", "open")]) == 1
    assert next(iter(picked[("ordinary", "SPY", "open")]))[0] == "2026-07-20"
    assert next(iter(picked[("opex", "SPY", "open")]))[0] == "2026-07-17"


def test_every_registered_method_variant_is_scored():
    assert method_variants() == [
        ("tick", "naive"),
        ("delta_tick", "residual"),
        ("backpair", "strict_15s"),
        ("backpair", "interp_15s"),
        ("backpair", "strict_30s"),
        ("backpair", "interp_30s"),
        ("backpair", "strict_60s"),
        ("backpair", "interp_60s"),
        ("backpair", "strict_5s_bound"),
        ("backpair", "interp_5s_bound"),
    ]


def test_method_measurability_depends_on_the_inputs_each_method_needs():
    avail = {"chain": {"SPY"}, "greeks": {"SPY"}, "tape": {"SPY"}}
    assert method_measurable("tick", "naive", "MSFT", avail)  # print lab is enough
    assert method_measurable("backpair", "strict_5s_bound", "MSFT", avail)
    assert not method_measurable("backpair", "strict_15s", "MSFT", avail)
    assert not method_measurable("delta_tick", "residual", "MSFT", avail)
    assert method_measurable("delta_tick", "residual", "SPY", avail)
    # SPX rides SPY's tape via the registered proxy
    assert method_measurable("delta_tick", "residual", "SPXW", {**avail, "greeks": {"SPXW"}})


# --- end to end -------------------------------------------------------------------

ORDINARY = ["2026-07-20", "2026-07-21", "2026-07-22", "2026-07-23", "2026-07-24"]
OPEX = "2026-07-17"


def _write_session(
    research, day: str, *, names=("SPY",), tape=True, chain=True, flat_prices=False
) -> None:
    """``flat_prices`` makes every print the same price — the tick test then claims
    nothing anywhere, while the chain sampler's off-centre quotes keep back-pairing
    working, which isolates a method that CANNOT label from a method that labels badly."""
    prints = []
    chain_rows = []
    tape_rows = []
    for name in names:
        occ = f"{name}260730C00600000"
        base = ms(day, 9, 31)
        for i in range(6):
            ts = base + i * 60_000
            price = 1.00 if flat_prices else 1.00 + 0.10 * i
            prints.append(
                {
                    "ts_ms": ts,
                    "occ": occ,
                    "u": name,
                    "price": price,
                    "size": BIG,
                    "dte": 0,
                    "dir": "buy_side" if i % 2 else "sell_side",
                    "basis": "stream_quote",
                    "bid": price - 0.10,
                    "ask": price + 0.10,
                    "q_ts_ms": ts,
                }
            )
            stamp = datetime.fromtimestamp(ts / 1000, tz=_ET).isoformat(timespec="seconds")
            chain_rows.append(
                {
                    "ts": stamp,
                    "q_ts": stamp,
                    "u": name,
                    "occ": occ,
                    # off-centre in flat mode so the quote rule decides without the tick
                    "bid": price - (0.15 if flat_prices else 0.10),
                    "ask": price + (0.05 if flat_prices else 0.10),
                    "iv": 0.20,
                    "delta": 0.5,
                    "gamma": 0.01,
                    "vega": 0.10,
                }
            )
            tape_rows.append({"ts_ms": ts, "sym": name, "price": 600.0 + i * 0.1})
        # one print truth refused to classify -> the overreach denominator
        prints.append(
            {
                "ts_ms": base + 7 * 60_000,
                "occ": occ,
                "u": name,
                "price": 1.00 if flat_prices else 1.55,
                "size": BIG,
                "dir": "undetermined",
                "basis": "none",
                "bid": None,
                "ask": None,
                "q_ts_ms": None,
            }
        )
    written = [("print_lab", prints)]
    if chain:
        written.append(("chain_samples", chain_rows))
    if tape:
        written.append(("underlying_trades", tape_rows))
    for sub, rows in written:
        d = research / sub
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{day}.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")


def _score(research, dates, names=frozenset({"SPY"})):
    return score(
        dates,
        names,
        research / "print_lab",
        research / "chain_samples",
        research / "underlying_trades",
    )


def pick(report, **match):
    """The one cell matching every field — the fixture only fills the open window."""
    match.setdefault("window", "open")
    match.setdefault("session_class", "ordinary")
    return next(c for c in report["cells"] if all(c.get(k) == v for k, v in match.items()))


def test_end_to_end_report_gates_on_five_ordinary_sessions(tmp_path):
    research = tmp_path / "research"
    for day in ORDINARY[:4]:
        _write_session(research, day)
    report = _score(research, ORDINARY[:4])
    assert report["gate"] == {"ordinary_sessions": 4, "required": 5, "met": False}
    assert all(v["viable_all_cells"] is None for v in report["verdicts"])


def test_end_to_end_scores_every_cell_and_never_pools_opex(tmp_path):
    research = tmp_path / "research"
    for day in [*ORDINARY, OPEX]:
        _write_session(research, day)
    report = _score(research, [*ORDINARY, OPEX])
    assert report["gate"]["met"] is True
    assert report["sessions"][OPEX] == "opex"
    assert report["sessions"]["2026-07-20"] == "ordinary"

    classes = {c["session_class"] for c in report["cells"]}
    assert classes == {"ordinary", "opex"}
    # one cell per method x variant x name x window x session class
    assert len(report["cells"]) == len(method_variants()) * 1 * 2 * 2
    assert len(report["verdicts"]) == len(method_variants())
    # the fixture writes no close-window prints, so those cells are unmeasured, never
    # a failed threshold that drags the verdict
    close = pick(report, method="tick", window="close")
    assert close["unmeasured_reason"] == "no_scored_prints"
    assert close["viable"] is None

    tick = pick(report, method="tick")
    metrics = tick["metrics"]["all"]
    # 6 truth prints per session, first has no previous trade; prices only rise, so
    # the tick test claims buy on 5 of 6 and truth alternates sell/buy from print 0.
    assert metrics["n_truth"] == 30
    assert metrics["n_noclaim"] == 5
    assert metrics["n_agree"] == 15
    assert metrics["n_disagree"] == 10
    assert metrics["agreement"] == pytest.approx(0.5)
    assert metrics["n_undetermined"] == 5
    # truth refused to guess on that print; the tick test claims a side anyway
    assert metrics["n_overreach"] == 5
    assert metrics["overreach_rate"] == pytest.approx(1.0)
    assert set(tick["metrics"]) == {"all", "spike", "first5", "rest"}
    # prints land on window minutes 1-6: minutes 1-4 are the first-5 slice, 5-6 the rest
    assert tick["metrics"]["first5"]["n_truth"] == 20
    assert tick["metrics"]["rest"]["n_truth"] == 10
    assert tick["metrics"]["all"]["net_gauge"]["n_observations"] == 5

    opex_cells = [
        c for c in report["cells"] if c["session_class"] == "opex" and c["metrics"].get("all")
    ]
    assert opex_cells
    assert all(c["metrics"]["all"]["net_gauge"]["n_observations"] == 1 for c in opex_cells)


# B6 — the pooled sign-agreement unit, and its realized resolution.
def test_pooled_sign_agreement_unit_beats_the_per_cell_resolution(tmp_path):
    research = tmp_path / "research"
    for day in ORDINARY:
        _write_session(research, day, names=("SPY", "QQQ"))
    report = _score(research, ORDINARY, frozenset({"SPY", "QQQ"}))
    verdict = next(v for v in report["verdicts"] if v["variant"] == "naive")
    pooled = verdict["net_sign_pooled"]
    cell = pick(report, method="tick", name="SPY")
    # per cell the unit is 5 sessions (resolution 0.20); pooled it is every
    # (session x name x window) observation in the class, which resolves finer
    assert cell["metrics"]["all"]["net_gauge"]["sign_agreement_resolution"] == pytest.approx(0.2)
    assert pooled["n_observations"] > cell["metrics"]["spike"]["net_gauge"]["n_observations"]
    assert pooled["sign_agreement_resolution"] == pytest.approx(1 / pooled["n_observations"])
    assert report["thresholds"]["net_sign_unit"] == "pooled_session_name_window"
    assert report["thresholds"]["net_sign_agreement"] == 0.95  # value unchanged


def test_a_spike_cell_can_have_a_single_session_and_still_reports_its_resolution(tmp_path):
    research = tmp_path / "research"
    for day in ORDINARY:
        _write_session(research, day)
    report = _score(research, ORDINARY)
    spike = pick(report, method="tick")["metrics"]["spike"]
    assert spike["net_gauge"]["n_observations"] == 1
    assert spike["net_gauge"]["sign_agreement_resolution"] == pytest.approx(1.0)
    assert spike["net_gauge"]["correlation"] is None


# B2/B8 — an absent input is unmeasured, and the proxy is reported separately.
def test_absent_underlying_tape_makes_delta_cells_unmeasured_not_failed(tmp_path):
    research = tmp_path / "research"
    for day in ORDINARY:
        _write_session(research, day, tape=False)
    report = _score(research, ORDINARY)
    cell = pick(report, method="delta_tick")
    assert cell["unmeasured"] is True
    assert cell["unmeasured_reason"] == "no_input"
    assert cell["sessions_measured"] == 0
    assert cell["viable"] is None  # never False on a structural gap
    verdict = next(v for v in report["verdicts"] if v["method"] == "delta_tick")
    assert verdict["cells_gating"] == 0
    assert verdict["viable_all_cells"] is None
    assert report["inputs"]["2026-07-20"]["underlying_trades"] == []
    # the tick test needs no tape, so it is still measured on the same sessions
    assert pick(report, method="tick")["unmeasured"] is False


def test_a_name_without_chain_coverage_does_not_drag_backpairing_to_false(tmp_path):
    research = tmp_path / "research"
    for day in ORDINARY:
        _write_session(research, day, names=("SPY",))
    # MSFT is scored but the chain sampler never covered it
    report = _score(research, ORDINARY, frozenset({"SPY", "MSFT"}))
    msft = pick(report, name="MSFT", variant="strict_15s")
    assert msft["unmeasured"] is True
    assert msft["unmeasured_reason"] == "no_input"
    assert msft["viable"] is None
    # MSFT has no prints in the capture either, so even the print-lab-only methods have
    # nothing to score — but that is "no_scored_prints", a different fact from "no_input"
    assert pick(report, name="MSFT", method="tick")["unmeasured_reason"] == "no_scored_prints"
    verdict = next(v for v in report["verdicts"] if v["variant"] == "strict_15s")
    # MSFT open+close and SPY close are all unmeasured; only SPY open gates
    assert verdict["cells_unmeasured"] == 3
    assert verdict["cells_gating"] == 1
    assert [c["name"] for c in report["cells"] if c["variant"] == "strict_15s"].count("MSFT") == 2


def test_spx_proxy_cells_are_tagged_and_kept_out_of_the_all_cells_verdict(tmp_path):
    research = tmp_path / "research"
    for day in ORDINARY:
        _write_session(research, day, names=("SPY", "SPXW"))
    report = _score(research, ORDINARY, frozenset({"SPY", "SPXW"}))
    proxy = pick(report, name="SPXW", method="delta_tick")
    assert proxy["underlying_proxy"] == "SPY x10"
    assert "underlying_proxy" not in pick(report, name="SPY", method="delta_tick")
    verdict = next(v for v in report["verdicts"] if v["method"] == "delta_tick")
    assert verdict["cells_proxy"] == 2  # SPXW open + close
    assert verdict["cells_gating"] == 1  # SPY open only (close window has no prints)
    assert "proxy_cells_viable" in verdict
    # back-pairing on SPXW is a real measurement, not a proxy
    assert "underlying_proxy" not in pick(report, name="SPXW", variant="strict_15s")


# N5/N6/N7/N8 — the labels and caveats that keep a number from being misread.
def test_report_carries_the_registered_caveats_and_measured_labels(tmp_path):
    research = tmp_path / "research"
    for day in ORDINARY:
        _write_session(research, day)
    report = _score(research, ORDINARY)
    caveats = " ".join(report["caveats"])
    assert "SUBSCRIBED SLICE" in caveats
    assert "BOUND" in caveats
    assert "NON-CAUSAL" in caveats
    assert "QUOTE_EPS" in caveats
    assert "SPY x10" in caveats
    assert "2026-07-17 forward" in caveats
    bound = pick(report, variant="strict_5s_bound")
    assert bound["is_bound"] is True
    assert bound["gate_population"] == "excl_self_paired"
    assert bound["nominal_interval_s"] == 5
    # the bound's timeline is the print lab, not the chain sampler — its cadence is
    # unrelated to the sampler's measured spacing, so the label is omitted
    assert "measured_spacing_s" not in bound
    interp = pick(report, variant="interp_30s")
    assert interp["non_causal"] is True
    assert interp["nominal_interval_s"] == 30
    assert interp["measured_spacing_s"] == pytest.approx(60.0)  # fixture samples 60s apart
    coverage = report["inputs"]["2026-07-20"]["chain_coverage_by_root"]
    assert coverage["SPY"]["samples"] == 6
    assert coverage["SPY"]["greek_samples"] == 6
    assert report["inputs"]["2026-07-20"]["median_inter_sample_spacing_s"] == pytest.approx(60.0)


def test_report_names_both_the_base_prereg_and_the_registered_addendum(tmp_path):
    research = tmp_path / "research"
    for day in ORDINARY:
        _write_session(research, day)
    report = _score(research, ORDINARY)
    assert report["prereg"] == "References/CLASSIFICATION_LAB_PREREG_2026-07-16.md"
    assert report["addenda"] == ["References/CLASSIFICATION_LAB_PREREG_2026-07-16_ADDENDUM_A.md"]
    note = report["thresholds"]["net_sign_unit_note"]
    assert note.startswith("References/CLASSIFICATION_LAB_PREREG_2026-07-16_ADDENDUM_A.md")
    assert "pending" not in note
    assert report["thresholds"]["net_sign_agreement"] == 0.95  # value still unchanged


def _cell_key(method, variant, name, conditioning):
    return ("ordinary", method, variant, name, "open", conditioning)


def _perfect_acc(dates) -> Acc:
    """A cell the method labelled perfectly: agreement 1.0, zero bias, nets identical."""
    acc = Acc()
    for i, day in enumerate(dates):
        call = mk_print(ms(day, 9, 31), 1.0, size=BIG, truth="buy_side")
        put = mk_print(ms(day, 9, 32), 1.0, occ=OCC_PUT, size=BIG, truth="sell_side")
        _fold(acc, call, "buy_side", day, False)
        _fold(acc, put, "sell_side", day, False)
        assert i >= 0
    return acc


def test_proxy_observations_never_enter_the_real_pool():
    dates = ["2026-07-20", "2026-07-21", "2026-07-22"]
    cells = {
        _cell_key("delta_tick", "residual", "SPY", "all"): _perfect_acc(dates),
        _cell_key("delta_tick", "residual", "SPXW", "all"): _perfect_acc(dates),
        _cell_key("backpair", "strict_15s", "SPXW", "all"): _perfect_acc(dates),
    }
    real, proxy = pool_net_observations(cells)
    # SPY's three sessions only — SPXW's proxied nets pool on their own
    assert real[("ordinary", "delta_tick", "residual", "all")]["n_observations"] == 3
    assert proxy[("ordinary", "delta_tick", "residual", "all")]["n_observations"] == 3
    # back-pairing on SPXW is a real measurement, so it stays in the real pool
    assert ("ordinary", "backpair", "strict_15s", "all") in real
    assert ("ordinary", "backpair", "strict_15s", "all") not in proxy


def test_a_proxy_row_is_judged_even_when_the_real_pool_is_empty():
    # The only delta-adjusted cells are proxied, so the real pool has nothing in it.
    # Borrowing that empty pool would score the proxy row False on a missing number.
    dates = ["2026-07-20", "2026-07-21", "2026-07-22"]
    cells = {
        _cell_key("delta_tick", "residual", "SPXW", cond): _perfect_acc(dates)
        for cond in ("all", "spike")
    }
    real, proxy = pool_net_observations(cells)
    assert ("ordinary", "delta_tick", "residual", "spike") not in real
    assert proxy[("ordinary", "delta_tick", "residual", "spike")]["sign_agreement"] == 1.0

    report = _report(
        cells,
        {("ordinary", "delta_tick", "residual", "SPXW"): 3},
        dict.fromkeys([*dates, "2026-07-23", "2026-07-24"], "ordinary"),
        {},
        5,
        frozenset({"SPXW"}),
    )
    row = next(c for c in report["cells"] if c["window"] == "open")
    assert row["underlying_proxy"] == "SPY x10"
    assert row["unmeasured"] is False
    assert row["viable"] is True  # judged on the proxy pool, not on an absent one
    verdict = report["verdicts"][0]
    assert verdict["net_sign_pooled"] == {}  # the real pool stayed empty
    assert verdict["proxy_cells_viable"] == 1
    assert verdict["viable_all_cells"] is None  # no real cell to decide the method on


def test_verdicts_carry_the_claim_rate_behind_every_agreement_figure(tmp_path):
    research = tmp_path / "research"
    for day in ORDINARY:
        _write_session(research, day)
    report = _score(research, ORDINARY)
    verdict = next(v for v in report["verdicts"] if v["variant"] == "naive")
    # the tick test cannot label the first print of each contract (no predecessor):
    # 25 of 30 truth prints claimed
    assert verdict["claim_rate_all"] == {
        "claimed": 25,
        "n_truth": 30,
        "rate": pytest.approx(25 / 30),
    }
    assert verdict["claim_rate_spike"]["n_truth"] > 0
    assert " ".join(report["caveats"]).count("claim_rate") == 1


def test_a_gate_slice_the_method_labelled_nothing_in_is_unmeasured_not_failed(tmp_path):
    research = tmp_path / "research"
    for day in ORDINARY:
        _write_session(research, day, flat_prices=True)
    report = _score(research, ORDINARY)
    # every price is identical, so the tick test is a zero tick on every print after the
    # first — it claims nothing anywhere, which is a coverage gap, not a failed threshold
    tick = pick(report, method="tick")
    assert tick["metrics"]["spike"]["n_truth"] > 0
    assert tick["metrics"]["spike"]["n_agree"] + tick["metrics"]["spike"]["n_disagree"] == 0
    assert tick["unmeasured_reason"] == "no_scored_prints"
    assert tick["viable"] is None
    verdict = next(v for v in report["verdicts"] if v["variant"] == "naive")
    assert verdict["cells_gating"] == 0
    assert verdict["viable_all_cells"] is None
    # back-pairing still has quotes to work with, so it stays measured on the same data
    assert pick(report, variant="strict_15s")["unmeasured"] is False
