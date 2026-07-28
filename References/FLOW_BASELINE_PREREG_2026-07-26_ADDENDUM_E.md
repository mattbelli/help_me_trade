# Flow baseline prereg — Addendum E: the multileg grain, redefined on labeled data

*Registered 2026-07-27, founder decision ("we should redefine on this evidence"), before any
confirmatory grading and before the test split (2026-06-01 → 2026-07-25) has been touched.
Companion to Addenda A–D; this is the "vendor-supplied complex-trade indicator" replacement that
Addendum D pre-authorized as a new addendum. Addendum D is not edited.*

**One thing changes: how a print earns `multileg=1`. Nothing else.** The direction rule, the three
weightings, the abstention band and states, `u`'s definition, and the both-arms presentation rule
all stand exactly as registered.

## The evidence this is registered on

Measured 2026-07-27 (`tools/alpaca_condition_join_probe.py`, session 2026-07-24, 8 single names,
top-250 contracts each, 1,855,925 matched prints — BUILD_LOG row of the same date):

- Alpaca's OPRA entitlement delivers per-trade condition codes; the join onto the Databento tcbbo
  tape is **exact**: participant-timestamp offset 0ns (p1–p99), join rate 99.54%/99.60%.
- Against true codes, the registered ns-collision heuristic scores **precision 94.7%, recall
  50.9%** — package legs frequently print on different nanoseconds, so the heuristic's
  `multileg=0` arm still contains roughly half the true package legs. It is a floor, not a fence.
- Consequence for the record: measurement 1.8's "single names flip 0/3" was computed on the
  undercounting flag and must be re-run under this definition before it is leaned on.

## The new definition

A print is `multileg=1` iff its joined condition code is in the **package family**:

> `PACKAGE_CODES = {f,g,h,i,j,l,m,t,u} ∪ {k,n,o,p,q,r,s}`

i.e. every multi-leg option package (MLET/MLAT/MLCT/MLFT/MESL/MASL/MFSL/CBMO/MCTP) **and** every
stock-option combo (TLAT/TLET/TLCT/TLFT/TESL/TASL/TFSL). The rationale is the same contamination
Addendum D fenced: package legs are priced by allocation and need not sit inside their own NBBO,
and that is equally true when one leg of the package is stock. The raw code is stored alongside
the flag, so finer splits (electronic vs auction vs floor vs tied) remain available without a
further schema change.

**Fallback hierarchy, registered now:** where no label joins (unmatched prints; a capture gap),
the print falls back to the ns-collision heuristic, and every session records its
**label coverage** (share of prints carrying a code label). A session's labels are adopted only
when its join rate is **≥ 98% of prints**; below that the whole session stays on the heuristic
with the reason logged — a mixed-provenance session would be worse than an honestly-heuristic one.

## Label source and join method, frozen

- **Source:** Alpaca OPRA historical/stream trades (`c` field). Raw per-session capture files are
  the source of truth (flat files beside the DBN archive); every derived table is regenerable
  from them.
- **Join, exactly as probed:** per contract, per identical `(price_cents, size)` key, k-th
  occurrence on one tape matches the k-th on the other, accepted when
  `|Δt − median_offset| ≤ 100ms` (measured offset: 0). Changing the join mechanics is a new
  addendum, not a tuning knob.

## Migration mechanics — all-or-nothing, no seam

- The corpus re-rolls at **`schema_version = 6`** with code-based `multileg` **only when every
  session in the corpus has its label file at ≥ the coverage floor.** No partially-migrated
  corpus: a definition that changes mid-window manufactures a seam event, which is the exact
  failure Addendum B was written against.
- Until v6 lands, v5 stands, and **every v5 multileg reading carries the floor-not-fence caveat**
  in any surface that shows it.
- Both arms (`multileg` 0/1) remain stored at v6; neither is presentable alone — Addendum D's
  rule, inherited unchanged.
- **1.8 re-runs at v6** across the corpus. Its current verdict is provisional.

## What would invalidate or change this

- Alpaca label coverage collapsing at whole-market scale (the probe covered 8 liquid names; the
  backfill reports join rate per session — sustained failures below the floor on ordinary
  sessions would send this back for redesign, recorded as a new addendum).
- Databento shipping native condition codes: adopted, if at all, via its own addendum, with the
  Alpaca sidecar retained as the cross-vendor validation diff over the overlap window.
- Any change to `PACKAGE_CODES`, the coverage floor, or the join mechanics — new addendum.

Nothing here claims edge. This addendum replaces an inference with a measurement, on the same
quantity, before any outcome has been graded against either.
