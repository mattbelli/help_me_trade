"""Roadmap 1.7: is the registered `|z| >= 2` rule a liquidity detector?

Method registered in FLOW_ROADMAP §1.7 BEFORE this ran. Shorthand: kill the
signal, keep the market structure, then run the rule exactly as registered and
see who still fires.

The null randomizes each **ORDER's** buy/sell classification (a sweep is one
decision — shuffling per print would fake independence the tape does not have).
For a name-day the null tilt numerator is sum(eps_i * p_i) with eps = +/-1 per
order and denominator sum(p_i), so E[tilt] = 0 and Var(tilt) = sum(p^2)/(sum p)^2
= 1/N_eff, with N_eff = (sum p)^2 / sum(p^2) the effective number of independent
orders. N_eff comes exactly from the rollup's `premium_sq_orders`; the null tilt
is drawn Normal(0, 1/sqrt(N_eff)).

Then the REGISTERED rule verbatim: trailing 20 days of the name's own null tilt
excluding today, z = (tilt - mean20)/std20, >=15 of 20 days present, liquidity
floor >= $250k total option premium on the day, event |z| >= 2.0. Both registered
arms: ALL, and SHORT_OTM = dte_bucket in (0-7, 8-30) and mny_bucket in
(otm2_10, otm10p).

PASS = least-liquid quintile firing rate < 2.0x the most-liquid quintile's.
⚠️ Registered asymmetry: a FAIL is decisive (the artifact survives a null whose
tails are conservative); a PASS is provisional and needs an exact per-order
shuffle on >=20 sessions before 1.7 closes.

    python tools/z_null_probe.py            # uses cached aggregate if present
    python tools/z_null_probe.py --rebuild  # re-scan the 82M-row rollup
"""

from __future__ import annotations

import argparse
import logging
import sqlite3
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
CORPUS_DB = ROOT / "data" / "flow_corpus.db"
CACHE = ROOT / "data" / "z_null_aggregate.npz"

LOOKBACK = 20
MIN_PRESENT = 15
PREMIUM_FLOOR = 250_000.0
Z_EVENT = 2.0
N_DRAWS = 20              # independent null universes; firing rates are pooled
SEED = 20260728
SHORT_DTE = ("0-7", "8-30")
OTM_MNY = ("otm2_10", "otm10p")

logger = logging.getLogger("z_null_probe")


def _aggregate() -> dict[str, np.ndarray]:
    """One scan of flow_rollup -> per (date, underlying) sums for both arms."""
    conn = sqlite3.connect(f"file:{CORPUS_DB}?mode=ro", uri=True)
    q = f"""
        SELECT date, underlying,
               SUM(premium_usd)                                        AS p_all,
               SUM(premium_sq_orders)                                  AS q_all,
               SUM(CASE WHEN dte_bucket IN {SHORT_DTE} AND mny_bucket IN {OTM_MNY}
                        THEN premium_usd ELSE 0 END)                   AS p_so,
               SUM(CASE WHEN dte_bucket IN {SHORT_DTE} AND mny_bucket IN {OTM_MNY}
                        THEN premium_sq_orders ELSE 0 END)             AS q_so
        FROM flow_rollup
        GROUP BY date, underlying
    """
    logger.info("scanning flow_rollup (82M rows, one pass)...")
    rows = conn.execute(q).fetchall()
    logger.info("aggregated to %s name-days", f"{len(rows):,}")
    dates = np.array([r[0] for r in rows])
    unds = np.array([r[1] for r in rows])
    out = {
        "date": dates, "und": unds,
        "p_all": np.array([r[2] or 0.0 for r in rows]),
        "q_all": np.array([r[3] or 0.0 for r in rows]),
        "p_so": np.array([r[4] or 0.0 for r in rows]),
        "q_so": np.array([r[5] or 0.0 for r in rows]),
    }
    np.savez_compressed(CACHE, **out)
    return out


def _fire_rate(tilt: np.ndarray, present: np.ndarray, eligible: np.ndarray
               ) -> tuple[np.ndarray, np.ndarray]:
    """Registered rule on a (names x days) null tilt panel.

    Returns (fired, tested) boolean/valid masks aligned to the panel. Baseline is
    the trailing LOOKBACK days EXCLUDING today, needs >= MIN_PRESENT present days,
    and the day itself must clear the premium floor (`eligible`).
    """
    n_names, n_days = tilt.shape
    fired = np.zeros_like(tilt, dtype=bool)
    tested = np.zeros_like(tilt, dtype=bool)
    vals = np.where(present, tilt, 0.0)
    cnt = present.astype(np.float64)
    # rolling sums over the PRIOR LOOKBACK days (exclusive of today)
    csum = np.cumsum(np.concatenate([np.zeros((n_names, 1)), vals], axis=1), axis=1)
    csq = np.cumsum(np.concatenate([np.zeros((n_names, 1)), vals ** 2], axis=1), axis=1)
    ccnt = np.cumsum(np.concatenate([np.zeros((n_names, 1)), cnt], axis=1), axis=1)
    for d in range(LOOKBACK, n_days):
        lo, hi = d - LOOKBACK, d           # [lo, hi) = prior 20 days
        s = csum[:, hi] - csum[:, lo]
        s2 = csq[:, hi] - csq[:, lo]
        k = ccnt[:, hi] - ccnt[:, lo]
        ok = (k >= MIN_PRESENT) & eligible[:, d] & present[:, d]
        with np.errstate(invalid="ignore", divide="ignore"):
            mean = s / np.maximum(k, 1)
            var = s2 / np.maximum(k, 1) - mean ** 2
            std = np.sqrt(np.maximum(var, 0.0))
            z = (tilt[:, d] - mean) / std
        good = ok & np.isfinite(z) & (std > 0)
        tested[:, d] = good
        fired[:, d] = good & (np.abs(z) >= Z_EVENT)
    return fired, tested


