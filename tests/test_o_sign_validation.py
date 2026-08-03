"""Pinned tests for the o sign-validation prereg (2026-08-03) — these lock the
sign conventions and mechanics BEFORE any fetch, per the registration."""

import numpy as np
import pytest

from tools.o_sign_validation import (
    COHERENCE_MIN,
    conjunction_power,
    match_event,
    prevailing_quote_idx_strict,
    sigma_net,
    sign_stock_prints,
    taker_stock_side,
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
