"""Addendum G obligation 3 — the registered pre-run record, emitted in one shot.

`References/FLOW_BASELINE_PREREG_2026-07-26_ADDENDUM_G.md` §G.10 obligation 3 asks
for four things, all pre-run, all input-side, first run governing:

  1. the G.9c table of record on the registered pipeline — per tier `f₀` (the mean
     of the 20 draw-level rates), the across-draw SE (their sd, ddof = 0, NO
     ÷sqrt(20)) and the DISTINCT tested name-days (the UNION across draws),
  2. per-tier `f`, measured on the identical pipeline and window,
  3. the dilution gate's PER-TIER precision record at G.9b's exact parameters,
  4. the real-vs-null per-name tilt AR(1) and dispersion on the training window —
     G.2's DEAD (dilution) wording names the iid-null-vs-dependent-series confound,
     and this puts its size on the face of the record.

3b (the 2.4 census re-count) lives with the census it re-runs:
`tools/open_close_split.py --stage count --registered`. `--part all` runs both.

**No outcome statistic is computed anywhere here.** Firing rates, censuses and
tilt dependence are input properties (G.10), which is exactly why this may run
before the close of 2026-08-07 while obligations 5 and 6 may not.

    python tools/order_grain_null.py --extract --fenced --sessions 0   # first
    python tools/flow_obligation3.py --part all
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools.base_rate_table import (  # noqa: E402
    ARMS,
    DISTINCT_TESTED_BAR,
    N_DRAWS,
    SE_REPORT_PT,
    _build,
    _name_index,
    real_tilt,
    registered_run,
)
from tools.order_grain_null import FENCED_OUT_DIR, PREMIUM_FLOOR, _sessions  # noqa: E402

# G.9b, verbatim: 100,000 replications, seed 20260812, percentile interval, LCB =
# the 1st percentile of the resampled (f - f₀); block length 10 with G.4's
# assembly (circular, ceil(G/10) draws, truncated to G).
DILUTION_REPS = 100_000
DILUTION_SEED = 20260812
BLOCK = 10
LCB_PCT = 1.0
RESOLVE_TARGET_PT = 1.0   # a gate unable to resolve 1.0pt routes to UNEVALUABLE

# G.4's calibration training window. The corpus's first session is 2025-07-28
# (base prereg 134 registers 07-25; the attribution is to its END date).
TRAIN_START = "2025-07-28"
TRAIN_END = "2026-03-31"
MIN_AR1_OBS = 30          # per-name present-day minimum for an AR(1) to be quoted

QUINT_NAMES = ["Q1 least liquid", "Q2", "Q3", "Q4", "Q5 most liquid"]
DEFAULT_OUT = ROOT / "data" / "flow_obligation3_record.json"

logger = logging.getLogger("flow_obligation3")


# --------------------------------------------------------------------------
# G.9b — the dilution gate's precision record
# --------------------------------------------------------------------------


def circular_blocks(rng, g: int, reps: int, block: int = BLOCK) -> np.ndarray:
    """G.4's block assembly: ceil(g/block) circular blocks, truncated to g.

    Circular because the series wraps — otherwise the endpoints are systematically
    under-weighted, which at G ≈ 37 and block 10 is not a rounding detail.
    """
    n_blocks = -(-g // block)
    starts = rng.integers(0, g, size=(reps, n_blocks))
    idx = (starts[:, :, None] + np.arange(block)[None, None, :]) % g
    return idx.reshape(reps, n_blocks * block)[:, :g]


def dilution_precision(date_fired: np.ndarray, date_tested: np.ndarray,
                       draw_fired: np.ndarray, draw_tested: np.ndarray, *,
                       reps: int = DILUTION_REPS, seed: int = DILUTION_SEED,
                       block: int = BLOCK) -> dict | None:
    """The joint bootstrap of `(f - f₀)`, and the difference it can resolve.

    `f` is resampled by circular moving block over the tier's judgment-window
    event-date series; `f₀` by the registered two-stage resample — the 20 null
    draws with replacement, then tested name-days iid within each draw (iid
    Bernoulli resampling of a draw's tested name-days IS a Binomial(n_d, p_d),
    which is how it is drawn here).

    Disclosed property of the registered parameters, not a defect: when a tier's
    G is <= the block length there is exactly ONE circular block, every replicate
    is a rotation of the same series, and `f` contributes NO resampling variance —
    that tier's precision is f₀'s alone. The judgment window is registered at 37
    dates (4 blocks); a thin tier can still land there, and `event_dates` is
    reported per tier so it is visible on the face of the record.

    Two judgment calls, both disclosed rather than buried:
    * dates with no tested name-day in the tier are not part of that tier's event-date
      series and are dropped before blocking (they would otherwise contribute
      zero-weight positions and shrink the effective block length);
    * each resampled statistic uses the SAME functional as its point estimate —
      `f` pooled fired/tested, `f₀` the mean of the draw-level rates.

    Returns the minimum resolvable difference `mrd = (f - f₀) - LCB`: a true
    difference smaller than this cannot push the 99% LCB off zero.
    """
    keep = date_tested > 0
    df, dt = date_fired[keep].astype(np.float64), date_tested[keep].astype(np.float64)
    g = len(dt)
    if g == 0 or draw_tested.sum() == 0:
        return None

    p_draw = np.where(draw_tested > 0, draw_fired / np.maximum(draw_tested, 1), np.nan)
    f = float(df.sum() / dt.sum())
    f0 = float(np.nanmean(p_draw))

    rng = np.random.default_rng(seed)
    idx = circular_blocks(rng, g, reps, block)
    f_b = df[idx].sum(1) / np.maximum(dt[idx].sum(1), 1.0)

    k = len(draw_tested)
    sel = rng.integers(0, k, size=(reps, k))
    n = draw_tested[sel]
    x = rng.binomial(n, np.nan_to_num(p_draw)[sel])
    with np.errstate(invalid="ignore", divide="ignore"):
        f0_b = np.nanmean(np.where(n > 0, x / np.maximum(n, 1), np.nan), axis=1)

    diff = f_b - f0_b
    lcb = float(np.percentile(diff, LCB_PCT))
    mrd = float((f - f0) - lcb)
    return {"f": f, "f0": f0, "diff": f - f0, "lcb": lcb,
            "mrd_pt": mrd * 100.0, "resolves_1pt": bool(mrd * 100.0 <= RESOLVE_TARGET_PT),
            "event_dates": g, "reps": reps, "seed": seed, "block": block,
            "boot_sd_pt": float(diff.std(ddof=0) * 100.0)}


# --------------------------------------------------------------------------
# obligation 3's tilt-dependence record
# --------------------------------------------------------------------------


def ar1_dispersion(tilt: np.ndarray, present: np.ndarray,
                   rows: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Per-name lag-1 autocorrelation and sd of the tilt series on PRESENT days.

    Measured on present days in order, NOT on the calendar — that is the series
    `_fire`'s trailing mean/std actually sees, so it is the series whose
    dependence the confound is about. `rows` selects the names to quote.
    """
    ar = np.full(len(rows), np.nan)
    sd = np.full(len(rows), np.nan)
    for j, i in enumerate(rows):
        x = tilt[i][present[i]]
        sd[j] = x.std()
        a, b = x[1:], x[:-1]
        if len(a) > 1 and a.std() > 0 and b.std() > 0:
            ar[j] = float(np.corrcoef(a, b)[0, 1])
    return ar, sd


