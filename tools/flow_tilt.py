"""The registered call/put directional tilt — the prereg statistic, finally built.

`FLOW_BASELINE_PREREG_2026-07-26.md:68-72` registers

    bull = call_buy  + put_sell
    bear = call_sell + put_buy
    tilt = (bull - bear) / (bull + bear)        in [-1, +1]

and until 2026-07-29 **nothing in this repo computed it**. The paths that produced
real numbers used aggressor `buy - sell` with `right` ignored
(`tools/multileg_arm_probe_v6.py:11`), which is a different statistic: measured
over all 251 sessions / 1,073,060 name-days, the two correlate **+0.235** and take
**opposite signs on 37.0%** of name-days at the registered $250k floor, degrading
to +0.102 / 39.4% at $1M — they disagree MORE on the name-days that carry signal.

The nulls are unaffected. They replace the sign with a coin flip, and
`eps * right_sign` is distributed identically to `eps` while `bull + bear`
partitions exactly the classified set `buy + sell` does, so 1.7/A2's noise curve
carries over to this statistic unchanged. Full disposition in FLOW_ROADMAP §1.7.

⚠️ **Registered ambiguity, resolved here and flagged rather than resolved
silently.** The prereg defines SHORT_OTM as "DTE <= 30 and |K/S - 1| >= 2%". Read
literally that admits deep-ITM contracts, contradicting both the arm's name and
the academic prior it cites (informational flow concentrates in short-dated OTM).
This implements OTM only, which is also what A1/A2 already measured. The literal
reading is a one-line change — and a registered one, not an edit.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from db.flow_corpus import DEFAULT_DB_PATH  # noqa: E402

SHORT_DTE = ("0-7", "8-30")
OTM_MNY = ("otm2_10", "otm10p")

# The prereg's PRIMARY classification is the midpoint side. `strict_side` is a
# second, stricter labelling in the same table; it is not the registered one.
_SIDE_COL = "side"


def _legs(db_path: Path, arm: str, exclude_multileg: bool) -> tuple[np.ndarray, ...]:
    """One (date, underlying) row per name-day: dates, unds, call/put x buy/sell."""
    where = [f"{_SIDE_COL} IN ('buy','sell')"]
    if arm == "SHORT_OTM":
        where += [f"dte_bucket IN {SHORT_DTE!r}", f"mny_bucket IN {OTM_MNY!r}"]
    elif arm != "ALL":
        raise ValueError(f"unknown arm {arm!r} — registered arms are ALL, SHORT_OTM")
    if exclude_multileg:
        where.append("multileg = 0")

    def leg(side: str, right: str) -> str:
        return (f"SUM(CASE WHEN {_SIDE_COL}='{side}' AND right='{right}' "
                f"THEN premium_usd ELSE 0 END)")

    sql = f"""
        SELECT date, underlying, {leg('buy', 'C')}, {leg('sell', 'C')},
               {leg('buy', 'P')}, {leg('sell', 'P')}
        FROM flow_rollup WHERE {' AND '.join(where)}
        GROUP BY date, underlying
    """
    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
        rows = conn.execute(sql).fetchall()
    return (np.array([r[0] for r in rows]), np.array([r[1] for r in rows]),
            *(np.array([r[i] for r in rows], float) for i in (2, 3, 4, 5)))


def tilt_rows(
    db_path: Path = DEFAULT_DB_PATH,
    arm: str = "ALL",
    exclude_multileg: bool = False,
) -> dict[str, np.ndarray]:
    """Registered tilt per (date, underlying) for one arm.

    Also returns `classified` — the bull+bear premium, which is what the
    registered $250k floor and the quintile ranking are measured on. Name-days
    with no classified premium are DROPPED, not zero-filled: a name that did not
    trade has no tilt, and a zero would read as "balanced".
    """
    dates, unds, cb, cs, pb, ps = _legs(db_path, arm, exclude_multileg)
    bull, bear = cb + ps, cs + pb
    classified = bull + bear
    keep = classified > 0
    with np.errstate(invalid="ignore", divide="ignore"):
        tilt = (bull - bear) / classified
    return {"date": dates[keep], "underlying": unds[keep], "bull": bull[keep],
            "bear": bear[keep], "tilt": tilt[keep], "classified": classified[keep]}


def _netflip(db_path: Path) -> None:
    """Roadmap 1.8's question, re-asked on the REGISTERED axis.

    1.8 concluded that excluding packages FLIPS the pooled single-name net sign
    (-$13.7B -> +$48.3B) and that the package fence therefore reaches single
    names. That was measured on `buy - sell` (`multileg_arm_probe_v6.py:11`,
    no `right` in the query). Same cells, same arm split, directional axis.

    ⚠️ Arm split reuses `ETF_SET | INDEX_ROOTS` for comparability with 1.8, and
    inherits roadmap 1.22: ETF_SET is hand-built and incomplete, so the
    index/ETF arm is a floor and some ETFs sit in the single-name arm.
    """
    from db.flow_corpus import INDEX_ROOTS
    from tools.multileg_arm_probe import ETF_SET

    idx = sorted(set(INDEX_ROOTS) | ETF_SET)
    ph = ",".join("?" * len(idx))

    def leg(side: str, right: str) -> str:
        return (f"SUM(CASE WHEN {_SIDE_COL}='{side}' AND right='{right}' "
                f"THEN premium_usd ELSE 0 END)")

    sql = f"""
        SELECT date,
               CASE WHEN underlying IN ({ph}) THEN 1 ELSE 0 END AS idx_arm,
               CASE WHEN multileg != 0 THEN 1 ELSE 0 END AS ml,
               {leg('buy', 'C')}, {leg('sell', 'C')},
               {leg('buy', 'P')}, {leg('sell', 'P')}
        FROM flow_rollup WHERE {_SIDE_COL} IN ('buy','sell')
        GROUP BY date, idx_arm, ml
    """
    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
        rows = conn.execute(sql, idx).fetchall()

    per: dict = {}
    for ds, ia, ml, cb, cs, pb, ps in rows:
        # dir = (call_buy + put_sell) - (call_sell + put_buy);  side = buy - sell
        per.setdefault(ds, {}).setdefault(ia, {})[ml] = (
            (cb + ps) - (cs + pb), (cb + pb) - (cs + ps))

    print("=== 1.8 re-asked: does excluding packages flip the pooled sign? ===")
    print("  arm          axis      pooled ALL      pooled CLEAN   flip   per-session flips")
    for ia, nm in ((0, "single-name"), (1, "index/ETF")):
        for j, axis in ((0, "dir "), (1, "side")):
            tot_a = tot_c = 0.0
            flips = 0
            sess = 0
            for ds, arms in per.items():
                by_ml = arms.get(ia)
                if not by_ml:
                    continue
                a = sum(v[j] for v in by_ml.values())
                c = by_ml.get(0, (0.0, 0.0))[j]
                tot_a += a
                tot_c += c
                sess += 1
                if a and c and np.sign(a) != np.sign(c):
                    flips += 1
            fl = "FLIPS" if np.sign(tot_a) != np.sign(tot_c) else "no"
            print(f"  {nm:<12} {axis}  ${tot_a/1e9:>+10.1f}B  ${tot_c/1e9:>+12.1f}B   "
                  f"{fl:<6} {flips:>4}/{sess} ({flips/max(sess,1):.1%})")


def main() -> None:
    ap = argparse.ArgumentParser(description="registered tilt vs the aggressor axis")
    ap.add_argument("--mode", default="axis", choices=("axis", "netflip"))
    ap.add_argument("--arm", default="ALL", choices=("ALL", "SHORT_OTM"))
    ap.add_argument("--exclude-multileg", action="store_true")
    args = ap.parse_args()

    if args.mode == "netflip":
        _netflip(DEFAULT_DB_PATH)
        return

    _, _, cb, cs, pb, ps = _legs(DEFAULT_DB_PATH, args.arm, args.exclude_multileg)
    den = cb + cs + pb + ps
    k0 = den > 0
    cb, cs, pb, ps, den = cb[k0], cs[k0], pb[k0], ps[k0], den[k0]
    t_dir = (cb + ps - cs - pb) / den      # registered
    t_side = (cb + pb - cs - ps) / den     # aggressor, for comparison only

    ml = " (multileg excluded)" if args.exclude_multileg else ""
    print(f"=== arm {args.arm}{ml} — registered call/put tilt vs aggressor axis ===")
    print(f"name-days: {len(den):,}")
    print("      floor        n     corr  opposite    |both|>=0.1        opp")
    for floor in (0, 250_000, 1_000_000):
        k = den >= floor
        if k.sum() < 2:
            continue
        opp = (np.sign(t_dir[k]) != np.sign(t_side[k])) & (t_dir[k] != 0) & (t_side[k] != 0)
        inf = k & (np.abs(t_dir) >= 0.1) & (np.abs(t_side) >= 0.1)
        oi = (np.sign(t_dir[inf]) != np.sign(t_side[inf])).mean() if inf.sum() else np.nan
        corr = np.corrcoef(t_dir[k], t_side[k])[0, 1]
        print(f"  ${floor:>9,} {k.sum():>8,}  {corr:+.3f}   {opp.mean():6.1%}   "
              f"{inf.sum():>12,}   {oi:8.1%}")


if __name__ == "__main__":
    main()
