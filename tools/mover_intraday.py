"""Roadmap 1.14f: the intraday screen — the grain descent.

PRE-REGISTERED 2026-07-30 (`References/FLOW_ROADMAP.md` §1.14f) BEFORE any
intraday outcome existed on disk. This file implements that registration with
EVERY interpretive fork declared in `AMBIGUITIES` below — it does not get to
improve the registration, and it does not get to pretend the registered text was
unambiguous where it was not. 1.14c/d/e closed the day-grain arc — the
fingerprint describes big-move windows without selecting them, and the one
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
THIN_NORMALIZER = 0.0005   # 5 bps: below this, ">=3x" is reachable on noise
DIST_PCTL = (25, 50, 75, 90)   # "report the DISTRIBUTION, never a hit rate"

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
        "the DEFAULT run uses the REGISTERED universe: every opra_defs "
        "underlying with >=1 RTH tcbbo print, with NO index/ETF exclusion. The "
        "registration's outcome-store bullet defines the universe that way and "
        "never mentions the day-grain arc's fence, so applying that fence by "
        "default (as an earlier revision of this tool did, inheriting "
        "1.14c/d/e) was a deviation, not an interpretation. ETFs are IN; "
        "cash-index roots enter and NA out naturally, having no equity bars. "
        "--universe-fence runs the fenced variant, which is a NON-REGISTERED "
        "SECONDARY arm and is labelled as such in the output. This is not a "
        "cosmetic choice: the peer cell is a trailing dollar-volume QUINTILE, "
        "so removing the highest-volume names in the corpus moves the "
        "cut-points for every name that remains, and therefore changes which "
        "names fire — the two universes are not nested results."),
    "robustness_arm_normalizer_scope": (
        "the >=98%-join robustness arm filters analysis ROWS only. The trailing "
        "state — the vol_max_ratio (boundary, W) window-premium ring, the "
        "outcome (b, H) ring, and the trailing-60-session daily premium_max "
        "median — is built from ALL train sessions, including below-floor-join "
        "days. Kept deliberately: rebuilding the rings over only the 107 "
        "in-subset sessions would thin every history against its registered "
        "minimum (>=10 valid sessions for vol_max_ratio and the outcome "
        "normalizer, >=20 valid days for premium_max), so a large share of rows "
        "would go NA and the arm would stop being a robustness check on the "
        "same measurements and become a different, weaker study. The arm "
        "therefore tests whether the label provenance of the FEATURE rows "
        "matters, not whether it matters to the normalizers."),
    "half_day_close_horizon": (
        "H=close is a variable-length horizon by construction, and on an "
        "early-close session it spans ~3.5h where a full session spans ~6.5h. "
        "Both feed the SAME trailing (b, H=close) normalizer ring, so an "
        "early-close session's measure is normalized against a median that is "
        "mostly full-length sessions (and contributes to later ones). Kept: "
        "the registration defines H=close as 'to the close' and says the "
        "measure runs over the bars that exist, and early closes are ~1-2% of "
        "sessions. Disclosed instead of corrected — `sessions_early_close` "
        "counts them, `early_close_sessions` lists each one with its detected "
        "last bin so the detection can be checked against the exchange's real "
        "early-close calendar, and every such session is logged at INFO. H=30m "
        "and H=2h are unaffected: a fixed-length horizon that would overrun the "
        "detected close is NA, never truncated."),
    "outcome_base_price": (
        "'cumulative 5-min log return from b' is measured against the close of "
        "the last bar strictly before b (the price AT b), so the outcome shares "
        "no bar with the feature window."),
    "half_day_sessions": (
        "the session's real bar grid is derived from where the CROSS-SECTIONAL "
        "per-bin symbol count collapses (last bin holding >=50% of the median "
        "per-bin count), not from the count of distinct timestamps. Measured on "
        "2025-11-28 and 2025-12-24 (13:00 closes): ~500 names carry "
        "late-reported bars through 15:55, so a distinct-timestamp grid "
        "saturates at 78, every ordinary name reads ~55% coverage and fails the "
        "80% rule, and the only survivors are those anomalous names — the NA "
        "rule inverts into a selection filter. A horizon that would run past "
        "the derived close is NA, not truncated: truncating would pool a "
        "30-minute measure with real 2-hour ones under the label H=2h and make "
        "the trailing (b,H) normalizer a mixture of two measures."),
    "reading_rule_c_horizon": (
        "reading rule (c) 'replication star at W=30 or W=120' does not name a "
        "horizon; it is evaluated at the PRIMARY horizon (H=2h) only, the "
        "tighter reading — allowing any horizon would give (c) 6 chances "
        "instead of 2 and weaken the rule it exists to tighten."),
    "direction_test_count": (
        "the registration says 'ONE direction test ... at (W=60) for fired "
        "rows' without naming a component, so it is reported once PER "
        "COMPONENT (4 tests) because 'fired rows' is only defined per "
        "component. Rows with zero window tilt or exactly zero signed return "
        "are dropped rather than scored as agreement or disagreement (a sign of "
        "0 is neither); the dropped count is reported."),
    "complement_uses_end_of_day_information": (
        "The complement under the registered 'never fired that session' "
        "definition is selected using end-of-day information: early-boundary "
        "complement rows come from names quiet for the REST of the day, a "
        "systematically calmer population than a contemporaneous non-fire set."),
    "fired_to_complement_row_ratio": (
        "The primary contrast pools 1 fired row per name-day against up to 11 "
        "complement rows per quiet name-day; per-(b,H) normalization removes "
        "level effects but not composition — see by_boundary counts and the "
        "reading-B diagnostic."),
    "universe_is_the_corpus": (
        "the universe is every underlying in `flow_rollup` (i.e. observed "
        "anywhere across the corpus sessions) — the registered universe, no "
        "fence, NOT a per-session defs universe. Under --universe-fence (a "
        "NON-REGISTERED arm) index/ETF roots are removed. A name enters the "
        "peer cell on a session only via the eligibility and quintile gates."),
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


def horizon_end_bin(boundary_bin: int, horizon: str,
                    last_bin: int = RTH_BINS) -> int | None:
    """Exclusive bin bound for the outcome window, or None if b is ineligible.

    Mechanical, exactly as registered: H=2h needs b <= 14:00 (bin 54, whose 2
    hours end precisely at 16:00); H=30m and H=close need b <= 15:30.

    `last_bin` is the session's real bar grid (78 on an ordinary day, 42 on a
    13:00 half day — see `session_last_bin`). A horizon that would run past the
    session's own close is NA, not truncated: truncating would quietly turn
    "H=2h" into a 30-minute measure for some rows and pool it with real 2-hour
    ones, and would make the trailing (b, H) normalizer a mix of two different
    measures. The registration's "the measure runs over the bars that exist" is
    about halt/LULD gaps INSIDE a window, which is handled by the NaN-tolerant
    reductions in `outcome_measures`.
    """
    span = H_BINS[horizon]
    end = last_bin if span is None else boundary_bin + span
    return end if end <= last_bin else None


def _rth_open_ns(session: date) -> int:
    """Epoch ns of 09:30 ET — zoneinfo, so the corpus's DST changes are right."""
    return int(datetime.combine(session, RTH_OPEN, ET).timestamp()) * 1_000_000_000


