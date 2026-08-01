#!/usr/bin/env python3
"""Phase 3.2 — the hedge footprint on large single-leg option prints.

Spec: `References/FLOW_ROADMAP.md` → "3.2 spec (registered 2026-08-01, before
build)". Registered BEFORE any data was touched; this module implements it
clause by clause and never substitutes a threshold.

The mechanism sentence, restated so the code can be read against it: a market
maker who fills a large single-leg option order carries instant directional risk
and neutralizes delta in the stock within seconds–minutes; that stock volume IS
the footprint. The falsifiable prediction is a SIZE, not a direction:

    H = (customer side sign) × Δ_signed × contracts × 100        [shares]

side sign +1 customer buy / −1 sell, Δ_signed + for calls / − for puts. Customer
buys a call → dealer is short the call → dealer BUYS stock (H > 0). Customer buys
a put → dealer is long delta → dealer SELLS (H < 0). Sells reverse both.

Measured β is an **attenuated lower bound** (dealer-fill share × hedge intensity
× book netting × slicing, minus the customer-side self-hedge channel, minus
errors-in-variables in H). Existence means β > 0, never β = 1.

Four resumable stages, artifacts under `data/research/hedge_footprint/`; nothing
is ever refetched once it is on disk:

    select   Stage 1 — on-disk only. Every corpus-computable filter over the
             qualifying (code-provenance) training sessions; Δ_select from the
             PRIOR close; rank |H_select|; top 1,000 + the full drop ledger.
    bars     Stage 2 — detectability. Prior-20-session 1-min bars per candidate,
             expected 29s volume = median same-clock-minute volume × 29.1/60,
             D = |H_select| / expected ≥ 3; news fence; rank D, cap 10 per
             underlying, top 300.
    fetch    Per event AND its 20 symmetric same-clock-time control sessions:
             trades + quotes over [·−300s, ·+300s], feed="sip", volume asserted
             against the day's SIP daily bar, RAISES on the page cap.
    analyze  Δ_measure (spot = equity NBBO mid at T), S/E/Z per horizon
             (16g/64g/165g, g = 1.82s from 3.1), quiet tier at p50, Looks
             A/A′/B/C, cluster-permutation inference, cluster bootstrap, the MDE
             power floor by injection, and the registered verdict table.

    .venv/bin/python tools/hedge_footprint.py select
    .venv/bin/python tools/hedge_footprint.py bars
    .venv/bin/python tools/hedge_footprint.py fetch
    .venv/bin/python tools/hedge_footprint.py analyze

Read-only market data; no orders; nothing under `trading/`. Consumes NO outcomes
— microstructure seconds around prints, never forward returns — so the test split
stays sealed.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import sys
from datetime import date, datetime, timedelta
from datetime import time as dtime
from pathlib import Path
from typing import Callable, NamedTuple
from zoneinfo import ZoneInfo

import httpx
import numpy as np
from scipy.stats import theilslopes

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from config.settings import alpaca_config  # noqa: E402
from db.flow_corpus import DEFAULT_DB_PATH, bars_before_date, connect  # noqa: E402
from tools.condition_labels import JOIN_FLOOR, PACKAGE_CODES, capture_complete  # noqa: E402
from tools.flow_corpus_rollup import (  # noqa: E402
    DEFS_DIR,
    LABELS_DIR,
    PX,
    TCBBO_DIR,
    _multileg,
    _order_groups,
    fence_and_classify,
    multileg_flags,
)
from tools.hedge_clock_skew import parse_t_ns  # noqa: E402
from tools.root_classification_build import load as load_classification  # noqa: E402
from trading.market_calendar import is_early_close  # noqa: E402

logger = logging.getLogger("hedge_footprint")

SPEC = "FLOW_ROADMAP.md 3.2 (registered 2026-08-01)"
_ET = ZoneInfo("America/New_York")
OUT_DIR = ROOT / "data" / "research" / "hedge_footprint"
ROOT_CLASSIFICATION = ROOT / "signals" / "options_flow" / "root_classification.json"

# --- registered constants (every one of these is a spec clause) --------------
TRAIN_START = date(2025, 7, 28)
TRAIN_END = date(2026, 5, 31)             # inclusive
G_NS = 1_820_000_000                      # 3.1's measured lead-lag grain, 1.82s
HORIZONS_G = (16, 64, 165)                # ≈29s (primary) / 2min / 5min
PRIMARY_HORIZON_G = 16
SNAPSHOT_S = 300                          # [·−300s, ·+300s] per window
PREMIUM_MIN = 25_000.0
PREMIUM_ROBUST = 100_000.0                # reported robustness leg
DTE_MIN = 1                               # 0DTE is a logged future stratum
DELTA_BAND = (0.15, 0.85)                 # primary
DELTA_BAND_SECONDARY = (0.05, 0.95)       # registered secondary read
DELTA_STABILITY_MAX = 0.05                # ±1 tick may not move |Δ| by more
ADV_MIN = 200_000.0
ADV_LOOKBACK = 20
PRICE_MIN = 5.0
T_WINDOW_ET = (9 * 3600 + 35 * 60, 15 * 3600 + 55 * 60)     # inclusive both ends
RELEASE_FENCES_ET = (
    (9 * 3600 + 55 * 60, 10 * 3600 + 5 * 60),
    (13 * 3600 + 55 * 60, 14 * 3600 + 5 * 60),
)
ISOLATION_WINDOW_NS = 120 * 1_000_000_000
ISOLATION_MAX_FRAC = 0.25
NEWS_FENCE_S = 300
STAGE1_TOP = 1_000
STAGE2_TOP = 300
STAGE2_TOP_ROBUST = 150                   # reported robustness leg
PER_UNDERLYING_CAP = 10
D_MIN = 3.0
EXPECTED_WINDOW_S = 29.1                  # the spec's own 29s figure
N_CONTROLS = 20                           # 10 before / 10 after, filled one-sided
R_FLAT = 0.04
Q_FLAT = 0.0
YEAR_SECONDS = 365.0 * 86400.0
QUIET_PCTL = 50.0                         # quiet tier = pre-window vol ≤ p50 of controls
FLOOR_EVENTS = 100
FLOOR_UNDERLYINGS = 25
FLOOR_MDE = 0.3
PERM_ALPHA = 0.01                         # one-sided
N_PERM = 2_000
N_BOOT = 2_000
SEED = 20260801

# --- mechanics ---------------------------------------------------------------
FEED = "sip"                              # passed LITERALLY, per spec
PAGE_LIMIT = 10_000
MAX_PAGES = 200
TRF_EXCHANGE = "D"                        # FINRA TRF: reporting-firm clock
UNDEF_SIZE = 4_294_967_295                # tcbbo size sentinel
# An IEX fallback understates SIP volume ~20×. A window that carries less than
# this fraction of its pro-rata share of the day's SIP volume is a feed fault,
# not a quiet tape: IEX runs ~2-3% of SIP, a genuinely quiet 10-minute window
# still runs tens of percent of pro-rata. ASSUMPTION — the spec mandates the
# assertion but does not fix the threshold.
FEED_ASSERT_MIN_FRAC = 0.10
RTH_SECONDS = 6.5 * 3600
# ASSUMPTION — the spec says "±1 option tick" without defining it. OPRA penny
# pilot: $0.01 below $3.00, $0.05 at or above.
TICK_BREAK = 3.0
TICK_SMALL, TICK_LARGE = 0.01, 0.05

_PACKAGE_LIST = sorted(PACKAGE_CODES)


# =============================================================================
# the registered per-tape (z) condition exclusion table
# =============================================================================

def build_condition_table() -> dict[str, dict[str, list[str]]]:
    """Per-tape trade-condition exclusions, built at run and logged as a table.

    The CTA (tapes A/B, NYSE-listed) and UTP (tape C, Nasdaq-listed) alphabets
    differ on exactly the codes this study must exclude — measured: F regular
    ' ' / average-price 'B'; NVDA regular '@' / average-price 'W'. One pooled
    set would either keep NVDA's average-price crosses or throw away F's
    regular sales.

    `qct` is NOT part of the exclusion set proper: a Qualified Contingent Trade
    is ANOTHER party's tied stock leg and is reported separately rather than
    read as this event's hedge.
    """
    common_oos = ["P", "Z", "U", "T"]     # prior-reference-price + out-of-sequence
    return {
        "CTA": {
            "regular": [" "],
            "prior_reference_price": ["P"],
            "out_of_sequence": ["Z", "U", "T"],
            "average_price": ["B"],
            "derivatively_priced": ["4"],
            "excluded": sorted(set(common_oos + ["B", "4"])),
            "qct": ["7"],
        },
        "UTP": {
            "regular": ["@"],
            "prior_reference_price": ["P"],
            "out_of_sequence": ["Z", "U", "T"],
            "average_price": ["W"],
            "derivatively_priced": ["4"],
            "excluded": sorted(set(common_oos + ["W", "4"])),
            "qct": ["7"],
        },
    }


def _tape_family(tape: np.ndarray) -> np.ndarray:
    """'A'/'B' → CTA, 'C' → UTP, anything else → '' (both sets applied)."""
    fam = np.full(len(tape), "", dtype="U3")
    fam[(tape == "A") | (tape == "B")] = "CTA"
    fam[tape == "C"] = "UTP"
    return fam


def _has_code(cond: np.ndarray, codes: list[str]) -> np.ndarray:
    """True where the packed condition string carries any of `codes`."""
    out = np.zeros(len(cond), dtype=bool)
    for ch in codes:
        out |= np.char.find(cond, ch) >= 0
    return out


def condition_masks(
    cond: np.ndarray, tape: np.ndarray, table: dict[str, dict[str, list[str]]]
) -> tuple[np.ndarray, np.ndarray]:
    """(excluded, qct) per print, resolving the alphabet by tape.

    A print with an unknown/absent tape gets the UNION of both alphabets —
    the conservative arm, so an unlabelled average-price cross can never leak in.
    """
    fam = _tape_family(tape)
    excluded = np.zeros(len(cond), dtype=bool)
    qct = np.zeros(len(cond), dtype=bool)
    both_excl = sorted(set(table["CTA"]["excluded"]) | set(table["UTP"]["excluded"]))
    for name, sel in (("CTA", fam == "CTA"), ("UTP", fam == "UTP")):
        if sel.any():
            excluded[sel] = _has_code(cond[sel], table[name]["excluded"])
            qct[sel] = _has_code(cond[sel], table[name]["qct"])
    unknown = fam == ""
    if unknown.any():
        excluded[unknown] = _has_code(cond[unknown], both_excl)
        qct[unknown] = _has_code(cond[unknown], table["CTA"]["qct"])
    return excluded, qct


# =============================================================================
# Black–Scholes, IV inversion, Δ — all pure
# =============================================================================

_SQRT2 = math.sqrt(2.0)


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / _SQRT2))


def discounted_intrinsic(
    spot: float, strike: float, t_years: float, is_put: bool,
    r: float = R_FLAT, q: float = Q_FLAT,
) -> float:
    """European lower bound. The spec drops a quote-mid below THIS, not below
    the undiscounted intrinsic — a deep ITM call legitimately trades under S−K."""
    fwd = spot * math.exp(-q * t_years)
    disc_k = strike * math.exp(-r * t_years)
    return max(disc_k - fwd, 0.0) if is_put else max(fwd - disc_k, 0.0)


def bs_price(
    spot: float, strike: float, t_years: float, vol: float, is_put: bool,
    r: float = R_FLAT, q: float = Q_FLAT,
) -> float:
    if t_years <= 0.0 or vol <= 0.0 or spot <= 0.0 or strike <= 0.0:
        return discounted_intrinsic(spot, strike, max(t_years, 0.0), is_put, r, q)
    sqrt_t = math.sqrt(t_years)
    d1 = (math.log(spot / strike) + (r - q + 0.5 * vol * vol) * t_years) / (vol * sqrt_t)
    d2 = d1 - vol * sqrt_t
    df_s = spot * math.exp(-q * t_years)
    df_k = strike * math.exp(-r * t_years)
    if is_put:
        return df_k * _norm_cdf(-d2) - df_s * _norm_cdf(-d1)
    return df_s * _norm_cdf(d1) - df_k * _norm_cdf(d2)


def bs_delta(
    spot: float, strike: float, t_years: float, vol: float, is_put: bool,
    r: float = R_FLAT, q: float = Q_FLAT,
) -> float:
    """Signed delta: positive for calls, negative for puts (the Δ_signed of H)."""
    if t_years <= 0.0 or vol <= 0.0 or spot <= 0.0 or strike <= 0.0:
        itm = (spot < strike) if is_put else (spot > strike)
        return (-1.0 if is_put else 1.0) * (1.0 if itm else 0.0)
    sqrt_t = math.sqrt(t_years)
    d1 = (math.log(spot / strike) + (r - q + 0.5 * vol * vol) * t_years) / (vol * sqrt_t)
    disc = math.exp(-q * t_years)
    return disc * (_norm_cdf(d1) - 1.0) if is_put else disc * _norm_cdf(d1)


def implied_vol(
    price: float, spot: float, strike: float, t_years: float, is_put: bool,
    r: float = R_FLAT, q: float = Q_FLAT,
    lo: float = 1e-4, hi: float = 5.0, tol: float = 1e-7, max_iter: int = 200,
) -> float | None:
    """Bisected IV. None when the price is below the discounted intrinsic or
    above the arbitrage ceiling — never a silently clamped number."""
    if t_years <= 0.0 or spot <= 0.0 or strike <= 0.0 or price <= 0.0:
        return None
    if price < discounted_intrinsic(spot, strike, t_years, is_put, r, q) - 1e-12:
        return None
    ceiling = (strike * math.exp(-r * t_years)) if is_put else (spot * math.exp(-q * t_years))
    if price >= ceiling:
        return None
    f_lo = bs_price(spot, strike, t_years, lo, is_put, r, q) - price
    f_hi = bs_price(spot, strike, t_years, hi, is_put, r, q) - price
    if f_lo > 0.0 or f_hi < 0.0:
        return None
    for _ in range(max_iter):
        mid = 0.5 * (lo + hi)
        f_mid = bs_price(spot, strike, t_years, mid, is_put, r, q) - price
        if abs(f_mid) < tol or (hi - lo) < tol:
            return mid
        if f_mid < 0.0:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def option_tick(price: float) -> float:
    """OPRA penny-pilot tick. ASSUMPTION — see TICK_BREAK."""
    return TICK_SMALL if price < TICK_BREAK else TICK_LARGE


class DeltaFit(NamedTuple):
    ok: bool
    reason: str          # "" | "sub_intrinsic" | "no_iv" | "unstable"
    iv: float
    delta: float         # signed: + call, − put
    delta_swing: float   # max |Δ| move over ±1 tick


def fit_delta(
    quote_mid: float, spot: float, strike: float, t_years: float, is_put: bool,
    r: float = R_FLAT, q: float = Q_FLAT, stability_max: float = DELTA_STABILITY_MAX,
) -> DeltaFit:
    """IV bisected from the OPTION QUOTE MIDPOINT (never the trade price), then Δ.

    Aggressive prints push IV — and so |Δ|, and so D — up on buys and down on
    sells: a side-correlated error in the regressor AND a side-skewed selection.
    The quote mid is the registered input; the trade-price fit is a logged
    secondary the caller computes separately.

    Δ-stability is part of the fit, not a later filter: ±1 option tick moving
    |Δ| by more than `stability_max` means the inversion is ill-conditioned at
    this quote and the event is dropped + logged. A bump that falls through the
    discounted intrinsic counts as unstable for the same reason.
    """
    if quote_mid < discounted_intrinsic(spot, strike, t_years, is_put, r, q) - 1e-12:
        return DeltaFit(False, "sub_intrinsic", float("nan"), float("nan"), float("nan"))
    iv = implied_vol(quote_mid, spot, strike, t_years, is_put, r, q)
    if iv is None:
        return DeltaFit(False, "no_iv", float("nan"), float("nan"), float("nan"))
    delta = bs_delta(spot, strike, t_years, iv, is_put, r, q)
    tick = option_tick(quote_mid)
    swing = 0.0
    for bumped in (quote_mid - tick, quote_mid + tick):
        if bumped <= 0.0:
            return DeltaFit(False, "unstable", iv, delta, float("inf"))
        iv_b = implied_vol(bumped, spot, strike, t_years, is_put, r, q)
        if iv_b is None:
            return DeltaFit(False, "unstable", iv, delta, float("inf"))
        swing = max(swing, abs(abs(bs_delta(spot, strike, t_years, iv_b, is_put, r, q))
                               - abs(delta)))
    if swing > stability_max:
        return DeltaFit(False, "unstable", iv, delta, swing)
    return DeltaFit(True, "", iv, delta, swing)


def hedge_shares(side_sign: int, delta_signed: float, contracts: float) -> float:
    """H = (customer side sign) × Δ_signed × contracts × 100, in shares.

    The four worked examples the spec keeps as unit tests:
      buy call  → +1 × +Δ × N × 100 > 0   dealer BUYS
      buy put   → +1 × −Δ × N × 100 < 0   dealer SELLS
      sell call → −1 × +Δ × N × 100 < 0
      sell put  → −1 × −Δ × N × 100 > 0
    """
    return float(side_sign) * float(delta_signed) * float(contracts) * 100.0


# =============================================================================
# fences, filters, grouping — all pure
# =============================================================================

def session_midnight_ns(session: date) -> int:
    """ET midnight for a session as epoch-ns; the anchor every clock-time read
    uses, so DST is handled by zoneinfo rather than by an offset constant."""
    return int(datetime.combine(session, dtime(0, 0), tzinfo=_ET).timestamp() * 1e9)


def et_second_of_day(ts_ns: np.ndarray | int, midnight_ns: int) -> np.ndarray | int:
    return (np.asarray(ts_ns, dtype=np.int64) - midnight_ns) // 1_000_000_000


def in_trading_window(sec: np.ndarray | int) -> np.ndarray | bool:
    """T ∈ [09:35:00, 15:55:00] ET — every horizon sits inside continuous trading."""
    sec = np.asarray(sec)
    return (sec >= T_WINDOW_ET[0]) & (sec <= T_WINDOW_ET[1])


def in_release_fence(sec: np.ndarray | int) -> np.ndarray | bool:
    """[09:55,10:05] or [13:55,14:05] ET — 3.1's own burst physics would
    otherwise sit inside the primary tier."""
    sec = np.asarray(sec)
    out = np.zeros(sec.shape, dtype=bool)
    for lo, hi in RELEASE_FENCES_ET:
        out |= (sec >= lo) & (sec <= hi)
    return out


def single_leg_groups(
    gid: np.ndarray, n_groups: int, cond: np.ndarray, ml_heuristic: np.ndarray
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """The registered TRIPLE test, per order group.

    A group is single-leg iff (1) EVERY print in it carries a joined condition
    code (`cond != ''` — an unjoined print certifies nothing, and `'' ∉
    PACKAGE_CODES` would otherwise pass it), (2) no code is in PACKAGE_CODES,
    and (3) no print carries the corpus ns-collision heuristic flag.

    Returns (single_leg, has_unjoined, has_package) so the drop ledger can
    attribute each failure to its own clause.
    """
    n = np.bincount(gid, minlength=n_groups)
    joined = np.bincount(gid, weights=(cond != "").astype(np.float64), minlength=n_groups)
    has_unjoined = joined < n
    pkg = np.bincount(gid, weights=np.isin(cond, _PACKAGE_LIST).astype(np.float64),
                      minlength=n_groups) > 0
    heur = np.bincount(gid, weights=np.asarray(ml_heuristic, dtype=np.float64),
                       minlength=n_groups) > 0
    return (~has_unjoined) & (~pkg) & (~heur), has_unjoined, pkg | heur


def group_side(gid: np.ndarray, n_groups: int, side: np.ndarray) -> np.ndarray:
    """Unanimous `good_q` midpoint side per group: 0 buy, 1 sell, −1 unusable.

    `side` is the corpus alphabet (0 buy / 1 sell / 2 unclassified / 3 wide).
    Mixed, wide or unquoted anywhere in the group → −1 (dropped + logged).
    """
    n = np.bincount(gid, minlength=n_groups)
    n_buy = np.bincount(gid, weights=(side == 0).astype(np.float64), minlength=n_groups)
    n_sell = np.bincount(gid, weights=(side == 1).astype(np.float64), minlength=n_groups)
    out = np.full(n_groups, -1, dtype=np.int8)
    out[n_buy == n] = 0
    out[n_sell == n] = 1
    return out


def neighbour_premium(ts_sorted: np.ndarray, premium_sorted: np.ndarray,
                      window_ns: int = ISOLATION_WINDOW_NS) -> np.ndarray:
    """Sum of OTHER single-leg premium within ±window of each group (same name).

    Isolation is on ACTIVITY, not just on qualifying events: twenty $10k prints
    produce unattributable hedge flow while no single one would ever "qualify".
    """
    if not len(ts_sorted):
        return np.zeros(0, dtype=np.float64)
    cum = np.concatenate([[0.0], np.cumsum(premium_sorted, dtype=np.float64)])
    lo = np.searchsorted(ts_sorted, ts_sorted - window_ns, side="left")
    hi = np.searchsorted(ts_sorted, ts_sorted + window_ns, side="right")
    return cum[hi] - cum[lo] - premium_sorted


def expected_29s_volume(minute_volumes: np.ndarray) -> float:
    """Median same-clock-minute volume × (29.1/60).

    `ADV/780` may NOT appear anywhere: measured 2026-05-28 the open runs 15–40×
    ADV/780 (MRVL 09:30 = 39.9×), so a flat-day gate certifies "≥3× normal" on
    events that are really ~0.2× and turns a null verdict into blindness.
    """
    v = np.asarray(minute_volumes, dtype=np.float64)
    v = v[np.isfinite(v)]
    if not len(v):
        return float("nan")
    return float(np.median(v)) * (EXPECTED_WINDOW_S / 60.0)


def detectability(h_shares: float, expected: float) -> float:
    if not np.isfinite(expected) or expected <= 0.0:
        return float("nan")
    return abs(h_shares) / expected


# =============================================================================
# signing + the S / E / Z statistics — all pure
# =============================================================================

def prevailing_quote_idx(trade_t: np.ndarray, quote_t: np.ndarray) -> np.ndarray:
    """Index of the last quote at or before each trade; −1 where none exists."""
    if not len(quote_t):
        return np.full(len(trade_t), -1, dtype=np.int64)
    return np.searchsorted(quote_t, trade_t, side="right") - 1


def sign_midpoint(
    trade_t: np.ndarray, trade_px: np.ndarray,
    quote_t: np.ndarray, bid: np.ndarray, ask: np.ndarray,
) -> np.ndarray:
    """The corpus's own rule, on the equity tape: price vs the prevailing SIP
    NBBO midpoint. At-mid or no-quote → UNSIGNED (0), never guessed.

    At-mid is 13–22% of volume and is mechanism-aligned — dealers route hedges
    to midpoint dark precisely to avoid signalling — which is why Look C exists
    rather than a tick-rule patch.
    """
    idx = prevailing_quote_idx(trade_t, quote_t)
    out = np.zeros(len(trade_t), dtype=np.int8)
    ok = idx >= 0
    if not ok.any():
        return out
    b = np.asarray(bid, dtype=np.float64)[idx[ok]]
    a = np.asarray(ask, dtype=np.float64)[idx[ok]]
    usable = (b > 0.0) & (a > 0.0) & (a > b)
    mid = np.where(usable, (a + b) / 2.0, np.nan)
    px = np.asarray(trade_px, dtype=np.float64)[ok]
    s = np.zeros(int(ok.sum()), dtype=np.int8)
    with np.errstate(invalid="ignore"):
        s[usable & (px > mid)] = 1
        s[usable & (px < mid)] = -1
    out[ok] = s
    return out


def sign_tick_rule(trade_px: np.ndarray) -> np.ndarray:
    """Logged SECONDARY only. Zero until the first price change, then the sign
    of the last non-zero tick carried forward."""
    px = np.asarray(trade_px, dtype=np.float64)
    if not len(px):
        return np.zeros(0, dtype=np.int8)
    d = np.sign(np.diff(px, prepend=px[0])).astype(np.int8)
    src = np.where(d != 0, np.arange(len(d)), -1)
    src = np.maximum.accumulate(src)
    out = np.zeros(len(px), dtype=np.int8)
    hit = src >= 0
    out[hit] = d[src[hit]]
    return out


def window_sum(t_ns: np.ndarray, values: np.ndarray, lo_ns: int, hi_ns: int) -> float:
    """Σ values over [lo, hi). Half-open so pre and post never double-count."""
    t = np.asarray(t_ns, dtype=np.int64)
    m = (t >= lo_ns) & (t < hi_ns)
    return float(np.asarray(values, dtype=np.float64)[m].sum())


def statistic_s(t_ns: np.ndarray, signed_size: np.ndarray, t0_ns: int,
                horizon_ns: int) -> float:
    """S = signed vol(post) − signed vol(pre); post = [T, T+h), pre = [T−h, T)."""
    post = window_sum(t_ns, signed_size, t0_ns, t0_ns + horizon_ns)
    pre = window_sum(t_ns, signed_size, t0_ns - horizon_ns, t0_ns)
    return post - pre


class Excess(NamedTuple):
    e: float          # shares — the interpretable β lives here
    z: float          # E / MAD(controls) — PRIMARY for every test
    median: float
    mad: float


def excess(s_event: float, s_ctrl: np.ndarray) -> Excess:
    """E = S_event − median(S_ctrl); Z = E / MAD(S_ctrl), MAD of the 20.

    Z is primary because raw shares let one mega-cap set the curve. A degenerate
    control set (MAD = 0) yields a NaN Z rather than an infinity — the caller
    drops and counts it.
    """
    c = np.asarray(s_ctrl, dtype=np.float64)
    c = c[np.isfinite(c)]
    if not len(c):
        return Excess(float("nan"), float("nan"), float("nan"), float("nan"))
    med = float(np.median(c))
    mad = float(np.median(np.abs(c - med)))
    e = float(s_event) - med
    z = e / mad if mad > 0.0 else float("nan")
    return Excess(e, z, med, mad)


def hedge_aligned(z: np.ndarray, h_sign: np.ndarray) -> np.ndarray:
    """Ẽ = Z · sign(H) — the form that makes the direction-only alternative
    visible. A pooled slope of Ẽ on SIGNED H would pass on a pure direction
    effect; this one cannot, because a constant direction effect lands entirely
    in the intercept."""
    return np.asarray(z, dtype=np.float64) * np.asarray(h_sign, dtype=np.float64)


def theil_sen(x: np.ndarray, y: np.ndarray) -> tuple[float, float]:
    """(slope, intercept). The intercept is reported, never dropped: a large
    intercept with a flat slope IS the direction-only alternative."""
    x = np.asarray(x, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64)
    if len(x) < 3 or np.ptp(x) == 0.0:
        return float("nan"), float("nan")
    res = theilslopes(y, x)
    return float(res[0]), float(res[1])


def ols_two_term(y: np.ndarray, x1: np.ndarray, x2: np.ndarray) -> tuple[float, float, float]:
    """(intercept, a, b) for y ~ a·x1 + b·x2. Look A′'s discriminator."""
    design = np.column_stack([np.ones(len(y)), np.asarray(x1, dtype=np.float64),
                              np.asarray(x2, dtype=np.float64)])
    coef, *_ = np.linalg.lstsq(design, np.asarray(y, dtype=np.float64), rcond=None)
    return float(coef[0]), float(coef[1]), float(coef[2])


