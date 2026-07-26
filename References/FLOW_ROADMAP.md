# Flow corpus → tradeable signal: the ordered plan

*Written 2026-07-26. Founder set the order: fix what the research round found, then open interest,
then equity matching, then the market-regime layer. Companion to
`FLOW_ALPHA_RESEARCH_2026-07-26.md` (the research), `FLOW_BASELINE_PREREG_2026-07-26.md` + Addenda
A–D (the registered study), and the `whole-market-flow-corpus` board card.*

**Read this file top to bottom. Do the phases in order. Do not start a phase before the one above
it is checked off** — each phase's output is the next one's input, and skipping ahead means
measuring against a corpus we already know is wrong.

## The end state we are building toward

Two layers, combined as a **gate** — not a blend:

- **Layer 1 — regime.** Whole-market aggregate flow says whether the tape is leaning up or down.
- **Layer 2 — selection.** Name-level flow says which names stand out.
- **The gate:** no long when Layer 1 is bearish, no short when it is bullish, regardless of how good
  the name looks.

A gate, because a blend lets a strong name score override a hostile tape — and the measured reason
is that **6.4 of our 5,554 names are effectively independent** (mean pairwise daily-return
correlation 0.157, PC1 23.1%, verified 2026-07-26 on the 250-day panel). The market factor is most
of the move. Fighting it on a single-name read is paying idiosyncratic risk for a bet you lose on
beta.

Phases 2–4 each attack one known blind spot, in the order the founder set:
open-vs-close (Phase 2), who was on the other side (Phase 3), and the regime gate (Phase 4).

---

## Phase 0 — the live lane (PARALLEL TRACK, blocks nothing below)

Independent of the research track: Phases 1–4 all run on historical files. This phase blocks only
**arming the live Databento provider**, which is a separate decision.

- [ ] **0.1 Liveness proof.** Prints/minute counter, a loud alarm on zero prints during RTH, and a
      subscription-echo assert that the server acked what we asked for.
      *Done when:* a deliberately killed socket raises inside 2 minutes.
      *Why:* right now a dead feed and a quiet market look identical. This is the arming
      precondition from the 07-26 audit and it is still not built.
- [ ] **0.2 Corpus-gap check.** A daily job that reports which sessions are missing from the corpus.
      *Done when:* deleting one session's file makes the check fail loudly.

---

## Phase 1 — make the corpus trustworthy

Nothing downstream means anything until this is done. **Everything here is free right now** because
no outcome has been graded and the test split (2026-06-01 → 2026-07-25) is untouched. It stops
being free the moment we grade anything.

### 1a. The bugs

- [ ] **1.1 Look-ahead: moneyness uses the wrong day's close.** `tools/flow_corpus_rollup.py:215`
      passes the session's own date to `bars_for_date`; Addendum B line 22 commits verbatim to the
      **prior** session's close. Every OTM label is conditioned on the move that already happened.
      *Fix:* pass the prior trading day. *Done when:* a test pins that a session's moneyness is
      unchanged by editing that session's own close row.
- [ ] **1.2 `mny_bucket='unknown'` pools two different things.** Index roots (no spot exists) land in
      the same bucket as ordinary names that merely lack a bar. That silently deletes the thin-name
      tier the mid-cap sub-hypothesis is about. *Fix:* separate `no_spot_index` from `no_spot_missing`.
      *Done when:* the two counts are reported separately per session.
- [ ] **1.3 Sweeps still count as N observations.** 99.4% of multi-print groups share one BBO
      snapshot, so N prints are one order and one quote. *Fix:* add a `group_prints` column and
      adopt one sign per ns-group. *Done when:* a known sweep contributes once, not N times.
- [ ] **1.4 Declare the delisting/acquisition rule.** Acquired names are the archetypal winners and
      their bars simply stop. *Fix:* write the terminal-value rule into the prereg **before**
      grading. *Done when:* it is registered in Addendum E.
- [ ] **1.5 Contract multiplier.** `premium = price/PX * size * 100.0` hardcodes 100; adjusted
      contracts do not always deliver 100 shares. ⚠️ The research round proposed using
      `unit_of_measure_qty` — **that does not work**, the field is `UNDEF` on all 1,592,395 C/P
      contracts (verified). Needs a different source or an explicit "known limitation" note.
- [ ] **1.6 Re-roll every session** at the new `schema_version`. *Done when:* `ingested_dates()`
      returns the full corpus at the current version.

