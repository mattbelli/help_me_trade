# Addendum G — the judging-rules amendment (roadmap 1.15–1.23, hosting 1.4 and 1.5)

*⚠️ DRAFT v11 — NOT LAW until the gauntlet converges. Blockers by round: v1 18, v2 16, v3 11,
v4 13, v5 9, v6 7, v7 9, v8 10, v9 6, v10 1 (0 methodology — verdict: "Converged… let it
become law" — + 1 record: a single v10-introduced digit, $161.6M → $153.8M). All resolved
below; v11 = v10 + that digit + round-10 nit closures, pending a targeted diff verification.
Orchestrator, 2026-07-31.*

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
reference** (clause 1 wide-print rule implemented at `SPREAD_MAX_FRAC = 0.50`; clause 2 retired;
clause 3 still open and unimplemented — G.2's RAW-tilt coordinate relies on exactly that).
Scope note on the supersession list: it names stale ROUTINGS only — the roadmap's five
descriptive mentions of the real 07-28 Addendum F (160, 522, 571, 1531, 1533) are accurate and
untouched.
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
(`:45-48`); no other DTE gate. **Direction:** production requires ≥ `PRESSURE_MIN_CLASSIFIED =
0.5` of cluster premium classified else `undetermined`, clustered per contract; the
reconstruction grades direction by the Addendum A midpoint rule — parameter-equivalent, with the
direction source stated, not assumed identical. **Universe:** the live lane consumes the
~30–40-name flow watchlist; the reconstruction applies these parameters whole-market (universe
per G.5). The primary verdict does NOT require beating baseline #4 — a named amendment of
base prereg 121-122's "minimum bar" (G.2's primary cell carries survival authority).

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
`f` and `f₀` both live on exactly this universe (G.9b/c). **One implementation emits the
fired/tested sets for `f`, `f₀`, the event cohort, AND the baseline — the
`order_grain_null._fire` conventions as driven through `base_rate_table` /
`open_close_split.py:121`: trailing-20 presence counts ANY classified-premium day; the $250k
floor gates only the EVENT day** (a fresh grader applying the floor to presence too would
silently put the cohort and `f₀` on different universes — named so it cannot happen).

**Coordinates:**

- **Contract arm: ALL** (base prereg 76-80 registers ALL and SHORT_OTM as two reported arms;
  **designating ALL as the kill-authority arm is a registration decision made here, now,
  pre-outcome** — SHORT_OTM's Q1/Q2 have no usable null (G.9d) and its Q3–Q5 cells grade as
  labelled exploratory).
- **Root universe: EQUITY-classified roots only (G.8 fence, mapping applied) — a registered
  coordinate, previously implicit.** The metric requires underlying bars; cash-index roots are
  structurally bar-less (`no_spot_index` — measured at drafting on the registered 37-date
  window: **37.7% of judgment-window CLASSIFIED premium, $624.6B of $1,655.7B** (the
  registered operative base); on the all-sides base 37.2%, $672.9B of $1,806.7B — bases
  labelled because v8 mixed them; v7's $687.2B/$1,852.8B
  was the stale 38-date ALL-SIDES measurement, corrected here; `no_spot_index` premium equals
  `INDEX_ROOTS` premium exactly on BOTH bases, so the bucket/root identification is sound)
  and would
  otherwise route silently to UNGRADEABLE — they,
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
  (`open_close_split.py:68`) — which also carried index/ETF roots that the registered EQUITY
  coordinate removes, shifting the cross-sectional quintile cut-points, so the quoted counts
  will NOT reproduce on the registered pipeline (part of why 3b re-counts) — and gave Q1 0/1,
  Q2 4/3, Q3 41/34, Q4 189/179, **Q5 192/192** —
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
  1)] × open_{d+1} / ATR14` — beta = 1 by registration; entry at the next session's
  open per base prereg 97/103. **ATR14 pinned:** `TR_t = max(h_t − l_t, |h_t − c_{t−1}|,
  |l_t − c_{t−1}|)`, SIMPLE (unsmoothed) mean over the 14 sessions ending at the event date
  `d` INCLUSIVE — **a named amendment of base prereg 37's "as of the day before the event"**,
  adopted eyes-open: including `d` was measured on the training window (top-600 equity roots,
  registered z rule, $250k floor; **convention: ratio = ATR14 including `d` ÷ ATR14
  excluding `d`**) at 0.9970 on |z| ≥ 2 name-days vs 1.0051 sub-threshold
  — event denominators shrink and baseline denominators grow, nudging the contrast WIDER
  (easier) by under 1% (draft v9 stated the direction backwards; corrected — the effect is an
  order of magnitude below the MDE grid step and is disclosed, not defended). Fewer than 15 bars
  through `d`, or ATR14 ≤ 0 ⇒ terminal-value case (iii) —
  symmetric on both arms, pinned because it is the metric's denominator. Event observations are signed by `sign(z)` per base prereg 106;
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
- **Terminal-value rule (roadmap 1.4), FOUR registered cases:** (i) a name whose bars STOP
  mid-horizon — with at least one bar at or after `d+1` — stays in the cohort, flagged
  `truncated_horizon`, graded at last available close over the truncated window, SPY
  same-window, same ATR14 (disclosed bias: ~√(d/10) shrink
  toward zero for DIFFUSIVE returns — the truncated set is jump-dominated (M&A/halts), where
  the move may already be realized inside the truncated window, so the shrink is an
  approximation, not a guarantee; symmetric across arms; count + premium share reported). **Unavailability
  of BENCHMARK (SPY) bars is NEVER a truncation trigger: SPY is named in obligation 2's
  acquisition scope, and a missing benchmark bar is a blocking data fault that STOPS the run**
  — otherwise a short benchmark series would silently shrink every late-window observation on
  both arms. **Neither (i)
  nor UNGRADEABLE may be assigned from the LOCAL bars store alone: the vendor, not the store,
  is the authority** — the measured store fault is BLD (an NYSE
  equity trading through 2026-06-30: 0 local bars while the VENDOR serves 21 bars for
  June — vendor-verified in round 9; $57.1M classified / 7 eligible name-days in-window,
  $59.0M/10 all-sides; draft v9 also listed TMHC and NUVL here, but the vendor matches the
  store on both — they are genuine terminations, struck from the store-fault list and kept
  below as terminal-value examples), and a coverage hole's bias is non-random by name,
  unlike (i)'s disclosed shrink. Both flags require a vendor re-fetch returning nothing first
  (obligation 2's backfill, extended below, is where that happens). (ii) UNGRADEABLE by the
  ZERO-BAR route applies
  when the identity's **resolved terminal symbol and its dotted class-share form** both
  return zero bars after that re-fetch ("only" scoped to this route — cases (iii)/(iv) also
  assign UNGRADEABLE). (iii) a name-day whose resolved bar series carries
  **fewer than 15 bars THROUGH the event date `d` (14 sessions before `d` plus `d` itself =
  14 true ranges), no bar AT `d` itself, or ATR14 ≤ 0**, is UNGRADEABLE by the same
  census — the metric's denominator is otherwise undefined ("no bar at `d`" pinned so a
  series ending before `d` routes here deterministically rather than splitting readings with
  (iv)). Measured instances, windows named: SPCX's first bar is
  2026-06-12 with eligible flow from 2026-06-16 — 2 bars before the event date; no SPCX row
  exists between 04-06 and 06-16 — a JUDGMENT-window case (roughly the first dozen of its 26
  eligible name-days route here); PSKY's bars begin at its 2025-08-07 listing, so
  PARA's 8 name-days, $132.9M classified, route here — that clause is calibration-window
  only; both are symmetric across arms, and the 242-row completeness fact
  is this rule's caveat, not its reassurance.
  **(iv) a name-day with NO bar at `d+1` under the resolved terminal symbol is UNGRADEABLE,
  counted with premium in the same census** — without this case the delisted-in-horizon
  population (the M&A/halt tail the signal should be richest in) would land
  in (i) with a zero-length window and no `open_{d+1}`,
  where the metric is undefined and three readings were available. Instance shape, stated
  precisely: NUVL (bars end 2026-07-14) and TMHC (07-23) — both vendor-confirmed genuine
  terminations — truncate under (i) with 1–3-session windows for events shortly BEFORE their
  last bar; (iv) catches any name-day AT or beyond the final bar, the state (i) cannot grade. **Order and indexing
  pinned: cases are checked (ii) → (iii) → (iv) → (i), each name-day tallied exactly once;
  `d+k` means the k-th session after `d` in the registered BENCHMARK (SPY) session calendar**
  — and an interior bar hole (empirically empty: 0 interior gaps across 5,939 symbols × the
  corpus span), including a bar missing at `d+10` with later bars present, is a BLOCKING
  data fault referred to the obligation-2 vendor re-fetch, never a gradeable state. All four cases apply
  IDENTICALLY to the event cohort and the baseline (G.4). **Restated-history disclosure:**
  under a merger continuation the predecessor's prices ride the successor symbol restated by
  the exchange ratio, which cancels in `close/open` and again between `open_{d+1}` and ATR14 —
  the metric is ratio-invariant, so restatement is benign (residual: a cash consideration leg
  is invisible to bars; negligible in this window, disclosed).

  **Identity semantics, registered in TWO directions (v7 registered only one and was wrong in
  the other):**
  - **FLOW identity is date-scoped:** rows with `date < process_date` belong to the
    predecessor segment, `date ≥ process_date` to the successor segment — the interval is
    closed on the successor side, so the seam session itself is assigned exactly once; both
    segments are ONE identity, and presence (15-of-20), trailing-60 quintile ranks, and
    z-scores run on the merged series. **Ordering pinned (the overlap test and the reuse
    split would otherwise fire on the same pair — SPCX is exactly that case and it is not
    marginal: $13.9B classified across 26 eligible name-days in-window, the 12th-largest
    equity root there): the reuse-SPLIT list is constructed FIRST, and the overlap test then
    applies PER SEGMENT — an overlap arising from a registered split is not an exclusion.** A
    pair whose corpus rows still OVERLAP on any date after splitting is
    excluded from the map and recorded (obligation 2 checks every pair; the same pass also
    checks map TOPOLOGY — two old roots resolving to one terminal symbol would duplicate the
    successor's post-seam name-days across two identities; any such collision is recorded
    and resolved before the map freezes; the two seams
    measured at drafting — SATS/ECHO 06-23→06-24, PARA/PSKY 08-06→08-08 — are clean).
  - **PRICE/BARS/CLASSIFICATION reads are NOT date-scoped:** a merged identity resolves to
    its TERMINAL symbol (the last in its rename chain at the obligation-2 pull) and the
    ENTIRE bar series, ATR14, spot, and G.8 classification are read there — restated history
    rides the new symbol and the predecessor symbol returns zero bars by construction, so a
    date-scoped bars read would have routed every pre-seam predecessor day to UNGRADEABLE
    (SATS's own 16 in-window name-days, $839.7M classified / $946.4M all-sides — the largest
    RENAME-driven bar-less mass in the window; BRKB's $1,048.5M classified / $1,118.4M
    all-sides ticker-format hole is larger on either base
    but is covered by the dotted-form backfill — under v7's wording). The identity classifies
    ONCE, under the terminal symbol.

  **The successor map is SOURCED, not inferred — v6's frozen five-pair hand list is
  superseded** (measured at drafting: **106 renames whose old root carries ≥ $250k of TOTAL
  (all-sides) corpus flow premium** — the base is pinned here because the map-scoping filter
  is not a downstream grading quantity, and the classified base yields 97, not 106 — not
  five; count independently reproduced in rounds 7 and 9). Registered source:
  **Alpaca corporate-actions `name_change` records** (`data.alpaca.markets/v1/
  corporate-actions`, `types=name_change`, span 2025-07-25 → the obligation-2 run date),
  filtered to plain alphabetic old/new roots with corpus flow on the old side **and
  `old ≠ new`** (32 records are CUSIP-only changes with an unchanged symbol — without this
  filter the count reads 138, with it exactly 106), plus one
  merger-sourced continuation absent from the name-change feed (PARA→PSKY, corpus-seam
  verified). **Adjacency/seam inference is FORBIDDEN as a map source:** cross-checking the
  authoritative records refuted four of the nine seam-inferred candidate pairs proposed in
  review, correcting five old-root assignments
  (ERJ→EMBJ, not RKLX; HOND→IMSR, not NUAI; COMM→VISN and MNMD→DFTX — two renames one day
  apart, cross-paired, one refutation correcting two roots; BYON→BBBY, not RR). v6's five
  instances all reconfirm under the
  authoritative source.

  **Symbol REUSE requires a registered SPLIT, not just a scoped map:** the corpus `underlying`
  key and `equity_bars.symbol` are both non-point-in-time, so a reused symbol's rows share one
  presence count, one z baseline, and ONE bar series that belongs to at most one issuer
  (measured: ARIS was vacated 2025-10-14 and re-occupied 2026-02-19 by ARMN, yet carries a
  single continuous 251-row bar series — the NEW issuer's restated history; GOLD's vacating
  predates the corpus, but its 251 bars under the reused symbol likewise belong to the current
  issuer, not whoever's flow printed there earlier; SPCX shows the third shape — renamed away
  2026-04-07 with the symbol re-listed mid-June and flow continuing after `process_date`).
  Obligation 2 therefore also freezes a **date-scoped split list** from the same records: a
  `name_change` whose NEW symbol matches earlier corpus rows, or whose OLD symbol keeps
  printing after `process_date`, creates a split at that date — the earlier segment is a
  distinct identity that resolves through ITS OWN chain's terminal symbol, or, if
  unresolvable, is excluded and counted with premium by the normal ungradeable census.
  **Registered residual, known and unmitigated:** a reuse whose vacating produced NO
  `name_change` record (bankruptcy/liquidation followed by ticker reassignment) is
  undetectable by this source — no split fires, and the terminal-symbol read then prices the
  earlier occupant's flow off the current occupant's series, silently wrong rather than
  loudly ungradeable. Every reuse measured in THIS corpus (ARIS, GOLD, SPCX) does carry a
  record, so the machinery covers every known case; the residual is disclosed, not solved.

  Bankruptcy renames (Q-suffix terminal symbols, mostly bar-less) map, re-fetch, fail the bar
  test honestly, and stay in the ungradeable census by the normal rule. Obligation 2's **bar
  backfill covers: the dotted class-share forms (BRK.B, BF.B, HEI.A, PBR.A — BRKB has 0 bars
  under both forms today), any mapped successor still bar-less, AND every equity-classified
  root with an eligible name-day in the judgment or calibration window whose bars are absent
  or end before the run date** — data acquisition, not a touch. What remains after mapping +
  re-fetch (e.g. WBA, taken private) is the true ungradeable set: excluded, counted per
  session with premium via the per-root `no_spot_missing` AND `no_spot_index` breakdowns —
  **per-root, never the bucket** (the `no_spot_missing` bucket range at its cited
  2026-07-27/138-session vintage is $41.3M–$7,196.6M/session, mean $256M; on the full
  253-session v7 corpus it is $10.1M–$7,196.6M, mean $176.8M — either way the tail is
  adjusted contracts, which G.8's `adjusted` class now removes; the earlier $54M–$574M/
  37-session figure is retracted in the record) — recorded pre-run (obligation 5).
- **Data vintage:** v7 corpus of record; Addendum F's ≥98% subset = robustness arm.

**Verdict — ordered checklist; every input combination lands exactly once:**

1. **Evaluability:** ≥ 20 distinct complete-or-terminated event dates AND ≥ 30 events in the
   cell; the null tier usable — **registered criterion: across-draw SE ≤ 0.5pt (a NEW
   registered criterion, numerically equal to the 1.7 B3 reporting threshold, per obligation
   3; "across-draw SE" pinned = the sd over the 20 draw-level f₀ values — B3's implemented
   quantity, `base_rate_table.py:234`, with NO ÷√20: v8 pinned the ÷√20 form — under EITHER form the criterion sits far under
   the bar on populous tiers (the implemented no-÷√20 SEs on the full-corpus ALL arm average
   ~0.05pt; ÷√20 would read ~0.01pt) and binds only on thin tiers (SHORT_OTM Q1: 2.73pt) —
   a gate that cannot fail there is not a gate; the dilution gate's precision record is the
   quantitative protection either way) AND ≥ 1,000 DISTINCT
   tested name-days in the tier (pinned: the UNION of tested name-days across the 20 draws —
   `tested` varies slightly by draw)**, both evaluated at obligation 3; the
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
   zero **when the gate's recorded precision can resolve 1.0pt**, ⇒ **DEAD (dilution)**.
3. **The test:** the UNCORRECTED per-event-date difference series (G.4) against the calibrated
   one-sided cutoff — pass ⇒ **ALIVE**; fail ⇒ **DEAD (test)**.

**The two DEAD paths are logically different claims and carry SEPARATE registered wordings,
never interchanged:** **DEAD (dilution)** = "the event rule fires no more often than the
order-sign null; NO claim is made about the fired set — two rules can fire at identical rates
and select entirely different name-days, and the null's iid-across-days construction is a
known confound against the real tilt series' temporal dependence (a persistent tilt level
shrinks the trailing std and inflates z)." Obligation 3 records the real-vs-null per-name
tilt AR(1) and dispersion on the training window so that confound's size is on the face of
the record. **DEAD (test)** = the dose-response wording below. **Pre-run likelihood
disclosure (round-9 review; input-side firing rates only — no outcome statistic touched):**
on an APPROXIMATION of the registered pipeline (v7 rollup, no successor map, v6-fence null),
judgment-window Q5 `f` measured ≈ 7.9% against the v6-fence `f₀` = 8.6%. Not comparable as
registered — different fences, and both sides are re-measured at obligation 3 — but the
dilution gate is plainly LIVE: **DEAD (dilution) is a realistic outcome of this run, not a
tail case**, which is exactly why its wording above claims no more than the gate tests.

Everything else is exploratory, labelled, FDR reported, no survival authority (172-173). No
partial rescue. **Every DEAD verdict prints the recorded MDE beside the observed effect** — a
DEAD at MDE near the ceiling mostly reflects power, and the record must show it on its face.
And the verdict label carries its contrast: the baseline is signed sub-threshold days, so
**DEAD (test) reads "no dose-response over sub-threshold days," never "events carry no
alpha"** (DEAD (dilution)'s separate registered wording is at step 2).

## G.3 (1.17) Horizon of record = 10 trading days

Grid 3/10/21/45 frozen and fully reported; only 10td confirms or kills. At the 37-session
judgment window the 21/45d columns hold ≈ 1.8 / ≈ 0.8 independent observations (the
roadmap's "2–4" was a TWELVE-MONTH figure, mis-applied in earlier drafts, corrected here) —
reported, non-authoritative; 3d likewise. **Stated plainly rather than left for a reader to
notice: by the same arithmetic the 10td primary column holds ≈ 3.7 independent time-blocks —
which is exactly why block 10 = H, lags 9 = H−1, the POWER-LIMITED route, and the
obligation-6 MDE record exist; that record carries the registration's credibility.** **At the earliest
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
  excluded by the same four-case terminal-value rule whether it sits in the event cohort or
  the baseline —
  delisting/M&A moves enter both arms or neither. Named plainly: an asymmetric truncation rule
  was the one channel in this design that could carry a null to ALIVE, leaking in exactly the
  direction flow is supposed to predict; symmetry closes it.
- **Inference:** collapse to the recorded date series; **Newey-West HAC, 9 lags** (≡
  Driscoll-Kraay on a scalar equal-weighted series; 9 = H−1), autocovariances by **SPY-SESSION gap** (sessions of the registered benchmark calendar —
  every "calendar-gap vector" in this document means this) — **written as a formula, since the date series is gappy and the normalizer
  was otherwise unpinned:** with `x̃_t = x_t − x̄`,
  `LRV = γ̂₀ + 2·Σ_{h=1..9} (1 − h/10)·γ̂_h`, where
  `γ̂_h = (1/G)·Σ_{(t,s): gap(t,s)=h, t>s} x̃_t·x̃_s` — **each unordered pair counted exactly
  ONCE (`t>s`); `γ̂₀ = (1/G)·Σ_t x̃_t²`** — a symmetric distance-matrix implementation that
  sums both (t,s) and (s,t) doubles every off-diagonal term against the explicit 2· prefactor
  (the 1/G normalizer regardless of the pair
  count at gap h; a gap with zero pairs contributes γ̂_h = 0; pairs at gaps > 9 are dropped);
  the test statistic's denominator is `sqrt(LRV/G)`. Identical formula inside every
  calibration replicate. **Fallback, written as a formula (v7's prose admitted readings differing by
  a factor of G):** if the Bartlett sum returns a non-positive variance, then for the series
  in hand `x_1..x_G`, `LRV = Var(x, ddof=1)` (the iid value — conservative exactly when the
  Bartlett sum fails, i.e. under estimated negative autocorrelation) and the test statistic's
  denominator is `sqrt(LRV / G)`. **The fallback is computed on the SERIES IN HAND: each
  replicate's own resampled series inside the calibration, and the REAL judgment-window
  series in the real run — never a training-window constant** (v7's "identical in the real
  run" wording would have let a training-era scale set the real t, an unbounded NULL→ALIVE
  channel gated only on the Bartlett sum's sign).
- **Critical value — size-calibrated:** tabulated `t_{0.99, G−1}` is INVALID at this bandwidth
  (type-I ~4–10%, measured by simulation during this addendum's review, no artifact retained; the recorded
  calibration supersedes that estimate). **Registered procedure, every parameter fixed: the
  TRAINING-window — dated: 2025-07-28 → 2026-03-31 (base prereg 134 registers a 2025-07-25
  start; the corpus's first session is 07-28, so the effective series starts there — the
  attribution is to 134's END date, not its start; excluded: the registered validation split
  2026-04-01 → 2026-05-31
  per base prereg 135 — last corpus session 2026-05-29, session-equivalent — and the test
  split). Disclosure: roadmap 2.4 used a different "training" span (through 2026-05-31) —
  two conventions were live in the record, and this pin is made with training-window outcomes
  already seen (no calibration window exists whose outcomes were unseen; the ORIGINAL
  registered split is the narrower, pre-existing choice; further disclosed: 2.4's graded run
  published bootstrap intervals on the same tier, horizon, and ATR units, so the dispersion
  the shape parameters are calibrated against — block 10 = H, lags 9 = H−1, both principled
  and stated — was already in the record). The calibration series is built at
  the SAME registered coordinates as the cell — pipeline, equity universe, selected tier, ALL
  arm, RAW, midpoint, fenced — as per-date event-minus-baseline differences per the estimand
  above, **on the training dates carrying ≥ 1 gradeable event in the cell** (the difference
  is undefined elsewhere). **The calibration panel starts at the corpus's first session — no
  60-session warm-up exists before it; dates whose trailing-60 quintile lookback is short are
  RETAINED** (drafting-approximation impact: Q5 membership ~777 vs ~788 names/date, ~50 vs ~53
  events/date — small either way; the registered numbers come from the obligation-3
  pipeline, and the literal reading is now the registered one). That** per-date difference series is DEMEANED TO ZERO, then moving-block
  bootstrapped — block length 10, 100,000 replications, seed 20260810; **block assembly
  pinned: CIRCULAR blocks (the series wraps, so endpoints are not under-weighted), draw
  ⌈G/10⌉ blocks by uniformly random start position, concatenate, truncate to length G** — at
  the RECORDED complete-or-terminated date count G and calendar-gap vector (obligation 5
  precedes obligation 6); each replicate's statistic is `mean_b / NW_SE_b` at 9 lags on the
  assigned gap vector; the IDENTICAL variance rule applies inside every replicate and none is
  dropped (a replicate whose Bartlett sum fails uses its OWN series' `Var(x_b, ddof=1)` per
  the fallback formula above);
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
evidence. Context: the v7 corpus carries **6,999 distinct roots** (1.7's probe used 6,998);
the earlier "~5,554" figure is the EQUITY-RETURN correlation universe (roadmap:22), not the
option-active one — mislabelled in v7, corrected here; its $100k floor keeps 1,437; top-~1,000
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

Replaces the three incompatible lists. **{adjusted, test, index, etf, equity}, in that
precedence, the only fence any tool may use** (`adjusted` and `test` are EXCLUDED classes —
counted with premium, never arms, never universes):

- **Mapping first:** a root with TRAILING DIGITS classifies **`adjusted`** — a registered
  class of its own, EXCLUDED from the equity universe and every arm, counted per session with
  premium (v7 stripped digits and classified the plain root, which was ambiguous between two
  bad readings: classify-then-ungradeable — measured: at least seven digit roots clear the
  $250k floor in-window with zero bars of their own — on the registered classified base
  BABA2 $58.3M/17 name-days, FDX1 $29.3M/23 (all-sides: $61.0M/18, $35.2M/24), EOSE1, SOXS2,
  HON2, SPGI1, DVN1 — or an identity merge that folds
  multiplier-inflated premium into the plain root's tilt and z; the 1.5 bound below already
  says adjusted premium is not comparable, so exclusion is the only consistent disposition; 0
  of 13,074 directory symbols contain a digit, so no legitimate directory root is lost).
  Then: normalize `.`/`$` suffixes in BOTH directions (OCC `BRKB` ↔ directory
  `BRK.B` — the roadmap §1.2/§1.4 known offender), and resolve the **registered successor
  map + reuse-split list** per G.2's terminal-value rule (Alpaca corporate-actions
  `name_change` records + the one merger-sourced continuation; flow identity date-scoped,
  bars/classification read at the TERMINAL symbol; generated and frozen into the snapshot at
  obligation 2 — never hand-listed, never seam-inferred). A normalized root matching NO
  directory row stays `unclassified` — never guessed toward a near-miss row (CMCS1 is now
  `adjusted`; a hypothetical bare CMCS would stay unclassified, not become CMCSA).
- **test** = directory rows with `Test Issue = Y` (33 in the snapshot, all ETF = N) ∪ the
  frozen `test_roots` list of OPRA-only test symbols absent from the directory (e.g. ZTST —
  enumerated at obligation 2 from the zero-bar census). Test roots print real corpus premium
  (measured, ALL-SIDES base: $280.8M across the 12 known so far — $153.8M classified — of
  which CBX $38.5M, CBO $37.3M, CTEST,
  ZAZZT, ZJZZT, NTEST are DIRECTORY Test-Issue rows, and ZTST $28.6M is the one confirmed
  OPRA-only member; the census also surfaces PTEST and ZZK as directory-absent test-shaped
  candidates for the frozen list — v8's illustrative list mis-assigned the directory rows to
  the OPRA-only side; the union was unchanged, the frozen membership comes from the snapshot
  enumeration) and are excluded everywhere.
- **index** (checked next) = the 18-root `INDEX_ROOTS` (`db/flow_corpus.py:91-94`) **∪ a
  registered index-addition list frozen in the snapshot**; supersedes
  Addendum B's 9-root list and (with etf) replaces base prereg 163-164's arm list. **Pre-run
  check, recorded, TWO instruments (the top-20 check alone cannot see small index roots —
  census review at drafting surfaces XAU, NANOS, DJXW as sub-top-20 index-product candidates,
  plus **CBTX — the largest single disposition item: $1,704.9M all-sides corpus premium
  ($1,682.4M classified) over 193
  sessions, $338.9M all-sides ($332.7M classified) in-window, zero bars, directory-absent;
  identified in review as a
  when-issued artifact and disposed BY NAME at obligation 2, together with its sibling
  CBTXW and the other `…W` when-issued/warrant tickers**):
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
  `ETF = N` and no ETN match (test rows never reach here — the `test` class precedes);
  (layer 2) **delisted/corporate-action roots = roots with an eligible name-day in the
  judgment or calibration window that match NO directory row — the G.8 `unclassified`
  census, already computed — reviewed and frozen as a dated list inside the snapshot.** The
  review ASSIGNS each frozen root to its correct class — a directory-absent root that was an
  ETF/ETN/warrant/when-issued instrument classifies THERE, never blanket-tagged
  `delisted → equity` (measured: the judgment window admits zero such ETFs; the calibration
  window contains ALTS $62.8M, SPLG $10.8M classified, XHYE, LQIG — all delisted funds the
  review must route to `etf`). **Re-sourced at v9: v8 fed this layer from `no_spot_missing`, which
  structurally cannot see a mid-window delisting that HAS local bars** (NUVL and TMHC
  end in-window with bars present, so they never enter a no-spot bucket; MVO's bars run to
  the store's last date — its relevance is directory-absence WITH local bars, not an early
  stop) — and since the
  directory snapshot post-dates the judgment window, directory-ABSENCE, not spot-absence, is
  the correct instrument; without this layer the universe would silently drop names that
  delisted inside the window, non-randomly, on the M&A/bankruptcy tail the signal should be
  richest in. Measured residual, derivation written out (v9 stated only the endpoint): the
  in-window directory-absent census is $1,300.2M classified across 25 roots; after the
  successor map recovers SATS ($839.7M) and CBTX is disposed ($332.7M classified) it is
  $127.9M; after obligation 2 dispositions the index-addition candidates (XAU, NANOS, DJXW),
  the when-issued/warrant siblings (CBTXW, MBTXW, MGTNW/MGTN), and backfills BLD, the true
  equity residual is **~$24.8M against $757.9B of equity classified premium (0.003%)** —
  small, and now disclosed
  rather than silent. (The equity-bars
  store CANNOT serve this layer either: delisting is why a live bars API returns nothing —
  verified: WBA/PARA/SATS/FI have 0 `equity_bars` rows; the repo fixed this same conflation
  once already, `multileg_arm_probe.py:75-77`.) **unclassified := matching none of the
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
  corpus-wide the tail is NOT small: WOLF1 alone is $6.99B = 10.61% of CLASSIFIED session
  premium on 2025-12-19 and $2.54B on 2026-01-16, OPEN1 $1.72B on 2025-11-21, and digit roots
  exceed 1% of classified session premium on 4 sessions (5 on the TOTAL base;
  `db/flow_corpus.py:57-64` records the same phenomenon on the total base — $7,082M / $2,740M
  / OPEN1 $2,188M — the two bases differ by up to 27% per root and the base is named here
  because total-vs-classified is itself a named amendment in G.2). All are
  outside the judgment window, so "bounded" is a JUDGMENT-WINDOW statement; every table
  reports its own `adjusted` premium share.**

## G.9 (1.23) The noise floor is a curve; grading divides by it, per tier

- **(a)** No pooled grading with kill authority (tiers differ ~4.4×; flat floors over-penalize
  thin names ~3.5×).
- **(b) Dilution — the reporting-only correction, plus a parameterized GATE (v7's heading
  said "reporting-only" three sentences before granting the gate DEAD authority — retitled):**
  `q̂ = f₀/f`, `lift_real = lift_observed / (1 − q̂)`; the
  t-statistic is computed on the uncorrected series (G.4, G.2 step 3); q̂ fixed, rescales the
  point estimate and its reported interval only. q̂ is an upper bound on the noise share (true
  share = (1−π)·f₀/f), so `lift_real` is an **upper bound in magnitude — reporting-only; it
  enters no gate.** Uncertainty enters at ONE place: **the dilution gate's
  lower 99% confidence bound on `(f − f₀)` from a joint bootstrap — parameterized, since this
  gate carries DEAD authority on its own: 100,000 replications, seed 20260812, percentile
  interval, LCB = the 1st percentile of the resampled `(f − f₀)` (one-sided 99%); `f`
  resampled by moving block (length 10, G.4's assembly: circular, ⌈G/10⌉ draws, truncated
  to G) over the
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
  panel starts EXACTLY 60 sessions before 2026-06-01 (quintile + presence warm-up; EXACTLY,
  not ≥, because `base_rate_table` seeds per session by POSITIONAL index —
  `default_rng([SEED, d])` — so the panel start defines the entire random stream and a
  different lead-in silently changes every draw), tallies MASKED to
  the 37 judgment dates — the same window as G.2, 2026-07-23 excluded** (the existing `masks`
  machinery; DRAFT v6's "38" was the stale pre-exclusion count ("v6" names the draft here, the corpus fence elsewhere in this clause), and because this mask defines the
  universe `f` and `f₀` live on, it is a registered quantity, not prose). Same construction otherwise:
  trailing-60 quintiles on classified premium, ddof=0, seed 20260729, 20 draws, one run, first
  run governs. Full-corpus v6-fence table = context only (Q1 1.961% … Q5 8.629%, pooled
  6.612%; stationarity v6 ALL 0.29pt / SHORT_OTM 0.63pt — the roadmap's arm-unnamed v7-line figure, arm resolved from `data/base_rate_table.json`: ALL 0.29094, SHORT_OTM 0.62678). Expectation pre-stated:
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
   provenance only) — ETN list + delisted census list + **successor map AND reuse-split list
   (the registered Alpaca corporate-actions pull per G.2's terminal-value rule; overlap check
   run on every pair, overlapping pairs excluded and recorded; frozen as part of the snapshot
   JSON)** + **test-root list (directory `Test Issue = Y` rows + OPRA-only
   test roots the census surfaces)** + **index-addition list (both index-miss instruments
   recorded, per G.8)** + LEGACY_ETF_SET — the two naming reviews (index-addition, test-root)
   are the ORCHESTRATOR's, and their output is committed VERBATIM, never summarized — plus
   the **bar backfill at G.2's extended scope: dotted class-share forms, bar-less mapped
   successors, EVERY equity-classified root (post-layer-2 — the layer-2 delisted list is
   part of the universe this scope quantifies over) with an eligible name-day in the
   judgment or calibration window whose bars are absent or end before the run date, AND the
   BENCHMARK — SPY bars through `d+10` for every judgment date; benchmark unavailability is
   never a truncation trigger, it BLOCKS the run (G.2) — the vendor, not the
   local store, is the authority for `truncated_horizon`/UNGRADEABLE** (data acquisition, not
   a touch). **Tooling
   note, recorded so this obligation is not misread as pure data acquisition:**
   `tools/flow_corpus_bars.py` derives its universe from OPRA definition files and its
   `isalnum()` filter (`:57`) drops dotted forms, with no symbol-list override — the backfill
   needs a small tool extension, and the READ side needs terminal-symbol resolution (successor
   chain + reuse split + dotted form) or bars stored under `BRK.B` stay unreachable. Measured
   scope at drafting: the five v6-mapped successors carry bars complete from their first day
   of existence (PSKY: 242 rows from 2025-08-07, its listing day); the mechanical work is the
   four dotted forms, the vendor-verified store-fault name (BLD — TMHC and NUVL were listed
   here in draft v9 but are vendor-confirmed genuine terminations, not faults), plus whatever
   the full sourced map adds.
3. The G.9c re-emission built and run (fenced extractor — `fence_and_classify()` +
   `multileg_flags()` from `tools/flow_corpus_rollup.py`, the tested v7 functions, never a
   fifth hand-copy — versioned cache, floored, masked to the judgment window; **implementation
   note: `base_rate_table.py:232` guards `per_draw` with `if m == "full"`, so the judgment
   mask must BE the mask that feeds the across-draw SE — a mask added beside `full` would
   compute the registered SE on the wrong window while the tallies look right**) + per-tier `f` +
   the null-usability criteria (across-draw SE ≤ 0.5pt — a NEW registered criterion,
   numerically equal to the 1.7 B3 reporting threshold — and ≥ 1,000 DISTINCT tested
   name-days) + the dilution gate's PER-TIER precision record + **the real-vs-null per-name
   tilt AR(1) and dispersion on the training window** (per G.2's DEAD (dilution) wording —
   the iid-null-vs-dependent-series confound's size goes on the face of the record). **3b:
   the 2.4 census counting
   stage re-run on the registered pipeline, logging the f_open-ranked share — the tier is what
   the registered rule selects (expectation: Q5). Pinned so no choice is made at run time: the
   re-count runs on the same event span 2.4's census used (2025-07-28 → 2026-05-29 —
   outcome-blind either way); ties in the max-per-quintile-min rule break toward the HIGHER
   quintile. Disclosed: the rule's min-of-open/close-sides form is inherited from 2.4 even
   though the primary cell uses no open/close split — kept because changing the rule's form
   now would be a post-hoc selection-rule edit.**
4. *(reserved — merged into 5/6 ordering below)*
5. The G.2 step-1 input-property records — **census base: CLASSIFIED premium, the registered
   operative base (drafting figures quoted on other bases are labelled as such)**:
   complete-or-terminated date count + calendar-gap
   vector, event count, regime-span composition on the cell's dates — **including the named
   event counts on the two tail dates the regime-span rule currently hinges on (2026-06-25,
   the only ≥ +2% SPY date measured so far, and 2026-07-15, the only ≤ −2% one)** —
   truncated/ungradeable census (both `no_spot` buckets), arm-split unclassified and
   `adjusted` shares, **and the per-date gradeable-event-count DISTRIBUTION for BOTH the
   judgment window and the calibration training window — registered disclosure clause: the
   size calibration is scale-invariant but NOT shape-invariant (each per-date mean's tail
   weight is set by its cohort size), so a judgment window materially thinner per-date than
   the training window is reported ON THE FACE of the verdict** — this was the one remaining
   channel where a null reaches ALIVE with no implementer error.
   **Timing: obligations 5 and 6 ALSO cannot run before the close of 2026-08-07** — the date
   count, regime-span composition, and calibration inputs need bars through `d+10`; the
   scoring-run date bound is inherited here.
6. The G.4 size calibration + MDE record, computed AT the recorded date count and gap vector
   from obligation 5.
Classification-lab work is independent and does not gate this.
