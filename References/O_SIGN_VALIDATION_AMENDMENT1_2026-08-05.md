# o sign-convention validation — AMENDMENT 1 (2026-08-05): midpoint sign rule + millisecond match window

*Founder GO 2026-08-05 ("ok lets start the 2ms register") on board card `o-sign-validation-go`.
Amends `O_SIGN_VALIDATION_PREREG_2026-08-03.md` (THE LAW — inherited VERBATIM except the
enumerated changes below). **This amendment is not law until two split-mandate Opus reviewers
(stats/inference; microstructure/data-mechanics) approve — no stage runs before that line is
appended to this file.** Companions: BUILD_LOG 2026-08-04 row, `FLOW_ROADMAP.md` 3.4
consequence (ii), memory `o-sign-step1-inversion`.*

**THE ONE-LOOK RULE carries over unchanged: no outcome data — forward returns stay sealed;
the founder's unseal authorization stays deliberately unused. The one look is the Step-2
agreement readout.**

## Standing of the 2026-08-04 run (not reopened)
The registered Step-1 verdict — INSTRUMENT-DEAD at the strict/±100 ms arm (a_cal 0.545,
CI [0.484, 0.606] ∋ 0.5; T+60 s placebo c 0.0216 > 0.01) — STANDS. Step 2 never ran;
nothing was unsealed. This amendment is a successor arm, not a re-cut: the registered arm's
result is final and publishes alongside whatever this arm finds.

## Family ledger (publishes beside any verdict of this arm)
Arm count to date: **2** (arm 1 = the registered strict/±100 ms arm, verdict
INSTRUMENT-DEAD-MECHANICS; arm 2 = this amendment). Selection depth from which arm 2's
cell was drawn on the 2026-08-04 data: **2 sign rules × 3 |Δt| bands × window placement
(symmetric ±2 ms vs signed [−1, +3] ms — the latter chosen after comparing both coverage
AND a_cal on the selection data).** No α adjustment is applied — disjoint-session scoring
keeps the conditional error rate honest — but this depth publishes beside the verdict,
not only in this file. If Step 1′ lands in ANY row other than LIVE′ —
INSTRUMENT-DEAD′-{MECHANICS, NO-DIRECTION, INVERTED-TERMINAL}, INSTRUMENT-WEAK′,
CALIBRATED-NO-RUN′, or STOP-MECHANICS′ — this successor is DEAD as a Step-2 path and the
o-band stays sign-agnostic; **no further arm without a new prereg.**

## Motivating measurements (2026-08-04 run + cached-window remeasurements; artifacts on disk)
All numbers in this section are MOTIVATION, not evidence — see the selection-honesty
statement below. Population labels are exact:
- The registration's required diagnostic (`step1_diag.json`) inverted the a_p ≈ 1 named
  assumption: **strict-EXCLUSIVE** midpoint-signed tied prints (the `_nonstrict_events`
  population = non-strict-unanimous clusters only) read a_cal 0.726 [0.687, 0.765] κ 0.45
  (n = 4,326) vs the registered strict arm's 0.545 [0.484, 0.606] κ 0.07 (n = 846).
  At/through-quote position on package legs is allocation noise — the corpus's own
  doctrine (`db/flow_corpus.py:210-219`), now measured cross-tape.
- |Δt| split on the strict-exclusive population (`step1_diag.json → nonstrict_dt_bands`,
  arrays `diag_events_analyzed.npz`): ≤ 2 ms a_cal **0.858 [0.826, 0.886] κ 0.72
  (n = 2,695)**; 2–20 ms 0.504 [0.461, 0.548]; > 20 ms 0.561. Strict arm same shape:
  0.689 / 0.450 / 0.308. In the signed window [−1, +3] ms: a_cal 0.861 (n = 2,820), and
  the [2, 3) ms band the +3 edge newly admits reads **0.894 (n = 141)** — not the
  decaying 2–20 ms average, so the right-edge extension is not buying dilution.
  **No measurement of a_cal on the amended primary population (strict ∪ non-strict
  union) exists on any data**, and no measurement of the amended ESTIMAND (S_net
  aggregated within the millisecond window) exists either — the figures above are
  nearest-print stratifications of a ±100 ms-aggregated statistic.
- Signed Δt structure (equity ts − option t0): 24.1% of matches negative; a negative-side
  mode at ≈ −0.1 to −0.3 ms and a dominant positive-side mode at [1, 2) ms (1,409 of
  2,695); per-session positive modal Δt 1.67–1.90 ms through 2026-01, **1.38–1.46 ms from
  2026-03 on** — a ≈0.3 ms cross-SIP infrastructure shift inside the sample is an
  established fact. The negative-side mode is DOMINANT in 5–6 of 18 sessions; its
  per-session margin to a −1 ms edge measures 0.70–0.95 ms across 0.05–0.2 ms binnings —
  the tripwire outcome is binning-insensitive — and the registered two-sub-mode tripwire
  estimator (A1-2) measures ALL 18 sessions clearing both clauses under [−1, +3] ms. In-window a_cal steps across the
  2026-03 boundary: **0.839 pre-shift (n = 1,941) vs 0.909 post-shift (n = 879)**;
  per-session capture ranges 0.435–0.838 with the low values concentrated pre-shift
  (no February session falls in the 08-04 sample — the 08-04 diag jumps 2026-01-09 →
  2026-03-06 — so this split is boundary-invariant vs the pinned 2026-02-01 cut).
