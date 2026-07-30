#!/usr/bin/env python3
"""Classification-lab scorer — grades the cheap classification methods vs DIR-1 truth.

Implements `References/CLASSIFICATION_LAB_PREREG_2026-07-16.md` exactly. That document
is LAW: the three methods, their variants, every metric, the conditioning slices and the
success thresholds were fixed before the first ground-truth day accrued, so this script
computes what was pre-registered and nothing else — no extra metric, no tuned threshold.

    uv run python tools/classification_lab_scorer.py                 # every complete session
    uv run python tools/classification_lab_scorer.py --dates 2026-07-20,2026-07-21

Reads gitignored research captures only (data/research/print_lab, chain_samples,
underlying_trades); writes one JSON report to data/research/classification_lab/.
Offline and read-only: no network, no broker, nothing that touches trading/.
"""

from __future__ import annotations

import argparse
import gzip
import json
import math
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
from signals.options_flow.constants import GAUGE_UNDERLYINGS  # noqa: E402
from signals.options_flow.opening_pressure import WINDOWS  # noqa: E402

_ET = ZoneInfo("America/New_York")

DEFAULT_DATA_DIR = ROOT / "data"
CONTRACT_MULT = 100.0

# --- registered method geometry ----------------------------------------------------
# Prereg "Methods under test" 3: back-pairing at 15s/30s/60s effective sampling, two
# lookup variants each; plus a 5s BOUND off the print lab's own tick quotes (an
# approximation — quotes exist only at print times — labelled as such everywhere).
BACKPAIR_INTERVALS_S = (15, 30, 60)
BOUND_INTERVAL_S = 5
LOOKUPS = ("strict", "interp")

# Prereg "Methods under test" 2: SPX/SPXW have no equity tape — SPY x 10 proxy, scored
# separately (its own variant, never pooled with the real-tape names).
SPX_ROOTS = frozenset({"SPX", "SPXW"})
SPX_PROXY_SYMBOL = "SPY"
SPX_PROXY_MULT = 10.0

# --- registered conditioning + thresholds ------------------------------------------
SPIKE_TOP_FRACTION = 0.10  # "top-decile volume minutes"
FIRST_MINUTES = 5  # "first-5-minutes vs rest"

THRESH_AGREEMENT_PW = 0.85
THRESH_ASYMMETRY_PP = 5.0
THRESH_NET_SIGN = 0.95
THRESH_NET_MAG_ERR = 0.15
MIN_ORDINARY_SESSIONS = 5


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


def _window_of(ts_ms: int) -> tuple[str, int] | None:
    """Gauge window + 0-based minute offset, or None outside both windows.

    The print lab writes 5-minute shoulders around each window; the prereg scores
    "the gauge windows", so the shoulders are out of scope.
    """
    dt = datetime.fromtimestamp(ts_ms / 1000, tz=_ET)
    minute = dt.hour * 60 + dt.minute
    for window, (start, length) in WINDOWS.items():
        offset = minute - start
        if 0 <= offset < length:
            return window, offset
    return None


@dataclass(slots=True)
class Print:
    """One print-lab row inside a gauge window, with its ground-truth label."""

    ts_ms: int
    occ: str
    name: str
    price: float
    size: int
    right: str
    window: str
    minute: int
    premium: float
    truth: str | None  # buy_side / sell_side, only when basis == stream_quote
    truth_undetermined: bool
    q_bid: float | None
    q_ask: float | None
    q_ts_ms: int | None