# =============================================================================
# inference — cluster permutation, cluster bootstrap, MDE by injection
# =============================================================================

def cluster_permutation_p(
    stat_fn: Callable[[np.ndarray], float],
    h_sign: np.ndarray,
    cluster_id: np.ndarray,
    n_perm: int = N_PERM,
    seed: int = SEED,
) -> tuple[float, float]:
    """One-sided cluster-permutation p: flip sign(H) for a whole (underlying ×
    session) cluster TOGETHER, recompute.

    The 50/50 binomial is BANNED here — 6.4 effective independent names is this
    repo's own measurement, and customers buy more than they sell, so H-signs
    are not balanced under the null. Returns (observed, p).
    """
    h_sign = np.asarray(h_sign, dtype=np.float64)
    _, inv = np.unique(np.asarray(cluster_id), return_inverse=True)
    n_clusters = int(inv.max()) + 1 if len(inv) else 0
    observed = float(stat_fn(h_sign))
    if not np.isfinite(observed) or n_clusters == 0:
        return observed, float("nan")
    rng = np.random.default_rng(seed)
    hits = 0
    for _ in range(n_perm):
        flips = rng.integers(0, 2, n_clusters) * 2 - 1
        val = stat_fn(h_sign * flips[inv])
        if np.isfinite(val) and val >= observed:
            hits += 1
    return observed, (hits + 1) / (n_perm + 1)


