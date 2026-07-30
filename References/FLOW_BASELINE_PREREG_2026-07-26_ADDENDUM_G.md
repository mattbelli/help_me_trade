# Addendum G — the judging-rules amendment (roadmap 1.15–1.23, hosting 1.4 and 1.5)

*⚠️ DRAFT v4 — NOT LAW until the gauntlet converges. Blocker counts by round: v1 18, v2 16, v3 11
(8 methodology + 3 record, one shared). Every v3 blocker is resolved below. Drafted by the
orchestrator, 2026-07-30.*

*Dated addendum to `FLOW_BASELINE_PREREG_2026-07-26.md`. This is the amendment roadmap §1d
reserved as "Addendum F"; that letter was consumed 07-28 by the label-adoption floor redesign, so
this is G (`INDEX.md`'s Addendum E row records the renumber). **This addendum also supersedes the
two stale routings of that letter**: roadmap 1948's "registered hypothesis in Addendum F" and
HANDOFF's "(a) Addendum F registration of the three 1.14f findings" both now route to
`References/FLOW_1_14F_FINDINGS_REGISTRATION.md` (separate study family; not yet written; **G.2's
scoring run may not execute before it is committed law**). Scope: roadmap 1.15–1.23 + 1.4's
terminal-value rule + 1.5's multiplier bound. 1.24/1.25 are dispositioned and untouched — and the
**registered event universe everywhere in this addendum incorporates §1.25's dispositions by
reference** (clause 1 wide-print rule implemented; clause 2 retired), since those live in the
roadmap, not the addendum chain. Addenda A–F stand unedited (D:6-9). The test split
(2026-06-01 → 2026-07-25) has had NO outcome statistic computed on its events; training-window
outcome measurements that exist are disclosed in G.2. Citation convention: "Addendum B" =
`FLOW_BASELINE_PREREG_2026-07-26_ADDENDUM_B.md`; the per-tier null table lives in **roadmap 1.7's
"ADDENDUM B" run** (`tools/base_rate_table.py`), cited as "the 1.7 null-table run".*

---

## G.1 (1.15) Baseline #4 corrected to production's real parameters

Base prereg 120-122 registers baseline #4 as "≥ $500k premium, DTE ≤ 30" — false, verified in
code (roadmap 1.10(d)'s list is also incomplete). Registered against
`signals/options_flow/{constants,sweeps}.py` (2026-07-30): **baseline #4 is a
parameter-equivalent whole-market reconstruction of the production sweep lane** — premium floor
`MIN_PREMIUM_USD = 25_000` single-name / `MIN_PREMIUM_INDEX_USD = 250_000` for
`INDEX_MEGACAP_SET` {SPY, QQQ, IWM, DIA, SPX, SPXW}; cluster window `SWEEP_WINDOW_MS = 500`;
fires on ≥ `SWEEP_MIN_EXCHANGES = 3` exchanges OR the split-order variant (one exchange,
≥ `SPLIT_MIN_PRINTS = 5` prints) — branch at `sweeps.py:113-114`, both emit `event_type="sweep"`
at `sweeps.py:144`; the index-megacap 0–1DTE skip (`sweeps.py:51`); the spread-leg/hygiene
exclusions (`sweeps.py:45-47`); no other DTE gate. **Universe disclosure:** the live lane
consumes the ~30–40-name flow watchlist; baseline #4 applies the same parameters whole-market,
universe stated per G.5 — a reconstruction at production's parameters, not the live lane's
reach. The primary verdict does NOT require beating baseline #4; kill authority runs against the
no-signal baseline only.

## G.2 (1.16 + 1.23a) Falsification is rewritten around ONE primary cell

Base prereg 168-173's kill rule cannot fail (a null pipeline almost surely leaves a surviving
cell across ~64 variants). **Amended: survival authority belongs to exactly one primary cell.**
Verdicts carry their cell — "ALIVE/DEAD (ALL, Q5, 10td)", never "the mechanism" unqualified.

**Coordinates:**

- **Contract arm: ALL** — the base prereg's registered primary, raw and unfiltered (163);
  SHORT_OTM is exploratory (its Q1/Q2 have no usable null — G.9d; its Q3–Q5 nulls exist and its
  exploratory cells are graded normally).
- **Axis:** registered call/put tilt (`dir`), base prereg 68-72.
- **Tilt variant: RAW** (Addendum A:70's demeaned variant reported exploratory; kill authority
  may not depend on the unimplemented 1.25-clause-3 transform).
- **Classification:** midpoint PRIMARY per Addendum A.
- **Package fence:** multileg-EXCLUDED under **Addendum E as amended by F.1**; Addendum F.4's
  caveat rides every surface (unlabeled slice retains package legs, heuristic recall ≈ 51%,
  worst index-open). Addendum D's both-arms rule stands; kill authority attaches to the excluded
  arm only. Contamination-justified (v7, legacy fence per G.8: packages remove 62% of the
  single-name directional net, +$26.4B → +$10.1B).
- **Signal arm:** `|z| ≥ 2.0`, with the registered $250k day floor and ≥15-of-20 presence rule
  (base prereg 84-90) — **the floor, the presence rule, and the package fence are all part of
  the event definition and therefore of BOTH f and f₀'s universes** (G.9b/c).
- **Liquidity tier: Q5**, by roadmap 2.4's registered outcome-blind stage-1 rule (*max
  per-quintile MIN of the open/close event-date census sides*; `tools/open_close_split.py`,
  2026-07-29, pre-outcome; census covers the 87.9% of events f_open ranked): Q1 0/1, Q2 4/3,
  Q3 41/34, Q4 189/179, **Q5 192/192** ⇒ Q5 under that rule and any monotone variant. Noted
  plainly: max-dates favors populous tiers, so kill authority lands on the tier with the
  HIGHEST noise floor; the registered mid-cap sub-hypothesis (149-151) grades as an exploratory
  contrast.
  **Outcome-data disclosure:** at drafting, roadmap 2.4 stage-2 (2026-07-29) had graded 16,102
  of 16,598 events with per-event signed 10-day terminal alpha vs SPY on this same event
  population — tabled as open-minus-close tercile differentials (Q5 −0.043, 95% CI
  [−0.133, +0.046], FAIL; Q4 +0.078; Q3 n-too-small; robustness legs: dose gradient
  +0.026/+0.000/+0.046/−0.058/+0.041, ex-expiry −0.048, tercile-edge ±5pt −0.052/−0.012,
  same-side splits; 2.4's own Addendum A exploratory Q5 horizon curve 1d +0.000 / 2d +0.009 /
  5d +0.008); per-tier cohort alpha was one groupby away. The 1.14c/d screens also ran on
  training. The tier is selected by 2.4's rule, which predates stage-2; no inference is claimed
  about Q5's cohort alpha. **Pre-commitment:** training-window per-tier cohort alpha is
  published alongside the test result. Note: 2.4's grader is close-to-close — a DIFFERENT
  convention from this cell's metric (below).
