"""Roadmap 1.14f: the intraday screen — the grain descent.

PRE-REGISTERED 2026-07-30 (`References/FLOW_ROADMAP.md` §1.14f) BEFORE any
intraday outcome existed on disk. This file implements that registration
literally; it does not get to improve it. 1.14c/d/e closed the day-grain arc —
the fingerprint describes big-move windows without selecting them, and the one
conditioning that works (earnings) is a calendar fact. The registered question
here: does the SAME fingerprint, computed inside the session from the labeled
print tape, select forthcoming moves at minutes-to-hours?

**NOT A BACKTEST.** No entry, no exit, no slippage, no sizing, no P&L, no hit
rate appears anywhere. The output is a distribution: fired vs complement median
normalized move, with intervals, plus the NA accounting at every stage.

What the registration fixes, and where it lands below:
  * feature tape = raw `opra_tcbbo` joined to `opra_defs`, fenced with the v7
    semantics — `fence_and_classify` / `multileg_flags` imported from the corpus
    roller so there is exactly ONE fence in the repo. Features come per print
    from the raw tape, NEVER from the daily rollup (the one exception the
    registration itself names: `largest_print_ratio`'s day-grain denominator,
    which it declares "unchanged").
  * outcome store = `data/equity_bars_5min/` (READ ONLY — written by
    `tools/equity_bars_5min_backfill.py`).
  * analysis mask = train sessions <= 2026-05-31. The test tail lands on disk
    and is never read into analysis.
  * grain = half-hour boundaries 10:00..15:30 ET, trailing W in {30,60,120} min,
    window fully inside RTH, eligibility >= 20 fenced classified prints.
  * peer cell = (session x boundary-of-day x trailing-20-session equity
    dollar-volume quintile); firing = component >= p90 in the cell.
  * primary cell = (W=60, H=2h), 4 components => 4 primary intervals.
    Circular moving-block bootstrap over SESSIONS, block=5, 10,000 draws, ONE
    shared resampling, SEED=20260808.

Chronological single pass, numpy only (no pandas — serv holds the OPRA socket).
Trailing normalizers are read from a ring buffer BEFORE this session is written
into it, which is what makes "all denominators end at t-1" true by construction
rather than by review.

    python tools/mover_intraday.py                      # full registered run
    python tools/mover_intraday.py --limit-sessions 3    # smoke
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
import warnings
from datetime import date, datetime
from datetime import time as dtime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools.flow_corpus_rollup import (  # noqa: E402
    TCBBO_DIR,
    _bucket_dte,
    _load_definitions,
    fence_and_classify,
    multileg_flags,
)
from tools.mover_controls import TRAIN_END, _tier_quintiles  # noqa: E402
from tools.mover_forward import _roll_med60  # noqa: E402

logger = logging.getLogger("mover_intraday")

CORPUS_DB = ROOT / "data" / "flow_corpus.db"
BARS_DIR = ROOT / "data" / "equity_bars_5min"
EARN_DB = ROOT / "data" / "research" / "earnings.db"
JOIN_LEDGER = ROOT / "data" / "alpaca_conditions" / "join_ledger.json"
OUT = ROOT / "data" / "research" / "mover_intraday_114f.json"

ET = ZoneInfo("America/New_York")
RTH_OPEN = dtime(9, 30)
OPEN_MIN = 9 * 60 + 30
BAR_MIN = 5
RTH_BINS = 78                      # 09:30..15:55, 5-minute bars
BIN_NS = BAR_MIN * 60 * 1_000_000_000

# Registered grid: half-hour boundaries 10:00..15:30 ET, as 5-minute bin index.
BOUNDARY_BINS = tuple(range(6, 73, 6))
BOUNDARY_LABELS = tuple(
    f"{(OPEN_MIN + b * BAR_MIN) // 60:02d}:{(OPEN_MIN + b * BAR_MIN) % 60:02d}"
    for b in BOUNDARY_BINS
)
WINDOWS = (30, 60, 120)            # trailing minutes
HORIZONS = ("30m", "2h", "close")  # H_BINS None = to the close
H_BINS = {"30m": 6, "2h": 24, "close": None}
PRIMARY_W, PRIMARY_H = 60, "2h"

MIN_PRINTS = 20        # registered eligibility: fenced classified prints/window
MIN_PMAX_DAYS = 20     # largest_print_ratio denominator: >=20 valid days
MIN_VOL_SESSIONS = 10  # vol_max_ratio denominator: >=10 valid sessions
MIN_OUT_SESSIONS = 10  # outcome normalizer: >=10 valid sessions
PMAX_LOOKBACK = 60     # trailing sessions for the daily premium_max median
VOL_LOOKBACK = 20      # trailing sessions for the (boundary, W) premium median
OUT_LOOKBACK = 20      # trailing sessions for the (boundary, H) move median
BAR_COVERAGE = 0.80    # <80% of the session's expected bars => name-day is NA
RANK_PCT = 90.0        # firing threshold within the peer cell
SHORT_DTE_MAX_BUCKET = 1   # _bucket_dte: 0 = "0-7", 1 = "8-30"
UNDEF_SIZE = 4_294_967_295  # tcbbo size sentinel (see tools/order_grain_null.py)

FEATS = ("short_dte_share", "concentration", "largest_print_ratio",
         "vol_max_ratio")
N_BOOT = 10_000
BLOCK = 5              # registered block length, in SESSIONS
SEED = 20260808
TAIL_X = 3.0           # secondary: tail-rate difference at >=3x normalized move
K_GATE = 10            # 1.14e earnings gate: EDGAR filing within 10 sessions

# Every point where the registration admitted more than one reading. Recorded in
# the output so the reader sees the choice rather than inferring it from code.
AMBIGUITIES = {
    "complement_boundaries": (
        "'first-fire-per-day ... excluded from fired AND complement' + "
        "'complement = eligible names never fired that session, evaluated at "
        "the same boundary cell' are reconciled as: a name that fires anywhere "
        "in the session contributes ONE fired row (its first firing boundary) "
        "and is excluded from the complement at EVERY boundary; a name that "
        "never fires contributes a complement row at every eligible boundary. "
        "This keeps the complement boundary-matched to the fired rows, which "
        "the mandatory boundary-of-day cell exists to protect. The alternative "
        "reading (complement also first-boundary-only) is reported as the "
        "'complement_first_boundary_only' diagnostic median, no authority."),
    "p90_population": (
        "p90 is taken over the FULL eligible population at that (session, "
        "boundary, quintile) cell, then the first-fire rule filters which rows "
        "ENTER the analysis — 1.14e's 'fires are NOT re-ranked inside the "
        "gate' applied at the new grain. The threshold therefore does not "
        "drift as earlier-fired names are removed."),
    "largest_print_ratio_denominator": (
        "the registration says 'the name's own trailing-60-session median "
        "daily premium_max ... same normalizer as day grain ... denominator "
        "unchanged', so the DENOMINATOR is read from flow_rollup (the "
        "day-grain quantity, same fenced basis) while the NUMERATOR is the raw "
        "tape's max single print in the window. This is the one registered "
        "exception to 'never from the daily rollup'. Window ends at t-1."),
    "vol_max_ratio_valid_session": (
        "a 'valid session' for the trailing (boundary, W) window-premium "
        "median = a session where the name had >=1 fenced classified print "
        "anywhere in RTH; the window premium is then recorded even when it is "
        "0. Counting a genuine 0 rather than dropping it avoids biasing the "
        "denominator upward. Median must be > 0 or the row is NA."),
    "expected_bars": (
        "'<80% of expected bars' uses the session's own bar grid — the count "
        "of distinct 5-minute timestamps present anywhere in that session's "
        "npz, capped at 78 — so an early-close session is measured against its "
        "own 42-bar grid instead of NA-ing every name in it."),
    "concentration_floor": (
        "the day-grain concentration carried a $250k active-day floor; the "
        "registration's intraday form states no floor, and the >=20-print "
        "eligibility plays that role here. No floor is applied."),
    "universe_fence": (
        "the analysis universe inherits the day-grain arc's fence "
        "(INDEX_ROOTS | ETF_SET, as 1.14c/d/e used) because 1.14f is "
        "explicitly the SAME fingerprint at a finer grain. --no-universe-fence "
        "runs the unfenced superset; both counts are disclosed."),
    "outcome_base_price": (
        "'cumulative 5-min log return from b' is measured against the close of "
        "the last bar strictly before b (the price AT b), so the outcome shares "
        "no bar with the feature window."),
}


# --------------------------------------------------------------------------
# geometry — the registered grid, as pure functions so it can be tested
# --------------------------------------------------------------------------

def window_bins(boundary_bin: int, w_minutes: int) -> tuple[int, int] | None:
    """Half-open 5-minute bin range `[lo, hi)` for the trailing window, or None.

    None means the window would need bars from before 09:30. The registration is
    explicit: a (b, W) pair enters only if the window lies fully inside RTH —
    "the open bucket participates as window CONTENT, never as a truncated
    window". So W=60 drops the 10:00 boundary and W=120 drops 10:00/10:30/11:00.
    """
    lo = boundary_bin - w_minutes // BAR_MIN
    return (lo, boundary_bin) if lo >= 0 else None


def horizon_end_bin(boundary_bin: int, horizon: str) -> int | None:
    """Exclusive bin bound for the outcome window, or None if b is ineligible.

    Mechanical, exactly as registered: H=2h needs b <= 14:00 (bin 54, whose 2
    hours end precisely at 16:00); H=30m and H=close need b <= 15:30.
    """
    span = H_BINS[horizon]
    end = RTH_BINS if span is None else boundary_bin + span
    return end if end <= RTH_BINS else None


def _rth_open_ns(session: date) -> int:
    """Epoch ns of 09:30 ET — zoneinfo, so the corpus's DST changes are right."""
    return int(datetime.combine(session, RTH_OPEN, ET).timestamp()) * 1_000_000_000