- MF-1 at millisecond width degenerates benignly (measured on 900 cached diag windows,
  signed window): match 537/900 ≈ 0.597, signable | matched 0.996, n_qual = 1 for 89.4%
  of matched events, p50 size retention vs ±100 ms = 1.000, obs_side flips on 0 of 516
  doubly-signable events, coherence p10 = 1.000. The matched print is typically a
  PARTIAL leg (median matched 300 sh vs implied leg p50 2,037 sh).
- Placebo at the signed window, T+60 s, on the 900 cached windows: **1 match in 900**
  (rate 0.0011, c ≈ 0.0019 vs the 0.01 bound) — identical at ±2 ms and [−1, +3] ms,
  independently confirming the same-width recentring has identical chance-capture
  properties. A measured projection, labelled as such. The base prereg's chance-density
  model is FALSIFIED (predicted ≈0.013% at ±100 ms; measured t60 rate 1.01%, ~80×) and
  is not cited by this amendment for anything.
- Realized frame drops from the 200 ms claimant radius (frozen frame, all 105 sessions):
  **67.0%** of the Step-1 population (20,516 of 30,626), **75.9%** of the Step-2 primary
  cell (12,159 of 16,013, leaving 3,854 events). The base's "44.2%/60.7% upper bound" is
  exceeded by both and is retired.
- Predicted-side match-rate split remeasured at millisecond width on cached windows:
  65.1% (pred_buy) vs 49.6% (pred_sell) — wider than the 11.2-point split that fired at
  ±100 ms. Registered expectation: this tripwire will likely fire; A1-8 pre-registers the
  decomposition that resolves it.
- Step-2 reachability projection (motivation-grade): on the post-radius frame (1,298
  units / 3,854 events / 341 names), the registered conjunction-power harness reads
  ≈0.94 at a_cal 0.86 and ≈0.90 at 0.83 at the amended keep rate ≈0.57 — θ is achievable
  and A1-6(c)'s NO-RUN branch is a safety net, not the expected path.

**Selection-honesty statement: both amended choices — the midpoint sign rule and the
millisecond window — were identified on the 2026-08-04 Step-1/diagnostic data. The window
boundary was selected AFTER seeing agreement split by post-hoc Δt bands, and the window
PLACEMENT after comparing coverage and a_cal across placements — selections on the
outcome variable. What the two independent p50 measurements (QCT probe 1.7 ms, pre-freeze;
run 1.70 ms) establish is only that the coupling timescale is milliseconds. Nothing in
the section above may enter a gate, a threshold, or a verdict. The amended arm's validity
rests entirely on Step 1′ being scored on disjoint sessions its selection never touched.**

## Enumerated changes (everything else inherits verbatim)

**A1-1 — Option-print sign rule (Step 1′ only): MIDPOINT, fully pinned.** Predicted taker
option side = the corpus `side` label from `fence_and_classify`
(`tools/flow_corpus_rollup.py:240-275`; midpoint rule, price > mid ⇒ buy, price < mid ⇒
sell), evaluated on the BBO carried on **each print's own tcbbo row** (pre-trade
consolidated book — the settled 07-26 tcbbo finding; NOT the Step-2 leg's first-print BBO,
NOT fetched quote series). Unsignable classes, EACH its own counted funnel row:
(a) at-mid or no two-sided positive non-crossed quote (`side = 2`; locked markets — ask ≤
bid — are unsignable); (b) wide quote per the v7 fence, `SPREAD_MAX_FRAC = 0.50`
(`side = 3`; measured 5.7–7.6% of classified prints); (c) non-unanimous side across a
multi-print cluster (UNSIGNABLE-MIXED-CLUSTER — the registered aggregation rule is
**unanimity-or-drop**, matching the motivating measurement's rule; a premium-weighted-net
alternative is NOT registered). Cluster `n_prints` is a disclosed output and a_cal
publishes split 1-print vs >1-print (measured: 15.3% of framed single-leg clusters are
multi-print). The instrument map (taker stock leg = negation of the dealer hedge map) and
its pinned tests are unchanged; the 0/1-label-encoding pinned test is EXTENDED to cover
the `strict`→`side` column-source swap (both alphabets are 0=buy/1=sell; a wrong-column
read is silent).
**Population:** ALL single-leg tied clusters passing the inherited filters (§3a TIED
codes, equity class, premium ≥ $10k, T ∈ [09:35, 15:55] ET) — the **union**
strict-unanimous ∪ non-strict; the `strict` flag is no longer an eligibility filter but IS
a registered stratification class. **Per-session cap 300, stratified premium tercile ×
quote-position class (strict-unanimous / non-strict): premium terciles are cut over the
session's FULL eligible union population and then crossed with class — 6 cells, equal
allocation, evenly spaced by premium rank within cell (deterministic — the cap is sample
SELECTION, not a draw, and is unaffected by the seed); a cell short of quota
redistributes the shortfall within its premium tercile to the other class, disclosed.**
Registered expectation, disclosed in advance: the strict-unanimous class measures ~96
non-ambiguous events/session against a 150 quota, so shortfall redistribution is expected
on most sessions and the realized mixture is expected near 1/3 strict : 2/3 non-strict —
NOT the 50/50 the allocation targets. The realized 6-cell fill matrix and per-session,
per-cell eligible-before-cap counts publish with the artifact, so the sampling fraction
and realized composition are known. a_cal publishes per class and for the mixture. Step 2
is untouched by this change (its predicted side comes from σ_net, not from a print sign
rule).

