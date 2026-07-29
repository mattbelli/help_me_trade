"""Roadmap 1.13: the preceding-flow profile — movers vs their matched controls.

Registered 2026-07-29 (FLOW_ROADMAP.md 1.13) BEFORE this ran. The first look at
mover flow. Discovery (1c): every number is reported, no pass/fail; the reading
rule (fixed in the registration): a difference is a FINDING only if its CI is
clear of zero, repeats across >=2 window lengths, replicates in direction in the
raw arm, and survives the liquidity split. ~130 primary-arm intervals => ~6-7
false positives expected at 95% by chance; lone hits are noise-compatible.

Primary statistic per (feature, window, sign): exceedance-share DIFFERENCE —
share of mover windows at/beyond the FROZEN pooled control p90 (upper) / p10
(lower) from 1.12, minus the same share among matched controls. Both tails,
every feature. 95% CI by bootstrap over event SESSIONS (10,000 draws, one
shared resampling per draw, SEED=20260731). NA shares and presence are their
own comparisons. Chase-adjusted tilt (1.11b follow-on): window_tilt minus
b x premium-weighted mean prev-day excess z; b fit on unique CONTROL name-days
ONLY, and its thresholds (plus the secondary p1 color, absent from the 1.12
freeze) come from control windows computed BEFORE any mover value — the
control-first code order in main() is the enforcement.

Windows end t-1 <= 2026-05-28: training flow only, test split untouched.
Output: tables + data/research/mover_profile_113.json.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools.mover_controls import (  # noqa: E402
    ACTIVE_FLOOR,
    CORPUS_DB,
    WINDOWS,
    _bars,
    _flow_features,
    _tier_quintiles,
    _window_features,
    _z_scores,
)

IN_JSON = ROOT / "data" / "research" / "mover_controls.json"
OUT = ROOT / "data" / "research" / "mover_profile_113.json"
N_BOOT = 10_000
SEED = 20260731
FEATURES = ("active_days", "window_tilt", "chase_adj_tilt", "max_run",
            "largest_print_ratio", "concentration", "call_share",
            "short_dte_share", "otm_share", "multileg_share")


def _universe():
    import sqlite3

    from db.flow_corpus import INDEX_ROOTS
    from tools.multileg_arm_probe import ETF_SET
    fence = set(INDEX_ROOTS) | ETF_SET
    con = sqlite3.connect(f"file:{CORPUS_DB}?mode=ro", uri=True)
    names = sorted(
        r[0] for r in con.execute("SELECT DISTINCT underlying FROM flow_rollup")
        if r[0] not in fence)
    con.close()
    return names


def _chase_slope(ctrl_days, d_tilt, x_prev) -> tuple[float, int]:
    """OLS slope of daily tilt on prev-day excess z over unique control
    name-days that are active with finite x. Controls only — untainted."""
    ii = np.array([i for i, _ in ctrl_days])
    tt = np.array([t for _, t in ctrl_days])
    y, x = d_tilt[ii, tt], x_prev[ii, tt]
    ok = np.isfinite(y) & np.isfinite(x)
    y, x = y[ok], x[ok]
    b = float(np.cov(x, y)[0, 1] / np.var(x)) if len(x) > 1 else 0.0
    return b, int(ok.sum())


def _chase_feature(i, t, W, F, x_prev, wt, b):
    """window_tilt - b * premium-weighted mean prev-day excess z (available-x)."""
    if wt is None:
        return None
    lo = t - W
    cls = F["cls"][i, lo:t]
    x = x_prev[i, lo:t]
    ok = (cls > 0) & np.isfinite(x)
    if not ok.any():
        return None
    return float(wt - b * np.average(x[ok], weights=cls[ok]))


def main() -> None:
    data = json.loads(IN_JSON.read_text())
    frozen = data["control_base_rates"]
    universe = _universe()
    assert len(universe) == data["meta"]["universe_size"], "universe drifted"
    ni = {n: i for i, n in enumerate(universe)}
    spy_dates, H, L, C, V, spy_c = _bars(universe)
    di = {d: t for t, d in enumerate(spy_dates)}
    z_res, _z_raw = _z_scores(H, L, C, spy_c)
    quint = _tier_quintiles(C, V)
    F, pmax = _flow_features(universe, spy_dates)

    with np.errstate(invalid="ignore", divide="ignore"):
        d_tilt = np.where(F["cls"] >= ACTIVE_FLOOR,
                          (2 * F["bull"] - F["cls"]) / F["cls"], np.nan)
    x_prev = np.concatenate(
        [np.full((len(universe), 1), np.nan), z_res[:, :-1]], axis=1)

    episodes = data["episodes"]
    ctrl_days = sorted({(ni[c["name"]], di[e["date"]])
                       for e in episodes for c in e["controls"]})

    # ---- CONTROLS FIRST (code-order enforcement of the registration) ----
    b, n_fit = _chase_slope(ctrl_days, d_tilt, x_prev)
    print(f"chase slope b = {b:+.5f} (fit on {n_fit} active control name-days "
          f"with finite prev-day z)")

    def feats_for(i, t):
        out = {}
        for W in WINDOWS:
            f = _window_features(i, t, W, F, pmax)
            f["chase_adj_tilt"] = _chase_feature(
                i, t, W, F, x_prev, f["window_tilt"], b)
            out[W] = f
        return out

    ctrl_feats = {(i, t): feats_for(i, t) for i, t in ctrl_days}
    # runtime thresholds computed on control windows only, pre-mover:
    # chase_adj_tilt p10/p90 (feature absent from the 1.12 freeze) + p1 color
    thr, p1 = {}, {}
    for W in WINDOWS:
        thr[W] = {}
        for feat in FEATURES:
            if feat == "chase_adj_tilt":
                vals = np.array([f[W][feat] for f in ctrl_feats.values()
                                 if f[W][feat] is not None], float)
                thr[W][feat] = {"p10": float(np.percentile(vals, 10)),
                                "p90": float(np.percentile(vals, 90)),
                                "p99": float(np.percentile(vals, 99))}
            else:
                thr[W][feat] = frozen[str(W)][feat]
            vals = np.array([f[W][feat] for f in ctrl_feats.values()
                             if f[W][feat] is not None], float)
            p1[(W, feat)] = float(np.percentile(vals, 1)) if len(vals) else None

    # ---- movers read here, under the committed registration ----
    mover_feats = {}
    for e in episodes:
        key = (ni[e["name"]], di[e["date"]])
        if key not in mover_feats:
            mover_feats[key] = feats_for(*key)

    sessions = sorted({e["date"] for e in episodes})
    si = {d: k for k, d in enumerate(sessions)}
    S = len(sessions)

    # per-stat, per-session sums: stat value in a bootstrap draw =
    # (w@num_m)/(w@den_m) - (w@num_c)/(w@den_c)
    stats, cols = [], []   # cols: (num_m, den_m, num_c, den_c) each [S]

    def add_stat(meta, rows):
        """rows: (session_idx, m_ind, m_val, c_ind, c_val) per episode; *_ind
        counts toward numerator, *_val toward denominator (NaN = excluded)."""
        arr = np.zeros((4, S))
        for k, mi, mv, ci, cv in rows:
            arr[0, k] += mi
            arr[1, k] += mv
            arr[2, k] += ci
            arr[3, k] += cv
        stats.append(meta)
        cols.append(arr)

    def tail_rows(subset, W, feat, tail, cut):
        rows = []
        for e in subset:
            k = si[e["date"]]
            mv = mover_feats[(ni[e["name"]], di[e["date"]])][W][feat]
            m_ind = m_val = 0.0
            if mv is not None:
                m_val = 1.0
                m_ind = float(mv >= cut if tail == "hi" else mv <= cut)
            c_ind = c_val = 0.0
            for c in e["controls"]:
                cv = ctrl_feats[(ni[c["name"]], di[e["date"]])][W][feat]
                if cv is not None:
                    c_val += 1.0
                    c_ind += float(cv >= cut if tail == "hi" else cv <= cut)
            rows.append((k, m_ind, m_val, c_ind, c_val))
        return rows

    for arm in ("residual", "raw"):
        for sign in ("rise", "fall"):
            subset = [e for e in episodes
                      if e["arm"] == arm and e["sign"] == sign]
            for W in WINDOWS:
                rows = []
                for e in subset:
                    k = si[e["date"]]
                    m = mover_feats[(ni[e["name"]], di[e["date"]])][W]
                    c_ind = c_val = 0.0
                    for c in e["controls"]:
                        cf = ctrl_feats[(ni[c["name"]], di[e["date"]])][W]
                        c_val += 1.0
                        c_ind += float(cf["active_days"] >= 1)
                    rows.append((k, float(m["active_days"] >= 1), 1.0,
                                 c_ind, c_val))
                add_stat({"arm": arm, "sign": sign, "W": W,
                          "feature": "presence", "tail": "hi",
                          "thr": ">=1 active day"}, rows)
                for feat in FEATURES:
                    for tail, q in (("hi", "p90"), ("lo", "p10")):
                        cut = thr[W][feat].get(q)
                        if cut is None:
                            continue
                        add_stat({"arm": arm, "sign": sign, "W": W,
                                  "feature": feat, "tail": tail, "thr": cut},
                                 tail_rows(subset, W, feat, tail, cut))
                    # NA-share comparison
                    rows = []
                    for e in subset:
                        k = si[e["date"]]
                        mv = mover_feats[(ni[e["name"]], di[e["date"]])][W][feat]
                        c_ind = c_val = 0.0
                        for c in e["controls"]:
                            cv = ctrl_feats[(ni[c["name"]], di[e["date"]])][W][feat]
                            c_val += 1.0
                            c_ind += float(cv is None)
                        rows.append((k, float(mv is None), 1.0, c_ind, c_val))
                    add_stat({"arm": arm, "sign": sign, "W": W,
                              "feature": feat, "tail": "na", "thr": None}, rows)

    # liquidity split (residual arm): presence + largest_print_ratio tails
    for sign in ("rise", "fall"):
        subset = [e for e in episodes
                  if e["arm"] == "residual" and e["sign"] == sign]
        unknown_q = sum(1 for e in subset
                        if quint[ni[e["name"]], di[e["date"]]] < 0)
        print(f"liquidity split {sign}: {unknown_q} episodes with unknown "
              f"quintile excluded (counted, never silent)")
        for q in range(5):
            sub_q = [e for e in subset
                     if quint[ni[e["name"]], di[e["date"]]] == q]
            for W in WINDOWS:
                rows = []
                for e in sub_q:
                    k = si[e["date"]]
                    m = mover_feats[(ni[e["name"]], di[e["date"]])][W]
                    c_ind = c_val = 0.0
                    for c in e["controls"]:
                        cf = ctrl_feats[(ni[c["name"]], di[e["date"]])][W]
                        c_val += 1.0
                        c_ind += float(cf["active_days"] >= 1)
                    rows.append((k, float(m["active_days"] >= 1), 1.0,
                                 c_ind, c_val))
                add_stat({"arm": "residual", "sign": sign, "W": W,
                          "feature": "presence", "tail": "hi", "quintile": q,
                          "thr": ">=1 active day", "n_episodes": len(sub_q)},
                         rows)
                for tail, qq in (("hi", "p90"), ("lo", "p10")):
                    cut = thr[W]["largest_print_ratio"][qq]
                    add_stat({"arm": "residual", "sign": sign, "W": W,
                              "feature": "largest_print_ratio", "tail": tail,
                              "quintile": q, "thr": cut,
                              "n_episodes": len(sub_q)},
                             tail_rows(sub_q, W, "largest_print_ratio",
                                       tail, cut))

    # ---- point estimates + session bootstrap (one shared resampling/draw)
    K = len(stats)
    A = np.stack(cols)                     # [K, 4, S]
    with np.errstate(invalid="ignore", divide="ignore"):
        tot = A.sum(axis=2)                # [K, 4]
        m_share = tot[:, 0] / tot[:, 1]
        c_share = tot[:, 2] / tot[:, 3]
    rng = np.random.default_rng(SEED)
    flatA = A.reshape(K * 4, S)
    Wmat = np.empty((N_BOOT, S))
    for bidx in range(N_BOOT):
        Wmat[bidx] = np.bincount(rng.integers(0, S, size=S), minlength=S)
    agg = (Wmat @ flatA.T).reshape(N_BOOT, K, 4)
    with np.errstate(invalid="ignore", divide="ignore"):
        diffs = agg[:, :, 0] / agg[:, :, 1] - agg[:, :, 2] / agg[:, :, 3]
    lo, hi = np.nanpercentile(diffs, [2.5, 97.5], axis=0)

    out_rows = []
    for k, meta in enumerate(stats):
        row = dict(meta)
        row.update({
            "mover_share": None if not np.isfinite(m_share[k]) else round(float(m_share[k]), 4),
            "ctrl_share": None if not np.isfinite(c_share[k]) else round(float(c_share[k]), 4),
            "diff": None if not (np.isfinite(m_share[k]) and np.isfinite(c_share[k])) else round(float(m_share[k] - c_share[k]), 4),
            "ci": [round(float(lo[k]), 4), round(float(hi[k]), 4)],
            "n_mover": int(cols[k][1].sum()), "n_ctrl": int(cols[k][3].sum()),
        })
        out_rows.append(row)

    # ---- report: primary arm first, findings-candidates flagged
    def fmt(row):
        d, (clo, chi) = row["diff"], row["ci"]
        star = " *" if d is not None and (clo > 0 or chi < 0) else ""
        thr_s = (f"{row['thr']:.3g}" if isinstance(row["thr"], (int, float))
                 else str(row["thr"]))
        return (f"  {row['feature']:<20} {row['tail']:<3} thr={thr_s:<12} "
                f"mover={row['mover_share']} ctrl={row['ctrl_share']} "
                f"diff={d:+.4f} [{clo:+.4f},{chi:+.4f}]{star}"
                if d is not None else
                f"  {row['feature']:<20} {row['tail']:<3} (no valid windows)")

    for arm in ("residual", "raw"):
        for sign in ("rise", "fall"):
            for W in WINDOWS:
                print(f"\n== {arm} / {sign} / {W}d ==")
                for row in out_rows:
                    if (row["arm"], row["sign"], row["W"]) == (arm, sign, W) \
                            and "quintile" not in row:
                        print(fmt(row))
    print("\n== liquidity split (residual arm) ==")
    for row in out_rows:
        if "quintile" in row:
            print(f" q{row['quintile']} {row['sign']:<4} {row['W']:>2}d "
                  f"n={row['n_episodes']:<5}" + fmt(row))

    OUT.write_text(json.dumps({
        "meta": {"registered": "2026-07-29", "seed": SEED, "n_boot": N_BOOT,
                 "chase_slope_b": b, "chase_fit_n": n_fit,
                 "chase_thresholds": {str(W): thr[W]["chase_adj_tilt"]
                                      for W in WINDOWS},
                 "p1_color": {f"{W}:{f}": v for (W, f), v in p1.items()},
                 "n_sessions": S},
        "stats": out_rows}, indent=1))
    print(f"\nwrote {OUT}")


if __name__ == "__main__":
    main()
