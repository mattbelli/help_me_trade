#!/usr/bin/env python3
"""Classification-lab scorer — grades the cheap classification methods vs DIR-1 truth.

Implements `References/CLASSIFICATION_LAB_PREREG_2026-07-16.md` exactly. That document
is LAW: the three methods, their variants, every metric, the conditioning slices and the
success thresholds were fixed before the first ground-truth day accrued, so this script
computes what was pre-registered and nothing else. Anything useful but unregistered is
reported under an ``unregistered_diagnostics`` key and never gates a verdict.

    uv run python tools/classification_lab_scorer.py                 # every complete session
    uv run python tools/classification_lab_scorer.py --dates 2026-07-20,2026-07-21

Reads gitignored research captures only (data/research/print_lab, chain_samples,
underlying_trades — the last written by tools/underlying_trades_fetch.py); writes one
JSON report to data/research/classification_lab/. Offline and read-only: no network, no
broker, nothing that touches trading/.
"""

from __future__ import annotations

import argparse
import gzip
import json
import math
import statistics
import sys
from bisect import bisect_right
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from imports.option_symbol import parse_occ_symbol  # noqa: E402
from signals.options_flow.constants import GAUGE_UNDERLYINGS, QUOTE_EPS  # noqa: E402
from signals.options_flow.opening_pressure import WINDOWS  # noqa: E402
from signals.options_flow.rolling import CLUSTER_PRINT_MIN_USD  # noqa: E402

_ET = ZoneInfo("America/New_York")

DEFAULT_DATA_DIR = ROOT / "data"
CONTRACT_MULT = 100.0

# The print lab captures 5-minute shoulders around each gauge window. Shoulder prints are
# never SCORED (the prereg scores "the gauge windows") but they ARE read as lookback:
# they seed the tick chains and the 5s bound grid so the window's first prints are not
# structurally unclassifiable.
SHOULDER_MINUTES = 5

# --- registered method geometry ----------------------------------------------------
# Prereg "Methods under test" 3: back-pairing at 15s/30s/60s effective sampling, two
# lookup variants each; plus a 5s BOUND off the print lab's own tick quotes (an
# approximation — quotes exist only at print times — labelled as such everywhere).
BACKPAIR_INTERVALS_S = (15, 30, 60)
BOUND_INTERVAL_S = 5
LOOKUPS = ("strict", "interp")

# Prereg "Methods under test" 2: SPX/SPXW have no equity tape — SPY x 10 proxy, scored
# separately. Proxy cells carry ``underlying_proxy``, stay OUT of the all-cells verdict
# and out of the pooled net-sign gate; they are reported in their own verdict fields.
SPX_ROOTS = frozenset({"SPX", "SPXW"})
SPX_PROXY_SYMBOL = "SPY"
SPX_PROXY_MULT = 10.0
SPX_PROXY_LABEL = "SPY x10"

# Vendor greeks quote vega per 1% of vol while ``iv`` is a decimal — measured, not
# assumed: tools/option_spread_probe.py:121 scales by 100 for exactly this reason and
# vega_convention_check() there verifies it against closed-form ATM vega. The residual
# needs both sides in the same units or the vol term silently vanishes (100x too small).
VEGA_PER_VOL_SCALE = 100.0

# --- registered conditioning + thresholds ------------------------------------------
SPIKE_TOP_FRACTION = 0.10  # "top-decile volume minutes"
FIRST_MINUTES = 5  # "first-5-minutes vs rest"

THRESH_AGREEMENT_PW = 0.85
THRESH_ASYMMETRY_PP = 5.0
THRESH_NET_SIGN = 0.95
THRESH_NET_MAG_ERR = 0.15
MIN_ORDINARY_SESSIONS = 5

# The prereg fixes the net-sign THRESHOLD (>=95%) but not the unit it is measured over.
# Read per cell, a 5-session cell can only realize 0/20/40/60/80/100%, so 95% is
# unreachable by construction. This constant implements a pending dated addendum
# (registered before any scoring run): the evaluation unit is the POOLED set of
# (session x name x window) net observations within method x variant x session class;
# the threshold VALUE is unchanged. Per-cell nets are still reported as before, and
# every sign-agreement figure carries its realized N and achievable resolution 1/N.
NET_SIGN_UNIT = "pooled_session_name_window"
NET_SIGN_UNIT_NOTE = (
    "pending dated addendum to CLASSIFICATION_LAB_PREREG_2026-07-16: sign agreement is "
    "evaluated on pooled (session x name x window) net observations within method x "
    "variant x session class; threshold value unchanged at >=95%"
)


# ---------------------------------------------------------------------------------
# inputs
# ---------------------------------------------------------------------------------


def _open_capture(base: Path, date_iso: str):
    """Research captures are gzipped by the nightly retention job; today's is plain."""
    gz = base / f"{date_iso}.jsonl.gz"
    if gz.exists():
        return gzip.open(gz, "rt")
    plain = base / f"{date_iso}.jsonl"
    if plain.exists():
        return plain.open()
    return None


def _window_of(ts_ms: int) -> tuple[str, int, bool] | None:
    """(window, minute offset from the window start, in_scope) or None outside capture.

    ``minute`` runs -SHOULDER_MINUTES..length+SHOULDER_MINUTES-1; ``in_scope`` is True
    only inside the gauge window proper, which is the only slice the prereg scores.
    """
    dt = datetime.fromtimestamp(ts_ms / 1000, tz=_ET)
    minute = dt.hour * 60 + dt.minute
    for window, (start, length) in WINDOWS.items():
        offset = minute - start
        if -SHOULDER_MINUTES <= offset < length + SHOULDER_MINUTES:
            return window, offset, 0 <= offset < length
    return None


@dataclass(slots=True)
class Print:
    """One print-lab row in a gauge window (or its shoulder), with its truth label."""

    ts_ms: int
    occ: str
    name: str
    price: float
    size: int
    right: str
    window: str
    minute: int
    in_scope: bool
    premium: float
    truth: str | None  # buy_side / sell_side, only when basis == stream_quote
    truth_undetermined: bool
    gauge_eligible: bool
    q_bid: float | None
    q_ask: float | None
    q_ts_ms: int | None


