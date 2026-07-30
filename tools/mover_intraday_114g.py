"""Roadmap 1.14g: the vol-clustering fork — RV-nested controls + placebos.

PRE-REGISTERED 2026-07-30 (`References/FLOW_ROADMAP.md` §1.14g, v3) BEFORE any
RV-stratified or placebo statistic existed anywhere. This file implements that
registration LITERALLY. Every point where the registered text admitted more than
one reading is recorded in `AMBIGUITIES` below and shipped in the output — the
tool does not get to improve the registration, and it does not get to pretend the
text was unambiguous where it was not.

1.14f found three magnitude stars at the intraday grain and left ONE confound
undischarged: its peer cell matches session, time-of-day and liquidity but NOT
contemporaneous realized vol. So the two claims this file keeps separate:

  (i)  PRODUCT — does the 1.14f screen add anything over a free equity-RV
       monitor computed from the SAME bars at the SAME decision time?
  (ii) SCIENCE — do the stars carry options-flow information at all, or do they
       re-discover that volatility clusters, with the options tape as an
       expensive thermometer?

**NOT A BACKTEST.** No entry, no exit, no slippage, no sizing, no P&L, no hit
rate. The output is a distribution: stratified fired-minus-complement median
normalized move, with intervals, plus occupancy/balance/NA accounting at every
stage.

The registered structure, and where it lands below:
  * the stratifier = `rv_park`, Parkinson RV over the ONE-BAR-GAPPED window
    [b-W/5, b-1), per-bin averaged, own-normalized by the name's own trailing-20
    (b, W) median (`rv_ratio`).
  * assignment = equal-count rank cuts inside each 1.14f peer cell (session x
    boundary x $-vol quintile), per W, side='left'.
  * analysis strata = POOLED ACROSS SESSIONS: boundary x $-vol quintile x
    rv-quintile. Session is absorbed by the block bootstrap, never by a stratum
    (v1's session-nested strata fabricated ~64% of the 1.14f effect under an
    exact null).
  * Arm 1 (primary), Arm 1b (lagged adjudicator), Arm 2a (machinery placebo),
    Arm 2b (split-sample survival benchmark).
  * two mandatory calibration gates, a balance-adequacy gate, reading rules
    (a)-(d), and SEPARATE science / product verdicts.
  * SEED=20260809, circular moving-block bootstrap over SESSIONS, block=5,
    10,000 draws, ONE shared resampling per arm.

Implementation lock (registered): this file IMPORTS the 1.14f machinery from
`tools.mover_intraday` — tape/window/component/firing/cell-mask/Ring/bootstrap
— there is NO copied firing path, and it gates on REPRODUCING 1.14f's four
primary cells against the locked JSON before emitting anything.

    python tools/mover_intraday_114g.py                    # registered run
    python tools/mover_intraday_114g.py --smoke-verify     # vectorizer proof
    python tools/mover_intraday_114g.py --vectorized       # proof, then run
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import platform
import sqlite3
import sys
import time
import warnings
from datetime import date
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import tools.mover_intraday as mi  # noqa: E402
from tools.mover_controls import TRAIN_END, _tier_quintiles  # noqa: E402
from tools.mover_forward import _roll_med60  # noqa: E402

logger = logging.getLogger("mover_intraday_114g")

CORPUS_DB = ROOT / "data" / "flow_corpus.db"
BARS_DIR = ROOT / "data" / "equity_bars_5min"
EARN_DB = ROOT / "data" / "research" / "earnings.db"
JOIN_LEDGER = ROOT / "data" / "alpaca_conditions" / "join_ledger.json"
LOCKED_114F = ROOT / "data" / "research" / "mover_intraday_114f.json"
OUT = ROOT / "data" / "research" / "mover_intraday_114g.json"
OUT_KEYS = ROOT / "data" / "research" / "mover_intraday_114g_fired_keys.npz"

# ---- registered constants (a prereg fence: a tidy-up must not retune these) --
SEED = 20260809
N_BOOT = 10_000
BLOCK = 5                       # SESSIONS

RV_LOOKBACK = 20                # trailing sessions for the own-normalizer
MIN_RV_SESSIONS = 10            # >=10 valid sessions else NA
RV_COVERAGE = 0.80              # >=80% of the window's own bin count present
LN2_4 = 4.0 * np.log(2.0)       # Parkinson's 4 ln 2

MIN_ASSIGN_ROWS = 25            # a peer cell below this contributes NO strata
MIN_ASSIGN_DISTINCT = 5         # ...and likewise below 5 distinct rv_ratio values
QUINT_PCTL = (20.0, 40.0, 60.0, 80.0)
DECILE_PCTL = tuple(float(x) for x in range(10, 100, 10))
N_QUINT = 5

OCC_MIN_FIRED = 20              # occupancy floor: >=20 fired AND
OCC_MIN_COMPLEMENT = 20         # ...>=20 complement rows, else the stratum drops
DROPPED_MASS_MAX = 0.10         # >10% of fired mass in dropped strata => UNAVAILABLE
PER_DRAW_DROPPED_P95_MAX = 0.20  # p95 of per-draw dropped fired mass > 20% => same

PERM_N = 10                     # permuted-label null: 10 permutations
PERM_TOL = 0.02                 # ABSOLUTE tolerance, by design (certifies the estimator)
COARSE_MIN_ABS = 0.005          # (d): draws with |coarse delta| below this are undefined
D_DROP_MAX = 0.05               # >5% dropped draws => (d) UNAVAILABLE

BAND_FLOOR = 0.01               # negligible band = max(0.01, 0.25 x |pooled 1.14f delta|)
BAND_FRACTION = 0.25
RELIABILITY_FLOOR_FRAC = 0.80   # W=120's odd-even correlation vs the primary cell's

# The three component primaries are THE multiplicity budget. `short_dte_share`
# is parked by the registration, not retested as a primary here.
COMPONENTS = ("vol_max_ratio", "largest_print_ratio", "concentration")
# The registered pooled 1.14f effects and the bands they imply. The registration
# states the bands as literals; they are law here, and the reproduce gate proves
# the pooled effects they were derived from.
POOLED_114F = {"vol_max_ratio": 0.16421, "largest_print_ratio": 0.11441,
               "concentration": -0.02073}
REGISTERED_SIGN = {"vol_max_ratio": 1, "largest_print_ratio": 1,
                   "concentration": -1}
NEGLIGIBLE_BAND = {"vol_max_ratio": 0.041, "largest_print_ratio": 0.029,
                   "concentration": 0.010}

# power constant for the reported minimum detectable effect (see AMBIGUITIES)
MDE_Z = 1.959963984540054 + 0.8416212335729143

# stratum ids: (boundary x $-vol quintile x rv-quintile) and the coarse baseline
N_COARSE = len(mi.BOUNDARY_BINS) * N_QUINT
N_STRATA = N_COARSE * N_QUINT

RV_KINDS = ("rv", "lag", "odd", "even", "c2c", "zg")
# stratifier column per RV kind, and the analysis scope each is registered for
PRIMARY_ONLY_KINDS = ("lag", "c2c", "zg")

# The registration's own advance expectations and disclosure, carried verbatim
# so a reader meets them at the same time as the numbers (and so a surprising
# result cannot be re-narrated afterwards as the one that was expected).
EXPECTATIONS = (
    "Conditioned on the SEEN 1.14f result — a motivated follow-up, and says so. "
    "Arm 2a strongly positive; every component attenuates substantially under "
    "Arm 1 (simulation says ~40-57% retention is compatible with ZERO flow "
    "information — which is exactly why the science test is beating the Arm 2b "
    "benchmark, not retaining a fraction); vol_max_ratio most exposed (its "
    "numerator is mechanically coupled to RV); largest_print_ratio the likelier "
    "science survivor; concentration expected PRODUCT-DEMOTED with the "
    "mechanical descriptive carrying the story. short_dte_share is parked, not "
    "retested as a primary here."
)
DISCLOSURE = (
    "Fifth forward read of the training corpus. Design conditioned on the "
    "observed 1.14f result; registered before any RV-stratified or placebo "
    "number existed; v1->v3 changes were driven entirely by pre-run adversarial "
    "review and simulation against 1.14f's published distributions, never by any "
    "1.14g outcome. No new data: same feature tape (v7 fence), same outcome "
    "store, same train mask (<= 2026-05-31); the test tail stays untouched."
)
SIMULATION_PRIOR = (
    "Under a pure-clustering null, components retain ~43-47% of their raw effect "
    "after quintile stratification with ZERO flow information; the split-sample "
    "placebo retains ~57%. 'CI clear of zero' alone is therefore NOT diagnostic "
    "— survival requires beating the Arm 2b benchmark."
)

# Whether the vectorized per-stratum bootstrap has been PROVEN bit-identical to
# `mi.boot_medians` in THIS process. The registration allows vectorization only
# behind that proof, so the flag is process state, never a config knob.
_VECTOR_PROVEN = False


AMBIGUITIES = {
    "bootstrap_stratum_weights": (
        "the registration fixes w_s = fired-count share for the POINT estimate "
        "and says only that 'within-draw one-sided-empty strata drop with "
        "weights renormalized'. It never says a draw RE-COUNTS its own fired "
        "rows, so the base weights stay the point-estimate fired-count shares "
        "and a draw only renormalizes them over the strata that survive that "
        "draw. Re-counting per draw would make the estimand drift between "
        "draws and would no longer be the ATT-on-alarms weighting the "
        "registration names."),
    "dropped_mass_denominator": (
        "'>10% of a component's fired rows in occupancy-dropped strata' is "
        "measured against that ANALYSIS CELL's rv-valid fired rows — the "
        "registration defines the estimand as 'recomputed per (W, H) analysis "
        "cell after that cell's NA filtering', so the denominator is that same "
        "post-filter fired set, not the 1.14f fired total (which includes "
        "rv-NA rows the cell cannot see)."),
    "unavailable_is_per_cell": (
        "the >10% dropped-mass rule and the per-draw p95>20% rule are "
        "evaluated PER analysis cell. So (a) can be UNAVAILABLE while a (b) "
        "horizon is evaluable, and each is recorded on its own cell. The "
        "registration states the rule for 'that component's primary'; applying "
        "it per cell is the literal extension and never lets an evaluable cell "
        "borrow an UNAVAILABLE cell's authority — a component whose PRIMARY is "
        "UNAVAILABLE cannot pass the science test at all."),
    "coverage_rounding": (
        "'>=80% of W/5-1 bins present' is implemented as "
        "n_present >= 0.80 * n_bins on floats, mirroring 1.14f's BAR_COVERAGE. "
        "At W=60 the 11 gapped bins need >=9 (8.8), the 6 even bins need >=5 "
        "(4.8) and the 5 odd bins need >=4 (exactly 4.0 passes). No ceil() is "
        "applied — the registration writes a percentage, not a count."),
    "post_close_bars_are_absent": (
        "'the window holds >=80% of its bins at-or-before session_last_bin "
        "(post-close stragglers never count)' is implemented by NaN-ing the "
        "high/low/close panels at bins >= session_last_bin before any RV is "
        "computed — the same masking 1.14f applies to its outcome panel. A "
        "straggler bar is therefore ABSENT, not present-and-ignored, so it can "
        "neither satisfy the coverage rule nor enter a sum."),
    "assignment_population_generalizes": (
        "the registration defines the assignment population for `rv_ratio` "
        "(>=20 fenced classified prints AND q >= 0 AND valid rv_ratio) and then "
        "says Arm 1b and Arm 2b use 'the same assignment algorithm'. So each "
        "stratifier substitutes ITS OWN ratio into the validity clause: the "
        "lagged panel assigns on valid rv_ratio_lag, Arm 2b on valid EVEN-bin "
        "ratio, and the two disclosed secondary RV variants on their own. The "
        "print-count and quintile clauses are unchanged, so one shared "
        "assignment still serves all components within each stratifier."),
    "quintile_distinct_values": (
        "'<5 distinct values' counts np.unique over the assignment "
        "population's ratio values in that peer cell (session x boundary x "
        "$-vol quintile, per W) — the cell whose breakpoints would be "
        "degenerate, which is the cell the rule protects."),
    "arm2b_needs_its_stratifier": (
        "Arm 2b fires on the ODD-bin ratio and stratifies on the EVEN-bin "
        "ratio, so a row needs BOTH: rows with a valid odd ratio but no "
        "even-quintile are excluded from both sides and counted per cell as "
        "`n_na_stratifier_rows`. This mirrors Arm 1's registered handling of NA "
        "rv_ratio rather than inventing a partial-stratum fallback."),
    "arm2a_scope_and_gate": (
        "Arm 2a is reported UNSTRATIFIED at every (W, H) cell (the "
        "registration says 'unstratified everywhere it appears' without naming "
        "cells). Calibration gate 2 — 'Arm 2a must star POSITIVE' — is read at "
        "the PRIMARY cell (W=60, H=2h), the cell every other primary is read "
        "at."),
    "lagged_scope_is_w60": (
        "Arm 1b is registered 'reported at W=60, all three horizons', so the "
        "lagged panel is computed at W=60 only. The BOUNDARY-MATCHED "
        "restriction of Arm 1 is mechanical (b >= the first boundary whose "
        "lagged window lies fully inside RTH: 11:30 at W=60, 13:30 at W=120, "
        "10:30 at W=30) and is therefore reported per W without needing lagged "
        "values at W != 60. The restriction is computed from the geometry, "
        "never from realized NA counts."),
    "secondary_rv_variants_scope": (
        "the two disclosed secondary RV variants (close-to-close; zero-gap "
        "window) are computed at the PRIMARY cell only (W=60, H=2h). The "
        "registration lists them as reported-with-no-authority and never names "
        "a grid; the primary cell is the only cell whose reading they could "
        "inform, and computing them everywhere would add 18 more numbers with "
        "no registered use."),
    "close_to_close_bridges_gaps": (
        "'returns between consecutive PRESENT closes' means a missing bin is "
        "bridged: the return spans the gap rather than being dropped. The "
        "return count is then n_present_closes - 1 and the >=80% rule is "
        "applied to that count (>=8 of 10 at W=60). This is the registered "
        "wording taken literally; the alternative (only adjacent-bin returns) "
        "would silently change the measure's horizon mix."),
    "permutation_gate_occupancy_mismatch": (
        "gate 1 compares a permuted STRATIFIED delta against the coarse delta on "
        "the same locked rows, but the two apply the occupancy floor at "
        "different grains, so they can end up weighting slightly different row "
        "sets even under an exact null. That puts a small floor on the "
        "achievable deviation which has nothing to do with the estimator. The "
        "registration fixes the tolerance at +-0.02 ABSOLUTE 'by design', so the "
        "floor is not corrected for; all 10 deviations and each permutation's "
        "included-stratum count are reported so the floor is visible."),
    "extra_rows_from_the_placebo_pools": (
        "the emitted row table keeps rows that are in Arm 2a's or Arm 2b's pool "
        "but in NO component pool (rv defined where every component is NA). They "
        "are needed for the placebos and cannot reach a component cell, because "
        "`mi.cell_mask` requires that component's own pool bit. The reproduce "
        "gate therefore still compares like with like — asserted by a test "
        "rather than argued."),
    "coarse_baseline_occupancy": (
        "'a coarse baseline failing the occupancy floor likewise makes (d) "
        "UNAVAILABLE' is implemented as: the coarse baseline is UNAVAILABLE "
        "when it has no included stratum, or when its own dropped fired mass "
        "exceeds the same 10% threshold — the same rule the stratified arm is "
        "held to, since the registration calls it 'the same functional'."),
    "d_test_dropped_draw_denominator": (
        "the (d) dropped-draw share is measured against all N_BOOT draws. A "
        "draw is dropped when |coarse delta| < 0.005 for EITHER arm (the "
        "registered guard) or when either arm's draw is NaN (every stratum "
        "one-sided-empty in that draw) — a NaN draw is as undefined as a "
        "near-zero denominator, and counting only the near-zero ones would let "
        "a degenerate arm slip past the >5% rule."),
    "attenuation_ratio_is_signed": (
        "the attenuation ratio is the SIGNED quotient stratified/coarse. For a "
        "negative-sign component (concentration) both are negative and the "
        "ratio is positive, so it stays comparable to Arm 2b's benchmark "
        "without an abs() that would hide a sign flip. A sign flip shows up as "
        "a NEGATIVE ratio, which cannot beat the benchmark — the registered "
        "sign-flip branch records it as a finding of its own."),
    "balance_quintile_gap": (
        "the balance gate compares a per-quintile number against 10% of the "
        "unstratified gap. The registration says 'the within-stratum "
        "fired-vs-complement median rv_ratio gap ... in ALL evaluable "
        "quintiles' without naming the within-quintile aggregation, so: the "
        "quintile's gap = |fired-weighted mean over that quintile's INCLUDED "
        "strata of (median_fired - median_complement)|, and the unstratified "
        "gap = |median rv_ratio fired - median rv_ratio complement| over the "
        "cell's included rows. Fired-weighting matches the estimand's own "
        "weighting, so the gate measures the imbalance the estimate actually "
        "carries."),
    "balance_evaluability_folds_coverage": (
        "'an rv-quintile with no included stratum is NOT-EVALUABLE' plus 'each "
        "evaluable quintile must hold >=1 included stratum at >=6 of 8 "
        "primary-cell boundaries' are read as ONE evaluability test: a "
        "quintile is evaluable iff it has an included stratum at >=6 of the "
        "primary cell's boundaries. The alternative (coverage as a separate "
        "hard fail) would make 'evaluable' mean two different things in the "
        "same paragraph. The 8 boundaries are derived mechanically — at "
        "(W=60, H=2h) exactly 8 boundaries have both a full window and a "
        "reachable horizon — never hard-coded."),
    "reliability_primary_reference": (
        "'W=120's odd-even correlation ... below the floor (odd-even "
        "correlation >=80% of the primary cell's)' needs ONE number per cell, "
        "but the reliability table is per (b, W). The cell's number is the "
        "row-count-weighted mean of its own boundaries' Pearson correlations, "
        "over the boundaries the analysis cell actually uses. Reported per "
        "boundary as well, so the aggregation can be checked."),
    "co_firing_first_fire": (
        "'share of a component's fired rows also Arm 2a-fired at the same "
        "(name, session, boundary)' is ambiguous about Arm 2a's first-fire "
        "filter, so BOTH are reported: `any_hit` uses Arm 2a's raw p90 hit at "
        "that boundary (the co-incidence the sentence describes) and "
        "`first_fire_only` uses Arm 2a's emitted fired rows. The denominator "
        "is the component's fired rows in the primary cell either way."),
    "above_peer_feature_return": (
        "'share of fired rows with above-peer feature-window |cum return|' "
        "defines neither the peer nor 'above', so: peer = the row's own 1.14f "
        "peer cell (session x boundary x $-vol quintile) and 'above' = "
        "strictly greater than that cell's MEDIAN over the component's pool "
        "rows. The feature-window |cum return| is |log(close at b-1) - "
        "log(close at the bar before the window's first bin)| — the same price "
        "convention 1.14f's outcome uses at its own base — and is NA (excluded "
        "from the share's denominator) when either of those two bars is absent. "
        "A window starting at the open has no prior bar, so bin 0's close is the "
        "base there."),
    "raw_outcome_row_universe": (
        "the denominator-inflation diagnostics (the same estimand on the RAW "
        "unnormalized forward max |log return|, and on outcome / same-window "
        "rv_park) are run on the SAME rows as the normalized primary: the raw "
        "column is NA wherever the normalized one is. The registration names the "
        "diagnostics but not their row universe, and holding the rows fixed is "
        "what makes them a diagnostic OF the primary rather than a different "
        "study with a different denominator."),
    "decile_recut_minimums": (
        "the decile re-cut keeps the peer-cell minimums unchanged (>=25 rows, "
        ">=5 distinct values) because the registration names no decile-specific "
        "floor. It is assigned in the SESSION PASS over the same assignment "
        "population as the quintile cut (never from an emitted row table) so "
        "the two grains differ only in the cut. Decile strata are mechanically "
        "thinner, so the re-cut is reported as a POINT estimate with its full "
        "occupancy accounting and NO interval — it exists to say whether finer "
        "strata would have changed a balance verdict, which needs occupancy, "
        "not a CI."),
    "mde_definition": (
        "'the implied minimum detectable effect' is reported as "
        "(z_0.975 + z_0.80) x realized bootstrap SE = 2.8016 x SE, the "
        "conventional two-sided 95%/80%-power detectable effect. The "
        "registration asks for the number without defining the power, so the "
        "convention is stated rather than inferred; the SE it is built from is "
        "reported next to it."),
    "shared_resampling_is_per_arm": (
        "'ONE shared resampling' holds WITHIN an arm: the all-train arm draws "
        "one weight matrix over its 212 sessions and every cell, placebo and "
        "coarse baseline in that arm reuses it (which is what makes the paired "
        "(d) test well-defined). The join>=98 MIRROR has a different session "
        "count, so it builds its own matrix from the same SEED — exactly what "
        "1.14f did for the same arm."),
    "join98_reuses_the_assignment": (
        "the join>=98 mirror filters analysis ROWS only. Trailing rings AND the "
        "rv-quintile assignment are the all-train ones — the registration says "
        "the rings keep 1.14f's normalizer scope and that 'quintile "
        "assignments and stratum memberships are computed once on the real "
        "data', so re-cutting quintiles inside the subset would make it a "
        "different study rather than a robustness check."),
    "fired_key_artifact_scope": (
        "the persisted fired-row-key artifact covers the FOUR 1.14f components "
        "at all three W — the screen's alarms, which is what a 1.14h follow-up "
        "would need to join against. The placebo arms' firings are not the "
        "screen and are excluded so the artifact cannot be mistaken for one. "
        "The hash is taken over the canonical record bytes, not the .npz "
        "container (which carries non-deterministic metadata)."),
    "vectorizer_bit_identity": (
        "the smoke proof asserts equality of the vectorized and per-stratum "
        "draw vectors elementwise with NaN treated as equal. The vectorized "
        "path performs the SAME arithmetic in the SAME order as "
        "`mi.boot_medians` (cumsum then add the chunk base, then "
        "(0.0 + v_lo + v_hi) / 2.0), so the only pair of distinct bit patterns "
        "it could ever compare equal is +0.0 vs -0.0, which cannot arise from "
        "a normalized move. The registration says '10-session smoke subset' "
        "without saying how many strata, so the proof walks the real primary "
        "cell's strata for all three components up to a bounded array count and "
        "REPORTS how many it compared — a partial proof that says its own size."),
    "boundary_matched_arm1_scope": (
        "the boundary-matched restriction of Arm 1 (b >= the first lagged-valid "
        "boundary) is reported at W=60 for all three horizons — the cells Arm "
        "1b adjudicates — plus (W=120, H=2h), the replication cell. The "
        "registration says 'reported alongside the unrestricted primary' "
        "without naming a grid; these are the cells where a matrix branch could "
        "read it."),
    "gate_failure_blocks_reading": (
        "'failing either makes the run INCONCLUSIVE' is implemented as a "
        "run-level flag: every component result is still computed and emitted "
        "(suppressing them would hide what the gate failed on) but each carries "
        "`read_blocked=true` and the verdicts collapse to INCONCLUSIVE. The "
        "registration says the gates are evaluated BEFORE any component result "
        "is read, and a flag is the only way a JSON can carry that ordering."),
}


# --------------------------------------------------------------------------
# the stratifier — Parkinson RV, gapped, per-bin averaged
# --------------------------------------------------------------------------

def gapped_bins(boundary_bin: int, w_minutes: int) -> np.ndarray | None:
    """Absolute 5-minute bin indices of the GAPPED window `[b-W/5, b-1)`.

    The flow window MINUS its final bin, so the outcome-base bin b-1 contributes
    nothing to the stratifier: microstructure noise in the boundary price would
    otherwise sit on both sides of the row (a collider). None when the flow
    window itself would need bars from before 09:30 — the same rule 1.14f's
    `window_bins` applies, reused so the two grids cannot drift apart.
    """
    span = mi.window_bins(boundary_bin, w_minutes)
    if span is None:
        return None
    return np.arange(span[0], boundary_bin - 1)


def lagged_bins(boundary_bin: int, w_minutes: int) -> np.ndarray | None:
    """Absolute bins of the UNGAPPED lagged window `[b-2W/5, b-W/5)`.

    All W/5 bins: the collider that motivates the contemporaneous gap does not
    exist at the lag. None where the lagged window exits RTH, which at W=60
    mechanically excludes every boundary before 11:30.
    """
    n = w_minutes // mi.BAR_MIN
    lo = boundary_bin - 2 * n
    return np.arange(lo, boundary_bin - n) if lo >= 0 else None


def first_lagged_boundary(w_minutes: int) -> int:
    """Index into BOUNDARY_BINS of the first boundary with a valid lagged window.

    Mechanical, from the geometry — never from realized NA counts. W=60 => 11:30.
    """
    for bi, bb in enumerate(mi.BOUNDARY_BINS):
        if lagged_bins(bb, w_minutes) is not None:
            return bi
    return len(mi.BOUNDARY_BINS)


def rv_park(high: np.ndarray, low: np.ndarray, bins: np.ndarray,
            coverage: float = RV_COVERAGE) -> np.ndarray:
    """Per-name Parkinson RV over `bins`, per-bin averaged. NaN where NA.

    sqrt( sum_k ln(h_k/l_k)^2 / (4 ln2 * n_present) ).

    Registered absence conditions, enumerated: non-finite h or l, l <= 0, h < l.
    A bin with h == l is PRESENT and contributes exactly 0 — 11.6% of bars are
    zero-range and turning them into absences would make bin availability
    masquerade as vol, which is also why the divisor is `n_present` (the per-bin
    AVERAGE) rather than the window's nominal bin count.

    No time scaling: the own-normalized ratio cancels it.
    """
    if not len(bins):
        return np.full(high.shape[0], np.nan)
    h, lo = high[:, bins], low[:, bins]
    with np.errstate(invalid="ignore"):
        ok = np.isfinite(h) & np.isfinite(lo) & (lo > 0) & (h >= lo)
    n_present = ok.sum(axis=1)
    with np.errstate(invalid="ignore", divide="ignore"):
        r = np.log(np.where(ok, h, 1.0) / np.where(ok, lo, 1.0))
    ssq = np.where(ok, r * r, 0.0).sum(axis=1)
    with np.errstate(invalid="ignore", divide="ignore"):
        val = np.sqrt(ssq / (LN2_4 * n_present))
    return np.where(n_present >= coverage * len(bins), val, np.nan)


def rv_close_to_close(logc: np.ndarray, bins: np.ndarray,
                      coverage: float = RV_COVERAGE) -> np.ndarray:
    """Disclosed secondary variant: per-return-averaged close-to-close RV.

    sqrt( sum r^2 / n_returns ) over returns between CONSECUTIVE PRESENT closes
    of `bins` (= [b-W/5, b-2] inclusive, so 10 returns at W=60). A missing bin
    is bridged, not dropped — see AMBIGUITIES['close_to_close_bridges_gaps'] —
    and the >=80% rule applies to the return count, not the bin count.
    """
    n = logc.shape[0]
    if len(bins) < 2:
        return np.full(n, np.nan)
    prev = np.full(n, np.nan)
    ssq = np.zeros(n)
    nret = np.zeros(n, np.int64)
    for k in bins:
        c = logc[:, k]
        have = np.isfinite(c)
        with np.errstate(invalid="ignore"):
            d = c - prev
        add = have & np.isfinite(prev)
        ssq += np.where(add, d * d, 0.0)
        nret += add
        prev = np.where(have, c, prev)
    with np.errstate(invalid="ignore", divide="ignore"):
        val = np.sqrt(ssq / nret)
    return np.where(nret >= coverage * (len(bins) - 1), val, np.nan)


def parity_bins(bins: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """`(even, odd)` split by parity of the ABSOLUTE 5-minute bin index.

    For the registered grid this equals window-relative parity (every boundary
    bin index and every W/5 are even), so at W=60 the 11 gapped bins split
    6 even / 5 odd and the STRATIFIER (even) is the thicker side.
    """
    return bins[bins % 2 == 0], bins[bins % 2 == 1]


def rank_quintiles(vals: np.ndarray, elig: np.ndarray,
                   quint: np.ndarray, pctl=QUINT_PCTL) -> tuple[np.ndarray, int]:
    """Equal-count rank labels inside each 1.14f peer cell. -1 = no strata.

    The peer cell INCLUDES the $-vol quintile, so cuts are taken separately per
    `quint` value: session-local ranks mean a market-wide vol day cannot shift
    labels. `np.percentile(..., [20,40,60,80], linear)` then
    `searchsorted(side='left')`, so a value exactly on a breakpoint falls to the
    LOWER quintile. A cell with <25 rows or <5 distinct values contributes NO
    strata (never a partial-quintile stratum); its rows are counted, not labelled.
    """
    lab = np.full(len(vals), -1, np.int8)
    too_small = 0
    n_lab = len(pctl) + 1
    for q in range(N_QUINT):
        m = elig & (quint == q)
        n = int(m.sum())
        if n == 0:
            continue
        v = vals[m]
        if n < MIN_ASSIGN_ROWS or len(np.unique(v)) < MIN_ASSIGN_DISTINCT:
            too_small += n
            continue
        bp = np.percentile(v, pctl)
        lab[m] = np.minimum(np.searchsorted(bp, v, side="left"),
                            n_lab - 1).astype(np.int8)
    return lab, too_small


# --------------------------------------------------------------------------
# the session pass — 1.14f's pass, plus the RV panels
# --------------------------------------------------------------------------

def new_na_114g() -> dict[str, int]:
    """1.14f's NA ledger plus every counter this fork adds."""
    na = mi.new_na_counters()
    na.update(dict.fromkeys((
        "sessions_rv_valid_any", "cells_na_rv_ratio", "cells_na_rv_lag",
        "cells_na_rv_odd", "cells_na_rv_even", "cells_na_rv_c2c",
        "cells_na_rv_zg", "na_strata_cell_too_small",
        "na_strata_cell_too_small_lag", "na_strata_cell_too_small_even",
        "cells_pool_arm2a", "cells_pool_arm2b",
        "rows_2a_excluded_later_boundary", "rows_2b_excluded_later_boundary",
    ), 0))
    return na