def load_prints(base: Path, date_iso: str, names: frozenset[str]) -> list[Print] | None:
    """Ground truth: every classified print in the gauge windows on the subscribed slice.

    Only ``basis == "stream_quote"`` rows score as truth (prereg "Ground truth"); rows
    whose direction came from another basis are neither truth nor overreach denominator.
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
            basis = str(row.get("basis") or "")
            truth = (
                direction
                if (direction in ("buy_side", "sell_side") and basis == "stream_quote")
                else None
            )
            out.append(
                Print(
                    ts_ms=ts_ms,
                    occ=occ,
                    name=name,
                    price=price,
                    size=size,
                    right=parsed.right,
                    window=slot[0],
                    minute=slot[1],
                    premium=price * size * CONTRACT_MULT,
                    truth=truth,
                    truth_undetermined=(direction == "undetermined"),
                    q_bid=_num(row.get("bid")),
                    q_ask=_num(row.get("ask")),
                    q_ts_ms=int(row["q_ts_ms"]) if row.get("q_ts_ms") is not None else None,
                )
            )
    out.sort(key=lambda p: p.ts_ms)
    return out


@dataclass(slots=True)
class Sample:
    """One 15s chain-sampler observation of a contract."""

    ts_ms: int
    bid: float | None
    ask: float | None
    iv: float | None
    delta: float | None
    gamma: float | None
    vega: float | None


def load_chain(base: Path, date_iso: str) -> dict[str, list[Sample]] | None:
    """The 15s chain sampler's quote/greek timeline, per OCC contract."""
    handle = _open_capture(base, date_iso)
    if handle is None:
        return None
    by_occ: dict[str, list[Sample]] = defaultdict(list)
    with handle as fh:
        for line in fh:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            occ = row.get("occ")
            ts = row.get("ts")
            if not occ or not ts:
                continue  # sampler error rows carry neither
            try:
                ts_ms = int(datetime.fromisoformat(str(ts)).timestamp() * 1000)
            except ValueError:
                continue
            by_occ[str(occ).strip().upper()].append(
                Sample(
                    ts_ms=ts_ms,
                    bid=_num(row.get("bid")),
                    ask=_num(row.get("ask")),
                    iv=_num(row.get("iv")),
                    delta=_num(row.get("delta")),
                    gamma=_num(row.get("gamma")),
                    vega=_num(row.get("vega")),
                )
            )
    for rows in by_occ.values():
        rows.sort(key=lambda s: s.ts_ms)
    return dict(by_occ)


def load_underlying(base: Path, date_iso: str) -> dict[str, tuple[list[int], list[float]]]:
    """Underlying trade tape S_t at exchange timestamps: {"ts_ms", "sym", "price"} rows.

    Prereg method 2 sources S from the historical equity-trades REST endpoint; this
    scorer is offline, so the fetched tape is supplied as a capture file. Missing tape
    is never silent — method 2 simply makes no claim and the counts show it.
    """
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
    5.5 hours stale otherwise, which is not a tick."""
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


def _underlying_price(
    tape: dict[str, tuple[list[int], list[float]]], name: str, ts_ms: int
) -> float | None:
    if name in SPX_ROOTS:
        spot = _s_at(tape, SPX_PROXY_SYMBOL, ts_ms)
        return None if spot is None else spot * SPX_PROXY_MULT
    return _s_at(tape, name, ts_ms)


def delta_adjusted_labels(
    prints: list[Print],
    chain: dict[str, list[Sample]],
    tape: dict[str, tuple[list[int], list[float]]],
) -> list[str | None]:
    """Method 2 — delta-adjusted tick test on the founder's residual:

        resid = (P_t - P_{t-1}) - [d*(S_t - S_{t-1}) + 0.5*g*(S_t - S_{t-1})^2
                                   + V*(sig_t - sig_{t-1})]

    d/g/V/sig come from the nearest 15s chain sample with ts <= trade ts; sig is read
    at both trade timestamps. Any missing input claims nothing.
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
            now.delta * ds + 0.5 * now.gamma * ds * ds + now.vega * (now.iv - before.iv)
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
    downsampled to a 5s grid. Approximation: quotes are observed only at print times,
    so a print can pair with the quote it itself contributed (counted as self_paired)."""
    step = interval_s * 1000
    acc: dict[str, list[QuoteNode]] = defaultdict(list)
    seen: dict[tuple[str, int], bool] = {}
    for i, p in enumerate(prints):
        if p.q_ts_ms is None or p.q_bid is None or p.q_ask is None:
            continue
        bucket = p.q_ts_ms // step
        if seen.get((p.occ, bucket)):
            continue
        seen[(p.occ, bucket)] = True
        acc[p.occ].append(QuoteNode(ts_ms=p.q_ts_ms, bid=p.q_bid, ask=p.q_ask, src=i))
    out: dict[str, list[QuoteNode]] = {}
    for occ, nodes in acc.items():
        nodes.sort(key=lambda n: n.ts_ms)
        out[occ] = nodes
    return out


def _quote_rule(price: float, bid: float | None, ask: float | None, tick: str | None) -> str | None:
    """Lee-Ready: quote rule off the midpoint, tick test at the midpoint."""
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
    systematically flips buyer sweeps to sells). ``interp``: linear interpolation of
    bid and ask between the surrounding samples. Returns the labels and, per print,
    whether it paired with a quote it itself contributed (5s bound only).
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
class Acc:
    """Every registered metric's raw ingredients for one cell x conditioning."""

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
    n_undetermined: int = 0
    n_overreach: int = 0
    prem_undetermined: float = 0.0
    prem_overreach: float = 0.0
    self_paired: int = 0
    # net-gauge fidelity: per session, the window's per-name net premium
    truth_net: dict[str, float] = field(default_factory=lambda: defaultdict(float))
    method_net: dict[str, float] = field(default_factory=lambda: defaultdict(float))


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


