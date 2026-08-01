#!/usr/bin/env python3
"""Phase 3.3 — does the hedge-footprint label predict aligned forward drift?

Spec: `References/FLOW_ROADMAP.md` → "3.3 SPEC v2 — REGISTERED 2026-08-01". That
block IS LAW; this module implements it clause by clause. Where the spec is
genuinely ambiguous the reading taken is named in a `SPEC AMBIGUITY` comment and
echoed into the artifact's `spec_ambiguities` list — never resolved silently.

    The question: HIGH (footprint score s ≥ +3) ⇒ a dealer absorbed a real
    outside risk-taker's bet, sometimes informed ⇒ aligned forward drift. NULL
    (|s| < 3) ⇒ plausibly customer-vs-customer ⇒ ≈ 0 drift (an EQUIVALENCE
    claim, never an accepted null). ANTI (s ≤ −3) is reported, never pooled.

★ THE SEAL. Forward returns of the 117 frozen events are SEALED until Gate 1
passes. Three mechanical locks, each pinned by a test:

  1. every stage verifies `hedge_scores_frozen_3p3.json` against the registered
     SHA-256 and aborts on mismatch;
  2. `gate1` reads ONLY on-disk data and only STRICTLY BEFORE each event's own
     session — `closes_before()` raises if asked for anything later;
  3. `unseal` refuses to run without `gate1_3p3.json` carrying `proceed: true`,
     and `analyze` refuses without the unseal artifact. Exit code 1 either way.

Three resumable stages, artifacts under `data/research/hedge_footprint/equity/`:

    gate1    Controls-only power gate. Pseudo-events from the already-unblinded
             control-day marks + the flow corpus's own daily bars (bounded to
             strictly before each event). δ injected into HIGH pseudo-events
             only over the registered grid; a replication counts as DETECTED
             only if the FULL registered PREDICTIVE conjunction fires. Floor:
             MDE ≤ 1.5% aligned drift at h2, 80% power.
    unseal    THE outcome fetch — the first time this study reads a forward
             price. Daily bars in BOTH adjustment spaces (the per-event factor
             f_i = close_all/close_raw), per-mark minute bars, SPY marks and
             closes, symbols resolved date-scoped through the corporate-actions
             `name_change` feed. Hard continuity tripwire per event.
    analyze   y per spec at three marks × three horizons (+ two reported-only
             legs), cluster/week-block label permutation, exact minP over the
             two co-primaries, stratified cluster bootstrap, the m-residualized
             contrast, leave-one-week-out jackknife, the NULL equivalence test,
             the Gate 2 realized-MDE re-gate, and the registered verdict table.

    .venv/bin/python tools/hedge_label_drift.py gate1
    .venv/bin/python tools/hedge_label_drift.py unseal
    .venv/bin/python tools/hedge_label_drift.py analyze

Read-only market data; no orders; nothing under `trading/`. Research code —
floats, not Decimal — but every return is computed in LOG space as registered.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import math
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import numpy as np
from scipy.stats import rankdata

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from config.settings import alpaca_config  # noqa: E402
from db.flow_corpus import DEFAULT_DB_PATH, connect  # noqa: E402
from tools.hedge_footprint import (  # noqa: E402
    _load_window,
    _paged_get,
    _rfc3339,
    nbbo_mid_at,
    prevailing_quote_idx,
    save_json,
    session_midnight_ns,
)
from tools.root_classification_build import fetch_name_changes  # noqa: E402

logger = logging.getLogger("hedge_label_drift")

SPEC = "FLOW_ROADMAP.md 3.3 SPEC v2 (REGISTERED 2026-08-01)"
OUT_DIR = ROOT / "data" / "research" / "hedge_footprint" / "equity"
_ET = ZoneInfo("America/New_York")

# --- the seal -----------------------------------------------------------------
FROZEN_NAME = "hedge_scores_frozen_3p3.json"
FROZEN_SHA256 = "104c184e4a20503c8ee6858b2b6efd5078852e07ee5875ab6d9a5aabba089e57"
GATE1_NAME = "gate1_3p3.json"
UNSEAL_NAME = "unseal_3p3.json"
RESULTS_NAME = "results_3p3.json"

# --- registered constants (every one is a spec clause) ------------------------
HIGH_THRESHOLD = 3.0                  # HIGH s ≥ +3, ANTI s ≤ −3, NULL between
SPLIT_BOUNDARY = date(2026, 5, 5)     # sealed-split boundary; windows truncate here
ALPHA = 0.01                          # one-sided
N_PERM = 50_000                       # registered floor
SEED = 20260801                       # registered seed
DELTA_MIN = 0.005                     # point Δ̂ ≥ 0.5%
EQUIV_BAND = 0.005                    # NULL equivalence: 95% CI inside ±0.5%
NULL_POSITIVE_ALPHA = 0.05            # GRADED trigger: NULL sign-flip p < 0.05
DELTA_GRID = (0.0025, 0.005, 0.0075, 0.010, 0.015, 0.020, 0.030)
GATE_MDE_FLOOR = 0.015                # MDE ≤ 1.5% aligned drift at h2
POWER_TARGET = 0.80
FLOOR_HIGH_EVENTS = 20
FLOOR_HIGH_UNDERLYINGS = 16
FLOOR_NULL_EVENTS = 65
CENSOR_MAX_FRAC = 0.05                # drops > 5% of either stratum ⇒ NO-VERDICT
BOOT_NONFINITE_MAX = 0.01             # > 1% non-finite draws ⇒ CI invalid
CONTINUITY_TRIPWIRE = 0.10            # |log(first forward close / M0_adj)|
MARK_PRE_S = -1                       # M_pre  = mid at T−1s
MARK_FAST_S = 60                      # M0_fast= mid at T+60s
MARK_M0_S = 300                       # M0     = mid at T+300s (primary anchor)
STALE_QUOTE_S = 60.0                  # no quote update within 60s → last trade
H1_OFFSET_S = MARK_M0_S + 30 * 60     # h1 = M0 → T+30min
HORIZON_SESSIONS = {"h2": 1, "h3": 5, "h4": 21}
H_EXP_CAP_SESSIONS = 30
BETA_LOOKBACK = 60                    # β̂ from 60 pre-sessions
SIGMA_LOOKBACK = 20                   # σ_pre / the 20-prior-session dispersion
VERDICT_HORIZONS = ("h1", "h2", "h3")
PRIMARY_HORIZON = "h2"
LABELLINGS = ("score", "score_trf_incl", "score_tick")
VERDICT_MARKS = ("M0", "M0_fast")
ALL_MARKS = ("M_pre", "M0", "M0_fast")

# --- mechanics ----------------------------------------------------------------
FEED = "sip"
PAGE_LIMIT = 10_000
N_BOOT = 2_000                        # ASSUMPTION — the spec fixes n_perm, not n_boot
MAD_TO_SD = 1.4826
MINUTE_PRE_S = 600                    # minute-bar window around each mark
MINUTE_POST_S = 2_400
MAX_RENAME_HOPS = 32

# Every reading this module had to choose. Echoed into every artifact so a
# reviewer sees them beside the numbers rather than having to find them here.
SPEC_AMBIGUITIES = [
    "Gate 2: the verdict table's one-liner reads 'NO-VERDICT[-UNDERPOWERED] = "
    "floors breached or Gate 2 failed' while the Gate 2 clause itself reads "
    "'realized MDE > 1.5% ⇒ any NON-SIGNIFICANT result routes "
    "NO-VERDICT-UNDERPOWERED and may NEVER route LABEL-EMPTY'. Implemented: the "
    "Gate 2 clause — a failed power gate downgrades only NON-significant "
    "outcomes; a significant co-primary still routes PREDICTIVE/GRADED/INVERTED. "
    "FLAGGED, not resolved.",
    "Continuity tripwire: 'first forward close' is read LITERALLY as the event "
    "session's own close (the first close after the T+300s mark). Note the "
    "algebra: close_all/(M0_raw·f_i) = close_raw/M0_raw, so the literal reading "
    "is blind to the adjustment join it was written to protect. The "
    "next-session ratio is computed and DISCLOSED per event "
    "(`continuity_next_session`) without halting. FLAGGED for the orchestrator.",
    "Calendar-week blocks: exact HIGH-cluster-count preservation and "
    "block-level movement are jointly unachievable when weeks hold different "
    "numbers of clusters. Implemented: blocks move together (the stated purpose "
    "— events in one week are dependent), and the realized HIGH-count "
    "distribution across draws is disclosed as `high_count_preserved_frac`.",
    "LABEL-GRADED is read as the full PREDICTIVE conjunction minus the NULL-is-"
    "zero claim (i.e. everything PREDICTIVE needs passes, but NULL's own mean is "
    "significantly positive), not as 'the co-primary alone passed'.",
    "Gate 1's PREDICTIVE conjunction covers the four simulable legs (minP "
    "co-primary, m-residualized, point Δ ≥ 0.5%, bootstrap CI lo > 0). Sign "
    "agreement across label variants/anchors and the jackknife are not "
    "simulable from controls and are excluded from the harness — which makes "
    "the reported MDE OPTIMISTIC relative to the full rule.",
    "Gate 1's SPY leg uses SPY close-to-close over the pseudo-window (no SPY "
    "intraday marks are on disk). The mark mismatch ADDS variance, so the MDE "
    "estimate is conservative in that direction.",
    "The flow corpus's `equity_bars` already holds daily closes through "
    "2026-07-24 for every event symbol — the seal is PROCEDURAL (no forward "
    "return has been computed), not physical. Gate 1 therefore fetches nothing "
    "at all and bounds every corpus read to strictly before the event session, "
    "which is stricter than the registered 'end date ≤ the event session'.",
]


class SealError(RuntimeError):
    """A seal lock refused. Never caught inside this module — it exits 1."""


class TripwireError(RuntimeError):
    """The registered continuity tripwire fired — the run HALTS with the event."""


# =============================================================================
# the seal
# =============================================================================

def verify_frozen(out_dir: Path) -> dict:
    """Load the frozen label file, aborting unless it hashes to the registered
    SHA-256. Every stage calls this first: the labels are the registration, and
    a study run against a different label file is a different study."""
    path = out_dir / FROZEN_NAME
    if not path.exists():
        raise SealError(f"frozen label file missing: {path}")
    raw = path.read_bytes()
    got = hashlib.sha256(raw).hexdigest()
    if got != FROZEN_SHA256:
        raise SealError(
            f"FROZEN LABEL SHA MISMATCH: {path} hashes to {got}, registered "
            f"{FROZEN_SHA256} — this is not the registered study")
    return json.loads(raw)


def require_gate1(out_dir: Path) -> dict:
    """`unseal`'s lock: no outcome is fetched until Gate 1 said proceed."""
    path = out_dir / GATE1_NAME
    if not path.exists():
        raise SealError(
            f"SEALED: {path} does not exist — run `gate1` before `unseal`. "
            "Forward returns stay sealed until the power gate passes.")
    doc = json.loads(path.read_text())
    if doc.get("proceed") is not True:
        raise SealError(
            f"SEALED: {path} carries proceed={doc.get('proceed')!r} — the "
            "registered ladder applies; nothing is unsealed.")
    return doc


def require_unseal(out_dir: Path) -> dict:
    """`analyze`'s lock: no outcome is analysed before it was legitimately
    fetched."""
    path = out_dir / UNSEAL_NAME
    if not path.exists():
        raise SealError(
            f"SEALED: {path} does not exist — run `unseal` before `analyze`.")
    return json.loads(path.read_text())


# =============================================================================
# small pure helpers
# =============================================================================

def session_of(ts_ns: int) -> str:
    """The ET session an epoch-ns mark belongs to. ET, never the host's clock:
    every control timestamp was BUILT as ET-midnight + an offset, so reading it
    back in local time silently rolls the date for anything after 19:00 UTC."""
    return datetime.fromtimestamp(int(ts_ns) / 1e9, tz=_ET).date().isoformat()