**A1-2 — Primary match window: signed [−1 ms, +3 ms] around t0** (was ±100 ms), both
steps, real and placebo matchers alike. Definitions registered: **Δt = equity print ts −
option cluster t0**; **t0 = the MINIMUM ts_event across the cluster's prints** (for
multi-print clusters this biases Δt positive by the cluster span — the cluster-span
distribution is a disclosed output alongside the Δt distribution). The window is signed
and asymmetric because the measured coupling is: dominant positive mode at [1, 2) ms,
negative mode at ≈ −0.3 ms, and a measured ≈0.3 ms mode shift inside the sample —
[−1, +3] ms has the same 4 ms total width (identical chance-capture properties, measured:
the T+60 s placebo count is 1/900 under both placements). Fixed HERE, before any amended
stage runs. Sensitivity windows (reported-only, never verdict inputs): **deliberately
SYMMETRIC** ±10 ms and ±100 ms — they exist to bridge to symmetric-window diagnostics and
must match them; the ±100 ms leg bridges to the **diagnostic's** 0.726 (strict-exclusive
population); only the strict stratum at ±100 ms bridges to the registered arm's 0.545.
The full Δt distribution over ±300 s stays a registered output. Fetch stays
[T−300 s, T+300 s] (fetch wide, match narrow; the placebo offsets need the width).
- **Skew diagnostics (pre-verdict, registered, estimator PINNED):** per-session SIGNED Δt
  deciles publish BEFORE any gate is evaluated. The mode is located on **0.2 ms bins over
  the in-window population, computed SEPARATELY for the two registered sub-populations —
  the negative-side mode (Δt < 0.5 ms) and the positive-side mode (Δt ≥ 0.5 ms)** (the
  distribution is bimodal; a single global mode jumps ~2 ms between sessions and hides
  the mode nearest an edge). **Skew tripwire:** each sub-mode must sit ≥ 0.5 ms from its
  nearest window edge; either failing, or the capture fraction falling below 0.30, routes
  that session to the INVESTIGATE path (clock/venue-latency artifact); a match-gate
  failure so driven may NOT be read as INSTRUMENT-DEAD′-MECHANICS. **Capture fraction =
  (events with ≥ 1 qualifying print inside the primary window) / (events with ≥ 1
  qualifying print inside ±100 ms).** Measured on the 08-04 sessions, all 18 clear both
  clauses under [−1, +3] ms (minimum capture 0.435; sub-mode margins ≥ 0.7 ms negative /
  ≥ 1.1 ms positive).
- **Regime split (registered stratum, not optional):** a_cal′, match rate, and in-window
  capture publish split at the registered infrastructure boundary — **sessions on or
  after 2026-02-01 are post-shift** (date pinned so two implementers cut the stratum
  identically) — with session counts, at both grains where applicable. A > 10-point a_cal′
  difference across the boundary is a NAMED TRANSPORT ASSUMPTION carried on the Step-2
  gate input and on every verdict; if the Step-1′ and Step-2 regime compositions differ
  by more than 10 percentage points, the run routes to INVESTIGATE.
- **Step-2 inter-leg span precondition (frame-side, runs BEFORE any Step-2 fetch):** the
  window was placed on a ~90% single-print population (span ≈ 0), but every Step-2 event
  has TWO legs and t0 = min ts_event biases its Δt positive by the inter-leg span — and
  the frozen frame persists no per-leg timestamps, so this is currently unmeasurable from
  disk. Registered obligation: the Step-2 inter-leg span distribution (max ts_event −
  min ts_event within the 2-leg cluster) is computed from the session inputs, persisted
  into the frame artifact, and publishes before any Step-2 fetch. **Coded precondition:
  if the primary cell's p90 inter-leg span exceeds 1.0 ms, the amended window may NOT be
  applied to Step 2 without a further dated amendment** (the options being a Step-2
  window re-registration or per-leg t0) — this computation consumes no fetches and no
  agreement numbers. The two-sub-mode skew tripwire is registered generally and GOVERNS
  STEP 2 AS WELL — the tighter guard is in force regardless of this precondition.
- **MF-1 within the window:** aggregation + coherence (≥ 0.60) unchanged in FORM, and
  measured to reduce to ~1 print/event at this width — so the base's "a one-print rule
  deletes the large packages" rationale no longer describes the mechanism, and the gate
  is expected inert. Registered accordingly: within-window multiplicity, Σsize in-window
  as a fraction of Σsize at ±100 ms (captured share), and matched-shares vs
  implied-leg-shares are disclosed outputs; only the SIGN of S_net is used and its
  magnitude is never read as leg coverage. **NAMED ASSUMPTION, carried on every
  downstream use including the Step-2 gate: a_cal′ measures agreement against a
  possibly-PARTIAL stock leg.** a_cal′ publishes stratified by captured-share tercile.
  Pre-run disclosed measurement (funnel-only, no new agreement numbers): within-window
  multiplicity and captured share on the existing cached diag windows — the 89.4%/1.000
  figures above stand as that disclosure.

