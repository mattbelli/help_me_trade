# Flow baseline prereg — Addendum C: the direction decision rule

*Registered 2026-07-26, before the 260-session corpus lands. Companion to
`FLOW_BASELINE_PREREG_2026-07-26.md` and Addenda A (classification rules) and B (spot reference).*

## Why this addendum exists

Addendum A registered *how a print is signed*. It did not register *how a bucket's direction is
read*, and the audit of 2026-07-26 showed that gap is load-bearing: the answer depends on which
summary statistic you pick, and the disagreement is concentrated in the buckets that hold the money.

`u = (price − mid) / half` is a ratio with the half-spread underneath, so it is **unbounded**.
Measured on 2025-07-28 (6,930,544 prints post-join):

| | |
|---|---|
| observed `u` range | **[−61, +90]** |
| prints with `\|u\|>2` | 27,972 — **0.40%** of the corpus |
| their share of `sum(u × premium)` | **171.92%** |
| `u_prem` total / `\|u\|>2` tail | `+366,445,672` / `+629,989,228` |

Past the touch, a larger `|u|` is not more conviction — it is a worse quote. 36% of those prints
share a `ts_event` with a *different* instrument (the multi-leg signature: a spread leg is priced by
the firm allocating it and need not sit inside that leg's own NBBO); the rest sit against stale or
wide quotes on illiquid contracts.

## The three measures

Registered in order of robustness. All three are already stored or derivable — no new capture.

1. **`net_premium`** = buy premium − sell premium, from the `side` key. Each print contributes at
   most its own premium, with a sign. **Bounded by construction, outlier-immune.** No column needed.
2. **`u_premium_clipped`** = `Σ clip(u, −1, 1) × premium`. Direction saturates where the quote does.
3. **`u_premium`** = `Σ u × premium`, **exactly as pre-registered in Addendum A. Unchanged.**

`u_premium` is not redefined by this addendum. The registered analysis still runs as registered;
measures 1 and 2 sit beside it.

## The rule

> A bucket is called **directional** only when `sign(net_premium)`, `sign(u_premium_clipped)` and
> `sign(u_premium)` are all non-zero and identical. Otherwise the bucket is **`abstain`** — reported
> as its own mass, never coerced to neutral and never resolved by picking a preferred measure.

A zero on any measure abstains (deny-by-default). Abstain is an outcome, not missing data.

## Declared abstention rate — the honest part

This rule was chosen **after** examining 2025-07-28. That session is therefore in-sample and cannot
confirm it. Measured there, across 45,384 buckets:

| | by bucket | by premium |
|---|---|---|
| all three agree | **92.15%** | **75.33%** |
| `u_raw` vs `u_clip` disagree | 0.24% | **15.8%** |

**The count-weighted view is the trap.** 0.24% of buckets reads as a rounding error; those same
buckets carry 15.8% of the premium. Any robustness check on this corpus must be premium-weighted.

**Pre-registered expectation for the confirmatory set** — the ~258 sessions not yet examined:
abstention between **20% and 35% of premium**. Landing outside that band is itself a finding and
must be reported, not absorbed by adjusting the rule.

## What would invalidate or change this

- Changing the clip bound from `[−1, 1]`, changing the agreement requirement (e.g. 2-of-3), or
  adding a fourth measure is a **`schema_version` bump and a new addendum**. It is not a tuning knob.
- `u_premium`'s definition is frozen. An agent may add measures beside it; an agent may not
  redefine it.
- Raw prints are retained, so any of the above is a re-roll, not a re-purchase.

## The empirical arm — what actually settles this

Which estimator is "correct" is not answerable in the abstract, and this addendum does not claim to
answer it. It is answerable in the outcome ledger.

Every idea is graded mechanically whether or not it is taken (`calibration-counts-everything`). So:
**log the directional call each of the three measures would have made, independently, alongside the
consensus call** — including on abstained buckets, where the individual measures still have opinions.
Then let the grading ledger rank them ordinally, the way `calibration-filter-not-goal` prescribes:
not to chase a precise win%, but to find out which lane predicts and prune the ones that do not.

This is registered now for one reason: **the comparison cannot be run retroactively if the calls are
not recorded as the corpus is built.** `signals/options_flow/corpus_direction.py` emits all four
(three individual + consensus) so a later study has the data to adjudicate with.

Nothing in this addendum claims edge. It commits us to a rule and to an abstention rate before the
data arrives, so that a directional read published later cannot be an artifact of a choice made
after seeing the answer.
