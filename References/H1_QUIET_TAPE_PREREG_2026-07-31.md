# H1 pre-registration — opening-classified flow on a quiet tape

*⚠️ DRAFT v2 — NOT LAW until its gauntlet converges (round 1: 15 unique blockers — 10
methodology + 5 record, 2 shared — all resolved below; the two design catches, an
RV-confounded baseline and a fair-trial attribution hole, are fixed by an RV-MATCHED
primary contrast). This is the registered follow-up hypothesis H1 of
`FLOW_1_14F_FINDINGS_REGISTRATION.md` §R.4 (LAW 2026-07-31), written BEFORE any H1
statistic exists. Together with the Addendum G scoring-run grade it constitutes the
registered fair trial of the Databento `tcbbo` subscription — and per R.4's law: **the
subscription consequence is NON-ADMISSIBLE in this measurement** (no coordinate, cell,
reading rule, or re-run may be chosen, widened, or repeated because the subscription
outcome depends on it; a null on the first registered run is a complete discharge;
first-run-governs). Orchestrator, 2026-07-31.*

**Mechanism sentence (per the 2026-07-30 standing rule):** a trader with genuine private
information prefers to size BEFORE the move, on a quiet tape (better prices, lower
impact), and prefers OPENING new positions to closing old ones; the counterparty is a
market-maker quoting a calm book who hedges mechanically, not a crowd chasing a move.
The persistence half: opening-vs-closing cannot be observed live on standard feeds — it
requires next-day OI reconciliation — so the niche is structurally under-exploited at
retail-relevant capacity.

**Instrument ceiling (roadmap §2.3; "blind to which party opened" is §2.4's registered
honesty note):** `f_open` is a T+1 DAILY AGGREGATE — ΔOI reconciled against the day's
prints, published pre-open for the prior session (06:30–08:44 ET, ~45 minutes before this
design's entry — observable pre-entry, but this is registered as a RESEARCH measurement
on the proxy, not a live signal). A null attributable to the instrument is recorded as
instrument-limited, not as evidence against the mechanism — and still discharges the
fair trial (R.4). `f_open` is used CROSS-SECTIONALLY ONLY (its >0.5 level is an OI-decay
artifact; never thresholded on level).

---

## H1.1 The registered event instrument (ONE population, ONE convention)

Pipeline: the obligation-3 fenced equity pipeline — v7 fence (`SPREAD_MAX_FRAC = 0.50` +
quote validity), package prints masked per F.1, the frozen `root_classification.json`
equity universe, identities per G.2's two directions (flow reads date-scoped; bars/price
reads at the terminal symbol), classified premium operative, ≥15-of-20 presence, $250k
classified floor on the EVENT day only. Cache: `data/order_grain_null_v7_equity/`.

**The percentile cross-section for BOTH conditions — one population, stated once:**
identities in the fenced equity universe on session `d` that (i) clear the $250k
classified floor on `d`, (ii) appear in `oi_reconcile_rollup` with **≥ 1,000
identity-aggregated contracts** on `d` (§2.3/§2.4's registered volume floor — below it
`f_open` is denominator noise; f_open re-keyed onto identities date-scoped, volume and
volume-weighted f_open summed per identity BEFORE ranking, as at obligation 3b), and
(iii) carry a valid `rv_park` (below). A floor-clearing name-day failing (ii) or (iii)
is **INELIGIBLE and counted** (measured at drafting: 10–18% of floor-clearing name-days
per judgment date lack an OI match) — it enters NEITHER arm. **Percentile convention:
obligation 3b's ordinal rank, `100·rank/(n−1)`, `argsort` ties to the lower rank; cuts
applied as ≥ 80.0 and ≤ 33.0.** This population deliberately differs from `_f_open_pct`'s
raw OI cross-section (which includes non-equity roots) and from the census population
obligation 3b's 90.1% coverage was measured on — disclosed, registered here.

A name-day is an **H1 event** when, within that cross-section:
1. **Opening-heavy:** `f_open_pct(d)` ≥ the 80th percentile.
2. **Quiet tape:** `rv_park(d)` ≤ the 33rd percentile.

