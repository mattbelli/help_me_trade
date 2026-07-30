# Addendum G — the judging-rules amendment (roadmap 1.15–1.23, hosting 1.4 and 1.5)

*⚠️ DRAFT v7 — NOT LAW until the gauntlet converges. Blockers by round: v1 18, v2 16, v3 11,
v4 13, v5 9, v6 7 (6 methodology + 2 record, 1 shared). All resolved below. Orchestrator,
2026-07-30.*

*Dated addendum to `FLOW_BASELINE_PREREG_2026-07-26.md`. This is the amendment roadmap §1d
reserved as "Addendum F"; that letter was consumed 07-28, so this is G (`INDEX.md`'s Addendum E
row records the renumber). **Supersedes every stale "Addendum F (the amendment)" routing in the
roadmap — lines 1948, 123 (1.4), 132/142 (1.5), 595, 1984 (§1.23), the §1d heading, and the
§"Phase 1 is done when" completion gate at 2178, which the 07-28 label-adoption Addendum F must
NOT be read as satisfying**; also superseded: the roadmap's "base-rate table re-run against v7"
bullet — verified false (the extractor short-circuits on cached npz, all dated pre-re-roll; the
existing table is v6-fence semantics, per G.9c); the three 1.14f findings
register separately in `References/FLOW_1_14F_FINDINGS_REGISTRATION.md` (not yet written; gates
the G.2 scoring run). Scope: roadmap 1.15–1.23 + 1.4 + 1.5. 1.24/1.25 are dispositioned and
untouched; **the registered event universe everywhere here incorporates §1.25's dispositions by
reference** (clause 1 wide-print rule implemented at `SPREAD_MAX_FRAC = 0.50`; clause 2 retired).
Addenda A–F stand unedited (D:6-9). The test split (2026-06-01 → 2026-07-25) has had NO outcome
statistic computed on its events; training-window outcome measurements that exist are disclosed
in G.2. **First-run-governs applies to EVERY pre-run obligation in G.10**: the first recorded
execution of each governs; a re-run is robustness-only unless its defect is documented in a
dated amendment BEFORE the re-run. Citation convention: "Addendum B" = the prereg addendum file;
the per-tier null table is **roadmap 1.7's "ADDENDUM B" run** ("the 1.7 null-table run").*

---

## G.1 (1.15) Baseline #4 corrected to production's real parameters

Base prereg 120-122's "≥ $500k premium, DTE ≤ 30" is false — verified in code (roadmap 1.10(d)
is also incomplete). Registered against `signals/options_flow/{constants,sweeps}.py`
(2026-07-30): **baseline #4 is a parameter-equivalent whole-market reconstruction of the
production sweep lane** — floors `MIN_PREMIUM_USD = 25_000` single-name /
`MIN_PREMIUM_INDEX_USD = 250_000` for `INDEX_MEGACAP_SET` {SPY, QQQ, IWM, DIA, SPX, SPXW};
`SWEEP_WINDOW_MS = 500`; fires on ≥ `SWEEP_MIN_EXCHANGES = 3` exchanges OR the split-order
variant (one exchange, ≥ `SPLIT_MIN_PRINTS = 5` prints) — branch `sweeps.py:113-114`, both emit
`event_type="sweep"` at `:144`; index-megacap 0–1DTE skip (`:51`); spread-leg/hygiene exclusions
(`:45-47`); no other DTE gate. **Direction:** production requires ≥ `PRESSURE_MIN_CLASSIFIED =
0.5` of cluster premium classified else `undetermined`, clustered per contract; the
reconstruction grades direction by the Addendum A midpoint rule — parameter-equivalent, with the
direction source stated, not assumed identical. **Universe:** the live lane consumes the
~30–40-name flow watchlist; the reconstruction applies these parameters whole-market (universe
per G.5). The primary verdict does NOT require beating baseline #4.

## G.2 (1.16 + 1.23a) Falsification is rewritten around ONE primary cell

Base prereg 168-173's kill rule cannot fail. **Amended: survival authority belongs to exactly
one primary cell.** Verdicts carry their cell — "ALIVE/DEAD (ALL, Q5, 10td)", never "the
mechanism" unqualified.

**The registered event pipeline (order of operations, one parameterization):** per-print v7
fence first — §1.25 clause-1 spread gate (`SPREAD_MAX_FRAC = 0.50`) and quote-validity, then
per-print package exclusion under Addendum E-as-amended-by-F.1; **classified premium** (the
post-fence `bull + bear` base) is the operative premium for everything downstream — the $250k
day floor (named amendment: base prereg 87 says "total"; classified is registered as the
operative base now, matching the implemented precedent), the ≥15-of-20 presence rule, the
`|z| ≥ 2.0` event rule (z-scores with **ddof = 0**), and the trailing-60-day quintile ranks.
`f` and `f₀` both live on exactly this universe (G.9b/c).

