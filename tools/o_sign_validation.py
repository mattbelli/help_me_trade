#!/usr/bin/env python3
"""o sign-convention validation — pipeline for the REGISTERED prereg
`References/O_SIGN_VALIDATION_PREREG_2026-08-03.md` (LAW; founder GO 2026-08-03).

Does quote-position carry taker direction on negotiated option packages?
Ground truth = tied-to-stock trades: the stock leg prints on the equity tape as
part of the same deal ('7'∧'V' QCT prints), mechanically coupled to the option
side. Consumes NO outcome data — no forward returns, nothing sealed.

Stages (run in order; every stage writes an artifact under data/research/o_sign):
    frame   build + SHA the per-session tied frames (Step-1 strict single-leg
            population, Step-2 2-leg spread-edge cell) for all qualifying
            sessions; census re-run through the FIXED loader (UNDEF_SIZE strip,
            o_sum corner) with supersession deltas vs tied_stock_census.py
    fetch1  Step-1 stock snapshots (±300s trades+quotes, 3.2 snap cache) +
            alt-session placebo windows, every 6th qualifying session
    step1   MF-1 match + strict-quote signing + placebos -> a_cal, mechanics
            gates, Step-1 truth table
    pilot   Step-2 pilot: 3 tercile-median sessions, fetch + match, label-blind
            BY FILE RULE (observed sides -> pilot_observed_sealed.json, SHA'd;
            no gate code reads it)
    gate    pre-run power gate: full verdict-conjunction simulation ->
            n_required; registered ladder step 1 (join floor 0.97) else NO-RUN
    fetch2  full-run Step-2 snapshots (every remaining qualifying session)
    step2   unit aggregation + dual permutation + Gate 2 + verdict + strata

Amendment 1 (2026-08-05, References/O_SIGN_VALIDATION_AMENDMENT1_2026-08-05.md
— IS LAW) adds the successor-arm stages and amends pilot/gate/fetch2/step2 in
place (signed [−1,+3] ms window, seeded terciles, reweighted LB of record):
    record_shas  SHA-256 every frame npz into the manifest (precondition)
    s2meta       Step-2 inter-leg spans + leg quote-position classes (A1-2/6b)
    fetch1p      Step-1′ midpoint event table + FRESH fetches on pool[3::6]
    step1p       Step-1′ analysis; rows (iv)–(viii) finalize at the gate

Registered conventions pinned here (tests in tests/test_o_sign_validation.py):
  - corpus `side`/`strict` are LABELS (0=buy, 1=sell, 2/3 unusable), mapped to
    ±1 explicitly; equity signing returns ±1/0.
  - the taker's stock leg is the NEGATION of the dealer hedge map: taker buys
    call ⇒ taker SELLS stock; buys put ⇒ BUYS; sells reverse.
  - Step-2: A = lower instrument_id; o_diff ≥ 0.5 ⇒ taker bought A / sold B;
    σ_net = sign(dir_A × (Δ_A − Δ_B)); predicted stock side = −σ_net
    (A/B-swap invariant away from o_diff = 0.5, which the edge cell excludes).
  - stock-quote rule: last NBBO STRICTLY BEFORE the print's ts
    (`prevailing_quote_idx_strict`); a quote stamped at exactly ts is NOT used.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from config.settings import alpaca_config  # noqa: E402
from db.flow_corpus import DEFAULT_DB_PATH, bars_before_date  # noqa: E402
from tools.condition_labels import TIED_CODES  # noqa: E402
from tools.flow_corpus_rollup import (  # noqa: E402
    DEFS_DIR,
    LABELS_DIR,
    PX,
    TCBBO_DIR,
    fence_and_classify,
    multileg_flags,
)
from tools.hedge_footprint import (  # noqa: E402
    DTE_MIN,
    UNDEF_SIZE,
    FeedAssertionError,
    MissingDailyBar,
    PageCapError,
    _snap_path,
    build_condition_table,
    et_second_of_day,
    fetch_window,
    fit_delta,
    in_trading_window,
    load_option_defs,
    qualifying_sessions,
    save_json,
    save_npz,
    session_midnight_ns,
    time_to_expiry_years,
)
from tools.root_classification_build import load as load_classification  # noqa: E402

# --- registered constants (prereg 2026-08-03) --------------------------------
W_NS = 100_000                       # §3a pairing window
EDGE_C = 0.10                        # §3c corner cut (spread-edge cell)
PREM_MIN_S1 = 10_000.0               # Step-1 premium floor
NETD_MIN = 0.10                      # Step-2 |netΔ_select| floor
PRIMARY_W_NS = 100_000_000           # ±100 ms — Addendum E join tolerance
SENS_W_NS = (1_820_000_000, 5_000_000_000)   # ±1.82 s (3.1 grain), ±5 s
COHERENCE_MIN = 0.60                 # |S_net| / Σsize
MATCH_GATE = 0.20                    # absolute mechanics gates …
SIGNABLE_GATE = 0.50                 # … on the MF-1 rule, claimant drops out of denom
C_MAX_PRIMARY = 0.01                 # placebo contamination bound at ±100 ms
PLACEBO_OFFSET_NS = 60_000_000_000   # T+60 s (inside the fetched ±300 s window)
DRIFT_PRE_NS = 2_000_000_000         # pre-print |δS| over [T−2s, T)
LAG_SENS_NS = 1_000_000_000          # ±1 s lagged-quote sensitivity
STEP1_EVERY = 6                      # every 6th qualifying session (~18)
STEP1_CAP = 300                      # per session, premium-stratified thirds
N_PERM = 50_000
SEED = 20260803
N_BOOT = 10_000                      # session-cluster bootstrap draws
THETA_BACKSTOP = 0.60
A_O_DETECT = 0.65                    # gate: power ≥ 0.80 to detect true a_o = 0.65
POWER_TARGET = 0.80
STRESS_POWER_MIN = 0.60
FLOOR_UNDERLYINGS = 25
FLOOR_SESSIONS = 20
PILOT_SESSIONS = 3
STOP_MECHANICS_FRAC = 0.20           # >20% of sampled events lost to fetch faults
GATE_SIM = 400                       # power-sim replicates
GATE_PERM = 1_000                    # name-block flips inside each sim replicate

# --- Amendment 1 constants (O_SIGN_VALIDATION_AMENDMENT1_2026-08-05.md) ------
W_LO_NS = -1_000_000                 # A1-2 signed primary window [−1 ms, +3 ms]
W_HI_NS = 3_000_000
A1_SENS_W_NS = (10_000_000, 100_000_000)   # deliberately SYMMETRIC ±10/±100 ms
CLAIMANT_R_NS = 200_000_000          # A1-3: DECOUPLED from the match window
SEED_A1 = 20260805                   # A1-7: every amended stage's draws
TIE_SUBSTREAM_OFF = 5                # A1-5/A1-7: tie-break substream = SEED+5
LAG_TRIPWIRE_NS = 20_000_000         # A1-8: tripwire-bearing lag leg (±20 ms)
STEP1P_RESIDUE = 3                   # A1-4: Step 1′ sessions = pool[3::6]
REGIME_BOUNDARY = "2026-02-01"       # A1-2: sessions ≥ this are post-shift
SKEW_BIN_MS = 0.2                    # A1-2 pinned skew estimator
SKEW_SPLIT_MS = 0.5                  #   sub-mode split (neg-side < 0.5 ≤ pos-side)
SKEW_EDGE_MIN_MS = 0.5               #   each sub-mode ≥ 0.5 ms from nearest edge
CAPTURE_FLOOR = 0.30                 #   primary-window / ±100 ms capture floor
PLACEBO_COVERAGE_MIN = 0.95          # A1-4 coded placebo-coverage assertion
THETA_GRID_A1 = [0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90, 0.95]  # A1-6(c)
LADDER_N = (105, 175)                # A1-6(c): θ at N=105, recompute at N=175
SPAN_P90_MAX_NS = 1_000_000          # A1-2 Step-2 inter-leg span precondition
REGIME_MISMATCH_PP = 0.10            # A1-2: Step1′ vs Step2 composition tripwire
TRIPWIRE_DELTA = 0.10                # A1-8: >10-point a_cal/split tripwires

OUT_DIR = ROOT / "data" / "research" / "o_sign"
FRAME_DIR = OUT_DIR / "frame"


# =============================================================================
# sign conventions — pure, pinned by tests
# =============================================================================

def taker_stock_side(option_side_label: int, is_put: bool) -> int:
    """Predicted stock-leg side (±1 buy/sell) for a SINGLE option leg.

    `option_side_label` is the corpus LABEL alphabet (0=buy, 1=sell; 2/3 are
    unusable and must be filtered upstream — passing one raises). The taker's
    stock leg is the NEGATION of the dealer map: buy call ⇒ SELL stock (−1),
    buy put ⇒ BUY (+1), sells reverse.
    """
    if option_side_label not in (0, 1):
        raise ValueError(f"unusable side label {option_side_label}")
    side_sign = 1 if option_side_label == 0 else -1
    cp_sign = -1 if is_put else 1              # sign(Δ): call +, put −
    return -side_sign * cp_sign


def sigma_net(o_diff: float, net_delta: float) -> int:
    """σ_net for a 2-leg package: sign of the TAKER's net option delta.

    o_diff ≥ 0.5 ⇒ taker bought A / sold B (§3 convention, A = lower
    instrument_id), so taker net Δ = +(Δ_A − Δ_B); o_diff < 0.5 the reverse.
    Swapping A and B flips both o_diff and (Δ_A − Δ_B): σ_net is invariant
    away from o_diff = 0.5 (excluded by the spread-edge cell).
    """
    dir_a = 1 if o_diff >= 0.5 else -1
    s = np.sign(net_delta)
    if s == 0:
        raise ValueError("net_delta == 0 has no σ_net")
    return int(dir_a * s)


def prevailing_quote_idx_strict(trade_t: np.ndarray, quote_t: np.ndarray) -> np.ndarray:
    """Index of the last quote STRICTLY BEFORE each trade ts; −1 where none.

    NEW function per the prereg: `prevailing_quote_idx` (at-or-before) is NOT
    reused on the stock side — a quote stamped at exactly the print's ts is
    excluded here.
    """
    if not len(quote_t):
        return np.full(len(trade_t), -1, dtype=np.int64)
    return np.searchsorted(quote_t, trade_t, side="left") - 1


def sign_stock_prints(trade_t: np.ndarray, trade_px: np.ndarray,
                      quote_t: np.ndarray, bid: np.ndarray, ask: np.ndarray,
                      lag_ns: int = 0) -> np.ndarray:
    """±1/0 per stock print vs the strictly-prior NBBO midpoint (0 = unsignable:
    at-mid, no prior quote, or crossed/empty book). `lag_ns` shifts the quote
    clock for the registered ±1 s sensitivity leg."""
    idx = prevailing_quote_idx_strict(trade_t, quote_t + lag_ns)
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


# =============================================================================
# MF-1 matcher — stock side AGGREGATES, option side already deduplicated
# =============================================================================

_EXCL_BY_FAM = None


def _excluded_mask(cond: np.ndarray, tape: np.ndarray) -> np.ndarray:
    """True where the print carries any code in its tape's excluded set
    (unknown tape → union of both alphabets, the conservative arm)."""
    global _EXCL_BY_FAM
    if _EXCL_BY_FAM is None:
        t = build_condition_table()
        both = sorted(set(t["CTA"]["excluded"]) | set(t["UTP"]["excluded"]))
        _EXCL_BY_FAM = {"CTA": t["CTA"]["excluded"], "UTP": t["UTP"]["excluded"],
                        "": both}
    fam = np.full(len(tape), "", dtype="U3")
    fam[(tape == "A") | (tape == "B")] = "CTA"
    fam[tape == "C"] = "UTP"
    out = np.zeros(len(cond), dtype=bool)
    for f, codes in _EXCL_BY_FAM.items():
        sel = fam == f
        if sel.any():
            m = np.zeros(int(sel.sum()), dtype=bool)
            for ch in codes:
                m |= np.char.find(cond[sel], ch) >= 0
            out[sel] = m
    return out


def match_event(trades: dict, quotes: dict, t0_ns: int, w_ns: int,
                lag_ns: int = 0) -> dict:
    """MF-1 on one event window. Returns the funnel + S_net + Δt of the
    nearest candidate. `matched` = ≥1 qualifying '7'∧'V' print in [t0−w, t0+w];
    `signable` = coherence |S_net|/Σsize ≥ 0.60 with S_net ≠ 0."""
    cond, tape = trades["cond"], trades["tape"]
    n = len(cond)
    if n:
        has7 = np.char.find(cond, "7") >= 0
        hasv = np.char.find(cond, "V") >= 0
        excl = _excluded_mask(cond, tape)
    else:
        has7 = hasv = excl = np.zeros(0, dtype=bool)
    in_w = np.abs(trades["t_ns"] - t0_ns) <= w_ns
    qual = has7 & hasv & ~excl & in_w
    out = {
        "n_window": int(in_w.sum()),
        "n_7only": int((has7 & ~hasv & in_w).sum()),
        "n_Vonly": int((hasv & ~has7 & in_w).sum()),   # data-change tripwire
        "n_excl_overlap": int((has7 & hasv & excl & in_w).sum()),
        "n_qual": int(qual.sum()),
        "matched": bool(qual.any()),
        "signable": False, "S_net": 0.0, "sum_size": 0.0, "coherence": 0.0,
        "obs_side": 0, "dt_ns": None, "stock_shares": 0.0,
    }
    if not qual.any():
        return out
    q_t = trades["t_ns"][qual]
    q_px = trades["price"][qual]
    q_sz = trades["size"][qual].astype(np.float64)
    signs = sign_stock_prints(q_t, q_px, quotes["t_ns"], quotes["bid"],
                              quotes["ask"], lag_ns=lag_ns)
    s_net = float((signs * q_sz).sum())
    tot = float(q_sz.sum())
    coh = abs(s_net) / tot if tot > 0 else 0.0
    out.update({
        "S_net": s_net, "sum_size": tot, "coherence": coh,
        "signable": bool(coh >= COHERENCE_MIN and s_net != 0.0),
        "obs_side": int(np.sign(s_net)),
        "dt_ns": int(q_t[np.argmin(np.abs(q_t - t0_ns))] - t0_ns),
        "stock_shares": tot,
    })
    return out


def drift_pre(quotes: dict, t0_ns: int) -> float:
    """Pre-print |δS| over [T−2s, T): |mid(strictly before T) −
    mid(strictly before T−2s)| / the earlier mid. NaN if either side missing."""
    mids = []
    for t in (t0_ns - DRIFT_PRE_NS, t0_ns):
        i = int(prevailing_quote_idx_strict(np.array([t]), quotes["t_ns"])[0])
        if i < 0:
            return float("nan")
        b, a = float(quotes["bid"][i]), float(quotes["ask"][i])
        if not (b > 0.0 and a > b):
            return float("nan")
        mids.append((a + b) / 2.0)
    return abs(mids[1] - mids[0]) / mids[0]


# =============================================================================
# statistics — session-cluster bootstrap, permutations, κ
# =============================================================================

def agreement(pred: np.ndarray, obs: np.ndarray) -> float:
    return float((pred == obs).mean()) if len(pred) else float("nan")


def kappa(pred: np.ndarray, obs: np.ndarray) -> float:
    """Chance-corrected agreement with chance from the REALIZED marginals."""
    if not len(pred):
        return float("nan")
    a = agreement(pred, obs)
    p1, o1 = float((pred == 1).mean()), float((obs == 1).mean())
    pe = p1 * o1 + (1 - p1) * (1 - o1)
    return (a - pe) / (1 - pe) if pe < 1.0 else float("nan")


def session_bootstrap_ci(pred: np.ndarray, obs: np.ndarray, sess: np.ndarray,
                         n_boot: int = N_BOOT, seed: int = SEED,
                         alpha: float = 0.05) -> tuple[float, float]:
    """Percentile CI on agreement, resampling SESSIONS with replacement
    (never naive Wilson — the prereg's clustering rule)."""
    rng = np.random.default_rng(seed)
    uniq = np.unique(sess)
    stats = np.empty(n_boot)
    hit = pred == obs
    by = {u: hit[sess == u] for u in uniq}
    for i in range(n_boot):
        pick = rng.choice(uniq, size=len(uniq), replace=True)
        arr = np.concatenate([by[u] for u in pick])
        stats[i] = arr.mean() if len(arr) else np.nan
    stats = stats[np.isfinite(stats)]
    return (float(np.percentile(stats, 100 * alpha / 2)),
            float(np.percentile(stats, 100 * (1 - alpha / 2))))


def perm_p_within_session(pred: np.ndarray, obs: np.ndarray, sess: np.ndarray,
                          n_perm: int = N_PERM, seed: int = SEED) -> float:
    """Two-sided session-stratified permutation p: shuffle predictions across
    events WITHIN each session."""
    rng = np.random.default_rng(seed)
    real = abs(agreement(pred, obs) - 0.5)
    uniq = np.unique(sess)
    idx_by = [np.flatnonzero(sess == u) for u in uniq]
    hits = 0
    p = pred.copy()
    for _ in range(n_perm):
        for idx in idx_by:
            p[idx] = pred[idx][rng.permutation(len(idx))]
        if abs(agreement(p, obs) - 0.5) >= real - 1e-12:
            hits += 1
    return (hits + 1) / (n_perm + 1)


def signflip_p(pred: np.ndarray, obs: np.ndarray, blocks: np.ndarray,
               n_perm: int = N_PERM, seed: int = SEED) -> float:
    """Two-sided sign-flip permutation at BLOCK grain (block = unit's session
    for the session null, = underlying for the name-block null): all of a
    block's predictions flip together. Vectorized over draws."""
    rng = np.random.default_rng(seed)
    uniq, inv = np.unique(blocks, return_inverse=True)
    hit = np.where(pred == obs, 1.0, 0.0)
    real = abs(hit.mean() - 0.5)
    hits = 0
    for start in range(0, n_perm, 5_000):          # chunked: memory stays flat
        k = min(5_000, n_perm - start)
        flips = rng.integers(0, 2, size=(k, len(uniq))) * 2 - 1
        # a flipped prediction agrees iff it disagreed before
        agree = np.where(flips[:, inv] == 1, hit, 1.0 - hit).mean(axis=1)
        hits += int((np.abs(agree - 0.5) >= real - 1e-12).sum())
    return (hits + 1) / (n_perm + 1)


# =============================================================================
# power gate — simulate the FULL verdict conjunction (registered)
# =============================================================================

def _sim_units(rng, blocks: list[dict], keep_rate: float, p_agree: float):
    """One replicate: thin each unit's events by keep_rate (binomial), draw
    per-event agreement at p_agree, aggregate premium-weighted to unit grain.
    Returns (unit_hit ±1-as-bool, unit_sess, unit_name, unit_drift_tercile,
    multi_hit) or None if no units survive."""
    hits, sess, name = [], [], []
    for b in blocks:
        k = rng.binomial(b["n_events"], keep_rate)
        if k == 0:
            continue
        prem = rng.choice(np.asarray(b["premiums"]), size=k, replace=False) \
            if k < len(b["premiums"]) else np.asarray(b["premiums"])
        pred = rng.integers(0, 2, size=k) * 2 - 1
        agree_ev = rng.random(k) < p_agree
        obs = np.where(agree_ev, pred, -pred)
        u_pred = np.sign((prem * pred).sum())
        u_obs = np.sign((prem * obs).sum())
        if u_pred == 0 or u_obs == 0:
            continue
        hits.append(1.0 if u_pred == u_obs else 0.0)
        sess.append(b["session"])
        name.append(b["symbol"])
    if not hits:
        return None
    return (np.array(hits), np.array(sess), np.array(name),
            None, None)


def _fast_signflip_p(hit: np.ndarray, blocks: np.ndarray, rng,
                     n_perm: int = GATE_PERM) -> float:
    uniq, inv = np.unique(blocks, return_inverse=True)
    real = abs(hit.mean() - 0.5)
    flips = rng.integers(0, 2, size=(n_perm, len(uniq))) * 2 - 1
    agree = np.where(flips[:, inv] == 1, hit, 1.0 - hit).mean(axis=1)
    return (int((np.abs(agree - 0.5) >= real - 1e-12).sum()) + 1) / (n_perm + 1)


def conjunction_power(blocks: list[dict], keep_rate: float, a_cal: float,
                      a_o: float = A_O_DETECT, n_sim: int = GATE_SIM,
                      seed: int = SEED) -> float:
    """Power of the FULL registered conjunction: session sign-flip p < 0.01
    AND name-block p < 0.01 AND lowest-drift tercile same-sign p < 0.05 AND
    multi-print co-primary sign agreement. Drift terciles are assigned
    uniformly at random in-sim (registered arithmetic note: this leg alone
    costs ≈1.74× a single-null n). Multi-print co-primary simulated as an
    independent 1/3-sized cell required to agree in sign."""
    rng = np.random.default_rng(seed)
    p_agree = 0.5 + (2 * a_cal - 1) * (a_o - 0.5)
    passes = 0
    for _ in range(n_sim):
        sim = _sim_units(rng, blocks, keep_rate, p_agree)
        if sim is None:
            continue
        hit, sess, name, _, _ = sim
        if hit.mean() <= 0.5:
            continue
        if _fast_signflip_p(hit, sess, rng) >= 0.01:
            continue
        if _fast_signflip_p(hit, name, rng) >= 0.01:
            continue
        terc = rng.integers(0, 3, size=len(hit))
        low = hit[terc == 0]
        if len(low) == 0 or low.mean() <= 0.5:
            continue
        if _fast_signflip_p(low, sess[terc == 0], rng) >= 0.05:
            continue
        k_multi = max(1, len(hit) // 3)
        m_pred = rng.integers(0, 2, size=k_multi) * 2 - 1
        m_obs = np.where(rng.random(k_multi) < p_agree, m_pred, -m_pred)
        if (m_pred == m_obs).mean() <= 0.5:
            continue
        passes += 1
    return passes / n_sim


def n_required(blocks: list[dict], keep_rate: float, a_cal: float,
               seed: int = SEED) -> int:
    """Smallest unit count with conjunction power ≥ POWER_TARGET, by scaling
    the realized block distribution (resample blocks with replacement)."""
    rng = np.random.default_rng(seed + 1)
    for scale in (0.25, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0, 5.0, 8.0):
        k = max(4, int(len(blocks) * scale))
        # A1-7 stream-separation reservation: SEED+5 is the drift-tercile
        # tie-break substream and may never be produced as a sim seed here
        # (asserted, not assumed — k is data-dependent; pinned test).
        assert k != TIE_SUBSTREAM_OFF, \
            "seed+5 is reserved for the A1-5 tie-break substream"
        sample = [blocks[i] for i in rng.integers(0, len(blocks), size=k)]
        if conjunction_power(sample, keep_rate, a_cal, seed=seed + k) >= POWER_TARGET:
            return max(1, round(k * keep_rate))
    return 10 ** 9   # unreachable at any tested scale


# =============================================================================
# frame stage — the fixed loader + per-session event tables
# =============================================================================

def claimant_ambig(g_ts: np.ndarray, g_und: np.ndarray) -> np.ndarray:
    """AMBIGUOUS-CLAIMANT flag per cluster: ANY other tied cluster in the same
    underlying within CLAIMANT_R_NS of this one's t0. A1-3: the radius is a
    STANDALONE registered constant — it does NOT move with the match window
    (pinned test), and 200 ms drops a strict superset of any narrower rule."""
    n_g = len(g_ts)
    ambig = np.zeros(n_g, dtype=bool)
    if not n_g:
        return ambig
    ord_g = np.lexsort((g_ts, g_und))
    su, st_ = g_und[ord_g], g_ts[ord_g]
    same_prev = np.r_[False, (su[1:] == su[:-1])
                      & ((st_[1:] - st_[:-1]) <= CLAIMANT_R_NS)]
    ambig[ord_g] = same_prev | np.r_[same_prev[1:], False]
    return ambig


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_session_fixed(session: date):
    """The FIXED loader (build obligation): census-style load with the
    UNDEF_SIZE strip that `package_orientation_probe._load` lacks (measured
    24k rows/session), fence_and_classify strict/quotes, code join."""
    import databento as dbn

    defs = load_option_defs(session, defs_dir=DEFS_DIR)
    tpath = TCBBO_DIR / f"{session.isoformat()}.dbn.zst"
    if defs is None or not tpath.exists():
        return None
    arr = dbn.DBNStore.from_file(tpath).to_ndarray()
    arr = arr[arr["size"] != UNDEF_SIZE]
    pid = arr["instrument_id"].astype(np.int64)
    idx = np.clip(np.searchsorted(defs.ids, pid), 0, len(defs.ids) - 1)
    hit = defs.ids[idx] == pid
    arr, idx = arr[hit], idx[hit]

    fx = fence_and_classify(arr)
    ts = arr["ts_event"].astype(np.int64)
    iid = arr["instrument_id"].astype(np.int64)
    size = arr["size"].astype(np.int64)
    _, cond, src, jr, _ = multileg_flags(session, ts, iid, fx.price, size)
    if src != "codes" or jr is None:
        return None
    bid = arr["bid_px_00"].astype(np.int64)
    ask = arr["ask_px_00"].astype(np.int64)
    bidf = np.where((bid > 0) & (bid < np.iinfo(np.int64).max), bid / PX, np.nan)
    askf = np.where((ask > 0) & (ask < np.iinfo(np.int64).max), ask / PX, np.nan)
    return defs, fx, ts, iid, size, cond, bidf, askf, idx, float(jr)


def frame_session(session: date, rc, out_dir: Path = FRAME_DIR) -> dict | None:
    """Build one session's Step-1 + Step-2 event tables; save npz; return
    counts + file SHAs for the manifest. `out_dir` exists so `stage_verify`
    can rebuild into a scratch dir instead of overwriting the frozen frame
    in place (A1 build obligation 4)."""
    loaded = load_session_fixed(session)
    if loaded is None:
        return None
    defs, fx, ts, iid, size, cond, bidf, askf, didx, jr = loaded
    px_usd = fx.price / PX
    und = defs.und_code[didx]
    tied = np.isin(cond, list(TIED_CODES))
    if not tied.any():
        return None
    order = np.lexsort((ts[tied], und[tied]))

    def col(a):
        return a[tied][order]

    t_ts, t_iid, t_und = col(ts), col(iid), col(und)
    t_px, t_sz, t_prem = col(px_usd), col(size), col(fx.premium)
    t_bid, t_ask, t_good = col(bidf), col(askf), col(fx.good_q)
    t_strict = col(fx.strict)
    t_def = col(didx)

    new = np.empty(len(t_ts), dtype=bool)
    new[0] = True
    new[1:] = (t_und[1:] != t_und[:-1]) | ((t_ts[1:] - t_ts[:-1]) > W_NS)
    gid = np.cumsum(new) - 1
    n_g = int(gid[-1]) + 1

    # legs: (cluster, instrument); first row = min-ts print
    o2 = np.lexsort((t_ts, t_iid, gid))
    g2, i2 = gid[o2], t_iid[o2]
    first = np.empty(len(g2), dtype=bool)
    first[0] = True
    first[1:] = (g2[1:] != g2[:-1]) | (i2[1:] != i2[:-1])
    leg_id = np.cumsum(first) - 1
    leg_gid = g2[first]
    leg_sz = np.bincount(leg_id, weights=t_sz[o2].astype(np.float64))
    leg_vwap = np.bincount(leg_id, weights=(t_px * t_sz)[o2]) / leg_sz
    leg_prem = np.bincount(leg_id, weights=t_prem[o2])
    leg_nprints = np.bincount(leg_id)
    fidx = np.flatnonzero(first)
    leg_bid, leg_ask = t_bid[o2][fidx], t_ask[o2][fidx]
    leg_good = t_good[o2][fidx]
    leg_ts = t_ts[o2][fidx]
    leg_def = t_def[o2][fidx]
    leg_iid = i2[first]

    legs_per = np.bincount(leg_gid, minlength=n_g)
    c_prem = np.bincount(gid, weights=t_prem, minlength=n_g)
    g_first_row = np.r_[0, 1 + np.flatnonzero(gid[1:] != gid[:-1])]
    g_ts = t_ts[g_first_row]          # cluster t0 = min ts (und-major sort)
    g_und = t_und[g_first_row]

    ds = session.isoformat()
    roots = [str(s) for s in defs.roots]
    cls = np.array([rc.classify(r, ds) for r in roots])
    ident = np.array([str(rc.resolve(r, ds) or "") for r in roots])
    mid = session_midnight_ns(session)

    # option-side dedupe (AMBIGUOUS-CLAIMANT): ANY other tied cluster in the
    # same underlying within CLAIMANT_R_NS (= the registered 200 ms radius;
    # A1-3 decoupled it from the match window — value unchanged)
    ambig = claimant_ambig(g_ts, g_und)

    counts = {"clusters": n_g, "tied_prints": int(tied.sum())}

    # ---- Step 1: single-leg clusters, strict, unanimous side ----------------
    g1 = np.flatnonzero(legs_per == 1)
    l1 = np.searchsorted(leg_gid, g1)               # leg rows are gid-sorted
    n_str0 = np.bincount(gid, weights=(t_strict == 0).astype(float), minlength=n_g)
    n_str1 = np.bincount(gid, weights=(t_strict == 1).astype(float), minlength=n_g)
    n_all = np.bincount(gid, minlength=n_g)
    unani = np.full(n_g, -1, dtype=np.int8)
    unani[n_str0 == n_all] = 0
    unani[n_str1 == n_all] = 1
    s1_side = unani[g1]
    s1_keep = (s1_side >= 0)
    counts["s1_single_leg"] = int(len(g1))
    counts["s1_strict_unanimous"] = int(s1_keep.sum())
    s1_keep &= c_prem[g1] >= PREM_MIN_S1
    counts["s1_premium"] = int(s1_keep.sum())
    sec = et_second_of_day(g_ts[g1], mid)
    s1_keep &= in_trading_window(sec)
    counts["s1_time_window"] = int(s1_keep.sum())
    rootc = defs.und_code[leg_def[l1]]
    s1_keep &= (cls[rootc] == "equity") & (ident[rootc] != "")
    counts["s1_equity"] = int(s1_keep.sum())
    k1 = np.flatnonzero(s1_keep)
    s1 = {
        "ts_ns": g_ts[g1][k1].astype(np.int64),
        "sym": ident[rootc[k1]].astype("U16"),
        "is_put": defs.is_put[leg_def[l1[k1]]].astype(bool),
        "side": s1_side[k1].astype(np.int8),
        "prem": c_prem[g1][k1],
        "n_prints": leg_nprints[l1[k1]].astype(np.int32),
        "ambig": ambig[g1][k1],
    }
    counts["s1_final"] = len(k1)
    counts["s1_ambig"] = int(s1["ambig"].sum())

    # ---- Step 2: 2-leg equal-size, spread-edge cell -------------------------
    pair_g = np.flatnonzero(legs_per == 2)
    a_idx = np.flatnonzero(np.isin(leg_gid, pair_g))[0::2] \
        if len(pair_g) else np.empty(0, dtype=np.int64)
    b_idx = a_idx + 1
    eq = leg_sz[a_idx] == leg_sz[b_idx]
    A, B = a_idx[eq], b_idx[eq]
    counts["s2_pairs_equal_size"] = int(len(A))
    swap = leg_iid[A] > leg_iid[B]                  # A = lower instrument_id
    A, B = np.where(swap, B, A), np.where(swap, A, B)

    # census re-run through the FIXED loader (supersession deltas vs
    # tied_stock_census.py, which lacked the UNDEF_SIZE strip): the census's
    # own quantity — band-valid, in-band, 1D edge@0.10 — no other filters.
    # Edge distance is A/B-swap invariant, so the swap above is immaterial.
    c_lo = leg_bid[A] - leg_ask[B]
    c_hi = leg_ask[A] - leg_bid[B]
    c_w = c_hi - c_lo
    c_valid = np.isfinite(c_lo) & np.isfinite(c_hi) & (c_w > 0)
    with np.errstate(invalid="ignore", divide="ignore"):
        c_o = (leg_vwap[A] - leg_vwap[B] - c_lo) / c_w
    c_in = c_valid & (c_o >= 0.0) & (c_o <= 1.0)
    counts["s2_census_edge1d_fixed_loader"] = \
        int((c_in & (np.minimum(c_o, 1.0 - c_o) <= EDGE_C)).sum())

    keep = np.ones(len(A), dtype=bool)
    rootA = defs.und_code[leg_def[A]]
    keep &= (cls[rootA] == "equity") & (ident[rootA] != "")
    counts["s2_equity"] = int(keep.sum())
    lo = leg_bid[A] - leg_ask[B]
    hi = leg_ask[A] - leg_bid[B]
    width = hi - lo
    keep &= np.isfinite(lo) & np.isfinite(hi) & (width > 0)
    keep &= leg_good[A] & leg_good[B]
    counts["s2_band_quotes"] = int(keep.sum())
    with np.errstate(invalid="ignore", divide="ignore"):
        o_diff = (leg_vwap[A] - leg_vwap[B] - lo) / width
        o_sum = (leg_vwap[A] + leg_vwap[B] - (leg_bid[A] + leg_bid[B])) / width
    ed = np.minimum(o_diff, 1.0 - o_diff)
    es = np.minimum(o_sum, 1.0 - o_sum)
    in_band = (o_diff >= 0.0) & (o_diff <= 1.0)
    keep &= in_band & (ed <= EDGE_C) & (es > EDGE_C)
    counts["s2_spread_edge_cell"] = int(keep.sum())
    epoch_days = (session - date(1970, 1, 1)).days
    dteA = defs.exp_days[leg_def[A]] - epoch_days
    dteB = defs.exp_days[leg_def[B]] - epoch_days
    keep &= (dteA >= DTE_MIN) & (dteB >= DTE_MIN)
    counts["s2_dte"] = int(keep.sum())
    t0 = np.minimum(leg_ts[A], leg_ts[B])
    sec2 = et_second_of_day(t0, mid)
    keep &= in_trading_window(sec2)
    counts["s2_time_window"] = int(keep.sum())

    closes = bars_before_date(ds, db_path=DEFAULT_DB_PATH)
    sym2 = ident[rootA]
    prior_close = np.array([closes.get(str(s), (np.nan, ""))[0] for s in sym2],
                           dtype=np.float64)
    keep &= np.isfinite(prior_close)
    counts["s2_prior_close"] = int(keep.sum())

    net_d = np.full(len(A), np.nan)
    for i in np.flatnonzero(keep):
        deltas = []
        for leg in (A[i], B[i]):
            d = leg_def[leg]
            t_years = time_to_expiry_years(int(leg_ts[leg]), int(defs.exp_days[d]))
            if t_years <= 0.0:
                break
            qmid = (leg_bid[leg] + leg_ask[leg]) / 2.0
            fit = fit_delta(float(qmid), float(prior_close[i]),
                            float(defs.strike[d]), t_years,
                            bool(defs.is_put[d]))
            if not fit.ok:
                break
            deltas.append(fit.delta)
        if len(deltas) == 2:
            net_d[i] = deltas[0] - deltas[1]        # Δ_A − Δ_B, per contract
    keep &= np.isfinite(net_d)
    counts["s2_iv_fit"] = int(keep.sum())
    below_floor = keep & (np.abs(net_d) < NETD_MIN)
    keep &= np.abs(net_d) >= NETD_MIN
    counts["s2_netd_floor"] = int(keep.sum())
    counts["s2_below_netd_retained"] = int(below_floor.sum())

    k2 = np.flatnonzero(keep | below_floor)         # below-floor kept for sensitivity
    dir_a = np.where(o_diff[k2] >= 0.5, 1, -1)
    sig = (dir_a * np.sign(net_d[k2])).astype(np.int8)
    single = (leg_nprints[A[k2]] == 1) & (leg_nprints[B[k2]] == 1)
    pair_gid = leg_gid[A[k2]]
    s2 = {
        "ts_ns": t0[k2].astype(np.int64),
        "sym": sym2[k2].astype("U16"),
        "o_diff": o_diff[k2], "o_sum": o_sum[k2],
        "sigma": sig, "net_d": net_d[k2],
        "prem": (leg_prem[A] + leg_prem[B])[k2],    # GROSS both legs, disclosed
        "contracts": leg_sz[A[k2]],
        "single_print": single,
        "mixed": (defs.is_put[leg_def[A[k2]]] != defs.is_put[leg_def[B[k2]]]),
        "netd_floor_ok": keep[k2],
        "ambig": ambig[pair_gid],
    }
    prim = keep[k2] & single & ~ambig[pair_gid]
    counts["s2_primary_cell"] = int((keep[k2] & single).sum())
    counts["s2_primary_nonambig"] = int(prim.sum())
    counts["s2_multi_print"] = int((keep[k2] & ~single).sum())

    out_dir.mkdir(parents=True, exist_ok=True)
    save_npz(out_dir / f"{ds}.npz",
             **{f"s1_{k}": v for k, v in s1.items()},
             **{f"s2_{k}": v for k, v in s2.items()})

    labels = sorted(LABELS_DIR.glob(f"{ds}*"))
    shas = {"tcbbo": _sha256(TCBBO_DIR / f"{ds}.dbn.zst"),
            "defs": _sha256(DEFS_DIR / f"{ds}.dbn.zst"),
            "labels": {p.name: _sha256(p) for p in labels}}
    return {"session": ds, "join_rate": jr, "counts": counts, "sha": shas}


def stage_frame(limit: int | None = None) -> None:
    rc = load_classification(ROOT / "signals" / "options_flow"
                             / "root_classification.json")
    pool, stats = qualifying_sessions()
    if limit:
        pool = pool[:limit]
    rows = []
    for s in pool:
        row = frame_session(s, rc)
        if row is None:
            print(f"{s}: SKIPPED (load/provenance)")
            continue
        c = row["counts"]
        print(f"{s}: s1 {c['s1_final']:,} (ambig {c['s1_ambig']:,})  "
              f"s2 primary {c['s2_primary_cell']:,} "
              f"(non-ambig {c['s2_primary_nonambig']:,})  "
              f"multi {c['s2_multi_print']:,}")
        rows.append(row)
    manifest = {
        "prereg": "References/O_SIGN_VALIDATION_PREREG_2026-08-03.md",
        "session_stats": stats, "n_sessions": len(rows), "sessions": rows,
        "per_month": _per_month([r["session"] for r in rows]),
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    save_json(OUT_DIR / "frame_manifest.json", manifest)
    blob = json.dumps(manifest, sort_keys=True).encode()
    print(f"\nframe: {len(rows)} sessions  manifest sha256 "
          f"{hashlib.sha256(blob).hexdigest()[:16]}")
    tot = defaultdict(int)
    for r in rows:
        for k, v in r["counts"].items():
            tot[k] += v
    print("pooled:", "  ".join(f"{k} {v:,}" for k, v in tot.items()))


def stage_verify(limit: int | None = None) -> None:
    """Frame reproducibility from the disk manifest (pinned build obligation):
    re-hash each session's tcbbo/defs/labels inputs against the recorded SHAs,
    rebuild the frame, and assert the saved npz reproduces array-for-array."""
    manifest = json.loads((OUT_DIR / "frame_manifest.json").read_text())
    rc = load_classification(ROOT / "signals" / "options_flow"
                             / "root_classification.json")
    rows = manifest["sessions"][:limit] if limit else manifest["sessions"]
    for row in rows:
        ds = row["session"]
        session = date.fromisoformat(ds)
        assert _sha256(TCBBO_DIR / f"{ds}.dbn.zst") == row["sha"]["tcbbo"], ds
        assert _sha256(DEFS_DIR / f"{ds}.dbn.zst") == row["sha"]["defs"], ds
        for name, sha in row["sha"]["labels"].items():
            assert _sha256(LABELS_DIR / name) == sha, f"{ds} {name}"
        with np.load(FRAME_DIR / f"{ds}.npz", allow_pickle=False) as z:
            saved = {k: z[k].copy() for k in z.files}
        # A1 build obligation 4: rebuild into a SCRATCH dir — the frozen frame
        # npz is never rewritten by verification (the old in-place rebuild
        # could overwrite the frame BEFORE the reproducibility assert ran)
        scratch = OUT_DIR / "frame_verify_scratch"
        frame_session(session, rc, out_dir=scratch)
        with np.load(scratch / f"{ds}.npz", allow_pickle=False) as z:
            rebuilt = {k: z[k].copy() for k in z.files}
        (scratch / f"{ds}.npz").unlink()
        assert saved.keys() == rebuilt.keys(), ds
        for k in saved:
            eq_nan = np.issubdtype(saved[k].dtype, np.floating)
            assert np.array_equal(saved[k], rebuilt[k], equal_nan=eq_nan), \
                f"{ds}: {k} not reproducible"
        print(f"{ds}: inputs SHA-verified, frame reproduces "
              f"({len(saved)} arrays)")


def _per_month(sessions: list[str]) -> dict[str, int]:
    out: dict[str, int] = defaultdict(int)
    for s in sessions:
        out[s[:7]] += 1
    return dict(sorted(out.items()))


# =============================================================================
# fetch stages — 3.2 snap cache + alt-session placebo windows
# =============================================================================

def _client():
    import httpx
    cfg = alpaca_config()
    return (httpx.Client(headers={
        "APCA-API-KEY-ID": cfg["api_key_id"],
        "APCA-API-SECRET-KEY": cfg["api_secret_key"]}, timeout=30.0),
        cfg["data_base_url"])


def step1_sample_sessions(pool: list[date]) -> list[date]:
    return pool[::STEP1_EVERY]


def _stratified_cap(prem: np.ndarray, cap: int = STEP1_CAP) -> np.ndarray:
    """Indices of ≤cap events, equal thirds by premium tercile, evenly spaced
    by premium rank inside each tercile (deterministic)."""
    n = len(prem)
    if n <= cap:
        return np.arange(n)
    order = np.argsort(prem, kind="stable")
    thirds = np.array_split(order, 3)
    out = []
    for t in thirds:
        want = cap // 3
        if len(t) <= want:
            out.append(t)
        else:
            out.append(t[np.linspace(0, len(t) - 1, want).round().astype(int)])
    return np.sort(np.concatenate(out))


def _alt_session(session: date, pool: list[date]) -> date:
    i = pool.index(session)
    return pool[(i + 1) % len(pool)]


def _load_frame(session: date) -> dict[str, np.ndarray] | None:
    p = FRAME_DIR / f"{session.isoformat()}.npz"
    if not p.exists():
        return None
    with np.load(p, allow_pickle=False) as z:
        return {k: z[k] for k in z.files}


def _fetch_events(sessions: list[date], pool: list[date], events_of,
                  label: str, placebo: bool = True) -> None:
    """`events_of(session) -> (sym, ts) | None`. A1 build obligation 3: the
    event source is a callback so Step 1′ can feed the midpoint event TABLE
    through the exact same fetch path (real + both-offset placebo windows,
    fault ledger) that the frame-driven stages use."""
    client, url = _client()
    daily_path = OUT_DIR / "daily_volume.json"
    daily = json.loads(daily_path.read_text()) if daily_path.exists() else {}
    ledger = {"label": label, "events": 0, "fetched": 0, "fault": 0,
              "placebo_fault": 0, "faults": []}
    for session in sessions:
        evs = events_of(session)
        if evs is None:
            continue
        sym, ts = evs
        alt = _alt_session(session, pool)
        base, alt_base = session_midnight_ns(session), session_midnight_ns(alt)
        for s_, t_ in zip(sym, ts):
            ledger["events"] += 1
            try:
                fetch_window(OUT_DIR, client, url, str(s_), int(t_), daily)
                ledger["fetched"] += 1
            except (MissingDailyBar, FeedAssertionError, PageCapError,
                    RuntimeError) as exc:
                ledger["fault"] += 1
                ledger["faults"].append(f"{s_} {t_}: {exc}")
                continue
            if not placebo:
                continue
            t_alt = alt_base + (int(t_) - base)
            try:
                fetch_window(OUT_DIR, client, url, str(s_), t_alt, daily)
            except (MissingDailyBar, FeedAssertionError, PageCapError,
                    RuntimeError) as exc:
                ledger["placebo_fault"] += 1
                ledger["faults"].append(f"placebo {s_} {t_alt}: {exc}")
        save_json(daily_path, daily)
        print(f"{session}: fetched (cum {ledger['fetched']}/{ledger['events']}, "
              f"faults {ledger['fault']})")
    save_json(OUT_DIR / f"fetch_ledger_{label}.json", ledger)


def stage_fetch1(limit: int | None = None) -> None:
    pool, _ = qualifying_sessions()
    sample = step1_sample_sessions(pool)
    if limit:
        sample = sample[:limit]

    def events_of(session):
        fr = _load_frame(session)
        if fr is None:
            print(f"{session}: no frame — run stage frame first")
            return None
        keep = ~fr["s1_ambig"]
        idx = np.flatnonzero(keep)
        sel = idx[_stratified_cap(fr["s1_prem"][idx])]
        return fr["s1_sym"][sel], fr["s1_ts_ns"][sel]

    _fetch_events(sample, pool, events_of, "step1")


def _s2_events(session):
    fr = _load_frame(session)
    if fr is None:
        print(f"{session}: no frame — run stage frame first")
        return None
    keep = fr["s2_netd_floor_ok"] & ~fr["s2_ambig"]
    idx = np.flatnonzero(keep)
    if not len(idx):
        return None
    return fr["s2_sym"][idx], fr["s2_ts_ns"][idx]


def pilot_pick(pool: list[date]) -> list[date]:
    """3 sessions: the median session of each tercile of per-session PRIMARY
    non-ambiguous cell counts over the frozen frame (ties → earlier date)."""
    counts = []
    for s in pool:
        fr = _load_frame(s)
        if fr is None:
            continue
        n = int((fr["s2_netd_floor_ok"] & fr["s2_single_print"]
                 & ~fr["s2_ambig"]).sum())
        counts.append((s, n))
    counts.sort(key=lambda t: (t[1], t[0]))
    thirds = np.array_split(np.arange(len(counts)), 3)
    return sorted(counts[t[(len(t) - 1) // 2]][0] for t in thirds if len(t))


def stage_pilot_fetch() -> None:
    # A1: the pilot IS a Step-2 fetch — the inter-leg span precondition runs
    # BEFORE it (consumes no fetches, no agreement numbers)
    assert_frame_shas()
    assert_step2_window_ok()
    pool, _ = qualifying_sessions()
    picks = pilot_pick(pool)
    save_json(OUT_DIR / "pilot_sessions.json",
              {"sessions": [s.isoformat() for s in picks]})
    print("pilot sessions:", [s.isoformat() for s in picks])
    _fetch_events(picks, pool, _s2_events, "pilot")


def stage_fetch2(limit: int | None = None) -> None:
    assert_frame_shas()
    assert_step2_window_ok()
    pool, _ = qualifying_sessions()
    sessions = pool[:limit] if limit else pool
    _fetch_events(sessions, pool, _s2_events, "step2")


# =============================================================================
# analysis helpers shared by step1/pilot/step2
# =============================================================================

def _load_window(symbol: str, ts_ns: int) -> tuple[dict, dict] | None:
    t_path = _snap_path(OUT_DIR, symbol, ts_ns, "trades")
    q_path = _snap_path(OUT_DIR, symbol, ts_ns, "quotes")
    if not (t_path.exists() and q_path.exists()):
        return None
    with np.load(t_path, allow_pickle=False) as z:
        trades = {k: z[k] for k in z.files}
    with np.load(q_path, allow_pickle=False) as z:
        quotes = {k: z[k] for k in z.files}
    return trades, quotes


def _match_bundle(trades: dict, quotes: dict, t_: int, alt_win, t_alt: int,
                  arm: str) -> dict:
    """One event's full match set. `arm='registered'` = the ±100 ms design;
    `arm='a1'` = Amendment 1: signed [−1, +3] ms primary window through the
    real AND placebo matchers, symmetric ±10/±100 ms sensitivity legs, and
    lag legs at ±20 ms (tripwire, A1-8) + ±1 s (reported-only)."""
    if arm == "a1":
        prim = lambda tr, qt, t0, lag=0: match_event_signed(   # noqa: E731
            tr, qt, t0, W_LO_NS, W_HI_NS, lag_ns=lag)
        sens_w = A1_SENS_W_NS
        lags = (-LAG_TRIPWIRE_NS, LAG_TRIPWIRE_NS, -LAG_SENS_NS, LAG_SENS_NS)
    else:
        prim = lambda tr, qt, t0, lag=0: match_event(          # noqa: E731
            tr, qt, t0, PRIMARY_W_NS, lag_ns=lag)
        sens_w = SENS_W_NS
        lags = (-LAG_SENS_NS, LAG_SENS_NS)
    out = {
        "match": prim(trades, quotes, t_),
        "match_sens": {str(w): match_event(trades, quotes, t_, w)
                       for w in sens_w},
        "match_lag": {str(lag): prim(trades, quotes, t_, lag=lag)
                      for lag in lags},
        "placebo_t": prim(trades, quotes, t_ + PLACEBO_OFFSET_NS),
        "placebo_t_sens": {str(w): match_event(trades, quotes,
                                               t_ + PLACEBO_OFFSET_NS, w)
                           for w in sens_w},
    }
    if alt_win:
        out["placebo_s"] = prim(*alt_win, t_alt)
        out["placebo_s_sens"] = {str(w): match_event(*alt_win, t_alt, w)
                                 for w in sens_w}
    else:
        out["placebo_s"] = None
        out["placebo_s_sens"] = {}
    return out


def analyze_events(sessions: list[date], pool: list[date], events_of,
                   pred_of, arm: str = "registered") -> dict:
    """Run MF-1 + placebos over fetched events. Returns per-event record lists
    (predictions, observations, funnel, Δt, drift, placebo matches)."""
    rec: dict[str, list] = defaultdict(list)
    for session in sessions:
        fr = _load_frame(session)
        if fr is None:
            continue
        evs = events_of(fr)
        if evs is None:
            continue
        sel, sym, ts = evs
        alt = _alt_session(session, pool)
        base, alt_base = session_midnight_ns(session), session_midnight_ns(alt)
        for j, s_, t_ in zip(sel, sym, ts):
            win = _load_window(str(s_), int(t_))
            if win is None:
                rec["missing"].append(1)
                continue
            trades, quotes = win
            t_alt = alt_base + (int(t_) - base)
            win_alt = _load_window(str(s_), t_alt)
            b = _match_bundle(trades, quotes, int(t_), win_alt, t_alt, arm)
            m, m_sens = b["match"], b["match_sens"]
            m_lag = b["match_lag"]
            plc_t, plc_t_sens = b["placebo_t"], b["placebo_t_sens"]
            plc_s, plc_s_sens = b["placebo_s"], b["placebo_s_sens"]
            rec["session"].append(session.isoformat())
            rec["sym"].append(str(s_))
            rec["idx"].append(int(j))
            rec["pred"].append(pred_of(fr, int(j)))
            rec["match"].append(m)
            rec["match_sens"].append({str(k): v for k, v in m_sens.items()})
            rec["match_lag"].append({str(k): v for k, v in m_lag.items()})
            rec["placebo_t"].append(plc_t)
            rec["placebo_t_sens"].append({str(k): v for k, v in plc_t_sens.items()})
            rec["placebo_s"].append(plc_s)
            rec["placebo_s_sens"].append({str(k): v for k, v in plc_s_sens.items()})
            rec["drift"].append(drift_pre(quotes, int(t_)))
    return rec


def _rates(rec: dict, key: str = "match") -> dict:
    ms = rec[key]
    n = len(ms)
    matched = sum(1 for m in ms if m["matched"])
    signable = sum(1 for m in ms if m["signable"])
    return {"events": n, "matched": matched, "signable": signable,
            "match_rate": matched / n if n else float("nan"),
            "signable_rate": signable / matched if matched else float("nan")}


def _contamination(rec: dict) -> dict:
    """c = matched-rate(placebo) / matched-rate(real), per window, both
    placebo offsets (T+60 s and same-clock alt session)."""
    out = {}
    real_primary = _rates(rec)["match_rate"]
    for lab, key in (("t60", "placebo_t"), ("alt", "placebo_s")):
        ms = [m for m in rec[key] if m is not None]
        r = sum(1 for m in ms if m["matched"]) / len(ms) if ms else float("nan")
        out[f"{lab}_primary"] = {"rate": r,
                                 "c": r / real_primary if real_primary else float("nan")}
    for w in SENS_W_NS:
        real = sum(1 for m in rec["match_sens"] if m[str(w)]["matched"]) \
            / len(rec["match_sens"]) if rec["match_sens"] else float("nan")
        for lab, key in (("t60", "placebo_t_sens"), ("alt", "placebo_s_sens")):
            ms = [m for m in rec[key] if m]
            r = sum(1 for m in ms if m[str(w)]["matched"]) / len(ms) \
                if ms else float("nan")
            out[f"{lab}_{w}"] = {"rate": r,
                                 "c": r / real if real else float("nan")}
    return out


# =============================================================================
# step1 stage
# =============================================================================

def _terciles(x: np.ndarray) -> np.ndarray:
    """0/1/2 tercile labels (NaN → −1)."""
    out = np.full(len(x), -1, dtype=np.int8)
    ok = np.isfinite(x)
    if ok.sum() >= 3:
        q = np.percentile(x[ok], [100 / 3, 200 / 3])
        out[ok] = np.digitize(x[ok], q).astype(np.int8)
    elif ok.any():
        out[ok] = 0
    return out


def stage_step1() -> None:
    pool, _ = qualifying_sessions()
    sample = step1_sample_sessions(pool)

    def events_of(fr):
        keep = ~fr["s1_ambig"]
        idx = np.flatnonzero(keep)
        sel = idx[_stratified_cap(fr["s1_prem"][idx])]
        return sel, fr["s1_sym"][sel], fr["s1_ts_ns"][sel]

    def pred_of(fr, j):
        return taker_stock_side(int(fr["s1_side"][j]), bool(fr["s1_is_put"][j]))

    rec = analyze_events(sample, pool, events_of, pred_of)
    n_events = len(rec["pred"])
    n_missing = len(rec.get("missing", []))
    fault_frac = n_missing / (n_events + n_missing) if n_events + n_missing else 0.0

    rates = _rates(rec)
    contam = _contamination(rec)

    # a_cal on matched+signable events, overall + premium terciles
    ok = np.array([m["signable"] for m in rec["match"]])
    pred = np.array(rec["pred"])[ok]
    obs = np.array([m["obs_side"] for m in rec["match"]])[ok]
    sess = np.array(rec["session"])[ok]
    prem = np.array([_frame_val(rec, i, "s1_prem")
                     for i in range(n_events)])[ok]
    drift = np.array(rec["drift"])[ok]
    dts = np.array([m["dt_ns"] if m["dt_ns"] is not None else np.nan
                    for m in rec["match"]])[ok].astype(float)

    a_cal = agreement(pred, obs)
    ci = session_bootstrap_ci(pred, obs, sess) if len(pred) else (np.nan, np.nan)
    p = perm_p_within_session(pred, obs, sess) if len(pred) else float("nan")
    kap = kappa(pred, obs)

    by_terc = {}
    terc = _terciles(prem)
    for t in (0, 1, 2):
        m = terc == t
        by_terc[str(t)] = {"n": int(m.sum()), "a_cal": agreement(pred[m], obs[m])}
    drift_terc = _terciles(drift)
    by_drift = {str(t): {"n": int((drift_terc == t).sum()),
                         "a_cal": agreement(pred[drift_terc == t],
                                            obs[drift_terc == t])}
                for t in (0, 1, 2)}
    dt_terc = _terciles(np.abs(dts))
    by_dt = {str(t): {"n": int((dt_terc == t).sum()),
                      "a_cal": agreement(pred[dt_terc == t], obs[dt_terc == t])}
             for t in (0, 1, 2)}
    by_side = {}
    for lab, m in (("pred_buy", np.array(rec["pred"]) == 1),
                   ("pred_sell", np.array(rec["pred"]) == -1)):
        sub = [rec["match"][i] for i in np.flatnonzero(m)]
        by_side[lab] = {"events": len(sub),
                        "match_rate": (sum(x["matched"] for x in sub) / len(sub))
                        if sub else float("nan")}
    lag_acal = {}
    for lag in (-LAG_SENS_NS, LAG_SENS_NS):
        lok = np.array([m[str(lag)]["signable"] for m in rec["match_lag"]])
        lp = np.array(rec["pred"])[lok]
        lo_ = np.array([m[str(lag)]["obs_side"] for m in rec["match_lag"]])[lok]
        lag_acal[str(lag)] = agreement(lp, lo_)

    # mechanics gates (claimant drops already excluded pre-fetch, disclosed)
    gates = {
        "match_ge_20": rates["match_rate"] >= MATCH_GATE,
        "signable_ge_50": rates["signable_rate"] >= SIGNABLE_GATE,
        "placebo_c_lt_01": all(
            contam[k]["c"] < C_MAX_PRIMARY for k in ("t60_primary", "alt_primary")
            if np.isfinite(contam[k]["c"])),
        "stop_mechanics": fault_frac > STOP_MECHANICS_FRAC,
    }

    # θ*: self-consistent threshold from the Step-2 gate arithmetic, using
    # Step-1's own realized match×signable as the projection rates (the pilot
    # refines this; disclosed)
    blocks = _frame_unit_blocks(pool)
    keep_rate = rates["match_rate"] * rates["signable_rate"] \
        if np.isfinite(rates["match_rate"] * rates["signable_rate"]) else 0.0
    theta = _theta_star(blocks, keep_rate)

    verdict = _step1_verdict(gates, a_cal, ci, p, theta)
    art = {
        "prereg": "References/O_SIGN_VALIDATION_PREREG_2026-08-03.md",
        "sessions": [s.isoformat() for s in sample],
        "events": n_events, "fetch_fault_frac": fault_frac,
        "rates": rates, "contamination": contam, "gates": gates,
        "a_cal": a_cal, "a_cal_ci_session_bootstrap": ci,
        "perm_p_two_sided": p, "kappa": kap,
        "pred_marginal": float((pred == 1).mean()) if len(pred) else None,
        "obs_marginal": float((obs == 1).mean()) if len(pred) else None,
        "by_premium_tercile": by_terc, "by_drift_tercile": by_drift,
        "by_dt_tercile": by_dt, "match_by_pred_side": by_side,
        "lagged_quote_a_cal": lag_acal,
        "theta_star": theta, "verdict": verdict,
        "note_upper_bound": "a_p≈1 is a NAMED ASSUMPTION: a_cal is a LOWER "
                            "bound on a_s; â_o derived from it is an UPPER "
                            "bound on a_o.",
    }
    save_json(OUT_DIR / "step1.json", art)
    np.savez(OUT_DIR / "step1_events.npz", pred=pred, obs=obs, sess=sess,
             prem=prem, drift=drift, dt=dts)
    print(json.dumps({k: art[k] for k in
                      ("events", "rates", "a_cal", "a_cal_ci_session_bootstrap",
                       "perm_p_two_sided", "kappa", "theta_star", "verdict")},
                     indent=1, default=str))


_FRAME_CACHE: dict[str, dict] = {}


def _frame_val(rec: dict, i: int, key: str) -> float:
    ds = rec["session"][i]
    if ds not in _FRAME_CACHE:
        _FRAME_CACHE[ds] = _load_frame(date.fromisoformat(ds))
    return float(_FRAME_CACHE[ds][key][rec["idx"][i]])


def _theta_star(blocks: list[dict], keep_rate: float) -> dict:
    proj = round(len(blocks) * keep_rate)
    grid = [0.55, 0.60, 0.65, 0.70, 0.75, 0.80]
    theta = None
    for a in grid:
        if keep_rate <= 0:
            break
        if n_required(blocks, keep_rate, a) <= proj:
            theta = a
            break
    val = max(theta, THETA_BACKSTOP) if theta is not None else THETA_BACKSTOP
    return {"theta": theta, "theta_star": val, "projected_units": proj,
            "keep_rate": keep_rate,
            "basis": "Step-1 realized match×signable (pilot refines)"}


def _step1_verdict(gates: dict, a_cal: float, ci: tuple, p: float,
                   theta: dict) -> str:
    if gates["stop_mechanics"]:
        return "STOP-MECHANICS"
    if not (gates["match_ge_20"] and gates["signable_ge_50"]
            and gates["placebo_c_lt_01"]):
        return "INSTRUMENT-DEAD-MECHANICS"
    lb, ub = ci
    if not np.isfinite(a_cal) or not np.isfinite(lb):
        return "INSTRUMENT-DEAD-MECHANICS"
    if lb <= 0.5 <= ub:
        return "INSTRUMENT-DEAD-NO-DIRECTION"
    if ub < 0.5:
        return "INSTRUMENT-DEAD-INVERTED-TERMINAL"
    ts = theta["theta_star"]
    if p < 0.01 and lb >= ts:
        return "LIVE"
    return "INSTRUMENT-WEAK"


# =============================================================================
# Step-1 required diagnostic — a_cal on strict vs NON-strict prints
# =============================================================================

def _nonstrict_events(session: date, rc) -> dict | None:
    """Single-leg tied events signable by the MIDPOINT side rule but NOT
    strict-unanimous — the comparison arm of the registered a_p≈1 diagnostic.
    Same filters/cap/dedupe as Step 1 otherwise."""
    loaded = load_session_fixed(session)
    if loaded is None:
        return None
    defs, fx, ts, iid, size, cond, bidf, askf, didx, jr = loaded
    und = defs.und_code[didx]
    tied = np.isin(cond, list(TIED_CODES))
    if not tied.any():
        return None
    order = np.lexsort((ts[tied], und[tied]))

    def col(a):
        return a[tied][order]

    t_ts, t_iid, t_und = col(ts), col(iid), col(und)
    t_prem = col(fx.premium)
    t_side, t_strict = col(fx.side), col(fx.strict)
    t_def = col(didx)
    new = np.empty(len(t_ts), dtype=bool)
    new[0] = True
    new[1:] = (t_und[1:] != t_und[:-1]) | ((t_ts[1:] - t_ts[:-1]) > W_NS)
    gid = np.cumsum(new) - 1
    n_g = int(gid[-1]) + 1
    o2 = np.lexsort((t_ts, t_iid, gid))
    g2, i2 = gid[o2], t_iid[o2]
    first = np.empty(len(g2), dtype=bool)
    first[0] = True
    first[1:] = (g2[1:] != g2[:-1]) | (i2[1:] != i2[:-1])
    leg_gid = g2[first]
    fidx = np.flatnonzero(first)
    leg_def = t_def[o2][fidx]
    legs_per = np.bincount(leg_gid, minlength=n_g)
    c_prem = np.bincount(gid, weights=t_prem, minlength=n_g)
    g_first_row = np.r_[0, 1 + np.flatnonzero(gid[1:] != gid[:-1])]
    g_ts = t_ts[g_first_row]
    g_und = t_und[g_first_row]

    ambig = claimant_ambig(g_ts, g_und)

    n_all = np.bincount(gid, minlength=n_g)
    n_s0 = np.bincount(gid, weights=(t_side == 0).astype(float), minlength=n_g)
    n_s1 = np.bincount(gid, weights=(t_side == 1).astype(float), minlength=n_g)
    mid_side = np.full(n_g, -1, dtype=np.int8)
    mid_side[n_s0 == n_all] = 0
    mid_side[n_s1 == n_all] = 1
    n_t0 = np.bincount(gid, weights=(t_strict == 0).astype(float), minlength=n_g)
    n_t1 = np.bincount(gid, weights=(t_strict == 1).astype(float), minlength=n_g)
    strict_unani = (n_t0 == n_all) | (n_t1 == n_all)

    g1 = np.flatnonzero(legs_per == 1)
    l1 = np.searchsorted(leg_gid, g1)
    ds = session.isoformat()
    roots = [str(s) for s in defs.roots]
    cls = np.array([rc.classify(r, ds) for r in roots])
    ident = np.array([str(rc.resolve(r, ds) or "") for r in roots])
    rootc = defs.und_code[leg_def[l1]]
    mid = session_midnight_ns(session)
    keep = ((mid_side[g1] >= 0) & ~strict_unani[g1]
            & (c_prem[g1] >= PREM_MIN_S1)
            & in_trading_window(et_second_of_day(g_ts[g1], mid))
            & (cls[rootc] == "equity") & (ident[rootc] != "")
            & ~ambig[g1])
    k = np.flatnonzero(keep)
    if not len(k):
        return None
    sel = k[_stratified_cap(c_prem[g1][k])]
    return {"ts_ns": g_ts[g1][sel].astype(np.int64),
            "sym": ident[rootc[sel]].astype("U16"),
            "side": mid_side[g1][sel].astype(np.int8),
            "is_put": defs.is_put[leg_def[l1[sel]]].astype(bool)}


def stage_diag_fetch() -> None:
    pool, _ = qualifying_sessions()
    sample = step1_sample_sessions(pool)
    rc = load_classification(ROOT / "signals" / "options_flow"
                             / "root_classification.json")
    cache: dict[date, dict | None] = {}

    def events_of_factory(session):
        if session not in cache:
            cache[session] = _nonstrict_events(session, rc)
        return cache[session]

    client, url = _client()
    daily_path = OUT_DIR / "daily_volume.json"
    daily = json.loads(daily_path.read_text()) if daily_path.exists() else {}
    ledger = {"label": "diag_nonstrict", "events": 0, "fetched": 0, "fault": 0}
    rows = []
    for session in sample:
        ev = events_of_factory(session)
        if ev is None:
            continue
        for i in range(len(ev["ts_ns"])):
            ledger["events"] += 1
            try:
                fetch_window(OUT_DIR, client, url, str(ev["sym"][i]),
                             int(ev["ts_ns"][i]), daily)
                ledger["fetched"] += 1
            except (MissingDailyBar, FeedAssertionError, PageCapError,
                    RuntimeError):
                ledger["fault"] += 1
            rows.append({"session": session.isoformat(),
                         "sym": str(ev["sym"][i]), "ts_ns": int(ev["ts_ns"][i]),
                         "side": int(ev["side"][i]),
                         "is_put": bool(ev["is_put"][i])})
        save_json(daily_path, daily)
        print(f"{session}: diag events cum {ledger['events']} "
              f"(faults {ledger['fault']})")
    save_json(OUT_DIR / "diag_events.json", rows)
    save_json(OUT_DIR / "fetch_ledger_diag.json", ledger)


def stage_diag() -> None:
    """Publish a_cal strict vs non-strict side by side (required diagnostic:
    a null difference is evidence AGAINST the a_p≈1 named assumption)."""
    rows = json.loads((OUT_DIR / "diag_events.json").read_text())
    pred, obs, sess = [], [], []
    for r in rows:
        win = _load_window(r["sym"], r["ts_ns"])
        if win is None:
            continue
        trades, quotes = win
        m = match_event(trades, quotes, r["ts_ns"], PRIMARY_W_NS)
        if not m["signable"]:
            continue
        pred.append(taker_stock_side(r["side"], r["is_put"]))
        obs.append(m["obs_side"])
        sess.append(r["session"])
    pred, obs, sess = map(np.array, (pred, obs, sess))
    step1 = json.loads((OUT_DIR / "step1.json").read_text())
    art = {
        "nonstrict": {"n_signable": int(len(pred)),
                      "a_cal": agreement(pred, obs),
                      "ci_session_bootstrap": session_bootstrap_ci(pred, obs, sess)
                      if len(pred) else None,
                      "kappa": kappa(pred, obs)},
        "strict": {"n_signable": step1["rates"]["signable"],
                   "a_cal": step1["a_cal"],
                   "ci_session_bootstrap": step1["a_cal_ci_session_bootstrap"],
                   "kappa": step1["kappa"]},
        "reading": "null strict-vs-nonstrict difference = evidence against "
                   "a_p≈1; strict >> nonstrict = a_p≈1 credible and the "
                   "stock-leg instrument itself carries no direction",
    }
    save_json(OUT_DIR / "step1_diag.json", art)
    print(json.dumps(art, indent=1, default=str))


# =============================================================================
# frame unit blocks (session, underlying) for the gate
# =============================================================================

def _frame_unit_blocks(pool: list[date], join_floor: float | None = None) -> list[dict]:
    """(session, underlying) blocks over the frozen frame's PRIMARY
    non-ambiguous cell: event counts + premiums per block."""
    blocks = []
    for s in pool:
        fr = _load_frame(s)
        if fr is None:
            continue
        keep = fr["s2_netd_floor_ok"] & fr["s2_single_print"] & ~fr["s2_ambig"]
        idx = np.flatnonzero(keep)
        if not len(idx):
            continue
        syms = fr["s2_sym"][idx]
        for u in np.unique(syms):
            m = idx[syms == u]
            blocks.append({"session": s.isoformat(), "symbol": str(u),
                           "n_events": len(m),
                           "premiums": fr["s2_prem"][m].tolist()})
    return blocks


# =============================================================================
# pilot + gate stages
# =============================================================================

def stage_pilot() -> None:
    """Step-2 pilot, AMENDED (A1-6): signed [−1, +3] ms matcher; additionally
    persists the label-blind composition target for the A1-6(b) reweighted LB
    — premium tercile (Step-1′ pooled edges) × quote-position class over the
    pilot's matched-and-signable primary-cell events, with the 3-way class
    breakdown and the all-primary-cell composition beside it. Leg quote
    position comes off the tcbbo and crosses nothing with an observed side
    (allowlist extension registered in A1-6(b))."""
    assert_frame_shas()
    assert_step2_window_ok()
    pool, _ = qualifying_sessions()
    picks = [date.fromisoformat(s) for s in
             json.loads((OUT_DIR / "pilot_sessions.json").read_text())["sessions"]]

    def events_of(fr):
        keep = fr["s2_netd_floor_ok"] & fr["s2_single_print"] & ~fr["s2_ambig"]
        idx = np.flatnonzero(keep)
        if not len(idx):
            return None
        return idx, fr["s2_sym"][idx], fr["s2_ts_ns"][idx]

    def pred_of(fr, j):
        return -int(fr["s2_sigma"][j])

    rec = analyze_events(picks, pool, events_of, pred_of, arm="a1")

    # LABEL-BLIND FILE RULE: per-event observed sides go ONLY to the sealed
    # file; the pilot artifact carries funnel/rates/Δt/units — nothing that
    # crosses observed with predicted.
    sealed = [{"session": s, "sym": y, "idx": i, "obs": m["obs_side"]}
              for s, y, i, m in zip(rec["session"], rec["sym"], rec["idx"],
                                    rec["match"])]
    sealed_path = OUT_DIR / "pilot_observed_sealed.json"
    save_json(sealed_path, sealed)
    sha = _sha256(sealed_path)

    units = defaultdict(int)
    for s, y, m in zip(rec["session"], rec["sym"], rec["match"]):
        if m["signable"]:
            units[(s, y)] += 1
    dts = [m["dt_ns"] for m in rec["match"] if m["dt_ns"] is not None]
    by_side = {}
    for lab, v in (("pred_buy", 1), ("pred_sell", -1)):
        sub = [m for p_, m in zip(rec["pred"], rec["match"]) if p_ == v]
        by_side[lab] = {"events": len(sub),
                        "match_rate": sum(x["matched"] for x in sub) / len(sub)
                        if sub else float("nan")}

    # --- A1-6(b) composition target (label-blind: pre-trade attributes only)
    step1p = json.loads((OUT_DIR / "step1p.json").read_text())
    edges = np.array(step1p["premium_tercile_edges"], dtype=float)
    meta = json.loads((OUT_DIR / "s2_leg_meta.json").read_text())["sessions"]
    n_ev = len(rec["pred"])

    def _cells(indices):
        cells, three = [], {"0": 0, "1": 0, "2": 0}
        for i in indices:
            c3 = int(meta[rec["session"][i]]["cls3"][rec["idx"][i]])
            three[str(c3)] += 1
            cls_bin = 0 if c3 == 0 else 1      # both-strict ⇒ strict-unanimous
            t = int(np.digitize([_frame_val(rec, i, "s2_prem")], edges)[0])
            cells.append(t * 2 + cls_bin)
        return cells, three

    ms_idx = [i for i in range(n_ev) if rec["match"][i]["signable"]]
    ms_cells, ms_three = _cells(ms_idx)
    all_cells, all_three = _cells(range(n_ev))
    prem_ms = [_frame_val(rec, i, "s2_prem") for i in ms_idx]

    def _shares(cells):
        n = len(cells)
        return {str(c): cells.count(c) / n for c in sorted(set(cells))} \
            if n else {}

    composition = {
        "population": "matched-and-signable primary-cell events (the gate's "
                      "projection population; A1-6(b))",
        "cell_alphabet": "cell = premium_tercile*2 + class "
                         "(class 0 = strict-unanimous [both-strict], "
                         "1 = non-strict); terciles by Step-1' pooled edges",
        "premium_tercile_edges_step1p": edges.tolist(),
        "target_cells": _shares(ms_cells),
        "n_matched_signable": len(ms_idx),
        "three_way_class_breakdown": ms_three,
        "all_primary_cell_shares": _shares(all_cells),
        "all_primary_cell_three_way": all_three,
        "premium_range_matched_signable": ([float(min(prem_ms)),
                                            float(max(prem_ms))]
                                           if prem_ms else None),
    }

    contam = _contamination_a1(rec)
    art = {
        "amendment": "References/O_SIGN_VALIDATION_AMENDMENT1_2026-08-05.md",
        "sessions": [s.isoformat() for s in picks],
        "rates": _rates(rec), "contamination": contam,
        "placebo_law": placebo_gate_a1(contam),
        "units": {"n": len(units),
                  "events_per_unit": sorted(units.values(), reverse=True)},
        "dt_ns_percentiles": {q: float(np.percentile(dts, q))
                              for q in (10, 50, 90)} if dts else {},
        "match_by_pred_side": by_side,
        "composition": composition,
        "sealed_observed_sha256": sha,
        "label_blind": "per-event observed sides in pilot_observed_sealed.json"
                       " ONLY; no gate code reads it",
    }
    save_json(OUT_DIR / "pilot.json", art)
    print(json.dumps({k: art[k] for k in ("sessions", "rates", "units",
                                          "composition")},
                     indent=1, default=str))


def stage_gate() -> None:
    """Pre-run power gate, AMENDED (A1-6): the a_cal input is Step 1′'s
    composition-reweighted session-cluster bootstrap-t LB — the SINGLE lower
    bound of record — computed here (it needs the pilot's composition), the θ
    grid runs to 0.95 with the N=175 ladder recompute and the row-(viii)
    routing, the containment check extends to BOTH axes, and Step 1′'s
    truth-table rows (iv)–(viii) FINALIZE here. The pre-run sim keeps the
    uniform-1/3 tercile draw (limitation registered in A1-5; Gate 2 resamples
    realized structure)."""
    assert_frame_shas()
    assert_step2_window_ok()
    pilot = json.loads((OUT_DIR / "pilot.json").read_text())
    step1p = json.loads((OUT_DIR / "step1p.json").read_text())
    pool, _ = qualifying_sessions()
    blocks = _frame_unit_blocks(pool)

    keep = pilot["rates"]["match_rate"] * pilot["rates"]["signable_rate"]
    if not np.isfinite(keep):
        keep = 0.0

    # --- A1-6(b): the composition-reweighted LB of record --------------------
    z = np.load(OUT_DIR / "step1p_events_analyzed.npz", allow_pickle=False)
    sig = z["signable"].astype(bool)
    hit = (z["pred"] == z["obs"]).astype(float)[sig]
    sess_sig = z["sess"][sig].astype(str)
    cell_sig = z["cell"][sig].astype(np.int64)
    target = {int(k): float(v)
              for k, v in pilot["composition"]["target_cells"].items()}
    rw_lb, rw_ub, rw_meta = bootstrap_t_ci(hit, sess_sig, cells=cell_sig,
                                           target=target)
    mix_lb, mix_ub = step1p["a_cal_mixture_ci_bootstrap_t"]

    # --- two-axis containment (coded: outside ⇒ the gate may not proceed) ---
    s1_lo, s1_hi = (float(z["prem"][sig].min()), float(z["prem"][sig].max())) \
        if sig.any() else (float("nan"), float("nan"))
    s2_prems = np.concatenate([np.asarray(b["premiums"]) for b in blocks])
    range_ok = bool((s2_prems.min() >= s1_lo) & (s2_prems.max() <= s1_hi))
    calibrated_cells = {int(k.split("terc")[1][0]) * 2
                        + (0 if k.endswith("_strict") else 1): v["n"]
                        for k, v in step1p["by_cell"].items()}
    class_ok = all(calibrated_cells.get(c, 0) > 0
                   for c, share in target.items() if share > 0)

    # --- A1-6(c): θ grid + ladder + unachievable routing ---------------------
    theta = _theta_star_a1(blocks, keep, seed=SEED_A1)
    theta["theta_basis"] = "pilot-refined"

    # --- finalize Step 1′ rows (iv)–(viii) on the LB of record ---------------
    verdict_final = _step1p_verdict(
        step1p["gates"], (mix_lb, mix_ub), step1p["perm_p_two_sided"],
        lb=rw_lb, theta_star=theta["theta_star"],
        theta_unachievable=theta["theta_unachievable"],
        skew_tripped=bool(step1p["skew_tripped_sessions"]))
    divergence = bool(np.isfinite(mix_lb) and np.isfinite(rw_lb)
                      and ((mix_lb - theta["theta_star"])
                           * (rw_lb - theta["theta_star"]) < 0))
    step1p.update({
        "lb_of_record_composition_reweighted": rw_lb,
        "reweighted_ci": [rw_lb, rw_ub], "reweighted_meta": rw_meta,
        "mixture_vs_reweighted_lb_divergence": divergence,
        "theta": theta, "verdict_final": verdict_final,
    })
    save_json(OUT_DIR / "step1p.json", step1p)

    proj_units = round(len(blocks) * keep)
    a_in = rw_lb if np.isfinite(rw_lb) else 0.0
    nreq = n_required(blocks, keep, a_in, seed=SEED_A1)
    power = conjunction_power(blocks, keep, a_in, seed=SEED_A1)
    stress = conjunction_power(blocks, keep / 2.0, max(0.5, a_in - 0.05),
                               seed=SEED_A1 + 7)
    assert stress <= power + 0.05, "stressed power must not exceed unstressed"

    names = len({b["symbol"] for b in blocks})
    sess = len({b["session"] for b in blocks})
    passed = (range_ok and class_ok and proj_units >= nreq
              and power >= POWER_TARGET and stress >= STRESS_POWER_MIN
              and names >= FLOOR_UNDERLYINGS and sess >= FLOOR_SESSIONS
              and not theta["theta_unachievable"])
    art = {
        "amendment": "References/O_SIGN_VALIDATION_AMENDMENT1_2026-08-05.md",
        "seed": SEED_A1,
        "keep_rate_pilot": keep,
        "a_cal_input_lb_of_record": rw_lb,
        "a_cal_mixture_lb_published_beside": mix_lb,
        "lb_divergence_tripwire": divergence,
        "projected_units": proj_units, "n_required": nreq,
        "power": power, "power_stressed": stress,
        "containment": {"premium_range_ok": range_ok,
                        "quote_position_class_ok": class_ok},
        "theta": theta,
        "step1p_verdict_final": verdict_final,
        "distinct_underlyings": names, "sessions_with_units": sess,
        "floors": {"n_required": nreq, "underlyings": FLOOR_UNDERLYINGS,
                   "sessions": FLOOR_SESSIONS},
        "passed": passed,
        "tercile_sim_note": "pre-run gate keeps the uniform-1/3 drift-tercile "
                            "draw (A1-5, bounded limitation); Gate 2 resamples "
                            "the realized tercile-by-session structure",
        "ladder": None if passed else
        "step 1: re-run ENTIRE pipeline at join floor 0.97 (N=175, "
        "partially-labeled-session caveat); step 2: NO-RUN (zero fetches)",
    }
    save_json(OUT_DIR / "gate.json", art)
    print(json.dumps(art, indent=1, default=str))


# =============================================================================
# step2 stage — units, dual permutation, Gate 2, verdict
# =============================================================================

def stage_step2() -> None:
    """Step 2, AMENDED (A1-6): signed-window matcher, seeded drift terciles,
    realized-structure Gate 2, quote-position class breakdown, A1-6(d) â_o.
    Runs only on gate PASSED + Step 1′ verdict_final LIVE′; the A1-2 regime-
    composition check runs BEFORE any agreement readout (one-look intact)."""
    assert_frame_shas()
    assert_step2_window_ok()
    pool, _ = qualifying_sessions()
    gate = json.loads((OUT_DIR / "gate.json").read_text())
    if not gate["passed"]:
        print("gate not passed — step2 may not run (registered ladder governs)")
        return
    step1p = json.loads((OUT_DIR / "step1p.json").read_text())
    if step1p.get("verdict_final") != "LIVE'":
        print(f"Step 1' verdict {step1p.get('verdict_final')} — "
              f"step2 may not run")
        return

    # A1-2 regime-composition check (pre-readout: uses only session lists)
    s1p_sess = step1p["sessions"]
    s1p_post = sum(1 for s in s1p_sess if s >= REGIME_BOUNDARY) / len(s1p_sess)
    blk_sess = sorted({b["session"] for b in _frame_unit_blocks(pool)})
    s2_post = (sum(1 for s in blk_sess if s >= REGIME_BOUNDARY) / len(blk_sess)
               if blk_sess else float("nan"))
    regime_gap = abs(s2_post - s1p_post)
    if not np.isfinite(regime_gap) or regime_gap > REGIME_MISMATCH_PP:
        art = {"amendment":
               "References/O_SIGN_VALIDATION_AMENDMENT1_2026-08-05.md",
               "verdict": "INVESTIGATE-REGIME-COMPOSITION",
               "step1p_post_shift_share": s1p_post,
               "step2_post_shift_share": s2_post,
               "gap_pp": regime_gap, "bound_pp": REGIME_MISMATCH_PP,
               "note": "A1-2: compositions differ by more than 10 pp — the "
                       "run routes to INVESTIGATE before any readout"}
        save_json(OUT_DIR / "step2.json", art)
        print(json.dumps(art, indent=1))
        return

    def events_of(fr):
        keep = fr["s2_netd_floor_ok"] & ~fr["s2_ambig"]
        idx = np.flatnonzero(keep)
        if not len(idx):
            return None
        return idx, fr["s2_sym"][idx], fr["s2_ts_ns"][idx]

    def pred_of(fr, j):
        return -int(fr["s2_sigma"][j])

    rec = analyze_events(pool, pool, events_of, pred_of, arm="a1")
    n_events = len(rec["pred"])
    n_missing = len(rec.get("missing", []))
    fault_frac = n_missing / (n_events + n_missing) if n_events + n_missing else 0.0

    # split primary (single-print) vs multi-print co-primary via the frame
    single = np.array([bool(_frame_val(rec, i, "s2_single_print"))
                       for i in range(n_events)])
    sigma = np.array([-p for p in rec["pred"]])       # σ_net back from pred
    signable = np.array([m["signable"] for m in rec["match"]])
    obs = np.array([m["obs_side"] for m in rec["match"]])
    prem = np.array([_frame_val(rec, i, "s2_prem") for i in range(n_events)])
    drift = np.array(rec["drift"])
    sess = np.array(rec["session"])
    sym = np.array(rec["sym"])
    mixed = np.array([bool(_frame_val(rec, i, "s2_mixed"))
                      for i in range(n_events)])
    shares = np.array([m["stock_shares"] for m in rec["match"]])

    def unit_table(mask):
        """(session, underlying) units over matched-and-signable events of the
        mask; both sides premium-weighted over the SAME event set."""
        units = {}
        m = mask & signable
        for i in np.flatnonzero(m):
            key = (sess[i], sym[i])
            u = units.setdefault(key, {"pred": 0.0, "obs": 0.0, "n": 0,
                                       "drift": [], "prem": []})
            u["pred"] += prem[i] * (-sigma[i])
            u["obs"] += prem[i] * obs[i]
            u["n"] += 1
            u["drift"].append((drift[i], prem[i]))
            u["prem"].append(prem[i])
        rows = []
        for (s_, y), u in units.items():
            ps, os_ = np.sign(u["pred"]), np.sign(u["obs"])
            if ps == 0 or os_ == 0:
                continue                                # exact-zero sums dropped
            dp = np.array(u["drift"])
            w = dp[:, 1].sum()
            rows.append({"session": s_, "sym": y, "pred": int(ps),
                         "obs": int(os_), "n": u["n"],
                         "drift": float((dp[:, 0] * dp[:, 1]).sum() / w)
                         if w > 0 else float("nan")})
        return rows

    prim = unit_table(single)
    multi = unit_table(~single)

    n_sess_units = len({r["session"] for r in prim}) or 1

    def infer(rows, seed_off=0):
        if not rows:
            return {"n_units": 0}
        pr = np.array([r["pred"] for r in rows])
        ob = np.array([r["obs"] for r in rows])
        ss = np.array([r["session"] for r in rows])
        nm = np.array([r["sym"] for r in rows])
        agr = agreement(pr, ob)
        p_sess = signflip_p(pr, ob, ss, seed=SEED_A1 + seed_off)
        p_name = signflip_p(pr, ob, nm, seed=SEED_A1 + 1 + seed_off)
        dr = np.array([r["drift"] for r in rows])
        # A1-5: seeded tie-break terciles (SEED+5 substream), both steps
        terc = terciles_seeded(dr)
        low = terc == 0
        p_low = signflip_p(pr[low], ob[low], ss[low],
                           seed=SEED_A1 + 2 + seed_off) \
            if low.sum() >= 2 else float("nan")
        zero = np.isfinite(dr) & (dr == 0.0)
        nz = np.isfinite(dr) & (dr > 0.0)
        a_low_nz = None
        if nz.sum() >= 3:
            t_nz = terciles_seeded(np.where(nz, dr, np.nan))
            a_low_nz = agreement(pr[t_nz == 0], ob[t_nz == 0])
        a_low = agreement(pr[low], ob[low])
        return {"n_units": len(rows), "agreement": agr,
                "kappa": kappa(pr, ob),
                "direction": int(np.sign(agr - 0.5)),
                "p_session": p_sess, "p_name_block": p_name,
                "low_drift": {"n": int(low.sum()), "agreement": a_low,
                              "p": p_low},
                "low_drift_session_coverage":
                    len(set(ss[low])) / n_sess_units,
                "zero_drift": {"n": int(zero.sum()), "share": float(zero.mean()),
                               "agreement": agreement(pr[zero], ob[zero])},
                "a_low_nonzero_drift": a_low_nz,
                "zero_nonzero_divergence_tripwire": bool(
                    a_low is not None and a_low_nz is not None
                    and np.isfinite(a_low) and np.isfinite(a_low_nz)
                    and ((a_low - 0.5) * (a_low_nz - 0.5) < 0
                         or abs(a_low - a_low_nz) > TRIPWIRE_DELTA)),
                "by_drift_tercile": {str(t): agreement(pr[terc == t], ob[terc == t])
                                     for t in (0, 1, 2)}}

    inf_p = infer(prim)
    inf_m = infer(multi, seed_off=10)

    # Gate 2 — realized power on the REALIZED tercile-by-session structure of
    # the fetched units (A1-5; replaces the pre-run uniform-1/3 draw here)
    r2 = _rates(rec)
    keep_real = r2["match_rate"] * r2["signable_rate"]
    if not np.isfinite(keep_real):
        keep_real = 0.0
    a_lb = gate["a_cal_input_lb_of_record"]
    terc_prim = terciles_seeded(np.array([r["drift"] for r in prim],
                                         dtype=float)) if prim else np.empty(0)
    units_g2 = [{"session": r["session"], "sym": r["sym"],
                 "tercile": int(t)} for r, t in zip(prim, terc_prim)]
    realized_power = conjunction_power_realized(units_g2, a_lb,
                                                seed=SEED_A1 + 3)

    verdict = _step2_verdict(inf_p, inf_m, realized_power, fault_frac)

    # quote-position class breakdown over matched-and-signable events (A1-6(b))
    leg_meta = json.loads((OUT_DIR / "s2_leg_meta.json").read_text())["sessions"]
    three_way = {"0": 0, "1": 0, "2": 0}
    for i in np.flatnonzero(signable):
        three_way[str(leg_meta[rec["session"][i]]["cls3"][rec["idx"][i]])] += 1

    # â_o (REPORTED-ONLY, upper bound) — A1-6(d) joint bootstrap with
    # published dropped/truncated counts and the Fieller fallback
    a_cal = step1p["a_cal_mixture"]
    agr = inf_p.get("agreement", float("nan"))
    a_o_hat = 0.5 + (agr - 0.5) / (2 * a_cal - 1) if a_cal and a_cal != 0.5 \
        else float("nan")
    a_o_ci = _joint_ao_ci_a1(prim, pool) if prim else None

    per_name = defaultdict(lambda: [0, 0])
    for r in prim:
        per_name[r["sym"]][0] += 1
        per_name[r["sym"]][1] += int(r["pred"] == r["obs"])

    art = {
        "prereg": "References/O_SIGN_VALIDATION_PREREG_2026-08-03.md",
        "amendment": "References/O_SIGN_VALIDATION_AMENDMENT1_2026-08-05.md",
        "family_ledger": FAMILY_LEDGER,
        "seed": SEED_A1,
        "scope": "TLET-family ('n') tied 2-leg in-band spread-edge packages",
        "events": n_events, "fetch_fault_frac": fault_frac,
        "rates": _rates(rec), "contamination": _contamination_a1(rec),
        "primary": inf_p, "multi_print_coprimary": inf_m,
        "quote_position_three_way": three_way,
        "regime_composition": {"step1p_post_shift_share": s1p_post,
                               "step2_post_shift_share": s2_post,
                               "gap_pp": regime_gap},
        "gate2": {"keep_rate_realized": keep_real, "a_cal_lb_input": a_lb,
                  "realized_power": realized_power,
                  "tercile_structure": "realized tercile-by-session resample "
                                       "(A1-5)"},
        "verdict": verdict,
        "a_o_hat_upper_bound_reported_only": a_o_hat,
        "a_o_joint_bootstrap_a1": a_o_ci,
        "strata": {
            "mixed_vs_same_right": {
                "mixed": _rates({"match": [rec["match"][i]
                                           for i in np.flatnonzero(mixed)]}),
                "same": _rates({"match": [rec["match"][i]
                                          for i in np.flatnonzero(~mixed)]})},
            "stock_size_p50": float(np.nanmedian(shares[signable]))
            if signable.any() else None,
            "sigma_sign_match": {
                "+1": _rates({"match": [rec["match"][i]
                                        for i in np.flatnonzero(sigma == 1)]}),
                "-1": _rates({"match": [rec["match"][i]
                                        for i in np.flatnonzero(sigma == -1)]})},
            "per_name": {k: {"units": v[0], "agree": v[1]}
                         for k, v in sorted(per_name.items())},
        },
    }
    save_json(OUT_DIR / "step2.json", art)
    print(json.dumps({k: art[k] for k in
                      ("events", "rates", "primary", "multi_print_coprimary",
                       "gate2", "verdict")}, indent=1, default=str))


def _joint_ao_ci(prim: list[dict], pool: list[date],
                 n_boot: int = 2_000) -> tuple[float, float]:
    """Joint bootstrap over COMMON sessions: resample the qualifying-session
    list; recompute a_cal (Step-1 events of resampled sessions) and unit
    agreement together; truncation to [0,1] disclosed by the caller."""
    s1 = np.load(OUT_DIR / "step1_events.npz")
    rng = np.random.default_rng(SEED + 4)
    sessions = np.array([s.isoformat() for s in pool])
    vals = []
    s1_sess = s1["sess"]
    s1_hit = (s1["pred"] == s1["obs"]).astype(float)
    p_sess = np.array([r["session"] for r in prim])
    p_hit = np.array([float(r["pred"] == r["obs"]) for r in prim])
    for _ in range(n_boot):
        pick = rng.choice(sessions, size=len(sessions), replace=True)
        h1 = np.concatenate([s1_hit[s1_sess == s] for s in pick]) \
            if len(s1_hit) else np.empty(0)
        h2 = np.concatenate([p_hit[p_sess == s] for s in pick]) \
            if len(p_hit) else np.empty(0)
        if not len(h1) or not len(h2):
            continue
        ac, ag = h1.mean(), h2.mean()
        if ac <= 0.5:
            continue
        vals.append(min(1.0, max(0.0, 0.5 + (ag - 0.5) / (2 * ac - 1))))
    if not vals:
        return float("nan"), float("nan")
    return (float(np.percentile(vals, 2.5)), float(np.percentile(vals, 97.5)))


def _step2_verdict(inf_p: dict, inf_m: dict, realized_power: float,
                   fault_frac: float) -> str:
    if fault_frac > STOP_MECHANICS_FRAC:
        return "STOP-MECHANICS"
    if not inf_p.get("n_units"):
        return "NO-VERDICT-UNDERPOWERED" if realized_power < POWER_TARGET \
            else "NO-VALIDATION"
    sig = (inf_p["p_session"] < 0.01 and inf_p["p_name_block"] < 0.01)
    low = inf_p["low_drift"]
    low_ok = (np.isfinite(low["p"]) and low["p"] < 0.05
              and np.sign(low["agreement"] - 0.5) == inf_p["direction"])
    multi_ok = (not inf_m.get("n_units")) or \
        (np.sign(inf_m["agreement"] - 0.5) == inf_p["direction"])
    if sig and low_ok and multi_ok:
        return "O-SIGN-VALIDATED-TIED" if inf_p["direction"] > 0 \
            else "O-SIGN-INVERTED-TIED-CONFIRM-BEFORE-SHIP"
    if sig and not low_ok:
        return "AMBIGUOUS-DRIFT"
    if sig and not multi_ok:
        return "AMBIGUOUS"
    # non-significant: Amendment-1 semantics — power decides the label
    return "NO-VALIDATION" if realized_power >= POWER_TARGET \
        else "NO-VERDICT-UNDERPOWERED"


# =============================================================================
# AMENDMENT 1 (2026-08-05) — midpoint sign rule + signed millisecond window
# References/O_SIGN_VALIDATION_AMENDMENT1_2026-08-05.md (IS LAW). Everything
# below implements the 15 delta build obligations; the registered-arm code
# above is untouched (its 2026-08-04 verdict stands and is the record).
# =============================================================================

def match_event_signed(trades: dict, quotes: dict, t0_ns: int,
                       lo_ns: int = W_LO_NS, hi_ns: int = W_HI_NS,
                       lag_ns: int = 0) -> dict:
    """A1-2 matcher: MF-1 with Δt = equity ts − t0 in the SIGNED window
    [lo_ns, hi_ns] (inclusive both edges). The in-window funnel mirrors
    `match_event`; the data-change tripwire counters are ADDITIONALLY counted
    over the FULL fetched ±300 s window (`*_full`) so narrowing the matcher
    cannot blind the feed-schema detectors (build obligation 1)."""
    cond, tape = trades["cond"], trades["tape"]
    n = len(cond)
    if n:
        has7 = np.char.find(cond, "7") >= 0
        hasv = np.char.find(cond, "V") >= 0
        excl = _excluded_mask(cond, tape)
    else:
        has7 = hasv = excl = np.zeros(0, dtype=bool)
    dt = trades["t_ns"] - t0_ns
    in_w = (dt >= lo_ns) & (dt <= hi_ns)
    qual = has7 & hasv & ~excl & in_w
    out = {
        "n_window": int(in_w.sum()),
        "n_7only": int((has7 & ~hasv & in_w).sum()),
        "n_Vonly": int((hasv & ~has7 & in_w).sum()),
        "n_excl_overlap": int((has7 & hasv & excl & in_w).sum()),
        "n_7only_full": int((has7 & ~hasv).sum()),
        "n_Vonly_full": int((hasv & ~has7).sum()),
        "n_excl_overlap_full": int((has7 & hasv & excl).sum()),
        "n_qual": int(qual.sum()),
        "matched": bool(qual.any()),
        "signable": False, "S_net": 0.0, "sum_size": 0.0, "coherence": 0.0,
        "obs_side": 0, "dt_ns": None, "stock_shares": 0.0,
    }
    if not qual.any():
        return out
    q_t = trades["t_ns"][qual]
    q_px = trades["price"][qual]
    q_sz = trades["size"][qual].astype(np.float64)
    signs = sign_stock_prints(q_t, q_px, quotes["t_ns"], quotes["bid"],
                              quotes["ask"], lag_ns=lag_ns)
    s_net = float((signs * q_sz).sum())
    tot = float(q_sz.sum())
    coh = abs(s_net) / tot if tot > 0 else 0.0
    out.update({
        "S_net": s_net, "sum_size": tot, "coherence": coh,
        "signable": bool(coh >= COHERENCE_MIN and s_net != 0.0),
        "obs_side": int(np.sign(s_net)),
        "dt_ns": int(q_t[np.argmin(np.abs(q_t - t0_ns))] - t0_ns),
        "stock_shares": tot,
    })
    return out


def cluster_bounds(gid: np.ndarray, ts: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Per-cluster (t0, span): t0 = the MINIMUM ts_event across the cluster's
    prints (A1-2, pinned test), span = max − min. `gid` must be grouped
    contiguously (frame sort order); intra-group order is free."""
    first = np.r_[0, 1 + np.flatnonzero(gid[1:] != gid[:-1])]
    t0 = np.minimum.reduceat(ts, first)
    t1 = np.maximum.reduceat(ts, first)
    return t0, t1 - t0


def cluster_side_class(gid: np.ndarray, side: np.ndarray,
                       n_g: int) -> tuple[np.ndarray, np.ndarray]:
    """A1-1 unanimity-or-drop on the corpus MIDPOINT `side` alphabet
    (0 buy, 1 sell, 2 at-mid/no-quote/locked, 3 wide-fence). Returns
    (uside, cls): uside = 0/1 where EVERY print in the cluster carries the
    same signable label, else −1; cls = the funnel row — 0 signable,
    1 at-mid/no-quote (a), 2 wide-fence (b), 3 mixed-cluster (c), with
    priority (c) > (b) > (a) so every unsignable cluster lands in exactly
    one counted row. Pinned against the `strict`→`side` column-swap trap."""
    n_all = np.bincount(gid, minlength=n_g)
    n0 = np.bincount(gid, weights=(side == 0).astype(float), minlength=n_g)
    n1 = np.bincount(gid, weights=(side == 1).astype(float), minlength=n_g)
    n3 = np.bincount(gid, weights=(side == 3).astype(float), minlength=n_g)
    uside = np.full(n_g, -1, dtype=np.int8)
    ok = n_all > 0
    uside[ok & (n0 == n_all)] = 0
    uside[ok & (n1 == n_all)] = 1
    cls = np.ones(n_g, dtype=np.int8)               # (a) at-mid/no-quote
    cls[n3 > 0] = 2                                 # (b) wide-fence
    cls[(n0 > 0) & (n1 > 0)] = 3                    # (c) mixed cluster
    cls[uside >= 0] = 0                             # signable
    return uside, cls


def stratified_cap_a1(prem: np.ndarray, is_strict: np.ndarray,
                      cap: int = STEP1_CAP) -> tuple[np.ndarray, dict]:
    """A1-1 per-session cap: premium terciles cut over the FULL eligible union
    population, crossed with quote-position class (strict-unanimous /
    non-strict) — 6 cells, equal allocation, evenly spaced by premium rank
    within cell (deterministic: the cap is sample SELECTION, not a draw).
    A cell short of quota redistributes the shortfall WITHIN its premium
    tercile to the other class. Returns (selected indices, disclosure:
    eligible-before-cap counts + fill matrix, both 3×2 tercile×class)."""
    n = len(prem)
    order = np.argsort(prem, kind="stable")
    terc = np.empty(n, dtype=np.int8)
    for t, part in enumerate(np.array_split(order, 3)):
        terc[part] = t
    q = cap // 6
    elig = [[0, 0], [0, 0], [0, 0]]
    fill = [[0, 0], [0, 0], [0, 0]]
    sel: list[np.ndarray] = []
    for t in (0, 1, 2):
        cells = []
        for c, m in ((0, (terc == t) & is_strict), (1, (terc == t) & ~is_strict)):
            idx = np.flatnonzero(m)
            cells.append(idx[np.argsort(prem[idx], kind="stable")])
            elig[t][c] = len(idx)
        if n <= cap:
            take = [len(cells[0]), len(cells[1])]
        else:
            take = [min(q, len(cells[0])), min(q, len(cells[1]))]
            spare0, spare1 = q - take[0], q - take[1]
            take[1] = min(len(cells[1]), take[1] + spare0)
            take[0] = min(len(cells[0]), take[0] + spare1)
        for c in (0, 1):
            idx = cells[c]
            if take[c] >= len(idx):
                pick = idx
            else:
                pick = idx[np.linspace(0, len(idx) - 1,
                                       take[c]).round().astype(int)]
            fill[t][c] = len(pick)
            sel.append(pick)
    picked = np.sort(np.concatenate(sel)) if sel else np.empty(0, dtype=np.int64)
    return picked, {"eligible": elig, "fill": fill, "cell_quota": q}


def terciles_seeded(x: np.ndarray,
                    seed: int = SEED_A1 + TIE_SUBSTREAM_OFF) -> np.ndarray:
    """A1-5 drift terciles: rank-based EQUAL-COUNT bins ordered by (x, U),
    U a per-event uniform draw from the SEED+5 substream — ties (notably the
    zero-drift mass) break at RANDOM, never by array position (the measured
    date-clump trap). NaN → −1. Lowest tercile = label 0, always ≈ n/3."""
    out = np.full(len(x), -1, dtype=np.int8)
    ok = np.isfinite(x)
    idx = np.flatnonzero(ok)
    if len(idx) < 3:
        out[idx] = 0
        return out
    u = np.random.default_rng(seed).random(len(x))
    order = idx[np.lexsort((u[idx], x[idx]))]
    for t, part in enumerate(np.array_split(order, 3)):
        out[part] = t
    return out


# --- A1-7: session-cluster bootstrap-t intervals (+ A1-6(b) reweighting) -----

def _jack_se(loo: np.ndarray) -> float:
    """Delete-one jackknife SE from leave-one-out statistics."""
    loo = loo[np.isfinite(loo)]
    g = len(loo)
    if g < 2:
        return float("nan")
    return float(np.sqrt((g - 1) / g * ((loo - loo.mean()) ** 2).sum()))


def _bt_bounds(a_hat: float, se: float, t_star: np.ndarray,
               alpha: float = 0.05) -> tuple[float, float]:
    """A1-7 CROSSED quantiles — LB = â − t*_{1−α/2}·SE, UB = â − t*_{α/2}·SE
    (getting the crossing backwards inflates the LB toward LIVE′; pinned)."""
    t_lo = float(np.quantile(t_star, alpha / 2))
    t_hi = float(np.quantile(t_star, 1 - alpha / 2))
    return a_hat - t_hi * se, a_hat - t_lo * se


def _bca(a_hat: float, a_star: np.ndarray, loo: np.ndarray,
         alpha: float = 0.05) -> tuple[float, float]:
    """BCa fallback (A1-7: > 1% non-finite within-draw SE ⇒ bootstrap-t is
    declared unstable and BCa is reported instead)."""
    from scipy.stats import norm
    a_star = a_star[np.isfinite(a_star)]
    if not len(a_star):
        return float("nan"), float("nan")
    z0 = norm.ppf(np.clip((a_star < a_hat).mean(), 1e-9, 1 - 1e-9))
    lf = loo[np.isfinite(loo)]
    d = lf.mean() - lf
    denom = float((d ** 2).sum() ** 1.5)
    acc = float((d ** 3).sum() / (6 * denom)) if denom > 0 else 0.0
    out = []
    for qq in (alpha / 2, 1 - alpha / 2):
        z = z0 + norm.ppf(qq)
        adj = float(norm.cdf(z0 + z / (1 - acc * z)))
        out.append(float(np.quantile(a_star, np.clip(adj, 0.0, 1.0))))
    return out[0], out[1]


def bootstrap_t_ci(hit: np.ndarray, sess: np.ndarray, n_boot: int = N_BOOT,
                   seed: int = SEED_A1, alpha: float = 0.05,
                   cells: np.ndarray | None = None,
                   target: dict[int, float] | None = None
                   ) -> tuple[float, float, dict]:
    """A1-7 session-cluster bootstrap-t CI on mean(hit), fully specified:
    per draw, the studentizing SE* is the delete-one-cluster jackknife WITHIN
    that draw; t* = (a* − â)/SE*; the unstarred SE is the delete-one-session
    jackknife on the ORIGINAL sample; bounds take CROSSED quantiles.

    With `cells` + `target`, the statistic is the A1-6(b) composition-
    reweighted agreement — reweighting happens INSIDE every draw and every
    jackknife leave-out (never reweight the point estimate and reuse an
    unweighted width; no percentile-of-draws construction exists here).
    Returns (lb, ub, meta) with the cluster count recorded (A1-7)."""
    hit = np.asarray(hit, dtype=np.float64)
    uniq, inv = np.unique(np.asarray(sess), return_inverse=True)
    g = len(uniq)
    c = (np.zeros(len(hit), dtype=np.int64) if cells is None
         else np.asarray(cells, dtype=np.int64))
    k = int(c.max()) + 1 if len(c) else 1
    if target:
        # cover target cells the data never realized: they stay present in the
        # weight vector (and renormalize away per-draw, disclosed via the
        # gate's containment check) instead of silently dropping target mass
        k = max(k, max(int(j) for j in target) + 1)
    S = np.zeros((g, k))
    N = np.zeros((g, k))
    np.add.at(S, (inv, c), hit)
    np.add.at(N, (inv, c), 1.0)
    w = (np.ones(k) if target is None
         else np.array([float(target.get(j, 0.0)) for j in range(k)]))

    def stat(sm, nm):
        pres = nm > 0
        means = np.where(pres, sm / np.where(pres, nm, 1.0), 0.0)
        wt = np.where(pres, w, 0.0)
        den = wt.sum(axis=-1)
        num = (wt * means).sum(axis=-1)
        return np.where(den > 0, num / np.where(den > 0, den, 1.0), np.nan)

    a_hat = float(stat(S.sum(0), N.sum(0)))
    loo = stat(S.sum(0)[None] - S, N.sum(0)[None] - N)
    se = _jack_se(loo)
    rng = np.random.default_rng(seed)
    a_star = np.full(n_boot, np.nan)
    t_star = np.full(n_boot, np.nan)
    for i in range(n_boot):
        rows = rng.integers(0, g, size=g)
        sd, nd = S[rows], N[rows]
        st, nt = sd.sum(0), nd.sum(0)
        a_i = float(stat(st, nt))
        se_i = _jack_se(stat(st[None] - sd, nt[None] - nd))
        a_star[i] = a_i
        if np.isfinite(a_i) and np.isfinite(se_i) and se_i > 0:
            t_star[i] = (a_i - a_hat) / se_i
    finite = np.isfinite(t_star)
    frac_bad = float(1.0 - finite.mean())
    meta = {"n_clusters": int(g), "n_boot": int(n_boot),
            "frac_nonfinite_se": frac_bad}
    if frac_bad > 0.01 or not (np.isfinite(se) and se > 0):
        meta["method"] = "BCa"
        lb, ub = _bca(a_hat, a_star, loo, alpha)
    else:
        meta["method"] = "bootstrap-t"
        lb, ub = _bt_bounds(a_hat, se, t_star[finite], alpha)
    meta["a_hat"] = a_hat
    return float(lb), float(ub), meta


# --- A1-9: placebo law at the millisecond window -----------------------------

def clopper_pearson_ub(k: int, n: int, conf: float = 0.95) -> float:
    """One-sided Clopper–Pearson upper bound on a binomial rate."""
    if n == 0:
        return float("nan")
    if k >= n:
        return 1.0
    from scipy.stats import beta
    return float(beta.ppf(conf, k + 1, n - k))


def _contamination_a1(rec: dict) -> dict:
    """A1-9(a): both placebo artifacts publish RAW numerator and denominator
    counts plus a Clopper–Pearson 95% upper bound beside the ratio, at the
    primary (signed) window and each symmetric sensitivity window."""
    n_real = len(rec["match"])
    k_real = sum(1 for m in rec["match"] if m["matched"])
    real_rate = k_real / n_real if n_real else float("nan")

    def block(ms, real_r):
        n = len(ms)
        k_ = sum(1 for m in ms if m["matched"])
        r = k_ / n if n else float("nan")
        c = (r / real_r if np.isfinite(r) and np.isfinite(real_r)
             and real_r > 0 else float("nan"))
        return {"n_matches": k_, "n_windows": n, "rate": r,
                "cp95_ub": clopper_pearson_ub(k_, n), "c": c}

    out = {"t60_primary": block(rec["placebo_t"], real_rate),
           "alt_primary": block([m for m in rec["placebo_s"] if m is not None],
                                real_rate)}
    for w in A1_SENS_W_NS:
        real_w = (sum(1 for m in rec["match_sens"] if m[str(w)]["matched"])
                  / n_real) if n_real else float("nan")
        out[f"t60_{w}"] = block([m[str(w)] for m in rec["placebo_t_sens"]],
                                real_w)
        out[f"alt_{w}"] = block([m[str(w)] for m in rec["placebo_s_sens"] if m],
                                real_w)
    return out


def placebo_gate_a1(contam: dict) -> dict:
    """A1-9 placebo law: c < 0.01 at the PRIMARY window for BOTH offsets, and
    a non-finite c is a FAIL, never a skip (the inherited all()-over-finite
    gate passed vacuously on an empty placebo — pinned test). Positive
    control: the ±100 ms leg's placebo match rate must be finite and > 0 for
    both offsets — nan fails by assertion, not by NumPy accident (pinned in
    the SAME test). The primary-window placebo carries no evidential weight
    against chance capture; it is a SYSTEMATIC-contamination tripwire only."""
    cs = {k: contam[k]["c"] for k in ("t60_primary", "alt_primary")}
    c_ok = all(bool(np.isfinite(v)) and v < C_MAX_PRIMARY for v in cs.values())
    w100 = A1_SENS_W_NS[1]
    pos = {}
    for lab in ("t60", "alt"):
        r = contam[f"{lab}_{w100}"]["rate"]
        pos[lab] = bool(np.isfinite(r)) and r > 0
    return {"placebo_c_lt_01": bool(c_ok),
            "positive_control_100ms_gt_0": bool(all(pos.values())),
            "positive_control_by_offset": pos,
            "attenuation": "reported as nil at c < 0.01 (A1-9; < 0.5 points)"}


# --- A1-2: pinned skew diagnostics + capture fraction ------------------------

def skew_diagnostics(dt_ns: np.ndarray, capture_num: int,
                     capture_den: int) -> dict:
    """A1-2 PINNED estimator for ONE session. `dt_ns` = signed Δt of the
    session's matched events INSIDE the primary window. Modes are located on
    0.2 ms bins computed SEPARATELY for the two registered sub-populations
    (Δt < 0.5 ms / Δt ≥ 0.5 ms); each sub-mode must sit ≥ 0.5 ms from its
    nearest window edge (an empty sub-population's clause passes — no mode
    exists to sit near an edge). Capture fraction = primary-window matched
    events / ±100 ms matched events, floor 0.30. A tripped session routes to
    INVESTIGATE — a match-gate failure so driven may NOT be read as
    INSTRUMENT-DEAD′-MECHANICS."""
    lo_ms, hi_ms = W_LO_NS / 1e6, W_HI_NS / 1e6
    dt_ms = np.asarray(dt_ns, dtype=np.float64) / 1e6
    dt_ms = dt_ms[(dt_ms >= lo_ms) & (dt_ms <= hi_ms)]
    edges = np.arange(lo_ms, hi_ms + SKEW_BIN_MS / 2, SKEW_BIN_MS)
    modes = {}
    for name, m in (("neg", dt_ms < SKEW_SPLIT_MS),
                    ("pos", dt_ms >= SKEW_SPLIT_MS)):
        sub = dt_ms[m]
        if not len(sub):
            modes[name] = {"mode_ms": None, "edge_margin_ms": None, "ok": True}
            continue
        h, _ = np.histogram(sub, bins=edges)
        i = int(np.argmax(h))
        mode = (edges[i] + edges[i + 1]) / 2
        margin = min(mode - lo_ms, hi_ms - mode)
        modes[name] = {"mode_ms": float(mode), "edge_margin_ms": float(margin),
                       "ok": bool(margin >= SKEW_EDGE_MIN_MS)}
    capture = capture_num / capture_den if capture_den else float("nan")
    cap_ok = bool(np.isfinite(capture)) and capture >= CAPTURE_FLOOR
    return {"n_in_window": int(len(dt_ms)), "sub_modes": modes,
            "capture_fraction": float(capture) if np.isfinite(capture) else None,
            "capture_ok": cap_ok,
            "tripped": not (modes["neg"]["ok"] and modes["pos"]["ok"]
                            and cap_ok)}


# --- A1-3: frame-npz SHA preconditions (build obligation 4) ------------------

def record_frame_shas() -> None:
    """SHA-256 every frame npz into frame_manifest.json. First call records;
    later calls verify (never overwrite — the frozen frame stays frozen)."""
    path = OUT_DIR / "frame_manifest.json"
    manifest = json.loads(path.read_text())
    shas = {row["session"]: _sha256(FRAME_DIR / f"{row['session']}.npz")
            for row in manifest["sessions"]}
    if "frame_npz_sha256" in manifest:
        assert manifest["frame_npz_sha256"] == shas, \
            "frame npz SHAs changed since recording — frozen frame violated"
        print(f"frame npz SHAs verified for {len(shas)} sessions (already recorded)")
        return
    manifest["frame_npz_sha256"] = shas
    save_json(path, manifest)
    print(f"frame npz SHAs recorded for {len(shas)} sessions")


def assert_frame_shas() -> None:
    """Coded precondition on EVERY amended stage: abort if any frame npz SHA
    differs from the recorded value (A1-3 — closes the overwrite-before-assert
    hazard; an aborted stage mis-joins nothing)."""
    manifest = json.loads((OUT_DIR / "frame_manifest.json").read_text())
    recorded = manifest.get("frame_npz_sha256")
    assert recorded, "frame npz SHAs not recorded — run `record_shas` first"
    for ds, sha in recorded.items():
        assert _sha256(FRAME_DIR / f"{ds}.npz") == sha, \
            f"frame npz SHA mismatch for {ds} — frozen frame changed"


# --- A1-4: Step 1′ sessions + midpoint event table ---------------------------

def step1p_sample_sessions(pool: list[date]) -> list[date]:
    """A1-4: Step 1′ = pool[3::6] — disjoint from Step 1's pool[::6] by
    construction, with the CODED assertion the amendment requires."""
    sample = pool[STEP1P_RESIDUE::STEP1_EVERY]
    overlap = set(sample) & set(step1_sample_sessions(pool))
    assert not overlap, \
        f"Step 1' sessions overlap Step 1's: {sorted(overlap)}"
    return sample


def _step1p_session_events(session: date, rc) -> dict | None:
    """A1-1 Step-1′ midpoint event table for one session: the UNION population
    (strict-unanimous ∪ non-strict single-leg tied clusters passing the
    inherited §3a filters) signed by the corpus MIDPOINT `side` label under
    unanimity-or-drop, with the three unsignable funnel rows and the 6-cell
    stratified cap. Reads the same SHA-verified session inputs as the frame;
    NEVER opens a frame npz (A1-3)."""
    loaded = load_session_fixed(session)
    if loaded is None:
        return None
    defs, fx, ts, iid, size, cond, bidf, askf, didx, jr = loaded
    und = defs.und_code[didx]
    tied = np.isin(cond, list(TIED_CODES))
    if not tied.any():
        return None
    order = np.lexsort((ts[tied], und[tied]))

    def col(a):
        return a[tied][order]

    t_ts, t_iid, t_und = col(ts), col(iid), col(und)
    t_prem = col(fx.premium)
    # A1-1: predicted side = the corpus MIDPOINT label (`fx.side`), never the
    # at/through-quote `strict` column — both alphabets are 0/1 and a
    # wrong-column read is silent (pinned test covers the swap trap)
    t_side, t_strict = col(fx.side), col(fx.strict)
    t_def = col(didx)
    new = np.empty(len(t_ts), dtype=bool)
    new[0] = True
    new[1:] = (t_und[1:] != t_und[:-1]) | ((t_ts[1:] - t_ts[:-1]) > W_NS)
    gid = np.cumsum(new) - 1
    n_g = int(gid[-1]) + 1
    o2 = np.lexsort((t_ts, t_iid, gid))
    g2, i2 = gid[o2], t_iid[o2]
    first = np.empty(len(g2), dtype=bool)
    first[0] = True
    first[1:] = (g2[1:] != g2[:-1]) | (i2[1:] != i2[:-1])
    leg_gid = g2[first]
    leg_def = t_def[o2][np.flatnonzero(first)]
    legs_per = np.bincount(leg_gid, minlength=n_g)
    c_prem = np.bincount(gid, weights=t_prem, minlength=n_g)
    g_t0, g_span = cluster_bounds(gid, t_ts)
    g_first_row = np.r_[0, 1 + np.flatnonzero(gid[1:] != gid[:-1])]
    g_und = t_und[g_first_row]
    n_prints = np.bincount(gid, minlength=n_g)

    ambig = claimant_ambig(g_t0, g_und)
    uside, ucls = cluster_side_class(gid, t_side, n_g)
    n_t0 = np.bincount(gid, weights=(t_strict == 0).astype(float), minlength=n_g)
    n_t1 = np.bincount(gid, weights=(t_strict == 1).astype(float), minlength=n_g)
    n_all = np.bincount(gid, minlength=n_g)
    strict_unani = (n_t0 == n_all) | (n_t1 == n_all)

    g1 = np.flatnonzero(legs_per == 1)
    l1 = np.searchsorted(leg_gid, g1)
    ds = session.isoformat()
    roots = [str(s) for s in defs.roots]
    cls_arr = np.array([rc.classify(r, ds) for r in roots])
    ident = np.array([str(rc.resolve(r, ds) or "") for r in roots])
    rootc = defs.und_code[leg_def[l1]]
    mid = session_midnight_ns(session)
    base = ((c_prem[g1] >= PREM_MIN_S1)
            & in_trading_window(et_second_of_day(g_t0[g1], mid))
            & (cls_arr[rootc] == "equity") & (ident[rootc] != ""))
    funnel = {
        "single_leg_tied": int(len(g1)),
        "pass_prem_time_equity": int(base.sum()),
        "unsignable_atmid_or_noquote": int((base & (ucls[g1] == 1)).sum()),
        "unsignable_wide_fence": int((base & (ucls[g1] == 2)).sum()),
        "unsignable_mixed_cluster": int((base & (ucls[g1] == 3)).sum()),
        "signable_union": int((base & (uside[g1] >= 0)).sum()),
        "signable_strict_unanimous": int((base & (uside[g1] >= 0)
                                          & strict_unani[g1]).sum()),
    }
    keep = base & (uside[g1] >= 0)
    funnel["ambiguous_claimant"] = int((keep & ambig[g1]).sum())
    keep &= ~ambig[g1]
    funnel["eligible"] = int(keep.sum())
    k = np.flatnonzero(keep)
    if not len(k):
        return None
    sel_rel, cap_info = stratified_cap_a1(c_prem[g1][k], strict_unani[g1][k])
    sel = k[sel_rel]
    events = [{
        "sym": str(ident[rootc[j]]), "ts_ns": int(g_t0[g1][j]),
        "span_ns": int(g_span[g1][j]), "side": int(uside[g1][j]),
        "is_put": bool(defs.is_put[leg_def[l1[j]]]),
        "strict_cls": bool(strict_unani[g1][j]), "prem": float(c_prem[g1][j]),
        "n_prints": int(n_prints[g1][j]),
    } for j in sel]
    return {"events": events, "funnel": funnel, "cap": cap_info,
            "join_rate": jr}


def stage_fetch1p(limit: int | None = None) -> None:
    """A1-4: build + persist the Step-1′ event table, then FRESH fetches
    through the full `_fetch_events` path (real + both placebo offsets +
    fault ledger; NOT the placebo-blind `stage_diag_fetch` path). The
    2026-08-04 snapshots are unreachable by construction — the snap cache
    keys on absolute epoch-ns and the sessions are disjoint."""
    assert_frame_shas()
    pool, _ = qualifying_sessions()
    sample = step1p_sample_sessions(pool)
    if limit:
        sample = sample[:limit]
    rc = load_classification(ROOT / "signals" / "options_flow"
                             / "root_classification.json")
    table: dict[str, dict] = {}
    for s in sample:
        row = _step1p_session_events(s, rc)
        if row is None:
            print(f"{s}: no eligible Step-1' events")
            continue
        table[s.isoformat()] = row
        f = row["funnel"]
        print(f"{s}: {len(row['events'])} selected of {f['eligible']} eligible "
              f"(union {f['signable_union']}, strict {f['signable_strict_unanimous']})")
    save_json(OUT_DIR / "step1p_events.json", {
        "amendment": "References/O_SIGN_VALIDATION_AMENDMENT1_2026-08-05.md",
        "sessions": table,
    })

    def events_of(session):
        t = table.get(session.isoformat())
        if t is None:
            return None
        return (np.array([e["sym"] for e in t["events"]]),
                np.array([e["ts_ns"] for e in t["events"]], dtype=np.int64))

    _fetch_events(sample, pool, events_of, "step1p", placebo=True)


def _assert_table_keys(table_rows: list[dict], rec: dict) -> None:
    """Build obligation 3: the analyzed events' (symbol, ts_ns) keys must be
    an in-order subset of the event table's — an index misalignment aborts
    rather than silently mis-joining premiums."""
    table_keys = [(e["sym"], e["ts_ns"]) for e in table_rows]
    got = list(zip(rec["sym"], rec["ts_ns"]))
    it = iter(table_keys)
    for key in got:
        for cand in it:
            if cand == key:
                break
        else:
            raise AssertionError(
                f"analyzed event {key} not found in-order in the Step-1' "
                f"event table — index misalignment")


FAMILY_LEDGER = {
    "arm_count": 2,
    "arms": ["strict at/through-quote, ±100 ms (2026-08-03 prereg) — "
             "INSTRUMENT-DEAD-MECHANICS, final",
             "midpoint, signed [−1,+3] ms (Amendment 1, 2026-08-05)"],
    "selection_depth": "2 sign rules × 3 |Δt| bands × window placement "
                       "(symmetric ±2 ms vs signed [−1,+3] ms) on the "
                       "2026-08-04 data",
    "stopping_rule": "any non-LIVE' row ⇒ this successor is DEAD as a Step-2 "
                     "path and the o-band stays sign-agnostic; no further arm "
                     "without a new prereg",
}


def _theta_star_a1(blocks: list[dict], keep_rate: float,
                   seed: int = SEED_A1) -> dict:
    """A1-6(c): θ grid [0.55, 0.95] in 0.05 steps, computed on the N=105
    projection; if unachievable there, recomputed on the ladder N=175
    projection (partially-labeled caveat carried); still unachievable at
    0.95 ⇒ `theta_unachievable` and the run routes to row (viii)
    CALIBRATED-NO-RUN′. θ* = max(θ, 0.60) — the 0.60 backstop is θ* in the
    unachievable branch, so row (viii) still requires LB ≥ 0.60."""
    proj = round(len(blocks) * keep_rate)
    theta, basis = None, None
    if keep_rate > 0 and blocks:
        for a in THETA_GRID_A1:
            if n_required(blocks, keep_rate, a, seed=seed) <= proj:
                theta, basis = a, f"N={LADDER_N[0]}"
                break
        if theta is None:
            rng = np.random.default_rng(seed + 11)
            k = int(round(len(blocks) * LADDER_N[1] / LADDER_N[0]))
            b175 = [blocks[i] for i in rng.integers(0, len(blocks), size=k)]
            proj175 = round(k * keep_rate)
            for a in THETA_GRID_A1:
                if n_required(b175, keep_rate, a, seed=seed) <= proj175:
                    theta = a
                    basis = f"N={LADDER_N[1]} ladder (partially-labeled caveat)"
                    break
    unach = theta is None
    return {"theta": theta,
            "theta_star": (max(theta, THETA_BACKSTOP) if theta is not None
                           else THETA_BACKSTOP),
            "theta_unachievable": unach, "theta_grid_basis": basis,
            "projected_units": proj, "keep_rate": keep_rate,
            "grid": THETA_GRID_A1}


def _step1p_verdict(gates: dict, ci: tuple, p: float,
                    lb: float | None = None, theta_star: float | None = None,
                    theta_unachievable: bool = False,
                    skew_tripped: bool = False) -> str:
    """A1-10 truth table, gapless. `ci` is the MIXTURE bootstrap-t CI — rows
    (ii)/(iii) are deliberately evaluated FIRST and on the mixture
    (composition-robust); `lb` is the composition-reweighted LB of record
    (A1-6(b)), None until the gate computes it — a run that clears rows
    (i)–(iii) then returns PENDING-GATE-REWEIGHTED-LB. Row (viii)
    CALIBRATED-NO-RUN′ takes precedence over LIVE′ (pinned test)."""
    if gates["stop_mechanics"]:
        return "STOP-MECHANICS'"
    if not gates.get("positive_control", True):
        return "INVESTIGATE'-PLACEBO-CONTROL"       # A1-9(b): matcher presumed dead
    if not (gates["match_ge_20"] and gates["signable_ge_50"]
            and gates["placebo_c_lt_01"]):
        # A1-2: a match-gate failure driven by a tripped skew session may NOT
        # be read as INSTRUMENT-DEAD′-MECHANICS
        return "INVESTIGATE'-SKEW" if skew_tripped \
            else "INSTRUMENT-DEAD'-MECHANICS"
    lb_m, ub_m = ci
    if not (np.isfinite(lb_m) and np.isfinite(ub_m)):
        return "INSTRUMENT-DEAD'-MECHANICS"
    if lb_m <= 0.5 <= ub_m:
        return "INSTRUMENT-DEAD'-NO-DIRECTION"      # row (ii)
    if ub_m < 0.5:
        return "INSTRUMENT-DEAD'-INVERTED-TERMINAL"  # row (iii)
    if lb is None or theta_star is None:
        return "PENDING-GATE-REWEIGHTED-LB"
    if p < 0.01 and lb >= theta_star:
        return "CALIBRATED-NO-RUN'" if theta_unachievable else "LIVE'"
    return "INSTRUMENT-WEAK'"                       # row (vii)


def stage_step1p() -> None:
    """Step 1′ (Amendment 1): midpoint sign rule at the signed [−1, +3] ms
    window on the DISJOINT pool[3::6] sessions. Emits every registered
    calibration output and the PROVISIONAL truth-table position — rows
    (iv)–(viii) finalize at the gate on the composition-reweighted LB
    (A1-6(b)), which needs the pilot's composition. Never opens a frame npz."""
    assert_frame_shas()
    pool, _ = qualifying_sessions()
    sample = step1p_sample_sessions(pool)
    table = json.loads((OUT_DIR / "step1p_events.json").read_text())["sessions"]
    ledger = json.loads((OUT_DIR / "fetch_ledger_step1p.json").read_text())
    n_table = sum(len(t["events"]) for t in table.values())
    assert n_table == ledger["events"], \
        f"event table ({n_table}) != fetch ledger ({ledger['events']})"

    rec: dict[str, list] = defaultdict(list)
    n_missing = 0
    alt_missing = 0
    for session in sample:
        t = table.get(session.isoformat())
        if t is None:
            continue
        alt = _alt_session(session, pool)
        base, alt_base = session_midnight_ns(session), session_midnight_ns(alt)
        for e in t["events"]:
            win = _load_window(e["sym"], e["ts_ns"])
            if win is None:
                n_missing += 1
                continue
            trades, quotes = win
            t_alt = alt_base + (e["ts_ns"] - base)
            win_alt = _load_window(e["sym"], t_alt)
            if win_alt is None:
                alt_missing += 1
            b = _match_bundle(trades, quotes, e["ts_ns"], win_alt, t_alt, "a1")
            rec["session"].append(session.isoformat())
            rec["sym"].append(e["sym"])
            rec["ts_ns"].append(e["ts_ns"])
            rec["pred"].append(taker_stock_side(e["side"], e["is_put"]))
            rec["is_put"].append(e["is_put"])
            rec["cls"].append(0 if e["strict_cls"] else 1)
            rec["prem"].append(e["prem"])
            rec["n_prints"].append(e["n_prints"])
            rec["span_ns"].append(e["span_ns"])
            for key in ("match", "match_sens", "match_lag", "placebo_t",
                        "placebo_t_sens", "placebo_s", "placebo_s_sens"):
                rec[key].append(b[key])
            rec["q2s"].append(int(((quotes["t_ns"] >= e["ts_ns"] - DRIFT_PRE_NS)
                                   & (quotes["t_ns"] < e["ts_ns"])).sum()))
            rec["drift"].append(drift_pre(quotes, e["ts_ns"]))
    n_events = len(rec["pred"])
    fault_frac = (n_missing / (n_events + n_missing)
                  if n_events + n_missing else 0.0)
    for ds, t in table.items():
        sub = {"sym": [y for s_, y in zip(rec["session"], rec["sym"])
                       if s_ == ds],
               "ts_ns": [v for s_, v in zip(rec["session"], rec["ts_ns"])
                         if s_ == ds]}
        _assert_table_keys(t["events"], sub)
    # A1-4 placebo-coverage coded assertion: ≥95% of real events must have a
    # loaded window for BOTH offsets (T+60 s rides the real ±300 s window, so
    # its coverage is 1.0 by construction; the alt offset has its own fetch)
    alt_cov = 1.0 - alt_missing / n_events if n_events else 0.0
    assert n_events and alt_cov >= PLACEBO_COVERAGE_MIN, \
        f"alt-session placebo coverage {alt_cov:.3f} < {PLACEBO_COVERAGE_MIN}"

    rates = _rates(rec)
    contam = _contamination_a1(rec)
    placebo = placebo_gate_a1(contam)

    pred_all = np.array(rec["pred"])
    matched = np.array([m["matched"] for m in rec["match"]])
    signable = np.array([m["signable"] for m in rec["match"]])
    obs_all = np.array([m["obs_side"] for m in rec["match"]])
    sess_all = np.array(rec["session"])
    sym_all = np.array(rec["sym"])
    prem_all = np.array(rec["prem"])
    cls_all = np.array(rec["cls"])
    npr_all = np.array(rec["n_prints"])
    span_all = np.array(rec["span_ns"], dtype=np.int64)
    is_put_all = np.array(rec["is_put"])
    q2s_all = np.array(rec["q2s"])
    drift_all = np.array(rec["drift"], dtype=float)
    dt_all = np.array([m["dt_ns"] if m["dt_ns"] is not None else np.nan
                       for m in rec["match"]], dtype=float)
    m100_key = str(A1_SENS_W_NS[1])
    m100 = np.array([m[m100_key]["matched"] for m in rec["match_sens"]])

    # --- A1-8 side-split tripwire: routing recorded BEFORE the a_cal readout
    def _mr(mask):
        return float(matched[mask].mean()) if mask.any() else float("nan")

    side_split = {"cells": {}, "cp_marginal": {
        "call_match_rate": _mr(~is_put_all), "put_match_rate": _mr(is_put_all)}}
    within = {}
    for right, rm in (("call", ~is_put_all), ("put", is_put_all)):
        for lab, pm in (("pred_buy", pred_all == 1),
                        ("pred_sell", pred_all == -1)):
            m_ = rm & pm
            side_split["cells"][f"{right}_{lab}"] = {
                "events": int(m_.sum()), "match_rate": _mr(m_)}
        b_, s_ = _mr(rm & (pred_all == 1)), _mr(rm & (pred_all == -1))
        within[right] = (abs(b_ - s_)
                         if np.isfinite(b_) and np.isfinite(s_)
                         else float("nan"))
    marg_split = abs(_mr(pred_all == 1) - _mr(pred_all == -1))
    fired = np.isfinite(marg_split) and marg_split > TRIPWIRE_DELTA
    surviving = any(np.isfinite(v) and v > TRIPWIRE_DELTA
                    for v in within.values())
    side_split.update({
        "marginal_split": float(marg_split) if np.isfinite(marg_split) else None,
        "within_right_split": {k: (float(v) if np.isfinite(v) else None)
                               for k, v in within.items()},
        "routing": ("investigate" if fired and surviving
                    else "recorded-cp-marginal-explained" if fired
                    else "not-fired"),
        "rule": "routing written before the a_cal readout (A1-8): a split "
                "explained by the C/P marginal is recorded; a split surviving "
                "within-right conditioning routes to investigate",
    })

    # --- A1-2 skew diagnostics + capture, per session (pre-verdict) ---------
    skew, dt_deciles = {}, {}
    cap_num_pre = cap_den_pre = cap_num_post = cap_den_post = 0
    for ds in sorted(set(sess_all)):
        m_ = sess_all == ds
        num = int(matched[m_].sum())
        den = int(m100[m_].sum())
        d = dt_all[m_ & matched]
        d = d[np.isfinite(d)]
        skew[ds] = skew_diagnostics(d, num, den)
        dt_deciles[ds] = ({str(qq): float(np.percentile(d / 1e6, qq))
                           for qq in range(10, 100, 10)} if len(d) else {})
        if ds >= REGIME_BOUNDARY:
            cap_num_post += num
            cap_den_post += den
        else:
            cap_num_pre += num
            cap_den_pre += den
    skew_tripped_sessions = [ds for ds, v in skew.items() if v["tripped"]]

    # --- calibration: mixture + classes + cells ------------------------------
    ok = signable
    pred = pred_all[ok]
    obs = obs_all[ok]
    sess = sess_all[ok]
    sym = sym_all[ok]
    prem = prem_all[ok]
    cls_sig = cls_all[ok]
    npr = npr_all[ok]
    drift = drift_all[ok]
    hit = (pred == obs).astype(float)

    a_cal = agreement(pred, obs)
    kap = kappa(pred, obs)
    lb_m, ub_m, bt_meta = (bootstrap_t_ci(hit, sess) if len(pred)
                           else (float("nan"), float("nan"), {}))
    p = perm_p_within_session(pred, obs, sess, seed=SEED_A1) \
        if len(pred) else float("nan")
    p_name = signflip_p(pred, obs, sym, seed=SEED_A1 + 1) \
        if len(pred) else float("nan")
    name_block_tripwire = bool(np.isfinite(p) and np.isfinite(p_name)
                               and p < 0.01 and p_name >= 0.01)

    edges = (np.percentile(prem, [100 / 3, 200 / 3]) if len(prem) >= 3
             else np.array([np.inf, np.inf]))
    ptc = np.digitize(prem, edges).astype(np.int8)
    cell = (ptc * 2 + cls_sig).astype(np.int8)
    ptc_all = np.digitize(prem_all, edges).astype(np.int8)
    cell_all = (ptc_all * 2 + cls_all).astype(np.int8)

    def _calib(mask):
        n = int(mask.sum())
        if not n:
            return {"n": 0, "a_cal": None}
        lo, hi, meta = bootstrap_t_ci(hit[mask], sess[mask])
        return {"n": n, "a_cal": agreement(pred[mask], obs[mask]),
                "ci": [lo, hi], "ci_method": meta.get("method"),
                "n_sessions": meta.get("n_clusters")}

    by_class = {"strict_unanimous": _calib(cls_sig == 0),
                "non_strict": _calib(cls_sig == 1)}
    by_cell = {f"terc{t}_{['strict', 'nonstrict'][c]}":
               _calib(cell == t * 2 + c)
               for t in (0, 1, 2) for c in (0, 1)}
    by_nprints = {"single_print": _calib(npr == 1),
                  "multi_print": _calib(npr > 1)}

    # --- A1-5 drift terciles (seeded tie-break) ------------------------------
    terc_d = terciles_seeded(drift)
    n_sess_arm = len(set(sess_all))
    by_drift, coverage = {}, {}
    for t in (0, 1, 2):
        m_ = terc_d == t
        by_drift[str(t)] = {"n": int(m_.sum()),
                            "a_cal": agreement(pred[m_], obs[m_])}
        coverage[str(t)] = (len(set(sess[m_])) / n_sess_arm
                            if n_sess_arm else float("nan"))
    low_cov_trip = bool(np.isfinite(coverage["0"]) and coverage["0"] < 0.90)
    zero = drift == 0.0
    zero_block = {
        "n": int(zero.sum()), "share": float(zero.mean()) if len(pred) else None,
        "a_cal": agreement(pred[zero], obs[zero]),
        "nbbo_updates_2s": {
            "mean": float(q2s_all[ok][zero].mean()) if zero.any() else None,
            "median": float(np.median(q2s_all[ok][zero])) if zero.any() else None},
    }
    nz = drift > 0
    a_low = by_drift["0"]["a_cal"]
    a_low_nz = None
    if nz.sum() >= 3:
        terc_nz = terciles_seeded(np.where(nz, drift, np.nan))
        m_ = terc_nz == 0
        a_low_nz = agreement(pred[m_], obs[m_])
    zero_nz_divergence = bool(
        a_low is not None and a_low_nz is not None
        and np.isfinite(a_low) and np.isfinite(a_low_nz)
        and ((a_low - 0.5) * (a_low_nz - 0.5) < 0
             or abs(a_low - a_low_nz) > TRIPWIRE_DELTA))

    # --- A1-2 regime split (registered stratum) ------------------------------
    post_all = sess_all >= REGIME_BOUNDARY
    post_sig = sess >= REGIME_BOUNDARY
    a_pre = agreement(pred[~post_sig], obs[~post_sig])
    a_post = agreement(pred[post_sig], obs[post_sig])
    regime = {
        "boundary": REGIME_BOUNDARY,
        "sessions_pre": int(len({s for s in sess_all if s < REGIME_BOUNDARY})),
        "sessions_post": int(len({s for s in sess_all if s >= REGIME_BOUNDARY})),
        "a_cal_pre": a_pre, "a_cal_post": a_post,
        "match_rate_pre": _mr(~post_all), "match_rate_post": _mr(post_all),
        "capture_pre": cap_num_pre / cap_den_pre if cap_den_pre else None,
        "capture_post": cap_num_post / cap_den_post if cap_den_post else None,
        "transport_assumption_gt_10pt": bool(
            np.isfinite(a_pre) and np.isfinite(a_post)
            and abs(a_post - a_pre) > TRIPWIRE_DELTA),
    }

    # --- A1-8 lag legs: ±20 ms tripwire, ±1 s reported-only ------------------
    lag_report = {}
    lag_trip = False
    for lag in (-LAG_TRIPWIRE_NS, LAG_TRIPWIRE_NS, -LAG_SENS_NS, LAG_SENS_NS):
        lk = str(lag)
        lok = np.array([m[lk]["signable"] for m in rec["match_lag"]])
        lobs = np.array([m[lk]["obs_side"] for m in rec["match_lag"]])
        both = lok & signable
        a_unres = agreement(pred_all[lok], lobs[lok])
        a_int_lag = agreement(pred_all[both], lobs[both])
        a_int_unlag = agreement(pred_all[both], obs_all[both])
        delta = (abs(a_int_lag - a_int_unlag)
                 if np.isfinite(a_int_lag) and np.isfinite(a_int_unlag)
                 else float("nan"))
        tripwire_leg = abs(lag) == LAG_TRIPWIRE_NS
        lag_report[lk] = {
            "role": "tripwire" if tripwire_leg else "reported-only",
            "n_lag_signable": int(lok.sum()),
            "n_intersection": int(both.sum()),
            "a_cal_lag_unrestricted": a_unres,
            "a_cal_lag_intersection": a_int_lag,
            "a_cal_unlagged_intersection": a_int_unlag,
            "delta_intersection": float(delta) if np.isfinite(delta) else None,
        }
        if tripwire_leg and np.isfinite(delta) and delta > TRIPWIRE_DELTA:
            lag_trip = True

    # --- MF-1 disclosures at millisecond width (A1-2) ------------------------
    sz100 = np.array([m[m100_key]["sum_size"] for m in rec["match_sens"]])
    szp = np.array([m["sum_size"] for m in rec["match"]])
    with np.errstate(invalid="ignore", divide="ignore"):
        cap_share = np.where(sz100 > 0, szp / sz100, np.nan)
    nq = np.array([m["n_qual"] for m in rec["match"]])
    cs_sig = cap_share[ok]
    terc_cs = terciles_seeded(cs_sig, seed=SEED_A1 + 25)   # disclosure stratum
    mf1 = {
        "n_qual_eq_1_share": (float((nq[matched] == 1).mean())
                              if matched.any() else None),
        "captured_share_p50": (float(np.nanmedian(cs_sig))
                               if np.isfinite(cs_sig).any() else None),
        "a_cal_by_captured_share_tercile": {
            str(t): {"n": int((terc_cs == t).sum()),
                     "a_cal": agreement(pred[terc_cs == t], obs[terc_cs == t])}
            for t in (0, 1, 2)},
        "named_assumption": "a_cal' measures agreement against a possibly-"
                            "PARTIAL stock leg (A1-2); S_net magnitude is "
                            "never read as leg coverage",
    }
    span_pct = ({str(qq): float(np.percentile(span_all, qq))
                 for qq in (50, 90, 99, 100)} if len(span_all) else {})

    gates = {
        "match_ge_20": bool(rates["match_rate"] >= MATCH_GATE),
        "signable_ge_50": bool(rates["signable_rate"] >= SIGNABLE_GATE),
        "placebo_c_lt_01": placebo["placebo_c_lt_01"],
        "positive_control": placebo["positive_control_100ms_gt_0"],
        "stop_mechanics": bool(fault_frac > STOP_MECHANICS_FRAC),
    }
    verdict = _step1p_verdict(gates, (lb_m, ub_m), p,
                              skew_tripped=bool(skew_tripped_sessions))

    art = {
        "amendment": "References/O_SIGN_VALIDATION_AMENDMENT1_2026-08-05.md",
        "arm": "midpoint / signed [-1,+3] ms (Step 1')",
        "family_ledger": FAMILY_LEDGER,
        "seed": SEED_A1,
        "sessions": [s.isoformat() for s in sample],
        "events": n_events, "fetch_fault_frac": fault_frac,
        "placebo_coverage": {"t60": 1.0, "alt": alt_cov},
        "rates": rates, "contamination": contam, "placebo_law": placebo,
        "gates": gates,
        "match_by_pred_side_decomposed": side_split,
        "skew_diagnostics": skew,
        "skew_tripped_sessions": skew_tripped_sessions,
        "dt_deciles_ms_by_session": dt_deciles,
        "cluster_span_ns_percentiles": span_pct,
        "a_cal_mixture": a_cal, "kappa": kap,
        "a_cal_mixture_ci_bootstrap_t": [lb_m, ub_m],
        "bootstrap_meta": bt_meta,
        "perm_p_two_sided": p, "name_block_signflip_p": p_name,
        "name_block_tripwire": name_block_tripwire,
        "premium_tercile_edges": edges.tolist(),
        "by_class": by_class, "by_cell": by_cell, "by_nprints": by_nprints,
        "by_drift_tercile": by_drift,
        "drift_tercile_session_coverage": coverage,
        "low_drift_coverage_lt_90pct": low_cov_trip,
        "zero_drift_block": zero_block,
        "a_cal_lowest_nonzero_drift": a_low_nz,
        "zero_nonzero_divergence_tripwire": zero_nz_divergence,
        "regime_split": regime,
        "lagged_quote_legs": lag_report,
        "lag_20ms_tripwire": lag_trip,
        "mf1_disclosures": mf1,
        "verdict_provisional": verdict,
        "verdict_note": "rows (iv)-(viii) finalize at the gate on the "
                        "composition-reweighted LB (A1-6(b))",
    }
    save_json(OUT_DIR / "step1p.json", art)
    np.savez(OUT_DIR / "step1p_events_analyzed.npz",
             pred=pred_all, obs=obs_all, sess=sess_all, sym=sym_all,
             prem=prem_all, cls=cls_all, ptc=ptc_all, cell=cell_all,
             n_prints=npr_all, drift=drift_all, dt=dt_all, span=span_all,
             matched=matched, signable=signable, is_put=is_put_all,
             q2s=q2s_all)
    print(json.dumps({k: art[k] for k in
                      ("events", "rates", "a_cal_mixture",
                       "a_cal_mixture_ci_bootstrap_t", "perm_p_two_sided",
                       "kappa", "by_class", "verdict_provisional")},
                     indent=1, default=str))


# --- A1-2/A1-6(b): Step-2 leg metadata (spans + quote-position classes) ------

def s2_meta_session(session: date, rc) -> dict | None:
    """Recompute the frame's Step-2 equal-size pair candidates from the
    SHA-verified session inputs and return, in candidate order, (sym, t0)
    keys + the cluster inter-leg span + the leg-level quote-position class
    (both-strict ⇒ 0, mixed ⇒ 1, both-non-strict ⇒ 2). The frozen frame
    persists neither and no amended stage may edit a frame npz (A1-3); the
    caller aligns candidates to the frame's s2 rows and asserts every row
    resolves."""
    loaded = load_session_fixed(session)
    if loaded is None:
        return None
    defs, fx, ts, iid, size, cond, bidf, askf, didx, jr = loaded
    und = defs.und_code[didx]
    tied = np.isin(cond, list(TIED_CODES))
    if not tied.any():
        return None
    order = np.lexsort((ts[tied], und[tied]))

    def col(a):
        return a[tied][order]

    t_ts, t_iid, t_und = col(ts), col(iid), col(und)
    t_sz = col(size)
    t_strict = col(fx.strict)
    t_def = col(didx)
    new = np.empty(len(t_ts), dtype=bool)
    new[0] = True
    new[1:] = (t_und[1:] != t_und[:-1]) | ((t_ts[1:] - t_ts[:-1]) > W_NS)
    gid = np.cumsum(new) - 1
    n_g = int(gid[-1]) + 1
    o2 = np.lexsort((t_ts, t_iid, gid))
    g2, i2 = gid[o2], t_iid[o2]
    first = np.empty(len(g2), dtype=bool)
    first[0] = True
    first[1:] = (g2[1:] != g2[:-1]) | (i2[1:] != i2[:-1])
    leg_id = np.cumsum(first) - 1
    leg_gid = g2[first]
    leg_sz = np.bincount(leg_id, weights=t_sz[o2].astype(np.float64))
    fidx = np.flatnonzero(first)
    leg_ts = t_ts[o2][fidx]
    leg_def_ = t_def[o2][fidx]
    leg_iid = i2[first]
    l_n = np.bincount(leg_id)
    st = t_strict[o2]
    l_s0 = np.bincount(leg_id, weights=(st == 0).astype(float))
    l_s1 = np.bincount(leg_id, weights=(st == 1).astype(float))
    leg_strict = (l_s0 == l_n) | (l_s1 == l_n)
    legs_per = np.bincount(leg_gid, minlength=n_g)
    _, g_span = cluster_bounds(gid, t_ts)
    pair_g = np.flatnonzero(legs_per == 2)
    a_idx = np.flatnonzero(np.isin(leg_gid, pair_g))[0::2] \
        if len(pair_g) else np.empty(0, dtype=np.int64)
    b_idx = a_idx + 1
    eq = leg_sz[a_idx] == leg_sz[b_idx]
    A, B = a_idx[eq], b_idx[eq]
    swap = leg_iid[A] > leg_iid[B]
    A, B = np.where(swap, B, A), np.where(swap, A, B)
    ds = session.isoformat()
    roots = [str(s) for s in defs.roots]
    ident = np.array([str(rc.resolve(r, ds) or "") for r in roots])
    sym = ident[defs.und_code[leg_def_[A]]]
    t0 = np.minimum(leg_ts[A], leg_ts[B])
    span = g_span[leg_gid[A]]
    cls3 = np.where(leg_strict[A] & leg_strict[B], 0,
                    np.where(leg_strict[A] | leg_strict[B], 1, 2))
    return {"keys": list(zip(sym.tolist(), t0.astype(np.int64).tolist())),
            "span_ns": span.astype(np.int64).tolist(),
            "cls3": [int(c) for c in cls3]}


def stage_s2meta() -> None:
    """Build obligation 15 (+ A1-6(b) composition inputs): persist per-frame-row
    inter-leg spans and leg quote-position classes into s2_leg_meta.json,
    aligned to the frozen frame's s2 arrays with a coded key-match assertion.
    Consumes no fetches and no agreement numbers."""
    assert_frame_shas()
    pool, _ = qualifying_sessions()
    rc = load_classification(ROOT / "signals" / "options_flow"
                             / "root_classification.json")
    out: dict[str, dict] = {}
    for s in pool:
        fr = _load_frame(s)
        if fr is None:
            continue
        meta = s2_meta_session(s, rc)
        if meta is None:
            assert not len(fr["s2_ts_ns"]), f"{s}: frame has s2 rows, recompute none"
            continue
        cand: dict[tuple, list] = defaultdict(list)
        for key, sp, c3 in zip(meta["keys"], meta["span_ns"], meta["cls3"]):
            cand[(key[0], key[1])].append((sp, c3))
        spans, classes = [], []
        for y, t_ in zip(fr["s2_sym"], fr["s2_ts_ns"]):
            lst = cand.get((str(y), int(t_)))
            assert lst, (f"{s}: frame s2 row (sym={y}, ts={t_}) not found in "
                         f"session-input recompute — alignment broken")
            sp, c3 = lst.pop(0)
            spans.append(int(sp))
            classes.append(int(c3))
        out[s.isoformat()] = {"span_ns": spans, "cls3": classes}
        print(f"{s}: {len(spans)} s2 rows aligned")
    save_json(OUT_DIR / "s2_leg_meta.json", {
        "amendment": "References/O_SIGN_VALIDATION_AMENDMENT1_2026-08-05.md",
        "cls3_alphabet": {"0": "both-strict (strict-unanimous)", "1": "mixed",
                          "2": "both-non-strict"},
        "sessions": out,
    })
    stats = step2_span_stats()
    print(json.dumps({"interleg_span": stats}, indent=1))


def step2_span_stats() -> dict:
    """Primary-cell inter-leg span distribution from s2_leg_meta.json."""
    meta = json.loads((OUT_DIR / "s2_leg_meta.json").read_text())["sessions"]
    spans = []
    for ds, m in meta.items():
        fr = _load_frame(date.fromisoformat(ds))
        if fr is None:
            continue
        prim = fr["s2_netd_floor_ok"] & fr["s2_single_print"] & ~fr["s2_ambig"]
        spans.extend(np.asarray(m["span_ns"])[prim].tolist())
    arr = np.array(spans, dtype=np.float64)
    if not len(arr):
        return {"n": 0, "p90_ns": float("nan")}
    return {"n": int(len(arr)),
            "p50_ns": float(np.percentile(arr, 50)),
            "p90_ns": float(np.percentile(arr, 90)),
            "p99_ns": float(np.percentile(arr, 99)),
            "max_ns": float(arr.max())}


def assert_step2_window_ok() -> dict:
    """A1-2 coded precondition — runs BEFORE any Step-2 fetch: if the primary
    cell's p90 inter-leg span exceeds 1.0 ms, the amended window may NOT be
    applied to Step 2 without a further dated amendment. Consumes no fetches
    and no agreement numbers."""
    stats = step2_span_stats()
    p90 = stats["p90_ns"]
    assert np.isfinite(p90) and p90 <= SPAN_P90_MAX_NS, \
        (f"Step-2 primary-cell inter-leg span p90 {p90:.0f} ns > "
         f"{SPAN_P90_MAX_NS} ns — amended window may not be applied to "
         f"Step 2 without a further dated amendment (A1-2)")
    return stats


# --- A1-5 Gate 2 + A1-6(d) â_o ----------------------------------------------

def conjunction_power_realized(units: list[dict], a_cal: float,
                               a_o: float = A_O_DETECT, n_sim: int = GATE_SIM,
                               seed: int = SEED_A1) -> float:
    """Gate 2 (A1-5): realized power — drift terciles come from resampling the
    REALIZED tercile-by-session block structure of the fetched units,
    replacing the pre-run gate's uniform-1/3 in-sim draw (that gate keeps the
    uniform draw; its limitation is registered and disclosed with it).
    `units` = [{"session", "sym", "tercile"}]."""
    rng = np.random.default_rng(seed)
    p_agree = 0.5 + (2 * a_cal - 1) * (a_o - 0.5)
    by_sess: dict[str, list] = defaultdict(list)
    for u in units:
        by_sess[u["session"]].append(u)
    sess_list = sorted(by_sess)
    if not sess_list:
        return 0.0
    passes = 0
    for _ in range(n_sim):
        pick = rng.integers(0, len(sess_list), size=len(sess_list))
        sim = [(f"b{j}", u["sym"], u["tercile"])
               for j, i in enumerate(pick) for u in by_sess[sess_list[i]]]
        n = len(sim)
        if not n:
            continue
        blk = np.array([s[0] for s in sim])
        name = np.array([s[1] for s in sim])
        terc = np.array([s[2] for s in sim])
        hit = (rng.random(n) < p_agree).astype(float)
        if hit.mean() <= 0.5:
            continue
        if _fast_signflip_p(hit, blk, rng) >= 0.01:
            continue
        if _fast_signflip_p(hit, name, rng) >= 0.01:
            continue
        low = terc == 0
        if not low.any() or hit[low].mean() <= 0.5:
            continue
        if _fast_signflip_p(hit[low], blk[low], rng) >= 0.05:
            continue
        k_multi = max(1, n // 3)
        m_pred = rng.integers(0, 2, size=k_multi) * 2 - 1
        m_obs = np.where(rng.random(k_multi) < p_agree, m_pred, -m_pred)
        if (m_pred == m_obs).mean() <= 0.5:
            continue
        passes += 1
    return passes / n_sim


def _fieller_ao(s1_hit: np.ndarray, s1_sess: np.ndarray, u_hit: np.ndarray,
                u_sess: np.ndarray, z_conf: float = 1.959963984540054) -> dict:
    """Fieller interval for â_o = 0.5 + (agr − 0.5)/(2·a_cal − 1) — the A1-6(d)
    fallback when the joint bootstrap is INVALID. Session-jackknife
    (co)variances over the union of contributing sessions."""
    sessions = sorted(set(s1_sess.tolist()) | set(u_sess.tolist()))
    g0 = float(u_hit.mean()) - 0.5
    h0 = 2.0 * float(s1_hit.mean()) - 1.0
    gs, hs = [], []
    for s in sessions:
        m1, m2 = s1_sess != s, u_sess != s
        if not m1.any() or not m2.any():
            continue
        gs.append(float(u_hit[m2].mean()) - 0.5)
        hs.append(2.0 * float(s1_hit[m1].mean()) - 1.0)
    m = len(gs)
    if m < 2:
        return {"ok": False, "reason": "too few jackknife sessions"}
    ga, ha = np.array(gs), np.array(hs)
    fac = (m - 1) / m
    vg = fac * ((ga - ga.mean()) ** 2).sum()
    vh = fac * ((ha - ha.mean()) ** 2).sum()
    cgh = fac * ((ga - ga.mean()) * (ha - ha.mean())).sum()
    t2 = z_conf ** 2
    aa = h0 ** 2 - t2 * vh
    bb = -2 * (g0 * h0 - t2 * cgh)
    cc = g0 ** 2 - t2 * vg
    disc = bb ** 2 - 4 * aa * cc
    if aa <= 0 or disc < 0:
        return {"ok": False,
                "reason": "unbounded — denominator not significantly nonzero"}
    r1 = float((-bb - np.sqrt(disc)) / (2 * aa))
    r2 = float((-bb + np.sqrt(disc)) / (2 * aa))
    lo, hi = sorted((r1, r2))
    return {"ok": True, "a_o_ci": [0.5 + lo, 0.5 + hi]}


def _joint_ao_ci_a1(prim: list[dict], pool: list[date],
                    n_boot: int = 2_000, seed: int = SEED_A1) -> dict:
    """A1-6(d): â_o joint bootstrap AS THE CODE IMPLEMENTS IT — one draw =
    resample the FULL qualifying-session list with replacement; a_cal′ on the
    Step-1′ sessions appearing in the draw, unit agreement on the Step-2
    sessions appearing in the draw. Dropped (a_cal ≤ 0.5) and truncated draw
    counts PUBLISH; either > 1% ⇒ the interval is INVALID and Fieller is
    reported instead. â_o stays reported-only, an upper bound."""
    z = np.load(OUT_DIR / "step1p_events_analyzed.npz", allow_pickle=False)
    sig = z["signable"].astype(bool)
    s1_hit = (z["pred"] == z["obs"]).astype(float)[sig]
    s1_sess = z["sess"][sig].astype(str)
    rng = np.random.default_rng(seed + 4)
    sessions = np.array([s.isoformat() for s in pool])
    p_sess = np.array([r["session"] for r in prim])
    p_hit = np.array([float(r["pred"] == r["obs"]) for r in prim])
    vals: list[float] = []
    dropped = truncated = 0
    for _ in range(n_boot):
        pick = rng.choice(sessions, size=len(sessions), replace=True)
        h1 = np.concatenate([s1_hit[s1_sess == s] for s in pick]) \
            if len(s1_hit) else np.empty(0)
        h2 = np.concatenate([p_hit[p_sess == s] for s in pick]) \
            if len(p_hit) else np.empty(0)
        if not len(h1) or not len(h2) or h1.mean() <= 0.5:
            dropped += 1
            continue
        raw = 0.5 + (h2.mean() - 0.5) / (2 * h1.mean() - 1)
        if raw < 0.0 or raw > 1.0:
            truncated += 1
        vals.append(min(1.0, max(0.0, raw)))
    invalid = dropped > 0.01 * n_boot or truncated > 0.01 * n_boot
    out = {"n_boot": n_boot, "dropped_acal_le_half": dropped,
           "truncated_to_unit_interval": truncated,
           "invalid_gt_1pct": bool(invalid),
           "ci_joint_bootstrap": ([float(np.percentile(vals, 2.5)),
                                   float(np.percentile(vals, 97.5))]
                                  if vals else None),
           "reported": "fieller" if invalid else "joint_bootstrap"}
    if invalid:
        out["fieller"] = _fieller_ao(s1_hit, s1_sess, p_hit, p_sess)
    return out


# =============================================================================
# CLI
# =============================================================================

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("stage", choices=["frame", "verify", "fetch1", "step1",
                                      "diag_fetch", "diag", "pilot_fetch",
                                      "pilot", "gate", "fetch2", "step2",
                                      "record_shas", "s2meta", "fetch1p",
                                      "step1p"])
    ap.add_argument("--limit", type=int, default=None,
                    help="session limit (frame/fetch smoke runs)")
    args = ap.parse_args()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    {
        "frame": lambda: stage_frame(args.limit),
        "verify": lambda: stage_verify(args.limit),
        "fetch1": lambda: stage_fetch1(args.limit),
        "diag_fetch": stage_diag_fetch,
        "diag": stage_diag,
        "step1": stage_step1,
        "pilot_fetch": stage_pilot_fetch,
        "pilot": stage_pilot,
        "gate": stage_gate,
        "fetch2": lambda: stage_fetch2(args.limit),
        "step2": stage_step2,
        "record_shas": record_frame_shas,
        "s2meta": stage_s2meta,
        "fetch1p": lambda: stage_fetch1p(args.limit),
        "step1p": stage_step1p,
    }[args.stage]()


if __name__ == "__main__":
    main()