def summarize(acc: Acc) -> dict:
    """Turn one accumulator into the registered metric set."""
    dates = sorted(set(acc.truth_net) | set(acc.method_net))
    truth = [acc.truth_net.get(d, 0.0) for d in dates]
    method = [acc.method_net.get(d, 0.0) for d in dates]
    sign_hits = sum(
        1 for t, m in zip(truth, method, strict=True) if (t > 0) - (t < 0) == (m > 0) - (m < 0)
    )
    mag_errors = [abs(m - t) / abs(t) for t, m in zip(truth, method, strict=True) if t != 0]

    p_buy_given_sell = _rate(acc.prem_method_sell_truth_buy, acc.prem_method_sell)
    p_sell_given_buy = _rate(acc.prem_method_buy_truth_sell, acc.prem_method_buy)
    asymmetry_pp = (
        None
        if p_buy_given_sell is None or p_sell_given_buy is None
        else abs(p_buy_given_sell - p_sell_given_buy) * 100.0
    )
    return {
        "n_truth": acc.n_truth,
        "n_agree": acc.n_agree,
        "n_disagree": acc.n_disagree,
        "n_noclaim": acc.n_noclaim,
        "agreement": _rate(acc.n_agree, acc.n_truth),
        "agreement_premium_weighted": _rate(acc.prem_agree, acc.prem_truth),
        "p_truth_buy_given_method_sell_pw": p_buy_given_sell,
        "p_truth_sell_given_method_buy_pw": p_sell_given_buy,
        "directional_bias_asymmetry_pp": asymmetry_pp,
        "n_undetermined": acc.n_undetermined,
        "n_overreach": acc.n_overreach,
        "overreach_rate": _rate(acc.n_overreach, acc.n_undetermined),
        "overreach_rate_premium_weighted": _rate(acc.prem_overreach, acc.prem_undetermined),
        "self_paired": acc.self_paired,
        "net_gauge": {
            "n_sessions": len(dates),
            "correlation": _pearson(truth, method),
            "sign_agreement": _rate(sign_hits, len(dates)),
            "max_magnitude_error": max(mag_errors) if mag_errors else None,
            "n_zero_truth_nets": len(dates) - len(mag_errors),
        },
    }


