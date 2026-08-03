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
        sample = [blocks[i] for i in rng.integers(0, len(blocks), size=k)]
        if conjunction_power(sample, keep_rate, a_cal, seed=seed + k) >= POWER_TARGET:
            return max(1, round(k * keep_rate))
    return 10 ** 9   # unreachable at any tested scale


# =============================================================================
# frame stage — the fixed loader + per-session event tables
# =============================================================================

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


def frame_session(session: date, rc) -> dict | None:
    """Build one session's Step-1 + Step-2 event tables; save npz; return
    counts + file SHAs for the manifest."""
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
    # same underlying whose PRIMARY window overlaps (|Δt0| ≤ 2×100 ms)
    ambig = np.zeros(n_g, dtype=bool)
    ord_g = np.lexsort((g_ts, g_und))
    su, st_ = g_und[ord_g], g_ts[ord_g]
    same_prev = np.r_[False, (su[1:] == su[:-1])
                      & ((st_[1:] - st_[:-1]) <= 2 * PRIMARY_W_NS)]
    amb_sorted = same_prev | np.r_[same_prev[1:], False]
    ambig[ord_g] = amb_sorted

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

    FRAME_DIR.mkdir(parents=True, exist_ok=True)
    save_npz(FRAME_DIR / f"{ds}.npz",
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
        frame_session(session, rc)                 # rewrites the npz in place
        with np.load(FRAME_DIR / f"{ds}.npz", allow_pickle=False) as z:
            rebuilt = {k: z[k].copy() for k in z.files}
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
                  label: str) -> None:
    client, url = _client()
    daily_path = OUT_DIR / "daily_volume.json"
    daily = json.loads(daily_path.read_text()) if daily_path.exists() else {}
    ledger = {"label": label, "events": 0, "fetched": 0, "fault": 0,
              "placebo_fault": 0, "faults": []}
    for session in sessions:
        fr = _load_frame(session)
        if fr is None:
            print(f"{session}: no frame — run stage frame first")
            continue
        evs = events_of(fr)
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

    def events_of(fr):
        keep = ~fr["s1_ambig"]
        idx = np.flatnonzero(keep)
        sel = idx[_stratified_cap(fr["s1_prem"][idx])]
        return fr["s1_sym"][sel], fr["s1_ts_ns"][sel]

    _fetch_events(sample, pool, events_of, "step1")


def _s2_events(fr):
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
    pool, _ = qualifying_sessions()
    picks = pilot_pick(pool)
    save_json(OUT_DIR / "pilot_sessions.json",
              {"sessions": [s.isoformat() for s in picks]})
    print("pilot sessions:", [s.isoformat() for s in picks])
    _fetch_events(picks, pool, _s2_events, "pilot")


def stage_fetch2(limit: int | None = None) -> None:
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


def analyze_events(sessions: list[date], pool: list[date], events_of,
                   pred_of) -> dict:
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
            m = match_event(trades, quotes, int(t_), PRIMARY_W_NS)
            m_sens = {w: match_event(trades, quotes, int(t_), w)
                      for w in SENS_W_NS}
            m_lag = {lag: match_event(trades, quotes, int(t_), PRIMARY_W_NS,
                                      lag_ns=lag)
                     for lag in (-LAG_SENS_NS, LAG_SENS_NS)}
            plc_t = match_event(trades, quotes, int(t_) + PLACEBO_OFFSET_NS,
                                PRIMARY_W_NS)
            plc_t_sens = {w: match_event(trades, quotes,
                                         int(t_) + PLACEBO_OFFSET_NS, w)
                          for w in SENS_W_NS}
            t_alt = alt_base + (int(t_) - base)
            win_alt = _load_window(str(s_), t_alt)
            plc_s = match_event(*win_alt, t_alt, PRIMARY_W_NS) \
                if win_alt else None
            plc_s_sens = {w: match_event(*win_alt, t_alt, w)
                          for w in SENS_W_NS} if win_alt else {}
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

    rec = analyze_events(picks, pool, events_of, pred_of)

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
    art = {
        "sessions": [s.isoformat() for s in picks],
        "rates": _rates(rec), "contamination": _contamination(rec),
        "units": {"n": len(units),
                  "events_per_unit": sorted(units.values(), reverse=True)},
        "dt_ns_percentiles": {q: float(np.percentile(dts, q))
                              for q in (10, 50, 90)} if dts else {},
        "match_by_pred_side": by_side,
        "sealed_observed_sha256": sha,
        "label_blind": "per-event observed sides in pilot_observed_sealed.json"
                       " ONLY; no gate code reads it",
    }
    save_json(OUT_DIR / "pilot.json", art)
    print(json.dumps({k: art[k] for k in ("sessions", "rates", "units")},
                     indent=1, default=str))


