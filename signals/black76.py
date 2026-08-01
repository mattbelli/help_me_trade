"""Black-76 implied volatility + greeks, priced off the chain's OWN forward.

Why this exists: Alpaca's greeks have holes, and they are not where you would
guess. ⚠️ My first measurement sampled only the default expiry — which that day
WAS the 0DTE — and I generalised it to "Alpaca serves no greeks on our plan".
Wrong. Measured properly across SPY/QQQ/NVDA/AAPL/IWM on 2026-07-31, the vendor
publishes greeks + IV on every expiry EXCEPT the one expiring today, where it
publishes none at all (0 of 500 SPY contracts); and even beyond 0DTE its coverage
is partial (157 of 339 SPY contracts one week out — the illiquid strikes go
without). So this module is a FALLBACK, not a replacement: vendor values are used
wherever they exist and never overwritten, and these fill the holes — the whole
0DTE board, plus the wings on every other expiry. The payload's ``greeks_source``
says which you are looking at (``vendor`` / ``black76`` / ``mixed``), because a
column that silently blends two models is worse than one that admits it.

**Why Black-76 and not Black-Scholes.** Databento's own write-up flags the two
things that make textbook Black-Scholes wrong for US equity options: dividends
(which need an estimate we do not buy) and American early exercise (which has no
closed form at all). Black-76 prices off a FORWARD instead of a spot, and the
forward is recoverable from the chain itself — so the dividend, the risk-free
rate and the borrow cost never have to be supplied. They are already inside the
quotes, and :func:`implied_forward` reads them back out.

That is the whole trick. Put-call parity says ``C - P = D·(F - K)``, so with the
discount factor D in hand the forward falls straight out of a single well-quoted
strike: ``F = K + (C - P)/D``. No dividend feed and no borrow assumption — both
are already inside the quotes, and this reads them back out. It stays right for a
hard-to-borrow name or through a dividend change, which a hardcoded yield would
not.

**D comes from the Treasury curve, not from the options.** ⚠️ An earlier cut
tried to fit D and F jointly (parity is linear in K, so OLS on ``C - P`` vs ``K``
has slope ``-D``). Measured on live SPY, that recovered D = 1.004 on a 12-DTE
expiry and 1.011 on a 30-DTE one — negative implied rates, because the slope is
the difference of noisy mids over a short strike range. Widening the strike band
to condition the slope made it worse, not better: deep-ITM mids are noisy by
±$0.80 against ±$0.03 at the money, and least squares hands those points the most
leverage. The lesson is that the discount factor is not option-implied
information at all — it is a rate we already have from ``signals.macro_substrate``
— whereas the forward IS (it carries dividends and borrow). Take each from the
source that actually knows it.

**What is still an approximation, stated plainly.** Parity is exact for European
options and only approximate for the American ones we quote, because early
exercise makes it an inequality. The error is negligible near the money and grows
deep in the money, which is why the regression only ever looks at near-ATM
strikes. Per-side IV inverted from a deep-ITM American option is the weakest
number here; it fails soft to ``None`` rather than pretending.

Conventions, chosen to match how a chain is read rather than how a paper is
written: **vega per 1 volatility point** (a 1% move in IV), **theta per calendar
day**, and **delta/gamma with respect to SPOT** — the forward is the pricing
variable but nobody reads a delta column expecting a forward delta.
"""

from __future__ import annotations

import math

# Bisection bounds. Price is monotone in sigma, so a bracket is all we need and
# the solve cannot diverge the way a Newton step off a near-zero vega can.
_IV_LOW, _IV_HIGH = 1e-4, 5.0
_IV_TOL = 1e-6
_IV_MAX_ITERS = 100

# Strikes averaged for the forward. Each is an independent parity reading, so a
# median over a handful survives one crossed or stale quote; taking only the
# single nearest strike would not. Near-ATM only — that is where spreads are
# tightest and where American ≈ European actually holds.
_FORWARD_STRIKES = 6
_FORWARD_MIN_POINTS = 3

_SQRT_2PI = math.sqrt(2.0 * math.pi)


def norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / _SQRT_2PI