def week_id(session: str) -> str:
    """ISO calendar week — the registered block unit at h2/h3."""
    iso = date.fromisoformat(session).isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def robust_sd(x) -> float:
    """1.4826 × MAD. Robust because a handful of the 117 are thin names whose
    return distribution has a tail the sample SD would chase."""
    a = np.asarray(x, dtype=np.float64)
    a = a[np.isfinite(a)]
    if len(a) < 2:
        return float("nan")
    mad = float(np.median(np.abs(a - np.median(a))))
    if mad > 0.0:
        return MAD_TO_SD * mad
    sd = float(np.std(a, ddof=1))
    return sd if sd > 0.0 else float("nan")


def log_ret(p0: float, p1: float) -> float:
    if not (math.isfinite(p0) and math.isfinite(p1)) or p0 <= 0.0 or p1 <= 0.0:
        return float("nan")
    return math.log(p1 / p0)


def aligned_excess(px0: float, px1: float, spy0: float, spy1: float,
                   h_sign: float, beta: float = 1.0) -> float:
    """The registered outcome kernel: customer-direction-aligned log return,
    market-subtracted at IDENTICAL marks.

    `beta = 0` is the reported raw leg; `beta = 1` the SPY-subtracted leg;
    `beta = β̂` the 60-pre-session beta-adjusted leg.
    """
    r = log_ret(px0, px1)
    if beta == 0.0:
        return h_sign * r
    m = log_ret(spy0, spy1)
    return h_sign * (r - beta * m)


def adjustment_factor(close_all: float, close_raw: float) -> float:
    """f_i = close_all / close_raw on the EVENT session — the one number that
    carries a raw intraday quote into the `adjustment=all` price space the
    forward closes live in. Mixing the two spaces was measured at 244–656 bps of
    pure bookkeeping error, 5–13× the decision floor."""
    if not (math.isfinite(close_all) and math.isfinite(close_raw)) or close_raw <= 0.0:
        return float("nan")
    return close_all / close_raw


def continuity_ratio(first_forward_close_adj: float, m0_adj: float) -> float:
    return abs(log_ret(m0_adj, first_forward_close_adj))


@dataclass(frozen=True)
class Mark:
    price: float
    source: str          # "mid" | "last_trade" | "missing"
    age_s: float
    stale: bool


def mark_at(quotes: dict, trades: dict, ts_ns: int,
            stale_s: float = STALE_QUOTE_S) -> Mark:
    """NBBO mid at `ts_ns`, with the registered staleness rule: no quote update
    within 60s → the last trade, FLAGGED."""
    idx = prevailing_quote_idx(np.array([ts_ns], dtype=np.int64), quotes["t_ns"])[0]
    age = ((ts_ns - int(quotes["t_ns"][idx])) / 1e9) if idx >= 0 else float("inf")
    mid = nbbo_mid_at(quotes, ts_ns)
    if idx >= 0 and math.isfinite(mid) and age <= stale_s:
        return Mark(float(mid), "mid", float(age), False)
    lt = last_trade_at(trades, ts_ns)
    if math.isfinite(lt):
        return Mark(float(lt), "last_trade", float(age), True)
    return Mark(float("nan"), "missing", float(age), True)


def last_trade_at(trades: dict, ts_ns: int) -> float:
    """Last print at or before `ts_ns` — the staleness fallback and the
    registered last-trade-mark robustness leg."""
    t = np.asarray(trades["t_ns"], dtype=np.int64)
    idx = int(np.searchsorted(t, ts_ns, side="right")) - 1
    if idx < 0:
        return float("nan")
    return float(trades["price"][idx])


# =============================================================================
# session calendar, horizons, censoring
# =============================================================================

def horizon_target(sessions: list[str], anchor: str, k: int,
                   boundary: date = SPLIT_BOUNDARY) -> tuple[str | None, bool]:
    """The session k trading days after `anchor`, truncated at the sealed-split
    boundary and FLAGGED (returns (session, truncated))."""
    try:
        i = sessions.index(anchor)
    except ValueError:
        return None, False
    j = i + k
    truncated = False
    if j >= len(sessions):
        j, truncated = len(sessions) - 1, True
    if date.fromisoformat(sessions[j]) > boundary:
        cut = [s for s in sessions[:j + 1] if date.fromisoformat(s) <= boundary]
        if not cut:
            return None, True
        return cut[-1], True
    return sessions[j], truncated


def resolve_close(sessions: list[str], closes: dict[str, float], target: str,
                  boundary: date = SPLIT_BOUNDARY) -> tuple[float, str, str]:
    """The registered CENSORING KEEP-RULE, as one function.

    halt → the first post-resumption price; delisting/acquisition → the last
    price that exists; drop ONLY if no price exists at all. Returns
    (price, session_used, flag) with flag ∈ {"ok","resumption","last_price","none"}.
    """
    if target in closes and math.isfinite(closes[target]):
        return closes[target], target, "ok"
    try:
        i = sessions.index(target)
    except ValueError:
        i = len(sessions)
    for j in range(i, len(sessions)):
        s = sessions[j]
        if date.fromisoformat(s) > boundary:
            break
        if s in closes and math.isfinite(closes[s]):
            return closes[s], s, "resumption"
    prior = [s for s in sessions[:i] if s in closes and math.isfinite(closes[s])]
    if prior:
        return closes[prior[-1]], prior[-1], "last_price"
    return float("nan"), "", "none"


def earnings_in_window(earnings_dates: list[str], session: str, end_session: str) -> bool:
    """The pre-unseal earnings fence: an event is earnings-STRADDLING at a
    horizon when a release lands in (session, horizon end]. Those form a
    declared separate stratum — reported, never pooled."""
    return any(session < d <= end_session for d in earnings_dates)


# =============================================================================
# inference — block permutation, minP, stratified bootstrap
# =============================================================================

def _block_structure(block_id) -> tuple[np.ndarray, np.ndarray, np.ndarray,
                                        np.ndarray, np.ndarray]:
    """(event→block, within-block rank, block starts, block sizes, ordering)."""
    keys, eblock = np.unique(np.asarray(block_id), return_inverse=True)
    order = np.argsort(eblock, kind="stable")
    sizes = np.bincount(eblock, minlength=len(keys))
    starts = np.concatenate([[0], np.cumsum(sizes)[:-1]])
    erank = np.empty(len(eblock), dtype=np.int64)
    erank[order] = np.arange(len(order)) - starts[eblock[order]]
    return eblock, erank, starts, sizes, order


def block_source_index(eblock: np.ndarray, erank: np.ndarray, starts: np.ndarray,
                       sizes: np.ndarray, order: np.ndarray,
                       perm: np.ndarray) -> np.ndarray:
    """Source-event index per target event for a batch of block permutations.

    `perm` is (draws, n_blocks). A whole block's LABEL BLOCK moves together —
    that is the registered cluster-level (and, at h2/h3, calendar-week-level)
    null. When block sizes differ the within-block rank wraps modulo the source
    size, which is the only well-defined way to carry a size-n block's
    attributes onto a size-m one. Every cluster in the frozen file is a
    singleton, so the wrap never fires on the registered data.
    """
    src_block = perm[:, eblock]                       # (draws, n)
    return order[starts[src_block] + (erank[None, :] % sizes[src_block])]


def _one_sided_p(obs: float, draws: np.ndarray) -> float:
    d = np.asarray(draws, dtype=np.float64)
    d = d[np.isfinite(d)]
    if not np.isfinite(obs) or not len(d):
        return float("nan")
    return float((1 + int((d >= obs).sum())) / (1 + len(d)))


def _ecdf_p(stats: np.ndarray) -> np.ndarray:
    """Per-draw one-sided p against the whole draw set (the inner p of minP).

    Ties count as ≥, which is what makes minP exact rather than anti-conservative.
    """
    s = np.asarray(stats, dtype=np.float64)
    n = len(s)
    finite = np.isfinite(s)
    out = np.full(n, np.nan)
    if not finite.any():
        return out
    v = s[finite]
    # count of entries >= each value, via the sorted array
    srt = np.sort(v)
    ge = len(v) - np.searchsorted(srt, v, side="left")
    out[finite] = ge / len(v)
    return out


def minp_adjusted(stats: dict[str, np.ndarray]) -> dict:
    """Exact minP / max-statistic FWER over co-primaries computed ON THE SAME
    DRAWS. Index 0 of every array is the OBSERVED assignment.

    Returns the adjusted p, the per-statistic raw p, and the observed values.
    """
    inner = {k: _ecdf_p(v) for k, v in stats.items()}
    grid = np.vstack([inner[k] for k in stats])
    minp = np.where(np.isnan(grid), np.inf, grid).min(axis=0)
    minp[np.isinf(minp)] = np.nan
    obs, draws = minp[0], minp[1:]
    draws = draws[np.isfinite(draws)]
    adj = ((1 + int((draws <= obs).sum())) / (1 + len(draws))
           if np.isfinite(obs) and len(draws) else float("nan"))
    return {
        "adjusted_p": adj,
        "raw_p": {k: _one_sided_p(v[0], v[1:]) for k, v in stats.items()},
        "observed": {k: float(v[0]) for k, v in stats.items()},
        "n_draws": int(len(draws)),
    }


class PermEngine:
    """The registered null, precomputed once as three linear operators.

    Under a label permutation the OUTCOME never moves — the label side
    (stratum, score, and the concurrent-move covariate m, all properties of the
    print) moves as a block. Every co-primary is then linear in y, so the whole
    null collapses to three fixed (draws × n) matrices and each new outcome
    vector costs three matrix-vector products. That is what makes 50,000 draws
    affordable at every horizon × labelling × mark, and what makes Gate 1's
    injection harness (thousands of replications) finish at all.

    Row 0 of every operator is the OBSERVED assignment.
    """

    def __init__(self, block_id, is_high, score, m, n_perm: int = N_PERM,
                 seed: int = SEED, chunk: int = 2_000):
        self.n = len(np.asarray(is_high))
        self.n_perm = int(n_perm)
        is_high = np.asarray(is_high, dtype=bool)
        score = np.asarray(score, dtype=np.float64)
        m = np.asarray(m, dtype=np.float64)
        eblock, erank, starts, sizes, order = _block_structure(block_id)
        n_blocks = len(sizes)
        rng = np.random.default_rng(seed)

        rows = self.n_perm + 1
        w_delta = np.zeros((rows, self.n), dtype=np.float64)
        w_rho = np.zeros((rows, self.n), dtype=np.float64)
        w_res = np.zeros((rows, self.n), dtype=np.float64)
        n_high_draws = np.zeros(rows, dtype=np.int64)

        done = 0
        while done < rows:
            take = min(chunk, rows - done)
            perm = np.tile(np.arange(n_blocks), (take, 1))
            if done == 0:
                if take > 1:
                    perm[1:] = rng.permuted(perm[1:], axis=1)
            else:
                perm = rng.permuted(perm, axis=1)
            src = block_source_index(eblock, erank, starts, sizes, order, perm)
            hi = is_high[src]                                  # (take, n)
            sc = score[src]
            mm = m[src]
            nh = hi.sum(axis=1)
            nn = self.n - nh
            ok = (nh > 0) & (nn > 0)
            hf = hi.astype(np.float64)
            with np.errstate(invalid="ignore", divide="ignore"):
                w_delta[done:done + take] = np.where(
                    ok[:, None],
                    hf / np.maximum(nh, 1)[:, None]
                    - (1.0 - hf) / np.maximum(nn, 1)[:, None], 0.0)
            n_high_draws[done:done + take] = nh

            rx = rankdata(sc, axis=1)
            rx -= rx.mean(axis=1, keepdims=True)
            sdx = rx.std(axis=1)
            with np.errstate(invalid="ignore", divide="ignore"):
                w_rho[done:done + take] = np.where(
                    (sdx > 0)[:, None], rx / (self.n * np.maximum(sdx, 1e-300))[:, None],
                    0.0)

            design = np.stack([np.ones_like(mm), mm, mm * mm, hf], axis=2)
            pinv = np.linalg.pinv(design)                      # (take, 4, n)
            w_res[done:done + take] = np.where(ok[:, None], pinv[:, 3, :], 0.0)
            done += take

        self._w_delta, self._w_rho, self._w_res = w_delta, w_rho, w_res
        self._valid = n_high_draws > 0
        self.n_high_observed = int(n_high_draws[0])
        self.high_count_preserved_frac = float(
            np.mean(n_high_draws[1:] == self.n_high_observed)) if self.n_perm else 1.0

    # --- the statistics, each one matvec -------------------------------------

    def delta(self, y: np.ndarray) -> np.ndarray:
        """Δ = mean y(HIGH) − mean y(NULL), observed at index 0."""
        return self._w_delta @ np.asarray(y, dtype=np.float64)

    def rho(self, y: np.ndarray) -> np.ndarray:
        """Spearman ρ of score vs y over HIGH+NULL, observed at index 0."""
        ry = rankdata(np.asarray(y, dtype=np.float64))
        ry = ry - ry.mean()
        sdy = ry.std()
        if sdy <= 0:
            return np.full(self._w_rho.shape[0], np.nan)
        return (self._w_rho @ ry) / sdy

    def residualized(self, y: np.ndarray) -> np.ndarray:
        """The HIGH coefficient in y ~ 1 + m + m² + HIGH — the registered
        concurrent-move control, verdict-bearing."""
        return self._w_res @ np.asarray(y, dtype=np.float64)

    def coprimary(self, y: np.ndarray) -> dict:
        d, r = self.delta(y), self.rho(y)
        out = minp_adjusted({"delta": d, "spearman": r})
        neg = minp_adjusted({"delta": -d, "spearman": -r})
        res = self.residualized(y)
        out["inverted_adjusted_p"] = neg["adjusted_p"]
        out["residualized"] = {
            "coefficient": float(res[0]),
            "permutation_p": _one_sided_p(res[0], res[1:]),
        }
        out["high_count_preserved_frac"] = self.high_count_preserved_frac
        out["n_high"] = self.n_high_observed
        out["n_null"] = self.n - self.n_high_observed
        return out