def cluster_bootstrap(
    stat_fn: Callable[[np.ndarray], float],
    cluster_id: np.ndarray,
    n_boot: int = N_BOOT,
    seed: int = SEED,
) -> np.ndarray:
    """Resample UNDERLYINGS with replacement; `stat_fn` takes the row indices."""
    keys, inv = np.unique(np.asarray(cluster_id), return_inverse=True)
    members = [np.flatnonzero(inv == i) for i in range(len(keys))]
    rng = np.random.default_rng(seed)
    out = np.empty(n_boot, dtype=np.float64)
    for b in range(n_boot):
        pick = rng.integers(0, len(members), len(members))
        idx = np.concatenate([members[p] for p in pick])
        out[b] = stat_fn(idx)
    return out


def _look_a_slope(h_abs: np.ndarray, z: np.ndarray, h_sign: np.ndarray) -> float:
    return theil_sen(h_abs, hedge_aligned(z, h_sign))[0]


def mde_by_injection(
    h_abs: np.ndarray,
    h_sign: np.ndarray,
    cluster_id: np.ndarray,
    s_ctrl: np.ndarray,
    betas: tuple[float, ...] = (0.05, 0.1, 0.15, 0.2, 0.25, 0.3, 0.4, 0.5),
    n_rep: int = 100,
    n_perm: int = 400,
    power: float = 0.80,
    alpha: float = PERM_ALPHA,
    seed: int = SEED,
) -> dict:
    """MDE for Look A at 80% power, by injecting β·|H| into CONTROL windows.

    Consumes no event outcomes: every replication builds a pseudo-event out of
    one control window (leave-one-out for that window's own median/MAD), adds
    the synthetic hedge β·|H|·sign(H), and runs the registered cluster
    permutation on the pseudo-set. MDE > 0.3 makes any null
    NO-VERDICT-UNDERPOWERED, never ABSENT.
    """
    h_abs = np.asarray(h_abs, dtype=np.float64)
    h_sign = np.asarray(h_sign, dtype=np.float64)
    s_ctrl = np.asarray(s_ctrl, dtype=np.float64)
    n, n_ctrl = s_ctrl.shape
    rng = np.random.default_rng(seed)
    out: dict[str, float] = {}
    mde = float("inf")
    for beta in betas:
        rejects = 0
        for _ in range(n_rep):
            pick = rng.integers(0, n_ctrl, n)
            z = np.empty(n, dtype=np.float64)
            for i in range(n):
                rest = np.delete(s_ctrl[i], pick[i])
                pseudo = s_ctrl[i, pick[i]] + beta * h_abs[i] * h_sign[i]
                z[i] = excess(pseudo, rest).z
            ok = np.isfinite(z)
            if ok.sum() < 3:
                continue
            _, p = cluster_permutation_p(
                lambda s, _h=h_abs[ok], _z=z[ok]: _look_a_slope(_h, _z, s),
                h_sign[ok], np.asarray(cluster_id)[ok], n_perm=n_perm,
                seed=int(rng.integers(1, 2**31 - 1)),
            )
            if np.isfinite(p) and p < alpha:
                rejects += 1
        pw = rejects / max(n_rep, 1)
        out[f"{beta:g}"] = round(pw, 4)
        if pw >= power and beta < mde:
            mde = beta
    return {"power_by_beta": out, "mde": None if math.isinf(mde) else mde,
            "n_rep": n_rep, "n_perm": n_perm, "power_target": power}


# =============================================================================
# the registered verdict table
# =============================================================================

def verdict(
    look_a: dict, look_a_prime: dict, look_b: dict, look_c: dict,
    direction_p: float, n_events: int, n_underlyings: int, mde: float | None,
    all_horizon_failures: bool,
) -> dict:
    """The complete table, on the quiet tier / equity stratum / non-TRF / Z /
    16g primary, with floors n ≥ 100 events, ≥ 25 underlyings, MDE ≤ 0.3."""
    floors = {
        "n_events": n_events, "n_events_ok": n_events >= FLOOR_EVENTS,
        "n_underlyings": n_underlyings,
        "n_underlyings_ok": n_underlyings >= FLOOR_UNDERLYINGS,
        "mde": mde, "mde_ok": mde is not None and mde <= FLOOR_MDE,
    }
    floors_met = floors["n_events_ok"] and floors["n_underlyings_ok"] and floors["mde_ok"]
    if not floors_met:
        name = ("NO-VERDICT-UNDERPOWERED" if not floors["mde_ok"] else "NO-VERDICT")
        return {"verdict": name, "floors": floors, "floors_met": False}

    a_pass = bool(look_a.get("pass"))
    b_pass = bool(look_b.get("pass"))
    dir_pass = bool(np.isfinite(direction_p) and direction_p < PERM_ALPHA)
    if a_pass and b_pass and dir_pass:
        name = "FOOTPRINT-CONFIRMED"
    elif all_horizon_failures:
        name = "FOOTPRINT-ABSENT"
    else:
        name = "FOOTPRINT-AMBIGUOUS"
    return {
        "verdict": name, "floors": floors, "floors_met": True,
        "look_a_pass": a_pass, "look_b_pass": b_pass,
        "look_a_prime_pass": look_a_prime.get("pass"),
        "look_c_pass": look_c.get("pass"),
        "direction_permutation_p": direction_p,
    }


# =============================================================================
# IO — atomic artifacts
# =============================================================================

