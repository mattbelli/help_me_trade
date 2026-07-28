# Addendum F — label adoption is per-print, and the 98% floor becomes a measurement gate (2026-07-28, pre-outcome — FOUNDER-BLESSED 2026-07-28)

*Dated addendum to `FLOW_BASELINE_PREREG_2026-07-26.md`, per Addendum E's own contingency clause:
"sustained failures below the floor on ordinary sessions would send this back for redesign,
recorded as a new addendum." That contingency fired on the first corpus-wide sweep. Written before
any measurement has been run on labeled arms; Addenda A–E stand unedited.*

## What the sweep measured (2026-07-28, all 251 sessions, ~2.0B prints joined)

- **136 of 251 sessions fall below the 98% join floor.** The distribution is a fat band at
  96–98% (most misses), a thinner band at 92–96%, and four structural outliers
  (2025-09-04 77.0%, 2025-09-10 78.6%, 2025-09-15 82.6%, 2025-10-22 84.7%).
- **The join mechanics are not the problem.** Unmatched-print census on a representative
  96.4% session (2026-06-24, 422,634 unmatched of 11.66M): only **1,426 prints (0.3% of
  misses)** involve a contract Alpaca never saw. The rest are missing *copies* inside hot
  (contract, price, size) keys — 57% of all unmatched prints are SPXW alone (14.3% of SPXW's
  prints), QQQ next; the unmatch rate is 7.8% in the opening half hour decaying to <1% late day.
  One missing copy also cascades k-rank misalignment down its key, which is what the
  "tolerance/pairing" residual is.
- **The holes are vendor-side and deterministic.** 2025-09-10 refetched from scratch returned
  **exactly** the stored capture: 7,054,483 trades, byte-identical count. Alpaca's history simply
  serves ~19% fewer resolved prints than Databento that day (join 78.6% dbn-side vs 97.0%
  alpaca-side). Recapture cannot recover what the vendor does not have.
- **Conclusion: Alpaca's historical OPRA tape has a completeness ceiling** — typically 96–98.5%
  of Databento's per session, worst in index products at the open, catastrophic on a few days in
  Aug–Oct 2025. The 8-liquid-name probe's 99.5% was the ceiling's best case, and the 98% floor
  was calibrated on it.

## Why the floor, as an adoption switch, is wrong

Addendum E already registered the per-print fallback: unmatched prints take the ns-collision
heuristic, and every session records `multileg_source` / `label_join_rate` / `label_coverage`.
Given that, the per-session floor produces an absurdity: a 97.5% session would discard 97.5%
**correct vendor labels** and take the heuristic (recall 50.9%) on *everything*, manufacturing a
33%-vs-58% definitional cliff against its adjacent 98.1% neighbor — the exact seam artifact
Addendum B exists to prevent, created *by* the rule meant to prevent it. Partial adoption
strictly dominates: the effective label quality of a session degrades continuously with coverage,
and a whole-session heuristic session is just the coverage=0 endpoint of the same scale. There is
no coverage level at which refusing good labels improves the corpus.

## The rule (replacing the 98% adoption floor; join mechanics and PACKAGE_CODES unchanged)

1. **Adoption is per-print, always.** Every session adopts joined codes where they exist;
   unmatched prints take the registered per-print heuristic fallback. `multileg_source = 'codes'`
   for every session with a completed capture; `label_coverage` records the fraction labeled.
   The whole-session heuristic path remains only for sessions with **no capture at all**.
2. **The v6 migration stays all-or-nothing** exactly as Addendum E registered: one definition
   corpus-wide (codes-where-joined + registered fallback), one re-roll, no mixed vintages.
3. **The 98% floor is repurposed as a measurement gate, not an adoption gate.** Any measurement
   on labeled arms (1.8 and successors) must (a) report the coverage distribution of the sessions
   it pools, (b) flag any session below **95%** coverage in its output, and (c) state the
   registered bias caveat below. Robustness arm, registered now: re-run the headline split on the
   ≥98% subset (115 sessions) — if the answer changes materially vs the full corpus, the
   coverage dependence is itself a finding and must be reported, not smoothed over.
4. **Registered bias caveat (mandatory wherever labeled multileg is surfaced):** unlabeled prints
   are not random — they over-represent index products in the opening burst, where package flow
   concentrates. The heuristic fallback undercounts multileg (recall ~51%), so per-session
   package share is a **floor within its unlabeled slice**, and the bias is largest exactly in
   index open-window arms. Index-arm results carry this caveat verbatim.
5. **Structural-outlier register.** Sessions below 95% coverage (currently expected: the four
   outliers plus a handful of 92–95% days) are listed in the ledger and excluded from the
   robustness subset by construction; they remain in the corpus with their coverage recorded.

## What would send this back again (new addendum, as always)

- Evidence of join **mislabeling** (precision failure, as opposed to coverage shortfall) — e.g.,
  a cascade-pairing audit showing swapped codes across adjacent same-key prints at a rate that
  moves any measure.
- Databento shipping native condition codes: adopted via its own addendum; the Alpaca sidecar
  then becomes the cross-vendor validation diff, and this addendum's coverage machinery prices
  exactly how much the two tapes disagree.

Nothing here changes what is presented to the user. It changes which prints carry vendor truth
versus a labeled approximation, and it records the difference per session instead of pretending
a binary floor could make the approximation vanish.