- **Horizon:** 10 trading days. **Metric:** terminal alpha vs SPY in ATR units, **entry at the
  next session's open per base prereg 97/103** — `(close_{d+10} − open_{d+1}) / ATR14`, SPY
  same-window, signed; wrong-direction significance is a FAIL. Named amendment: kill authority
  rests on terminal alpha alone (base prereg 170 coupled MFE/MAE; MFE/MAE now exploratory).
- **Judgment window:** the registered test split — sessions **2026-06-01 → 2026-07-24 (38
  sessions; the split's 2026-07-25 boundary is a Saturday)**, touched once. **The scoring run
  executes no earlier than the CLOSE of 2026-08-07** — the first session on which every
  judgment-window event has a complete 10-day forward window **or is terminated by a corporate
  action (`truncated_horizon`), whose window is complete at its last available close**. The
  ≥20-date bar counts dates whose events have complete-or-terminated windows under the same
  definition.
- **Terminal-value rule (roadmap 1.4):** a name whose bars stop mid-horizon (delisting,
  acquisition — the archetypal winners; detection surface `no_spot_missing`, spikes WBA $386M /
  SATS $70.5M) **stays in the cohort**, flagged `truncated_horizon`, graded at last available
  close over the truncated window, SPY same-window, pre-event ATR14. Disclosed bias: a
  truncated window against a 10-day-scale ATR shrinks the entry ~√(d/10) toward zero —
  conservative on the winners; the cell reports the count and premium share of
  `truncated_horizon` events.
- **Data vintage:** v7 corpus of record, full window; Addendum F's ≥98% subset = robustness arm.

**Verdict — an ordered checklist; every input combination lands exactly once:**

1. **Evaluability:** ≥ 20 distinct event dates with complete-or-terminated windows AND ≥ 30
   events in the cell; the null tier usable (G.9c/d); the regime-span rule met — registered
   mechanically on the FORWARD reading: the 38 dates' own 10-day forward SPY returns
   (open_{d+1} → close_{d+10}, matching the metric convention) must include values ≤ −2%,
   within ±2%, and ≥ +2% (composition reported per base prereg 128); and the pre-run power
   record (G.4) must not have declared the cell power-limited. ANY failure ⇒
   **UNEVALUABLE (ALL, Q5, 10td)** — not alive; no substitute cell, no re-declaration. (G.8's
   unclassified ceiling gates ARM-SPLIT TABLES only — the primary cell's estimand has no root
   fence, so the share is recorded and reported, never a gate here.)
2. **Dilution gate:** `f ≤ f₀`, or the registered lower confidence bound on `(f − f₀)` failing
   to exclude zero (G.9b), ⇒ **DEAD** — firing indistinguishable from chance is a substantive
   negative.
3. **The test:** the UNCORRECTED per-event-date difference series (G.4) against the
   size-calibrated critical value — pass ⇒ **ALIVE**; fail (including wrong-direction
   significance) ⇒ **DEAD**. The dilution correction rescales the reported effect size only
   (G.9b); it never touches this step.

Everything else is exploratory, labelled, FDR reported, no survival authority; a surviving
exploratory cell is a fresh-prereg hypothesis (172-173). No partial rescue.

## G.3 (1.17) Horizon of record = 10 trading days

The 3/10/21/45 grid stays frozen and fully reported; only 10td carries confirm-or-kill
authority. 21/45d give ~2–4 independent observations on 12 months — reported, cannot confirm or
kill; 3d same non-authoritative status.

## G.4 (1.18 + 1.23d) The statistical bar — size-calibrated, power-stated, attainable

Base prereg 124-128's `t = avg/STD × √N` per event is withdrawn as the inference rule (measured
2026-07-26: 6.4 effectively independent names of 5,554 — naive √N overstates ~29×; motivates
date-grain inference, the estimator sizes the residualized metric). v2's block bootstrap was
withdrawn as unattainable **under Rademacher weights** (p-floor 0.125–0.25 at 3–4 blocks;
disclosed: Webb six-point weights would lift that floor, so "no attainable resampling estimator"
was over-claimed — the calibrated route below is chosen by registration, not necessity).
**Registered, one estimator:**