**A1-3 — Ambiguous-claimant radius: a SEPARATE registered constant
`CLAIMANT_R_NS = 200_000_000`, decoupled from the match window.** Deliberately NOT shrunk
to 2×window. Honest basis, in order: (1) the flag is baked into the frozen SHA'd frame
and shrinking it forces a re-freeze; (2) it is additionally strictly conservative — the
200 ms rule drops a strict superset of any narrower consistent rule and cannot
manufacture a match (verified; and the superset property holds for the SIGNED window too:
two clusters' windows can overlap only within the 4 ms total width, a strict subset of
200 ms). Realized coverage cost disclosed above (67.0% / 75.9%).
**Scope restriction carried on every verdict: a_cal′ is calibrated on the
ISOLATED-CLUSTER subpopulation only; Step 2 applies the same rule, so the transfer is
internally consistent, and the realized competitor-density comparison between Step 1′'s
retained events and Step 2's retained units publishes — a material difference is a named
transport assumption.** Frame reuse is SCOPED: only the frame's **Step-2 (`s2_*`) arrays
are reused**; the frame's `s1_*` arrays (strict-unanimous population, strict side labels)
are NOT usable by Step 1′ — Step 1′'s event table is built by the midpoint loader (the
`_nonstrict_events` path generalized to the A1-1 union) reading the same SHA-verified
session inputs and writing its own artifact. **No amended stage may edit a frame npz, and
Step 1′ may not OPEN one** (see Build obligation 3: `analyze_events`/`_frame_val`
repointed to the Step-1′ event table with a coded key-match assertion — the frame's
strict-only `s1_prem` array indexed by a different population's indices mis-joins
silently). Before Step 1′ runs, every frame npz is SHA-256'd into `frame_manifest.json`
and a coded precondition aborts any amended stage whose frame npz SHA differs (closes the
`stage_verify` overwrite-before-assert hazard).

**A1-4 — Step 1′ runs on DISJOINT sessions, through the FULL fetch path.** Sampling:
`pool[3::6]` of the same SHA-pinned 105-session list — verified: 17 sessions, zero
overlap with Step 1's `pool[::6]` 18; a coded disjointness assertion runs before fetch1′.
Fresh fetches via **`_fetch_events` with `placebo=True`** (NOT the `stage_diag_fetch`
path, which fetches no placebo windows and counts no placebo faults); the 2026-08-04
snapshots are selection data and are not reused for Step 1′ (verified unreachable by
construction: the snap cache keys on absolute epoch-ns, disjoint sessions ⇒ disjoint
paths; the alt-session placebo pool occupies residues {1,4} mod 6 — disjoint from both
arms' real sessions). Placebo coverage is a registered output with a coded assertion:
≥ 95% of real events must have a loaded window for BOTH offsets. Every other Step-1
mechanic inherits verbatim and is evaluated AT the amended window: MF-1,
`prevailing_quote_idx_strict` stock-quote rule (unchanged — the midpoint change is the
OPTION side only), both placebo offsets (law in A1-9), mechanics gates (match ≥ 20%,
signable ≥ 50%, claimant drops out of the denominators), session-cluster bootstrap +
session-stratified permutation, drift stratification (A1-5), and the truth table (A1-10)
with θ* per A1-6. Verdict names gain a prime (LIVE′, INSTRUMENT-DEAD′-…).

**A1-5 — Drift terciles: rank-based equal-count with a SEEDED tie-break (both steps).**
The 2026-08-04 run's value-quantile terciles degenerated on ties at |δS| = 0 (38.1% of
events exactly zero; tercile 0 empty), and a stable-index tie-break was measured to be
worse — array position is session×time order, so the zero-drift block date-clumps
(measured: tercile 0 = 100% zero-drift, spanning 13 of 18 sessions and stopping at
2026-01, a_cal 0.450 vs 0.609 in the top tercile — a time-selected stratum confounded
with the 2026-03 regime shift). Registered rule: **order by (|δS|, U) where U is a
per-event uniform draw from the registered seed 20260805 — ties in |δS| (notably the
zero-drift mass) are broken at RANDOM, never by array position** — and split into three
EQUAL-COUNT bins, so the lowest-drift tercile is always populated, always ≈1/3, and its
session composition matches the population's. **Per-tercile session coverage publishes at
both grains; a lowest-drift tercile spanning fewer than 90% of the arm's sessions routes
to INVESTIGATE.** The zero-drift block is a registered stratum in its own right: the
lowest-drift-tercile verdict rule is evaluated on the lowest tercile AND republished on
the lowest NONZERO-drift tercile, and a divergence between the two is a registered
tripwire routing to investigate, never a downgrade. Exactly-zero drift over 2 s is a
stale-quote signature — this limitation is carried on every verdict, and the zero-drift
subgroup publishes separately (n, a_cal, and the count of NBBO updates in [T−2 s, T)).
The zero-drift share publishes at both grains. **The PRE-RUN gate simulation retains the
uniform-1/3 tercile draw — under equal-count terciles the marginal share is exactly 1/3,
and the within-session clustering of tercile membership is unknowable before the Step-2
fetch (the inherited label-blind pilot allowlist carries no drift, and honoring the
allowlist is the higher obligation); this limitation is disclosed with the gate, and it
is BOUNDED: the defect that motivated realized-structure resampling was the date-clumping
of the stable-index tie-break, which the SEED+5 random tie-break removes at its source —
the residual gap between uniform-1/3 and reality is only the between-session variation in
zero-drift share, not a systematic time selection. Gate 2 (post-fetch, realized power) draws drift terciles by
resampling the REALIZED tercile-by-session block structure of the fetched units.**