def load_prints(base: Path, date_iso: str, names: frozenset[str]) -> list[Print] | None:
    """Ground truth: every classified print in the gauge windows on the subscribed slice.

    Only ``basis == "stream_quote"`` rows score as truth (prereg "Ground truth"); rows
    whose direction came from another basis are neither truth nor overreach denominator.

    ``gauge_eligible`` reproduces the gauge's own three print filters — spread legs,
    hygiene-excluded conditions, and the CLUSTER_PRINT_MIN_USD dust floor
    (signals/options_flow/opening_pressure.py:96-110). Net-gauge fidelity is only
    fidelity to the gauge if both nets are built from the prints the gauge actually
    accrues; agreement/bias/overreach stay on the full capture ("every classified print").
    """
    handle = _open_capture(base, date_iso)
    if handle is None:
        return None
    out: list[Print] = []
    with handle as fh:
        for line in fh:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            name = str(row.get("u") or "").strip().upper()
            if name not in names:
                continue
            try:
                ts_ms = int(row["ts_ms"])
                price = float(row["price"])
                size = int(row["size"])
            except (KeyError, TypeError, ValueError):
                continue
            if price <= 0 or size <= 0:
                continue
            slot = _window_of(ts_ms)
            if slot is None:
                continue
            occ = str(row.get("occ") or "").strip().upper()
            parsed = parse_occ_symbol(occ)
            if parsed is None:
                continue
            direction = str(row.get("dir") or "")
            is_truth = direction in ("buy_side", "sell_side") and row.get("basis") == "stream_quote"
            premium = price * size * CONTRACT_MULT
            window, minute, in_scope = slot
            out.append(
                Print(
                    ts_ms=ts_ms,
                    occ=occ,
                    name=name,
                    price=price,
                    size=size,
                    right=parsed.right,
                    window=window,
                    minute=minute,
                    in_scope=in_scope,
                    premium=premium,
                    truth=direction if is_truth else None,
                    truth_undetermined=(direction == "undetermined"),
                    gauge_eligible=(
                        not row.get("spread")
                        and not row.get("hyg")
                        and premium >= CLUSTER_PRINT_MIN_USD
                    ),
                    q_bid=_num(row.get("bid")),
                    q_ask=_num(row.get("ask")),
                    q_ts_ms=int(row["q_ts_ms"]) if row.get("q_ts_ms") is not None else None,
                )
            )
    out.sort(key=lambda p: p.ts_ms)
    return out


@dataclass(slots=True)
class Sample:
    """One 15s chain-sampler observation of a contract.

    ``ts_ms`` is the OBSERVATION time, taken as max(pass timestamp, quote timestamp).
    The sampler stamps every row in a pass with the pass's start second, but a pass
    walks its roster sequentially, so a quote fetched late in the pass carries a
    ``q_ts`` AFTER its own row's ``ts`` — using the pass stamp back-dates it and lets a
    trade read a quote that did not exist yet.
    """

    ts_ms: int
    bid: float | None
    ask: float | None
    iv: float | None
    delta: float | None
    gamma: float | None
    vega: float | None


def _parse_ts_ms(value) -> int | None:
    try:
        return int(datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp() * 1000)
    except (TypeError, ValueError):
        return None


def load_chain(base: Path, date_iso: str) -> tuple[dict[str, list[Sample]], dict] | None:
    """The 15s chain sampler's quote/greek timeline per OCC, plus coverage stats."""
    handle = _open_capture(base, date_iso)
    if handle is None:
        return None
    by_occ: dict[str, list[Sample]] = defaultdict(list)
    by_root: dict[str, dict[str, int]] = defaultdict(lambda: {"samples": 0, "greek_samples": 0})
    with handle as fh:
        for line in fh:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            occ = row.get("occ")
            pass_ts = _parse_ts_ms(row.get("ts"))
            if not occ or pass_ts is None:
                continue  # sampler error rows carry neither
            quote_ts = _parse_ts_ms(row.get("q_ts"))
            root = str(row.get("u") or "").strip().upper()
            by_root[root]["samples"] += 1
            if row.get("delta") is not None:
                by_root[root]["greek_samples"] += 1
            by_occ[str(occ).strip().upper()].append(
                Sample(
                    ts_ms=max(pass_ts, quote_ts) if quote_ts is not None else pass_ts,
                    bid=_num(row.get("bid")),
                    ask=_num(row.get("ask")),
                    iv=_num(row.get("iv")),
                    delta=_num(row.get("delta")),
                    gamma=_num(row.get("gamma")),
                    vega=_num(row.get("vega")),
                )
            )
    spacings: list[float] = []
    for rows in by_occ.values():
        rows.sort(key=lambda s: s.ts_ms)
        spacings.extend((b.ts_ms - a.ts_ms) / 1000.0 for a, b in zip(rows, rows[1:], strict=False))
    stats = {
        "contracts": len(by_occ),
        "coverage_by_root": {k: dict(v) for k, v in sorted(by_root.items())},
        "median_inter_sample_spacing_s": statistics.median(spacings) if spacings else None,
    }
    return dict(by_occ), stats


def load_underlying(base: Path, date_iso: str) -> dict[str, tuple[list[int], list[float]]]:
    """Underlying trade tape S_t at exchange timestamps: {"ts_ms", "sym", "price"} rows,
    as written by tools/underlying_trades_fetch.py."""
    handle = _open_capture(base, date_iso)
    if handle is None:
        return {}
    acc: dict[str, list[tuple[int, float]]] = defaultdict(list)
    with handle as fh:
        for line in fh:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            sym = str(row.get("sym") or "").strip().upper()
            try:
                ts_ms = int(row["ts_ms"])
                price = float(row["price"])
            except (KeyError, TypeError, ValueError):
                continue
            if not sym or price <= 0:
                continue
            acc[sym].append((ts_ms, price))
    out: dict[str, tuple[list[int], list[float]]] = {}
    for sym, rows in acc.items():
        rows.sort()
        out[sym] = ([r[0] for r in rows], [r[1] for r in rows])
    return out


