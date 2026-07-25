# The spread gate — is the +0.0445 premium reachable? (PRE-REGISTERED 2026-07-25)

*Status: CURRENT. Follows `IV_HORIZON_BREADTH_PREREG_2026-07-25.md` (Study C), which raised this.*

⚠️ *Provenance, stated accurately: this was **written** in full — thresholds, cohorts, the
closed-market asymmetry, the refusal rule — before the probe was built or run, but it was
**committed in the same round as the results**. Study C's prereg was committed first (`36a49c8`,
before its run) and this one was not. The git history therefore does not independently prove the
thresholds predate the data; the reader has only the ordering above. Noted rather than glossed,
because the value of a pre-registration is exactly its verifiability.*

## The question

Study C measured a variance risk premium of **+0.0445 vol points** (CI [+0.0178, +0.0661]) in the
top IV-level decile under a 60% ceiling, across 2,083 symbols — and **−0.0035** on the 37 names we
actually trade, at nearly the same implied vol (0.554 vs 0.547). The discriminator is *which names*.

The hypothesis is competition: premium survives where few people sell it. If true, that is bad news,
because thinly-sold options are thinly-quoted options. **Every number in Studies A, B and C is
derived from vendor mid-market IV and pays no bid-ask whatsoever.** A seller does not trade at the
mid; they sell at the bid.

So: express the bid-ask in the same unit as the edge, and see what is left.

## Metric

For an at-the-money option near 30 DTE:

    half_spread_vol_pts = 0.5 × (ask − bid) / vega

with vega on a per-1.00-of-vol basis, so the result is directly comparable to **0.0445**. The half
spread is the cost of crossing **once** — selling to open and holding to expiry. A position that is
also bought back pays it **twice**.

Vendor vega is used, and its convention is **verified, not assumed**: the probe prints vendor vega
against the closed-form ATM value `0.3989 × S × √T` for the same contracts. A mismatch invalidates
the run rather than being silently rescaled.

## Cohorts

- **BAND** — the names that carry the premium. Taken from the corpus's most recent session
  (2026-07-24): symbols whose `iv_current` sits in the **top decile of that day's cross-section**
  and is **below 0.60**. This is the tradable cohort as it exists now, not a historical average.
- **DEEP** — the 37–53 names we actually trade, as the control. Study C says the premium is absent
  here; the question is whether the spread is correspondingly tighter.

Both cohorts measured the same way, same session, same contract-selection rule.

## Thresholds, fixed before the run

Read against the **+0.0445** edge, using the **median** half-spread per cohort (the mean would be
hostage to one untradeable name):

| median half-spread | verdict | consequence |
|---|---|---|
| **≤ 0.010** (≤ ~22% of the edge) | **CHEAP** | the spread does not explain the premium; the capped-payoff study becomes the sole remaining gate |
| **0.010 – 0.022** (22–50%) | **ERODED** | the edge is thin but alive; any capture estimate must net the spread explicitly, and a round trip is marginal |
| **> 0.022** (>50% one-way) | **PROHIBITIVE** | a sell-and-close round trip costs more than the edge; the C4 premium is treated as **substantially an artifact of quoting at the mid**, and premium selling in these names is recorded as not reachable by this route |

## The closed-market caveat, and why it makes the test asymmetric

This is registered on **Saturday 2026-07-25**. The market is shut, so every quote is Friday's
closing book, verified from quote timestamps (`19:59:59Z` = 15:59:59 ET). Closing spreads are
**wider** than intraday spreads.

That makes the measurement a one-sided bound, and the two outcomes are not equally conclusive:

- **CHEAP is decisive.** If the spread is small even at the worst quoting moment of the week, it is
  small.
- **PROHIBITIVE is not decisive.** It must be re-run intraday before it is recorded as a finding.

Registering this now, so that a wide result on Saturday cannot later be quietly promoted into a
verdict it does not support.

## Honesty boundary

A tight spread does **not** open `options-income-lane`. The capped-payoff gate is untouched by this
measurement: a seller's upside stops at the premium while the left tail does not, and in the C4 band
29.7% of observations were negative with a 5th percentile of −0.3105. This probe can only ever
remove one of two obstacles.

## Refusal rule

A contract with no vega, a zero or crossed bid, or no quote is **dropped and counted**, never
imputed. A symbol whose chain does not reach the DTE window is dropped and counted. Coverage is
reported alongside every statistic; a cohort with under 20 usable symbols is reported as
**unmeasured**, not as a number.

