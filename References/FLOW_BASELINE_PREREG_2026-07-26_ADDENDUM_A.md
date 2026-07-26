# Addendum A — classification rule, set from corpus characterization (2026-07-26)

*Dated addendum to `FLOW_BASELINE_PREREG_2026-07-26.md`, written the same day, **before any outcome
test has been run**. This changes which classification rule is PRIMARY. It is based on the
distribution of prints against the NBBO — a property of the corpus — not on any forward-return
result. No grading, no MFE/MAE, no returns have been computed at the time of writing.*

## What was measured

One full session of whole-market OPRA `tcbbo` (2026-07-23, 11,152,080 prints, 0.31 GB on disk):

```
class          prints        pct     premium_$M      pct
buy (≥ ask)    3,152,740    28.3%       9,748.7     21.4%
sell (≤ bid)   3,252,588    29.2%       9,581.4     21.0%
mid            4,549,467    40.8%      26,219.1     57.5%
no_quote         195,949     1.8%          15.6      0.0%
crossed            1,336     0.0%           7.6      0.0%
```

**Quote coverage is essentially complete — only 1.8% of prints arrive with no NBBO.** But the
strict at-the-quote rule the prereg specified resolves just **57.4% of prints and 42.4% of
premium**, because 40.8% of prints land strictly between bid and ask — and those mid prints carry
**the majority of the premium (57.5%)**. Under the original rule the primary arm would discard most
of the dollar flow the study exists to measure.

## Why the mid prints are recoverable

Spread position `u = (price − mid) / (half-spread)`, where −1 is at the bid and +1 at the ask:

```
at/below bid        3,249,864   30.0%      $9,572.0M   21.2%
bid-ish (-1,-.5)      316,648    2.9%      $2,160.1M    4.8%
weak sell (-.5,0)   1,553,889   14.4%      $9,695.3M   21.5%
exactly mid         1,190,344   11.0%      $3,296.1M    7.3%
weak buy (0,.5)     1,182,004   10.9%      $8,740.3M   19.4%
ask-ish (.5,1)        306,582    2.8%      $2,327.2M    5.2%
at/above ask        3,022,488   27.9%      $9,335.0M   20.7%
```

**Only 11.0% of prints sit exactly on the midpoint.** The other 89% are off-centre and carry a
side. This is not a wide-spread artifact — 77.9% of prints are on contracts whose relative spread
is under 10%.

## Revised classification (PRIMARY)

| side | rule |
|---|---|
| `buy_side` | price > midpoint |
| `sell_side` | price < midpoint |
| `unclassified` | price exactly at midpoint, or no/crossed quote |

This resolves **89.0% of prints and 92.7% of premium**, against 57.4% / 42.4% under the strict rule.

**Retained as SECONDARY, unchanged:** the strict at-the-quote rule (≥ ask / ≤ bid). It is the
higher-confidence, lower-coverage arm. Both are computed on every run and reported side by side; a
finding that appears under one and not the other is reported as such, never collapsed.

**Also retained:** the continuous `u` itself, stored per print-aggregate as a premium-weighted mean.
Binary side discards the difference between a print at the ask and one barely above the midpoint;
`u` keeps it and costs one float per rollup row.

## A bias that must be characterized before it is interpreted

The corpus shows a standing skew: sell 47.3% vs buy 41.6% of prints (47.5% vs 45.3% of premium)
under the midpoint rule. **This is not to be read as a market signal.** It is exactly the
asymmetric-error mode `CLASSIFICATION_LAB_PREREG_2026-07-16.md` names as the danger zone — symmetric
errors wash out of a net measure, asymmetric ones move its sign.

Pre-committed: every `tilt` is reported **net of the whole-market daily mean tilt** as well as raw.
A name is unusual relative to the market's own skew that day, not relative to zero. If the skew is
a real structural feature (put-heavy hedging demand), demeaning preserves the signal; if it is a
classification artifact, demeaning removes it. Either way the study does not depend on which.

## Correction to the record

The claim that whole-market `tcbbo` delivers "direction on 100% of prints", made in conversation on
2026-07-26 before this measurement, was wrong. The accurate statement is: a **quote** is attached to
98.2% of prints, and **side resolves for 89.0% of prints / 92.7% of premium** under the midpoint
rule.

For comparison, the system this replaces resolves 57.4% of events overall and **41.8% of opening-30-minute
sweeps carry no side at all** — and does so on ~21 contracts per name across 40 names. The new
figure is also **flat across the session** (58.1% open-30 vs 57.3% rest under the strict rule), because
the quote is attached by the vendor at print time rather than depending on our subscription being
warm. The opening blind spot was an artifact of anchor warm-up and is gone, not reduced.