def _num(value) -> float | None:
    if value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


# ---------------------------------------------------------------------------------
# methods
# ---------------------------------------------------------------------------------


def _chain_key(prints: list[Print]) -> list[tuple[str, str]]:
    """Contract chains reset per window — a close-window print's "previous trade" is
    5.5 hours stale otherwise, which is not a tick. Shoulder prints share the window's
    chain, so the window's first scored print already has a predecessor."""
    return [(p.window, p.occ) for p in prints]


def tick_labels(prints: list[Print]) -> list[str | None]:
    """Method 1 — naive tick test: sign of the price change vs the previous trade on
    the same contract. A zero tick claims nothing (the registration says *sign*)."""
    prev: dict[tuple[str, str], float] = {}
    out: list[str | None] = []
    for key, p in zip(_chain_key(prints), prints, strict=True):
        last = prev.get(key)
        if last is None or p.price == last:
            out.append(None)
        else:
            out.append("buy_side" if p.price > last else "sell_side")
        prev[key] = p.price
    return out


def _lookup_le(ts_list: list[int], ts_ms: int) -> int:
    """Index of the newest entry with ts <= ts_ms, or -1."""
    return bisect_right(ts_list, ts_ms) - 1


def _s_at(tape: dict[str, tuple[list[int], list[float]]], sym: str, ts_ms: int) -> float | None:
    entry = tape.get(sym)
    if entry is None:
        return None
    idx = _lookup_le(entry[0], ts_ms)
    return entry[1][idx] if idx >= 0 else None


def underlying_source(name: str) -> tuple[str, float]:
    """Which tape symbol carries S for this name, and its multiplier."""
    if name in SPX_ROOTS:
        return SPX_PROXY_SYMBOL, SPX_PROXY_MULT
    return name, 1.0


def _underlying_price(
    tape: dict[str, tuple[list[int], list[float]]], name: str, ts_ms: int
) -> float | None:
    sym, mult = underlying_source(name)
    spot = _s_at(tape, sym, ts_ms)
    return None if spot is None else spot * mult


def delta_adjusted_labels(
    prints: list[Print],
    chain: dict[str, list[Sample]],
    tape: dict[str, tuple[list[int], list[float]]],
) -> list[str | None]:
    """Method 2 — delta-adjusted tick test on the founder's residual:

        resid = (P_t - P_{t-1}) - [d*(S_t - S_{t-1}) + 0.5*g*(S_t - S_{t-1})^2
                                   + V*(sig_t - sig_{t-1})]

    d/g/V/sig come from the nearest 15s chain sample with ts <= trade ts; sig is read
    at both trade timestamps. Vendor vega is per 1% of vol and ``iv`` is a decimal, so V
    is scaled by VEGA_PER_VOL_SCALE before it meets d(sig). Any missing input claims
    nothing.
    """
    ts_index = {occ: [s.ts_ms for s in rows] for occ, rows in chain.items()}
    prev: dict[tuple[str, str], Print] = {}
    out: list[str | None] = []
    for key, p in zip(_chain_key(prints), prints, strict=True):
        last = prev.get(key)
        prev[key] = p
        rows = chain.get(p.occ)
        if last is None or not rows:
            out.append(None)
            continue
        idx_now = _lookup_le(ts_index[p.occ], p.ts_ms)
        idx_prev = _lookup_le(ts_index[p.occ], last.ts_ms)
        if idx_now < 0 or idx_prev < 0:
            out.append(None)
            continue
        now, before = rows[idx_now], rows[idx_prev]
        s_now = _underlying_price(tape, p.name, p.ts_ms)
        s_prev = _underlying_price(tape, p.name, last.ts_ms)
        if (
            s_now is None
            or s_prev is None
            or now.delta is None
            or now.gamma is None
            or now.vega is None
            or now.iv is None
            or before.iv is None
        ):
            out.append(None)
            continue
        ds = s_now - s_prev
        resid = (p.price - last.price) - (
            now.delta * ds
            + 0.5 * now.gamma * ds * ds
            + now.vega * VEGA_PER_VOL_SCALE * (now.iv - before.iv)
        )
        out.append(None if resid == 0 else ("buy_side" if resid > 0 else "sell_side"))
    return out


@dataclass(slots=True)
class QuoteNode:
    ts_ms: int
    bid: float | None
    ask: float | None
    src: int  # index of the source print for the 5s bound, else -1


def downsample_chain(chain: dict[str, list[Sample]], interval_s: int) -> dict[str, list[QuoteNode]]:
    """Effective sampling interval: keep the FIRST sample in each interval bucket."""
    step = interval_s * 1000
    out: dict[str, list[QuoteNode]] = {}
    for occ, rows in chain.items():
        kept: list[QuoteNode] = []
        seen: set[int] = set()
        for s in rows:
            bucket = s.ts_ms // step
            if bucket in seen:
                continue
            seen.add(bucket)
            kept.append(QuoteNode(ts_ms=s.ts_ms, bid=s.bid, ask=s.ask, src=-1))
        out[occ] = kept
    return out


def bound_timeline(prints: list[Print], interval_s: int) -> dict[str, list[QuoteNode]]:
    """The 5s BOUND's quote timeline, built from the print lab's own tick quotes
    downsampled to a 5s grid. Approximation: quotes are observed only at print times, so
    a print can pair with the quote it itself contributed — which is truth graded against
    truth, and on any spread wider than a couple of cents it agrees by construction.
    Those prints are flagged; the bound's headline and its gate are the self-excluded
    numbers."""
    step = interval_s * 1000
    acc: dict[str, list[QuoteNode]] = defaultdict(list)
    seen: set[tuple[str, int]] = set()
    for i, p in enumerate(prints):
        if p.q_ts_ms is None or p.q_bid is None or p.q_ask is None:
            continue
        bucket = p.q_ts_ms // step
        if (p.occ, bucket) in seen:
            continue
        seen.add((p.occ, bucket))
        acc[p.occ].append(QuoteNode(ts_ms=p.q_ts_ms, bid=p.q_bid, ask=p.q_ask, src=i))
    out: dict[str, list[QuoteNode]] = {}
    for occ, nodes in acc.items():
        nodes.sort(key=lambda n: n.ts_ms)
        out[occ] = nodes
    return out


