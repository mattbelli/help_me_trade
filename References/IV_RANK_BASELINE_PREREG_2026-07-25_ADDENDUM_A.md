# Addendum A to the IV-Rank Baseline Pre-Registration (2026-07-25)

Amends `IV_RANK_BASELINE_PREREG_2026-07-25.md`. Written **after** seeing the primary
result and **before** computing any number defined here — which is exactly the case the
parent document reserves an addendum for. The primary verdict stands as pre-registered
and is NOT restated, rescored or softened by anything below. Every arm here is
**SECONDARY and exploratory**, and is reported with that label attached.

## Why an addendum was needed

The primary run surfaced two things the parent design did not anticipate:

1. **The panel truncated to 2024-08-12 → 2026-06-12** (648,231 observations, 1,676
   symbols, 34,507 symbol-months). `RV_fwd` was defined as `hv_current` **30 rows**
   ahead, guarded to a 28-60 day span. In the corpus's sparse era (~60% of sessions
   through 2020-22) 30 rows ahead spans ~70 calendar days, so every pre-2024
   observation was correctly refused as mis-horizoned. The parent forbids repairing
   this inline — *"never interpolated to the nearest date"* — so it is repaired here,
   as a separate arm, rather than by editing the primary.
2. **IV rank and IV level are entangled.** Mean IV rises monotonically across rank
   deciles (0.2818 → 0.6157). H2/H3 passing is therefore consistent with a pure
   *level* effect wearing a rank costume. This distinction is not academic: "sell when
   a name's IV is high **for that name**" and "sell whichever names have the highest
   IV" are different strategies with different risk, and only the first is what an
   IV-rank gate claims to do.

## A1 — Horizon recovery (recovers 2019-2024)

`RV_fwd_dated(t)` = `hv_current` at the row whose date is nearest to **t + 44 calendar
days** (30 sessions × 365/252), accepted only within **±7 days**, else dropped and
counted. Because `hv_current` is a 30-session trailing window (established in the
parent's Instruments section), that row's window covers approximately (t, t+30
sessions] regardless of how densely the corpus sampled the intervening period.

Re-runs H1/H2/H3 unchanged, on the recovered panel. **Reported separately from the
primary — it does not overwrite it.** The parent's with/without-2020 split becomes
meaningful here for the first time (the primary panel contains no 2020 data at all, so
that split was vacuous and is reported as such).

## A2 — Level-vs-rank control (the confound)

The decisive arm. Two tests:

- **A2a, level sort:** rebuild the decile table sorting on **IV level** instead of IV
  rank. If the level table is as monotone as the rank table, rank adds nothing.
- **A2b, double sort:** partition into **IV-level quintiles**, and within each, into
  **IV-rank quintiles**. Report mean VRP for all 25 cells. This asks the only question
  that matters for a gate: *holding IV level roughly fixed, does a name's IV being high
  relative to its own year still predict a larger VRP?*

**Thresholds, fixed now:** the rank effect is judged REAL only if (i) within **every**
IV-level quintile the top rank-quintile mean VRP exceeds the bottom rank-quintile mean,
and (ii) the pooled within-level difference has a 95% month-block CI excluding zero.
Anything less and the honest description is a **level** effect, and an IV-rank entry
gate would be a fabricated rationale.

## A3 — Conditional premise

The primary's H1 tested the *unconditional* premise (sell everything, always) and its
CI straddled zero. The conditional question was never given a threshold, so it gets one
now: **top IV-rank decile mean VRP > 0 with a 95% month-block CI excluding zero.**

A3 passing does **not** revive H1 and does not by itself open the lane. It changes only
which follow-up is worth building: a strategy backtest restricted to high-rank entries,
versus abandoning the VRP premise entirely.

## Unchanged from the parent

Statistics (month-block bootstrap, 2,000 draws, effective n in symbol-months), the
mandatory tail columns on every mean, the curated-universe caveat, and the honesty
boundary — **VRP in vol points is not P&L**, no number here licenses arming anything,
and none of it may reach a user-facing surface as a scored signal.