def bins_from_ns(ts_ns: np.ndarray, session: date) -> np.ndarray:
    """5-minute RTH bin index per timestamp; -1 outside [09:30, 16:00) ET."""
    rel = (ts_ns - _rth_open_ns(session)) // BIN_NS
    return np.where((rel >= 0) & (rel < RTH_BINS), rel, -1).astype(np.int64)


def session_last_bin(bins: np.ndarray) -> int:
    """The session's real bar grid, from where the CROSS-SECTION collapses.

    Counting distinct timestamps does not work, and the failure is not
    hypothetical: measured on 2025-11-28 and 2025-12-24 (13:00 closes) roughly
    500 names carry late-reported prints in every bin through 15:55, so a
    distinct-timestamp count saturates at 78. Every ordinary name then reads
    ~55% bar coverage, fails the 80% rule, and the only survivors are exactly
    those few anomalous post-close-tail names — the NA rule would have inverted
    into a selection filter that keeps the weirdest names on the weirdest days.

    So the grid is read from the per-bin SYMBOL count: real RTH bins carry the
    whole cross-section, post-close stragglers carry a small tail. The cut is
    half the median per-bin count over populated bins.
    """
    ok = bins[bins >= 0]
    if not len(ok):
        return 0
    counts = np.bincount(ok, minlength=RTH_BINS)
    populated = counts[counts > 0]
    thresh = 0.5 * float(np.median(populated))
    real = np.flatnonzero(counts >= thresh)
    return int(real[-1]) + 1 if len(real) else 0


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
    closes: np.ndarray, last_bin: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """`(max_abs, signed_terminal, bars_ok)` per (name, boundary, horizon).

    `closes` is (n_names, RTH_BINS) with NaN for absent bars. The measure is the
    max |cumulative 5-minute log return| from b, against the close of the last
    bar strictly before b — so the outcome shares no bar with the trailing
    feature window. max|x| over a set is max(max(x), -min(x)), which turns the
    whole thing into two range reductions per (b, H) instead of a per-name loop.

    `last_bin` is the session's real grid (`session_last_bin`). It does two jobs:
    it is the denominator of the registered <80%-of-expected-bars rule, and it
    bounds every horizon. Bars at or after it are post-close stragglers and are
    excluded from BOTH — counting them would let a handful of late-reporting
    names look like the only well-covered names on a half day.

    Halt/LULD gaps inside a kept name-day are fine: the reductions are
    NaN-tolerant, so the measure runs over the bars that exist.
    """
    n = closes.shape[0]
    n_b, n_h = len(BOUNDARY_BINS), len(HORIZONS)
    bars_ok = (np.isfinite(closes[:, :last_bin]).sum(axis=1)
               >= BAR_COVERAGE * last_bin) if last_bin > 0 \
        else np.zeros(n, bool)

    with np.errstate(invalid="ignore", divide="ignore"):
        logp = np.log(np.where(closes > 0, closes, np.nan))
    logp[:, last_bin:] = np.nan          # post-close stragglers never measured

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
        if bb >= last_bin:
            continue                     # the boundary itself is past the close
        base = ff[:, bb - 1]
        for hi, h in enumerate(HORIZONS):
            end = horizon_end_bin(bb, h, last_bin)
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
    """`(closes, last_bin, n_symbols)` from one outcome-store npz.

    READ-ONLY consumer of the layout `tools/equity_bars_5min_backfill.py`
    writes: a sorted `symbols` index table plus parallel `t_ns`/`open`/`high`/
    `low`/`close`/`volume`/`sym_idx` arrays. Rows for symbols outside the
    analysis universe are dropped here rather than filtered downstream.

    `last_bin` comes from `session_last_bin` over the WHOLE file, not just the
    universe rows: the wider the cross-section, the sharper the collapse that
    marks an early close.
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
    last_bin = session_last_bin(b)
    mapped = np.array([name_index.get(str(s), -1) for s in symbols], dtype=np.int64)
    ni = mapped[sym_idx]
    keep = (ni >= 0) & (b >= 0)
    closes[ni[keep], b[keep]] = close[keep]
    return closes, last_bin, int((mapped >= 0).sum())


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
        # truncate to EXACTLY n_sessions: ceil(S/block) whole blocks overshoot by
        # up to block-1 when S is not divisible, and a resample larger than the
        # sample narrows every interval built from it
        idx = (starts[:, None] + offs[None, :]).ravel()[:n_sessions] % n_sessions
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

def load_universe(db: Path, fenced: bool = False) -> list[str]:
    """The analysis universe. `fenced=False` is the REGISTERED default.

    The registration's universe is every `opra_defs` underlying with >=1 RTH
    print — index and ETF roots included. `fenced=True` drops
    `INDEX_ROOTS | ETF_SET` (what the day-grain arc used) and produces a
    non-registered secondary arm; see AMBIGUITIES['universe_fence'] for why the
    two are not nested results.
    """
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
    # The size sentinel is dropped HERE, after the flags, not before the join:
    # `multileg_flags` keys on (ts_event, instrument_id) collisions, so it must
    # see exactly the row set `rollup_session` sees or a package could lose a leg
    # and change a sibling print's flag. The roller never filters sentinels at
    # all — they are excluded incidentally by the definitions join — so dropping
    # them after the flags is what keeps the two row sets identical.
    sentinel = arr["size"] == UNDEF_SIZE
    # the registered fenced classified basis: a real side, single-leg, in RTH
    fenced = (fx.side <= 1) & (ml == 0) & (b >= 0) & ~sentinel
    na["prints_fenced_out"] += int((~fenced).sum())
    ni = np.array([name_index.get(str(u), -1) for u in uniq_und],
                  dtype=np.int64)[d_und[idx]]
    # prints that clear the fence but whose underlying is outside the analysis
    # universe (names absent from flow_rollup; under --universe-fence also the
    # removed index/ETF roots) are their OWN bucket: pooling them into
    # `prints_fenced_out` would hide them under a heading that claims nothing
    # is silently dropped
    in_universe = ni >= 0
    na["prints_outside_universe"] += int((fenced & ~in_universe).sum())
    keep = fenced & in_universe
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
    """Every counter the run discloses. `cells_*` are over TAPE-ACTIVE names only
    (see NA_NOTE) so they cannot be misread as data loss."""
    return dict.fromkeys((
        "sessions_on_disk", "sessions_train", "sessions_after_train_end",
        "sessions_no_tape", "sessions_no_defs", "sessions_empty_tape",
        "sessions_join_empty", "sessions_no_calendar_position",
        "sessions_no_bar_file", "sessions_empty_bar_file", "sessions_analysed",
        "sessions_early_close", "prints_unresolved", "prints_fenced_out",
        "prints_outside_universe", "prints_kept",
        "namedays_bars_below_coverage", "namedays_with_bars",
        "cells_ineligible_prints", "cells_no_quintile",
        "cells_na_short_dte_share", "cells_na_concentration",
        "cells_na_largest_print_ratio", "cells_na_vol_max_ratio",
        "rows_na_outcome_normalizer", "rows_na_outcome_measure",
        "rows_normalizer_below_5bp", "rows_emitted",
        "rows_excluded_later_boundary",
    ), 0)


# Caveats on the registered definitions — NOT defects, and not fixable without
# changing a registered measure. They ship in the run log verbatim so a reader
# reaches them at the same time as the numbers.
RUN_NOTES = (
    "The complement under the registered 'never fired that session' definition "
    "is selected using end-of-day information: early-boundary complement rows "
    "come from names quiet for the REST of the day, a systematically calmer "
    "population than a contemporaneous non-fire set.",
    "The primary contrast pools 1 fired row per name-day against up to 11 "
    "complement rows per quiet name-day; per-(b,H) normalization removes level "
    "effects but not composition — see by_boundary counts and the reading-B "
    "diagnostic.",
)

NA_NOTE = (
    "cells_* count (name, boundary, W) cells for names with >=1 fenced "
    "classified print in that session only. Counting the full universe grid "
    "would measure how many of ~7,000 corpus roots are illiquid at any given "
    "half hour, not any loss of data this study could have used. "
    "prints_fenced_out and prints_outside_universe are disjoint, and together "
    "with prints_kept and prints_unresolved they account for every print on the "
    "tape. sessions_no_* are disjoint causes, split so a bar-store gap cannot "
    "be mistaken for a tape gap."
)


def run_sessions(sessions, universe, dates, quint, med_pmax, cnt_pmax,
                 bars_dir, na, early_close=None):
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
        "sess", "name", "bi", "pool", "fired", "excl", "norm", "signed",
        "tilt", "wcls")}

    for s_i, session in enumerate(sessions):
        ds = session.isoformat()
        vol_write = np.full((n_names, n_b, n_w), np.nan, np.float32)
        out_write = np.full((n_names, n_b, n_h), np.nan, np.float32)

        # Every `continue` below MUST push both rings. A skipped push does not
        # skip a session, it silently stretches "the trailing 20 sessions" across
        # the gap — the slot has to exist and be NaN.
        if ds not in di:
            # a corpus session with no SPY equity bar: no quintile calendar
            # position exists for it, so it cannot be peer-celled
            na["sessions_no_calendar_position"] += 1
            vol_ring.push(vol_write)
            out_ring.push(out_write)
            continue
        tape = tape_bins(session, name_index, n_names, na)
        if tape is None:
            vol_ring.push(vol_write)
            out_ring.push(out_write)
            continue
        cls_b, short_b, bull_b, cnt_b, pmax_b, active = tape

        # ---- window aggregates first: they need the TAPE only ---------------
        # The vol_max_ratio normalizer is "the same (boundary, W) window
        # premium", which is computable whenever the tape exists. Deriving it
        # only on sessions that also have equity bars would put bar-store gaps
        # into a flow denominator and quietly contradict the disclosed
        # definition, so the ring is filled here, before the bars gate.
        ws_cache: dict[tuple[int, int], dict[str, np.ndarray]] = {}
        for wi, W in enumerate(WINDOWS):
            for bi, bb in enumerate(BOUNDARY_BINS):
                span = window_bins(bb, W)
                if span is None:
                    continue
                ws = window_stats(cls_b, short_b, bull_b, cnt_b, pmax_b, *span)
                ws_cache[(bi, wi)] = ws
                vol_write[:, bi, wi] = np.where(active, ws["cls"], np.nan)

        # ---- READ the trailing state, then write it: this IS the t-1 rule ----
        vol_med, vol_cnt = vol_ring.median_count()
        out_med, out_cnt = out_ring.median_count()

        bpath = bars_dir / f"{ds}.npz"
        if not bpath.exists():
            na["sessions_no_bar_file"] += 1
            vol_ring.push(vol_write)
            out_ring.push(out_write)
            continue
        closes, last_bin, _n_sym = load_bars_session(bpath, name_index, n_names)
        if last_bin <= 0:
            na["sessions_empty_bar_file"] += 1
            vol_ring.push(vol_write)
            out_ring.push(out_write)
            continue
        if last_bin < RTH_BINS:
            # Logged AND listed, not just counted: the detection is heuristic
            # (a cross-sectional collapse), so a reader has to be able to check
            # the detected list against the exchange's real early-close calendar.
            na["sessions_early_close"] += 1
            if early_close is not None:
                early_close[ds] = last_bin
            end_min = OPEN_MIN + last_bin * BAR_MIN
            logger.info("%s EARLY CLOSE detected: last_bin=%d (grid ends "
                        "%02d:%02d ET) — verify against the exchange calendar",
                        ds, last_bin, end_min // 60, end_min % 60)
        meas, signed, bars_ok = outcome_measures(closes, last_bin)
        has_bars = np.isfinite(closes[:, :last_bin]).any(axis=1)
        na["namedays_with_bars"] += int(has_bars.sum())
        na["namedays_bars_below_coverage"] += int((has_bars & ~bars_ok).sum())

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
        # a normalizer this small makes the ">=3x" tail threshold reachable on
        # noise; reported, never floored (that would change a registered measure)
        na["rows_normalizer_below_5bp"] += int(
            (np.isfinite(norm) & (out_med < THIN_NORMALIZER)).sum())

        # ---- features, firing, first-fire bookkeeping -------------------
        pool_a = np.zeros((n_names, n_b, n_w, n_f), bool)
        fired = np.zeros((n_names, n_b, n_w, n_f), bool)
        ever = np.zeros((n_names, n_w, n_f), bool)
        tilt = np.full((n_names, n_b), np.nan)
        wcls = np.full((n_names, n_b, n_w), np.nan)

        for wi, W in enumerate(WINDOWS):
            for bi, _bb in enumerate(BOUNDARY_BINS):
                ws = ws_cache.get((bi, wi))
                if ws is None:
                    continue
                comp = components(ws, mp, cp, vol_med[:, bi, wi],
                                  vol_cnt[:, bi, wi])
                ok = ws["cnt"] >= MIN_PRINTS
                wcls[:, bi, wi] = ws["cls"]
                # cells_* are counted over tape-active names only: over the full
                # ~7,000-root grid they would measure universe illiquidity
                na["cells_ineligible_prints"] += int((~ok & active).sum())
                na["cells_no_quintile"] += int((ok & active & (q < 0)).sum())
                for fi, f in enumerate(FEATS):
                    vals = comp[f]
                    # THE firing pool: eligible, component defined, peer-celled.
                    # A row outside it can never fire, so it is not a complement
                    # either — 1.14d's `unfired = ~fr & pool`, not `~fr & elig`.
                    pool = ok & np.isfinite(vals) & (q >= 0)
                    pool_a[:, bi, wi, fi] = pool
                    na[f"cells_na_{f}"] += int(
                        (ok & active & ~np.isfinite(vals)).sum())
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
        na["rows_excluded_later_boundary"] += int((excl & pool_a).sum())

        out_write[:] = np.where(bars_ok[:, None, None], meas, np.nan)
        keep_rows = pool_a.any(axis=(2, 3)) & np.isfinite(norm).any(axis=2)
        ii, bb_i = np.nonzero(keep_rows)
        if len(ii):
            cols["sess"].append(np.full(len(ii), s_i, np.int32))
            cols["name"].append(ii.astype(np.int32))
            cols["bi"].append(bb_i.astype(np.int8))
            cols["pool"].append(pool_a[ii, bb_i])
            cols["fired"].append(fired[ii, bb_i])
            cols["excl"].append(excl[ii, bb_i])
            cols["norm"].append(norm[ii, bb_i].astype(np.float32))
            cols["signed"].append(signed[ii, bb_i].astype(np.float32))
            cols["tilt"].append(tilt[ii, bb_i].astype(np.float32))
            cols["wcls"].append(wcls[ii, bb_i].astype(np.float32))
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

def cell_mask(table, wi, fi, hi):
    """Rows in one (W, component, horizon) cell's FIRING POOL.

    `pool`, never `elig`. A row whose component is NA (no normalizer history
    yet) or whose name has no liquidity quintile could not have fired however
    extreme the tape was, so counting it as "did not fire" pollutes the
    complement with rows that were never at risk. Measured consequence of
    getting this wrong: `largest_print_ratio` needs a 60-session denominator, so
    the corpus's first ~20 sessions contribute complement-only rows and shift the
    comparison. 1.14d's rule is `fired = fr & pool` / `unfired = ~fr & pool`
    (`tools/mover_earnings_gate.py:241-242`); this is that rule at the new grain.
    """
    return (table["pool"][:, wi, fi] & ~table["excl"][:, wi, fi]
            & np.isfinite(table["norm"][:, hi]))


def cell_rows(table, wi, fi, hi, sess_map):
    """`(values, sessions, fired_mask)` for one (W, component, horizon) cell."""
    keep = cell_mask(table, wi, fi, hi)
    if not keep.any():
        return np.array([]), np.array([], np.int64), np.array([], bool)
    return (table["norm"][keep, hi], sess_map[table["sess"][keep]],
            table["fired"][keep][:, wi, fi])


def _pctl(vals: np.ndarray) -> dict[str, float] | None:
    """The registration demands the DISTRIBUTION, not just a median: a setup that
    is usually a small move and occasionally a huge one is invisible in a median
    and is exactly what this phase hunts."""
    if not len(vals):
        return None
    qs = np.percentile(vals, DIST_PCTL)
    return {f"p{p}": round(float(v), 5) for p, v in zip(DIST_PCTL, qs)}


def analyse(table, sessions, arm_sessions, rng_seed, gate=None, known=None,
            dates=None):
    """The registered battery for one arm (all-train, or the >=98% join subset).

    `gate`/`known`/`dates` are optional only so the geometry can be exercised
    without an earnings DB; when present, EVERY starred cell carries its
    earnings-gate split, which is what the registration asks for ("earnings-gate
    split disclosed on every star").
    """
    keep_s = np.array([i for i, s in enumerate(sessions)
                       if s.isoformat() in arm_sessions], dtype=np.int64) \
        if arm_sessions is not None else np.arange(len(sessions))
    S = len(keep_s)
    # Guard BEFORE the indexing, not after: an empty comprehension yields a
    # float64 array, and `sess_map[keep_s]` then raises IndexError — after the
    # multi-hour session pass has already run.
    if S < BLOCK:
        return {"n_sessions": S,
                "unavailable": f"fewer sessions ({S}) than the bootstrap "
                               f"block length ({BLOCK})"}
    sess_map = np.full(len(sessions), -1, np.int64)
    sess_map[keep_s] = np.arange(S)
    rng = np.random.default_rng(rng_seed)
    wmat = block_weights(S, N_BOOT, BLOCK, rng)

    in_arm = sess_map[table["sess"]] >= 0
    rows = {k: v[in_arm] for k, v in table.items()}
    # `rows["sess"]` still holds GLOBAL session indices, so the gate masks are
    # built against the full session list and simply come out arm-length
    gmask = kmask = None
    if gate is not None and known is not None and dates is not None:
        gmask, kmask = gate_row_masks(rows, sessions, gate, dates, known)

    cells, primary = [], []
    for wi, W in enumerate(WINDOWS):
        for hi, H in enumerate(HORIZONS):
            for fi, f in enumerate(FEATS):
                v, s, fr = cell_rows(rows, wi, fi, hi, sess_map)
                if not len(v) or not fr.any() or not (~fr).any():
                    cells.append({"W": W, "H": H, "component": f,
                                  "n_fired": int(fr.sum()),
                                  "n_complement": int((~fr).sum()),
                                  "primary_cell": (W == PRIMARY_W
                                                   and H == PRIMARY_H),
                                  "unavailable": "empty pool"})
                    continue
                keep = cell_mask(rows, wi, fi, hi)
                wc = rows["wcls"][keep][:, wi]
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
                    # firing rate over the pool. p90 with ties-fire and no
                    # activity floor can run well past 10% in quiet cells; that
                    # is disclosed here, NOT corrected by re-adding a floor the
                    # registration's intraday form does not have.
                    "fire_rate": round(float(fr.sum() / len(fr)), 5),
                    # the fired windows' own classified premium — so a reader can
                    # see whether a component is firing on dust
                    "fired_window_premium_usd": _pctl(wc[fr]),
                    "complement_window_premium_usd": _pctl(wc[~fr]),
                    # the DISTRIBUTION the registration asks for, not just medians
                    "normalized_move_fired": _pctl(v[fr]),
                    "normalized_move_complement": _pctl(v[~fr]),
                    "tail_rate_diff_3x": None if tp is None else round(tp, 5),
                    "tail_ci": [None if tlo is None else round(tlo, 5),
                                None if thi is None else round(thi, 5)],
                    "primary_cell": (W == PRIMARY_W and H == PRIMARY_H),
                }
                # "earnings-gate split disclosed on every star" — attached to the
                # starred cell's own object, including tail-rate stars, so a
                # secondary finding cannot be read without its split. Unstarred
                # cells stay lean; the primary cells always get one separately.
                if gmask is not None and (row["star"] or starred(tlo, thi)):
                    row["earnings_split"] = split_cell(
                        rows, wi, fi, hi, gmask, kmask)
                    row["earnings_split_reason"] = (
                        "median" if row["star"] else "tail_rate")
                cells.append(row)
                if row["primary_cell"]:
                    primary.append(row)

    # the ONE registered direction test: window-tilt sign vs signed H=2h return
    direction = []
    wi, hi = WINDOWS.index(PRIMARY_W), HORIZONS.index(PRIMARY_H)
    for fi, f in enumerate(FEATS):
        fired_rows = (rows["pool"][:, wi, fi] & rows["fired"][:, wi, fi]
                      & ~rows["excl"][:, wi, fi]
                      & np.isfinite(rows["signed"][:, hi]))
        # a sign of exactly 0 is neither agreement nor disagreement, so those
        # rows are dropped rather than scored — count reported, not hidden
        scored = (fired_rows & np.isfinite(rows["tilt"]) & (rows["tilt"] != 0)
                  & (rows["signed"][:, hi] != 0))
        n_zero = int((fired_rows & ~scored).sum())
        if not scored.any():
            direction.append({"component": f, "n": 0,
                              "n_dropped_zero_sign": n_zero,
                              "unavailable": "no scorable fired rows"})
            continue
        s = sess_map[rows["sess"][scored]]
        agree = (np.sign(rows["tilt"][scored])
                 == np.sign(rows["signed"][scored, hi])).astype(float)
        h = np.bincount(s, weights=agree, minlength=S)
        n = np.bincount(s, minlength=S).astype(float)
        p, lo, hi_ci = boot_share_diff(h, n, n * 0.5, n, wmat)
        direction.append({
            "component": f, "n": int(scored.sum()),
            "n_dropped_zero_sign": n_zero,
            "agreement_minus_half": None if p is None else round(p, 5),
            "ci": [None if lo is None else round(lo, 5),
                   None if hi_ci is None else round(hi_ci, 5)],
            "star": starred(lo, hi_ci)})

    # Ambiguity diagnostic (AMBIGUITIES['complement_boundaries']): the SAME
    # primary comparison with the complement restricted to each name's first
    # pooled boundary — reading B. No survival authority; it exists to answer one
    # question, "does the other reading flip the conclusion?", which a complement
    # median alone cannot. Same shared resampling, so the two are comparable.
    diag = []
    n_names_max = int(rows["name"].max()) + 1 if len(rows["name"]) else 1
    for fi, f in enumerate(FEATS):
        keep = cell_mask(rows, wi, fi, hi)
        fr_all = rows["fired"][:, wi, fi]
        # only meaningful next to a real fired pool: a component that never fired
        # has no complement to contrast, and printing one would read as a result
        if not keep.any() or not (keep & fr_all).any():
            continue
        key = (rows["sess"][keep].astype(np.int64) * n_names_max
               + rows["name"][keep])
        ordv = np.lexsort((rows["bi"][keep], key))
        ks = key[ordv]
        firstb = np.zeros(len(key), bool)
        firstb[ordv[np.flatnonzero(np.r_[True, ks[1:] != ks[:-1]])]] = True
        vk, sk, frk = (rows["norm"][keep][:, hi],
                       sess_map[rows["sess"][keep]], fr_all[keep])
        sel = firstb & ~frk
        if not sel.any() or not frk.any():
            continue
        mf = float(np.median(vk[frk]))
        mc = float(np.median(vk[sel]))
        d = boot_medians(vk[frk], sk[frk], S, wmat) - \
            boot_medians(vk[sel], sk[sel], S, wmat)
        lo, hi_ci = ci(d)
        diag.append({
            "component": f, "median_fired": round(mf, 5),
            "median_complement": round(mc, 5), "diff": round(mf - mc, 5),
            "ci": [None if lo is None else round(lo, 5),
                   None if hi_ci is None else round(hi_ci, 5)],
            "star": starred(lo, hi_ci), "n_complement": int(sel.sum()),
            "n_fired": int(frk.sum())})

    # per-boundary composition at the primary cell: the ever-extreme-vs-
    # never-extreme caveat is a composition question, so the composition ships
    by_boundary = []
    for fi, f in enumerate(FEATS):
        keep = cell_mask(rows, wi, fi, hi)
        if not keep.any():
            continue
        frk, bik = rows["fired"][keep][:, wi, fi], rows["bi"][keep]
        by_boundary.append({
            "component": f,
            "fired": {BOUNDARY_LABELS[b]: int(((bik == b) & frk).sum())
                      for b in range(len(BOUNDARY_BINS))},
            "complement": {BOUNDARY_LABELS[b]: int(((bik == b) & ~frk).sum())
                           for b in range(len(BOUNDARY_BINS))}})

    return {"n_sessions": S, "cells": cells, "primary": primary,
            "direction_test": direction,
            "complement_first_boundary_only": diag,
            "primary_cell_rows_by_boundary": by_boundary}


# --------------------------------------------------------------------------

def reading_rule(primary, cells) -> dict:
    """(a) primary CI clear of zero, (b) same-direction star at >=2 of 3
    horizons for W=60, (c) replication star at W=30 or W=120 (at the primary
    horizon — see AMBIGUITIES['reading_rule_c_horizon']). Reported as booleans
    per component: the registration's rule, not a verdict.

    Every component gets a row. A component whose primary cell is empty is
    reported as explicitly unavailable rather than omitted — a missing row reads
    as "not checked" when it means "could not be evaluated".
    """
    out = {}
    by_comp = {r["component"]: r for r in primary}
    for f in FEATS:
        row = by_comp.get(f)
        if row is None or row.get("diff") is None:
            out[f] = {"unavailable": "primary cell empty — not evaluable",
                      "a_primary_clear_of_zero": None, "b_pass": None,
                      "c_replication_other_window": None, "all_three": False}
            continue
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


def gate_row_masks(table, sessions, gate, dates, known):
    """`(gated, known)` boolean masks aligned to the row table.

    Names with NO resolved EDGAR history are UNKNOWN, not earnings-far: they land
    in NEITHER arm, with the count disclosed. 1.14e is explicit about this
    (`far = ~gate & known`, `tools/mover_earnings_gate.py:205`) and it matters
    here because ~55% of corpus roots have no EDGAR history — sweeping them into
    "far" would make the contrast arm mostly a bucket of unflaggable names.
    """
    di = {d: t for t, d in enumerate(dates)}
    tcol = np.array([di.get(s.isoformat(), -1) for s in sessions])[table["sess"]]
    g = np.zeros(len(tcol), bool)
    ok = tcol >= 0
    g[ok] = gate[table["name"][ok], tcol[ok]]
    return g, known[table["name"]]


def split_cell(table, wi, fi, hi, g, kn) -> dict:
    """The 1.14e gated/far split of one (W, component, H) cell.

    A finding that collapses to the earnings-adjacent split is the calendar
    wearing a flow costume, which is why the registration attaches this to every
    star rather than to a chosen headline.
    """
    keep = cell_mask(table, wi, fi, hi)
    fr = table["fired"][:, wi, fi]
    out = {"n_unknown_excluded": int((keep & ~kn).sum())}
    for lab, m in (("gated", g & kn), ("far", ~g & kn)):
        kf, kc = keep & fr & m, keep & ~fr & m
        out[lab] = {
            "median_fired": (round(float(np.median(table["norm"][kf, hi])), 5)
                             if kf.any() else None),
            "median_complement": (
                round(float(np.median(table["norm"][kc, hi])), 5)
                if kc.any() else None),
            "n_fired": int(kf.sum()), "n_complement": int(kc.sum())}
    return out


def earnings_split(table, sessions, gate, dates, known) -> list[dict]:
    """The primary cell's split, computed unconditionally (starred or not)."""
    g, kn = gate_row_masks(table, sessions, gate, dates, known)
    wi, hi = WINDOWS.index(PRIMARY_W), HORIZONS.index(PRIMARY_H)
    return [{"component": f, **split_cell(table, wi, fi, hi, g, kn)}
            for fi, f in enumerate(FEATS)]


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
    p.add_argument("--universe-fence", action="store_true",
                   help="NON-REGISTERED secondary arm: drop index/ETF roots as "
                        "the day-grain arc did. The registered universe (the "
                        "default) keeps them — see AMBIGUITIES['universe_fence']")
    a = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    db, bars_dir = Path(a.corpus_db), Path(a.bars_dir)
    na = new_na_counters()
    universe = load_universe(db, a.universe_fence)
    if a.universe_fence:
        logger.warning("--universe-fence: this is a NON-REGISTERED secondary "
                       "arm. Quintile cut-points differ from the registered "
                       "run, so firing differs and the two are not nested.")
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
    early_close: dict[str, int] = {}
    table = run_sessions(sessions, universe, dates, quint, med_pmax, cnt_pmax,
                         bars_dir, na, early_close)
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
        results[arm] = (analyse(table, sessions, arm_sessions, SEED,
                                gate=gate, known=known, dates=dates)
                        if len(table["sess"]) else {"unavailable": "no rows"})
        if "cells" in results[arm]:
            results[arm]["reading_rule"] = reading_rule(
                results[arm]["primary"], results[arm]["cells"])

    split = (earnings_split(table, sessions, gate, dates, known)
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
            "complement": "pool minus fired, where pool = eligible AND "
                          "component defined AND peer-celled (1.14d's "
                          "`unfired = ~fr & pool`) — never merely 'eligible'",
            "components": list(FEATS),
            "universe_source": "DISTINCT underlying FROM flow_rollup over the "
                               "whole corpus (not a per-session defs universe)",
            "universe_size": len(universe),
            "universe_fenced": a.universe_fence,
            "universe_arm": ("NON-REGISTERED secondary (index/ETF roots "
                             "dropped; quintile cut-points differ)"
                             if a.universe_fence else
                             "registered (every opra_defs underlying with >=1 "
                             "RTH print; index/ETF roots included)"),
            "sessions_analysed": na["sessions_analysed"],
            "distribution_percentiles": list(DIST_PCTL),
            "thin_normalizer_threshold": THIN_NORMALIZER,
            "na_note": NA_NOTE,
            "run_notes": list(RUN_NOTES),
            "ambiguities": AMBIGUITIES,
        },
        "arms": results,
        "earnings_split_primary_cell": split,
        "early_close_sessions": early_close,
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
        print("\n  == firing geometry + fired-window premium (disclosure) ==")
        for r in res["cells"]:
            if r.get("primary_cell") and "fire_rate" in r:
                fw = r["fired_window_premium_usd"] or {}
                print(f"    {r['component']:<22} fire_rate={r['fire_rate']} "
                      f"fired window premium p25/50/75/90="
                      f"{fw.get('p25')}/{fw.get('p50')}/{fw.get('p75')}"
                      f"/{fw.get('p90')}")
        print("\n  == normalized-move distribution, primary cell ==")
        for r in res["cells"]:
            if r.get("primary_cell") and "normalized_move_fired" in r:
                a_, b_ = (r["normalized_move_fired"] or {},
                          r["normalized_move_complement"] or {})
                print(f"    {r['component']:<22} fired "
                      + "/".join(str(a_.get(f'p{p}')) for p in DIST_PCTL)
                      + "  complement "
                      + "/".join(str(b_.get(f'p{p}')) for p in DIST_PCTL))
        print("\n  == reading rule (a)(b)(c) ==")
        for f, rr in res.get("reading_rule", {}).items():
            if "unavailable" in rr:
                print(f"    {f:<22} unavailable ({rr['unavailable']})")
                continue
            print(f"    {f:<22} a={rr['a_primary_clear_of_zero']} "
                  f"b={rr['b_horizons_starred_same_direction']}/3 "
                  f"({rr['b_pass']}) c={rr['c_replication_other_window']} "
                  f"=> all_three={rr['all_three']}")
        print("\n  == direction test (window tilt vs signed 2h return) ==")
        for d in res.get("direction_test", []):
            print(f"    {d['component']:<22} n={d['n']} "
                  f"dropped_zero_sign={d.get('n_dropped_zero_sign')} "
                  f"agree-0.5={d.get('agreement_minus_half')} "
                  f"ci={d.get('ci')} star={d.get('star')}")
        print("\n  == reading-B complement diagnostic (NO authority) ==")
        for d in res.get("complement_first_boundary_only", []):
            lo, hi = d["ci"]
            print(f"    {d['component']:<22} fired={d['median_fired']} "
                  f"comp={d['median_complement']} (n={d['n_complement']}) "
                  f"diff={d['diff']} [{lo},{hi}]"
                  f"{' *' if d['star'] else ''}")
        print("\n  == primary-cell rows by boundary ==")
        for d in res.get("primary_cell_rows_by_boundary", []):
            fired_s = " ".join(f"{k}={v}" for k, v in d["fired"].items() if v)
            comp_s = " ".join(f"{k}={v}" for k, v in d["complement"].items() if v)
            print(f"    {d['component']:<22} fired[{fired_s}]")
            print(f"    {'':<22} comp [{comp_s}]")
    print("\n== earnings-gate split of the primary cell (1.14e gate) ==")
    print("   names with no resolved EDGAR history are in NEITHER arm")
    for r in payload["earnings_split_primary_cell"]:
        for lab in ("gated", "far"):
            g = r[lab]
            print(f"  {r['component']:<22} {lab:<6} "
                  f"fired={g['median_fired']} (n={g['n_fired']}) "
                  f"complement={g['median_complement']} (n={g['n_complement']})")
        print(f"  {r['component']:<22} unknown-EDGAR rows excluded="
              f"{r['n_unknown_excluded']}")
    starred_secondary = [
        (arm, c) for arm, res in payload["arms"].items()
        for c in res.get("cells", [])
        if "earnings_split" in c and not c.get("primary_cell")]
    if starred_secondary:
        print("\n== earnings split of STARRED SECONDARY cells "
              "(no survival authority) ==")
        for arm, c in starred_secondary:
            print(f"  [{arm}] {c['component']:<20} W={c['W']:>3} "
                  f"H={c['H']:<5} starred_on={c['earnings_split_reason']}")
            for lab in ("gated", "far"):
                g = c["earnings_split"][lab]
                print(f"      {lab:<6} fired={g['median_fired']} "
                      f"(n={g['n_fired']}) complement={g['median_complement']} "
                      f"(n={g['n_complement']})")

    ec = payload.get("early_close_sessions") or {}
    print(f"\n== early-close sessions detected: {len(ec)} ==")
    print("   CHECK this list against the exchange's real early-close calendar "
          "— detection is a cross-sectional heuristic, not a calendar lookup")
    for ds, lb in sorted(ec.items()):
        end_min = OPEN_MIN + lb * BAR_MIN
        print(f"   {ds}  last_bin={lb:<3} grid ends {end_min // 60:02d}:"
              f"{end_min % 60:02d} ET")

    print("\n== NA accounting (nothing is silently dropped) ==")
    for k, v in payload["na_accounting"].items():
        print(f"  {k:<34} {v:,}")
    print(f"\n  note: {payload['meta']['na_note']}")
    print("\n== notes on the registered definitions (caveats, not defects) ==")
    for note in payload["meta"]["run_notes"]:
        print(f"  * {note}")


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
