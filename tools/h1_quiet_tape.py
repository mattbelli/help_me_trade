"""H1 registered implementation — opening-classified flow on a quiet tape.

`References/H1_QUIET_TAPE_PREREG_2026-07-31.md` (LAW 2026-07-31, v5) is the
governing document; this module is the H1.6 #2 implementation freeze. Nothing
here may change after the freeze commit except by a new dated addendum.

What it reuses (imports, never copies — the prereg's own requirement):
  * the obligation-3 fenced equity pipeline: `_sessions`/`require_cache`/
    `_name_index`/`_build` panels off `data/order_grain_null_v7_equity/`,
    `real_tilt`, `_fire` (G-fired-cohort overlap count only),
    `_trailing_quintiles` (tier-composition record only) and
    `_cache_provenance` (the record's input identification);
  * `f_open_by_identity` + `_cached_resolver` (tools/open_close_split.py) —
    obligation 3b's re-key + sum-before-rank convention with §2.4's
    1,000-contract floor;
  * `rv_park` (tools/mover_intraday_114g.py) — 1.14g's registered Parkinson
    formula and absence conditions, at H1's own file-based bin grid;
  * `load_bars_session`/`session_last_bin` (tools/mover_intraday.py) — the
    5-minute store reader whose ET bin computation is DST-correct.

What is NEW here (nothing existed to import — verified 2026-07-31): G.2's
open-anchored alpha, the ATR14 helper at G.2's inclusive-`d` pin, the four-case
terminal-value rule, the gapped-Bartlett Newey-West LRV, the G.4 size
calibration + MDE (H1 seed 20260818), and the tilt-sign permutation placebo
(seed 20260819).

Implementation pins (this module's reading of prereg text; the freeze review
checked every one against the document — two split-mandate Opus rounds):
  P1  Presence: `≥15-of-20` mirrors `_fire` exactly — window `[d-20, d)`,
      defined only from panel index 20 on; present = classified premium > 0
      with finite sigma (base_rate_table's `present`). No z/std condition —
      H1 has no z rule.
  P2  The ranked population on session d = presence ∧ $250k floor ∧ OI match
      (≥1,000 contracts via `f_open_by_identity`) ∧ valid `rv_park`. Both
      percentiles rank within this one population, identities in ascending
      terminal-symbol order (the panel's sorted-name order), double
      `argsort(kind='stable')`, `100·rank/max(n-1,1)`, cuts ≥ 80.0 / ≤ 33.0.
      (Adjudicated in review: the prereg's "≤1,186 rankable" drafting figure
      is the raw OI cross-section — 1,186 reproduces exactly on 2026-07-17 —
      and the registered conjunction measures 707 there, strictly inside it.)
  P3  Instrument coverage per date (H1.5 gate) = |presence ∧ floor ∧ OI match|
      / |presence ∧ floor| — the denominator is the base population BEFORE the
      `rv_park` condition; the <70% drop applies to the JUDGMENT window only
      (H1.4 defines calibration dates by the both-arms rule alone).
  P4  `rv_park` bin grid = `arange(78)` (09:30–15:55 ET) from the session's
      own file; on the two NAMED early closes (2025-11-28, 2025-12-24) the
      grid is `arange(42)`, post-close bins discarded; the presence
      denominator is the grid length. The file's own cross-section collapse
      (`session_last_bin`) is cross-checked against the named grid and an
      unnamed early close BLOCKS rather than silently NA-ing the date.
  P5  Bars and 5-minute panels for a merged identity are read at the terminal
      symbol; the OCC⇄directory sibling forms (`BRKB`⇄`BRK.B`) are aliased
      into every store read, the identity's own key winning, so a vendor-form
      difference is never misread as case (ii) "zero bars". Series under
      different forms are never merged; the first form with bars wins whole.
  P6  "ATR14/price" in the H1.6 #3 record is pinned as ATR14 / close_d (the
      event date's own close); "open/ATR14" is open_{d+1}/ATR14.
  P7  "Before the close of 2026-08-07" gates are enforced as run-date
      ≥ 2026-08-08 (the needed d+10 bars cannot exist earlier anyway). The
      `eligibility` stage is restricted to the TRAINING window — the
      judgment-window input-property halves live in the gated #3 record.
  P8  The interior-bar-hole rule (G.2: BLOCKING, never a gradeable state)
      raises for any bar missing at a needed calendar index with a later bar
      present — including a missing entry bar at d+1, and including a
      non-consecutive ATR14 window (a hole BEFORE d).
  P9  The 21/45-column not-yet-elapsed state is the G.3 truncated-at-run-date
      DISCLOSURE (share per column), never the terminal-value rule; the 10td
      record column must be complete-or-terminated or the run aborts.
  P10 H1.6 #3 records the complete-or-terminated date count G and the gap
      vector from CASE-LEVEL gradeability only (bar availability + tilt sign)
      — no alpha is computed at #3; #4 reads G and the gap vector from the #3
      record and asserts its own training series against the recorded one;
      #5 asserts BOTH the series dates and the gap vector against #3.
  P11 "≥ 30 events" in H1.5 is counted over GRADEABLE primary-arm events on
      the series dates; "≥ 20 distinct complete-or-terminated event dates" is
      counted over dates carrying ≥1 gradeable event.
  P12 The regime-span rule is evaluated on the primary SERIES dates (the
      dates that enter the test), recorded as such in #3.

Stages (CLI): `eligibility` (training-window input-property census, runnable
any time) — `record` (H1.6 #3) — `calibrate` (H1.6 #4) — `score` (H1.6 #5,
one-shot). `record`/`calibrate`/`score` refuse to run before 2026-08-08 (P7);
`score` additionally requires the recorded Addendum G verdict artifact.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import platform
import sqlite3
import subprocess
import sys
from datetime import date
from pathlib import Path
from typing import NamedTuple

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools.base_rate_table import (  # noqa: E402
    _build,
    _cache_provenance,
    _name_index,
    _trailing_quintiles,
    real_tilt,
    require_cache,
)
from tools.flow_obligation3 import BLOCK  # noqa: E402
from tools.mover_intraday import BARS_DIR as BARS5_DIR  # noqa: E402
from tools.mover_intraday import (  # noqa: E402
    RTH_BINS,
    bins_from_ns,
    load_bars_session,
    session_last_bin,
)
from tools.mover_intraday_114g import rv_park  # noqa: E402
from tools.open_close_split import (  # noqa: E402
    VOL_FLOOR,
    _cached_resolver,
    f_open_by_identity,
)
from tools.order_grain_null import (  # noqa: E402
    FENCED_OUT_DIR,
    LOOKBACK,
    MIN_PRESENT,
    PREMIUM_FLOOR,
    _fire,
    _sessions,
    classification,
)

logger = logging.getLogger("h1_quiet_tape")

# ------------------------------ registered constants ------------------------------
F_OPEN_CUT = 80.0                 # condition 1: f_open percentile >= p80
RV_CUT = 33.0                     # condition 2: rv_park percentile <= p33
RV_PRESENCE = 0.80                # >=80% of the session's bin grid, else NA
EARLY_CLOSE_BINS = {"2025-11-28": 42, "2025-12-24": 42}   # named 13:00 ET closes
TRAIN_START = "2025-07-28"        # G.4's dated split (corpus first session)
TRAIN_END = "2026-03-31"
JUDGMENT_START = "2026-06-01"
JUDGMENT_END = "2026-07-24"
JUDGMENT_EXCLUDE = frozenset({"2026-07-23"})
JUDGMENT_DATES = 37
MONTHLY_EXPIRIES = ("2026-06-18", "2026-07-17")           # secondary (c), named
HORIZON = 10                      # G.3 horizon of record
H_GRID = (3, 10, 21, 45)          # G.3 grid, fully reported
NW_LAGS = 9                       # G.4: 9 = H - 1
CAL_REPS = 100_000
CAL_SEED = 20260818               # H1.4 — one seed for cutoff AND MDE
PLACEBO_REPS = 1_000
PLACEBO_SEED = 20260819
MDE_STEP = 0.01
MDE_POWER = 0.80
MDE_CEILING = 1.00                # > 1.00 ATR => POWER-LIMITED
MDE_SEARCH_MAX = 2.00             # search stops here; above it record ">2.00"
COVERAGE_MIN = 0.70               # H1.5 per-date instrument-coverage tripwire
MIN_EVENT_DATES = 20
MIN_EVENTS = 30
REGIME_CUT = 0.02                 # SPY 10td forward-return buckets at +/-2%
EARLIEST_RUN = date(2026, 8, 8)   # P7: after the close of 2026-08-07
ATR_N = 14

CORPUS_DB = ROOT / "data" / "flow_corpus.db"
RECORD_PATH = ROOT / "data" / "h1_quiet_tape_record.json"
PREREG = "References/H1_QUIET_TAPE_PREREG_2026-07-31.md"


# ------------------------------ panel and population ------------------------------

class Panel(NamedTuple):
    sessions: list[date]          # corpus start .. JUDGMENT_END, contiguous
    names: np.ndarray             # sorted resolved identities (terminal form)
    ni: dict[str, int]
    prem: np.ndarray              # classified premium P[ALL], (names x days)
    tilt: np.ndarray              # real_tilt(P, B), (names x days)
    present: np.ndarray           # base_rate_table's present
    presence_ok: np.ndarray       # P1: >=15-of-20 trailing


def panel_files(sessions: list[date],
                cache_dir: Path = FENCED_OUT_DIR) -> list[Path]:
    return [cache_dir / f"{s.isoformat()}.npz" for s in sessions]


def build_panel(cache_dir: Path = FENCED_OUT_DIR) -> Panel:
    sessions = [s for s in _sessions(0) if s.isoformat() <= JUDGMENT_END]
    files = panel_files(sessions, cache_dir)
    require_cache(files, cache_dir)
    # symmetric guard: the tcbbo dir defines `sessions`, the npz cache defines
    # the panel content — a session present as npz but missing from the tcbbo
    # scan would silently shift every presence window and stay undetected
    on_disk = {p.stem for p in cache_dir.glob("*.npz")
               if p.stem <= JUDGMENT_END}
    listed = {s.isoformat() for s in sessions}
    if on_disk != listed:
        raise SystemExit(
            f"panel session list and {cache_dir} disagree: "
            f"only-on-disk {sorted(on_disk - listed)}, "
            f"only-listed {sorted(listed - on_disk)} — blocking")
    names, ni = _name_index(files)
    P, Q, B, _ = _build(files, len(names), ni, draws=0)
    prem, q, bull = P["ALL"], Q["ALL"], B["ALL"]
    with np.errstate(invalid="ignore", divide="ignore"):
        n_eff = np.where(q > 0, prem**2 / q, np.nan)
    sigma = np.where(np.isfinite(n_eff) & (n_eff > 0), 1 / np.sqrt(n_eff), np.nan)
    present = (prem > 0) & np.isfinite(sigma)
    tilt = real_tilt(prem, bull)
    # P1 — presence counts, the _fire window verbatim: [d-20, d), from index 20.
    ck = np.cumsum(np.c_[np.zeros(present.shape[0]), present.astype(np.float64)], 1)
    presence_ok = np.zeros(present.shape, bool)
    for d in range(LOOKBACK, present.shape[1]):
        presence_ok[:, d] = (ck[:, d] - ck[:, d - LOOKBACK]) >= MIN_PRESENT
    return Panel(sessions, names, ni, prem, tilt, present, presence_ok)


def rv_grid(session: date) -> np.ndarray:
    """P4 — the session's registered bin grid (deterministic, by name)."""
    return np.arange(EARLY_CLOSE_BINS.get(session.isoformat(), RTH_BINS))