- **Estimand:** the difference of equal-weighted per-event-date means — event cohort minus the
  no-signal baseline (base prereg 117), baseline drawn on the SAME judgment window and entering
  the same date series — of the G.2 metric. UNCORRECTED by dilution (G.9b rescales reporting
  only).
- **Inference:** collapse to the G ≤ 38 date series; **Newey-West HAC with 9 lags** (identical
  to Driscoll-Kraay on a scalar equal-weighted date series; 9 = H−1 spans the overlap exactly),
  autocovariances computed by **calendar session gap**, not array position.
- **Critical value — size-calibrated, not tabulated:** the tabulated `t_{0.99, 37}` = 2.43 is
  registered as INVALID here (measured by simulation: at 9/38 bandwidth the nominal-1% test
  rejects a true null ~4–10% depending on overlap; the true 1% cutoff lies ~3.5–5.8).
  **Pre-run obligation: simulate the null distribution of the NW t using the per-date series'
  dependence estimated on the TRAINING window (outcome-free), fix the 1% cutoff, record it.**
- **Power, stated before the run:** from the same training-window series, publish the per-date
  sd, autocorrelation, and the implied minimum detectable effect (80% power) at the calibrated
  cutoff. **If the recorded MDE exceeds 1.0 ATR — an effect larger than anything this program
  has measured — the cell is declared POWER-LIMITED in the pre-run record and G.2 step 1
  treats it as an evaluability failure** (threshold fixed now, pre-outcome).
- **Power bar:** ≥ 20 distinct complete-or-terminated-window event dates per cell, per tier
  (G.9d); `N ≥ 30` events (both in G.2 step 1); regime composition reported, span rule per
  G.2 step 1.

## G.5 (1.19) The missing "Universe" section, inserted

Base prereg 32's forward-referenced "Universe" section, written: **every table states its
universe — total names, names surviving each registered floor, per-tier name-day counts, and
(G.8) the unclassified premium share.** No universe numbers ⇒ not usable as evidence. Context:
~5,554 option-active names; $100k floor keeps 1,437; top-~1,000 concentration is an unverified
audit assertion, motivation only.

## G.6 (1.20) The abstention band: arm-split REPORTING, pooled band unchanged

