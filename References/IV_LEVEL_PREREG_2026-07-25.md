# IV-Level Study (Study B) — Pre-Registration (2026-07-25, before any Study-B number)

Successor to `IV_RANK_BASELINE_PREREG_2026-07-25.md` (+ `_ADDENDUM_A`), which established
that **IV rank is not a usable entry gate**: the rank effect did not survive holding IV
level fixed, and reversed in low-vol names. The exploratory control arm (A2a) showed the
**level** axis was the one actually ordering the variance risk premium — Spearman +1.000
on level-sorted deciles — but level was never given a threshold or a confidence interval.
This document gives it both, **before** any Study-B statistic is computed.

**What this gates:** whether premium selling (`options-income-lane`) has *any* surviving
entry basis, and if so whether that basis is a **timing rule** or merely a **stock-picking
rule** — a distinction that decides whether it is usable for a wheel at all.

**Standing rule:** thresholds and outcome-consequences below may not be revised after
seeing results. Revisions get a dated addendum and rescore from scratch.

## Why the obvious test is not enough

Sorting by IV level is *largely sorting by which stock it is*. High-IV names are
structurally different — small caps, biotech, event-driven — so a "high IV level" bucket
is mostly a **list of companies**, not a list of moments. Study A's own failure is the
warning: an effect can be perfectly monotone on the axis you sorted by and still be an
artifact of a variable you did not hold fixed. So Study B tests the confound first and
treats the decomposition (B3) as the decisive arm, not the headline decile table.

## Data and definitions

Corpus, instruments and drop rules are inherited unchanged from Study A, including the
proven identity of `hv_current` (30-session annualized close-to-close stdev, so forward
RV is that column read 30 sessions later) and the point-in-time status of the vendor's
IV-year extremes. `VRP = IV(t) − RV_fwd(t)` in annualized vol points; **positive means
sold rich**. Statistics are the same: **month-block bootstrap, 2,000 draws**, effective
n reported in **symbol-months**, mandatory tail columns (p05, worst symbol-month, share
negative) on every reported mean.

Primary panel for Study B is the **A1 calendar-anchored panel** (2019-02-09 → 2026-06-17,
~1.70M observations, 2,272 symbols) — the longest honest history available — with the
recent-only panel reported alongside.

Three axes, all point-in-time, all expressed as cross-sectional percentiles so the
decile tables are comparable:

- **LEVEL** — the observation's own `iv_current`.
- **BETWEEN** (stock selection) — the symbol's **trailing-365-day mean IV** as of *t*.
  Answers "is this a habitually expensive name?"
- **WITHIN** (timing) — `(iv_current − trailing-year mean IV) / trailing-year mean IV`.
  Answers "is this name expensive *relative to its own normal*, right now?"

BETWEEN and WITHIN are a decomposition of LEVEL into the part that is about the company
and the part that is about the moment. Both require the same **≥ 120 trailing
observations** floor Study A used.

## Hypotheses, tests and thresholds — all fixed now

- **B1 — level premise.** Mean VRP in the **top LEVEL decile** > 0, with a 95%
  month-block CI **excluding zero**, on the 7-year panel **and** with Feb-Apr 2020
  excluded. *(Both, not either.)*
- **B2 — level survives the rank control.** The mirror of the test rank failed: in a
  5×5 **rank × level** double sort, the pooled within-rank-quintile (top level quintile
  − bottom level quintile) > 0 with a 95% CI excluding zero, **and** correctly ordered
  inside **every** rank quintile.
- **B3 — timing vs selection (DECISIVE).** Run B1's test separately on the **BETWEEN**
  and **WITHIN** axes.
  - **B3-within passes** if the WITHIN top decile mean VRP > 0 with a 95% CI excluding
    zero. This is the only arm that can establish a **timing** rule.
  - **B3-between** is reported the same way but licenses nothing on its own.
- **B4 — tradable band.** Repeat B1 restricted to observations that are *plausibly
  wheelable*: symbols in our own sampler universe **and** `iv_current` below **0.60**
  annualized. A statistically real effect that lives only at 60%+ vol is not a strategy
  this product can run.
- **B5 — outlier robustness.** B1's top-decile mean must stay positive after dropping
  the **top 5% of VRP observations**, so the result cannot rest on a handful of
  jackpots.

## What each outcome licenses — decided before seeing results

| Outcome | Consequence |
|---|---|
| **B1 fails** | The level axis is dead too. Premium selling has no surviving entry basis on this corpus; `options-income-lane` closes as a VRP play and any revival needs a genuinely new argument. |
| **B1 passes, B2 fails** | Level is a rank effect in disguise — symmetric to Study A's failure, and equally disqualifying. Dead. |
| **B1+B2 pass, B3-within fails** | The effect is **stock selection, not timing.** It tells you *which names* carry premium, never *when* to sell one. Licenses a **universe-construction** study only; an entry-timing gate built on it would be a fabricated rationale. |
| **B1+B2+B3-within pass, B4 fails** | Real but **not tradable by us** — it lives outside the vol band this product can hold. Do not build; record and stop. |
| **All pass** | Licenses building an actual **wheel backtest** with strike selection, assignment and gap mechanics. Still not a live lane, and still not a win%. |
| **B5 fails** | Whatever else passed is reported as outlier-driven and gates nothing. |

## Honesty boundary (unchanged)

**VRP in vol points is not P&L.** Strike selection, delta, assignment path, gap risk,
early exercise and commissions are all absent, so nothing here measures what a wheel
earns — only whether the options were priced rich. No number from this study reaches a
user-facing surface as a scored signal, and none of it produces a calibrated win%.
A **null result is a publishable outcome** and is recorded with equal prominence.

## Outputs

`data/research/iv_level_study/` + one BUILD_LOG row stating the verdict against every
threshold above, whichever way it lands.

---

## POST-RUN NOTE (2026-07-25) — B5 was a miscalibrated instrument

**Nothing above is altered.** The thresholds stood and the verdict was scored against
them; this note exists so the broken test is not reused.

B5 was written as "drop the **top 5% of VRP observations**" to check the result is not
carried by a handful of jackpots. Because VRP is *correlated with the very axis B1 sorts
on*, a global 5% trim removed **31.6% of the top-level decile** — a one-sided cut that
strips a third of the winning bucket's upside while leaving every one of its losses in
place. It is a far harsher test than the one intended, and its failure is therefore
**not evidence that the effect is outlier-driven**. Measured both ways on the top decile:
one-sided global trim → mean **−0.0330**; symmetric 5%/5% trim (jackpots *and* disasters
removed) → mean **+0.0586**, median **+0.0771**.

B5 is recorded as FAILED because that is what was pre-registered, and per the outcome
table it gates everything else. But the honest reading is that outlier-robustness is
**untested**, not disproven. Any successor study must define the trim **within the bucket
being tested** and symmetrically — or better, replace it with the question that actually
matters for a short-premium seller: the payoff is **capped at the premium collected**
while the left tail is not, so a mean-VRP test overstates what a wheel can capture no
matter how it is trimmed. Model the capped payoff directly instead.