COLS = ("sess", "name", "bi", "qvol", "pool", "fired", "excl", "prev",
        "norm", "raw", "rvq", "rvq_lag", "rvq_even", "rvq_c2c", "rvq_zg",
        "rvd", "rvr", "rvp", "orr", "err", "pool2a", "fired2a", "excl2a",
        "hit2a", "pool2b", "fired2b", "excl2b", "wcls", "wcnt", "fwret")


def run_sessions_114g(sessions, universe, dates, quint, med_pmax, cnt_pmax,
                      bars_dir, na, early_close=None):
    """Chronological single pass: 1.14f's row table plus the RV panels.

    Structurally 1.14f's pass — every component, window aggregate and firing
    decision comes from the IMPORTED machinery (`mi.window_stats`,
    `mi.components`, `mi.fire_p90`, `mi.Ring`, `mi.outcome_measures`), so the
    reproduce gate compares this pass against 1.14f's own numbers rather than
    against a re-derivation. What is added: six RV rings, the rv-quintile
    assignment (computed inside the session pass over the full name grid, never
    from an emitted row table), and the two placebo firings.

    Every `continue` pushes EVERY ring. A skipped push does not skip a session,
    it silently stretches "the trailing 20 sessions" across the gap.
    """
    n_names = len(universe)
    name_index = {n: i for i, n in enumerate(universe)}
    di = {d: t for t, d in enumerate(dates)}
    n_b, n_w = len(mi.BOUNDARY_BINS), len(mi.WINDOWS)
    n_h, n_f = len(mi.HORIZONS), len(mi.FEATS)
    wi_primary = mi.WINDOWS.index(mi.PRIMARY_W)

    vol_ring = mi.Ring((n_names, n_b, n_w), mi.VOL_LOOKBACK)
    out_ring = mi.Ring((n_names, n_b, n_h), mi.OUT_LOOKBACK)
    rv_rings = {k: mi.Ring((n_names, n_b, n_w), RV_LOOKBACK) for k in RV_KINDS}

    cols: dict[str, list[np.ndarray]] = {k: [] for k in COLS}

    for s_i, session in enumerate(sessions):
        ds = session.isoformat()
        vol_write = np.full((n_names, n_b, n_w), np.nan, np.float32)
        out_write = np.full((n_names, n_b, n_h), np.nan, np.float32)
        rv_write = {k: np.full((n_names, n_b, n_w), np.nan, np.float32)
                    for k in RV_KINDS}

        def push_all():
            vol_ring.push(vol_write)
            out_ring.push(out_write)
            for k, r in rv_rings.items():
                r.push(rv_write[k])

        if ds not in di:
            na["sessions_no_calendar_position"] += 1
            push_all()
            continue
        tape = mi.tape_bins(session, name_index, n_names, na)
        if tape is None:
            push_all()
            continue
        cls_b, short_b, bull_b, cnt_b, pmax_b, active = tape

        ws_cache: dict[tuple[int, int], dict[str, np.ndarray]] = {}
        for wi, W in enumerate(mi.WINDOWS):
            for bi, bb in enumerate(mi.BOUNDARY_BINS):
                span = mi.window_bins(bb, W)
                if span is None:
                    continue
                ws = mi.window_stats(cls_b, short_b, bull_b, cnt_b, pmax_b, *span)
                ws_cache[(bi, wi)] = ws
                vol_write[:, bi, wi] = np.where(active, ws["cls"], np.nan)

        # ---- READ the trailing state, then write it: this IS the t-1 rule ----
        vol_med, vol_cnt = vol_ring.median_count()
        out_med, out_cnt = out_ring.median_count()
        rv_med = {k: r.median_count() for k, r in rv_rings.items()}

        bpath = bars_dir / f"{ds}.npz"
        if not bpath.exists():
            na["sessions_no_bar_file"] += 1
            push_all()
            continue
        closes, last_bin, _n_sym, highs, lows = mi.load_bars_session(
            bpath, name_index, n_names, with_range=True)
        if last_bin <= 0:
            na["sessions_empty_bar_file"] += 1
            push_all()
            continue
        if last_bin < mi.RTH_BINS:
            na["sessions_early_close"] += 1
            if early_close is not None:
                early_close[ds] = last_bin
        meas, _signed, bars_ok = mi.outcome_measures(closes, last_bin)
        has_bars = np.isfinite(closes[:, :last_bin]).any(axis=1)
        na["namedays_with_bars"] += int(has_bars.sum())
        na["namedays_bars_below_coverage"] += int((has_bars & ~bars_ok).sum())

        # post-close stragglers are ABSENT for every RV measure, exactly as they
        # are for the outcome panel
        highs = highs.copy()
        lows = lows.copy()
        highs[:, last_bin:] = np.nan
        lows[:, last_bin:] = np.nan
        with np.errstate(invalid="ignore", divide="ignore"):
            logc = np.log(np.where(closes > 0, closes, np.nan))
        logc[:, last_bin:] = np.nan

        # ---- the RV panels, per (boundary, W) --------------------------------
        rv_val = {k: np.full((n_names, n_b, n_w), np.nan) for k in RV_KINDS}
        for wi, W in enumerate(mi.WINDOWS):
            for bi, bb in enumerate(mi.BOUNDARY_BINS):
                gap = gapped_bins(bb, W)
                if gap is None:
                    continue
                if len(gap):
                    rv_val["rv"][:, bi, wi] = rv_park(highs, lows, gap)
                    ev, od = parity_bins(gap)
                    rv_val["even"][:, bi, wi] = rv_park(highs, lows, ev)
                    rv_val["odd"][:, bi, wi] = rv_park(highs, lows, od)
                if W != mi.PRIMARY_W:
                    continue
                lag = lagged_bins(bb, W)
                if lag is not None:
                    rv_val["lag"][:, bi, wi] = rv_park(highs, lows, lag)
                rv_val["zg"][:, bi, wi] = rv_park(
                    highs, lows, np.arange(bb - W // mi.BAR_MIN, bb))
                if len(gap):
                    rv_val["c2c"][:, bi, wi] = rv_close_to_close(logc, gap)

        ratio = {}
        for k in RV_KINDS:
            med, cnt = rv_med[k]
            with np.errstate(invalid="ignore", divide="ignore"):
                ratio[k] = np.where((cnt >= MIN_RV_SESSIONS) & (med > 0),
                                    rv_val[k] / med, np.nan)
        na["sessions_rv_valid_any"] += int(np.isfinite(rv_val["rv"]).any())

        t = di[ds]
        q = quint[:, t]
        mp, cp = med_pmax[:, t], cnt_pmax[:, t]

        with np.errstate(invalid="ignore", divide="ignore"):
            norm = np.where((out_cnt >= mi.MIN_OUT_SESSIONS) & (out_med > 0)
                            & bars_ok[:, None, None], meas / out_med, np.nan)
        na["rows_na_outcome_measure"] += int(
            (~np.isfinite(meas) & bars_ok[:, None, None]).sum())
        na["rows_na_outcome_normalizer"] += int(
            (np.isfinite(meas) & ~np.isfinite(norm)).sum())
        na["rows_normalizer_below_5bp"] += int(
            (np.isfinite(norm) & (out_med < mi.THIN_NORMALIZER)).sum())
        raw = np.where(bars_ok[:, None, None] & np.isfinite(norm), meas, np.nan)

        # ---- features, firing, first-fire bookkeeping ------------------------
        pool_a = np.zeros((n_names, n_b, n_w, n_f), bool)
        fired = np.zeros((n_names, n_b, n_w, n_f), bool)
        prev = np.zeros((n_names, n_b, n_w, n_f), bool)
        ever = np.zeros((n_names, n_w, n_f), bool)
        wcls = np.full((n_names, n_b, n_w), np.nan)
        wcnt = np.full((n_names, n_b, n_w), np.nan)
        fwret = np.full((n_names, n_b, n_w), np.nan)
        rvq = {k: np.full((n_names, n_b, n_w), -1, np.int8) for k in RV_KINDS}
        rvd = np.full((n_names, n_b), -1, np.int8)     # decile re-cut, W=60 only
        pool2 = {a: np.zeros((n_names, n_b, n_w), bool) for a in ("2a", "2b")}
        fired2 = {a: np.zeros((n_names, n_b, n_w), bool) for a in ("2a", "2b")}
        prev2 = {a: np.zeros((n_names, n_b, n_w), bool) for a in ("2a", "2b")}
        hit2a = np.zeros((n_names, n_b, n_w), bool)
        ever2 = {a: np.zeros((n_names, n_w), bool) for a in ("2a", "2b")}

        for wi, W in enumerate(mi.WINDOWS):
            for bi, bb in enumerate(mi.BOUNDARY_BINS):
                ws = ws_cache.get((bi, wi))
                if ws is None:
                    continue
                comp = mi.components(ws, mp, cp, vol_med[:, bi, wi],
                                     vol_cnt[:, bi, wi])
                ok = ws["cnt"] >= mi.MIN_PRINTS
                wcls[:, bi, wi] = ws["cls"]
                wcnt[:, bi, wi] = ws["cnt"]
                na["cells_ineligible_prints"] += int((~ok & active).sum())
                na["cells_no_quintile"] += int((ok & active & (q < 0)).sum())
                lo_bin = mi.window_bins(bb, W)[0]
                base_bin = lo_bin - 1 if lo_bin >= 1 else 0
                with np.errstate(invalid="ignore"):
                    fwret[:, bi, wi] = np.abs(logc[:, bb - 1] - logc[:, base_bin])
                for fi, f in enumerate(mi.FEATS):
                    vals = comp[f]
                    pool = ok & np.isfinite(vals) & (q >= 0)
                    pool_a[:, bi, wi, fi] = pool
                    na[f"cells_na_{f}"] += int(
                        (ok & active & ~np.isfinite(vals)).sum())
                    hit = mi.fire_p90(vals, q, pool)
                    fired[:, bi, wi, fi] = hit & ~ever[:, wi, fi]
                    prev[:, bi, wi, fi] = ever[:, wi, fi]
                    ever[:, wi, fi] |= hit

                # ---- the rv-quintile assignment, per stratifier -------------
                base = ok & (q >= 0)
                for k in RV_KINDS:
                    if k in PRIMARY_ONLY_KINDS and wi != wi_primary:
                        continue
                    finite = np.isfinite(ratio[k][:, bi, wi])
                    na[f"cells_na_rv_{k}" if k != "rv" else "cells_na_rv_ratio"] \
                        += int((base & active & ~finite).sum())
                    lab, small = rank_quintiles(ratio[k][:, bi, wi],
                                                base & finite, q)
                    rvq[k][:, bi, wi] = lab
                    if k == "rv":
                        na["na_strata_cell_too_small"] += small
                        if wi == wi_primary:
                            # the decile diagnostic, cut over the SAME
                            # assignment population, never a row table
                            rvd[:, bi] = rank_quintiles(
                                ratio[k][:, bi, wi], base & finite, q,
                                pctl=DECILE_PCTL)[0]
                    elif k == "lag":
                        na["na_strata_cell_too_small_lag"] += small
                    elif k == "even":
                        na["na_strata_cell_too_small_even"] += small

                # ---- Arm 2a: rv_ratio through the IDENTICAL machinery -------
                p2a = base & np.isfinite(ratio["rv"][:, bi, wi])
                pool2["2a"][:, bi, wi] = p2a
                na["cells_pool_arm2a"] += int((p2a & active).sum())
                h2a = mi.fire_p90(ratio["rv"][:, bi, wi], q, p2a)
                hit2a[:, bi, wi] = h2a
                fired2["2a"][:, bi, wi] = h2a & ~ever2["2a"][:, wi]
                prev2["2a"][:, bi, wi] = ever2["2a"][:, wi]
                ever2["2a"][:, wi] |= h2a

                # ---- Arm 2b: the odd-bin ratio fires, the even-bin strats ---
                p2b = base & np.isfinite(ratio["odd"][:, bi, wi])
                pool2["2b"][:, bi, wi] = p2b
                na["cells_pool_arm2b"] += int((p2b & active).sum())
                h2b = mi.fire_p90(ratio["odd"][:, bi, wi], q, p2b)
                fired2["2b"][:, bi, wi] = h2b & ~ever2["2b"][:, wi]
                prev2["2b"][:, bi, wi] = ever2["2b"][:, wi]
                ever2["2b"][:, wi] |= h2b

        excl = ever[:, None, :, :] & ~fired
        na["rows_excluded_later_boundary"] += int((excl & pool_a).sum())
        excl2 = {a: ever2[a][:, None, :] & ~fired2[a] for a in ("2a", "2b")}
        for a in ("2a", "2b"):
            na[f"rows_{a}_excluded_later_boundary"] += int(
                (excl2[a] & pool2[a]).sum())

        out_write[:] = np.where(bars_ok[:, None, None], meas, np.nan)
        for k in RV_KINDS:
            rv_write[k][:] = rv_val[k]

        keep_rows = ((pool_a.any(axis=(2, 3)) | pool2["2a"].any(axis=2)
                      | pool2["2b"].any(axis=2))
                     & np.isfinite(norm).any(axis=2))
        ii, bb_i = np.nonzero(keep_rows)
        if len(ii):
            app = cols
            app["sess"].append(np.full(len(ii), s_i, np.int32))
            app["name"].append(ii.astype(np.int32))
            app["bi"].append(bb_i.astype(np.int8))
            app["qvol"].append(q[ii].astype(np.int8))
            app["pool"].append(pool_a[ii, bb_i])
            app["fired"].append(fired[ii, bb_i])
            app["excl"].append(excl[ii, bb_i])
            app["prev"].append(prev[ii, bb_i])
            app["norm"].append(norm[ii, bb_i].astype(np.float32))
            app["raw"].append(raw[ii, bb_i].astype(np.float32))
            app["rvq"].append(rvq["rv"][ii, bb_i])
            app["rvq_lag"].append(rvq["lag"][ii, bb_i])
            app["rvq_even"].append(rvq["even"][ii, bb_i])
            app["rvq_c2c"].append(rvq["c2c"][ii, bb_i])
            app["rvq_zg"].append(rvq["zg"][ii, bb_i])
            app["rvd"].append(rvd[ii, bb_i])
            app["rvr"].append(ratio["rv"][ii, bb_i].astype(np.float32))
            app["rvp"].append(rv_val["rv"][ii, bb_i].astype(np.float32))
            app["orr"].append(ratio["odd"][ii, bb_i].astype(np.float32))
            app["err"].append(ratio["even"][ii, bb_i].astype(np.float32))
            for a in ("2a", "2b"):
                app[f"pool{a}"].append(pool2[a][ii, bb_i])
                app[f"fired{a}"].append(fired2[a][ii, bb_i])
                app[f"excl{a}"].append(excl2[a][ii, bb_i])
            app["hit2a"].append(hit2a[ii, bb_i])
            app["wcls"].append(wcls[ii, bb_i].astype(np.float32))
            app["wcnt"].append(wcnt[ii, bb_i].astype(np.float32))
            app["fwret"].append(fwret[ii, bb_i].astype(np.float32))
            na["rows_emitted"] += len(ii)
        na["sessions_analysed"] += 1

        push_all()
        logger.info("%s rows=%d cum=%d", ds, len(ii), na["rows_emitted"])

    return {k: (np.concatenate(v) if v else np.array([]))
            for k, v in cols.items()}


# --------------------------------------------------------------------------
# the reproduce-1.14f gate — runs BEFORE anything is emitted
# --------------------------------------------------------------------------

def environment() -> dict:
    """Stamped into meta: 1.14f never recorded its own, so this run fixes it."""
    return {"numpy": np.__version__,
            "python": platform.python_version(),
            "python_build": " ".join(platform.python_build()),
            "platform": platform.platform()}


def primary_screen(table) -> list[dict]:
    """The four 1.14f primary cells recomputed from THIS run's row table.

    Shaped exactly like the locked JSON's `arms.all_train.primary` rows so the
    gate is a field-by-field comparison, not a re-derivation. `mi.cell_mask` is
    imported, so the fired/complement definition cannot drift.
    """
    wi = mi.WINDOWS.index(mi.PRIMARY_W)
    hi = mi.HORIZONS.index(mi.PRIMARY_H)
    out = []
    for fi, f in enumerate(mi.FEATS):
        keep = mi.cell_mask(table, wi, fi, hi) if len(table["sess"]) \
            else np.zeros(0, bool)
        fr = table["fired"][keep][:, wi, fi] if keep.any() else np.zeros(0, bool)
        v = table["norm"][keep][:, hi] if keep.any() else np.zeros(0)
        row = {"component": f, "W": mi.PRIMARY_W, "H": mi.PRIMARY_H,
               "n_fired": int(fr.sum()), "n_complement": int((~fr).sum())}
        row["median_fired"] = (round(float(np.median(v[fr])), 5)
                               if fr.any() else None)
        row["median_complement"] = (round(float(np.median(v[~fr])), 5)
                                    if (~fr).any() else None)
        row["diff"] = (None if row["median_fired"] is None
                       or row["median_complement"] is None
                       else round(row["median_fired"]
                                  - row["median_complement"], 5))
        out.append(row)
    return out


class ReproduceGateFailure(RuntimeError):
    """1.14f did not reproduce. The registration says ABORT, never loosen."""


def reproduce_114f(table, locked: dict) -> dict:
    """Gate: the recomputed screen must MATCH the locked 1.14f JSON.

    n_fired and n_complement integer-exact, medians and diffs to 5 decimals, at
    all four primary cells. On mismatch: raise, reporting the per-cell delta and
    the environment diff. Never proceed on a loosened tolerance — a screen that
    does not reproduce is not the screen the stars were found with, and every
    stratified number below inherits it.
    """
    want = {r["component"]: r for r in
            locked.get("arms", {}).get("all_train", {}).get("primary", [])}
    got = {r["component"]: r for r in primary_screen(table)}
    deltas, ok = [], True
    for f in mi.FEATS:
        w, g = want.get(f), got.get(f)
        if w is None:
            ok = False
            deltas.append({"component": f, "problem": "absent from locked JSON"})
            continue
        d = {"component": f}
        for key in ("n_fired", "n_complement"):
            d[key] = {"locked": w.get(key), "recomputed": g[key],
                      "match": w.get(key) == g[key]}
            ok &= bool(d[key]["match"])
        for key in ("median_fired", "median_complement", "diff"):
            lv, gv = w.get(key), g[key]
            match = (lv is None and gv is None) or (
                lv is not None and gv is not None and abs(lv - gv) < 5e-6)
            d[key] = {"locked": lv, "recomputed": gv, "match": bool(match),
                      "delta": (None if lv is None or gv is None
                                else round(gv - lv, 8))}
            ok &= bool(match)
        deltas.append(d)
    report = {"passed": bool(ok), "cells": deltas,
              "environment_this_run": environment(),
              "environment_locked_run": locked.get("meta", {}).get(
                  "environment", "NOT STAMPED — 1.14f recorded no environment; "
                                 "this run's stamp fixes it retroactively"),
              "tolerance": "n_fired/n_complement integer-exact; medians and "
                           "diff to 5 decimals"}
    if not ok:
        raise ReproduceGateFailure(json.dumps(report, indent=1))
    return report


# --------------------------------------------------------------------------
# bootstrap — the shared resampling, and the (optionally) vectorized path
# --------------------------------------------------------------------------

def boot_medians_vec(values, sess, n_sessions, wmat, chunk: int = 1024):
    """`mi.boot_medians`, with the per-draw Python loop turned inside out.

    Identical algorithm and identical arithmetic: sort once, bin into equal-count
    chunks of the SORTED order, get every draw's weighted chunk CDF with one
    matmul, then refine exactly inside the single chunk that straddles the
    median. The only change is that draws are grouped BY that chunk, so the
    refinement is one vector op per chunk instead of one per draw.

    `searchsorted(a, x, 'left')` on a monotone `a` is exactly `(a < x).sum()`,
    which is how the per-draw searchsorted becomes a matrix comparison. The
    weighted CDF is monotone in sorted position, so chunking cannot move the
    answer — it only narrows the search. Bit-identity is asserted, not assumed:
    see `prove_vectorized`.
    """
    n = len(values)
    if n == 0:
        return np.full(len(wmat), np.nan)
    order = np.argsort(values, kind="stable")
    v = values[order].astype(np.float64)
    s = sess[order].astype(np.int64)

    n_chunks = max(1, min(chunk, n))
    bounds = np.linspace(0, n, n_chunks + 1).astype(np.int64)
    grp = np.repeat(np.arange(n_chunks), np.diff(bounds))
    cnt = np.bincount(s * n_chunks + grp,
                      minlength=n_sessions * n_chunks
                      ).reshape(n_sessions, n_chunks).astype(np.float64)
    acc = np.cumsum(wmat @ cnt, axis=1)          # (n_boot, n_chunks)
    total = acc[:, -1].astype(np.int64)
    live = total > 0

    out = np.full(len(wmat), np.nan)
    acc_sum = np.zeros(len(wmat))
    for which in (0, 1):
        r = (total - 1) // 2 if which == 0 else total // 2
        g = (acc < (r + 1)[:, None]).sum(axis=1)          # straddling chunk
        for gg in np.unique(g[live]):
            d = np.flatnonzero(live & (g == gg))
            lo, hi = int(bounds[gg]), int(bounds[gg + 1])
            base = acc[d, gg - 1] if gg else 0.0
            c = np.cumsum(wmat[np.ix_(d, s[lo:hi])], axis=1)
            c = c + (base[:, None] if gg else 0.0)
            idx = (c < (r[d] + 1)[:, None]).sum(axis=1)
            acc_sum[d] += v[lo + idx]
    out[live] = acc_sum[live] / 2.0
    return out


def prove_vectorized(table, sessions, n_subset: int = 10,
                     n_boot: int | None = None,
                     max_arrays: int = 600) -> dict:
    """Prove `boot_medians_vec` bit-identical to `mi.boot_medians`, in-process.

    The registration allows the vectorized per-stratum bootstrap ONLY if the
    vectorized path is proven bit-identical on a 10-session smoke subset BEFORE
    the full run, so the proof runs here, on the REAL strata of the real primary
    cell, and sets the process flag the full run checks. Refusing the flag is
    the point: an unproven vectorizer must not be reachable.
    """
    global _VECTOR_PROVEN
    _VECTOR_PROVEN = False
    n_boot = N_BOOT if n_boot is None else n_boot
    sess_ids = np.unique(table["sess"])[:n_subset] if len(table["sess"]) \
        else np.zeros(0, np.int64)
    if len(sess_ids) < BLOCK:
        return {"proven": False,
                "reason": f"only {len(sess_ids)} sessions available; the smoke "
                          f"subset needs at least the block length ({BLOCK})"}
    keep_sess = np.isin(table["sess"], sess_ids)
    rows = {k: v[keep_sess] for k, v in table.items()}
    S = len(sess_ids)
    sess_map = np.full(int(table["sess"].max()) + 1, -1, np.int64)
    sess_map[sess_ids] = np.arange(S)
    wmat = mi.block_weights(S, n_boot, BLOCK, np.random.default_rng(SEED))

    wi = mi.WINDOWS.index(mi.PRIMARY_W)
    hi = mi.HORIZONS.index(mi.PRIMARY_H)
    compared = 0
    for f in COMPONENTS:
        fi = mi.FEATS.index(f)
        sel = select_cell(rows, sess_map, wi, fi, hi)
        if sel["vals"] is None:
            continue
        strat, fired, vals, sv = (sel["strat"], sel["fired"], sel["vals"],
                                  sel["sess"])
        for s in np.unique(strat):
            for side in (fired, ~fired):
                if compared >= max_arrays:
                    break
                m = side & (strat == s)
                if not m.any():
                    continue
                a = mi.boot_medians(vals[m], sv[m], S, wmat)
                b = boot_medians_vec(vals[m], sv[m], S, wmat)
                if not np.array_equal(a, b, equal_nan=True):
                    bad = int(np.flatnonzero(~((a == b) | (np.isnan(a)
                                                          & np.isnan(b))))[0])
                    return {"proven": False, "component": f,
                            "stratum": int(s), "first_bad_draw": bad,
                            "per_stratum": float(a[bad]),
                            "vectorized": float(b[bad])}
                compared += 1
    if not compared:
        return {"proven": False,
                "reason": "no populated stratum in the smoke subset"}
    _VECTOR_PROVEN = True
    return {"proven": True, "arrays_compared": compared,
            "sessions": int(S), "n_boot": int(n_boot), "block": BLOCK,
            "equality": "elementwise, NaN treated as equal — see "
                        "AMBIGUITIES['vectorizer_bit_identity']"}


def _bootfn(use_vector: bool):
    if not use_vector:
        return mi.boot_medians
    if not _VECTOR_PROVEN:
        raise RuntimeError(
            "the vectorized per-stratum bootstrap was requested but has NOT "
            "been proven bit-identical in this process — run prove_vectorized() "
            "first (the registration's implementation lock)")
    return boot_medians_vec


# --------------------------------------------------------------------------
# the stratified estimator
# --------------------------------------------------------------------------

def _arm_masks(rows, wi, fi, arm):
    if arm == "comp":
        return (rows["pool"][:, wi, fi], rows["fired"][:, wi, fi],
                rows["excl"][:, wi, fi])
    keys = {"2a": ("pool2a", "fired2a", "excl2a"),
            "2b": ("pool2b", "fired2b", "excl2b")}[arm]
    return tuple(rows[k][:, wi] for k in keys)


def _values(rows, wi, hi, value):
    if value == "norm":
        return rows["norm"][:, hi]
    if value == "raw":
        return rows["raw"][:, hi]
    if value == "raw_over_rv":
        with np.errstate(invalid="ignore", divide="ignore"):
            return rows["raw"][:, hi] / rows["rvp"][:, wi]
    raise ValueError(value)


def select_cell(rows, sess_map, wi, fi, hi, *, arm="comp", qcol="rvq",
                value="norm", coarse=False, restrict=None,
                contemporaneous_complement=False):
    """One analysis cell's rows: values, sessions, fired mask, stratum ids.

    The 1.14f cell (`pool & ~excl & finite outcome`, imported semantics) then
    the registered rv filter: rows with an NA stratifier are excluded from BOTH
    sides and counted. `coarse=True` gives the locked coarse baseline's strata
    (boundary x $-vol quintile) over the SAME row universe — NA composition can
    never move the denominator between the two.
    """
    if not len(rows["sess"]):
        return {"vals": None, "n_rows": 0, "n_na_stratifier": 0}
    pool, fired, excl = _arm_masks(rows, wi, fi, arm)
    v = _values(rows, wi, hi, value)
    keep = pool & ~excl & np.isfinite(v)
    if contemporaneous_complement:
        # decision-time population: names not yet fired AS OF b. The fired rows
        # are unchanged (a first firing is a decision-time event by definition).
        keep = pool & np.isfinite(v) & (fired | ~rows["prev"][:, wi, fi])
    if restrict is not None:
        keep = keep & restrict
    q = rows[qcol][:, wi]
    n_na = int((keep & (q < 0)).sum())
    keep = keep & (q >= 0)
    if not keep.any() or not fired[keep].any() or not (~fired[keep]).any():
        return {"vals": None, "n_rows": int(keep.sum()), "n_na_stratifier": n_na,
                "n_fired": int((keep & fired).sum()),
                "n_complement": int((keep & ~fired).sum())}
    coarse_id = rows["bi"][keep].astype(np.int64) * N_QUINT + rows["qvol"][keep]
    strat = coarse_id if coarse else coarse_id * N_QUINT + q[keep]
    return {"vals": v[keep].astype(np.float64),
            "sess": sess_map[rows["sess"][keep]],
            "fired": fired[keep], "strat": strat,
            "n_strata_space": N_COARSE if coarse else N_STRATA,
            "keep": keep, "n_na_stratifier": n_na, "n_rows": int(keep.sum())}


def _group_slices(strat, n_strata):
    """Per-stratum row indices from ONE argsort (300 boolean scans would cost
    minutes at 3M rows)."""
    order = np.argsort(strat, kind="stable")
    starts = np.searchsorted(strat[order], np.arange(n_strata + 1), "left")
    return order, starts


def stratified(vals, sess, fired, strat, n_strata, S, wmat, *, weight="fired",
               boot=True, use_vector=False, aux=None):
    """The registered estimand: sum_s w_s (median_fired,s - median_comp,s).

    w_s = fired-count share (ATT-on-alarms — the decision-relevant weighting);
    `weight="min"` gives the registered min(n_f, n_c)-weighted secondary.
    Occupancy floor: a stratum needs >=20 fired AND >=20 complement rows or it
    drops with its rows counted. ONE threshold governs the point estimate: >10%
    of the cell's fired rows in dropped strata => UNAVAILABLE, no re-cut
    fallback (a re-cut would be a new registration).

    A draw reweights SESSIONS and recomputes per-stratum weighted medians via
    the shared weight matrix. Within-draw one-sided-empty strata drop with
    weights renormalized — a DISTINCT mechanism from the point-estimate floor,
    reported as its own per-draw dropped-fired-mass distribution.
    """
    nf = np.bincount(strat[fired], minlength=n_strata)
    nc = np.bincount(strat[~fired], minlength=n_strata)
    inc = (nf >= OCC_MIN_FIRED) & (nc >= OCC_MIN_COMPLEMENT)
    total_f = int(nf.sum())
    dropped_f = int(nf[~inc].sum())
    share = (dropped_f / total_f) if total_f else None
    out = {
        "n_fired": int(fired.sum()), "n_complement": int((~fired).sum()),
        "n_strata_populated": int(((nf > 0) | (nc > 0)).sum()),
        "n_strata_included": int(inc.sum()),
        "n_strata_dropped_occupancy": int(((nf > 0) | (nc > 0)).sum()
                                          - inc.sum()),
        "dropped_fired_rows": dropped_f, "total_fired_rows": total_f,
        "dropped_fired_share": None if share is None else round(share, 5),
        "weighting": weight,
    }
    ids = np.flatnonzero(inc)
    if not len(ids):
        out["unavailable"] = "no stratum clears the >=20/>=20 occupancy floor"
        out["unavailable_reasons"] = [out["unavailable"]]
        return out
    # the two dropped-mass rules are DISTINCT mechanisms, so both reasons are
    # kept when both trip — overwriting one with the other would hide a rule
    reasons: list[str] = []
    if share is not None and share > DROPPED_MASS_MAX:
        reasons.append(
            f"{share:.1%} of fired rows sit in occupancy-dropped strata "
            f"(> {DROPPED_MASS_MAX:.0%}) — UNAVAILABLE, not null, not star; no "
            f"re-cut fallback is registered")

    w = (nf[ids] if weight == "fired" else np.minimum(nf[ids], nc[ids])
         ).astype(np.float64)
    w = w / w.sum()
    of, sf = _group_slices(strat[fired], n_strata)
    oc, sc = _group_slices(strat[~fired], n_strata)
    vf, ssf = vals[fired], sess[fired]
    vc, ssc = vals[~fired], sess[~fired]
    # stratum ids are built as ((boundary x $-vol quintile) x stratifier bin),
    # so the decode is arithmetic and works for the coarse (1 bin) and the
    # decile-grain (10 bins) re-cuts as well as the registered 5
    per_boundary = max(1, n_strata // len(mi.BOUNDARY_BINS))
    n_bins_strat = max(1, per_boundary // N_QUINT)
    per = []
    med_f = np.empty(len(ids))
    med_c = np.empty(len(ids))
    df_rows, dc_rows = [], []
    for j, s in enumerate(ids):
        rf = of[sf[s]:sf[s + 1]]
        rc = oc[sc[s]:sc[s + 1]]
        med_f[j] = np.median(vf[rf])
        med_c[j] = np.median(vc[rc])
        df_rows.append(rf)
        dc_rows.append(rc)
        si = int(s)
        row = {"stratum": si,
               "boundary": mi.BOUNDARY_LABELS[si // per_boundary],
               "vol_quintile": (si // n_bins_strat) % N_QUINT,
               "n_fired": int(nf[s]), "n_complement": int(nc[s]),
               "median_fired": round(float(med_f[j]), 5),
               "median_complement": round(float(med_c[j]), 5),
               "weight": round(float(w[j]), 6)}
        if n_bins_strat > 1:
            row["rv_quintile"] = si % n_bins_strat
        if aux is not None:
            row["aux_median_fired"] = round(float(np.median(aux[fired][rf])), 5)
            row["aux_median_complement"] = round(
                float(np.median(aux[~fired][rc])), 5)
        per.append(row)
    out["delta"] = round(float((w * (med_f - med_c)).sum()), 5)
    out["per_stratum"] = per
    out["_w"] = w
    out["_nf"] = nf[ids].astype(np.float64)
    if not boot:
        if reasons:
            out["unavailable"] = " AND ".join(reasons)
            out["unavailable_reasons"] = reasons
        return out

    bf = _bootfn(use_vector)
    draws_f = np.stack([bf(vf[r], ssf[r], S, wmat) for r in df_rows])
    draws_c = np.stack([bf(vc[r], ssc[r], S, wmat) for r in dc_rows])
    ok = np.isfinite(draws_f) & np.isfinite(draws_c)
    wcol = w[:, None] * ok
    den = wcol.sum(axis=0)
    diff = np.where(ok, draws_f - draws_c, 0.0)
    with np.errstate(invalid="ignore", divide="ignore"):
        draws = np.where(den > 0, (wcol * diff).sum(axis=0) / den, np.nan)
    per_draw_dropped = (out["_nf"][:, None] * ~ok).sum(axis=0) / out["_nf"].sum()
    lo, hi_ci = mi.ci(draws)
    p95 = float(np.percentile(per_draw_dropped, 95))
    out["ci"] = [None if lo is None else round(lo, 5),
                 None if hi_ci is None else round(hi_ci, 5)]
    out["star"] = mi.starred(lo, hi_ci)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        se = float(np.nanstd(draws, ddof=1))
    out["bootstrap_se"] = round(se, 5)
    out["min_detectable_effect"] = round(MDE_Z * se, 5)
    out["per_draw_dropped_fired_mass"] = {
        "mean": round(float(per_draw_dropped.mean()), 5),
        "p50": round(float(np.percentile(per_draw_dropped, 50)), 5),
        "p95": round(p95, 5),
        "max": round(float(per_draw_dropped.max()), 5)}
    out["_draws"] = draws
    if p95 > PER_DRAW_DROPPED_P95_MAX:
        reasons.append(
            f"per-draw dropped fired mass p95 = {p95:.1%} exceeds "
            f"{PER_DRAW_DROPPED_P95_MAX:.0%} — UNAVAILABLE (a DISTINCT rule "
            f"from the point-estimate occupancy floor)")
    if reasons:
        out["unavailable"] = " AND ".join(reasons)
        out["unavailable_reasons"] = reasons
    return out


def unstratified(vals, sess, fired, S, wmat):
    """Arm 2a's reported form: no strata at all (rv-stratifying a screen defined
    on rv_ratio is degenerate — its fired rows all sit in the top quintile)."""
    mf, mc = float(np.median(vals[fired])), float(np.median(vals[~fired]))
    df = mi.boot_medians(vals[fired], sess[fired], S, wmat)
    dc = mi.boot_medians(vals[~fired], sess[~fired], S, wmat)
    lo, hi_ci = mi.ci(df - dc)
    return {"median_fired": round(mf, 5), "median_complement": round(mc, 5),
            "delta": round(mf - mc, 5),
            "ci": [None if lo is None else round(lo, 5),
                   None if hi_ci is None else round(hi_ci, 5)],
            "star": mi.starred(lo, hi_ci),
            "n_fired": int(fired.sum()), "n_complement": int((~fired).sum()),
            "stratified": False, "_draws": df - dc}


# --------------------------------------------------------------------------
# calibration gate 1 — the permuted-label null
# --------------------------------------------------------------------------

def permute_within(cell_id, status, labels, rng):
    """Permute `labels` within each (cell_id x status) group.

    This is what preserves every stratum's fired and complement counts EXACTLY,
    so the gate certifies the estimator on the REAL run's occupancy geometry
    rather than on some other geometry a label reshuffle happened to produce.
    Two lexsorts: one gives the rows grouped in stable order, one gives them
    grouped in random order; assigning across the two is a within-group
    permutation.
    """
    key = cell_id.astype(np.int64) * 2 + status.astype(np.int64)
    n = len(key)
    order_orig = np.lexsort((np.arange(n), key))
    order_rand = np.lexsort((rng.random(n), key))
    out = np.empty(n, labels.dtype)
    out[order_rand] = labels[order_orig]
    return out


def permutation_gate(rows, sess_map, S, wmat, component) -> dict:
    """Gate 1: permuted rv-quintile labels must reproduce the COARSE functional.

    Labels are permuted within (assignment cell x that component's
    fired/complement status) over the Arm 1 analysis rows at (W=60, H=2h) — the
    same LOCKED row universe as the coarse baseline, so NA composition can never
    move the denominator. Pass iff the MAXIMUM over 10 permutations of
    |permuted stratified delta - same-functional coarse delta| <= 0.02 ABSOLUTE
    (the gate certifies the ESTIMATOR, not the component, so it does not scale
    with component bands). All 10 deviations reported.
    """
    wi = mi.WINDOWS.index(mi.PRIMARY_W)
    hi = mi.HORIZONS.index(mi.PRIMARY_H)
    fi = mi.FEATS.index(component)
    sel = select_cell(rows, sess_map, wi, fi, hi)
    if sel["vals"] is None:
        return {"component": component, "unavailable": "empty primary cell"}
    coarse = select_cell(rows, sess_map, wi, fi, hi, coarse=True)
    base = stratified(coarse["vals"], coarse["sess"], coarse["fired"],
                      coarse["strat"], N_COARSE, S, wmat, boot=False)
    if "delta" not in base:
        return {"component": component,
                "unavailable": "coarse baseline has no included stratum"}
    keep = sel["keep"]
    cell_id = (rows["sess"][keep].astype(np.int64) * len(mi.BOUNDARY_BINS)
               * N_QUINT + rows["bi"][keep].astype(np.int64) * N_QUINT
               + rows["qvol"][keep])
    lab = rows["rvq"][keep, wi]
    coarse_id = rows["bi"][keep].astype(np.int64) * N_QUINT + rows["qvol"][keep]
    devs = []
    for k in range(1, PERM_N + 1):
        rng = np.random.default_rng(SEED + k)
        plab = permute_within(cell_id, sel["fired"], lab, rng)
        pstrat = coarse_id * N_QUINT + plab
        pr = stratified(sel["vals"], sel["sess"], sel["fired"], pstrat,
                        N_STRATA, S, wmat, boot=False)
        devs.append({"seed": SEED + k,
                     "permuted_stratified_delta": pr.get("delta"),
                     "deviation": (None if pr.get("delta") is None else
                                   round(abs(pr["delta"] - base["delta"]), 5)),
                     "n_strata_included": pr["n_strata_included"]})
    vals = [d["deviation"] for d in devs if d["deviation"] is not None]
    worst = max(vals) if vals else None
    return {"component": component, "coarse_delta": base["delta"],
            "permutations": devs, "max_abs_deviation": worst,
            "tolerance": PERM_TOL,
            "passed": bool(worst is not None and worst <= PERM_TOL),
            "note": "labels permuted within (assignment cell x fired/complement "
                    "status), which preserves every stratum's fired and "
                    "complement counts exactly"}


# --------------------------------------------------------------------------
# criterion (d) — the paired benchmark test
# --------------------------------------------------------------------------

def band(component: str) -> float:
    """The registered negligible band: max(0.01, 0.25 x |pooled 1.14f delta|)."""
    return NEGLIGIBLE_BAND[component]


def coarse_available(coarse: dict, component: str) -> str | None:
    """Why (d) is UNAVAILABLE from the coarse side, or None if it is evaluable.

    Registered preconditions: the coarse delta's bootstrap CI clear of zero AND
    |coarse delta| >= the component's negligible band. Plus the registered
    extension in AMBIGUITIES['coarse_baseline_occupancy']: a coarse baseline
    failing the occupancy floor likewise makes (d) UNAVAILABLE.
    """
    if "delta" not in coarse:
        return "coarse baseline has no included stratum"
    if coarse.get("unavailable"):
        return f"coarse baseline UNAVAILABLE: {coarse['unavailable']}"
    if not coarse.get("star"):
        return "coarse baseline CI is not clear of zero"
    if abs(coarse["delta"]) < band(component):
        return (f"|coarse delta| {abs(coarse['delta']):.5f} is inside the "
                f"component's negligible band {band(component):.3f}")
    return None


def benchmark_test(comp_strat, comp_coarse, b2_strat, b2_coarse,
                   component) -> dict:
    """(d): does the component RETAIN more than the split-sample placebo does?

    Per draw d, R_x(d) = delta_strat,x(d) / delta_coarse,x(d) using the SAME draw
    row of the shared weight matrix for the component and for Arm 2b; statistic
    = R_comp(d) - R_2b(d); pass iff its 2.5th percentile > 0. Simulation under a
    pure-clustering null: components retain ~43-47% of their raw effect with
    ZERO flow information, the split-sample placebo ~57% — so "CI clear of zero"
    alone is NOT diagnostic, and this test is what survival means.
    """
    out = {"component": component}
    why = coarse_available(comp_coarse, component)
    if why:
        return {**out, "available": False,
                "unavailable": f"component side: {why}"}
    for label, arm in (("arm2b stratified", b2_strat),
                       ("arm2b coarse", b2_coarse)):
        if "delta" not in arm or arm.get("unavailable"):
            return {**out, "available": False,
                    "unavailable": f"{label} UNAVAILABLE: "
                                   f"{arm.get('unavailable', 'no strata')} — an "
                                   f"UNAVAILABLE Arm 2b makes (d), and "
                                   f"therefore survival, UNAVAILABLE for every "
                                   f"component"}
    if "_draws" not in comp_strat or "_draws" not in b2_strat:
        return {**out, "available": False, "unavailable": "no bootstrap draws"}

    cs, cc = comp_strat["_draws"], comp_coarse["_draws"]
    bs, bc = b2_strat["_draws"], b2_coarse["_draws"]
    good = (np.isfinite(cs) & np.isfinite(cc) & np.isfinite(bs)
            & np.isfinite(bc) & (np.abs(cc) >= COARSE_MIN_ABS)
            & (np.abs(bc) >= COARSE_MIN_ABS))
    dropped = float(1.0 - good.mean())
    out["dropped_draw_share"] = round(dropped, 5)
    out["dropped_draw_limit"] = D_DROP_MAX
    if dropped > D_DROP_MAX:
        return {**out, "available": False,
                "unavailable": f"{dropped:.1%} of draws undefined "
                               f"(> {D_DROP_MAX:.0%}) — (d) UNAVAILABLE, not "
                               f"passed, not failed"}
    with np.errstate(invalid="ignore", divide="ignore"):
        stat = cs[good] / cc[good] - bs[good] / bc[good]
    p025 = float(np.percentile(stat, 2.5))
    out.update({
        "available": True,
        "attenuation_ratio_component": round(
            comp_strat["delta"] / comp_coarse["delta"], 5),
        "attenuation_ratio_arm2b": round(b2_strat["delta"] / b2_coarse["delta"], 5),
        "paired_statistic_p2_5": round(p025, 5),
        "paired_statistic_median": round(float(np.median(stat)), 5),
        "passed": bool(p025 > 0),
        "n_draws_used": int(good.sum())})
    return out


# --------------------------------------------------------------------------
# balance / adequacy and reliability
# --------------------------------------------------------------------------

def primary_boundaries(wi: int, hi: int) -> list[int]:
    """Boundary indices an analysis cell can use: full window AND reachable
    horizon. At (W=60, H=2h) this is exactly the registration's 8."""
    W, H = mi.WINDOWS[wi], mi.HORIZONS[hi]
    return [bi for bi, bb in enumerate(mi.BOUNDARY_BINS)
            if mi.window_bins(bb, W) is not None
            and mi.horizon_end_bin(bb, H) is not None]


def balance_table(rows, sess_map, S, wmat, component, wi, hi) -> dict:
    """The stratification-adequacy gate (SURVIVAL AUTHORITY).

    Within-stratum median rv_ratio, fired vs complement, per included stratum.
    An rv-quintile with no included stratum at >=6 of the primary cell's 8
    boundaries is NOT-EVALUABLE (distinct from failing). >=4 evaluable quintiles
    required. Adequate iff the quintile gap is <10% of the unstratified gap in
    ALL evaluable quintiles when 4 are evaluable, or >=4 of 5 when all 5 are,
    AND in every quintile holding >=25% of the component's included fired mass
    (in practice the top quintile — the pile-up the matching exists to control).
    A component failing balance has NO survival authority: the matching failed,
    not the hypothesis.
    """
    fi = mi.FEATS.index(component)
    sel = select_cell(rows, sess_map, wi, fi, hi)
    if sel["vals"] is None:
        return {"component": component, "unavailable": "empty cell"}
    rvr = rows["rvr"][sel["keep"], wi].astype(np.float64)
    res = stratified(sel["vals"], sel["sess"], sel["fired"], sel["strat"],
                     N_STRATA, S, wmat, boot=False, aux=rvr)
    if "per_stratum" not in res:
        return {"component": component, "unavailable": res.get("unavailable")}
    inc_rows = np.isin(sel["strat"], [p["stratum"] for p in res["per_stratum"]])
    f_inc = sel["fired"] & inc_rows
    c_inc = (~sel["fired"]) & inc_rows
    unstrat_gap = abs(float(np.median(rvr[f_inc]) - np.median(rvr[c_inc])))
    limit = 0.10 * unstrat_gap
    n_bounds = len(primary_boundaries(wi, hi))
    need_bounds = int(np.ceil(0.75 * n_bounds)) if n_bounds != 8 else 6

    fired_total = sum(p["n_fired"] for p in res["per_stratum"])
    quints = []
    for qq in range(N_QUINT):
        mem = [p for p in res["per_stratum"] if p["rv_quintile"] == qq]
        bounds = {p["boundary"] for p in mem}
        wsum = sum(p["weight"] for p in mem)
        gap = (abs(sum(p["weight"] * (p["aux_median_fired"]
                                     - p["aux_median_complement"])
                       for p in mem) / wsum) if wsum > 0 else None)
        fired_share = (sum(p["n_fired"] for p in mem) / fired_total
                       if fired_total else 0.0)
        evaluable = bool(mem) and len(bounds) >= need_bounds
        quints.append({
            "rv_quintile": qq, "n_included_strata": len(mem),
            "boundaries_covered": len(bounds), "boundaries_needed": need_bounds,
            "evaluable": evaluable,
            "fired_mass_share": round(fired_share, 5),
            "gap": None if gap is None else round(gap, 5),
            "gap_within_limit": bool(gap is not None and gap < limit),
            "heavy": bool(fired_share >= 0.25)})
    ev = [x for x in quints if x["evaluable"]]
    n_ev = len(ev)
    passing = [x for x in ev if x["gap_within_limit"]]
    if n_ev < 4:
        verdict, adequate = "INADEQUATE", False
        reason = f"only {n_ev} evaluable rv-quintiles (<4)"
    else:
        need = n_ev if n_ev == 4 else 4
        heavy_ok = all(x["gap_within_limit"] for x in ev if x["heavy"])
        adequate = len(passing) >= need and heavy_ok
        verdict = "ADEQUATE" if adequate else "INADEQUATE"
        reason = (f"{len(passing)}/{n_ev} evaluable quintiles within 10% of the "
                  f"unstratified gap (need {need}); heavy-quintile gap ok="
                  f"{heavy_ok}")
    return {"component": component, "W": mi.WINDOWS[wi], "H": mi.HORIZONS[hi],
            "unstratified_rv_gap": round(unstrat_gap, 5),
            "limit_10pct": round(limit, 6),
            "n_primary_boundaries": n_bounds,
            "quintiles": quints, "n_evaluable": n_ev,
            "adequate": adequate, "verdict": verdict, "reason": reason,
            "fired_mass_by_rv_quintile_included": {
                str(qq): round(sum(p["n_fired"] for p in res["per_stratum"]
                                   if p["rv_quintile"] == qq)
                               / fired_total, 5) if fired_total else None
                for qq in range(N_QUINT)},
            "per_stratum": res["per_stratum"]}


def reliability(rows, wi, hi) -> dict:
    """Stratifier reliability: odd-bin vs even-bin ratio correlation per (b, W).

    Reported alongside the balance table. W=30 was DECLARED IN ADVANCE unusable
    for replication (5 gapped bins => 3 even / 2 odd), so this table is advance
    justification made checkable, not a runtime gate — except at W=120, where a
    correlation below 80% of the primary cell's makes (c)'s star
    reliability-caveated.
    """
    orr, err = rows["orr"][:, wi], rows["err"][:, wi]
    per = {}
    tot_n, tot_w = 0, 0.0
    for bi in primary_boundaries(wi, hi):
        m = (rows["bi"] == bi) & np.isfinite(orr) & np.isfinite(err)
        n = int(m.sum())
        if n < 3 or np.std(orr[m]) == 0 or np.std(err[m]) == 0:
            per[mi.BOUNDARY_LABELS[bi]] = {"n": n, "corr": None}
            continue
        c = float(np.corrcoef(orr[m], err[m])[0, 1])
        per[mi.BOUNDARY_LABELS[bi]] = {"n": n, "corr": round(c, 5)}
        tot_n += n
        tot_w += c * n
    return {"W": mi.WINDOWS[wi], "H": mi.HORIZONS[hi], "by_boundary": per,
            "n_rows": tot_n,
            "cell_correlation": round(tot_w / tot_n, 5) if tot_n else None}


# --------------------------------------------------------------------------
# the arm battery
# --------------------------------------------------------------------------

def _cell_label(wi, hi, extra=None):
    d = {"W": mi.WINDOWS[wi], "H": mi.HORIZONS[hi]}
    if extra:
        d.update(extra)
    return d


def analyse_114g(table, sessions, arm_sessions, *, use_vector=False,
                 full=True, gate=None, known=None, dates=None) -> dict:
    """The registered battery for one arm.

    `full=False` runs the registered join>=98 MIRROR: the three component
    primaries (stratified) plus Arm 2a (unstratified), nothing else.
    """
    keep_s = np.array([i for i, s in enumerate(sessions)
                       if s.isoformat() in arm_sessions], dtype=np.int64) \
        if arm_sessions is not None else np.arange(len(sessions))
    S = len(keep_s)
    if S < BLOCK or not len(table["sess"]):
        return {"n_sessions": int(S),
                "unavailable": f"fewer sessions ({S}) than the bootstrap block "
                               f"length ({BLOCK})" if S < BLOCK else "no rows"}
    sess_map = np.full(len(sessions), -1, np.int64)
    sess_map[keep_s] = np.arange(S)
    wmat = mi.block_weights(S, N_BOOT, BLOCK, np.random.default_rng(SEED))
    in_arm = sess_map[table["sess"]] >= 0
    rows = {k: v[in_arm] for k, v in table.items()}

    wi_p = mi.WINDOWS.index(mi.PRIMARY_W)
    hi_p = mi.HORIZONS.index(mi.PRIMARY_H)
    wi_rep = mi.WINDOWS.index(120)
    res: dict = {"n_sessions": int(S)}
    w_range = list(range(len(mi.WINDOWS))) if full else [wi_p]
    h_range = list(range(len(mi.HORIZONS))) if full else [hi_p]

    # ---- Arm 2a: the machinery placebo (control, outside the budget) --------
    arm2a = []
    for wi in w_range:
        for hi in h_range:
            sel = select_cell(rows, sess_map, wi, 0, hi, arm="2a", qcol="rvq")
            row = _cell_label(wi, hi, {"arm": "2a", "stratified": False})
            if sel["vals"] is None:
                row["unavailable"] = "empty pool"
            else:
                row.update(unstratified(sel["vals"], sel["sess"], sel["fired"],
                                        S, wmat))
            row["primary_cell"] = (wi == wi_p and hi == hi_p)
            arm2a.append(row)
    res["arm2a_machinery_placebo"] = arm2a
    res["arm2a_note"] = (
        "an equity-RV screen on the OPTIONS-ELIGIBLE pool (same universe-"
        "moments as the components, so magnitudes share a footing), NOT a "
        "market-wide equity-RV study. Its unstratified delta and any stratified "
        "component delta are NOT magnitude-comparable — the registration forbids "
        "demoting or promoting by comparing those two numbers.")
    a2a_primary = next((r for r in arm2a if r.get("primary_cell")), None)

    # ---- Arm 1: the contemporaneous panel (PRIMARY) -------------------------
    cells, coarse_cells = [], {}
    for f in COMPONENTS:
        fi = mi.FEATS.index(f)
        for wi in w_range:
            for hi in h_range:
                sel = select_cell(rows, sess_map, wi, fi, hi)
                row = _cell_label(wi, hi, {"component": f, "arm": "1"})
                row["primary_cell"] = (wi == wi_p and hi == hi_p)
                if sel["vals"] is None:
                    row["unavailable"] = "empty pool"
                    row["n_na_stratifier_rows"] = sel["n_na_stratifier"]
                    cells.append(row)
                    continue
                st = stratified(sel["vals"], sel["sess"], sel["fired"],
                                sel["strat"], N_STRATA, S, wmat,
                                use_vector=use_vector)
                row.update(st)
                row["n_na_stratifier_rows"] = sel["n_na_stratifier"]
                cells.append(row)
                if (wi, hi) in ((wi_p, hi_p), (wi_rep, hi_p)):
                    csel = select_cell(rows, sess_map, wi, fi, hi, coarse=True)
                    co = stratified(csel["vals"], csel["sess"], csel["fired"],
                                    csel["strat"], N_COARSE, S, wmat,
                                    use_vector=use_vector)
                    coarse_cells[(f, wi, hi)] = co
    res["arm1_contemporaneous"] = cells
    res["arm1_coarse_baseline"] = [
        {**_cell_label(wi, hi, {"component": f, "arm": "1-coarse"}), **v}
        for (f, wi, hi), v in coarse_cells.items()]

    if not full:
        res["mirror_note"] = ("join>=98 MIRROR: the three component primaries "
                              "(stratified) + Arm 2a (unstratified) only, with "
                              "all trailing rings and the rv-quintile "
                              "assignment built from every train session")
        return res

    # ---- Arm 1 secondary: min(n_f, n_c) weighting ---------------------------
    minw = []
    for f in COMPONENTS:
        fi = mi.FEATS.index(f)
        sel = select_cell(rows, sess_map, wi_p, fi, hi_p)
        row = _cell_label(wi_p, hi_p, {"component": f, "arm": "1-min-weighted"})
        if sel["vals"] is None:
            row["unavailable"] = "empty pool"
        else:
            row.update(stratified(sel["vals"], sel["sess"], sel["fired"],
                                  sel["strat"], N_STRATA, S, wmat,
                                  weight="min", use_vector=use_vector))
        minw.append(row)
    res["arm1_min_weighted_secondary"] = minw

    # ---- Arm 1 boundary-matched restriction + Arm 1b (the adjudicator) ------
    matched, arm1b = [], []
    for f in COMPONENTS:
        fi = mi.FEATS.index(f)
        for wi, hi in [(wi_p, h) for h in range(len(mi.HORIZONS))] \
                + [(wi_rep, hi_p)]:
            b0 = first_lagged_boundary(mi.WINDOWS[wi])
            restrict = rows["bi"] >= b0
            sel = select_cell(rows, sess_map, wi, fi, hi, restrict=restrict)
            row = _cell_label(wi, hi, {
                "component": f, "arm": "1-boundary-matched",
                "first_lagged_boundary": mi.BOUNDARY_LABELS[b0]
                if b0 < len(mi.BOUNDARY_LABELS) else None})
            if sel["vals"] is None:
                row["unavailable"] = "empty pool"
            else:
                row.update(stratified(sel["vals"], sel["sess"], sel["fired"],
                                      sel["strat"], N_STRATA, S, wmat,
                                      use_vector=use_vector))
            matched.append(row)
        for hi in range(len(mi.HORIZONS)):
            sel = select_cell(rows, sess_map, wi_p, fi, hi, qcol="rvq_lag")
            row = _cell_label(wi_p, hi, {"component": f, "arm": "1b-lagged",
                                         "survival_authority": False})
            if sel["vals"] is None:
                row["unavailable"] = "empty pool"
            else:
                row.update(stratified(sel["vals"], sel["sess"], sel["fired"],
                                      sel["strat"], N_STRATA, S, wmat,
                                      use_vector=use_vector))
            row["n_na_stratifier_rows"] = sel["n_na_stratifier"]
            arm1b.append(row)
    res["arm1_boundary_matched"] = matched
    res["arm1b_lagged"] = arm1b

    # ---- Arm 2b: the split-sample placebo (the survival benchmark) ----------
    arm2b, arm2b_coarse = {}, {}
    for wi in (wi_p, wi_rep):
        sel = select_cell(rows, sess_map, wi, 0, hi_p, arm="2b",
                          qcol="rvq_even")
        row = _cell_label(wi, hi_p, {"arm": "2b"})
        if sel["vals"] is None:
            row["unavailable"] = "empty pool"
            arm2b[wi] = row
            arm2b_coarse[wi] = {"unavailable": "empty pool"}
            continue
        row.update(stratified(sel["vals"], sel["sess"], sel["fired"],
                              sel["strat"], N_STRATA, S, wmat,
                              use_vector=use_vector))
        row["n_na_stratifier_rows"] = sel["n_na_stratifier"]
        arm2b[wi] = row
        csel = select_cell(rows, sess_map, wi, 0, hi_p, arm="2b",
                           qcol="rvq_even", coarse=True)
        arm2b_coarse[wi] = stratified(csel["vals"], csel["sess"], csel["fired"],
                                      csel["strat"], N_COARSE, S, wmat,
                                      use_vector=use_vector)
    res["arm2b_split_sample_placebo"] = [
        {**arm2b[wi], "coarse": arm2b_coarse[wi]} for wi in (wi_p, wi_rep)]

    # ---- calibration gates --------------------------------------------------
    perm = [permutation_gate(rows, sess_map, S, wmat, f) for f in COMPONENTS]
    gate1 = all(p.get("passed") for p in perm) and bool(perm)
    gate2 = bool(a2a_primary and a2a_primary.get("star")
                 and (a2a_primary.get("delta") or 0) > 0)
    res["calibration_gates"] = {
        "gate1_permuted_label_null": {"per_component": perm, "passed": gate1,
                                      "tolerance_absolute": PERM_TOL},
        "gate2_machinery_control": {
            "arm2a_primary": None if a2a_primary is None else
            {k: a2a_primary.get(k) for k in ("delta", "ci", "star")},
            "passed": gate2,
            "requirement": "Arm 2a must star POSITIVE; null or negative means "
                           "the machinery failed to detect known physics — "
                           "investigate the RV measure before reading anything"},
        "run_inconclusive": not (gate1 and gate2)}
    blocked = not (gate1 and gate2)

    # ---- balance / reliability ---------------------------------------------
    res["balance"] = [balance_table(rows, sess_map, S, wmat, f, wi_p, hi_p)
                      for f in COMPONENTS]
    res["stratifier_reliability"] = [reliability(rows, wi, hi_p)
                                     for wi in range(len(mi.WINDOWS))]
    prim_corr = next((r["cell_correlation"]
                      for r in res["stratifier_reliability"]
                      if r["W"] == mi.PRIMARY_W), None)
    rep_corr = next((r["cell_correlation"]
                     for r in res["stratifier_reliability"]
                     if r["W"] == 120), None)
    res["reliability_floor"] = {
        "primary_cell_correlation": prim_corr,
        "floor": None if prim_corr is None
        else round(RELIABILITY_FLOOR_FRAC * prim_corr, 5),
        "w120_correlation": rep_corr,
        "w120_below_floor": bool(prim_corr is not None and rep_corr is not None
                                 and rep_corr
                                 < RELIABILITY_FLOOR_FRAC * prim_corr),
        "note": "W=30 was DECLARED IN ADVANCE unusable for replication (5 "
                "gapped bins: 3 even / 2 odd); this table is that advance "
                "justification made checkable, not a runtime gate"}

    # ---- criterion (d) and the reading rule --------------------------------
    by_cell = {(r["component"], r["W"], r["H"]): r
               for r in cells if "component" in r}
    dtests = {}
    for f in COMPONENTS:
        for wi in (wi_p, wi_rep):
            cs = by_cell.get((f, mi.WINDOWS[wi], mi.PRIMARY_H), {})
            dtests[(f, wi)] = benchmark_test(
                cs, coarse_cells.get((f, wi, hi_p), {}), arm2b[wi],
                arm2b_coarse[wi], f)
    res["benchmark_test_d"] = [
        {**v, "W": mi.WINDOWS[wi], "H": mi.PRIMARY_H}
        for (f, wi), v in dtests.items()]

    balance_by = {b["component"]: b for b in res["balance"]}
    res["reading_rule"] = {}
    res["verdicts"] = {}
    # "Arm 2b's attenuation ratio >= every component's AND its own stratified CI
    # clear of zero => coarsening alone explains all residuals"
    r2b = [dtests[(f, wi_p)].get("attenuation_ratio_arm2b")
           for f in COMPONENTS if dtests[(f, wi_p)].get("available")]
    rcomp = [dtests[(f, wi_p)].get("attenuation_ratio_component")
             for f in COMPONENTS if dtests[(f, wi_p)].get("available")]
    coarsening_override = bool(
        r2b and rcomp and arm2b[wi_p].get("star")
        and all(r2b[0] >= c for c in rcomp))
    res["coarsening_override"] = {
        "triggered": coarsening_override,
        "arm2b_attenuation_ratio": r2b[0] if r2b else None,
        "component_attenuation_ratios": rcomp,
        "arm2b_stratified_star": bool(arm2b[wi_p].get("star")),
        "meaning": "coarsening alone explains all residuals => INCONCLUSIVE for "
                   "all science claims"}

    arm1b_by = {r["component"]: r for r in arm1b
                if r["W"] == mi.PRIMARY_W and r["H"] == mi.PRIMARY_H}
    for f in COMPONENTS:
        res["reading_rule"][f], res["verdicts"][f] = verdict_for(
            f, by_cell, dtests, balance_by.get(f, {}), arm2b[wi_p],
            res["reliability_floor"], blocked, coarsening_override,
            arm1b_by.get(f, {}))

    # ---- the secondary battery (reported, no survival authority) ------------
    res["secondary"] = secondary_battery(rows, sess_map, S, wmat, cells,
                                         res["balance"], use_vector)
    # "the registered mechanical descriptive ... is reported before any
    # non-mechanical reading" — so a negative-sign component's verdict carries it
    mech_by = {m["component"]: m for m in res["secondary"]
               ["mechanical_descriptive"]}
    for f in COMPONENTS:
        if REGISTERED_SIGN[f] < 0:
            res["verdicts"][f]["mechanical_descriptive_first"] = mech_by.get(f)
    if gate is not None and known is not None and dates is not None:
        res["earnings_split_of_stars"] = earnings_splits(
            rows, sessions, gate, dates, known, cells)
    return res


def verdict_for(component, by_cell, dtests, balance, arm2b_primary,
                rel_floor, blocked, coarsening_override, arm1b_primary=None):
    """Reading rules (a)-(d), then the SEPARATE science and product verdicts.

    Precedence, pinned: a failed calibration gate makes the run INCONCLUSIVE and
    nothing below is read; Arm 2b explaining every residual likewise. Then per
    component: a stratified star with the sign OPPOSITE to 1.14f is recorded as
    a finding of confounding-or-over-adjustment, never folded silently into "did
    not pass"; the PRODUCT verdict governs any ship decision; and the
    unqualified word SURVIVES is reserved for science-positive AND not
    product-demoted.
    """
    W_P, H_P = mi.PRIMARY_W, mi.PRIMARY_H
    prim = by_cell.get((component, W_P, H_P), {})
    sign = REGISTERED_SIGN[component]
    bnd = band(component)

    def same_sign(row):
        return bool(row.get("star") and row.get("delta") is not None
                    and np.sign(row["delta"]) == sign
                    and not row.get("unavailable"))

    a_ok = same_sign(prim)
    b_rows = [by_cell.get((component, W_P, h), {}) for h in mi.HORIZONS]
    b_n = sum(same_sign(r) for r in b_rows)
    rep = by_cell.get((component, 120, H_P), {})
    d_prim = dtests.get((component, mi.WINDOWS.index(W_P)), {})
    d_rep = dtests.get((component, mi.WINDOWS.index(120)), {})
    c_ok = same_sign(rep) and bool(d_rep.get("passed"))
    d_ok = bool(d_prim.get("passed"))
    d_unavailable = not d_prim.get("available", False)

    rule = {
        "a_primary_clear_of_zero_same_sign": a_ok,
        "a_registered_sign": sign,
        "a_unavailable": prim.get("unavailable"),
        "b_horizons_starred_same_sign": b_n, "b_pass": b_n >= 2,
        "c_w120_star": same_sign(rep),
        "c_w120_benchmark_passed": bool(d_rep.get("passed")),
        "c_pass": c_ok,
        "c_reliability_caveated": bool(rel_floor.get("w120_below_floor")
                                       and same_sign(rep)),
        "d_available": not d_unavailable, "d_pass": d_ok,
        "d_detail": d_prim.get("unavailable"),
        "all_four": bool(a_ok and b_n >= 2 and c_ok and d_ok),
    }

    v = {"read_blocked": bool(blocked)}
    star_opposite = bool(prim.get("star") and prim.get("delta") is not None
                         and np.sign(prim["delta"]) == -sign)
    v["sign_flip_vs_114f"] = star_opposite
    lo, hi = (prim.get("ci") or [None, None])
    inside_band = bool(lo is not None and hi is not None
                       and abs(lo) < bnd and abs(hi) < bnd)
    contains_zero = bool(lo is not None and hi is not None and lo <= 0 <= hi)
    v["negligible_band"] = bnd
    v["stratified_primary"] = {k: prim.get(k) for k in
                               ("delta", "ci", "star", "bootstrap_se",
                                "min_detectable_effect", "unavailable")}
    v["balance"] = balance.get("verdict", "UNAVAILABLE")

    if blocked:
        v["science"] = "INCONCLUSIVE — a calibration gate failed"
        v["product"] = "INCONCLUSIVE — a calibration gate failed"
        v["survives"] = False
        return rule, v
    if coarsening_override:
        v["science"] = ("INCONCLUSIVE — Arm 2b's attenuation ratio is at least "
                        "every component's and its own stratified CI is clear "
                        "of zero: coarsening alone explains all residuals")
    elif prim.get("unavailable"):
        v["science"] = f"UNAVAILABLE — {prim['unavailable']}"
    elif star_opposite:
        v["science"] = ("CONFOUNDING-OR-OVER-ADJUSTMENT — stratified star with "
                        "the sign OPPOSITE to the pooled 1.14f effect; recorded "
                        "as a finding, not as 'did not pass'")
    elif balance.get("verdict") == "INADEQUATE":
        v["science"] = ("NO SURVIVAL AUTHORITY — stratification INADEQUATE: the "
                        "matching failed, not the hypothesis")
    elif d_unavailable:
        v["science"] = f"UNAVAILABLE — criterion (d): {d_prim.get('unavailable')}"
    elif rule["all_four"]:
        v["science"] = ("RV-ORTHOGONAL ASSOCIATION — incremental to "
                        "contemporaneous equity RV as measured by [Parkinson, "
                        "gapped W-window]"
                        + (" (NEGATIVE discrimination — read the mechanical "
                           "descriptive first)" if sign < 0 else ""))
    else:
        v["science"] = "DID NOT PASS the four-part science test"

    if prim.get("unavailable"):
        v["product"] = f"UNAVAILABLE — {prim['unavailable']}"
    elif inside_band:
        v["product"] = ("PRODUCT-DEMOTED — no incremental value over a free "
                        "equity-RV monitor at this grain, for this screen "
                        "(regardless of mediation: RV is decision-time "
                        "observable)")
    elif contains_zero:
        v["product"] = ("UNDERPOWERED — CI contains zero but is not inside the "
                        "negligible band; not demoted")
    else:
        v["product"] = "NOT DEMOTED — stratified CI clear of the negligible band"
    v["survives"] = bool(v["science"].startswith("RV-ORTHOGONAL")
                         and not v["product"].startswith("PRODUCT-DEMOTED"))
    v["precedence"] = ("the PRODUCT verdict governs any perception/ship "
                       "decision; 'SURVIVES' unqualified = science-positive AND "
                       "not product-demoted")

    # "The flow ANGLE after a product demotion is adjudicated by Arm 1b on the
    # boundary-matched restriction" — and the licensed language differs by
    # outcome, so the record states which words are allowed.
    if v["product"].startswith("PRODUCT-DEMOTED") and not blocked:
        lag = arm1b_primary or {}
        lag_star = bool(lag.get("star") and not lag.get("unavailable"))
        if lag.get("delta") is None or lag.get("unavailable"):
            v["flow_angle_adjudication"] = (
                "UN-ADJUDICATED — Arm 1b is unavailable at the primary cell "
                f"({lag.get('unavailable', 'no lagged cell')}); neither "
                "'thermometer' nor 'costume' language is licensed")
        elif lag_star:
            v["flow_angle_adjudication"] = (
                "NOT RV-ORTHOGONAL AT THIS GRAIN — the lagged panel stars, so "
                "the angle stays alive for other constructions and 'costume' "
                "language is FORBIDDEN")
        else:
            v["flow_angle_adjudication"] = (
                "CLUSTERING STORY STRENGTHENS — the lagged panel is null too, "
                "so 'thermometer' language is licensed FOR THE ADJUDICATED "
                "SUBPOPULATION ONLY (b >= the first lagged-valid boundary); the "
                "early-boundary fired mass is UN-ADJUDICATED")
        v["arm1b_note"] = ("Arm 1b adjudicates ONLY the boundary-matched "
                           "restriction of Arm 1 and carries NO survival "
                           "authority")
    return rule, v


# --------------------------------------------------------------------------
# the secondary battery
# --------------------------------------------------------------------------

def secondary_battery(rows, sess_map, S, wmat, cells, balance, use_vector):
    """Reported, no survival authority. Every item the registration lists."""
    wi_p = mi.WINDOWS.index(mi.PRIMARY_W)
    hi_p = mi.HORIZONS.index(mi.PRIMARY_H)
    out: dict = {}

    # per-rv-quintile delta tables (fired-weighted mean of delta_s within it)
    quint_tables = []
    for b in balance:
        if "per_stratum" not in b:
            continue
        rowsq = []
        for qq in range(N_QUINT):
            mem = [p for p in b["per_stratum"] if p["rv_quintile"] == qq]
            wsum = sum(p["weight"] for p in mem)
            rowsq.append({
                "rv_quintile": qq, "n_strata": len(mem),
                "n_fired": sum(p["n_fired"] for p in mem),
                "delta": (round(sum(p["weight"] * (p["median_fired"]
                                                   - p["median_complement"])
                                    for p in mem) / wsum, 5)
                          if wsum > 0 else None)})
        quint_tables.append({"component": b["component"], "quintiles": rowsq})
    out["delta_by_rv_quintile"] = quint_tables

    # decile-grain re-cut: point estimate + occupancy only (see AMBIGUITIES)
    dec = []
    for f in COMPONENTS:
        fi = mi.FEATS.index(f)
        sel = select_cell(rows, sess_map, wi_p, fi, hi_p)
        row = {"component": f, "grain": "decile"}
        if sel["vals"] is None:
            row["unavailable"] = "empty pool"
            dec.append(row)
            continue
        keep = sel["keep"]
        lab = rows["rvd"][keep]
        bi_k, q_k = rows["bi"][keep], rows["qvol"][keep]
        ok = lab >= 0
        strat = (bi_k[ok].astype(np.int64) * N_QUINT + q_k[ok]) * 10 + lab[ok]
        st = stratified(sel["vals"][ok], sel["sess"][ok], sel["fired"][ok],
                        strat, N_COARSE * 10, S, wmat, boot=False)
        row.update({k: st.get(k) for k in
                    ("delta", "n_strata_included", "dropped_fired_share",
                     "unavailable")})
        row["note"] = ("point estimate + occupancy only, no interval — a "
                       "diagnostic about whether finer strata would have "
                       "changed a balance verdict")
        dec.append(row)
    out["decile_recut"] = dec

    # co-firing with Arm 2a, and the top-rv-quintile share of fired rows
    cof = []
    for f in COMPONENTS:
        fi = mi.FEATS.index(f)
        sel = select_cell(rows, sess_map, wi_p, fi, hi_p)
        if sel["vals"] is None:
            cof.append({"component": f, "unavailable": "empty pool"})
            continue
        keep, fr = sel["keep"], sel["fired"]
        nf = int(fr.sum())
        any_hit = rows["hit2a"][keep, wi_p][fr]
        first = rows["fired2a"][keep, wi_p][fr]
        top = rows["rvq"][keep, wi_p][fr] == N_QUINT - 1
        cof.append({"component": f, "n_fired": nf,
                    "co_fire_share_any_hit": round(float(any_hit.mean()), 5),
                    "co_fire_share_first_fire_only": round(float(first.mean()), 5),
                    "fired_share_top_rv_quintile": round(float(top.mean()), 5)})
    out["co_firing_and_top_quintile"] = cof

    # the contemporaneous-complement arm (decision-time population)
    contemp = []
    for f in COMPONENTS:
        fi = mi.FEATS.index(f)
        sel = select_cell(rows, sess_map, wi_p, fi, hi_p,
                          contemporaneous_complement=True)
        row = {"component": f, "arm": "contemporaneous-complement"}
        if sel["vals"] is None:
            row["unavailable"] = "empty pool"
        else:
            row.update(stratified(sel["vals"], sel["sess"], sel["fired"],
                                  sel["strat"], N_STRATA, S, wmat,
                                  use_vector=use_vector))
        contemp.append(row)
    out["contemporaneous_complement_arm"] = contemp

    # denominator-inflation diagnostics
    denom = []
    for f in COMPONENTS:
        fi = mi.FEATS.index(f)
        for value in ("raw", "raw_over_rv"):
            sel = select_cell(rows, sess_map, wi_p, fi, hi_p, value=value)
            row = {"component": f, "outcome": value}
            if sel["vals"] is None:
                row["unavailable"] = "empty pool"
            else:
                row.update(stratified(sel["vals"], sel["sess"], sel["fired"],
                                      sel["strat"], N_STRATA, S, wmat,
                                      use_vector=use_vector))
            denom.append(row)
    out["denominator_inflation"] = denom

    # the mechanical descriptive + the above-peer feature-return share
    mech = []
    for f in COMPONENTS:
        fi = mi.FEATS.index(f)
        sel = select_cell(rows, sess_map, wi_p, fi, hi_p)
        if sel["vals"] is None:
            mech.append({"component": f, "unavailable": "empty pool"})
            continue
        keep, fr = sel["keep"], sel["fired"]
        wcls = rows["wcls"][keep, wi_p]
        wcnt = rows["wcnt"][keep, wi_p]
        fw = rows["fwret"][keep, wi_p]
        peer = (rows["sess"][keep].astype(np.int64) * len(mi.BOUNDARY_BINS)
                * N_QUINT + rows["bi"][keep].astype(np.int64) * N_QUINT
                + rows["qvol"][keep])
        above = np.zeros(len(fw), bool)
        order, starts = _group_slices(peer, int(peer.max()) + 1)
        for p in range(len(starts) - 1):
            idx = order[starts[p]:starts[p + 1]]
            if not len(idx):
                continue
            v = fw[idx]
            fin = np.isfinite(v)
            if not fin.any():
                continue
            above[idx[fin]] = v[fin] > np.median(v[fin])
        mech.append({
            "component": f,
            "median_window_premium_fired": round(float(np.nanmedian(wcls[fr])), 2),
            "median_window_premium_complement": round(
                float(np.nanmedian(wcls[~fr])), 2),
            "median_window_prints_fired": round(float(np.nanmedian(wcnt[fr])), 2),
            "median_window_prints_complement": round(
                float(np.nanmedian(wcnt[~fr])), 2),
            "fired_share_above_peer_feature_window_abs_return": round(
                float(above[fr].mean()), 5)})
    out["mechanical_descriptive"] = mech

    # the two disclosed secondary RV-measure variants, primary cell only
    variants = []
    for qcol, label in (("rvq_c2c", "close-to-close RV"),
                        ("rvq_zg", "zero-gap window [b-W/5, b)")):
        for f in COMPONENTS:
            fi = mi.FEATS.index(f)
            sel = select_cell(rows, sess_map, wi_p, fi, hi_p, qcol=qcol)
            row = {"component": f, "stratifier_variant": label}
            if sel["vals"] is None:
                row["unavailable"] = "empty pool"
            else:
                row.update(stratified(sel["vals"], sel["sess"], sel["fired"],
                                      sel["strat"], N_STRATA, S, wmat,
                                      use_vector=use_vector))
            row["n_na_stratifier_rows"] = sel["n_na_stratifier"]
            variants.append(row)
    out["stratifier_variants"] = variants

    out["fired_mass_all_strata_by_rv_quintile"] = [
        {"component": f, "shares": _all_strata_shares(rows, sess_map, f)}
        for f in COMPONENTS]
    out["note"] = ("no direction tests: 1.14f returned all-null at this grain. "
                   "An RV-conditional RE-FIRED screen (p90 within peer x rv "
                   "cell) is explicitly OUT OF SCOPE — a new screen needs its "
                   "own registration.")
    return out


def _all_strata_shares(rows, sess_map, component):
    """Fired-mass distribution across rv-quintiles over ALL strata (the INCLUDED
    -only version rides with the balance table)."""
    wi = mi.WINDOWS.index(mi.PRIMARY_W)
    hi = mi.HORIZONS.index(mi.PRIMARY_H)
    sel = select_cell(rows, sess_map, wi, mi.FEATS.index(component), hi)
    if sel["vals"] is None:
        return None
    q = sel["strat"] % N_QUINT
    fr = sel["fired"]
    tot = int(fr.sum())
    return {str(qq): round(float(((q == qq) & fr).sum() / tot), 5)
            for qq in range(N_QUINT)} if tot else None


def earnings_splits(rows, sessions, gate, dates, known, cells):
    """1.14f's raw `split_cell` on every starred finding — an UNSTRATIFIED
    diagnostic, labelled as such. A finding that collapses to the
    earnings-adjacent split is the calendar wearing a flow costume."""
    g, kn = mi.gate_row_masks(rows, sessions, gate, dates, known)
    out = []
    for c in cells:
        if not c.get("star") or c.get("unavailable"):
            continue
        wi = mi.WINDOWS.index(c["W"])
        hi = mi.HORIZONS.index(c["H"])
        fi = mi.FEATS.index(c["component"])
        out.append({"component": c["component"], "W": c["W"], "H": c["H"],
                    "unstratified_diagnostic": True,
                    **mi.split_cell(rows, wi, fi, hi, g, kn)})
    return out


# --------------------------------------------------------------------------
# the forward artifact — fired row keys and their hash
# --------------------------------------------------------------------------

def fired_key_records(table, sessions, universe) -> np.ndarray:
    """(session, name, boundary, component, W) for every 1.14f fired row.

    A FORWARD artifact for 1.14h+, not provenance for 1.14f — the reproduce gate
    is the provenance. Canonical order so the hash is a function of the content.
    """
    dt = np.dtype([("session", "U10"), ("name", "U16"), ("boundary", "U5"),
                   ("component", "U24"), ("W", "i2")])
    if not len(table["sess"]):
        return np.zeros(0, dt)
    recs = []
    sess_arr = np.array([s.isoformat() for s in sessions])
    name_arr = np.array(universe)
    for wi, W in enumerate(mi.WINDOWS):
        for fi, f in enumerate(mi.FEATS):
            m = table["fired"][:, wi, fi] & table["pool"][:, wi, fi]
            if not m.any():
                continue
            r = np.zeros(int(m.sum()), dt)
            r["session"] = sess_arr[table["sess"][m]]
            r["name"] = name_arr[table["name"][m]]
            r["boundary"] = np.array(mi.BOUNDARY_LABELS)[table["bi"][m]]
            r["component"] = f
            r["W"] = W
            recs.append(r)
    if not recs:
        return np.zeros(0, dt)
    out = np.concatenate(recs)
    order = np.lexsort((out["boundary"], out["name"], out["W"],
                        out["component"], out["session"]))
    return out[order]


def write_fired_keys(recs: np.ndarray, path: Path) -> dict:
    path.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(recs.tobytes()).hexdigest()
    np.savez_compressed(path, keys=recs)
    return {"path": str(path), "n_rows": int(len(recs)),
            "sha256": digest,
            "hashed": "sha256 over the canonical record bytes (not the .npz "
                      "container, whose metadata is not deterministic)",
            "scope": "the four 1.14f components at all three W — see "
                     "AMBIGUITIES['fired_key_artifact_scope']"}


# --------------------------------------------------------------------------

def strip_private(obj):
    """Drop `_`-prefixed keys (bootstrap draw vectors, weight arrays) before the
    payload is serialised: they are megabytes of intermediate state, and the
    registration asks for tables, not draws."""
    if isinstance(obj, dict):
        return {k: strip_private(v) for k, v in obj.items()
                if not (isinstance(k, str) and k.startswith("_"))}
    if isinstance(obj, list):
        return [strip_private(v) for v in obj]
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        return float(obj)
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    return obj


def band_arithmetic_check(locked: dict) -> dict:
    """The registered bands, against `max(0.01, 0.25 x |pooled 1.14f delta|)`.

    The registration states the bands as literals (+-0.041 / +-0.029 / +-0.010)
    and those literals are LAW. This records what the arithmetic gives on the
    locked pooled effects so a reader can see the 3-decimal rounding rather than
    discover it.
    """
    want = {r["component"]: r for r in
            locked.get("arms", {}).get("all_train", {}).get("primary", [])}
    out = {}
    for f in COMPONENTS:
        d = (want.get(f) or {}).get("diff")
        computed = (None if d is None
                    else max(BAND_FLOOR, BAND_FRACTION * abs(d)))
        out[f] = {"registered_band": NEGLIGIBLE_BAND[f],
                  "pooled_114f_delta": d,
                  "computed_band": None if computed is None else round(computed, 6),
                  "registered_matches_computed_to_3dp": bool(
                      computed is not None
                      and abs(round(computed, 3) - NEGLIGIBLE_BAND[f]) < 1e-9)}
    return out


def build_inputs(corpus_db: Path, train_end: str,
                 limit_sessions: int | None = None) -> dict:
    """The run's inputs: universe, calendar panels, train session list, NA ledger.

    Factored out of `main` so the reproduce gate can be exercised on EXACTLY the
    inputs the registered run uses — a test that rebuilt them by hand would be
    testing its own copy of the setup.
    """
    na = new_na_114g()
    universe = mi.load_universe(corpus_db)
    dates, C, V, pmax = mi.load_daily(corpus_db, universe)
    con = sqlite3.connect(f"file:{corpus_db}?mode=ro", uri=True)
    all_sessions = sorted(date.fromisoformat(r[0]) for r in
                          con.execute("SELECT date FROM corpus_sessions"))
    con.close()
    na["sessions_on_disk"] = len(all_sessions)
    sessions = [s for s in all_sessions if s.isoformat() <= train_end]
    na["sessions_after_train_end"] = len(all_sessions) - len(sessions)
    if limit_sessions:
        sessions = sessions[:limit_sessions]
    na["sessions_train"] = len(sessions)
    return {"universe": universe, "dates": dates, "sessions": sessions,
            "all_sessions": all_sessions, "quint": _tier_quintiles(C, V),
            "na": na,
            "pmax_panels": mi.shift_one_session(*_roll_med60(pmax))}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--corpus-db", default=str(CORPUS_DB))
    p.add_argument("--bars-dir", default=str(BARS_DIR))
    p.add_argument("--earnings-db", default=str(EARN_DB))
    p.add_argument("--join-ledger", default=str(JOIN_LEDGER))
    p.add_argument("--locked-114f", default=str(LOCKED_114F))
    p.add_argument("--out", default=str(OUT))
    p.add_argument("--keys-out", default=str(OUT_KEYS))
    p.add_argument("--train-end", default=TRAIN_END,
                   help="analysis mask; sessions after this are never read")
    p.add_argument("--limit-sessions", type=int, default=None,
                   help="first N train sessions (smoke only)")
    p.add_argument("--vectorized", action="store_true",
                   help="use the vectorized per-stratum bootstrap. The proof "
                        "runs in-process first; without it the path refuses")
    p.add_argument("--smoke-verify", action="store_true",
                   help="run ONLY the vectorizer proof, then exit")
    p.add_argument("--smoke-sessions", type=int, default=10,
                   help="how many ROW-BEARING sessions the vectorizer proof "
                        "uses (the registered smoke subset is 10). Bound the "
                        "pass itself with --limit-sessions: the first ~20 "
                        "sessions emit no rows at all, since every trailing "
                        "ring needs >=10 valid sessions first")
    a = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    t0 = time.time()

    db, bars_dir = Path(a.corpus_db), Path(a.bars_dir)
    inp = build_inputs(db, a.train_end, a.limit_sessions)
    na = inp["na"]
    universe, dates, sessions = inp["universe"], inp["dates"], inp["sessions"]
    med_pmax, cnt_pmax = inp["pmax_panels"]

    logger.info("universe=%d sessions=%d (of %d on disk; train_end=%s)",
                len(universe), len(sessions), len(inp["all_sessions"]),
                a.train_end)
    early_close: dict[str, int] = {}
    table = run_sessions_114g(sessions, universe, dates, inp["quint"],
                              med_pmax, cnt_pmax, bars_dir, na, early_close)

    if a.smoke_verify:
        proof = prove_vectorized(table, sessions, n_subset=a.smoke_sessions)
        print(json.dumps(proof, indent=1))
        return 0 if proof.get("proven") else 1

    # ---- THE GATE: reproduce 1.14f before emitting anything -----------------
    locked_path = Path(a.locked_114f)
    if not locked_path.exists():
        logger.error("locked 1.14f JSON absent at %s — the reproduce gate "
                     "cannot run, so nothing is emitted", locked_path)
        return 2
    locked = json.loads(locked_path.read_text())
    try:
        gate_report = reproduce_114f(table, locked)
    except ReproduceGateFailure as exc:
        print("ABORT: the 1.14f screen did NOT reproduce. Per the "
              "registration's implementation lock, nothing is emitted and no "
              "tolerance is loosened.\n")
        print(str(exc))
        return 3
    logger.info("reproduce gate PASSED at all four 1.14f primary cells")

    proof = None
    if a.vectorized:
        proof = prove_vectorized(table, sessions, n_subset=a.smoke_sessions)
        logger.info("vectorizer proof: %s", proof)
        if not proof.get("proven"):
            logger.error("the vectorized bootstrap was NOT proven "
                         "bit-identical — refusing it and using the "
                         "per-stratum path")

    gate_e, known = mi.load_gate(Path(a.earnings_db), universe, dates)
    na["names_no_edgar_history"] = int((~known).sum())
    subset = mi.load_join_subset(Path(a.join_ledger))

    use_vector = bool(a.vectorized and _VECTOR_PROVEN)
    arms = {"all_train": analyse_114g(
        table, sessions, None, use_vector=use_vector, full=True,
        gate=gate_e, known=known, dates=dates)}
    if subset is None:
        logger.warning("no join_ledger.json — the join>=98 mirror is disabled")
    else:
        arms["join_ge_98"] = analyse_114g(table, sessions, subset,
                                          use_vector=use_vector, full=False)

    keys = write_fired_keys(fired_key_records(table, sessions, universe),
                            Path(a.keys_out))
    payload = {
        "meta": {
            "registered": "2026-07-30", "roadmap": "FLOW_ROADMAP.md 1.14g v3",
            "not_a_backtest": "distributions only — no entry/exit/P&L/hit-rate",
            "seed": SEED, "n_boot": N_BOOT,
            "bootstrap": f"circular moving-block over SESSIONS, block={BLOCK}, "
                         f"one shared resampling per arm",
            "train_end": a.train_end,
            "primary_cell": {"W": mi.PRIMARY_W, "H": mi.PRIMARY_H},
            "multiplicity_budget": list(COMPONENTS),
            "budget_note": "the 3 component primaries at (W=60, H=2h), Arm 1 — "
                           "~0.15 expected false at 95%. Arms 1b/2a/2b and the "
                           "calibration gates are controls/adjudicators OUTSIDE "
                           "the budget",
            "stratifier": "rv_park = Parkinson RV over the one-bar-gapped "
                          "window [b-W/5, b-1), per-bin averaged, own-"
                          "normalized by the name's own trailing-20 (b, W) "
                          "median (>=10 valid sessions, median > 0)",
            "strata": "POOLED ACROSS SESSIONS: boundary-of-day x $-vol quintile "
                      "x rv-quintile; session is absorbed by the block "
                      "bootstrap, never by a stratum",
            "occupancy_floor": {"min_fired": OCC_MIN_FIRED,
                                "min_complement": OCC_MIN_COMPLEMENT,
                                "dropped_mass_max": DROPPED_MASS_MAX,
                                "per_draw_dropped_p95_max":
                                    PER_DRAW_DROPPED_P95_MAX},
            "negligible_bands": NEGLIGIBLE_BAND,
            "band_arithmetic": band_arithmetic_check(locked),
            "pooled_114f_effects": POOLED_114F,
            "registered_signs": REGISTERED_SIGN,
            "reproduce_gate": gate_report,
            "environment": environment(),
            "vectorized_bootstrap": {"requested": bool(a.vectorized),
                                     "used": use_vector, "proof": proof},
            "fired_row_keys": keys,
            "universe_size": len(universe),
            "sessions_analysed": na["sessions_analysed"],
            "expectations_recorded_in_advance": EXPECTATIONS,
            "simulation_prior": SIMULATION_PRIOR,
            "disclosure": DISCLOSURE,
            "wall_seconds": None,        # filled below
            "ambiguities": AMBIGUITIES,
        },
        "arms": arms,
        "early_close_sessions": early_close,
        "na_accounting": na,
    }
    payload["meta"]["wall_seconds"] = round(time.time() - t0, 1)
    outp = Path(a.out)
    outp.parent.mkdir(parents=True, exist_ok=True)
    outp.write_text(json.dumps(strip_private(payload), indent=1))
    _print_tables(payload)
    print(f"\nwrote {outp}  ({payload['meta']['wall_seconds']}s)")
    return 0


def _print_tables(payload: dict) -> None:
    m = payload["meta"]
    print(f"\n== 1.14g the vol-clustering fork — {m['sessions_analysed']} "
          f"sessions, {m['universe_size']} names, train_end={m['train_end']} ==")
    print("NOT a backtest: stratified medians of normalized moves, fired vs "
          "vol-matched complement.")
    print(f"reproduce gate: {'PASSED' if m['reproduce_gate']['passed'] else 'FAILED'}"
          f"   env: numpy {m['environment']['numpy']} / python "
          f"{m['environment']['python']}")
    for arm, res in payload["arms"].items():
        print(f"\n---- arm: {arm} ({res.get('n_sessions', 0)} sessions) ----")
        if "arm1_contemporaneous" not in res:
            print(f"  unavailable: {res.get('unavailable')}")
            continue
        cg = res.get("calibration_gates")
        if cg:
            print(f"  calibration gate 1 (permuted null): "
                  f"{cg['gate1_permuted_label_null']['passed']}   "
                  f"gate 2 (Arm 2a positive): "
                  f"{cg['gate2_machinery_control']['passed']}   "
                  f"=> run INCONCLUSIVE: {cg['run_inconclusive']}")
        print("\n  == Arm 1 stratified PRIMARY (W=60, H=2h) ==")
        for r in res["arm1_contemporaneous"]:
            if not r.get("primary_cell"):
                continue
            print(f"    {r['component']:<20} delta={r.get('delta')} "
                  f"ci={r.get('ci')}{' *' if r.get('star') else ''} "
                  f"n_f={r.get('n_fired')} n_c={r.get('n_complement')} "
                  f"strata={r.get('n_strata_included')} "
                  f"dropped_mass={r.get('dropped_fired_share')} "
                  f"{r.get('unavailable') or ''}")
        for f, v in res.get("verdicts", {}).items():
            print(f"\n    {f}")
            print(f"      science : {v['science']}")
            print(f"      product : {v['product']}")
            print(f"      balance : {v['balance']}   survives={v['survives']}")
        print("\n  == criterion (d): retention vs the split-sample placebo ==")
        for d in res.get("benchmark_test_d", []):
            print(f"    {d['component']:<20} W={d['W']:>3} "
                  f"available={d.get('available')} passed={d.get('passed')} "
                  f"R_comp={d.get('attenuation_ratio_component')} "
                  f"R_2b={d.get('attenuation_ratio_arm2b')} "
                  f"{d.get('unavailable') or ''}")
    print("\n== NA accounting (nothing is silently dropped) ==")
    for k, v in payload["na_accounting"].items():
        print(f"  {k:<38} {v:,}")


if __name__ == "__main__":
    raise SystemExit(main())