def _alias_index(panel: Panel) -> dict[str, int]:
    """P5 — store keys under OCC⇄directory sibling forms; identities win."""
    rc = classification()
    alias = dict(panel.ni)
    for n, i in panel.ni.items():
        for form in (rc.occ_form(n), rc.directory_form(n)):
            if form:
                alias.setdefault(form, i)
    return alias


def rv_for_sessions(panel: Panel, idxs: list[int]) -> dict[int, np.ndarray]:
    """rv_park per identity for the given panel session indices; NaN = NA."""
    alias = _alias_index(panel)
    out: dict[int, np.ndarray] = {}
    for j in idxs:
        s = panel.sessions[j]
        f = BARS5_DIR / f"{s.isoformat()}.npz"
        if not f.exists() or not f.with_suffix(".json").exists():
            raise SystemExit(f"5-minute store missing session {s} — blocking "
                             "(the registered rv_park coordinate reads it)")
        with np.load(f) as z:
            observed = session_last_bin(bins_from_ns(z["t_ns"], s))
        grid = rv_grid(s)
        if abs(observed - len(grid)) > 1:                    # P4 guard
            raise SystemExit(
                f"{s}: file cross-section collapses at bin {observed} but the "
                f"registered grid is {len(grid)} bins — an unnamed early close "
                "BLOCKS (it would silently NA the whole date)")
        _, _, _, highs, lows = load_bars_session(f, alias, len(panel.names),
                                                 with_range=True)
        out[j] = rv_park(highs, lows, grid, coverage=RV_PRESENCE)
    return out


class SessionPop(NamedTuple):
    base: np.ndarray              # presence & floor (P3 denominator)
    oi_ok: np.ndarray             # base & OI-matched
    ranked: np.ndarray            # P2 population
    f_raw: np.ndarray             # raw f_open per identity (NaN off-map)
    rv_raw: np.ndarray            # raw rv_park per identity
    f_pct: np.ndarray             # NaN off-population
    rv_pct: np.ndarray
    coverage: float               # P3
    n_no_oi: int
    n_no_rv: int