### 1b. The four measurements

Each decides something downstream. Run them, do not skip to building.

- [ ] **1.7 (a) Is `|z| ≥ 2` a liquidity detector?** Shuffle print signs randomly, run the exact
      registered rule on real data, count firing rate per liquidity quintile. Zero signal in, so
      anything that fires is a false positive. *Decides:* whether the mid-cap sub-hypothesis is
      testable at all. *Note:* the alarming 12× figure is currently a **simulation** — this replaces
      it with a measurement. Change no threshold before this runs.
- [ ] **1.8 (b) Is the multi-leg sign flip real, or just SPX?** Redo the Addendum D flip test on
      single stocks only, excluding index roots and ETFs. *Decides:* whether multi-leg contamination
      is a whole-corpus problem or a big-index artifact. **Cheapest item in this phase — do it first.**
- [ ] **1.9 (c) Validate the nanosecond multi-leg heuristic, free.** Use `ts_recv` (manually-reported
      complex trades lag auto-executed ones), quote sizes and price granularity as an independent
      check. *Decides:* whether we ever need to buy condition-code days. Data is already on disk.
- [ ] **1.10 (d) The drift × sweep 2×2 — the founder's original question.** Per name-day: did drift
      fire, did a sweep fire. Grade all four cells separately, split by liquidity. Rebuild the sweep
      lane to **production's real parameters** (`MIN_PREMIUM_USD=25_000`, `SWEEP_WINDOW_MS=500`,
      `SWEEP_MIN_EXCHANGES=3`, no DTE gate), not the prereg's wrong ones.
      *Decides:* fuse, rank separately, or keep as rivals. *Pre-registered expectation:* they overlap
      heavily on thin names and barely on liquid ones.

### 1c. The prereg amendment (Addendum E)

- [ ] **1.11 Correct baseline #4** to production's real parameters. It currently says "$500k
      premium, DTE≤30", which describes nothing we run. "We beat production" would be a false claim.
- [ ] **1.12 Make the kill rule able to fail.** Today: dead only if it fails at **all** four horizons
      across **all** five quintiles, over ~64 declared arm variants. Something always survives by
      luck. *Fix:* declare **one primary cell** (recommend: midpoint sign, multileg-excluded,
      pooled quintiles, terminal alpha vs SPY in ATR units). Alive only if that passes. Everything
      else is exploratory, labelled, with no survival authority.
- [ ] **1.13 Horizon of record = 10 trading days.** 12 months gives 2–4 independent observations at
      21/45 days. Report them; state in advance they cannot confirm or kill anything.
- [ ] **1.14 Fix the significance bar.** Naive √N assumes 5,554 independent names; the real figure is
      **6.4** (verified). Use time-block-clustered standard errors and require ≥20 independent event
      dates per cell.
- [ ] **1.15 State universe size on every table.** The top ~1,000 names carry 99.6% of premium.

**Phase 1 is done when:** the corpus is re-rolled clean, the four numbers exist, and Addendum E is
committed. Only then does anything below produce a trustworthy result.

---

## Phase 2 — open interest (open vs close)

**The gap:** buy-to-open a call is a bullish bet; buy-to-close a short call is someone covering.
Same print, opposite meaning. We cannot currently tell them apart, and this split is where the
academic predictability actually lives. No retail platform has it.

**Cost: $0.00, and no deadline** — OPRA `statistics` is L0-tier, so unlike `tcbbo` it does not age
out of a rolling window. There is no rush; do it after Phase 1.

- [ ] **2.1 Confirm OI is in the payload.** One free pull of `statistics` for one day; check the
      stat-type codes actually include open interest. *Verified so far:* the pull prices at $0.00
      (96,468,746 records for 2025-07-28; 72,044,113 for 2024-01-02) — the **contents** are not yet
      confirmed. Do this before planning anything on top of it.
- [ ] **2.2 Backfill** `statistics` across the corpus window. Sequential, `nice`+`ionice`, same
      pattern as the tcbbo backfill. Never concurrent with another ALL_SYMBOLS pull.
- [ ] **2.3 Reconcile prints against daily OI change** per contract to estimate opening vs closing
      interest. *Known limit:* OI is a daily net, so this is an aggregate estimate, never a per-print
      label. Say so wherever it is surfaced.
- [ ] **2.4 Re-grade the Phase 1 signal split by open vs close.** *Decides:* whether the split
      recovers the predictability the literature reports.