def bins_from_ns(ts_ns: np.ndarray, session: date) -> np.ndarray:
    """5-minute RTH bin index per timestamp; -1 outside [09:30, 16:00) ET."""
    rel = (ts_ns - _rth_open_ns(session)) // BIN_NS
    return np.where((rel >= 0) & (rel < RTH_BINS), rel, -1).astype(np.int64)


# --------------------------------------------------------------------------
# per-session feature construction from the raw tape
# --------------------------------------------------------------------------

def group_max(key: np.ndarray, values: np.ndarray, n_cells: int) -> np.ndarray:
    """Per-cell maximum of `values`, zero where a cell is empty.

    `np.maximum.at` is a Python-level ufunc loop and costs minutes on an 11M
    print session; one stable argsort plus `maximum.reduceat` stays in C.
    """
    out = np.zeros(n_cells)
    if not len(key):
        return out
    order = np.argsort(key, kind="stable")
    ks, vs = key[order], values[order]
    starts = np.flatnonzero(np.r_[True, ks[1:] != ks[:-1]])
    out[ks[starts]] = np.maximum.reduceat(vs, starts)
    return out


def window_stats(
    cls_bin: np.ndarray, short_bin: np.ndarray, bull_bin: np.ndarray,
    cnt_bin: np.ndarray, pmax_bin: np.ndarray, lo: int, hi: int,
) -> dict[str, np.ndarray]:
    """Per-name window aggregates over 5-minute bins `[lo, hi)`.

    `bin_top` is the largest single 5-minute bin's classified premium (the
    declared intraday analog of day-grain concentration) and `print_top` the
    largest single PRINT's premium, which are different things and were easy to
    conflate: the first descends the granule with the grain, the second only
    descends the numerator.
    """
    return {
        "cls": cls_bin[:, lo:hi].sum(axis=1),
        "short": short_bin[:, lo:hi].sum(axis=1),
        "bull": bull_bin[:, lo:hi].sum(axis=1),
        "cnt": cnt_bin[:, lo:hi].sum(axis=1),
        "bin_top": cls_bin[:, lo:hi].max(axis=1),
        "print_top": pmax_bin[:, lo:hi].max(axis=1),
    }


def components(
    ws: dict[str, np.ndarray], med_pmax: np.ndarray, cnt_pmax: np.ndarray,
    vol_med: np.ndarray, vol_cnt: np.ndarray,
) -> dict[str, np.ndarray]:
    """The four registered components for one (boundary, W), NaN where NA.

    Denominators are all trailing and all end at t-1; the caller guarantees that
    by reading the ring buffers before writing this session into them.
    """
    cls = ws["cls"]
    with np.errstate(invalid="ignore", divide="ignore"):
        return {
            # 1. direct transplant: short-DTE share of window classified premium
            "short_dte_share": np.where(cls > 0, ws["short"] / cls, np.nan),
            # 2. declared analog: top 5-minute bin's share (no activity floor —
            #    the >=20-print eligibility is the intraday form of that guard)
            "concentration": np.where(cls > 0, ws["bin_top"] / cls, np.nan),
            # 3. numerator descends to the print, denominator unchanged
            "largest_print_ratio": np.where(
                (cnt_pmax >= MIN_PMAX_DAYS) & (med_pmax > 0)
                & np.isfinite(ws["print_top"]),
                ws["print_top"] / med_pmax, np.nan),
            # 4. the time-of-day-matched RVOL analog: same (boundary, W) window
            "vol_max_ratio": np.where(
                (vol_cnt >= MIN_VOL_SESSIONS) & (vol_med > 0),
                cls / vol_med, np.nan),
        }


