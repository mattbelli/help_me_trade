"""Roadmap 1.14b: the catalyst split — does the established pre-move
fingerprint differ between earnings-adjacent and no-catalyst events?

Registered 2026-07-29 (FLOW_ROADMAP.md 1.14b) BEFORE any per-episode catalyst
flag was computed. Catalyst flag: >=1 EDGAR 8-K Item-2.02 filing date within
+-1 SPY session of the episode date (data/research/earnings.db, mover names
only — controls were required quiet). Earnings-ONLY detection: "no-catalyst"
is really no-KNOWN-catalyst, contamination pushes the subsets together, so a
null is the weak conclusion and a surviving difference is strong evidence.

Features — the four established fingerprint components at pre-existing
thresholds (zero new degrees of freedom): short_dte_share / concentration /
largest_print_ratio at the FROZEN 1.12 control p90s, vol_max_ratio at 1.13b's
controls-first thresholds (mover_volume_113b.json meta, reused verbatim).
Primary per (feature, window, sign): X = D_cat - D_nocat where D = mover-minus-
control exceedance share within the subset; paired per bootstrap draw as in
1.14. 24 residual X intervals => ~1 expected false positive. Session-cluster
bootstrap, 10,000 draws, one shared resampling, SEED=20260804. Reading rule:
CI clear + >=2 windows + raw-arm direction replication.

Windows end t-1 <= 2026-05-28: training only, test split untouched.
Output: tables + data/research/mover_catalyst_114b.json.
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
    WINDOWS,
    _bars,
    _flow_features,
    _window_features,
)
from tools.mover_profile import _universe  # noqa: E402
from tools.mover_volume import _vol_feats  # noqa: E402

IN_JSON = ROOT / "data" / "research" / "mover_controls.json"
VOL_JSON = ROOT / "data" / "research" / "mover_volume_113b.json"
EARN_DB = ROOT / "data" / "research" / "earnings.db"
OUT = ROOT / "data" / "research" / "mover_catalyst_114b.json"
N_BOOT = 10_000
SEED = 20260804
FEATURES = ("short_dte_share", "concentration", "largest_print_ratio",
            "vol_max_ratio")


def _earnings_by_name() -> dict[str, list[str]]:
    con = sqlite3.connect(f"file:{EARN_DB}?mode=ro", uri=True)
    out: dict[str, list[str]] = {}
    for s, d in con.execute("SELECT symbol, date FROM earnings"):
        out.setdefault(s, []).append(d)
    con.close()
    return out


def _feats(i: int, t: int, W: int, dv, F, pmax) -> dict:
    f = _window_features(i, t, W, F, pmax)
    v = _vol_feats(i, t, W, dv, F, pmax)
    return {"short_dte_share": f["short_dte_share"],
            "concentration": f["concentration"],
            "largest_print_ratio": f["largest_print_ratio"],
            "vol_max_ratio": v["vol_max_ratio"]}


def main() -> None:
    data = json.loads(IN_JSON.read_text())
    frozen = data["control_base_rates"]
    vol_thr = json.loads(VOL_JSON.read_text())["meta"]["thresholds"]
    universe = _universe()
    assert len(universe) == data["meta"]["universe_size"], "universe drifted"
    ni = {n: i for i, n in enumerate(universe)}
    spy_dates, H, L, C, V, spy_c = _bars(universe)
    di = {d: t for t, d in enumerate(spy_dates)}
    F, pmax = _flow_features(universe, spy_dates)
    dv = C * V

    thr = {}
    for W in WINDOWS:
        thr[W] = {feat: frozen[str(W)][feat]["p90"]
                  for feat in FEATURES if feat != "vol_max_ratio"}
        thr[W]["vol_max_ratio"] = vol_thr[str(W)]["vol_max_ratio"]["p90"]

    # ---- catalyst flags (registered definition; flags computed here, after
    # the committed registration)
    earn = _earnings_by_name()
    episodes = data["episodes"]
    n_unflaggable = len({e["name"] for e in episodes if e["name"] not in earn})
    for e in episodes:
        t = di[e["date"]]
        lo_d = spy_dates[max(0, t - 1)]
        hi_d = spy_dates[min(len(spy_dates) - 1, t + 1)]
        e["catalyst"] = any(lo_d <= d <= hi_d for d in earn.get(e["name"], ()))
    print(f"names with no earnings dates at all (unflaggable, counted in "
          f"no-catalyst per registration): {n_unflaggable}")
    for arm in ("residual", "raw"):
        for sign in ("rise", "fall"):
            sub = [e for e in episodes
                   if e["arm"] == arm and e["sign"] == sign]
            ncat = sum(e["catalyst"] for e in sub)
            print(f"catalyst share {arm}/{sign}: {ncat}/{len(sub)} "
                  f"= {100 * ncat / len(sub):.1f}%")

    keys = sorted(
        {(ni[e["name"]], di[e["date"]]) for e in episodes}
        | {(ni[c["name"]], di[e["date"]])
           for e in episodes for c in e["controls"]})
    feats = {k: {W: _feats(k[0], k[1], W, dv, F, pmax) for W in WINDOWS}
             for k in keys}

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

    def hi_rows(subset, W, feat, cut):
        rows = []
        for e in subset:
            k = si[e["date"]]
            mv = feats[(ni[e["name"]], di[e["date"]])][W][feat]
            m_ind = m_val = 0.0
            if mv is not None:
                m_val = 1.0
                m_ind = float(mv >= cut)
            c_ind = c_val = 0.0
            for c in e["controls"]:
                cv = feats[(ni[c["name"]], di[e["date"]])][W][feat]
                if cv is not None:
                    c_val += 1.0
                    c_ind += float(cv >= cut)
            rows.append((k, m_ind, m_val, c_ind, c_val))
        return rows

    for arm in ("residual", "raw"):
        for sign in ("rise", "fall"):
            base = [e for e in episodes
                    if e["arm"] == arm and e["sign"] == sign]
            for subset_name in ("cat", "nocat"):
                subset = [e for e in base
                          if e["catalyst"] == (subset_name == "cat")]
                for W in WINDOWS:
                    # presence, descriptive per subset
                    rows = []
                    for e in subset:
                        k = si[e["date"]]
                        m = feats[(ni[e["name"]], di[e["date"]])][W]
                        has = any(m[f] is not None for f in
                                  ("short_dte_share", "concentration"))
                        c_ind = c_val = 0.0
                        for c in e["controls"]:
                            cf = feats[(ni[c["name"]], di[e["date"]])][W]
                            c_val += 1.0
                            c_ind += float(any(
                                cf[f] is not None for f in
                                ("short_dte_share", "concentration")))
                        rows.append((k, float(has), 1.0, c_ind, c_val))
                    add_stat({"arm": arm, "sign": sign, "W": W,
                              "subset": subset_name, "feature": "presence",
                              "n_episodes": len(subset)}, rows)
                    for feat in FEATURES:
                        add_stat({"arm": arm, "sign": sign, "W": W,
                                  "subset": subset_name, "feature": feat,
                                  "thr": thr[W][feat],
                                  "n_episodes": len(subset)},
                                 hi_rows(subset, W, feat, thr[W][feat]))

    # ---- point estimates + session bootstrap
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

    # contrasts X = D_cat - D_nocat from the SAME draws
    def _ckey(m):
        return tuple(sorted((k, v) for k, v in m.items()
                            if k not in ("subset", "n_episodes")))

    idx = {(_ckey(m), m["subset"]): k for k, m in enumerate(stats)}
    contrasts = []
    for k, m in enumerate(stats):
        if m["subset"] != "cat":
            continue
        kn = idx.get((_ckey(m), "nocat"))
        if kn is None:
            continue
        xd = diffs[:, k] - diffs[:, kn]
        xp = point[k] - point[kn]
        xlo, xhi = np.nanpercentile(xd, [2.5, 97.5])
        row = {kk: vv for kk, vv in m.items()
               if kk not in ("subset", "n_episodes")}
        row.update({"X": (None if not np.isfinite(xp)
                          else round(float(xp), 4)),
                    "ci": [round(float(xlo), 4), round(float(xhi), 4)]})
        contrasts.append(row)

    # ---- report
    def _star(d, ci):
        return " *" if d is not None and (ci[0] > 0 or ci[1] < 0) else ""

    def _drow(feature, arm, sign, W, subset):
        for r in out_rows:
            if (r["feature"], r["arm"], r["sign"], r["W"], r["subset"]) == (
                    feature, arm, sign, W, subset):
                return r
        return None

    def _xrow(feature, arm, sign, W):
        for r in contrasts:
            if (r["feature"], r["arm"], r["sign"], r["W"]) == (
                    feature, arm, sign, W):
                return r
        return None

    def _f(v):
        return " none  " if v is None else f"{v:+.4f}"

    print("\n== PRIMARY: D per subset + contrast X = D_cat - D_nocat ==")
    for arm in ("residual", "raw"):
        for sign in ("rise", "fall"):
            for feat in FEATURES:
                for W in WINDOWS:
                    dc = _drow(feat, arm, sign, W, "cat")
                    dn = _drow(feat, arm, sign, W, "nocat")
                    x = _xrow(feat, arm, sign, W)
                    print(f"  {arm:<8} {sign:<4} {feat:<19} {W:>2}d  "
                          f"D_cat={_f(dc['diff'])} "
                          f"[{dc['ci'][0]:+.4f},{dc['ci'][1]:+.4f}]"
                          f"{_star(dc['diff'], dc['ci'])} "
                          f"(n={dc['n_episodes']})  "
                          f"D_nocat={_f(dn['diff'])} "
                          f"[{dn['ci'][0]:+.4f},{dn['ci'][1]:+.4f}]"
                          f"{_star(dn['diff'], dn['ci'])}  "
                          f"X={_f(x['X'])} [{x['ci'][0]:+.4f},"
                          f"{x['ci'][1]:+.4f}]{_star(x['X'], x['ci'])}")

    print("\n== presence per subset (descriptive) ==")
    for arm in ("residual", "raw"):
        for sign in ("rise", "fall"):
            for W in WINDOWS:
                dc = _drow("presence", arm, sign, W, "cat")
                dn = _drow("presence", arm, sign, W, "nocat")
                print(f"  {arm:<8} {sign:<4} {W:>2}d  "
                      f"cat={_f(dc['diff'])} (n={dc['n_episodes']})  "
                      f"nocat={_f(dn['diff'])} (n={dn['n_episodes']})")

    OUT.write_text(json.dumps({
        "meta": {"registered": "2026-07-29", "seed": SEED, "n_boot": N_BOOT,
                 "n_sessions": S, "n_unflaggable_names": n_unflaggable,
                 "thresholds": {str(W): thr[W] for W in WINDOWS}},
        "stats": out_rows, "contrasts": contrasts}, indent=1))
    print(f"\nwrote {OUT}")


if __name__ == "__main__":
    main()