def sign_flip_p(y, cluster, n_perm: int = 10_000, seed: int = SEED) -> dict:
    """The v1 sign-flip null, RETAINED for one-sample statements only (here:
    'is NULL's own mean significantly positive?', the LABEL-GRADED trigger).
    Flips run per cluster, never per event."""
    y = np.asarray(y, dtype=np.float64)
    ok = np.isfinite(y)
    y = y[ok]
    _, inv = np.unique(np.asarray(cluster)[ok], return_inverse=True)
    if not len(y):
        return {"mean": float("nan"), "p": float("nan"), "n": 0}
    rng = np.random.default_rng(seed)
    obs = float(np.mean(y))
    flips = rng.integers(0, 2, (n_perm, int(inv.max()) + 1)) * 2 - 1
    draws = (flips[:, inv] * y[None, :]).mean(axis=1)
    return {"mean": obs, "p": _one_sided_p(obs, draws), "n": int(len(y))}


def stratified_bootstrap_weights(cluster, is_high, n_boot: int = N_BOOT,
                                 seed: int = SEED) -> np.ndarray:
    """STRATIFIED cluster bootstrap: resample clusters with replacement WITHIN
    each stratum so per-stratum cluster counts are preserved in every draw.

    Returned as a (n_boot, n) weight matrix so Δ*_b = W @ y is one matvec.
    """
    is_high = np.asarray(is_high, dtype=bool)
    keys, inv = np.unique(np.asarray(cluster), return_inverse=True)
    n_clusters = len(keys)
    cluster_high = np.zeros(n_clusters, dtype=bool)
    cluster_high[inv] = is_high
    memb = [np.flatnonzero(inv == c) for c in range(n_clusters)]
    hi_c = np.flatnonzero(cluster_high)
    lo_c = np.flatnonzero(~cluster_high)
    rng = np.random.default_rng(seed)
    n = len(is_high)
    out = np.zeros((n_boot, n), dtype=np.float64)
    for b in range(n_boot):
        counts = np.zeros(n, dtype=np.float64)
        for pool in (hi_c, lo_c):
            if not len(pool):
                continue
            for c in pool[rng.integers(0, len(pool), len(pool))]:
                counts[memb[c]] += 1.0
        wh = counts * is_high
        wl = counts * ~is_high
        sh, sl = wh.sum(), wl.sum()
        if sh > 0 and sl > 0:
            out[b] = wh / sh - wl / sl
        else:
            out[b] = np.nan
    return out


def jackknife_weights(cluster, is_high) -> np.ndarray:
    """Leave-one-cluster-out Δ weights — BCa's acceleration term."""
    is_high = np.asarray(is_high, dtype=bool)
    keys, inv = np.unique(np.asarray(cluster), return_inverse=True)
    n = len(is_high)
    out = np.zeros((len(keys), n), dtype=np.float64)
    for c in range(len(keys)):
        keep = inv != c
        wh = (keep & is_high).astype(np.float64)
        wl = (keep & ~is_high).astype(np.float64)
        if wh.sum() > 0 and wl.sum() > 0:
            out[c] = wh / wh.sum() - wl / wl.sum()
        else:
            out[c] = np.nan
    return out


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_ppf(p: float) -> float:
    """Acklam-free inverse normal by bisection — 60 iterations is exact enough
    for a CI endpoint and keeps the dependency surface at scipy.stats.rankdata."""
    lo, hi = -10.0, 10.0
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if _norm_cdf(mid) < p:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def bca_ci(obs: float, boot: np.ndarray, jack: np.ndarray,
           conf: float = 0.95) -> dict:
    """BCa where it is computable, percentile where it is not — the spec allows
    either. > 1% non-finite draws ⇒ the CI is declared INVALID rather than
    silently reported on the survivors."""
    b = np.asarray(boot, dtype=np.float64)
    finite = np.isfinite(b)
    nonfinite_frac = float(1.0 - finite.mean()) if len(b) else 1.0
    b = b[finite]
    if not len(b) or nonfinite_frac > BOOT_NONFINITE_MAX:
        return {"lo": float("nan"), "hi": float("nan"), "method": "invalid",
                "valid": False, "nonfinite_frac": nonfinite_frac}
    tail = (1.0 - conf) / 2.0
    z0_frac = float((b < obs).mean())
    j = np.asarray(jack, dtype=np.float64)
    j = j[np.isfinite(j)]
    method = "percentile"
    lo_q, hi_q = tail, 1.0 - tail
    if 0.0 < z0_frac < 1.0 and len(j) > 2:
        z0 = _norm_ppf(z0_frac)
        dev = j.mean() - j
        denom = 6.0 * (float(np.sum(dev ** 2)) ** 1.5)
        a = float(np.sum(dev ** 3)) / denom if denom > 0 else 0.0
        zl, zh = _norm_ppf(tail), _norm_ppf(1.0 - tail)
        for z, slot in ((zl, "lo"), (zh, "hi")):
            adj = z0 + (z0 + z) / max(1.0 - a * (z0 + z), 1e-12)
            q = _norm_cdf(adj)
            if slot == "lo":
                lo_q = q
            else:
                hi_q = q
        if 0.0 < lo_q < hi_q < 1.0:
            method = "bca"
        else:
            lo_q, hi_q = tail, 1.0 - tail
    return {"lo": float(np.quantile(b, lo_q)), "hi": float(np.quantile(b, hi_q)),
            "method": method, "valid": True, "nonfinite_frac": nonfinite_frac}


def equivalence_ok(ci: dict, band: float = EQUIV_BAND) -> bool:
    """NULL's ≈ 0 claim is an EQUIVALENCE test, never an accepted null."""
    return bool(ci.get("valid") and math.isfinite(ci["lo"]) and math.isfinite(ci["hi"])
                and ci["lo"] > -band and ci["hi"] < band)


# =============================================================================
# the injection harness — Gate 1 pre-unseal, Gate 2 post-unseal
# =============================================================================

@dataclass
class Harness:
    """Everything a replication needs, precomputed once."""
    engine: PermEngine
    boot_w: np.ndarray
    jack_w: np.ndarray
    is_high: np.ndarray
    bank: list[np.ndarray]        # per-event standardized residual pool
    block: np.ndarray             # residual draws move per BLOCK, not per event
    sigma: np.ndarray             # per-event dispersion (from the stratum's σ)


def conjunction_passes(harness: Harness, y: np.ndarray, alpha: float = ALPHA) -> bool:
    """The FULL registered PREDICTIVE conjunction, in the four legs a
    controls-only harness can simulate: minP co-primary (same-signed), the
    m-residualized contrast, point Δ̂ ≥ 0.5%, and a stratified-bootstrap lower
    bound above zero. Scoring only the p-leg is precisely what made v1's power
    harness report 80% where the operative rule delivered ~56%."""
    cp = harness.engine.coprimary(y)
    if not (np.isfinite(cp["adjusted_p"]) and cp["adjusted_p"] < alpha):
        return False
    if not (cp["observed"]["delta"] > 0 and cp["observed"]["spearman"] > 0):
        return False
    rp = cp["residualized"]["permutation_p"]
    if not (np.isfinite(rp) and rp < alpha and cp["residualized"]["coefficient"] > 0):
        return False
    delta_hat = float(harness.engine.delta(y)[0])
    if not (delta_hat >= DELTA_MIN):
        return False
    boot = harness.boot_w @ y
    jack = harness.jack_w @ y
    ci = bca_ci(delta_hat, boot, jack)
    return bool(ci["valid"] and ci["lo"] > 0)


def _draw_y(harness: Harness, rng, binv: np.ndarray, n_blocks: int) -> np.ndarray:
    """One replication's outcome vector: a residual per BLOCK, scaled to the
    stratum's registered dispersion. Two events on the same underlying and
    session share a forward return almost exactly, so drawing independently
    inside a block would inflate power in the stratum that matters."""
    pick = rng.random(n_blocks)
    y = np.empty(len(harness.is_high), dtype=np.float64)
    for i in range(len(y)):
        pool = harness.bank[i]
        y[i] = pool[int(pick[binv[i]] * len(pool)) % len(pool)] * harness.sigma[i]
    return y


def leg_pass_rates(harness: Harness, delta: float, n_rep: int = 200,
                   seed: int = SEED) -> dict:
    """Which leg of the registered conjunction BINDS at a given δ.

    A gate that fails with one number is not actionable; a gate that says WHICH
    registered requirement the sample cannot meet is. Diagnostic only — the
    verdict rule is unchanged and the conjunction is still all-or-nothing.
    """
    rng = np.random.default_rng(seed)
    _, binv = np.unique(harness.block, return_inverse=True)
    n_blocks = int(binv.max()) + 1 if len(binv) else 0
    hits = dict.fromkeys(
        ("minp_coprimary", "coprimary_signs", "m_residualized",
         "point_delta_half_percent", "bootstrap_ci_above_zero", "conjunction"), 0)
    for _ in range(n_rep):
        y = _draw_y(harness, rng, binv, n_blocks) + delta * harness.is_high
        cp = harness.engine.coprimary(y)
        rp = cp["residualized"]["permutation_p"]
        delta_hat = float(harness.engine.delta(y)[0])
        ci = bca_ci(delta_hat, harness.boot_w @ y, harness.jack_w @ y)
        hits["minp_coprimary"] += bool(
            np.isfinite(cp["adjusted_p"]) and cp["adjusted_p"] < ALPHA)
        hits["coprimary_signs"] += bool(
            cp["observed"]["delta"] > 0 and cp["observed"]["spearman"] > 0)
        hits["m_residualized"] += bool(
            np.isfinite(rp) and rp < ALPHA
            and cp["residualized"]["coefficient"] > 0)
        hits["point_delta_half_percent"] += bool(delta_hat >= DELTA_MIN)
        hits["bootstrap_ci_above_zero"] += bool(ci["valid"] and ci["lo"] > 0)
        hits["conjunction"] += bool(conjunction_passes(harness, y))
    out = {k: round(v / max(n_rep, 1), 4) for k, v in hits.items()}
    out["delta"] = delta
    out["n_rep"] = n_rep
    return out


