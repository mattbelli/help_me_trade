"""Roadmap 1.8 at v6 — the multi-leg sign-flip test on CODE-LABELED arms, whole corpus.

The 2025-07-28 single-session run (tools/multileg_arm_probe.py) answered "just
index/ETF" using the ns heuristic — which 1.9 then measured at recall 50.9%, so
~half the true package legs sat inside the "clean" arm when it was declared
clean. This re-run asks the same registered question with the real OPRA
condition codes across all 251 sessions, at the rollup grain (`flow_rollup`,
schema_version=6, `multileg` from codes per Addendum E/F).

Same three measures as the original probe, from the corpus of record:
    net  = sum(premium | side=buy) - sum(premium | side=sell)
    clip = sum(u_premium_clipped)
    raw  = sum(u_premium)
Flip = pooled sign(all) != sign(ex-multileg) per arm (ALL / single names /
index+ETF), plus a per-session flip census.

Addendum F measurement gate (registered 2026-07-28) is honored in the output:
(a) coverage distribution of pooled sessions, (b) sessions <95% coverage
flagged, (c) the registered bias caveat printed verbatim, and the robustness
arm re-runs the headline split on the >=98%-coverage subset.

    python tools/multileg_arm_probe_v6.py            # runs where the data lives
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db.flow_corpus import INDEX_ROOTS  # noqa: E402
from tools.multileg_arm_probe import ETF_SET  # noqa: E402

DB = Path(__file__).resolve().parent.parent / "data" / "flow_corpus.db"

MEASURES = ("net", "clip", "raw")
ARMS = ("ALL", "SINGLE", "IDX_ETF")

BIAS_CAVEAT = (
    "Registered bias caveat (Addendum F.4): unlabeled prints are not random — "
    "they over-represent index products in the opening burst, where package "
    "flow concentrates. The heuristic fallback undercounts multileg (recall "
    "~51%), so per-session package share is a FLOOR within its unlabeled "
    "slice, and the bias is largest exactly in index open-window arms."
)


def load(db: sqlite3.Connection):
    idx_names = sorted(set(INDEX_ROOTS) | ETF_SET)
    ph = ",".join("?" * len(idx_names))
    # date -> arm(0=single,1=idx/etf) -> multileg(0/1) -> [buy_prem, sell_prem, clip, raw]
    rows = db.execute(
        f"""
        SELECT date,
               CASE WHEN underlying IN ({ph}) THEN 1 ELSE 0 END AS idx_arm,
               CASE WHEN multileg != 0 THEN 1 ELSE 0 END AS ml,
               SUM(CASE WHEN side = 'buy' THEN premium_usd ELSE 0 END),
               SUM(CASE WHEN side = 'sell' THEN premium_usd ELSE 0 END),
               SUM(u_premium_clipped),
               SUM(u_premium),
               SUM(premium_usd)
        FROM flow_rollup
        GROUP BY date, idx_arm, ml
        """,
        idx_names,
    ).fetchall()
    out: dict[str, dict[int, dict[int, tuple[float, ...]]]] = {}
    for ds, idx_arm, ml, buy, sell, clip, raw, tot in rows:
        out.setdefault(ds, {}).setdefault(idx_arm, {})[ml] = (
            buy or 0.0, sell or 0.0, clip or 0.0, raw or 0.0, tot or 0.0)
    return out


def arm_cells(per_date, dates, arm):
    """Sum (all, clean) measure triples for one arm over the given sessions."""
    tot = {"all": [0.0, 0.0, 0.0], "clean": [0.0, 0.0, 0.0]}
    prem = {"all": 0.0, "ml": 0.0}
    for ds in dates:
        for idx_arm, by_ml in per_date.get(ds, {}).items():
            if arm == "SINGLE" and idx_arm != 0:
                continue
            if arm == "IDX_ETF" and idx_arm != 1:
                continue
            for ml, (buy, sell, clip, raw, total) in by_ml.items():
                vals = (buy - sell, clip, raw)
                for i, v in enumerate(vals):
                    tot["all"][i] += v
                    if ml == 0:
                        tot["clean"][i] += v
                prem["all"] += total
                if ml:
                    prem["ml"] += total
    return tot, prem


def session_flips(per_date, dates, arm):
    counts = [0, 0, 0]
    for ds in dates:
        tot, _ = arm_cells(per_date, [ds], arm)
        for i in range(3):
            if (tot["all"][i] > 0) != (tot["clean"][i] > 0):
                counts[i] += 1
    return counts


def report(per_date, dates, title):
    print(f"\n=== {title} — {len(dates)} sessions ===")
    headline_flips = None
    for arm in ARMS:
        tot, prem = arm_cells(per_date, dates, arm)
        ml_share = prem["ml"] / prem["all"] if prem["all"] else 0.0
        per_sess = session_flips(per_date, dates, arm)
        print(f"\n{arm:<8} premium ${prem['all'] / 1e12:.2f}T   "
              f"multileg {ml_share:.1%} of premium (code-labeled)")
        flips = 0
        for i, nm in enumerate(MEASURES):
            a, c = tot["all"][i], tot["clean"][i]
            f = (a > 0) != (c > 0)
            flips += f
            print(f"    {nm:>4}: all {a / 1e9:>+10.2f}B   ex-multileg "
                  f"{c / 1e9:>+10.2f}B   "
                  f"{'*** POOLED FLIP ***' if f else 'same sign'}   "
                  f"per-session flips {per_sess[i]}/{len(dates)}")
        if arm == "SINGLE":
            headline_flips = flips
    print(f"\nSingle-name arm pooled flips: {headline_flips}/3.  "
          + ("Contamination is NOT single-name at corpus scale."
             if headline_flips == 0 else
             "Contamination REACHES single names — Addendum D alarm extends."))
    return headline_flips


def main():
    db = sqlite3.connect(DB)
    vers = db.execute(
        "SELECT DISTINCT schema_version FROM corpus_sessions").fetchall()
    if vers != [(6,)]:
        raise SystemExit(f"corpus not uniformly v6: {vers}")
    cov = dict(db.execute(
        "SELECT date, label_coverage FROM corpus_sessions"))
    per_date = load(db)
    all_dates = sorted(cov)
    hi_dates = [d for d in all_dates if cov[d] >= 0.98]

    print("1.8 at v6 — flow_rollup grain, multileg from OPRA condition codes")
    covs = sorted(cov.values())
    print(f"Coverage of pooled sessions (Addendum F.3a): "
          f"min {covs[0]:.1%}  p25 {covs[len(covs) // 4]:.1%}  "
          f"median {covs[len(covs) // 2]:.1%}  max {covs[-1]:.1%}")
    low = sorted((d, c) for d, c in cov.items() if c < 0.95)
    print("Sessions below 95% coverage (F.3b, flagged): "
          + (", ".join(f"{d} {c:.1%}" for d, c in low) if low else "none"))

    report(per_date, all_dates, "FULL CORPUS")
    report(per_date, hi_dates, "ROBUSTNESS ARM: >=98% coverage subset (F.3)")
    print(f"\n{BIAS_CAVEAT}")


if __name__ == "__main__":
    main()
