"""Roadmap 1.7 Addendum B / 1.23(c): the per-quintile noise floor on the GRADING window.

Registered in FLOW_ROADMAP §1.7 Addendum B before running. Two things it does, and
one it deliberately does not:

* **Does:** produce the per-liquidity-tier exact-null firing rate over all 251 real
  sessions (2025-07-28 .. 2026-07-27), which is what 1.23(c) needs before any grade
  can subtract a base rate; and populate the SHORT_OTM arm's Q1, which 40 sessions
  provably could not (criterion B1).
* **Does NOT:** re-test A2's normal-vs-exact agreement bar. That criterion was
  breached on 2026-07-29 and 1.7 is recorded FAILED. Re-running a failed criterion
  at a new window until it passes is the fishing the prereg law exists to stop. The
  normal null is still computed, but as CONTEXT only, and labelled as such.

Two departures from `tools/order_grain_null.py`, both forced and both registered:

* **Quintiles are trailing-60-day, per the prereg** (`FLOW_BASELINE_PREREG_2026-07-26.md:149`),
  not the whole-corpus median that A1/A2 used. The external audit of 2026-07-29
  caught that the implementation had drifted from the registration and that the
  whole-corpus median conditions cut-points on the reserved test window. Since this
  table is INDEXED BY QUINTILE, the drift lands directly on it. Cut-points are
  cross-sectional per day, so a name may change tier across the year — which is
  also what a grader can actually know at decision time.
* **Sessions outer, draws inner.** A2 held every session's per-order array in
  memory at once; at 251 sessions that is ~30 GB. Here each session is loaded once,
  all draws are taken against it, and only the (names x days) tilt panels persist.
  This changes the random stream, so this is a fresh run and not a reproduction.

  python tools/base_rate_table.py            # needs order_grain_null.py --extract first
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools.order_grain_null import OUT_DIR, _fire, _sessions  # noqa: E402

N_DRAWS = 20
SEED = 20260729
QUINT_LOOKBACK = 60       # prereg:149 — trailing-60-day median daily premium
SE_REPORT_PT = 0.5        # B3: quintiles noisier than this carry their SE
STATIONARITY_PT = 2.0     # B2: half-vs-half gap that declares non-stationarity

logger = logging.getLogger("base_rate_table")


def _name_index(files: list[Path]) -> tuple[np.ndarray, dict[str, int]]:
    """Global ticker set from the small per-session `names` lists only.

    npz members decompress lazily, so touching `names` does not pull the
    per-order arrays — which is the whole reason the panel fits in memory.
    """
    all_names: set[str] = set()
    for f in files:
        if f.exists():
            with np.load(f, allow_pickle=False) as z:
                all_names.update(z["names"].tolist())
    names = np.array(sorted(all_names))
    return names, {n: i for i, n in enumerate(names.tolist())}


def _build(files: list[Path], n_names: int, ni: dict[str, int]) -> tuple:
    """One pass over sessions: day totals, N_eff inputs, and every draw's tilt."""
    n_days = len(files)
    P = {a: np.zeros((n_names, n_days)) for a in ("ALL", "SHORT_OTM")}
    Q = {a: np.zeros((n_names, n_days)) for a in ("ALL", "SHORT_OTM")}
    # float32: 20 draws x ~8k names x 251 days x 2 arms is ~320 MB here and would
    # be 640 MB at float64. The panel feeds a mean/std over 20 values; 1e-7 of
    # relative error cannot move a 2-sigma threshold.
    T = {a: np.zeros((N_DRAWS, n_names, n_days), np.float32)
         for a in ("ALL", "SHORT_OTM")}

    for d, f in enumerate(files):
        if not f.exists():
            logger.warning("%s missing — session skipped", f.name)
            continue
        with np.load(f, allow_pickle=False) as z:
            lut = np.array([ni[n] for n in z["names"].tolist()], dtype=np.int32)
            rows = lut[z["und"]]
            p = {"ALL": z["p_all"].astype(np.float64),
                 "SHORT_OTM": z["p_so"].astype(np.float64)}
        for a in ("ALL", "SHORT_OTM"):
            P[a][:, d] = np.bincount(rows, weights=p[a], minlength=n_names)
            Q[a][:, d] = np.bincount(rows, weights=p[a] ** 2, minlength=n_names)
        # Seeded per SESSION so the stream does not depend on loop order.
        rng = np.random.default_rng([SEED, d])
        for k in range(N_DRAWS):
            # One flip per ORDER, shared by both arms: a sweep is one decision,
            # so its short-OTM legs cannot take a different sign from its others.
            eps = (rng.integers(0, 2, size=len(rows), dtype=np.int8) * 2 - 1).astype(np.float64)
            for a in ("ALL", "SHORT_OTM"):
                num = np.bincount(rows, weights=eps * p[a], minlength=n_names)
                with np.errstate(invalid="ignore", divide="ignore"):
                    T[a][k, :, d] = np.where(P[a][:, d] > 0, num / P[a][:, d], 0.0)
        if (d + 1) % 25 == 0:
            logger.info("session %d/%d", d + 1, n_days)
    return P, Q, T


