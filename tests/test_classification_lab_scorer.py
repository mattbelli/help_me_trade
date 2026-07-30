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
    Acc,
    Print,
    QuoteNode,
    Sample,
    _net_sign,
    _viable,
    _window_of,
    backpair_labels,
    bound_timeline,
    delta_adjusted_labels,
    downsample_chain,
    is_opex,
    method_variants,
    score,
    spike_minutes,
    summarize,
    tick_labels,
)

_ET = ZoneInfo("America/New_York")
OCC = "SPY260730C00600000"
OCC_PUT = "SPY260730P00600000"


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
        premium=price * size * 100.0,
        truth=truth,
        truth_undetermined=undetermined,
        q_bid=bid,
        q_ask=ask,
        q_ts_ms=q_ts_ms,
    )


# --- scope: the gauge windows, shoulders excluded ---------------------------------


def test_window_of_covers_both_gauge_windows_and_excludes_shoulders():
    assert _window_of(ms("2026-07-20", 9, 30)) == ("open", 0)
    assert _window_of(ms("2026-07-20", 9, 34, 59)) == ("open", 4)
    assert _window_of(ms("2026-07-20", 9, 59, 59)) == ("open", 29)
    assert _window_of(ms("2026-07-20", 15, 30)) == ("close", 0)
    assert _window_of(ms("2026-07-20", 15, 59)) == ("close", 29)
    # print-lab shoulders (09:25-09:30, 10:00-10:05) are outside the gauge windows
    assert _window_of(ms("2026-07-20", 9, 27)) is None
    assert _window_of(ms("2026-07-20", 10, 2)) is None
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


# --- method 2: delta-adjusted tick test -------------------------------------------


def _delta_fixture(second_price: float):
    t0 = ms("2026-07-20", 9, 31)
    t1 = ms("2026-07-20", 9, 32)
    prints = [mk_print(t0, 1.00), mk_print(t1, second_price)]
    chain = {
        OCC: [
            Sample(t0 - 1000, 0.99, 1.01, 0.20, 0.5, 0.01, 0.10),
            Sample(t1 - 1000, 0.99, 1.01, 0.22, 0.5, 0.01, 0.10),
        ]
    }
    tape = {"SPY": ([t0 - 500, t1 - 500], [600.0, 600.4])}
    return prints, chain, tape


def test_delta_adjusted_residual_matches_the_registered_formula():
    # resid = dP - [d*dS + 0.5*g*dS^2 + V*dSigma]
    #       = 0.30 - [0.5*0.4 + 0.5*0.01*0.16 + 0.10*0.02] = 0.0972 > 0 -> buy
    prints, chain, tape = _delta_fixture(1.30)
    assert delta_adjusted_labels(prints, chain, tape) == [None, "buy_side"]


def test_delta_adjustment_can_flip_the_naive_tick_verdict():
    # price ROSE (naive tick says buy) but by less than the underlying+vol move:
    # resid = 0.15 - 0.2028 = -0.0528 -> sell. This is the whole point of method 2.
    prints, chain, tape = _delta_fixture(1.15)
    assert tick_labels(prints) == [None, "buy_side"]
    assert delta_adjusted_labels(prints, chain, tape) == [None, "sell_side"]


def test_delta_adjusted_claims_nothing_without_greeks_or_underlying_tape():
    prints, chain, tape = _delta_fixture(1.30)
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
    # SPY 600.00 -> 600.40 proxies SPX 6000.0 -> 6004.0, dS = 4.0
    # resid = 0.30 - 0.05*4.0 = 0.10 > 0 -> buy
    tape = {"SPY": ([t0 - 500, t1 - 500], [600.0, 600.4])}
    assert delta_adjusted_labels(prints, chain, tape) == [None, "buy_side"]
    # no SPY tape -> no proxy -> no claim (never falls back to something else)
    assert delta_adjusted_labels(prints, chain, {"SPXW": ([t0], [6000.0])}) == [None, None]


# --- method 3: sampled-quote back-pairing -----------------------------------------


