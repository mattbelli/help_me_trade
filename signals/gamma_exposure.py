"""Index dealer exposure — standing OI × our own gamma, vanna and charm.

The question this answers is not "where will SPX go" but "how is the option board
likely to make dealers behave if it moves". Positive net gamma means the hedging
flow that option inventory forces is *counter*-trend (sell strength, buy weakness
— a dampener); negative means it is *pro*-trend (an amplifier). That is a claim
about mechanics, and it is only as good as the assumption underneath it, which is
stated in the payload every single time — see CONVENTION below.

**Three axes, one board.** All three are dollars of dealer delta, and they differ
only in what has to move to create that delta: gamma needs the index to move 1%,
vanna needs implied vol to move 1 point, charm needs nothing to move at all —
just a day to pass. Same board, same OI, same convention; three different
questions asked of it. They are NOT comparable to each other as numbers, because
a 1% index move and a 1-point vol move are not the same size of event.

**Where each number comes from.**
  * Open interest: ``data/oi_walls.db``, the OPRA per-strike daily store. It is
    published overnight, so it is ALWAYS the prior session's positioning — the
    payload carries ``oi_as_of`` and its age rather than implying it is live.
  * Quotes: the live Alpaca chain for both index roots (SPX = AM-settled
    monthlies, SPXW = the PM dailies/weeklies — two different boards under one
    index, and leaving either out drops real gamma).
  * IV, gamma, vanna and charm: ours, via ``signals.black76``, off the chain's
    own parity forward. ⚠️ Measured 2026-08-11: Alpaca publishes greeks and IV on
    0 of 12,563 index contracts. On equities black76 is a fallback that fills
    vendor holes; here there is no vendor value at all, so every greek in this
    module is model output and ``greeks_source`` says so. Each was checked against
    a central finite difference of the pricer before being trusted — gamma against
    the price, vanna and charm against the analytic delta.
  * Spot: index options have no stock quote, so the level is read back out of
    put-call parity on the nearest expiry.

**★ CONVENTION — the load-bearing assumption.** Netting calls against puts
assumes dealers are long every call and short every put. That is the industry's
convention, not a measurement: we have no dealer inventory data, and the flow
corpus that could eventually sign this is blocked upstream (the o-sign thread).
So the payload reports the two UNCONDITIONAL magnitudes — total call gamma and
total put gamma, which are facts about the board — alongside the netted number,
which is a fact only if the convention holds. Never quote the net without the
convention attached, and never call it "dealer positioning" as though it were
observed.

**Why the whole board, not a band.** The obvious optimisation is to fetch only
near-dated near-money strikes. Measured 2026-08-11, that is wrong by a factor of
two: contracts inside 60 DTE carry 67.6% of absolute gamma and their net is
$34.6B against $65.8B for the full board. A ±15% strike band distorts the net by
9% because the far wings are net negative. The full board is 30,691 contracts and
fetches in 2.3 s, so there is nothing to buy by cutting it.

**What is an approximation, plainly.** The flip search shifts spot with each
contract's implied vol held fixed (sticky-strike) and the forward scaled with it.
Real vol moves when spot moves, so the flip is a level on today's surface, not a
forecast of where the board will actually flip. And gamma is a model number, not
an observation: a strike whose quote will not invert is skipped rather than
guessed at, which is why ``coverage`` reports the OI that got priced.

⚠️ **Charm is an instantaneous rate, and a chunk of it is not drift at all.**
Measured 2026-08-11: for the 98.9% of legs that survive a day, multiplying the
rate by one day lands within 1.7% of actually rolling the clock forward — so the
linear read is fine there. But **26% of the board's headline charm sat in
contracts expiring within that same day** ($4.79B of $18.65B). Those do not drift
toward a new delta, they resolve to zero or to parity, and no rate describes that.
``charm_from_expiring_b`` reports that piece each day so the headline is read with
it rather than extrapolated past it — as a level rather than a share, because the
front of the board and the rest of it can point opposite ways and a share then
prints above 100% and reads like an error.

**No edge is claimed.** Nothing here has been backtested against forward returns.
This is a perception source — one angle on the tape — and it must be presented as
one.
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from datetime import datetime
from typing import Any, NamedTuple

from signals.black76 import greeks, implied_forward, implied_vol

logger = logging.getLogger(__name__)

# Both boards, always. SPX is the AM-settled monthly/LEAPS root and SPXW the PM
# daily/weekly one; they are separate chains under one index and SPX carries the
# larger standing OI (18.4M vs 5.4M on 2026-08-11).
INDEX_ROOTS = ("SPX", "SPXW")
CONTRACT_MULTIPLIER = 100

CONVENTION = (
    "net = call gamma - put gamma, which ASSUMES dealers are long every call and "
    "short every put. That is the industry convention, not a measurement of who "
    "holds what; call_gamma and put_gamma below are the unconditional facts."
)

# Term buckets for the horizon table. Measured 2026-08-11, the DTE cut moves the
# net by 2x, so this is the shape of the number, not decoration.
HORIZONS: tuple[int, ...] = (7, 30, 60, 365)

# Flip search. +-30% brackets any plausible crossing on a 60-expiry board, and 0.5
# index points is far inside the noise of a model gamma.
_FLIP_RANGE = 0.30
_FLIP_GRID = 24
_FLIP_TOL_POINTS = 0.5
_FLIP_MAX_ITERS = 40

# Recompute at most every two minutes. The board is ~21k contracts and one full
# evaluation is ~27ms, but the fetch is 2.3s and the inputs move on a quote's
# timescale, not a tool call's — three questions in a row should cost one fetch.
_CACHE_TTL_SECONDS = 120
_CACHE: dict[str, tuple[float, "Board"]] = {}


class Leg(NamedTuple):
    """One priced contract. Enough to re-evaluate gamma at any hypothetical spot."""

    root: str
    expiry: str
    cp: str
    strike: float
    dte: int
    oi: int
    t_years: float
    discount: float
    forward: float
    sigma: float


class Exposure(NamedTuple):
    """One leg's three dollar-delta sensitivities. Fields are in dollars of delta."""

    gamma: float  # per 1% move in the index
    vanna: float  # per 1 volatility point
    charm: float  # per calendar day