def _quote_rule(price: float, bid: float | None, ask: float | None, tick: str | None) -> str | None:
    """Lee-Ready with an abstaining tick fallback: the quote rule off the midpoint, and
    at the midpoint the tick test — which abstains on a zero tick rather than guessing."""
    if bid is None or ask is None or bid <= 0 or ask <= 0 or bid >= ask:
        return None
    mid = (bid + ask) / 2.0
    if price > mid:
        return "buy_side"
    if price < mid:
        return "sell_side"
    return tick


def backpair_labels(
    prints: list[Print],
    timeline: dict[str, list[QuoteNode]],
    lookup: str,
    ticks: list[str | None],
) -> tuple[list[str | None], list[bool]]:
    """Method 3 — Lee-Ready against a sampled quote timeline.

    ``strict``: newest sample with ts <= trade ts (never forward — forward pairing
    systematically flips buyer sweeps to sells). ``interp``: linear interpolation of bid
    and ask between the surrounding samples, which is non-causal by construction.
    Returns the labels and, per print, whether it paired with a quote it itself
    contributed (5s bound only).
    """
    ts_index = {occ: [n.ts_ms for n in nodes] for occ, nodes in timeline.items()}
    out: list[str | None] = []
    self_flags: list[bool] = []
    for i, p in enumerate(prints):
        nodes = timeline.get(p.occ)
        idx = _lookup_le(ts_index[p.occ], p.ts_ms) if nodes else -1
        if idx < 0:
            out.append(None)
            self_flags.append(False)
            continue
        left = nodes[idx]
        if lookup == "strict":
            bid, ask = left.bid, left.ask
            self_flags.append(left.src == i)
        else:
            if idx + 1 >= len(nodes):
                out.append(None)
                self_flags.append(False)
                continue
            right = nodes[idx + 1]
            if left.bid is None or left.ask is None or right.bid is None or right.ask is None:
                out.append(None)
                self_flags.append(False)
                continue
            span = right.ts_ms - left.ts_ms
            w = 0.0 if span <= 0 else (p.ts_ms - left.ts_ms) / span
            bid = left.bid + w * (right.bid - left.bid)
            ask = left.ask + w * (right.ask - left.ask)
            self_flags.append(left.src == i or right.src == i)
        out.append(_quote_rule(p.price, bid, ask, ticks[i]))
    return out, self_flags


# ---------------------------------------------------------------------------------
# metrics
# ---------------------------------------------------------------------------------


@dataclass
class Core:
    """Agreement, bias and net-gauge ingredients for one population of prints."""

    n_truth: int = 0
    n_agree: int = 0
    n_disagree: int = 0
    n_noclaim: int = 0
    prem_truth: float = 0.0
    prem_agree: float = 0.0
    prem_method_buy: float = 0.0
    prem_method_buy_truth_sell: float = 0.0
    prem_method_sell: float = 0.0
    prem_method_sell_truth_buy: float = 0.0
    # Net-gauge fidelity on the MATCHED population: truth-labelled, gauge-eligible
    # prints only, so both nets are built from exactly the same prints.
    truth_net: dict[str, float] = field(default_factory=lambda: defaultdict(float))
    method_net: dict[str, float] = field(default_factory=lambda: defaultdict(float))


@dataclass
class Acc:
    """One cell x conditioning. ``excl_self`` repeats ``full`` over the prints that did
    not pair with their own DIR-1 quote (only ever different for the 5s bound)."""

    full: Core = field(default_factory=Core)
    excl_self: Core = field(default_factory=Core)
    n_undetermined: int = 0
    n_overreach: int = 0
    prem_undetermined: float = 0.0
    prem_overreach: float = 0.0
    self_paired: int = 0
    # Unregistered diagnostic: the method labels every print it can, so its gauge net
    # over the FULL claimed population differs from the matched-population net above.
    full_pop_method_net: dict[str, float] = field(default_factory=lambda: defaultdict(float))


def _net_sign(right: str, side: str) -> float:
    """Gauge convention: bull = call buys + put sells, bear = put buys + call sells."""
    bull = (right == "C" and side == "buy_side") or (right == "P" and side == "sell_side")
    return 1.0 if bull else -1.0


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n < 3:
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True))
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx <= 0 or syy <= 0:
        return None
    return sxy / math.sqrt(sxx * syy)


def _rate(num: float, den: float) -> float | None:
    return None if den <= 0 else num / den


def net_observations(core: Core) -> list[tuple[float, float]]:
    """The cell's (truth net, method net) pairs — one per scored session."""
    dates = sorted(set(core.truth_net) | set(core.method_net))
    return [(core.truth_net.get(d, 0.0), core.method_net.get(d, 0.0)) for d in dates]


def net_fidelity(observations: list[tuple[float, float]]) -> dict:
    """Correlation, sign agreement and max magnitude error over net observations.

    Sign agreement carries its realized N and the finest difference that N can express
    (1/N): a 5-observation figure cannot resolve a 95% threshold at all.
    """
    truth = [t for t, _ in observations]
    method = [m for _, m in observations]
    hits = sum(1 for t, m in observations if (t > 0) - (t < 0) == (m > 0) - (m < 0))
    mag_errors = [abs(m - t) / abs(t) for t, m in observations if t != 0]
    n = len(observations)
    return {
        "n_observations": n,
        "correlation": _pearson(truth, method),
        "sign_agreement": _rate(hits, n),
        "sign_agreement_resolution": (1.0 / n) if n else None,
        "max_magnitude_error": max(mag_errors) if mag_errors else None,
        "n_zero_truth_nets": n - len(mag_errors),
    }


