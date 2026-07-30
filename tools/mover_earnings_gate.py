"""Roadmap 1.14e: the event-gated screen — earnings-adjacent days only.

Registered 2026-07-29 (FLOW_ROADMAP.md 1.14e) BEFORE this ran. 1.14b showed
the fingerprint's flow regime lives on catalyst-adjacent windows; 1.14c/d
showed no day-grain screen works unconditionally. The gate is the
conditioning day grain CAN see: name-day t is EARNINGS-ADJACENT iff the name
has an EDGAR 8-K Item-2.02 filing date in t+1..t+10 SPY sessions (K=10 = the
pre-existing earnings_drift td=10 constant — cited, not tuned). Gate data =
earnings.db extended to the FULL equity_bars universe (the 1.14b fetch
covered mover names only — outcome-conditioned coverage). Names with no
resolved EDGAR history are EXCLUDED from the gated analysis (unflaggable),
count disclosed.

Everything else inherited from 1.14d verbatim: peer-relative p90 rank fires
within (session x trailing-20d dollar-volume quintile) computed over the
FULL eligible population (fires are NOT re-ranked inside the gate), frozen
outcome, FRESH primary arm / all-days reported, residual primary / raw
replication, fired-minus-unfired pp difference WITHIN the gated subset
primary, circular moving-block bootstrap (block = W), 10,000 draws, one
shared resampling per W. 15 primary intervals => ~1 expected false.
SEED=20260807. The earnings-far complement battery is computed as contrast
color — no pass/fail authority. Training only; test split untouched.
Output: tables + data/research/mover_earnings_114e.json.
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
OUT = ROOT / "data" / "research" / "mover_earnings_114e.json"
N_BOOT = 10_000
SEED = 20260807
N_VERIFY = 200
K_GATE = 10  # earnings_drift td=10 — pre-existing product constant
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

    # ---- feature panels (identical construction to 1.14c/d) ----
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

    # ---- episode starts (1.14c/d definitions) ----
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

    # ---- the gate: EDGAR filing in t+1..t+K_GATE SPY sessions ----
    con = sqlite3.connect(f"file:{EARN_DB}?mode=ro", uri=True)
    earn: dict[str, list[str]] = {}
    for sym, d in con.execute("SELECT symbol, date FROM earnings"):
        earn.setdefault(sym, []).append(d)
    con.close()
    known = np.zeros(N, bool)
    fil = np.zeros((N, T), bool)
    sd_arr = np.array(spy_dates)
    for sym, dates in earn.items():
        i = ni.get(sym)
        if i is None:
            continue
        known[i] = True
        # filing date -> the first SPY session >= that date
        idx = np.searchsorted(sd_arr, np.array(sorted(set(dates))), "left")
        idx = idx[idx < T]
        fil[i, idx] = True
    cs_fil = _prefix(fil)
    n_unknown = int((~known).sum())
    print(f"gate: {int(known.sum())}/{N} names with EDGAR history; "
          f"{n_unknown} unflaggable (excluded from the gated analysis); "
          f"filing-session flags={int(fil.sum())}")

    # verify the gate against a slow per-point scan of raw dates
    ver_ts = np.arange(MIN_HISTORY, train_last - min(WINDOWS) + 1)
    pick = rng.choice(len(ver_ts) * N, size=300, replace=False)
    for p in pick:
        i, t = int(p % N), int(ver_ts[p // N])
        fast = (cs_fil[i, min(t + K_GATE + 1, T)] - cs_fil[i, t + 1]) > 0
        slow = False
        for d in earn.get(universe[i], ()):
            k = int(np.searchsorted(sd_arr, d, "left"))
            if t + 1 <= k <= min(t + K_GATE, T - 1):
                slow = True
                break
        assert fast == slow, f"gate mismatch {universe[i]}@{spy_dates[t]}"
    print("gate verification OK (300 samples)")

    # ---- statistics ----
    out_rows = []
    desc = {"firing": [], "signs": [], "base": []}
    for W in WINDOWS:
        ts = np.arange(MIN_HISTORY, train_last - W + 1)
        S = len(ts)
        elig = np.isfinite(C[:, ts])
        qs = quint[:, ts]
        hit = {arm: (cs_arm[arm][:, ts + W + 1] - cs_arm[arm][:, ts + 1]) > 0
               for arm in cs_arm}
        hit_sign = {s: (cs_sign[s][:, ts + W + 1] - cs_sign[s][:, ts + 1]) > 0
                    for s in cs_sign}
        fresh = (cs_any[:, ts + 1] - cs_any[:, ts - W]) == 0
        hi_idx = np.minimum(ts + K_GATE + 1, T)
        gate = (cs_fil[:, hi_idx] - cs_fil[:, ts + 1]) > 0
        gate &= known[:, None]
        far = ~gate & known[:, None]

        # peer-relative fires: p90 rank within (session x quintile) cell,
        # over the FULL eligible population (1.14d verbatim — not re-ranked
        # inside the gate)
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
            for glab, gm in (("gated", gate), ("far", far)):
                for mode, sub in (("fresh", fresh),
                                  ("all", np.ones_like(fresh))):
                    fired = fr & pool & gm & sub
                    unfired = ~fr & pool & gm & sub
                    for arm in ("residual", "raw"):
                        h = hit[arm]
                        arr = np.stack([(fired & h).sum(0), fired.sum(0),
                                        (unfired & h).sum(0),
                                        unfired.sum(0)]).astype(float)
                        stats.append({"W": W, "trigger": trig, "gate": glab,
                                      "mode": mode, "arm": arm})
                        cols.append(arr)
            fa = fr & pool & gate
            desc["firing"].append(
                {"W": W, "trigger": trig,
                 "gated_pool": int((pool & gate).sum()),
                 "fire_rate": round(float(
                     fa.sum() / (pool & gate).sum()), 5)
                 if (pool & gate).any() else None,
                 "alerts_per_session": round(float(fa.sum(0).mean()), 1)})
            ff = fr & pool & gate & fresh
            n_hit = int((ff & hit["residual"]).sum())
            desc["signs"].append(
                {"W": W, "trigger": trig, "n_fired_hits": n_hit,
                 "rise_share": round(float(
                     (ff & hit_sign["rise"]).sum() / n_hit), 4)
                 if n_hit else None,
                 "fall_share": round(float(
                     (ff & hit_sign["fall"]).sum() / n_hit), 4)
                 if n_hit else None})

        eg = elig & fresh & gate
        ef = elig & fresh & far
        desc["base"].append(
            {"W": W,
             "gated_share_of_elig": round(float(
                 (elig & gate).sum() / elig.sum()), 4),
             "excluded_unknown_share": round(float(
                 (elig & ~known[:, None]).sum() / elig.sum()), 4),
             "base_gated_fresh": round(float(
                 hit["residual"][eg].mean()), 4) if eg.any() else None,
             "base_far_fresh": round(float(
                 hit["residual"][ef].mean()), 4) if ef.any() else None})
        print(f"\nW={W}: {S} sessions; gated share of eligible="
              f"{desc['base'][-1]['gated_share_of_elig']}; base fresh "
              f"gated={desc['base'][-1]['base_gated_fresh']} vs "
              f"far={desc['base'][-1]['base_far_fresh']}")

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

    print("\n== PRIMARY: fired - unfired forward-episode share WITHIN the "
          "earnings gate ==")
    for glab in ("gated", "far"):
        tag = "" if glab == "gated" else " (contrast color, no authority)"
        for mode in ("fresh", "all"):
            for arm in ("residual", "raw"):
                print(f"\n-- {glab} / {mode} / {arm}{tag} --")
                for row in out_rows:
                    if (row["gate"], row["mode"], row["arm"]) != (
                            glab, mode, arm):
                        continue
                    d, (clo, chi) = row["diff"], row["ci"]
                    star = (" *" if d is not None
                            and (clo > 0 or chi < 0) else "")
                    print(f"  {row['trigger']:<20} {row['W']:>2}d "
                          f"fired={row['fired_hit_share']} "
                          f"(n={row['n_fired']}) "
                          f"unfired={row['unfired_hit_share']} "
                          f"diff={d} [{clo:+.4f},{chi:+.4f}]{star} "
                          f"ratio={row['ratio']}")
    print("\n== gate geometry + base rates ==")
    for r in desc["base"]:
        print(f"  W={r['W']}: gated_share={r['gated_share_of_elig']} "
              f"excluded_unknown={r['excluded_unknown_share']} "
              f"base gated={r['base_gated_fresh']} far={r['base_far_fresh']}")
    print("\n== firing in the gated lane ==")
    for r in desc["firing"]:
        print(f"  {r['trigger']:<20} {r['W']:>2}d pool={r['gated_pool']} "
              f"fire_rate={r['fire_rate']} "
              f"alerts/session={r['alerts_per_session']}")
    print("\n== hit composition (gated fresh residual, descriptive) ==")
    for r in desc["signs"]:
        print(f"  {r['trigger']:<20} {r['W']:>2}d n_hits={r['n_fired_hits']} "
              f"rise={r['rise_share']} fall={r['fall_share']}")

    OUT.write_text(json.dumps({
        "meta": {"registered": "2026-07-29", "seed": SEED, "n_boot": N_BOOT,
                 "block_bootstrap": "circular moving-block, length=W",
                 "gate": f"EDGAR 8-K 2.02 filing in t+1..t+{K_GATE} sessions",
                 "cell": "session x trailing-20d dollar-volume quintile",
                 "rank_threshold": "p90 within cell (full population)",
                 "n_names_known": int(known.sum()),
                 "n_names_unknown": n_unknown,
                 "train_last": spy_dates[train_last]},
        "stats": out_rows, "descriptive": desc}, indent=1))
    print(f"\nwrote {OUT}")


if __name__ == "__main__":
    main()