class Board(NamedTuple):
    legs: tuple[Leg, ...]
    spot: float
    quote_as_of: str | None
    oi_as_of: str
    oi_as_of_age_days: int
    coverage: dict[str, Any]


def _mid(snapshot: dict[str, Any]) -> float | None:
    """Mid from a genuine two-sided market, or None.

    A zero bid is nobody buying at any price; ask/2 would invert into a confident
    implied vol built on half a quote (same rule as the chain builder).
    """
    quote = snapshot.get("latestQuote") or snapshot.get("latest_quote") or {}
    bid, ask = quote.get("bp"), quote.get("ap")
    if bid is None or ask is None or bid <= 0 or ask < bid:
        return None
    return (float(bid) + float(ask)) / 2.0


def build_board(*, broker: Any = None, now_et: datetime | None = None) -> Board | None:
    """Fetch both index chains, join standing OI, invert IV, return the priced board.

    None when the OI store or the chain is unavailable — a missing input must read
    as "no answer", never as a board that happens to sum to zero.
    """
    from brokers.options_chain import _discount_factor, _time_to_expiry_years
    from db.oi_walls import oi_board
    from imports.option_symbol import parse_occ_symbol
    from trading.market_hours import _ET

    now_et = now_et or datetime.now(_ET)
    today = now_et.date()

    oi_as_of, oi_map = oi_board(roots=INDEX_ROOTS)
    if not oi_as_of or not oi_map:
        return None

    if broker is None:
        from brokers.factory import get_broker

        broker = get_broker()

    raw: dict[tuple[str, str], list[tuple[str, float, dict[str, Any]]]] = defaultdict(list)
    quote_times: list[str] = []  # vendor ISO strings, compared lexicographically
    for root in INDEX_ROOTS:
        # No expiry window and no strike band: see the module docstring — banding
        # this board was measured wrong by 2x, and the whole thing costs 2.3s.
        snapshot = broker.get_option_chain_snapshot(
            root, expiration_date_gte=today.isoformat(), limit=1000, max_pages=60
        )
        if not isinstance(snapshot, dict):
            continue
        for occ, snap in snapshot.items():
            parsed = parse_occ_symbol(str(occ))
            if parsed is None:
                continue
            raw[(root, parsed.expiration.isoformat())].append(
                (parsed.right, float(parsed.strike), snap if isinstance(snap, dict) else {})
            )
    if not raw:
        return None

    legs: list[Leg] = []
    skipped: dict[str, int] = defaultdict(int)
    # Open interest we could NOT price, per reason. Counts alone would hide the
    # thing that matters: 1,000 unpriceable deep-ITM strikes carrying 30 contracts
    # each is noise, the same count carrying 300,000 is a hole in the aggregate.
    unpriced_oi: dict[str, int] = defaultdict(int)
    spot: float | None = None
    for (root, expiry), contracts in sorted(raw.items(), key=lambda kv: (kv[0][1], kv[0][0])):
        t_years = _time_to_expiry_years(expiry, root, now_et)
        if t_years is None:  # already settled today
            skipped["expired"] += len(contracts)
            for cp, strike, _snap in contracts:
                unpriced_oi["expired"] += oi_map.get((root, expiry, cp, strike)) or 0
            continue
        discount = _discount_factor(t_years, today.isoformat())
        mids: dict[float, dict[str, float]] = defaultdict(dict)
        for cp, strike, snap in contracts:
            mid = _mid(snap)
            if mid is not None:
                mids[strike][cp] = mid
        forward = implied_forward(
            [(k, v["C"], v["P"]) for k, v in mids.items() if "C" in v and "P" in v],
            discount,
        )
        if forward is None:
            skipped["no_forward"] += len(contracts)
            for cp, strike, _snap in contracts:
                unpriced_oi["no_forward"] += oi_map.get((root, expiry, cp, strike)) or 0
            continue
        if spot is None:
            # Shortest UNEXPIRED expiry, which in production is 1 DTE: F*D = S*e^(-qT),
            # measured 0.8 index points (0.01%) under the direct parity read. The
            # dividend term is the whole gap and it is nothing at the front of the
            # board — it would be 0.4% out if this were taken from a 38-DTE expiry.
            spot = forward * discount
        dte = (datetime.fromisoformat(expiry).date() - today).days
        for cp, strike, snap in contracts:
            oi = oi_map.get((root, expiry, cp, strike))
            if not oi:
                # No OI row at all. The store holds only OI >= 1, so this is a
                # listed-but-empty strike: zero contracts, zero gamma, correctly
                # absent rather than missing (join verified exact 2026-08-11).
                skipped["no_open_interest"] += 1
                continue
            mid = _mid(snap)
            if mid is None:
                skipped["no_two_sided_quote"] += 1
                unpriced_oi["no_two_sided_quote"] += oi
                continue
            sigma = implied_vol(mid, forward, strike, t_years, discount, cp == "C")
            if sigma is None:
                # Deep-ITM American options, where put-call parity is an inequality
                # and the quote sits outside the model's bracket. None is the honest
                # answer; a guessed vol here would be invented gamma.
                skipped["no_implied_vol"] += 1
                unpriced_oi["no_implied_vol"] += oi
                continue
            legs.append(
                Leg(root, expiry, cp, strike, dte, oi, t_years, discount, forward, sigma)
            )
            ts = (snap.get("latestQuote") or snap.get("latest_quote") or {}).get("t")
            if ts:
                quote_times.append(str(ts))

    if not legs or spot is None:
        return None

    oi_priced = sum(leg.oi for leg in legs)
    oi_standing = sum(oi_map.values())
    return Board(
        legs=tuple(legs),
        spot=round(spot, 2),
        quote_as_of=max(quote_times) if quote_times else None,
        oi_as_of=oi_as_of,
        oi_as_of_age_days=(today - datetime.fromisoformat(oi_as_of).date()).days,
        coverage={
            "contracts_priced": len(legs),
            "expiries": len({leg.expiry for leg in legs}),
            "open_interest_priced": oi_priced,
            "open_interest_standing": oi_standing,
            "open_interest_covered_pct": round(100 * oi_priced / oi_standing, 1)
            if oi_standing
            else None,
            "skipped_contracts": dict(skipped),
            "unpriced_open_interest": dict(unpriced_oi),
        },
    )


