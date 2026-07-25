"""Per-symbol volatility — EWMA estimator + vol-targeted size multiplier (v1).

Two things live here, deliberately kept apart:

* ``forecast_vol`` — a *perception* read: how volatile is this name right now,
  relative to its own history. Pure function of completed daily bars.
* ``size_multiplier`` — the *sizing* use of that read: scale a strategy's
  notional toward a target annual volatility, inside a hard clamp.

**EWMA, not GARCH — settled by measurement (2026-07-24).** The daily bake-off
(``research/vol_bakeoff.py``) ran 85 symbols / 169,185 symbol-days walk-forward
on QLIKE: GARCH led by a mean of 0.0107 with a 95% CI of [-0.0348, +0.0129],
straddling zero. Under the pre-registered tie rule the zero-dependency
incumbent wins, so GARCH does not earn the ``arch`` dependency for daily
sizing. Anything that replaces EWMA later swaps in behind ``forecast_vol``'s
signature — the sizing seam and the toggle surface do not move.

Everything here is fail-soft: an unusable window returns ``None``, and a
``None`` forecast sizes at the identity multiplier 1.0. Volatility is an
*adjustment* to sizing, never a precondition for it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from math import log, sqrt
from typing import Literal
from zoneinfo import ZoneInfo

from signals.trade_context import DatedBar

logger = logging.getLogger(__name__)

_ET = ZoneInfo("America/New_York")

# RiskMetrics lambda. The bake-off's `ewma_sigma` used exactly this.
EWMA_LAMBDA = 0.94
# Returns needed before we forecast at all. Below this the percentile that
# decides the regime is noise dressed as a reading.
MIN_OBS = 40
# Seed the recursion from the variance of the first N returns (matches the
# bake-off, so the shipped estimator is the one that was measured).
SEED_OBS = 60
# 1bp daily. Below this is a data artifact, not a quiet stock.
SIGMA_FLOOR = 1e-4
# Above this fraction of exactly-zero returns the instrument wasn't trading.
MAX_ZERO_RETURN_FRACTION = 0.20
TRADING_DAYS = 252
# Regime percentiles are self-relative — a "storm" for KO is not a storm for
# TSLA, and a cross-sectional threshold would just re-rank the universe by beta.
REGIME_LOOKBACK = 252
CALM_PCTILE = 33.0
STORM_PCTILE = 80.0

Regime = Literal["calm", "normal", "storm"]

# One global dial instead of raw numbers on the founder's surface. clamp_lo is
# 0.25x on all three: shrinking into a storm is never the risky side, so the
# profiles differ only in how much they are allowed to ADD.
VOL_TARGET_PROFILES: dict[str, tuple[float, float, float]] = {
    # name: (target_annual_vol, clamp_lo, clamp_hi)
    "conservative": (0.12, 0.25, 1.25),
    "balanced": (0.20, 0.25, 1.50),
    "aggressive": (0.30, 0.25, 2.00),
}
DEFAULT_PROFILE = "balanced"

IDENTITY_MULTIPLIER = Decimal("1")


@dataclass(frozen=True)
class VolForecast:
    """One symbol's volatility read, as of a completed bar date."""

    symbol: str
    daily_sigma: float
    annualized: float
    regime: Regime
    as_of: str  # ISO date of the last completed bar the estimate used
    observations: int


def _completed_bars(bars: list[DatedBar], today: date) -> list[DatedBar]:
    """Drop a trailing bar dated today — it is still forming.

    The daily-bar baseline trap: mid-session the provider's last daily bar is
    *today's partial*, whose close is just the last print. Feeding it to the
    recursion makes the newest, highest-weighted return a fiction. We drop
    today's bar unconditionally rather than only while the market is open —
    an EOD bar can still settle after the close, and a sizing multiplier does
    not need today's half-formed return to be useful.
    """
    out = list(bars)
    while out and out[-1]["d"] >= today.isoformat():
        out.pop()
    return out


def _log_returns(bars: list[DatedBar]) -> list[float]:
    returns: list[float] = []
    for prev, cur in zip(bars, bars[1:], strict=False):
        p0 = float(prev["c"])
        p1 = float(cur["c"])
        if p0 <= 0 or p1 <= 0:
            continue
        returns.append(log(p1 / p0))
    return returns