def save_npz(path: Path, **arrays) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".part")
    # file handle, not a bare path: np.savez appends ".npz" to a path and the
    # subsequent tmp.replace() then moves a file that was never written.
    with open(tmp, "wb") as fh:
        np.savez_compressed(fh, **arrays)
    tmp.replace(path)


def save_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".part")
    tmp.write_text(json.dumps(obj, indent=1, default=str))
    tmp.replace(path)


class PageCapError(RuntimeError):
    """Pagination hit the cap — silent truncation stales every prevailing quote
    after it, so this RAISES rather than warning (3.1's fetcher warned)."""


class FeedAssertionError(RuntimeError):
    """A window's volume is too small for the SIP daily bar — an IEX fallback
    understates ~20× and would manufacture FOOTPRINT-ABSENT."""


def _paged_get(client, url: str, params: dict, key: str,
               max_pages: int = MAX_PAGES) -> list[dict]:
    rows: list[dict] = []
    token: str | None = None
    for _ in range(max_pages):
        q = dict(params)
        if token:
            q["page_token"] = token
        resp = client.get(url, params=q)
        if resp.status_code != 200:
            raise RuntimeError(f"GET {url} failed ({resp.status_code}): {resp.text[:200]}")
        payload = resp.json()
        rows.extend(payload.get(key) or [])
        token = payload.get("next_page_token")
        if not token:
            return rows
    raise PageCapError(f"{url}: pagination hit the {max_pages}-page cap — TRUNCATED")


def _rfc3339(ts_ns: int) -> str:
    return f"{np.datetime64(int(ts_ns), 'ns')}Z"


def fetch_trades(client, data_base_url: str, symbol: str, lo_ns: int,
                 hi_ns: int) -> dict[str, np.ndarray]:
    """One [lo, hi) trade window. Keeps price/size/exchange/condition/tape —
    the condition codes and the tape are what the per-tape exclusion table and
    the TRF split are built from."""
    rows = _paged_get(
        client, f"{data_base_url}/v2/stocks/{symbol}/trades",
        {"start": _rfc3339(lo_ns), "end": _rfc3339(hi_ns), "limit": PAGE_LIMIT,
         "sort": "asc", "feed": FEED}, "trades")
    n = len(rows)
    out = {
        "t_ns": np.empty(n, dtype=np.int64),
        "price": np.empty(n, dtype=np.float64),
        "size": np.empty(n, dtype=np.int64),
        "exchange": np.empty(n, dtype="U2"),
        "cond": np.empty(n, dtype="U8"),
        "tape": np.empty(n, dtype="U1"),
    }
    for i, tr in enumerate(rows):
        out["t_ns"][i] = parse_t_ns(tr["t"])
        out["price"][i] = float(tr["p"])
        out["size"][i] = int(tr["s"])
        out["exchange"][i] = str(tr.get("x") or "")
        out["cond"][i] = "".join(str(c) for c in (tr.get("c") or []))[:8]
        out["tape"][i] = str(tr.get("z") or "")
    order = np.argsort(out["t_ns"], kind="stable")
    return {k: v[order] for k, v in out.items()}


def fetch_quotes(client, data_base_url: str, symbol: str, lo_ns: int,
                 hi_ns: int) -> dict[str, np.ndarray]:
    rows = _paged_get(
        client, f"{data_base_url}/v2/stocks/{symbol}/quotes",
        {"start": _rfc3339(lo_ns), "end": _rfc3339(hi_ns), "limit": PAGE_LIMIT,
         "sort": "asc", "feed": FEED}, "quotes")
    n = len(rows)
    out = {
        "t_ns": np.empty(n, dtype=np.int64),
        "bid": np.empty(n, dtype=np.float64),
        "ask": np.empty(n, dtype=np.float64),
        "tape": np.empty(n, dtype="U1"),
    }
    for i, qt in enumerate(rows):
        out["t_ns"][i] = parse_t_ns(qt["t"])
        out["bid"][i] = float(qt.get("bp") or 0.0)
        out["ask"][i] = float(qt.get("ap") or 0.0)
        out["tape"][i] = str(qt.get("z") or "")
    order = np.argsort(out["t_ns"], kind="stable")
    return {k: v[order] for k, v in out.items()}


def fetch_minute_bars(client, data_base_url: str, symbol: str, start: date,
                      end: date) -> list[dict]:
    return _paged_get(
        client, f"{data_base_url}/v2/stocks/{symbol}/bars",
        {"start": start.isoformat(), "end": end.isoformat(), "timeframe": "1Min",
         "limit": PAGE_LIMIT, "adjustment": "raw", "feed": FEED}, "bars")


def fetch_daily_volume(client, data_base_url: str, symbol: str, day: date) -> float:
    bars = _paged_get(
        client, f"{data_base_url}/v2/stocks/{symbol}/bars",
        {"start": day.isoformat(), "end": day.isoformat(), "timeframe": "1Day",
         "limit": 10, "adjustment": "raw", "feed": FEED}, "bars")
    return float(bars[0]["v"]) if bars else 0.0


def fetch_news(client, data_base_url: str, symbol: str, lo_ns: int,
               hi_ns: int) -> list[dict]:
    return _paged_get(
        client, f"{data_base_url}/v1beta1/news",
        {"symbols": symbol, "start": _rfc3339(lo_ns), "end": _rfc3339(hi_ns),
         "limit": 50, "sort": "asc"}, "news")


def assert_window_volume(window_shares: float, day_shares: float, window_s: float,
                         label: str, min_frac: float = FEED_ASSERT_MIN_FRAC) -> float:
    """Guard the SIP feed. Returns the measured pro-rata ratio; raises below the
    floor, because a silently downgraded feed manufactures FOOTPRINT-ABSENT."""
    if day_shares <= 0.0:
        raise FeedAssertionError(f"{label}: no SIP daily bar volume to assert against")
    prorata = day_shares * (window_s / RTH_SECONDS)
    ratio = window_shares / prorata if prorata > 0 else 0.0
    if ratio < min_frac:
        raise FeedAssertionError(
            f"{label}: window volume {window_shares:,.0f} is {ratio:.3f}× its pro-rata "
            f"share of the SIP daily bar ({day_shares:,.0f}) — feed downgrade suspected")
    return ratio


# =============================================================================
# session pool
# =============================================================================

def qualifying_sessions(
    tcbbo_dir: Path = TCBBO_DIR, labels_dir: Path = LABELS_DIR,
    start: date = TRAIN_START, end: date = TRAIN_END,
) -> tuple[list[date], dict]:
    """Training-window sessions with CODE provenance, half-days excluded.

    Code provenance = a completed Alpaca condition capture AND join_rate_dbn ≥
    0.98 (Addendum E). 107 of 212 training sessions qualify — disclosed, the
    pool halves. Half-days are out for events AND controls. The per-session pass
    re-verifies the join rate from the join itself, so a stale ledger cannot let
    a below-floor session in.
    """
    ledger_path = labels_dir / "join_ledger.json"
    ledger = json.loads(ledger_path.read_text()) if ledger_path.exists() else {}
    stats = {"on_disk": 0, "in_window": 0, "half_day": 0, "no_capture": 0,
             "below_join_floor": 0, "qualified": 0}
    out: list[date] = []
    for path in sorted(tcbbo_dir.glob("*.dbn.zst")):
        stats["on_disk"] += 1
        # A market holiday leaves an 87-byte file; a size filter, not a calendar,
        # so it cannot drift out of date.
        if path.stat().st_size < 1_000_000:
            continue
        session = date.fromisoformat(path.name.split(".")[0])
        if session < start or session > end:
            continue
        stats["in_window"] += 1
        if is_early_close(session):
            stats["half_day"] += 1
            continue
        if not capture_complete(session, labels_dir):
            stats["no_capture"] += 1
            continue
        row = ledger.get(session.isoformat())
        if row is not None and float(row.get("join_rate_dbn", 0.0)) < JOIN_FLOOR:
            stats["below_join_floor"] += 1
            continue
        out.append(session)
    stats["qualified"] = len(out)
    return out, stats


def adv_before_date(session: date, lookback: int = ADV_LOOKBACK,
                    db_path: Path = DEFAULT_DB_PATH) -> dict[str, float]:
    """Mean daily share volume over the `lookback` sessions strictly before."""
    with connect(db_path) as conn:
        return {
            r["symbol"]: float(r["adv"])
            for r in conn.execute(
                "SELECT symbol, AVG(v) AS adv FROM ("
                "  SELECT symbol, v, ROW_NUMBER() OVER ("
                "    PARTITION BY symbol ORDER BY date DESC) rn"
                "  FROM equity_bars WHERE date < ? AND adjustment = 'split' AND v IS NOT NULL"
                f") WHERE rn <= {int(lookback)} GROUP BY symbol",
                (session.isoformat(),),
            )
        }


class OptionDefs(NamedTuple):
    ids: np.ndarray          # sorted instrument_id
    und_code: np.ndarray     # index into `roots`
    roots: np.ndarray        # printed OPRA underlying roots
    is_put: np.ndarray
    strike: np.ndarray
    exp_days: np.ndarray     # days since epoch (UTC), the rollup's own convention


def load_option_defs(session: date, defs_dir: Path = DEFS_DIR) -> OptionDefs | None:
    """Contract definitions for one session.

    The rollup's `_load_definitions` returns an integer DTE; 3.2 needs the
    expiry DATE so time-to-expiry can be taken to 16:00 ET in SECONDS.
    """
    import databento as dbn

    path = defs_dir / f"{session.isoformat()}.dbn.zst"
    if not path.exists():
        return None
    arr = dbn.DBNStore.from_file(path).to_ndarray()
    cls = arr["instrument_class"]
    arr = arr[(cls == b"C") | (cls == b"P")]
    if not len(arr):
        return None
    ids = arr["instrument_id"].astype(np.int64)
    order = np.argsort(ids, kind="stable")
    ids, arr = ids[order], arr[order]
    roots, und_code = np.unique(np.char.strip(arr["underlying"].astype("U16")),
                                return_inverse=True)
    return OptionDefs(
        ids, und_code, roots,
        arr["instrument_class"] == b"P",
        arr["strike_price"].astype(np.float64) / PX,
        arr["expiration"].astype(np.int64) // 86_400_000_000_000,
    )


def expiry_ns(exp_days: int) -> int:
    """Expiry at 16:00 ET on the expiration date, as epoch-ns."""
    d = date(1970, 1, 1) + timedelta(days=int(exp_days))
    return session_midnight_ns(d) + 16 * 3600 * 1_000_000_000


def time_to_expiry_years(ts_event_ns: int, exp_days: int) -> float:
    return (expiry_ns(exp_days) - int(ts_event_ns)) / 1e9 / YEAR_SECONDS


# =============================================================================
# stage 1 — select
# =============================================================================

_LEDGER_KEYS = (
    "groups", "drop_not_single_leg_unjoined", "drop_not_single_leg_package_or_heuristic",
    "drop_universe_unresolved", "drop_universe_class", "drop_side_not_unanimous",
    "drop_premium", "drop_dte", "drop_time_window", "drop_release_fence",
    "drop_no_prior_close", "drop_price_min", "drop_adv", "drop_no_option_quote",
    "drop_iv_sub_intrinsic", "drop_iv_no_solution", "drop_delta_band",
    "drop_delta_unstable", "drop_isolation", "kept_primary_band", "kept_secondary_band",
)


def _empty_ledger() -> dict[str, int]:
    return dict.fromkeys(_LEDGER_KEYS, 0)


