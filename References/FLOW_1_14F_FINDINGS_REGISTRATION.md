# 1.14f/1.14g findings registration — the record frozen before the test window opens

*⚠️ DRAFT v1 — NOT LAW until its gauntlet converges. Discharges **obligation 1 of Addendum
G §G.10** (`FLOW_BASELINE_PREREG_2026-07-26_ADDENDUM_G.md`): the intraday-screen findings
register HERE, in a dated committed document, before any pre-run obligation executes and
before the untouched test split (2026-06-01 → 2026-07-25) is opened by the G.2 scoring run.
Purpose: freeze what the training window taught us — findings, adjudications, wrong
expectations, and the follow-up hypotheses they generate — so that no post-hoc narrative
can connect them to the test-window grade after the grade exists. Sources of record:
roadmap §1.14f RESULT (`FLOW_ROADMAP.md:1601-1628`), the 1.14g result
(`data/research/mover_intraday_114g.json`; BUILD_LOG 2026-07-30), and the founder
dispositions of 2026-07-30. Nothing here carries survival authority; nothing here touches
a test-split outcome. Orchestrator, 2026-07-31.*

---

## R.1 The findings of record (train window only; 212 sessions; single registered runs)

Both runs were single registered executions (1.14f SEED=20260808, 1.14g SEED=20260809)
behind pre-registrations that survived pre-run adversarial review; 1.14g additionally
reproduced 1.14f bit-identically before emitting anything. All figures verified against the
emitted JSONs.

**F1 — `vol_max_ratio` +0.164★ [+0.144, +0.184] (1.14f primary cell, W=60/H=2h).** Fired
names' next-2h max-|move| runs 1.156× their own time-of-day-matched baseline vs 0.992× for
matched complement names. Stars all three horizons, replicates at W=30 and W=120 and in the
join≥98 arm (+0.158★). **1.14g adjudication:** stratified on contemporaneous equity RV the
effect attenuates to **+0.121★ [+0.107, +0.135]** — still starred, same sign, clear of its
registered negligible band ⇒ **PRODUCT: NOT DEMOTED**; but retention through
stratification is **0.708**, BELOW the 0.848 split-sample pure-RV placebo, and the balance
gate FAILED (below) ⇒ **SCIENCE: no survival authority**, leaning "mostly RV in disguise."

**F2 — `largest_print_ratio` +0.114★ [+0.100, +0.130] (same cell).** Fires on real money
(median fired-window premium $354k). Full reading rule; join≥98 +0.107★. **1.14g:**
stratified **+0.078★ [+0.069, +0.088]** — NOT DEMOTED as product; retention **0.650** vs
the 0.848 placebo ⇒ same science verdict as F1.

**F3 — `concentration` −0.021★ [−0.029, −0.012] (same cell), NEGATIVE.** One-burst-dominated
premium marks a QUIETER next 2h. The earnings split flips it (in-gate +0.017, far −0.025):
reads as mechanical selection, not signal. **1.14g:** stratified **−0.020★ [−0.026,
−0.013]** — NOT DEMOTED; retention split 0.96/0.63 across windows; mostly dies on the
lagged panel.

**F4 — `short_dte_share` transplant FAILED.** The recurring day-grain 5d positive is NULL
at intraday grain (+0.003 [−0.008, +0.015], b=0/3). One secondary cell starred with the
expected sign (W=120/H=close +0.014) — disclosed, no survival authority. The day-grain
finding is not thereby dead; the TRANSPLANT is. Parked.

**F5 — Direction: all four components NULL** (±0.005 of coin-flip agreement between window
tilt-sign and signed forward return). The screen is **magnitude selection with zero
directional information** at this grain and horizon.

**Shared caveats of record:** (a) the 1.14g balance gate failed everywhere it was tested —
the top RV quintile holds ~37% of fired mass with residual RV gap 0.241 vs the 0.019
limit (worse than the 0.193 unstratified gap): fired names are the choppiest of the
choppy and matching cannot equalize them, so NO stratified estimate carries survival
authority ("the matching failed, not the hypothesis"); (b) the free equity-RV screen's own
raw delta is 0.32–0.43 — 3–4× the components' stratified residuals; (c) effects are LARGER
far from earnings (not the calendar in costume); (d) the lagged panel keeps F1/F2 starred
at +0.08–0.10 across horizons (a real anticipatory residual is COMPATIBLE with the record
but not established by it).