def injection_power(harness: Harness, deltas=DELTA_GRID, n_rep: int = 300,
                    seed: int = SEED, power_target: float = POWER_TARGET) -> dict:
    """Power by injecting δ into HIGH pseudo-events ONLY, over the registered
    grid. MDE = the smallest δ reaching `power_target`."""
    rng = np.random.default_rng(seed)
    _, binv = np.unique(harness.block, return_inverse=True)
    n_blocks = int(binv.max()) + 1 if len(binv) else 0
    power: dict[str, float] = {}
    mde = None
    for d in deltas:
        hits = 0
        for _ in range(n_rep):
            if conjunction_passes(harness,
                                  _draw_y(harness, rng, binv, n_blocks)
                                  + d * harness.is_high):
                hits += 1
        pw = hits / max(n_rep, 1)
        power[f"{d:.4g}"] = round(pw, 4)
        if pw >= power_target and mde is None:
            mde = float(d)
    return {"power_by_delta": power, "mde": mde, "n_rep": n_rep,
            "n_perm": harness.engine.n_perm, "power_target": power_target,
            "grid": list(deltas)}


# =============================================================================
# the registered verdict table
# =============================================================================

@dataclass
class VerdictInputs:
    """Every input the registered table reads, and nothing else."""
    n_high_events: int
    n_high_underlyings: int
    n_null_events: int
    censor_frac_high: float = 0.0
    censor_frac_null: float = 0.0
    gate2_mde: float | None = None
    coprimary_pass: bool = False          # minP < α at h2, both same-signed +
    coprimary_inverted: bool = False      # minP < α at h2, both same-signed −
    coprimary_conflicting_signs: bool = False
    residualized_pass: bool = False
    delta_point: float = 0.0
    ci_lo: float = float("nan")
    ci_valid: bool = False
    sign_agreement: bool = False
    jackknife_ok: bool = False
    null_mean_positive: bool = False      # sign-flip p < 0.05
    all_horizons_all_labellings_fail: bool = False
    notes: list[str] = field(default_factory=list)


def route_verdict(vi: VerdictInputs) -> dict:
    """The registered table, as a pure function. Routing order matters and is
    the spec's own: floors → INVERTED → the PREDICTIVE conjunction (GRADED when
    NULL is not ≈0) → the Gate 2 downgrade → the kill → AMBIGUOUS.

    Three traps the table is explicit about and this encodes:
      · LABEL-EMPTY needs floors AND Gate 2 AND both co-primaries dead at ALL
        three horizons under ALL three labellings on BOTH marks. Any live
        co-primary anywhere makes `all_horizons_all_labellings_fail` False and
        the route is AMBIGUOUS, never the kill.
      · a failed Gate 2 may NEVER route LABEL-EMPTY.
      · residualized-only failure ⇒ AMBIGUOUS with the registered reading
        "label proxies the concurrent order-flow move".
    """
    floors = {
        "n_high_events": vi.n_high_events,
        "n_high_events_ok": vi.n_high_events >= FLOOR_HIGH_EVENTS,
        "n_high_underlyings": vi.n_high_underlyings,
        "n_high_underlyings_ok": vi.n_high_underlyings >= FLOOR_HIGH_UNDERLYINGS,
        "n_null_events": vi.n_null_events,
        "n_null_events_ok": vi.n_null_events >= FLOOR_NULL_EVENTS,
        "censor_frac_high": vi.censor_frac_high,
        "censor_frac_null": vi.censor_frac_null,
        "censoring_ok": (vi.censor_frac_high <= CENSOR_MAX_FRAC
                         and vi.censor_frac_null <= CENSOR_MAX_FRAC),
    }
    floors_met = all(floors[k] for k in
                     ("n_high_events_ok", "n_high_underlyings_ok",
                      "n_null_events_ok", "censoring_ok"))
    gate2_ok = vi.gate2_mde is not None and vi.gate2_mde <= GATE_MDE_FLOOR
    base = {"floors": floors, "floors_met": floors_met,
            "gate2_mde": vi.gate2_mde, "gate2_ok": gate2_ok,
            "notes": list(vi.notes)}

    if not floors_met:
        return {"verdict": "NO-VERDICT", "reason": "registered floors breached", **base}
    if vi.coprimary_inverted:
        return {"verdict": "LABEL-INVERTED",
                "reason": "one-sided p < α in the OPPOSITE direction on the "
                          "co-primaries — recorded finding, no re-cut, no trade "
                          "license", **base}
    predictive_legs = {
        "coprimary": vi.coprimary_pass,
        "residualized": vi.residualized_pass,
        "delta_point": bool(vi.delta_point >= DELTA_MIN),
        "ci_lo_above_zero": bool(vi.ci_valid and math.isfinite(vi.ci_lo)
                                 and vi.ci_lo > 0),
        "sign_agreement": vi.sign_agreement,
        "jackknife": vi.jackknife_ok,
    }
    base["predictive_legs"] = predictive_legs
    if all(predictive_legs.values()):
        if vi.null_mean_positive:
            return {"verdict": "LABEL-GRADED",
                    "reason": "contrast passes but NULL's own mean is "
                              "significantly positive — the score GRADES "
                              "information; 'customer-vs-customer carries none' "
                              "is falsified", **base}
        return {"verdict": "LABEL-PREDICTIVE",
                "reason": "the full registered conjunction passed at h2 on the "
                          "earnings-free stratum", **base}
    if not gate2_ok and not vi.coprimary_pass:
        return {"verdict": "NO-VERDICT-UNDERPOWERED",
                "reason": "realized MDE above the 1.5% floor — a non-significant "
                          "result here is uninformative and may never route "
                          "LABEL-EMPTY", **base}
    if vi.all_horizons_all_labellings_fail:
        return {"verdict": "LABEL-EMPTY",
                "reason": "floors met, Gate 2 passed, and both co-primaries "
                          "failed at all three horizons under all three "
                          "labellings on both marks", **base}
    reason = "mixed outcome"
    if vi.coprimary_conflicting_signs:
        reason = "co-primaries carry conflicting signs"
    elif vi.coprimary_pass and not vi.residualized_pass:
        reason = ("co-primary passes but the m-residualized contrast does not — "
                  "registered reading: the label proxies the concurrent "
                  "order-flow move")
    elif vi.coprimary_pass and not vi.jackknife_ok:
        reason = "a single calendar week's removal pushes the primary p above α"
    elif vi.coprimary_pass and not vi.sign_agreement:
        reason = "sign flips across a label variant, anchor, or the σ-standardized leg"
    return {"verdict": "AMBIGUOUS",
            "reason": f"{reason} — no kill, no re-cut", **base}


# =============================================================================
# on-disk corpus reads (gate1 — nothing is fetched, nothing forward is read)
# =============================================================================

def closes_before(symbol: str, before: date, db_path: Path = DEFAULT_DB_PATH,
                  limit: int | None = None) -> list[tuple[str, float]]:
    """Split-adjusted daily closes STRICTLY BEFORE `before`, oldest first.

    ★ This is Gate 1's only price source and its seal lock: the bound is in the
    SQL, so no forward close can reach the caller even by accident. Callers pass
    the EVENT's own session, never a control's.
    """
    with connect(db_path) as conn:
        rows = list(conn.execute(
            "SELECT date, c FROM equity_bars WHERE symbol = ? AND date < ? "
            "AND adjustment = 'split' AND c IS NOT NULL ORDER BY date ASC",
            (symbol, before.isoformat())))
    out = [(r["date"], float(r["c"])) for r in rows]
    return out[-limit:] if limit else out


# =============================================================================
# stage 1 — gate1, the controls-only power gate
# =============================================================================

def _pseudo_returns(out_dir: Path, ev: dict, control_ts: list[int],
                    event_session: date, spy: dict[str, float],
                    db_path: Path = DEFAULT_DB_PATH) -> tuple[list[float], dict]:
    """Aligned SPY-subtracted h2 pseudo-returns from this event's control days.

    A control's pseudo-forward window must end STRICTLY BEFORE the event's own
    session — the youngest controls are dropped for exactly that reason, and the
    count is disclosed.
    """
    closes = dict(closes_before(ev["symbol"], event_session))
    sessions = sorted(closes)
    led = {"controls_seen": 0, "drop_window_touches_event": 0, "drop_no_bars": 0,
           "drop_no_mark": 0, "drop_split_space": 0}
    rows: list[float] = []
    h_sign = float(ev["h_sign"])
    for ts in control_ts:
        led["controls_seen"] += 1
        cs = session_of(ts)
        if date.fromisoformat(cs) >= event_session:
            # 3.2's controls are SYMMETRIC (10 before / 10 after) — the whole
            # post-event half is unusable here by construction.
            led["drop_window_touches_event"] += 1
            continue
        if cs not in closes:
            led["drop_no_bars"] += 1
            continue
        i = sessions.index(cs)
        if i + 1 >= len(sessions):
            led["drop_window_touches_event"] += 1
            continue
        nxt = sessions[i + 1]
        if date.fromisoformat(nxt) >= event_session:
            led["drop_window_touches_event"] += 1
            continue
        loaded = _load_window(out_dir, ev["symbol"], ts)
        if loaded is None:
            led["drop_no_mark"] += 1
            continue
        trades, quotes = loaded
        mark = mark_at(quotes, trades, ts + MARK_M0_S * 1_000_000_000)
        if not math.isfinite(mark.price) or mark.price <= 0:
            led["drop_no_mark"] += 1
            continue
        # split-space guard: corpus bars are split-adjusted, the quote is raw, so
        # a split after the control day would show up as a huge same-day "move".
        if abs(log_ret(mark.price, closes[cs])) >= CONTINUITY_TRIPWIRE:
            led["drop_split_space"] += 1
            continue
        if cs not in spy or nxt not in spy:
            led["drop_no_bars"] += 1
            continue
        rows.append(aligned_excess(mark.price, closes[nxt], spy[cs], spy[nxt], h_sign))
    return [r for r in rows if math.isfinite(r)], led


def _daily_dispersion(symbol: str, event_session: date, spy: dict[str, float],
                      lookback: int = SIGMA_LOOKBACK) -> float:
    """The registered second dispersion arm: the 20-prior-session daily-return
    dispersion ending the session BEFORE the event."""
    rows = closes_before(symbol, event_session, limit=lookback + 1)
    if len(rows) < 3:
        return float("nan")
    rets = []
    for (d0, c0), (d1, c1) in zip(rows[:-1], rows[1:], strict=True):
        if d0 in spy and d1 in spy:
            rets.append(log_ret(c0, c1) - log_ret(spy[d0], spy[d1]))
    return robust_sd(rets)


