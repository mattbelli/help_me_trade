"""Roadmap 1.14d: the peer-relative forward screen.

Registered 2026-07-29 (FLOW_ROADMAP.md 1.14d) BEFORE this ran. 1.14c showed
the pooled global-threshold screen dies in cross-name composition while the
backwards design that found the fingerprint compared movers against controls
matched on session + liquidity tier. This is the forward mirror: a component
fires when the name-day's feature is >= the 90th percentile of that feature
among eligible name-days with a defined value in the SAME (session x
trailing-20d dollar-volume quintile) cell. No new level parameters; p90
mirrors the frozen backwards tail choice.

Everything else inherited from 1.14c verbatim: grain, eligibility, outcome
(frozen 1.11 episode starts, either sign, in t+1..t+W), FRESH primary arm /
all-days reported, residual primary / raw replication, fired-minus-unfired pp
difference primary (unfired = the trigger's defined pool that did not fire),
circular moving-block bootstrap (block = W), 10,000 draws, one shared
resampling per W. 15 primary intervals => ~1 expected false. SEED=20260806.

Sector is NOT in the matching cell (too thin) — one rung coarser than the
backwards controls, disclosed. Training only; test split untouched.
Output: tables + data/research/mover_relative_114d.json.
"""

from __future__ import annotations

import json
import sqlite3
import sys
import warnings
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools.mover_controls import (  # noqa: E402
    ACTIVE_FLOOR,
    MIN_HISTORY,
    TRAIN_END,
    WINDOWS,
    _bars,
    _flow_features,
    _tier_quintiles,
    _window_features,
)
from tools.mover_forward import _roll_max, _roll_med60, _roll_sum  # noqa: E402
from tools.mover_profile import _universe  # noqa: E402
from tools.mover_volume import _vol_feats  # noqa: E402

CTRL_JSON = ROOT / "data" / "research" / "mover_controls.json"
EARN_DB = ROOT / "data" / "research" / "earnings.db"
OUT = ROOT / "data" / "research" / "mover_relative_114d.json"
N_BOOT = 10_000
SEED = 20260806
N_VERIFY = 200
FEATS = ("short_dte_share", "concentration", "largest_print_ratio",
         "vol_max_ratio")
TRIGGERS = FEATS + ("vol_and_short",)