**A1-6 — Step 2 inherits its ENTIRE design** (frame/pilot/gate/Gate-2, unit construction,
dual permutation p < 0.01 × 2, label-blind pilot file rule, ladder, scope carried on TLET
tied 2-leg packages, full truth table) with these substitutions:
- (a) matcher window per A1-2, subject to A1-2's inter-leg span precondition;
- (b) the gate's a_cal input = **Step 1′'s composition-reweighted session-cluster-bootstrap
  lower bound**: the reweighting happens INSIDE each bootstrap draw (reweight the drawn
  events to the pilot's composition — defined below — then form the LB by the A1-7
  bootstrap-t construction applied to the composition-reweighted statistic: â is the
  reweighted point estimate, the within-draw delete-one-session jackknife SE is computed
  on the reweighted statistic, and the LB is â − t*_{1−α/2}·SE. The A1-7 instability
  fallback (>1% non-finite SE ⇒ BCa) applies to the reweighted LB as well. **No
  percentile-of-draws construction is registered**, and never reweight the point estimate
  and reuse an unweighted CI width). **Composition definitions:** the Step-2 unit's
  quote-position class is **both-strict ⇒ strict-unanimous, otherwise non-strict** (the
  same unanimity-or-drop convention A1-1 applies to a multi-print single-leg cluster),
  with the 3-way {both-strict, mixed, both-non-strict} breakdown published beside it;
  both the in-draw reweighting target and the two-axis containment check are computed on
  the pilot's **matched-and-signable primary-cell events** — the population the gate
  projects units from — with the all-primary-cell composition published beside it for
  comparison (leg quote position comes off the tcbbo and crosses nothing with an observed
  stock side, so the label-blind rule holds). **The inherited pilot allowlist is extended
  by this amendment to include leg-level quote-position class and premium-tercile
  composition over matched-and-signable primary-cell events — pre-trade attributes that
  cross nothing with any observed side; no other field is added.** Per-cell n / a_cal /
  CI persist in the Step-1′ artifact. **The composition-reweighted LB is the SINGLE a_cal lower bound of record:
  truth-table rows (iv)–(viii) are evaluated on IT, not on the design-weighted mixture LB
  (the cap forces a composition that matches neither the population nor the pilot).
  Rows (ii)/(iii) — CI containment of 0.5 — deliberately stay on the mixture: "is there
  any direction at all" is composition-robust. The design-weighted mixture a_cal, its CI,
  and the per-class figures publish beside the LB of record; where the two LBs fall on
  opposite sides of θ*, the reweighted LB governs and the divergence publishes with the
  verdict.** The coded containment check EXTENDS to both axes: Step-2's premium range
  must lie inside Step-1′'s calibrated range AND the pilot primary cell's leg-level
  quote-position composition (disclosed label-blind — it crosses nothing with an observed
  side) must lie within Step 1′'s calibrated classes; outside ⇒ the gate may not proceed.
  **Evaluation order is deliberate: rows (ii)/(iii) are evaluated FIRST and on the
  mixture; a run where the mixture CI contains 0.5 while the reweighted LB ≥ θ* routes
  row (ii) and publishes the divergence as a tripwire.**
- (c) **θ grid extended to [0.55, 0.95] in 0.05 steps.** θ is computed on the N = 105
  projection; if unachievable there, recomputed on the ladder N = 175 projection (the
  partially-labeled caveat carried); if still unachievable at 0.95,
  `theta_unachievable: true` is recorded and the run routes to truth-table row (viii)
  (CALIBRATED-NO-RUN′, A1-10) — **Step 2 is NO-RUN and LIVE′ is not reachable.**
  **θ* = max(θ, 0.60); when `theta_unachievable` is true, θ* is the 0.60 backstop, so
  row (viii) requires LB ≥ 0.60 and a run with LB < 0.60 routes row (vii)
  INSTRUMENT-WEAK′ as usual.** `theta_basis` is recorded beside `theta` in the artifact
  (Step-1′-projected vs pilot-refined), so the two are distinguishable.
- (d) **â_o joint bootstrap, registered as the estimator the code implements:** one draw
  = resample the FULL qualifying-session list with replacement; a_cal′ recomputed on the
  Step-1′ sessions appearing in the draw, unit agreement on the Step-2 sessions appearing
  in the draw; numerator–denominator correlation is induced through the sessions common
  to both. The count of draws dropped for resampled a_cal ≤ 0.5 and the count truncated
  to [0,1] PUBLISH; if either exceeds 1% of draws the interval is declared INVALID and
  Fieller is reported instead. â_o stays reported-only, an upper bound, as registered.
- Step-2 fetches are all fresh (none exist).

**A1-7 — Seed and intervals.** Seed **20260805** for every amended stage's draws
(bootstrap/permutation/gate); no draw reuse from the 2026-08-04 run (the deterministic
cap is selection, not a draw — A1-1). **Stream separation: the A1-5 tie-break draws from
a distinct derived substream — `np.random.default_rng(SEED + 5)` — never the stream any
bootstrap, permutation, or gate stage consumes** (the file's existing `SEED + k`
convention); the offset is pinned in a test asserting BOTH that the tie-break U vector
is not equal to the first draws of the bootstrap stream AND that `SEED + 5` is not
produced as any `conjunction_power` seed in the run (`n_required` derives data-dependent
`SEED + k` offsets, so the reservation must be asserted, not assumed). Session-cluster bootstrap intervals
for Step 1′ use a **bootstrap-t construction, fully specified**: per bootstrap draw, the
studentizing SE is the delete-one-session jackknife SE computed WITHIN that draw; the
studentized statistic is t* = (a* − â)/SE*, where the unstarred SE is the delete-one-
session jackknife SE on the ORIGINAL sample; the bounds take CROSSED quantiles —
**LB = â − t*_{1−α/2}·SE, UB = â − t*_{α/2}·SE** (getting the crossing backwards is the
classic bootstrap-t error and inflates the LB in the direction that favors LIVE′ — the
pinned coverage test must catch it, and the formulas are written here so it need not).
**If more than 1% of draws yield a non-finite SE, the bootstrap-t is declared unstable
and BCa is reported instead.** Rationale recorded: a plain percentile bootstrap
under-covers at 17 clusters and the under-coverage direction favors LIVE′; a synthetic
coverage check at 17 clusters is a pinned test. The cluster count is recorded in the
artifact beside every interval.

