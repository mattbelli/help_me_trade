# Addendum B — the spot-reference rule, and the index-root deferral (2026-07-26)

*Dated addendum to `FLOW_BASELINE_PREREG_2026-07-26.md`, written before any session beyond the
2026-07-23 validation day has been rolled and before any outcome has been graded. It fixes a
constraint on study design, not a metric.*

## The hazard: a seam in the baselines that looks like a signal

Moneyness needs a reference price. The backfill has daily bars; the live lane will have streaming
spot. Using each where it is available is the obvious thing to do and it is **wrong**, because the
study's whole mechanism is *a name's flow distribution changing over days*. If the reference price
changes at the historic→live join, every name's `mny_bucket` mix shifts on that date, the trailing
20-day baseline straddles the shift, and **every underlying registers a change event at the seam** —
a signal manufactured entirely by our own plumbing.

This is the same failure class the corpus already guards against elsewhere: a detector threshold
changed mid-history silently invalidates every baseline computed before it.

## The rule (pre-committed)

1. **One spot source for both halves.** Historic and live both compute moneyness against the
   **prior session's split-adjusted close** (`equity_bars`, `adjustment=split`, Alpaca SIP). The
   live lane uses the prior close even though live spot is available — slightly less precise,
   but *uniformly* so, and uniform beats precise here.
2. **The spot source is part of `schema_version`.** Changing it (to intraday spot, to a different
   vendor, to a put-call-parity forward) is a version bump plus a **full re-roll of the corpus** —
   never an in-place edit and never a forward-only change. Raw prints are retained precisely so
   this stays a re-roll rather than a re-purchase.
3. **What was referenced is recorded.** `corpus_spot` stores the exact spot used per
   `(date, underlying)` with its source string, so any later audit can reconstruct a bucket
   assignment instead of inferring it.

Note this makes the moneyness bucket deliberately **stale by one session**. That is the intended
trade: a bucket boundary that moves for a real reason (the stock moved) is signal; one that moves
because our reference changed is noise, and only the second kind is avoidable.

## Index roots: deferred, and this is the decision not the gap

SPX, SPXW, NDX, NDXP, VIX, VIXW, RUT, RUTW and XSP carry **30.8% of corpus premium** and have **no
equity bars from any vendor** — they are indices, not stocks. Databento offers us no index dataset
(29 datasets available; none index). They therefore roll up with `mny_bucket = 'unknown'`.

**This is acceptable for this study and is not to be treated as missing data**, because:
- the pre-registered secondary arm already excludes ETF and index roots as the heaviest carriers of
  non-informational flow (dealer hedging, index arb, the quarterly collar);
- the primary arm tests whether a name's option flow predicts *that name's tradeable stock*, and no
  such trade exists for a cash index.

`unknown` is reported as its own mass in every result, never silently pooled and never dropped.

**When index coverage is wanted later**, three options, in preference order, none purchased today:
1. **Proxy** — SPY×10, QQQ, IWM×10. Bars already local; drifts ~1%/yr on dividends, so it needs
   periodic recalibration rather than a fixed multiplier.
2. **Put-call parity from our own prints** — keyless and self-consistent, and it yields the
   *forward*, which is a better moneyness reference for an option than spot. Measured 2026-07-26 on
   the validation day: SPX 7,484.30 from 530 C/P pairs (±1.31%), NDX 28,524.55 (±0.43%), RUT
   2,942.73 (±1.05%) — dispersion dominated by mixing expiries and end-of-day quote timestamps, so
   a per-expiry time-matched implementation is required before this is usable at the 2% bucket
   boundary.
3. **A free index feed** (stooq serves `^SPX`/`^NDX`/`^RUT`/`^VIX` daily, keyless) if the point is
   simply to have it.

Any of these is a `schema_version` bump under rule 2 above.