The 20–35% band (C:60-62; D:136-138, binding on the sum of the three states) **stands unchanged
as the only binding band.** Confirmatory set by range and exclusion: v7 sessions in
**2025-07-29 → 2026-07-27** minus **2026-07-23** (characterized by Addenda A/B pre-band;
2025-07-28 is pre-range — the session the rule was chosen on). Sessions after 2026-07-27:
reported separately, never pooled. Vintage disclosure: the band predates v7's wide-print rule
(~0.3–0.4% of premium removed) — disclosed, not adjusted. The band check spends no outcomes
(same-session inputs only). Arm-split reporting: pooled (binding) + single-name + index/ETF
(per G.8); pooled premium is index-dominated (30.8% — Addendum B). No per-arm bands declared
(D:139-141); one would require a future addendum on unexamined data.

## G.7 (1.21) Standing rule: pooled premium-weighted statistics carry their single-name arm

**Always reported together.** Measured justification (v7, legacy fence per G.8, v7 re-roll =
256 sessions): single-name pooled directional net holds sign ex-multileg (+$26.4B → +$10.1B);
index/ETF flips (−$93.5B → +$9.4B); per-session flip counts 47 and 117. Generalizes Addendum
D's both-arms rule to every premium-weighted statistic; format per 1.25's standing practice
(name-day survival + premium share together).

## G.8 (1.22 + 1.5's bound) One registered root classification, reused everywhere

Replaces the three incompatible lists (base prereg 163-164's 9 entries; 18-root `INDEX_ROOTS`;
50-ticker hand-built `ETF_SET`). **{index, etf, equity}, in that precedence, the only fence any
tool may use:**

- **Mapping first:** strip trailing digits from the OCC root (WOLF1 → WOLF; 0 of 13,074
  directory symbols contain a digit), and match the directory `Symbol` with `.`/`$` suffix
  normalization in BOTH directions (OCC `BRKB` ↔ directory `BRK.B` — roadmap 1.8's known
  offender).
- **index** (checked first) = the 18-root `INDEX_ROOTS` (`db/flow_corpus.py:91-94`), registered
  as-is; supersedes Addendum B's 9-root prose list and (with etf) replaces base prereg
  163-164's arm list. Its own comment declares it incomplete for spot resolution — an argument
  that does not transfer to arm fencing; **pre-run check, recorded: top-20 equity-classified
  roots by corpus premium reviewed against index-product naming; misses added by dated
  amendment before the scoring run.**
- **etf** = `ETF = Y` in the **Nasdaq Trader symbol directory** (`nasdaqtraded.txt`; 13,074
  symbols, 5,563 flagged Y, verified 2026-07-30) **∪ exchange-traded notes by mechanical
  rule**: Security Name matching case-sensitive `\bETNs?\b|Exchange[- ]Traded Note` (the
  case-insensitive variant trips on "Vietnam"; live counts: 47 Y-flagged + **16 N-flagged — 
  AMUB ATMP BDCZ DGP DGZ DJP DZZ FNGD FNGO GRN MLPB PFFL SMHB UCIB VXX VXZ**, enumerated in the
  snapshot). FNGU/FNGD land in the same arm.
- **equity** = everything else, in two registered layers: a stripped-and-normalized root
  matching no directory row but matching the **corpus equity-bars namespace** classifies
  **equity, tagged `delisted`** (WBA, PARA, SATS, FI — absent from any current-day directory by
  construction; the delisted tail is EXPECTED, measured $54M–$574M/session ≈ 0.1–1.2% of
  premium); **unclassified := matching neither.** Every table reports unclassified premium
  share; **share > 1% ⇒ the ARM SPLIT is unevaluable for that table** — arm-split tables only,
  never the primary cell (G.2 step 1) — **and the share is measured and recorded BEFORE the
  scoring run**, so the ceiling can never be a results-time veto.
- **Snapshot discipline:** one JSON at `signals/options_flow/root_classification.json` +
  loader; snapshot date stamped; frozen once generated; regeneration = new addendum. Not
  point-in-time (an ETF delisted mid-corpus resolves equity-delisted) — disclosed.
- **Migration and history:** the seven `ETF_SET` consumers migrate; `ETF_SET` is **retired
  into the snapshot as `LEGACY_ETF_SET`** (never deleted — frozen results stay reproducible).
  Frozen as computed on the legacy fence: 1.8's netflip and the mover_*/lead_lag outputs (the
  axis table has no `ETF_SET` dependency). Frozen results labelled, never mixed with
  post-migration arms; every NEW grading uses the registered fence.
- **The 1.5 multiplier bound:** `premium = price × size × 100` hardcodes the multiplier; no
  OPRA definition field can correct it (all candidates UNDEF on 1,592,395 contracts). Adjusted
  contracts detectable by digit suffix; measured exposure 225 roots / 1,897 prints / $7.2M =
  0.028% of session premium — bounded, closed against re-opening.

