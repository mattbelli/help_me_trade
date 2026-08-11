"""Addendum G obligations 5+6 and the registered scoring run (G.10 #5, #6, score).

`References/FLOW_BASELINE_PREREG_2026-07-26_ADDENDUM_G.md` (LAW v12, 2026-07-31)
governs. Obligations 1-3/3b are recorded (`data/flow_obligation3_record.json`);
this module discharges the remainder, IN ORDER, first run governing:

  --stage obligation5   G.2 step-1 input-property records on the primary cell:
                        complete-or-terminated date count G + calendar-gap
                        vector, event count, regime-span composition (with the
                        two named tail dates), truncated/ungradeable census,
                        per-root no_spot_index AND no_spot_missing breakdowns +
                        arm-split unclassified/adjusted root-class shares
                        (corpus scan, per-session cached), and the per-date
                        gradeable-event-count distribution for BOTH windows.
                        NO alpha is computed here (G.10's "touch" rule).
  --stage obligation6   The G.4 size calibration + MDE at the RECORDED G and
                        gap vector (training window outcomes — already seen,
                        disclosed in law). Seed 20260810. Asserts its training
                        series against the obligation-5 record and stores the
                        series it calibrated on.
  --stage score         The one-shot verdict per G.2's ordered checklist.
                        Emits `data/flow_g_verdict.json` — the artifact
                        `h1_quiet_tape.py --stage score --g-verdict` waits on —
                        carrying the verdict, its note, the MDE and the
                        observed effect (G.2: every DEAD prints both).

What it reuses (imports, never copies — G.2's one-implementation pin):
  * `h1_quiet_tape.build_panel` — the fenced full-corpus equity panel (the
    freeze constrains edits to that module, not imports from it), plus
    `_window_indices`/`_environment`, `spy_calendar`/
    `check_benchmark_continuity`/`daily_bars`/`grade_name_day` (the four-case
    terminal-value rule), `nw_stats` (G.4's written formula) and `calibrate`
    (circular MBB + MDE; seed passed per THIS registration);
  * `order_grain_null._fire` — the registered event rule. The cohort and the
    baseline are ITS masks. This module additionally needs sign(z), which
    `_fire` does not emit: `_signed_z` recomputes the identical trailing
    window and the run ASSERTS its fired/tested masks equal `_fire`'s
    elementwise on the real panel — divergence is a blocking fault. That
    assert is invariant to a sign-only edit, so
    `test_flow_obligation56.py::test_sign_is_sign_of_z_not_sign_of_tilt`
    is LOAD-BEARING for the sign convention;
  * `base_rate_table._trailing_quintiles` — the registered tier rule; the
    TIER ITSELF is read from the obligation-3b record (`tier`: 4 == Q5),
    never re-derived (first-run-governs: the 3b selection is the selection);
  * `order_grain_null._join_session` + the rollup's `multileg_flags`/
    `fence_and_classify`/`_bucket_dte`/`_bucket_mny` — the census corpus
    scan drives the SAME per-print fence as the registered extractor and
    counts what the equity npz cache by construction cannot contain: index
    premium, non-equity classes, equity roots with no bars in the store
    (`no_spot_missing`), and the print-side fence exclusions.

G.2's vendor-authority rule: UNGRADEABLE may never be assigned from the local
store alone. `_require_backfill` refuses every stage until the obligation-2
backfill scope (the four dotted class-share forms + BLD, vendor re-fetched
2026-08-11) is demonstrably present in `equity_bars`, and obligation 5
records the check as the run's store-state evidence.

Registered constants pinned here: CAL_SEED_G = 20260810 (G.4; H1's 20260818
is a DIFFERENT registration's seed), the G.2 verdict wordings (verbatim), the
two named regime tail dates. Everything else — windows, floors, z rule,
horizon grid — is imported from the modules that registered it.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sqlite3
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from db.flow_corpus import DEFAULT_DB_PATH, bars_before_date  # noqa: E402
from tools.base_rate_table import _trailing_quintiles  # noqa: E402
from tools.flow_corpus_rollup import (  # noqa: E402
    _bucket_dte,
    _bucket_mny,
    fence_and_classify,
    multileg_flags,
)
from tools.h1_quiet_tape import (  # noqa: E402
    H_GRID,
    HORIZON,
    REGIME_CUT,
    BlockingHole,
    Panel,
    _environment,
    _window_indices,
    build_panel,
    calibrate,
    check_benchmark_continuity,
    daily_bars,
    grade_name_day,
    nw_stats,
    spy_calendar,
)
from tools.order_grain_null import (  # noqa: E402
    INDEX_ROOTS,
    LOOKBACK,
    MIN_PRESENT,
    OTM_MNY_IDX,
    PREMIUM_FLOOR,
    SHORT_DTE_IDX,
    Z_EVENT,
    _fire,
    _join_session,
    classification,
)

logger = logging.getLogger("flow_obligation56")

CAL_SEED_G = 20260810              # G.4 — one seed for cutoff AND MDE
EARLIEST_RUN = date(2026, 8, 8)    # G.10: no earlier than the close of 2026-08-07
NAMED_TAIL_DATES = ("2026-06-25", "2026-07-15")   # obligation 5's named events
MDE_CEILING = 1.00                 # G.4: MDE > 1.00 ATR => POWER-LIMITED
CENSUS_CACHE_VERSION = 1

# G.10 #2's backfill scope, the store-presence precondition (G.2: the vendor,
# not the store, is the authority — these were vendor re-fetched 2026-08-11).
BACKFILL_SENTINELS = ("BRK.B", "BF.B", "HEI.A", "PBR.A", "BLD", "SPY")

OB3_RECORD = ROOT / "data" / "flow_obligation3_record.json"
RECORD_PATH = ROOT / "data" / "flow_scoring_record.json"
VERDICT_PATH = ROOT / "data" / "flow_g_verdict.json"
CENSUS_CACHE = ROOT / "data" / "flow_census_cache"

# G.2's registered wordings, verbatim — never paraphrased on any surface.
# They pin the Q5 cell by name; stage_score asserts tier == 4 before use.
WORDING_DEAD_DILUTION = (
    "DEAD (dilution) (ALL, Q5, 10td) — the event rule fires no more often than "
    "the order-sign null; NO claim is made about the fired set — two rules can "
    "fire at identical rates and select entirely different name-days, and the "
    "null's iid-across-days construction is a known confound against the real "
    "tilt series' temporal dependence (a persistent tilt level shrinks the "
    "trailing std and inflates z)."
)
WORDING_DEAD_TEST = (
    "DEAD (test) (ALL, Q5, 10td) — no dose-response over sub-threshold days, "
    "never \"events carry no alpha\"."
)
WORDING_ALIVE = "ALIVE (ALL, Q5, 10td)"


# ------------------------------ record I/O ------------------------------

def _gate(stage: str, today: date | None = None) -> None:
    today = today or datetime.now(timezone.utc).date()
    if today < EARLIEST_RUN:
        raise SystemExit(f"stage {stage} refuses to run before {EARLIEST_RUN} "
                         "— G.10's scoring-date bound is inherited by "
                         "obligations 5 and 6 (bars through d+10)")


def _load_record() -> dict:
    if RECORD_PATH.exists():
        rec = json.loads(RECORD_PATH.read_text())
        rec.setdefault("stages", {})
        return rec
    return {"registration": "FLOW_BASELINE_PREREG_2026-07-26 Addendum G (v12)",
            "stages": {}}


def _save_record(rec: dict) -> None:
    tmp = RECORD_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(rec, indent=1))
    os.replace(tmp, RECORD_PATH)
    logger.info("record written: %s", RECORD_PATH)


def _require_stage_absent(rec: dict, stage: str) -> None:
    if stage in rec["stages"]:
        raise SystemExit(f"stage {stage} already recorded — first run governs, "
                         "no re-run (G.10)")


def _ob3() -> dict:
    if not OB3_RECORD.exists():
        raise SystemExit(f"obligation-3 record missing: {OB3_RECORD} — "
                         "obligations run IN ORDER (G.10)")
    return json.loads(OB3_RECORD.read_text())


def _require_backfill() -> dict:
    """G.2's vendor-authority precondition: refuse to grade anything until
    the obligation-2 backfill scope is present in the local store. Returns
    the per-symbol evidence for the record."""
    con = sqlite3.connect(DEFAULT_DB_PATH)
    cur = con.cursor()
    evidence = {}
    missing = []
    for s in BACKFILL_SENTINELS:
        cur.execute("SELECT COUNT(*), MIN(date), MAX(date) FROM equity_bars "
                    "WHERE symbol = ?", (s,))
        n, lo, hi = cur.fetchone()
        evidence[s] = {"rows": n, "first": lo, "last": hi}
        if not n:
            missing.append(s)
    con.close()
    if missing:
        raise SystemExit(
            f"obligation-2 backfill absent from the store: {missing} — "
            "UNGRADEABLE may never be assigned from the local store alone "
            "(G.2); run tools/flow_corpus_bars.py --symbols on the missing "
            "names first")
    return evidence


# ------------------------------ the cell's masks ------------------------------

def _signed_z(tilt: np.ndarray, present: np.ndarray,
              eligible: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """`_fire`'s arithmetic, re-run to expose z's SIGN (sign, fired, tested).

    The caller MUST assert the returned fired/tested against `_fire`'s own —
    this function exists only because `_fire` discards z, and it may never
    drift from it. The assert cannot see a sign-only edit; the hermetic sign
    test named in the module docstring is what holds that line.
    """
    n_names, n_days = tilt.shape
    fired = np.zeros(tilt.shape, bool)
    tested = np.zeros(tilt.shape, bool)
    sign = np.zeros(tilt.shape, np.float64)
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
        ok = (k >= MIN_PRESENT) & eligible[:, d] & present[:, d] \
            & np.isfinite(z) & (std > 0)
        tested[:, d] = ok
        fired[:, d] = ok & (np.abs(z) >= Z_EVENT)
        sign[:, d] = np.where(ok, np.sign(z), 0.0)
    return sign, fired, tested


class CellMasks:
    """fired/tested/sign/tier for the registered cell, `_fire`-verified."""

    def __init__(self, panel: Panel, tier: int):
        eligible = panel.prem >= PREMIUM_FLOOR
        sign, fired, tested = _signed_z(panel.tilt, panel.present, eligible)
        ref_fired, ref_tested = _fire(panel.tilt, panel.present, eligible)
        if not ((fired == ref_fired).all() and (tested == ref_tested).all()):
            raise SystemExit("_signed_z diverged from order_grain_null._fire "
                             "on the real panel — the one-implementation pin "
                             "is broken; blocking (G.2)")
        self.sign, self.fired, self.tested = sign, fired, tested
        self.quint = _trailing_quintiles(panel.prem)
        self.tier = tier
        self.in_tier = self.quint == tier

    def events(self, j: int) -> np.ndarray:
        return self.fired[:, j] & self.in_tier[:, j]

    def baseline(self, j: int) -> np.ndarray:
        return (self.tested[:, j] & ~self.fired[:, j] & self.in_tier[:, j]
                & (self.sign[:, j] != 0.0))

    def baseline_zero_z(self, j: int) -> np.ndarray:
        return (self.tested[:, j] & ~self.fired[:, j] & self.in_tier[:, j]
                & (self.sign[:, j] == 0.0))


# ------------------------------ windows ------------------------------

def build_cell_window(panel: Panel, masks: CellMasks, which: str,
                      with_alpha: bool) -> dict:
    """Population, grading census, and (with_alpha) the per-date series.

    G.4's estimand: per-date equal-weighted mean over gradeable EVENTS minus
    the same over the gradeable BASELINE, each observation signed by its own
    sign(z), of the G.2 metric. A date enters the series iff BOTH arms carry
    >=1 gradeable observation (the difference is undefined otherwise); dates
    dropped for an empty arm are counted and disclosed. The >=30-events bar
    counts gradeable events "in the cell" — on every evaluable event date,
    including dates the series drops for an empty baseline arm.
    """
    idxs = _window_indices(panel, which)
    cal_dates, spy_o, spy_c = spy_calendar()
    check_benchmark_continuity(panel, cal_dates)
    cal_pos = {d: i for i, d in enumerate(cal_dates)}
    last_idx = len(cal_dates) - 1
    rc = classification()

    needed: set[str] = set()
    for j in idxs:
        needed.update(panel.names[masks.events(j) | masks.baseline(j)].tolist())
        if with_alpha:
            # the per-tier precommitment grades FIRED name-days in ALL tiers —
            # their bars must be fetched too, or non-selected tiers silently
            # grade as "no bars" and vanish (caught in review)
            needed.update(panel.names[masks.fired[:, j]
                                      & (masks.quint[:, j] >= 0)].tolist())
    sibs = {f for n in needed for f in (rc.occ_form(n), rc.directory_form(n))
            if f}
    bars_all = daily_bars(needed | sibs)

    def bars_for(name: str):
        for key in (name, rc.directory_form(name), rc.occ_form(name)):
            b = bars_all.get(key) if key else None
            if b is not None and len(b[0]):
                return b
        return None

    census: dict = {
        "window": which, "sessions": len(idxs),
        "with_alpha": with_alpha, "tier": masks.tier,
        "per_date": [], "events_condition_total": 0,
        "events_gradeable": 0, "event_dates_gradeable": 0,
        "event_dates_list": [],
        "baseline_zero_z_names_total": 0,
        "dates_dropped_empty_event_arm": 0,
        "dates_dropped_empty_baseline_arm": 0,
        "ungradeable": {"ii": 0, "iii": 0, "iv": 0},
        "ungradeable_premium_usd": 0.0,
        "ungradeable_roots": {},
        "truncated_10td": 0,
        "truncated_premium_usd": 0.0,
        "grid_not_elapsed": {str(h): 0 for h in H_GRID},
        "grid_observations": {str(h): 0 for h in H_GRID},
    }
    dates: list[str] = []
    positions: list[int] = []
    diffs: list[float] = []
    event_means: list[float] = []
    base_means: list[float] = []
    grid_diffs: dict[str, list] = {str(hz): [] for hz in H_GRID}
    tier_cohort: dict[str, list[float]] = {str(q): [] for q in range(5)}

    for j in idxs:
        s_iso = panel.sessions[j].isoformat()
        if s_iso not in cal_pos:
            raise SystemExit(f"panel session {s_iso} missing from the SPY "
                             "calendar — benchmark blocking (G.2)")
        k0 = cal_pos[s_iso]
        ev_mask = masks.events(j)
        ba_mask = masks.baseline(j)
        census["baseline_zero_z_names_total"] += int(masks.baseline_zero_z(j).sum())
        row = {"date": s_iso, "events": int(ev_mask.sum()),
               "baseline": int(ba_mask.sum())}
        census["per_date"].append(row)
        census["events_condition_total"] += int(ev_mask.sum())

        grade_idx = (np.nonzero(ev_mask | ba_mask)[0] if not with_alpha
                     else np.nonzero(ev_mask | ba_mask
                                     | (masks.fired[:, j]
                                        & (masks.quint[:, j] >= 0)))[0])
        graded = {}
        for i in grade_idx:
            name = str(panel.names[i])
            try:
                graded[int(i)] = grade_name_day(
                    bars_for(name), cal_pos, spy_o, spy_c, k0, last_idx,
                    with_alpha)
            except BlockingHole as e:
                raise SystemExit(
                    f"interior bar hole ({s_iso}, {name}): {e} — a BLOCKING "
                    "data fault referred to the vendor re-fetch (G.2)") from e

        for i in np.nonzero(ev_mask | ba_mask)[0]:
            gr = graded[int(i)]
            if gr.case in census["ungradeable"]:
                census["ungradeable"][gr.case] += 1
                prem = float(panel.prem[i, j])
                census["ungradeable_premium_usd"] += prem
                root = str(panel.names[i])
                census["ungradeable_roots"][root] = (
                    census["ungradeable_roots"].get(root, 0.0) + prem)

        def usable(mask: np.ndarray) -> list[int]:
            out = []
            for i in np.nonzero(mask)[0]:
                gr = graded[int(i)]
                if gr.case not in ("full", "truncated"):
                    continue
                if gr.covered.get(HORIZON) == "not_elapsed":
                    raise SystemExit(
                        f"10td horizon not elapsed for {panel.names[i]} at "
                        f"{s_iso} — the run is premature (G.2/G.3)")
                out.append(int(i))
            return out

        ev_u, ba_u = usable(ev_mask), usable(ba_mask)
        row["events_gradeable"], row["baseline_gradeable"] = len(ev_u), len(ba_u)
        for i in ev_u + ba_u:
            if graded[i].covered.get(HORIZON) == "truncated":
                census["truncated_10td"] += 1
                census["truncated_premium_usd"] += float(panel.prem[i, j])
        for hz in H_GRID:
            for i in ev_u + ba_u:
                cov = graded[i].covered.get(hz)
                if cov == "not_elapsed":
                    census["grid_not_elapsed"][str(hz)] += 1
                elif cov is not None:
                    census["grid_observations"][str(hz)] += 1
        if with_alpha:
            # per-tier cohort alpha (G.2's pre-commitment): every FIRED
            # name-day with a defined tier, its own sign, all 5 tiers —
            # UNCONDITIONED on the selected tier's arms (review finding)
            for i in np.nonzero(masks.fired[:, j]
                                & (masks.quint[:, j] >= 0))[0]:
                gr = graded[int(i)]
                sgn = masks.sign[i, j]
                if gr.case in ("full", "truncated") and sgn != 0.0:
                    tier_cohort[str(int(masks.quint[i, j]))].append(
                        float(sgn * gr.alpha[HORIZON]))
        if ev_u:
            census["event_dates_gradeable"] += 1
            census["event_dates_list"].append(s_iso)
            # ">= 30 events in the cell" counts every gradeable event on an
            # evaluable event date, series membership notwithstanding
            census["events_gradeable"] += len(ev_u)
        if not ev_u:
            census["dates_dropped_empty_event_arm"] += 1
            continue
        if not ba_u:
            census["dates_dropped_empty_baseline_arm"] += 1
            continue
        dates.append(s_iso)
        positions.append(k0)
        if with_alpha:
            sg = masks.sign[:, j]
            e_signed = np.array([sg[i] * graded[i].alpha[HORIZON] for i in ev_u])
            b_signed = np.array([sg[i] * graded[i].alpha[HORIZON] for i in ba_u])
            diffs.append(float(e_signed.mean() - b_signed.mean()))
            event_means.append(float(e_signed.mean()))
            base_means.append(float(b_signed.mean()))
            for hz in H_GRID:
                he = [sg[i] * graded[i].alpha[hz] for i in ev_u
                      if graded[i].alpha[hz] is not None]
                hb = [sg[i] * graded[i].alpha[hz] for i in ba_u
                      if graded[i].alpha[hz] is not None]
                if he and hb:
                    grid_diffs[str(hz)].append(
                        (s_iso, k0, float(np.mean(he) - np.mean(hb))))

    diffs_arr = np.array(diffs)
    if with_alpha and len(diffs_arr) and not np.isfinite(diffs_arr).all():
        raise SystemExit("non-finite value in the difference series — data "
                         "fault, never a verdict input")
    census["series_dates"] = len(dates)
    pos_arr = np.array(positions, int)
    return {"dates": dates, "gaps": pos_arr,
            "gap_vector": np.diff(pos_arr).tolist(),   # G.4's registered sense
            "diffs": diffs_arr,
            "event_means": event_means, "base_means": base_means,
            "census": census, "grid_diffs": grid_diffs,
            "tier_cohort_alpha": {
                q: (float(np.mean(v)) if v else None)
                for q, v in tier_cohort.items()},
            "tier_cohort_n": {q: len(v) for q, v in tier_cohort.items()}}


# ------------------------------ corpus census (obligation 5) ------------------------------

_BARS_SYMBOLS: set[str] | None = None


def _bars_symbols() -> set[str]:
    global _BARS_SYMBOLS
    if _BARS_SYMBOLS is None:
        con = sqlite3.connect(DEFAULT_DB_PATH)
        _BARS_SYMBOLS = {r[0] for r in
                         con.execute("SELECT DISTINCT symbol FROM equity_bars")}
        con.close()
    return _BARS_SYMBOLS


def _census_session(s_iso: str, rc) -> dict:
    """One judgment session's fenced corpus census, on CLASSIFIED premium
    (the registered operative base): per-root no_spot_index (INDEX_ROOTS —
    the registered bucket key; the G.8 index-class additions are recorded
    separately), per-root no_spot_missing (equity-classified roots with no
    bars in the store under any registered form), per-root non-equity
    classes, and the arm-split (ALL / SHORT_OTM) unclassified + adjusted
    root-class shares G.10 #5 names. Print-side fence exclusions are
    reported under print_* names — a different quantity from the G.8
    root-class shares, never conflated."""
    cache = CENSUS_CACHE / f"{s_iso}.json"
    if cache.exists():
        cached = json.loads(cache.read_text())
        if cached.get("cache_version") == CENSUS_CACHE_VERSION:
            return cached
        logger.info("census %s: stale cache (v%s != v%s) — recomputing",
                    s_iso, cached.get("cache_version"), CENSUS_CACHE_VERSION)
    session = date.fromisoformat(s_iso)
    j = _join_session(session)
    if j is None:
        raise SystemExit(f"census: session {s_iso} failed to join — blocking")
    arr = j.arr
    ml, _cond, ml_source, join_rate, coverage = multileg_flags(
        session, arr["ts_event"].astype(np.int64),
        arr["instrument_id"].astype(np.int64),
        arr["price"].astype(np.float64), arr["size"].astype(np.int64))
    fx = fence_and_classify(arr)
    classified = fx.side <= 1
    keep = classified & (ml == 0)                 # the operative base (G.2)
    roots = [str(s) for s in j.uniq_und]
    und = j.und_code

    # per-print SHORT_OTM flag, the fenced extractor's own recipe: spot at
    # the terminal symbol, dte/mny buckets from the rollup's tested functions
    closes = bars_before_date(s_iso)
    term = [rc.resolve(r, s_iso) or r for r in roots]
    spot_by_root = np.array([closes.get(t, (np.nan, ""))[0] for t in term],
                            dtype=np.float64)
    is_index_by_root = np.array([r in INDEX_ROOTS for r in roots])
    dte_b = _bucket_dte(j.dte)
    mny_b = _bucket_mny(j.strike, spot_by_root[und], j.right,
                        is_index_by_root[und])
    short_otm = np.isin(dte_b, SHORT_DTE_IDX) & np.isin(mny_b, OTM_MNY_IDX)

    cls_by_root = [rc.classify(r, s_iso) for r in roots]
    bars_syms = _bars_symbols()

    def has_bars(ri: int) -> bool:
        cands = {roots[ri], term[ri], rc.occ_form(term[ri]),
                 rc.directory_form(term[ri])}
        return any(c in bars_syms for c in cands if c)

    per_root: dict[int, float] = {}
    for code, p in zip(und[keep], fx.premium[keep]):
        per_root[int(code)] = per_root.get(int(code), 0.0) + float(p)

    idx_roots, missing_roots, other_cls, idx_additions = {}, {}, {}, {}
    equity_prem = 0.0
    for ri, p in per_root.items():
        r, cls = roots[ri], cls_by_root[ri]
        if r in INDEX_ROOTS:
            idx_roots[r] = p
        elif cls == "index":
            idx_additions[r] = p                  # G.8 additions (XAU et al.)
        elif cls == "equity":
            if has_bars(ri):
                equity_prem += p
            else:
                missing_roots[r] = p              # no_spot_missing, per-root
        else:
            other_cls[f"{r} [{cls}]"] = p

    def _arm_shares(mask: np.ndarray) -> dict:
        prem = fx.premium[mask]
        codes = und[mask]
        tot = float(prem.sum())
        adj = uncls = 0.0
        for code, p in zip(codes, prem):
            c = cls_by_root[int(code)]
            if c == "adjusted":
                adj += float(p)
            elif c == "unclassified":
                uncls += float(p)
        return {"classified_premium_usd": tot,
                "adjusted_share": adj / tot if tot else None,
                "unclassified_share": uncls / tot if tot else None}

    out = {
        "cache_version": CENSUS_CACHE_VERSION,
        "date": s_iso, "ml_source": str(ml_source),
        "join_rate": join_rate, "label_coverage": coverage,
        "classified_premium_usd": float(fx.premium[keep].sum()),
        "equity_classified_premium_usd": equity_prem,
        "no_spot_index_per_root": dict(sorted(
            idx_roots.items(), key=lambda kv: -kv[1])),
        "index_additions_per_root": dict(sorted(
            idx_additions.items(), key=lambda kv: -kv[1])),
        "no_spot_missing_per_root": dict(sorted(
            missing_roots.items(), key=lambda kv: -kv[1])),
        "other_class_per_root": dict(sorted(
            other_cls.items(), key=lambda kv: -kv[1])),
        "arm_shares": {"ALL": _arm_shares(keep),
                       "SHORT_OTM": _arm_shares(keep & short_otm)},
        "print_fence": {
            "total_premium_usd": float(fx.premium.sum()),
            "print_unclassified_usd": float(fx.premium[fx.side == 2].sum()),
            "print_wide_spread_usd": float(fx.premium[fx.side == 3].sum()),
            "package_masked_classified_usd":
                float(fx.premium[classified & (ml != 0)].sum()),
        },
    }
    CENSUS_CACHE.mkdir(parents=True, exist_ok=True)
    tmp = cache.with_suffix(".tmp")
    tmp.write_text(json.dumps(out))
    os.replace(tmp, cache)
    logger.info("census %s: classified $%.1fB, index $%.1fB, missing $%.1fM",
                s_iso, out["classified_premium_usd"] / 1e9,
                sum(idx_roots.values()) / 1e9,
                sum(missing_roots.values()) / 1e6)
    return out


# ------------------------------ stages ------------------------------

def stage_obligation5() -> dict:
    _gate("obligation5")
    rec = _load_record()
    _require_stage_absent(rec, "obligation5")
    backfill = _require_backfill()
    ob3 = _ob3()
    tier = int(ob3["obligation_3b"]["tier"])
    panel = build_panel()
    masks = CellMasks(panel, tier)
    cal_dates, spy_o, spy_c = spy_calendar()
    cal_pos = {d: i for i, d in enumerate(cal_dates)}

    out: dict = {
        "obligation": "G.10 #5",
        "environment": _environment(),
        "tier": tier, "tier_source": "obligation_3b record (first-run-governs)",
        "backfill_evidence": backfill,
        "panel": {"first": panel.sessions[0].isoformat(),
                  "last": panel.sessions[-1].isoformat(),
                  "sessions": len(panel.sessions),
                  "identities": int(len(panel.names))},
    }
    windows = {}
    for w in ("train", "judgment"):
        windows[w] = build_cell_window(panel, masks, w, with_alpha=False)
        out[w] = {"census": windows[w]["census"],
                  "series_dates_list": windows[w]["dates"],
                  "calendar_positions": windows[w]["gaps"].tolist(),
                  "gap_vector": windows[w]["gap_vector"]}
    judg = windows["judgment"]
    out["G"] = len(judg["dates"])
    out["event_count"] = judg["census"]["events_gradeable"]

    # G.2: the regime-span rule is measured on the cell's EVALUABLE EVENT
    # dates (>=1 gradeable event), which can exceed the both-arms series dates.
    spans = {}
    for s_iso in judg["census"]["event_dates_list"]:
        k0 = cal_pos[s_iso]
        if k0 + HORIZON >= len(cal_dates):
            raise SystemExit(f"SPY bars end before {s_iso}+10td — the record "
                             "cannot run yet (benchmark blocking, G.2)")
        spans[s_iso] = float(spy_c[k0 + HORIZON] / spy_o[k0 + 1] - 1.0)
    out["spy_forward_returns"] = spans
    out["regime_buckets"] = {
        "le_-2pct": [d for d, r in spans.items() if r <= -REGIME_CUT],
        "open_interval": [d for d, r in spans.items()
                          if -REGIME_CUT < r < REGIME_CUT],
        "ge_+2pct": [d for d, r in spans.items() if r >= REGIME_CUT]}
    per_date = {r["date"]: r for r in judg["census"]["per_date"]}
    out["named_tail_dates"] = {}
    for d in NAMED_TAIL_DATES:
        row = {"events_gradeable": per_date.get(d, {}).get("events_gradeable"),
               "spy_fwd_10td": spans.get(d)}
        if row["spy_fwd_10td"] is None:
            row["note"] = ("ZERO gradeable events on a NAMED tail date — the "
                           "regime-span rule hinges here (G.10 #5); loud by "
                           "design")
        out["named_tail_dates"][d] = row

    rc = classification()
    rows = [_census_session(s, rc) for s in judg["dates"]]
    tot_cls = sum(r["classified_premium_usd"] for r in rows)
    tot_idx = sum(sum(r["no_spot_index_per_root"].values()) for r in rows)
    tot_missing = sum(sum(r["no_spot_missing_per_root"].values()) for r in rows)
    tot_adj = sum(sum(p for k, p in r["other_class_per_root"].items()
                      if k.endswith("[adjusted]")) for r in rows)
    tot_uncls = sum(sum(p for k, p in r["other_class_per_root"].items()
                        if k.endswith("[unclassified]")) for r in rows)
    out["corpus_census"] = {
        "per_session": rows,
        "classified_premium_total_usd": tot_cls,
        "no_spot_index_total_usd": tot_idx,
        "no_spot_index_share": tot_idx / tot_cls if tot_cls else None,
        "no_spot_missing_total_usd": tot_missing,
        "adjusted_class_total_usd": tot_adj,
        "adjusted_class_share": tot_adj / tot_cls if tot_cls else None,
        "unclassified_class_total_usd": tot_uncls,
        "unclassified_class_share": tot_uncls / tot_cls if tot_cls else None,
    }
    rec["stages"]["obligation5"] = out
    _save_record(rec)
    return out


def stage_obligation6() -> dict:
    _gate("obligation6")
    rec = _load_record()
    _require_stage_absent(rec, "obligation6")
    if "obligation5" not in rec["stages"]:
        raise SystemExit("G.10 ordering: obligation 5 precedes obligation 6")
    ob5 = rec["stages"]["obligation5"]
    _require_backfill()
    ob3 = _ob3()
    tier = int(ob3["obligation_3b"]["tier"])
    panel = build_panel()
    masks = CellMasks(panel, tier)
    train = build_cell_window(panel, masks, "train", with_alpha=True)
    if train["dates"] != ob5["train"]["series_dates_list"]:
        raise SystemExit("training series dates diverge from the obligation-5 "
                         "record — data changed under the frozen inputs; "
                         "blocking")
    if train["gap_vector"] != ob5["train"]["gap_vector"]:
        raise SystemExit("training gap vector diverges from the obligation-5 "
                         "record — blocking")
    cal = calibrate(train["diffs"],
                    np.array(ob5["judgment"]["calendar_positions"], int),
                    seed=CAL_SEED_G)
    cal["environment"] = _environment()
    cal["power_limited"] = bool(cal["mde_atr"] is None
                                or cal["mde_atr"] > MDE_CEILING)
    cal["train_series_dates"] = train["dates"]
    cal["train_series"] = train["diffs"].tolist()
    cal["train_event_cohort_alpha"] = float(np.mean(train["event_means"]))
    cal["train_baseline_alpha"] = float(np.mean(train["base_means"]))
    cal["train_tier_cohort_alpha"] = train["tier_cohort_alpha"]
    cal["train_tier_cohort_n"] = train["tier_cohort_n"]
    cal["obligation"] = "G.10 #6"
    rec["stages"]["obligation6"] = cal
    _save_record(rec)
    return cal


def decide_verdict(evaluable: bool, f: float, f0: float, gate: dict,
                   t: float, cutoff: float, mean: float,
                   mde: float | None) -> tuple[str, str | None]:
    """G.2's ordered checklist, steps 1-3 — every input combination lands
    exactly once. Every DEAD prints the recorded MDE beside the observed
    effect (G.2's closing rule)."""
    if not evaluable:
        return ("UNEVALUABLE (ALL, Q5, 10td) — not alive; no substitute "
                "cell, no re-declaration (G.2 step 1)", None)
    if f <= f0 or (gate["resolves_1pt"] and gate["lcb"] <= 0.0):
        return (WORDING_DEAD_DILUTION,
                f"f {f:.4%} vs f0 {f0:.4%}; observed effect {mean:+.4f} ATR "
                f"beside MDE {mde} ATR (G.2: every DEAD prints both)")
    if t >= cutoff:
        return WORDING_ALIVE, None
    return (WORDING_DEAD_TEST,
            f"t {t:.3f} < cutoff {cutoff:.3f}; observed effect {mean:+.4f} "
            f"ATR beside MDE {mde} ATR")


def _write_verdict_artifact(rec: dict, out: dict) -> None:
    """The artifact h1 waits on. It carries the MDE and observed effect on
    its face — the registered no-stripping rule applies to every transport."""
    tmp = VERDICT_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(
        {"registration": rec["registration"], "cell": out["cell"],
         "verdict": out["verdict"],
         "verdict_note": out.get("verdict_note"),
         "mde_atr": out.get("mde_atr"),
         "observed_effect_atr": out.get("observed", {}).get("diff_series_mean"),
         "record": str(RECORD_PATH)}, indent=1))
    os.replace(tmp, VERDICT_PATH)
    logger.info("verdict artifact written: %s", VERDICT_PATH)


