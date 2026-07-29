"""Roadmap 1.11 + 1.12: the event set and its matched control set.

Registered 2026-07-29 (FLOW_ROADMAP.md, joint 1.11+1.12 block) BEFORE this ran.
Two deliverables, and a hard line between them:

1. EVENTS (1.11): sessions where a name's one-day excess-vs-SPY return exceeds
   3.0x its own trailing ATR14 (residual arm; raw arm graded alongside), rises
   and falls separate, consecutive event days within 5 sessions collapsed into
   episodes dated at their FIRST day.
2. CONTROLS + BASE RATES (1.12): 5 controls per episode — same session, same
   EDGAR-SIC sector, same trailing-20d dollar-volume quintile, |z|<1 that day,
   no event of their own in t-20..t — and the registered base-rate battery
   computed on CONTROL name-days ONLY.

THE LINE: mover flow is never read here. The only flow queries feed control
windows. 1.13 registers the mover-vs-control comparisons before anyone looks.

Base rates are reported over UNIQUE control (name, session) windows — a control
drawn for several episodes counts once in the descriptive table (draw counts are
recorded separately; 1.13's paired comparison uses the per-episode mapping).

Output: data/research/mover_controls.json — the frozen input 1.13 profiles
against. Deterministic: SEED=20260729, episodes processed in sorted order.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from db.flow_corpus import INDEX_ROOTS  # noqa: E402
from tools.flow_tilt import OTM_MNY, SHORT_DTE  # noqa: E402
from tools.multileg_arm_probe import ETF_SET  # noqa: E402

TRAIN_END = "2026-05-31"
CORPUS_DB = ROOT / "data" / "flow_corpus.db"
FUND_DB = ROOT / "data" / "research" / "fundamentals.db"
OUT = ROOT / "data" / "research" / "mover_controls.json"

Z_EVENT = 3.0          # registered event threshold, own-ATR14 units
Z_QUIET = 1.0          # registered control "did not move" cap
EPISODE_GAP = 5        # sessions; event days closer than this join one episode
N_CONTROLS = 5
SEED = 20260729
MIN_HISTORY = 21       # event date needs >= this many prior bar sessions
ACTIVE_FLOOR = 250_000  # registered active-day premium floor
WINDOWS = (5, 10, 20)
QUANTS = (10, 25, 50, 75, 90, 99)
LADDER = ("sector+quintile", "quintile", "sector", "date-only")


def _bars(universe: list[str]):
    """SPY-calendar OHLCV matrices for the universe."""
    con = sqlite3.connect(f"file:{CORPUS_DB}?mode=ro", uri=True)
    want = set(universe) | {"SPY"}
    marks = ",".join("?" * len(want))
    rows = con.execute(
        f"SELECT symbol, date, h, l, c, v FROM equity_bars "
        f"WHERE adjustment='split' AND symbol IN ({marks}) ORDER BY date",
        sorted(want)).fetchall()
    con.close()
    spy = {d: c for s, d, h, lo, c, v in rows if s == "SPY"}
    spy_dates = sorted(spy)
    di = {d: t for t, d in enumerate(spy_dates)}
    ni = {n: i for i, n in enumerate(universe)}
    shape = (len(universe), len(spy_dates))
    H, L, C, V = (np.full(shape, np.nan) for _ in range(4))
    for s, d, h, lo, c, v in rows:
        i, t = ni.get(s), di.get(d)
        if i is not None and t is not None:
            H[i, t], L[i, t], C[i, t], V[i, t] = h, lo, c, v
    spy_c = np.array([spy[d] for d in spy_dates])
    return spy_dates, H, L, C, V, spy_c


def _z_scores(H, L, C, spy_c):
    """z_res, z_raw (names x T): one-day return in own-ATR14 units; col 0 NaN."""
    N, T = C.shape
    pad = np.full((N, 1), np.nan)
    with np.errstate(invalid="ignore", divide="ignore"):
        ret = np.concatenate([pad, C[:, 1:] / C[:, :-1] - 1.0], axis=1)
        exc = ret - np.concatenate(
            [[np.nan], spy_c[1:] / spy_c[:-1] - 1.0])[None, :]
        tr = np.concatenate([pad, np.maximum(
            H[:, 1:] - L[:, 1:],
            np.maximum(np.abs(H[:, 1:] - C[:, :-1]),
                       np.abs(L[:, 1:] - C[:, :-1]))) / C[:, :-1]], axis=1)
    atr = np.full((N, T), np.nan)
    for t in range(15, T):
        w = tr[:, t - 14:t]
        ok = np.isfinite(w).sum(axis=1) >= 10
        with np.errstate(invalid="ignore"):
            m = np.nanmean(np.where(np.isfinite(w), w, np.nan), axis=1)
        atr[:, t] = np.where(ok, m, np.nan)
    with np.errstate(invalid="ignore", divide="ignore"):
        return exc / atr, ret / atr


def _episodes(z, arm: str, train_mask):
    """Event days -> episodes [(name_idx, t, sign, n_days)], first-day dated."""
    eps = []
    with np.errstate(invalid="ignore"):
        for sign, hits in (("rise", z > Z_EVENT), ("fall", z < -Z_EVENT)):
            hits = hits & train_mask[None, :]
            hits[:, :MIN_HISTORY] = False
            for i in np.flatnonzero(hits.any(axis=1)):
                ts = np.flatnonzero(hits[i])
                starts = np.flatnonzero(
                    np.concatenate([[True], np.diff(ts) > EPISODE_GAP]))
                bounds = np.append(starts, len(ts))
                for a, b in zip(bounds[:-1], bounds[1:]):
                    eps.append((int(i), int(ts[a]), sign, arm, int(b - a)))
    return eps


def _tier_quintiles(C, V):
    """Per-session liquidity quintile (0-4) of trailing-20d median $ volume;
    -1 where ineligible (<15 valid sessions)."""
    N, T = C.shape
    dv = C * V
    q = np.full((N, T), -1, np.int8)
    for t in range(MIN_HISTORY, T):
        w = dv[:, t - 20:t]
        ok = np.isfinite(w).sum(axis=1) >= 15
        med = np.nanmedian(np.where(np.isfinite(w), w, np.nan), axis=1)
        idx = np.flatnonzero(ok & np.isfinite(med))
        if not idx.size:
            continue
        order = np.argsort(med[idx], kind="stable")
        ranks = np.empty(len(idx), int)
        ranks[order] = np.arange(len(idx))
        q[idx, t] = np.minimum(4, (5 * ranks) // len(idx))
    return q


def _sectors(universe: list[str]) -> dict[str, str]:
    con = sqlite3.connect(f"file:{FUND_DB}?mode=ro", uri=True)
    rows = con.execute("SELECT symbol, sector FROM companies").fetchall()
    con.close()
    bad = {None, "", "Unknown"}
    m = {s: sec for s, sec in rows if sec not in bad}
    return {n: m[n] for n in universe if n in m}


def _flow_features(universe: list[str], spy_dates: list[str]):
    """Per name-day aggregates on the registered fenced tilt basis
    (side in buy/sell, multileg=0), plus the blended totals the multileg-share
    feature is defined on."""
    cls = "side IN ('buy','sell') AND multileg=0"
    con = sqlite3.connect(f"file:{CORPUS_DB}?mode=ro", uri=True)
    rows = con.execute(f"""
        SELECT date, underlying,
          SUM(CASE WHEN {cls} THEN premium_usd ELSE 0 END),
          SUM(CASE WHEN {cls} AND ((side='buy' AND right='C') OR
                                   (side='sell' AND right='P'))
              THEN premium_usd ELSE 0 END),
          MAX(CASE WHEN {cls} THEN premium_max ELSE NULL END),
          SUM(CASE WHEN {cls} AND right='C' THEN premium_usd ELSE 0 END),
          SUM(CASE WHEN {cls} AND dte_bucket IN {SHORT_DTE!r}
              THEN premium_usd ELSE 0 END),
          SUM(CASE WHEN {cls} AND mny_bucket IN {OTM_MNY!r}
              THEN premium_usd ELSE 0 END),
          SUM(premium_usd),
          SUM(CASE WHEN multileg=1 THEN premium_usd ELSE 0 END)
        FROM flow_rollup GROUP BY date, underlying""").fetchall()
    con.close()
    di = {d: t for t, d in enumerate(spy_dates)}
    ni = {n: i for i, n in enumerate(universe)}
    shape = (len(universe), len(spy_dates))
    F = {k: np.zeros(shape) for k in
         ("cls", "bull", "call", "short", "otm", "total", "ml")}
    pmax = np.full(shape, np.nan)
    for d, u, c, bull, pm, call, sh, otm, tot, ml in rows:
        i, t = ni.get(u), di.get(d)
        if i is None or t is None:
            continue
        F["cls"][i, t], F["bull"][i, t] = c, bull
        F["call"][i, t], F["short"][i, t], F["otm"][i, t] = call, sh, otm
        F["total"][i, t], F["ml"][i, t] = tot, ml
        if pm is not None:
            pmax[i, t] = pm
    return F, pmax


def _max_run(signs: np.ndarray) -> int:
    """Longest same-sign run in the ordered sequence of active-day tilt signs
    (zeros break runs)."""
    best = cur = 0
    prev = 0
    for s in signs:
        cur = cur + 1 if (s == prev and s != 0) else (1 if s != 0 else 0)
        prev = s
        best = max(best, cur)
    return best


def _window_features(i: int, t: int, W: int, F, pmax) -> dict:
    lo = t - W
    cls = F["cls"][i, lo:t]
    bull = F["bull"][i, lo:t]
    active = cls >= ACTIVE_FLOOR
    out = {"active_days": int(active.sum())}
    bb = float(cls.sum())          # bull + bear = classified premium
    net = float((2 * bull - cls).sum())
    out["window_tilt"] = net / bb if bb > 0 else None
    with np.errstate(invalid="ignore", divide="ignore"):
        d_tilt = np.where(cls > 0, (2 * bull - cls) / cls, 0.0)
    out["max_run"] = _max_run(np.sign(d_tilt[active]).astype(int))
    out["concentration"] = (float(cls.max()) / bb
                            if active.any() and bb > 0 else None)
    hist = pmax[i, max(0, t - 60):t]
    hist = hist[np.isfinite(hist)]
    wmax = pmax[i, lo:t]
    wmax = wmax[np.isfinite(wmax)]
    out["largest_print_ratio"] = (
        float(wmax.max() / np.median(hist))
        if len(hist) >= 20 and wmax.size and np.median(hist) > 0 else None)
    for key, col in (("call_share", "call"), ("short_dte_share", "short"),
                     ("otm_share", "otm")):
        out[key] = float(F[col][i, lo:t].sum() / bb) if bb > 0 else None
    tot = float(F["total"][i, lo:t].sum())
    out["multileg_share"] = float(F["ml"][i, lo:t].sum() / tot) if tot > 0 else None
    return out


def main() -> None:
    fence = set(INDEX_ROOTS) | ETF_SET
    con = sqlite3.connect(f"file:{CORPUS_DB}?mode=ro", uri=True)
    universe = sorted(
        r[0] for r in con.execute("SELECT DISTINCT underlying FROM flow_rollup")
        if r[0] not in fence)
    con.close()
    spy_dates, H, L, C, V, spy_c = _bars(universe)
    train_mask = np.array([d <= TRAIN_END for d in spy_dates])
    z_res, z_raw = _z_scores(H, L, C, spy_c)

    episodes = sorted(
        _episodes(z_res, "residual", train_mask)
        + _episodes(z_raw, "raw", train_mask),
        key=lambda e: (spy_dates[e[1]], universe[e[0]], e[2], e[3]))
    n_by = {}
    for _, _, sign, arm, _ in episodes:
        n_by[(arm, sign)] = n_by.get((arm, sign), 0) + 1
    print(f"universe {len(universe)} names; episodes: "
          + ", ".join(f"{a}/{s}={n}" for (a, s), n in sorted(n_by.items())))

    # event DAYS (either sign, either arm) — the control contamination mask
    with np.errstate(invalid="ignore"):
        event_any = (((np.abs(z_res) > Z_EVENT) | (np.abs(z_raw) > Z_EVENT))
                     & train_mask[None, :])
    event_any[:, :MIN_HISTORY] = False
    # recent_event[i, t] = any event day in t-20..t
    cum = np.cumsum(np.concatenate(
        [np.zeros((len(universe), 1), int), event_any.astype(int)], axis=1), axis=1)
    T = len(spy_dates)
    recent = np.zeros_like(event_any)
    for t in range(T):
        recent[:, t] = (cum[:, t + 1] - cum[:, max(0, t - 20)]) > 0

    quint = _tier_quintiles(C, V)
    sectors = _sectors(universe)
    sec_idx = np.array([sectors.get(n, "") for n in universe])
    print(f"sector coverage: {sum(1 for n in universe if n in sectors)}"
          f"/{len(universe)} names")

    rng = np.random.default_rng(SEED)
    ladder_used = dict.fromkeys(LADDER, 0)
    dropped = no_sector = 0
    ctrl_map = []          # per episode: list of (name_idx, rung)
    with np.errstate(invalid="ignore"):
        quiet = np.abs(z_res) < Z_QUIET
    for i, t, sign, arm, _ in episodes:
        base = quiet[:, t] & ~recent[:, t] & (quint[:, t] >= 0)
        base[i] = False
        sec = sec_idx[i]
        rungs = []
        if sec:
            rungs.append(("sector+quintile",
                          base & (sec_idx == sec) & (quint[:, t] == quint[i, t])))
        else:
            no_sector += 1
        rungs.append(("quintile", base & (quint[:, t] == quint[i, t])))
        if sec:
            rungs.append(("sector", base & (sec_idx == sec)))
        rungs.append(("date-only", base))
        picked, used = [], set()
        for rung, mask in rungs:
            cand = np.setdiff1d(np.flatnonzero(mask), sorted(used))
            if cand.size:
                take = rng.choice(cand, size=min(N_CONTROLS - len(picked),
                                                 cand.size), replace=False)
                picked += [(int(c), rung) for c in np.sort(take)]
                used.update(int(c) for c in take)
                ladder_used[rung] += len(take)
            if len(picked) == N_CONTROLS:
                break
        if not picked:
            dropped += 1
        ctrl_map.append(picked)
    print(f"ladder use: {ladder_used}; episodes w/o sector {no_sector}; "
          f"dropped (zero candidates) {dropped}")

    # ---- base rates: UNIQUE control (name, session) windows, flow read here ONLY
    F, pmax = _flow_features(universe, spy_dates)
    uniq = sorted({(c, ep[1]) for ep, picks in zip(episodes, ctrl_map)
                   for c, _ in picks})
    n_draws = sum(len(p) for p in ctrl_map)
    print(f"control draws {n_draws}, unique control name-days {len(uniq)}")
    base_rates = {}
    for W in WINDOWS:
        feats = [_window_features(i, t, W, F, pmax) for i, t in uniq]
        table = {}
        for key in feats[0]:
            vals = np.array([f[key] for f in feats if f[key] is not None], float)
            table[key] = {
                "n": len(vals), "n_na": len(feats) - len(vals),
                **({f"p{q}": round(float(np.percentile(vals, q)), 4)
                    for q in QUANTS} if len(vals) else {})}
        base_rates[str(W)] = table
        print(f"\n-- window {W}d base rates ({len(feats)} unique control "
              f"windows) --")
        for key, row in table.items():
            qs = " ".join(f"p{q}={row[f'p{q}']:g}" for q in QUANTS
                          if f"p{q}" in row)
            print(f"  {key:<20} n={row['n']:<6} na={row['n_na']:<5} {qs}")

    OUT.write_text(json.dumps({
        "meta": {"registered": "2026-07-29", "seed": SEED, "z_event": Z_EVENT,
                 "z_quiet": Z_QUIET, "episode_gap": EPISODE_GAP,
                 "n_controls": N_CONTROLS, "train_end": TRAIN_END,
                 "active_floor": ACTIVE_FLOOR, "windows": list(WINDOWS),
                 "ladder_used": ladder_used, "episodes_no_sector": no_sector,
                 "episodes_dropped": dropped,
                 "sector_coverage": sum(1 for n in universe if n in sectors),
                 "universe_size": len(universe)},
        "episodes": [
            {"name": universe[i], "date": spy_dates[t], "sign": sign,
             "arm": arm, "event_days": nd,
             "z_res": round(float(z_res[i, t]), 3) if np.isfinite(z_res[i, t]) else None,
             "z_raw": round(float(z_raw[i, t]), 3) if np.isfinite(z_raw[i, t]) else None,
             "controls": [{"name": universe[c], "rung": rung}
                          for c, rung in picks]}
            for (i, t, sign, arm, nd), picks in zip(episodes, ctrl_map)],
        "control_base_rates": base_rates,
    }, indent=1))
    print(f"\nwrote {OUT}")


if __name__ == "__main__":
    main()
