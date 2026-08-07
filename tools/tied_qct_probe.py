#!/usr/bin/env python3
"""QCT-presence probe — the o-sign-v2 feasibility linchpin, pre-registration.

For ~200 sampled single-leg TIED option prints (2 sessions), fetch Alpaca SIP
equity trades in [T-2s, T+2s] and measure: does a QCT ('7'-coded) stock print
actually appear? Presence rate, |dt| distribution, per-window multiplicity,
size vs 0.5*contracts*100 proxy. Mechanics only: no quotes, no signing, no
outcome data, no o involvement.
"""
import sys
from collections import Counter
from datetime import date
from pathlib import Path

import httpx
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from config.settings import alpaca_config  # noqa: E402
from tools.condition_labels import TIED_CODES  # noqa: E402
from tools.flow_corpus_rollup import _load_definitions  # noqa: E402
from tools.hedge_footprint import fetch_trades  # noqa: E402
from tools.package_orientation_probe import _clusters, _load  # noqa: E402
from tools.root_classification_build import load as load_rc  # noqa: E402

W_NS = 100_000
SESSIONS = ["2025-09-16", "2026-01-07"]
PER_SESSION = 100
PREM_MIN = 10_000.0

cfg = alpaca_config()
client = httpx.Client(headers={
    "APCA-API-KEY-ID": cfg["api_key_id"],
    "APCA-API-SECRET-KEY": cfg["api_secret_key"],
}, timeout=30.0)
data_url = cfg["data_base_url"]

rc = load_rc(ROOT / "signals" / "options_flow" / "root_classification.json")

tot = Counter()
dts, n_qct_in_win, sizes_ratio = [], [], []
for s in SESSIONS:
    session = date.fromisoformat(s)
    ts, iid, und, right, price, size, cond = None, None, None, None, None, None, None
    ts, iid, und, right, price, size, bid, ask, cond, st = _load(session)
    good = size != 4_294_967_295
    ts, iid, und, right, price, size, cond = (a[good] for a in (ts, iid, und, right, price, size, cond))
    prem = price * size * 100.0
    tied = np.isin(cond, list(TIED_CODES)) & (und >= 0)
    order = np.lexsort((ts[tied], und[tied]))
    t_ts, t_iid, t_und = ts[tied][order], iid[tied][order], und[tied][order]
    t_size, t_prem = size[tied][order], prem[tied][order]
    gid = _clusters(t_ts, t_und, W_NS)
    n_g = int(gid[-1]) + 1
    new_leg = np.r_[True, (t_iid[1:] != t_iid[:-1]) | (gid[1:] != gid[:-1])]
    legs = np.bincount(gid, weights=new_leg.astype(float), minlength=n_g)
    c_prem = np.bincount(gid, weights=t_prem, minlength=n_g)
    first_row = np.r_[0, 1 + np.flatnonzero(gid[1:] != gid[:-1])]
    g_ts = t_ts[first_row]
    g_und = t_und[first_row]
    g_size = np.bincount(gid, weights=t_size.astype(float), minlength=n_g)

    ok = (legs == 1) & (c_prem >= PREM_MIN)
    cand = np.flatnonzero(ok)
    cand = cand[np.argsort(-c_prem[cand])][:PER_SESSION]

    d = _load_definitions(session)
    uniq = d[2]
    roots = [str(u) for u in uniq]
    ds = session.isoformat()

    for gi in cand:
        root = roots[int(g_und[gi])]
        sym = rc.resolve(root, ds)
        if sym is None or rc.classify(root, ds) != "equity":
            tot["skipped_class"] += 1
            continue
        t0 = int(g_ts[gi])
        try:
            tr = fetch_trades(client, data_url, sym,
                              t0 - 30_000_000_000, t0 + 30_000_000_000)
        except Exception:  # noqa: BLE001
            tot["fetch_err"] += 1
            continue
        tot["fetched"] += 1
        has7 = np.char.find(tr["cond"], "7") >= 0 if len(tr["cond"]) else \
            np.empty(0, bool)
        hasv = np.char.find(tr["cond"], "V") >= 0 if len(tr["cond"]) else \
            np.empty(0, bool)
        qct = has7 & hasv
        tot["n7only"] += int((has7 & ~hasv).sum())
        tot["n7V"] += int(qct.sum())
        nq = int(qct.sum())
        n_qct_in_win.append(nq)
        if nq:
            tot["has_qct"] += 1
            dt = (tr["t_ns"][qct] - t0) / 1e6  # ms
            j = int(np.argmin(np.abs(dt)))
            dts.append(float(dt[j]))
            pred = 0.5 * g_size[gi] * 100.0
            if pred > 0:
                sizes_ratio.append(float(tr["size"][qct][j] / pred))
        else:
            tot["no_qct"] += 1

print("totals:", dict(tot))
if n_qct_in_win:
    hist = Counter(min(n, 3) for n in n_qct_in_win)
    print("QCT prints per 4s window (0/1/2/3+):",
          [hist.get(k, 0) for k in range(4)])
if dts:
    a = np.array(dts)
    print(f"nearest-QCT dt ms: p10 {np.percentile(a, 10):.1f}  p50 "
          f"{np.percentile(a, 50):.1f}  p90 {np.percentile(a, 90):.1f}  "
          f"|dt|<=100ms {np.mean(np.abs(a) <= 100):.1%}  "
          f"|dt|<=500ms {np.mean(np.abs(a) <= 500):.1%}")
if sizes_ratio:
    r = np.array(sizes_ratio)
    print(f"stock size / (0.5*contracts*100): p10 {np.percentile(r, 10):.2f}  "
          f"p50 {np.percentile(r, 50):.2f}  p90 {np.percentile(r, 90):.2f}")
