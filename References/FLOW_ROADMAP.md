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

**The highest-information step is 1c, not any of those.** It runs the study backwards — take the
big moves that already happened and characterize the flow that preceded them. It is descriptive, so
it does not need to clear a significance bar to be useful, and it tells us what the later phases
should even be looking for. Do not skip it to get to the detectors.

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

- [x] **1.1 Look-ahead: moneyness uses the wrong day's close. — DONE 2026-07-27.** Fixed via
      `bars_before_date()` (most recent split-adjusted close STRICTLY BEFORE the session, so
      weekends/holidays/halts resolve to a real bar). **Measured effect on 2025-07-28: 212,205
      prints — 3.1% of the session — moved out of ATM**, $490M of premium with them. The bug was
      hindsight ATM-ness: strikes near where the stock ENDED UP looked at-the-money after the fact.
      The OTM 2-10% bucket was understated by 8.4%, and OTM is where the academic prior lives.
      `stale_spot=0`. Three tests pin it, incl. that rewriting a session's own close cannot move
      its spot. *Original finding below.* `tools/flow_corpus_rollup.py:215`
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
- [x] **1.8 (b) Is the multi-leg sign flip real, or just SPX? — ANSWERED 2026-07-27: JUST INDEX/ETF.**
      `tools/multileg_arm_probe.py`, 2025-07-28. Single names flip **0/3** measures (net -354.3M all
      vs -89.7M ex-multileg — same sign); index roots + ETFs flip **3/3**; pooled flips 3/3. Multi-leg
      is 19.5% of single-name premium but **48.4% of index/ETF premium** — SPX condors and ETF
      collars. **So the Addendum D alarm is real but does not reach the single-name flow the 3-45d
      swing thesis targets.** Bonus finding: single names were net SHORT (-$354M) while index/ETF was
      net LONG (+$908M) the same session — pooling conflates two opposite signals, which matters for
      the Phase 4 aggregate's denominator. ⚠️ One session, in-sample; ETF list is hand-built and
      incomplete. Re-run across the corpus before leaning on it. *Original framing below.* Redo the Addendum D flip test on
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

### 1c. Backwards discovery — what actually preceded the big moves

**Founder's framing, 2026-07-26, and it reorders the phase:** *"We don't need to prove edge by
entry/exit/slippage. We need to find what more often foreshadows the rise/fall in a stock. Is it
multiple days of bull flow, is it abnormally large sweeps on smaller caps, could be both — or maybe
bullish flow correlates more with bearish movement. We need a pattern, not absolutes."*

This runs the study **backwards**: not "given this flow, what happens" but "given a big move, what
was the flow before it." That is a different and more data-efficient question. The 6.4-effective-names
problem bites **significance testing**; it bites far less when describing a conditional
distribution, because conditioning on large moves selects the informative subset instead of diluting
across every name-day. Sits before the amendment deliberately — what this finds should shape what
Addendum E registers, so the amendment absorbs it in one pass.

⚠️ **The trap that would make this worthless, and the rule that prevents it.**
`P(flow | big move)` is NOT `P(big move | flow)`. If 80% of big rises had heavy bullish flow, that is
worth nothing when 78% of all name-days do. **Every statistic here is computed against a matched
control set — same liquidity tier, same sector, same period, names that did NOT move — and reported
as lift over base rate, never as a raw frequency.** A number without its control is not a finding,
it is a description of the market. No exceptions.

- [ ] **1.11 Define the event set — MARKET-RELATIVE, graded RAW.** Founder, 07-27: *"essentially
      measuring alpha over average market returns — not every stock went up."* Correct, and it fixes
      a flaw in the first draft of this file: a top-decile raw move in a rising market selects
      **high-beta names**, so we would "discover" beta. Define the event on the **residual** (move
      net of market/sector beta) in **ATR units**. But **grade the raw move alongside it** — a stop
      does not care about residuals, it is hit by the actual price. Two arms, two different
      questions. Rises and falls stay **separate** throughout; pooling them is exactly what would
      hide a reversal effect.
- [ ] **1.11a THE HORIZON IS A FINDING, NOT AN INPUT.** Founder, 07-27: *"we don't know if that's 5
      minutes after a big sweep, or the stock went up 10% over the next 30 days."* Right — so do not
      inherit the prereg's 3/10/21/45 grid here; that grid belongs to the registered study, not to
      discovery. **Scan:** 5 min, 15 min, 1 hour, session close, 1 day, 3, 5, 10, 21, 45. We hold the
      data for every one of them (nanosecond option prints; Alpaca SIP equity trades at the same
      resolution, free).
      ⚠️ **Report the WHOLE CURVE, never the peak.** "It works at 10 days" after scanning ten
      horizons is one look dressed as the only look. "Here is the curve across all ten" is a finding.
      Same computation, opposite epistemic status. Every scan gets a ledger row.
