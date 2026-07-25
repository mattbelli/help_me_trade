"""Chain-snapshot shaping — banding, aggregates, and ATM / constant-maturity IV.

Pure functions over already-fetched vendor payloads, so a session's capture is
testable without a network. The runner (``tools/options_chain_sampler.py``) owns
fetching, storage and scheduling.

This module exists mostly to contain two vendor quirks, both measured live
2026-07-25 against Alpaca:

  * ``dailyBar`` is the contract's LAST TRADED session, not the previous one.
    SPY carried 10,802 daily bars but only 5,038 were stamped with the session
    being sampled — reading ``dailyBar.v`` as session volume would invent trades
    that never happened for the other 5,764. Volume therefore counts ONLY when
    the bar's own date matches the session; a stale bar means the contract did
    not trade, which is a real zero, not a missing value.
  * vendor greeks/IV are model output, not observations. They are stored, but the
    raw solver inputs (bid/ask/spot/strike/expiry/style) are stored beside them so
    the corpus can be re-solved under our own model later. That primary/cross-check
    split is what the options lane requires before IV-rank gates real money.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import date

# Band for the per-contract table. A 25%-away strike is roughly 1.5 sigma at one
# year for a 20-vol name, so the band holds every strike the IV surface, the OI
# walls and expression selection actually reference; 400 days covers the wheel /
# covered-call tenors plus a year of structure without paying for 2-year LEAPS.
# Aggregates in :func:`board_aggregates` are deliberately computed BEFORE banding
# — a banded total would understate the chain's real volume and OI silently.
MONEYNESS_BAND = 0.25
MAX_DTE = 400

# Constant-maturity target for the headline IV-rank input.
TARGET_DTE = 30
# Front-month ATM skips the 0-1DTE contracts, whose IV is dominated by pin and
# gamma effects rather than by the level the vol lane cares about.
MIN_FRONT_DTE = 7

# The OCC tail is fixed-width: 6 date digits + C/P + 8 strike digits (thousandths).
# Anchoring on the tail rather than the root keeps roots with digits or padding
# (BRKB, index weeklies) parsing correctly.
_OCC = re.compile(r"^(?P<root>.+?)(?P<ymd>\d{6})(?P<cp>[CP])(?P<strike>\d{8})$")


@dataclass(frozen=True)
class Contract:
    """One parsed OCC contract identifier."""

    occ: str
    root: str
    expiration: date
    opt_type: str  # "call" | "put"
    strike: float


def parse_occ(occ: str) -> Contract | None:
    """Parse an OCC symbol, or None when it is not one."""
    text = (occ or "").strip().upper()
    m = _OCC.match(text)
    if not m:
        return None
    try:
        expiration = date(
            2000 + int(m.group("ymd")[:2]),
            int(m.group("ymd")[2:4]),
            int(m.group("ymd")[4:6]),
        )
    except ValueError:
        return None
    return Contract(
        occ=text,
        root=m.group("root"),
        expiration=expiration,
        opt_type="call" if m.group("cp") == "C" else "put",
        strike=int(m.group("strike")) / 1000.0,
    )


def in_band(strike: float, spot: float, dte: int) -> bool:
    """Whether a contract is inside the stored moneyness / tenor band."""
    if spot <= 0 or dte < 0 or dte > MAX_DTE:
        return False
    return abs(strike / spot - 1.0) <= MONEYNESS_BAND


def bar_volume(daily_bar: dict | None, session: str) -> tuple[int, str | None]:
    """Session volume and the bar's own date.

    Returns ``(0, bar_date)`` when the bar predates the session: the contract
    did not trade, which is a true zero. See the module docstring — carrying a
    stale bar's volume forward is the trap this function exists to close.
    """
    if not isinstance(daily_bar, dict):
        return 0, None
    bar_date = str(daily_bar.get("t") or "")[:10] or None
    if bar_date != session:
        return 0, bar_date
    try:
        return int(daily_bar.get("v") or 0), bar_date
    except (TypeError, ValueError):
        return 0, bar_date


def _strike_iv_curve(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Average IV per strike, ascending by strike."""
    by_strike: dict[float, list[float]] = {}
    for strike, iv in points:
        if iv is not None and iv > 0:
            by_strike.setdefault(strike, []).append(iv)
    return sorted((k, sum(v) / len(v)) for k, v in by_strike.items())