def summarize_core(core: Core) -> dict:
    p_buy_given_sell = _rate(core.prem_method_sell_truth_buy, core.prem_method_sell)
    p_sell_given_buy = _rate(core.prem_method_buy_truth_sell, core.prem_method_buy)
    asymmetry_pp = (
        None
        if p_buy_given_sell is None or p_sell_given_buy is None
        else abs(p_buy_given_sell - p_sell_given_buy) * 100.0
    )
    claimed = core.n_agree + core.n_disagree
    return {
        "n_truth": core.n_truth,
        "n_agree": core.n_agree,
        "n_disagree": core.n_disagree,
        "n_noclaim": core.n_noclaim,
        "agreement": _rate(core.n_agree, core.n_truth),
        "agreement_premium_weighted": _rate(core.prem_agree, core.prem_truth),
        "p_truth_buy_given_method_sell_pw": p_buy_given_sell,
        "p_truth_sell_given_method_buy_pw": p_sell_given_buy,
        "directional_bias_asymmetry_pp": asymmetry_pp,
        "net_gauge": net_fidelity(net_observations(core)),
        "unregistered_diagnostics": {
            "claim_rate": _rate(claimed, core.n_truth),
            "agreement_when_claimed": _rate(core.n_agree, claimed),
        },
    }


def summarize(acc: Acc, *, is_bound: bool) -> dict:
    """Turn one accumulator into the registered metric set (+ labelled diagnostics)."""
    out = summarize_core(acc.full)
    out.update(
        {
            "n_undetermined": acc.n_undetermined,
            "n_overreach": acc.n_overreach,
            "overreach_rate": _rate(acc.n_overreach, acc.n_undetermined),
            "overreach_rate_premium_weighted": _rate(acc.prem_overreach, acc.prem_undetermined),
            "self_paired": acc.self_paired,
            "self_paired_share": _rate(acc.self_paired, acc.full.n_truth),
        }
    )
    if is_bound:
        # Truth-vs-truth prints are out of the bound's headline and out of its gate.
        out["excl_self_paired"] = summarize_core(acc.excl_self)
    dates = sorted(set(acc.full.truth_net) | set(acc.full_pop_method_net))
    out["unregistered_diagnostics"]["full_population_net"] = net_fidelity(
        [(acc.full.truth_net.get(d, 0.0), acc.full_pop_method_net.get(d, 0.0)) for d in dates]
    )
    return out


def gating_core(metrics: dict, *, is_bound: bool) -> dict:
    """The numbers a verdict rides: self-excluded for the bound, plain otherwise."""
    return metrics["excl_self_paired"] if is_bound else metrics


def _viable(metrics: dict, *, is_bound: bool, pooled_sign: float | None) -> bool:
    """The registered gate, on spike-conditioned premium-weighted terms. Sign agreement
    comes from the pooled unit (NET_SIGN_UNIT); the other three are per cell."""
    core = gating_core(metrics, is_bound=is_bound)
    agreement = core["agreement_premium_weighted"]
    asymmetry = core["directional_bias_asymmetry_pp"]
    mag_err = core["net_gauge"]["max_magnitude_error"]
    if agreement is None or asymmetry is None or mag_err is None or pooled_sign is None:
        return False
    return (
        agreement >= THRESH_AGREEMENT_PW
        and asymmetry <= THRESH_ASYMMETRY_PP
        and pooled_sign >= THRESH_NET_SIGN
        and mag_err <= THRESH_NET_MAG_ERR
    )


# ---------------------------------------------------------------------------------
# session classification + orchestration
# ---------------------------------------------------------------------------------


def is_opex(day: date) -> bool:
    """Monthly OpEx = the third Friday. Scored, but never pooled with ordinary days."""
    if day.weekday() != 4:
        return False
    return 15 <= day.day <= 21


def is_bound_variant(variant: str) -> bool:
    return variant.endswith("_bound")


def is_proxy_cell(method: str, name: str) -> bool:
    """SPX/SPXW delta-adjusted cells ride the registered SPY x10 proxy, not a real tape."""
    return method == "delta_tick" and name in SPX_ROOTS


def method_variants() -> list[tuple[str, str]]:
    """Every registered method x variant, in the order the prereg lists them."""
    out = [("tick", "naive"), ("delta_tick", "residual")]
    for interval in BACKPAIR_INTERVALS_S:
        for lookup in LOOKUPS:
            out.append(("backpair", f"{lookup}_{interval}s"))
    for lookup in LOOKUPS:
        out.append(("backpair", f"{lookup}_{BOUND_INTERVAL_S}s_bound"))
    return out


def variant_interval_s(variant: str) -> int | None:
    """Nominal sampling interval a back-pairing variant simulates."""
    head = variant.removesuffix("_bound").rsplit("_", 1)[-1]
    return int(head[:-1]) if head.endswith("s") and head[:-1].isdigit() else None


def label_session(
    prints: list[Print],
    chain: dict[str, list[Sample]],
    tape: dict[str, tuple[list[int], list[float]]],
) -> tuple[dict[tuple[str, str], list[str | None]], dict[tuple[str, str], list[bool]]]:
    """All method x variant labels for one session's captured prints (shoulders included
    as lookback; the caller scores only the in-scope ones)."""
    ticks = tick_labels(prints)
    none_flags = [False] * len(prints)
    labels: dict[tuple[str, str], list[str | None]] = {
        ("tick", "naive"): ticks,
        ("delta_tick", "residual"): delta_adjusted_labels(prints, chain, tape),
    }
    self_paired: dict[tuple[str, str], list[bool]] = {
        ("tick", "naive"): none_flags,
        ("delta_tick", "residual"): none_flags,
    }
    for interval in BACKPAIR_INTERVALS_S:
        timeline = downsample_chain(chain, interval)
        for lookup in LOOKUPS:
            key = ("backpair", f"{lookup}_{interval}s")
            labels[key], _ = backpair_labels(prints, timeline, lookup, ticks)
            self_paired[key] = none_flags
    bound = bound_timeline(prints, BOUND_INTERVAL_S)
    for lookup in LOOKUPS:
        key = ("backpair", f"{lookup}_{BOUND_INTERVAL_S}s_bound")
        labels[key], self_paired[key] = backpair_labels(prints, bound, lookup, ticks)
    return labels, self_paired