def select_session(session: date, rc, stratum: str, out_dir: Path,
                   db_path: Path = DEFAULT_DB_PATH) -> dict | None:
    """Stage 1 over one session, ON DISK ONLY. Writes the npz then the ledger
    JSON (the completion marker), so a torn write can never look done."""
    npz_path = out_dir / "select" / f"{session.isoformat()}.npz"
    led_path = out_dir / "select" / f"{session.isoformat()}.json"
    if led_path.exists() and npz_path.exists():
        return json.loads(led_path.read_text())

    defs = load_option_defs(session)
    if defs is None:
        logger.warning("%s: no definitions — skipped", session)
        return None
    tpath = TCBBO_DIR / f"{session.isoformat()}.dbn.zst"
    if not tpath.exists():
        return None

    import databento as dbn

    arr = dbn.DBNStore.from_file(tpath).to_ndarray()
    arr = arr[arr["size"] != UNDEF_SIZE]
    pid = arr["instrument_id"].astype(np.int64)
    idx = np.clip(np.searchsorted(defs.ids, pid), 0, len(defs.ids) - 1)
    hit = defs.ids[idx] == pid
    arr, idx = arr[hit], idx[hit]
    if not len(arr):
        logger.warning("%s: all prints failed the definitions join", session)
        return None

    fx = fence_and_classify(arr)
    ts_ev = arr["ts_event"].astype(np.int64)
    iid = arr["instrument_id"].astype(np.int64)
    _, cond, ml_source, join_rate, _cov = multileg_flags(
        session, ts_ev, iid, fx.price, arr["size"].astype(np.int64))
    if ml_source != "codes" or join_rate is None or join_rate < JOIN_FLOOR:
        logger.warning("%s: join_rate=%s source=%s — below the code-provenance "
                       "floor, session skipped", session, join_rate, ml_source)
        return None
    ml_heur = _multileg(ts_ev, iid)

    gid, n_groups = _order_groups(ts_ev, iid)
    led = _empty_ledger()
    led["groups"] = n_groups

    # --- group-grain aggregates ------------------------------------------
    # Every print in a group shares its (ts_event, instrument_id) by definition,
    # so ANY member indexes the group's timestamp and contract.
    first = np.zeros(n_groups, dtype=np.int64)
    first[gid] = np.arange(len(gid), dtype=np.int64)
    g_ts = ts_ev[first]
    g_def = idx[first]
    g_contracts = np.bincount(gid, weights=fx.size, minlength=n_groups)
    g_premium = np.bincount(gid, weights=fx.premium, minlength=n_groups)

    single, has_unjoined, has_pkg = single_leg_groups(gid, n_groups, cond, ml_heur)
    led["drop_not_single_leg_unjoined"] = int(has_unjoined.sum())
    led["drop_not_single_leg_package_or_heuristic"] = int((~has_unjoined & has_pkg).sum())

    # --- universe ---------------------------------------------------------
    ds = session.isoformat()
    roots = [str(s) for s in defs.roots]
    cls = np.array([rc.classify(r, ds) for r in roots])
    ident = np.array([rc.resolve(r, ds) for r in roots])
    unresolved = cls == "unclassified"
    in_class = cls == stratum
    g_root = defs.und_code[g_def]
    keep = single & in_class[g_root]
    led["drop_universe_unresolved"] = int((single & unresolved[g_root]).sum())
    led["drop_universe_class"] = int((single & ~in_class[g_root] & ~unresolved[g_root]).sum())

    # --- isolation base: every single-leg group in the name, any size ------
    iso_ts = g_ts[keep]
    iso_prem = g_premium[keep]
    iso_root = g_root[keep]
    iso_ident = ident[iso_root]

    # --- the registered filters, in the registered order -------------------
    sides = group_side(gid, n_groups, fx.side)
    keep &= sides >= 0
    led["drop_side_not_unanimous"] = int((single & in_class[g_root] & (sides < 0)).sum())

    def _drop(mask: np.ndarray, key: str) -> np.ndarray:
        nonlocal keep
        led[key] = int((keep & ~mask).sum())
        keep = keep & mask
        return keep

    _drop(g_premium >= PREMIUM_MIN, "drop_premium")
    dte = defs.exp_days[g_def] - (session - date(1970, 1, 1)).days
    _drop(dte >= DTE_MIN, "drop_dte")
    midnight = session_midnight_ns(session)
    sec = et_second_of_day(g_ts, midnight)
    _drop(in_trading_window(sec), "drop_time_window")
    _drop(~in_release_fence(sec), "drop_release_fence")

    closes = bars_before_date(ds, db_path=db_path)
    adv_map = adv_before_date(session, db_path=db_path)
    prior_close = np.array([closes.get(str(s), (np.nan, ""))[0] for s in ident],
                           dtype=np.float64)[g_root]
    adv = np.array([adv_map.get(str(s), np.nan) for s in ident], dtype=np.float64)[g_root]
    _drop(np.isfinite(prior_close), "drop_no_prior_close")
    _drop(prior_close >= PRICE_MIN, "drop_price_min")
    _drop(np.isfinite(adv) & (adv >= ADV_MIN), "drop_adv")

    # option quote midpoint on the print (the tcbbo BBO is pre-trade)
    good = fx.good_q
    bid = arr["bid_px_00"].astype(np.float64) / PX
    ask = arr["ask_px_00"].astype(np.float64) / PX
    mid = np.where(good, (bid + ask) / 2.0, np.nan)
    q_n = np.bincount(gid, weights=good.astype(np.float64), minlength=n_groups)
    q_sum = np.bincount(gid, weights=np.where(good, mid, 0.0), minlength=n_groups)
    with np.errstate(invalid="ignore", divide="ignore"):
        g_mid = np.where(q_n > 0, q_sum / np.maximum(q_n, 1.0), np.nan)
    _drop(np.isfinite(g_mid) & (g_mid > 0.0), "drop_no_option_quote")

    # --- Δ_select: spot = the PRIOR close (the snapshot spot cannot gate
    #     selection — that would be circular) --------------------------------
    rows = np.flatnonzero(keep)
    band_lo, band_hi = DELTA_BAND
    wide_lo, wide_hi = DELTA_BAND_SECONDARY
    recs: list[dict] = []
    for i in rows:
        t_years = time_to_expiry_years(int(g_ts[i]), int(defs.exp_days[g_def[i]]))
        if t_years <= 0.0:
            led["drop_iv_no_solution"] += 1
            continue
        fit = fit_delta(float(g_mid[i]), float(prior_close[i]),
                        float(defs.strike[g_def[i]]), t_years, bool(defs.is_put[g_def[i]]))
        if not fit.ok:
            led[{"sub_intrinsic": "drop_iv_sub_intrinsic",
                 "no_iv": "drop_iv_no_solution",
                 "unstable": "drop_delta_unstable"}[fit.reason]] += 1
            continue
        a = abs(fit.delta)
        if not (wide_lo <= a <= wide_hi):
            led["drop_delta_band"] += 1
            continue
        side_sign = 1 if sides[i] == 0 else -1
        h = hedge_shares(side_sign, fit.delta, g_contracts[i])
        recs.append({
            "session": ds, "ts_ns": int(g_ts[i]),
            "instrument_id": int(iid[first[i]]),
            "root": str(defs.roots[g_root[i]]), "symbol": str(ident[g_root[i]]),
            "stratum": stratum,
            "is_put": bool(defs.is_put[g_def[i]]),
            "strike": float(defs.strike[g_def[i]]),
            "exp_days": int(defs.exp_days[g_def[i]]),
            "dte": int(dte[i]), "contracts": int(g_contracts[i]),
            "premium": float(g_premium[i]), "side_sign": side_sign,
            "option_quote_mid": float(g_mid[i]),
            "prior_close": float(prior_close[i]), "adv": float(adv[i]),
            "iv_select": float(fit.iv), "delta_select": float(fit.delta),
            "delta_swing": float(fit.delta_swing),
            "h_select": float(h), "abs_h_select": abs(float(h)),
            "band": "primary" if band_lo <= a <= band_hi else "secondary",
        })

    # --- isolation on ACTIVITY (last registered filter) ---------------------
    kept: list[dict] = []
    by_name: dict[str, tuple[np.ndarray, np.ndarray]] = {}
    for name in {r["symbol"] for r in recs}:
        sel = iso_ident == name
        order = np.argsort(iso_ts[sel], kind="stable")
        by_name[name] = (iso_ts[sel][order], iso_prem[sel][order])
    for r in recs:
        ts_all, prem_all = by_name[r["symbol"]]
        pos = np.searchsorted(ts_all, r["ts_ns"])
        lo = np.searchsorted(ts_all, r["ts_ns"] - ISOLATION_WINDOW_NS, side="left")
        hi = np.searchsorted(ts_all, r["ts_ns"] + ISOLATION_WINDOW_NS, side="right")
        others = float(prem_all[lo:hi].sum())
        if pos < len(ts_all) and ts_all[pos] == r["ts_ns"]:
            others -= r["premium"]
        r["neighbour_premium"] = max(others, 0.0)
        if r["neighbour_premium"] > ISOLATION_MAX_FRAC * r["premium"]:
            led["drop_isolation"] += 1
            continue
        kept.append(r)

    led["kept_primary_band"] = sum(1 for r in kept if r["band"] == "primary")
    led["kept_secondary_band"] = sum(1 for r in kept if r["band"] == "secondary")

    # --- persist: the ≥$25k activity index the controls need, then the
    #     candidates, then the ledger LAST (the completion marker) -----------
    act = single & (g_premium >= PREMIUM_MIN)
    save_npz(
        npz_path,
        activity_ts=g_ts[act].astype(np.int64),
        activity_premium=g_premium[act].astype(np.float64),
        activity_symbol=ident[g_root[act]].astype("U16"),
    )
    save_json(out_dir / "select" / f"{session.isoformat()}_candidates.json", kept)
    led["join_rate_dbn"] = float(join_rate)
    save_json(led_path, led)
    logger.info("%s kept=%s (primary %s / secondary %s) of %s groups",
                session, len(kept), led["kept_primary_band"],
                led["kept_secondary_band"], f"{n_groups:,}")
    return led


def stage_select(out_dir: Path, stratum: str, sessions: list[date] | None = None) -> dict:
    rc = load_classification(ROOT_CLASSIFICATION)
    pool, pool_stats = qualifying_sessions()
    if sessions:
        pool = [s for s in pool if s in set(sessions)]
    total = _empty_ledger()
    for session in pool:
        led = select_session(session, rc, stratum, out_dir)
        if led is None:
            continue
        for k in _LEDGER_KEYS:
            total[k] += int(led.get(k, 0))

    cands: list[dict] = []
    for session in pool:
        path = out_dir / "select" / f"{session.isoformat()}_candidates.json"
        if path.exists():
            cands.extend(json.loads(path.read_text()))
    primary = sorted((c for c in cands if c["band"] == "primary"),
                     key=lambda c: -c["abs_h_select"])[:STAGE1_TOP]
    secondary = sorted((c for c in cands if c["band"] == "secondary"),
                       key=lambda c: -c["abs_h_select"])[:STAGE1_TOP]
    doc = {
        "spec": SPEC, "stage": "select", "stratum": stratum,
        "sessions": {"pool": [s.isoformat() for s in pool], **pool_stats},
        "drop_ledger": total,
        "candidates_total": len(cands),
        "candidates": primary + secondary,
        "note": (
            "Ranked by |H_select|, top 1,000 per registered band. The secondary "
            "|Δ|∈[0.05,0.95] list is carried so the registered secondary read is "
            "computable; the PRIMARY verdict uses the primary band only."
        ),
    }
    save_json(out_dir / f"stage1_candidates_{stratum}.json", doc)
    logger.info("stage 1: %s sessions, %s candidates, top %s primary / %s secondary",
                len(pool), len(cands), len(primary), len(secondary))
    return doc


