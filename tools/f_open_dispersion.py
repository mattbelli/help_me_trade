"""Pre-check for roadmap 2.4: does `f_open` have enough cross-sectional spread to
sort names, and how much of that spread is just the expiry calendar?

Reads `data/oi_reconcile.db` (built by the 2.3 full-corpus run; grain =
session x underlying x right x dte_bucket). Three questions, all cheap:

  1. DISPERSION -- per session, what does the cross-name distribution of
     name-day f_open look like (p10/p50/p90, std)? If most names sit in a
     thin band there is nothing to split on and 2.4 is unrunnable.
  2. DTE DEPENDENCE -- per dte_bucket, the vol-weighted mean f_open AND the
     within-bucket cross-name dispersion. If the spread collapses once DTE
     is held fixed, "open vs close" would mostly rediscover the expiry
     cycle (the known calendar artifact: f_open spikes expiry-adjacent).
  3. PERSISTENCE -- day-over-day Spearman rank autocorrelation of name-day
     f_open. A name trait can be conditioned on; pure day noise cannot.

Cross-sectional use only, per 2.3: f_open LEVELS are uninterpretable (OI
decays via exercise/assignment), so everything here is ranks and spreads,
never "X% of trades open."

This is a pre-registration INPUT, not a graded result -- it consumes no
outcomes and never touches the reserved test split's outcome data.
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

import numpy as np

DB = Path("data/oi_reconcile.db")
VOL_FLOOR = 1_000          # name-day contracts; below this f_open is denominator noise
BUCKETS = ("0", "1-4", "5-29", "30-89", "90-364", "365+")


def _load(db_path: Path):
    con = sqlite3.connect(db_path)
    rows = con.execute(
        "SELECT session, underlying, dte_bucket, volume, vol_weighted_f_open "
        "FROM oi_reconcile_rollup WHERE volume > 0 "
        "AND vol_weighted_f_open IS NOT NULL"
    ).fetchall()
    con.close()
    return rows


def _name_day(rows, exclude_buckets=()):
    """(session, underlying) -> (volume, vol-weighted f_open), optionally
    dropping cells in `exclude_buckets` before aggregating."""
    acc: dict[tuple[str, str], list[float]] = {}
    for s, u, b, v, f in rows:
        if b in exclude_buckets:
            continue
        a = acc.setdefault((s, u), [0.0, 0.0])
        a[0] += v
        a[1] += v * f
    return {k: (v, wf / v) for k, (v, wf) in acc.items() if v >= VOL_FLOOR}


def _xsec_stats(nd):
    """Per-session cross-name p10/p50/p90/std of f_open, averaged over sessions."""
    by_s: dict[str, list[float]] = {}
    for (s, _u), (_v, f) in nd.items():
        by_s.setdefault(s, []).append(f)
    p10, p50, p90, std, n = [], [], [], [], []
    for s, fs in by_s.items():
        a = np.asarray(fs)
        if len(a) < 30:
            continue
        p10.append(np.percentile(a, 10))
        p50.append(np.percentile(a, 50))
        p90.append(np.percentile(a, 90))
        std.append(a.std())
        n.append(len(a))
    return (np.mean(p10), np.mean(p50), np.mean(p90), np.mean(std),
            np.mean(n), len(p10))


def _spearman(nd, lag=1):
    """Mean day-over-day Spearman rank autocorr of name-day f_open."""
    sessions = sorted({s for s, _ in nd})
    by_s: dict[str, dict[str, float]] = {}
    for (s, u), (_v, f) in nd.items():
        by_s.setdefault(s, {})[u] = f
    rhos = []
    for a, b in zip(sessions, sessions[lag:]):
        common = sorted(by_s[a].keys() & by_s[b].keys())
        if len(common) < 30:
            continue
        x = np.asarray([by_s[a][u] for u in common])
        y = np.asarray([by_s[b][u] for u in common])
        rx = np.argsort(np.argsort(x)).astype(float)
        ry = np.argsort(np.argsort(y)).astype(float)
        rhos.append(np.corrcoef(rx, ry)[0, 1])
    return np.mean(rhos), np.std(rhos), len(rhos)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", type=Path, default=DB)
    args = ap.parse_args()
    rows = _load(args.db)

    print(f"=== f_open pre-check for 2.4 (vol floor {VOL_FLOOR:,}/name-day) ===")

    nd_all = _name_day(rows)
    p10, p50, p90, std, n, k = _xsec_stats(nd_all)
    print(f"\n[1] cross-name dispersion, all DTE ({k} sessions, ~{n:.0f} names/day):")
    print(f"    p10={p10:.3f}  p50={p50:.3f}  p90={p90:.3f}  "
          f"(p90-p10 spread {p90 - p10:.3f})  xsec std={std:.3f}")

    print("\n[2] by dte_bucket -- vol-weighted mean AND within-bucket cross-name spread:")
    print(f"    {'bucket':>7} {'cells':>9} {'vw mean':>8} {'p10':>6} {'p50':>6} "
          f"{'p90':>6} {'spread':>7}")
    for b in BUCKETS:
        sub = [r for r in rows if r[2] == b]
        if not sub:
            continue
        v = np.asarray([r[3] for r in sub], dtype=float)
        f = np.asarray([r[4] for r in sub], dtype=float)
        vw = (v * f).sum() / v.sum()
        nd_b = _name_day(sub)
        if len({s for s, _ in nd_b}) >= 30:
            bp10, bp50, bp90, _bstd, _bn, _bk = _xsec_stats(nd_b)
            print(f"    {b:>7} {len(sub):>9,} {vw:>8.3f} {bp10:>6.3f} "
                  f"{bp50:>6.3f} {bp90:>6.3f} {bp90 - bp10:>7.3f}")
        else:
            print(f"    {b:>7} {len(sub):>9,} {vw:>8.3f}   (too few sessions "
                  f"with >=30 qualifying names)")

    # the calendar control: drop expiry-week cells entirely and re-ask [1]
    nd_x = _name_day(rows, exclude_buckets=("0", "1-4"))
    p10x, p50x, p90x, stdx, nx, kx = _xsec_stats(nd_x)
    print(f"\n[3] cross-name dispersion EXCLUDING DTE 0 and 1-4 "
          f"({kx} sessions, ~{nx:.0f} names/day):")
    print(f"    p10={p10x:.3f}  p50={p50x:.3f}  p90={p90x:.3f}  "
          f"(spread {p90x - p10x:.3f})  xsec std={stdx:.3f}")

    for lag in (1, 5):
        rho, rho_sd, pairs = _spearman(nd_all, lag=lag)
        print(f"\n[4] persistence lag={lag}: Spearman rho = {rho:.3f} "
              f"(sd {rho_sd:.3f} across {pairs} session pairs)")


if __name__ == "__main__":
    main()