def cached_board(*, ttl: int = _CACHE_TTL_SECONDS) -> Board | None:
    """``build_board`` behind a short TTL so repeated questions cost one fetch."""
    hit = _CACHE.get("board")
    if hit is not None and time.monotonic() - hit[0] < ttl:
        return hit[1]
    board = build_board()
    if board is not None:
        _CACHE["board"] = (time.monotonic(), board)
    return board


def _leg_greeks(leg: Leg, spot: float, base_spot: float) -> dict[str, float]:
    """Per-contract greeks at a hypothetical index level.

    The forward is scaled with spot because ``F = S*e^((r-q)T)`` is linear in S;
    the contract's implied vol is held fixed (sticky-strike), which is the
    approximation the module docstring flags.
    """
    forward = leg.forward * spot / base_spot
    return greeks(
        forward, leg.strike, leg.t_years, leg.discount, leg.sigma, leg.cp == "C", spot=spot
    )


def leg_gamma_usd(leg: Leg, spot: float, base_spot: float) -> float:
    """Dollar gamma of one leg's open interest per 1% move, evaluated at ``spot``.

    ``gamma * OI * 100 * S^2 * 0.01`` is the change in the position's delta, in
    dollars, for a 1% move in the index. Kept separate from
    :func:`leg_exposures_usd` because the flip search evaluates this alone across
    a grid — up to 65 passes over 21k legs — with no use for the other two.
    """
    greek = _leg_greeks(leg, spot, base_spot)["gamma"]
    return greek * leg.oi * CONTRACT_MULTIPLIER * spot * spot * 0.01


