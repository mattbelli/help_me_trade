"""Black-76 IV + greeks — identities, inversions, and the fail-soft edges.

Every number here is checked against something independent: a textbook closed
form, a finite difference, or a put-call parity identity that must hold whatever
the implementation does.
"""

from __future__ import annotations

import math

from signals.black76 import (
    black76_price,
    greeks,
    implied_forward,
    implied_vol,
    norm_cdf,
)


def test_atm_price_matches_closed_form() -> None:
    # F = K = 100, T = 1, sigma = 0.2, no discounting: the textbook value is
    # 100*(N(0.1) - N(-0.1)) = 7.9656.
    assert round(black76_price(100, 100, 1.0, 1.0, 0.2, True), 4) == 7.9656


def test_put_call_parity_holds_in_the_pricer() -> None:
    call = black76_price(100, 95, 0.35, 0.985, 0.3, True)
    put = black76_price(100, 95, 0.35, 0.985, 0.3, False)
    assert abs((call - put) - 0.985 * (100 - 95)) < 1e-12


def test_implied_vol_round_trips_across_strikes_and_vols() -> None:
    for sigma in (0.08, 0.2, 0.65, 1.8):
        for strike in (80, 100, 125):
            for is_call in (True, False):
                price = black76_price(100, strike, 0.35, 0.985, sigma, is_call)
                back = implied_vol(price, 100, strike, 0.35, 0.985, is_call)
                assert back is not None
                assert abs(back - sigma) < 1e-4


def test_implied_vol_is_none_outside_the_no_arbitrage_band() -> None:
    # Below intrinsic and above the option's own ceiling there is no solution —
    # a crossed quote or an American early-exercise premium, not a volatility.
    assert implied_vol(1.0, 100, 90, 0.5, 1.0, True) is None  # under intrinsic
    assert implied_vol(101.0, 100, 90, 0.5, 1.0, True) is None  # over ceiling
    assert implied_vol(0.0, 100, 90, 0.5, 1.0, True) is None
    assert implied_vol(5.0, 100, 90, 0.0, 1.0, True) is None  # expired


def test_implied_forward_recovers_a_synthetic_chain() -> None:
    forward, discount, sigma = 741.5, 0.9962, 0.18
    pairs = [
        (
            float(k),
            black76_price(forward, k, 0.25, discount, sigma, True),
            black76_price(forward, k, 0.25, discount, sigma, False),
        )
        for k in range(700, 790, 5)
    ]
    got = implied_forward(pairs, discount)
    assert got is not None
    assert abs(got - forward) < 1e-6


def test_implied_forward_survives_one_garbage_strike() -> None:
    """The median is the point: a single crossed quote must not move the forward."""
    forward, discount, sigma = 741.5, 0.9962, 0.18
    pairs = [
        (
            float(k),
            black76_price(forward, k, 0.25, discount, sigma, True),
            black76_price(forward, k, 0.25, discount, sigma, False),
        )
        for k in range(730, 755, 5)
    ]
    pairs.append((742.0, 90.0, 0.02))  # nonsense two-sided quote at the money
    got = implied_forward(pairs, discount)
    assert got is not None
    assert abs(got - forward) < 0.5


def test_implied_forward_needs_two_sided_quotes() -> None:
    assert implied_forward([(100.0, 5.0, 0.0), (105.0, 3.0, 2.0)], 1.0) is None
    assert implied_forward([], 1.0) is None


def test_delta_difference_is_the_discount_factor() -> None:
    """Parity differentiates: dC/dF - dP/dF = D, exactly, at every strike."""
    for strike in (80.0, 100.0, 130.0):
        call = greeks(100, strike, 0.35, 0.985, 0.3, True)
        put = greeks(100, strike, 0.35, 0.985, 0.3, False)
        assert abs((call["delta"] - put["delta"]) - 0.985) < 1e-9
        assert abs(call["gamma"] - put["gamma"]) < 1e-12
        assert abs(call["vega"] - put["vega"]) < 1e-12


def test_greeks_match_finite_differences() -> None:
    spot, forward, strike, t, discount, sigma = 741.0, 744.0, 740.0, 0.25, 0.99, 0.2
    got = greeks(forward, strike, t, discount, sigma, True, spot=spot)

    def price_at_spot(s: float) -> float:
        # F is linear in S, so bumping spot bumps the forward proportionally.
        return black76_price(forward * (s / spot), strike, t, discount, sigma, True)

    h = 0.01
    fd_delta = (price_at_spot(spot + h) - price_at_spot(spot - h)) / (2 * h)
    fd_gamma = (
        price_at_spot(spot + h) - 2 * price_at_spot(spot) + price_at_spot(spot - h)
    ) / (h * h)
    assert abs(got["delta"] - fd_delta) < 1e-6
    assert abs(got["gamma"] - fd_gamma) < 1e-8

    hv = 1e-5
    fd_vega = (
        black76_price(forward, strike, t, discount, sigma + hv, True)
        - black76_price(forward, strike, t, discount, sigma - hv, True)
    ) / (2 * hv) / 100.0
    assert abs(got["vega"] - fd_vega) < 1e-6

    rate = -math.log(discount) / t
    ht = 1e-5
    fd_theta = -(
        black76_price(forward, strike, t + ht, math.exp(-rate * (t + ht)), sigma, True)
        - black76_price(forward, strike, t - ht, math.exp(-rate * (t - ht)), sigma, True)
    ) / (2 * ht) / 365.0
    assert abs(got["theta"] - fd_theta) < 1e-8


def test_norm_cdf_reference_points() -> None:
    assert abs(norm_cdf(0.0) - 0.5) < 1e-12
    assert abs(norm_cdf(1.96) - 0.975) < 1e-4
    assert abs(norm_cdf(-1.96) - 0.025) < 1e-4
