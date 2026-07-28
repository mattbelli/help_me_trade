"""Roadmap 1.7 Addendum A2: the EXACT per-order sign shuffle.

Registered in FLOW_ROADMAP §1.7 Addendum A before running. The original 1.7 null
drew each name-day's tilt from Normal(0, 1/sqrt(N_eff)) — variance exact, tails
approximate — and the registration stated up front that a PASS under that null is
provisional until an exact per-order shuffle confirms it on >=20 sessions. This is
that run: every order gets an independent eps = +/-1, no distributional assumption.

Two differences from the A1 machinery, both deliberate:

* **The flip is per ORDER, not per (bucket, order) pair.** The rollup's
  `premium_sq_orders` treats the parts of one sweep that land in different buckets
  as separate observations; here the whole order carries ONE sign. That is
  strictly more conservative (fewer independent decisions) and is exactly the kind
  of structure a normal approximation cannot see.
* **Classified premium only.** The registered rule tilts over buy + sell, so
  unclassified prints are excluded from the base. A1 summed `premium_usd` across
  all sides. To keep the comparison honest this run recomputes BOTH nulls — exact
  shuffle and normal draw — on the same classified-only base and the same
  sessions, so any gap is the null mechanism and not the data.

  python tools/order_grain_null.py --extract   # decode 40 sessions -> per-order npz
  python tools/order_grain_null.py             # run both nulls, compare
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from db.flow_corpus import INDEX_ROOTS, bars_before_date  # noqa: E402
from tools.flow_corpus_rollup import (  # noqa: E402
    PX,
    TCBBO_DIR,
    UNDEF,
    _bucket_dte,
    _bucket_mny,
    _load_definitions,
    _order_groups,
)

OUT_DIR = ROOT / "data" / "order_grain_null"
N_SESSIONS = 40           # 20 baseline burn-in + 20 tested days
LOOKBACK = 20
MIN_PRESENT = 15
PREMIUM_FLOOR = 250_000.0
Z_EVENT = 2.0
N_DRAWS = 20
SEED = 20260728
SHORT_DTE_IDX = (0, 1)    # DTE_BUCKETS 0-7, 8-30
OTM_MNY_IDX = (2, 3)      # MNY_BUCKETS otm2_10, otm10p
# tcbbo `size` UNDEF sentinel. v6 is protected only INCIDENTALLY (sentinel prints
# fail the definitions join), so this run guards explicitly and reports the count.
UNDEF_SIZE = 4_294_967_295

logger = logging.getLogger("order_grain_null")


def _sessions() -> list[date]:
    """The N_SESSIONS most recent sessions that have BOTH a tcbbo file and defs."""
    days = sorted(p.name.split(".")[0] for p in TCBBO_DIR.glob("*.dbn.zst"))
    return [date.fromisoformat(d) for d in days[-N_SESSIONS:]]


def _extract(session: date) -> None:
    """Decode one session to per-ORDER premium: (underlying, p_all, p_short_otm)."""
    out = OUT_DIR / f"{session.isoformat()}.npz"
    if out.exists():
        return
    defs = _load_definitions(session)
    if defs is None:
        logger.warning("%s: no definitions — skipped", session)
        return
    d_ids, d_und, uniq_und, d_right, d_strike, d_dte = defs

    import databento as dbn

    arr = dbn.DBNStore.from_file(TCBBO_DIR / f"{session.isoformat()}.dbn.zst").to_ndarray()
    n_prints = len(arr)
    if not n_prints:
        return
    n_sentinel = int((arr["size"] == UNDEF_SIZE).sum())
    arr = arr[arr["size"] != UNDEF_SIZE]

    pid = arr["instrument_id"].astype(np.int64)
    idx = np.searchsorted(d_ids, pid)
    np.clip(idx, 0, len(d_ids) - 1, out=idx)
    hit = d_ids[idx] == pid
    arr, idx = arr[hit], idx[hit]
    if not len(arr):
        logger.warning("%s: all prints failed the definitions join", session)
        return
    und_code, right, strike, dte = d_und[idx], d_right[idx], d_strike[idx], d_dte[idx]

    price = arr["price"].astype(np.float64)
    size = arr["size"].astype(np.float64)
    bid = arr["bid_px_00"].astype(np.float64)
    ask = arr["ask_px_00"].astype(np.float64)
    good_q = ((arr["bid_px_00"] != UNDEF) & (arr["ask_px_00"] != UNDEF)
              & (bid > 0) & (ask > 0) & (ask > bid))
    mid = np.where(good_q, (bid + ask) / 2.0, np.nan)
    # The registered rule tilts over buy + sell only; price == mid is unclassified
    # and contributes to neither, so it is out of the null's premium base too.
    classified = good_q & (price != mid)
    premium = price / PX * size * 100.0

    closes = bars_before_date(session.isoformat())
    spot_by_code = np.array([closes.get(str(s), (np.nan, ""))[0] for s in uniq_und],
                            dtype=np.float64)
    spot = spot_by_code[und_code]
    is_index = np.array([str(s) in INDEX_ROOTS for s in uniq_und])[und_code]
    dte_b = _bucket_dte(dte)
    mny_b = _bucket_mny(strike, spot, right, is_index)
    short_otm = np.isin(dte_b, SHORT_DTE_IDX) & np.isin(mny_b, OTM_MNY_IDX)

    gid, n_groups = _order_groups(arr["ts_event"].astype(np.int64), pid[hit])
    p_all = np.bincount(gid[classified], weights=premium[classified], minlength=n_groups)
    m_so = classified & short_otm
    p_so = np.bincount(gid[m_so], weights=premium[m_so], minlength=n_groups)
    # An instrument maps to exactly one underlying, so a group does too.
    und_of_g = np.zeros(n_groups, dtype=np.int64)
    und_of_g[gid] = und_code

    keep = p_all > 0
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        out, und=und_of_g[keep].astype(np.int32), p_all=p_all[keep],
        p_so=p_so[keep], names=np.array([str(s) for s in uniq_und]),
    )
    logger.info("%s: %s prints -> %s orders (%s sentinel-size dropped)",
                session, f"{n_prints:,}", f"{int(keep.sum()):,}", f"{n_sentinel:,}")


def _load_panel(sessions: list[date]) -> tuple:
    """Per-order premiums -> ragged per (name, day) lists, plus the day totals."""
    per_day, all_names = [], set()
    for s in sessions:
        f = OUT_DIR / f"{s.isoformat()}.npz"
        if not f.exists():
            per_day.append(None)
            continue
        with np.load(f, allow_pickle=False) as z:
            names = z["names"]
            und = names[z["und"]]
            # float32 per order: 372M orders across the window would be 7.4 GB at
            # float64. Sums still accumulate in float64 (bincount's output), and a
            # 1e-7 relative error on one order's premium cannot move a tilt ratio.
            per_day.append((und, z["p_all"].astype(np.float32),
                            z["p_so"].astype(np.float32)))
            all_names.update(und.tolist())
    names = np.array(sorted(all_names))
    ni = {n: i for i, n in enumerate(names.tolist())}
    n_names, n_days = len(names), len(sessions)
    # group index -> row, so a shuffle is one vectorised pass per day
    rows, groups = [], []
    for day in per_day:
        if day is None:
            rows.append(None)
            groups.append(None)
            continue
        und, p_all, p_so = day
        r = np.fromiter((ni[x] for x in und.tolist()), np.int32, len(und))
        rows.append(r)
        groups.append((p_all, p_so))
    return names, rows, groups, n_names, n_days


def _fire(tilt, present, eligible):
    """The registered rule, verbatim, on a (names x days) tilt panel."""
    n_names, n_days = tilt.shape
    fired = np.zeros(tilt.shape, bool)
    tested = np.zeros(tilt.shape, bool)
    vals = np.where(present, tilt, 0.0)
    cnt = present.astype(np.float64)
    cs = np.cumsum(np.c_[np.zeros(n_names), vals], 1)
    cq = np.cumsum(np.c_[np.zeros(n_names), vals ** 2], 1)
    ck = np.cumsum(np.c_[np.zeros(n_names), cnt], 1)
    for d in range(LOOKBACK, n_days):
        lo, hi = d - LOOKBACK, d
        s, s2, k = cs[:, hi] - cs[:, lo], cq[:, hi] - cq[:, lo], ck[:, hi] - ck[:, lo]
        with np.errstate(invalid="ignore", divide="ignore"):
            mean = s / np.maximum(k, 1)
            std = np.sqrt(np.maximum(s2 / np.maximum(k, 1) - mean ** 2, 0.0))
            z = (tilt[:, d] - mean) / std
        ok = (k >= MIN_PRESENT) & eligible[:, d] & present[:, d] & np.isfinite(z) & (std > 0)
        tested[:, d] = ok
        fired[:, d] = ok & (np.abs(z) >= Z_EVENT)
    return fired, tested


def _report(label, fired_q, tested_q):
    print(f"\n=== {label} ===")
    print("  quintile          tested       fired     rate")
    rates = []
    for i in range(5):
        r = fired_q[i] / tested_q[i] if tested_q[i] else float("nan")
        rates.append(r)
        name = ["Q1 least liquid", "Q2", "Q3", "Q4", "Q5 most liquid"][i]
        print(f"  {name:<16} {tested_q[i]:>10,} {fired_q[i]:>10,}  {r:>7.3%}")
    pooled = fired_q.sum() / max(tested_q.sum(), 1)
    print(f"  {'pooled':<16} {tested_q.sum():>10,} {fired_q.sum():>10,}  {pooled:>7.3%}")
    ratio = rates[0] / rates[4] if rates[4] else float("nan")
    print(f"  Q1/Q5 = {ratio:.2f}x")
    return pooled, ratio


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--extract", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    sessions = _sessions()
    logger.info("sessions: %s .. %s (%d)", sessions[0], sessions[-1], len(sessions))

    if args.extract:
        for s in sessions:
            _extract(s)
        return

    names, rows, groups, n_names, n_days = _load_panel(sessions)
    logger.info("panel: %d names x %d sessions", n_names, n_days)

    # Day totals and N_eff are FIXED market structure — identical for both nulls.
    P_ALL = np.zeros((n_names, n_days))
    P_SO = np.zeros((n_names, n_days))
    Q_ALL = np.zeros((n_names, n_days))
    Q_SO = np.zeros((n_names, n_days))
    # bincount, not np.add.at: the exact null does ~7.4e9 scatter-adds and add.at
    # is ~30x slower, which is the difference between minutes and days.
    for d in range(n_days):
        if rows[d] is None:
            continue
        p_all, p_so = (groups[d][0].astype(np.float64), groups[d][1].astype(np.float64))
        P_ALL[:, d] = np.bincount(rows[d], weights=p_all, minlength=n_names)
        P_SO[:, d] = np.bincount(rows[d], weights=p_so, minlength=n_names)
        Q_ALL[:, d] = np.bincount(rows[d], weights=p_all ** 2, minlength=n_names)
        Q_SO[:, d] = np.bincount(rows[d], weights=p_so ** 2, minlength=n_names)

    med = np.array([np.median(r[r > 0]) if (r > 0).any() else 0.0 for r in P_ALL])
    live = med > 0
    quint = np.full(n_names, -1, np.int64)
    quint[live] = np.digitize(med[live], np.quantile(med[live], [0.2, 0.4, 0.6, 0.8]))
    eligible = P_ALL >= PREMIUM_FLOOR

    sig, pres = {}, {}
    for arm, P, Q in (("ALL", P_ALL, Q_ALL), ("SHORT_OTM", P_SO, Q_SO)):
        with np.errstate(invalid="ignore", divide="ignore"):
            n_eff = np.where(Q > 0, P ** 2 / Q, np.nan)
        s = np.where(np.isfinite(n_eff) & (n_eff > 0), 1 / np.sqrt(n_eff), np.nan)
        sig[arm], pres[arm] = s, (P > 0) & np.isfinite(s)

    counts = {(a, m): [np.zeros(5, np.int64), np.zeros(5, np.int64)]
              for a in ("ALL", "SHORT_OTM") for m in ("exact", "normal")}

    def _tally(arm, mech, tilt):
        f, t = _fire(tilt, pres[arm], eligible)
        fq, tq = counts[(arm, mech)]
        for i in range(5):
            m = quint == i
            fq[i] += int(f[m].sum())
            tq[i] += int(t[m].sum())

    # EXACT: one coin flip per order, shared by both arms — a sweep is one
    # decision, so its short-OTM legs cannot get a different sign from its others.
    rng = np.random.default_rng(SEED)
    for draw in range(N_DRAWS):
        t_all = np.zeros((n_names, n_days))
        t_so = np.zeros((n_names, n_days))
        for d in range(n_days):
            if rows[d] is None:
                continue
            p_all, p_so = groups[d]
            eps = (rng.integers(0, 2, size=len(p_all), dtype=np.int8) * 2 - 1
                   ).astype(np.float64)
            num_a = np.bincount(rows[d], weights=eps * p_all, minlength=n_names)
            num_s = np.bincount(rows[d], weights=eps * p_so, minlength=n_names)
            with np.errstate(invalid="ignore", divide="ignore"):
                t_all[:, d] = np.where(P_ALL[:, d] > 0, num_a / P_ALL[:, d], 0.0)
                t_so[:, d] = np.where(P_SO[:, d] > 0, num_s / P_SO[:, d], 0.0)
        _tally("ALL", "exact", t_all)
        _tally("SHORT_OTM", "exact", t_so)
        logger.info("exact draw %d/%d", draw + 1, N_DRAWS)

    # NORMAL: the original 1.7 mechanism, on the SAME sessions and premium base.
    rng = np.random.default_rng(SEED)
    for _ in range(N_DRAWS):
        for arm in ("ALL", "SHORT_OTM"):
            s = sig[arm]
            _tally(arm, "normal",
                   rng.standard_normal(s.shape) * np.nan_to_num(s, nan=0.0))

    out = {k: _report(f"arm {k[0]} — {k[1]} null", *v) for k, v in counts.items()}

    print("\n=== A2 registered comparison (bar: |Δpooled| ≤ 2.0pt, |ΔQ1/Q5| ≤ 0.5x) ===")
    for arm in ("ALL", "SHORT_OTM"):
        pe, re_ = out[(arm, "exact")]
        pn, rn = out[(arm, "normal")]
        dp, dr = abs(pe - pn) * 100, abs(re_ - rn)
        ok = "PASS" if (dp <= 2.0 and dr <= 0.5) else "FAIL"
        print(f"  {arm:<10} pooled {pe:.3%} vs {pn:.3%} (Δ{dp:.2f}pt)   "
              f"Q1/Q5 {re_:.2f}x vs {rn:.2f}x (Δ{dr:.2f}x)   -> {ok}")


if __name__ == "__main__":
    main()