def leg_exposures_usd(leg: Leg, spot: float, base_spot: float) -> Exposure:
    """All three dollar exposures of one leg's open interest, from one greeks call.

    Each is the same shape — a per-contract sensitivity times the position's index
    notional (``OI * 100 * S``) — differing only in what moves:
      * gamma: delta change for a **1% move** in the index (hence the extra
        ``S * 0.01``, converting per-point gamma into per-percent);
      * vanna: delta change for a **1 volatility-point** rise in implied vol;
      * charm: delta change from **one calendar day** passing, nothing else moving.
    All in dollars of delta, so they add across legs and are directly comparable
    to a dollar position — but only gamma is per-1%, so they are NOT comparable to
    each other without saying what moved.
    """
    g = _leg_greeks(leg, spot, base_spot)
    notional = leg.oi * CONTRACT_MULTIPLIER * spot
    return Exposure(
        gamma=g["gamma"] * notional * spot * 0.01,
        vanna=g["vanna"] * notional,
        charm=g["charm"] * notional,
    )


def net_gamma_at(legs: tuple[Leg, ...], spot: float, base_spot: float) -> float:
    """Net dollar gamma per 1% at a hypothetical index level, under CONVENTION."""
    total = 0.0
    for leg in legs:
        value = leg_gamma_usd(leg, spot, base_spot)
        total += value if leg.cp == "C" else -value
    return total


