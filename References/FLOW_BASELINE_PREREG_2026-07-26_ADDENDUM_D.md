# Flow baseline prereg — Addendum D: correcting Addendum C, and the multi-leg grain

*Registered 2026-07-26, roughly one hour after Addendum C and before any confirmatory session
was analyzed. Companion to `FLOW_BASELINE_PREREG_2026-07-26.md` and Addenda A, B, C.*

**Addendum C is not edited. It stays in the record, wrong narrative and all.** This addendum
corrects it. Two things change — what the three measures *are*, and what an abstain records about
itself — plus one new grain. **The decision rule itself is unchanged, and so is the declared
abstention band.** The set of buckets that receive a directional call is identical before and after.

## Process note — why this is one hour old

Addendum C was registered before its own central claim was checked. It described `net_premium`,
`u_premium_clipped` and `u_premium` as three measures whose agreement is evidence. They are not
independent, and a reader who later credited "three independent measures agreed" would badly
over-weight that consensus. Correcting it now — before any confirmatory session is analyzed —
is the point of keeping a record. Leaving the false claim registered so the timeline looked tidier
would have been the worse prereg sin.

## Correction 1 — one signed axis at three weightings, not three votes

`sign(u)` **is** the Addendum-A midpoint side, by construction. `u = (price − mid) / half`, and
`good_q` requires `ask > bid`, so `half > 0` and the division cannot change the sign. Verified in
`tools/flow_corpus_rollup.py` (the `good_q` / `u` / `side` block). Every one of the three measures is

> `Σ sign(uᵢ) · w(|uᵢ|) · premiumᵢ`

with `w = 1` for `net_premium`, `w = min(|u|, 1)` for `u_premium_clipped`, and `w = |u|` for
`u_premium`. They share every print's sign and differ only in weight. Measured on 2025-07-28:

| | same sign |
|---|---|
| `u_premium` vs `u_premium_clipped` | 99.76% |
| `net_premium` vs `u_premium` | 92.20% |
| `net_premium` vs `u_premium_clipped` | 92.33% |

Consequences, all binding:

- **Their agreement tests exactly one property: that the bucket's direction survives the choice of
  weighting.** That is a real and worthwhile robustness check. It is not three confirmations.
- **A 2-of-3 majority is meaningless here** and is forbidden — not merely "a different rule needing
  its own addendum" as C put it, but incoherent, because a majority among sign-clones counts one
  opinion twice. The majority-vote `dissenters()` helper written under C has been removed rather
  than kept alongside.
- Adding a fourth weighting (`w = u²`, say) would not add evidence. The spectrum is not a menu.

## Correction 2 — an abstain must record why

The rule is unchanged: **a bucket is directional only when the aggregate is non-zero and identical
in sign at all three weightings; otherwise it abstains.** What is new is that every abstain is
recorded as exactly one of three states, checked in this order:

1. **`no_opinion`** — all three read exactly zero. Buckets made entirely of at-the-mid prints, where
   `side` is `unclassified` and `u` is 0 by construction. Honestly directionless, not conflicted.
   2,858 of 6,421 abstaining buckets on 2025-07-28.
2. **`weighting_unstable`** — `sign(u_premium) ≠ sign(u_premium_clipped)`. The intensity weighting is
   internally unstable; the |u| tail is steering the sum. Checked *before* `contested`, because when
   the intensity axis is already unstable, a disagreement with net premium is a symptom of the same
   tail rather than a separate finding.
3. **`contested`** — raw and clipped agree and are non-zero, but `net_premium` is zero or opposite.
   The dollar majority and the aggressively-priced dollars point opposite ways.

**Each state is reported by premium share, never pooled.** This is not cosmetic: raw-vs-clipped
disagreement is 0.24% of buckets but **15.8% of premium** against a total abstention of 24.68%, so
`weighting_unstable` is plausibly the dominant abstain driver by dollars. Pooled into one number it
disappears behind the at-the-mid buckets, and the band below would hide its own most interesting
failure mode. Implemented as `abstention_by_state()` in `signals/options_flow/corpus_direction.py`.

`contested` is a **recorded perception state, not a signal.** It carries no predictive claim. Any
test of whether it predicts requires its own prereg, using the definition frozen above. Naming it
now, before any outcome exists, is what stops it from being defined later against whichever
definition happens to work.