def build_gate1_harness(out_dir: Path, frozen: dict, ledger: dict,
                        n_perm: int, seed: int) -> tuple[Harness, dict]:
    """Assemble the pseudo-event bank + the registered null, on-disk only."""
    # SPY carries no event outcome — the equity stratum never contains an ETF —
    # so the market leg is the one series that may span every event session. It
    # is still bounded, and it is only ever paired with a symbol close that came
    # from a window ending strictly before the event.
    newest = max(date.fromisoformat(e["session"]) for e in frozen["events"])
    spy = dict(closes_before("SPY", newest + timedelta(days=10)))
    spy_sessions = sorted(spy)
    by_key = {(e["symbol"], e["ts_ns"]): e for e in ledger["events"]}
    rows, disclosure = [], {"controls_seen": 0, "drop_window_touches_event": 0,
                            "drop_no_bars": 0, "drop_no_mark": 0,
                            "drop_split_space": 0}
    for ev in frozen["events"]:
        if ev["stratum"] == "ANTI":
            continue                     # ANTI is reported, never in the contrast
        session = date.fromisoformat(ev["session"])
        h2_end, _ = horizon_target(spy_sessions, ev["session"], 1)
        if h2_end and earnings_in_window(ev["earnings_dates_45d"], ev["session"],
                                         h2_end):
            continue                     # earnings-free primary stratum
        led_ev = by_key.get((ev["symbol"], ev["ts_ns"]))
        if led_ev is None:
            continue
        pseudo, led = _pseudo_returns(out_dir, ev, led_ev["control_ts_ns"], session, spy)
        for k in disclosure:
            disclosure[k] += led[k]
        if len(pseudo) < 3:
            continue
        arr = np.asarray(pseudo, dtype=np.float64)
        rows.append({
            "symbol": ev["symbol"], "session": ev["session"],
            "cluster": ev["cluster"], "week": week_id(ev["session"]),
            "is_high": ev["stratum"] == "HIGH", "score": ev["score"],
            "m": (ev["m_bps"] or 0.0) / 1e4,
            "pseudo": arr - np.median(arr),
            "sigma_daily": _daily_dispersion(ev["symbol"], session, spy),
        })
    if not rows:
        raise RuntimeError("gate1: no pseudo-events survived — nothing to gate on")

    is_high = np.array([r["is_high"] for r in rows], dtype=bool)
    score = np.array([r["score"] for r in rows], dtype=np.float64)
    m = np.array([r["m"] for r in rows], dtype=np.float64)
    week = np.array([r["week"] for r in rows])

    # per-stratum dispersion = max(control-day estimate, 20-prior-session daily)
    sigma = np.empty(len(rows), dtype=np.float64)
    strat_sigma = {}
    for name, mask in (("HIGH", is_high), ("NULL", ~is_high)):
        ctrl = robust_sd(np.concatenate([rows[i]["pseudo"] for i in np.flatnonzero(mask)]))
        daily = float(np.nanmedian([rows[i]["sigma_daily"] for i in np.flatnonzero(mask)]))
        chosen = float(np.nanmax([ctrl, daily]))
        strat_sigma[name] = {"control_day": ctrl, "daily_20_session": daily,
                             "registered_max": chosen}
        sigma[mask] = chosen

    bank = []
    for r, s in zip(rows, sigma, strict=True):
        sd = robust_sd(r["pseudo"])
        bank.append(r["pseudo"] / sd if math.isfinite(sd) and sd > 0
                    else np.zeros(len(r["pseudo"])))
    # h2 inference is the calendar-week-blocked null — the gate is a floor on
    # the PRIMARY horizon, so it is estimated under the primary horizon's null.
    engine = PermEngine(week, is_high, score, m, n_perm=n_perm, seed=seed)
    harness = Harness(
        engine=engine,
        boot_w=stratified_bootstrap_weights(
            np.array([r["cluster"] for r in rows]), is_high, seed=seed),
        jack_w=jackknife_weights(np.array([r["cluster"] for r in rows]), is_high),
        is_high=is_high.astype(np.float64),
        bank=bank,
        block=week,
        sigma=sigma,
    )
    disclosure.update({
        "n_pseudo_events": len(rows),
        "n_high": int(is_high.sum()),
        "n_null": int((~is_high).sum()),
        "n_underlyings_high": len({r["symbol"] for r in rows if r["is_high"]}),
        "n_weeks": int(len(set(week))),
        "stratum_dispersion": strat_sigma,
        "high_count_preserved_frac": engine.high_count_preserved_frac,
    })
    return harness, disclosure


LADDER = [
    "(1) re-run 3.2 selection at cap 20/underlying + top 600/band — PRIMARY band "
    "ONLY (the secondary Δ-band is never pooled into the contrast, per 3.2 "
    "Amendment 1; it may appear only as its own reported stratum). NOT implemented "
    "here: re-selection is a separate founder-visible round.",
    "(2) STOP = NO-RUN. Nothing is unsealed.",
]


def stage_gate1(out_dir: Path, n_perm: int, n_rep: int, seed: int) -> dict:
    frozen = verify_frozen(out_dir)
    ledger = json.loads((out_dir / "fetch_ledger_equity.json").read_text())
    harness, disclosure = build_gate1_harness(out_dir, frozen, ledger, n_perm, seed)
    power = injection_power(harness, n_rep=n_rep, seed=seed)
    mde = power["mde"]
    proceed = mde is not None and mde <= GATE_MDE_FLOOR

    # the measured event/control vol ratio the spec wants reported BESIDE the gate
    vol_ratio = None
    hi = disclosure["stratum_dispersion"].get("HIGH", {})
    lo = disclosure["stratum_dispersion"].get("NULL", {})
    if hi.get("control_day") and lo.get("control_day"):
        vol_ratio = {"high_control_day_sigma": hi["control_day"],
                     "null_control_day_sigma": lo["control_day"],
                     "high_over_null": hi["control_day"] / lo["control_day"],
                     "registered_measured_event_over_control": 1.46,
                     "note": "3.2's review measured event-day realized vol at "
                             "1.46× the same name's control days in HIGH (LOW "
                             "1.03×) — a controls-only power estimate is "
                             "OPTIMISTIC exactly in the stratum that matters."}
    doc = {
        "spec": SPEC, "stage": "gate1", "sealed": True,
        "frozen_sha256": FROZEN_SHA256,
        "horizon": PRIMARY_HORIZON, "floor_mde": GATE_MDE_FLOOR,
        "seed": seed, **power, "proceed": proceed,
        "binding_leg_at_the_floor": leg_pass_rates(harness, GATE_MDE_FLOOR,
                                                   n_rep=n_rep, seed=seed),
        "disclosure": disclosure,
        "event_control_vol_ratio": vol_ratio,
        "ladder_if_failed": LADDER,
        "spec_ambiguities": SPEC_AMBIGUITIES,
        "seal": ("No forward return of any of the 117 events was read. Every "
                 "price came from the on-disk flow corpus bounded to STRICTLY "
                 "BEFORE the event's own session, and every control pseudo-window "
                 "ends strictly before it too."),
    }
    save_json(out_dir / GATE1_NAME, doc)
    logger.info("gate1: MDE=%s (floor %.3f) proceed=%s", mde, GATE_MDE_FLOOR, proceed)
    if not proceed:
        logger.error("GATE 1 FAILED — the registered ladder, in order:")
        for step in LADDER:
            logger.error("  %s", step)
    return doc


# =============================================================================
# stage 2 — unseal (THE outcome fetch)
# =============================================================================

def build_rename_chain(records: list[dict]) -> dict[str, list[tuple[str, str]]]:
    """old_symbol → [(process_date, new_symbol)], date-scoped.

    A global symbol→symbol dict is a bug: symbol reuse is real (ARIS and GOLD
    were each vacated and re-occupied inside one year).
    """
    chain: dict[str, list[tuple[str, str]]] = {}
    for rec in records:
        old, new = rec.get("old_symbol"), rec.get("new_symbol")
        when = rec.get("process_date") or rec.get("date")
        if old and new and when:
            chain.setdefault(str(old), []).append((str(when), str(new)))
    for v in chain.values():
        v.sort()
    return chain


def resolve_forward(symbol: str, after: str, until: str,
                    chain: dict[str, list[tuple[str, str]]]) -> str:
    """Follow name_change hops with `after` < process_date ≤ `until`.

    Restated bar history rides the NEW symbol; the old symbol returns zero bars —
    so the fetch symbol is the one the chain lands on at the fetch date.
    """
    cur, cursor = symbol, after
    for _ in range(MAX_RENAME_HOPS):
        hops = [(d, n) for d, n in chain.get(cur, []) if cursor < d <= until]
        if not hops:
            return cur
        cursor, cur = hops[0]
    raise RuntimeError(f"rename chain for {symbol} exceeded {MAX_RENAME_HOPS} hops")


def fetch_daily_bars(client, data_base_url: str, symbol: str, start: date,
                     end: date, adjustment: str) -> list[dict]:
    return _paged_get(
        client, f"{data_base_url}/v2/stocks/{symbol}/bars",
        {"start": start.isoformat(), "end": end.isoformat(), "timeframe": "1Day",
         "limit": PAGE_LIMIT, "adjustment": adjustment, "feed": FEED}, "bars")


def fetch_minute_window(client, data_base_url: str, symbol: str, lo_ns: int,
                        hi_ns: int) -> list[dict]:
    return _paged_get(
        client, f"{data_base_url}/v2/stocks/{symbol}/bars",
        {"start": _rfc3339(lo_ns), "end": _rfc3339(hi_ns), "timeframe": "1Min",
         "limit": PAGE_LIMIT, "adjustment": "all", "feed": FEED}, "bars")


def _cached(path: Path, produce) -> list[dict]:
    """Resumable + atomic: nothing is ever refetched once it is on disk."""
    if path.exists():
        return json.loads(path.read_text())
    rows = produce()
    save_json(path, rows)
    return rows


def stage_unseal(out_dir: Path, client, data_base_url: str,
                 limit: int | None = None) -> dict:
    frozen = verify_frozen(out_dir)
    gate1 = require_gate1(out_dir)
    ledger = json.loads((out_dir / "fetch_ledger_equity.json").read_text())
    by_key = {(e["symbol"], e["ts_ns"]): e for e in ledger["events"]}
    events = frozen["events"][:limit] if limit else frozen["events"]

    udir = out_dir / "unseal"
    today = date.today()
    sessions_all = sorted({e["session"] for e in events})
    start = date.fromisoformat(sessions_all[0]) - timedelta(days=150)
    end = min(SPLIT_BOUNDARY, today)

    names = _cached(udir / "name_changes.json",
                    lambda: fetch_name_changes(
                        start.isoformat(), today.isoformat(),
                        api_key=client.headers.get("APCA-API-KEY-ID", ""),
                        api_secret=client.headers.get("APCA-API-SECRET-KEY", "")))
    chain = build_rename_chain(names)

    # --- SPY: the market leg, one fetch for the whole window ------------------
    for adj in ("all", "raw"):
        _cached(udir / "daily" / f"SPY_{adj}.json",
                lambda a=adj: fetch_daily_bars(client, data_base_url, "SPY",
                                               start, end, a))
    spy_days = sorted({e["session"] for e in events}
                      | {session_of(ts) for e in events
                         for ts in by_key[(e["symbol"], e["ts_ns"])]["control_ts_ns"]})
    for day in spy_days:
        d = date.fromisoformat(day)
        lo = session_midnight_ns(d) + 9 * 3600 * 1_000_000_000
        hi = session_midnight_ns(d) + 17 * 3600 * 1_000_000_000
        _cached(udir / "spy_minute" / f"{day}.json",
                lambda a=lo, b=hi: fetch_minute_window(client, data_base_url,
                                                       "SPY", a, b))

    manifest, tripped = [], []
    for i, ev in enumerate(events, 1):
        sym, ts = ev["symbol"], ev["ts_ns"]
        fetch_sym = resolve_forward(sym, ev["session"], today.isoformat(), chain)
        bars = {}
        for adj in ("all", "raw"):
            bars[adj] = _cached(
                udir / "daily" / f"{fetch_sym}_{adj}.json",
                lambda a=adj, s=fetch_sym: fetch_daily_bars(
                    client, data_base_url, s, start, end, a))
        marks = [ts] + list(by_key[(sym, ts)]["control_ts_ns"])
        for mts in marks:
            _cached(udir / "minute" / fetch_sym / f"{mts}.json",
                    lambda t=mts, s=fetch_sym: fetch_minute_window(
                        client, data_base_url, s,
                        t - MINUTE_PRE_S * 1_000_000_000,
                        t + MINUTE_POST_S * 1_000_000_000))
        row = {"symbol": sym, "fetch_symbol": fetch_sym, "session": ev["session"],
               "ts_ns": ts, "renamed": fetch_sym != sym,
               "n_daily_all": len(bars["all"]), "n_daily_raw": len(bars["raw"]),
               "n_marks": len(marks)}
        trip = _continuity_check(out_dir, udir, ev, fetch_sym)
        row.update(trip)
        if trip["tripped"]:
            tripped.append(row)
        manifest.append(row)
        if i % 10 == 0:
            logger.info("unsealed %s/%s events", i, len(events))

    doc = {"spec": SPEC, "stage": "unseal",
           "gate1_mde": gate1.get("mde"), "gate1_proceed": gate1.get("proceed"),
           "frozen_sha256": FROZEN_SHA256,
           "boundary": SPLIT_BOUNDARY.isoformat(),
           "n_events": len(manifest), "n_renamed": sum(r["renamed"] for r in manifest),
           "n_name_change_records": len(names),
           "events": manifest, "tripwire_hits": tripped,
           "spec_ambiguities": SPEC_AMBIGUITIES}
    if tripped:
        # the artifact lands under a DIFFERENT name so `analyze` still refuses:
        # a tripped run is evidence, not a licence to proceed.
        save_json(out_dir / "unseal_TRIPPED_3p3.json", doc)
        raise TripwireError(
            "continuity tripwire fired — the run HALTS. Events: "
            + ", ".join(f"{r['symbol']}@{r['session']} "
                        f"|log(close/M0_adj)|={r['continuity']:.4f}" for r in tripped))
    save_json(out_dir / UNSEAL_NAME, doc)
    logger.info("unseal: %s events, %s renamed, 0 tripwire hits", len(manifest),
                doc["n_renamed"])
    return doc


