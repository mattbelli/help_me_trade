"""Roadmap 1.14: the targeted sign test — does the DIRECTION of preceding tilt
carry information about the direction of the coming big move?

Registered 2026-07-29 (FLOW_ROADMAP.md 1.14) BEFORE this ran; second read of
mover flow. Two direction features: all-flow tilt sign (bullish = frozen
window_tilt > 0, parameter-free) and short-DTE tilt sign (same tilt restricted
to dte_bucket 0-7/8-30 on the identical fence — 1.13 localized the informative
flow in short tenor). Primary per (feature, window): D_sign =
bull-share(movers) - bull-share(matched controls) per sign, then the contrast
C = D_fall - D_rise. Sign map fixed in advance: C > 0 => reversal
(Pan-Poteshman direction), C < 0 => right-way flow, CI covering 0 => no
day-grain sign information. 18 primary intervals => ~1 expected false positive.
Secondary color (no stars claimed): mean-tilt magnitude, frozen p90/p10
tilt-tail continuity rows, tilt-sign x move-sign cross-tab. Session-cluster
bootstrap, 10,000 draws, one shared resampling, SEED=20260802; C per draw =
that draw's D_fall - D_rise.

Windows end t-1 <= 2026-05-28: training flow only, test split untouched.
Output: tables + data/research/mover_sign_114.json.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools.mover_controls import (  # noqa: E402
    CORPUS_DB,
    SHORT_DTE,
    WINDOWS,
    _bars,
    _flow_features,
    _tier_quintiles,
    _window_features,
)
from tools.mover_profile import _universe  # noqa: E402

IN_JSON = ROOT / "data" / "research" / "mover_controls.json"
OUT = ROOT / "data" / "research" / "mover_sign_114.json"
N_BOOT = 10_000
SEED = 20260802
FKEYS = (("tilt_sign", "window_tilt"), ("short_tilt_sign", "short_tilt"))


def _short_bull(universe: list[str], spy_dates: list[str]) -> np.ndarray:
    """Short-DTE bull premium (call-buy + put-sell) per name-day on the
    identical fence as _flow_features."""
    cls = "side IN ('buy','sell') AND multileg=0"
    con = sqlite3.connect(f"file:{CORPUS_DB}?mode=ro", uri=True)
    rows = con.execute(f"""
        SELECT date, underlying, SUM(premium_usd)
        FROM flow_rollup
        WHERE {cls} AND dte_bucket IN {SHORT_DTE!r}
          AND ((side='buy' AND right='C') OR (side='sell' AND right='P'))
        GROUP BY date, underlying""").fetchall()
    con.close()
    di = {d: t for t, d in enumerate(spy_dates)}
    ni = {n: i for i, n in enumerate(universe)}
    bs = np.zeros((len(universe), len(spy_dates)))
    for d, u, v in rows:
        i, t = ni.get(u), di.get(d)
        if i is not None and t is not None:
            bs[i, t] = v
    return bs


def _dirs(i: int, t: int, W: int, F, pmax, bull_s) -> dict:
    """The two registered direction features for one window: the frozen
    window_tilt (same code path as 1.12/1.13) and the short-DTE tilt."""
    lo = t - W
    wt = _window_features(i, t, W, F, pmax)["window_tilt"]
    cs = float(F["short"][i, lo:t].sum())
    st = (float((2 * bull_s[i, lo:t] - F["short"][i, lo:t]).sum()) / cs
          if cs > 0 else None)
    return {"window_tilt": wt, "short_tilt": st}


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
    bull_s = _short_bull(universe, spy_dates)

    episodes = data["episodes"]
    keys = sorted(
        {(ni[e["name"]], di[e["date"]]) for e in episodes}
        | {(ni[c["name"]], di[e["date"]])
           for e in episodes for c in e["controls"]})
    feats = {k: {W: _dirs(k[0], k[1], W, F, pmax, bull_s) for W in WINDOWS}
             for k in keys}

    sessions = sorted({e["date"] for e in episodes})
    si = {d: k for k, d in enumerate(sessions)}
    S = len(sessions)
    stats, cols = [], []   # per stat: (num_m, den_m, num_c, den_c) each [S]

    def add_stat(meta, rows):
        arr = np.zeros((4, S))
        for k, mi, mv, ci, cv in rows:
            arr[0, k] += mi
            arr[1, k] += mv
            arr[2, k] += ci
            arr[3, k] += cv
        stats.append(meta)
        cols.append(arr)

    def share_rows(subset, W, fkey, pred):
        rows = []
        for e in subset:
            k = si[e["date"]]
            mv = feats[(ni[e["name"]], di[e["date"]])][W][fkey]
            m_ind = m_val = 0.0
            if mv is not None:
                m_val = 1.0
                m_ind = float(pred(mv))
            c_ind = c_val = 0.0
            for c in e["controls"]:
                cv = feats[(ni[c["name"]], di[e["date"]])][W][fkey]
                if cv is not None:
                    c_val += 1.0
                    c_ind += float(pred(cv))
            rows.append((k, m_ind, m_val, c_ind, c_val))
        return rows

    def mean_rows(subset, W, fkey):
        rows = []
        for e in subset:
            k = si[e["date"]]
            mv = feats[(ni[e["name"]], di[e["date"]])][W][fkey]
            m_num = m_den = 0.0
            if mv is not None:
                m_num, m_den = float(mv), 1.0
            c_num = c_den = 0.0
            for c in e["controls"]:
                cv = feats[(ni[c["name"]], di[e["date"]])][W][fkey]
                if cv is not None:
                    c_den += 1.0
                    c_num += float(cv)
            rows.append((k, m_num, m_den, c_num, c_den))
        return rows

    for arm in ("residual", "raw"):
        for sign in ("rise", "fall"):
            subset = [e for e in episodes
                      if e["arm"] == arm and e["sign"] == sign]
            for W in WINDOWS:
                for label, fkey in FKEYS:
                    add_stat({"stat": "D", "feature": label, "arm": arm,
                              "sign": sign, "W": W},
                             share_rows(subset, W, fkey, lambda v: v > 0))
                    add_stat({"stat": "mean", "feature": label, "arm": arm,
                              "sign": sign, "W": W}, mean_rows(subset, W, fkey))
                for tail, q in (("hi", "p90"), ("lo", "p10")):
                    cut = frozen[str(W)]["window_tilt"][q]
                    add_stat({"stat": "tail", "feature": "window_tilt",
                              "tail": tail, "arm": arm, "sign": sign, "W": W,
                              "thr": cut},
                             share_rows(subset, W, "window_tilt",
                                        lambda v, c=cut, t=tail:
                                        v >= c if t == "hi" else v <= c))

    # liquidity split (feature 1, residual arm), robustness not primary
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
                add_stat({"stat": "D", "feature": "tilt_sign",
                          "arm": "residual", "sign": sign, "W": W,
                          "quintile": q, "n_episodes": len(sub_q)},
                         share_rows(sub_q, W, "window_tilt", lambda v: v > 0))

    # descriptive cross-tab (residual arm): tilt sign x move sign
    xtab = {}
    for sign in ("rise", "fall"):
        subset = [e for e in episodes
                  if e["arm"] == "residual" and e["sign"] == sign]
        for W in WINDOWS:
            cnt = {"bull": 0, "bear": 0, "zero": 0, "na": 0}
            ccnt = {"bull": 0, "bear": 0, "zero": 0, "na": 0}
            for e in subset:
                mv = feats[(ni[e["name"]], di[e["date"]])][W]["window_tilt"]
                cat = ("na" if mv is None else "bull" if mv > 0
                       else "bear" if mv < 0 else "zero")
                cnt[cat] += 1
                for c in e["controls"]:
                    cv = feats[(ni[c["name"]], di[e["date"]])][W]["window_tilt"]
                    cat = ("na" if cv is None else "bull" if cv > 0
                           else "bear" if cv < 0 else "zero")
                    ccnt[cat] += 1
            xtab[f"{sign}:{W}d"] = {"mover": cnt, "ctrl": ccnt}

    # ---- point estimates + session bootstrap (one shared resampling/draw)
    K = len(stats)
    A = np.stack(cols)                     # [K, 4, S]
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

    # contrasts C = D_fall - D_rise from the SAME draws (paired per draw)
    def _ckey(m):
        return tuple(sorted((k, v) for k, v in m.items()
                            if k not in ("sign", "n_episodes")))

    idx = {(_ckey(m), m["sign"]): k for k, m in enumerate(stats)}
    contrasts = []
    for k, m in enumerate(stats):
        if m["sign"] != "fall":
            continue
        kr = idx.get((_ckey(m), "rise"))
        if kr is None:
            continue
        cd = diffs[:, k] - diffs[:, kr]
        cp = point[k] - point[kr]
        clo, chi = np.nanpercentile(cd, [2.5, 97.5])
        row = {kk: vv for kk, vv in m.items()
               if kk not in ("sign", "n_episodes")}
        row.update({"C": (None if not np.isfinite(cp)
                          else round(float(cp), 4)),
                    "ci": [round(float(clo), 4), round(float(chi), 4)]})
        contrasts.append(row)

    # ---- report
    def _star(d, ci):
        return " *" if d is not None and (ci[0] > 0 or ci[1] < 0) else ""

    def _drow(stat, feature, arm, sign, W, tail=None, quintile=None):
        for r in out_rows:
            if (r["stat"], r["feature"], r["arm"], r["sign"], r["W"],
                    r.get("tail"), r.get("quintile")) == (
                    stat, feature, arm, sign, W, tail, quintile):
                return r
        return None

    def _crow(stat, feature, arm, W, tail=None, quintile=None):
        for r in contrasts:
            if (r["stat"], r["feature"], r["arm"], r["W"], r.get("tail"),
                    r.get("quintile")) == (stat, feature, arm, W, tail,
                                           quintile):
                return r
        return None

    print("\n== PRIMARY: bull-share D per sign + contrast C = D_fall - D_rise "
          "(C>0 reversal / C<0 right-way) ==")
    for arm in ("residual", "raw"):
        for label, _f in FKEYS:
            for W in WINDOWS:
                dr = _drow("D", label, arm, "rise", W)
                df = _drow("D", label, arm, "fall", W)
                c = _crow("D", label, arm, W)
                print(f"  {arm:<8} {label:<16} {W:>2}d  "
                      f"D_rise={dr['diff']:+.4f} [{dr['ci'][0]:+.4f},"
                      f"{dr['ci'][1]:+.4f}]{_star(dr['diff'], dr['ci'])}  "
                      f"D_fall={df['diff']:+.4f} [{df['ci'][0]:+.4f},"
                      f"{df['ci'][1]:+.4f}]{_star(df['diff'], df['ci'])}  "
                      f"C={c['C']:+.4f} [{c['ci'][0]:+.4f},{c['ci'][1]:+.4f}]"
                      f"{_star(c['C'], c['ci'])}")

    print("\n== SECONDARY color: mean tilt (mover - control) + contrast ==")
    for arm in ("residual", "raw"):
        for label, _f in FKEYS:
            for W in WINDOWS:
                dr = _drow("mean", label, arm, "rise", W)
                df = _drow("mean", label, arm, "fall", W)
                c = _crow("mean", label, arm, W)
                print(f"  {arm:<8} {label:<16} {W:>2}d  "
                      f"rise={dr['diff']:+.4f}  fall={df['diff']:+.4f}  "
                      f"C={c['C']:+.4f} [{c['ci'][0]:+.4f},{c['ci'][1]:+.4f}]")

    print("\n== SECONDARY color: frozen p90/p10 tilt-tail continuity ==")
    for arm in ("residual", "raw"):
        for tail in ("hi", "lo"):
            for W in WINDOWS:
                dr = _drow("tail", "window_tilt", arm, "rise", W, tail=tail)
                df = _drow("tail", "window_tilt", arm, "fall", W, tail=tail)
                c = _crow("tail", "window_tilt", arm, W, tail=tail)
                print(f"  {arm:<8} tilt-{tail:<3} {W:>2}d  "
                      f"rise={dr['diff']:+.4f}  fall={df['diff']:+.4f}  "
                      f"C={c['C']:+.4f} [{c['ci'][0]:+.4f},{c['ci'][1]:+.4f}]")

    print("\n== liquidity split (tilt_sign, residual): D per quintile + C ==")
    for q in range(5):
        for W in WINDOWS:
            dr = _drow("D", "tilt_sign", "residual", "rise", W, quintile=q)
            df = _drow("D", "tilt_sign", "residual", "fall", W, quintile=q)
            c = _crow("D", "tilt_sign", "residual", W, quintile=q)
            print(f"  q{q} {W:>2}d  n_rise={dr['n_mover']:<5} "
                  f"n_fall={df['n_mover']:<5} "
                  f"D_rise={_fmt(dr['diff'])}  D_fall={_fmt(df['diff'])}  "
                  f"C={_fmt(c['C'])} [{c['ci'][0]:+.4f},{c['ci'][1]:+.4f}]"
                  f"{_star(c['C'], c['ci'])}")

    print("\n== cross-tab (residual): tilt sign x move sign, episode counts ==")
    for key, v in xtab.items():
        print(f"  {key:<9} mover {v['mover']}  ctrl {v['ctrl']}")

    OUT.write_text(json.dumps({
        "meta": {"registered": "2026-07-29", "seed": SEED, "n_boot": N_BOOT,
                 "n_sessions": S},
        "stats": out_rows, "contrasts": contrasts, "crosstab": xtab},
        indent=1))
    print(f"\nwrote {OUT}")


def _fmt(v) -> str:
    return "  none " if v is None else f"{v:+.4f}"


if __name__ == "__main__":
    main()