def _viable(spike: dict) -> bool:
    """The registered gate, on spike-conditioned premium-weighted terms."""
    agreement = spike["agreement_premium_weighted"]
    asymmetry = spike["directional_bias_asymmetry_pp"]
    net = spike["net_gauge"]
    if agreement is None or asymmetry is None:
        return False
    if net["sign_agreement"] is None or net["max_magnitude_error"] is None:
        return False
    return (
        agreement >= THRESH_AGREEMENT_PW
        and asymmetry <= THRESH_ASYMMETRY_PP
        and net["sign_agreement"] >= THRESH_NET_SIGN
        and net["max_magnitude_error"] <= THRESH_NET_MAG_ERR
    )


# ---------------------------------------------------------------------------------
# session classification + orchestration
# ---------------------------------------------------------------------------------


def is_opex(day: date) -> bool:
    """Monthly OpEx = the third Friday. Scored, but never pooled with ordinary days."""
    if day.weekday() != 4:
        return False
    return 15 <= day.day <= 21


def method_variants() -> list[tuple[str, str]]:
    """Every registered method x variant, in the order the prereg lists them."""
    out = [("tick", "naive"), ("delta_tick", "residual")]
    for interval in BACKPAIR_INTERVALS_S:
        for lookup in LOOKUPS:
            out.append(("backpair", f"{lookup}_{interval}s"))
    for lookup in LOOKUPS:
        out.append(("backpair", f"{lookup}_{BOUND_INTERVAL_S}s_bound"))
    return out