def is_degenerate(returns: list[float]) -> bool:
    """True when the window is too stale to estimate volatility from.

    Found the hard way in the bake-off: NBIS (Nebius, ex-Yandex) was halted on
    Nasdaq ~2.5 years, leaving 636 zero returns in 2,610 bars. A long run of
    identical closes decays EWMA's variance toward 1e-9.

    For *sizing* that failure is worse than for scoring: a near-zero sigma
    makes ``target / sigma`` explode, and the clamp would then UPSIZE a halted
    name to clamp_hi — exactly backwards. So a degenerate window is not floored
    and used, it is refused: no forecast, identity multiplier, normal size.
    """
    if not returns:
        return True
    zeros = sum(1 for r in returns if r == 0.0)
    return zeros / len(returns) > MAX_ZERO_RETURN_FRACTION


def _ewma_series(returns: list[float], lam: float = EWMA_LAMBDA) -> list[float]:
    """Sigma after each return — the last value is the one-step-ahead forecast.

    The whole series is kept because the regime read is a percentile of the
    current sigma within this symbol's OWN recent sigmas.
    """
    seed = returns[: min(SEED_OBS, len(returns))]
    mean = sum(seed) / len(seed)
    var = sum((r - mean) ** 2 for r in seed) / len(seed)
    series: list[float] = []
    for r in returns:
        var = lam * var + (1 - lam) * r * r
        series.append(sqrt(var))
    return series


def _percentile_rank(value: float, population: list[float]) -> float:
    """Percent of the population at or below ``value`` (0-100)."""
    if not population:
        return 50.0
    at_or_below = sum(1 for x in population if x <= value)
    return 100.0 * at_or_below / len(population)


def _classify(sigma: float, history: list[float]) -> Regime:
    rank = _percentile_rank(sigma, history)
    if rank <= CALM_PCTILE:
        return "calm"
    if rank >= STORM_PCTILE:
        return "storm"
    return "normal"


def forecast_vol(
    symbol: str,
    bars: list[DatedBar],
    *,
    today: date | None = None,
    lam: float = EWMA_LAMBDA,
) -> VolForecast | None:
    """EWMA volatility for ``symbol`` from completed daily bars, or None.

    ``None`` means "no usable read" — too few bars, a halted/degenerate window,
    or a sigma at the data-artifact floor. Callers must treat it as unknown and
    size normally, never as "zero volatility".
    """
    if today is None:
        today = datetime.now(_ET).date()

    try:
        completed = _completed_bars(bars or [], today)
        returns = _log_returns(completed)
    except (KeyError, TypeError, ValueError):
        logger.exception("Volatility: unusable bars for %s", symbol)
        return None

    if len(returns) < MIN_OBS:
        return None
    if is_degenerate(returns):
        logger.debug(
            "Volatility: %s window is degenerate (>%.0f%% zero returns) — no forecast",
            symbol,
            MAX_ZERO_RETURN_FRACTION * 100,
        )
        return None

    series = _ewma_series(returns, lam)
    sigma = series[-1]
    if sigma < SIGMA_FLOOR:
        logger.debug(
            "Volatility: %s sigma %.2e below the %.0ebp floor — no forecast",
            symbol,
            sigma,
            SIGMA_FLOOR,
        )
        return None

    history = series[-REGIME_LOOKBACK:]
    return VolForecast(
        symbol=symbol,
        daily_sigma=sigma,
        annualized=sigma * sqrt(TRADING_DAYS),
        regime=_classify(sigma, history),
        as_of=completed[-1]["d"],
        observations=len(returns),
    )


def resolve_profile(name: str | None) -> tuple[float, float, float]:
    """(target_annual_vol, clamp_lo, clamp_hi) for a profile name.

    An unknown name falls back to the balanced preset rather than raising —
    a typo in settings must not stop the scan from proposing.
    """
    key = str(name or DEFAULT_PROFILE).strip().lower()
    profile = VOL_TARGET_PROFILES.get(key)
    if profile is None:
        logger.warning(
            "Volatility: unknown vol_target_profile %r — using %s",
            name,
            DEFAULT_PROFILE,
        )
        return VOL_TARGET_PROFILES[DEFAULT_PROFILE]
    return profile


def size_multiplier(
    forecast: VolForecast | None,
    *,
    profile: str | None = DEFAULT_PROFILE,
) -> Decimal:
    """Notional scale factor for a vol-targeted strategy — always within the clamp.

    ``target / realized`` shrinks a stormy name and grows a calm one. The clamp
    is the safety property, not a nicety: it bounds how far this can move size
    in either direction, and RiskEngine still gates the resulting order. A
    missing forecast is the identity — unknown volatility means normal size.
    """
    if forecast is None or forecast.annualized <= 0:
        return IDENTITY_MULTIPLIER
    target, clamp_lo, clamp_hi = resolve_profile(profile)
    raw = target / forecast.annualized
    clamped = min(max(raw, clamp_lo), clamp_hi)
    return Decimal(str(round(clamped, 4)))