## R.2 Expectations vs outcomes — recorded, including the misses

The 1.14g pre-registration recorded expectations in advance; the record keeps score:
- "Arm 2a strongly positive" — **RIGHT** (+0.398★, machinery control).
- "Every component attenuates substantially" — **RIGHT** (retentions 0.65–0.71).
- "`largest_print_ratio` the likelier science survivor" — **WRONG in effect**: it retained
  LESS than vol_max (0.650 vs 0.708) and neither beat the placebo.
- "`concentration` expected PRODUCT-DEMOTED" — **WRONG**: it cleared its negligible band.
- 1.14f's own registered motivated positive (`short_dte_share`) — **WRONG at this grain**
  (F4).
Kept on the record because a registration program that only remembers its hits is
calibrating nothing.

## R.3 What these findings do — and do NOT — license for the G.2 scoring run

Registered BEFORE the test window opens, so it cannot be written the other way around:

1. **No transfer of authority in either direction.** F1–F5 are intraday (2h–close),
   magnitude-only, train-window findings. The G.2 primary cell is a multi-day (10td),
   DIRECTIONAL, test-window cell. Whatever the scoring run returns, neither "1.14f starred
   so the mechanism was real all along" (if ALIVE) nor "1.14f already showed flow carries
   nothing" (if DEAD) is a licensed reading — different grain, different axis, different
   window.
2. **Pre-stated prior, for honesty not authority:** the drafting expectation is that the
   G.2 run returns DEAD — DEAD (dilution) specifically is disclosed inside Addendum G as a
   realistic outcome (approximate judgment-window Q5 f ≈ 7.9% vs f₀ ≈ 8.6%), and F5's
   direction-null, while at a different grain (weak transfer, stated as such), points the
   same way. Recording the prior now prevents a DEAD verdict from being retold as a
   surprise, and an ALIVE verdict from being retold as expected.
3. **The perception-surface disposition is already made and is not contingent on G.2:**
   founder, 2026-07-30 — the 1.14f-shaped flow perception surface is PARKED ("it's a
   possibility but at this time it's not providing any value"); the product direction that
   inherits F1/F2's magnitude finding is the **vol/volume abnormality scanner** (card
   `vol-abnormality-scanner`), built on equity data the RV comparison showed is 3–4×
   stronger anyway. A G.2 ALIVE would be a reason to REVISIT, by founder decision — never
   an auto-unpark.

## R.4 Registered follow-up hypotheses (labelled; no survival authority; each carries its
mechanism sentence per the 2026-07-30 standing rule)

**H1 — Opening-flow on a quiet tape (the anticipatory complement).** The 1.14g record
says premium bursts ride names that are ALREADY moving. The undischarged interesting case
is flow WITHOUT contemporaneous movement. *Mechanism sentence: a trader with genuine
private information prefers to size BEFORE the move on a quiet tape (better prices, lower
impact), and prefers opening new positions to closing old ones — so opening-classified
flow (requires the Phase-2 OI work) conditioned on LOW contemporaneous RV strips out the
reactive/momentum share of the tape that 1.14g showed dominates the unconditioned signal;
the counterparty is a market-maker quoting a quiet book, not a crowd chasing a move.*
Status: hypothesis only; MUST be pre-registered (its own dated document, gauntlet per
house rule) before any measurement runs; gated on OI availability. **Together with the
G.2 test-window grade, this is the registered fair trial of the Databento `tcbbo`
subscription (founder rule, 2026-07-30): if both come up empty, the subscription is
cancelled and the bought corpus is retained.**

**H2 — 1.14h row-level identity gate (parked).** `data/research/mover_intraday_114g_fired_keys.npz`
persists the fired row keys so a future 1.14h can run a stronger row-level identity gate
than 1.14g's cell-level reproduction gate. *Mechanism sentence: none — this is a
methodology upgrade, not an edge claim.* Parked, not scheduled.

## R.5 Amendment rule

This document inherits Addendum G's regime: first-run-governs; any change to what is
registered here is a NEW dated addendum to this file, never an edit; an outcome statistic
computed on the test split before the G.2 run voids per G.10. The findings above are the
train window's complete star/null record for the intraday screen — nothing withheld,
nothing added later without a date.