def test_downsampling_keeps_the_first_sample_in_each_interval_bucket():
    base = ms("2026-07-20", 9, 31)
    chain = {OCC: [Sample(base + i * 15_000, 1.0, 1.2, None, None, None, None) for i in range(5)]}
    assert [n.ts_ms for n in downsample_chain(chain, 15)[OCC]] == [base + i * 15_000 for i in range(5)]
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


# --- metrics ----------------------------------------------------------------------


def test_agreement_counts_no_claims_against_the_truth_denominator():
    acc = Acc()
    acc.n_truth, acc.n_agree, acc.n_disagree, acc.n_noclaim = 10, 6, 3, 1
    acc.prem_truth, acc.prem_agree = 1000.0, 900.0
    out = summarize(acc)
    assert out["agreement"] == pytest.approx(0.6)
    assert out["agreement_premium_weighted"] == pytest.approx(0.9)


def test_directional_bias_is_the_premium_weighted_asymmetry():
    acc = Acc()
    acc.prem_method_sell, acc.prem_method_sell_truth_buy = 1000.0, 300.0
    acc.prem_method_buy, acc.prem_method_buy_truth_sell = 2000.0, 100.0
    out = summarize(acc)
    assert out["p_truth_buy_given_method_sell_pw"] == pytest.approx(0.30)
    assert out["p_truth_sell_given_method_buy_pw"] == pytest.approx(0.05)
    assert out["directional_bias_asymmetry_pp"] == pytest.approx(25.0)


def test_overreach_is_scored_on_the_prints_truth_refused_to_guess():
    acc = Acc()
    acc.n_undetermined, acc.n_overreach = 10, 4
    acc.prem_undetermined, acc.prem_overreach = 5000.0, 2000.0
    out = summarize(acc)
    assert out["overreach_rate"] == pytest.approx(0.4)
    assert out["overreach_rate_premium_weighted"] == pytest.approx(0.4)


def test_net_gauge_fidelity_reports_correlation_sign_agreement_and_max_error():
    acc = Acc()
    truth = {"2026-07-20": 100.0, "2026-07-21": -50.0, "2026-07-22": 200.0}
    method = {"2026-07-20": 110.0, "2026-07-21": -40.0, "2026-07-22": 150.0}
    acc.truth_net.update(truth)
    acc.method_net.update(method)
    net = summarize(acc)["net_gauge"]
    assert net["n_sessions"] == 3
    assert net["sign_agreement"] == pytest.approx(1.0)
    assert net["max_magnitude_error"] == pytest.approx(0.25)  # |150-200|/200
    expected = statistics.correlation(list(truth.values()), list(method.values()))
    assert net["correlation"] == pytest.approx(expected)


def test_net_gauge_sign_agreement_catches_a_flipped_window():
    acc = Acc()
    acc.truth_net.update({"a": 100.0, "b": -50.0})
    acc.method_net.update({"a": 100.0, "b": 50.0})
    net = summarize(acc)["net_gauge"]
    assert net["sign_agreement"] == pytest.approx(0.5)
    assert net["correlation"] is None  # fewer than 3 sessions


def test_net_premium_follows_the_gauge_bull_bear_convention():
    assert _net_sign("C", "buy_side") == 1.0
    assert _net_sign("P", "sell_side") == 1.0
    assert _net_sign("P", "buy_side") == -1.0
    assert _net_sign("C", "sell_side") == -1.0


# --- thresholds -------------------------------------------------------------------


def _spike(agreement=0.90, asymmetry=2.0, sign=1.0, mag=0.05):
    return {
        "agreement_premium_weighted": agreement,
        "directional_bias_asymmetry_pp": asymmetry,
        "net_gauge": {"sign_agreement": sign, "max_magnitude_error": mag},
    }


def test_registered_thresholds_pass_exactly_at_the_boundary():
    assert _viable(_spike(agreement=0.85, asymmetry=5.0, sign=0.95, mag=0.15))


@pytest.mark.parametrize(
    "kwargs",
    [
        {"agreement": 0.8499},
        {"asymmetry": 5.01},
        {"sign": 0.9499},
        {"mag": 0.1501},
        {"agreement": None},
        {"sign": None},
    ],
)
def test_missing_or_failing_any_registered_threshold_is_not_viable(kwargs):
    assert not _viable(_spike(**kwargs))


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


# --- end to end -------------------------------------------------------------------