def spike_minutes(
    volumes: dict[tuple[str, str, str], dict[tuple[str, int], int]],
) -> dict[tuple[str, str, str], set[tuple[str, int]]]:
    """Top-decile volume minutes per (session class, name, window), pooled over the
    class's sessions and never across classes. Method-independent by construction, so
    every method is judged on the same minutes."""
    out: dict[tuple[str, str, str], set[tuple[str, int]]] = {}
    for key, minutes in volumes.items():
        ranked = sorted(minutes.items(), key=lambda kv: (-kv[1], kv[0]))
        take = math.ceil(len(ranked) * SPIKE_TOP_FRACTION)
        out[key] = {k for k, _ in ranked[:take]}
    return out


def method_measurable(method: str, variant: str, name: str, avail: dict) -> bool:
    """Does this method have its structural inputs for this name on this session?

    A method with no input file is UNMEASURED, which is a different fact from a method
    that measured badly — an absent tape must never read as a failed threshold.
    """
    if method == "tick" or is_bound_variant(variant):
        return True  # the print lab alone is enough
    if method == "delta_tick":
        return name in avail["greeks"] and underlying_source(name)[0] in avail["tape"]
    return name in avail["chain"]  # back-pairing needs the sampler's quote timeline


def _availability(
    chain: dict[str, list[Sample]],
    tape: dict[str, tuple[list[int], list[float]]],
    names: frozenset[str],
) -> dict[str, set[str]]:
    """Which names the session's captures can actually feed, per input kind."""
    with_chain: set[str] = set()
    with_greeks: set[str] = set()
    for occ, rows in chain.items():
        parsed = parse_occ_symbol(occ)
        if parsed is None or parsed.underlying not in names:
            continue
        with_chain.add(parsed.underlying)
        if any(s.delta is not None and s.gamma is not None and s.vega is not None for s in rows):
            with_greeks.add(parsed.underlying)
    return {"chain": with_chain, "greeks": with_greeks, "tape": set(tape)}


def score(
    dates: list[str],
    names: frozenset[str],
    print_dir: Path,
    chain_dir: Path,
    underlying_dir: Path,
) -> dict:
    sessions: dict[str, str] = {}
    inputs: dict[str, dict] = {}
    volumes: dict[tuple[str, str, str], dict[tuple[str, int], int]] = defaultdict(dict)

    # Pass 1 — session inventory + the minute-volume distribution the spike conditioning
    # ranks. Needs the pooled class, so it must precede scoring.
    usable: list[str] = []
    for date_iso in dates:
        prints = load_prints(print_dir, date_iso, names)
        if prints is None:
            inputs[date_iso] = {"print_lab": False}
            continue
        klass = "opex" if is_opex(date.fromisoformat(date_iso)) else "ordinary"
        sessions[date_iso] = klass
        usable.append(date_iso)
        scoped = [p for p in prints if p.in_scope]
        inputs[date_iso] = {
            "print_lab": True,
            "session_class": klass,
            "prints_captured": len(prints),
            "prints_in_scope": len(scoped),
            "truth_prints": sum(1 for p in scoped if p.truth),
            "undetermined_prints": sum(1 for p in scoped if p.truth_undetermined),
            "gauge_eligible_prints": sum(1 for p in scoped if p.gauge_eligible),
        }
        for p in scoped:
            bucket = volumes[(klass, p.name, p.window)]
            cell = (date_iso, p.minute)
            bucket[cell] = bucket.get(cell, 0) + p.size

    spikes = spike_minutes(volumes)
    cells: dict[tuple[str, str, str, str, str, str], Acc] = defaultdict(Acc)
    measured: dict[tuple[str, str, str, str], int] = defaultdict(int)

    # Pass 2 — label every print with every method and fold into the cells.
    for date_iso in usable:
        prints = load_prints(print_dir, date_iso, names) or []
        loaded = load_chain(chain_dir, date_iso)
        chain, chain_stats = loaded if loaded else ({}, {})
        tape = load_underlying(underlying_dir, date_iso)
        avail = _availability(chain, tape, names)
        inputs[date_iso].update(
            {
                "chain_samples": loaded is not None,
                "chain_contracts": chain_stats.get("contracts", 0),
                "chain_coverage_by_root": chain_stats.get("coverage_by_root", {}),
                "median_inter_sample_spacing_s": chain_stats.get("median_inter_sample_spacing_s"),
                "underlying_trades": sorted(tape),
                "names_with_greeks": sorted(avail["greeks"]),
            }
        )
        labels, self_paired = label_session(prints, chain, tape)
        klass = sessions[date_iso]

        for (method, variant), per_print in labels.items():
            flags = self_paired[(method, variant)]
            for name in names:
                # Touch the key either way: a cell with no measurable session must still
                # be REPORTED as unmeasured, never silently dropped from the grid.
                measured[(klass, method, variant, name)] += int(
                    method_measurable(method, variant, name, avail)
                )
            for i, p in enumerate(prints):
                if not p.in_scope:
                    continue  # shoulders are lookback only
                if not method_measurable(method, variant, p.name, avail):
                    continue  # unmeasured: never folded, never a failed threshold
                slices = ["all", "first5" if p.minute < FIRST_MINUTES else "rest"]
                if (date_iso, p.minute) in spikes.get((klass, p.name, p.window), ()):
                    slices.append("spike")
                for conditioning in slices:
                    acc = cells[(klass, method, variant, p.name, p.window, conditioning)]
                    _fold(acc, p, per_print[i], date_iso, flags[i])

    ordinary_count = sum(1 for k in sessions.values() if k == "ordinary")
    return _report(cells, measured, sessions, inputs, ordinary_count, names)


def _fold(acc: Acc, p: Print, claim: str | None, date_iso: str, self_paired: bool) -> None:
    if p.truth:
        _fold_core(acc.full, p, claim, date_iso)
        if self_paired:
            acc.self_paired += 1
        else:
            _fold_core(acc.excl_self, p, claim, date_iso)
        if claim is not None and p.gauge_eligible:
            acc.full_pop_method_net[date_iso] += _net_sign(p.right, claim) * p.premium
    elif p.truth_undetermined:
        acc.n_undetermined += 1
        acc.prem_undetermined += p.premium
        if claim is not None:
            acc.n_overreach += 1
            acc.prem_overreach += p.premium
            if p.gauge_eligible:
                acc.full_pop_method_net[date_iso] += _net_sign(p.right, claim) * p.premium