## The real gap C left open — print-level sign error

Consensus among the three weightings protects against the analyst's choice of weight. It provides
**zero** protection against error in the per-print sign itself, because all three inherit that sign
identically. The measured instance is multi-leg allocation:

A spread leg is priced by the firm allocating the package and need not sit inside that leg's own
NBBO, so its midpoint side is unreliable. Measured on 2025-07-28, post-definition-join
(6,930,544 prints), where a print is flagged multi-leg iff it shares its `ts_event` with a print on a
**different** instrument:

| | prints | share of prints | share of premium |
|---|---|---|---|
| multi-leg signature | 897,854 | **12.96%** | **33.33%** |

| session total | all prints | excluding multi-leg |
|---|---|---|
| `net_premium` | +$553.6M | **−$219.3M** |
| `u_premium_clipped` | +$95.4M | **−$84.3M** |
| `u_premium` | +$366.4M | **−$176.5M** |

**Excluding multi-leg prints flips the sign of all three measures at once.** The session's unanimous
"long" is unanimous *because* the three share one contaminated sign — the agreement is one error
counted three times, which is exactly the failure Correction 1 predicts and exactly what a
three-independent-votes reading of Addendum C would have hidden.

This also kills C's implied hierarchy in which `net_premium` is the safe, outlier-immune measure.
It is not clean. It is merely less *leveraged* to the contamination (weight 1 rather than weight
|u|); a $300M leg allocated above its own mid contributes $300M of "buying" at full weight.

### It is a grain, not a filter

`multileg` (0/1) joins the rollup primary key at **`schema_version = 3`**. Both arms are stored for
every bucket. Excluding the prints outright would be a choice made on one in-sample session; storing
both means neither inclusion nor exclusion can be selected after seeing the answer, and it costs one
re-roll rather than a re-purchase (raw prints are retained). Mirrors Addendum A's strict/midpoint
two-arm pattern.

**Until a reading is presented per-arm, no bucket-level result — directional or abstain — may be
described as free of this contamination.**

### The flag is a heuristic, and is registered as one

OPRA `tcbbo` carries no condition code that marks complex trades. Measured by
`tools/tcbbo_flags_probe.py`: DBN `flags` is **constant at 194 across all 6,954,842 prints** of
2025-07-28 — `P(multileg | flags)` equals the base rate exactly, so it discriminates nothing. The
`ts_event` collision is all we have.

It is a strong heuristic, not a guess. `ts_event` is true-nanosecond (only 0.10% of prints land on a
µs boundary — the 1/1000 expected from random ns), so across 6.95M prints in a 23,400-second session
the expected number of *coincidental* collisions is **~1**. A shared nanosecond is real simultaneity.
Requiring a **different** instrument is what separates a package from a sweep: same instrument at the
same nanosecond is one order crossing several venues, which is 44.5% of the two-print groups and is
not contamination. Enrichment where it should be: `P(multileg | |u|>2) = 36.0%` against a 13.0% base.

What would falsify it: a vendor-supplied complex-trade indicator, or evidence that packages are
published with per-leg timestamps that differ. Either would replace the heuristic and is a new
addendum.

## What is NOT changed

- **The rule.** Non-zero and sign-identical at all three weightings, else abstain.
- **The declared band.** Abstention between **20% and 35% of premium** on the ~258 sessions not yet
  examined, binding on the **sum** of the three abstain states. Revising it here would be the
  "absorbed by adjusting the rule" move Addendum C forbids.
- **No sub-bands are declared** for the individual states. The 2025-07-28 splits above are
  **in-sample reference only**. Declaring fresh sub-bands from the one session already examined
  would manufacture new predictions out of data already seen.
- **`u_premium`'s definition.** Still frozen, still exactly as pre-registered in Addendum A.

## What would invalidate or change this

- Changing the clip bound, the agreement requirement, or adding a fourth weighting — as under C, a
  `schema_version` bump and a new addendum.
- Changing the multi-leg definition away from "shares `ts_event` with a different instrument" — same.
- Presenting either multi-leg arm alone as *the* directional read, without the other, is a
  violation of this addendum rather than a tuning choice.

Nothing here claims edge. Addendum C committed us to a rule and a rate before the data arrived;
this addendum makes sure the reason the rule is trusted is a true one.