ORDINARY = ["2026-07-20", "2026-07-21", "2026-07-22", "2026-07-23", "2026-07-24"]
OPEX = "2026-07-17"


def _write_session(research, day: str) -> None:
    prints = []
    chain = []
    tape = []
    base = ms(day, 9, 31)
    for i in range(6):
        ts = base + i * 60_000
        price = 1.00 + 0.10 * i
        prints.append(
            {
                "ts_ms": ts,
                "occ": OCC,
                "u": "SPY",
                "price": price,
                "size": 50,
                "dte": 0,
                "dir": "buy_side" if i % 2 else "sell_side",
                "basis": "stream_quote",
                "bid": price - 0.10,
                "ask": price + 0.10,
                "q_ts_ms": ts,
            }
        )
        chain.append(
            {
                "ts": datetime.fromtimestamp(ts / 1000, tz=_ET).isoformat(timespec="seconds"),
                "u": "SPY",
                "occ": OCC,
                "bid": price - 0.10,
                "ask": price + 0.10,
                "iv": 0.20,
                "delta": 0.5,
                "gamma": 0.01,
                "vega": 0.10,
            }
        )
        tape.append({"ts_ms": ts, "sym": "SPY", "price": 600.0 + i * 0.1})
    # one print truth refused to classify -> the overreach denominator
    prints.append(
        {
            "ts_ms": base + 7 * 60_000,
            "occ": OCC,
            "u": "SPY",
            "price": 1.55,
            "size": 10,
            "dir": "undetermined",
            "basis": "none",
            "bid": None,
            "ask": None,
            "q_ts_ms": None,
        }
    )
    for sub, rows in (("print_lab", prints), ("chain_samples", chain), ("underlying_trades", tape)):
        d = research / sub
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{day}.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")


def test_end_to_end_report_gates_on_five_ordinary_sessions(tmp_path):
    research = tmp_path / "research"
    for day in ORDINARY[:4]:
        _write_session(research, day)
    report = score(
        ORDINARY[:4],
        frozenset({"SPY"}),
        research / "print_lab",
        research / "chain_samples",
        research / "underlying_trades",
    )
    assert report["gate"] == {"ordinary_sessions": 4, "required": 5, "met": False}
    assert all(v["viable_all_cells"] is None for v in report["verdicts"])


def test_end_to_end_scores_every_cell_and_never_pools_opex(tmp_path):
    research = tmp_path / "research"
    for day in [*ORDINARY, OPEX]:
        _write_session(research, day)
    report = score(
        [*ORDINARY, OPEX],
        frozenset({"SPY"}),
        research / "print_lab",
        research / "chain_samples",
        research / "underlying_trades",
    )
    assert report["gate"]["met"] is True
    assert report["sessions"][OPEX] == "opex"
    assert report["sessions"]["2026-07-20"] == "ordinary"

    classes = {c["session_class"] for c in report["cells"]}
    assert classes == {"ordinary", "opex"}
    # one cell per method x variant x name x window x session class
    assert len(report["cells"]) == len(method_variants()) * 2
    # verdicts ride ordinary sessions only
    assert len(report["verdicts"]) == len(method_variants())

    tick = next(
        c for c in report["cells"] if c["method"] == "tick" and c["session_class"] == "ordinary"
    )
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

    opex_cells = [c for c in report["cells"] if c["session_class"] == "opex"]
    assert all(c["metrics"]["all"]["net_gauge"]["n_sessions"] == 1 for c in opex_cells)


def test_end_to_end_delta_method_is_unavailable_without_the_underlying_tape(tmp_path):
    research = tmp_path / "research"
    for day in ORDINARY:
        _write_session(research, day)
    for day in ORDINARY:
        (research / "underlying_trades" / f"{day}.jsonl").unlink()
    report = score(
        ORDINARY,
        frozenset({"SPY"}),
        research / "print_lab",
        research / "chain_samples",
        research / "underlying_trades",
    )
    cell = next(c for c in report["cells"] if c["method"] == "delta_tick")
    metrics = cell["metrics"]["all"]
    assert metrics["n_noclaim"] == metrics["n_truth"]
    assert cell["viable"] is False
    assert report["inputs"]["2026-07-20"]["underlying_trades"] == []