# =============================================================================
# stage 2 — detectability + news fence
# =============================================================================

def minute_profile(bars: list[dict]) -> dict[int, list[float]]:
    """ET minute-of-day → volumes across the fetched sessions."""
    out: dict[int, list[float]] = {}
    for b in bars:
        ts = parse_t_ns(b["t"])
        dt = datetime.fromtimestamp(ts / 1e9, tz=_ET)
        out.setdefault(dt.hour * 60 + dt.minute, []).append(float(b["v"]))
    return out


def stage_bars(out_dir: Path, stratum: str, client, data_base_url: str,
               limit: int | None = None) -> dict:
    doc = json.loads((out_dir / f"stage1_candidates_{stratum}.json").read_text())
    cands = doc["candidates"][:limit] if limit else doc["candidates"]
    led = {"candidates": len(cands), "drop_no_bars": 0, "drop_zero_expected": 0,
           "drop_detectability": 0, "drop_news": 0, "drop_underlying_cap": 0,
           "drop_beyond_top": 0}
    scored: list[dict] = []
    for c in cands:
        session = date.fromisoformat(c["session"])
        cache = out_dir / "bars" / f"{c['symbol']}_{session.isoformat()}.json"
        if cache.exists():
            profile = {int(k): v for k, v in json.loads(cache.read_text()).items()}
        else:
            bars = fetch_minute_bars(client, data_base_url, c["symbol"],
                                     session - timedelta(days=40), session - timedelta(days=1))
            profile = minute_profile(bars)
            save_json(cache, {str(k): v for k, v in profile.items()})
        minute = datetime.fromtimestamp(c["ts_ns"] / 1e9, tz=_ET)
        vols = profile.get(minute.hour * 60 + minute.minute, [])
        # prior TWENTY sessions of the same clock minute
        vols = vols[-ADV_LOOKBACK:]
        if not vols:
            led["drop_no_bars"] += 1
            continue
        expected = expected_29s_volume(np.array(vols, dtype=np.float64))
        d = detectability(c["abs_h_select"], expected)
        if not np.isfinite(d):
            led["drop_zero_expected"] += 1
            continue
        if d < D_MIN:
            led["drop_detectability"] += 1
            continue
        news_cache = out_dir / "news" / f"{c['symbol']}_{c['ts_ns']}.json"
        if news_cache.exists():
            stories = json.loads(news_cache.read_text())
        else:
            stories = fetch_news(client, data_base_url, c["symbol"],
                                 c["ts_ns"] - NEWS_FENCE_S * 1_000_000_000, c["ts_ns"])
            save_json(news_cache, stories)
        if stories:
            led["drop_news"] += 1
            continue
        scored.append({**c, "expected_29s": expected, "d": d,
                       "minute_samples": len(vols)})

    selected: list[dict] = []
    for band, cap in (("primary", STAGE2_TOP), ("secondary", STAGE2_TOP)):
        pool = sorted((s for s in scored if s["band"] == band), key=lambda s: -s["d"])
        per_name: dict[str, int] = {}
        picked: list[dict] = []
        for s in pool:
            if per_name.get(s["symbol"], 0) >= PER_UNDERLYING_CAP:
                led["drop_underlying_cap"] += 1
                continue
            if len(picked) >= cap:
                led["drop_beyond_top"] += 1
                continue
            per_name[s["symbol"]] = per_name.get(s["symbol"], 0) + 1
            picked.append(s)
        selected.extend(picked)

    out = {"spec": SPEC, "stage": "bars", "stratum": stratum, "ledger": led,
           "selected": selected,
           "n_primary": sum(1 for s in selected if s["band"] == "primary"),
           "n_secondary": sum(1 for s in selected if s["band"] == "secondary")}
    save_json(out_dir / f"stage2_selected_{stratum}.json", out)
    logger.info("stage 2: %s scored, %s selected (%s primary)", len(scored),
                len(selected), out["n_primary"])
    return out


# =============================================================================
# stage 3 — snapshots for events and controls
# =============================================================================

def load_activity(out_dir: Path, session: date) -> tuple[np.ndarray, np.ndarray] | None:
    npz_path = out_dir / "select" / f"{session.isoformat()}.npz"
    if not npz_path.exists():
        return None
    with np.load(npz_path, allow_pickle=False) as z:
        return z["activity_symbol"], z["activity_ts"]


def control_sessions(
    event_session: date, symbol: str, event_ts_ns: int, pool: list[date],
    activity: Callable[[date], tuple[np.ndarray, np.ndarray] | None],
    n: int = N_CONTROLS,
) -> list[date]:
    """20 nearest qualifying sessions, SYMMETRIC (10 before / 10 after, filled
    from the available side), skipping any session where the name has ≥$25k
    single-leg activity within ±300s of that clock time.

    The skip runs on the PRE-D filter set, not on the selected 300: near-misses
    contaminate the baseline exactly as much as a selected event would. Half-days
    and release-fence windows never enter the pool at all.
    """
    ev_sec = et_second_of_day(event_ts_ns, session_midnight_ns(event_session))
    if in_release_fence(ev_sec):
        return []
    before = [s for s in pool if s < event_session][::-1]
    after = [s for s in pool if s > event_session]

    def _ok(sess: date) -> bool:
        act = activity(sess)
        if act is None:
            return False
        syms, ts = act
        same = syms == symbol
        if not same.any():
            return True
        t0 = session_midnight_ns(sess) + int(event_ts_ns - session_midnight_ns(event_session))
        near = np.abs(ts[same] - t0) <= SNAPSHOT_S * 1_000_000_000
        return not bool(near.any())

    def _take(cands: list[date], want: int) -> list[date]:
        out: list[date] = []
        for s in cands:
            if len(out) >= want:
                break
            if _ok(s):
                out.append(s)
        return out

    half = n // 2
    pre = _take(before, half)
    post = _take(after, n - len(pre))
    if len(pre) + len(post) < n:
        pre = _take(before, n - len(post))
    return sorted(pre + post)


def _snap_path(out_dir: Path, symbol: str, ts_ns: int, kind: str) -> Path:
    return out_dir / "snap" / symbol / f"{ts_ns}_{kind}.npz"


def fetch_window(out_dir: Path, client, data_base_url: str, symbol: str, ts_ns: int,
                 daily: dict[str, float]) -> None:
    """One [T−300s, T+300s] trades+quotes snapshot, with the SIP feed assertion."""
    t_path = _snap_path(out_dir, symbol, ts_ns, "trades")
    q_path = _snap_path(out_dir, symbol, ts_ns, "quotes")
    if t_path.exists() and q_path.exists():
        return
    lo = ts_ns - SNAPSHOT_S * 1_000_000_000
    hi = ts_ns + SNAPSHOT_S * 1_000_000_000
    trades = fetch_trades(client, data_base_url, symbol, lo, hi)
    day = datetime.fromtimestamp(ts_ns / 1e9, tz=_ET).date()
    key = f"{symbol}:{day.isoformat()}"
    if key not in daily:
        daily[key] = fetch_daily_volume(client, data_base_url, symbol, day)
    ratio = assert_window_volume(float(trades["size"].sum()), daily[key],
                                 2.0 * SNAPSHOT_S, f"{symbol} {_rfc3339(ts_ns)}")
    quotes = fetch_quotes(client, data_base_url, symbol, lo, hi)
    save_npz(t_path, **trades, prorata_ratio=np.array([ratio]))
    save_npz(q_path, **quotes)


def stage_fetch(out_dir: Path, stratum: str, client, data_base_url: str,
                limit: int | None = None) -> dict:
    doc = json.loads((out_dir / f"stage2_selected_{stratum}.json").read_text())
    events = doc["selected"][:limit] if limit else doc["selected"]
    pool, _ = qualifying_sessions()
    cache: dict[date, tuple[np.ndarray, np.ndarray] | None] = {}

    def _activity(sess: date):
        if sess not in cache:
            cache[sess] = load_activity(out_dir, sess)
        return cache[sess]

    daily_path = out_dir / "daily_volume.json"
    daily = json.loads(daily_path.read_text()) if daily_path.exists() else {}
    ledger: list[dict] = []
    for i, ev in enumerate(events, 1):
        session = date.fromisoformat(ev["session"])
        ctrls = control_sessions(session, ev["symbol"], ev["ts_ns"], pool, _activity)
        base = session_midnight_ns(session)
        ctrl_ts = [session_midnight_ns(c) + int(ev["ts_ns"] - base) for c in ctrls]
        fetch_window(out_dir, client, data_base_url, ev["symbol"], ev["ts_ns"], daily)
        for ts in ctrl_ts:
            fetch_window(out_dir, client, data_base_url, ev["symbol"], ts, daily)
        ledger.append({**ev, "control_sessions": [c.isoformat() for c in ctrls],
                       "control_ts_ns": ctrl_ts})
        save_json(daily_path, daily)
        if i % 10 == 0:
            logger.info("fetched %s/%s events", i, len(events))
    out = {"spec": SPEC, "stage": "fetch", "stratum": stratum, "events": ledger}
    save_json(out_dir / f"fetch_ledger_{stratum}.json", out)
    logger.info("stage 3: %s events, %s windows",
                len(ledger), sum(1 + len(e["control_ts_ns"]) for e in ledger))
    return out


# =============================================================================
# stage 4 — analyze
# =============================================================================

def _load_window(out_dir: Path, symbol: str, ts_ns: int) -> tuple[dict, dict] | None:
    t_path = _snap_path(out_dir, symbol, ts_ns, "trades")
    q_path = _snap_path(out_dir, symbol, ts_ns, "quotes")
    if not (t_path.exists() and q_path.exists()):
        return None
    with np.load(t_path, allow_pickle=False) as z:
        trades = {k: z[k] for k in z.files}
    with np.load(q_path, allow_pickle=False) as z:
        quotes = {k: z[k] for k in z.files}
    return trades, quotes


def nbbo_mid_at(quotes: dict, ts_ns: int) -> float:
    """Equity NBBO midpoint at T — the Δ_measure spot. The last pre-T PRINT is
    too often a 1-share TRF odd lot (measured), which is why this is the quote."""
    idx = prevailing_quote_idx(np.array([ts_ns], dtype=np.int64), quotes["t_ns"])[0]
    if idx < 0:
        return float("nan")
    b, a = float(quotes["bid"][idx]), float(quotes["ask"][idx])
    if b <= 0.0 or a <= 0.0 or a <= b:
        return float("nan")
    return 0.5 * (a + b)


def _signed_arrays(trades: dict, quotes: dict, table: dict) -> dict:
    """Per-window signed size arrays for every registered arm."""
    excluded, qct = condition_masks(trades["cond"], trades["tape"], table)
    trf = trades["exchange"] == TRF_EXCHANGE
    base = ~excluded & ~qct
    signs = sign_midpoint(trades["t_ns"], trades["price"], quotes["t_ns"],
                          quotes["bid"], quotes["ask"])
    ticks = sign_tick_rule(trades["price"])
    size = trades["size"].astype(np.float64)
    keep = base & ~trf
    return {
        "t": trades["t_ns"],
        "primary": np.where(keep, signs * size, 0.0),
        "trf_incl": np.where(base, signs * size, 0.0),
        "tick": np.where(keep, ticks * size, 0.0),
        "unsigned": np.where(keep, size, 0.0),
        "qct": np.where(qct, size, 0.0),
        "signable": float(size[keep & (signs != 0)].sum()),
        "keep_volume": float(size[keep].sum()),
        "trf_volume": float(size[base & trf].sum()),
        "total_volume": float(size.sum()),
    }