def gamma_flip(legs: tuple[Leg, ...], base_spot: float) -> tuple[float | None, int]:
    """The index level where net gamma crosses zero, and how many crossings exist.

    Scans a coarse grid first because the profile is not monotone — it peaked at
    +2% and fell away above that on 2026-08-11 — so a bisection launched blind can
    land on the wrong root or miss a pair. The crossing nearest spot is the one
    returned; ``crossings`` is reported so a board with an ambiguous flip says so
    instead of naming one level with false confidence.

    The grid is what bounds this: two crossings inside one 2.5% step read as none.
    That is acceptable because 21k contracts make the profile smooth, and the
    ``profile`` view exists so a reader can look at the shape rather than trust a
    single number — but a spikier board than we have seen would need a finer grid.
    """
    lo, hi = base_spot * (1 - _FLIP_RANGE), base_spot * (1 + _FLIP_RANGE)
    step = (hi - lo) / _FLIP_GRID
    grid = [(lo + i * step, net_gamma_at(legs, lo + i * step, base_spot)) for i in range(_FLIP_GRID + 1)]

    brackets = [
        (grid[i], grid[i + 1])
        for i in range(len(grid) - 1)
        if (grid[i][1] > 0) != (grid[i + 1][1] > 0)
    ]
    if not brackets:
        return None, 0

    (left, right) = min(brackets, key=lambda b: abs(0.5 * (b[0][0] + b[1][0]) - base_spot))
    low, low_val, high = left[0], left[1], right[0]
    for _ in range(_FLIP_MAX_ITERS):
        if high - low <= _FLIP_TOL_POINTS:
            break
        mid = 0.5 * (low + high)
        val = net_gamma_at(legs, mid, base_spot)
        if (val > 0) == (low_val > 0):
            low, low_val = mid, val
        else:
            high = mid
    return round(0.5 * (low + high), 2), len(brackets)


# --- views over a priced board ------------------------------------------------
def _signed(board: Board) -> list[tuple[Leg, Exposure]]:
    """Each leg's exposures at today's spot, signed by CONVENTION (puts negated).

    ⚠️ The sign flip is the convention, not arithmetic: it is what turns "the board
    holds this much" into "dealers are exposed this much". Summing these gives the
    net; summing by ``leg.cp`` first gives the two unconditional magnitudes.
    """
    out = []
    for leg in board.legs:
        exposure = leg_exposures_usd(leg, board.spot, board.spot)
        out.append((leg, exposure if leg.cp == "C" else Exposure(*(-v for v in exposure))))
    return out


def _split(signed: list[tuple[Leg, Exposure]], field: str) -> tuple[float, float, float]:
    """(calls, puts, net) for one exposure field — calls and puts UNCONDITIONAL.

    ⚠️ Splits on ``leg.cp``, never on the sign of the value. Gamma is positive for
    both rights so the sign happens to identify the side; vanna is negative below
    the forward for calls and puts alike, so a sign-based split would file half the
    call book under "puts".
    """
    calls = sum(getattr(e, field) for leg, e in signed if leg.cp == "C")
    puts = -sum(getattr(e, field) for leg, e in signed if leg.cp != "C")
    return calls, puts, calls - puts


def _billions(value: float) -> float:
    return round(value / 1e9, 3)