def _ordinal_pct(vals: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """P2 — ordinal rank in ascending-identity input order, stable, ties distinct."""
    out = np.full(vals.shape, np.nan)
    idx = np.nonzero(mask)[0]
    if not len(idx):
        return out
    f = vals[idx]
    rank = np.argsort(np.argsort(f, kind="stable"), kind="stable").astype(np.float64)
    out[idx] = 100.0 * rank / max(len(f) - 1, 1)
    return out


def session_population(panel: Panel, rv_j: np.ndarray, fo: dict,
                       j: int) -> SessionPop:
    s = panel.sessions[j].isoformat()
    names = panel.names.tolist()
    base = panel.presence_ok[:, j] & (panel.prem[:, j] >= PREMIUM_FLOOR)
    has_oi = np.array([(s, n) in fo for n in names])
    oi_ok = base & has_oi
    rv_valid = np.isfinite(rv_j)
    ranked = oi_ok & rv_valid
    f_vals = np.array([fo.get((s, n), (0.0, np.nan))[1] for n in names])
    nb = int(base.sum())
    return SessionPop(base, oi_ok, ranked, f_vals, rv_j,
                      _ordinal_pct(f_vals, ranked), _ordinal_pct(rv_j, ranked),
                      coverage=(int(oi_ok.sum()) / nb) if nb else float("nan"),
                      n_no_oi=int((base & ~has_oi).sum()),
                      n_no_rv=int((oi_ok & ~rv_valid).sum()))


# ------------------------------ bars, ATR14, the metric ------------------------------

def spy_calendar() -> tuple[list[str], np.ndarray, np.ndarray]:
    """The registered benchmark session calendar with SPY opens/closes."""
    con = sqlite3.connect(f"file:{CORPUS_DB}?mode=ro", uri=True)
    rows = con.execute("SELECT date, o, c FROM equity_bars WHERE symbol='SPY' "
                       "AND adjustment='split' ORDER BY date").fetchall()
    con.close()
    if not rows:
        raise SystemExit("no SPY bars — benchmark unavailability BLOCKS the run (G.2)")
    return ([r[0] for r in rows], np.array([r[1] for r in rows]),
            np.array([r[2] for r in rows]))


def check_benchmark_continuity(panel: Panel, cal_dates: list[str]) -> None:
    """A flow session missing from SPY's own calendar is a benchmark hole —
    a BLOCKING data fault (G.2), never a silently-lengthened horizon."""
    cal = set(cal_dates)
    missing = [s.isoformat() for s in panel.sessions if s.isoformat() not in cal]
    if missing:
        raise SystemExit(f"flow sessions absent from the SPY calendar: "
                         f"{missing} — benchmark fault BLOCKS the run (G.2)")


def daily_bars(symbols: set[str]) -> dict[str, tuple]:
    """symbol -> (dates, o, h, l, c), split-adjusted, date-ascending."""
    con = sqlite3.connect(f"file:{CORPUS_DB}?mode=ro", uri=True)
    raw: dict[str, list] = {}
    syms = sorted(symbols)
    for i in range(0, len(syms), 500):
        chunk = syms[i:i + 500]
        q = ",".join("?" * len(chunk))
        for sym, d, o, h, lo, c in con.execute(
                f"SELECT symbol, date, o, h, l, c FROM equity_bars "
                f"WHERE adjustment='split' AND symbol IN ({q}) "
                f"ORDER BY symbol, date", chunk):
            raw.setdefault(sym, []).append((d, o, h, lo, c))
    con.close()
    return {sym: (
        [r[0] for r in rows],
        np.array([r[1] for r in rows]), np.array([r[2] for r in rows]),
        np.array([r[3] for r in rows]), np.array([r[4] for r in rows]),
    ) for sym, rows in raw.items()}


def atr14(h: np.ndarray, low: np.ndarray, c: np.ndarray, m0: int) -> float | None:
    """G.2's pinned ATR14: simple mean of the 14 TRs ending at `m0` INCLUSIVE.

    `m0` indexes the name's OWN bar series at the event date. None when fewer
    than 15 bars exist through `m0` — the caller routes that to case (iii).
    """
    if m0 < ATR_N:
        return None
    wh, wl = h[m0 - ATR_N + 1:m0 + 1], low[m0 - ATR_N + 1:m0 + 1]
    pc = c[m0 - ATR_N:m0]
    tr = np.maximum(wh - wl, np.maximum(np.abs(wh - pc), np.abs(wl - pc)))
    return float(tr.mean())


class Graded(NamedTuple):
    case: str                     # "full" | "truncated" | "ii" | "iii" | "iv"
    covered: dict[int, str]       # per grid column: "full"|"truncated"|"not_elapsed"
    alpha: dict[int, float | None]  # UNSIGNED; populated only with_alpha
    trunc_at: int | None          # sessions covered when the 10td column truncates
    atr: float | None
    open_over_atr: float | None
    atr_over_price: float | None  # P6: ATR14 / close_d


class BlockingHole(Exception):
    """G.2/P8: an interior bar hole is a data fault, never a gradeable state."""


_UNGRADED = {h: None for h in H_GRID}


def grade_name_day(bars, cal_pos: dict[str, int], spy_o: np.ndarray,
                   spy_c: np.ndarray, k0: int, last_idx: int,
                   with_alpha: bool) -> Graded:
    """The four-case terminal-value rule, checked (ii) -> (iii) -> (iv) -> (i).

    `with_alpha=False` (P10) determines cases and coverage from bar
    AVAILABILITY alone — no close beyond `d` is read, no alpha exists.
    """
    if bars is None:                                         # (ii) zero bars
        return Graded("ii", dict(_UNGRADED), dict(_UNGRADED), None, None, None, None)
    dates, o, h, low, c = bars
    kk = np.array([cal_pos.get(d, -1) for d in dates])       # own-series -> calendar
    keep = kk >= 0
    kk, o, h, low, c = kk[keep], o[keep], h[keep], low[keep], c[keep]
    if not len(kk):
        return Graded("ii", dict(_UNGRADED), dict(_UNGRADED), None, None, None, None)
    at_d = np.nonzero(kk == k0)[0]
    if not len(at_d):                                        # (iii) no bar at d
        return Graded("iii", dict(_UNGRADED), dict(_UNGRADED), None, None, None, None)
    m0 = int(at_d[0])
    a = atr14(h, low, c, m0)
    if a is None or a <= 0 or not np.isfinite(a):            # (iii) <15 bars / ATR<=0
        return Graded("iii", dict(_UNGRADED), dict(_UNGRADED), None, None, None, None)
    if int(kk[m0]) - int(kk[m0 - ATR_N]) != ATR_N:           # P8: hole BEFORE d
        raise BlockingHole(
            f"ATR14 window spans {int(kk[m0]) - int(kk[m0 - ATR_N])} calendar "
            f"sessions (needs {ATR_N}) — interior hole before d")
    k1 = k0 + 1
    at_e = np.nonzero(kk == k1)[0]
    if not len(at_e):
        if kk[-1] <= k0:                                     # (iv) no bar at d+1
            return Graded("iv", dict(_UNGRADED), dict(_UNGRADED), None, a, None, None)
        raise BlockingHole(f"bar missing at entry d+1 (calendar {k1}) "
                           "with later bars present")        # P8
    m1 = int(at_e[0])
    entry, spy_entry = float(o[m1]), float(spy_o[k1])
    k_last = int(kk[-1])
    covered: dict[int, str] = {}
    alphas: dict[int, float | None] = dict(_UNGRADED)
    trunc_at = None
    for hz in H_GRID:
        kt = k0 + hz
        if kt > last_idx:                                    # P9: not elapsed
            covered[hz] = "not_elapsed"
            continue
        if k_last >= kt:
            at_t = np.nonzero(kk == kt)[0]
            if not len(at_t):
                raise BlockingHole(f"bar missing at d+{hz} (calendar {kt}) "
                                   "with later bars present")  # P8
            m_t, k_end, covered[hz] = int(at_t[0]), kt, "full"
        else:                                                # (i) bars stop mid-horizon
            m_t, k_end, covered[hz] = len(kk) - 1, k_last, "truncated"
            if hz == HORIZON:
                trunc_at = k_end - k0
        if with_alpha:
            alphas[hz] = float(((c[m_t] / entry - 1.0)
                                - (spy_c[k_end] / spy_entry - 1.0)) * entry / a)
    case = "truncated" if covered.get(HORIZON) == "truncated" else "full"
    return Graded(case, covered, alphas, trunc_at, a,
                  entry / a, a / float(c[m0]))


# ------------------------------ inference (G.4 by reference) ------------------------------

def _gap_pairs(gaps: np.ndarray) -> dict[int, tuple[np.ndarray, np.ndarray]]:
    """Unordered (t>s) index pairs at each SPY-session gap h in 1..NW_LAGS."""
    g = len(gaps)
    dm = np.abs(gaps[:, None] - gaps[None, :])
    lower = np.tri(g, g, -1, dtype=bool)                      # t > s exactly once
    return {h: np.nonzero((dm == h) & lower) for h in range(1, NW_LAGS + 1)}


def _nw_batch(X: np.ndarray, gaps: np.ndarray,
              pairs: dict | None = None) -> tuple[np.ndarray, ...]:
    """Vectorized G.4 NW over rows of X (each row = one series on `gaps`)."""
    g = X.shape[1]
    if len(gaps) != g:
        raise ValueError(f"gap vector length {len(gaps)} != series length {g}")
    if g < 2:
        raise ValueError(f"series length {g} < 2 — the estimator is undefined")
    pairs = pairs if pairs is not None else _gap_pairs(gaps)
    mean = X.mean(axis=1)
    Xc = X - mean[:, None]
    lrv = (Xc**2).sum(axis=1) / g                             # gamma_0
    for h, (ti, si) in pairs.items():
        if not len(ti):
            continue                                          # zero-pair gap: 0
        gam = (Xc[:, ti] * Xc[:, si]).sum(axis=1) / g
        lrv = lrv + 2.0 * (1.0 - h / (NW_LAGS + 1.0)) * gam
    fb = lrv <= 0
    with np.errstate(invalid="ignore", divide="ignore"):
        if fb.any():                                          # fallback: own series
            lrv = np.where(fb, X.var(axis=1, ddof=1), lrv)
        se = np.sqrt(lrv / g)
        return mean / se, mean, se, fb


def nw_stats(x: np.ndarray, gaps: np.ndarray) -> tuple[float, float, float, bool]:
    """(t, mean, se, used_fallback) — G.4's written formula on one series."""
    t, m, se, fb = _nw_batch(np.asarray(x, np.float64)[None, :], np.asarray(gaps))
    return float(t[0]), float(m[0]), float(se[0]), bool(fb[0])


def h1_blocks(rng, g_src: int, g_out: int, reps: int,
              block: int = BLOCK) -> np.ndarray:
    """G.4 circular block assembly, generalized: blocks drawn from a length-
    `g_src` series (wrapping), concatenated, truncated to `g_out`. With
    `g_src == g_out` this is `flow_obligation3.circular_blocks` verbatim —
    identical operations, identical RNG stream (pinned by test)."""
    n_blocks = -(-g_out // block)
    starts = rng.integers(0, g_src, size=(reps, n_blocks))
    idx = (starts[:, :, None] + np.arange(block)[None, None, :]) % g_src
    return idx.reshape(reps, n_blocks * block)[:, :g_out]


def calibrate(train_x: np.ndarray, judg_gaps: np.ndarray,
              seed: int = CAL_SEED, reps: int = CAL_REPS) -> dict:
    """H1.4: demeaned training series -> circular MBB at the judgment G and
    gap vector -> 99th percentile of signed replicate t; the MDE is δ-shifted
    on the SAME replicates (no second seed), 0.01-ATR grid, >=80% power."""
    judg_gaps = np.asarray(judg_gaps)
    x = np.asarray(train_x, np.float64)
    if len(x) < 2:
        raise SystemExit(f"training series has {len(x)} dates — cannot calibrate")
    if not np.isfinite(x).all():
        raise SystemExit("non-finite value in the training series — data fault")
    xc = x - x.mean()                                         # demeaned to zero
    rng = np.random.default_rng(seed)
    idx = h1_blocks(rng, len(xc), len(judg_gaps), reps)
    ts, means, ses, fb = _nw_batch(xc[idx], judg_gaps)
    if not np.isfinite(ts).all():
        raise SystemExit(
            f"{int((~np.isfinite(ts)).sum())} of {reps} calibration replicates "
            "produced a non-finite t — a degenerate replicate is a fault, "
            "never a quiet UNEVALUABLE")
    cutoff = float(np.percentile(ts, 99.0))
    mde = None
    for k in range(1, int(MDE_SEARCH_MAX / MDE_STEP) + 1):
        delta = round(k * MDE_STEP, 2)
        if float(((means + delta) / ses >= cutoff).mean()) >= MDE_POWER:
            mde = delta
            break
    ac1 = float(np.corrcoef(x[:-1], x[1:])[0, 1]) if len(x) > 2 else None
    return {
        "seed": seed, "reps": reps, "block": BLOCK,
        "cutoff_t99": cutoff,
        "replicate_fallback_share": float(fb.mean()),
        "train_dates": int(len(x)),
        "train_mean": float(x.mean()),
        "train_sd_ddof1": float(np.std(x, ddof=1)),
        "train_autocorr_lag1": ac1,
        "judgment_G": int(len(judg_gaps)),
        "mde_atr": mde,
        "mde_search_max": MDE_SEARCH_MAX,
        "mde_above_search_max": bool(mde is None),            # ">2.00" marker
        "power_limited": bool(mde is None or mde > MDE_CEILING),
    }


def placebo(event_signs: list[np.ndarray], event_mags: list[np.ndarray],
            base_means: np.ndarray, gaps: np.ndarray, t_real: float,
            seed: int = PLACEBO_SEED, reps: int = PLACEBO_REPS) -> dict:
    """H1.2's registered diagnostic: EVENT tilt signs permuted WITHIN date,
    baseline untouched; p floored at ~1/(reps+1). Changes no verdict."""
    rng = np.random.default_rng(seed)
    pairs = _gap_pairs(np.asarray(gaps))
    diffs = np.empty((reps, len(base_means)))
    for r in range(reps):
        for k, (s, m, b) in enumerate(zip(event_signs, event_mags, base_means)):
            diffs[r, k] = float((s[rng.permutation(len(s))] * m).mean()) - b
    ts, _, _, _ = _nw_batch(diffs, np.asarray(gaps), pairs)
    exceed = int((ts >= t_real).sum())
    return {"seed": seed, "reps": reps, "t_real": t_real,
            "p_upper": (1 + exceed) / (reps + 1),
            "perm_t_mean": float(ts.mean()),
            "perm_t_sd": float(np.std(ts, ddof=1))}


# ------------------------------ windows, arms, series ------------------------------

class WindowResult(NamedTuple):
    dates: list[str]              # primary series dates (G)
    gaps: np.ndarray              # SPY-calendar index per series date
    diffs: np.ndarray             # primary per-date difference series (10td)
    census: dict
    arms: dict


def _window_indices(panel: Panel, which: str) -> list[int]:
    iso = [s.isoformat() for s in panel.sessions]
    if which == "train":
        return [j for j, s in enumerate(iso) if TRAIN_START <= s <= TRAIN_END]
    if which == "judgment":
        idxs = [j for j, s in enumerate(iso)
                if JUDGMENT_START <= s <= JUDGMENT_END
                and s not in JUDGMENT_EXCLUDE]
        if len(idxs) != JUDGMENT_DATES:
            raise SystemExit(f"judgment window selects {len(idxs)} dates, "
                             f"registration pins {JUDGMENT_DATES}")
        return idxs
    raise ValueError(which)


def build_window(panel: Panel, fo: dict, which: str, mode: str) -> WindowResult:
    """Population, arms, and (mode-dependent) the primary series for one window.

    mode = "census":  population + coverage only — no DAILY bar is read (the
                      5-minute store is read for the rv_park condition).
    mode = "cases":   + the four-case rule from bar AVAILABILITY (P10) — the
                      series dates G and gap vector exist, but no alpha does.
    mode = "outcomes": + alphas and the per-date difference series.
    """
    if mode not in ("census", "cases", "outcomes"):
        raise ValueError(mode)
    idxs = _window_indices(panel, which)
    rv = rv_for_sessions(panel, idxs)
    census: dict = {
        "window": which, "sessions": len(idxs), "mode": mode,
        "vol_floor_contracts": VOL_FLOOR, "premium_floor_usd": PREMIUM_FLOOR,
        "per_date": [], "events_condition_total": 0, "events_gradeable": 0,
        "event_dates_gradeable": 0,                           # P11 date bar
        "event_zero_tilt": 0, "baseline_zero_tilt": 0,
        "ineligible_no_oi": 0, "ineligible_no_rv": 0,
        "no_rv_dotted_names": [],                             # P5 disclosure
        "instrument_dead_dates": [], "empty_population_dates": [],
        "ungradeable": {"ii": 0, "iii": 0, "iv": 0},
        "ungradeable_premium_usd": 0.0, "truncated_10td": 0,
        "grid_not_elapsed": {str(h): 0 for h in H_GRID},
        "grid_observations": {str(h): 0 for h in H_GRID},
        "tier_composition": {"event": {}, "baseline": {}},    # per ARM
    }
    pops: dict[int, SessionPop] = {}
    live_dates: list[int] = []
    dotted_no_rv: set[str] = set()
    for j in idxs:
        pop = session_population(panel, rv[j], fo, j)
        pops[j] = pop
        s_iso = panel.sessions[j].isoformat()
        census["ineligible_no_oi"] += pop.n_no_oi
        census["ineligible_no_rv"] += pop.n_no_rv
        for i in np.nonzero(pop.oi_ok & ~np.isfinite(pop.rv_raw))[0]:
            n = str(panel.names[i])
            if "." in n:
                dotted_no_rv.add(n)
        row = {"date": s_iso, "base": int(pop.base.sum()),
               "ranked": int(pop.ranked.sum()),
               "coverage": None if np.isnan(pop.coverage) else pop.coverage}
        quiet = pop.ranked & (pop.rv_pct <= RV_CUT)
        ev = quiet & (pop.f_pct >= F_OPEN_CUT)
        row.update(events=int(ev.sum()), baseline=int((quiet & ~ev).sum()))
        # realized percentile cuts (H1.6 #3): the f_open value AT the p80
        # boundary of the RANKED cross-section (not the event intersection)
        # and the rv_park value AT the p33 boundary
        top = pop.ranked & (pop.f_pct >= F_OPEN_CUT)
        row["f_open_cut_value"] = (float(pop.f_raw[top].min())
                                   if top.any() else None)
        row["rv_cut_value"] = (float(pop.rv_raw[quiet].max())
                              if quiet.any() else None)
        census["per_date"].append(row)
        if np.isnan(pop.coverage):
            census["empty_population_dates"].append(s_iso)
            continue
        if which == "judgment" and pop.coverage < COVERAGE_MIN:   # P3
            census["instrument_dead_dates"].append(s_iso)    # dropped and counted
            continue
        census["events_condition_total"] += int(ev.sum())
        live_dates.append(j)
    census["no_rv_dotted_names"] = sorted(dotted_no_rv)
    if mode == "census":
        return WindowResult([], np.array([], int), np.array([]), census,
                            {"event_signs": [], "event_mags": [],
                             "base_means": np.array([]),
                             "event_means": np.array([]), "magnitude": [],
                             "joint": [], "grid_diffs": {},
                             "event_members": [], "net_signed_share": {},
                             "per_arm_scale": {}})

    cal_dates, spy_o, spy_c = spy_calendar()
    check_benchmark_continuity(panel, cal_dates)
    cal_pos = {d: i for i, d in enumerate(cal_dates)}
    last_idx = len(cal_dates) - 1
    quint = _trailing_quintiles(panel.prem)
    rc = classification()
    needed: set[str] = set()
    for j in live_dates:
        needed.update(panel.names[pops[j].ranked].tolist())
    sibs = {f for n in needed for f in (rc.occ_form(n), rc.directory_form(n))
            if f}
    bars_all = daily_bars(needed | sibs)

    def bars_for(name: str):
        for key in (name, rc.directory_form(name), rc.occ_form(name)):  # P5
            b = bars_all.get(key) if key else None
            if b is not None and len(b[0]):
                return b
        return None

    with_alpha = mode == "outcomes"
    dates: list[str] = []
    gaps: list[int] = []
    diffs: list[float] = []
    ev_signs: list[np.ndarray] = []
    ev_mags: list[np.ndarray] = []
    base_means: list[float] = []
    event_means: list[float] = []
    scale: dict = {a: {"open_over_atr": [], "atr_over_price": []}
                   for a in ("event", "baseline")}
    magnitude: list[tuple[str, int, float]] = []
    joint: list[tuple[str, int, float]] = []
    grid_diffs: dict[str, list] = {str(hz): [] for hz in H_GRID}
    event_members: list[tuple[str, list[int]]] = []
    net_signed_share: dict[str, float] = {}
    for j in live_dates:
        pop = pops[j]
        s_iso = panel.sessions[j].isoformat()
        k0 = cal_pos[s_iso]                                   # continuity-checked
        quiet = pop.ranked & (pop.rv_pct <= RV_CUT)
        ev_mask = quiet & (pop.f_pct >= F_OPEN_CUT)
        ba_mask = quiet & ~ev_mask
        joint_mask = pop.ranked & ~ev_mask                    # secondary (a)
        signs = np.sign(panel.tilt[:, j])
        graded: dict[int, Graded] = {}
        for i in np.nonzero(pop.ranked)[0]:
            name = str(panel.names[i])
            try:
                graded[int(i)] = grade_name_day(
                    bars_for(name), cal_pos, spy_o, spy_c, k0, last_idx,
                    with_alpha)
            except BlockingHole as e:
                raise SystemExit(
                    f"interior bar hole ({s_iso}, {name}): {e} — a BLOCKING "
                    "data fault referred to the vendor re-fetch (G.2/P8)"
                ) from e

        def members(mask: np.ndarray) -> list[int]:
            return [int(i) for i in np.nonzero(mask)[0]]

        for i in members(ev_mask) + members(ba_mask):
            gr = graded[i]
            if gr.case in census["ungradeable"]:
                census["ungradeable"][gr.case] += 1
                census["ungradeable_premium_usd"] += float(panel.prem[i, j])

        def usable(mask: np.ndarray, need_sign: bool) -> list[int]:
            out = []
            for i in members(mask):
                gr = graded[i]
                if gr.case not in ("full", "truncated"):
                    continue
                if gr.covered.get(HORIZON) == "not_elapsed":
                    raise SystemExit(
                        f"10td horizon not elapsed for {panel.names[i]} at "
                        f"{s_iso} — the run is premature (P9)")
                if need_sign and signs[i] == 0.0:
                    continue
                out.append(i)
            return out

        ev_u = usable(ev_mask, need_sign=True)
        ba_u = usable(ba_mask, need_sign=True)
        census["event_zero_tilt"] += sum(
            1 for i in members(ev_mask)
            if graded[i].case in ("full", "truncated") and signs[i] == 0.0)
        census["baseline_zero_tilt"] += sum(
            1 for i in members(ba_mask)
            if graded[i].case in ("full", "truncated") and signs[i] == 0.0)
        census["truncated_10td"] += sum(
            1 for i in ev_u + ba_u if graded[i].covered.get(HORIZON) == "truncated")
        for hz in H_GRID:
            for i in ev_u + ba_u:
                cov = graded[i].covered.get(hz)
                if cov == "not_elapsed":
                    census["grid_not_elapsed"][str(hz)] += 1
                elif cov is not None:
                    census["grid_observations"][str(hz)] += 1
        for arm, lst in (("event", ev_u), ("baseline", ba_u)):
            scale[arm]["open_over_atr"] += [graded[i].open_over_atr for i in lst]
            scale[arm]["atr_over_price"] += [graded[i].atr_over_price for i in lst]
            tc = census["tier_composition"][arm]
            for i in lst:
                qk = str(int(quint[i, j]))
                tc[qk] = tc.get(qk, 0) + 1
        if ev_u:
            census["event_dates_gradeable"] += 1              # P11 date bar
            event_members.append((s_iso, ev_u))
            net_signed_share[s_iso] = float(np.mean([signs[i] for i in ev_u]))
        if with_alpha:
            mag_e = [graded[i].alpha[HORIZON] for i in members(ev_mask)
                     if graded[i].case in ("full", "truncated")]
            mag_b = [graded[i].alpha[HORIZON] for i in members(ba_mask)
                     if graded[i].case in ("full", "truncated")]
            if mag_e and mag_b:
                magnitude.append((s_iso, k0, float(
                    np.abs(np.array(mag_e)).mean() - np.abs(np.array(mag_b)).mean())))
            jt_u = usable(joint_mask, need_sign=True)
            if ev_u and jt_u:
                e_s = np.array([signs[i] * graded[i].alpha[HORIZON] for i in ev_u])
                jt_signed = np.array([signs[i] * graded[i].alpha[HORIZON]
                                      for i in jt_u])
                joint.append((s_iso, k0,
                              float(e_s.mean() - jt_signed.mean())))
        if not ev_u or not ba_u:      # a date needs >=1 gradeable of EACH arm
            continue
        census["events_gradeable"] += len(ev_u)               # P11 event bar
        dates.append(s_iso)
        gaps.append(k0)
        if with_alpha:
            e_signed = np.array([signs[i] * graded[i].alpha[HORIZON] for i in ev_u])
            b_signed = np.array([signs[i] * graded[i].alpha[HORIZON] for i in ba_u])
            diffs.append(float(e_signed.mean() - b_signed.mean()))
            ev_signs.append(signs[np.array(ev_u)])
            ev_mags.append(np.array([graded[i].alpha[HORIZON] for i in ev_u]))
            base_means.append(float(b_signed.mean()))
            event_means.append(float(e_signed.mean()))
            for hz in H_GRID:                                 # grid fully reported
                he = [signs[i] * graded[i].alpha[hz] for i in ev_u
                      if graded[i].alpha[hz] is not None]
                hb = [signs[i] * graded[i].alpha[hz] for i in ba_u
                      if graded[i].alpha[hz] is not None]
                if he and hb:
                    grid_diffs[str(hz)].append(
                        (s_iso, k0, float(np.mean(he) - np.mean(hb))))
    census["series_dates"] = len(dates)
    diffs_arr = np.array(diffs)
    if with_alpha and len(diffs_arr) and not np.isfinite(diffs_arr).all():
        raise SystemExit("non-finite value in the primary series — data fault, "
                         "never a verdict input")
    per_arm_scale = {
        arm: {k: (float(np.mean([v for v in vals if v is not None]))
                  if any(v is not None for v in vals) else None)
              for k, vals in d.items()}
        for arm, d in scale.items()}
    return WindowResult(
        dates, np.array(gaps, int), diffs_arr, census,
        {"event_signs": ev_signs, "event_mags": ev_mags,
         "base_means": np.array(base_means),
         "event_means": np.array(event_means), "magnitude": magnitude,
         "joint": joint, "grid_diffs": grid_diffs,
         "event_members": event_members,
         "net_signed_share": net_signed_share,
         "per_arm_scale": per_arm_scale})


# ------------------------------ record / stages ------------------------------

def _environment() -> dict:
    def git(*args):
        try:
            return subprocess.run(["git", *args], cwd=ROOT, capture_output=True,
                                  text=True, timeout=10).stdout.strip()
        except Exception:
            return None
    status = git("status", "--porcelain")
    return {"git_hash": git("rev-parse", "HEAD"),
            "git_dirty": None if status is None else bool(status),
            "numpy": np.__version__, "python": sys.version.split()[0],
            "platform": platform.platform(),
            "run_date": date.today().isoformat()}


def _gate(stage: str, today: date | None = None) -> None:
    """P7 — the registered calendar gates, enforced in code."""
    today = today or date.today()
    if stage in ("record", "calibrate", "score") and today < EARLIEST_RUN:
        raise SystemExit(
            f"stage '{stage}' is registered to run no earlier than the close "
            f"of 2026-08-07 (H1.3/H1.6); today is {today.isoformat()} — refusing")


def _load_record() -> dict:
    if RECORD_PATH.exists():
        rec = json.loads(RECORD_PATH.read_text())
        rec.setdefault("stages", {})
        return rec
    return {"prereg": PREREG, "stages": {}}


def _save_record(rec: dict) -> None:
    tmp = RECORD_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(rec, indent=1, sort_keys=True))
    os.replace(tmp, RECORD_PATH)                              # atomic
    logger.info("record written: %s", RECORD_PATH)


def _window_record(res: WindowResult) -> dict:
    return {**res.census,
            "series_dates_list": res.dates,
            "gap_vector": res.gaps.tolist(),
            "net_signed_share": res.arms["net_signed_share"],
            "per_arm_scale": res.arms["per_arm_scale"]}


def stage_eligibility() -> dict:
    """TRAINING-window input-property census — runnable any time (P7).

    The judgment-window halves are H1.6 #3 content and live in the gated
    `record` stage.
    """
    panel = build_panel()
    fo = f_open_by_identity(resolve=_cached_resolver(classification()))
    return {"train": build_window(panel, fo, "train", mode="census").census}


def stage_record() -> dict:
    """H1.6 #3 — the input-property record (first run governs).

    Runs in "cases" mode (P10): the four-case rule from bar availability, the
    date count G and gap vector, censuses, realized cuts, net signed share,
    per-arm tier composition and scale, regime span, the G-fired overlap, and
    the inputs' provenance — and NO alpha anywhere.
    """
    _gate("record")
    rec = _load_record()
    if "record" in rec["stages"]:
        raise SystemExit("H1.6 #3 already recorded — first run governs")
    panel = build_panel()
    fo = f_open_by_identity(resolve=_cached_resolver(classification()))
    cal_dates, spy_o, spy_c = spy_calendar()
    check_benchmark_continuity(panel, cal_dates)
    cal_pos = {d: i for i, d in enumerate(cal_dates)}
    files = panel_files(panel.sessions)
    out: dict = {
        "environment": _environment(),
        "panel": {"first": panel.sessions[0].isoformat(),
                  "last": panel.sessions[-1].isoformat(),
                  "sessions": len(panel.sessions),
                  "identities": len(panel.names)},
        "cache_provenance": _cache_provenance(files),
        "bars5_store": {"files": len(list(BARS5_DIR.glob("*.npz"))),
                        "last": max(p.stem for p in BARS5_DIR.glob("*.npz"))},
        "notes": [
            "drafting reconciliation: with the 07-24 reconcile extension, "
            "2026-07-24 instrument coverage measures 81.4% — inside the "
            "70-82% band the prereg's tripwire note declared empty; the gate "
            "outcome is unchanged (all 37 dates clear 70%)",
            "the prereg's tie-group count (line 58-63) was measured on the "
            "1,186-member raw OI cross-section, not the registered "
            "population; the stable-sort convention resolves ties "
            "deterministically either way",
            "regime-span rule evaluated on the primary series dates (P12)",
        ],
    }
    results = {}
    for w in ("train", "judgment"):
        res = build_window(panel, fo, w, mode="cases")
        results[w] = res
        out[w] = _window_record(res)
    judg = results["judgment"]
    spans = {}
    for s_iso in judg.dates:
        k0 = cal_pos[s_iso]
        if k0 + HORIZON >= len(cal_dates):
            raise SystemExit(f"SPY bars end before {s_iso}+10td — the record "
                             "cannot run yet (benchmark blocking, G.2)")
        spans[s_iso] = float(spy_c[k0 + HORIZON] / spy_o[k0 + 1] - 1.0)
    out["spy_forward_returns"] = spans
    out["regime_buckets"] = {
        "le_-2pct": [d for d, r in spans.items() if r <= -REGIME_CUT],
        "open_interval": [d for d, r in spans.items()
                          if -REGIME_CUT < r < REGIME_CUT],
        "ge_+2pct": [d for d, r in spans.items() if r >= REGIME_CUT]}
    # H1-event x G-fired overlap on the judgment window (input-side)
    eligible = panel.prem >= PREMIUM_FLOOR
    fired, _ = _fire(panel.tilt, panel.present, eligible)
    si = {s.isoformat(): j for j, s in enumerate(panel.sessions)}
    out["h1_event_x_g_fired_overlap"] = sum(
        int(fired[np.array(mem), si[s_iso]].sum())
        for s_iso, mem in judg.arms["event_members"])
    rec["stages"]["record"] = out
    _save_record(rec)
    return out


def stage_calibrate() -> dict:
    """H1.6 #4 — the G.4 calibration + MDE, computed AT #3's recorded inputs."""
    _gate("calibrate")
    rec = _load_record()
    if "record" not in rec["stages"]:
        raise SystemExit("H1.6 ordering: run --stage record first (#3 precedes #4)")
    if "calibrate" in rec["stages"]:
        raise SystemExit("H1.6 #4 already recorded — first run governs")
    panel = build_panel()
    fo = f_open_by_identity(resolve=_cached_resolver(classification()))
    train = build_window(panel, fo, "train", mode="outcomes")
    recorded_train = rec["stages"]["record"]["train"]
    if train.dates != recorded_train["series_dates_list"]:
        raise SystemExit(
            "training series dates diverge from the #3 record — the inputs "
            "changed between #3 and #4; recorded "
            f"{len(recorded_train['series_dates_list'])} dates, got "
            f"{len(train.dates)}. Blocking (P10).")
    judg_gaps = np.array(rec["stages"]["record"]["judgment"]["gap_vector"], int)
    if len(judg_gaps) < 2:
        raise SystemExit("#3 recorded a judgment gap vector shorter than 2 — "
                         "UNEVALUABLE territory, not calibratable")
    out = {"environment": _environment(),
           "train_series_dates": train.dates,
           "train_series": train.diffs.tolist(),
           # H1.2 pre-commitment: the training-window H1 COHORT alpha (the
           # event arm's own per-date-mean level), beside the diff series
           "train_event_cohort_alpha": float(train.arms["event_means"].mean()),
           "train_baseline_alpha": float(train.arms["base_means"].mean()),
           **calibrate(train.diffs, judg_gaps)}
    rec["stages"]["calibrate"] = out
    _save_record(rec)
    return out


def stage_score(g_verdict: str) -> dict:
    """H1.6 #5 — the one-shot scoring run, strictly after the G verdict."""
    _gate("score")
    gv = Path(g_verdict)
    if not gv.exists():
        raise SystemExit(f"Addendum G verdict artifact not found: {gv} — the "
                         "scoring run executes STRICTLY AFTER that verdict (H1.3)")
    rec = _load_record()
    for need in ("record", "calibrate"):
        if need not in rec["stages"]:
            raise SystemExit(f"H1.6 ordering: stage '{need}' must precede score")
    if "score" in rec["stages"]:
        raise SystemExit("H1 scoring already ran — first run governs, no re-run")
    panel = build_panel()
    fo = f_open_by_identity(resolve=_cached_resolver(classification()))
    judg = build_window(panel, fo, "judgment", mode="outcomes")
    recorded = rec["stages"]["record"]["judgment"]
    if judg.dates != recorded["series_dates_list"]:
        raise SystemExit(
            "judgment series dates diverge from the #3 record — data changed "
            "under the frozen inputs. Recorded as a fault, not re-run.")
    if judg.gaps.tolist() != recorded["gap_vector"]:          # P10
        raise SystemExit(
            "judgment gap vector diverges from the #3 record — the benchmark "
            "calendar changed between #3 and #5. Blocking.")
    cal = rec["stages"]["calibrate"]
    regime = rec["stages"]["record"]["regime_buckets"]
    n_event_dates = judg.census["event_dates_gradeable"]      # P11
    n_events = judg.census["events_gradeable"]
    checklist = {
        "event_dates_gradeable": n_event_dates,
        "min_event_dates": MIN_EVENT_DATES,
        "series_dates": len(judg.dates),
        "events_gradeable": n_events, "min_events": MIN_EVENTS,
        "regime_buckets": {k: len(v) for k, v in regime.items()},
        "power_limited": cal["power_limited"],
        "instrument_dead_dates": judg.census["instrument_dead_dates"],
    }
    # shape-invariance disclosure (H1.6 #3 clause) — unconditional, on the face
    def _dist(census):
        dead = (set(census["empty_population_dates"])
                | set(census["instrument_dead_dates"]))
        counts = sorted(r["events"] for r in census["per_date"]
                        if r.get("events") is not None and r["date"] not in dead)
        return {"min": counts[0], "median": counts[len(counts) // 2],
                "max": counts[-1]} if counts else None
    shape = {"train_events_per_date": _dist(rec["stages"]["record"]["train"]),
             "judgment_events_per_date": _dist(judg.census)}
    evaluable = (n_event_dates >= MIN_EVENT_DATES and n_events >= MIN_EVENTS
                 and all(len(v) for v in regime.values())
                 and not cal["power_limited"])
    out: dict = {
        "environment": _environment(),
        "checklist": checklist,
        "shape_disclosure": shape,
        "per_arm_scale": {"judgment": judg.arms["per_arm_scale"],
                          "train":
                          rec["stages"]["record"]["train"]["per_arm_scale"]},
        "census": judg.census,
        "train_cohort_precommitment": {
            "event_cohort_alpha": cal["train_event_cohort_alpha"],
            "baseline_alpha": cal["train_baseline_alpha"],
            "diff_series_mean": cal["train_mean"],
            "note": "published alongside the test result per H1.2"},
    }
    if not evaluable:
        reasons = []
        if cal["power_limited"]:
            reasons.append("under-powered (POWER-LIMITED)")
        if n_event_dates < MIN_EVENT_DATES or n_events < MIN_EVENTS:
            reasons.append("under-powered (count failure)")
        if not all(len(v) for v in regime.values()):
            reasons.append("regime-span unevaluable")
        if judg.census["instrument_dead_dates"]:
            reasons.append("instrument-limited (dead dates)")
        out["verdict"] = (
            f"UNEVALUABLE [{'; '.join(reasons)}] — H1 did not resolve; no "
            "substitute cell, no re-declaration, no re-run (H1.5; NOT a "
            "fair-trial discharge)")
        rec["stages"]["score"] = out
        _save_record(rec)
        return out
    t, mean, se, fb = nw_stats(judg.diffs, judg.gaps)
    if not (np.isfinite(t) and np.isfinite(cal["cutoff_t99"])):
        raise SystemExit(f"non-finite test statistic (t={t}, cutoff="
                         f"{cal['cutoff_t99']}) — computation fault, never a "
                         "verdict")
    if t >= cal["cutoff_t99"]:
        out["verdict"] = (
            "ALIVE (H1: opening-heavy ∧ quiet-tape, 10td, equity) — "
            "attributable to the JOINT conditioning of the tcbbo-derived tilt "
            "sign on f_open, the marginal-contribution claim of H1.2; carries "
            "the per-arm scale imbalance on this record as its attribution "
            "caveat")
    else:
        out["verdict"] = (
            "DEAD — opening-heavy flow on a quiet tape carries no directional "
            "edge at 10td over an equally-quiet non-opening-heavy baseline, "
            "ON THE T+1 AGGREGATE PROXY")
    pl = placebo(judg.arms["event_signs"], judg.arms["event_mags"],
                 judg.arms["base_means"], judg.gaps, t)
    keep = np.array([d not in MONTHLY_EXPIRIES for d in judg.dates])
    t_ex = (nw_stats(judg.diffs[keep], judg.gaps[keep])[0]
            if int(keep.sum()) > 1 else None)

    def _sec(series):
        if len(series) < 2:
            return None
        x = np.array([m[2] for m in series])
        gp = np.array([m[1] for m in series])
        tt, mm, _, _ = nw_stats(x, gp)
        return {"t": tt, "mean": mm, "dates": len(series)}
    grid_report = {}
    for hz in H_GRID:
        rows = judg.arms["grid_diffs"][str(hz)]
        grid_report[str(hz)] = {
            "contrast": _sec(rows),
            "not_elapsed": judg.census["grid_not_elapsed"][str(hz)],
            "observations": judg.census["grid_observations"][str(hz)],
        }
    out["primary"] = {"t": t, "mean": mean, "se": se, "nw_fallback": bool(fb),
                      "cutoff_t99": cal["cutoff_t99"], "mde_atr": cal["mde_atr"],
                      "series_dates": judg.dates, "series": judg.diffs.tolist()}
    out["secondaries"] = {
        "joint": _sec(judg.arms["joint"]),
        "magnitude": _sec(judg.arms["magnitude"]),
        "ex_monthly_t": t_ex,
        "grid": grid_report,
        "note": "reported, labelled, NO kill authority",
    }
    out["placebo"] = pl
    rec["stages"]["score"] = out
    _save_record(rec)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(
        description="H1 quiet-tape registered implementation (prereg v5)")
    ap.add_argument("--stage", required=True,
                    choices=["eligibility", "record", "calibrate", "score"])
    ap.add_argument("--g-verdict", default=None,
                    help="path to the recorded Addendum G verdict artifact "
                         "(required for --stage score)")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    if args.stage == "eligibility":
        print(json.dumps(stage_eligibility(), indent=1))
    elif args.stage == "record":
        stage_record()
    elif args.stage == "calibrate":
        stage_calibrate()
    else:
        if not args.g_verdict:
            raise SystemExit("--stage score requires --g-verdict (H1.3: strictly "
                             "after the Addendum G verdict is recorded)")
        stage_score(args.g_verdict)


if __name__ == "__main__":
    main()