---

## POST-RUN NOTE — 2026-07-25 (results; thresholds above unchanged)

**Instrument check first.** Vendor vega is per-1%-of-vol as assumed: median vendor/closed-form ratio
**0.995** across 262 contracts. A scale error here would have moved every number by 100×, so this is
the number that licenses the rest. Quote timestamps confirm the caveat is live —
`2026-07-24T19:59:4x–19:59:59Z`, i.e. Friday 15:59 ET, the closing book.

Cohort: latest corpus session 2026-07-24, cross-section 1,444 symbols, 1,105 under the 0.60 ceiling,
top decile of those = **111 names, cutoff 0.530, mean IV 0.564** — which lines up with C4's own
band mean of 0.554, so this is the right cohort. 80 probed, 79 usable; 53 deep names, 53 usable.

⚠️ **Deviation from the prereg wording, disclosed rather than quietly absorbed.** The Cohorts section
above says "top decile of that day's cross-section **and** below 0.60", i.e. decile first. As
implemented it is **ceiling first, then the top decile of what remains** — which is what C4 itself
does, and is why C4's band mean IV is 0.554 rather than something far higher. The prereg order is
not merely different, it is **empty**: the latest session's true top decile starts at **0.767**, well
above the 0.60 ceiling, so nothing at all would have been probed. The dry run caught this before any
vendor call. The prereg text is left as written; this note is the correction, and a regression test
(`test_band_cohort_applies_the_ceiling_before_the_decile`) now pins the order.

| cohort | n | median half-spread | share of the 0.0445 edge | median spread as % of premium | verdict |
|---|---|---|---|---|---|
| **BAND** (top IV decile, IV < 0.60) | 79 | **0.0870** | **195%** | **31.2%** | PROHIBITIVE |
| **DEEP** (names we trade) | 53 | **0.0292** | 66% | **9.8%** | PROHIBITIVE |

- **The competition hypothesis is confirmed in direction.** The names carrying the premium are
  **~3.2× more expensive to trade** than ours (31.2% vs 9.8% of premium). Study C could only show
  that the discriminator was *which names*; this says what is different about them.
- **The magnitude is worse than the edge.** Crossing once in the band costs **about twice** the
  entire measured premium. Only **17 of 79** band names have a one-way cost below the full edge, and
  only **4 of 79** below half of it — the threshold a sell-and-buy-back round trip would need.
  Even the tightest name in the cohort (NOW, 0.0189) eats 42% of the edge in a single crossing.
- **The control behaves as expected.** Our own names are ~3× tighter, which is consistent with them
  being the most heavily option-sold instruments on the market. It is also academic: Study C says
  there is no premium there to collect (−0.0035).

### The pre-registered caveat is binding, and it is doing real work here

The prereg states plainly that **CHEAP would be decisive and PROHIBITIVE would not**, because
Saturday quotes are the closing book and closing spreads are wider than intraday. That is exactly
the result we got, so **this is not yet recorded as a finding**. What it is: a strong upper bound
that must be re-run intraday before anything is concluded.

⏳ **Open: re-run `tools/option_spread_probe.py` during a live session** (Monday 2026-07-27 or later).

One thing does survive the caveat better than the absolute levels: **the ratio between the cohorts**.
Both were measured in the same instant under identical conditions, so the 3.2× gap is far less
sensitive to the closing-book widening than either number alone. It is not immune — illiquid names
may widen *more* into the close, which would exaggerate the ratio — but the direction is safe.

### Two honest mitigants, recorded so the intraday run is read fairly

1. **The half-spread is the cost of a market order.** A patient seller resting a limit between mid
   and bid captures some of it. In thinly-quoted names, though, resting at the mid frequently does
   not fill at all — which is the same illiquidity the spread is measuring.
2. **This measures the ATM contract.** A wheel sells OTM puts, whose relative spreads are typically
   *worse*, not better. ATM is the friendly case, so the real cost is unlikely to be lower.

### What this does not change

`options-income-lane` stays shut, and it would have stayed shut on a CHEAP result too. The
capped-payoff gate is untouched by anything measured here: in the C4 band 29.7% of observations were
negative with a 5th percentile of −0.3105, and a seller's upside stops at the premium.