**Coordinates:**

- **Contract arm: ALL** (base prereg 76-80 registers ALL and SHORT_OTM as two reported arms;
  **designating ALL as the kill-authority arm is a registration decision made here, now,
  pre-outcome** — SHORT_OTM's Q1/Q2 have no usable null (G.9d) and its Q3–Q5 cells grade as
  labelled exploratory).
- **Root universe: EQUITY-classified roots only (G.8 fence, mapping applied) — a registered
  coordinate, previously implicit.** The metric requires underlying bars; cash-index roots are
  structurally bar-less (`no_spot_index` — measured at drafting: **37.1% of judgment-window
  premium, $687.2B of $1,852.8B**) and would otherwise route silently to UNGRADEABLE — they,
  not corporate actions, are the dominant would-be-ungradeable mass, and the census instrument
  for them is `no_spot_index`, a different bucket from `no_spot_missing` (the 1.2 split exists
  precisely to separate them). ETF roots grade as a labelled exploratory arm — G.7's measured
  sign-flip (index/ETF pooled net flips ex-multileg; single names hold) is the recorded reason
  they carry no kill authority. The null table, `f`, `f₀`, the tier census, and the baseline
  all live on this same equity universe.
- **Axis:** registered call/put tilt (`dir`), base prereg 68-72.
- **Tilt variant: RAW** (demeaned variant exploratory per Addendum A:70; kill authority may not
  depend on the unimplemented 1.25-clause-3 transform). Acknowledged: A registered demeaning
  against the standing buy/sell skew, so RAW-only kill authority re-imports that skew — accepted
  and disclosed, since the alternative hangs the verdict on unbuilt code.
- **Classification:** midpoint PRIMARY per Addendum A.
- **Package fence:** multileg-EXCLUDED (E as amended by F.1); F.4's caveat rides every surface
  (unlabeled slice retains package legs, heuristic recall ≈ 51%, worst index-open). D's
  both-arms rule stands; contamination-justified (v7, legacy fence per G.8: packages remove 62%
  of the single-name directional net, +$26.4B → +$10.1B).
- **Liquidity tier: selected by roadmap 2.4's registered outcome-blind rule — max per-quintile
  MIN of the open/close event-date census sides — RE-COUNTED on the registered pipeline above.**
  Disclosure: 2.4's census ran on the multileg-INCLUDED, unfenced base
  (`open_close_split.py:68`) and gave Q1 0/1, Q2 4/3, Q3 41/34, Q4 189/179, **Q5 192/192** —
  Q5 Pareto-dominates every tier there. Because the registered pipeline fences packages first,
  **obligation 3b re-runs the counting stage (outcome-blind — no forward return enters a
  census) on the registered universe; the tier is whatever the registered rule selects there;
  the pre-stated expectation is Q5.** Noted plainly: max-dates favors populous tiers, so kill
  authority is expected to land on the tier with the highest noise floor; the registered
  mid-cap sub-hypothesis (149-151) grades as an exploratory contrast.
  **Outcome-data disclosure:** at drafting, 2.4 stage-2 (2026-07-29) had graded 16,102 of
  16,598 events with per-event signed 10-day terminal alpha vs SPY on the same population —
  tabled as open-minus-close tercile differentials (Q5 −0.043, 95% CI [−0.133, +0.046], FAIL;
  Q4 +0.078; Q3 n-too-small; same-side −0.073 [−0.189, +0.007]; DTE 5–89 −0.059; ex-expiry
  −0.048; tercile-edge ±5pt −0.052/−0.012; dose gradient +0.026/+0.000/+0.046/−0.058/+0.041;
  2.4-Addendum-A exploratory Q5 horizon curve 1d +0.000 / 2d +0.009 / 5d +0.008); per-tier
  cohort alpha was one groupby away. The 1.14c/d screens also ran on training. The tier rule
  predates stage-2; no inference is claimed about Q5 cohort alpha. **Pre-commitment:**
  training-window per-tier cohort alpha is published alongside the test result. 2.4's grader is
  close-to-close — a DIFFERENT convention from this cell's metric.