def _trailing_quintiles(p_all: np.ndarray) -> np.ndarray:
    """Prereg quintiles: trailing-60-day median daily premium, cut cross-sectionally.

    -1 where a name has no trailing history yet, so those name-days are excluded
    rather than silently dumped into Q1. Cut-points use only days STRICTLY BEFORE
    the day being classified, so no name-day helps define its own tier.
    """
    n_names, n_days = p_all.shape
    quint = np.full((n_names, n_days), -1, np.int8)
    for d in range(n_days):
        lo = max(0, d - QUINT_LOOKBACK)
        w = p_all[:, lo:d]
        if not w.shape[1]:
            continue
        med = np.array([np.median(r[r > 0]) if (r > 0).any() else 0.0 for r in w])
        live = med > 0
        if live.sum() < 5:
            continue
        cuts = np.quantile(med[live], [0.2, 0.4, 0.6, 0.8])
        quint[live, d] = np.digitize(med[live], cuts).astype(np.int8)
    return quint


def _whole_corpus_quintiles(p_all: np.ndarray) -> np.ndarray:
    """A1/A2's implementation: one median per name over the WHOLE window.

    Not the registered rule — it conditions cut-points on every session including
    the reserved test window, which is what the 2026-07-29 audit caught. Kept
    solely to isolate how much of Addendum B's shift came from the rule change
    versus the window extension, since B changed both at once.
    """
    med = np.array([np.median(r[r > 0]) if (r > 0).any() else 0.0 for r in p_all])
    live = med > 0
    quint = np.full(len(med), -1, np.int8)
    quint[live] = np.digitize(med[live], np.quantile(med[live], [0.2, 0.4, 0.6, 0.8]))
    return np.repeat(quint[:, None], p_all.shape[1], axis=1)


def _tally(fired, tested, quint, daymask) -> tuple[np.ndarray, np.ndarray]:
    fq, tq = np.zeros(5, np.int64), np.zeros(5, np.int64)
    for i in range(5):
        m = (quint == i) & daymask
        fq[i] = int((fired & m).sum())
        tq[i] = int((tested & m).sum())
    return fq, tq


def _rates(fq, tq):
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(tq > 0, fq / np.maximum(tq, 1), np.nan)


