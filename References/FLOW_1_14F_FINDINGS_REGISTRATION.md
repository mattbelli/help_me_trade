# 1.14f/1.14g findings registration — the record frozen before the test window opens

*⚠️ DRAFT v3 — NOT LAW until its gauntlet converges (round 1: 9 unique blockers — 6
methodology + 4 record, 1 shared; round 2: 6, every one a one-clause record fix — all
resolved below). Discharges **obligation 1 of
Addendum G §G.10** (`FLOW_BASELINE_PREREG_2026-07-26_ADDENDUM_G.md`): the intraday-screen
findings register HERE, in a dated committed document, before obligations 2→6 execute and
before the untouched test split (2026-06-01 → 2026-07-25) is opened by the G.2 scoring run.
(Obligation 1 is itself a pre-run obligation — this document is it.) Purpose: freeze what
the training window taught us — findings, adjudications, wrong expectations, and the
follow-up hypotheses they generate — so that no post-hoc narrative can connect them to the
test-window grade after the grade exists. G's preamble says "the three 1.14f findings";
this document registers five — the two nulls are ADDED, not substituted, because
anti-selection content is the point; no G obligation changes. Sources of record: roadmap
§1.14f RESULT (`FLOW_ROADMAP.md:1601-1628`), the emitted artifacts
`data/research/mover_intraday_114f.json` AND `mover_intraday_114g.json` (ground truth for
every number), BUILD_LOG 2026-07-30, and the founder dispositions of 2026-07-30. Nothing
here carries survival authority; nothing here touches a test-split outcome. Orchestrator,
2026-07-31.*

---

## R.1 The findings of record (train window only; 212 sessions; single registered runs)

