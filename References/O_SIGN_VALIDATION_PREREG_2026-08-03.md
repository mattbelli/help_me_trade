# o sign-convention validation — REGISTERED PREREG (2026-08-03). THIS IS LAW.

*Founder GO 2026-08-03 ("ok validate the o band") on board card `o-sign-validation-go`.
Three review rounds, two split-mandate Opus reviewers (stats/inference; microstructure/
data-mechanics): v1 BLOCK+BLOCK, v2 BLOCK+BLOCK, v3 APPROVE-WITH-CHANGES twice — every
round-3 change applied verbatim below. Any change after this line is a dated amendment
registered BEFORE the affected stage runs. Companions: `FLOW_PACKAGE_ORIENTATION_DESIGN_2026-07-27.md`
(the o-band §3/§3a–§3c), `FLOW_ROADMAP.md` 3.4 round-1 block (the fix-list this
inherits), BUILD_LOG 2026-08-03 rows.*

**THE ONE-LOOK RULE: this study consumes NO outcome data — no forward returns, nothing
sealed; the founder's unseal authorization goes deliberately UNUSED. The one look is the
Step-2 agreement readout; re-cuts are new preregs.**

## Lineage (why this design)
- v1 (aggregate o-implied direction vs next-day excess returns) was BLOCKED on measured
  grounds: stressed MDE in the hundreds of bps vs a ≤30 bps plausibility bar; the (m,m²)
  residualization manufactured up to +47 bps of functional-form artifact in the
  large-move tail; a null was uninterpretable (sign-wrong vs uninformed-flow). Recorded
  as UNRUNNABLE at current corpus scale, not abandoned by choice.
- v2/v3: tied-to-stock trades give a mechanically-coupled ground truth: the stock leg
  prints on the equity tape as part of the same deal and pairs the +Δ-option holder
  with the stock-sell side by construction (true for both parties; no initiator concept
  needed). If quote-position carries direction on both tapes, the readings must agree
  through the delta-offset map; two broken instruments cannot fake strong agreement.

**Question.** Does quote-position carry taker direction on negotiated option packages —
the §3 UNVERIFIED assumption under the o sign convention ("o near 1 ⇒ taker bought A /
sold B")? §3c proved edge CONCENTRATION (premium-weighted 3.33×/4.7×; count-weighted
1.85×/2.28×, measured 2026-08-03); direction was never validated.

## Measured feasibility base (sources named; re-verified at build)
- `'7'∧'V'` ('V' = stock-option trade) is the stock-leg universe: 9,139 prints across
  3.2's stored 24,093 snapshot windows (34.7M prints) — 100% TRF ('D'), 2.0% odd-lot,
  p50 200sh, tapes A 5,122 / C 3,920 / B 97. `'7'`-only non-'V' is ~96% odd-lot noise
  and is NOT the universe. 'V'-only count is provably 0 of 34.7M — a nonzero count at
  build is a data-change tripwire.
- QCT-presence probe (152 top-premium single-leg tied events, ±30s): 57.2% have ≥1
  `'7'∧'V'` print; nearest Δt p50 = 1.7 ms, 80.5% within ±100 ms, p90 1.46 s;
  multiplicity 47/12/28 (1/2/3+); equity-fence cost 24%. TRF stamps behave as
  participant time — measured, not assumed.
- Step-2 primary-cell yield (full chain incl. o_sum corner + single-print-per-leg):
  176 events / 23 underlyings (2025-09-16), 36 / 12 (2026-01-07); per-unit event count
  p50 2 / max 55. Implied stock-leg sizes p50 2,037 / p75 6,115 sh vs `'7'∧'V'` print
  p75 500 sh — stock legs split across prints for most events (drives the MF-1 rule).
  Competitor density (upper bound, all tied clusters): 44.2%/60.7% at ±100 ms.
- Chance `'7'∧'V'` density 6.32e-4 /symbol-second ⇒ chance presence ≈0.013% at ±100 ms.
- Storage ≈27 KB/window ⇒ ~0.3–0.6 GB total. Page-cap headroom 28× worst-case
  (INTC 70,886 quote rows vs 2M cap) — measured DEAD; a `PageCapError`/feed-assertion
  failure on ONE event is a counted per-event drop, never a run abort.