def summarize(board: Board) -> dict[str, Any]:
    """The regime axes: a handful of numbers plus everything needed to read them."""
    signed = _signed(board)
    calls, puts, net = _split(signed, "gamma")
    v_calls, v_puts, v_net = _split(signed, "vanna")
    c_calls, c_puts, c_net = _split(signed, "charm")
    flip, crossings = gamma_flip(board.legs, board.spot)

    horizons = []
    for cap in (*HORIZONS, None):
        rows = [(leg, e) for leg, e in signed if cap is None or leg.dte <= cap]
        if not rows:
            continue
        horizons.append({
            "dte_max": cap,
            "net_usd_per_1pct_b": _billions(sum(e.gamma for _l, e in rows)),
            "gross_usd_per_1pct_b": _billions(sum(abs(e.gamma) for _l, e in rows)),
            # Vanna and charm concentrate at a different point on the curve than
            # gamma does — gamma is a front-week number, vanna lives further out —
            # so the term row carries all three or it misleads about two of them.
            "net_vanna_usd_per_volpt_b": _billions(sum(e.vanna for _l, e in rows)),
            "net_charm_usd_per_day_b": _billions(sum(e.charm for _l, e in rows)),
            "open_interest": sum(leg.oi for leg, _e in rows),
        })

    row: dict[str, Any] = {
        "index": "SPX",
        "spot": board.spot,
        "spot_source": "options_parity",
        "quote_as_of": board.quote_as_of,
        "oi_as_of": board.oi_as_of,
        "oi_as_of_age_days": board.oi_as_of_age_days,
        "net_gamma_usd_per_1pct_b": _billions(net),
        "call_gamma_usd_per_1pct_b": _billions(calls),
        "put_gamma_usd_per_1pct_b": _billions(puts),
        "gamma_flip_spot": flip,
        "gamma_flip_crossings": crossings,
        "net_vanna_usd_per_volpt_b": _billions(v_net),
        "call_vanna_usd_per_volpt_b": _billions(v_calls),
        "put_vanna_usd_per_volpt_b": _billions(v_puts),
        "net_charm_usd_per_day_b": _billions(c_net),
        "call_charm_usd_per_day_b": _billions(c_calls),
        "put_charm_usd_per_day_b": _billions(c_puts),
        "by_horizon": horizons,
        "greeks_source": "black76",
        "convention": CONVENTION,
        "units": (
            "all three are billions of dollars of dealer DELTA that would change "
            "hands — sensitivities, not positions — and they differ only in what "
            "moves: gamma per 1% move in the index, vanna per 1 volatility point of "
            "implied vol, charm per calendar day with nothing else moving. Same "
            "units, different denominators: never compare them to each other"
        ),
        "not_a_signal": (
            "no edge has been measured for any of this against forward returns — "
            "it is one angle on positioning, never a probability or a direction call"
        ),
    }
    if flip is not None:
        # Signed on purpose: positive = spot sits ABOVE the flip. An unsigned
        # "distance" would make the two regimes read identically.
        row["spot_above_flip_pct"] = round(100 * (board.spot / flip - 1), 2)
        row["gamma_regime"] = "positive" if net > 0 else "negative"
        row["gamma_regime_means"] = (
            "positive: IF the convention holds, hedging that inventory is "
            "counter-trend (sells strength, buys weakness) and tends to dampen "
            "moves; negative: it is pro-trend and tends to amplify them"
        )
    # The charm sitting in contracts that expire within the day. Those resolve
    # rather than drift, so the headline rate does not describe them — measured at
    # $4.79B of $18.65B on 2026-08-11, far too big to leave implied. Reported as a
    # LEVEL, not a share: the front and the rest of the board can point opposite
    # ways, and a share then prints 129% and reads like a bug.
    row["charm_from_expiring_b"] = _billions(
        sum(e.charm for leg, e in signed if leg.dte <= 1)
    )
    row["vanna_charm_mean"] = (
        "SAME convention, so the same caveat. VANNA: dealer delta created by a 1 "
        "point rise in implied vol. Positive means a vol RISE leaves them longer so "
        "the re-hedge is selling, and a vol collapse leaves them shorter so it is "
        "buying — the usual post-event 'vanna rally' story runs that way. CHARM: "
        "the delta the clock alone creates. Positive means the hedge drifts toward "
        "selling on a day the tape does nothing; negative means it drifts toward "
        "buying. ⚠️ charm_from_expiring_b is the part of it carried by contracts "
        "expiring within the day — those RESOLVE rather than drift, so subtract it "
        "before reading the rest as a smooth overnight flow. Both are "
        "mechanical claims about hedging; neither is tested against forward returns"
    )
    row["coverage"] = board.coverage
    return row