Both results come from single registered executions behind pre-registrations that survived
pre-run adversarial review (1.14f SEED=20260808; 1.14g SEED=20260809 — 1.14g took three
launches: #1 aborted at the reproduce gate ITSELF — counts integer-exact and medians
digit-exact at all 36 cells, but 6 cells' diff off by exactly 1e-5 because the gate
subtracted already-rounded medians while the locked 1.14f writer rounds the difference of
the unrounded ones (fixed `98ebefb`, regression-pinned; the cosmetic gate-log fix
`92bfd08` rode along) — the gate fired correctly on a real mismatch; #2 died on an
over-broad Arm 2b assert; both aborts BEFORE any
statistic was emitted, so the fix loop stayed uncontaminated; #3 ran end-to-end). 1.14g
additionally reproduced 1.14f's aggregate signature at all 36 cells before emitting
anything — counts integer-exact, medians and diffs to 5 decimals; **aggregate-only, never
a row-level identity proof** (the artifact's own `fire_set_provenance` stamp; the
row-level gate is exactly what H2 below is for). All figures verified against the emitted
JSONs. 1.14g's registered reading-rule outcome, in its own grammar: **(a) pass, (b) pass,
(c) fail, (d) fail; balance INADEQUATE** — for all three starred components.

**F1 — `vol_max_ratio` +0.164★ [+0.144, +0.184] (1.14f primary cell, W=60/H=2h).** Fired
names' next-2h max-|move| runs 1.156× their own time-of-day-matched baseline vs 0.992× for
matched complement names. Stars all three horizons, replicates at W=30 and W=120 and in the
join≥98 arm (+0.158★). **1.14g adjudication:** stratified on contemporaneous equity RV the
effect attenuates to **+0.121★ [+0.107, +0.135]** — still starred, same sign, clear of its
registered negligible band ⇒ **PRODUCT: NOT DEMOTED**; but retention through
stratification is **0.708**, BELOW the 0.848 split-sample pure-RV placebo (the registered
(d) benchmark), and the balance gate FAILED (caveat (a)) ⇒ **SCIENCE: no survival
authority**, leaning "mostly RV in disguise" (counterweighted by caveat (d)).

**F2 — `largest_print_ratio` +0.114★ [+0.100, +0.130] (same cell).** Fires on real money
(median fired-window premium $354k). Full reading rule; join≥98 +0.107★. **1.14g:**
stratified **+0.078★ [+0.069, +0.088]** — NOT DEMOTED as product; retention **0.650** vs
the 0.848 placebo ⇒ same science verdict as F1 (counterweighted by caveat (d)).

**F3 — `concentration` −0.021★ [−0.029, −0.012] (same cell), NEGATIVE.** One-burst-dominated
premium marks a QUIETER next 2h — and the registered mechanical descriptive that precedes
any reading says why: fired windows are SMALLER and SPARSER (median $103.9k premium / 44
prints vs $174.1k / 133 for the complement). The earnings split flips it (in-gate +0.017,
far −0.025): reads as mechanical selection, not signal. **1.14g:** stratified **−0.020★
[−0.026, −0.013]** — NOT DEMOTED; retention 0.96 at the primary cell / 0.63 at W=120;
mostly dies on the lagged panel (starred only at H=2h).

**F4 — `short_dte_share` transplant FAILED.** The recurring day-grain 5d positive is NULL
at intraday grain on the registered median statistic (+0.003 [−0.008, +0.015], b=0/3). One
secondary cell starred with the expected sign (W=120/H=close **+0.014 [+0.005, +0.023]**)
— disclosed, no survival authority. The day-grain finding is not thereby dead; the
TRANSPLANT is. Parked.

**F5 — Direction: all four components NULL** (±0.005 of coin-flip agreement between window
tilt-sign and signed forward return). The screen is **magnitude selection with zero
directional information** at this grain and horizon.

**Shared caveats of record:**
- **(a) The 1.14g balance gate failed for all three components** (figures below are
  `vol_max_ratio`'s row; the other two fail on their own scales — largest_print 31.3% /
  0.151 vs 0.0125; concentration 19.3% / 0.0166 vs 0.00089 — concentration's recorded
  failure is the 4-of-5-quintiles clause, its heavy-quintile test passed): the top RV quintile holds
  ~37% of fired mass with residual RV gap 0.241 vs the 0.019 limit — WORSE than the 0.193
  unstratified gap, i.e. the stratified estimates are UNDER-adjusted, so "NOT DEMOTED" is
  a failure to demote under an under-powered adjustment, not a product claim. No
  stratified estimate carries survival authority ("the matching failed, not the
  hypothesis").
- **(b)** The registered magnitude comparison for science is retention vs the split-sample
  placebo — 0.708 / 0.650 vs 0.848 — and that is the ONLY cross-screen comparison this
  document carries: the §1.14g registration FORBIDS comparing Arm 2a's unstratified
  equity-RV Δ against the components' stratified Δs ("NOT magnitude-comparable … forbids
  demoting or promoting by comparing those two numbers"). A "3–4×" version of that
  forbidden comparison lived in FOUR places — the 2026-07-30 BUILD_LOG run row
  (append-only; superseded by this caveat), board cards `flow-perception-ship` (title)
  and `1-14f-vol-matched-followup` (notes), and `memory/vol-scanner-direction.md` — the
  living three are CORRECTED in this document's own commit, and the comparison may not be
  quoted from this program's record.
- **(c)** Effects are LARGER far from earnings (vol_max far +0.180 vs in-gate +0.158) —
  not the calendar in costume.
- **(d)** The lagged panel keeps F1/F2 starred at +0.06–0.10 across all horizons — a real
  anticipatory residual is COMPATIBLE with the record but not established by it.
- **(e) Registered denominator diagnostics (recorded because they complicate the
  story):** on the RAW unnormalized forward move the estimand's point estimates are ≈
  ZERO (−0.001 / +0.000 / −0.002; no CI emitted; raw medians run 0.010–0.042, so these
  are 2–6% of level) — **F1–F3 are statements about a name moving more than is normal
  FOR THAT NAME, not evidence that fired names move more in absolute terms**; on outcome
  ÷ same-window rv_park the estimand is LARGER than the primary for `vol_max_ratio`
  (+0.212) and `largest_print_ratio` (+0.128), while `concentration` reads **+0.098,
  SIGN-FLIPPED against its registered −1 sign** — recorded as a flip per the 1.14g
  registration's own rule, never folded into "larger"; a secondary with no authority.
  The contemporaneous-complement (decision-time-population) arm retains +0.097 / +0.056 /
  −0.017; the decile re-cut +0.109 / +0.070 / −0.019; the close-to-close stratifier
  variant reads +0.142 / +0.098 / −0.020 (BOTH positive components above their
  primaries); the zero-gap-window variant +0.118 / +0.076 / −0.021 (at the primaries).
  All variants share the FIRED set (complement/stratum rows differ per each variant's
  registration); none
  changes a verdict; all are on the record so none can be "discovered" later.
- **(f)** The 3× tail-rate secondary is clear of zero at the primary cell for all four
  components — including `short_dte_share` (+0.006 [+0.003, +0.009], starred on tail in 9
  of 9 all-train cells, 7 of 9 in the join≥98 arm) whose registered median statistic is
  null (F4). Recorded, no
  survival authority; F4's verdict is the median statistic's, per its registration.

## R.2 Expectations vs outcomes — the full scorecard, misses kept

The 1.14g pre-registration recorded five expectations in advance (roadmap:1908-1916,
re-stamped in the artifact); all five are scored, plus 1.14f's own:
- "Arm 2a strongly positive" — **RIGHT** (+0.398★, machinery control).
- "Every component attenuates substantially" — **RIGHT for two of three** (vol_max 0.708,
  largest_print 0.650; concentration barely attenuated at the primary cell — 0.96, 0.63
  at W=120). On the record before it can be "found" later: both RIGHT components
  retained MORE than the ~40–57% the registration's own zero-flow-information simulation
  prior predicts — retention alone distinguishes nothing, which is why (d) is the
  benchmark.
- "`vol_max_ratio` most exposed (numerator mechanically coupled to RV)" — **WRONG**: it
  retained MORE than largest_print (0.708 vs 0.650), the less exposed of the two.
- "`largest_print_ratio` the likelier science survivor" — **WRONG**: it retained least,
  and neither beat the placebo.
- "`concentration` expected PRODUCT-DEMOTED" — **WRONG**: it cleared its negligible band.
- 1.14f's own registered motivated positive (`short_dte_share`) — **WRONG at this grain**
  (F4).
- 1.14f's registered NEGATIVE day-grain forward priors on `vol_max_ratio` and
  `largest_print_ratio` (from 1.14e; "a negative finding here is confirmatory color") —
  **WRONG, in the informative direction**: both starred strongly POSITIVE at intraday
  grain (the run row's own headline: "REAL but not where the prior pointed").
  `concentration` had no surviving prior to score.
Kept in full because a registration program that only remembers its hits is calibrating
nothing.

## R.3 What these findings do — and do NOT — license for the G.2 scoring run

Registered BEFORE the test window opens, so it cannot be written the other way around:

1. **No transfer of authority in ANY of the four verdict×finding pairings.** F1–F5 are
   intraday (2h–close), magnitude-only, train-window findings. The G.2 primary cell is a
   multi-day (10td), DIRECTIONAL, test-window cell. Unlicensed readings, enumerated:
   "1.14f starred so the mechanism was real all along" (if ALIVE); "1.14f already showed
   flow carries nothing" (if DEAD); **F1/F2's stars may not soften or partially rescue a
   DEAD** ("the intraday grain works, the 10td cell was the wrong grain" is a partial
   rescue G.2 bans by another door); **the R.3.2 prior and F5 may not be cited to
   discount, downgrade, or raise the evidentiary bar on an ALIVE** — the prior has zero
   evidentiary weight in either direction and may not enter the verdict's adjudication.
   An UNEVALUABLE licenses no substitute cell, re-declaration, or re-run (G.2's own ban),
   and nothing here softens it. Nor does the transfer run backwards: no G.2 verdict
   re-grades F1–F5, and a DEAD
   (dilution) in particular makes NO claim about the fired set (G.2 step 2's registered
   wording) and therefore says nothing about these findings.
2. **Pre-stated prior, for honesty not authority:** the drafting expectation is that the
   G.2 run returns DEAD — DEAD (dilution) specifically is disclosed inside Addendum G as
   a realistic outcome (approximate judgment-window Q5 f ≈ 7.9% vs the v6-fence f₀ = 8.6% — carrying
   G's own qualifier: NOT comparable as registered, different fences, both sides
   re-measured at obligation 3; and "Q5" is G's pre-stated expectation, obligation 3b
   selects the tier), and F5's direction-null, at a different grain (weak transfer, stated
   as such), points the same way. Recording the prior now prevents a DEAD verdict from
   being retold as a surprise and an ALIVE from being retold as expected — and per R.3.1
   the prior itself licenses nothing.
3. **The perception-surface disposition is already made and is not contingent on G.2:**
   founder, 2026-07-30 (recorded in `memory/vol-scanner-direction.md`, indirect speech —
   no reason to surface something a simple volatility scanner picks up; the result should
   be known and it stays a possibility, but it provides no value today): the 1.14f-shaped
   flow perception surface is PARKED; the product direction that inherits F1/F2's
   magnitude finding is the **vol/volume abnormality scanner** (card
   `vol-abnormality-scanner`). That disposition is a founder decision and needs no
   statistical support from any cross-screen comparison (see caveat (b)). A G.2 ALIVE
   would be a reason to REVISIT, by founder decision — never an auto-unpark.

## R.4 Registered follow-up hypotheses (labelled; no survival authority; each EDGE-CLAIM
hypothesis carries its mechanism sentence per the 2026-07-30 standing rule)

**H1 — Opening-flow on a quiet tape (the anticipatory complement).** The 1.14g record
says premium bursts ride names that are ALREADY moving. The undischarged interesting case
is flow WITHOUT contemporaneous movement. *Mechanism sentence: a trader with genuine
private information prefers to size BEFORE the move on a quiet tape (better prices, lower
impact), and prefers opening new positions to closing old ones — so opening-classified
flow conditioned on LOW contemporaneous RV strips out the reactive/momentum share of the
tape that 1.14g showed dominates the unconditioned signal; the counterparty is a
market-maker quoting a quiet book, not a crowd chasing a move (the persistence half of
the mechanism is discharged in H1's own prereg).* Status: hypothesis only; MUST be
pre-registered (its own dated document, gauntlet per house rule) before any measurement
runs; **gated on Phase-2 §2.3, whose registered ceiling applies: ΔOI is a daily net
published pre-open for the prior close — an aggregate contract-day estimate of open
share, never a per-print or intraday label (§2.3), and blind to which party opened
(§2.4's registered honesty note). H1's prereg
must state whether it measures on that T+1 proxy (research-only) or defines a
live-observable substitute; a null attributable to the instrument is recorded as such,
not as a discharge of the fair trial.** Together with the G.2 test-window grade, H1 is
the registered fair trial of the Databento `tcbbo` subscription (founder rule,
2026-07-30). **The subscription consequence is NON-ADMISSIBLE in the measurement: no
coordinate, cell, reading rule, or re-run of H1 may be chosen, widened, or repeated
because the subscription outcome depends on it, and a null H1 on its first registered
run is a complete discharge of the fair trial (first-run-governs). The fair trial adds
no criterion to G.2 and does not gate the scoring run; "empty" is deliberately not
defined here — the trial delivers two recorded verdicts and the cancellation decision is
the founder's, made on that record** (the bought corpus is retained either way).

**H2 — 1.14h row-level identity gate (parked).** `data/research/mover_intraday_114g_fired_keys.npz`
persists the fired row keys so a future 1.14h can run the row-level identity gate that
1.14g's aggregate-signature reproduction (R.1) deliberately is not. *Mechanism sentence:
none — this is a methodology upgrade, not an edge claim.* Parked, not scheduled.

## R.5 Amendment rule

This document inherits Addendum G's regime: first-run-governs; any change to what is
registered here is a NEW dated addendum to this file, never an edit. As this document's
OWN additional undertaking (stricter than G.10's "before law" wording, attributed as
such): an outcome statistic computed on the test split before the G.2 run voids the run.
The findings above are the train window's complete star/null record **for the registered
primary (median) statistics of the intraday screen**, with every registered secondary
that complicates the story disclosed in caveats (e)/(f) — nothing withheld, nothing added
later without a date.