def measure_event(out_dir: Path, ev: dict, table: dict) -> dict | None:
    """Δ_measure, S/E/Z per horizon, the Look-B profile, all arms."""
    loaded = _load_window(out_dir, ev["symbol"], ev["ts_ns"])
    if loaded is None:
        return None
    trades, quotes = loaded
    spot = nbbo_mid_at(quotes, ev["ts_ns"])
    if not np.isfinite(spot) or spot <= 0.0:
        return None
    t_years = time_to_expiry_years(ev["ts_ns"], ev["exp_days"])
    fit = fit_delta(ev["option_quote_mid"], spot, ev["strike"], t_years, ev["is_put"])
    if not fit.ok:
        return {"symbol": ev["symbol"], "session": ev["session"], "ts_ns": ev["ts_ns"],
                "dropped": f"delta_measure_{fit.reason or 'failed'}"}
    h = hedge_shares(ev["side_sign"], fit.delta, ev["contracts"])
    ev_arr = _signed_arrays(trades, quotes, table)

    ctrl_arrays = []
    for ts in ev["control_ts_ns"]:
        c = _load_window(out_dir, ev["symbol"], ts)
        if c is None:
            continue
        ctrl_arrays.append((ts, _signed_arrays(*c, table)))
    if not ctrl_arrays:
        return {"symbol": ev["symbol"], "session": ev["session"], "ts_ns": ev["ts_ns"],
                "dropped": "no_controls"}

    rec: dict = {
        "symbol": ev["symbol"], "session": ev["session"], "ts_ns": ev["ts_ns"],
        "band": ev["band"], "stratum": ev["stratum"], "contracts": ev["contracts"],
        "premium": ev["premium"], "d": ev.get("d"),
        "spot_measure": spot, "iv_measure": fit.iv, "delta_measure": fit.delta,
        "abs_delta_measure": abs(fit.delta), "h": h, "abs_h": abs(h),
        "h_sign": 1.0 if h > 0 else (-1.0 if h < 0 else 0.0),
        "n_controls": len(ctrl_arrays),
        "signable_share": (ev_arr["signable"] / ev_arr["keep_volume"]
                           if ev_arr["keep_volume"] > 0 else float("nan")),
        "trf_share": (ev_arr["trf_volume"] / ev_arr["total_volume"]
                      if ev_arr["total_volume"] > 0 else float("nan")),
        "qct_shares": float(ev_arr["qct"].sum()),
        "horizons": {},
    }
    t0 = ev["ts_ns"]
    for k in HORIZONS_G:
        h_ns = k * G_NS
        row = {}
        for arm in ("primary", "trf_incl", "tick", "unsigned"):
            s_ev = statistic_s(ev_arr["t"], ev_arr[arm], t0, h_ns)
            s_ct = np.array([statistic_s(a["t"], a[arm], ts, h_ns)
                             for ts, a in ctrl_arrays], dtype=np.float64)
            ex = excess(s_ev, s_ct)
            row[arm] = {"s": s_ev, "e": ex.e, "z": ex.z, "mad": ex.mad,
                        "ctrl_median": ex.median}
            row[f"{arm}_ctrl"] = s_ct.tolist()
        pre_ev = window_sum(ev_arr["t"], ev_arr["unsigned"], t0 - h_ns, t0)
        pre_ct = np.array([window_sum(a["t"], a["unsigned"], ts - h_ns, ts)
                           for ts, a in ctrl_arrays], dtype=np.float64)
        row["pre_unsigned"] = pre_ev
        row["pre_unsigned_ctrl_p50"] = float(np.percentile(pre_ct, QUIET_PCTL))
        row["quiet"] = bool(pre_ev <= row["pre_unsigned_ctrl_p50"])
        # reverse-causality check: the SAME excess statistic on the pre-window,
        # hedge-aligned. A trader working both markets leaves a positive pre
        # excess; a dealer hedging after the fill does not.
        h_sign = 1.0 if h > 0 else (-1.0 if h < 0 else 0.0)
        pre_ex = excess(
            window_sum(ev_arr["t"], ev_arr["primary"], t0 - h_ns, t0),
            np.array([window_sum(a["t"], a["primary"], ts - h_ns, ts)
                      for ts, a in ctrl_arrays], dtype=np.float64))
        row["pre_window_excess_z"] = pre_ex.z * h_sign

        # Look B: control-normalized signed excess PER g-BIN, summed over post.
        bins_ev = _profile(ev_arr["t"], ev_arr["primary"], t0, k)
        bins_ct = np.array([_profile(a["t"], a["primary"], ts, k)
                            for ts, a in ctrl_arrays], dtype=np.float64)
        b_shares = float((bins_ev - np.median(bins_ct, axis=0))[k:].sum())
        row["b_shares"] = b_shares
        mad = row["primary"]["mad"]
        row["b_z"] = b_shares / mad if mad > 0 else float("nan")
        rec["horizons"][str(k)] = row
    return rec


def _profile(t_ns: np.ndarray, signed: np.ndarray, t0: int, k: int) -> np.ndarray:
    """Signed volume per g-bin over [T−k·g, T+k·g); index k is the first post bin.

    ⚠️ 165g = 300.3s but the registered fetch window is ±300s, so the outermost
    bin of the 165g profile is short by 0.3s — identically on the event and on
    every control, so the comparison stays like-for-like.
    """
    edges = t0 + (np.arange(-k, k + 1, dtype=np.int64) * G_NS)
    idx = np.searchsorted(edges, np.asarray(t_ns, dtype=np.int64), side="right") - 1
    out = np.zeros(2 * k, dtype=np.float64)
    ok = (idx >= 0) & (idx < 2 * k)
    np.add.at(out, idx[ok], np.asarray(signed, dtype=np.float64)[ok])
    return out


def _arrays(records: list[dict], k: int, arm: str = "primary",
            tier: str | None = "quiet", band: str = "primary",
            stat: str = "z") -> dict[str, np.ndarray]:
    """Pull the analysis arrays for one (horizon, arm, tier, band) cut."""
    rows = []
    for r in records:
        if r.get("dropped") or r.get("band") != band:
            continue
        h = r["horizons"][str(k)]
        if tier == "quiet" and not h["quiet"]:
            continue
        if tier == "active" and h["quiet"]:
            continue
        val = h[arm][stat] if arm in h else float("nan")
        if not np.isfinite(val) or r["h_sign"] == 0.0:
            continue
        rows.append((r, h, val))
    return {
        "value": np.array([v for _, _, v in rows], dtype=np.float64),
        "abs_h": np.array([r["abs_h"] for r, _, _ in rows], dtype=np.float64),
        "h_sign": np.array([r["h_sign"] for r, _, _ in rows], dtype=np.float64),
        "shares": np.array([r["contracts"] * 100.0 for r, _, _ in rows], dtype=np.float64),
        "abs_delta": np.array([r["abs_delta_measure"] for r, _, _ in rows], dtype=np.float64),
        "premium": np.array([r["premium"] for r, _, _ in rows], dtype=np.float64),
        "cluster": np.array([f"{r['symbol']}|{r['session']}" for r, _, _ in rows]),
        "underlying": np.array([r["symbol"] for r, _, _ in rows]),
        "ctrl": [h[f"{arm}_ctrl"] for _, h, _ in rows],
        "records": [r for r, _, _ in rows],
    }


def look_a(a: dict, n_perm: int = N_PERM, n_boot: int = N_BOOT) -> dict:
    """Ẽ = Z·sign(H) vs |H|: Theil–Sen slope, intercept REPORTED, cluster
    bootstrap CI over underlyings, cluster-permutation p."""
    x, z, s = a["abs_h"], a["value"], a["h_sign"]
    if len(x) < 5:
        return {"n": len(x), "pass": False, "reason": "too few events"}
    slope, intercept = theil_sen(x, hedge_aligned(z, s))
    boot = cluster_bootstrap(
        lambda idx: theil_sen(x[idx], hedge_aligned(z[idx], s[idx]))[0],
        a["underlying"], n_boot=n_boot)
    boot = boot[np.isfinite(boot)]
    lo, hi = (float(np.percentile(boot, 2.5)), float(np.percentile(boot, 97.5))) \
        if len(boot) else (float("nan"), float("nan"))
    _, p = cluster_permutation_p(lambda sg: _look_a_slope(x, z, sg), s, a["cluster"],
                                 n_perm=n_perm)
    aligned = hedge_aligned(z, s)
    quints = np.array_split(np.argsort(x), 5)
    return {
        "n": len(x), "slope": slope, "intercept": intercept,
        "ci95": [lo, hi], "permutation_p": p,
        "mean_aligned": float(np.mean(aligned)), "median_aligned": float(np.median(aligned)),
        "n_positive_h": int((s > 0).sum()), "n_negative_h": int((s < 0).sum()),
        "slope_h_positive": theil_sen(x[s > 0], aligned[s > 0])[0] if (s > 0).sum() >= 5 else None,
        "slope_h_negative": theil_sen(x[s < 0], aligned[s < 0])[0] if (s < 0).sum() >= 5 else None,
        # illustrative only — no verdict authority
        "quintile_mean_aligned": [float(np.mean(aligned[q])) for q in quints],
        "pass": bool(np.isfinite(slope) and slope > 0 and np.isfinite(lo) and lo > 0),
    }


def look_a_prime(a: dict, n_perm: int = N_PERM) -> dict:
    """Ẽ ~ a·(contracts×100) + b·|H|. Common information scales with SIZE traded;
    only a delta hedge scales with Δ holding contracts fixed. Needs b > 0."""
    x1, x2, z, s = a["shares"], a["abs_h"], a["value"], a["h_sign"]
    iqr = float(np.percentile(a["abs_delta"], 75) - np.percentile(a["abs_delta"], 25)) \
        if len(a["abs_delta"]) else float("nan")
    if len(z) < 8 or not np.isfinite(iqr) or iqr < 0.15:
        return {"n": len(z), "abs_delta_iqr": iqr, "evaluable": False,
                "pass": None, "reason": "|Δ| IQR < 0.15 — the LEG is unevaluable, not the study"}
    def _b(sg: np.ndarray) -> float:
        return ols_two_term(hedge_aligned(z, sg), x1, x2)[2]
    b_obs, p = cluster_permutation_p(_b, s, a["cluster"], n_perm=n_perm)
    icept, a_coef, _ = ols_two_term(hedge_aligned(z, s), x1, x2)
    return {"n": len(z), "abs_delta_iqr": iqr, "evaluable": True,
            "intercept": icept, "a_contracts": a_coef, "b_absH": b_obs,
            "permutation_p": p,
            "pass": bool(b_obs > 0 and np.isfinite(p) and p < PERM_ALPHA)}


def look_b(records: list[dict], k: int, tier: str, band: str,
           n_perm: int = N_PERM) -> dict:
    """The stacked profile AS A TEST: per-event control-normalized hedge-aligned
    signed excess per g-bin, summed over the post window."""
    rows = [r for r in records
            if not r.get("dropped") and r.get("band") == band and r["h_sign"] != 0.0
            and (tier is None or (r["horizons"][str(k)]["quiet"] == (tier == "quiet")))]
    b = np.array([r["horizons"][str(k)]["b_z"] for r in rows], dtype=np.float64)
    s = np.array([r["h_sign"] for r in rows], dtype=np.float64)
    cluster = np.array([f"{r['symbol']}|{r['session']}" for r in rows])
    ok = np.isfinite(b)
    b, s, cluster = b[ok], s[ok], cluster[ok]
    if len(b) < 5:
        return {"n": len(b), "pass": False, "reason": "too few events"}
    stat, p = cluster_permutation_p(lambda sg: float(np.mean(b * sg)), s, cluster,
                                    n_perm=n_perm)
    return {"n": len(b), "mean_aligned_b_z": stat, "permutation_p": p,
            "mean_b_shares": float(np.mean([r["horizons"][str(k)]["b_shares"] * r["h_sign"]
                                            for r in rows])),
            "pass": bool(stat > 0 and np.isfinite(p) and p < PERM_ALPHA)}