def _fold_core(core: Core, p: Print, claim: str | None, date_iso: str) -> None:
    core.n_truth += 1
    core.prem_truth += p.premium
    if claim is None:
        core.n_noclaim += 1
    elif claim == p.truth:
        core.n_agree += 1
        core.prem_agree += p.premium
    else:
        core.n_disagree += 1
    if claim == "buy_side":
        core.prem_method_buy += p.premium
        if p.truth == "sell_side":
            core.prem_method_buy_truth_sell += p.premium
    elif claim == "sell_side":
        core.prem_method_sell += p.premium
        if p.truth == "buy_side":
            core.prem_method_sell_truth_buy += p.premium
    # Net-gauge fidelity rides the gauge's own print filters, on the matched population.
    if p.gauge_eligible:
        core.truth_net[date_iso] += _net_sign(p.right, p.truth) * p.premium
        if claim is not None:
            core.method_net[date_iso] += _net_sign(p.right, claim) * p.premium


def _report(
    cells: dict[tuple[str, str, str, str, str, str], Acc],
    measured: dict[tuple[str, str, str, str], int],
    sessions: dict[str, str],
    inputs: dict[str, dict],
    ordinary_count: int,
    names: frozenset[str],
) -> dict:
    grouped: dict[tuple[str, str, str, str, str], dict[str, dict]] = defaultdict(dict)
    for (klass, method, variant, name, window, conditioning), acc in cells.items():
        grouped[(klass, method, variant, name, window)][conditioning] = summarize(
            acc, is_bound=is_bound_variant(variant)
        )

    # Pooled net observations (NET_SIGN_UNIT): every (session x name x window) net in the
    # method x variant x class, excluding proxy cells so a proxied underlying can never
    # move a real method's gate. Unmeasured cells contribute nothing by construction.
    pooled_obs: dict[tuple[str, str, str, str], list[tuple[float, float]]] = defaultdict(list)
    for (klass, method, variant, name, window, conditioning), acc in cells.items():
        if is_proxy_cell(method, name):
            continue
        core = acc.excl_self if is_bound_variant(variant) else acc.full
        pooled_obs[(klass, method, variant, conditioning)].extend(net_observations(core))
    pooled = {key: net_fidelity(obs) for key, obs in pooled_obs.items()}

    keys = set(grouped) | {(k, m, v, n, w) for (k, m, v, n) in measured for w in WINDOWS}
    rows: list[dict] = []
    for key in sorted(keys):
        klass, method, variant, name, window = key
        by_cond = grouped.get(key, {})
        sessions_measured = measured.get((klass, method, variant, name), 0)
        is_bound = is_bound_variant(variant)
        spike = by_cond.get("spike")
        overall = by_cond.get("all")
        pooled_spike = pooled.get((klass, method, variant, "spike"), {}).get("sign_agreement")
        pooled_all = pooled.get((klass, method, variant, "all"), {}).get("sign_agreement")
        viable_spike = bool(spike and _viable(spike, is_bound=is_bound, pooled_sign=pooled_spike))
        viable_all = bool(overall and _viable(overall, is_bound=is_bound, pooled_sign=pooled_all))
        # An absent input, or a window the capture holds no truth prints for, is
        # UNMEASURED — never a failed threshold. Both are structural gaps, not results.
        if sessions_measured == 0:
            unmeasured_reason = "no_input"
        elif not overall or overall["n_truth"] == 0:
            unmeasured_reason = "no_scored_prints"
        else:
            unmeasured_reason = None
        row = {
            "session_class": klass,
            "method": method,
            "variant": variant,
            "name": name,
            "window": window,
            "sessions_measured": sessions_measured,
            "unmeasured": unmeasured_reason is not None,
            "unmeasured_reason": unmeasured_reason,
            "metrics": by_cond,
            "viable": None if unmeasured_reason else viable_spike,
            # "A method that passes average-conditioned but fails spike-conditioned
            # is REJECTED (danger zone)."
            "rejected_danger_zone": bool(viable_all and not viable_spike),
        }
        if is_bound:
            row["is_bound"] = True
            row["gate_population"] = "excl_self_paired"
        if variant.startswith("interp"):
            row["non_causal"] = True
        interval = variant_interval_s(variant)
        if interval is not None:
            row["nominal_interval_s"] = interval
            row["measured_spacing_s"] = _median_spacing(inputs, klass, sessions)
        if is_proxy_cell(method, name):
            row["underlying_proxy"] = SPX_PROXY_LABEL
        rows.append(row)

    gate_met = ordinary_count >= MIN_ORDINARY_SESSIONS
    verdicts: list[dict] = []
    by_method: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in rows:
        if row["session_class"] == "ordinary":
            by_method[(row["method"], row["variant"])].append(row)
    for (method, variant), method_rows in sorted(by_method.items()):
        proxy_rows = [r for r in method_rows if r.get("underlying_proxy")]
        gating = [r for r in method_rows if not r["unmeasured"] and not r.get("underlying_proxy")]
        verdicts.append(
            {
                "method": method,
                "variant": variant,
                "cells": len(method_rows),
                "cells_unmeasured": sum(1 for r in method_rows if r["unmeasured"]),
                "cells_proxy": len(proxy_rows),
                "cells_gating": len(gating),
                "cells_viable": sum(1 for r in gating if r["viable"]),
                "cells_rejected_danger_zone": sum(1 for r in gating if r["rejected_danger_zone"]),
                "viable_all_cells": (
                    all(r["viable"] for r in gating) if (gating and gate_met) else None
                ),
                "proxy_cells_viable": sum(1 for r in proxy_rows if r["viable"]),
                "net_sign_pooled": pooled.get(("ordinary", method, variant, "spike"), {}),
            }
        )

    return {
        "prereg": "References/CLASSIFICATION_LAB_PREREG_2026-07-16.md",
        "generated_at": datetime.now(tz=_ET).isoformat(timespec="seconds"),
        "thresholds": {
            "agreement_premium_weighted": THRESH_AGREEMENT_PW,
            "directional_bias_asymmetry_pp": THRESH_ASYMMETRY_PP,
            "net_sign_agreement": THRESH_NET_SIGN,
            "net_max_magnitude_error": THRESH_NET_MAG_ERR,
            "conditioning": "spike",
            "net_sign_unit": NET_SIGN_UNIT,
            "net_sign_unit_note": NET_SIGN_UNIT_NOTE,
        },
        "caveats": _caveats(names),
        "sessions": sessions,
        "gate": {
            "ordinary_sessions": ordinary_count,
            "required": MIN_ORDINARY_SESSIONS,
            "met": gate_met,
        },
        "inputs": inputs,
        "cells": rows,
        "verdicts": verdicts,
    }