def label_session(
    prints: list[Print],
    chain: dict[str, list[Sample]],
    tape: dict[str, tuple[list[int], list[float]]],
) -> tuple[dict[tuple[str, str], list[str | None]], dict[tuple[str, str], list[bool]]]:
    """All method x variant labels for one session's scoped prints."""
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
    class's sessions. Method-independent by construction, so every method is judged
    on the same minutes."""
    out: dict[tuple[str, str, str], set[tuple[str, int]]] = {}
    for key, minutes in volumes.items():
        ranked = sorted(minutes.items(), key=lambda kv: (-kv[1], kv[0]))
        take = math.ceil(len(ranked) * SPIKE_TOP_FRACTION)
        out[key] = {k for k, _ in ranked[:take]}
    return out


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

    # Pass 1 — session inventory + the minute-volume distribution the spike
    # conditioning ranks. Needs the pooled class, so it must precede scoring.
    usable: list[str] = []
    for date_iso in dates:
        prints = load_prints(print_dir, date_iso, names)
        if prints is None:
            inputs[date_iso] = {"print_lab": False}
            continue
        klass = "opex" if is_opex(date.fromisoformat(date_iso)) else "ordinary"
        sessions[date_iso] = klass
        usable.append(date_iso)
        inputs[date_iso] = {
            "print_lab": True,
            "session_class": klass,
            "prints_in_scope": len(prints),
            "truth_prints": sum(1 for p in prints if p.truth),
            "undetermined_prints": sum(1 for p in prints if p.truth_undetermined),
        }
        for p in prints:
            bucket = volumes[(klass, p.name, p.window)]
            cell = (date_iso, p.minute)
            bucket[cell] = bucket.get(cell, 0) + p.size

    spikes = spike_minutes(volumes)
    cells: dict[tuple[str, str, str, str, str, str], Acc] = defaultdict(Acc)

    # Pass 2 — label every print with every method and fold into the cells.
    for date_iso in usable:
        prints = load_prints(print_dir, date_iso, names) or []
        chain = load_chain(chain_dir, date_iso)
        inputs[date_iso]["chain_samples"] = chain is not None
        chain = chain or {}
        inputs[date_iso]["chain_contracts"] = len(chain)
        inputs[date_iso]["chain_greek_samples"] = sum(
            1 for rows in chain.values() for s in rows if s.delta is not None
        )
        tape = load_underlying(underlying_dir, date_iso)
        inputs[date_iso]["underlying_trades"] = sorted(tape)
        labels, self_paired = label_session(prints, chain, tape)
        klass = sessions[date_iso]

        for (method, variant), per_print in labels.items():
            flags = self_paired[(method, variant)]
            for i, p in enumerate(prints):
                slices = ["all", "first5" if p.minute < FIRST_MINUTES else "rest"]
                if (date_iso, p.minute) in spikes.get((klass, p.name, p.window), ()):
                    slices.append("spike")
                for conditioning in slices:
                    acc = cells[(klass, method, variant, p.name, p.window, conditioning)]
                    _fold(acc, p, per_print[i], date_iso, flags[i])

    ordinary_count = sum(1 for k in sessions.values() if k == "ordinary")
    return _report(cells, sessions, inputs, ordinary_count)


def _fold(acc: Acc, p: Print, claim: str | None, date_iso: str, self_paired: bool) -> None:
    if self_paired:
        acc.self_paired += 1
    if p.truth:
        acc.n_truth += 1
        acc.prem_truth += p.premium
        acc.truth_net[date_iso] += _net_sign(p.right, p.truth) * p.premium
        if claim is None:
            acc.n_noclaim += 1
        elif claim == p.truth:
            acc.n_agree += 1
            acc.prem_agree += p.premium
        else:
            acc.n_disagree += 1
        if claim == "buy_side":
            acc.prem_method_buy += p.premium
            if p.truth == "sell_side":
                acc.prem_method_buy_truth_sell += p.premium
        elif claim == "sell_side":
            acc.prem_method_sell += p.premium
            if p.truth == "buy_side":
                acc.prem_method_sell_truth_buy += p.premium
    elif p.truth_undetermined:
        acc.n_undetermined += 1
        acc.prem_undetermined += p.premium
        if claim is not None:
            acc.n_overreach += 1
            acc.prem_overreach += p.premium
    if claim is not None:
        acc.method_net[date_iso] += _net_sign(p.right, claim) * p.premium


def _report(
    cells: dict[tuple[str, str, str, str, str, str], Acc],
    sessions: dict[str, str],
    inputs: dict[str, dict],
    ordinary_count: int,
) -> dict:
    grouped: dict[tuple[str, str, str, str, str], dict[str, dict]] = defaultdict(dict)
    for (klass, method, variant, name, window, conditioning), acc in cells.items():
        grouped[(klass, method, variant, name, window)][conditioning] = summarize(acc)

    rows: list[dict] = []
    for key, by_cond in sorted(grouped.items()):
        klass, method, variant, name, window = key
        spike = by_cond.get("spike")
        overall = by_cond.get("all")
        viable_spike = _viable(spike) if spike else False
        viable_all = _viable(overall) if overall else False
        rows.append(
            {
                "session_class": klass,
                "method": method,
                "variant": variant,
                "name": name,
                "window": window,
                "metrics": by_cond,
                "viable": viable_spike,
                # "A method that passes average-conditioned but fails spike-conditioned
                # is REJECTED (danger zone)."
                "rejected_danger_zone": viable_all and not viable_spike,
            }
        )

    gate_met = ordinary_count >= MIN_ORDINARY_SESSIONS
    verdicts: list[dict] = []
    by_method: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in rows:
        if row["session_class"] == "ordinary":
            by_method[(row["method"], row["variant"])].append(row)
    for (method, variant), method_rows in sorted(by_method.items()):
        verdicts.append(
            {
                "method": method,
                "variant": variant,
                "cells": len(method_rows),
                "cells_viable": sum(1 for r in method_rows if r["viable"]),
                "cells_rejected_danger_zone": sum(
                    1 for r in method_rows if r["rejected_danger_zone"]
                ),
                "viable_all_cells": (
                    all(r["viable"] for r in method_rows) if (method_rows and gate_met) else None
                ),
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
        },
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
        f"{'method':<11} {'variant':<17} {'cells':>5} {'viable':>6} {'danger':>6} {'all-cells':>9}"
    )
    for v in report["verdicts"]:
        print(
            f"{v['method']:<11} {v['variant']:<17} {v['cells']:>5} {v['cells_viable']:>6} "
            f"{v['cells_rejected_danger_zone']:>6} {str(v['viable_all_cells']):>9}"
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