def _print(label, fq, tq, se=None) -> None:
    print(f"\n=== {label} ===")
    print("  quintile           tested        fired      rate" + ("      SE" if se is not None else ""))
    r = _rates(fq, tq)
    for i in range(5):
        nm = ["Q1 least liquid", "Q2", "Q3", "Q4", "Q5 most liquid"][i]
        tail = f"  {se[i]*100:5.2f}pt" if se is not None else ""
        print(f"  {nm:<16} {tq[i]:>11,} {fq[i]:>12,}  {r[i]:>8.3%}{tail}")
    pooled = fq.sum() / max(tq.sum(), 1)
    print(f"  {'pooled':<16} {tq.sum():>11,} {fq.sum():>12,}  {pooled:>8.3%}")
    if np.isfinite(r[0]) and np.isfinite(r[4]) and r[4] > 0:
        print(f"  Q1/Q5 = {r[0]/r[4]:.2f}x")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--draws", type=int, default=N_DRAWS)
    # Addendum B changed BOTH the window (40 -> 251) and the quintile rule
    # (whole-corpus median -> prereg trailing-60). Those are confounded in its
    # result, so this isolates the rule at a fixed window. `whole` is A1/A2's
    # implementation, kept only as the comparison leg -- it is not the registered
    # rule and must not be used to build a table grading consumes.
    ap.add_argument("--quintile-rule", default="trailing60",
                    choices=("trailing60", "whole"))
    ap.add_argument("--out", type=Path, default=ROOT / "data" / "base_rate_table.json")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    sessions = _sessions(0)
    files = [OUT_DIR / f"{s.isoformat()}.npz" for s in sessions]
    logger.info("sessions: %s .. %s (%d)", sessions[0], sessions[-1], len(sessions))

    names, ni = _name_index(files)
    n_names, n_days = len(names), len(files)
    logger.info("panel: %d names x %d sessions", n_names, n_days)

    P, Q, T = _build(files, n_names, ni)
    if args.quintile_rule == "trailing60":
        quint = _trailing_quintiles(P["ALL"])
    else:
        quint = _whole_corpus_quintiles(P["ALL"])
    logger.info("quintiles: %s, %d classified name-days",
                args.quintile_rule, int((quint >= 0).sum()))

    # Addendum B runs the A1-corrected no-floor universe: A1 measured that the
    # registered $250k DAY floor admits 0.105% of Q1 name-days, which is what made
    # 1.7's illiquid bucket unaskable in the first place.
    eligible = P["ALL"] > 0
    half = np.zeros(n_days, bool)
    half[:n_days // 2] = True
    masks = {"full": np.ones(n_days, bool), "H1": half, "H2": ~half}

    out: dict = {"window": [str(sessions[0]), str(sessions[-1])], "sessions": n_days,
                 "names": n_names, "draws": args.draws, "seed": SEED,
                 "quintile_rule": f"trailing-{QUINT_LOOKBACK}d median (prereg:149)",
                 "arms": {}}

    for arm in ("ALL", "SHORT_OTM"):
        with np.errstate(invalid="ignore", divide="ignore"):
            n_eff = np.where(Q[arm] > 0, P[arm] ** 2 / Q[arm], np.nan)
        sigma = np.where(np.isfinite(n_eff) & (n_eff > 0), 1 / np.sqrt(n_eff), np.nan)
        present = (P[arm] > 0) & np.isfinite(sigma)

        acc = {m: [np.zeros(5, np.int64), np.zeros(5, np.int64)] for m in masks}
        per_draw = []
        for k in range(args.draws):
            f, t = _fire(T[arm][k].astype(np.float64), present, eligible)
            for m, dm in masks.items():
                fq, tq = _tally(f, t, quint, dm[None, :])
                acc[m][0] += fq
                acc[m][1] += tq
                if m == "full":
                    per_draw.append(_rates(fq, tq))
        se = np.nanstd(np.array(per_draw), axis=0)

        # CONTEXT ONLY — the A2 agreement criterion is closed as FAIL, not re-tested.
        rng = np.random.default_rng(SEED)
        nfq, ntq = np.zeros(5, np.int64), np.zeros(5, np.int64)
        for _ in range(args.draws):
            tilt = rng.standard_normal(sigma.shape) * np.nan_to_num(sigma, nan=0.0)
            f, t = _fire(tilt, present, eligible)
            a, b = _tally(f, t, quint, masks["full"][None, :])
            nfq += a
            ntq += b

        _print(f"arm {arm} — EXACT null, full window", *acc["full"], se=se)
        _print(f"arm {arm} — H1 (first {int(half.sum())} sessions)", *acc["H1"])
        _print(f"arm {arm} — H2 (last {int((~half).sum())} sessions)", *acc["H2"])
        _print(f"arm {arm} — normal null [CONTEXT ONLY, not a re-test]", nfq, ntq)

        r1, r2 = _rates(*acc["H1"]), _rates(*acc["H2"])
        gap = np.abs(r1 - r2) * 100
        drift = np.nanmax(gap) if np.isfinite(gap).any() else float("nan")
        stationary = bool(np.isfinite(drift) and drift <= STATIONARITY_PT)
        print(f"\n  [B2] max half-vs-half quintile gap: {drift:.2f}pt "
              f"(bar {STATIONARITY_PT}pt) -> {'STATIONARY' if stationary else 'NON-STATIONARY'}")

        rf = _rates(*acc["full"])
        q1_n = int(acc["full"][1][0])
        b1 = bool(q1_n >= 1000 and np.isfinite(rf[0]) and np.isfinite(rf[4])
                  and rf[4] > 0 and rf[0] / rf[4] < 2.0)
        print(f"  [B1] Q1 tested={q1_n:,} (bar 1,000), Q1/Q5="
              f"{rf[0]/rf[4] if rf[4] else float('nan'):.2f}x (bar <2.0x) -> "
              f"{'MET' if b1 else 'NOT MET'}")
        loud = [f"Q{i+1}" for i in range(5) if se[i] * 100 > SE_REPORT_PT]
        print(f"  [B3] quintiles with across-draw SE > {SE_REPORT_PT}pt: "
              f"{', '.join(loud) if loud else 'none'}")

        out["arms"][arm] = {
            "exact": {"fired": acc["full"][0].tolist(), "tested": acc["full"][1].tolist(),
                      "rate": np.where(np.isfinite(rf), rf, None).tolist(),
                      "se_pt": (se * 100).tolist()},
            "H1_rate": np.where(np.isfinite(r1), r1, None).tolist(),
            "H2_rate": np.where(np.isfinite(r2), r2, None).tolist(),
            "normal_context": {"fired": nfq.tolist(), "tested": ntq.tolist()},
            "B1_met": b1, "B2_stationary": stationary, "B2_max_gap_pt": float(drift),
        }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out, indent=2))
    print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