## Sessions
`qualifying_sessions()` (`tools/hedge_footprint.py:949`) is AUTHORITATIVE — training
window ∩ join ≥ 0.98 (`join_ledger.json`) ∩ not half-day: **N = 105** (2025-07-28 →
2026-05-05; the endpoint is a join-floor coincidence, NOT 3.3's split boundary).
Per-month counts (all eleven, disclosed): 2025-07:3, 08:17, 09:13, 10:12, 11:12,
12:11, 2026-01:8, 02:2, 03:14, 04:12, 05:1. List enumerated + SHA-pinned in the frame
artifact. The Addendum-F lever (floor 0.97 ⇒ N = 175, measured — 1.67×) is reserved
for the registered ladder ONLY (below). (The stale "107 of 212" docstring is a code
fix, not a prereg change.)

## Instrument map (pinned tests BEFORE any fetch)
The taker's stock leg is the NEGATION of `hedge_shares`' dealer map: taker buys call ⇒
taker SELLS stock; buys put ⇒ BUYS; sells reverse. Encodings pinned: corpus `side` is
a LABEL (0=buy, 1=sell, 2/3 unusable) mapped to ±1 explicitly; equity signing returns
±1/0. Four worked cases as unit tests, plus the synthetic-drift test (below).

## Step 1 — calibrate the stock-leg instrument, on STRICT single-leg tied prints
- Population: single-option-leg tied clusters (§3a rule, TIED codes), equity class,
  premium ≥ $10k, T ∈ [09:35, 15:55] ET, option print passes the strict
  at/through-quote rule (`strict` ∈ {0,1}, `flow_corpus_rollup.py:269`; measured
  availability 309–435/session). **NAMED ASSUMPTION, not an identification: a_p ≈ 1 on
  strict tied prints.** The corpus's own doctrine is that package legs need not sit
  inside their own NBBO (`db/flow_corpus.py:210-219`), and tied legs are package legs.
  What is measured is the PRODUCT (2a_p−1)(2a_s−1), so **a_cal is a LOWER bound on a_s
  and â_o is an UPPER bound on a_o — every appearance of â_o carries "upper bound".**
  Required diagnostic: a_cal on strict vs non-strict prints published side by side —
  a null difference is evidence against a_p ≈ 1 and publishes with the verdict.
- Sampling: every 6th qualifying session (~18); per session ALL strict-eligible events
  up to 300, premium-stratified (equal thirds by tercile; a_cal per tercile; the
  Step-2 gate uses the tercile-reweighted value).
- Fetch: Alpaca SIP trades + quotes, [T−300s, T+300s] (the inherited feed assertion is
  calibrated for 300s; fetch wide, match narrow), 3.2 snap-cache pattern.
- Candidate stock prints: **`'7'∧'V'` AND no code in the tape's excluded set** (measured
  overlap 15/9,139 = 0.16%, mostly out-of-sequence 'Z' which breaks the timestamp
  basis; its own funnel row). 'V' registered in BOTH CTA and UTP blocks of
  `build_condition_table()` as `"stock_option": ["V"]`; `'7'`-only and 'V'-only are
  counted funnel rows.
- **Stock side — aggregate, never deduplicate.** Within the primary window take EVERY
  qualifying `'7'∧'V'` print, sign each by the stock-quote rule, and form the
  size-weighted net signed shares S_net = Σ sign_i·size_i. Observed stock side =
  sign(S_net). Coherence gate: |S_net| / Σ size_i ≥ 0.60, else UNSIGNABLE-INCOHERENT
  (counted). Registered rationale (measured): implied stock legs p50 2,037 / p75 6,115
  sh vs print p75 500 sh — a one-print rule deletes the large packages by construction.
- **Option side — deduplicate.** Drop an event when another qualifying tied option
  event in the same underlying has a primary window overlapping it; counted as
  AMBIGUOUS-CLAIMANT (own funnel row). Measured upper-bound competitor density
  44.2%/60.7% at ±100 ms; the realized rate on the Step-1/Step-2 populations is a
  REGISTERED build-time output, published before the mechanics gates are evaluated.