def strike_rows(board: Board, *, limit: int = 20) -> list[dict[str, Any]]:
    """The strikes carrying the most gamma, returned in strike order.

    Ranked by GROSS, not by net. 8000 on 2026-08-11 held 1.67M contracts and $63B
    of gamma but netted to $4.2B — rank it by net and the single biggest
    concentration on the board falls out of the list behind strikes a twentieth
    its size. Hedging happens against the gross; the net only says which way.
    """
    totals: dict[float, dict[str, Any]] = {}
    for leg, exposure in _signed(board):
        agg = totals.setdefault(
            leg.strike,
            {"strike": leg.strike, "net": 0.0, "call": 0.0, "put": 0.0,
             "vanna": 0.0, "charm": 0.0, "oi": 0},
        )
        agg["net"] += exposure.gamma
        agg["call" if leg.cp == "C" else "put"] += abs(exposure.gamma)
        agg["vanna"] += exposure.vanna
        agg["charm"] += exposure.charm
        agg["oi"] += leg.oi
    ranked = sorted(
        totals.values(), key=lambda a: a["call"] + a["put"], reverse=True
    )[:limit]
    return [
        {
            "strike": a["strike"],
            "distance_pct": round(100 * (a["strike"] / board.spot - 1), 2),
            "net_gamma_usd_per_1pct_b": _billions(a["net"]),
            "call_gamma_usd_per_1pct_b": _billions(a["call"]),
            "put_gamma_usd_per_1pct_b": _billions(a["put"]),
            # Net only: a call and a put at one strike have the SAME vanna, so a
            # call/put split of it would be two copies of the moneyness, not a
            # split of anything. The gamma split above is the one that informs.
            "net_vanna_usd_per_volpt_b": _billions(a["vanna"]),
            "net_charm_usd_per_day_b": _billions(a["charm"]),
            "open_interest": a["oi"],
        }
        for a in sorted(ranked, key=lambda a: a["strike"])
    ]


def expiry_rows(board: Board, *, limit: int = 20) -> list[dict[str, Any]]:
    """Gamma, vanna and charm by expiry, nearest first — the term structure."""
    totals: dict[str, dict[str, Any]] = {}
    for leg, exposure in _signed(board):
        agg = totals.setdefault(
            leg.expiry,
            {"expiry": leg.expiry, "dte": leg.dte, "net": 0.0, "gross": 0.0,
             "vanna": 0.0, "charm": 0.0, "oi": 0},
        )
        agg["net"] += exposure.gamma
        agg["gross"] += abs(exposure.gamma)
        agg["vanna"] += exposure.vanna
        agg["charm"] += exposure.charm
        agg["oi"] += leg.oi
    return [
        {
            "expiry": a["expiry"],
            "dte": a["dte"],
            "net_gamma_usd_per_1pct_b": _billions(a["net"]),
            "gross_gamma_usd_per_1pct_b": _billions(a["gross"]),
            "net_vanna_usd_per_volpt_b": _billions(a["vanna"]),
            "net_charm_usd_per_day_b": _billions(a["charm"]),
            "open_interest": a["oi"],
        }
        for a in sorted(totals.values(), key=lambda a: a["dte"])[:limit]
    ]


def profile_rows(board: Board, *, span_pct: float = 8.0, steps: int = 17) -> list[dict[str, Any]]:
    """Net gamma across a ladder of hypothetical index levels — the flip's shape.

    This is what a single flip level cannot say: how fast the board crosses, and
    whether the regime an inch away is the same one it is in now.
    """
    out = []
    for i in range(steps):
        pct = -span_pct + (2 * span_pct) * i / (steps - 1)
        spot = board.spot * (1 + pct / 100)
        out.append({
            "move_pct": round(pct, 2),
            # NOT "spot": these rows travel alongside a context block carrying the
            # REAL spot, and a same-named hypothetical would overwrite it — the
            # reader would then see 7107 as the current index level.
            "hypothetical_spot": round(spot, 2),
            "net_gamma_usd_per_1pct_b": _billions(net_gamma_at(board.legs, spot, board.spot)),
        })
    return out