- **Horizon:** 10 trading days. **Metric, written out:**
  `alpha = sign-graded [(close_{d+10} / open_{d+1} − 1) − (SPY_close_{d+10} / SPY_open_{d+1} −
  1)] × open_{d+1} / ATR14_pre-event` — beta = 1 by registration; entry at the next session's
  open per base prereg 97/103. Event observations are signed by `sign(z)` per base prereg 106;
  **baseline observations are signed by the SAME `sign(z)` rule on their own day** (|z| < 2
  days have a defined sign; exact-zero-z days are excluded from the baseline and counted) — a
  dose-response contrast, not a market contrast. The test is one-sided in the registered
  positive direction (G.4); the lower tail is never calibrated, so **a large wrong-way t is
  DEAD and reported, and may never be described as "significant"** on any surface. Named
  amendment: kill authority rests on terminal alpha alone (170's MFE/MAE coupling dropped;
  MFE/MAE exploratory).
- **Judgment window:** the test split — sessions **2026-06-01 → 2026-07-24, MINUS 2026-07-23
  (37 dates; 2026-07-25 is a Saturday)**, touched once. 07-23 is excluded because the midpoint
  classification rule — a registered coordinate of this cell — was set on that session's own
  data (A:10); G.6 already excludes it as in-sample, and one document may not treat one session
  two ways. G.10's outcome-only definition of "touching" is what would otherwise have admitted
  it — stated so the narrowing is visible. **The scoring run executes no earlier than the CLOSE of
  2026-08-07** — the first session on which every judgment-window event has a complete 10-day
  forward window **or is terminated (`truncated_horizon`)**. The ≥20-date bar counts
  complete-or-terminated dates.
- **Terminal-value rule (roadmap 1.4), two registered cases:** (i) a name whose bars STOP
  mid-horizon stays in the cohort, flagged `truncated_horizon`, graded at last available close
  over the truncated window, SPY same-window, pre-event ATR14 (disclosed bias: ~√(d/10) shrink
  toward zero — conservative on the winners; count + premium share reported). (ii) UNGRADEABLE
  applies only when the root, **its registered successor identity, and its dotted class-share
  form ALL return zero bars.** **The successor map is SOURCED, not inferred — v6's frozen
  five-pair hand list is superseded** (measured at drafting: **106 renames whose old root
  carries ≥ $250k of corpus flow premium**, not five). Registered source: **Alpaca
  corporate-actions `name_change` records** (`data.alpaca.markets/v1/corporate-actions`,
  `types=name_change`, span 2025-07-25 → the obligation-2 run date), filtered to plain
  alphabetic old/new roots with corpus flow on the old side, **date-scoped by `process_date`**
  — symbol reuse is real inside the corpus span (ARIS and GOLD were each vacated by one issuer
  and re-occupied by another), so a global symbol→symbol dict is forbidden — plus one
  merger-sourced continuation absent from the name-change feed (PARA→PSKY, corpus-seam
  verified). **Adjacency/seam inference is FORBIDDEN as a map source:** cross-checking the
  authoritative records refuted four of nine seam-inferred candidate pairs proposed in review
  (ERJ→EMBJ, not RKLX; HOND→IMSR, not NUAI; COMM→VISN and MNMD→DFTX — two renames one day
  apart, cross-paired; BYON→BBBY, not RR). v6's five instances all reconfirm under the
  authoritative source. **Map grain, registered: an identity MERGE at event grain** — 
  predecessor and successor are ONE underlying identity in G.8's mapping layer; presence
  (15-of-20), trailing-60 quintile ranks, z-scores, and grading all run on the merged series
  (old rows before `process_date`, new rows after; seams verified non-overlapping), so a
  mid-window rename manufactures no warm-up hole (unmerged, SATS→ECHO — $946M of predecessor
  premium in-window, handoff 06-23/24 — would zero ECHO's eligibility for the rest of the
  window; merged, it events from 06-24). Bankruptcy renames (Q-suffix successors, mostly
  bar-less) map and then fail the bar test honestly — they stay in the ungradeable census by
  the normal rule. BRKB has 0 bars under BOTH forms today, so obligation 2 includes a **bar
  backfill for the dotted class-share forms (BRK.B, BF.B, HEI.A, PBR.A) and any mapped
  successor still bar-less** — data acquisition, not a touch. What remains after mapping +
  backfill (e.g. WBA, taken private) is the true ungradeable set: excluded, counted per session
  with premium via the per-root `no_spot_missing` AND `no_spot_index` breakdowns — **per-root,
  never the bucket** (the `no_spot_missing` bucket range is $41.3M–$7,196.6M/session, mean
  $256M over 138 sessions, and its tail is adjusted contracts the digit-strip removes; the
  earlier $54M–$574M/37-session figure is retracted in the record) — recorded pre-run
  (obligation 5).