**A1-8 — Tripwires and sensitivity legs, re-registered at the new window.**
- **Predicted-side match-rate split:** publishes DECOMPOSED by right (C/P) × option side
  label, with the put/call marginal disclosed. A split explained by the C/P marginal is
  recorded and does not by itself route to investigate; a split surviving within-right
  conditioning does. The routing decision is written before the a_cal readout is taken.
  (Measured expectation: fires — 65.1/49.6 on cached windows.)
- **Lagged-quote legs:** the inherited ±1 s leg is REPORTED-ONLY at the amended window —
  a large a_cal collapse at a lag 250–1000× the window is the expected signature of a
  millisecond-scale coupling, not a defect. The TRIPWIRE-bearing lag leg is re-registered
  at **±20 ms**: a > 10-point a_cal delta there routes to investigate. Both legs publish
  on the intersection of events signable with and without the lag, alongside the
  unrestricted figure (composition change must not masquerade as sign change).
- **Name-block dependence:** Step 1′ additionally reports a name-block sign-flip p
  (`signflip_p` with underlying blocks) beside the session-stratified permutation — the
  per-session cap binds (measured: 300 × 18 in the diag), concentrating events in few
  names, and within-session exchangeability is then doubtful. A name-block p ≥ 0.01 while
  the session permutation p < 0.01 is a registered tripwire routing to investigate (not
  an automatic downgrade).
- **Skew tripwire and regime split:** per A1-2. **Drift-tercile session-coverage and
  zero/nonzero divergence tripwires:** per A1-5.