def tilt_dependence(cache_dir: Path = FENCED_OUT_DIR, draws: int = N_DRAWS,
                    sessions: list[date] | None = None) -> dict:
    """Real vs null per-name tilt AR(1) and dispersion, training window."""
    all_sessions = sessions if sessions is not None else _sessions(0)
    panel = [s for s in all_sessions if TRAIN_START <= s.isoformat() <= TRAIN_END]
    if not panel:
        raise SystemExit(f"no sessions on the training window {TRAIN_START}..{TRAIN_END}")
    files = [cache_dir / f"{s.isoformat()}.npz" for s in panel]
    names, ni = _name_index(files)
    P, _Q, B, T = _build(files, len(names), ni, draws)

    present = P["ALL"] > 0
    rows = np.flatnonzero(present.sum(1) >= MIN_AR1_OBS)
    logger.info("tilt dependence: %s .. %s (%d sessions), %d of %d identities "
                "with >=%d present days", panel[0], panel[-1], len(panel),
                len(rows), len(names), MIN_AR1_OBS)

    ar_r, sd_r = ar1_dispersion(real_tilt(P["ALL"], B["ALL"]), present, rows)
    ar_n = np.full((draws, len(rows)), np.nan)
    sd_n = np.full((draws, len(rows)), np.nan)
    for k in range(draws):
        ar_n[k], sd_n[k] = ar1_dispersion(T["ALL"][k].astype(np.float64), present, rows)

    def _s(v):
        return {"n": int(np.isfinite(v).sum()),
                "median": float(np.nanmedian(v)), "mean": float(np.nanmean(v)),
                "p10": float(np.nanpercentile(v, 10)),
                "p90": float(np.nanpercentile(v, 90))}

    return {"window": [TRAIN_START, TRAIN_END], "sessions": len(panel),
            "names_quoted": len(rows), "min_present_days": MIN_AR1_OBS,
            "draws": draws,
            "real": {"ar1": _s(ar_r), "dispersion": _s(sd_r)},
            # per-name mean across the 20 draws, then the same summary — the null's
            # per-name quantity, not a pooled smear across draws.
            "null": {"ar1": _s(np.nanmean(ar_n, axis=0)),
                     "dispersion": _s(np.nanmean(sd_n, axis=0))}}


# --------------------------------------------------------------------------
# reporting
# --------------------------------------------------------------------------