- **Data vintage:** v7 corpus of record; Addendum F's ≥98% subset = robustness arm.

**Verdict — ordered checklist; every input combination lands exactly once:**

1. **Evaluability:** ≥ 20 distinct complete-or-terminated event dates AND ≥ 30 events in the
   cell; the null tier usable — **registered criterion: across-draw SE ≤ 0.5pt (a NEW
   registered criterion, numerically equal to the 1.7 B3 reporting threshold, per obligation
   3) AND ≥ 1,000 DISTINCT tested name-days in the tier**, both evaluated at obligation 3; the
   dilution gate resolvable (G.9b — pre-run precision record; a gate unable to resolve
   `f − f₀ = 1.0pt` at its 99% bound routes here, not to DEAD); the regime-span rule met —
   **measured on the cell's evaluable event dates** (operationalizing base prereg 128's
   "cohort must span", named as this amendment's mechanical form): their 10-day forward SPY
   returns (open_{d+1} → close_{d+10}) must include values ≤ −2%, in the OPEN interval
   (−2%, +2%), and ≥ +2%, composition reported; the pre-run power record (G.4) must not have
   declared the cell power-limited. ANY failure ⇒ **UNEVALUABLE** — not alive; no substitute
   cell, no re-declaration. (G.8's unclassified ceiling gates ARM-SPLIT tables only, never
   this cell.)
2. **Dilution gate:** `f ≤ f₀`, or the registered LCB on `(f − f₀)` (G.9b) failing to exclude
   zero **when the gate's recorded precision can resolve 1.0pt**, ⇒ **DEAD**.
3. **The test:** the UNCORRECTED per-event-date difference series (G.4) against the calibrated
   one-sided cutoff — pass ⇒ **ALIVE**; fail ⇒ **DEAD**.

Everything else is exploratory, labelled, FDR reported, no survival authority (172-173). No
partial rescue. **Every DEAD verdict prints the recorded MDE beside the observed effect** — a
DEAD at MDE near the ceiling mostly reflects power, and the record must show it on its face.
And the verdict label carries its contrast: the baseline is signed sub-threshold days, so
**DEAD reads "no dose-response over sub-threshold days," never "events carry no alpha."**

## G.3 (1.17) Horizon of record = 10 trading days

Grid 3/10/21/45 frozen and fully reported; only 10td confirms or kills (21/45d ≈ 2–4
independent observations — reported, non-authoritative; 3d likewise). **At the earliest
permitted scoring date (close of 2026-08-07), completeness differs sharply by column — 10d:
37 of 37 dates complete; 21d: 27 of 37 (73%); 45d: 3 of 37 (v6's "~1" corrected against the
real calendar): the 21/45 columns are reported TRUNCATED AT THE RUN
DATE with the truncation share disclosed per column** — they are not graded under the
`truncated_horizon` rule (that rule is for bars that stop, not horizons that have not
elapsed), and no later re-computation touches the split a second time.

## G.4 (1.18 + 1.23d) The statistical bar — size-calibrated, power-stated

Base prereg 124-128's per-event t-rule is withdrawn for inference (6.4 effective names of 5,554,
measured; naive √N ~29× overstated). v2's block bootstrap withdrawn (Rademacher p-floor
0.125–0.25 at 3–4 blocks; Webb weights would lift it — the calibrated route is chosen by
registration). **Registered, one estimator, one parameterization:**

- **Estimand:** difference of equal-weighted per-event-date means — event cohort minus the
  no-signal baseline (117), baseline on the SAME judgment window, same date series — of the
  G.2 metric. Uncorrected by dilution. **The baseline's universe is pinned (117's "same
  names" reads three ways; registered here as one):** per date, ALL eligible name-days in the
  SELECTED tier (obligation-3b census) — same registered pipeline and fence, same equity
  universe, same G.8 mapping — that do NOT clear `|z| ≥ 2.0`, each signed by its own sign(z)
  (exact-zero-z excluded and counted, per G.2). **The terminal-value rule applies IDENTICALLY
  on both sides:** a truncated name-day grades `truncated_horizon` and an ungradeable one is
  excluded by the same three-way test whether it sits in the event cohort or the baseline —
  delisting/M&A moves enter both arms or neither. Named plainly: an asymmetric truncation rule
  was the one channel in this design that could carry a null to ALIVE, leaking in exactly the
  direction flow is supposed to predict; symmetry closes it.