**A1-9 — Placebo law at the millisecond window.** Both offsets (T+60 s riding the real
fetch; same-clock alt-session, own fetch) are retained with **c < 0.01 required at the
primary window for BOTH offsets — and a non-finite c at either offset is a FAIL, never a
skip** (the inherited implementation's `all()`-over-finite gate passes vacuously on an
empty placebo; pinned test required). Registered honestly: at 4 ms total width the
placebo ratio is uninformative (single-digit expected numerator — the count is not the
point, the ratio's information content is), so the primary-window placebo carries **no
evidential weight for instrument cleanliness against chance capture** — it is retained as
a SYSTEMATIC-contamination tripwire only, and it is structurally blind to same-deal echo
(a +60 s shift cannot reach it). Accordingly: (a) both placebo artifacts publish raw
numerator AND denominator counts, plus a Clopper–Pearson 95% upper bound on the placebo
match rate beside the ratio; (b) **positive control:** the ±100 ms sensitivity leg's
placebo match rate must be > 0 for both offsets — else the placebo matcher is presumed
dead and the run routes to investigate. Expected magnitudes from the registered arm:
t60 19/1,883 and alt 4/1,883 at ±100 ms, scaling to roughly 50 and 11 over Step 1′'s
~5,100 events — a real check, not a coin flip. **The > 0 comparison is pinned in the
same test as the empty-placebo FAIL** (today `nan > 0` fails safe only by NumPy
accident; a refactor must not be able to restore the vacuous path); (c) the ±100 ms
placebo is the INFORMATIVE reported number for contamination structure. The base's
registered "false-match attenuation on a_cal": at c < 0.01 the attenuation is < 0.5
points and is reported as nil rather than applied.

**A1-10 — Truth-table completions (gapless, stated in law).**
- Row (vii) added verbatim: **(vii) any remaining combination — in particular CI excludes
  0.5 ABOVE, permutation p ≥ 0.01, cluster-bootstrap LB < θ* — ⇒ INSTRUMENT-WEAK′; Step 2
  does not run; recorded.** The α asymmetry (95% CI containment in rows (ii)/(iii) vs the
  1% permutation gate in rows (iv)–(viii)) is a deliberate choice, stated here so it
  reads as one.
- Row (viii) added verbatim: **(viii) permutation p < 0.01 AND LB ≥ θ* but
  `theta_unachievable` at both N = 105 and N = 175 ⇒ CALIBRATED-NO-RUN′ — the instrument
  is calibrated, Step 2 is NO-RUN, LIVE′ is not reachable, and the a_cal′ readout
  publishes as calibration only.** `_step1_verdict` takes `theta_unachievable` and emits
  CALIBRATED-NO-RUN′ in preference to LIVE′ (pinned test — the law/implementation
  divergence this closes produced two of round 1's blockers).
- The stopping rule covers every non-LIVE′ row (family ledger above).
- STOP-MECHANICS′ inherits (> 20% fetch-fault loss ⇒ investigate route only).

## Coverage arithmetic (disclosed, not gated on)
On the cached diag windows the signed-window match rate measured 537/900 ≈ 0.597
(signable | matched = 0.996) vs the 0.20 mechanics gate — headroom ≈ 3×, but the gate
reads the REALIZED Step 1′ rate on fresh sessions, not this projection. θ sits close to
its boundary in keep-rate terms (power ≈ 0.84 at keep 0.45 / a_cal 0.83 vs ≈ 0.94 at
keep 0.57 / a_cal 0.86): a realized Step-1′ keep rate materially below projection puts
the A1-6(c) CALIBRATED-NO-RUN′ branch in play — registered here so that outcome does not
read as a surprise. Step-2 yield at the amended window flows through the registered
pilot → gate arithmetic (the gate outputs n_required; ladder step 1 = join floor 0.97,
N = 175; step 2 = NO-RUN), computed from pilot-realized rates under the amended matcher.
Step-2 primary-cell frame yield after the A1-3 radius: 3,854 events (disclosed above).

## Build obligations (delta only — nothing inherited-by-reference that does not exist)
The base pipeline (`tools/o_sign_validation.py`, 14 pinned tests) stands built for the
REGISTERED arm; the following are NEW obligations this amendment requires before any
amended stage runs:
1. Signed-window parameterization (`W_LO_NS = −1 ms`, `W_HI_NS = +3 ms`) through real +
   placebo matchers and both steps; `SENS_W_NS = (10 ms, 100 ms)`; artifact keys updated.
   **The data-change tripwire counters (`n_Vonly` / `n_7only` / `n_excl_overlap`) are
   counted over the FULL fetched ±300 s window and reported separately from the
   in-window funnel** — narrowing the matcher must not blind the feed-schema detectors.
2. `CLAIMANT_R_NS = 200_000_000` as a standalone constant; pinned test asserting the
   claimant radius is unchanged when the match window moves (both in the frame path and
   in the Step-1′ loader).
3. The Step-1′ midpoint loader: `_nonstrict_events` generalized to the A1-1 union with
   `fence_and_classify` side labels, the three unsignable funnel rows, n_prints, and the
   6-cell stratified cap (terciles over the full union) with shortfall redistribution;
   per-session per-cell eligible-before-cap counts + fill matrix persisted.
   **`analyze_events` and `_frame_val` are repointed to the Step-1′ event table for the
   amended arm — Step 1′ may not open a frame npz — with a coded assertion that the
   loaded event-table row count equals the fetched event count and per-event
   (symbol, ts_ns) keys match, so an index misalignment aborts rather than silently
   mis-joining premiums.** Pinned tests: midpoint sign cases (above-mid ⇒ buy, below-mid
   ⇒ sell, at-mid ⇒ unsignable), wide-fence class, mixed-cluster drop, and the
   `strict`→`side` column-swap trap.
4. Frame-npz SHA-256 recording into `frame_manifest.json` + the coded precondition on
   every amended stage; removal of `stage_verify`'s in-place overwrite hazard for
   amended-arm use.
5. `pool[3::6]` sampler + coded disjointness assertion against Step 1's session list.
6. Rank-based equal-count terciles with the SEEDED tie-break (order by (|δS|, U),
   U ~ `np.random.default_rng(SEED + 5)` — the A1-7 substream, never the bootstrap
   stream; substream separation pinned in a test). Pinned tests: all-zero-drift input
   yields a populated lowest tercile of size n/3, AND a tied input yields terciles whose
   session composition matches the population's within sampling error (the stable-index
   date-clump trap). Zero-drift subgroup reporting with NBBO-update counts; per-tercile
   session coverage; lowest-nonzero republish. The PRE-RUN gate keeps the uniform-1/3
   tercile draw (limitation disclosed); GATE 2 resamples the realized tercile-by-session
   block structure of the fetched units (replaces `rng.integers(0, 3)` there).
7. Per-cell (premium × class) bootstrap machinery: reweight-inside-each-draw LB per
   A1-6(b), per-cell stats persisted, single-LB-of-record plumbing into the truth table;
   pinned test that the reweighted LB responds to composition. Two-axis gate containment
   check.
8. θ grid extension + `theta_unachievable` routing to row (viii) with the
   CALIBRATED-NO-RUN′-over-LIVE′ pinned test; `theta_basis` recorded.
9. â_o bootstrap per A1-6(d) with dropped/truncated draw counts published + the 1%
   invalidity rule (Fieller fallback).
10. Bootstrap-t session-cluster intervals per A1-7 (within-draw delete-one-session
    jackknife SE; instability fallback to BCa); pinned synthetic coverage test at 17
    clusters; cluster count in the artifact.
11. Placebo law: finite-c-required gate + the ±100 ms positive-control > 0 comparison in
    ONE pinned test (empty placebo ⇒ FAIL; nan must fail the > 0 control by assertion,
    not by NumPy accident); raw counts + Clopper–Pearson UB; ≥95% placebo-coverage
    assertion.
12. Skew diagnostics per the pinned estimator (0.2 ms bins, two sub-modes split at
    0.5 ms, per-session), capture fraction as defined, the skew tripwire, and the regime
    split at the pinned 2026-02-01 boundary with its transport-assumption plumbing; signed
    per-session Δt deciles in the artifact; cluster-span distribution; t0 = min-ts_event
    pinned in a test.
13. ±20 ms tripwire lag leg + intersection-set reporting; name-block sign-flip p in the
    Step-1′ artifact.
14. Seed 20260805 plumbed to every amended stage.
15. Step-2 inter-leg span computation persisted into the frame artifact + the coded
    p90 > 1.0 ms precondition on applying the amended window to Step 2 (A1-2) — runs
    before any Step-2 fetch, consumes no agreement numbers.

## Verified-clean record (so they are not re-litigated)
`pool[3::6]` = 17 sessions, `pool[::6]` = 18, intersection empty, all 105 frame npz
present; snap cache keyed on absolute epoch-ns ⇒ 08-04 snapshots unreachable from
Step 1′; alt-placebo pool residues {1,4} mod 6 disjoint from both arms' real sessions;
the 200 ms claimant rule drops a strict superset of any narrower consistent rule
(including the signed window); `daily_volume.json` keyed symbol:date — safe; no 08-04
session trips the pinned two-sub-mode skew tripwire under [−1, +3] ms (minimum capture
0.435 vs the 0.30 floor); Step-2 θ reachability confirmed on the post-radius frame
(power ≈ 0.94 at a_cal 0.86, keep 0.57); Step-2 inter-leg span MEASURED from session
inputs (two-leg tied clusters: p90 0.0054/0.0056 ms, p99 0.033/0.036 ms on
2025-09-16/2026-04-16, 0 of 6,229 pairs above 1 ms — package legs stamp at essentially
the same ts_event, the corpus's ns-collision multileg signature; single-leg cluster span
p99 0.065–0.080 ms), so the A1-2 precondition passes by ~170× and t0 = min ts_event
introduces no material Δt bias at either step; regime compositions measured at the pinned
2026-02-01 boundary (Step 1′ 4/17 = 23.5% post-shift vs Step 2 29/105 = 27.6% — a
4.1 pp gap vs the 10 pp tripwire, and no boundary-adjacent session sits in `pool[3::6]`).

## Review record
- v1 (2026-08-05): Reviewer A (stats/inference) — **BLOCK** (3 blockers: reweighted gate
  input not built; union-population mismatch vs motivating numbers + unregistered
  multi-print rule; amended estimand never measured + MF-1 rationale voided. 9
  change-required, 2 notes). Reviewer B (microstructure/data-mechanics) —
  **APPROVE-WITH-CHANGES** (8 change-required: frame s1_* unusable for Step 1′ + radius
  constant coupling + frame-SHA gap; midpoint rule under-pinned (4 DOF); vacuous placebo
  gate + dead diag-fetch path; unregistered cross-SIP skew with measured in-sample mode
  shift; strict/non-strict mixture composition an artifact of the cap; ±1 s lag leg
  semantics inverted; stale coverage-cost numbers; side-split tripwire pre-fired. 4
  notes). All fixes applied in v2, including adopting the signed [−1, +3] ms window from
  B's measured recentring case.
- v2 (2026-08-05): Reviewer A — **APPROVE-WITH-CHANGES** (7: seeded tie-break for the
  drift-tercile tie mass (measured date-clump, a_cal 0.450 vs 0.609); row (viii)
  CALIBRATED-NO-RUN′ closing the theta_unachievable/LIVE′ divergence; single
  LB-of-record for rows (iv)+; Step-2 inter-leg span precondition; bootstrap-t SE
  specification; family-ledger depth updated for window placement; regime split promoted
  to registered stratum. 3 notes incl. measured Step-2 power reachability). Reviewer B —
  **APPROVE-WITH-CHANGES** (4: same tie-break defect found independently (tercile 0 =
  100% zero-drift, 13/18 sessions, stale-quote×early-session stratum) + realized block
  structure in the gate sim; two-sub-mode skew estimator pinned (bimodal Δt); frame-index
  mis-join hazard in `analyze_events`/`_frame_val`; cap tercile basis + realized-mix
  expectation. 4 notes: no session trips the tripwire under [−1,+3]; signed-window
  remeasurements substituted; positive-control nan pin; wording). All fixes applied in
  this revision (v3).
- v3 (2026-08-05): Reviewer A — **APPROVE-WITH-CHANGES** (3, all law-internal
  divergences: dual LB constructions (percentile vs bootstrap-t) unified on bootstrap-t;
  pre-run gate ordered to consume drift structure the label-blind allowlist forbids —
  resolved as uniform-1/3 pre-run / realized-structure at Gate 2; θ* undefined in the
  row-(viii) branch — pinned to the 0.60 backstop. 3 notes: crossed-quantile formulas
  written; rows (ii)/(iii) evaluation-order precedence; (iv)–(viii) bookkeeping. All
  v3 numbers independently verified.). Reviewer B — **APPROVE-WITH-CHANGES** (2:
  Step-2 quote-position class mapping defined (both-strict ⇒ strict-unanimous, else
  non-strict; 3-way breakdown published; composition population pinned to
  matched-and-signable pilot events); seed stream separation for the tie-break
  (SEED + 5 substream, pinned test). 5 notes incl. MEASURED inter-leg span (passes the
  precondition ~170×), regime-boundary date pinned to 2026-02-01, full-window tripwire
  counters, binning-insensitive margins.). All fixes applied in this revision (v4).
- v4 (2026-08-05): Reviewer A — **APPROVE** (truth table re-derived end-to-end under the
  two-LB split and confirmed gapless; 3 notes applied: regime figures corrected to the
  pinned 2026-02-01 boundary (29/105 = 27.6%, 4.1 pp) + boundary-invariance clause on
  the 08-04 a_cal split; SEED+5 reservation asserted against `conjunction_power`'s
  data-dependent offsets, not assumed; pilot-allowlist extension stated explicitly).
  Reviewer B — **APPROVE** (crossed-quantile orientation independently re-verified;
  3 notes applied: same boundary correction; pre-run uniform-1/3 approximation recorded
  as BOUNDED once the seeded tie-break removes date-clumping; wording residuals noted,
  no change).

**v4 APPROVED by both split-mandate Opus reviewers (stats/inference; microstructure/
data-mechanics) on 2026-08-05 after 4 rounds (v1 BLOCK+APPROVE-WITH-CHANGES, v2/v3
APPROVE-WITH-CHANGES×2, v4 APPROVE+APPROVE). THIS AMENDMENT IS LAW. Amended stages may
run once the Build obligations (delta) are built and their pinned tests are green.**