def stage_gate() -> None:
    pilot = json.loads((OUT_DIR / "pilot.json").read_text())
    step1 = json.loads((OUT_DIR / "step1.json").read_text())
    pool, _ = qualifying_sessions()
    blocks = _frame_unit_blocks(pool)

    keep = pilot["rates"]["match_rate"] * pilot["rates"]["signable_rate"]
    a_lb = step1["a_cal_ci_session_bootstrap"][0]

    # registered coded check: Step-2 premium range ⊆ Step-1 calibrated range
    s1 = np.load(OUT_DIR / "step1_events.npz")
    s1_lo, s1_hi = float(s1["prem"].min()), float(s1["prem"].max())
    s2_prems = np.concatenate([np.asarray(b["premiums"]) for b in blocks])
    range_ok = bool((s2_prems.min() >= s1_lo) & (s2_prems.max() <= s1_hi))

    proj_units = round(len(blocks) * keep)
    nreq = n_required(blocks, keep, a_lb)
    power = conjunction_power(blocks, keep, a_lb)
    stress = conjunction_power(blocks, keep / 2.0, max(0.5, a_lb - 0.05),
                               seed=SEED + 7)
    assert stress <= power + 0.05, "stressed power must not exceed unstressed"

    names = len({b["symbol"] for b in blocks})
    sess = len({b["session"] for b in blocks})
    passed = (range_ok and proj_units >= nreq and power >= POWER_TARGET
              and stress >= STRESS_POWER_MIN and names >= FLOOR_UNDERLYINGS
              and sess >= FLOOR_SESSIONS)
    art = {
        "keep_rate_pilot": keep, "a_cal_lb": a_lb,
        "projected_units": proj_units, "n_required": nreq,
        "power": power, "power_stressed": stress,
        "premium_range_ok": range_ok,
        "distinct_underlyings": names, "sessions_with_units": sess,
        "floors": {"n_required": nreq, "underlyings": FLOOR_UNDERLYINGS,
                   "sessions": FLOOR_SESSIONS},
        "passed": passed,
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
    pool, _ = qualifying_sessions()
    gate = json.loads((OUT_DIR / "gate.json").read_text())
    if not gate["passed"]:
        print("gate not passed — step2 may not run (registered ladder governs)")
        return
    step1 = json.loads((OUT_DIR / "step1.json").read_text())
    if step1["verdict"] != "LIVE":
        print(f"Step 1 verdict {step1['verdict']} — step2 may not run")
        return

    def events_of(fr):
        keep = fr["s2_netd_floor_ok"] & ~fr["s2_ambig"]
        idx = np.flatnonzero(keep)
        if not len(idx):
            return None
        return idx, fr["s2_sym"][idx], fr["s2_ts_ns"][idx]

    def pred_of(fr, j):
        return -int(fr["s2_sigma"][j])

    rec = analyze_events(pool, pool, events_of, pred_of)
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

    def infer(rows, seed_off=0):
        if not rows:
            return {"n_units": 0}
        pr = np.array([r["pred"] for r in rows])
        ob = np.array([r["obs"] for r in rows])
        ss = np.array([r["session"] for r in rows])
        nm = np.array([r["sym"] for r in rows])
        agr = agreement(pr, ob)
        p_sess = signflip_p(pr, ob, ss, seed=SEED + seed_off)
        p_name = signflip_p(pr, ob, nm, seed=SEED + 1 + seed_off)
        dr = np.array([r["drift"] for r in rows])
        terc = _terciles(dr)
        low = terc == 0
        p_low = signflip_p(pr[low], ob[low], ss[low], seed=SEED + 2 + seed_off) \
            if low.sum() >= 2 else float("nan")
        return {"n_units": len(rows), "agreement": agr,
                "kappa": kappa(pr, ob),
                "direction": int(np.sign(agr - 0.5)),
                "p_session": p_sess, "p_name_block": p_name,
                "low_drift": {"n": int(low.sum()),
                              "agreement": agreement(pr[low], ob[low]),
                              "p": p_low},
                "by_drift_tercile": {str(t): agreement(pr[terc == t], ob[terc == t])
                                     for t in (0, 1, 2)}}

    inf_p = infer(prim)
    inf_m = infer(multi, seed_off=10)

    # Gate 2 — realized power, 3.3 Amendment-1 semantics
    r2 = _rates(rec)
    keep_real = r2["match_rate"] * r2["signable_rate"]
    if not np.isfinite(keep_real):
        keep_real = 0.0
    blocks = _frame_unit_blocks(pool)
    a_lb = step1["a_cal_ci_session_bootstrap"][0]
    realized_power = conjunction_power(blocks, keep_real, a_lb, seed=SEED + 3)

    verdict = _step2_verdict(inf_p, inf_m, realized_power, fault_frac)

    # â_o (REPORTED-ONLY, upper bound) + joint session bootstrap CI
    a_cal = step1["a_cal"]
    agr = inf_p.get("agreement", float("nan"))
    a_o_hat = 0.5 + (agr - 0.5) / (2 * a_cal - 1) if a_cal and a_cal != 0.5 \
        else float("nan")
    a_o_ci = _joint_ao_ci(prim, pool) if prim else (float("nan"), float("nan"))

    per_name = defaultdict(lambda: [0, 0])
    for r in prim:
        per_name[r["sym"]][0] += 1
        per_name[r["sym"]][1] += int(r["pred"] == r["obs"])

    art = {
        "prereg": "References/O_SIGN_VALIDATION_PREREG_2026-08-03.md",
        "scope": "TLET-family ('n') tied 2-leg in-band spread-edge packages",
        "events": n_events, "fetch_fault_frac": fault_frac,
        "rates": _rates(rec), "contamination": _contamination(rec),
        "primary": inf_p, "multi_print_coprimary": inf_m,
        "gate2": {"keep_rate_realized": keep_real,
                  "realized_power": realized_power},
        "verdict": verdict,
        "a_o_hat_upper_bound_reported_only": a_o_hat,
        "a_o_hat_ci_joint_bootstrap": a_o_ci,
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
# CLI
# =============================================================================

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("stage", choices=["frame", "verify", "fetch1", "step1",
                                      "pilot_fetch", "pilot", "gate", "fetch2",
                                      "step2"])
    ap.add_argument("--limit", type=int, default=None,
                    help="session limit (frame/fetch smoke runs)")
    args = ap.parse_args()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    {
        "frame": lambda: stage_frame(args.limit),
        "verify": lambda: stage_verify(args.limit),
        "fetch1": lambda: stage_fetch1(args.limit),
        "step1": stage_step1,
        "pilot_fetch": stage_pilot_fetch,
        "pilot": stage_pilot,
        "gate": stage_gate,
        "fetch2": lambda: stage_fetch2(args.limit),
        "step2": stage_step2,
    }[args.stage]()


if __name__ == "__main__":
    main()