- **Inference:** collapse to the recorded date series; **Newey-West HAC, 9 lags** (≡
  Driscoll-Kraay on a scalar equal-weighted series; 9 = H−1), autocovariances by **calendar
  session gap**. If the Bartlett sum returns a non-positive variance on the irregular series,
  the registered fallback is the equal-weighted long-run variance from the calibration
  bootstrap itself (always positive; same dependence model). **Units pinned:** the fallback is
  the long-run VARIANCE `LRV` of the per-date series; the test statistic's denominator is
  `sqrt(LRV / G)` — identical inside replicates and in the real run.
- **Critical value — size-calibrated:** tabulated `t_{0.99, G−1}` is INVALID at this bandwidth
  (type-I ~4–10%, measured by simulation during this addendum's review; the recorded
  calibration supersedes that estimate). **Registered procedure, every parameter fixed: the
  TRAINING-window — dated: 2025-07-28 → 2026-03-31, base prereg 134's registered train split
  (corpus starts 07-28; the validation span 2026-04-01 → 2026-05-29 and the test split are
  excluded). Disclosure: roadmap 2.4 used a different "training" span (through 2026-05-31) —
  two conventions were live in the record, and this pin is made with training-window outcomes
  already seen (no calibration window exists whose outcomes were unseen; the ORIGINAL
  registered split is the narrower, pre-existing choice). The calibration series is built at
  the SAME registered coordinates as the cell — pipeline, equity universe, selected tier, ALL
  arm, RAW, midpoint, fenced — as per-date event-minus-baseline differences per the estimand
  above. That** per-date difference series is DEMEANED TO ZERO, then moving-block
  bootstrapped — block length 10, 100,000 replications, seed 20260810 — at the RECORDED
  complete-or-terminated date count and calendar-gap vector (obligation 5 precedes obligation
  6); each replicate's statistic is `mean_b / NW_SE_b` at 9 lags on the assigned gap vector;
  the IDENTICAL variance rule applies inside every replicate and none is dropped (the fallback
  long-run variance is computed in a first pass over replicate means before any t is formed);
  cutoff = the 99th percentile of the signed replicate t (one-sided, registered-positive).**
  Registered approximation, disclosed: blocks are drawn by position in the training series
  while lags weight by calendar gap — near-daily in both windows, accepted. Recorded before
  the scoring run; first run governs.
- **Power:** from the same calibration replicates, the MDE is **SIMULATED** — the smallest δ
  on a 0.01-ATR grid at which ≥ 80% of δ-shifted replicates clear the calibrated cutoff —
  published with per-date sd and autocorrelation, **rounded to 2 decimals; MDE > 1.00 ATR ⇒
  POWER-LIMITED** in the pre-run record; G.2 step 1 treats it as evaluability failure. Until
  obligation 6 is recorded, no attainability claim is made.
- **Power bar:** ≥ 20 distinct complete-or-terminated event dates per cell per tier (G.9d);
  `N ≥ 30`; both in G.2 step 1.

## G.5 (1.19) The missing "Universe" section, inserted

Every table states its universe: total names, names surviving each registered floor, per-tier
name-day counts, unclassified premium share (G.8). No universe numbers ⇒ not usable as
evidence. Context: ~5,554 option-active names; $100k floor keeps 1,437; top-~1,000
concentration = unverified audit assertion, motivation only.

## G.6 (1.20) The abstention band: the confirmatory RUN IS COMPLETE — finding recorded