def daily_map(bars: list[dict]) -> dict[str, float]:
    """RFC3339 daily bars → {session: close}. Alpaca stamps a daily bar at
    00:00 UTC of its own session date, so the date prefix IS the session."""
    return {str(b["t"])[:10]: float(b["c"]) for b in bars}


def minute_close_at(bars: list[dict], ts_ns: int, max_stale_s: float = 300.0) -> float:
    """Close of the last 1-min bar at or before `ts_ns`."""
    if not bars:
        return float("nan")
    t = np.array([np.datetime64(str(b["t"]).replace("Z", ""), "ns").astype(np.int64)
                  for b in bars], dtype=np.int64)
    idx = int(np.searchsorted(t, ts_ns, side="right")) - 1
    if idx < 0 or (ts_ns - int(t[idx])) / 1e9 > max_stale_s + 60.0:
        return float("nan")
    return float(bars[idx]["c"])


def _continuity_check(out_dir: Path, udir: Path, ev: dict, fetch_sym: str) -> dict:
    """The registered per-event tripwire (see SPEC_AMBIGUITIES for the reading)."""
    all_bars = daily_map(json.loads((udir / "daily" / f"{fetch_sym}_all.json").read_text()))
    raw_bars = daily_map(json.loads((udir / "daily" / f"{fetch_sym}_raw.json").read_text()))
    session = ev["session"]
    f = adjustment_factor(all_bars.get(session, float("nan")),
                          raw_bars.get(session, float("nan")))
    loaded = _load_window(out_dir, ev["symbol"], ev["ts_ns"])
    if loaded is None or not math.isfinite(f):
        return {"factor": f, "continuity": float("nan"),
                "continuity_next_session": float("nan"), "tripped": False,
                "note": "no window or no factor — the event drops at analyze"}
    trades, quotes = loaded
    m0 = mark_at(quotes, trades, ev["ts_ns"] + MARK_M0_S * 1_000_000_000)
    m0_adj = m0.price * f
    ratio = continuity_ratio(all_bars.get(session, float("nan")), m0_adj)
    forward = sorted(s for s in all_bars if s > session)
    nxt = continuity_ratio(all_bars[forward[0]], m0_adj) if forward else float("nan")
    return {"factor": f, "continuity": ratio, "continuity_next_session": nxt,
            "tripped": bool(math.isfinite(ratio) and ratio >= CONTINUITY_TRIPWIRE)}


# =============================================================================
# stage 3 — analyze
# =============================================================================

@dataclass
class EventOutcome:
    symbol: str
    session: str
    cluster: str
    week: str
    h_sign: float
    scores: dict          # labelling → score
    m: float
    y: dict               # (mark, horizon, leg) → float
    censor: dict          # horizon → flag
    truncated: dict       # horizon → bool
    earnings_free: dict   # horizon → bool
    drops: list


def factor_for(day: str, bars_all: dict[str, float],
               bars_raw: dict[str, float]) -> float:
    """The adjustment factor for ONE session. The spec fixes f_i on the event
    session for the event's own marks; a CONTROL day's raw mark needs that day's
    own factor, or the control median is computed in a third price space and the
    subtraction stops cancelling."""
    return adjustment_factor(bars_all.get(day, float("nan")),
                             bars_raw.get(day, float("nan")))


def _leg_returns(px0: float, px_end: float, spy0: float, spy_end: float,
                 h_sign: float, beta: float) -> dict[str, float]:
    return {
        "raw": aligned_excess(px0, px_end, spy0, spy_end, h_sign, beta=0.0),
        "spy_only": aligned_excess(px0, px_end, spy0, spy_end, h_sign, beta=1.0),
        "beta_adj": aligned_excess(px0, px_end, spy0, spy_end, h_sign, beta=beta),
    }


def compute_outcomes(out_dir: Path, frozen: dict, ledger: dict,
                     unseal: dict) -> list[EventOutcome]:
    """Every registered y, at every mark × horizon × leg. Pure arithmetic over
    what `unseal` put on disk plus the already-unblinded ±300s snapshots."""
    udir = out_dir / "unseal"
    spy_all = daily_map(json.loads((udir / "daily" / "SPY_all.json").read_text()))
    spy_raw = daily_map(json.loads((udir / "daily" / "SPY_raw.json").read_text()))
    sessions = sorted(spy_all)
    by_key = {(e["symbol"], e["ts_ns"]): e for e in ledger["events"]}
    fetch_sym = {(r["symbol"], r["ts_ns"]): r["fetch_symbol"] for r in unseal["events"]}

    spy_minute: dict[str, list[dict]] = {}

    def _spy_min(day: str) -> list[dict]:
        if day not in spy_minute:
            p = udir / "spy_minute" / f"{day}.json"
            spy_minute[day] = json.loads(p.read_text()) if p.exists() else []
        return spy_minute[day]

    out: list[EventOutcome] = []
    for ev in frozen["events"]:
        key = (ev["symbol"], ev["ts_ns"])
        if key not in fetch_sym:
            continue
        sym = fetch_sym[key]
        try:
            bars_all = daily_map(json.loads(
                (udir / "daily" / f"{sym}_all.json").read_text()))
            bars_raw = daily_map(json.loads(
                (udir / "daily" / f"{sym}_raw.json").read_text()))
        except FileNotFoundError:
            continue
        session = ev["session"]
        h_sign = float(ev["h_sign"])
        f = adjustment_factor(bars_all.get(session, float("nan")),
                              bars_raw.get(session, float("nan")))
        spy_f = adjustment_factor(spy_all.get(session, float("nan")),
                                  spy_raw.get(session, float("nan")))
        drops: list[str] = []
        if not (math.isfinite(f) and math.isfinite(spy_f)):
            drops.append("no_adjustment_factor")

        beta = _beta_hat(sessions, bars_all, spy_all, session)
        sigma_pre = _sigma_pre(sessions, bars_all, spy_all, session)

        marks = _event_marks(out_dir, ev, f)
        spy_marks = _spy_marks(_spy_min(session), ev["ts_ns"], spy_f)

        ctrl_ts = list(by_key[key]["control_ts_ns"]) if key in by_key else []
        # PRIMARY = the PRE-event control sessions; the symmetric all-20 version
        # is carried alongside as the registered reported leg.
        pre_ctrl = [t for t in ctrl_ts if session_of(t) < session]

        y: dict[str, float] = {}
        censor: dict[str, str] = {}
        truncated: dict[str, bool] = {}
        earnings_free: dict[str, bool] = {}
        for hname, ksess in _horizon_steps(ev, sessions, session):
            target, trunc = horizon_target(sessions, session, ksess)
            truncated[hname] = trunc
            if target is None:
                censor[hname] = "none"
                continue
            px_end, used, flag = resolve_close(sessions, bars_all, target)
            censor[hname] = flag
            earnings_free[hname] = not earnings_in_window(
                ev["earnings_dates_45d"], session, used or target)
            spy_end = spy_all.get(used or target, float("nan"))
            # ALL_MARKS only: `marks` also carries M0_last_trade, which is a
            # robustness LEG at the M0 anchor, never a fourth anchor.
            for mark_name in ALL_MARKS:
                mk = marks[mark_name]
                legs = _leg_returns(mk, px_end, spy_marks[mark_name], spy_end,
                                    h_sign, beta)
                med = _control_median(out_dir, ev, pre_ctrl, sessions, bars_all,
                                      bars_raw, spy_all, spy_raw, ksess,
                                      mark_name, _spy_min)
                med_all = _control_median(out_dir, ev, ctrl_ts, sessions, bars_all,
                                          bars_raw, spy_all, spy_raw, ksess,
                                          mark_name, _spy_min)
                base = legs["spy_only"]
                y[f"{mark_name}|{hname}|primary"] = base - med
                y[f"{mark_name}|{hname}|all_controls"] = base - med_all
                y[f"{mark_name}|{hname}|raw"] = legs["raw"]
                y[f"{mark_name}|{hname}|spy_only"] = base
                y[f"{mark_name}|{hname}|beta_adj"] = legs["beta_adj"] - med
                scale = sigma_pre * math.sqrt(max(ksess, 1))
                y[f"{mark_name}|{hname}|sigma_std"] = (
                    (base - med) / scale if math.isfinite(scale) and scale > 0
                    else float("nan"))
            lt = marks.get("M0_last_trade")
            if lt is not None:
                y[f"M0|{hname}|last_trade"] = aligned_excess(
                    lt, px_end, spy_marks["M0"], spy_end, h_sign)
        # h1 is intraday: its endpoint is the minute bar at T+30min past M0
        _h1(out_dir, udir, ev, sym, marks, spy_marks, _spy_min, bars_all, bars_raw,
            spy_all, spy_raw, f, spy_f, h_sign, sigma_pre, beta, pre_ctrl, ctrl_ts,
            y, censor, truncated, earnings_free)

        out.append(EventOutcome(
            symbol=ev["symbol"], session=session, cluster=ev["cluster"],
            week=week_id(session), h_sign=h_sign,
            scores={k: float(ev[k]) for k in LABELLINGS},
            m=(ev["m_bps"] or 0.0) / 1e4, y=y, censor=censor, truncated=truncated,
            earnings_free=earnings_free, drops=drops))
    return out


def _horizon_steps(ev: dict, sessions: list[str], session: str):
    """(name, sessions-ahead) for every registered forward horizon."""
    for name, k in HORIZON_SESSIONS.items():
        yield name, k
    exp = ev.get("expiry_date")
    k_exp = H_EXP_CAP_SESSIONS
    if exp:
        later = [s for s in sessions if s >= exp]
        if later and session in sessions:
            k_exp = min(sessions.index(later[0]) - sessions.index(session),
                        H_EXP_CAP_SESSIONS)
    yield "h_exp", max(k_exp, 1)


def _event_marks(out_dir: Path, ev: dict, f: float) -> dict[str, float]:
    loaded = _load_window(out_dir, ev["symbol"], ev["ts_ns"])
    if loaded is None:
        return dict.fromkeys(ALL_MARKS, float("nan"))
    trades, quotes = loaded
    t0 = ev["ts_ns"]
    out = {}
    for name, off in (("M_pre", MARK_PRE_S), ("M0", MARK_M0_S),
                      ("M0_fast", MARK_FAST_S)):
        mk = mark_at(quotes, trades, t0 + off * 1_000_000_000)
        out[name] = mk.price * f
    out["M0_last_trade"] = last_trade_at(trades, t0 + MARK_M0_S * 1_000_000_000) * f
    return out


def _spy_marks(bars: list[dict], t0: int, spy_f: float) -> dict[str, float]:
    return {name: minute_close_at(bars, t0 + off * 1_000_000_000) * spy_f
            for name, off in (("M_pre", MARK_PRE_S), ("M0", MARK_M0_S),
                              ("M0_fast", MARK_FAST_S))}


MARK_OFFSETS = {"M_pre": MARK_PRE_S, "M0": MARK_M0_S, "M0_fast": MARK_FAST_S}


def _control_median(out_dir, ev, ctrl_ts, sessions, bars_all, bars_raw, spy_all,
                    spy_raw, ksess, mark_name, spy_min) -> float:
    """The registered control-median subtraction: the SAME quantity on the
    event's own control sessions — same name, same clock marks, same horizon."""
    vals = []
    off = MARK_OFFSETS[mark_name]
    for ts in ctrl_ts:
        cs = session_of(ts)
        if cs not in sessions:
            continue
        target, _ = horizon_target(sessions, cs, ksess)
        if target is None:
            continue
        px_end, used, flag = resolve_close(sessions, bars_all, target)
        if flag == "none":
            continue
        loaded = _load_window(out_dir, ev["symbol"], ts)
        if loaded is None:
            continue
        trades, quotes = loaded
        mk = mark_at(quotes, trades, ts + off * 1_000_000_000).price * factor_for(
            cs, bars_all, bars_raw)
        spy0 = minute_close_at(spy_min(cs), ts + off * 1_000_000_000) * factor_for(
            cs, spy_all, spy_raw)
        v = aligned_excess(mk, px_end, spy0, spy_all.get(used, float("nan")),
                           float(ev["h_sign"]))
        if math.isfinite(v):
            vals.append(v)
    return float(np.median(vals)) if vals else float("nan")