def look_c(a_unsigned: dict, n_boot: int = N_BOOT) -> dict:
    """Unsigned volume excess — immune to signing loss, which is mechanism-aligned
    (dealers route hedges to midpoint dark precisely to avoid signalling).

    ⚠️ The registered sign(H)-flip permutation is DEGENERATE on an unsigned
    statistic (flipping sign(H) leaves it unchanged, so p ≡ 1). Inference here is
    the registered cluster bootstrap over underlyings; recorded as a deviation.
    """
    z = a_unsigned["value"]
    if len(z) < 5:
        return {"n": len(z), "pass": False, "reason": "too few events"}
    boot = cluster_bootstrap(lambda idx: float(np.median(z[idx])),
                             a_unsigned["underlying"], n_boot=n_boot)
    boot = boot[np.isfinite(boot)]
    lo, hi = float(np.percentile(boot, 2.5)), float(np.percentile(boot, 97.5))
    return {"n": len(z), "median_z": float(np.median(z)), "mean_z": float(np.mean(z)),
            "ci95": [lo, hi], "inference": "cluster bootstrap (permutation degenerate)",
            "pass": bool(lo > 0)}


def _cut(records: list[dict], k: int, tier: str, band: str, arm: str,
         n_perm: int, n_boot: int) -> dict:
    a = _arrays(records, k, arm=arm, tier=tier, band=band)
    a_uns = _arrays(records, k, arm="unsigned", tier=tier, band=band)
    la = look_a(a, n_perm=n_perm, n_boot=n_boot)
    lap = look_a_prime(a, n_perm=n_perm)
    lb = look_b(records, k, tier, band, n_perm=n_perm)
    lc = look_c(a_uns, n_boot=n_boot)
    direction_stat, direction_p = cluster_permutation_p(
        lambda sg: float(np.mean(hedge_aligned(a["value"], sg))), a["h_sign"],
        a["cluster"], n_perm=n_perm) if len(a["value"]) >= 5 else (float("nan"), float("nan"))
    # β in SHARES — the interpretable one. Z is primary for every TEST, but the
    # number that means "how much of the theoretical hedge showed up" is the
    # Theil–Sen slope of the hedge-aligned E on |H|. Attenuated LOWER bound.
    a_e = _arrays(records, k, arm=arm, tier=tier, band=band, stat="e")
    beta_shares, beta_intercept = theil_sen(a_e["abs_h"],
                                            hedge_aligned(a_e["value"], a_e["h_sign"]))
    return {
        "n": len(a["value"]),
        "n_underlyings": int(len(np.unique(a["underlying"]))) if len(a["value"]) else 0,
        "look_a": la, "look_a_prime": lap, "look_b": lb, "look_c": lc,
        "direction_mean_aligned_z": direction_stat, "direction_permutation_p": direction_p,
        "beta_shares_attenuated_lower_bound": beta_shares,
        "beta_shares_intercept": beta_intercept,
        "mean_e_shares": float(np.mean([r["horizons"][str(k)][arm]["e"] * r["h_sign"]
                                        for r in a["records"]])) if a["records"] else None,
        "signable_share_median": (float(np.median([r["signable_share"] for r in a["records"]]))
                                  if a["records"] else None),
        "pre_window_excess_z_median": _median_finite(
            [r["horizons"][str(k)]["pre_window_excess_z"] for r in a["records"]]),
    }


def _median_finite(values: list[float]) -> float | None:
    arr = np.array([v for v in values if v is not None], dtype=np.float64)
    arr = arr[np.isfinite(arr)]
    return float(np.median(arr)) if len(arr) else None


def stage_analyze(out_dir: Path, stratum: str, n_perm: int = N_PERM,
                  n_boot: int = N_BOOT, mde_rep: int = 100,
                  mde_perm: int = 400) -> dict:
    table = build_condition_table()
    ledger = json.loads((out_dir / f"fetch_ledger_{stratum}.json").read_text())
    records: list[dict] = []
    for ev in ledger["events"]:
        rec = measure_event(out_dir, ev, table)
        if rec is not None:
            records.append(rec)
    usable = [r for r in records if not r.get("dropped")]
    logger.info("analyze: %s events measured, %s usable", len(records), len(usable))

    looks: dict = {}
    for tier in ("quiet", "active"):
        looks[tier] = {}
        for k in HORIZONS_G:
            looks[tier][str(k)] = _cut(usable, k, tier, "primary", "primary",
                                       n_perm, n_boot)

    # power floor — injected into CONTROL windows, consumes no event outcomes
    a = _arrays(usable, PRIMARY_HORIZON_G, tier="quiet", band="primary")
    width = min((len(c) for c in a["ctrl"]), default=0)
    if len(a["value"]) >= 5 and width >= 3:
        ctrl = np.array([c[:width] for c in a["ctrl"]], dtype=np.float64)
        power = mde_by_injection(a["abs_h"], a["h_sign"], a["cluster"], ctrl,
                                 n_rep=mde_rep, n_perm=mde_perm)
    else:
        power = {"mde": None, "power_by_beta": {}, "note": "too few events/controls"}

    primary = looks["quiet"][str(PRIMARY_HORIZON_G)]
    all_fail = all_horizons_failed(looks["quiet"])
    vd = verdict(primary["look_a"], primary["look_a_prime"], primary["look_b"],
                 primary["look_c"], primary["direction_permutation_p"],
                 primary["n"], primary["n_underlyings"], power.get("mde"), all_fail)

    robustness = {
        "trf_inclusive": {str(k): _cut(usable, k, "quiet", "primary", "trf_incl",
                                       n_perm, n_boot) for k in HORIZONS_G},
        "tick_rule_secondary": {str(k): _cut(usable, k, "quiet", "primary", "tick",
                                             n_perm, n_boot) for k in HORIZONS_G},
        "delta_band_secondary": {str(k): _cut(usable, k, "quiet", "secondary", "primary",
                                              n_perm, n_boot) for k in HORIZONS_G},
        "premium_100k": _horizon_cuts(
            [r for r in usable if r["premium"] >= PREMIUM_ROBUST], n_perm, n_boot),
        "top_150": _horizon_cuts(
            sorted((r for r in usable if r["band"] == "primary"),
                   key=lambda r: -(r.get("d") or 0.0))[:STAGE2_TOP_ROBUST],
            n_perm, n_boot),
        "active_tape_stratum": {str(k): looks["active"][str(k)] for k in HORIZONS_G},
        "etf_stratum": ("run `--stratum etf` separately — ETFs are a DECLARED "
                        "SEPARATE STRATUM and are never pooled with equities"),
    }

    stage1 = json.loads((out_dir / f"stage1_candidates_{stratum}.json").read_text())
    stage2 = json.loads((out_dir / f"stage2_selected_{stratum}.json").read_text())
    result = {
        "spec": SPEC,
        "stratum": stratum,
        "grain_ns": G_NS,
        "horizons_g": list(HORIZONS_G),
        "horizon_note": ("165g = 300.3s but the registered fetch window is ±300s; the "
                         "outermost 0.3s is missing identically on event and controls"),
        "condition_table": table,
        "sessions": stage1["sessions"],
        "drop_ledger_stage1": stage1["drop_ledger"],
        "drop_ledger_stage2": stage2["ledger"],
        "n_events_measured": len(records),
        "n_events_usable": len(usable),
        "n_events_dropped": [r.get("dropped") for r in records if r.get("dropped")],
        "qct_shares_median": _median_finite([r["qct_shares"] for r in usable]),
        "trf_share_median": _median_finite([r["trf_share"] for r in usable]),
        "signable_share_median": _median_finite([r["signable_share"] for r in usable]),
        "abs_delta_measure_iqr": (
            float(np.percentile([r["abs_delta_measure"] for r in usable], 75)
                  - np.percentile([r["abs_delta_measure"] for r in usable], 25))
            if usable else None),
        "looks": looks,
        "power": power,
        "robustness": robustness,
        **vd,
        "disclosure": (
            "Training window only; consumes NO outcomes (microstructure seconds around "
            "prints, never forward returns) — the test split stays sealed. β is an "
            "ATTENUATED LOWER BOUND: existence means β > 0, never β = 1."
        ),
    }
    save_json(out_dir / f"results_{stratum}.json", result)
    logger.info("verdict: %s", result["verdict"])
    return result


def all_horizons_failed(looks_quiet: dict) -> bool:
    """ABSENT's precondition: EVERYTHING dead at every horizon — including the
    direction test. The registered table enumerates "direction-without-size" as
    AMBIGUOUS, so a passing direction signal must never route to the kill."""
    return all(
        not looks_quiet[str(k)]["look_a"].get("pass")
        and not looks_quiet[str(k)]["look_b"].get("pass")
        and not looks_quiet[str(k)]["look_c"].get("pass")
        and looks_quiet[str(k)]["look_a_prime"].get("pass") is not True
        and not (np.isfinite(looks_quiet[str(k)]["direction_permutation_p"])
                 and looks_quiet[str(k)]["direction_permutation_p"] < PERM_ALPHA)
        for k in HORIZONS_G
    )


def _horizon_cuts(records: list[dict], n_perm: int, n_boot: int) -> dict:
    return {str(k): _cut(records, k, "quiet", "primary", "primary", n_perm, n_boot)
            for k in HORIZONS_G}


# =============================================================================
# cli
# =============================================================================

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("stage", choices=("select", "bars", "fetch", "analyze"))
    parser.add_argument("--stratum", choices=("equity", "etf"), default="equity",
                        help="ETFs are a DECLARED SEPARATE STRATUM, never pooled")
    parser.add_argument("--out-dir", type=Path, default=OUT_DIR)
    parser.add_argument("--limit", type=int, default=None,
                        help="cap events processed (bars/fetch) — smoke runs only")
    parser.add_argument("--n-perm", type=int, default=N_PERM)
    parser.add_argument("--n-boot", type=int, default=N_BOOT)
    parser.add_argument("--mde-rep", type=int, default=100)
    parser.add_argument("--mde-perm", type=int, default=400)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    out_dir = args.out_dir / args.stratum

    if args.stage == "select":
        stage_select(out_dir, args.stratum)
        return 0
    if args.stage == "analyze":
        stage_analyze(out_dir, args.stratum, n_perm=args.n_perm, n_boot=args.n_boot,
                      mde_rep=args.mde_rep, mde_perm=args.mde_perm)
        return 0

    cfg = alpaca_config()
    if not cfg["api_key_id"]:
        raise SystemExit("no Alpaca credentials in settings/.env")
    headers = {"APCA-API-KEY-ID": cfg["api_key_id"],
               "APCA-API-SECRET-KEY": cfg["api_secret_key"]}
    with httpx.Client(headers=headers, timeout=60.0) as client:
        if args.stage == "bars":
            stage_bars(out_dir, args.stratum, client, cfg["data_base_url"], args.limit)
        else:
            stage_fetch(out_dir, args.stratum, client, cfg["data_base_url"], args.limit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