def _print_arm(arm: str, rec: dict, gate: list[dict | None]) -> None:
    null, real = rec["null"], rec["real"]
    f0, se, distinct = null["f0"], null["se"], null["distinct_tested"]
    fired, tested = null["acc"]["judgment"]
    f = real["rate"]
    print(f"\n=== arm {arm} — G.9c table of record, judgment window ===")
    print("  quintile          null tested     f0      SE   distinct    "
          "real tested       f    usable")
    for i in range(5):
        ok = (np.isfinite(se[i]) and se[i] * 100 <= SE_REPORT_PT
              and distinct[i] >= DISTINCT_TESTED_BAR)
        print(f"  {QUINT_NAMES[i]:<16} {tested[i]:>11,} {f0[i]:>7.3%} "
              f"{se[i]*100:>6.2f}pt {distinct[i]:>9,} {real['tested'][i]:>13,} "
              f"{f[i]:>7.3%}    {'yes' if ok else 'NO':<3}")
    print(f"  {'pooled':<16} {tested.sum():>11,} "
          f"{fired.sum()/max(tested.sum(),1):>7.3%} {'':>8} {distinct.sum():>9,} "
          f"{real['tested'].sum():>13,} "
          f"{real['fired'].sum()/max(real['tested'].sum(),1):>7.3%}")
    print(f"  [G.2 step 1] null usable = across-draw SE <= {SE_REPORT_PT}pt AND "
          f">= {DISTINCT_TESTED_BAR:,} distinct tested name-days")

    print(f"\n=== arm {arm} — G.9b dilution-gate precision record ===")
    print("  quintile            f       f0    f-f0      LCB      MRD  resolves 1.0pt")
    for i in range(5):
        g = gate[i]
        if g is None:
            print(f"  {QUINT_NAMES[i]:<16} {'— no tested name-days in the window':>40}")
            continue
        print(f"  {QUINT_NAMES[i]:<16} {g['f']:>7.3%} {g['f0']:>8.3%} "
              f"{g['diff']*100:>+7.2f}pt {g['lcb']*100:>+7.2f}pt "
              f"{g['mrd_pt']:>7.2f}pt  {'yes' if g['resolves_1pt'] else 'NO'}")
    print("  MRD = (f - f0) - LCB: the smallest true difference whose 99% lower "
          "bound clears zero.")


def _jsonable(v):
    if isinstance(v, dict):
        return {k: _jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    if isinstance(v, np.ndarray):
        return [_jsonable(x) for x in v.tolist()]
    if isinstance(v, (np.integer, np.floating, np.bool_)):
        v = v.item()
    if isinstance(v, float) and not np.isfinite(v):
        return None
    return v


def run_part3(cache_dir: Path, draws: int, out: Path, reps: int) -> dict:
    res = registered_run(cache_dir=cache_dir, draws=draws)
    record: dict = {"obligation": "G.10 #3", "panel": {k: v for k, v in res.items()
                                                       if k != "arms"}, "arms": {}}
    for arm in ARMS:
        rec = res["arms"][arm]
        gate = [dilution_precision(rec["real"]["per_date_fired"][i],
                                   rec["real"]["per_date_tested"][i],
                                   rec["null"]["per_draw_fired"][:, i],
                                   rec["null"]["per_draw_tested"][:, i], reps=reps)
                for i in range(5)]
        _print_arm(arm, rec, gate)
        record["arms"][arm] = {
            "f0": rec["null"]["f0"], "se_pt": rec["null"]["se"] * 100.0,
            "distinct_tested": rec["null"]["distinct_tested"],
            "null_fired": rec["null"]["acc"]["judgment"][0],
            "null_tested": rec["null"]["acc"]["judgment"][1],
            "per_draw_fired": rec["null"]["per_draw_fired"],
            "per_draw_tested": rec["null"]["per_draw_tested"],
            "f": rec["real"]["rate"], "real_fired": rec["real"]["fired"],
            "real_tested": rec["real"]["tested"],
            "dilution_gate": gate,
        }
    record["tilt_dependence"] = tilt_dependence(cache_dir=cache_dir, draws=draws)
    td = record["tilt_dependence"]
    print("\n=== obligation 3 — real vs null tilt dependence, training window ===")
    print(f"  {td['window'][0]} .. {td['window'][1]}, {td['names_quoted']:,} "
          f"identities with >= {td['min_present_days']} present days")
    for k in ("ar1", "dispersion"):
        r, n = td["real"][k], td["null"][k]
        print(f"  {k:<11} real median {r['median']:+.4f} (mean {r['mean']:+.4f})   "
              f"null median {n['median']:+.4f} (mean {n['mean']:+.4f})")

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(_jsonable(record), indent=2))
    print(f"\nwrote {out}")
    return record


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--part", choices=("3", "3b", "all"), default="all")
    ap.add_argument("--cache-dir", type=Path, default=FENCED_OUT_DIR)
    ap.add_argument("--draws", type=int, default=N_DRAWS)
    ap.add_argument("--reps", type=int, default=DILUTION_REPS)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logger.info("registered pipeline: fence v7 + equity universe, $%s day floor, "
                "cache %s", f"{PREMIUM_FLOOR:,.0f}", args.cache_dir)

    if args.part in ("3", "all"):
        run_part3(args.cache_dir, args.draws, args.out, args.reps)
    if args.part in ("3b", "all"):
        from tools.open_close_split import stage_count_registered
        stage_count_registered(cache_dir=args.cache_dir)


if __name__ == "__main__":
    main()