def _h1(out_dir, udir, ev, sym, marks, spy_marks, spy_min, bars_all, bars_raw,
        spy_all, spy_raw, f, spy_f, h_sign, sigma_pre, beta, pre_ctrl, ctrl_ts,
        y, censor, truncated, earnings_free):
    """h1 = M0 → T+30min. Fixed width, inside continuous trading, so it replaces
    3.2's same-session close and never straddles an auction."""
    t0 = ev["ts_ns"]
    path = udir / "minute" / sym / f"{t0}.json"
    bars = json.loads(path.read_text()) if path.exists() else []
    end = minute_close_at(bars, t0 + H1_OFFSET_S * 1_000_000_000)
    truncated["h1"] = False
    earnings_free["h1"] = True          # a 30-minute window never straddles a release
    censor["h1"] = "ok" if math.isfinite(end) else "none"
    if not math.isfinite(end):
        return
    end_adj = end * f
    spy_end = minute_close_at(spy_min(ev["session"]),
                              t0 + H1_OFFSET_S * 1_000_000_000) * spy_f
    ctrl_vals = {"pre": [], "all": []}
    for ts in ctrl_ts:
        cp = udir / "minute" / sym / f"{ts}.json"
        if not cp.exists():
            continue
        cbars = json.loads(cp.read_text())
        cend = minute_close_at(cbars, ts + H1_OFFSET_S * 1_000_000_000)
        loaded = _load_window(out_dir, ev["symbol"], ts)
        if loaded is None or not math.isfinite(cend):
            continue
        ctrades, cquotes = loaded
        cs = session_of(ts)
        cf = factor_for(cs, bars_all, bars_raw)
        csf = factor_for(cs, spy_all, spy_raw)
        c0 = mark_at(cquotes, ctrades, ts + MARK_M0_S * 1_000_000_000).price * cf
        cspy0 = minute_close_at(spy_min(cs), ts + MARK_M0_S * 1_000_000_000) * csf
        cspy1 = minute_close_at(spy_min(cs), ts + H1_OFFSET_S * 1_000_000_000) * csf
        v = aligned_excess(c0, cend * cf, cspy0, cspy1, h_sign)
        if math.isfinite(v):
            ctrl_vals["all"].append(v)
            if ts in pre_ctrl:
                ctrl_vals["pre"].append(v)
    med = float(np.median(ctrl_vals["pre"])) if ctrl_vals["pre"] else float("nan")
    med_all = float(np.median(ctrl_vals["all"])) if ctrl_vals["all"] else float("nan")
    for mark_name in ALL_MARKS:
        base = aligned_excess(marks[mark_name], end_adj, spy_marks[mark_name],
                              spy_end, h_sign)
        y[f"{mark_name}|h1|primary"] = base - med
        y[f"{mark_name}|h1|all_controls"] = base - med_all
        y[f"{mark_name}|h1|raw"] = aligned_excess(marks[mark_name], end_adj,
                                                  spy_marks[mark_name], spy_end,
                                                  h_sign, beta=0.0)
        y[f"{mark_name}|h1|spy_only"] = base
        y[f"{mark_name}|h1|beta_adj"] = aligned_excess(
            marks[mark_name], end_adj, spy_marks[mark_name], spy_end, h_sign,
            beta=beta) - med
        scale = sigma_pre * math.sqrt(1.0 / 13.0)   # 30 min of a 6.5h session
        y[f"{mark_name}|h1|sigma_std"] = ((base - med) / scale
                                          if math.isfinite(scale) and scale > 0
                                          else float("nan"))
    y["M0|h1|last_trade"] = aligned_excess(marks["M0_last_trade"], end_adj,
                                           spy_marks["M0"], spy_end, h_sign)


def _pre_session_returns(sessions: list[str], bars: dict[str, float],
                         spy: dict[str, float], session: str,
                         lookback: int) -> tuple[np.ndarray, np.ndarray]:
    if session not in sessions:
        return np.zeros(0), np.zeros(0)
    i = sessions.index(session)
    lo = max(0, i - lookback)
    r, s = [], []
    for a, b in zip(sessions[lo:i], sessions[lo + 1:i + 1], strict=False):
        if a in bars and b in bars and a in spy and b in spy:
            r.append(log_ret(bars[a], bars[b]))
            s.append(log_ret(spy[a], spy[b]))
    return np.array(r, dtype=np.float64), np.array(s, dtype=np.float64)


def _beta_hat(sessions, bars, spy, session, lookback: int = BETA_LOOKBACK) -> float:
    r, s = _pre_session_returns(sessions, bars, spy, session, lookback)
    ok = np.isfinite(r) & np.isfinite(s)
    if ok.sum() < 10 or np.var(s[ok]) <= 0:
        return 1.0
    return float(np.cov(r[ok], s[ok])[0, 1] / np.var(s[ok], ddof=1))


def _sigma_pre(sessions, bars, spy, session, lookback: int = SIGMA_LOOKBACK) -> float:
    r, s = _pre_session_returns(sessions, bars, spy, session, lookback)
    ok = np.isfinite(r) & np.isfinite(s)
    return robust_sd(r[ok] - s[ok])


# --- the analysis proper ------------------------------------------------------

def _cut(outcomes: list[EventOutcome], labelling: str, mark: str, horizon: str,
         leg: str = "primary", earnings_free_only: bool = True) -> dict:
    key = f"{mark}|{horizon}|{leg}"
    rows = []
    for o in outcomes:
        s = o.scores[labelling]
        if s <= -HIGH_THRESHOLD:
            continue                     # ANTI is never pooled into the contrast
        if earnings_free_only and not o.earnings_free.get(horizon, True):
            continue
        if o.censor.get(horizon) == "none":
            continue
        v = o.y.get(key, float("nan"))
        if not math.isfinite(v):
            continue
        rows.append((o, s, v))
    return {
        "y": np.array([v for _, _, v in rows], dtype=np.float64),
        "score": np.array([s for _, s, _ in rows], dtype=np.float64),
        "is_high": np.array([s >= HIGH_THRESHOLD for _, s, _ in rows], dtype=bool),
        "m": np.array([o.m for o, _, _ in rows], dtype=np.float64),
        "cluster": np.array([o.cluster for o, _, _ in rows]),
        "week": np.array([o.week for o, _, _ in rows]),
        "symbol": np.array([o.symbol for o, _, _ in rows]),
        "h_sign": np.array([o.h_sign for o, _, _ in rows], dtype=np.float64),
        "rows": [o for o, _, _ in rows],
    }


def _blocks_for(horizon: str, cut: dict) -> np.ndarray:
    """Calendar-week blocks at h2/h3 (10.9% of event pairs sit within 5
    sessions); the cluster is the block everywhere else."""
    return cut["week"] if horizon in ("h2", "h3") else cut["cluster"]


def analyse_cut(cut: dict, horizon: str, n_perm: int, seed: int) -> dict:
    if len(cut["y"]) < 8 or cut["is_high"].sum() < 2 or (~cut["is_high"]).sum() < 2:
        return {"n": int(len(cut["y"])), "evaluable": False,
                "reason": "too few events in one stratum"}
    engine = PermEngine(_blocks_for(horizon, cut), cut["is_high"], cut["score"],
                        cut["m"], n_perm=n_perm, seed=seed)
    res = engine.coprimary(cut["y"])
    res["n"] = int(len(cut["y"]))
    res["evaluable"] = True
    res["delta_hat"] = float(engine.delta(cut["y"])[0])
    res["block"] = "calendar_week" if horizon in ("h2", "h3") else "cluster"
    res["coprimary_pass"] = bool(
        np.isfinite(res["adjusted_p"]) and res["adjusted_p"] < ALPHA
        and res["observed"]["delta"] > 0 and res["observed"]["spearman"] > 0)
    res["coprimary_inverted"] = bool(
        np.isfinite(res["inverted_adjusted_p"]) and res["inverted_adjusted_p"] < ALPHA
        and res["observed"]["delta"] < 0 and res["observed"]["spearman"] < 0)
    res["conflicting_signs"] = bool(
        res["observed"]["delta"] * res["observed"]["spearman"] < 0)
    res["residualized_pass"] = bool(
        np.isfinite(res["residualized"]["permutation_p"])
        and res["residualized"]["permutation_p"] < ALPHA
        and res["residualized"]["coefficient"] > 0)
    return res


def all_verdict_cells_fail(grid: dict[str, dict]) -> bool:
    """LABEL-EMPTY's precondition, as a pure function over the verdict grid:
    both co-primaries dead in EVERY evaluable cell of 3 horizons × 3 labellings
    × 2 marks. One live co-primary anywhere — in either direction — vetoes the
    kill, which is why an INVERTED cell counts as a failure to fail.

    A grid with no evaluable cell returns False: nothing was measured, so
    nothing may be killed.
    """
    cells = [v for v in grid.values() if v.get("evaluable")]
    if not cells:
        return False
    return all(not c.get("coprimary_pass") and not c.get("coprimary_inverted")
               for c in cells)


def jackknife_weeks(cut: dict, horizon: str, n_perm: int, seed: int) -> dict:
    """Leave-one-calendar-week-out: any single week whose removal pushes the
    primary p above α ⇒ AMBIGUOUS."""
    worst, per_week = 0.0, {}
    for w in sorted(set(cut["week"].tolist())):
        keep = cut["week"] != w
        sub = {k: (v[keep] if isinstance(v, np.ndarray) else v)
               for k, v in cut.items() if k != "rows"}
        sub["rows"] = [r for r, k in zip(cut["rows"], keep, strict=True) if k]
        r = analyse_cut(sub, horizon, n_perm, seed)
        p = r.get("adjusted_p", float("nan"))
        per_week[w] = p
        if not np.isfinite(p) or p > worst:
            worst = float(p) if np.isfinite(p) else float("inf")
    return {"max_p": worst, "per_week": per_week, "survives": bool(worst < ALPHA)}