- [ ] **1.11a-ii Separate IMPACT from DRIFT — this is the question that matters.** A move 5 minutes
      after a big sweep is most likely the sweep's own price impact plus the dealer hedging into the
      stock. It is mechanical, it will show up strongly, and **we cannot trade it** — by the time the
      print is visible the move has happened. (It is also exactly what Phase 3 detects.) A move over
      the following weeks is a forecast. So decompose the post-event return into **immediate impact
      (0–15 min) / short drift (15 min–1 day) / medium (1–10 d) / long (10–45 d)** and ask:
      **is there anything left after the impact settles?** All of it in bucket one ⇒ price impact,
      nothing to capture. Drift after the impact ⇒ information leaking out over time, and that is the
      tradeable part. This also makes the immediate move *useful rather than useless*: it stops being
      the trade and becomes the **confirmation** that something real happened.
      *Not an assumption about founder reaction speed* — if a pattern lands at 15 minutes and is
      actionable, that is live. The axis is impact vs drift, not fast vs slow.
- [ ] **1.11b Lead, lag, or coincident — run this FIRST, it is an afternoon.** Correlate name-level
      flow against forward and trailing returns at lags −10 … +10 days. The correlation between
      option flow and price is **mechanical and not in question** — options are derivatives. The open
      question is the **arrow of time**: a peak at negative lags means flow leads and there is
      something to trade; a peak at lag 0 or positive means flow is reacting and there is not. Most
      published flow work never separates the three, which is one reason it does not work. *Decides
      where every later step should look.*
- [ ] **1.12 Build the matched control set.** For every mover, controls matched on liquidity tier,
      sector and date. *Done when:* the control set's own base rates are computed and written down
      BEFORE any mover statistic is looked at.
- [ ] **1.13 Profile the preceding flow**, 5 / 10 / 20 days before each event, movers vs controls:
      consecutive same-sign tilt days (*the "multiple days of bull flow" hypothesis*); largest single
      print relative to that name's own normal, split by liquidity tier (*the "abnormally large
      sweeps on smaller caps" hypothesis*); concentration — one day or spread out; DTE mix; moneyness
      mix; call/put mix; multi-leg share.
- [ ] **1.13b Equity activity AFTER the flow.** Founder, 07-27. Distinct from the Phase 3 hedge
      footprint — that is seconds, this is days. Did underlying **volume expand** in the sessions
      following unusual option flow, and does flow-plus-volume-expansion foreshadow better than flow
      alone? Free: daily volume is already in `equity_bars.v`. A two-part pattern is also harder to
      fake than a one-part one, so if it holds it is more likely real.
- [ ] **1.14 Test the sign directly.** Compute the profile before big **rises** and before big
      **falls** separately. Same profile ⇒ no signal. Bullish flow preceding falls more often ⇒ the
      reversal effect, which is what Pan & Poteshman measured (price pressure at d+1, significantly
      reversing d+2 through d+10 — our exact window). If it replicates here it **inverts the sign the
      entire retail industry sells**, which would be the most valuable single finding available to us.
      *Note:* partially absorbs measurement 1.10's cross-tab — run 1.10 inside this frame.

**THIS PHASE IS NOT A BACKTEST.** Founder, 07-27: *"we aren't backtesting a strategy... we can get
to backtesting possibly if we find a pattern worth testing."* So, explicitly: **no entry, no exit,
no slippage, no position sizing, no P&L** appears anywhere in 1c. The only output is of the form

> *this configuration of flow preceded larger-than-average market-relative moves N× more often than
> its matched controls, and here is the horizon curve.*

Backtesting is a **separate, later decision**, taken only if a pattern is strong enough to be worth
spending the test window on. Most will not be, and that is a normal outcome — the profile still
tells us where to look next. Anyone who reaches for entry/exit mechanics inside this phase has
misread it.

**Report the outcome DISTRIBUTION, never a hit rate.** Founder, 07-27: *"I'm looking for ideas, not
expecting everyone to hit — but if more hit than lose, or those gains are larger than the losses, we
have alpha."* That is **expectancy**, and it is a more forgiving bar than a win rate: 35% right with
winners 3× the losers is a good pattern. So a hit-count screen would discard exactly what we are
hunting — the setup that is usually a small loss and occasionally a large win looks terrible on
frequency and excellent on expectancy. Report the full MFE / MAE / terminal / giveback distribution
against controls, in ATR units. Those metrics were chosen because they carry **magnitude**; a
win/loss tally throws that away.