**`rv_park(d)` — a NEW same-session coordinate registered HERE** (it reuses 1.14g's
FORMULA, not its stratifier: 1.14g's quantity is a trailing gapped intraday window and
its stratifier is the own-normalized `rv_ratio`; H1 uses the RAW same-session level
cross-sectionally — deliberately, and with the consequence disclosed: a raw-level rank
selects calm NAMES as much as calm DAYS; the RV-matched contrast below makes that a
population scope, not a bias): over ALL RTH 5-min bins of session `d` from
`data/equity_bars_5min/` (78 bins, 13:30–19:55 UTC), read at the terminal symbol,
`rv_park = sqrt( Σ ln(h/l)² / (4·ln2 · n_present) )`; a bin with `h == l` is present and
contributes 0; **≥ 80% of the session's bins present, else NA ⇒ ineligible, counted**
(1.14g's absence conditions verbatim).

The event's **direction** = the sign of the day's classified call/put premium tilt (base
prereg 68-72's `dir`, RAW); exact-zero tilt ⇒ the event enters the magnitude arm only,
counted.

## H1.2 Outcome, metric, and the registered contrasts

**Metric: Addendum G §G.2's, verbatim** — `alpha = [(close_{d+10}/open_{d+1} − 1) − (SPY
same)] × open_{d+1} / ATR14`, G.2's pinned ATR14 (14 TRs through `d`), entry at next
open, 10td horizon of record; grid **3/10/21/45 per G.3, with G.3's per-column
truncated-at-run-date disclosure inherited**; the FOUR-case terminal-value rule and the
benchmark blocking rule applied identically to events and every baseline.

- **PRIMARY (kill authority): the RV-MATCHED DIRECTIONAL contrast.** Events (conditions
  1+2) minus the **RV-matched baseline**: name-days of the SAME cross-section with
  `rv_park ≤ p33` that FAIL condition 1 (not opening-heavy), each signed by its own tilt
  sign (zero-tilt excluded and counted). Both arms are calm-tape by construction — the
  denominator (ATR14) and day-`d` move magnitude are held comparable, and the ONLY
  differencing coordinate is `f_open`, the ingredient the subscription uniquely
  provides. Estimand: difference of equal-weighted per-event-date means (G.4's shape).
  One-sided, registered-positive. **An ALIVE here is attributable to the paid instrument
  or it is not ALIVE — the fair-trial attribution hole is closed by construction.**
- **SECONDARY (reported, labelled, NO kill authority):** (a) the joint contrast (events
  vs fails-1-or-2) — the v1 primary, demoted for the reasons in the header; (b) the
  magnitude (|alpha|) version of the primary contrast, whose baseline uses the same
  RV-matched rule with zero-tilt name-days INCLUDED (they carry magnitude; pinned here);
  (c) an ex-monthly-expiry leg of the primary (2026-06-18 and 07-17 sit in the window at
  the lowest measured OI coverage, 84.7%/82.0% — reported separately, never a rescue).
- **Tilt-sign permutation placebo (registered diagnostic, seed 20260819):** the primary's
  per-date difference recomputed 1,000 times with event tilt signs permuted within date —
  a genuine directional null (the order-sign shuffle cannot reach this design; see
  H1.5). Reported beside the verdict; changes no verdict.

**Tier pooling — a NAMED AMENDMENT, registered here:** G.9(a)'s pooling prohibition
governs firing-rate grading against the per-tier null table; H1 grades an
ATR-normalized alpha contrast where the noise floor enters through the metric's own
denominator, and grades POOLED across liquidity tiers. The per-arm tier composition
(and per-arm mean ATR14/price, per-date net signed share) is recorded in the pre-run
record (H1.6 #3) so the composition claim is checkable, not assumed.

## H1.3 Windows, ordering, and the touch rule

- **Training window** (calibration only): 2025-07-28 → 2026-03-31 (G.4's dated split).
- **Judgment window: 2026-06-01 → 2026-07-24 MINUS 2026-07-23 (37 dates, G.2's cell
  verbatim).** Known instrument fact, disposed NOW: `oi_reconcile.db` ends 2026-07-23,
  so 2026-07-24 currently has ZERO f_open coverage. **Registered disposition: H1.6 #2
  extends the reconcile with the 2026-07-27 OPRA statistics file (pure data acquisition,
  §2.3's own machinery, pulled while the subscription is live); if the file is
  unobtainable, 07-24 is an instrument-dead date — dropped and counted, window = 36
  dates, disclosed on the face of the verdict.**
- **Ordering and the touch rule, stated correctly:** this registration must be LAW
  before the close of 2026-08-07 — the earliest date any registered scoring run may
  execute on this split (G.2/G.10's date bound) — hence before the Addendum G GRADE of
  the split exists. G.10's disclosed prior reads (2.4's June-close reads, 1.14f's
  outcome-store fetch) stand and are not reopened; the governing rule is behavioral,
  not calendrical: **an H1 outcome statistic on the split before this document is law
  voids the run (R.5's undertaking, adopted here).** The H1 scoring run executes
  STRICTLY AFTER the Addendum G verdict is recorded, reads only the frozen artifacts
  (fenced cache, classification snapshot, oi_reconcile, `data/equity_bars_5min/`), and
  computes nothing on any window other than those registered here.
- **Multiplicity, disclosed:** G.2 and H1 are TWO one-sided 99% tests touching the same
  37-date split (different cohorts, same metric family). No correction is applied —
  each is its own registered hypothesis — and the registered consequence is: the two
  verdicts are reported separately and may NEVER be combined into a single "flow works"
  claim; the input-side overlap between H1's event cohort and G's fired cohort is
  recorded in the pre-run record.

## H1.4 Inference — G.4's machinery by reference, one seed

Collapse to the per-event-date difference series (a date enters when it carries ≥ 1
gradeable EVENT and ≥ 1 gradeable BASELINE observation); **Newey-West HAC, 9 lags,
SPY-session gap, G.4's exact written formula** (t>s pairs, 1/G, zero-pair gaps = 0,
fallback `Var(x, ddof=1)` on the series in hand, denominator `sqrt(LRV/G)`). **Cutoff:
size-calibrated exactly as G.4** — the training-window per-date difference series at
THESE coordinates, demeaned, circular MBB (block 10, ⌈G/10⌉ draws truncated to G),
100,000 replications, **seed 20260818**, identical in-replicate variance rule, cutoff =
the 99th percentile of signed replicate t; **the MDE is δ-shifted on the SAME replicates
per G.4 (no second seed)**, 0.01-ATR grid, ≥ 80% power, > 1.00 ATR ⇒ POWER-LIMITED.
Calibration dates = training dates carrying ≥ 1 gradeable event AND ≥ 1 gradeable
baseline observation; H1's short-lookback rule is its own: the 15-of-20 presence rule
and ATR14's case-(iii) route govern early dates (no trailing-quintile lookback exists in
this design). Computed AT the date count and gap vector recorded by H1.6 #3.

## H1.5 Verdict — ordered checklist, every input lands exactly once

1. **Evaluability:** ≥ 20 distinct complete-or-terminated event dates AND ≥ 30 events;
   **instrument coverage PER DATE — the share of that session's floor-clearing fenced
   equity name-days carrying an OI match (≥1,000 contracts) — must be ≥ 70%; a date
   below it is instrument-dead: dropped and counted, never graded** (measured at
   drafting: 36 of 37 dates run 82.0–90.3%; 07-24 is the known zero pending H1.6 #2) —
   the ≥ 20-date bar then governs; the regime-span rule of G.2 with ALL THREE buckets
   (≤ −2%, the OPEN interval (−2%, +2%), ≥ +2% represented on evaluable event dates,
   composition reported — the two measured hinge dates, 2026-06-25 and 2026-07-15,
   recorded by name); not POWER-LIMITED. ANY run-level failure ⇒ **UNEVALUABLE —
   instrument-limited or under-powered, recorded as such; no substitute cell, no
   re-declaration, no re-run** (and per R.4 it still discharges the fair trial).
2. **The test:** the primary per-event-date difference series against the calibrated
   one-sided cutoff — pass ⇒ **ALIVE**; fail ⇒ **DEAD**. DEAD reads "opening-heavy flow
   on a quiet tape carries no directional edge at 10td over an equally-quiet
   non-opening-heavy baseline, ON THE T+1 AGGREGATE PROXY" — never more. A large
   wrong-way t is DEAD and may never be called significant. Secondaries change no
   verdict.

**Why there is no dilution gate (the sharp reason, registered):** G.9b's null is a
per-order ±1 sign shuffle, which leaves `f_open`, `rv_park`, and therefore H1's event
SELECTION invariant — `f ≡ f₀` mechanically, the gate is degenerate, and no dilution
correction is defined. The honest content of that fact: under the order-sign null the
selection carries zero information by construction, so this design's entire evidential
weight rests on the tilt SIGN and on the ranking variables' association with forward
returns — which is exactly what the calibrated test and the permutation placebo measure.

## H1.6 Pre-run obligations (in order, each recorded, first-run-governs)

1. This registration LAW (gauntlet converged) — before the close of 2026-08-07.
2. **Instrument extension + freeze:** pull the 2026-07-27 OPRA statistics file and
   extend `oi_reconcile` through session 2026-07-24 (§2.3 machinery, pure acquisition);
   then **the H1 implementation is committed and its git hash recorded BEFORE the
   Addendum G scoring run executes; the feasibility and input records below run on that
   frozen code; any later code change is a dated addendum.**
3. **Input-property record (the G.10 #5 analogue; ALSO cannot run before the close of
   2026-08-07 for the judgment-window halves):** training-window event counts and
   realized percentile cuts; per-date instrument coverage across all 37 dates; the
   complete-or-terminated date count + SPY-session gap vector; event count; **per-date
   cohort-size distribution for BOTH windows — G's shape-invariance disclosure imported
   verbatim: the calibration is scale- but NOT shape-invariant, and a judgment window
   materially thinner per-date than training is reported ON THE FACE of the verdict**;
   regime-span composition with the hinge dates named; truncated/ungradeable census on
   H1's cohort; per-arm tier composition, mean ATR14/price, per-date net signed share;
   the H1-event × G-fired-cohort overlap count.
4. The G.4-style calibration + MDE record, computed at #3's recorded inputs.
5. The scoring run: strictly after the Addendum G verdict is recorded, no earlier than
   the close of 2026-08-07.

## H1.7 What this registers and what it does not

Changing any coordinate, percentile, population rule, window, seed, or the verdict
grammar = a new dated addendum. This document creates NO gate on the Addendum G run and
inherits R.4's non-admissibility clause verbatim. The two fair-trial verdicts (G's grade
+ this one) go to the founder as a record; the subscription decision is his, made on
that record.