def main() -> None:
    data = json.loads(CTRL_JSON.read_text())
    universe = _universe()
    assert len(universe) == data["meta"]["universe_size"], "universe drifted"
    ni = {n: i for i, n in enumerate(universe)}
    spy_dates, H, L, C, V, spy_c = _bars(universe)
    di = {d: t for t, d in enumerate(spy_dates)}
    N, T = C.shape
    train_last = max(t for t, d in enumerate(spy_dates) if d <= TRAIN_END)
    F, pmax = _flow_features(universe, spy_dates)
    dv = C * V
    quint = _tier_quintiles(C, V)

    # ---- feature panels (identical construction to 1.14c) ----
    med_p, cnt_p = _roll_med60(pmax)
    med_v, cnt_v = _roll_med60(dv)
    feats = {}
    for W in WINDOWS:
        bb = _roll_sum(F["cls"], W)
        short = _roll_sum(F["short"], W)
        mx = _roll_max(F["cls"], W)
        wmax = _roll_max(pmax, W)
        vmax = _roll_max(dv, W)
        B = np.full((N, T), np.nan)
        Bc = np.zeros((N, T))
        B[:, W:] = med_v[:, :-W]
        Bc[:, W:] = cnt_v[:, :-W]
        with np.errstate(invalid="ignore", divide="ignore"):
            feats[W] = {
                "short_dte_share": np.where(bb > 0, short / bb, np.nan),
                "concentration": np.where(
                    (bb > 0) & (mx >= ACTIVE_FLOOR), mx / bb, np.nan),
                "largest_print_ratio": np.where(
                    (cnt_p >= 20) & (med_p > 0) & np.isfinite(wmax),
                    wmax / med_p, np.nan),
                "vol_max_ratio": np.where(
                    (Bc >= 20) & (B > 0) & np.isfinite(vmax),
                    vmax / B, np.nan),
            }

    # ---- verify panels against the registered feature functions ----
    rng = np.random.default_rng(SEED)
    for W in WINDOWS:
        elig = np.isfinite(C)
        elig[:, :MIN_HISTORY] = False
        elig[:, train_last - W + 1:] = False
        ii, tt = np.nonzero(elig)
        pick = rng.choice(len(ii), size=min(N_VERIFY, len(ii)), replace=False)
        for j in pick:
            i, t = int(ii[j]), int(tt[j])
            wf = _window_features(i, t + 1, W, F, pmax)
            vf = _vol_feats(i, t + 1, W, dv, F, pmax)
            ref = {"short_dte_share": wf["short_dte_share"],
                   "concentration": wf["concentration"],
                   "largest_print_ratio": wf["largest_print_ratio"],
                   "vol_max_ratio": vf["vol_max_ratio"]}
            for k, rv in ref.items():
                pv = feats[W][k][i, t]
                ok = (rv is None and not np.isfinite(pv)) or (
                    rv is not None and np.isfinite(pv)
                    and np.isclose(pv, rv, rtol=1e-9, atol=1e-12))
                assert ok, (f"panel mismatch {k} W={W} "
                            f"{universe[i]}@{spy_dates[t]}: {pv} vs {rv}")
    print(f"panel verification OK ({N_VERIFY} samples x {len(WINDOWS)} "
          f"windows x 4 features)")

    # ---- episode starts + catalyst flags (1.14c/1.14b definitions) ----
    ep_start = {"residual": np.zeros((N, T), bool),
                "raw": np.zeros((N, T), bool)}
    ep_sign = {"rise": np.zeros((N, T), bool), "fall": np.zeros((N, T), bool)}
    for e in data["episodes"]:
        i, t = ni[e["name"]], di[e["date"]]
        ep_start[e["arm"]][i, t] = True
        if e["arm"] == "residual":
            ep_sign[e["sign"]][i, t] = True
    any_start = ep_start["residual"] | ep_start["raw"]

    def _prefix(B):
        return np.concatenate([np.zeros((N, 1), int),
                               np.cumsum(B.astype(int), axis=1)], axis=1)

    cs_arm = {arm: _prefix(ep_start[arm]) for arm in ep_start}
    cs_sign = {s: _prefix(ep_sign[s]) for s in ep_sign}
    cs_any = _prefix(any_start)

    con = sqlite3.connect(f"file:{EARN_DB}?mode=ro", uri=True)
    earn = {}
    for sym, d in con.execute("SELECT symbol, date FROM earnings"):
        earn.setdefault(sym, []).append(d)
    con.close()
    cat_start = np.zeros((N, T), bool)
    for e in data["episodes"]:
        if e["arm"] != "residual":
            continue
        i, t = ni[e["name"]], di[e["date"]]
        lo_d, hi_d = spy_dates[max(0, t - 1)], spy_dates[min(T - 1, t + 1)]
        if any(lo_d <= d <= hi_d for d in earn.get(e["name"], ())):
            cat_start[i, t] = True
    cs_cat = _prefix(cat_start)

    # ---- statistics ----
    out_rows = []
    desc = {"firing": [], "signs": [], "catalyst": []}
    for W in WINDOWS:
        ts = np.arange(MIN_HISTORY, train_last - W + 1)
        S = len(ts)
        elig = np.isfinite(C[:, ts])
        qs = quint[:, ts]
        hit = {arm: (cs_arm[arm][:, ts + W + 1] - cs_arm[arm][:, ts + 1]) > 0
               for arm in cs_arm}
        hit_sign = {s: (cs_sign[s][:, ts + W + 1] - cs_sign[s][:, ts + 1]) > 0
                    for s in cs_sign}
        hit_cat = (cs_cat[:, ts + W + 1] - cs_cat[:, ts + 1]) > 0
        fresh = (cs_any[:, ts + 1] - cs_any[:, ts - W]) == 0

        # peer-relative fires: p90 rank within (session x quintile) cell
        pools, firemap = {}, {}
        for k in FEATS:
            vs = feats[W][k][:, ts]
            pool = elig & np.isfinite(vs) & (qs >= 0)
            fire = np.zeros_like(pool)
            for s in range(S):
                col = vs[:, s]
                pm = pool[:, s]
                for q in range(5):
                    m = pm & (qs[:, s] == q)
                    if not m.any():
                        continue
                    cut = np.percentile(col[m], 90)
                    fire[m & (col >= cut), s] = True
            pools[k], firemap[k] = pool, fire
        pools["vol_and_short"] = (pools["vol_max_ratio"]
                                  & pools["short_dte_share"])
        firemap["vol_and_short"] = (firemap["vol_max_ratio"]
                                    & firemap["short_dte_share"]
                                    & pools["vol_and_short"])

        stats, cols = [], []
        for trig in TRIGGERS:
            pool, fr = pools[trig], firemap[trig]
            for mode, sub in (("fresh", fresh), ("all", np.ones_like(fresh))):
                fired = fr & pool & sub
                unfired = ~fr & pool & sub
                for arm in ("residual", "raw"):
                    h = hit[arm]
                    arr = np.stack([(fired & h).sum(0), fired.sum(0),
                                    (unfired & h).sum(0), unfired.sum(0)]
                                   ).astype(float)
                    stats.append({"W": W, "trigger": trig, "mode": mode,
                                  "arm": arm})
                    cols.append(arr)
            fa = fr & pool
            desc["firing"].append(
                {"W": W, "trigger": trig, "pool": int(pool.sum()),
                 "fire_rate": round(float(fa.sum() / pool.sum()), 5)
                 if pool.any() else None,
                 "alerts_per_session": round(float(fa.sum(0).mean()), 1)})
            ff = fr & pool & fresh
            n_hit = int((ff & hit["residual"]).sum())
            desc["signs"].append(
                {"W": W, "trigger": trig, "n_fired_hits": n_hit,
                 "rise_share": round(float(
                     (ff & hit_sign["rise"]).sum() / n_hit), 4)
                 if n_hit else None,
                 "fall_share": round(float(
                     (ff & hit_sign["fall"]).sum() / n_hit), 4)
                 if n_hit else None})
            desc["catalyst"].append(
                {"W": W, "trigger": trig,
                 "cat_share_of_hits": round(float(
                     (ff & hit_cat).sum() / n_hit), 4) if n_hit else None})

        base = float(hit["residual"][elig & fresh].mean())
        print(f"\nW={W}: {S} sessions; base P(episode<={W}d) fresh={base:.4f}"
              + "".join(f"; {k} pool={int(pools[k].sum())}" for k in TRIGGERS))

        K = len(stats)
        A = np.stack(cols)
        with np.errstate(invalid="ignore", divide="ignore"):
            tot = A.sum(axis=2)
            f_share = tot[:, 0] / tot[:, 1]
            u_share = tot[:, 2] / tot[:, 3]
            point = f_share - u_share
        n_blocks = -(-S // W)
        Wmat = np.empty((N_BOOT, S))
        offs = np.arange(W)
        for b in range(N_BOOT):
            starts = rng.integers(0, S, size=n_blocks)
            idx = (starts[:, None] + offs[None, :]).ravel() % S
            Wmat[b] = np.bincount(idx, minlength=S)
        agg = (Wmat @ A.reshape(K * 4, S).T).reshape(N_BOOT, K, 4)
        with np.errstate(invalid="ignore", divide="ignore"):
            diffs = agg[:, :, 0] / agg[:, :, 1] - agg[:, :, 2] / agg[:, :, 3]
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            lo, hi = np.nanpercentile(diffs, [2.5, 97.5], axis=0)
        for k, m in enumerate(stats):
            row = dict(m)
            row.update({
                "fired_hit_share": (None if not np.isfinite(f_share[k])
                                    else round(float(f_share[k]), 4)),
                "unfired_hit_share": (None if not np.isfinite(u_share[k])
                                      else round(float(u_share[k]), 4)),
                "diff": (None if not np.isfinite(point[k])
                         else round(float(point[k]), 4)),
                "ci": [round(float(lo[k]), 4), round(float(hi[k]), 4)],
                "n_fired": int(cols[k][1].sum()),
                "n_unfired": int(cols[k][3].sum()),
                "ratio": (round(float(f_share[k] / u_share[k]), 2)
                          if np.isfinite(f_share[k]) and u_share[k] > 0
                          else None),
            })
            out_rows.append(row)

    print("\n== PRIMARY: fired - unfired forward-episode share, "
          "peer-relative triggers ==")
    for mode in ("fresh", "all"):
        for arm in ("residual", "raw"):
            print(f"\n-- {mode} / {arm} --")
            for row in out_rows:
                if (row["mode"], row["arm"]) != (mode, arm):
                    continue
                d, (clo, chi) = row["diff"], row["ci"]
                star = " *" if d is not None and (clo > 0 or chi < 0) else ""
                print(f"  {row['trigger']:<20} {row['W']:>2}d "
                      f"fired={row['fired_hit_share']} "
                      f"(n={row['n_fired']}) "
                      f"unfired={row['unfired_hit_share']} "
                      f"diff={d:+.4f} [{clo:+.4f},{chi:+.4f}]{star} "
                      f"ratio={row['ratio']}")
    print("\n== firing (defined pool per trigger) ==")
    for r in desc["firing"]:
        print(f"  {r['trigger']:<20} {r['W']:>2}d pool={r['pool']} "
              f"fire_rate={r['fire_rate']} "
              f"alerts/session={r['alerts_per_session']}")
    print("\n== hit composition (fresh residual, descriptive) ==")
    for r, c in zip(desc["signs"], desc["catalyst"]):
        print(f"  {r['trigger']:<20} {r['W']:>2}d n_hits={r['n_fired_hits']} "
              f"rise={r['rise_share']} fall={r['fall_share']} "
              f"earnings-adjacent={c['cat_share_of_hits']}")

    OUT.write_text(json.dumps({
        "meta": {"registered": "2026-07-29", "seed": SEED, "n_boot": N_BOOT,
                 "block_bootstrap": "circular moving-block, length=W",
                 "cell": "session x trailing-20d dollar-volume quintile",
                 "rank_threshold": "p90 within cell",
                 "train_last": spy_dates[train_last]},
        "stats": out_rows, "descriptive": desc}, indent=1))
    print(f"\nwrote {OUT}")


if __name__ == "__main__":
    main()