**Output is a PROFILE, not a score.** Ships as perception, labelled unvalidated. Discovery runs on
the **training window only**; the test split (2026-06-01 → 2026-07-25) stays untouched, and anything
found here becomes a registered hypothesis in Addendum E before it may be tested there.

### 1d. The prereg amendment (Addendum E)

- [ ] **1.15 Correct baseline #4** to production's real parameters. It currently says "$500k
      premium, DTE≤30", which describes nothing we run. "We beat production" would be a false claim.
- [ ] **1.16 Make the kill rule able to fail.** Today: dead only if it fails at **all** four horizons
      across **all** five quintiles, over ~64 declared arm variants. Something always survives by
      luck. *Fix:* declare **one primary cell** (recommend: midpoint sign, multileg-excluded,
      pooled quintiles, terminal alpha vs SPY in ATR units). Alive only if that passes. Everything
      else is exploratory, labelled, with no survival authority.
- [ ] **1.17 Horizon of record = 10 trading days.** 12 months gives 2–4 independent observations at
      21/45 days. Report them; state in advance they cannot confirm or kill anything.
- [ ] **1.18 Fix the significance bar.** Naive √N assumes 5,554 independent names; the real figure is
      **6.4** (verified). Use time-block-clustered standard errors and require ≥20 independent event
      dates per cell.
- [ ] **1.19 State universe size on every table.** The top ~1,000 names carry 99.6% of premium.

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

---

## The looks ledger — what makes "look again, look differently, look small" work

**Founder directive, 2026-07-27:** *"You are going to tell me there's nothing founded in the past
data. I will not accept that answer. We will look again. We will look differently. We will look
small. I know there is correlation with equity and option flow to price movement. We just need to
look at it the right way."*

This is the `CLAUDE.md` canon applied to this study, and it stands: **a dead end is evidence about
the METHOD, not the problem.** No agent may conclude "there is no signal here" — the honest report is
always *"this specific look, on this specific slice, did not separate from its control."* Never
conclude no-edge until at least two genuinely different methods have failed and the failure is
**measured, not assumed.**

**Looking a hundred times is legitimate. That is what a training window is for.** The failure is not
the number of looks — it is looking many times and presenting the survivor as if it were the only
look. So:

- [ ] **Build a looks ledger.** Every hypothesis tested gets a row: what was tried, on which slice,
      what came back, **including the duds**. Cheap — one table.
      *Why it is load-bearing:* something that survived 3 looks and something that survived 300 are
      different objects, and without the ledger they are indistinguishable. The ledger is what lets
      us look **without limit** and still know what a survivor is worth. It converts the founder's
      mandate from a posture into something executable.
- **★ Forward paper testing is the test resource that REGENERATES — so do not ration historical
  looks.** The historical test split is finite and burns once. Forward paper accrues one day per
  day, forever, is out-of-sample by construction, and cannot be look-ahead-contaminated. The rail
  already exists and is live: `book_deepseek`, `book_fable`, `book_panel`, all `enabled=1`,
  `entry_mode='propose'` (verified 2026-07-27). **So the arrangement is: look freely on history, log
  every look, and let forward paper be the arbiter.** Worst case for a hypothesis that does not hold
  up is some paper trades — not the study. This is what makes "we will look again, we will look
  differently" fully safe rather than merely permitted.
- **Two failure modes, not one.** The founder's fear is a false negative — missing something real.
  The symmetric risk is a false positive we then trade. **The same machinery prevents both:**
  matched controls, a sealed test window, and the ledger. Neither risk is a reason to look less.
- **"Look small" has an economic argument, not only a statistical hazard.** If edge survives
  anywhere it is in the corners nobody watches — thin names, odd expirations, unfashionable sectors.
  Mega-caps have a thousand professionals staring at them. Expect small to work; do not treat it as
  the consolation search after big fails.

## Standing rules for every phase

- **Ship angles as perception, not as scored signals.** "No proven edge" is never a reason to
  withhold an angle — the product's value is fusing several views. Dressing an unvalidated angle as
  a calibrated win% is the thing that is actually forbidden.
- **Every idea is graded whether or not it is taken.** One ledger, all sources.
- **Log what a phase dropped.** A silent cap reads as "we covered everything."
- **Verify before recording as fact.** This round alone, agent claims that did not survive contact
  with the code: the `unit_of_measure_qty` fix, "the corpus DB does not exist yet", and "we lack OI".