def stage_analyze(out_dir: Path, n_perm: int, n_boot: int, n_perm_jack: int,
                  n_rep: int, seed: int) -> dict:
    frozen = verify_frozen(out_dir)
    unseal = require_unseal(out_dir)
    ledger = json.loads((out_dir / "fetch_ledger_equity.json").read_text())
    outcomes = compute_outcomes(out_dir, frozen, ledger, unseal)
    logger.info("analyze: %s events with outcomes", len(outcomes))

    # --- the full EMPTY grid: 3 horizons × 3 labellings × 2 marks -------------
    grid: dict[str, dict] = {}
    for horizon in VERDICT_HORIZONS:
        for labelling in LABELLINGS:
            for mark in VERDICT_MARKS:
                cut = _cut(outcomes, labelling, mark, horizon)
                grid[f"{horizon}|{labelling}|{mark}"] = analyse_cut(
                    cut, horizon, n_perm, seed)
    all_fail = all_verdict_cells_fail(grid)

    # --- the primary contrast: h2, primary labelling ------------------------
    primary = {}
    for mark in VERDICT_MARKS:
        cut = _cut(outcomes, "score", mark, PRIMARY_HORIZON)
        res = analyse_cut(cut, PRIMARY_HORIZON, n_perm, seed)
        if res.get("evaluable"):
            boot = stratified_bootstrap_weights(cut["cluster"], cut["is_high"],
                                                n_boot=n_boot, seed=seed) @ cut["y"]
            jack = jackknife_weights(cut["cluster"], cut["is_high"]) @ cut["y"]
            res["ci95"] = bca_ci(res["delta_hat"], boot, jack)
            res["null_sign_flip"] = sign_flip_p(
                cut["y"][~cut["is_high"]], cut["cluster"][~cut["is_high"]], seed=seed)
            res["null_equivalence"] = _null_equivalence(cut, n_boot, seed)
            res["n_underlyings_high"] = int(
                len(set(cut["symbol"][cut["is_high"]].tolist())))
            res["long_side"] = _side_report(cut, +1.0)
            res["short_side"] = _side_report(cut, -1.0)
        primary[mark] = res

    carrier = next((m for m in VERDICT_MARKS
                    if primary[m].get("coprimary_pass")), VERDICT_MARKS[0])
    carrier_res = primary[carrier]
    carrier_cut = _cut(outcomes, "score", carrier, PRIMARY_HORIZON)

    jack = (jackknife_weeks(carrier_cut, PRIMARY_HORIZON, n_perm_jack, seed)
            if carrier_res.get("evaluable") else {"survives": False, "max_p": None})

    # --- sign agreement across label variants, anchors, σ-standardized -------
    signs = {}
    for labelling in LABELLINGS:
        c = _cut(outcomes, labelling, carrier, PRIMARY_HORIZON)
        signs[f"label:{labelling}"] = _point_delta(c)
    for mark in ALL_MARKS:
        c = _cut(outcomes, "score", mark, PRIMARY_HORIZON)
        signs[f"anchor:{mark}"] = _point_delta(c)
    c = _cut(outcomes, "score", carrier, PRIMARY_HORIZON, leg="sigma_std")
    signs["leg:sigma_std"] = _point_delta(c)
    finite = [v for v in signs.values() if math.isfinite(v)]
    sign_agreement = bool(finite and (all(v > 0 for v in finite)
                                      or all(v < 0 for v in finite)))

    # --- Gate 2: realized MDE from the ACTUAL events' dispersion -------------
    gate2 = _gate2(carrier_cut, n_perm=min(n_perm, 5_000), n_rep=n_rep,
                   n_boot=n_boot, seed=seed) if carrier_res.get("evaluable") else {
                       "mde": None, "power_by_delta": {}, "note": "not evaluable"}

    censor_frac = _censoring_report(outcomes)
    vi = VerdictInputs(
        n_high_events=carrier_res.get("n_high", 0),
        n_high_underlyings=carrier_res.get("n_underlyings_high", 0),
        n_null_events=carrier_res.get("n_null", 0),
        censor_frac_high=censor_frac["HIGH"], censor_frac_null=censor_frac["NULL"],
        gate2_mde=gate2.get("mde"),
        coprimary_pass=bool(carrier_res.get("coprimary_pass")),
        coprimary_inverted=bool(carrier_res.get("coprimary_inverted")),
        coprimary_conflicting_signs=bool(carrier_res.get("conflicting_signs")),
        residualized_pass=bool(carrier_res.get("residualized_pass")),
        delta_point=float(carrier_res.get("delta_hat", 0.0) or 0.0),
        ci_lo=float(carrier_res.get("ci95", {}).get("lo", float("nan"))),
        ci_valid=bool(carrier_res.get("ci95", {}).get("valid")),
        sign_agreement=sign_agreement,
        jackknife_ok=bool(jack.get("survives")),
        null_mean_positive=bool(
            (carrier_res.get("null_sign_flip") or {}).get("p", 1.0) < NULL_POSITIVE_ALPHA
            and (carrier_res.get("null_sign_flip") or {}).get("mean", 0.0) > 0),
        all_horizons_all_labellings_fail=all_fail,
        notes=[f"carrier mark = {carrier}; the other mark is disclosed in `primary`"],
    )
    vd = route_verdict(vi)

    result = {
        "spec": SPEC, "stage": "analyze", "frozen_sha256": FROZEN_SHA256,
        "seed": seed, "n_perm": n_perm, "n_boot": n_boot, "alpha": ALPHA,
        "n_events": len(outcomes),
        "carrier_mark": carrier,
        "primary": primary,
        "verdict_grid": grid,
        "all_horizons_all_labellings_fail": all_fail,
        "jackknife": jack,
        "sign_agreement": {"agrees": sign_agreement, "point_deltas": signs},
        "gate2": gate2,
        "censoring": censor_frac,
        "anti_stratum": _anti_report(outcomes, carrier),
        "reported_only": {
            h: {m: _point_delta(_cut(outcomes, "score", m, h))
                for m in VERDICT_MARKS} for h in ("h4", "h_exp")},
        "truncation": _truncation_report(outcomes),
        **vd,
        "spec_ambiguities": SPEC_AMBIGUITIES,
        "disclosure": (
            "Training window only; a sealed test split and forward paper remain "
            "the confirmation leg. Any positive here is a CALIBRATED HYPOTHESIS, "
            "never a live signal. Returns are gross of borrow/financing/SSR — a "
            "short-side-carried result is a research finding only."),
    }
    save_json(out_dir / RESULTS_NAME, result)
    logger.info("verdict: %s", result["verdict"])
    return result


def _point_delta(cut: dict) -> float:
    if not len(cut["y"]) or not cut["is_high"].any() or not (~cut["is_high"]).any():
        return float("nan")
    return float(cut["y"][cut["is_high"]].mean() - cut["y"][~cut["is_high"]].mean())


def _side_report(cut: dict, sign: float) -> dict:
    m = cut["h_sign"] == sign
    if m.sum() < 3:
        return {"n": int(m.sum()), "delta": None}
    sub = {k: (v[m] if isinstance(v, np.ndarray) else v) for k, v in cut.items()
           if k != "rows"}
    return {"n": int(m.sum()), "delta": _point_delta(sub)}


def _null_equivalence(cut: dict, n_boot: int, seed: int) -> dict:
    """NULL's ≈0 claim: a 95% stratified-bootstrap CI inside ±0.5%."""
    m = ~cut["is_high"]
    y = cut["y"][m]
    cl = cut["cluster"][m]
    if len(y) < 5:
        return {"n": int(len(y)), "ci": None, "equivalent": False}
    keys, inv = np.unique(cl, return_inverse=True)
    rng = np.random.default_rng(seed)
    draws = np.empty(n_boot, dtype=np.float64)
    memb = [np.flatnonzero(inv == c) for c in range(len(keys))]
    for b in range(n_boot):
        idx = np.concatenate([memb[c] for c in rng.integers(0, len(memb), len(memb))])
        draws[b] = float(y[idx].mean())
    ci = bca_ci(float(y.mean()), draws, np.array(
        [float(np.delete(y, memb[c]).mean()) if len(y) > len(memb[c]) else np.nan
         for c in range(len(memb))]))
    return {"n": int(len(y)), "mean": float(y.mean()), "ci": ci,
            "equivalent": equivalence_ok(ci), "band": EQUIV_BAND}


def _anti_report(outcomes: list[EventOutcome], mark: str) -> dict:
    """ANTI (s ≤ −3): n = 8 < 15, so counts + point estimates only, no p/CI.

    Registered prediction: a significantly negative aligned return here
    evidences MISCLASSIFICATION, not the thesis.
    """
    out = {}
    for horizon in VERDICT_HORIZONS:
        vals = [o.y.get(f"{mark}|{horizon}|primary", float("nan"))
                for o in outcomes if o.scores["score"] <= -HIGH_THRESHOLD]
        vals = [v for v in vals if math.isfinite(v)]
        out[horizon] = {"n": len(vals),
                        "mean": float(np.mean(vals)) if vals else None,
                        "median": float(np.median(vals)) if vals else None}
    out["note"] = ("n < 15 — counts and point estimates only, no p/CI, never "
                   "pooled into the HIGH−NULL contrast")
    return out


def _censoring_report(outcomes: list[EventOutcome]) -> dict:
    out = {}
    for name, pick in (("HIGH", lambda s: s >= HIGH_THRESHOLD),
                       ("NULL", lambda s: abs(s) < HIGH_THRESHOLD)):
        sub = [o for o in outcomes if pick(o.scores["score"])]
        dropped = [o.symbol for o in sub if o.censor.get(PRIMARY_HORIZON) == "none"]
        out[name] = len(dropped) / len(sub) if sub else 0.0
        out[f"{name}_dropped"] = dropped
        out[f"{name}_censored"] = [
            {"symbol": o.symbol, "session": o.session,
             "flag": o.censor.get(PRIMARY_HORIZON)}
            for o in sub if o.censor.get(PRIMARY_HORIZON) in ("resumption", "last_price")]
    return out


def _truncation_report(outcomes: list[EventOutcome]) -> dict:
    return {h: sum(1 for o in outcomes if o.truncated.get(h))
            for h in list(HORIZON_SESSIONS) + ["h_exp"]}


def _gate2(cut: dict, n_perm: int, n_rep: int, n_boot: int, seed: int) -> dict:
    """Gate 2: realized MDE recomputed from the ACTUAL events' dispersion."""
    y = cut["y"]
    is_high = cut["is_high"]
    sigma = np.empty(len(y), dtype=np.float64)
    strat = {}
    for name, mask in (("HIGH", is_high), ("NULL", ~is_high)):
        sd = robust_sd(y[mask])
        strat[name] = sd
        sigma[mask] = sd
    bank = []
    for i in range(len(y)):
        pool = y[is_high] if is_high[i] else y[~is_high]
        centred = pool - np.median(pool)
        sd = robust_sd(centred)
        bank.append(centred / sd if math.isfinite(sd) and sd > 0
                    else np.zeros(len(centred)))
    harness = Harness(
        engine=PermEngine(_blocks_for(PRIMARY_HORIZON, cut), is_high, cut["score"],
                          cut["m"], n_perm=n_perm, seed=seed),
        boot_w=stratified_bootstrap_weights(cut["cluster"], is_high,
                                            n_boot=n_boot, seed=seed),
        jack_w=jackknife_weights(cut["cluster"], is_high),
        is_high=is_high.astype(np.float64), bank=bank,
        block=_blocks_for(PRIMARY_HORIZON, cut), sigma=sigma,
    )
    out = injection_power(harness, n_rep=n_rep, seed=seed)
    out["realized_stratum_dispersion"] = strat
    out["gate2_ok"] = out["mde"] is not None and out["mde"] <= GATE_MDE_FLOOR
    out["rule"] = ("realized MDE > 1.5% ⇒ any non-significant result routes "
                   "NO-VERDICT-UNDERPOWERED and may NEVER route LABEL-EMPTY")
    return out


# =============================================================================
# cli
# =============================================================================

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("stage", choices=("gate1", "unseal", "analyze"))
    parser.add_argument("--out-dir", type=Path, default=OUT_DIR)
    parser.add_argument("--n-perm", type=int, default=N_PERM)
    parser.add_argument("--n-perm-gate1", type=int, default=5_000)
    parser.add_argument("--n-perm-jackknife", type=int, default=10_000)
    parser.add_argument("--n-boot", type=int, default=N_BOOT)
    parser.add_argument("--n-rep", type=int, default=300,
                        help="injection-harness replications per δ")
    parser.add_argument("--seed", type=int, default=SEED)
    parser.add_argument("--limit", type=int, default=None,
                        help="cap events (unseal) — smoke runs only")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")

    try:
        if args.stage == "gate1":
            doc = stage_gate1(args.out_dir, args.n_perm_gate1, args.n_rep, args.seed)
            return 0 if doc["proceed"] else 1
        if args.stage == "analyze":
            stage_analyze(args.out_dir, args.n_perm, args.n_boot,
                          args.n_perm_jackknife, args.n_rep, args.seed)
            return 0
        # ★ both locks fire BEFORE a client, a credential or a URL exists — the
        # refusal must not depend on being able to reach Alpaca at all.
        verify_frozen(args.out_dir)
        require_gate1(args.out_dir)
        cfg = alpaca_config()
        if not cfg["api_key_id"]:
            raise SystemExit("no Alpaca credentials in settings/.env")
        headers = {"APCA-API-KEY-ID": cfg["api_key_id"],
                   "APCA-API-SECRET-KEY": cfg["api_secret_key"]}
        with httpx.Client(headers=headers, timeout=90.0) as client:
            stage_unseal(args.out_dir, client, cfg["data_base_url"], args.limit)
        return 0
    except SealError as exc:
        logger.error("SEAL REFUSED: %s", exc)
        return 1
    except TripwireError as exc:
        logger.error("TRIPWIRE: %s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