⚠️ **What OI will NOT do:** disambiguate a spread's direction. A bull call spread and a bear call
spread produce identical OI changes on both legs. That problem is Phase 3's.

---

## Phase 3 — equity matching (the dealer hedge footprint)

**The idea (founder's).** A dealer who sells 1,000 calls at 0.50 delta must buy ~50,000 shares
within seconds. That hedge is mechanical, not an opinion. If we can see it in the equity tape we
learn three things nothing else gives us.

**Why this is not on the research round's kill list:** that list killed *inferring dealer gamma from
accumulated flow*. This is **directly observing** the hedge. Different mechanism, never evaluated.

**Data: free.** Alpaca SIP historical trades, nanosecond timestamps, already paid for — same
resolution as our OPRA prints. Verified 2026-07-26. Databento wanted $2.06/day whole-market for the
equivalent; we do not need it.

- [ ] **3.1 Measure the clock skew** between OPRA `ts_event` and Alpaca SIP trade timestamps. Must be
      measured, never assumed. *Done when:* the skew distribution is written down.
- [ ] **3.2 Hedge footprint on large single-leg prints.** Take the biggest single-leg option prints;
      measure signed equity volume in the following N seconds against matched control windows on the
      same name, same time of day. **The test is size, not just direction:** the hedge should be
      ≈ delta × contracts × 100 shares. That prediction is what makes it falsifiable.
      *Decides:* whether the footprint exists at all. If not, stop here.
- [ ] **3.3 Counterparty label.** Where a hedge is present, the other side was a **dealer**; where it
      is absent, plausibly another customer — and customer-vs-customer carries no information, just
      disagreement. *This is the direct answer to "there is always a taker on the other side."*
- [ ] **3.4 Spread orientation — the payoff.** A bull call spread and a bear call spread are the same
      two contracts, which is why four research lenses agreed direction is unrecoverable *from the
      option prints*. But they have **opposite net delta**: bull → dealer buys stock, bear → dealer
      sells. The hedge direction reveals the orientation from outside, never touching the corrupted
      leg prices. *If 3.2 works, this may unlock the 33% of premium currently fenced off.*

**Honest failure modes, stated in advance:** dealers net across a whole book rather than hedging
trade by trade; hedges get worked algorithmically over minutes and smear; **reverse causality** — an
informed trader buying stock *and* calls looks identical to a hedge, and only the size test
separates them.

---

## Phase 4 — the market regime gate

**The founder's framing, and the measured case for it:** 6.4 effective independent names out of
5,554. Most of a single stock's move is the market. Do not take a long into a bearish tape no matter
how good the name looks.

**What is new here:** summing directional flow across all ~5,554 names was impossible under the old
1,000-subscription cap. The existing index-pressure gauge (shipped 07-16) reads SPX/index options,
which is substantially index *hedging*; whole-market single-name flow summed is thousands of
separate decisions — a genuinely different measurement.

- [ ] **4.1 Build the aggregate series.** Daily whole-market directional flow from the corpus. One
      query over data already on disk — no new capture, no new detector.
- [ ] **4.2 Does it lead SPY?** Correlate the aggregate against forward SPY returns at 3/10/21/45
      days. *Honest caveat:* ~250 days of one autocorrelated series is a small sample. Expect a
      direction, not a precise number.
- [ ] **4.3 Compare against the index-pressure gauge.** Are they the same signal or different? If
      they agree, the cheaper one wins; if not, that difference is itself informative.
- [ ] **4.4 Wire it as a GATE.** Suppress longs when the aggregate is bearish and vice versa. **Not a
      blended score** — a blend lets a strong name signal override a hostile tape, which is exactly
      the failure the 6.4 figure predicts.
- [ ] **4.5 Grade gated vs ungated** on the Phase 1 signal. *Decides:* whether the gate earns its
      place, or just cuts the sample.

---

## Standing rules for every phase

- **Ship angles as perception, not as scored signals.** "No proven edge" is never a reason to
  withhold an angle — the product's value is fusing several views. Dressing an unvalidated angle as
  a calibrated win% is the thing that is actually forbidden.
- **Every idea is graded whether or not it is taken.** One ledger, all sources.
- **Log what a phase dropped.** A silent cap reads as "we covered everything."
- **Verify before recording as fact.** This round alone, agent claims that did not survive contact
  with the code: the `unit_of_measure_qty` fix, "the corpus DB does not exist yet", and "we lack OI".