def implied_forward(
    pairs: list[tuple[float, float, float]], discount: float
) -> float | None:
    """Forward from put-call parity, ``F = K + (C - P)/D``, at the money.

    ``pairs`` is ``(strike, call_mid, put_mid)``; ``discount`` is D from the rate
    curve. Returns the median estimate over the nearest-ATM strikes, or None if
    too few carry a two-sided market on both legs.

    Strikes are ranked by ``|C - P|`` — that quantity is smallest at the money,
    which locates ATM from the quotes alone, with no spot price needed (index
    roots have no stock quote to use).
    """
    usable = [(k, c, p) for k, c, p in pairs if k > 0 and c > 0 and p > 0]
    if len(usable) < _FORWARD_MIN_POINTS or discount <= 0:
        return None
    usable.sort(key=lambda kcp: abs(kcp[1] - kcp[2]))
    estimates = sorted(
        k + (c - p) / discount for k, c, p in usable[:_FORWARD_STRIKES]
    )
    n = len(estimates)
    forward = (
        estimates[n // 2]
        if n % 2
        else 0.5 * (estimates[n // 2 - 1] + estimates[n // 2])
    )
    return forward if math.isfinite(forward) and forward > 0 else None


def black76_price(
    forward: float, strike: float, t_years: float, discount: float,
    sigma: float, is_call: bool,
) -> float:
    if sigma <= 0 or t_years <= 0:
        intrinsic = forward - strike if is_call else strike - forward
        return discount * max(intrinsic, 0.0)
    vol_t = sigma * math.sqrt(t_years)
    d1 = (math.log(forward / strike) + 0.5 * sigma * sigma * t_years) / vol_t
    d2 = d1 - vol_t
    if is_call:
        return discount * (forward * norm_cdf(d1) - strike * norm_cdf(d2))
    return discount * (strike * norm_cdf(-d2) - forward * norm_cdf(-d1))


def implied_vol(
    price: float, forward: float, strike: float, t_years: float,
    discount: float, is_call: bool,
) -> float | None:
    """Invert Black-76 for sigma by bisection. None when no solution exists.

    A quote below intrinsic or above the option's own ceiling has no implied vol
    — that is a crossed/stale market or an American early-exercise premium, and
    returning None is the honest answer. Deep-ITM American options are exactly
    where this fires.
    """
    if price <= 0 or forward <= 0 or strike <= 0 or t_years <= 0 or discount <= 0:
        return None
    intrinsic = discount * max(
        (forward - strike) if is_call else (strike - forward), 0.0
    )
    ceiling = discount * (forward if is_call else strike)
    if price <= intrinsic or price >= ceiling:
        return None

    lo, hi = _IV_LOW, _IV_HIGH
    if black76_price(forward, strike, t_years, discount, hi, is_call) < price:
        return None  # beyond the bracket — implausible vol, not a real reading
    for _ in range(_IV_MAX_ITERS):
        if hi - lo <= _IV_TOL:
            break
        mid = 0.5 * (lo + hi)
        if black76_price(forward, strike, t_years, discount, mid, is_call) < price:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def greeks(
    forward: float, strike: float, t_years: float, discount: float,
    sigma: float, is_call: bool, spot: float | None = None,
) -> dict[str, float]:
    """Delta/gamma/theta/vega. Delta + gamma are w.r.t. SPOT, the rest per Black-76.

    ``F = S·e^{(r-q)T}`` is linear in S, so ``dF/dS`` is exactly ``F/S`` and the
    spot greeks are the forward greeks scaled by that ratio — no separate
    dividend yield needed. Index roots have no stock quote, and there ``spot``
    falls back to the forward (the ratio is then 1, which is the right answer for
    a chain whose only observable underlying IS the parity forward).
    """
    vol_t = sigma * math.sqrt(t_years)
    d1 = (math.log(forward / strike) + 0.5 * sigma * sigma * t_years) / vol_t
    d2 = d1 - vol_t
    rate = -math.log(discount) / t_years if 0 < discount < 1 else 0.0
    ratio = forward / spot if spot and spot > 0 else 1.0

    fwd_delta = discount * (norm_cdf(d1) if is_call else -norm_cdf(-d1))
    fwd_gamma = discount * norm_pdf(d1) / (forward * vol_t)
    decay = -forward * discount * norm_pdf(d1) * sigma / (2.0 * math.sqrt(t_years))
    if is_call:
        carry = rate * (forward * discount * norm_cdf(d1) - strike * discount * norm_cdf(d2))
    else:
        carry = rate * (strike * discount * norm_cdf(-d2) - forward * discount * norm_cdf(-d1))
    return {
        "delta": fwd_delta * ratio,
        "gamma": fwd_gamma * ratio * ratio,
        # Per calendar day, and per 1 volatility point — the way a chain is read.
        "theta": (decay + carry) / 365.0,
        "vega": discount * forward * norm_pdf(d1) * math.sqrt(t_years) / 100.0,
    }