## G.9 (1.23) The noise floor is a curve; grading divides by it, per tier

- **(a)** No pooled grading with kill authority (tiers differ ~4.4×; flat floors over-penalize
  thin names ~3.5× and tilt toward "signal lives in liquid names").
- **(b) Dilution correction, reporting-only by construction:** `q̂ = f₀/f`,
  `lift_real = lift_observed / (1 − q̂)`. **The t-statistic is computed on the uncorrected
  per-date difference series (G.4, G.2 step 3); q̂ — held fixed — rescales the point estimate
  and its reported interval only.** (Equivalently: scaling estimate and SE by the same fixed
  scalar leaves t unchanged; stated so no implementer rediscovers it.) Registered
  transparency: q̂ is an UPPER bound on the noise share (with signal fraction π the true share
  is (1−π)·f₀/f), so `lift_real` is reported as an upper bound — conservative in the gate
  direction. Uncertainty enters at exactly one place: **the dilution gate's lower 99%
  confidence bound on `(f − f₀)`, from a joint bootstrap over event dates × null draws**, both
  measured on the judgment window (point estimates and uncertainty from the SAME window).
  Assumptions registered: fired name-days partition into f₀ noise + (f − f₀) real; noise fires
  behave like the no-signal baseline (117). `f ≤ f₀` or a failing LCB ⇒ DEAD (G.2 step 2).
- **(c) The table of record is re-emitted before the scoring run on the REGISTERED EVENT
  UNIVERSE, ON THE JUDGMENT WINDOW:** v7 corpus (§1.25 dispositions incorporated), $250k day
  floor + ≥15-of-20 presence (both absent from the current run — `base_rate_table.py:205-208`
  deliberately ran no-floor; the presence rule exists in `order_grain_null._fire`), **and the
  multileg-excluded package fence per G.2** (absent from the current extractor, which decodes
  raw tape and never reads condition labels). **Stated plainly: this requires a code change —
  the null extractor is re-pointed at the v7 rollup's labelled rows — not a re-run of the
  existing tool.** Same construction otherwise: trailing-60 quintiles, seed 20260729, 20
  draws. **The FIRST run governs; a re-run is robustness-only unless the defect is documented
  in a dated amendment BEFORE the re-run.** The 38-session window is the divisor's window; the
  full-corpus table is reported as context (its numbers — Q1 1.961% … Q5 8.629%, pooled
  6.612%; v6 stationarity 0.29pt ALL, v7 line 0.63pt arm-unnamed — are provisional context,
  not the divisor). Expectation pre-stated: under the $250k floor, Q1 (0.105% of name-days
  admitted) and likely Q2 will be unevaluable in the ALL arm too — declared, not discovered.
  `f` is measured per tier on the same window/universe and recorded pre-run; both are
  input-property measurements (G.10); whatever they show, G.2's checklist assigns the verdict.
- **(d)** Power per tier (G.4's bar per quintile). Pre-declared: SHORT_OTM Q1/Q2 unevaluable
  (Q1: 34 real name-days; SE 2.73pt on 2.353%), never merged upward.
- **Deliberately NOT done:** no per-tier re-thresholding to flatten the null; revisit after the
  first grade.

## G.10 What would change this (new addendum, as always)

Changing the primary cell's coordinates or selection rule, the horizon of record, the
inference estimator or its calibration procedure, the root-classification source/snapshot/rule,
the table of record, or the dilution convention — each is a new dated addendum, never an edit.
**"Touching the test split" = computing any outcome statistic on test-split events**; data
acquisition, corpus rolls, and input-property measurements (firing rates, universe shares,
abstention, the null re-emission, the regime-span record, the date-count record, the size
calibration and MDE record) are not touches. Disclosed prior reads that stand: 2.4's June-close
reads, 1.14f's outcome-store fetch. An outcome statistic computed on the split before this is
law ⇒ this addendum is void, violation recorded.

**Pre-run obligations, IN ORDER, all recorded before the scoring run (no earlier than the close
of 2026-08-07):**
1. `FLOW_1_14F_FINDINGS_REGISTRATION.md` committed law.
2. The G.8 snapshot generated + the top-20 index-miss check recorded (the fence must exist
   before anything uses it).
3. The G.9c re-emission (fenced, floored, judgment-window) + the per-tier `f` measurement
   recorded.
4. The G.4 size calibration + MDE record (training-window dependence) recorded.
5. The G.2 step-1 input-property records: complete-or-terminated date count, event count,
   regime-span composition, arm-split unclassified shares.
Classification-lab work is independent and does not gate this.