def _median_spacing(inputs: dict[str, dict], klass: str, sessions: dict[str, str]) -> float | None:
    """Measured inter-sample spacing over the class's sessions — what the nominal
    15s/30s/60s labels actually rest on."""
    seen = [
        inputs[d].get("median_inter_sample_spacing_s")
        for d, k in sessions.items()
        if k == klass and inputs.get(d, {}).get("median_inter_sample_spacing_s") is not None
    ]
    return statistics.median(seen) if seen else None


def _caveats(names: frozenset[str]) -> list[str]:
    """The prereg's own stated limits, restated on every report that quotes a number."""
    return [
        "print_lab ground truth covers the SUBSCRIBED SLICE only (~1,000 contracts incl. "
        "the gauge lane); every figure extrapolates to unsubscribed contracts by "
        "assumption, never by measurement (prereg 'Data inputs and their limits')",
        f"scored names: {', '.join(sorted(names))} — a name with no chain-sampler "
        "coverage is reported unmeasured, never as a failed threshold",
        "the 5s cell is a BOUND, not a measurement: its quote timeline is the print "
        "lab's own tick quotes, observed only at print times (prereg 'Methods under "
        "test' 3). Prints that paired with the quote they themselves contributed are "
        "truth graded against truth; they are excluded from its headline and its gate",
        "interpolated back-pairing is NON-CAUSAL — it reads a quote sample after the "
        "trade; it bounds what a denser sampler could do, it is not a live-usable rule",
        "delta-adjusted cells for SPX/SPXW ride the registered SPY x10 proxy; they are "
        "reported separately and excluded from the all-cells verdict and pooled gate",
        "net-gauge fidelity applies the gauge's own print filters (spread legs, hygiene "
        "conditions, the CLUSTER_PRINT_MIN_USD dust floor) to BOTH nets, on the matched "
        "truth-labelled population; the spread flag only exists in the capture from "
        "2026-07-17 forward, so earlier sessions under-filter spread legs",
        f"DIR-1 truth uses QUOTE_EPS={QUOTE_EPS}: on quotes {2 * QUOTE_EPS:.2f} wide or "
        "narrower a print can satisfy both the at-ask and at-bid tests, and the buy test "
        "is checked first, so truth itself carries a buy-side artifact on penny-wide "
        "quotes (signals/options_flow/intraday.py classify_direction)",
        "OpEx sessions are scored in their own cells and never pooled with ordinary ones",
    ]


# ---------------------------------------------------------------------------------
# cli
# ---------------------------------------------------------------------------------


def discover_dates(print_dir: Path, today: date) -> list[str]:
    """Every captured session strictly before today (today's file is still growing)."""
    found: set[str] = set()
    for path in print_dir.glob("*.jsonl*"):
        stem = path.name.split(".")[0]
        try:
            day = date.fromisoformat(stem)
        except ValueError:
            continue
        if day < today:
            found.add(stem)
    return sorted(found)


def _print_summary(report: dict) -> None:
    gate = report["gate"]
    print(
        f"sessions: {gate['ordinary_sessions']} ordinary "
        f"(need {gate['required']}, gate {'MET' if gate['met'] else 'NOT MET'}); "
        f"opex: {sum(1 for k in report['sessions'].values() if k == 'opex')}"
    )
    print(
        f"{'method':<11} {'variant':<17} {'gate':>5} {'unmeas':>6} {'proxy':>5} "
        f"{'viable':>6} {'danger':>6} {'poolN':>6} {'all-cells':>9}"
    )
    for v in report["verdicts"]:
        pooled = v["net_sign_pooled"].get("n_observations", 0)
        print(
            f"{v['method']:<11} {v['variant']:<17} {v['cells_gating']:>5} "
            f"{v['cells_unmeasured']:>6} {v['cells_proxy']:>5} {v['cells_viable']:>6} "
            f"{v['cells_rejected_danger_zone']:>6} {pooled:>6} {str(v['viable_all_cells']):>9}"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--dates", help="comma-separated ISO dates (default: every complete session)"
    )
    parser.add_argument(
        "--names",
        default=",".join(sorted(GAUGE_UNDERLYINGS)),
        help="comma-separated underlyings (default: the gauge universe)",
    )
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument(
        "--out", type=Path, help="report path (default: data/research/classification_lab/)"
    )
    args = parser.parse_args(argv)

    research = args.data_dir / "research"
    print_dir = research / "print_lab"
    dates = (
        [d.strip() for d in args.dates.split(",") if d.strip()]
        if args.dates
        else discover_dates(print_dir, datetime.now(tz=_ET).date())
    )
    if not dates:
        print("no captured sessions found", file=sys.stderr)
        return 1

    report = score(
        dates,
        frozenset(n.strip().upper() for n in args.names.split(",") if n.strip()),
        print_dir,
        research / "chain_samples",
        research / "underlying_trades",
    )
    out = args.out or (
        research / "classification_lab" / f"scores_{datetime.now(tz=_ET):%Y%m%dT%H%M%S}.json"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=1))
    _print_summary(report)
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