**Disclosure first: the confirmatory run completed 2026-07-30, before this addendum is law**
(BUILD_LOG row; `tools/confirmatory_abstention.py`, built to the C/D registration with its set
fixed in code BEFORE the result existed). **This clause adopts the run's pre-result set
VERBATIM and does not redefine it**: corpus-of-record (v7) sessions minus the two examined —
2025-07-28 (C:49) and 2026-07-23 (A:10/B:4) — which at run time was **251 sessions (2025-07-29 →
2026-07-29)**. **The registered finding: pooled abstention 19.97% of premium — 0.03pt BELOW the
20–35% band ⇒ formally outside, recorded, not absorbed** (C:60-62's own rule); per-session
median 19.1%, 140 below / 9 above / 102 inside; states: contested 12.2%, weighting_unstable
7.7%, no_opinion 0.04%. The band itself stands unchanged (D:136-138). Sessions after 2026-07-29
are reported separately, never pooled into this finding. Completeness disclosure: three pooled
sessions (07-27/28/29) post-date the base prereg's primary window end (2026-07-25) — the runner
kept and flagged them (`sessions_after_prereg_window`), a filter nobody registered. Vintage
disclosure: the band predates v7's wide-print rule (~0.3–0.4% of premium) — disclosed, not
adjusted. Addendum E's 07-24 probe
and Addendum F's 06-24 census examined in-range sessions but measured no abstention statistic —
stated so the exclusion list is complete rather than inferable. The band check spends no
outcomes. Arm-split reporting (pooled + single-name + index/ETF per G.8) applies to FUTURE
abstention reporting; no per-arm bands are declared (D:139-141).

## G.7 (1.21) Standing rule: pooled premium-weighted statistics carry their single-name arm

**Always reported together.** Measured justification (v7, legacy fence per G.8): single-name
pooled directional net holds sign ex-multileg (+$26.4B → +$10.1B); index/ETF flips (−$93.5B →
+$9.4B); per-session flip counts 47 and 117. Generalizes D's both-arms rule; format per 1.25's
standing practice (name-day survival + premium share together).

## G.8 (1.22 + 1.5's bound) One registered root classification, reused everywhere

Replaces the three incompatible lists. **{index, etf, equity}, in that precedence, the only
fence any tool may use:**

- **Mapping first:** strip trailing digits from the OCC root (WOLF1 → WOLF; 0 of 13,074
  directory symbols contain a digit; **a stripped form matching NO directory row stays
  `unclassified` — never guessed toward a near-miss row**: CMCS1 strips to CMCS, which matches
  nothing, not CMCSA), normalize `.`/`$` suffixes in BOTH directions (OCC `BRKB` ↔ directory
  `BRK.B` — the roadmap §1.2/§1.4 known offender), and apply the **registered successor map**
  per G.2's terminal-value rule (Alpaca corporate-actions `name_change` records + the one
  merger-sourced continuation, date-scoped by `process_date`, an identity MERGE at event
  grain; generated and frozen into the snapshot at obligation 2 — never hand-listed, never
  seam-inferred).
- **index** (checked first) = the 18-root `INDEX_ROOTS` (`db/flow_corpus.py:91-94`) **∪ a
  registered index-addition list frozen in the snapshot**; supersedes
  Addendum B's 9-root list and (with etf) replaces base prereg 163-164's arm list. **Pre-run
  check, recorded, TWO instruments (the top-20 check alone cannot see small index roots —
  census review at drafting surfaces XAU, NANOS, DJXW as sub-top-20 index-product candidates):
  (1) top-20 equity-classified roots by corpus premium reviewed against index-product naming;
  (2) the full zero-bar census (roots with premium and no bars under root, dotted form, or
  successor) reviewed the same way. Confirmed additions land in the snapshot's index-addition
  list at obligation 2; later misses are a dated amendment.**
- **etf** = `ETF = Y` in the **Nasdaq Trader symbol directory** — registered snapshot:
  **`signals/options_flow/nasdaqtraded_20260730.txt`, committed** (vintage `File Creation
  Time: 0730202618:02`; 13,074 symbols, 5,563 Y; source URL
  `nasdaqtrader.com/dynamic/symdir/nasdaqtraded.txt` is provenance only — it serves only the
  current day) — **∪ exchange-traded notes**: Security Name matching case-sensitive
  `\bETNs?\b|Exchange[- ]Traded Note` (live: 60 matches = 44 already-Y + **16 N-flagged: AMUB
  ATMP BDCZ DGP DGZ DJP DZZ FNGD FNGO GRN MLPB PFFL SMHB UCIB VXX VXZ**, enumerated in the
  snapshot). FNGU/FNGD land in the same arm.