def fire_p90(values: np.ndarray, quint: np.ndarray, pool: np.ndarray) -> np.ndarray:
    """Component >= p90 within each liquidity quintile of one boundary cell.

    1.14d/e's rule verbatim (`np.percentile(..., 90)`, ties fire) so the grain
    is the only thing that changed between the arcs.
    """
    fire = np.zeros(len(values), bool)
    for q in range(5):
        m = pool & (quint == q)
        if not m.any():
            continue
        cut = np.percentile(values[m], RANK_PCT)
        fire[m & (values >= cut)] = True
    return fire


# --------------------------------------------------------------------------
# outcomes, from the 5-minute bar store
# --------------------------------------------------------------------------

def outcome_measures(
    closes: np.ndarray, expected_bars: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """`(max_abs, signed_terminal, bars_ok)` per (name, boundary, horizon).

    `closes` is (n_names, RTH_BINS) with NaN for absent bars. The measure is the
    max |cumulative 5-minute log return| from b, against the close of the last
    bar strictly before b — so the outcome shares no bar with the trailing
    feature window. max|x| over a set is max(max(x), -min(x)), which turns the
    whole thing into two range reductions per (b, H) instead of a per-name loop.

    `bars_ok` is the registered <80%-of-expected-bars rule, applied to the whole
    name-day. Halt/LULD gaps inside a kept name-day are fine: the measure runs
    over the bars that exist.
    """
    n = closes.shape[0]
    n_b, n_h = len(BOUNDARY_BINS), len(HORIZONS)
    bars_ok = np.isfinite(closes).sum(axis=1) >= BAR_COVERAGE * expected_bars

    with np.errstate(invalid="ignore", divide="ignore"):
        logp = np.log(np.where(closes > 0, closes, np.nan))

    # last finite bin at or before k, per name — the "price at b" lookup
    have = np.isfinite(logp)
    idx = np.where(have, np.arange(RTH_BINS)[None, :], 0)
    np.maximum.accumulate(idx, axis=1, out=idx)
    ff = np.where(np.take_along_axis(have, idx, axis=1),
                  np.take_along_axis(logp, idx, axis=1), np.nan)
    rows = np.arange(n)

    max_abs = np.full((n, n_b, n_h), np.nan)
    signed = np.full((n, n_b, n_h), np.nan)
    for bi, bb in enumerate(BOUNDARY_BINS):
        base = ff[:, bb - 1]
        for hi, h in enumerate(HORIZONS):
            end = horizon_end_bin(bb, h)
            if end is None:
                continue
            seg = logp[:, bb:end]
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                hi_v = np.nanmax(seg, axis=1)
                lo_v = np.nanmin(seg, axis=1)
            with np.errstate(invalid="ignore"):
                max_abs[:, bi, hi] = np.maximum(hi_v - base, base - lo_v)
            # terminal price INSIDE the horizon, for the ONE direction test: the
            # last bar in [b, end), not the last bar before end — a name that
            # stops trading at b must be NA here, not read as a zero return.
            last = np.where(have[:, bb:end], np.arange(bb, end)[None, :], -1
                            ).max(axis=1)
            with np.errstate(invalid="ignore"):
                signed[:, bi, hi] = np.where(
                    last >= 0, logp[rows, np.maximum(last, 0)], np.nan) - base
    return max_abs, signed, bars_ok


def load_bars_session(path: Path, name_index: dict[str, int], n_names: int):
    """`(closes, expected_bars, n_symbols)` from one outcome-store npz.

    READ-ONLY consumer of the layout `tools/equity_bars_5min_backfill.py`
    writes: a sorted `symbols` index table plus parallel `t_ns`/`open`/`high`/
    `low`/`close`/`volume`/`sym_idx` arrays. Rows for symbols outside the
    analysis universe are dropped here rather than filtered downstream.
    """
    with np.load(path) as z:
        symbols = z["symbols"]
        sym_idx = z["sym_idx"]
        t_ns = z["t_ns"]
        close = z["close"]
    closes = np.full((n_names, RTH_BINS), np.nan)
    if not len(t_ns):
        return closes, 0, 0
    session = date.fromisoformat(path.stem)
    b = bins_from_ns(t_ns, session)
    # the session's own bar grid — an early close has 42 bins, not 78
    expected = min(RTH_BINS, len(np.unique(b[b >= 0])))
    mapped = np.array([name_index.get(str(s), -1) for s in symbols], dtype=np.int64)
    ni = mapped[sym_idx]
    keep = (ni >= 0) & (b >= 0)
    closes[ni[keep], b[keep]] = close[keep]
    return closes, expected, int((mapped >= 0).sum())


# --------------------------------------------------------------------------
# trailing state — the t-1 discipline, made structural
# --------------------------------------------------------------------------

class Ring:
    """Trailing-K per-cell history. NaN = that session had no value for the cell.

    The contract that makes every denominator end at t-1: `median_count()` is
    called for session t BEFORE `push()` writes session t. A same-day value
    cannot leak into its own normalizer because it is not in the buffer yet.
    """

    def __init__(self, shape: tuple[int, ...], k: int) -> None:
        self.buf = np.full((k, *shape), np.nan, np.float32)
        self.n = 0

    def median_count(self) -> tuple[np.ndarray, np.ndarray]:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            med = np.nanmedian(self.buf, axis=0)
        return med, np.isfinite(self.buf).sum(axis=0)

    def push(self, values: np.ndarray) -> None:
        self.buf[self.n % len(self.buf)] = values
        self.n += 1


# --------------------------------------------------------------------------
# bootstrap — circular moving blocks over SESSIONS
# --------------------------------------------------------------------------

def shift_one_session(med: np.ndarray, cnt: np.ndarray):
    """Move a trailing panel that ends at t so it ends at t-1.

    `_roll_med60` windows end at t INCLUSIVE, which is the day-grain convention;
    1.14f says every denominator ends at t-1. One shift, in one place, so the
    rule is testable instead of being an off-by-one buried in `main`.
    """
    m = np.full_like(med, np.nan)
    c = np.zeros_like(cnt)
    m[:, 1:], c[:, 1:] = med[:, :-1], cnt[:, :-1]
    return m, c


def block_weights(n_sessions: int, n_boot: int, block: int, rng) -> np.ndarray:
    """(n_boot, n_sessions) resample counts. ONE call per arm = one shared
    resampling reused by every cell, as registered."""
    n_blocks = -(-n_sessions // block)
    offs = np.arange(block)
    out = np.empty((n_boot, n_sessions))
    for b in range(n_boot):
        starts = rng.integers(0, n_sessions, size=n_blocks)
        idx = (starts[:, None] + offs[None, :]).ravel() % n_sessions
        out[b] = np.bincount(idx, minlength=n_sessions)
    return out


def boot_medians(
    values: np.ndarray, sess: np.ndarray, n_sessions: int, wmat: np.ndarray,
    chunk: int = 1024,
) -> np.ndarray:
    """Median of each bootstrap draw, EXACT and with `np.median` semantics.

    A draw re-weights whole sessions, so the draw's sample is the multiset of
    rows from the resampled sessions. Materialising that multiset 10,000 times
    is impossible at 3M rows, and a histogram approximation is not acceptable in
    a registered study, so: sort the rows once, bin them into equal-count chunks
    of the SORTED order, get each draw's weighted chunk CDF with one matmul, and
    refine exactly inside the single chunk that straddles the median. The
    weighted CDF is monotone in sorted position, so the chunking cannot move the
    answer — it only narrows the search.
    """
    n = len(values)
    if n == 0:
        return np.full(len(wmat), np.nan)
    order = np.argsort(values, kind="stable")
    v = values[order].astype(np.float64)
    s = sess[order].astype(np.int64)

    n_chunks = max(1, min(chunk, n))
    bounds = np.linspace(0, n, n_chunks + 1).astype(np.int64)
    grp = np.repeat(np.arange(n_chunks), np.diff(bounds))
    cnt = np.bincount(s * n_chunks + grp,
                      minlength=n_sessions * n_chunks
                      ).reshape(n_sessions, n_chunks).astype(np.float64)
    acc = np.cumsum(wmat @ cnt, axis=1)          # (n_boot, n_chunks)

    out = np.empty(len(wmat))
    for d in range(len(wmat)):
        total = int(acc[d, -1])
        if total <= 0:
            out[d] = np.nan
            continue
        w, a = wmat[d], acc[d]
        acc_sum = 0.0
        for r in ((total - 1) // 2, total // 2):
            g = int(np.searchsorted(a, r + 1, "left"))
            lo, hi = int(bounds[g]), int(bounds[g + 1])
            c = np.cumsum(w[s[lo:hi]]) + (a[g - 1] if g else 0.0)
            acc_sum += v[lo + int(np.searchsorted(c, r + 1, "left"))]
        out[d] = acc_sum / 2.0
    return out


def ci(draws: np.ndarray) -> tuple[float | None, float | None]:
    if not np.isfinite(draws).any():
        return None, None
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        lo, hi = np.nanpercentile(draws, [2.5, 97.5])
    return float(lo), float(hi)


def boot_share_diff(
    hit_a: np.ndarray, n_a: np.ndarray, hit_b: np.ndarray, n_b: np.ndarray,
    wmat: np.ndarray,
) -> tuple[float | None, float | None, float | None]:
    """Difference of two ratio-of-sums statistics under the same draws.

    Per-session numerator/denominator counts make this linear in the resample
    weights, so the whole battery is one matmul (1.14e's machinery).
    """
    A = np.stack([hit_a, n_a, hit_b, n_b]).astype(float)
    agg = wmat @ A.T
    with np.errstate(invalid="ignore", divide="ignore"):
        tot = A.sum(axis=1)
        point = (tot[0] / tot[1] - tot[2] / tot[3]
                 if tot[1] > 0 and tot[3] > 0 else np.nan)
        draws = agg[:, 0] / agg[:, 1] - agg[:, 2] / agg[:, 3]
    lo, hi = ci(draws)
    return (float(point) if np.isfinite(point) else None), lo, hi


def starred(lo: float | None, hi: float | None) -> bool:
    return lo is not None and hi is not None and (lo > 0 or hi < 0)


# --------------------------------------------------------------------------
# corpus / universe / gate loaders
# --------------------------------------------------------------------------

def load_universe(db: Path, fenced: bool) -> list[str]:
    from db.flow_corpus import INDEX_ROOTS
    from tools.multileg_arm_probe import ETF_SET
    fence = (set(INDEX_ROOTS) | ETF_SET) if fenced else set()
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        return sorted(r[0] for r in
                      con.execute("SELECT DISTINCT underlying FROM flow_rollup")
                      if r[0] not in fence)
    finally:
        con.close()


def load_daily(db: Path, universe: list[str]):
    """`(dates, C, V, pmax)` on the SPY calendar.

    `C`/`V` feed the registered trailing-20-session dollar-volume quintile;
    `pmax` is the day-grain fenced `premium_max` per name-day, the one rollup
    quantity the registration keeps ("denominator unchanged").
    """
    ni = {n: i for i, n in enumerate(universe)}
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        spy = {d: c for d, c in con.execute(
            "SELECT date, c FROM equity_bars WHERE symbol='SPY' "
            "AND adjustment='split'")}
        dates = sorted(spy)
        di = {d: t for t, d in enumerate(dates)}
        shape = (len(universe), len(dates))
        C, V = np.full(shape, np.nan), np.full(shape, np.nan)
        for s, d, c, v in con.execute(
                "SELECT symbol, date, c, v FROM equity_bars "
                "WHERE adjustment='split'"):
            i, t = ni.get(s), di.get(d)
            if i is not None and t is not None:
                C[i, t], V[i, t] = c, v
        pmax = np.full(shape, np.nan)
        for d, u, pm in con.execute(
                "SELECT date, underlying, MAX(premium_max) FROM flow_rollup "
                "WHERE side IN ('buy','sell') AND multileg=0 "
                "GROUP BY date, underlying"):
            i, t = ni.get(u), di.get(d)
            if i is not None and t is not None and pm is not None:
                pmax[i, t] = pm
    finally:
        con.close()
    return dates, C, V, pmax


def load_gate(db: Path, universe: list[str], dates: list[str]):
    """`(gate, known)` — EDGAR 8-K Item 2.02 filing within K_GATE sessions.

    The 1.14e gate, same construction as `tools/mover_earnings_gate.py:151-205`
    (which keeps it inline in `main()`, so there is nothing to import). Names
    with no resolved EDGAR history are UNKNOWN, not "not gated", and are
    excluded from the split with the count disclosed.
    """
    n, t_n = len(universe), len(dates)
    known = np.zeros(n, bool)
    fil = np.zeros((n, t_n), bool)
    if not db.exists():
        return np.zeros((n, t_n), bool), known
    ni = {x: i for i, x in enumerate(universe)}
    darr = np.array(dates)
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        earn: dict[str, set[str]] = {}
        for sym, d in con.execute("SELECT symbol, date FROM earnings"):
            earn.setdefault(sym, set()).add(d)
    finally:
        con.close()
    for sym, ds in earn.items():
        i = ni.get(sym)
        if i is None:
            continue
        known[i] = True
        idx = np.searchsorted(darr, np.array(sorted(ds)), "left")
        fil[i, idx[idx < t_n]] = True
    cs = np.concatenate([np.zeros((n, 1), int),
                         np.cumsum(fil.astype(int), axis=1)], axis=1)
    ts = np.arange(t_n)
    gate = (cs[:, np.minimum(ts + K_GATE + 1, t_n)] - cs[:, ts + 1]) > 0
    return gate & known[:, None], known


def load_join_subset(path: Path) -> set[str] | None:
    """Sessions clearing the >=98% condition-label join floor (Addendum F).

    `data/alpaca_conditions/join_ledger.json`, written by
    `tools/condition_label_join.py --sweep`. Nothing read it before this. A
    missing ledger disables the robustness arm rather than silently running it
    on everything.
    """
    if not path.exists():
        return None
    led = json.loads(path.read_text())
    return {d for d, r in led.items() if r.get("floor_pass")}


# --------------------------------------------------------------------------
# the session pass
# --------------------------------------------------------------------------

def tape_bins(session: date, name_index: dict[str, int], n_names: int, na: dict):
    """Per (name, 5-minute bin) fenced classified aggregates from the raw tape.

    Returns `(cls, short, bull, cnt, pmax, active)` or None when the session's
    tape or definitions are missing. The fence is `fence_and_classify` +
    `multileg_flags` imported from the corpus roller — the same code path the
    corpus itself is built from, never a local copy.
    """
    tpath = TCBBO_DIR / f"{session.isoformat()}.dbn.zst"
    if not tpath.exists():
        na["sessions_no_tape"] += 1
        return None
    defs = _load_definitions(session)
    if defs is None:
        na["sessions_no_defs"] += 1
        return None
    d_ids, d_und, uniq_und, d_right, _d_strike, d_dte = defs

    import databento as dbn
    arr = dbn.DBNStore.from_file(tpath).to_ndarray()
    if not len(arr):
        na["sessions_empty_tape"] += 1
        return None

    # the size sentinel is filtered explicitly: the corpus roller is protected
    # from it only incidentally (sentinel prints fail the definitions join)
    arr = arr[arr["size"] != UNDEF_SIZE]
    pid = arr["instrument_id"].astype(np.int64)
    idx = np.clip(np.searchsorted(d_ids, pid), 0, len(d_ids) - 1)
    hit = d_ids[idx] == pid
    na["prints_unresolved"] += int((~hit).sum())
    arr, idx = arr[hit], idx[hit]
    if not len(arr):
        na["sessions_join_empty"] += 1
        return None

    fx = fence_and_classify(arr)
    ml, _cond, _src, _jr, _cov = multileg_flags(
        session, arr["ts_event"].astype(np.int64), pid[hit],
        fx.price, arr["size"].astype(np.int64))

    b = bins_from_ns(arr["ts_event"].astype(np.int64), session)
    # the registered fenced classified basis: a real side, single-leg, in RTH
    keep = (fx.side <= 1) & (ml == 0) & (b >= 0)
    na["prints_fenced_out"] += int((~keep).sum())
    ni = np.array([name_index.get(str(u), -1) for u in uniq_und],
                  dtype=np.int64)[d_und[idx]]
    keep &= ni >= 0
    na["prints_kept"] += int(keep.sum())

    ni, b = ni[keep], b[keep]
    prem = fx.premium[keep]
    dte_b = _bucket_dte(d_dte[idx][keep])
    side = fx.side[keep]
    right = d_right[idx][keep]

    n_cells = n_names * RTH_BINS
    cell = ni * RTH_BINS + b
    cls = np.bincount(cell, weights=prem, minlength=n_cells)
    short = np.bincount(cell, weights=np.where(dte_b <= SHORT_DTE_MAX_BUCKET,
                                               prem, 0.0), minlength=n_cells)
    # bull = buy calls + sell puts, the day-grain tilt definition
    is_bull = ((side == 0) & (right == 0)) | ((side == 1) & (right == 1))
    bull = np.bincount(cell, weights=np.where(is_bull, prem, 0.0),
                       minlength=n_cells)
    cnt = np.bincount(cell, minlength=n_cells).astype(np.float64)
    pmax = group_max(cell, prem, n_cells)
    shape = (n_names, RTH_BINS)
    out = tuple(a.reshape(shape) for a in (cls, short, bull, cnt, pmax))
    active = np.zeros(n_names, bool)
    active[np.unique(ni)] = True
    return (*out, active)


def new_na_counters() -> dict[str, int]:
    return dict.fromkeys((
        "sessions_on_disk", "sessions_train", "sessions_after_train_end",
        "sessions_no_tape", "sessions_no_defs", "sessions_empty_tape",
        "sessions_join_empty", "sessions_no_bars", "sessions_analysed",
        "prints_unresolved", "prints_fenced_out", "prints_kept",
        "namedays_bars_below_coverage", "namedays_with_bars",
        "cells_ineligible_prints", "cells_no_quintile",
        "cells_na_short_dte_share", "cells_na_concentration",
        "cells_na_largest_print_ratio", "cells_na_vol_max_ratio",
        "rows_na_outcome_normalizer", "rows_na_outcome_measure",
        "rows_emitted", "rows_excluded_later_boundary",
    ), 0)


def run_sessions(sessions, universe, dates, quint, med_pmax, cnt_pmax,
                 bars_dir, na):
    """Chronological single pass: returns the row table the statistics read.

    Bounded memory by construction — one session of tape at a time, and the
    trailing state is two fixed-size ring buffers rather than a growing history.
    """
    n_names = len(universe)
    name_index = {n: i for i, n in enumerate(universe)}
    di = {d: t for t, d in enumerate(dates)}
    n_b, n_w, n_h, n_f = len(BOUNDARY_BINS), len(WINDOWS), len(HORIZONS), len(FEATS)

    vol_ring = Ring((n_names, n_b, n_w), VOL_LOOKBACK)
    out_ring = Ring((n_names, n_b, n_h), OUT_LOOKBACK)

    cols: dict[str, list[np.ndarray]] = {k: [] for k in (
        "sess", "name", "bi", "elig", "fired", "excl", "norm", "signed", "tilt")}

    for s_i, session in enumerate(sessions):
        ds = session.isoformat()
        if ds not in di:
            # a corpus session with no SPY equity bar: no quintile calendar
            # position exists for it, so it cannot be peer-celled
            na["sessions_no_bars"] += 1
            continue
        bpath = bars_dir / f"{ds}.npz"
        tape = tape_bins(session, name_index, n_names, na)
        vol_write = np.full((n_names, n_b, n_w), np.nan, np.float32)
        out_write = np.full((n_names, n_b, n_h), np.nan, np.float32)

        if tape is None or not bpath.exists():
            if tape is not None and not bpath.exists():
                na["sessions_no_bars"] += 1
            vol_ring.push(vol_write)
            out_ring.push(out_write)
            continue
        cls_b, short_b, bull_b, cnt_b, pmax_b, active = tape

        closes, expected, _n_sym = load_bars_session(bpath, name_index, n_names)
        if expected <= 0:
            na["sessions_no_bars"] += 1
            vol_ring.push(vol_write)
            out_ring.push(out_write)
            continue
        meas, signed, bars_ok = outcome_measures(closes, expected)
        na["namedays_with_bars"] += int(np.isfinite(closes).any(axis=1).sum())
        na["namedays_bars_below_coverage"] += int(
            (np.isfinite(closes).any(axis=1) & ~bars_ok).sum())

        # ---- READ the trailing state, then write it: this IS the t-1 rule ----
        vol_med, vol_cnt = vol_ring.median_count()
        out_med, out_cnt = out_ring.median_count()

        t = di[ds]
        q = quint[:, t]
        mp, cp = med_pmax[:, t], cnt_pmax[:, t]

        # normalized outcome per (name, boundary, horizon), NaN = NA
        with np.errstate(invalid="ignore", divide="ignore"):
            norm = np.where((out_cnt >= MIN_OUT_SESSIONS) & (out_med > 0)
                            & bars_ok[:, None, None], meas / out_med, np.nan)
        na["rows_na_outcome_measure"] += int(
            (~np.isfinite(meas) & bars_ok[:, None, None]).sum())
        na["rows_na_outcome_normalizer"] += int(
            (np.isfinite(meas) & ~np.isfinite(norm)).sum())

        # ---- features, firing, first-fire bookkeeping -------------------
        elig = np.zeros((n_names, n_b, n_w), bool)
        fired = np.zeros((n_names, n_b, n_w, n_f), bool)
        ever = np.zeros((n_names, n_w, n_f), bool)
        tilt = np.full((n_names, n_b), np.nan)

        for wi, W in enumerate(WINDOWS):
            for bi, bb in enumerate(BOUNDARY_BINS):
                span = window_bins(bb, W)
                if span is None:
                    continue
                ws = window_stats(cls_b, short_b, bull_b, cnt_b, pmax_b, *span)
                vol_write[:, bi, wi] = np.where(active, ws["cls"], np.nan)
                comp = components(ws, mp, cp, vol_med[:, bi, wi],
                                  vol_cnt[:, bi, wi])
                ok = ws["cnt"] >= MIN_PRINTS
                elig[:, bi, wi] = ok
                na["cells_ineligible_prints"] += int((~ok).sum())
                na["cells_no_quintile"] += int((ok & (q < 0)).sum())
                for fi, f in enumerate(FEATS):
                    vals = comp[f]
                    pool = ok & np.isfinite(vals) & (q >= 0)
                    na[f"cells_na_{f}"] += int((ok & ~np.isfinite(vals)).sum())
                    hit = fire_p90(vals, q, pool)
                    # first-fire-per-day: only the FIRST firing boundary of a
                    # (name, session, component, W) is a fired row
                    fired[:, bi, wi, fi] = hit & ~ever[:, wi, fi]
                    ever[:, wi, fi] |= hit
                if W == PRIMARY_W:
                    # window tilt, for the ONE registered direction test
                    with np.errstate(invalid="ignore", divide="ignore"):
                        tilt[:, bi] = np.where(
                            ws["cls"] > 0,
                            (2 * ws["bull"] - ws["cls"]) / ws["cls"], np.nan)

        # A name that fires anywhere in the session is excluded from the
        # complement at EVERY boundary — "never fired that session" is
        # session-wide — and from `fired` at every boundary but its first.
        excl = ever[:, None, :, :] & ~fired
        na["rows_excluded_later_boundary"] += int((excl & elig[..., None]).sum())

        out_write[:] = np.where(bars_ok[:, None, None], meas, np.nan)
        keep_rows = elig.any(axis=2) & np.isfinite(norm).any(axis=2)
        ii, bb_i = np.nonzero(keep_rows)
        if len(ii):
            cols["sess"].append(np.full(len(ii), s_i, np.int32))
            cols["name"].append(ii.astype(np.int32))
            cols["bi"].append(bb_i.astype(np.int8))
            cols["elig"].append(elig[ii, bb_i])
            cols["fired"].append(fired[ii, bb_i])
            cols["excl"].append(excl[ii, bb_i])
            cols["norm"].append(norm[ii, bb_i].astype(np.float32))
            cols["signed"].append(signed[ii, bb_i].astype(np.float32))
            cols["tilt"].append(tilt[ii, bb_i].astype(np.float32))
            na["rows_emitted"] += len(ii)
        na["sessions_analysed"] += 1

        vol_ring.push(vol_write)
        out_ring.push(out_write)
        logger.info("%s rows=%d cum=%d", ds, len(ii), na["rows_emitted"])

    return {k: (np.concatenate(v) if v else np.array([]))
            for k, v in cols.items()}


# --------------------------------------------------------------------------
# statistics
# --------------------------------------------------------------------------

def cell_rows(table, wi, fi, hi, sess_map):
    """`(values, sessions, fired_mask)` for one (W, component, horizon) cell."""
    elig = table["elig"][:, wi]
    fired = table["fired"][:, wi, fi]
    excl = table["excl"][:, wi, fi]
    val = table["norm"][:, hi]
    keep = elig & ~excl & np.isfinite(val)
    if not keep.any():
        return np.array([]), np.array([], np.int64), np.array([], bool)
    return val[keep], sess_map[table["sess"][keep]], fired[keep]


def analyse(table, sessions, arm_sessions, rng_seed):
    """The registered battery for one arm (all-train, or the >=98% join subset)."""
    keep_s = np.array([i for i, s in enumerate(sessions)
                       if s.isoformat() in arm_sessions]) \
        if arm_sessions is not None else np.arange(len(sessions))
    sess_map = np.full(len(sessions), -1, np.int64)
    sess_map[keep_s] = np.arange(len(keep_s))
    S = len(keep_s)
    if S < BLOCK:
        return {"n_sessions": S, "unavailable": "fewer sessions than the block"}
    rng = np.random.default_rng(rng_seed)
    wmat = block_weights(S, N_BOOT, BLOCK, rng)

    in_arm = sess_map[table["sess"]] >= 0
    rows = {k: v[in_arm] for k, v in table.items()}

    cells, primary = [], []
    for wi, W in enumerate(WINDOWS):
        for hi, H in enumerate(HORIZONS):
            for fi, f in enumerate(FEATS):
                v, s, fr = cell_rows(rows, wi, fi, hi, sess_map)
                if not len(v) or not fr.any() or not (~fr).any():
                    cells.append({"W": W, "H": H, "component": f,
                                  "n_fired": int(fr.sum()),
                                  "n_complement": int((~fr).sum()),
                                  "unavailable": "empty pool"})
                    continue
                mf, mc = float(np.median(v[fr])), float(np.median(v[~fr]))
                df = boot_medians(v[fr], s[fr], S, wmat)
                dc = boot_medians(v[~fr], s[~fr], S, wmat)
                lo, hi_ci = ci(df - dc)
                # secondary: tail rate at >=3x, per-session counts
                th = np.bincount(s[fr], weights=(v[fr] >= TAIL_X).astype(float),
                                 minlength=S)
                tn = np.bincount(s[fr], minlength=S).astype(float)
                ch = np.bincount(s[~fr], weights=(v[~fr] >= TAIL_X).astype(float),
                                 minlength=S)
                cn = np.bincount(s[~fr], minlength=S).astype(float)
                tp, tlo, thi = boot_share_diff(th, tn, ch, cn, wmat)
                row = {
                    "W": W, "H": H, "component": f,
                    "median_fired": round(mf, 5),
                    "median_complement": round(mc, 5),
                    "diff": round(mf - mc, 5),
                    "ci": [None if lo is None else round(lo, 5),
                           None if hi_ci is None else round(hi_ci, 5)],
                    "star": starred(lo, hi_ci),
                    "n_fired": int(fr.sum()), "n_complement": int((~fr).sum()),
                    "n_sessions_fired": int((tn > 0).sum()),
                    "tail_rate_diff_3x": None if tp is None else round(tp, 5),
                    "tail_ci": [None if tlo is None else round(tlo, 5),
                                None if thi is None else round(thi, 5)],
                    "primary_cell": (W == PRIMARY_W and H == PRIMARY_H),
                }
                cells.append(row)
                if row["primary_cell"]:
                    primary.append(row)

    # the ONE registered direction test: window-tilt sign vs signed H=2h return
    direction = []
    wi, hi = WINDOWS.index(PRIMARY_W), HORIZONS.index(PRIMARY_H)
    for fi, f in enumerate(FEATS):
        elig = rows["elig"][:, wi]
        keep = (elig & rows["fired"][:, wi, fi] & ~rows["excl"][:, wi, fi]
                & np.isfinite(rows["signed"][:, hi]) & np.isfinite(rows["tilt"])
                & (rows["tilt"] != 0) & (rows["signed"][:, hi] != 0))
        if not keep.any():
            direction.append({"component": f, "n": 0, "unavailable": "empty"})
            continue
        s = sess_map[rows["sess"][keep]]
        agree = (np.sign(rows["tilt"][keep])
                 == np.sign(rows["signed"][keep, hi])).astype(float)
        h = np.bincount(s, weights=agree, minlength=S)
        n = np.bincount(s, minlength=S).astype(float)
        half_h, half_n = n * 0.5, n
        p, lo, hi_ci = boot_share_diff(h, n, half_h, half_n, wmat)
        direction.append({
            "component": f, "n": int(keep.sum()),
            "agreement_minus_half": None if p is None else round(p, 5),
            "ci": [None if lo is None else round(lo, 5),
                   None if hi_ci is None else round(hi_ci, 5)],
            "star": starred(lo, hi_ci)})

    # ambiguity diagnostic: complement restricted to each name's FIRST eligible
    # boundary (reading B). No authority — it exists so the reader can see
    # whether the boundary composition is carrying the primary number.
    diag = []
    n_names_max = int(rows["name"].max()) + 1 if len(rows["name"]) else 1
    for fi, f in enumerate(FEATS):
        keep = (rows["elig"][:, wi] & ~rows["excl"][:, wi, fi]
                & np.isfinite(rows["norm"][:, hi]))
        # only meaningful next to a real fired pool: a component that never fired
        # has no complement to contrast, and printing one would read as a result
        if not keep.any() or not (keep & rows["fired"][:, wi, fi]).any():
            continue
        key = (rows["sess"][keep].astype(np.int64) * n_names_max
               + rows["name"][keep])
        ordv = np.lexsort((rows["bi"][keep], key))
        ks = key[ordv]
        firstb = np.zeros(len(key), bool)
        firstb[ordv[np.flatnonzero(np.r_[True, ks[1:] != ks[:-1]])]] = True
        sel = firstb & ~rows["fired"][keep][:, wi, fi]
        if sel.any():
            diag.append({"component": f,
                         "complement_first_boundary_only_median":
                             round(float(np.median(
                                 rows["norm"][keep][:, hi][sel])), 5),
                         "n": int(sel.sum())})

    return {"n_sessions": S, "cells": cells, "primary": primary,
            "direction_test": direction,
            "complement_first_boundary_only": diag}


# --------------------------------------------------------------------------

def reading_rule(primary, cells) -> dict:
    """(a) primary CI clear of zero, (b) same-direction star at >=2 of 3
    horizons for W=60, (c) replication star at W=30 or W=120. Reported as three
    booleans per component — the registration's rule, not a verdict."""
    out = {}
    for row in primary:
        f = row["component"]
        sign = np.sign(row.get("diff") or 0.0)
        same = [c for c in cells if c["component"] == f and c["W"] == PRIMARY_W
                and c.get("star") and np.sign(c.get("diff") or 0.0) == sign]
        rep = [c for c in cells if c["component"] == f and c["W"] != PRIMARY_W
               and c["H"] == PRIMARY_H and c.get("star")
               and np.sign(c.get("diff") or 0.0) == sign]
        out[f] = {"a_primary_clear_of_zero": bool(row.get("star")),
                  "b_horizons_starred_same_direction": len(same),
                  "b_pass": len(same) >= 2,
                  "c_replication_other_window": bool(rep),
                  "all_three": bool(row.get("star")) and len(same) >= 2 and bool(rep)}
    return out


def earnings_split(table, sessions, gate, dates) -> list[dict]:
    """Every starred finding is reported split by the 1.14e earnings gate.

    A finding that collapses to the earnings-adjacent split is the calendar
    wearing a flow costume, so the split is computed for the primary cell
    unconditionally rather than only when something stars.
    """
    di = {d: t for t, d in enumerate(dates)}
    tcol = np.array([di.get(s.isoformat(), -1) for s in sessions])[table["sess"]]
    wi, hi = WINDOWS.index(PRIMARY_W), HORIZONS.index(PRIMARY_H)
    g = np.zeros(len(tcol), bool)
    ok = tcol >= 0
    g[ok] = gate[table["name"][ok], tcol[ok]]
    out = []
    for fi, f in enumerate(FEATS):
        elig = table["elig"][:, wi]
        keep = elig & ~table["excl"][:, wi, fi] & np.isfinite(table["norm"][:, hi])
        fr = table["fired"][:, wi, fi]
        row = {"component": f}
        for lab, m in (("gated", g), ("far", ~g)):
            kf, kc = keep & fr & m, keep & ~fr & m
            row[lab] = {
                "median_fired": (round(float(np.median(table["norm"][kf, hi])), 5)
                                 if kf.any() else None),
                "median_complement": (
                    round(float(np.median(table["norm"][kc, hi])), 5)
                    if kc.any() else None),
                "n_fired": int(kf.sum()), "n_complement": int(kc.sum())}
        out.append(row)
    return out


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--corpus-db", default=str(CORPUS_DB))
    p.add_argument("--bars-dir", default=str(BARS_DIR))
    p.add_argument("--earnings-db", default=str(EARN_DB))
    p.add_argument("--join-ledger", default=str(JOIN_LEDGER))
    p.add_argument("--out", default=str(OUT))
    p.add_argument("--train-end", default=TRAIN_END,
                   help="analysis mask; sessions after this are never read")
    p.add_argument("--limit-sessions", type=int, default=None,
                   help="first N train sessions (smoke only)")
    p.add_argument("--no-universe-fence", action="store_true",
                   help="keep index/ETF roots (see AMBIGUITIES)")
    a = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    db, bars_dir = Path(a.corpus_db), Path(a.bars_dir)
    na = new_na_counters()
    universe = load_universe(db, not a.no_universe_fence)
    dates, C, V, pmax = load_daily(db, universe)

    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    all_sessions = sorted(date.fromisoformat(r[0]) for r in
                          con.execute("SELECT date FROM corpus_sessions"))
    con.close()
    na["sessions_on_disk"] = len(all_sessions)
    # THE analysis mask. The test tail may sit on disk; it is dropped here and
    # nothing downstream can reach it.
    sessions = [s for s in all_sessions if s.isoformat() <= a.train_end]
    na["sessions_after_train_end"] = len(all_sessions) - len(sessions)
    if a.limit_sessions:
        sessions = sessions[: a.limit_sessions]
    na["sessions_train"] = len(sessions)

    quint = _tier_quintiles(C, V)
    # the day-grain normalizer, shifted one session: "denominators end at t-1"
    med_pmax, cnt_pmax = shift_one_session(*_roll_med60(pmax))

    logger.info("universe=%d sessions=%d (of %d on disk; train_end=%s)",
                len(universe), len(sessions), len(all_sessions), a.train_end)
    table = run_sessions(sessions, universe, dates, quint, med_pmax, cnt_pmax,
                         bars_dir, na)
    if not len(table["sess"]):
        logger.warning("no rows emitted — nothing to analyse")

    gate, known = load_gate(Path(a.earnings_db), universe, dates)
    na["names_no_edgar_history"] = int((~known).sum())

    subset = load_join_subset(Path(a.join_ledger))
    arms = {"all_train": None}
    if subset is None:
        logger.warning("no join_ledger.json — robustness arm unavailable")
    else:
        arms["join_ge_98"] = subset

    results = {}
    for arm, arm_sessions in arms.items():
        results[arm] = (analyse(table, sessions, arm_sessions, SEED)
                        if len(table["sess"]) else {"unavailable": "no rows"})
        if "cells" in results[arm]:
            results[arm]["reading_rule"] = reading_rule(
                results[arm]["primary"], results[arm]["cells"])

    split = (earnings_split(table, sessions, gate, dates)
             if len(table["sess"]) else [])

    payload = {
        "meta": {
            "registered": "2026-07-30", "roadmap": "FLOW_ROADMAP.md 1.14f",
            "not_a_backtest": "distributions only — no entry/exit/P&L/hit-rate",
            "seed": SEED, "n_boot": N_BOOT,
            "bootstrap": f"circular moving-block over SESSIONS, block={BLOCK}, "
                         f"one shared resampling per arm",
            "train_end": a.train_end,
            "boundaries_et": list(BOUNDARY_LABELS),
            "windows_min": list(WINDOWS), "horizons": list(HORIZONS),
            "primary_cell": {"W": PRIMARY_W, "H": PRIMARY_H},
            "eligibility_min_prints": MIN_PRINTS,
            "peer_cell": "session x boundary-of-day x trailing-20-session "
                         "equity dollar-volume quintile",
            "firing": f"component >= p{int(RANK_PCT)} within the peer cell",
            "components": list(FEATS),
            "universe_size": len(universe),
            "universe_fenced": not a.no_universe_fence,
            "sessions_analysed": na["sessions_analysed"],
            "ambiguities": AMBIGUITIES,
        },
        "arms": results,
        "earnings_split_primary_cell": split,
        "na_accounting": na,
    }
    outp = Path(a.out)
    outp.parent.mkdir(parents=True, exist_ok=True)
    outp.write_text(json.dumps(payload, indent=1))

    _print_tables(payload)
    print(f"\nwrote {outp}")
    return 0


def _print_tables(payload: dict) -> None:
    m = payload["meta"]
    print(f"\n== 1.14f intraday screen — {m['sessions_analysed']} sessions, "
          f"{m['universe_size']} names, train_end={m['train_end']} ==")
    print("NOT a backtest: medians of normalized moves, fired vs complement.")
    for arm, res in payload["arms"].items():
        print(f"\n---- arm: {arm} ({res.get('n_sessions', 0)} sessions) ----")
        if "cells" not in res:
            print(f"  unavailable: {res.get('unavailable')}")
            continue
        print(f"\n  == PRIMARY (W={m['primary_cell']['W']}, "
              f"H={m['primary_cell']['H']}) — 4 intervals ==")
        for r in res["primary"]:
            _print_cell(r)
        print("\n  == secondary battery (no survival authority) ==")
        for r in res["cells"]:
            if not r.get("primary_cell"):
                _print_cell(r, indent=4)
        print("\n  == reading rule (a)(b)(c) ==")
        for f, rr in res.get("reading_rule", {}).items():
            print(f"    {f:<22} a={rr['a_primary_clear_of_zero']} "
                  f"b={rr['b_horizons_starred_same_direction']}/3 "
                  f"({rr['b_pass']}) c={rr['c_replication_other_window']} "
                  f"=> all_three={rr['all_three']}")
        print("\n  == direction test (window tilt vs signed 2h return) ==")
        for d in res.get("direction_test", []):
            print(f"    {d['component']:<22} n={d['n']} "
                  f"agree-0.5={d.get('agreement_minus_half')} "
                  f"ci={d.get('ci')} star={d.get('star')}")
        for d in res.get("complement_first_boundary_only", []):
            print(f"    [diagnostic, no authority] {d['component']:<22} "
                  f"complement median (first eligible boundary only)="
                  f"{d['complement_first_boundary_only_median']} n={d['n']}")
    print("\n== earnings-gate split of the primary cell (1.14e gate) ==")
    for r in payload["earnings_split_primary_cell"]:
        for lab in ("gated", "far"):
            g = r[lab]
            print(f"  {r['component']:<22} {lab:<6} "
                  f"fired={g['median_fired']} (n={g['n_fired']}) "
                  f"complement={g['median_complement']} (n={g['n_complement']})")
    print("\n== NA accounting (nothing is silently dropped) ==")
    for k, v in payload["na_accounting"].items():
        print(f"  {k:<34} {v:,}")


def _print_cell(r: dict, indent: int = 4) -> None:
    pad = " " * indent
    if "unavailable" in r:
        print(f"{pad}{r['component']:<22} W={r['W']:>3} H={r['H']:<5} "
              f"unavailable ({r['unavailable']})")
        return
    lo, hi = r["ci"]
    print(f"{pad}{r['component']:<22} W={r['W']:>3} H={r['H']:<5} "
          f"fired={r['median_fired']} (n={r['n_fired']}) "
          f"comp={r['median_complement']} (n={r['n_complement']}) "
          f"diff={r['diff']} [{lo},{hi}]{' *' if r['star'] else ''} "
          f"tail3x={r['tail_rate_diff_3x']}")


if __name__ == "__main__":
    raise SystemExit(main())