- Windows: **primary ±100 ms** (= Addendum E's registered join tolerance; captures
  80.5% measured — the ±1.82 s leg is COVERAGE sensitivity, not an alternative
  primary); sensitivity ±1.82 s (3.1's registered lead-lag grain, cited) and ±5 s;
  the full Δt distribution over ±300 s is a registered output.
- Stock-quote rule: last NBBO **strictly before** the stock print's ts — a NEW function
  (`side='left'` − 1); `prevailing_quote_idx`/`sign_midpoint` (at-or-before,
  `tools/hedge_footprint.py:492-496`) are NOT reused on the stock side. The pinned
  synthetic-drift test includes a quote stamped at exactly the print's ts_ns and
  asserts it is NOT used. Sensitivity: the same reading with quotes lagged **±1 s**
  (two-sided — 3.1's skew band is two-sided); a material a_cal delta is a tripwire.
  At-mid/no-quote ⇒ unsignable, counted.
- **Dead-instrument test = placebos, two offsets:** the identical matcher at T+60 s
  AND at the same clock time on a different qualifying session (T+60 s alone sits
  inside the same trading burst for a large share of events — measured 68.7%/78.5%
  competitor share at ±5 s — so the two offsets bracket chance). Registered outputs:
  contamination fraction c = matched-rate(placebo)/matched-rate(real) per window;
  **c < 0.01 required at the primary window** (expected ≈0.0003); c reported at
  ±1.82 s/±5 s (expected ≈0.004/≈0.012) and applied as the registered false-match
  attenuation on a_cal. Absolute mechanics gates: match ≥ 20% of events, signable
  ≥ 50% of matches — evaluated on the MF-1 rule with **AMBIGUOUS-CLAIMANT drops
  removed from the DENOMINATOR** (they are not match failures; reported separately).
  A mechanics-gate failure driven by claimant ambiguity routes to the investigate
  path, never to INSTRUMENT-DEAD-MECHANICS.
- Statistic: a_cal (agreement of predicted vs observed stock side), **session-cluster
  bootstrap intervals everywhere (never naive Wilson)**; session-stratified
  permutation p, two-sided; chance-corrected κ with realized marginals reported
  (measured Step-1 predicted marginal ≈ 50/50). Match/signable split by predicted
  side, premium tercile, |Δt| tercile (side-split > 10 points ⇒ investigate).
- Drift stratification (both steps): pre-print |δS| over [T−2s, T) from the fetched
  SIP quote mids; terciles over EVENTS in Step 1, over UNITS in Step 2 (unit drift =
  premium-weighted mean of its events' |δS|). **Any directional verdict must hold
  (same sign, p < 0.05) in the lowest-drift tercile; high-drift-only ⇒
  AMBIGUOUS-DRIFT.**
- **Step-1 truth table (gapless).** Threshold θ* = max(θ, 0.60) where θ = the a_cal at
  which the Step-2 gate's n_required ≤ projected units (self-consistent, like the
  floors); 0.60 is the absolute backstop:
  (i) mechanics gates fail or placebo c-bound not met ⇒ INSTRUMENT-DEAD-MECHANICS
  (subject to the claimant-ambiguity routing above);
  (ii) a_cal CI contains 0.5 ⇒ INSTRUMENT-DEAD-NO-DIRECTION;
  (iii) a_cal significantly BELOW 0.5 ⇒ **INSTRUMENT-DEAD-INVERTED — TERMINAL** (the
  map is pre-pinned by tests; inversion is physics/booking indistinguishable from
  drift artifact; any flipped-map study is a NEW prereg; the drift leg publishes
  with it);
  (iv) permutation p < 0.01 above 0.5 but cluster-bootstrap LB < θ* ⇒ INSTRUMENT-WEAK
  (Step 2 does not run; recorded);
  (v) permutation p < 0.01 AND cluster-bootstrap LB ≥ θ* ⇒ **LIVE**;
  (vi) cluster-bootstrap LB ≥ θ* but permutation p ≥ 0.01 ⇒ INSTRUMENT-WEAK (the
  clustering-aware test governs).

## Step 2 — test o on 2-leg tied packages: pilot → gate → run
- Frame (frozen + SHA'd before any Step-2 fetch): 2-leg equal-size tied clusters
  (§3a), equity class, in-band spread-edge cell (0 ≤ o_diff ≤ 1,
  min(o_diff, 1−o_diff) ≤ 0.10, min(o_sum, 1−o_sum) > 0.10), both legs C/P-resolved,
  good first-print BBOs, DTE ≥ 1, |netΔ_select| ≥ 0.10 (per contract, Δ_A − Δ_B,
  fit_delta quote-mid IV at prior-close spot; ±2% spot-invariance diagnostic
  disclosed), **single-print-per-leg = the PRIMARY cell** (this is the load-bearing
  yield: 176/36 measured INCLUDE this cut), no extra premium floor (the structural
  ≥$25k-ish implied floor disclosed; below-|netΔ|-floor population retained for
  sensitivity). Multi-print cell = co-primary requiring sign agreement. σ_net as
  registered (§3a/§3c conventions verbatim); σ_net marginal disclosed at freeze at
  BOTH event and unit grain (majority voting amplifies skew). Predicted stock side =
  −σ_net. A/B-swap invariance holds away from o_diff = 0.5 (excluded by the cell).
- **Scope carried on any verdict:** the primary cell is ~100% code 'n' — every Step-2
  statement is about **TLET-family tied 2-leg packages**; extension anywhere else is a
  NAMED assumption.
- **Pilot (registered):** 3 sessions, one drawn from each tercile of the frozen
  frame's per-session primary-cell counts (the tercile-median session; ties to the
  earlier date) — a largest-3 pilot would bias match/signable high and n_required low.
  Full fetch/match/sign path. **Label-blind by FILE RULE:** per-event observed stock
  sides go to `pilot_observed_sealed.json` (SHA recorded; NO gate code reads it); the
  pilot artifact may contain ONLY: funnel, match/signable counts, Δt distribution,
  per-(session, underlying) unit counts, and match rates split by predicted side. It
  may NOT contain any per-event observed side, any observed×predicted cross-tab, or
  any statistic conditioned jointly on σ_net and the observed side. Violation is a
  registration breach. Pilot sessions count toward the frame; fetches reused.
- **Unit construction (both sides over the SAME event set):** the unit is built over
  the MATCHED-AND-SIGNABLE events of that (session, underlying) ONLY; unit statistic
  = sign(Σ_matched premium_i·σ_net,i) vs sign(Σ_matched premium_i·observed_i); events
  failing match or sign are excluded from BOTH sides and counted; exact-zero sums
  dropped + counted; per-unit event-count distribution disclosed at freeze.
  Registered sensitivity (reported-only): single largest-premium event per unit.
- **Gate (pre-run):** power on the unit grain, simulating the frozen frame's realized
  (session, underlying) block-size distribution. Inputs: frame unit counts × pilot
  match/signable × a_cal at its tercile-reweighted **session-cluster-bootstrap lower
  bound** (reweighted to the pilot's premium composition; coded check that Step-2's
  premium range lies inside Step-1's calibrated range — outside ⇒ the gate may not
  proceed). **Power target ≥ 0.80 to detect true a_o = 0.65, where a replication
  counts as DETECTED only if the FULL registered verdict conjunction passes —
  session-permutation p < 0.01 AND name-block permutation p < 0.01 AND lowest-drift
  tercile holds (same sign, p < 0.05) AND multi-print co-primary agrees in sign**
  (3.3 precedent: detection = the full predictive conjunction). Registered arithmetic
  note: the drift-tercile leg alone needs ≈1.74× the units of a single-null
  calculation, so n_required materially exceeds any single-null number. **Stress:
  pilot rates halved AND a_cal LB − 0.05, power ≥ 0.60** (coded assertion stressed <
  unstressed). The gate OUTPUTS n_required; the floor IS n_required (plus ≥ 25
  distinct underlyings pooled, ≥ 20 sessions; pooled distinct-underlying count
  disclosed at freeze). Projected units < n_required ⇒ **registered ladder, step 1:**
  re-run the ENTIRE pipeline at join floor 0.97 (N = 175, measured) with the
  partially-labeled-session caveat carried on every number; **step 2: NO-RUN
  (zero further fetches).**
- Full run: every remaining qualifying session; ALL primary-cell events (no top-N).
- **Inference:** unit agreement; session-cluster permutation (re-sign σ_net at unit
  grain within session) AND name-block permutation (flip an underlying's units
  together across sessions) — a directional verdict requires p < 0.01 under BOTH
  (intersection: joint size ≤ 0.01, cost is power, folded into the gate);
  n_perm ≥ 50,000 each, seed 20260803. "Direction" = sign(agree − permutation-null
  mean) so it can never diverge from κ. κ with realized marginals reported at both
  grains. â_o = 0.5 + (agree − 0.5)/(2·a_cal − 1) is REPORTED-ONLY, an **upper
  bound** (see Step 1), CI by joint bootstrap resampling COMMON sessions across
  Step 1 and Step 2 (they overlap by construction; Fieller acceptable), truncation
  to [0,1] disclosed; grain mismatch (event-grain a_cal on unit-grain agreement)
  biases it further upward — disclosed with it.
- **Gate 2 (post-fetch):** realized match/signable/unit counts; realized power (same
  conjunction harness). **Semantics per 3.3 Amendment 1 ruling (a): a failed Gate 2
  downgrades ONLY non-significant results** — a result clearing the full conjunction
  still routes VALIDATED/INVERTED with the realized power disclosed beside it; a
  non-significant result at realized power < 0.80 routes NO-VERDICT-UNDERPOWERED and
  may NEVER route NO-VALIDATION.
- Reported strata (never upgrade a verdict; one artifact): drift terciles
  (verdict-relevant via the lowest-tercile rule), |Δt| terciles, premium terciles,
  mixed vs same-right, multi-print co-primary, stock-size bands, σ_net-sign-split
  match rates (tripwire, > 10 points ⇒ investigate), collision-drop rate by
  underlying AND by σ_net sign (> 10 points ⇒ investigate; thinner-name population
  shift disclosed), ±1.82 s/±5 s windows, ±1 s lagged-quote rule, event-grain
  statistic, per-name contribution table.

## Step-2 truth table (gapless)
Preconditions: Step 1 LIVE; the pre-run gate passed (directly or via ladder step 1).
- **O-SIGN-VALIDATED-TIED:** the full conjunction passes with agreement > chance.
  σ_net ships as a labeled perception feature on TLET tied 2-leg in-band packages;
  wider use carries the named extension assumption; no win%; wiring per
  WIRE-IT-EVERYWHERE as a follow-up card.
- **O-SIGN-INVERTED-TIED:** the full conjunction with agreement < chance. Confirm-
  before-ship: an independent fresh-session confirmation prereg before any label
  flips.
- **NO-VALIDATION:** Gate 2 realized power ≥ 0.80 and neither direction clears ⇒
  evidence o does not carry direction on TLET tied packages at the powered accuracy;
  o stays sign-agnostic everywhere.
- **AMBIGUOUS-DRIFT:** direction clears overall but fails the lowest-drift tercile.
- **AMBIGUOUS:** co-primaries or the two nulls disagree at threshold; any unresolved
  tripwire. Dated amendment, never a re-cut.
- **NO-VERDICT-UNDERPOWERED:** non-significant at Gate-2 realized power < 0.80.
- **STOP-MECHANICS:** > 20% of sampled events lost to fetch faults ⇒ investigate
  route (diagnostics/repair/NO-RUN only). Match failure is NOT a mechanics stop — it
  flows to the instrument gates and Gate 2.

## Dropped (registered): the 5-min coupling-slope secondary (both reviewers:
over-readable — a positive slope is also the staleness confound — wrong timescale,
different population). Gone.

## Build obligations (named so nothing is inherited-by-reference that does not exist)
Tied frame builder (fixed loader: UNDEF_SIZE strip — `package_orientation_probe._load`
lacks it, measured 24k rows/session; o_sum corner included; census re-run + SHA'd,
supersession deltas recorded: edge-1D 197→190 / 39→38 on the probed sessions);
`"stock_option": ["V"]` in both tape blocks of `build_condition_table()`;
fetch/match/sign stage (3.2 fetch at 300s + MF-1 aggregate/dedupe matcher +
`prevailing_quote_idx_strict`); two placebo matchers; unit aggregation + dual
permutation + gate/Gate-2 conjunction harness; pinned tests: four worked sign cases,
the 0/1-label encoding trap, synthetic-drift (incl. the ts-equal quote exclusion),
stressed<unstressed assertion, frame reproducibility from the disk manifest
(per-session tcbbo/defs/labels SHAs recorded in the artifact).