def atm_iv(points: list[tuple[float, float]], spot: float) -> float | None:
    """ATM implied vol for ONE expiry, interpolated across the strikes at spot.

    ``points`` is ``(strike, iv)`` for every contract in the expiry — calls and
    puts both, averaged per strike, which cancels most of the put-call basis
    left by discrete dividends and American early exercise. Returns None when
    spot is not bracketed by two quoted strikes: extrapolating past the edge of
    the quoted board would be a fabricated number, and the vol lane may not be
    fed fabricated numbers.
    """
    if spot <= 0:
        return None
    curve = _strike_iv_curve(points)
    if len(curve) < 2:
        return None
    if spot <= curve[0][0] or spot >= curve[-1][0]:
        return None
    for (k0, iv0), (k1, iv1) in zip(curve, curve[1:], strict=False):
        if k0 <= spot <= k1:
            if k1 == k0:
                return iv0
            w = (spot - k0) / (k1 - k0)
            return iv0 + w * (iv1 - iv0)
    return None


def constant_maturity_iv(
    points: list[tuple[int, float]], target_dte: int = TARGET_DTE
) -> float | None:
    """Interpolate a constant-maturity ATM IV from per-expiry ``(dte, iv)`` points.

    Interpolation is linear in TOTAL VARIANCE (iv^2 * dte), the standard way to
    move between expiries — interpolating vol directly biases the result low
    across a sloped term structure. Returns None when ``target_dte`` is not
    bracketed; the term structure is not extrapolated for the same reason
    :func:`atm_iv` will not extrapolate across strikes.
    """
    usable = sorted((d, iv) for d, iv in points if d > 0 and iv and iv > 0)
    if not usable:
        return None
    for dte, iv in usable:
        if dte == target_dte:
            return iv
    before = [(d, iv) for d, iv in usable if d < target_dte]
    after = [(d, iv) for d, iv in usable if d > target_dte]
    if not before or not after:
        return None
    d0, iv0 = before[-1]
    d1, iv1 = after[0]
    var0, var1 = iv0 * iv0 * d0, iv1 * iv1 * d1
    w = (target_dte - d0) / (d1 - d0)
    var_t = var0 + w * (var1 - var0)
    if var_t <= 0:
        return None
    return math.sqrt(var_t / target_dte)


def front_atm_iv(
    by_expiry: dict[date, list[tuple[float, float]]], spot: float, session: date
) -> tuple[float | None, int | None]:
    """ATM IV of the nearest expiry at least ``MIN_FRONT_DTE`` out, with its DTE."""
    for expiry in sorted(by_expiry):
        dte = (expiry - session).days
        if dte < MIN_FRONT_DTE:
            continue
        iv = atm_iv(by_expiry[expiry], spot)
        if iv is not None:
            return iv, dte
    return None, None


@dataclass(frozen=True)
class BoardAggregates:
    """Whole-chain totals — computed before banding, never band-truncated."""

    call_volume: int
    put_volume: int
    call_oi: int
    put_oi: int
    contracts_observed: int
    bars_current: int


def board_aggregates(rows: list[dict]) -> BoardAggregates:
    """Sum volume and open interest across the FULL board.

    ``bars_current`` — how many contracts carried a daily bar stamped with the
    session — is the run's own freshness check: a sweep fired before the vendor
    rolled its daily bars would show volume collapsing to zero everywhere, and
    that must be distinguishable from a genuinely quiet session.
    """
    cv = pv = co = po = bars_current = 0
    for r in rows:
        is_call = r.get("opt_type") == "call"
        vol = int(r.get("volume") or 0)
        oi = int(r.get("open_interest") or 0)
        if is_call:
            cv += vol
            co += oi
        else:
            pv += vol
            po += oi
        if r.get("bar_date") and r.get("bar_date") == r.get("session"):
            bars_current += 1
    return BoardAggregates(cv, pv, co, po, len(rows), bars_current)
