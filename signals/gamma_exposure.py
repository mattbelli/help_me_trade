"""Index gamma exposure — standing OI × our own gamma, as a regime axis.

The question this answers is not "where will SPX go" but "how is the option board
likely to make dealers behave if it moves". Positive net gamma means the hedging
flow that option inventory forces is *counter*-trend (sell strength, buy weakness
— a dampener); negative means it is *pro*-trend (an amplifier). That is a claim
about mechanics, and it is only as good as the assumption underneath it, which is
stated in the payload every single time — see CONVENTION below.

**Where each number comes from.**
  * Open interest: ``data/oi_walls.db``, the OPRA per-strike daily store. It is
    published overnight, so it is ALWAYS the prior session's positioning — the
    payload carries ``oi_as_of`` and its age rather than implying it is live.
  * Quotes: the live Alpaca chain for both index roots (SPX = AM-settled
    monthlies, SPXW = the PM dailies/weeklies — two different boards under one
    index, and leaving either out drops real gamma).
  * IV and gamma: ours, via ``signals.black76``, off the chain's own parity
    forward. ⚠️ Measured 2026-08-11: Alpaca publishes greeks and IV on 0 of
    12,563 index contracts. On equities black76 is a fallback that fills vendor
    holes; here there is no vendor value at all, so every gamma in this module is
    model output and ``greeks_source`` says so.
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


def leg_gamma_usd(leg: Leg, spot: float, base_spot: float) -> float:
    """Dollar gamma of one leg's open interest per 1% move, evaluated at ``spot``.

    ``gamma * OI * 100 * S^2 * 0.01`` is the change in the position's delta, in
    dollars, for a 1% move in the index. The forward is scaled with spot because
    ``F = S*e^((r-q)T)`` is linear in S; the contract's implied vol is held fixed
    (sticky-strike), which is the approximation the docstring flags.
    """
    forward = leg.forward * spot / base_spot
    gamma = greeks(
        forward, leg.strike, leg.t_years, leg.discount, leg.sigma, leg.cp == "C", spot=spot
    )["gamma"]
    return gamma * leg.oi * CONTRACT_MULTIPLIER * spot * spot * 0.01


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
def _signed(board: Board) -> list[tuple[Leg, float]]:
    """Each leg with its dollar gamma at today's spot, call-positive/put-negative."""
    out = []
    for leg in board.legs:
        value = leg_gamma_usd(leg, board.spot, board.spot)
        out.append((leg, value if leg.cp == "C" else -value))
    return out


def _billions(value: float) -> float:
    return round(value / 1e9, 3)


def summarize(board: Board) -> dict[str, Any]:
    """The regime axes: a handful of numbers plus everything needed to read them."""
    signed = _signed(board)
    calls = sum(v for _leg, v in signed if v > 0)
    puts = -sum(v for _leg, v in signed if v < 0)
    net = calls - puts
    flip, crossings = gamma_flip(board.legs, board.spot)

    horizons = []
    for cap in HORIZONS:
        rows = [(leg, v) for leg, v in signed if leg.dte <= cap]
        if not rows:
            continue
        horizons.append({
            "dte_max": cap,
            "net_usd_per_1pct_b": _billions(sum(v for _l, v in rows)),
            "gross_usd_per_1pct_b": _billions(sum(abs(v) for _l, v in rows)),
            "open_interest": sum(leg.oi for leg, _v in rows),
        })
    horizons.append({
        "dte_max": None,
        "net_usd_per_1pct_b": _billions(net),
        "gross_usd_per_1pct_b": _billions(sum(abs(v) for _l, v in signed)),
        "open_interest": sum(leg.oi for leg in board.legs),
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
        "by_horizon": horizons,
        "greeks_source": "black76",
        "convention": CONVENTION,
        "units": (
            "usd_per_1pct_b = billions of dollars of dealer delta that would change "
            "hands for a 1% move in the index; it is a sensitivity, not a position"
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
    for leg, value in _signed(board):
        agg = totals.setdefault(
            leg.strike, {"strike": leg.strike, "net": 0.0, "call": 0.0, "put": 0.0, "oi": 0}
        )
        agg["net"] += value
        agg["call" if leg.cp == "C" else "put"] += abs(value)
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
            "open_interest": a["oi"],
        }
        for a in sorted(ranked, key=lambda a: a["strike"])
    ]


def expiry_rows(board: Board, *, limit: int = 20) -> list[dict[str, Any]]:
    """Gamma by expiry, nearest first — the term structure behind the headline."""
    totals: dict[str, dict[str, Any]] = {}
    for leg, value in _signed(board):
        agg = totals.setdefault(
            leg.expiry, {"expiry": leg.expiry, "dte": leg.dte, "net": 0.0, "gross": 0.0, "oi": 0}
        )
        agg["net"] += value
        agg["gross"] += abs(value)
        agg["oi"] += leg.oi
    return [
        {
            "expiry": a["expiry"],
            "dte": a["dte"],
            "net_gamma_usd_per_1pct_b": _billions(a["net"]),
            "gross_gamma_usd_per_1pct_b": _billions(a["gross"]),
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