- **equity, two registered layers:** (layer 1) a normalized root matching a directory row with
  `ETF = N`, **`Test Issue = N`** (the snapshot carries 33 `Test Issue = Y` rows, all ETF = N,
  and test roots DO print premium in the corpus — they are excluded from every arm and every
  universe; OPRA-only test roots absent from the directory, e.g. ZTST, are enumerated into the
  snapshot as a frozen `test_roots` list at obligation 2), and no ETN match; (layer 2)
  **delisted/corporate-action roots from the
  `no_spot_missing` census — frozen as a dated list inside the snapshot** (the equity-bars
  store CANNOT serve this layer: delisting is why a live bars API returns nothing — verified:
  WBA/PARA/SATS/FI have 0 `equity_bars` rows; the repo fixed this same conflation once already,
  `multileg_arm_probe.py:74-77`), tagged `delisted`. **unclassified := matching none of the
  above.** Every table reports unclassified premium share; **share > 1% ⇒ the ARM SPLIT is
  unevaluable for that table** (arm tables only; never G.2's cell), measured and recorded
  pre-run.
- **Snapshot discipline:** one JSON at `signals/options_flow/root_classification.json` +
  loader; snapshot date + source vintage + the delisted list + `LEGACY_ETF_SET` all inside;
  frozen once generated; regeneration = new addendum. Not point-in-time — disclosed.
- **Migration and history:** the seven `ETF_SET` consumers migrate; `ETF_SET` retired into the
  snapshot as `LEGACY_ETF_SET` (never deleted). Frozen as computed on the legacy fence: 1.8's
  netflip and the mover_*/lead_lag outputs (the axis table has no `ETF_SET` dependency).
  Frozen results labelled, never mixed with post-migration arms; every NEW grading uses the
  registered fence.
- **The 1.5 multiplier bound:** `premium = price × size × 100` hardcodes the multiplier; no
  OPRA definition field can correct it (all UNDEF on 1,592,395 contracts). Digit-suffix
  detectable; the census session's exposure is 225 roots / 1,897 prints / $7.2M = 0.028% of
  session premium. **v6's "bounded, closed" was a one-session claim over-generalized —
  corpus-wide the tail is NOT small: WOLF1 alone is $6.99B = 10.61% of session premium on
  2025-12-19 and $2.54B on 2026-01-16, OPEN1 $1.72B on 2025-11-21, and digit roots exceed 1%
  of session premium on 4 sessions (`db/flow_corpus.py:57-64` records the same). All four are
  outside the judgment window, so "bounded" is a JUDGMENT-WINDOW statement; every table
  reports its own digit-root premium share.**

## G.9 (1.23) The noise floor is a curve; grading divides by it, per tier

- **(a)** No pooled grading with kill authority (tiers differ ~4.4×; flat floors over-penalize
  thin names ~3.5×).
- **(b) Dilution, reporting-only:** `q̂ = f₀/f`, `lift_real = lift_observed / (1 − q̂)`; the
  t-statistic is computed on the uncorrected series (G.4, G.2 step 3); q̂ fixed, rescales the
  point estimate and its reported interval only. q̂ is an upper bound on the noise share (true
  share = (1−π)·f₀/f), so `lift_real` is an **upper bound in magnitude — reporting-only; it
  enters no gate.** Uncertainty enters at ONE place: **the dilution gate's
  lower 99% confidence bound on `(f − f₀)` from a joint bootstrap — parameterized, since this
  gate carries DEAD authority on its own: 100,000 replications, seed 20260812, percentile
  interval, LCB = the 1st percentile of the resampled `(f − f₀)` (one-sided 99%); `f`
  resampled by moving block (length 10, matching G.4's dependence model) over the
  judgment-window event-date series; `f₀` by a two-stage resample — the 20 null draws with
  replacement, then tested name-days iid within each draw — both on the judgment window.
  Pre-run precision record (obligation 3): the gate's minimum resolvable difference at these
  exact parameters; unable to
  resolve 1.0pt ⇒ the gate routes to UNEVALUABLE (G.2 step 1), never DEAD.** Assumptions:
  fired name-days partition into f₀ noise + (f − f₀) real; noise fires behave like the
  no-signal baseline (117).
- **(c) The table of record — re-emitted before the scoring run on the registered pipeline of
  G.2, and the build is specified exactly:** the change lives inside
  `order_grain_null._extract` — join per-print condition labels exactly as the rollup does
  (`tools/condition_labels.py` via the `flow_corpus_rollup.py` wrapper), apply the §1.25
  clause-1 spread gate (`SPREAD_MAX_FRAC = 0.50` — ABSENT from the current extractor's
  `good_q`, so the existing table is **v6-fence semantics** and is relabelled as such), mask
  package-coded prints, restrict the panel to the cell's registered EQUITY universe (G.8
  classification + mapping, per G.2's universe coordinate), THEN re-group orders — **the exact per-ORDER ±1 shuffle is retained**
  (an aggregate/normal draw is the approximation A2 measured ~4× wrong in Q1 — forbidden);
  order-level package exclusion = drop package-coded PRINTS per F.1's per-print adoption, then
  re-group the surviving legs. The $250k floor lands in `base_rate_table.py` (`eligible`,
  :208 — currently deliberately no-floor). Extract cache versioned to a NEW directory so the
  fenced re-emission can never silently reuse unfenced npz. **Panel lead-in registered: the
  panel spans ≥ 60 sessions before 2026-06-01 (quintile + presence warm-up), tallies MASKED to
  the 37 judgment dates — the same window as G.2, 2026-07-23 excluded** (the existing `masks`
  machinery; v6's "38" was the stale pre-exclusion count, and because this mask defines the
  universe `f` and `f₀` live on, it is a registered quantity, not prose). Same construction otherwise:
  trailing-60 quintiles on classified premium, ddof=0, seed 20260729, 20 draws, one run, first
  run governs. Full-corpus v6-fence table = context only (Q1 1.961% … Q5 8.629%, pooled
  6.612%; stationarity v6 ALL 0.29pt / v7-line 0.63pt arm-unnamed). Expectation pre-stated:
  under the floor, Q1 (0.105% of name-days admitted) and likely Q2 unevaluable in ALL too.
  `f` measured on the identical pipeline/window, recorded pre-run.
- **(d)** Power per tier. Pre-declared: SHORT_OTM Q1/Q2 unevaluable (Q1: 34 real name-days; SE
  2.73pt on 2.353%), never merged upward.
- **Deliberately NOT done:** no per-tier re-thresholding; revisit after the first grade.

## G.10 What would change this (new addendum, as always)

Changing the primary cell's coordinates or selection rule, the horizon of record, the
inference estimator or calibration parameters, the root classification, the table of record,
or the dilution convention — each is a new dated addendum. **"Touching the test split" =
computing any outcome statistic on test-split events**; data acquisition, corpus rolls, and
input-property measurements are not touches. Disclosed prior reads that stand: 2.4's
June-close reads, 1.14f's outcome-store fetch. An outcome statistic on the split before law ⇒
void, recorded.

**Pre-run obligations, IN ORDER, each recorded, first run governing (scoring run no earlier
than the close of 2026-08-07):**
1. `FLOW_1_14F_FINDINGS_REGISTRATION.md` committed law.
2. The G.8 snapshot generated from the COMMITTED directory copy
   (`signals/options_flow/nasdaqtraded_20260730.txt` — the registered vintage; the URL is
   provenance only) — ETN list + delisted census list + **successor map (the registered
   Alpaca corporate-actions pull per G.2's terminal-value rule, date-scoped, frozen as part
   of the snapshot JSON)** + **test-root list (directory `Test Issue = Y` rows + OPRA-only
   test roots the census surfaces)** + **index-addition list (both index-miss instruments
   recorded, per G.8)** + LEGACY_ETF_SET — plus the **bar backfill for dotted class-share
   forms and mapped successors still bar-less** (data acquisition, not a touch). **Tooling
   note, recorded so this obligation is not misread as pure data acquisition:**
   `tools/flow_corpus_bars.py` derives its universe from OPRA definition files and its
   `isalnum()` filter (`:57`) drops dotted forms, with no symbol-list override — the backfill
   needs a small tool extension, and the READ side needs the three-way lookup (root → dotted
   form → date-scoped successor) or bars stored under `BRK.B` stay unreachable. Measured
   scope at drafting: the five v6-mapped successors already carry full bars; the mechanical
   work is the four dotted forms plus whatever the full sourced map adds.
3. The G.9c re-emission built and run (fenced extractor — `fence_and_classify()` +
   `multileg_flags()` from `tools/flow_corpus_rollup.py`, the tested v7 functions, never a
   fifth hand-copy — versioned cache, floored, masked to the judgment window) + per-tier `f` +
   the null-usability criteria (across-draw SE ≤ 0.5pt — a NEW registered criterion,
   numerically equal to the 1.7 B3 reporting threshold — and ≥ 1,000 DISTINCT tested
   name-days) + the dilution gate's PER-TIER precision record. **3b: the 2.4 census counting
   stage re-run on the registered pipeline, logging the f_open-ranked share — the tier is what
   the registered rule selects (expectation: Q5). Pinned so no choice is made at run time: the
   re-count runs on the same event span 2.4's census used (2025-07-28 → 2026-05-29 —
   outcome-blind either way); ties in the max-per-quintile-min rule break toward the HIGHER
   quintile. Disclosed: the rule's min-of-open/close-sides form is inherited from 2.4 even
   though the primary cell uses no open/close split — kept because changing the rule's form
   now would be a post-hoc selection-rule edit.**
4. *(reserved — merged into 5/6 ordering below)*
5. The G.2 step-1 input-property records: complete-or-terminated date count + calendar-gap
   vector, event count, regime-span composition on the cell's dates, truncated/ungradeable
   census, arm-split unclassified shares.
6. The G.4 size calibration + MDE record, computed AT the recorded date count and gap vector
   from obligation 5.
Classification-lab work is independent and does not gate this.