def _run_arm(name: str, p: np.ndarray, q: np.ndarray, p_all: np.ndarray,
             quint: np.ndarray, rng: np.random.Generator) -> None:
    present = p > 0
    with np.errstate(invalid="ignore", divide="ignore"):
        n_eff = np.where(q > 0, p ** 2 / q, np.nan)
    eligible = p_all >= PREMIUM_FLOOR
    sigma = np.where(np.isfinite(n_eff) & (n_eff > 0), 1.0 / np.sqrt(n_eff), np.nan)

    fired_q = np.zeros(5, dtype=np.int64)
    tested_q = np.zeros(5, dtype=np.int64)
    for _ in range(N_DRAWS):
        tilt = rng.standard_normal(sigma.shape) * np.nan_to_num(sigma, nan=0.0)
        f, t = _fire_rate(tilt, present & np.isfinite(sigma), eligible)
        for i in range(5):
            m = quint == i
            fired_q[i] += int(f[m].sum())
            tested_q[i] += int(t[m].sum())

    print(f"\n=== arm: {name} — null firing rate by liquidity quintile "
          f"({N_DRAWS} draws) ===")
    print("  quintile          tested       fired     rate")
    rates = []
    for i in range(5):
        r = fired_q[i] / tested_q[i] if tested_q[i] else float("nan")
        rates.append(r)
        label = ["Q1 least liquid", "Q2", "Q3", "Q4", "Q5 most liquid"][i]
        print(f"  {label:<16} {tested_q[i]:>10,} {fired_q[i]:>10,}  {r:>7.3%}")
    pooled = fired_q.sum() / max(tested_q.sum(), 1)
    print(f"  {'pooled':<16} {tested_q.sum():>10,} {fired_q.sum():>10,}  {pooled:>7.3%}")
    if np.isfinite(rates[0]) and rates[4] > 0:
        ratio = rates[0] / rates[4]
        verdict = "FAIL — liquidity detector" if ratio >= 2.0 else "PASS (provisional)"
        print(f"  Q1/Q5 ratio = {ratio:.2f}x   -> {verdict}  (registered bar: < 2.00x)")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rebuild", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if args.rebuild or not CACHE.exists():
        agg = _aggregate()
    else:
        with np.load(CACHE, allow_pickle=False) as z:
            agg = {k: z[k] for k in z.files}
        logger.info("loaded cached aggregate: %s name-days", f"{len(agg['date']):,}")

    dates = np.unique(agg["date"])
    names = np.unique(agg["und"])
    di = {d: i for i, d in enumerate(dates.tolist())}
    ni = {n: i for i, n in enumerate(names.tolist())}
    shape = (len(names), len(dates))
    logger.info("panel: %d names x %d sessions", *shape)

    P_ALL = np.zeros(shape)
    Q_ALL = np.zeros(shape)
    P_SO = np.zeros(shape)
    Q_SO = np.zeros(shape)
    r = np.fromiter((ni[x] for x in agg["und"].tolist()), np.int64, len(agg["und"]))
    c = np.fromiter((di[x] for x in agg["date"].tolist()), np.int64, len(agg["date"]))
    P_ALL[r, c] = agg["p_all"]
    Q_ALL[r, c] = agg["q_all"]
    P_SO[r, c] = agg["p_so"]
    Q_SO[r, c] = agg["q_so"]

    # quintiles by each name's MEDIAN daily total premium (fixed per name)
    med = np.array([np.median(row[row > 0]) if (row > 0).any() else 0.0 for row in P_ALL])
    live = med > 0
    quint = np.full(len(names), -1, dtype=np.int64)
    edges = np.quantile(med[live], [0.2, 0.4, 0.6, 0.8])
    quint[live] = np.digitize(med[live], edges)
    for i in range(5):
        sel = med[quint == i]
        if len(sel):
            logger.info("Q%d: %d names, median daily premium $%s-$%s",
                        i + 1, len(sel), f"{sel.min():,.0f}", f"{sel.max():,.0f}")

    rng = np.random.default_rng(SEED)
    _run_arm("ALL", P_ALL, Q_ALL, P_ALL, quint, rng)
    _run_arm("SHORT_OTM", P_SO, Q_SO, P_ALL, quint, rng)


if __name__ == "__main__":
    main()
