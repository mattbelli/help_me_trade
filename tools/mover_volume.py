"""Roadmap 1.13b: equity-volume expansion in the preceding-window profile —
movers vs their frozen matched controls.

Registered 2026-07-29 (FLOW_ROADMAP.md 1.13b) BEFORE this ran. Backwards frame
(1c law): did the stock's own dollar volume expand inside the pre-move window,
did it expand in the sessions FOLLOWING the window's largest flow day, does the
flow burst LEAD the volume peak, and does flow-signature-plus-volume-expansion
(two_part) discriminate movers from controls better than the flow signature
alone? Baseline B = the name's own PRE-window 60d median dollar volume, so
expansion cannot leak into its own yardstick.

Statistics exactly as 1.13: exceedance-share differences at control p90/p10
(both tails for the ratio features), single shares for the indicators, NA
shares reported. flow_sig uses the FROZEN 1.12 largest_print_ratio p90; the
new volume features' thresholds cannot come from the freeze and are computed
on control windows FIRST at run start, before any mover value — the
controls-first code order in main() is the enforcement. Session-cluster
bootstrap, 10,000 draws, one shared resampling, SEED=20260803. 60 residual
intervals => ~3 expected false positives; reading rule: CI clear + >=2 windows
+ raw replication + liquidity split; "two-part foreshadows better" additionally
requires the two_part mover/control ratio to beat flow_only at the same
windows.

Registered conservative bias: control matching used trailing-20d median dv
over t-20..t-1, overlapping the windows (fully at 20d) — genuine mover
expansion is partially matched away, so the test leans TOWARD null and a 20d
null is NOT evidence of absence.

Windows end t-1 <= 2026-05-28: training only, test split untouched.
Output: tables + data/research/mover_volume_113b.json.
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
    WINDOWS,
    _bars,
    _flow_features,
    _tier_quintiles,
    _window_features,
)
from tools.mover_profile import _universe  # noqa: E402

IN_JSON = ROOT / "data" / "research" / "mover_controls.json"
OUT = ROOT / "data" / "research" / "mover_volume_113b.json"
N_BOOT = 10_000
SEED = 20260803
RATIO_FEATS = ("vol_expansion", "vol_max_ratio", "post_flow_vol")
BATTERY = ("two_part", "flow_only", "vol_only")


def _vol_feats(i: int, t: int, W: int, dv, F, pmax) -> dict:
    """Raw window values; the two-part indicators are derived later from lpr
    and vol_expansion once the control thresholds exist."""
    lo = t - W
    w = dv[i, lo:t]
    fin = np.isfinite(w)
    hist = dv[i, max(0, t - W - 60):lo]
    hist = hist[np.isfinite(hist)]
    B = float(np.median(hist)) if len(hist) >= 20 else None
    out = {"vol_expansion": None, "vol_max_ratio": None,
           "post_flow_vol": None, "flow_leads_vol": None, "lpr": None}
    if B is not None and B > 0 and fin.any():
        out["vol_expansion"] = float(w[fin].mean() / B)
        out["vol_max_ratio"] = float(np.nanmax(w) / B)
    cls = F["cls"][i, lo:t]
    if (cls >= ACTIVE_FLOOR).any():
        jmax = int(np.argmax(cls))   # the largest flow day is active if any is
        if B is not None and B > 0 and jmax < W - 1:
            post = w[jmax + 1:]
            pfin = np.isfinite(post)
            if pfin.any():
                out["post_flow_vol"] = float(post[pfin].mean() / B)
        if fin.any():
            out["flow_leads_vol"] = float(jmax < int(np.nanargmax(w)))
    out["lpr"] = _window_features(i, t, W, F, pmax)["largest_print_ratio"]
    return out


def _battery_val(f: dict, which: str, lpr_thr: float, ve_thr: float):
    """two_part / flow_only / vol_only indicator; None when either component
    is NA (missing is not absent)."""
    if f["lpr"] is None or f["vol_expansion"] is None:
        return None
    sig = f["lpr"] >= lpr_thr
    vhi = f["vol_expansion"] >= ve_thr
    if which == "two_part":
        return float(sig and vhi)
    if which == "flow_only":
        return float(sig and not vhi)
    return float(vhi and not sig)


def main() -> None:
    data = json.loads(IN_JSON.read_text())
    frozen = data["control_base_rates"]
    universe = _universe()
    assert len(universe) == data["meta"]["universe_size"], "universe drifted"
    ni = {n: i for i, n in enumerate(universe)}
    spy_dates, H, L, C, V, spy_c = _bars(universe)
    di = {d: t for t, d in enumerate(spy_dates)}
    quint = _tier_quintiles(C, V)
    F, pmax = _flow_features(universe, spy_dates)
    dv = C * V

    episodes = data["episodes"]
    ctrl_keys = sorted({(ni[c["name"]], di[e["date"]])
                        for e in episodes for c in e["controls"]})

    # ---- CONTROLS FIRST (code-order enforcement of the registration) ----
    ctrl_feats = {k: {W: _vol_feats(k[0], k[1], W, dv, F, pmax)
                      for W in WINDOWS} for k in ctrl_keys}
    thr = {}
    for W in WINDOWS:
        thr[W] = {"lpr_p90": frozen[str(W)]["largest_print_ratio"]["p90"]}
        for feat in RATIO_FEATS:
            vals = np.array([f[W][feat] for f in ctrl_feats.values()
                             if f[W][feat] is not None], float)
            thr[W][feat] = {"p10": float(np.percentile(vals, 10)),
                            "p90": float(np.percentile(vals, 90))}
        print(f"thresholds {W}d: " + ", ".join(
            f"{feat} p10={thr[W][feat]['p10']:.3g} "
            f"p90={thr[W][feat]['p90']:.3g}" for feat in RATIO_FEATS))

    # ---- movers read here, under the committed registration ----
    mover_feats = {}
    for e in episodes:
        key = (ni[e["name"]], di[e["date"]])
        if key not in mover_feats:
            mover_feats[key] = {W: _vol_feats(key[0], key[1], W, dv, F, pmax)
                                for W in WINDOWS}

    sessions = sorted({e["date"] for e in episodes})
    si = {d: k for k, d in enumerate(sessions)}
    S = len(sessions)
    stats, cols = [], []

    def add_stat(meta, rows):
        arr = np.zeros((4, S))
        for k, mi, mv, ci, cv in rows:
            arr[0, k] += mi
            arr[1, k] += mv
            arr[2, k] += ci
            arr[3, k] += cv
        stats.append(meta)
        cols.append(arr)

    def val_of(f, feat, W):
        if feat in BATTERY:
            return _battery_val(f[W], feat, thr[W]["lpr_p90"],
                                thr[W]["vol_expansion"]["p90"])
        return f[W][feat]

    def share_rows(subset, W, feat, pred):
        rows = []
        for e in subset:
            k = si[e["date"]]
            mv = val_of(mover_feats[(ni[e["name"]], di[e["date"]])], feat, W)
            m_ind = m_val = 0.0
            if mv is not None:
                m_val = 1.0
                m_ind = float(pred(mv))
            c_ind = c_val = 0.0
            for c in e["controls"]:
                cv = val_of(ctrl_feats[(ni[c["name"]], di[e["date"]])],
                            feat, W)
                if cv is not None:
                    c_val += 1.0
                    c_ind += float(pred(cv))
            rows.append((k, m_ind, m_val, c_ind, c_val))
        return rows

    NA_FEATS = RATIO_FEATS + ("flow_leads_vol", "two_part")
    for arm in ("residual", "raw"):
        for sign in ("rise", "fall"):
            subset = [e for e in episodes
                      if e["arm"] == arm and e["sign"] == sign]
            for W in WINDOWS:
                for feat in RATIO_FEATS:
                    for tail, q in (("hi", "p90"), ("lo", "p10")):
                        cut = thr[W][feat][q]
                        add_stat({"arm": arm, "sign": sign, "W": W,
                                  "feature": feat, "tail": tail, "thr": cut},
                                 share_rows(subset, W, feat,
                                            lambda v, c=cut, t=tail:
                                            v >= c if t == "hi" else v <= c))
                add_stat({"arm": arm, "sign": sign, "W": W,
                          "feature": "flow_leads_vol", "tail": "share",
                          "thr": None},
                         share_rows(subset, W, "flow_leads_vol",
                                    lambda v: v >= 0.5))
                for feat in BATTERY:
                    add_stat({"arm": arm, "sign": sign, "W": W,
                              "feature": feat, "tail": "share", "thr": None},
                             share_rows(subset, W, feat, lambda v: v >= 0.5))
                for feat in NA_FEATS:
                    rows = []
                    for e in subset:
                        k = si[e["date"]]
                        mv = val_of(
                            mover_feats[(ni[e["name"]], di[e["date"]])],
                            feat, W)
                        c_ind = c_val = 0.0
                        for c in e["controls"]:
                            cv = val_of(
                                ctrl_feats[(ni[c["name"]], di[e["date"]])],
                                feat, W)
                            c_val += 1.0
                            c_ind += float(cv is None)
                        rows.append((k, float(mv is None), 1.0, c_ind, c_val))
                    add_stat({"arm": arm, "sign": sign, "W": W,
                              "feature": feat, "tail": "na", "thr": None},
                             rows)

    # liquidity split (residual arm): vol_expansion tails + two_part
    for sign in ("rise", "fall"):
        subset = [e for e in episodes
                  if e["arm"] == "residual" and e["sign"] == sign]
        unknown = sum(1 for e in subset
                      if quint[ni[e["name"]], di[e["date"]]] < 0)
        print(f"liquidity split {sign}: {unknown} unknown-quintile episodes "
              f"excluded (counted, never silent)")
        for q in range(5):
            sub_q = [e for e in subset
                     if quint[ni[e["name"]], di[e["date"]]] == q]
            for W in WINDOWS:
                for tail, qq in (("hi", "p90"), ("lo", "p10")):
                    cut = thr[W]["vol_expansion"][qq]
                    add_stat({"arm": "residual", "sign": sign, "W": W,
                              "feature": "vol_expansion", "tail": tail,
                              "quintile": q, "thr": cut,
                              "n_episodes": len(sub_q)},
                             share_rows(sub_q, W, "vol_expansion",
                                        lambda v, c=cut, t=tail:
                                        v >= c if t == "hi" else v <= c))
                add_stat({"arm": "residual", "sign": sign, "W": W,
                          "feature": "two_part", "tail": "share",
                          "quintile": q, "thr": None,
                          "n_episodes": len(sub_q)},
                         share_rows(sub_q, W, "two_part", lambda v: v >= 0.5))

    # ---- point estimates + session bootstrap (one shared resampling/draw)
    K = len(stats)
    A = np.stack(cols)
    with np.errstate(invalid="ignore", divide="ignore"):
        tot = A.sum(axis=2)
        m_share = tot[:, 0] / tot[:, 1]
        c_share = tot[:, 2] / tot[:, 3]
        point = m_share - c_share
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
    for k, m in enumerate(stats):
        row = dict(m)
        row.update({
            "mover_share": (None if not np.isfinite(m_share[k])
                            else round(float(m_share[k]), 4)),
            "ctrl_share": (None if not np.isfinite(c_share[k])
                           else round(float(c_share[k]), 4)),
            "diff": (None if not np.isfinite(point[k])
                     else round(float(point[k]), 4)),
            "ci": [round(float(lo[k]), 4), round(float(hi[k]), 4)],
            "n_mover": int(cols[k][1].sum()), "n_ctrl": int(cols[k][3].sum()),
        })
        out_rows.append(row)

    def fmt(row):
        d, (clo, chi) = row["diff"], row["ci"]
        star = " *" if d is not None and (clo > 0 or chi < 0) else ""
        thr_s = (f"{row['thr']:.3g}" if isinstance(row["thr"], (int, float))
                 else "-")
        return (f"  {row['feature']:<15} {row['tail']:<5} thr={thr_s:<9} "
                f"mover={row['mover_share']} ctrl={row['ctrl_share']} "
                f"diff={d:+.4f} [{clo:+.4f},{chi:+.4f}]{star}"
                if d is not None else
                f"  {row['feature']:<15} {row['tail']:<5} (no valid windows)")

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
                 "n_sessions": S,
                 "thresholds": {str(W): thr[W] for W in WINDOWS}},
        "stats": out_rows}, indent=1))
    print(f"\nwrote {OUT}")


if __name__ == "__main__":
    main()