def stage_score() -> dict:
    _gate("score")
    rec = _load_record()
    _require_stage_absent(rec, "score")
    for need in ("obligation5", "obligation6"):
        if need not in rec["stages"]:
            raise SystemExit(f"G.10 ordering: stage {need} must precede score")
    ob5, cal = rec["stages"]["obligation5"], rec["stages"]["obligation6"]
    if not isinstance(ob5.get("corpus_census"), dict):
        raise SystemExit("obligation 5's corpus census is absent from the "
                         "record — blocking")
    _require_backfill()
    ob3 = _ob3()
    tier = int(ob3["obligation_3b"]["tier"])
    if tier != 4:
        raise SystemExit(f"recorded tier {tier} != 4 (Q5) — the registered "
                         "verdict wordings pin Q5; a different tier requires "
                         "a new dated addendum")
    arm = ob3["arms"]["ALL"]
    f, f0 = float(arm["f"][tier]), float(arm["f0"][tier])
    se_pt = float(arm["se_pt"][tier])
    distinct = int(arm["distinct_tested"][tier])
    gate = arm["dilution_gate"][tier]

    panel = build_panel()
    masks = CellMasks(panel, tier)
    # input-side cross-tie to the obligation-3 universe (G.2's fresh-grader
    # hazard): this panel must reproduce the recorded fired/tested EXACTLY
    jidx = _window_indices(panel, "judgment")
    n_fired = sum(int(masks.events(j).sum()) for j in jidx)
    n_tested = sum(int((masks.tested[:, j] & masks.in_tier[:, j]).sum())
                   for j in jidx)
    if (n_fired != int(arm["real_fired"][tier])
            or n_tested != int(arm["real_tested"][tier])):
        raise SystemExit(
            f"cell universe diverged from the obligation-3 record: fired "
            f"{n_fired} vs {arm['real_fired'][tier]}, tested {n_tested} vs "
            f"{arm['real_tested'][tier]} — blocking (G.2 one-implementation)")

    judg = build_cell_window(panel, masks, "judgment", with_alpha=True)
    if judg["dates"] != ob5["judgment"]["series_dates_list"]:
        raise SystemExit("judgment series dates diverge from the obligation-5 "
                         "record — data changed under the frozen inputs; "
                         "recorded as a fault, not re-run")
    if judg["gap_vector"] != ob5["judgment"]["gap_vector"]:
        raise SystemExit("judgment gap vector diverges from the obligation-5 "
                         "record — the benchmark calendar changed; blocking")

    checklist = {
        # G.2 step 1: ">= 20 distinct complete-or-terminated event dates" —
        # dates with >=1 gradeable EVENT, not the both-arms series dates.
        "event_dates": judg["census"]["event_dates_gradeable"],
        "min_event_dates": 20,
        "series_dates": len(judg["dates"]),
        "events_gradeable": judg["census"]["events_gradeable"],
        "min_events": 30,
        "null_se_pt": se_pt, "null_se_bar_pt": 0.5,
        "distinct_tested": distinct, "distinct_bar": 1000,
        "gate_resolves_1pt": bool(gate["resolves_1pt"]),
        "regime_buckets": {k: len(v)
                           for k, v in ob5["regime_buckets"].items()},
        "power_limited": bool(cal["power_limited"]),
        "series_len_ok": len(judg["dates"]) >= 2,   # estimator undefined below
    }
    evaluable = (
        checklist["event_dates"] >= 20
        and checklist["events_gradeable"] >= 30
        and se_pt <= 0.5 and distinct >= 1000
        and checklist["gate_resolves_1pt"]
        and all(n > 0 for n in checklist["regime_buckets"].values())
        and not checklist["power_limited"]
        and checklist["series_len_ok"])

    def _dist(rows: list[dict]) -> dict | None:
        counts = sorted(r["events_gradeable"] for r in rows
                        if r.get("events_gradeable") is not None)
        return ({"min": counts[0], "median": counts[len(counts) // 2],
                 "max": counts[-1]} if counts else None)

    cc = ob5["corpus_census"]
    out: dict = {
        "cell": f"(ALL, Q{tier + 1}, 10td)",
        "environment": _environment(),
        "checklist": checklist,
        "dilution_inputs": {"f": f, "f0": f0, "lcb": gate["lcb"],
                            "mrd_pt": gate["mrd_pt"],
                            "source": str(OB3_RECORD)},
        "dilution_reporting": {
            # G.9b's reporting-only correction; enters no gate
            "q_hat": f0 / f if f else None,
            "note": ("f < f0 makes 1 - q_hat negative and lift_real "
                     "meaningless; recorded per G.9b's reporting clause "
                     "with that caveat" if f <= f0 else None)},
        "universe": {   # G.5: no universe numbers => not usable as evidence
            "panel_identities": int(len(panel.names)),
            "per_tier_distinct_tested": arm["distinct_tested"],
            "per_tier_fired": arm["real_fired"],
            "unclassified_class_share": cc["unclassified_class_share"],
            "adjusted_class_share": cc["adjusted_class_share"]},
        # G.10 #5's shape clause: reported ON THE FACE of the verdict
        "shape_disclosure": {
            "train_events_per_date": _dist(
                ob5["train"]["census"]["per_date"]),
            "judgment_events_per_date": _dist(judg["census"]["per_date"])},
        "mde_atr": cal["mde_atr"], "cutoff_t99": cal["cutoff_t99"],
        "train_precommitment": {
            "per_tier_cohort_alpha": cal["train_tier_cohort_alpha"],
            "per_tier_cohort_n": cal["train_tier_cohort_n"],
            "event_cohort_alpha": cal["train_event_cohort_alpha"],
            "baseline_alpha": cal["train_baseline_alpha"]},
        "grid": {str(hz): {"diffs": judg["grid_diffs"][str(hz)],
                           "not_elapsed":
                           judg["census"]["grid_not_elapsed"][str(hz)],
                           "observations":
                           judg["census"]["grid_observations"][str(hz)],
                           "note": "reported, non-authoritative (G.3)"}
                 for hz in H_GRID},
        "census": judg["census"],
    }
    if not evaluable:
        # recorded BEFORE any test statistic exists — a thin or unpowered
        # cell is a registered outcome, never a crash (review finding)
        verdict, note = decide_verdict(False, f, f0, gate, 0.0, 0.0, 0.0,
                                       cal["mde_atr"])
        out["verdict"] = verdict
        rec["stages"]["score"] = out
        _save_record(rec)
        _write_verdict_artifact(rec, out)
        return out

    t, mean, se, fb = nw_stats(judg["diffs"], judg["gaps"])
    if not (np.isfinite(t) and np.isfinite(cal["cutoff_t99"])):
        raise SystemExit(f"non-finite test statistic (t={t}, cutoff="
                         f"{cal['cutoff_t99']}) — computation fault, never "
                         "a verdict")
    out["observed"] = {"t": t, "diff_series_mean": mean, "se": se,
                       "nw_fallback": bool(fb),
                       "series_dates": judg["dates"],
                       "series": judg["diffs"].tolist()}
    verdict, note = decide_verdict(True, f, f0, gate, t,
                                   cal["cutoff_t99"], mean, cal["mde_atr"])
    out["verdict"] = verdict
    if note:
        out["verdict_note"] = note
    rec["stages"]["score"] = out
    _save_record(rec)
    _write_verdict_artifact(rec, out)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Addendum G obligations 5+6 + the registered scoring run")
    ap.add_argument("--stage", required=True,
                    choices=["obligation5", "obligation6", "score"])
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    if args.stage == "obligation5":
        stage_obligation5()
    elif args.stage == "obligation6":
        stage_obligation6()
    else:
        out = stage_score()
        print(json.dumps({"verdict": out["verdict"],
                          "verdict_note": out.get("verdict_note"),
                          "mde_atr": out.get("mde_atr"),
                          "observed_effect_atr":
                          out.get("observed", {}).get("diff_series_mean")},
                         indent=1))


if __name__ == "__main__":
    main()
